"""Unit tests for TXController — all Redis and pyhackrf2 calls mocked (no live hardware required)."""

import threading
import unittest
from unittest.mock import MagicMock, call, patch

import redis


class _Logger:
    """Minimal logger stub for testing."""

    def __init__(self):
        self.warnings = []
        self.errors = []
        self.infos = []
        self.audits = []

    def info(self, msg):
        self.infos.append(msg)

    def warning(self, msg):
        self.warnings.append(msg)

    def error(self, msg):
        self.errors.append(msg)


class _MockNode:
    """Minimal HackRFNode stub for TXController testing."""

    def __init__(self, tx_freq_filter_enabled=True, tx_skip_antenna_check=False):
        self._hackrf = MagicMock()
        self._device_lock = threading.RLock()
        self._stop_rx_if_running = MagicMock()
        self._start_rx_if_stopped = MagicMock()
        self._tx_freq_filter_enabled = tx_freq_filter_enabled
        self._tx_skip_antenna_check = tx_skip_antenna_check

    def get_parameter(self, name):
        mock_param = MagicMock()
        if name == 'tx_freq_filter_enabled':
            mock_param.bool_value = self._tx_freq_filter_enabled
        elif name == 'tx_skip_antenna_check':
            mock_param.bool_value = self._tx_skip_antenna_check
        else:
            mock_param.bool_value = False
        return mock_param


from hackrf_ros.tx_controller import (
    TXBlockedError,
    TXController,
    TXFreqBlockedError,
    TXHardBlockedError,
    TXNotAuthorizedError,
)


class TestFreqRestricted(unittest.TestCase):
    """Tests for _is_freq_restricted() — filter-gated band check."""

    def setUp(self):
        self.node = _MockNode()
        self.redis_mock = MagicMock()
        self.logger = _Logger()
        self.ctrl = TXController(self.node, self.redis_mock, self.logger)

    def test_ism_433mhz_not_restricted(self):
        """Test 1: 433 MHz (ISM band) is allowed."""
        self.assertFalse(self.ctrl._is_freq_restricted(433_000_000))

    def test_aviation_121mhz_restricted(self):
        """Test 2: 121.5 MHz (aviation VHF 108-137 MHz) is restricted."""
        self.assertTrue(self.ctrl._is_freq_restricted(121_500_000))

    def test_cellular_850mhz_restricted(self):
        """Test 3: 850 MHz (cellular 700-900 MHz) is restricted."""
        self.assertTrue(self.ctrl._is_freq_restricted(850_000_000))

    def test_atc_modes_1090mhz_restricted(self):
        """Test 4: 1090 MHz (ATC Mode S) is restricted."""
        self.assertTrue(self.ctrl._is_freq_restricted(1_090_000_000))


class TestFreqFilterActive(unittest.TestCase):
    """Tests for _freq_filter_active() — dual-disable logic."""

    def setUp(self):
        self.logger = _Logger()

    def _make_ctrl(self, ros_param=True, redis_key_value=None):
        node = _MockNode(tx_freq_filter_enabled=ros_param)
        redis_mock = MagicMock()
        if redis_key_value is None:
            redis_mock.get.return_value = None
        else:
            redis_mock.get.return_value = redis_key_value
        return TXController(node, redis_mock, self.logger)

    def test_filter_active_when_ros_param_true(self):
        """Test 5: Filter is active when ROS2 param=True regardless of Redis key."""
        ctrl = self._make_ctrl(ros_param=True, redis_key_value=b'disabled')
        self.assertTrue(ctrl._freq_filter_active())

    def test_filter_active_when_param_false_redis_absent(self):
        """Test 6: Filter is active when ROS2 param=False but Redis key is absent."""
        ctrl = self._make_ctrl(ros_param=False, redis_key_value=None)
        self.assertTrue(ctrl._freq_filter_active())

    def test_filter_inactive_when_both_disabled(self):
        """Test 7: Filter inactive only when ROS2 param=False AND Redis=b'disabled'."""
        ctrl = self._make_ctrl(ros_param=False, redis_key_value=b'disabled')
        self.assertFalse(ctrl._freq_filter_active())


class TestConsumeAuthToken(unittest.TestCase):
    """Tests for _consume_auth_token() — Lua GETDEL with native GETDEL fallback."""

    def setUp(self):
        self.node = _MockNode()
        self.logger = _Logger()

    def _make_ctrl(self, redis_mock):
        return TXController(self.node, redis_mock, self.logger)

    def test_returns_true_when_token_matches(self):
        """Test 8: Returns True when Lua/GETDEL returns matching token."""
        redis_mock = MagicMock()
        redis_mock.execute_command.return_value = b'abc'
        ctrl = self._make_ctrl(redis_mock)
        self.assertTrue(ctrl._consume_auth_token('abc'))

    def test_returns_false_when_key_absent(self):
        """Test 9: Returns False when GETDEL returns None (key absent/expired)."""
        redis_mock = MagicMock()
        redis_mock.execute_command.return_value = None
        ctrl = self._make_ctrl(redis_mock)
        self.assertFalse(ctrl._consume_auth_token('abc'))

    def test_returns_false_when_token_mismatch(self):
        """Test 10: Returns False when GETDEL returns different token (wrong token)."""
        redis_mock = MagicMock()
        redis_mock.execute_command.return_value = b'xyz'
        ctrl = self._make_ctrl(redis_mock)
        self.assertFalse(ctrl._consume_auth_token('abc'))

    def test_native_getdel_first_fallback_to_lua(self):
        """Test 11: Native GETDEL tried first; ResponseError falls back to Lua eval."""
        redis_mock = MagicMock()
        # Native GETDEL raises ResponseError (Redis < 6.2)
        redis_mock.execute_command.side_effect = redis.exceptions.ResponseError('unknown command')
        # Lua eval fallback returns the token
        redis_mock.eval.return_value = b'abc'
        ctrl = self._make_ctrl(redis_mock)
        result = ctrl._consume_auth_token('abc')
        self.assertTrue(result)
        # Ensure eval was called (Lua fallback executed)
        self.assertTrue(redis_mock.eval.called)


class TestAntennaConfirmation(unittest.TestCase):
    """Tests for open() antenna confirmation logic."""

    def setUp(self):
        self.logger = _Logger()

    def test_open_sets_confirmed_when_redis_key_is_one(self):
        """Test 12: open() sets _antenna_confirmed=True when Redis key = b'1'."""
        node = _MockNode(tx_skip_antenna_check=False)
        redis_mock = MagicMock()
        redis_mock.get.return_value = b'1'
        ctrl = TXController(node, redis_mock, self.logger)
        ctrl.open()
        self.assertTrue(ctrl._antenna_confirmed)

    def test_open_sets_unconfirmed_when_redis_key_absent(self):
        """Test 13: open() sets _antenna_confirmed=False when Redis key absent."""
        node = _MockNode(tx_skip_antenna_check=False)
        redis_mock = MagicMock()
        redis_mock.get.return_value = None
        ctrl = TXController(node, redis_mock, self.logger)
        ctrl.open()
        self.assertFalse(ctrl._antenna_confirmed)

    def test_open_bypasses_check_with_skip_param(self):
        """Test 14: open() sets _antenna_confirmed=True when tx_skip_antenna_check=True and logs warning."""
        node = _MockNode(tx_skip_antenna_check=True)
        redis_mock = MagicMock()
        ctrl = TXController(node, redis_mock, self.logger)
        ctrl.open()
        self.assertTrue(ctrl._antenna_confirmed)
        # Should log a warning about bypass
        self.assertTrue(any('skip' in w.lower() or 'bypass' in w.lower() or 'antenna' in w.lower()
                            for w in self.logger.warnings))


class TestStartTxGuards(unittest.TestCase):
    """Tests for start_tx() guard logic ordering."""

    IQ_BYTES = bytes(128)  # dummy IQ data

    def setUp(self):
        self.logger = _Logger()

    def _make_ctrl(self, antenna_confirmed=True, tx_freq_filter_enabled=True,
                   tx_skip_antenna_check=False, redis_override=None):
        node = _MockNode(tx_freq_filter_enabled=tx_freq_filter_enabled,
                         tx_skip_antenna_check=tx_skip_antenna_check)
        redis_mock = MagicMock()
        # Default: GETDEL returns None (no token)
        redis_mock.execute_command.return_value = None
        # Default: antenna key absent
        redis_mock.get.return_value = None
        if redis_override:
            redis_mock.get.side_effect = lambda key: redis_override.get(key)
        ctrl = TXController(node, redis_mock, self.logger)
        ctrl._antenna_confirmed = antenna_confirmed
        return ctrl, node, redis_mock

    def test_raises_tx_blocked_when_no_antenna(self):
        """Test 15: start_tx raises TXBlockedError when _antenna_confirmed=False."""
        ctrl, node, redis_mock = self._make_ctrl(antenna_confirmed=False)
        with self.assertRaises(TXBlockedError):
            ctrl.start_tx(433_000_000, 'token', self.IQ_BYTES)

    def test_raises_freq_blocked_when_restricted_and_filter_active(self):
        """Test 16: start_tx raises TXFreqBlockedError when freq restricted and filter active."""
        ctrl, node, redis_mock = self._make_ctrl(antenna_confirmed=True, tx_freq_filter_enabled=True)
        with self.assertRaises(TXFreqBlockedError):
            ctrl.start_tx(121_500_000, 'token', self.IQ_BYTES)

    def test_raises_not_authorized_when_no_valid_token(self):
        """Test 17: start_tx raises TXNotAuthorizedError when token not in Redis."""
        ctrl, node, redis_mock = self._make_ctrl(antenna_confirmed=True, tx_freq_filter_enabled=False)
        # Make freq filter inactive: ROS param=False, Redis key=b'disabled'
        redis_mock.get.side_effect = lambda key: b'disabled' if key == TXController.FREQ_OVERRIDE_KEY else None
        redis_mock.execute_command.return_value = None
        with self.assertRaises(TXNotAuthorizedError):
            ctrl.start_tx(433_000_000, 'token', self.IQ_BYTES)

    def test_freq_check_before_token_consumption(self):
        """Test 18: Restricted freq raises TXFreqBlockedError WITHOUT consuming the token."""
        ctrl, node, redis_mock = self._make_ctrl(antenna_confirmed=True, tx_freq_filter_enabled=True)
        # Token is present in Redis
        redis_mock.execute_command.return_value = b'mytoken'
        with self.assertRaises(TXFreqBlockedError):
            ctrl.start_tx(121_500_000, 'mytoken', self.IQ_BYTES)
        # Verify that GETDEL (execute_command with AUTH_KEY) was NOT called
        for c in redis_mock.execute_command.call_args_list:
            args = c[0]
            self.assertFalse(
                len(args) >= 2 and args[1] == TXController.AUTH_KEY,
                "Auth token should NOT be consumed before freq check"
            )


class TestStartTxSuccess(unittest.TestCase):
    """Tests for start_tx() happy path."""

    IQ_BYTES = bytes(64)

    def setUp(self):
        self.logger = _Logger()

    def _make_ctrl_with_valid_token(self):
        node = _MockNode(tx_freq_filter_enabled=False)
        redis_mock = MagicMock()
        # FREQ_OVERRIDE_KEY returns b'disabled' to fully disable filter
        redis_mock.get.side_effect = lambda key: b'disabled' if key == TXController.FREQ_OVERRIDE_KEY else None
        # Token matches
        redis_mock.execute_command.return_value = b'mytoken'
        ctrl = TXController(node, redis_mock, self.logger)
        ctrl._antenna_confirmed = True
        return ctrl, node, redis_mock

    def test_start_tx_calls_hardware_correctly(self):
        """Test 19: start_tx calls node._stop_rx_if_running, sleeps 0.1s, sets hackrf.buffer, calls hackrf.start_tx()."""
        ctrl, node, redis_mock = self._make_ctrl_with_valid_token()
        with patch('hackrf_ros.tx_controller.time') as mock_time:
            mock_time.sleep = MagicMock()
            ctrl.start_tx(433_000_000, 'mytoken', self.IQ_BYTES)
        node._stop_rx_if_running.assert_called_once()
        mock_time.sleep.assert_any_call(0.1)
        node._hackrf.start_tx.assert_called_once()
        self.assertEqual(node._hackrf.buffer, bytearray(self.IQ_BYTES))


class TestStopTx(unittest.TestCase):
    """Tests for stop_tx() behavior."""

    def setUp(self):
        self.logger = _Logger()

    def _make_ctrl(self):
        node = _MockNode()
        redis_mock = MagicMock()
        ctrl = TXController(node, redis_mock, self.logger)
        return ctrl, node

    def test_stop_tx_stops_hardware_and_resumes_rx(self):
        """Test 20: stop_tx calls hackrf.stop_tx(), sets _is_transmitting=False, calls node._start_rx_if_stopped()."""
        ctrl, node = self._make_ctrl()
        ctrl._is_transmitting = True
        with patch('hackrf_ros.tx_controller.time') as mock_time:
            mock_time.sleep = MagicMock()
            ctrl.stop_tx()
        node._hackrf.stop_tx.assert_called_once()
        self.assertFalse(ctrl._is_transmitting)
        node._start_rx_if_stopped.assert_called_once()

    def test_stop_tx_is_noop_when_not_transmitting(self):
        """Test 21: stop_tx is a no-op when _is_transmitting=False."""
        ctrl, node = self._make_ctrl()
        ctrl._is_transmitting = False
        ctrl.stop_tx()
        node._hackrf.stop_tx.assert_not_called()
        node._start_rx_if_stopped.assert_not_called()


class TestAuditLog(unittest.TestCase):
    """Tests for audit logging in start_tx()."""

    IQ_BYTES = bytes(64)

    def setUp(self):
        self.logger = _Logger()

    def test_start_tx_logs_audit_message(self):
        """Test 22: start_tx logs AUDIT message containing freq_hz and token hint."""
        node = _MockNode(tx_freq_filter_enabled=False)
        redis_mock = MagicMock()
        redis_mock.get.side_effect = lambda key: b'disabled' if key == TXController.FREQ_OVERRIDE_KEY else None
        redis_mock.execute_command.return_value = b'mytoken1'
        ctrl = TXController(node, redis_mock, self.logger)
        ctrl._antenna_confirmed = True
        with patch('hackrf_ros.tx_controller.time') as mock_time:
            mock_time.sleep = MagicMock()
            ctrl.start_tx(433_000_000, 'mytoken1', self.IQ_BYTES)
        # Check that audit info was logged (freq_hz in the log)
        audit_logs = [m for m in self.logger.infos if '433000000' in m or '433_000_000' in str(m)]
        self.assertGreater(len(audit_logs), 0, "Expected audit log with freq_hz")


class TestHardBlockedBands(unittest.TestCase):
    """Tests 23 and 24: ALWAYS_BLOCKED_BANDS override even when filter is fully disabled."""

    IQ_BYTES = bytes(64)

    def setUp(self):
        self.logger = _Logger()

    def _make_ctrl_filter_fully_disabled(self):
        """Create controller with both filter disable mechanisms active."""
        node = _MockNode(tx_freq_filter_enabled=False)
        redis_mock = MagicMock()
        # FREQ_OVERRIDE_KEY returns b'disabled' — filter fully disabled
        redis_mock.get.side_effect = lambda key: b'disabled' if key == TXController.FREQ_OVERRIDE_KEY else None
        # Valid token present
        redis_mock.execute_command.return_value = b'testtoken'
        ctrl = TXController(node, redis_mock, self.logger)
        ctrl._antenna_confirmed = True
        return ctrl, node, redis_mock

    def test_epirb_always_blocked_even_with_filter_disabled(self):
        """Test 23: TXHardBlockedError for EPIRB 406_050_000 Hz even when filter fully disabled."""
        ctrl, node, redis_mock = self._make_ctrl_filter_fully_disabled()
        with self.assertRaises(TXHardBlockedError):
            ctrl.start_tx(406_050_000, 'testtoken', self.IQ_BYTES)

    def test_adsb_always_blocked_and_token_not_consumed(self):
        """Test 24: TXHardBlockedError for ADS-B 1_090_000_000 Hz; token NOT consumed."""
        ctrl, node, redis_mock = self._make_ctrl_filter_fully_disabled()
        with self.assertRaises(TXHardBlockedError):
            ctrl.start_tx(1_090_000_000, 'testtoken', self.IQ_BYTES)
        # Verify that execute_command (native GETDEL) was NOT called with AUTH_KEY
        for c in redis_mock.execute_command.call_args_list:
            args = c[0]
            self.assertFalse(
                len(args) >= 2 and args[1] == TXController.AUTH_KEY,
                "Auth token must NOT be consumed when hard block fires"
            )
        # Also verify redis.eval was NOT called with AUTH_KEY
        for c in redis_mock.eval.call_args_list:
            args = c[0]
            self.assertFalse(
                len(args) >= 2 and TXController.AUTH_KEY in str(args),
                "Auth token must NOT be consumed (eval) when hard block fires"
            )


if __name__ == '__main__':
    unittest.main()
