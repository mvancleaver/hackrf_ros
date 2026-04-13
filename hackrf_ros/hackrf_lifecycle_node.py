"""HackRF One ROS2 Lifecycle Driver Node.

Published topics (when ACTIVE):
    /hackrf/spectrum  SpectrumStamped     PSD in dB (4096 bins), ~10 Hz
    (BEST_EFFORT QoS, depth=5)

Services (when ACTIVE):
    /hackrf/sweep    hackrf_interfaces/srv/Sweep   Sweep freq_min..freq_max, return aggregated PSD

Lifecycle:
    UNCONFIGURED -> on_configure -> INACTIVE -> on_activate -> ACTIVE
"""
from __future__ import annotations

import queue
import threading
import time

import numpy as np
import scipy.fft

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.lifecycle import LifecycleNode, LifecycleState, TransitionCallbackReturn
from rclpy.parameter import Parameter
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from rclpy.callback_groups import ReentrantCallbackGroup
from rcl_interfaces.msg import (
    ParameterDescriptor,
    FloatingPointRange,
    IntegerRange,
    SetParametersResult,
)
from geometry_msgs.msg import TransformStamped
from tf2_ros import StaticTransformBroadcaster

from diagnostic_updater import Updater
from diagnostic_msgs.msg import DiagnosticStatus

from hackrf_interfaces.msg import SpectrumStamped
from hackrf_interfaces.srv import Sweep
from std_srvs.srv import Trigger


PARAM_RANGES = {
    'center_frequency': (1e6, 6e9),
    'sample_rate':      (2e6, 20e6),
    'lna_gain':         (0, 40),
    'vga_gain':         (0, 62),
}

# HackRF firmware supports hot retuning: center_freq register can be updated
# while streaming — no stop/start needed. Only sample_rate requires restart
# (USB bandwidth changes).  Removing center_frequency here prevents SIGSEGV
# in libhackrf caused by stopping/restarting USB transfers mid-sweep.
_STREAM_RESTART_PARAMS = frozenset({'sample_rate'})

FFT_SIZE = 4096
PSD_AVERAGING = 16
USABLE_BW_FRACTION = 0.8  # 80% — matches MAX2837 analog filter rolloff
EDGE_TRIM = int(FFT_SIZE * 0.10)  # 10% per side
ADC_CLIP_THRESHOLD = 0.005  # discard frame if >0.5% samples clipped

AGC_HOLD_FRAMES = 10   # publish cycles before AGC may fire again (hysteresis)
AGC_CLIP_WINDOW = 5    # clips within this many cycles triggers gain reduction
AGC_LNA_STEP = 8       # dB — HackRF LNA steps in 8 dB increments
AGC_VGA_STEP = 4       # dB — reduce VGA by 4 dB (2 dB minimum step x2)

DISCONNECT_ERROR_TIMEOUT = 2.0   # seconds before USB fault escalates WARN -> ERROR


class HackRFLifecycleNode(LifecycleNode):
    """ROS2 lifecycle node for HackRF One SDR."""

    def __init__(self, **kwargs):
        super().__init__('hackrf_node', **kwargs)
        self._hackrf = None
        self._device_lock = threading.RLock()
        self._iq_queue: queue.Queue = queue.Queue(maxsize=64)
        self._is_streaming = False
        self._sweep_lock = threading.Lock()
        self._timer = None
        self._spectrum_pub = None
        self._sweep_srv = None
        self._diag_updater = None
        self._activate_time: float | None = None
        self._last_rx_time: float = 0.0
        self._rx_overflow_count = 0
        self._clip_count = 0

        # AGC state (REL-01)
        self._agc_hold_counter: int = 0
        self._agc_clip_snapshot: int = 0   # _clip_count value at last AGC check
        self._agc_cycle_counter: int = 0   # publish cycles since last AGC fire
        self._agc_last_action: str = 'none'

        # USB disconnect state (REL-03)
        self._usb_fault: bool = False
        self._usb_fault_time: float = 0.0

        # Recorder fan-out queue — None when not recording (per D-09, D-10)
        self._recorder_q: queue.Queue | None = None
        self._recorder_q_drops: int = 0

        # Async parameter application — work queue + background worker (REL-02)
        self._param_queue: queue.Queue = queue.Queue(maxsize=4)
        self._param_worker_thread: threading.Thread | None = None

        # FFT state — Blackman window for -58 dB sidelobe suppression
        self._window = np.blackman(FFT_SIZE)
        self._window_S2 = np.sum(self._window ** 2)  # incoherent power norm
        self._psd_accum = np.zeros(FFT_SIZE)  # accumulate LINEAR power
        self._accum_count = 0

    # ------------------------------------------------------------------
    # Lifecycle transitions
    # ------------------------------------------------------------------

    def on_configure(self, state: LifecycleState) -> TransitionCallbackReturn:
        self.get_logger().info('Configuring...')
        self._declare_parameters()

        try:
            import pyhackrf2
            device_index = int(self.get_parameter('device_index').value)
            self._hackrf = pyhackrf2.HackRF(device_index=device_index)
        except ImportError:
            self.get_logger().error('pyhackrf2 not installed.')
            return TransitionCallbackReturn.FAILURE
        except (RuntimeError, OSError) as e:
            self.get_logger().error(
                f'Failed to open HackRF at device_index={device_index}: {e}')
            return TransitionCallbackReturn.FAILURE

        self._apply_params_to_device()

        qos_stream = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )
        self._spectrum_pub = self.create_publisher(
            SpectrumStamped, '/hackrf/spectrum', qos_stream)

        # Static TF: parent_frame -> antenna_frame (per D-07, D-08)
        self._tf_broadcaster = StaticTransformBroadcaster(self)
        tf_msg = TransformStamped()
        tf_msg.header.stamp = self.get_clock().now().to_msg()
        tf_msg.header.frame_id = self.get_parameter('parent_frame').value
        tf_msg.child_frame_id = self.get_parameter('antenna_frame').value
        tf_msg.transform.translation.x = self.get_parameter('antenna_x').value
        tf_msg.transform.translation.y = self.get_parameter('antenna_y').value
        tf_msg.transform.translation.z = self.get_parameter('antenna_z').value
        tf_msg.transform.rotation.w = 1.0  # identity quaternion
        self._tf_broadcaster.sendTransform(tf_msg)

        # Sweep service on a reentrant callback group so it doesn't block
        # the timer callback during long sweeps
        srv_group = ReentrantCallbackGroup()
        self._sweep_srv = self.create_service(
            Sweep, '/hackrf/sweep', self._handle_sweep,
            callback_group=srv_group)

        # Recording control services (per D-10, D-18)
        rec_group = ReentrantCallbackGroup()
        self._recording_start_srv = self.create_service(
            Trigger, '/hackrf/recording/start',
            self._handle_recording_start, callback_group=rec_group)
        self._recording_stop_srv = self.create_service(
            Trigger, '/hackrf/recording/stop',
            self._handle_recording_stop, callback_group=rec_group)

        self._diag_updater = Updater(self)
        self._diag_updater.setHardwareID('hackrf_one')
        self._diag_updater.add('hackrf_status', self._diagnostics_callback)

        self.add_on_set_parameters_callback(self._param_callback)

        self.get_logger().info('Configured.')
        return TransitionCallbackReturn.SUCCESS

    def on_activate(self, state: LifecycleState) -> TransitionCallbackReturn:
        self.get_logger().info('Activating...')
        with self._device_lock:
            if self._hackrf is None:
                self.get_logger().error('No device.')
                return TransitionCallbackReturn.FAILURE
            try:
                self._hackrf.start_rx(self._rx_callback)
                self._is_streaming = True
                self._last_rx_time = time.monotonic()
            except (RuntimeError, OSError) as e:
                self.get_logger().error(f'start_rx failed: {e}')
                return TransitionCallbackReturn.FAILURE

        self._activate_time = time.monotonic()
        self._rx_overflow_count = 0
        self._psd_accum[:] = 0
        self._accum_count = 0
        self._flush_queue()

        self._timer = self.create_timer(0.05, self._process_and_publish)

        # Flush stale param work items before starting fresh
        while not self._param_queue.empty():
            try:
                self._param_queue.get_nowait()
            except queue.Empty:
                break
        self._param_worker_thread = threading.Thread(
            target=self._param_worker, daemon=True, name='hackrf_param_worker')
        self._param_worker_thread.start()

        self.get_logger().info('Active.')
        return TransitionCallbackReturn.SUCCESS

    def on_deactivate(self, state: LifecycleState) -> TransitionCallbackReturn:
        self.get_logger().info('Deactivating...')
        if self._timer is not None:
            self._timer.cancel()
            self.destroy_timer(self._timer)
            self._timer = None
        with self._device_lock:
            if self._is_streaming and self._hackrf is not None:
                try:
                    self._hackrf.stop_rx()
                except (RuntimeError, OSError) as e:
                    self.get_logger().warning(f'stop_rx error: {e}')
                self._is_streaming = False

        # Signal param worker to exit and wait briefly
        if self._param_worker_thread is not None and self._param_worker_thread.is_alive():
            try:
                self._param_queue.put_nowait(None)  # sentinel
            except queue.Full:
                pass
            self._param_worker_thread.join(timeout=2.0)
            self._param_worker_thread = None

        self._activate_time = None
        self.get_logger().info('Deactivated.')
        return TransitionCallbackReturn.SUCCESS

    def on_cleanup(self, state: LifecycleState) -> TransitionCallbackReturn:
        self._close_device()
        self._spectrum_pub = None
        self._sweep_srv = None
        self._diag_updater = None
        return TransitionCallbackReturn.SUCCESS

    def on_shutdown(self, state: LifecycleState) -> TransitionCallbackReturn:
        if self._timer is not None:
            self._timer.cancel()
            self.destroy_timer(self._timer)
            self._timer = None
        if self._is_streaming and self._hackrf is not None:
            try:
                self._hackrf.stop_rx()
            except Exception:
                pass
            self._is_streaming = False
        self._close_device()
        return TransitionCallbackReturn.SUCCESS

    def on_error(self, state: LifecycleState) -> TransitionCallbackReturn:
        self.get_logger().error(f'Error in state {state.label}.')
        if self._timer is not None:
            self._timer.cancel()
            self.destroy_timer(self._timer)
            self._timer = None
        if self._is_streaming and self._hackrf is not None:
            try:
                self._hackrf.stop_rx()
            except Exception:
                pass
            self._is_streaming = False
        self._close_device()
        return TransitionCallbackReturn.SUCCESS

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------

    def _declare_parameters(self):
        self.declare_parameter('center_frequency', 2437e6,
            ParameterDescriptor(description='Center frequency Hz',
                floating_point_range=[FloatingPointRange(
                    from_value=1e6, to_value=6e9, step=0.0)]))
        self.declare_parameter('sample_rate', 20e6,
            ParameterDescriptor(description='Sample rate Hz',
                floating_point_range=[FloatingPointRange(
                    from_value=2e6, to_value=20e6, step=0.0)]))
        self.declare_parameter('lna_gain', 16,
            ParameterDescriptor(description='LNA gain dB (8 dB steps)',
                integer_range=[IntegerRange(
                    from_value=0, to_value=40, step=0)]))
        self.declare_parameter('vga_gain', 20,
            ParameterDescriptor(description='VGA gain dB (2 dB steps)',
                integer_range=[IntegerRange(
                    from_value=0, to_value=62, step=0)]))
        self.declare_parameter('amp_enabled', False,
            ParameterDescriptor(description='RF amplifier (adds ~11 dB gain + noise)'))
        self.declare_parameter('device_index', 0,
            ParameterDescriptor(description='HackRF device index (0-based, for multi-radio)'))

        # TF frame parameters (D-07, D-08)
        self.declare_parameter('antenna_frame', 'hackrf_antenna',
            ParameterDescriptor(description='TF frame_id for the antenna (child frame)'))
        self.declare_parameter('parent_frame', 'base_link',
            ParameterDescriptor(description='TF parent frame (robot body)'))
        self.declare_parameter('antenna_x', 0.0,
            ParameterDescriptor(description='Antenna X offset from parent_frame (m)'))
        self.declare_parameter('antenna_y', 0.0,
            ParameterDescriptor(description='Antenna Y offset from parent_frame (m)'))
        self.declare_parameter('antenna_z', 0.0,
            ParameterDescriptor(description='Antenna Z offset from parent_frame (m)'))

    def _param_callback(self, params: list[Parameter]) -> SetParametersResult:
        """Validate params and enqueue hardware application (REL-02).

        Returns immediately — hardware apply runs in _param_worker thread.
        """
        needs_restart = False
        pending = {}
        for param in params:
            name = param.name
            value = param.value
            if name in PARAM_RANGES:
                lo, hi = PARAM_RANGES[name]
                if not (lo <= value <= hi):
                    return SetParametersResult(
                        successful=False,
                        reason=f'{name}={value} outside [{lo}, {hi}]')
            if name in _STREAM_RESTART_PARAMS:
                needs_restart = True
            pending[name] = value

        if self._hackrf is not None and pending:
            try:
                self._param_queue.put_nowait((pending, needs_restart))
            except queue.Full:
                self.get_logger().warning(
                    'Param queue full — param change may be delayed')

        return SetParametersResult(successful=True)

    def _param_worker(self) -> None:
        """Background thread: applies parameter changes to HackRF hardware (REL-02).

        Drains _param_queue. Sentinel value None causes thread to exit.
        Retry logic for stop_rx/start_rx lives here — NOT in the executor thread.
        """
        while True:
            try:
                item = self._param_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:   # shutdown sentinel
                break
            pending, needs_restart = item
            with self._device_lock:
                if needs_restart and self._is_streaming and self._hackrf is not None:
                    try:
                        self._hackrf.stop_rx()
                        self._is_streaming = False
                    except (RuntimeError, OSError) as e:
                        self.get_logger().warning(f'stop_rx for retune: {e}')
                        self._handle_usb_fault('param_worker:stop_rx')
                if self._hackrf is not None:
                    self._apply_pending(pending)
                if needs_restart and not self._is_streaming and self._hackrf is not None:
                    for attempt, settle in enumerate([0.15, 0.3, 0.5]):
                        time.sleep(settle)
                        try:
                            self._hackrf.start_rx(self._rx_callback)
                            self._is_streaming = True
                            self._last_rx_time = time.monotonic()
                            break
                        except (RuntimeError, OSError) as e:
                            if attempt == 2:
                                self.get_logger().error(
                                    f'start_rx after retune failed: {e}')
                                self._handle_usb_fault('param_worker:start_rx')
            # Reset PSD accumulator after any param change
            self._psd_accum[:] = 0
            self._accum_count = 0
            self._param_queue.task_done()

    def _apply_pending(self, pending: dict):
        if 'center_frequency' in pending:
            self._hackrf.center_freq = int(pending['center_frequency'])
        if 'sample_rate' in pending:
            self._hackrf.sample_rate = int(pending['sample_rate'])
        if 'lna_gain' in pending:
            self._hackrf.lna_gain = int(pending['lna_gain'])
        if 'vga_gain' in pending:
            self._hackrf.vga_gain = int(pending['vga_gain'])
        if 'amp_enabled' in pending:
            self._hackrf.amplifier_on = bool(pending['amp_enabled'])

    def _apply_params_to_device(self):
        self._hackrf.center_freq = int(
            self.get_parameter('center_frequency').value)
        self._hackrf.sample_rate = int(
            self.get_parameter('sample_rate').value)
        self._hackrf.lna_gain = int(
            self.get_parameter('lna_gain').value)
        self._hackrf.vga_gain = int(
            self.get_parameter('vga_gain').value)
        self._hackrf.amplifier_on = bool(
            self.get_parameter('amp_enabled').value)

    # ------------------------------------------------------------------
    # IQ capture and PSD publishing
    # ------------------------------------------------------------------

    def _rx_callback(self, data: bytes) -> bool:
        """USB thread callback — must not raise (pyhackrf2 C thread). (REL-03)"""
        try:
            self._last_rx_time = time.monotonic()
            chunk = bytes(data)

            # ADC clipping detection — discard frames with >0.5% at rails
            arr = np.frombuffer(chunk, dtype=np.int8)
            if np.count_nonzero(np.abs(arr) >= 127) > len(arr) * ADC_CLIP_THRESHOLD:
                self._clip_count += 1
                return False  # drop clipped frame

            try:
                self._iq_queue.put_nowait(chunk)
            except queue.Full:
                try:
                    self._iq_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._iq_queue.put_nowait(chunk)
                except queue.Full:
                    pass
                self._rx_overflow_count += 1

            # Recorder fan-out (D-09, D-10): put_nowait; drop-oldest on full
            rq = self._recorder_q  # snapshot ref under CPython GIL
            if rq is not None:
                try:
                    rq.put_nowait(chunk)
                except queue.Full:
                    try:
                        rq.get_nowait()  # drop oldest
                    except queue.Empty:
                        pass
                    try:
                        rq.put_nowait(chunk)
                    except queue.Full:
                        pass
                    self._recorder_q_drops += 1
                    self.get_logger().warning(
                        'Recorder queue full — dropped oldest IQ chunk')

            return False
        except (RuntimeError, OSError):
            self._handle_usb_fault('rx_callback')
            return False

    def _flush_queue(self):
        while not self._iq_queue.empty():
            try:
                self._iq_queue.get_nowait()
            except queue.Empty:
                break

    def _handle_usb_fault(self, source: str) -> None:
        """Record USB disconnect event and stop streaming state (REL-03).

        Called from _rx_callback USB thread or _param_worker thread.
        Sets _usb_fault so diagnostics escalates to WARN then ERROR.
        """
        self._usb_fault = True
        self._usb_fault_time = time.monotonic()
        self._is_streaming = False
        self.get_logger().error(
            f'USB fault detected in {source} — HackRF may be disconnected')

    def _agc_tick(self) -> None:
        """Check ADC clip rate and reduce gain if needed (REL-01).

        Called once per PSD publish cycle. Uses a clip-count delta over
        AGC_CLIP_WINDOW cycles. Hysteresis: AGC_HOLD_FRAMES cycles between
        consecutive reductions.
        """
        self._agc_cycle_counter += 1

        if self._agc_hold_counter > 0:
            self._agc_hold_counter -= 1
            self._agc_clip_snapshot = self._clip_count  # keep snapshot fresh
            return

        clips_in_window = self._clip_count - self._agc_clip_snapshot
        self._agc_clip_snapshot = self._clip_count

        if clips_in_window < AGC_CLIP_WINDOW:
            return   # clip rate acceptable

        # Reduce LNA first, then VGA
        lna = int(self.get_parameter('lna_gain').value)
        vga = int(self.get_parameter('vga_gain').value)

        if lna > 0:
            new_lna = max(0, lna - AGC_LNA_STEP)
            self.set_parameters([Parameter('lna_gain', value=new_lna)])
            with self._device_lock:
                if self._hackrf is not None:
                    self._hackrf.lna_gain = new_lna
            self._agc_last_action = (
                f'lna {lna}->{new_lna} @ cycle {self._agc_cycle_counter}')
            self.get_logger().info(f'AGC: {self._agc_last_action}')
        elif vga > 0:
            new_vga = max(0, vga - AGC_VGA_STEP)
            self.set_parameters([Parameter('vga_gain', value=new_vga)])
            with self._device_lock:
                if self._hackrf is not None:
                    self._hackrf.vga_gain = new_vga
            self._agc_last_action = (
                f'vga {vga}->{new_vga} @ cycle {self._agc_cycle_counter}')
            self.get_logger().info(f'AGC: {self._agc_last_action}')
        else:
            self._agc_last_action = 'at_minimum_gains'
            return   # nothing to reduce

        self._agc_hold_counter = AGC_HOLD_FRAMES

    def _fft_frame(self, iq: np.ndarray, sample_rate: float) -> np.ndarray:
        """Compute single-frame PSD (linear power) with corrections.

        Applies DC offset removal, I/Q imbalance correction, windowing,
        and proper PSD normalization (V^2/Hz).
        """
        # DC offset removal
        iq = iq - np.mean(iq)

        # I/Q imbalance correction (first-order)
        I = iq.real
        Q = iq.imag
        I_pow = np.mean(I ** 2)
        Q_pow = np.mean(Q ** 2)
        if I_pow > 0 and Q_pow > 0:
            alpha = np.sqrt(Q_pow / I_pow)
            phi = np.mean(I * Q) / np.sqrt(I_pow * Q_pow)
            Q = (Q - phi * I) / alpha
            iq = I + 1j * Q

        windowed = iq * self._window
        spectrum = scipy.fft.fftshift(scipy.fft.fft(windowed))
        # PSD: |X[k]|^2 / (Fs * S2) — units V^2/Hz
        return np.abs(spectrum) ** 2 / (sample_rate * self._window_S2)

    def _process_raw(self, raw: bytes, accum: np.ndarray,
                     sample_rate: float) -> int:
        """Process one raw IQ chunk into linear PSD accumulator.

        Uses 50% overlapping FFT frames. Returns number of frames added.
        """
        samples = np.frombuffer(raw, dtype=np.int8).astype(np.float32)
        samples *= (1.0 / 128.0)
        n = FFT_SIZE * 2  # I and Q interleaved
        step = n // 2     # 50% overlap
        count = 0
        for i in range(0, len(samples) - n + 1, step):
            frame = samples[i:i + n]
            iq = frame[0::2] + 1j * frame[1::2]
            accum += self._fft_frame(iq, sample_rate)
            count += 1
        return count

    def _compute_psd_from_queue(self, averaging):
        """Drain IQ queue and return averaged PSD in dB. Blocks until enough."""
        sample_rate = self.get_parameter('sample_rate').value
        accum = np.zeros(FFT_SIZE)
        count = 0
        deadline = time.monotonic() + 5.0
        while count < averaging and time.monotonic() < deadline:
            try:
                raw = self._iq_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            count += self._process_raw(raw, accum, sample_rate)
        if count == 0:
            return None
        psd_linear = accum / count
        return 10.0 * np.log10(np.maximum(psd_linear, 1e-20))

    def _process_and_publish(self):
        """Timer callback: drain IQ, compute FFT, publish averaged PSD."""
        if not self._sweep_lock.acquire(blocking=False):
            return
        try:
            self._process_and_publish_inner()
        finally:
            self._sweep_lock.release()

    def _process_and_publish_inner(self):
        sample_rate = self.get_parameter('sample_rate').value
        while True:
            try:
                raw = self._iq_queue.get_nowait()
            except queue.Empty:
                break
            self._accum_count += self._process_raw(
                raw, self._psd_accum, sample_rate)

        if self._accum_count < PSD_AVERAGING:
            return

        psd_linear = self._psd_accum / self._accum_count
        psd_db = 10.0 * np.log10(np.maximum(psd_linear, 1e-20))
        self._psd_accum[:] = 0
        self._accum_count = 0

        msg = SpectrumStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self.get_parameter('antenna_frame').value
        msg.center_frequency_hz = float(self.get_parameter('center_frequency').value)
        msg.sample_rate_hz = float(sample_rate)
        msg.bin_width_hz = float(sample_rate) / float(FFT_SIZE)
        msg.fft_size = FFT_SIZE
        msg.psd_db = psd_db.tolist()
        msg.noise_floor_db = float(np.median(psd_db))
        self._spectrum_pub.publish(msg)

        self._agc_tick()

    # ------------------------------------------------------------------
    # Sweep service
    # ------------------------------------------------------------------

    def _handle_sweep(self, request, response):
        """Sweep freq_min..freq_max, aggregate PSD, return full scan."""
        freq_min = request.freq_min
        freq_max = request.freq_max
        averaging = max(request.averaging, 1)

        if freq_max <= freq_min:
            response.success = False
            response.message = 'freq_max must be > freq_min'
            return response

        if not self._is_streaming or self._hackrf is None:
            response.success = False
            response.message = 'Node not active / no device'
            return response

        sample_rate = self.get_parameter('sample_rate').value
        saved_center = self.get_parameter('center_frequency').value

        # Plan hops with 80% usable BW
        usable_bw = sample_rate * USABLE_BW_FRACTION
        hops = []
        f = freq_min + sample_rate / 2
        while f - sample_rate / 2 < freq_max:
            hops.append(f)
            f += usable_bw
        if hops:
            last = freq_max - sample_rate / 2
            if last > hops[-1]:
                hops.append(last)
            else:
                hops[-1] = max(last, hops[-1])

        if not hops:
            response.success = False
            response.message = 'Band too narrow for current sample_rate'
            return response

        # Composite grid — arange for exact bin spacing
        bin_width = sample_rate / FFT_SIZE
        comp_freqs = np.arange(freq_min, freq_max, bin_width)
        n_bins = len(comp_freqs)
        comp_linear = np.zeros(n_bins)   # accumulate linear power
        comp_weight = np.zeros(n_bins)   # blend weights

        self.get_logger().info(
            f'Sweep: {freq_min/1e6:.1f}-{freq_max/1e6:.1f} MHz, '
            f'{len(hops)} hops, avg={averaging}')

        with self._sweep_lock:
            for hop_idx, center in enumerate(hops):
                # Settle sequence: flush, retune, PLL lock, flush again
                self._flush_queue()
                with self._device_lock:
                    if self._hackrf is not None:
                        self._hackrf.center_freq = int(center)
                time.sleep(0.005)       # PLL lock ~1-5 ms
                self._flush_queue()     # discard transition data
                time.sleep(0.02)        # buffer clean data

                # Collect PSD for this hop (returned in dB)
                psd_db = self._compute_psd_from_queue(averaging)
                if psd_db is None:
                    self.get_logger().warning(
                        f'Hop {hop_idx} ({center/1e6:.1f} MHz): no data')
                    continue

                # Convert to linear for blending
                psd_lin = 10.0 ** (psd_db / 10.0)

                # Trim edges (10% per side)
                trimmed = psd_lin[EDGE_TRIM:-EDGE_TRIM]
                hop_freqs = np.linspace(
                    center - sample_rate / 2,
                    center + sample_rate / 2,
                    FFT_SIZE)
                trimmed_freqs = hop_freqs[EDGE_TRIM:-EDGE_TRIM]

                # Tukey-shaped blend weights (taper at edges)
                blend = np.ones(len(trimmed))
                taper_len = max(int(len(blend) * 0.1), 1)
                blend[:taper_len] = np.linspace(0, 1, taper_len)
                blend[-taper_len:] = np.linspace(1, 0, taper_len)

                # Map into composite grid with weighted blending
                for j in range(len(trimmed)):
                    idx = int((trimmed_freqs[j] - freq_min) / bin_width)
                    if 0 <= idx < n_bins:
                        comp_linear[idx] += trimmed[j] * blend[j]
                        comp_weight[idx] += blend[j]

            # Restore original frequency
            with self._device_lock:
                if self._hackrf is not None:
                    self._hackrf.center_freq = int(saved_center)
            self._flush_queue()

        # Finalize: weighted average in linear, convert to dB
        valid = comp_weight > 0
        comp_linear[valid] /= comp_weight[valid]
        comp_psd_db = np.full(n_bins, -100.0)
        comp_psd_db[valid] = 10.0 * np.log10(
            np.maximum(comp_linear[valid], 1e-20))

        response.success = True
        response.message = (
            f'{len(hops)} hops, {n_bins} bins, '
            f'peak={comp_psd_db.max():.1f} dB')
        response.freq_min = freq_min
        response.freq_max = freq_max
        response.bin_width = bin_width
        response.psd_db = comp_psd_db.tolist()

        peak_idx = np.argmax(comp_psd_db)
        self.get_logger().info(
            f'Sweep done: {n_bins} bins, '
            f'peak={comp_psd_db.max():.1f} dB @ '
            f'{comp_freqs[peak_idx]/1e6:.1f} MHz')

        return response

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def _diagnostics_callback(self, stat):
        now = time.monotonic()
        # USB disconnect escalation (REL-03)
        if self._usb_fault:
            fault_age = now - self._usb_fault_time
            if fault_age < DISCONNECT_ERROR_TIMEOUT:
                stat.summary(DiagnosticStatus.WARN, 'USB disconnect detected')
            else:
                stat.summary(DiagnosticStatus.ERROR, 'USB device lost')
            stat.add('usb_fault', 'true')
            stat.add('usb_fault_age_s', f'{fault_age:.1f}')
            return stat

        # Normal status path
        connected = self._hackrf is not None
        streaming = self._is_streaming
        if not connected:
            stat.summary(DiagnosticStatus.ERROR, 'Device not connected')
        elif not streaming:
            stat.summary(DiagnosticStatus.WARN, 'Not streaming')
        elif (now - self._last_rx_time) > 10.0:
            stat.summary(DiagnosticStatus.WARN, 'IQ stall detected')
        else:
            stat.summary(DiagnosticStatus.OK, 'Streaming')
        stat.add('connected', str(connected).lower())
        stat.add('streaming', str(streaming).lower())
        if self._hackrf:
            stat.add('center_frequency',
                     str(self.get_parameter('center_frequency').value))
            stat.add('sample_rate',
                     str(self.get_parameter('sample_rate').value))
        stat.add('rx_overflows', str(self._rx_overflow_count))
        stat.add('adc_clips', str(self._clip_count))
        stat.add('usb_fault', str(self._usb_fault).lower())
        stat.add('agc_last_action', self._agc_last_action)
        if self._activate_time is not None:
            stat.add('uptime_s', f'{now - self._activate_time:.1f}')
        return stat

    def _handle_recording_start(self, request, response):
        """Activate recorder fan-out queue (per D-10)."""
        if self._recorder_q is None:
            self._recorder_q = queue.Queue(maxsize=256)
            self._recorder_q_drops = 0
            self.get_logger().info('Recorder queue activated.')
        response.success = True
        response.message = 'Recording started'
        return response

    def _handle_recording_stop(self, request, response):
        """Deactivate recorder fan-out queue (per D-10)."""
        self._recorder_q = None
        response.success = True
        response.message = 'Recording stopped'
        return response

    def _close_device(self):
        with self._device_lock:
            if self._hackrf is not None:
                try:
                    self._hackrf.close()
                except Exception as e:
                    self.get_logger().warning(f'Device close error: {e}')
                self._hackrf = None


def main(args=None):
    rclpy.init(args=args)
    node = HackRFLifecycleNode()
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
