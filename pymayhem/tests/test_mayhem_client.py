"""Unit tests for pymayhem MayhemSerial, MayhemClient, and domain modules.

Migrated from test/test_mayhem_serial.py (9 tests) plus 4 new tests for:
- MayhemClient context manager
- RadioDomain.radioinfo() delegation
- UnsafeMayhemClient safety boundary
- MayhemClient.capabilities property (D-09)
"""

import unittest
from unittest.mock import patch, MagicMock
import queue


from pymayhem._serial import MayhemSerial


# ---------------------------------------------------------------------------
# Helper: minimal logger stub matching rclpy.Logger interface
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Migrated tests (9) from test/test_mayhem_serial.py
# ---------------------------------------------------------------------------

class TestMayhemSerialParsing(unittest.TestCase):
    """Tests for MayhemSerial command parsing without hardware."""

    def setUp(self):
        self.ms = MayhemSerial('/dev/null', timeout=1.0)

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
        If the retry succeeds, _send_command returns the result and a warning
        was logged exactly once.
        """
        successful_response = ['ok']
        attempt_call_count = [0]

        def side_effect(cmd):
            attempt_call_count[0] += 1
            if attempt_call_count[0] == 1:
                raise TimeoutError('first attempt timed out')
            return successful_response

        mock_serial = MagicMock()
        mock_serial.is_open = True
        self.ms._serial = mock_serial

        # Capture warnings from the stdlib logger
        import logging
        logger = logging.getLogger('pymayhem.serial')
        warnings_captured = []

        class _WarningHandler(logging.Handler):
            def emit(self, record):
                if record.levelno == logging.WARNING:
                    warnings_captured.append(record.getMessage())

        handler = _WarningHandler()
        logger.addHandler(handler)
        try:
            with patch.object(self.ms, '_attempt_send', side_effect=side_effect):
                result = self.ms._send_command('appstart test')
        finally:
            logger.removeHandler(handler)

        self.assertEqual(result, successful_response)
        self.assertEqual(len(warnings_captured), 1)
        self.assertIn('appstart test', warnings_captured[0])

    def test_send_command_raises_on_double_timeout(self):
        """_send_command re-raises TimeoutError if retry also times out."""
        mock_serial = MagicMock()
        mock_serial.is_open = True
        self.ms._serial = mock_serial

        with patch.object(self.ms, '_attempt_send',
                          side_effect=TimeoutError('always times out')):
            with self.assertRaises(TimeoutError):
                self.ms._send_command('radioinfo')


# ---------------------------------------------------------------------------
# New tests (4)
# ---------------------------------------------------------------------------

class TestMayhemClientContextManager(unittest.TestCase):
    """Test MayhemClient context manager protocol (D-05)."""

    def test_context_manager_calls_open_and_close(self):
        """MayhemClient __enter__ calls open(), __exit__ calls close()."""
        from pymayhem import MayhemClient

        client = MayhemClient('/dev/null')
        with patch.object(client, 'open', return_value=True) as mock_open, \
             patch.object(client, 'close') as mock_close:
            with client as ctx:
                self.assertIs(ctx, client)
                mock_open.assert_called_once()
                mock_close.assert_not_called()
            mock_close.assert_called_once()


class TestRadioDomainDelegation(unittest.TestCase):
    """Test RadioDomain command delegation via MayhemClient."""

    def test_radioinfo_delegates_to_send_command(self):
        """client.radio.radioinfo() calls the underlying send_command with 'radioinfo'."""
        from pymayhem import MayhemClient

        client = MayhemClient('/dev/null')
        # RadioDomain captures the _send_command callable at construction time;
        # patch the domain's internal _send reference directly.
        with patch.object(client.radio, '_send',
                          return_value=['freq: 433920000', 'bandwidth: 1750000']) as mock_cmd:
            result = client.radio.radioinfo()

        mock_cmd.assert_called_once_with('radioinfo')
        self.assertEqual(result, {'freq': '433920000', 'bandwidth': '1750000'})


class TestUnsafeClientSafetyBoundary(unittest.TestCase):
    """Test that dangerous commands exist only on UnsafeMayhemClient (D-06)."""

    def test_flash_on_unsafe_not_on_base(self):
        """UnsafeMayhemClient has flash(); MayhemClient does not."""
        from pymayhem import MayhemClient, UnsafeMayhemClient

        self.assertTrue(hasattr(UnsafeMayhemClient, 'flash'))
        self.assertFalse(hasattr(MayhemClient, 'flash'))

    def test_write_memory_on_unsafe_not_on_base(self):
        """UnsafeMayhemClient has write_memory(); MayhemClient does not."""
        from pymayhem import MayhemClient, UnsafeMayhemClient

        self.assertTrue(hasattr(UnsafeMayhemClient, 'write_memory'))
        self.assertFalse(hasattr(MayhemClient, 'write_memory'))

    def test_dfu_on_unsafe_not_on_base(self):
        """UnsafeMayhemClient has dfu(); MayhemClient does not."""
        from pymayhem import MayhemClient, UnsafeMayhemClient

        self.assertTrue(hasattr(UnsafeMayhemClient, 'dfu'))
        self.assertFalse(hasattr(MayhemClient, 'dfu'))


class TestCapabilitiesProperty(unittest.TestCase):
    """Test MayhemClient.capabilities property (D-09)."""

    def _make_client_with_info(self, info_return):
        """Create a MayhemClient with system.info() mocked to return info_return."""
        from pymayhem import MayhemClient
        client = MayhemClient('/dev/null')
        client.system.info = MagicMock(return_value=info_return)
        return client

    def test_capabilities_with_setfreq_key_in_info(self):
        """capabilities['supports_setfreq'] is True when 'setfreq' key is in info()."""
        client = self._make_client_with_info(
            {'version': '2.1.0', 'setfreq': 'supported'}
        )
        caps = client.capabilities
        self.assertTrue(caps['supports_setfreq'])
        self.assertEqual(caps['firmware_version'], '2.1.0')

    def test_capabilities_without_setfreq_key_old_firmware(self):
        """capabilities['supports_setfreq'] is False when version < 2.1.0 and no setfreq key."""
        client = self._make_client_with_info({'version': '2.0.1'})
        caps = client.capabilities
        self.assertFalse(caps['supports_setfreq'])
        self.assertEqual(caps['firmware_version'], '2.0.1')

    def test_capabilities_returns_safe_defaults_on_exception(self):
        """capabilities returns safe defaults dict when info() raises an exception."""
        from pymayhem import MayhemClient
        client = MayhemClient('/dev/null')
        client.system.info = MagicMock(side_effect=TimeoutError('device not responding'))
        caps = client.capabilities
        self.assertEqual(caps['firmware_version'], 'unknown')
        self.assertFalse(caps['supports_setfreq'])
        self.assertFalse(caps['supports_appstart'])

    def test_capabilities_keys_present(self):
        """capabilities dict always has firmware_version, supports_setfreq, supports_appstart."""
        client = self._make_client_with_info({'version': '2.2.0'})
        caps = client.capabilities
        self.assertIn('firmware_version', caps)
        self.assertIn('supports_setfreq', caps)
        self.assertIn('supports_appstart', caps)


if __name__ == '__main__':
    unittest.main()
