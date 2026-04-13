"""Tests for Plan 02-03 Task 2: IQRecorderNode action server with SigMF write thread.

All tests run without ROS2 or HackRF hardware. ROS2 imports are mocked.
"""
from __future__ import annotations

import json
import os
import queue
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Minimal ROS2 mocks (mirrors test_iq_recorder_driver.py approach)
# ---------------------------------------------------------------------------

def _install_ros_mocks():
    """Install lightweight mocks for rclpy and related packages."""
    rclpy_mod = types.ModuleType('rclpy')
    rclpy_mod.init = MagicMock()
    rclpy_mod.try_shutdown = MagicMock()

    rclpy_time = types.ModuleType('rclpy.time')
    rclpy_time.Time = MagicMock()

    rclpy_duration = types.ModuleType('rclpy.duration')
    rclpy_duration.Duration = MagicMock()

    rclpy_node = types.ModuleType('rclpy.node')

    class _FakeNode:
        def __init__(self, name, **kwargs):
            self._node_name = name

        def get_logger(self):
            logger = MagicMock()
            logger.info = MagicMock()
            logger.warning = MagicMock()
            logger.warn = MagicMock()
            logger.error = MagicMock()
            logger.debug = MagicMock()
            return logger

        def get_clock(self):
            clock = MagicMock()
            clock.now.return_value.to_msg.return_value = MagicMock()
            return clock

        def declare_parameter(self, *args, **kwargs):
            pass

        def get_parameter(self, name):
            param = MagicMock()
            defaults = {
                'driver_node_name': '/hackrf_node',
                'feedback_interval_s': 1.0,
                'max_duration_s': 300.0,
                'center_frequency': 2437e6,
                'sample_rate': 20e6,
                'lna_gain': 16,
                'vga_gain': 20,
            }
            param.value = defaults.get(name, 0)
            return param

        def create_subscription(self, *args, **kwargs):
            return MagicMock()

        def create_client(self, *args, **kwargs):
            client = MagicMock()
            client.wait_for_service.return_value = True
            future = MagicMock()
            future.result.return_value.success = True
            client.call.return_value = future.result.return_value
            return client

        def destroy_node(self):
            pass

    rclpy_node.Node = _FakeNode

    rclpy_executors = types.ModuleType('rclpy.executors')
    rclpy_executors.MultiThreadedExecutor = MagicMock()

    rclpy_parameter = types.ModuleType('rclpy.parameter')
    rclpy_parameter.Parameter = MagicMock()

    rclpy_qos = types.ModuleType('rclpy.qos')
    rclpy_qos.QoSProfile = MagicMock()
    rclpy_qos.ReliabilityPolicy = MagicMock()
    rclpy_qos.HistoryPolicy = MagicMock()
    rclpy_qos.DurabilityPolicy = MagicMock()

    rclpy_cbg = types.ModuleType('rclpy.callback_groups')
    rclpy_cbg.ReentrantCallbackGroup = MagicMock()

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

    # std_srvs
    std_srvs = types.ModuleType('std_srvs')
    std_srvs_srv = types.ModuleType('std_srvs.srv')
    std_srvs_srv.Trigger = MagicMock()

    # std_msgs
    std_msgs = types.ModuleType('std_msgs')
    std_msgs_msg = types.ModuleType('std_msgs.msg')
    std_msgs_msg.Float32MultiArray = MagicMock()

    # tf2_ros
    tf2_ros_mod = types.ModuleType('tf2_ros')
    tf2_ros_mod.Buffer = MagicMock()
    tf2_ros_mod.TransformListener = MagicMock()
    tf2_ros_mod.LookupException = Exception
    tf2_ros_mod.ExtrapolationException = Exception
    tf2_ros_mod.TransformException = Exception

    # rclpy action server
    rclpy_action = types.ModuleType('rclpy.action')
    rclpy_action.ActionServer = MagicMock()
    rclpy_action.GoalResponse = MagicMock()
    rclpy_action.CancelResponse = MagicMock()

    # hackrf_interfaces
    hackrf_if = types.ModuleType('hackrf_interfaces')
    hackrf_if_action = types.ModuleType('hackrf_interfaces.action')

    class _FakeRecordIQ:
        class Goal:
            duration_s = 0.1
            output_path = '/tmp'
            trigger_reason = 'test'

        class Result:
            success = False
            message = ''
            sigmf_data_path = ''
            sigmf_meta_path = ''
            samples_written = 0
            queue_drops = 0

        class Feedback:
            bytes_written = 0
            duration_elapsed_s = 0.0
            queue_drops = 0

    hackrf_if_action.RecordIQ = _FakeRecordIQ

    mods = {
        'rclpy': rclpy_mod,
        'rclpy.time': rclpy_time,
        'rclpy.duration': rclpy_duration,
        'rclpy.node': rclpy_node,
        'rclpy.executors': rclpy_executors,
        'rclpy.parameter': rclpy_parameter,
        'rclpy.qos': rclpy_qos,
        'rclpy.callback_groups': rclpy_cbg,
        'rclpy.action': rclpy_action,
        'rcl_interfaces': rcl_if,
        'rcl_interfaces.msg': rcl_if_msg,
        'geometry_msgs': geom_msgs,
        'geometry_msgs.msg': geom_msgs_msg,
        'std_srvs': std_srvs,
        'std_srvs.srv': std_srvs_srv,
        'std_msgs': std_msgs,
        'std_msgs.msg': std_msgs_msg,
        'tf2_ros': tf2_ros_mod,
        'hackrf_interfaces': hackrf_if,
        'hackrf_interfaces.action': hackrf_if_action,
    }
    for name, mod in mods.items():
        sys.modules.setdefault(name, mod)


_install_ros_mocks()

import importlib
import hackrf_ros.iq_recorder_node as _rec_module

importlib.reload(_rec_module)
IQRecorderNode = _rec_module.IQRecorderNode
_write_thread_func = _rec_module._write_thread_func
_STOP_SENTINEL = _rec_module._STOP_SENTINEL


# ---------------------------------------------------------------------------
# Test 1: _write_thread_func drains queue into file
# ---------------------------------------------------------------------------

class TestWriteThread(unittest.TestCase):

    def test_write_thread_drains_queue(self):
        """Write thread writes all chunks from queue to file."""
        chunk = b'\xAB\xCD' * 32  # 64 bytes per chunk
        n_chunks = 10

        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name

        write_q = queue.Queue()
        for _ in range(n_chunks):
            write_q.put(chunk)
        write_q.put(_STOP_SENTINEL)

        result_dict = {'bytes': 0}

        with open(path, 'wb') as fh:
            t = threading.Thread(
                target=_write_thread_func,
                args=(fh, write_q, result_dict),
                daemon=True,
            )
            t.start()
            t.join(timeout=5.0)

        self.assertFalse(t.is_alive(), 'Write thread must exit after sentinel')
        self.assertEqual(result_dict['bytes'], n_chunks * len(chunk))
        self.assertEqual(os.path.getsize(path), n_chunks * len(chunk))

        os.unlink(path)

    # ------------------------------------------------------------------
    # Test 2: sentinel causes clean exit
    # ------------------------------------------------------------------

    def test_sentinel_exits_cleanly(self):
        """Write thread exits cleanly when sentinel is first item."""
        write_q = queue.Queue()
        write_q.put(_STOP_SENTINEL)

        result_dict = {'bytes': 0}

        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name

        with open(path, 'wb') as fh:
            t = threading.Thread(
                target=_write_thread_func,
                args=(fh, write_q, result_dict),
                daemon=True,
            )
            t.start()
            t.join(timeout=3.0)

        self.assertFalse(t.is_alive(), 'Thread must exit on sentinel')
        self.assertEqual(result_dict['bytes'], 0)

        os.unlink(path)


# ---------------------------------------------------------------------------
# Test 3 & 4: _build_sigmf_meta
# ---------------------------------------------------------------------------

class TestBuildSigmfMeta(unittest.TestCase):

    def _make_node(self):
        """Return a bare IQRecorderNode without spinning."""
        node = IQRecorderNode.__new__(IQRecorderNode)
        node._write_q = None
        node._drop_count = 0
        node.get_logger = MagicMock(return_value=MagicMock(
            info=MagicMock(), warning=MagicMock(), error=MagicMock(),
            debug=MagicMock()))
        return node

    def test_meta_with_pose(self):
        """_build_sigmf_meta with pose dict produces ci8 and robot_pose key."""
        node = self._make_node()
        pose = {'x': 1.0, 'y': 2.0, 'z': 0.0,
                'qx': 0.0, 'qy': 0.0, 'qz': 0.0, 'qw': 1.0}

        with tempfile.NamedTemporaryFile(suffix='.sigmf-data', delete=False) as f:
            data_path = f.name

        meta = node._build_sigmf_meta(
            data_path=data_path,
            center_freq=2437e6,
            sample_rate=20e6,
            lna_gain=16,
            vga_gain=20,
            trigger_reason='test_mission',
            pose=pose,
            capture_timestamp_iso='2026-04-13T04:00:00Z',
        )

        # Dump to temp file and reload as JSON
        with tempfile.NamedTemporaryFile(
                suffix='.sigmf-meta', mode='w', delete=False) as mf:
            meta_path = mf.name
        meta.tofile(meta_path, skip_validate=True, overwrite=True)

        with open(meta_path) as jf:
            doc = json.load(jf)

        global_info = doc.get('global', {})
        self.assertEqual(global_info.get('core:datatype'), 'ci8',
                         'Datatype must be ci8')
        self.assertIn('hackrf_ros:robot_pose', global_info,
                      'robot_pose key must be present')
        self.assertIsNotNone(global_info['hackrf_ros:robot_pose'],
                             'robot_pose must not be null when pose provided')
        self.assertEqual(global_info['hackrf_ros:robot_pose']['x'], 1.0)

        os.unlink(data_path)
        os.unlink(meta_path)

    def test_meta_pose_none_stored_as_null(self):
        """_build_sigmf_meta with pose=None stores null (not missing key)."""
        node = self._make_node()

        with tempfile.NamedTemporaryFile(suffix='.sigmf-data', delete=False) as f:
            data_path = f.name

        meta = node._build_sigmf_meta(
            data_path=data_path,
            center_freq=2437e6,
            sample_rate=20e6,
            lna_gain=16,
            vga_gain=20,
            trigger_reason='no_pose',
            pose=None,
            capture_timestamp_iso='2026-04-13T04:00:00Z',
        )

        with tempfile.NamedTemporaryFile(
                suffix='.sigmf-meta', mode='w', delete=False) as mf:
            meta_path = mf.name
        meta.tofile(meta_path, skip_validate=True, overwrite=True)

        with open(meta_path) as jf:
            doc = json.load(jf)

        global_info = doc.get('global', {})
        self.assertEqual(global_info.get('core:datatype'), 'ci8')
        # Key must exist (not missing) and value must be None/null
        self.assertIn('hackrf_ros:robot_pose', global_info,
                      'robot_pose key must be present even when None')
        self.assertIsNone(global_info['hackrf_ros:robot_pose'],
                          'robot_pose must be null when pose=None')

        os.unlink(data_path)
        os.unlink(meta_path)


# ---------------------------------------------------------------------------
# Test 5: _iq_callback queue fan-out
# ---------------------------------------------------------------------------

class TestIqCallback(unittest.TestCase):

    def _make_node_with_queue(self):
        node = IQRecorderNode.__new__(IQRecorderNode)
        node._write_q = queue.Queue(maxsize=256)
        node._drop_count = 0
        node.get_logger = MagicMock(return_value=MagicMock(
            info=MagicMock(), warning=MagicMock(), error=MagicMock(),
            debug=MagicMock()))
        return node

    def test_iq_callback_puts_to_write_queue(self):
        """_iq_callback converts Float32MultiArray to ci8 and enqueues."""
        import numpy as np
        node = self._make_node_with_queue()

        # Simulate Float32MultiArray message with normalized floats
        msg = MagicMock()
        # 8 samples = 4 IQ pairs, values in [-1.0, 1.0]
        msg.data = [0.0, 0.5, -0.5, 1.0, -1.0, 0.25, -0.25, 0.75]

        node._iq_callback(msg)

        self.assertEqual(node._write_q.qsize(), 1)
        chunk = node._write_q.get_nowait()
        # Should be int8 bytes
        raw = np.frombuffer(chunk, dtype=np.int8)
        self.assertEqual(len(raw), 8)

    def test_iq_callback_no_write_when_queue_none(self):
        """_iq_callback does nothing when _write_q is None."""
        node = IQRecorderNode.__new__(IQRecorderNode)
        node._write_q = None
        node._drop_count = 0
        node.get_logger = MagicMock(return_value=MagicMock(
            info=MagicMock(), warning=MagicMock()))

        msg = MagicMock()
        msg.data = [0.0, 0.0, 0.0, 0.0]

        # Must not raise, no side-effects
        node._iq_callback(msg)
        self.assertEqual(node._drop_count, 0)


# ---------------------------------------------------------------------------
# Test 6: Invalid directory returns failure without partial files
# ---------------------------------------------------------------------------

class TestInvalidDirectory(unittest.TestCase):

    def test_bad_output_path_returns_failure(self):
        """Recording to a non-writable/invalid path aborts cleanly."""
        node = IQRecorderNode.__new__(IQRecorderNode)
        node._write_q = None
        node._drop_count = 0
        node.get_logger = MagicMock(return_value=MagicMock(
            info=MagicMock(), warning=MagicMock(), error=MagicMock(),
            debug=MagicMock()))
        node._tf_buffer = MagicMock()
        node._start_client = MagicMock()
        node._stop_client = MagicMock()
        node.get_parameter = MagicMock(side_effect=lambda n: MagicMock(
            value={'center_frequency': 2437e6, 'sample_rate': 20e6,
                   'lna_gain': 16, 'vga_gain': 20, 'max_duration_s': 300.0,
                   'feedback_interval_s': 1.0}.get(n, 0)
        ))

        # A path that is guaranteed to fail (root-owned directory)
        bad_path = '/proc/sys/impossible_subdir_for_test'

        goal_handle = MagicMock()
        goal_handle.request.output_path = bad_path
        goal_handle.request.duration_s = 1.0
        goal_handle.request.trigger_reason = 'test'
        goal_handle.is_cancel_requested = False

        result = node._execute_record(goal_handle)

        self.assertFalse(result.success, 'Result must be False for bad path')
        goal_handle.abort.assert_called_once()


if __name__ == '__main__':
    unittest.main()
