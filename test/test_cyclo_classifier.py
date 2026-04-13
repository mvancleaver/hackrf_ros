"""Tests for cyclo_classify (ADV-03).

Synthetic IQ generation:
- WiFi: add strong spectral lines at OFDM pilot bin positions 448 and 1344
  (802.11n/ac subcarriers ±7 and ±21 at 312.5 kHz spacing, per RESEARCH.md)
- BLE: amplitude-modulate IQ in 625us blocks (hopping power variance)
- ZigBee: DSSS-like flat spectrum (white noise with narrow bandwidth)
- Unknown: narrowband single tone — falls through all three classifiers
  (not flat-spectrum so not zigbee, not multi-tone OFDM so not wifi,
  constant amplitude so not BLE)

Run: pytest test/test_cyclo_classifier.py -v
"""
from __future__ import annotations
import sys
import types

# ---------------------------------------------------------------------------
# Minimal ROS2 stubs — cyclo_classify is a pure function, no ROS2 runtime
# required. These stubs allow importing hackrf_ros.cyclo_node without rclpy.
# ---------------------------------------------------------------------------
rclpy_mod = types.ModuleType('rclpy')
rclpy_mod.init = lambda args=None: None
rclpy_mod.try_shutdown = lambda: None
rclpy_node_mod = types.ModuleType('rclpy.node')
rclpy_node_mod.Node = object
rclpy_qos_mod = types.ModuleType('rclpy.qos')
rclpy_qos_mod.QoSProfile = object
rclpy_qos_mod.ReliabilityPolicy = type(
    'ReliabilityPolicy', (), {'RELIABLE': 'RELIABLE', 'BEST_EFFORT': 'BEST_EFFORT'})()
rclpy_qos_mod.HistoryPolicy = type(
    'HistoryPolicy', (), {'KEEP_LAST': 'KEEP_LAST'})()
rclpy_cb_mod = types.ModuleType('rclpy.callback_groups')
rclpy_cb_mod.ReentrantCallbackGroup = object
rclpy_exec_mod = types.ModuleType('rclpy.executors')
rclpy_exec_mod.MultiThreadedExecutor = object

sys.modules.setdefault('rclpy', rclpy_mod)
sys.modules.setdefault('rclpy.node', rclpy_node_mod)
sys.modules.setdefault('rclpy.qos', rclpy_qos_mod)
sys.modules.setdefault('rclpy.callback_groups', rclpy_cb_mod)
sys.modules.setdefault('rclpy.executors', rclpy_exec_mod)

# std_msgs stub
std_msgs_mod = types.ModuleType('std_msgs')
std_msgs_msg_mod = types.ModuleType('std_msgs.msg')
std_msgs_msg_mod.Float32MultiArray = object
sys.modules.setdefault('std_msgs', std_msgs_mod)
sys.modules.setdefault('std_msgs.msg', std_msgs_msg_mod)

# hackrf_interfaces stub — RFDetectionArray and RFDetection are not used
# by cyclo_classify (pure function), so minimal stubs suffice.
hackrf_iface_mod = types.ModuleType('hackrf_interfaces')
hackrf_iface_msg_mod = types.ModuleType('hackrf_interfaces.msg')
hackrf_iface_msg_mod.RFDetectionArray = object
hackrf_iface_msg_mod.RFDetection = object
sys.modules.setdefault('hackrf_interfaces', hackrf_iface_mod)
sys.modules.setdefault('hackrf_interfaces.msg', hackrf_iface_msg_mod)

import numpy as np
import pytest

from hackrf_ros.cyclo_node import cyclo_classify

SAMPLE_RATE = 20_000_000  # 20 MHz
N_SAMPLES = 65536
N_FFT = 4096
BIN_HZ = SAMPLE_RATE / N_FFT           # 4882.8 Hz / bin
# 802.11n/ac pilot subcarriers at ±7 and ±21 (subcarrier spacing 312.5 kHz)
# Bin 7 * (312500 / BIN_HZ) = 7 * 64 = 448
# Bin 21 * (312500 / BIN_HZ) = 21 * 64 = 1344
WIFI_PILOT_BIN_7 = 448                 # subcarrier +7
WIFI_PILOT_BIN_21 = 1344               # subcarrier +21
BLE_WINDOW_SAMPLES = 12500             # 625 us * 20 MHz = 12500 samples


def _make_wifi_iq(n: int = N_SAMPLES, snr_db: float = 20.0) -> np.ndarray:
    """Synthetic WiFi IQ: OFDM with strong spectral lines at 802.11 pilot positions.

    Creates tones at bins 448 and 1344 (subcarriers ±7 and ±21 at 312.5 kHz spacing).
    These are the actual 802.11n/ac pilot subcarrier positions per RESEARCH.md.
    """
    rng = np.random.default_rng(42)
    noise_amp = 10 ** (-snr_db / 20.0)
    t = np.arange(n, dtype=np.float64)
    iq = np.zeros(n, dtype=np.complex64)
    for k in [WIFI_PILOT_BIN_7, WIFI_PILOT_BIN_21]:
        freq_hz = k * BIN_HZ
        phase = rng.uniform(0, 2 * np.pi)
        iq += np.exp(1j * (2 * np.pi * freq_hz / SAMPLE_RATE * t + phase)).astype(np.complex64)
    iq += noise_amp * (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64)
    return iq


def _make_ble_iq(n: int = N_SAMPLES) -> np.ndarray:
    """Synthetic BLE IQ: amplitude varies between 625us hop windows.

    Each 12500-sample window has a different power level, simulating frequency hopping.
    """
    rng = np.random.default_rng(7)
    iq = np.zeros(n, dtype=np.complex64)
    n_windows = n // BLE_WINDOW_SAMPLES
    for i in range(n_windows):
        start = i * BLE_WINDOW_SAMPLES
        end = start + BLE_WINDOW_SAMPLES
        # Alternate between high and low power to create high CV
        amp = 2.0 if (i % 2 == 0) else 0.1
        window_noise = rng.standard_normal(BLE_WINDOW_SAMPLES) + 1j * rng.standard_normal(BLE_WINDOW_SAMPLES)
        iq[start:end] = (amp * window_noise).astype(np.complex64)
    return iq


def _make_zigbee_iq(n: int = N_SAMPLES) -> np.ndarray:
    """Synthetic ZigBee IQ: DSSS-like flat spectrum (white noise, narrow BW, constant power).

    Flat spectral envelope (SFM near 1) + constant amplitude (low CV).
    """
    rng = np.random.default_rng(13)
    # Wideband white noise -> flat spectrum, constant amplitude -> low CV
    iq = (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64)
    return iq


def _make_unknown_iq(n: int = N_SAMPLES) -> np.ndarray:
    """Narrowband single tone — falls through all three classifiers.

    Properties:
    - Single tone -> NOT flat spectrum (SFM near 0, peaks at tone bin) -> NOT zigbee
    - No 802.11 pilot-bin tones -> NOT wifi
    - Constant amplitude -> low power_cv -> NOT ble
    Result: cyclo_classify should return '' (no confident classification).
    """
    rng = np.random.default_rng(99)
    t = np.arange(n, dtype=np.float64)
    # Single tone at bin 200 (not a pilot bin: 200 is not 448 or 1344)
    freq_hz = 200 * BIN_HZ
    iq = np.exp(1j * 2 * np.pi * freq_hz / SAMPLE_RATE * t).astype(np.complex64)
    # Add very small noise to avoid exact single-bin spectrum
    iq += 0.01 * (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64)
    return iq


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestReturnContract:
    def test_returns_tuple(self):
        iq = _make_wifi_iq()
        result = cyclo_classify(iq)
        assert isinstance(result, tuple), "Must return a tuple"
        assert len(result) == 2, "Must return 2-tuple (classification, confidence)"

    def test_confidence_in_range(self):
        for iq in [_make_wifi_iq(), _make_ble_iq(), _make_zigbee_iq(), _make_unknown_iq()]:
            label, conf = cyclo_classify(iq)
            assert 0.0 <= conf <= 1.0, f"Confidence {conf} must be in [0, 1]"

    def test_label_is_string(self):
        iq = _make_wifi_iq()
        label, conf = cyclo_classify(iq)
        assert isinstance(label, str), "Classification must be a string"

    def test_valid_label_values(self):
        valid = {'wifi_2_4g', 'ble', 'zigbee', ''}
        for iq in [_make_wifi_iq(), _make_ble_iq(), _make_zigbee_iq(), _make_unknown_iq()]:
            label, _ = cyclo_classify(iq)
            assert label in valid, f"Label '{label}' not in valid set {valid}"


class TestWifiClassification:
    def test_wifi_ofdm_classified_correctly(self):
        """Strong OFDM pilot lines at bins 448/1344 -> wifi_2_4g classification."""
        iq = _make_wifi_iq(snr_db=20.0)
        label, conf = cyclo_classify(iq)
        assert label == 'wifi_2_4g', f"Expected wifi_2_4g, got '{label}'"

    def test_wifi_confidence_above_threshold(self):
        """WiFi classification confidence >= 0.5 for SNR=20 dB signal."""
        iq = _make_wifi_iq(snr_db=20.0)
        label, conf = cyclo_classify(iq)
        if label == 'wifi_2_4g':
            assert conf >= 0.5, f"WiFi confidence {conf:.3f} below threshold 0.5"

    def test_wifi_line_ratio_feature(self):
        """WiFi line ratio is >> 1 for OFDM signal at 802.11 pilot bins (448, 1344)."""
        import scipy.fft
        iq = _make_wifi_iq(snr_db=20.0)
        spec = np.abs(scipy.fft.fft(iq[:N_FFT])) ** 2
        # Check power at actual 802.11n/ac pilot bin positions
        pilot_power = np.mean([spec[WIFI_PILOT_BIN_7], spec[WIFI_PILOT_BIN_21]])
        noise_est = np.median(spec[:N_FFT // 2])
        ratio = pilot_power / (noise_est + 1e-30)
        assert ratio > 5.0, f"WiFi line ratio {ratio:.1f} too low for synthetic OFDM at bins 448/1344"


class TestBleClassification:
    def test_ble_classified_correctly(self):
        """High power-CV across 625us windows -> ble classification."""
        iq = _make_ble_iq()
        label, conf = cyclo_classify(iq)
        assert label == 'ble', f"Expected ble, got '{label}'"

    def test_ble_power_cv_feature(self):
        """BLE synthetic signal has high power CV (> 0.3)."""
        iq = _make_ble_iq()
        n_windows = N_SAMPLES // BLE_WINDOW_SAMPLES
        windows = iq[:n_windows * BLE_WINDOW_SAMPLES].reshape(n_windows, BLE_WINDOW_SAMPLES)
        power_per_win = np.mean(np.abs(windows) ** 2, axis=1)
        cv = np.std(power_per_win) / (np.mean(power_per_win) + 1e-30)
        assert cv > 0.3, f"BLE power CV {cv:.3f} should be > 0.3"


class TestZigbeeClassification:
    def test_zigbee_classified_correctly(self):
        """Flat spectrum (high SFM) + low power CV -> zigbee classification."""
        iq = _make_zigbee_iq()
        label, conf = cyclo_classify(iq)
        assert label == 'zigbee', f"Expected zigbee, got '{label}' (conf={conf:.3f})"

    def test_zigbee_sfm_high(self):
        """ZigBee (white noise) has high spectral flatness measure (SFM near 1)."""
        import scipy.fft
        iq = _make_zigbee_iq()
        spec = np.abs(scipy.fft.fft(iq[:N_FFT])) ** 2
        n_half = N_FFT // 2
        spec_nz = spec[:n_half] + 1e-30
        sfm = np.exp(np.mean(np.log(spec_nz))) / np.mean(spec_nz)
        assert sfm > 0.3, f"ZigBee SFM {sfm:.3f} should be > 0.3 for flat spectrum"


class TestUnknownClassification:
    def test_ambiguous_returns_empty_or_low_confidence(self):
        """Narrowband single tone returns '' or confidence < 0.5.

        Single tone at bin 200: non-flat (SFM~0, not zigbee), not at pilot bins
        448/1344 (not wifi), constant amplitude (low CV, not BLE).
        """
        iq = _make_unknown_iq()
        label, conf = cyclo_classify(iq)
        assert label == '' or conf < 0.5, \
            f"Narrowband single tone should return empty label or low confidence, got ('{label}', {conf:.3f})"


class TestComputeBudget:
    def test_classify_under_10ms(self):
        """Full classify pipeline on 65536-sample chunk must run in < 10 ms."""
        import time
        iq = _make_wifi_iq()
        # Warm up
        cyclo_classify(iq)
        # Time 10 runs
        t0 = time.perf_counter()
        for _ in range(10):
            cyclo_classify(iq)
        elapsed_ms = (time.perf_counter() - t0) / 10.0 * 1000.0
        assert elapsed_ms < 10.0, \
            f"cyclo_classify took {elapsed_ms:.2f} ms (budget: 10 ms)"
