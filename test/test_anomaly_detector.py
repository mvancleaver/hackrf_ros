"""Tests for AnomalyDetector (ADV-01).

Tests for EMA anomaly detection baseline learning, warmup suppression,
dual trigger (power spike + idle-band new emitter), and diagnostics integration.
Run: pytest test/test_anomaly_detector.py -v
"""
from __future__ import annotations
import sys
import types
import time
import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Minimal ROS2 + dependency stubs so cfar_node can be imported without rclpy
# ---------------------------------------------------------------------------

# rclpy core
_rclpy = types.ModuleType('rclpy')
_rclpy.init = lambda args=None: None
_rclpy.spin = lambda node: None
_rclpy.try_shutdown = lambda: None
_rclpy_node = types.ModuleType('rclpy.node')
_rclpy_node.Node = object
_rclpy_qos = types.ModuleType('rclpy.qos')
_rclpy_qos.QoSProfile = object
_rclpy_qos.ReliabilityPolicy = type(
    'ReliabilityPolicy', (), {'RELIABLE': 'RELIABLE', 'BEST_EFFORT': 'BEST_EFFORT'})()
_rclpy_qos.HistoryPolicy = type(
    'HistoryPolicy', (), {'KEEP_LAST': 'KEEP_LAST'})()
_rclpy_cb = types.ModuleType('rclpy.callback_groups')
_rclpy_cb.ReentrantCallbackGroup = object

sys.modules.setdefault('rclpy', _rclpy)
sys.modules.setdefault('rclpy.node', _rclpy_node)
sys.modules.setdefault('rclpy.qos', _rclpy_qos)
sys.modules.setdefault('rclpy.callback_groups', _rclpy_cb)

# diagnostic_updater stub
_diag_upd = types.ModuleType('diagnostic_updater')


class _DiagStatusWrapper:
    """Minimal stub for DiagnosticStatusWrapper."""

    OK = 0
    WARN = 1
    ERROR = 2

    def summary(self, level, msg):
        self._level = level
        self._msg = msg


class _Updater:
    """Minimal stub for diagnostic_updater.Updater."""

    def __init__(self, node=None):
        pass

    def setHardwareID(self, hw_id):
        pass

    def add(self, name, callback):
        pass


_diag_upd.Updater = _Updater
_diag_upd.DiagnosticStatusWrapper = _DiagStatusWrapper
sys.modules.setdefault('diagnostic_updater', _diag_upd)

# hackrf_interfaces stubs
_hi = types.ModuleType('hackrf_interfaces')
_hi_msg = types.ModuleType('hackrf_interfaces.msg')


class _RFDetection:
    center_frequency_hz: float = 0.0
    bandwidth_hz: float = 0.0
    power_dbm: float = 0.0
    snr_db: float = 0.0
    classification: str = ''
    persistence_frames: int = 0
    detection_id: int = 0
    is_anomaly: bool = False
    anomaly_type: str = ''
    cyclo_classification: str = ''
    cyclo_confidence: float = 0.0


_hi_msg.SpectrumStamped = object
_hi_msg.RFDetection = _RFDetection
_hi_msg.RFDetectionArray = object
_hi_msg.RFEnvironment = object
sys.modules.setdefault('hackrf_interfaces', _hi)
sys.modules.setdefault('hackrf_interfaces.msg', _hi_msg)

# ---------------------------------------------------------------------------
# Now import the module under test
# ---------------------------------------------------------------------------

# AnomalyDetector lives in hackrf_ros.cfar_node
from hackrf_ros.cfar_node import AnomalyDetector  # noqa: E402

N_BINS = 4096
ALPHA = 0.05
WARMUP_S = 30.0


@pytest.fixture
def detector():
    return AnomalyDetector(n_bins=N_BINS, alpha=ALPHA, warmup_s=WARMUP_S)


@pytest.fixture
def seeded_detector():
    """AnomalyDetector that has completed warmup via time-travel."""
    det = AnomalyDetector(n_bins=N_BINS, alpha=ALPHA, warmup_s=0.0)
    baseline = np.full(N_BINS, -80.0, dtype=np.float32)
    # Feed one frame to trigger baseline seeding
    det.update(baseline)
    return det


# ---------------------------------------------------------------------------
# Diagnostics integration smoke test (ADV-01, D-04)
# ---------------------------------------------------------------------------

def test_cfar_node_has_anomaly_diagnostics():
    """CFARNode must define _anomaly_diagnostics method (diagnostics integration check)."""
    from hackrf_ros.cfar_node import CFARNode
    assert hasattr(CFARNode, '_anomaly_diagnostics'), \
        "CFARNode must define _anomaly_diagnostics for /diagnostics integration"


class TestAnomalyCountIncrements:
    def test_anomaly_count_increments_on_spike(self, seeded_detector):
        """anomaly_count increments when a power spike is detected."""
        initial_count = seeded_detector.anomaly_count
        psd = np.full(N_BINS, -80.0, dtype=np.float32)
        psd[100] = -60.0  # 20 dB above -80 dBm baseline — triggers spike
        seeded_detector.update(psd)
        assert seeded_detector.anomaly_count > initial_count, \
            "anomaly_count must increment when a power spike fires"


class TestWarmupSuppression:
    def test_no_anomaly_during_warmup(self, detector):
        """During warmup, update() returns all-False masks."""
        huge_spike = np.full(N_BINS, 0.0, dtype=np.float32)  # very high power
        spike_mask, new_emitter_mask = detector.update(huge_spike)
        assert not np.any(spike_mask), "spike_mask must be all-False during warmup"
        assert not np.any(new_emitter_mask), "new_emitter_mask must be all-False during warmup"

    def test_warmup_accumulates_frames(self, detector):
        """Frames are accumulated during warmup for median seeding."""
        psd = np.full(N_BINS, -80.0, dtype=np.float32)
        for _ in range(10):
            detector.update(psd)
        assert len(detector._warmup_accum) == 10

    def test_baseline_none_during_warmup(self, detector):
        """Baseline remains None until warmup expires."""
        psd = np.full(N_BINS, -80.0, dtype=np.float32)
        detector.update(psd)
        assert detector._baseline is None


class TestBaselineSeeding:
    def test_baseline_seeded_from_median(self):
        """After warmup, baseline equals median of accumulated frames.

        Strategy: construct with warmup_s=30.0 to accumulate frames, then
        time-travel by resetting _start_time to the past to force warmup
        expiry, then call update() once more to trigger seeding from the
        accumulated median.
        """
        det = AnomalyDetector(n_bins=8, alpha=0.0, warmup_s=30.0)
        # Feed frames: alternate between -70 and -90 dB, median = -80 dB
        for val in [-70.0, -90.0, -70.0, -90.0, -70.0]:
            det.update(np.full(8, val, dtype=np.float32))
        # Verify frames accumulated (warmup still active)
        assert len(det._warmup_accum) == 5, "frames must accumulate during warmup"
        # Time-travel: force warmup to have expired
        det._start_time = time.monotonic() - 60.0
        # Next update() triggers seeding from the 5 accumulated frames
        # median([-70, -90, -70, -90, -70]) per bin = -70.0 (three -70 vs two -90)
        det.update(np.full(8, -80.0, dtype=np.float32))
        assert det._baseline is not None, "baseline must be set after warmup expiry"
        # median of [-70, -90, -70, -90, -70] = -70.0 (middle value when sorted)
        # sorted: [-90, -90, -70, -70, -70] -> median = -70.0
        np.testing.assert_allclose(det._baseline, -70.0, atol=1.0)

    def test_warmup_accum_cleared_after_seeding(self):
        """Warmup accumulation list is cleared after baseline is seeded."""
        det = AnomalyDetector(n_bins=8, alpha=0.05, warmup_s=30.0)
        for _ in range(5):
            det.update(np.full(8, -80.0, dtype=np.float32))
        # Time-travel to force warmup expiry
        det._start_time = time.monotonic() - 60.0
        # Trigger seeding
        det.update(np.full(8, -80.0, dtype=np.float32))
        assert len(det._warmup_accum) == 0


class TestPowerSpikeDetection:
    def test_power_spike_detected(self, seeded_detector):
        """Bin +15 dB above baseline triggers spike_mask."""
        # seeded_detector baseline is -80 dBm; spike threshold is +10 dB = -70 dBm
        spike_psd = np.full(N_BINS, -80.0, dtype=np.float32)
        spike_psd[100] = -60.0  # 20 dB above baseline: triggers
        spike_mask, _ = seeded_detector.update(spike_psd)
        assert spike_mask[100], "Bin 100 should be flagged as power spike"

    def test_below_threshold_not_flagged(self, seeded_detector):
        """Bin +5 dB above baseline does NOT trigger spike_mask."""
        psd = np.full(N_BINS, -80.0, dtype=np.float32)
        psd[200] = -76.0  # only 4 dB above baseline: below threshold
        spike_mask, _ = seeded_detector.update(psd)
        assert not spike_mask[200], "Bin 200 should NOT be flagged (below threshold)"

    def test_spike_mask_is_numpy_bool_array(self, seeded_detector):
        psd = np.full(N_BINS, -80.0, dtype=np.float32)
        spike_mask, new_emitter_mask = seeded_detector.update(psd)
        assert spike_mask.dtype == bool
        assert new_emitter_mask.dtype == bool
        assert len(spike_mask) == N_BINS
        assert len(new_emitter_mask) == N_BINS


class TestIdleBandNewEmitter:
    def test_new_emitter_in_idle_band(self, seeded_detector):
        """First spike in bin that was never detected triggers new_emitter_mask."""
        psd = np.full(N_BINS, -80.0, dtype=np.float32)
        psd[500] = -55.0  # 25 dB above baseline: triggers both spike and new_emitter
        spike_mask, new_emitter_mask = seeded_detector.update(psd)
        assert spike_mask[500], "spike_mask must fire"
        assert new_emitter_mask[500], "new_emitter_mask must fire for new emitter"

    def test_repeat_spike_not_new_emitter(self, seeded_detector):
        """Second spike in same bin triggers only spike_mask, not new_emitter_mask."""
        psd = np.full(N_BINS, -80.0, dtype=np.float32)
        psd[500] = -55.0
        # First update: new emitter
        seeded_detector.update(psd)
        # Second update: still spiking, not new emitter
        spike_mask, new_emitter_mask = seeded_detector.update(psd)
        assert spike_mask[500], "spike_mask should still fire"
        assert not new_emitter_mask[500], "new_emitter_mask must NOT fire on repeat"

    def test_quiet_bin_then_spike_is_new_emitter(self, seeded_detector):
        """A quiet bin that then receives a spike is correctly classified as new emitter."""
        psd_quiet = np.full(N_BINS, -80.0, dtype=np.float32)
        for _ in range(5):
            seeded_detector.update(psd_quiet)

        psd_spike = psd_quiet.copy()
        psd_spike[300] = -55.0
        spike_mask, new_emitter_mask = seeded_detector.update(psd_spike)
        assert new_emitter_mask[300], "quiet->spike must be new_emitter"


class TestEMAUpdate:
    def test_ema_converges_toward_input(self):
        """EMA baseline moves toward new input by alpha fraction each frame."""
        det = AnomalyDetector(n_bins=8, alpha=0.1, warmup_s=0.0)
        baseline_val = -80.0
        for _ in range(5):
            det.update(np.full(8, baseline_val, dtype=np.float32))
        # Trigger seeding
        det.update(np.full(8, baseline_val, dtype=np.float32))

        new_val = np.full(8, -60.0, dtype=np.float32)
        det.update(new_val)
        # After one EMA step: baseline = 0.9 * -80 + 0.1 * -60 = -78
        expected = 0.9 * baseline_val + 0.1 * (-60.0)
        np.testing.assert_allclose(det._baseline, expected, atol=0.1)
