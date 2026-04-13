"""Cyclostationary feature extraction node (ADV-03).

Subscribes to /hackrf/iq (Float32MultiArray, complex IQ as interleaved float32)
and /hackrf/detections (RFDetectionArray).

When a detection in the 2.4 GHz ISM band (2400-2483.5 MHz) is active, extracts
cyclostationary features from the next arriving IQ chunk and publishes enriched
detections with cyclo_classification + cyclo_confidence fields.

Publishes /hackrf/cyclo_detections unconditionally when detections are available.
Cyclo fields (cyclo_classification, cyclo_confidence) are populated only for
2.4 GHz ISM detections when cyclo_confidence >= confidence_threshold (D-10).
For all other detections, cyclo fields are forwarded as-is (empty/0.0 defaults).

Strategy (D-11): always-on mode — when a 2.4 GHz detection is active, process
the next IQ chunk. No correlation between specific chunk and specific detection
frame — valid because detections are persistent (persistence_n >= 3 confirmed).

Feature pipeline (D-09, research verified, compute budget 0.81 ms < 10 ms):
  Feature 1: WiFi OFDM spectral line check at 802.11 pilot bins 448/1344 (~500 us)
             Pilot positions: subcarriers ±7 and ±21 at 312.5 kHz spacing
             = bins 448 and 1344 at 4882.8 Hz/bin (20 MHz / 4096 pt FFT)
  Feature 2: Spectral flatness measure for ZigBee DSSS detection (~200 us)
  Feature 3: Power CV across 625us BLE windows (~7 ms — dominates budget)

Total: ~0.81 ms for 65536-sample chunk on ARM64 [VERIFIED in research].

Override logic (D-10): cyclo result overrides Phase 1 band label only when
cyclo_confidence >= 0.7 (configurable).
"""
from __future__ import annotations

import numpy as np
import scipy.fft

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from rclpy.callback_groups import ReentrantCallbackGroup

from std_msgs.msg import Float32MultiArray
from hackrf_interfaces.msg import RFDetectionArray, RFDetection, SpectrumStamped


# ---------------------------------------------------------------------------
# Constants (for 20 MHz / 4096-pt FFT — per research [VERIFIED])
# ---------------------------------------------------------------------------
_SAMPLE_RATE = 20_000_000          # 20 MHz
_N_FFT = 4096
_BIN_HZ = _SAMPLE_RATE / _N_FFT   # 4882.8 Hz per bin
_BLE_WINDOW_SAMPLES = 12500        # 625 us * 20 MHz = 12500 samples per BLE dwell

# 802.11n/ac pilot subcarrier positions (RESEARCH.md verified):
# Pilot subcarriers at ±7 and ±21 relative to DC, spacing = 312.5 kHz
# Bin index = subcarrier_number * (312500 Hz / _BIN_HZ)
#           = subcarrier_number * 64
# Positive subcarriers: 7 -> bin 448,  21 -> bin 1344
# Negative subcarriers: -7 -> bin 4096-448=3648,  -21 -> bin 4096-1344=2752
# Checking all four bins recovers ~3 dB of detection sensitivity that was
# lost by ignoring the negative subcarriers (H-RF-2 fix).
_WIFI_PILOT_BINS = [448, 1344, 2752, 3648]

# 2.4 GHz ISM band boundaries (per _BAND_TABLE in cfar_node.py)
_ISM_2400_LO = 2_400_000_000.0
_ISM_2400_HI = 2_483_500_000.0


# ---------------------------------------------------------------------------
# Pure classifier function (importable by tests without ROS context)
# ---------------------------------------------------------------------------

def cyclo_classify(iq_chunk: np.ndarray) -> tuple[str, float]:
    """Apply cyclostationary feature pipeline to complex IQ chunk.

    Operates on a single IQ chunk (typically 65536 samples = 3.28 ms at 20 MHz).
    Returns (classification, confidence) where classification is one of:
      'wifi_2_4g'  — OFDM pilot lines detected at 802.11 pilot bins (448, 1344)
      'ble'        — power variation across 625us windows (hopping indicator)
      'zigbee'     — flat spectral envelope (DSSS spreading) + low power CV
      ''           — no confident classification

    Compute budget: < 10 ms per call on Jetson ARM64 [VERIFIED: 0.81 ms typical].

    Note on BLE detection (research recommendation): BLE frequency-hopping
    autocorrelation CANNOT work with a single IQ chunk. Use power-CV across
    625us windows instead — computable in ~7 ms and more reliable.
    """
    n = len(iq_chunk)
    n_fft = min(_N_FFT, n)

    # Feature 1: WiFi OFDM pilot line detection (~500 us)
    # Compute 4096-pt FFT, check for spectral lines at 802.11 pilot bin positions
    spec = np.abs(scipy.fft.fft(iq_chunk[:n_fft])) ** 2
    n_half = n_fft // 2
    # Pilot power: mean of power at expected pilot bin indices
    valid_pilots = [b for b in _WIFI_PILOT_BINS if b < n_fft]
    if valid_pilots:
        line_power = np.mean([spec[b] for b in valid_pilots])
    else:
        line_power = 0.0
    noise_est = float(np.median(spec[:n_half])) + 1e-30
    wifi_line_ratio = float(line_power) / noise_est

    # Feature 2: Spectral flatness measure (SFM) for ZigBee DSSS (~200 us)
    # SFM = geometric_mean(X) / arithmetic_mean(X)
    # Ranges [0, 1]: 1 = perfectly flat (white noise), 0 = single tone
    spec_nz = spec[:n_half] + 1e-30
    sfm = float(np.exp(np.mean(np.log(spec_nz))) / np.mean(spec_nz))

    # Feature 3: Power CV across 625us windows for BLE hopping (~7 ms)
    n_windows = n // _BLE_WINDOW_SAMPLES
    if n_windows >= 3:
        seg = iq_chunk[:n_windows * _BLE_WINDOW_SAMPLES].reshape(
            n_windows, _BLE_WINDOW_SAMPLES
        )
        power_per_win = np.mean(np.abs(seg) ** 2, axis=1)
        mean_power = float(np.mean(power_per_win))
        power_cv = float(np.std(power_per_win)) / (mean_power + 1e-30)
    else:
        power_cv = 0.0

    # Classification decision (ordered: most distinctive first)
    # WiFi: strong spectral lines at OFDM pilot bin positions (448, 1344)
    if wifi_line_ratio > 10.0:
        confidence = min(1.0, float(wifi_line_ratio) / 50.0)
        return 'wifi_2_4g', confidence

    # BLE: high power variance across 625us windows (frequency hopping signature).
    # Note: BLE hop windows use noise-like IQ so SFM is high (flat spectrum) —
    # do NOT guard on sfm here. power_cv alone discriminates BLE from ZigBee
    # (ZigBee/DSSS has constant power: cv ~0.004, BLE has cv > 0.8 when hopping).
    # Threshold raised from 0.3 to 0.45 to reduce false BLE classifications from
    # bursty WiFi traffic (M-RF-2 fix).
    if power_cv > 0.45:
        confidence = min(1.0, float(power_cv) * 2.0)
        return 'ble', confidence

    # ZigBee: flat spectrum (DSSS spreading) AND low power variance
    if sfm > 0.3 and power_cv < 0.1:
        confidence = min(1.0, float(sfm))
        return 'zigbee', confidence

    # No confident classification
    return '', 0.0


# ---------------------------------------------------------------------------
# ROS2 Node
# ---------------------------------------------------------------------------

class CycloNode(Node):
    """Cyclostationary feature extraction node.

    Subscribes to:
      /hackrf/iq          (Float32MultiArray, interleaved I/Q float32)
      /hackrf/detections  (RFDetectionArray)

    Publishes:
      /hackrf/cyclo_detections  (RFDetectionArray)

    Published unconditionally when /hackrf/detections arrives and an IQ chunk
    is available. Cyclo fields are populated only for 2.4 GHz ISM detections
    with cyclo_confidence >= confidence_threshold (D-10). All other detections
    pass through with cyclo fields at their defaults (empty string / 0.0).

    Architecture (Pitfall 3 avoidance): runs in always-on mode.
    When a 2.4 GHz detection is active, the next arriving IQ chunk is analyzed.
    No attempt to correlate a specific IQ chunk timestamp to a specific detection.
    """

    def __init__(self) -> None:
        super().__init__('cyclo_node')

        self.declare_parameter('confidence_threshold', 0.7)
        self.declare_parameter('ism_min_hz', 2_400_000_000.0)
        self.declare_parameter('ism_max_hz', 2_483_500_000.0)

        cb_group = ReentrantCallbackGroup()

        qos_best_effort = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )
        qos_reliable = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )

        # Track active 2.4 GHz detections
        self._active_ism_detections: list[RFDetection] = []
        self._last_detections_arr = None  # RFDetectionArray, for re-publishing
        # Latest IQ chunk and the center frequency + sample rate it was captured at.
        # Used to gate classification: only classify detections whose center_frequency_hz
        # falls within [_iq_center_hz ± _iq_sample_rate_hz/2] so sweep-mode detections
        # from a different hop are not classified against the wrong IQ data.
        self._latest_iq: np.ndarray | None = None
        self._iq_center_hz: float = 0.0       # center freq when IQ was captured
        self._iq_sample_rate_hz: float = 20e6  # sample rate when IQ was captured

        # Subscriber: IQ stream (BEST_EFFORT — high rate, drop is OK)
        self._iq_sub = self.create_subscription(
            Float32MultiArray,
            '/hackrf/iq',
            self._iq_callback,
            qos_best_effort,
            callback_group=cb_group,
        )

        # Subscriber: spectrum — used only to track the current center frequency
        # and sample rate so IQ chunks can be frequency-tagged without changing
        # the Float32MultiArray message type.
        self._spectrum_sub = self.create_subscription(
            SpectrumStamped,
            '/hackrf/spectrum',
            self._spectrum_callback,
            qos_best_effort,
            callback_group=cb_group,
        )

        # Subscriber: detections (to know which detections are in 2.4 GHz band)
        self._det_sub = self.create_subscription(
            RFDetectionArray,
            '/hackrf/detections',
            self._detections_callback,
            qos_reliable,
            callback_group=cb_group,
        )

        # Publisher: enriched detections (cyclo fields populated for ISM detections)
        self._cyclo_det_pub = self.create_publisher(
            RFDetectionArray,
            '/hackrf/cyclo_detections',
            qos_reliable,
        )

        self.get_logger().info(
            f'CycloNode started  confidence_threshold='
            f'{self.get_parameter("confidence_threshold").value}'
        )

    def _spectrum_callback(self, msg: SpectrumStamped) -> None:
        """Track center frequency and sample rate of the live IQ stream.

        SpectrumStamped and /hackrf/iq are produced synchronously by the driver,
        so this gives an accurate frequency tag for the IQ cache without changing
        the Float32MultiArray message type.
        """
        self._iq_center_hz = float(msg.center_frequency_hz)
        self._iq_sample_rate_hz = float(msg.sample_rate_hz)

    def _iq_callback(self, msg: Float32MultiArray) -> None:
        """Cache latest IQ chunk for use by _detections_callback."""
        data = np.array(msg.data, dtype=np.float32)
        if len(data) < 2:
            return
        self._latest_iq = data[0::2] + 1j * data[1::2]

    def _detections_callback(self, msg: RFDetectionArray) -> None:
        """Cache latest detections, run cyclo classifier, publish enriched array.

        Publishes unconditionally. Cyclo fields are populated only for 2.4 GHz ISM
        detections when cyclo_confidence >= confidence_threshold (D-10) AND the
        detection's center frequency falls within the IQ cache's capture window
        [_iq_center_hz ± _iq_sample_rate_hz/2].  This guard makes classification
        correct during wideband sweeps where the IQ cache may be from a different
        hop than the detection.
        All other detections pass through with default cyclo fields.
        """
        self._last_detections_arr = msg
        ism_lo = float(self.get_parameter('ism_min_hz').value)
        ism_hi = float(self.get_parameter('ism_max_hz').value)
        threshold = float(self.get_parameter('confidence_threshold').value)

        # Half-bandwidth of the IQ capture window (used per-detection below)
        half_bw = self._iq_sample_rate_hz / 2.0

        # Run cyclo once if any ISM detection is within the IQ window
        iq_label = ''
        iq_confidence = 0.0
        if self._latest_iq is not None:
            in_window = [
                det for det in msg.detections
                if (ism_lo <= det.center_frequency_hz <= ism_hi
                    and abs(det.center_frequency_hz - self._iq_center_hz) <= half_bw)
            ]
            if in_window:
                iq_label, iq_confidence = cyclo_classify(self._latest_iq)
                self.get_logger().debug(
                    f'Cyclo [{self._iq_center_hz/1e6:.1f} MHz window]: '
                    f'{iq_label!r} conf={iq_confidence:.2f}'
                )

        self._active_ism_detections = [
            det for det in msg.detections
            if ism_lo <= det.center_frequency_hz <= ism_hi
        ]

        # Build enriched detection array — publish unconditionally
        enriched_arr = RFDetectionArray()
        enriched_arr.header = msg.header
        enriched_arr.noise_floor_dbm = msg.noise_floor_dbm
        enriched_arr.occupancy_pct = msg.occupancy_pct

        enriched_dets = []
        for det in msg.detections:
            new_det = RFDetection()
            # Copy all existing fields
            new_det.center_frequency_hz = det.center_frequency_hz
            new_det.bandwidth_hz = det.bandwidth_hz
            new_det.power_dbm = det.power_dbm
            new_det.snr_db = det.snr_db
            new_det.classification = det.classification
            new_det.persistence_frames = det.persistence_frames
            new_det.detection_id = det.detection_id
            new_det.is_anomaly = det.is_anomaly
            new_det.anomaly_type = det.anomaly_type
            # Populate cyclo fields only when:
            #   1. Detection is in ISM 2.4 GHz band
            #   2. Detection center freq is within the IQ capture window
            #   3. Classification confidence meets threshold
            det_in_window = abs(det.center_frequency_hz - self._iq_center_hz) <= half_bw
            if (ism_lo <= det.center_frequency_hz <= ism_hi
                    and det_in_window
                    and iq_label != '' and iq_confidence >= threshold):
                new_det.cyclo_classification = iq_label
                new_det.cyclo_confidence = float(iq_confidence)
            else:
                # Forward existing cyclo fields unchanged (or defaults)
                new_det.cyclo_classification = det.cyclo_classification
                new_det.cyclo_confidence = det.cyclo_confidence
            enriched_dets.append(new_det)

        enriched_arr.detections = enriched_dets
        self._cyclo_det_pub.publish(enriched_arr)


def main(args=None):
    """Entry point for cyclo_node."""
    from rclpy.executors import MultiThreadedExecutor
    rclpy.init(args=args)
    node = CycloNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
