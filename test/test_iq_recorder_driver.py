"""Tests for Plan 02-03 Task 1: driver recorder fan-out and recording services.

These tests exercise _rx_callback, _handle_recording_start, and
_handle_recording_stop by inspecting source or by importing the node
with all ROS2 / pyhackrf2 dependencies mocked out.
"""
from __future__ import annotations

import queue
import sys
import types
import unittest
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Build a minimal ROS2 / pyhackrf2 mock environment so we can import the
# module without a live ROS2 install.
# ---------------------------------------------------------------------------

def _make_ros_mocks():
    """Install lightweight mocks for rclpy and related packages."""
    # rclpy top-level
    rclpy_mod = types.ModuleType('rclpy')
    rclpy_mod.init = MagicMock()
    rclpy_mod.try_shutdown = MagicMock()

    # rclpy.lifecycle
    lifecycle_mod = types.ModuleType('rclpy.lifecycle')

    class _FakeLifecycleNode:
        """Minimal LifecycleNode stand-in."""

        def __init__(self, name, **kwargs):
            self._name = name

        def get_logger(self):
            logger = MagicMock()
            logger.info = MagicMock()
            logger.warning = MagicMock()
            logger.error = MagicMock()
            logger.warn = MagicMock()
            return logger

        def get_clock(self):
            clock = MagicMock()
            clock.now.return_value.to_msg.return_value = MagicMock()
            return clock

        def get_parameter(self, name):
            param = MagicMock()
            defaults = {
                'center_frequency': 2437e6,
                'sample_rate': 20e6,
                'lna_gain': 16,
                'vga_gain': 20,
                'amp_enabled': False,
                'antenna_frame': 'hackrf_antenna',
                'parent_frame': 'base_link',
                'antenna_x': 0.0,
                'antenna_y': 0.0,
                'antenna_z': 0.0,
            }
            param.value = defaults.get(name, 0)
            return param

        def declare_parameter(self, *args, **kwargs):
            pass

        def create_publisher(self, *args, **kwargs):
            return MagicMock()

        def create_service(self, *args, **kwargs):
            return MagicMock()

        def create_timer(self, *args, **kwargs):
            return MagicMock()

        def add_on_set_parameters_callback(self, *args, **kwargs):
            pass

        def destroy_timer(self, *args, **kwargs):
            pass

        def destroy_node(self, *args, **kwargs):
            pass

    lifecycle_mod.LifecycleNode = _FakeLifecycleNode
    lifecycle_mod.LifecycleState = MagicMock()
    lifecycle_mod.TransitionCallbackReturn = MagicMock()

    # rclpy.executors
    executors_mod = types.ModuleType('rclpy.executors')
    executors_mod.MultiThreadedExecutor = MagicMock()

    # rclpy.parameter
    parameter_mod = types.ModuleType('rclpy.parameter')
    parameter_mod.Parameter = MagicMock()

    # rclpy.qos
    qos_mod = types.ModuleType('rclpy.qos')
    qos_mod.QoSProfile = MagicMock()
    qos_mod.ReliabilityPolicy = MagicMock()
    qos_mod.HistoryPolicy = MagicMock()
    qos_mod.DurabilityPolicy = MagicMock()

    # rclpy.callback_groups
    cbg_mod = types.ModuleType('rclpy.callback_groups')
    cbg_mod.ReentrantCallbackGroup = MagicMock()

    # rcl_interfaces
    rcl_if = types.ModuleType('rcl_interfaces')
    rcl_if_msg = types.ModuleType('rcl_interfaces.msg')
    rcl_if_msg.ParameterDescriptor = MagicMock()
    rcl_if_msg.FloatingPointRange = MagicMock()
    rcl_if_msg.IntegerRange = MagicMock()
    rcl_if_msg.SetParametersResult = MagicMock(
        return_value=MagicMock(successful=True))

    # geometry_msgs
    geom_msgs = types.ModuleType('geometry_msgs')
    geom_msgs_msg = types.ModuleType('geometry_msgs.msg')
    geom_msgs_msg.TransformStamped = MagicMock()

    # tf2_ros
    tf2_ros_mod = types.ModuleType('tf2_ros')
    tf2_ros_mod.StaticTransformBroadcaster = MagicMock()

    # diagnostic_updater
    diag_updater = types.ModuleType('diagnostic_updater')
    diag_updater.Updater = MagicMock()

    # diagnostic_msgs
    diag_msgs = types.ModuleType('diagnostic_msgs')
    diag_msgs_msg = types.ModuleType('diagnostic_msgs.msg')
    diag_msgs_msg.DiagnosticStatus = MagicMock()

    # hackrf_interfaces
    hackrf_if = types.ModuleType('hackrf_interfaces')
    hackrf_if_msg = types.ModuleType('hackrf_interfaces.msg')
    hackrf_if_msg.SpectrumStamped = MagicMock()
    hackrf_if_srv = types.ModuleType('hackrf_interfaces.srv')
    hackrf_if_srv.Sweep = MagicMock()

    # std_srvs
    std_srvs = types.ModuleType('std_srvs')
    std_srvs_srv = types.ModuleType('std_srvs.srv')
    std_srvs_srv.Trigger = MagicMock()

    # pyhackrf2
    pyhackrf2_mod = types.ModuleType('pyhackrf2')
    pyhackrf2_mod.HackRF = MagicMock()

    mods = {
        'rclpy': rclpy_mod,
        'rclpy.lifecycle': lifecycle_mod,
        'rclpy.executors': executors_mod,
        'rclpy.parameter': parameter_mod,
        'rclpy.qos': qos_mod,
        'rclpy.callback_groups': cbg_mod,
        'rcl_interfaces': rcl_if,
        'rcl_interfaces.msg': rcl_if_msg,
        'geometry_msgs': geom_msgs,
        'geometry_msgs.msg': geom_msgs_msg,
        'tf2_ros': tf2_ros_mod,
        'diagnostic_updater': diag_updater,
        'diagnostic_msgs': diag_msgs,
        'diagnostic_msgs.msg': diag_msgs_msg,
        'hackrf_interfaces': hackrf_if,
        'hackrf_interfaces.msg': hackrf_if_msg,
        'hackrf_interfaces.srv': hackrf_if_srv,
        'std_srvs': std_srvs,
        'std_srvs.srv': std_srvs_srv,
        'pyhackrf2': pyhackrf2_mod,
    }
    for name, mod in mods.items():
        sys.modules.setdefault(name, mod)


_make_ros_mocks()

# Now import the driver module
import importlib
import hackrf_ros.hackrf_lifecycle_node as _drv_module

importlib.reload(_drv_module)
HackRFLifecycleNode = _drv_module.HackRFLifecycleNode


# ---------------------------------------------------------------------------
# Helper: create a bare-minimum node instance without on_configure
# ---------------------------------------------------------------------------

def _make_node():
    """Return an initialised HackRFLifecycleNode without hardware."""
    node = HackRFLifecycleNode.__new__(HackRFLifecycleNode)
    # Call only the parent stub __init__ (no ROS2 setup)
    node._name = 'hackrf_node'

    # Replicate __init__ attribute setup manually
    import queue as _q
    import threading
    import numpy as np

    node._hackrf = None
    node._device_lock = threading.RLock()
    node._iq_queue = _q.Queue(maxsize=64)
    node._is_streaming = False
    node._sweep_lock = threading.Lock()
    node._timer = None
    node._spectrum_pub = None
    node._sweep_srv = None
    node._diag_updater = None
    node._activate_time = None
    node._last_rx_time = 0.0
    node._rx_overflow_count = 0
    node._clip_count = 0

    node._window = np.blackman(4096)
    node._window_S2 = float(np.sum(node._window ** 2))
    node._psd_accum = np.zeros(4096)
    node._accum_count = 0

    # Attributes added by this plan's Task 1 (should exist post-implementation)
    node._recorder_q = None
    node._recorder_q_drops = 0

    # Provide a real logger mock so WARN calls can be asserted
    node._logger = MagicMock()
    node._logger.info = MagicMock()
    node._logger.warning = MagicMock()
    node._logger.warn = MagicMock()
    node._logger.error = MagicMock()
    node.get_logger = MagicMock(return_value=node._logger)

    return node


# ---------------------------------------------------------------------------
# Test 1: fan-out to both _iq_queue and _recorder_q when recorder active
# ---------------------------------------------------------------------------

class TestRxCallbackFanOut(unittest.TestCase):

    def test_fanout_to_both_queues_when_recorder_active(self):
        """_rx_callback puts chunk in both _iq_queue and _recorder_q."""
        node = _make_node()
        node._recorder_q = queue.Queue(maxsize=256)

        # Build a 4096-byte payload (all zeros, no clipping)
        payload = bytes(4096 * 2)  # int8 pairs — zeros won't clip

        node._rx_callback(payload)

        self.assertEqual(node._iq_queue.qsize(), 1,
                         '_iq_queue should have exactly 1 item')
        self.assertEqual(node._recorder_q.qsize(), 1,
                         '_recorder_q should have exactly 1 item after fan-out')

    # ------------------------------------------------------------------
    # Test 2: drop-oldest when _recorder_q is full
    # ------------------------------------------------------------------

    def test_drop_oldest_when_recorder_queue_full(self):
        """With _recorder_q full, _rx_callback drops oldest, inserts new."""
        node = _make_node()
        rq = queue.Queue(maxsize=256)
        # Fill the queue to capacity with sentinel bytes
        sentinel = b'\x01' * 8
        for _ in range(256):
            rq.put_nowait(sentinel)

        node._recorder_q = rq

        # A non-clipping payload
        new_payload = bytes(4096 * 2)

        node._rx_callback(new_payload)

        # Queue should still be exactly full (256 items)
        self.assertEqual(rq.qsize(), 256)
        # Drop counter must be incremented
        self.assertGreaterEqual(node._recorder_q_drops, 1,
                                '_recorder_q_drops must increment on overflow')
        # WARN logged
        node.get_logger().warning.assert_called()

    # ------------------------------------------------------------------
    # Test 3: _recorder_q = None → no fan-out attempt, no error
    # ------------------------------------------------------------------

    def test_no_fanout_when_recorder_inactive(self):
        """With _recorder_q=None, _rx_callback puts item in _iq_queue only."""
        node = _make_node()
        node._recorder_q = None  # recorder not active

        payload = bytes(4096 * 2)
        node._rx_callback(payload)

        self.assertEqual(node._iq_queue.qsize(), 1)
        # No exception raised — test passes if we reach this line


# ---------------------------------------------------------------------------
# Test 4: /hackrf/recording/start service
# ---------------------------------------------------------------------------

class TestRecordingStartService(unittest.TestCase):

    def test_recording_start_creates_queue(self):
        """/hackrf/recording/start Trigger sets _recorder_q and returns success."""
        node = _make_node()
        node._recorder_q = None  # start from idle

        request = MagicMock()
        response = MagicMock()

        result = node._handle_recording_start(request, response)

        # _recorder_q must now be a real Queue
        self.assertIsInstance(node._recorder_q, queue.Queue,
                              '_recorder_q must be set to queue.Queue after start')
        self.assertEqual(node._recorder_q.maxsize, 256)
        # response.success must be True
        self.assertTrue(result.success,
                        'response.success must be True')


# ---------------------------------------------------------------------------
# Test 5: /hackrf/recording/stop service
# ---------------------------------------------------------------------------

class TestRecordingStopService(unittest.TestCase):

    def test_recording_stop_clears_queue(self):
        """/hackrf/recording/stop Trigger sets _recorder_q to None."""
        node = _make_node()
        node._recorder_q = queue.Queue(maxsize=256)  # active

        request = MagicMock()
        response = MagicMock()

        result = node._handle_recording_stop(request, response)

        self.assertIsNone(node._recorder_q,
                          '_recorder_q must be None after stop service')
        self.assertTrue(result.success,
                        'response.success must be True')


if __name__ == '__main__':
    unittest.main()
