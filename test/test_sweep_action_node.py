"""Unit tests for sweep_action_node.py — no hardware or ROS2 init required.

Tests cover pure-function logic: hop planning, Tukey weights, linear-domain
blending, composite finalization, and the execute_sweep callback via mocks.
"""
from __future__ import annotations

import threading
import time
import types
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers to exercise pure functions without instantiating SweepActionNode
# (which would require rclpy.init).  We import the module-level functions
# directly by importing the module under a patched rclpy environment.
# ---------------------------------------------------------------------------

def _get_pure_functions():
    """Return the module with rclpy mocked out so imports succeed."""
    import importlib
    import sys

    # Build a minimal rclpy stub so the module can be imported without ROS2.
    rclpy_stub = types.ModuleType('rclpy')
    rclpy_stub.init = lambda *a, **k: None
    rclpy_stub.try_shutdown = lambda: None

    node_stub = types.ModuleType('rclpy.node')
    node_stub.Node = object  # base class

    exec_stub = types.ModuleType('rclpy.executors')
    exec_stub.MultiThreadedExecutor = MagicMock

    param_stub = types.ModuleType('rclpy.parameter')

    class _FakeParam:
        class Type:
            DOUBLE = 3
        def __init__(self, name, type_=None, value=None):
            self.name = name
            self.type_ = type_
            self.value = value

    param_stub.Parameter = _FakeParam

    param_client_stub = types.ModuleType('rclpy.parameter_client')
    param_client_stub.AsyncParametersClient = MagicMock

    action_stub = types.ModuleType('rclpy.action')
    action_stub.ActionServer = MagicMock
    action_stub.CancelResponse = MagicMock
    action_stub.GoalResponse = MagicMock

    cb_stub = types.ModuleType('rclpy.callback_groups')
    cb_stub.ReentrantCallbackGroup = MagicMock

    qos_stub = types.ModuleType('rclpy.qos')
    qos_stub.QoSProfile = MagicMock
    qos_stub.ReliabilityPolicy = MagicMock
    qos_stub.HistoryPolicy = MagicMock
    qos_stub.DurabilityPolicy = MagicMock

    # hackrf_interfaces stubs
    hi_stub = types.ModuleType('hackrf_interfaces')
    action_mod = types.ModuleType('hackrf_interfaces.action')

    class _SweepGoal:
        freq_min_hz = 0.0
        freq_max_hz = 0.0
        step_hz = 0.0
        averaging = 0

    class _SweepResult:
        success = False
        message = ''
        freq_min_hz = 0.0
        freq_max_hz = 0.0
        bin_width_hz = 0.0
        psd_db = []

    class _SweepFeedback:
        current_hop = 0
        total_hops = 0
        current_freq_hz = 0.0
        partial_psd_db = []

    class _Sweep:
        Goal = _SweepGoal
        Result = _SweepResult
        Feedback = _SweepFeedback

    action_mod.Sweep = _Sweep

    msg_mod = types.ModuleType('hackrf_interfaces.msg')

    class _SpectrumStamped:
        center_frequency_hz = 0.0
        sample_rate_hz = 20e6
        bin_width_hz = 0.0
        fft_size = 4096
        psd_db = []
        noise_floor_db = -90.0

    msg_mod.SpectrumStamped = _SpectrumStamped

    stubs = {
        'rclpy': rclpy_stub,
        'rclpy.node': node_stub,
        'rclpy.executors': exec_stub,
        'rclpy.parameter': param_stub,
        'rclpy.parameter_client': param_client_stub,
        'rclpy.action': action_stub,
        'rclpy.callback_groups': cb_stub,
        'rclpy.qos': qos_stub,
        'hackrf_interfaces': hi_stub,
        'hackrf_interfaces.action': action_mod,
        'hackrf_interfaces.msg': msg_mod,
    }

    saved = {}
    for k, v in stubs.items():
        saved[k] = sys.modules.get(k)
        sys.modules[k] = v

    # Also patch rclpy on the rclpy_stub itself
    rclpy_stub.node = node_stub
    rclpy_stub.executors = exec_stub
    rclpy_stub.parameter = param_stub
    rclpy_stub.action = action_stub
    rclpy_stub.callback_groups = cb_stub
    rclpy_stub.qos = qos_stub

    if 'hackrf_ros.sweep_action_node' in sys.modules:
        del sys.modules['hackrf_ros.sweep_action_node']

    import hackrf_ros.sweep_action_node as m

    # Restore
    for k, orig in saved.items():
        if orig is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = orig

    return m


# ---------------------------------------------------------------------------
# Test 1: _plan_hops returns correctly-spaced center frequencies
# ---------------------------------------------------------------------------

class TestPlanHops(unittest.TestCase):

    def setUp(self):
        self.m = _get_pure_functions()

    def test_plan_hops_covers_band(self):
        """_plan_hops(2.4e9, 2.5e9, 20e6, step_hz=20e6) covers full band."""
        centers = self.m._plan_hops(2.4e9, 2.5e9, 20e6, step_hz=20e6)
        self.assertGreater(len(centers), 0)
        # First center's lower edge must be at or below freq_min
        self.assertLessEqual(centers[0] - 20e6 / 2, 2.4e9 + 1)
        # Last center's upper edge must reach or exceed freq_max
        self.assertGreaterEqual(centers[-1] + 20e6 / 2, 2.5e9 - 1)

    def test_plan_hops_spacing(self):
        """Adjacent hops are spaced by effective_step (usable_bw when step>=usable_bw)."""
        # step_hz=20e6, usable_bw=16e6 → effective_step=16e6
        centers = self.m._plan_hops(2.4e9, 2.5e9, 20e6, step_hz=20e6)
        if len(centers) > 1:
            gap = centers[1] - centers[0]
            # Effective step = min(20e6, 0.8*20e6) = 16e6
            self.assertAlmostEqual(gap, 16e6, delta=1e6)

    def test_plan_hops_zero_step_uses_usable_bw(self):
        """step_hz=0 defaults to usable_bw = sample_rate * 0.8."""
        centers_explicit = self.m._plan_hops(2.4e9, 2.5e9, 20e6, step_hz=16e6)
        centers_default = self.m._plan_hops(2.4e9, 2.5e9, 20e6, step_hz=0)
        self.assertEqual(len(centers_explicit), len(centers_default))


# ---------------------------------------------------------------------------
# Test 2: _blend_hop_into_composite writes correct linear values
# ---------------------------------------------------------------------------

class TestBlendHop(unittest.TestCase):

    def setUp(self):
        self.m = _get_pure_functions()

    def test_blend_flat_psd(self):
        """A flat 0 dB (4096-bin) hop writes positive linear values into composite."""
        freq_min = 2.4e9
        freq_max = 2.5e9
        sample_rate = 20e6
        bin_width = sample_rate / 4096
        center = 2.41e9

        n_bins = int((freq_max - freq_min) / bin_width)
        comp_linear = np.zeros(n_bins, dtype=np.float64)
        comp_weight = np.zeros(n_bins, dtype=np.float64)

        psd_db = np.zeros(4096, dtype=np.float32)  # flat 0 dB → linear 1.0

        self.m._blend_hop_into_composite(
            psd_db, center, sample_rate, freq_min, bin_width,
            comp_linear, comp_weight)

        # After blending, at least some bins should have positive weight
        self.assertGreater(np.sum(comp_weight > 0), 0)
        # Weighted average of 0 dB is 1.0 in linear → values should be ~1.0
        valid = comp_weight > 0
        avg_linear = comp_linear[valid] / comp_weight[valid]
        np.testing.assert_allclose(avg_linear, 1.0, atol=0.01)


# ---------------------------------------------------------------------------
# Test 3: _tukey_weights returns correct shape with tapered ends
# ---------------------------------------------------------------------------

class TestTukeyWeights(unittest.TestCase):

    def setUp(self):
        self.m = _get_pure_functions()

    def test_length(self):
        w = self.m._tukey_weights(100, alpha=0.2)
        self.assertEqual(len(w), 100)

    def test_ramp_up(self):
        """First alpha/2 * N elements ramp 0 → 1."""
        w = self.m._tukey_weights(100, alpha=0.2)
        taper = int(100 * 0.2 / 2)  # 10
        self.assertAlmostEqual(w[0], 0.0, delta=0.01)
        self.assertAlmostEqual(w[taper - 1], 1.0, delta=0.15)

    def test_middle_is_one(self):
        """Middle section is all 1.0."""
        w = self.m._tukey_weights(100, alpha=0.2)
        taper = int(100 * 0.2 / 2)  # 10
        middle = w[taper:-taper]
        np.testing.assert_allclose(middle, 1.0, atol=1e-9)

    def test_ramp_down(self):
        """Last alpha/2 * N elements ramp 1 → 0."""
        w = self.m._tukey_weights(100, alpha=0.2)
        taper = int(100 * 0.2 / 2)  # 10
        self.assertAlmostEqual(w[-1], 0.0, delta=0.01)
        self.assertAlmostEqual(w[-(taper)], 1.0, delta=0.15)


# ---------------------------------------------------------------------------
# Test 4: _finalize_composite returns weighted-average dB in overlap regions
# ---------------------------------------------------------------------------

class TestFinalizeComposite(unittest.TestCase):

    def setUp(self):
        self.m = _get_pure_functions()

    def test_weighted_average_in_overlap(self):
        """Overlapping bins result in weighted-average dB, not double-counting."""
        n_bins = 100
        comp_linear = np.zeros(n_bins, dtype=np.float64)
        comp_weight = np.zeros(n_bins, dtype=np.float64)

        # Hop A: bins 0-59 with linear=1.0 (0 dB), weight=1.0
        comp_linear[0:60] += 1.0
        comp_weight[0:60] += 1.0
        # Hop B: bins 40-99 with linear=10.0 (10 dB), weight=1.0
        comp_linear[40:100] += 10.0
        comp_weight[40:100] += 1.0

        result = self.m._finalize_composite(comp_linear, comp_weight, n_bins)

        # Non-overlapping regions
        # bins 0-39: only hop A → 0 dB
        np.testing.assert_allclose(result[0:40], 0.0, atol=0.1)
        # bins 60-99: only hop B → 10 dB
        np.testing.assert_allclose(result[60:100], 10.0, atol=0.1)
        # Overlap bins 40-59: (1.0+10.0)/(1.0+1.0) = 5.5 linear → ~7.4 dB
        expected_db = 10 * np.log10(5.5)
        np.testing.assert_allclose(result[40:60], expected_db, atol=0.1)

    def test_empty_bins_get_sentinel(self):
        """Bins with zero weight are filled with -100.0 dB sentinel."""
        n_bins = 50
        comp_linear = np.zeros(n_bins, dtype=np.float64)
        comp_weight = np.zeros(n_bins, dtype=np.float64)
        # Fill only first half
        comp_linear[0:25] = 1.0
        comp_weight[0:25] = 1.0

        result = self.m._finalize_composite(comp_linear, comp_weight, n_bins)
        np.testing.assert_allclose(result[25:50], -100.0, atol=1e-6)


# ---------------------------------------------------------------------------
# Test 5: execute_sweep with mocked spectrum subscriber returns success
# ---------------------------------------------------------------------------

class TestExecuteSweep(unittest.TestCase):

    def setUp(self):
        self.m = _get_pure_functions()

    def _make_mock_spectrum(self, center_hz, sample_rate=20e6):
        """Create a synthetic SpectrumStamped-like object."""
        msg = MagicMock()
        msg.center_frequency_hz = center_hz
        msg.sample_rate_hz = sample_rate
        msg.bin_width_hz = sample_rate / 4096
        msg.fft_size = 4096
        msg.psd_db = list(np.zeros(4096, dtype=np.float32))
        return msg

    def test_execute_sweep_success(self):
        """Two hops → feedback published twice, result.success=True, psd_db non-empty."""
        import sys
        import types

        # We need a node-like mock with the pure methods available
        node = MagicMock()

        # Attach the pure functions as bound methods
        node._plan_hops = self.m._plan_hops
        node._tukey_weights = self.m._tukey_weights
        node._blend_hop_into_composite = self.m._blend_hop_into_composite
        node._finalize_composite = self.m._finalize_composite

        node._saved_center_freq = 2.41e9
        node._cancel_requested = threading.Event()
        node._cancel_requested.clear()

        freq_min = 2.4e9
        freq_max = 2.42e9  # small range → 2 hops max
        sample_rate = 20e6

        # _set_center_freq always succeeds
        node._set_center_freq = MagicMock(return_value=True)

        # _wait_for_spectrum returns a synthetic message
        hop_centers = self.m._plan_hops(freq_min, freq_max, sample_rate, step_hz=0)
        call_count = [0]

        def fake_wait_for_spectrum(timeout_s):
            center = hop_centers[min(call_count[0] // 1, len(hop_centers) - 1)]
            call_count[0] += 1
            return self._make_mock_spectrum(center, sample_rate)

        node._wait_for_spectrum = fake_wait_for_spectrum

        # goal_handle mock
        goal_handle = MagicMock()
        goal_handle.is_cancel_requested = False
        goal_handle.request = MagicMock()
        goal_handle.request.freq_min_hz = freq_min
        goal_handle.request.freq_max_hz = freq_max
        goal_handle.request.step_hz = 0.0
        goal_handle.request.averaging = 1
        goal_handle.publish_feedback = MagicMock()

        # Latest spectrum for reading sample_rate
        node._latest_spectrum = self._make_mock_spectrum(2.41e9, sample_rate)

        # Validate input guard
        node.get_logger = MagicMock(return_value=MagicMock())
        node.get_parameter = MagicMock(
            return_value=MagicMock(value=2.41e9))

        # Create result/feedback factory mocks
        feedback_calls = []

        def publish_feedback(fb):
            feedback_calls.append(fb)

        goal_handle.publish_feedback = publish_feedback

        # Run _execute_sweep directly as an unbound function
        result = self.m.SweepActionNode._execute_sweep(node, goal_handle)

        self.assertTrue(result.success)
        self.assertGreater(len(result.psd_db), 0)
        self.assertGreater(len(feedback_calls), 0)


# ---------------------------------------------------------------------------
# Test 6: Cancel path restores center_frequency and returns partial result
# ---------------------------------------------------------------------------

class TestCancelPath(unittest.TestCase):

    def setUp(self):
        self.m = _get_pure_functions()

    def _make_mock_spectrum(self, center_hz, sample_rate=20e6):
        msg = MagicMock()
        msg.center_frequency_hz = center_hz
        msg.sample_rate_hz = sample_rate
        msg.bin_width_hz = sample_rate / 4096
        msg.fft_size = 4096
        msg.psd_db = list(np.zeros(4096, dtype=np.float32))
        return msg

    def test_cancel_restores_center_freq_and_returns_partial(self):
        """Cancel after first hop: _set_center_freq called with saved freq, partial returned."""
        node = MagicMock()
        node._plan_hops = self.m._plan_hops
        node._tukey_weights = self.m._tukey_weights
        node._blend_hop_into_composite = self.m._blend_hop_into_composite
        node._finalize_composite = self.m._finalize_composite
        node._cancel_requested = threading.Event()
        node._cancel_requested.clear()

        saved_freq = 2.41e9
        node._saved_center_freq = saved_freq

        freq_min = 2.4e9
        freq_max = 2.44e9
        sample_rate = 20e6
        hop_centers = self.m._plan_hops(freq_min, freq_max, sample_rate, step_hz=0)

        set_freq_calls = []

        def fake_set_center_freq(hz):
            set_freq_calls.append(hz)
            return True

        node._set_center_freq = fake_set_center_freq

        hop_num = [0]

        def fake_wait_for_spectrum(timeout_s):
            center = hop_centers[min(hop_num[0], len(hop_centers) - 1)]
            hop_num[0] += 1
            return self._make_mock_spectrum(center, sample_rate)

        node._wait_for_spectrum = fake_wait_for_spectrum
        node._latest_spectrum = self._make_mock_spectrum(2.41e9, sample_rate)
        node.get_logger = MagicMock(return_value=MagicMock())
        node.get_parameter = MagicMock(return_value=MagicMock(value=saved_freq))

        feedback_calls = []

        goal_handle = MagicMock()
        goal_handle.publish_feedback = lambda fb: feedback_calls.append(fb)

        # Simulate cancel during second hop
        call_count = [0]
        original_is_cancel = property(lambda self: call_count[0] >= 1)

        # Cancel after 1 hop completes (check on second iteration)
        cancel_after = [1]
        hop_iter = [0]

        class CancelOnSecond:
            @property
            def is_cancel_requested(self):
                return hop_iter[0] >= cancel_after[0]

        # Patch goal_handle to use cancellable property
        goal_handle.is_cancel_requested = False  # start False

        # We'll patch the _execute_sweep to inject cancel after first hop
        # by making _set_center_freq track calls and flip cancel on 2nd call
        def smart_set_freq(hz):
            set_freq_calls.append(hz)
            # After first hop's spectrum collected and we set 2nd hop center:
            # flip cancel_requested
            if len(set_freq_calls) == 2:
                goal_handle.is_cancel_requested = True
            return True

        node._set_center_freq = smart_set_freq

        result = self.m.SweepActionNode._execute_sweep(node, goal_handle)

        # The last call to _set_center_freq should be restoring saved_freq
        self.assertIn(saved_freq, set_freq_calls,
                      f"saved freq {saved_freq} not in {set_freq_calls}")
        # Result should indicate cancelled with partial data
        self.assertFalse(result.success)
        # psd_db should have data (partial result from 1 hop)
        self.assertGreater(len(result.psd_db), 0)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
