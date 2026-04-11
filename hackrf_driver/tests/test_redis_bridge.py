"""Unit tests for RedisBridge (hackrf_driver) — all Redis calls mocked (no live Redis required)."""

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


from hackrf_driver.redis_bridge import RedisBridge


class TestRedisBridgeOpen(unittest.TestCase):
    """Tests for RedisBridge.open() lifecycle."""

    def setUp(self):
        self.logger = _Logger()
        self.q = queue.Queue()
        self.node = MagicMock()

    def test_open_returns_false_on_connection_error(self):
        """open() returns False when Redis is unreachable; does not propagate exception."""
        with patch('hackrf_driver.redis_bridge.redis.Redis') as MockRedis:
            mock_client = MockRedis.return_value
            mock_client.ping.side_effect = redis.exceptions.ConnectionError('refused')
            bridge = RedisBridge(self.q, self.node, self.logger)
            result = bridge.open()
        self.assertFalse(result)
        self.assertIsNone(bridge._redis)

    def test_open_returns_true_and_starts_thread_when_ping_succeeds(self):
        """open() returns True and daemon thread is alive when ping succeeds."""
        with patch('hackrf_driver.redis_bridge.redis.Redis') as MockRedis:
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
        with patch('hackrf_driver.redis_bridge.redis.Redis') as MockRedis:
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


class TestPublishMetrics(unittest.TestCase):
    """Tests for _publish_metrics() — OBS-01 / D-03."""

    def setUp(self):
        self.logger = _Logger()
        self.q = queue.Queue()
        self.node = MagicMock()
        # Provide driver attributes that _publish_metrics reads
        self.node._ros_queue = queue.Queue(maxsize=64)
        self.node._start_time = __import__('time').monotonic()
        self.node._last_rx_time = __import__('time').monotonic()
        self.node._watchdog_reconnects = 0

    def _make_bridge(self):
        bridge = RedisBridge(self.q, self.node, self.logger)
        bridge._redis = MagicMock()
        # Set up pipeline mock
        mock_pipe = MagicMock()
        bridge._redis.pipeline.return_value = mock_pipe
        return bridge, mock_pipe

    def test_publish_metrics_writes_all_9_fields(self):
        """_publish_metrics writes all 9 required fields to hackrf:metrics hash."""
        bridge, mock_pipe = self._make_bridge()
        bridge._publish_metrics()

        # Pipeline should have been used
        bridge._redis.pipeline.assert_called_once_with(transaction=False)
        mock_pipe.hset.assert_called_once()

        # Check all 9 required fields are in the mapping
        call_kwargs = mock_pipe.hset.call_args
        mapping = call_kwargs[1]['mapping']
        required_fields = [
            'iq_chunks_sec', 'iq_drops', 'rx_errors', 'cmd_errors',
            'queue_depth_iq', 'queue_depth_redis', 'uptime_s',
            'last_rx_time', 'watchdog_reconnects',
        ]
        for field in required_fields:
            self.assertIn(field, mapping, f"Missing metrics field: {field}")

    def test_publish_metrics_resets_iq_chunks_this_sec(self):
        """_publish_metrics resets _iq_chunks_this_sec to 0 after publish (Pitfall 5)."""
        bridge, mock_pipe = self._make_bridge()
        bridge._iq_chunks_this_sec = 42
        bridge._publish_metrics()
        self.assertEqual(bridge._iq_chunks_this_sec, 0)

    def test_publish_metrics_noop_when_redis_none(self):
        """_publish_metrics is a no-op when _redis is None."""
        bridge = RedisBridge(self.q, self.node, self.logger)
        bridge._redis = None
        # Should not raise
        bridge._publish_metrics()

    def test_publish_metrics_handles_redis_error(self):
        """_publish_metrics logs warning on RedisError, does not raise."""
        bridge, mock_pipe = self._make_bridge()
        mock_pipe.execute.side_effect = redis.exceptions.RedisError('pipe fail')
        bridge._publish_metrics()
        self.assertGreater(len(self.logger.warnings), 0)

    def test_xadd_iq_increments_iq_chunks_this_sec(self):
        """_xadd_iq increments _iq_chunks_this_sec on successful XADD."""
        bridge = RedisBridge(self.q, self.node, self.logger)
        bridge._redis = MagicMock()
        bridge._redis.xadd.return_value = b'1234-0'
        initial = bridge._iq_chunks_this_sec
        chunk = bytes([0, 64])
        bridge._xadd_iq(chunk)
        self.assertEqual(bridge._iq_chunks_this_sec, initial + 1)

    def test_bridge_loop_calls_publish_metrics_at_1hz(self):
        """_bridge_loop calls _publish_metrics when 1s has elapsed."""
        bridge = RedisBridge(self.q, self.node, self.logger)
        bridge._redis = MagicMock()
        bridge._redis.xread.return_value = []

        metrics_calls = []

        def fake_publish_metrics():
            metrics_calls.append(1)
            bridge._stop_event.set()  # stop after first metrics publish

        bridge._publish_metrics = fake_publish_metrics
        # Force _last_metrics_time to a long time ago so gate fires immediately
        bridge._last_metrics_time = __import__('time').monotonic() - 2.0

        bridge._bridge_loop()
        self.assertEqual(len(metrics_calls), 1)


class TestArchiveToDlq(unittest.TestCase):
    """Tests for _archive_to_dlq() — OBS-02 / D-05."""

    def setUp(self):
        self.logger = _Logger()
        self.q = queue.Queue()
        self.node = MagicMock()

    def _make_bridge(self):
        bridge = RedisBridge(self.q, self.node, self.logger)
        bridge._redis = MagicMock()
        return bridge

    def test_archive_to_dlq_writes_required_fields(self):
        """_archive_to_dlq writes action, error_type, error_msg, timestamp, args to DLQ."""
        bridge = self._make_bridge()
        cmd = {'action': 'setfreq', 'freq_hz': 100e6}
        bridge._archive_to_dlq('setfreq', 'HackRFError', 'device error', cmd)

        bridge._redis.xadd.assert_called_once()
        call_args = bridge._redis.xadd.call_args
        fields = call_args[0][1]
        self.assertIn('action', fields)
        self.assertIn('error_type', fields)
        self.assertIn('error_msg', fields)
        self.assertIn('timestamp', fields)
        self.assertIn('args', fields)

    def test_archive_to_dlq_redacts_auth_token(self):
        """_archive_to_dlq replaces auth_token value with [REDACTED] (D-05)."""
        bridge = self._make_bridge()
        cmd = {'action': 'start_tx', 'freq_hz': 100e6, 'auth_token': 'SECRET123'}
        bridge._archive_to_dlq('start_tx', 'TXNotAuthorizedError', 'bad token', cmd)

        call_args = bridge._redis.xadd.call_args
        fields = call_args[0][1]
        args_json = fields['args']
        args = json.loads(args_json)
        self.assertEqual(args.get('auth_token'), '[REDACTED]')
        self.assertNotIn('SECRET123', args_json)

    def test_archive_to_dlq_uses_maxlen_500(self):
        """_archive_to_dlq uses MAXLEN ~500 approximate trimming (D-06)."""
        bridge = self._make_bridge()
        cmd = {'action': 'setfreq', 'freq_hz': 100e6}
        bridge._archive_to_dlq('setfreq', 'HackRFError', 'error', cmd)

        call_kwargs = bridge._redis.xadd.call_args[1]
        self.assertEqual(call_kwargs.get('maxlen'), 500)
        self.assertTrue(call_kwargs.get('approximate'))

    def test_archive_to_dlq_writes_to_dlq_key(self):
        """_archive_to_dlq writes to hackrf:cmd:dlq stream."""
        bridge = self._make_bridge()
        cmd = {'action': 'setfreq'}
        bridge._archive_to_dlq('setfreq', 'HackRFError', 'error', cmd)

        call_args = bridge._redis.xadd.call_args[0]
        self.assertEqual(call_args[0], RedisBridge.DLQ_KEY)

    def test_archive_to_dlq_noop_when_redis_none(self):
        """_archive_to_dlq is a no-op when _redis is None."""
        bridge = RedisBridge(self.q, self.node, self.logger)
        bridge._redis = None
        # Should not raise
        bridge._archive_to_dlq('setfreq', 'HackRFError', 'error', {})

    def test_archive_to_dlq_never_raises_on_redis_error(self):
        """_archive_to_dlq never raises — DLQ is best-effort forensic."""
        bridge = self._make_bridge()
        bridge._redis.xadd.side_effect = redis.exceptions.RedisError('xadd fail')
        # Should not raise
        bridge._archive_to_dlq('setfreq', 'HackRFError', 'error', {'action': 'setfreq'})
        self.assertGreater(len(self.logger.warnings), 0)

    def test_dispatch_command_calls_archive_on_hackrf_error(self):
        """_dispatch_command calls _archive_to_dlq on HackRFError."""
        from hackrf_driver.exceptions import HackRFError

        bridge = self._make_bridge()
        self.node._set_center_frequency.side_effect = HackRFError('device error')

        with patch.object(bridge, '_archive_to_dlq') as mock_dlq:
            bridge._dispatch_command({'action': 'setfreq', 'freq_hz': 100e6})
            mock_dlq.assert_called_once()

    def test_dispatch_command_calls_archive_on_unexpected_exception(self):
        """_dispatch_command calls _archive_to_dlq on unexpected Exception."""
        bridge = self._make_bridge()
        self.node._set_center_frequency.side_effect = RuntimeError('unexpected')

        with patch.object(bridge, '_archive_to_dlq') as mock_dlq:
            bridge._dispatch_command({'action': 'setfreq', 'freq_hz': 100e6})
            mock_dlq.assert_called_once()

    def test_dispatch_command_increments_cmd_errors_count(self):
        """_dispatch_command increments _cmd_errors_count on error."""
        from hackrf_driver.exceptions import HackRFError

        bridge = self._make_bridge()
        self.node._set_center_frequency.side_effect = HackRFError('error')
        initial = bridge._cmd_errors_count
        bridge._dispatch_command({'action': 'setfreq', 'freq_hz': 100e6})
        self.assertEqual(bridge._cmd_errors_count, initial + 1)


if __name__ == '__main__':
    unittest.main()
