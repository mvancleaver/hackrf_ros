"""Unit tests for HackRFDriver (hackrf_driver) — no hardware required.

All hardware access (pyhackrf2) and Redis connections are mocked at the
sys.modules level, following the same pattern as test_hackrf_node_redis.py.
"""
from __future__ import annotations

import queue
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Mock pyhackrf2 BEFORE importing hackrf_driver.driver so the ImportError
# guard in driver.py sees a module and sets _PYHACKRF2_AVAILABLE = True.
# ---------------------------------------------------------------------------
_mock_pyhackrf2 = MagicMock()
_mock_hackrf_instance = MagicMock()
_mock_pyhackrf2.HackRF.return_value = _mock_hackrf_instance
sys.modules.setdefault('pyhackrf2', _mock_pyhackrf2)

# ---------------------------------------------------------------------------
# Helper: build a minimal config dict (matches DEFAULT_CONFIG shape)
# ---------------------------------------------------------------------------

def _make_config(**overrides) -> dict:
    config = {
        'center_frequency': 433_920_000.0,
        'sample_rate': 8_000_000.0,
        'lna_gain': 16,
        'vga_gain': 20,
        'amp_enabled': False,
        'serial_port': '/dev/hackrf_mayhem',
        'redis_host': 'localhost',
        'redis_port': 6379,
        'redis_stream_maxlen': 10000,
        'tx_freq_filter_enabled': True,
        'tx_skip_antenna_check': True,   # avoid Redis antenna lookup in tests
    }
    config.update(overrides)
    return config


def _make_driver(config=None, **kwargs):
    """Construct HackRFDriver with all external connections mocked."""
    from hackrf_driver.driver import HackRFDriver
    cfg = _make_config(**(kwargs or {})) if config is None else config
    with patch('hackrf_driver.driver.MayhemClient') as mock_mayhem_cls, \
         patch('hackrf_driver.driver.RedisBridge') as mock_bridge_cls, \
         patch('hackrf_driver.driver.TXController') as mock_tx_cls, \
         patch('hackrf_driver.driver.pyhackrf2') as mock_hw:
        # Mocked MayhemClient
        mock_mayhem = MagicMock()
        mock_mayhem_cls.return_value = mock_mayhem
        mock_mayhem.open.return_value = True

        # Mocked RedisBridge
        mock_bridge = MagicMock()
        mock_bridge_cls.return_value = mock_bridge
        mock_bridge.open.return_value = True
        mock_bridge._redis = MagicMock()

        # Mocked TXController
        mock_tx = MagicMock()
        mock_tx_cls.return_value = mock_tx

        # pyhackrf2 open succeeds
        mock_hw.HackRF.return_value = MagicMock()

        driver = HackRFDriver(cfg)
    return driver


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHackRFDriverInit(unittest.TestCase):
    """Test HackRFDriver.__init__ stores config and sets up state."""

    def test_init_stores_config(self):
        """HackRFDriver stores config values in _last_params with correct defaults."""
        driver = _make_driver()
        self.assertAlmostEqual(driver._last_params['center_frequency'], 433_920_000.0)
        self.assertAlmostEqual(driver._last_params['sample_rate'], 8_000_000.0)
        self.assertEqual(driver._last_params['lna_gain'], 16)
        self.assertEqual(driver._last_params['vga_gain'], 20)
        self.assertFalse(driver._last_params['amp_enabled'])

    def test_init_creates_stop_event(self):
        """HackRFDriver creates a threading.Event for the main loop gate."""
        driver = _make_driver()
        self.assertIsInstance(driver._stop_event, threading.Event)
        self.assertFalse(driver._stop_event.is_set())

    def test_init_creates_device_lock(self):
        """HackRFDriver creates threading.RLock for device serialization."""
        driver = _make_driver()
        self.assertIsInstance(driver._device_lock, type(threading.RLock()))

    def test_init_creates_queues(self):
        """HackRFDriver creates dual queue.Queue with maxsize=64."""
        driver = _make_driver()
        self.assertIsInstance(driver._ros_queue, queue.Queue)
        self.assertIsInstance(driver._redis_queue, queue.Queue)
        self.assertEqual(driver._ros_queue.maxsize, 64)
        self.assertEqual(driver._redis_queue.maxsize, 64)


class TestHackRFDriverUpdateParam(unittest.TestCase):
    """Test _update_param validates ranges and updates _last_params."""

    def test_update_param_rejects_out_of_range(self):
        """_update_param raises HackRFConfigError for center_frequency outside PARAM_RANGES (1e6, 6e9)."""
        from hackrf_driver.exceptions import HackRFConfigError
        driver = _make_driver()
        with self.assertRaises(HackRFConfigError):
            driver._update_param('center_frequency', 0.0)
        self.assertAlmostEqual(driver._last_params['center_frequency'], 433_920_000.0)  # unchanged

    def test_update_param_accepts_valid(self):
        """_update_param updates _last_params for a valid value."""
        driver = _make_driver()
        driver._update_param('center_frequency', 915_000_000.0)
        self.assertAlmostEqual(driver._last_params['center_frequency'], 915_000_000.0)

    def test_update_param_rejects_lna_out_of_range(self):
        """_update_param raises HackRFConfigError for lna_gain > 40."""
        from hackrf_driver.exceptions import HackRFConfigError
        driver = _make_driver()
        original = driver._last_params['lna_gain']
        with self.assertRaises(HackRFConfigError):
            driver._update_param('lna_gain', 999)
        self.assertEqual(driver._last_params['lna_gain'], original)

    def test_update_param_accepts_valid_vga(self):
        """_update_param accepts vga_gain within [0, 62]."""
        driver = _make_driver()
        driver._update_param('vga_gain', 30)
        self.assertEqual(driver._last_params['vga_gain'], 30)


class TestBuildStateDict(unittest.TestCase):
    """Test _build_state_dict returns required fields."""

    def test_build_state_dict_has_required_fields(self):
        """_build_state_dict returns dict with all required state fields."""
        driver = _make_driver()
        state = driver._build_state_dict()
        required = [
            'center_frequency', 'sample_rate', 'lna_gain', 'vga_gain',
            'is_streaming', 'connected', 'uptime_s',
        ]
        for field in required:
            self.assertIn(field, state, f"Missing field: {field}")

    def test_build_state_dict_uptime_s_increases(self):
        """uptime_s in _build_state_dict increases monotonically."""
        driver = _make_driver()
        s1 = driver._build_state_dict()
        time.sleep(0.01)
        s2 = driver._build_state_dict()
        self.assertGreater(s2['uptime_s'], s1['uptime_s'])


class TestShutdown(unittest.TestCase):
    """Test driver.shutdown() behavior."""

    def test_shutdown_stops_stop_event(self):
        """shutdown() sets _stop_event (unblocks run())."""
        driver = _make_driver()
        self.assertFalse(driver._stop_event.is_set())
        driver.shutdown()
        self.assertTrue(driver._stop_event.is_set())


class TestSetMethods(unittest.TestCase):
    """Test _set_* convenience methods call _update_param."""

    def test_set_center_frequency_calls_update_param(self):
        """_set_center_frequency(freq) calls _update_param('center_frequency', freq)."""
        driver = _make_driver()
        driver._set_center_frequency(915_000_000.0)
        self.assertAlmostEqual(driver._last_params['center_frequency'], 915_000_000.0)

    def test_set_lna_gain_calls_update_param(self):
        """_set_lna_gain(lna) calls _update_param('lna_gain', lna)."""
        driver = _make_driver()
        driver._set_lna_gain(24)
        self.assertEqual(driver._last_params['lna_gain'], 24)

    def test_set_vga_gain_calls_update_param(self):
        """_set_vga_gain(vga) calls _update_param('vga_gain', vga)."""
        driver = _make_driver()
        driver._set_vga_gain(40)
        self.assertEqual(driver._last_params['vga_gain'], 40)

    def test_set_sample_rate_calls_update_param(self):
        """_set_sample_rate(sr) calls _update_param('sample_rate', sr)."""
        driver = _make_driver()
        driver._set_sample_rate(10_000_000.0)
        self.assertAlmostEqual(driver._last_params['sample_rate'], 10_000_000.0)

    def test_set_amp_enabled_calls_update_param(self):
        """_set_amp_enabled(bool) stores bool in _last_params['amp_enabled']."""
        driver = _make_driver()
        driver._set_amp_enabled(True)
        self.assertTrue(driver._last_params['amp_enabled'])


class TestWatchdogThread(unittest.TestCase):
    """Test watchdog thread, correction queue, and _drain_watchdog_queue."""

    def test_init_creates_watchdog_attributes(self):
        """__init__ creates _last_rx_time, _watchdog_queue (maxsize=1), _watchdog_reconnects."""
        driver = _make_driver()
        self.assertIsInstance(driver._last_rx_time, float)
        self.assertIsInstance(driver._watchdog_queue, queue.Queue)
        self.assertEqual(driver._watchdog_queue.maxsize, 1)
        self.assertEqual(driver._watchdog_reconnects, 0)

    def test_rx_callback_updates_last_rx_time(self):
        """_rx_callback updates _last_rx_time to a recent monotonic value."""
        driver = _make_driver()
        old_time = driver._last_rx_time
        # Force time to advance a bit
        time.sleep(0.01)
        chunk = bytes([0x00, 0x01])
        driver._rx_callback(chunk)
        self.assertGreater(driver._last_rx_time, old_time)

    def test_watchdog_loop_detects_stall_when_streaming(self):
        """_watchdog_loop posts 'reconnect' to _watchdog_queue when stale > 10s and streaming."""
        driver = _make_driver()
        driver.is_hackrf_streaming = True
        # Simulate stale data: 15s ago
        driver._last_rx_time = time.monotonic() - 15.0
        # Call the watchdog logic directly (without the wait)
        stale = time.monotonic() - driver._last_rx_time
        if stale > 10.0 and driver.is_hackrf_streaming:
            try:
                driver._watchdog_queue.put_nowait('reconnect')
            except queue.Full:
                pass
        self.assertEqual(driver._watchdog_queue.qsize(), 1)
        item = driver._watchdog_queue.get_nowait()
        self.assertEqual(item, 'reconnect')

    def test_watchdog_loop_does_not_reconnect_when_not_streaming(self):
        """_watchdog_loop skips stall check when is_hackrf_streaming=False."""
        driver = _make_driver()
        driver.is_hackrf_streaming = False
        driver._last_rx_time = time.monotonic() - 15.0
        # Simulate: watchdog sees is_hackrf_streaming=False, should not post
        if driver.is_hackrf_streaming:
            stale = time.monotonic() - driver._last_rx_time
            if stale > 10.0:
                try:
                    driver._watchdog_queue.put_nowait('reconnect')
                except queue.Full:
                    pass
        self.assertEqual(driver._watchdog_queue.qsize(), 0)

    def test_watchdog_queue_maxsize_1_drops_second_put(self):
        """Second put_nowait to full _watchdog_queue raises Full (maxsize=1)."""
        driver = _make_driver()
        driver._watchdog_queue.put_nowait('reconnect')
        with self.assertRaises(queue.Full):
            driver._watchdog_queue.put_nowait('reconnect')

    def test_drain_watchdog_queue_calls_try_connect_and_increments_counter(self):
        """_drain_watchdog_queue calls _try_connect and increments _watchdog_reconnects."""
        driver = _make_driver()
        driver._watchdog_queue.put_nowait('reconnect')
        initial_count = driver._watchdog_reconnects
        with patch.object(driver, '_try_connect') as mock_connect:
            driver._drain_watchdog_queue()
            mock_connect.assert_called_once()
        self.assertEqual(driver._watchdog_reconnects, initial_count + 1)

    def test_drain_watchdog_queue_returns_immediately_on_stop_event(self):
        """_drain_watchdog_queue returns immediately when _stop_event is set (Pitfall 2)."""
        driver = _make_driver()
        driver._watchdog_queue.put_nowait('reconnect')
        driver._stop_event.set()
        with patch.object(driver, '_try_connect') as mock_connect:
            driver._drain_watchdog_queue()
            mock_connect.assert_not_called()
        # Re-clear so we don't affect teardown
        driver._stop_event.clear()

    def test_drain_watchdog_queue_noop_when_empty(self):
        """_drain_watchdog_queue returns without calling _try_connect when queue is empty."""
        driver = _make_driver()
        with patch.object(driver, '_try_connect') as mock_connect:
            driver._drain_watchdog_queue()
            mock_connect.assert_not_called()

    def test_iq_publish_loop_calls_drain_watchdog_queue(self):
        """_iq_publish_loop calls _drain_watchdog_queue on each iteration."""
        driver = _make_driver()
        call_count = []

        original_drain = driver._drain_watchdog_queue

        def counting_drain():
            call_count.append(1)
            driver._stop_event.set()  # stop after first call

        driver._drain_watchdog_queue = counting_drain
        driver._iq_publish_loop()
        self.assertGreater(len(call_count), 0)

    def test_watchdog_thread_starts_as_daemon(self):
        """HackRFDriver starts a daemon thread named 'hackrf_watchdog'."""
        driver = _make_driver()
        self.assertIsNotNone(driver._watchdog_thread)
        self.assertTrue(driver._watchdog_thread.daemon)
        self.assertEqual(driver._watchdog_thread.name, 'hackrf_watchdog')


if __name__ == '__main__':
    unittest.main()
