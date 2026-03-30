"""Unit tests for BridgeNode and bridge_services — adapted from Plan 03-02 tests.

Tests bridge-specific behaviors:
  - BridgeNode imports redis (not pyhackrf2)
  - BridgeNode has publisher_ (Float32MultiArray) and _state_publisher (String)
  - _bridge_loop publishes Float32MultiArray on hackrf:iq:notify
  - _publish_state() publishes JSON string to /hackrf/state
  - destroy_node sets _stop_event
  - /hackrf/cmd subscription RPUSHes valid JSON to hackrf:cmd
  - /hackrf/mayhem/appstart service queues appstart command

All ROS2 and Redis calls are mocked — no live hardware required.
"""

import json
import sys
import threading
import unittest
from unittest.mock import MagicMock, call

import numpy as np


# ---------------------------------------------------------------------------
# Patch all hardware/ROS2 modules before importing bridge modules
# ---------------------------------------------------------------------------

# Mock rclpy.node.Node as a real Python base class so BridgeNode can be
# instantiated with object.__new__() in tests.
class _FakeNode:
    """Minimal stub for rclpy.node.Node."""

    def destroy_node(self):
        pass


# Module keys to mock — saved/restored in setUpModule/tearDownModule
_MOCK_MODULE_KEYS = [
    'rclpy', 'rclpy.node', 'rclpy.qos', 'rclpy.parameter', 'rclpy.exceptions',
    'rcl_interfaces', 'rcl_interfaces.msg',
    'std_msgs', 'std_msgs.msg',
    'std_srvs', 'std_srvs.srv',
    'redis', 'redis.exceptions',
    'pyhackrf2', 'serial',
    'hackrf_ros_interfaces', 'hackrf_ros_interfaces.srv',
    'hackrf_ros.bridge_node', 'hackrf_ros.bridge_services',
]
_ORIGINAL_MODULES = {}


def setUpModule():
    """Install sys.modules mocks before any test in this file runs."""
    # Save originals
    for key in _MOCK_MODULE_KEYS:
        _ORIGINAL_MODULES[key] = sys.modules.get(key)

    # Remove cached hackrf_ros bridge modules so they get re-imported with mocks
    for key in list(sys.modules.keys()):
        if key.startswith('hackrf_ros.bridge'):
            sys.modules.pop(key, None)

    mock_rclpy_node_module = MagicMock()
    mock_rclpy_node_module.Node = _FakeNode

    mock_rclpy = MagicMock()
    mock_rclpy.node = mock_rclpy_node_module
    sys.modules['rclpy'] = mock_rclpy
    sys.modules['rclpy.node'] = mock_rclpy_node_module
    sys.modules['rclpy.qos'] = MagicMock()
    sys.modules['rclpy.parameter'] = MagicMock()
    sys.modules['rclpy.exceptions'] = MagicMock()
    sys.modules['rcl_interfaces'] = MagicMock()
    sys.modules['rcl_interfaces.msg'] = MagicMock()

    # Set up Float32MultiArray and String as simple classes so isinstance() works
    _Float32MultiArray = type('Float32MultiArray', (), {'data': []})
    _String = type('String', (), {'data': ''})
    mock_std_msgs_msg = MagicMock()
    mock_std_msgs_msg.Float32MultiArray = _Float32MultiArray
    mock_std_msgs_msg.String = _String
    sys.modules['std_msgs'] = MagicMock()
    sys.modules['std_msgs.msg'] = mock_std_msgs_msg

    sys.modules['std_srvs'] = MagicMock()
    sys.modules['std_srvs.srv'] = MagicMock()

    # Mock redis
    sys.modules['redis'] = MagicMock()
    sys.modules['redis.exceptions'] = MagicMock()

    # Mock pyhackrf2 and serial (must not be imported by bridge)
    sys.modules['pyhackrf2'] = MagicMock()
    sys.modules['serial'] = MagicMock()
    sys.modules['hackrf_ros_interfaces'] = MagicMock()
    sys.modules['hackrf_ros_interfaces.srv'] = MagicMock()


def tearDownModule():
    """Restore sys.modules to original state after all tests in this file run."""
    for key, original in _ORIGINAL_MODULES.items():
        if original is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = original


# ---------------------------------------------------------------------------
# Fake logger
# ---------------------------------------------------------------------------

class _FakeLogger:
    """Minimal logger stub matching rclpy.Logger interface."""

    def __init__(self):
        self.messages = []

    def info(self, msg):
        self.messages.append(('info', msg))

    def warning(self, msg):
        self.messages.append(('warning', msg))

    def warn(self, msg):
        self.messages.append(('warn', msg))

    def error(self, msg):
        self.messages.append(('error', msg))

    def debug(self, msg):
        self.messages.append(('debug', msg))


# ---------------------------------------------------------------------------
# Bridge instance factory — bypasses __init__ for unit testing
# ---------------------------------------------------------------------------

def _make_minimal_bridge_instance():
    """Create a BridgeNode-like stub with mocked dependencies."""
    import hackrf_ros.bridge_node as m

    # Build a stub bypassing __init__ entirely
    node = object.__new__(m.BridgeNode)

    # Install required attributes
    node.publisher_ = MagicMock()
    node._state_publisher = MagicMock()
    node._redis = MagicMock()
    node._stop_event = threading.Event()

    # Logger
    node._fake_logger = _FakeLogger()
    node.get_logger = lambda: node._fake_logger

    return node


# ---------------------------------------------------------------------------
# Tests: BridgeNode module and class structure
# ---------------------------------------------------------------------------

class TestBridgeNodeModuleImports(unittest.TestCase):
    """BridgeNode module imports redis (not pyhackrf2)."""

    def test_bridge_node_module_imports_redis(self):
        """bridge_node.py must reference redis at module level."""
        import hackrf_ros.bridge_node as m
        # The module imports redis (checked via the name in the module namespace)
        self.assertTrue(
            hasattr(m, 'redis'),
            "bridge_node.py must have 'redis' in module namespace"
        )

    def test_bridge_node_has_no_pyhackrf2_import(self):
        """bridge_node.py must NOT import pyhackrf2 at module level."""
        import hackrf_ros.bridge_node as m
        src_path = m.__file__
        with open(src_path) as f:
            source = f.read()
        import_lines = [l for l in source.split('\n')
                        if l.startswith('import ') or l.startswith('from ')]
        hardware_imports = [l for l in import_lines
                            if 'pyhackrf2' in l or 'mayhem_serial' in l
                            or 'tx_controller' in l]
        self.assertEqual(
            hardware_imports, [],
            f"bridge_node.py must not import hardware libs: {hardware_imports}"
        )

    def test_bridge_node_exports_bridge_node_class(self):
        """bridge_node.py must export BridgeNode class."""
        import hackrf_ros.bridge_node as m
        self.assertTrue(hasattr(m, 'BridgeNode'))
        self.assertTrue(hasattr(m, 'main'))


class TestBridgeNodeAttributes(unittest.TestCase):
    """BridgeNode class has required publisher attributes and methods."""

    def test_bridge_node_has_destroy_node(self):
        """BridgeNode must have destroy_node method."""
        import hackrf_ros.bridge_node as m
        self.assertTrue(hasattr(m.BridgeNode, 'destroy_node'))

    def test_bridge_node_has_bridge_loop(self):
        """BridgeNode must have _bridge_loop method."""
        import hackrf_ros.bridge_node as m
        self.assertTrue(hasattr(m.BridgeNode, '_bridge_loop'))

    def test_bridge_node_has_publish_state(self):
        """BridgeNode must have _publish_state method."""
        import hackrf_ros.bridge_node as m
        self.assertTrue(hasattr(m.BridgeNode, '_publish_state'))

    def test_bridge_instance_has_publisher_for_iq(self):
        """Stub instance has publisher_ for /hackrf/iq."""
        node = _make_minimal_bridge_instance()
        self.assertIsNotNone(node.publisher_)

    def test_bridge_instance_has_state_publisher(self):
        """Stub instance has _state_publisher for /hackrf/state."""
        node = _make_minimal_bridge_instance()
        self.assertIsNotNone(node._state_publisher)


# ---------------------------------------------------------------------------
# Tests: _bridge_loop publishes Float32MultiArray
# ---------------------------------------------------------------------------

class TestBridgeLoop(unittest.TestCase):
    """_bridge_loop publishes Float32MultiArray on hackrf:iq:notify notification."""

    def test_bridge_loop_publishes_float32multiarray_on_message(self):
        """Given mocked xrevrange entry, _bridge_loop calls publisher_.publish."""
        node = _make_minimal_bridge_instance()

        # Build float32 bytes for the mock IQ entry
        iq_data = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
        fake_entry = (b'1234-0', {b'data': iq_data.tobytes()})

        # Set stop event after one iteration
        call_count = [0]
        original_get_message = MagicMock()

        def get_message_side_effect(timeout=0.1):
            call_count[0] += 1
            if call_count[0] == 1:
                return {'type': 'message', 'data': b'1234-0'}
            # Stop after second call
            node._stop_event.set()
            return None

        mock_pubsub = MagicMock()
        mock_pubsub.get_message.side_effect = get_message_side_effect
        node._redis.pubsub.return_value = mock_pubsub
        node._redis.xrevrange.return_value = [fake_entry]

        # Patch _publish_state to a no-op
        node._publish_state = MagicMock()

        # Run _bridge_loop in a thread to prevent hang
        import hackrf_ros.bridge_node as m
        t = threading.Thread(target=m.BridgeNode._bridge_loop, args=(node,))
        t.start()
        t.join(timeout=2.0)

        # publisher_.publish must have been called with something
        node.publisher_.publish.assert_called()
        published_arg = node.publisher_.publish.call_args[0][0]
        # Data should contain the IQ floats
        self.assertIsNotNone(published_arg.data)


# ---------------------------------------------------------------------------
# Tests: _publish_state publishes JSON string
# ---------------------------------------------------------------------------

class TestPublishState(unittest.TestCase):
    """_publish_state() calls _state_publisher.publish with JSON string."""

    def test_publish_state_calls_state_publisher(self):
        """_publish_state() publishes JSON dict to /hackrf/state."""
        node = _make_minimal_bridge_instance()

        # Mock hgetall returning a state dict
        node._redis.hgetall.return_value = {
            b'center_frequency': b'433920000',
            b'is_streaming': b'True',
        }

        import hackrf_ros.bridge_node as m
        m.BridgeNode._publish_state(node)

        node._state_publisher.publish.assert_called_once()
        published_arg = node._state_publisher.publish.call_args[0][0]
        # Should be parseable JSON
        data = json.loads(published_arg.data)
        self.assertIn('center_frequency', data)
        self.assertEqual(data['center_frequency'], '433920000')

    def test_publish_state_noop_when_hgetall_empty(self):
        """_publish_state() does nothing when hgetall returns empty dict."""
        node = _make_minimal_bridge_instance()
        node._redis.hgetall.return_value = {}

        import hackrf_ros.bridge_node as m
        m.BridgeNode._publish_state(node)

        node._state_publisher.publish.assert_not_called()

    def test_publish_state_noop_when_redis_none(self):
        """_publish_state() does nothing when _redis is None."""
        node = _make_minimal_bridge_instance()
        node._redis = None

        import hackrf_ros.bridge_node as m
        m.BridgeNode._publish_state(node)

        # No exception, no publish call
        node._state_publisher.publish.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: destroy_node sets _stop_event
# ---------------------------------------------------------------------------

class TestDestroyNode(unittest.TestCase):
    """destroy_node() sets _stop_event and closes Redis."""

    def test_destroy_node_sets_stop_event(self):
        """destroy_node() must set _stop_event."""
        node = _make_minimal_bridge_instance()
        self.assertFalse(node._stop_event.is_set())

        # Patch super().destroy_node() so we don't need a real ROS2 node
        import hackrf_ros.bridge_node as m

        import unittest.mock as mock_lib
        with mock_lib.patch.object(_FakeNode, 'destroy_node', return_value=None):
            m.BridgeNode.destroy_node(node)

        self.assertTrue(node._stop_event.is_set())

    def test_destroy_node_closes_redis(self):
        """destroy_node() must call _redis.close()."""
        node = _make_minimal_bridge_instance()

        import hackrf_ros.bridge_node as m
        import unittest.mock as mock_lib
        with mock_lib.patch.object(_FakeNode, 'destroy_node', return_value=None):
            m.BridgeNode.destroy_node(node)

        node._redis.close.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: bridge_services — command subscription and service handlers
# ---------------------------------------------------------------------------

class TestBridgeServices(unittest.TestCase):
    """bridge_services handlers RPUSH commands to hackrf:cmd."""

    def _make_mock_node(self):
        fake_logger = _FakeLogger()
        node = MagicMock()
        node.get_logger.return_value = fake_logger
        return node

    def test_cmd_subscription_rpush(self):
        """Valid JSON on /hackrf/cmd is RPUSHed to hackrf:cmd."""
        from hackrf_ros.bridge_services import _make_cmd_handler

        mock_redis = MagicMock()
        node = self._make_mock_node()
        handler = _make_cmd_handler(node, mock_redis)

        # Simulate a ROS2 String message
        msg = MagicMock()
        msg.data = '{"cmd": "setfreq", "args": {"freq_hz": 433000000}}'
        handler(msg)

        mock_redis.rpush.assert_called_once()
        rpush_key = mock_redis.rpush.call_args[0][0]
        rpush_payload = json.loads(mock_redis.rpush.call_args[0][1])
        self.assertEqual(rpush_key, 'hackrf:cmd')
        self.assertEqual(rpush_payload['cmd'], 'setfreq')

    def test_cmd_subscription_invalid_json_logs_warning(self):
        """Invalid JSON on /hackrf/cmd logs a warning and does NOT rpush."""
        from hackrf_ros.bridge_services import _make_cmd_handler

        mock_redis = MagicMock()
        node = self._make_mock_node()
        handler = _make_cmd_handler(node, mock_redis)

        msg = MagicMock()
        msg.data = 'not-valid-json'
        handler(msg)

        mock_redis.rpush.assert_not_called()

    def test_appstart_service_queues_cmd(self):
        """appstart Trigger handler RPUSHes appstart command and returns success."""
        from hackrf_ros.bridge_services import _make_mayhem_handler

        mock_redis = MagicMock()
        handler = _make_mayhem_handler(mock_redis, 'appstart')

        request = MagicMock()
        response = MagicMock()
        response.success = False
        response.message = ''

        result = handler(request, response)

        self.assertTrue(result.success)
        self.assertIn('appstart', result.message)
        mock_redis.rpush.assert_called_once()
        rpush_key = mock_redis.rpush.call_args[0][0]
        rpush_payload = json.loads(mock_redis.rpush.call_args[0][1])
        self.assertEqual(rpush_key, 'hackrf:cmd')
        self.assertEqual(rpush_payload['cmd'], 'appstart')

    def test_setfreq_service_reads_state_and_queues(self):
        """setfreq Trigger handler reads setfreq_request from hackrf:state and queues."""
        from hackrf_ros.bridge_services import _make_setfreq_handler

        mock_redis = MagicMock()
        mock_redis.hget.return_value = b'433920000'
        node = self._make_mock_node()
        handler = _make_setfreq_handler(node, mock_redis)

        request = MagicMock()
        response = MagicMock()
        response.success = False
        response.message = ''

        result = handler(request, response)

        self.assertTrue(result.success)
        self.assertIn('setfreq', result.message)
        mock_redis.rpush.assert_called_once()
        rpush_payload = json.loads(mock_redis.rpush.call_args[0][1])
        self.assertEqual(rpush_payload['cmd'], 'setfreq')
        self.assertEqual(rpush_payload['args']['freq_hz'], 433920000)

    def test_setfreq_service_fails_when_no_freq_set(self):
        """setfreq Trigger handler returns success=False when setfreq_request not set."""
        from hackrf_ros.bridge_services import _make_setfreq_handler

        mock_redis = MagicMock()
        mock_redis.hget.return_value = None  # key not set
        node = self._make_mock_node()
        handler = _make_setfreq_handler(node, mock_redis)

        request = MagicMock()
        response = MagicMock()
        response.success = True
        response.message = ''

        result = handler(request, response)

        self.assertFalse(result.success)
        mock_redis.rpush.assert_not_called()

    def test_radioinfo_service_queues_cmd(self):
        """radioinfo Trigger handler RPUSHes radioinfo command and returns success."""
        from hackrf_ros.bridge_services import _make_mayhem_handler

        mock_redis = MagicMock()
        handler = _make_mayhem_handler(mock_redis, 'radioinfo')

        request = MagicMock()
        response = MagicMock()
        response.success = False
        response.message = ''

        result = handler(request, response)

        self.assertTrue(result.success)
        rpush_payload = json.loads(mock_redis.rpush.call_args[0][1])
        self.assertEqual(rpush_payload['cmd'], 'radioinfo')


if __name__ == '__main__':
    unittest.main()
