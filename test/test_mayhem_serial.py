"""Unit tests for MayhemSerial command parsing and retry behavior (D-12)."""

import unittest
from unittest.mock import patch, MagicMock, call
import queue
import logging


class _Logger:
    """Minimal logger stub for testing."""

    def __init__(self):
        self.warnings = []

    def info(self, msg):
        pass

    def warning(self, msg):
        self.warnings.append(msg)

    def error(self, msg):
        pass


from hackrf_ros.mayhem_serial import MayhemSerial


class TestMayhemSerialParsing(unittest.TestCase):
    """Tests for MayhemSerial command parsing without hardware."""

    def setUp(self):
        self.logger = _Logger()
        self.ms = MayhemSerial('/dev/null', self.logger, timeout=1.0)

    def _inject_response(self, lines: list):
        """Put lines into _response_queue for _send_command to consume."""
        for line in lines:
            self.ms._response_queue.put(line)

    def test_query_applist_parses_short_names(self):
        """query_applist parses the first token (short name) from each response line."""
        with patch.object(self.ms, '_send_command',
                          return_value=['capture full_name cat', 'scanner full2 cat2']):
            result = self.ms.query_applist()
        self.assertEqual(result, ['capture', 'scanner'])
        self.assertEqual(self.ms._known_apps, ['capture', 'scanner'])

    def test_radioinfo_parses_key_value(self):
        """radioinfo() returns a dict parsed from 'key: value' lines."""
        with patch.object(self.ms, '_send_command',
                          return_value=['freq: 433920000', 'bandwidth: 1750000']):
            result = self.ms.radioinfo()
        self.assertEqual(result, {'freq': '433920000', 'bandwidth': '1750000'})

    def test_appstart_returns_false_on_error(self):
        """appstart() returns False when response contains 'error'."""
        with patch.object(self.ms, '_send_command',
                          return_value=['error: app not found']):
            result = self.ms.appstart('nonexistent')
        self.assertFalse(result)

    def test_appstart_returns_true_on_success(self):
        """appstart() returns True when no 'error' in response."""
        with patch.object(self.ms, '_send_command', return_value=['ok']):
            result = self.ms.appstart('capture')
        self.assertTrue(result)

    def test_setfreq_returns_false_on_error(self):
        """setfreq() returns False when response contains 'error'."""
        with patch.object(self.ms, '_send_command',
                          return_value=['error: frequency out of range']):
            result = self.ms.setfreq(99999)
        self.assertFalse(result)

    def test_setfreq_returns_true_on_success(self):
        """setfreq() returns True when no 'error' in response."""
        with patch.object(self.ms, '_send_command', return_value=['ok']):
            result = self.ms.setfreq(433920000)
        self.assertTrue(result)

    def test_send_command_raises_timeout(self):
        """_send_command raises TimeoutError when queue.get always raises queue.Empty."""
        # Use a tiny timeout and patch _attempt_send to always raise TimeoutError
        self.ms._command_timeout = 0.01
        mock_serial = MagicMock()
        mock_serial.is_open = True
        self.ms._serial = mock_serial

        with patch.object(self.ms._response_queue, 'get', side_effect=queue.Empty):
            with self.assertRaises(TimeoutError):
                self.ms._send_command('applist')

    def test_send_command_retries_once_on_timeout(self):
        """_send_command logs a warning and retries once on TimeoutError (D-12).

        If first _attempt_send raises TimeoutError, the command retries once.
        If the retry succeeds, _send_command returns the result and
        logger.warning was called exactly once.
        """
        successful_response = ['ok']
        attempt_call_count = [0]

        def side_effect(cmd):
            attempt_call_count[0] += 1
            if attempt_call_count[0] == 1:
                raise TimeoutError('first attempt timed out')
            return successful_response

        with patch.object(self.ms, '_attempt_send', side_effect=side_effect):
            # _send_command acquires lock and calls _attempt_send
            mock_serial = MagicMock()
            mock_serial.is_open = True
            self.ms._serial = mock_serial

            result = self.ms._send_command('appstart test')

        self.assertEqual(result, successful_response)
        self.assertEqual(len(self.logger.warnings), 1)
        self.assertIn('appstart test', self.logger.warnings[0])

    def test_send_command_raises_on_double_timeout(self):
        """_send_command re-raises TimeoutError if retry also times out."""
        mock_serial = MagicMock()
        mock_serial.is_open = True
        self.ms._serial = mock_serial

        with patch.object(self.ms, '_attempt_send',
                          side_effect=TimeoutError('always times out')):
            with self.assertRaises(TimeoutError):
                self.ms._send_command('radioinfo')

        # Warning should have been logged before the retry
        self.assertEqual(len(self.logger.warnings), 1)


if __name__ == '__main__':
    unittest.main()
