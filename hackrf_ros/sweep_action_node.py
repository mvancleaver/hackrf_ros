"""Sweep Action Server for HackRF ROS2.

Implements SweepActionNode — a standalone ROS2 node that:
- Accepts wideband sweep goals via /hackrf/sweep action
- Retunes the driver (/hackrf_node) hop-by-hop via AsyncParametersClient
- Collects SpectrumStamped messages per hop from /hackrf/spectrum
- Tukey-blends hops in linear domain to produce a stitched composite PSD
- Publishes per-hop feedback and returns the full stitched result on success
- Restores the original center_frequency on cancel or completion

Usage::

    ros2 run hackrf_ros sweep_action_node

Architecture notes:
- MultiThreadedExecutor + ReentrantCallbackGroup required (action execute
  callback must run concurrently with subscription callbacks).
- Do NOT import or inherit from sweep_node.py (matplotlib display tool).
- Threat mitigations: goal field validation (T-02-02-01), averaging clamp
  (T-02-02-02), hop count limit (T-02-02-03).
"""
from __future__ import annotations

import threading
import time

import numpy as np

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.parameter_client import AsyncParametersClient
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy

from hackrf_interfaces.action import Sweep
from hackrf_interfaces.msg import SpectrumStamped


# ---------------------------------------------------------------------------
# Constants (must match hackrf_lifecycle_node.py)
# ---------------------------------------------------------------------------
FFT_SIZE = 4096
USABLE_BW_FRACTION = 0.8   # 80% usable bandwidth per hop
EDGE_TRIM = int(FFT_SIZE * 0.10)   # 410 bins trimmed from each side

HACKRF_MIN_HZ = 1e6    # 1 MHz
HACKRF_MAX_HZ = 6e9    # 6 GHz
MAX_HOPS = 500          # T-02-02-03: DoS guard
MAX_AVERAGING = 64      # T-02-02-02: DoS guard
MIN_AVERAGING = 1
DEFAULT_AVERAGING = 4
DEFAULT_STEP_HZ = 20e6
DEFAULT_SAMPLE_RATE = 20e6
SPECTRUM_TIMEOUT_S = 3.0
PLL_SETTLE_S = 0.025   # seconds to wait after retuning before collecting data


# ---------------------------------------------------------------------------
# Module-level pure functions (importable by tests without Node instantiation)
# ---------------------------------------------------------------------------

def _plan_hops(
    freq_min: float,
    freq_max: float,
    sample_rate: float,
    step_hz: float,
) -> list[float]:
    """Compute center frequencies to cover freq_min..freq_max.

    Args:
        freq_min: Lower band edge in Hz.
        freq_max: Upper band edge in Hz.
        sample_rate: HackRF sample rate in Hz (e.g. 20e6).
        step_hz: Hop step size in Hz; 0 → use usable_bw.

    Returns:
        List of center frequencies in Hz.
    """
    usable_bw = sample_rate * USABLE_BW_FRACTION
    effective_step = min(step_hz, usable_bw) if step_hz > 0 else usable_bw
    centers: list[float] = []
    center = freq_min + sample_rate / 2.0
    while center - sample_rate / 2.0 < freq_max:
        centers.append(center)
        center += effective_step
    return centers


def _tukey_weights(n: int, alpha: float = 0.2) -> np.ndarray:
    """Return a Tukey (tapered cosine) window of length n.

    Args:
        n: Number of samples.
        alpha: Fraction of the window inside the taper (0 = rectangular,
               1 = Hann). Default 0.2 → 10% taper each side.

    Returns:
        Float64 array of length n with values in [0, 1].
    """
    taper_len = int(n * alpha / 2)
    if taper_len == 0:
        return np.ones(n, dtype=np.float64)
    ramp_up = np.linspace(0.0, 1.0, taper_len)
    ramp_dn = np.linspace(1.0, 0.0, taper_len)
    middle = np.ones(n - 2 * taper_len, dtype=np.float64)
    return np.concatenate([ramp_up, middle, ramp_dn])


def _blend_hop_into_composite(
    psd_db: np.ndarray,
    center_hz: float,
    sample_rate: float,
    freq_min: float,
    bin_width: float,
    comp_linear: np.ndarray,
    comp_weight: np.ndarray,
) -> None:
    """Blend one hop's PSD (in dB) into a running linear composite.

    Converts psd_db to linear power, trims EDGE_TRIM bins from each side
    to remove filter roll-off artifacts, applies a Tukey window for smooth
    overlap blending, then scatter-accumulates into comp_linear/comp_weight.

    Args:
        psd_db:      4096-element float array in dB.
        center_hz:   Center frequency of this hop in Hz.
        sample_rate: Sample rate of this hop in Hz.
        freq_min:    Composite lower frequency edge in Hz.
        bin_width:   Composite bin width in Hz.
        comp_linear: Accumulator array (linear power, modified in-place).
        comp_weight: Weight accumulator array (modified in-place).
    """
    # dB → linear
    psd_lin = 10.0 ** (np.asarray(psd_db, dtype=np.float64) / 10.0)

    # Trim roll-off edges
    trimmed_lin = psd_lin[EDGE_TRIM: FFT_SIZE - EDGE_TRIM]
    n_trimmed = len(trimmed_lin)

    # Frequencies of the trimmed bins
    half_sr = sample_rate / 2.0
    trimmed_start = center_hz - half_sr + EDGE_TRIM * (sample_rate / FFT_SIZE)
    trimmed_freqs = trimmed_start + np.arange(n_trimmed) * (sample_rate / FFT_SIZE)

    # Tukey weights for smooth blending at boundaries
    weights = _tukey_weights(n_trimmed, alpha=0.2)

    # Scatter into composite
    n_comp = len(comp_linear)
    for j in range(n_trimmed):
        idx = int((trimmed_freqs[j] - freq_min) / bin_width)
        if 0 <= idx < n_comp:
            comp_linear[idx] += weights[j] * trimmed_lin[j]
            comp_weight[idx] += weights[j]


def _finalize_composite(
    comp_linear: np.ndarray,
    comp_weight: np.ndarray,
    n_bins: int,
) -> np.ndarray:
    """Convert the weighted linear accumulator back to dB.

    Args:
        comp_linear: Linear power accumulator.
        comp_weight: Weight accumulator.
        n_bins:      Expected output length.

    Returns:
        Float32 array of dB values; bins with no data are -100.0.
    """
    result = np.full(n_bins, -100.0, dtype=np.float32)
    valid = comp_weight > 0
    result[valid] = (
        10.0 * np.log10(
            np.maximum(comp_linear[valid] / comp_weight[valid], 1e-20)
        )
    ).astype(np.float32)
    return result


# ---------------------------------------------------------------------------
# SweepActionNode
# ---------------------------------------------------------------------------

class SweepActionNode(Node):
    """ROS2 action server that orchestrates a wideband frequency sweep.

    Accepts goals from /hackrf/sweep (hackrf_interfaces/action/Sweep).
    Retunes /hackrf_node via AsyncParametersClient, collects SpectrumStamped
    messages per hop, Tukey-blends them in linear domain, and returns a
    stitched composite PSD as the action result.
    """

    def __init__(self) -> None:
        super().__init__('sweep_action_node')

        # --- Parameters ---
        self.declare_parameter('driver_node_name', '/hackrf_node')
        self.declare_parameter('default_step_hz', DEFAULT_STEP_HZ)
        self.declare_parameter('default_averaging', DEFAULT_AVERAGING)
        self.declare_parameter('spectrum_timeout_s', SPECTRUM_TIMEOUT_S)

        driver_name = (
            self.get_parameter('driver_node_name').get_parameter_value().string_value
        )

        # --- Internal state ---
        self._latest_spectrum: SpectrumStamped | None = None
        self._spectrum_event = threading.Event()
        self._saved_center_freq: float | None = None

        # --- Subscription (RELIABLE) ---
        reliable_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
            durability=DurabilityPolicy.VOLATILE,
        )
        sub_cb_group = ReentrantCallbackGroup()
        self._spectrum_sub = self.create_subscription(
            SpectrumStamped,
            '/hackrf/spectrum',
            self._on_spectrum,
            qos_profile=reliable_qos,
            callback_group=sub_cb_group,
        )

        # --- AsyncParametersClient ---
        self._param_client = AsyncParametersClient(self, driver_name)

        # --- Action server ---
        action_cb_group = ReentrantCallbackGroup()
        self._action_server = ActionServer(
            self,
            Sweep,
            '/hackrf/sweep',
            execute_callback=self._execute_sweep,
            goal_callback=self._goal_callback,
            cancel_callback=self._cancel_callback,
            callback_group=action_cb_group,
        )

        self.get_logger().info('SweepActionNode ready — action server at /hackrf/sweep')

    # ------------------------------------------------------------------
    # Subscription callback
    # ------------------------------------------------------------------

    def _on_spectrum(self, msg: SpectrumStamped) -> None:
        """Store the latest spectrum message and signal waiting threads."""
        self._latest_spectrum = msg
        self._spectrum_event.set()

    # ------------------------------------------------------------------
    # Action server callbacks
    # ------------------------------------------------------------------

    def _goal_callback(self, goal_request) -> GoalResponse:
        return GoalResponse.ACCEPT

    def _cancel_callback(self, goal_handle) -> CancelResponse:
        return CancelResponse.ACCEPT

    # ------------------------------------------------------------------
    # Parameter helpers
    # ------------------------------------------------------------------

    def _set_center_freq(self, freq_hz: float) -> bool:
        """Set center_frequency on the driver node via AsyncParametersClient.

        Returns:
            True on success, False on timeout or error.
        """
        try:
            future = self._param_client.set_parameters(
                [Parameter('center_frequency', Parameter.Type.DOUBLE, freq_hz)]
            )
            rclpy.spin_until_future_complete(self, future, timeout_sec=5.0)
            if future.done():
                return True
            self.get_logger().warn(
                f'_set_center_freq({freq_hz:.3e}): future did not complete')
            return False
        except Exception as exc:
            self.get_logger().error(f'_set_center_freq error: {exc}')
            return False

    def _wait_for_spectrum(self, timeout_s: float) -> SpectrumStamped | None:
        """Block until a new SpectrumStamped is received or timeout.

        Clears the event before waiting so only *new* messages after this
        call trigger the return.

        Returns:
            The latest SpectrumStamped, or None on timeout.
        """
        self._spectrum_event.clear()
        fired = self._spectrum_event.wait(timeout=timeout_s)
        if fired:
            return self._latest_spectrum
        return None

    # ------------------------------------------------------------------
    # Execute callback
    # ------------------------------------------------------------------

    def _execute_sweep(self, goal_handle) -> Sweep.Result:
        """Execute a wideband sweep and return the stitched PSD result.

        This is the action execute callback.  It runs in a thread managed
        by the MultiThreadedExecutor.

        Security mitigations applied here:
        - T-02-02-01: freq range validation
        - T-02-02-02: averaging clamp
        - T-02-02-03: hop count limit
        """
        request = goal_handle.request
        freq_min = float(request.freq_min_hz)
        freq_max = float(request.freq_max_hz)
        step_hz = float(request.step_hz)
        averaging = int(request.averaging)

        result = Sweep.Result()

        # --- Input validation (T-02-02-01) ---
        if freq_max <= freq_min:
            result.success = False
            result.message = (
                f'Invalid goal: freq_max ({freq_max:.3e}) must be > freq_min ({freq_min:.3e})'
            )
            goal_handle.abort()
            return result

        if freq_min < HACKRF_MIN_HZ or freq_max > HACKRF_MAX_HZ:
            result.success = False
            result.message = (
                f'Frequencies out of HackRF range [{HACKRF_MIN_HZ:.0e}, {HACKRF_MAX_HZ:.0e}]'
            )
            goal_handle.abort()
            return result

        # --- Apply defaults and clamp (T-02-02-02) ---
        if averaging <= 0:
            averaging = DEFAULT_AVERAGING
        averaging = max(MIN_AVERAGING, min(averaging, MAX_AVERAGING))

        timeout_s = (
            self.get_parameter('spectrum_timeout_s')
            .get_parameter_value()
            .double_value
        )

        # --- Determine sample_rate from latest spectrum (or default) ---
        if self._latest_spectrum is not None:
            sample_rate = float(self._latest_spectrum.sample_rate_hz)
        else:
            sample_rate = DEFAULT_SAMPLE_RATE

        # --- Plan hops (T-02-02-03) ---
        hops = _plan_hops(freq_min, freq_max, sample_rate, step_hz)

        if len(hops) == 0:
            result.success = False
            result.message = 'No hops planned — check freq_min / freq_max / sample_rate'
            goal_handle.abort()
            return result

        if len(hops) > MAX_HOPS:
            result.success = False
            result.message = (
                f'Too many hops ({len(hops)}). Reduce band width or increase step_hz.'
            )
            goal_handle.abort()
            return result

        # --- Save current center frequency for restore ---
        saved_freq: float | None = None
        if self._latest_spectrum is not None:
            saved_freq = float(self._latest_spectrum.center_frequency_hz)
        self._saved_center_freq = saved_freq

        # --- Composite PSD state ---
        bin_width = sample_rate / FFT_SIZE
        n_bins = max(1, int(round((freq_max - freq_min) / bin_width)))
        comp_linear = np.zeros(n_bins, dtype=np.float64)
        comp_weight = np.zeros(n_bins, dtype=np.float64)

        total_hops = len(hops)
        hops_done = 0

        # --- Hop loop ---
        for i, center in enumerate(hops):

            # Check for cancel *before* starting this hop
            if goal_handle.is_cancel_requested:
                break

            # Retune driver
            ok = self._set_center_freq(center)
            if not ok:
                self.get_logger().warn(
                    f'Hop {i + 1}/{total_hops}: _set_center_freq({center:.3e}) failed, '
                    'continuing with stale frequency'
                )

            # Allow PLL to settle and stale IQ to flush
            time.sleep(PLL_SETTLE_S)

            # Collect `averaging` spectra and average in linear domain
            accum_linear: np.ndarray | None = None
            collected = 0
            for _ in range(averaging):
                if goal_handle.is_cancel_requested:
                    break
                msg = self._wait_for_spectrum(timeout_s)
                if msg is None:
                    self.get_logger().warn(
                        f'Hop {i + 1}: spectrum timeout after {timeout_s}s'
                    )
                    continue
                hop_lin = 10.0 ** (np.asarray(msg.psd_db, dtype=np.float64) / 10.0)
                if accum_linear is None:
                    accum_linear = hop_lin.copy()
                else:
                    accum_linear += hop_lin
                collected += 1

            if goal_handle.is_cancel_requested:
                break

            if collected == 0 or accum_linear is None:
                self.get_logger().warn(f'Hop {i + 1}: no spectra collected, skipping')
                continue

            # Average in linear domain, convert back to dB
            avg_lin = accum_linear / collected
            avg_psd_db = (10.0 * np.log10(np.maximum(avg_lin, 1e-20))).astype(np.float32)

            _blend_hop_into_composite(
                avg_psd_db, center, sample_rate, freq_min, bin_width,
                comp_linear, comp_weight,
            )
            hops_done += 1

            # Publish per-hop feedback
            feedback = Sweep.Feedback()
            feedback.current_hop = i + 1
            feedback.total_hops = total_hops
            feedback.current_freq_hz = center
            partial = _finalize_composite(comp_linear.copy(), comp_weight.copy(), n_bins)
            feedback.partial_psd_db = partial.tolist()
            goal_handle.publish_feedback(feedback)

        # --- Restore original center frequency ---
        if saved_freq is not None:
            self._set_center_freq(saved_freq)
        else:
            self.get_logger().warn(
                'sweep_action_node: no saved center_frequency to restore'
            )

        # --- Handle cancel ---
        if goal_handle.is_cancel_requested:
            if hops_done >= 1:
                partial_psd = _finalize_composite(comp_linear, comp_weight, n_bins)
                result.success = False
                result.message = f'Cancelled — partial result ({hops_done}/{total_hops} hops)'
                result.freq_min_hz = freq_min
                result.freq_max_hz = freq_max
                result.bin_width_hz = bin_width
                result.psd_db = partial_psd.tolist()
            else:
                result.success = False
                result.message = 'Cancelled — no hops completed'
                result.psd_db = []
            goal_handle.canceled()
            return result

        # --- Success ---
        final_psd = _finalize_composite(comp_linear, comp_weight, n_bins)
        result.success = True
        result.message = f'Sweep complete: {hops_done}/{total_hops} hops'
        result.freq_min_hz = freq_min
        result.freq_max_hz = freq_max
        result.bin_width_hz = bin_width
        result.psd_db = final_psd.tolist()
        goal_handle.succeed()
        return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args=None) -> None:
    """Entry point for ros2 run hackrf_ros sweep_action_node."""
    rclpy.init(args=args)
    node = SweepActionNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
