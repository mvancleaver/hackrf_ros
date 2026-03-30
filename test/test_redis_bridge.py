"""Unit tests for RedisBridge — all Redis calls mocked (no live Redis required)."""

import unittest
from unittest.mock import patch, MagicMock, call
import queue
import json
import numpy as np
import redis


class _Logger:
    """Minimal logger stub for testing."""

    def __init__(self):
        self.warnings = []
        self.errors = []
        self.infos = []

    def info(self, msg):
        self.infos.append(msg)

    def warning(self, msg):
        self.warnings.append(msg)

    def error(self, msg):
        self.errors.append(msg)


from hackrf_ros.redis_bridge import RedisBridge


class TestRedisBridgeOpen(unittest.TestCase):
    """Tests for RedisBridge.open() lifecycle."""

    def setUp(self):
        self.logger = _Logger()
        self.q = queue.Queue()
        self.node = MagicMock()

    def test_open_returns_false_on_connection_error(self):
        """open() returns False when Redis is unreachable; does not propagate exception."""
        with patch('hackrf_ros.redis_bridge.redis.Redis') as MockRedis:
            mock_client = MockRedis.return_value
            mock_client.ping.side_effect = redis.exceptions.ConnectionError('refused')
            bridge = RedisBridge(self.q, self.node, self.logger)
            result = bridge.open()
        self.assertFalse(result)
        self.assertIsNone(bridge._redis)

    def test_open_returns_true_and_starts_thread_when_ping_succeeds(self):
        """open() returns True and daemon thread is alive when ping succeeds."""
        with patch('hackrf_ros.redis_bridge.redis.Redis') as MockRedis:
            mock_client = MockRedis.return_value
            mock_client.ping.return_value = True
            # Prevent bridge loop from running
            bridge = RedisBridge(self.q, self.node, self.logger)
            bridge._stop_event.set()  # stop loop immediately after start
            result = bridge.open()
        self.assertTrue(result)
        self.assertIsNotNone(bridge._thread)


class TestRedisBridgeDrainIqQueue(unittest.TestCase):
    """Tests for _drain_iq_queue and _xadd_iq."""

    def setUp(self):
        self.logger = _Logger()
        self.q = queue.Queue()
        self.node = MagicMock()

    def _make_bridge_with_mock_redis(self):
        bridge = RedisBridge(self.q, self.node, self.logger, maxlen=100)
        bridge._redis = MagicMock()
        return bridge

    def test_drain_iq_queue_calls_xadd_with_float32_bytes(self):
        """_drain_iq_queue converts int8 bytes to float32 and calls xadd."""
        bridge = self._make_bridge_with_mock_redis()
        # 2 int8 bytes = 1 IQ pair
        chunk = bytes([0, 64])  # I=0, Q=64 as unsigned; treated as int8: I=0, Q=64
        self.q.put(chunk)

        bridge._drain_iq_queue()

        self.assertTrue(bridge._redis.xadd.called)
        call_args = bridge._redis.xadd.call_args
        # Check key
        self.assertEqual(call_args[0][0], 'hackrf:iq:stream')
        # Check maxlen and approximate
        self.assertEqual(call_args[1]['maxlen'], 100)
        self.assertTrue(call_args[1]['approximate'])
        # Check data field contains float32 bytes
        fields = call_args[0][1]
        self.assertIn(b'data', fields)
        float_bytes = fields[b'data']
        arr = np.frombuffer(float_bytes, dtype=np.float32)
        self.assertEqual(len(arr), 2)

    def test_float32_encoding_is_correct(self):
        """int8 pair (64, -128) encodes to float32 pair (0.5, -1.0)."""
        bridge = self._make_bridge_with_mock_redis()
        # int8 64 = 0x40, int8 -128 = 0x80 (unsigned)
        chunk = np.array([64, -128], dtype=np.int8).tobytes()
        self.q.put(chunk)

        bridge._drain_iq_queue()

        self.assertTrue(bridge._redis.xadd.called)
        fields = bridge._redis.xadd.call_args[0][1]
        arr = np.frombuffer(fields[b'data'], dtype=np.float32)
        self.assertAlmostEqual(float(arr[0]), 0.5, places=5)
        self.assertAlmostEqual(float(arr[1]), -1.0, places=5)


class TestRedisBridgePollCommands(unittest.TestCase):
    """Tests for _poll_commands."""

    def setUp(self):
        self.logger = _Logger()
        self.q = queue.Queue()
        self.node = MagicMock()

    def _make_bridge_with_mock_redis(self):
        bridge = RedisBridge(self.q, self.node, self.logger)
        bridge._redis = MagicMock()
        return bridge

    def test_poll_commands_parses_valid_json_and_calls_dispatch(self):
        """_poll_commands parses valid JSON cmd and calls _dispatch_command."""
        bridge = self._make_bridge_with_mock_redis()
        cmd_dict = {'action': 'setfreq', 'freq_hz': 433920000}
        cmd_bytes = json.dumps(cmd_dict).encode('utf-8')
        entry_id = b'1234567890-0'
        bridge._redis.xread.return_value = [
            (b'hackrf:cmd', [(entry_id, {b'cmd': cmd_bytes})])
        ]

        with patch.object(bridge, '_dispatch_command') as mock_dispatch:
            bridge._poll_commands(b'$')
            mock_dispatch.assert_called_once_with(cmd_dict)

    def test_poll_commands_logs_warning_on_malformed_json(self):
        """_poll_commands logs warning on malformed JSON without crashing."""
        bridge = self._make_bridge_with_mock_redis()
        entry_id = b'1234567890-0'
        bridge._redis.xread.return_value = [
            (b'hackrf:cmd', [(entry_id, {b'cmd': b'not-json'})])
        ]

        # Should not raise
        bridge._poll_commands(b'$')

        self.assertGreater(len(self.logger.warnings), 0)


class TestRedisBridgeDispatchCommand(unittest.TestCase):
    """Tests for _dispatch_command."""

    def setUp(self):
        self.logger = _Logger()
        self.q = queue.Queue()
        self.node = MagicMock()

    def _make_bridge(self):
        bridge = RedisBridge(self.q, self.node, self.logger)
        bridge._redis = MagicMock()
        return bridge

    def test_dispatch_command_routes_setfreq_to_node(self):
        """_dispatch_command routes 'setfreq' to node._set_center_frequency(freq_hz)."""
        bridge = self._make_bridge()
        bridge._dispatch_command({'action': 'setfreq', 'freq_hz': 100e6})
        self.node._set_center_frequency.assert_called_once_with(100e6)

    def test_dispatch_command_logs_warning_for_unknown_action(self):
        """_dispatch_command logs warning for unknown action without raising."""
        bridge = self._make_bridge()
        bridge._dispatch_command({'action': 'bogus'})
        self.assertGreater(len(self.logger.warnings), 0)
        self.assertIn('unknown command action', self.logger.warnings[0])


class TestRedisBridgePublishState(unittest.TestCase):
    """Tests for publish_state()."""

    def setUp(self):
        self.logger = _Logger()
        self.q = queue.Queue()
        self.node = MagicMock()

    def test_publish_state_calls_hset_when_connected(self):
        """publish_state() calls hset with str-coerced mapping when _redis is set."""
        bridge = RedisBridge(self.q, self.node, self.logger)
        bridge._redis = MagicMock()
        state = {'center_frequency': 433920000, 'is_streaming': True}

        bridge.publish_state(state)

        bridge._redis.hset.assert_called_once_with(
            'hackrf:state',
            mapping={'center_frequency': '433920000', 'is_streaming': 'True'}
        )

    def test_publish_state_is_noop_when_redis_is_none(self):
        """publish_state() is a no-op when _redis is None."""
        bridge = RedisBridge(self.q, self.node, self.logger)
        bridge._redis = None

        # Should not raise
        bridge.publish_state({'key': 'value'})
        # No hset calls happened (redis is None, no mock to check — just verifying no exception)


class TestRedisBridgeCloseAndReconnect(unittest.TestCase):
    """Tests for close() and needs_reconnect."""

    def setUp(self):
        self.logger = _Logger()
        self.q = queue.Queue()
        self.node = MagicMock()

    def test_close_sets_stop_event(self):
        """close() sets _stop_event."""
        with patch('hackrf_ros.redis_bridge.redis.Redis') as MockRedis:
            mock_client = MockRedis.return_value
            mock_client.ping.return_value = True
            bridge = RedisBridge(self.q, self.node, self.logger)
            bridge._stop_event.set()  # prevent bridge loop
            bridge.open()
            bridge._stop_event.clear()  # simulate running state

        bridge.close()
        self.assertTrue(bridge._stop_event.is_set())

    def test_needs_reconnect_true_when_stop_event_set_and_thread_not_none(self):
        """needs_reconnect is True when _stop_event is set and _thread is not None."""
        bridge = RedisBridge(self.q, self.node, self.logger)
        bridge._stop_event.set()
        bridge._thread = MagicMock()

        self.assertTrue(bridge.needs_reconnect)


if __name__ == '__main__':
    unittest.main()
