"""HackRFDriver — standalone main loop replacing HackRFNode (no rclpy).

Implements D-12, D-13:
  - No rclpy import anywhere in this module
  - Uses threading.Event as the main loop stop gate
  - Uses threading.Timer for reconnect backoff
  - Wires TXController with primitive callables
  - Wires RedisBridge with iq_queue and self as driver reference
  - SIGINT/SIGTERM handlers call shutdown() cleanly

Exposes same method surface as HackRFNode for RedisBridge _COMMAND_HANDLERS
dispatch compatibility:
  _set_center_frequency, _set_sample_rate, _set_lna_gain, _set_vga_gain,
  _set_amp_enabled, _start_rx_if_stopped, _stop_rx_if_running,
  _build_state_dict, _mayhem
"""
from __future__ import annotations

import json
import logging
import queue
import signal
import threading
import time

from hackrf_driver.config import PARAM_RANGES, _MIN_RECONNECT_DELAY, _MAX_RECONNECT_DELAY
from hackrf_driver.exceptions import HackRFConfigError
from hackrf_driver.redis_bridge import RedisBridge
from hackrf_driver.tx_controller import TXController

# Optional pyhackrf2 — import inside try/except so tests can mock at sys.modules
try:
    import pyhackrf2
    _PYHACKRF2_AVAILABLE = True
except ImportError:
    pyhackrf2 = None  # type: ignore[assignment]
    _PYHACKRF2_AVAILABLE = False

try:
    from pymayhem.client import MayhemClient
except ImportError:
    MayhemClient = None  # type: ignore[assignment, misc]


class HackRFDriver:
    """Standalone HackRF driver: pyhackrf2 RX/TX + RedisBridge + TXController.

    Replaces HackRFNode without any rclpy dependency. Uses threading.Event
    as the main loop stop gate (run() blocks until shutdown() is called).

    Usage::

        config = load_config('/etc/hackrf/driver.yaml')
        driver = HackRFDriver(config)
        driver.run()         # blocks until SIGINT/SIGTERM or shutdown()
    """

    def __init__(self, config: dict) -> None:
        """Initialise HackRFDriver.

        Args:
            config: Configuration dict (from load_config or CLI overrides).
                    Expected keys mirror DEFAULT_CONFIG in config.py.
        """
        self._config = config
        self._logger = logging.getLogger('hackrf_driver')
        self._start_time = time.monotonic()  # for uptime_s in state dict

        # D-01: dual queue.Queue(maxsize=64)
        self._ros_queue = queue.Queue(maxsize=64)    # IQ consumer (ROS or dropped)
        self._redis_queue = queue.Queue(maxsize=64)  # IQ consumer for Redis XADD

        # Main loop gate (D-12)
        self._stop_event = threading.Event()

        # Device access serialisation
        self._device_lock = threading.RLock()

        # Device state
        self._hackrf = None
        self.is_hackrf_streaming = False

        # Reconnect loop state
        self._reconnect_delay = _MIN_RECONNECT_DELAY
        self._reconnect_timer = None

        # REL-01 / D-01: watchdog state — lock-free stall detection
        # _last_rx_time: float write is effectively atomic under CPython GIL (Open Question 1)
        self._last_rx_time: float = time.monotonic()  # Pitfall 3: init to now
        self._watchdog_queue: queue.Queue = queue.Queue(maxsize=1)  # Pitfall 1: maxsize=1
        self._watchdog_reconnects: int = 0  # D-03: metric counter

        # D-07: _last_params mirrors HackRFNode pattern
        self._last_params = {
            'center_frequency': float(config.get('center_frequency', 2447e6)),
            'sample_rate':      float(config.get('sample_rate', 8e6)),
            'lna_gain':         int(config.get('lna_gain', 16)),
            'vga_gain':         int(config.get('vga_gain', 20)),
            'amp_enabled':      bool(config.get('amp_enabled', False)),
        }

        # --- Mayhem serial (pymayhem) ---
        serial_port = config.get('serial_port', '/dev/hackrf_mayhem')
        if MayhemClient is not None:
            self._mayhem = MayhemClient(serial_port)
        else:
            self._logger.warning(
                'pymayhem not available — Mayhem serial commands will not work.'
            )
            self._mayhem = None

        # Epoch timestamp for IQ sequence numbers (REL-03)
        self._driver_epoch: int = int(time.time())

        # --- RedisBridge ---
        self._redis_bridge = RedisBridge(
            self._redis_queue,
            self,
            self._logger,
            maxlen=config.get('redis_stream_maxlen', 10000),
            driver_epoch=self._driver_epoch,
        )
        if not self._redis_bridge.open():
            self._logger.warning(
                'RedisBridge: Redis unavailable — IQ will not stream to Redis.'
            )

        # Expose _redis directly for _handle_start_tx in redis_bridge.py (D-13)
        self._redis = self._redis_bridge._redis

        # --- TXController ---
        self._tx_controller = TXController(
            redis_client=self._redis_bridge._redis,
            logger=self._logger,
            hackrf_getter=lambda: self._hackrf,
            device_lock=self._device_lock,
            stop_rx_fn=self._stop_rx_if_running,
            start_rx_fn=self._start_rx_if_stopped,
            freq_filter_enabled=config.get('tx_freq_filter_enabled', True),
            skip_antenna_check=config.get('tx_skip_antenna_check', False),
        )
        self._tx_controller.open()

        # --- SIGINT/SIGTERM handlers ---
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        # --- Attempt pyhackrf2 connection ---
        self._try_connect()

        # --- Watchdog daemon thread (REL-01 / D-01) ---
        # Started AFTER _try_connect() but BEFORE _iq_thread so _last_rx_time is valid
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            daemon=True,
            name='hackrf_watchdog',
        )
        self._watchdog_thread.start()

        # --- IQ publish daemon thread ---
        self._iq_thread = threading.Thread(
            target=self._iq_publish_loop,
            daemon=True,
            name='iq_publish_loop',
        )
        self._iq_thread.start()

        # --- Open Mayhem serial ---
        if self._mayhem is not None:
            if not self._mayhem.open():
                self._logger.warning(
                    'MayhemClient: serial open failed at startup — '
                    'Mayhem commands will not work until reconnected.'
                )

        self._logger.info('HackRFDriver initialised.')

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Block until shutdown() is called (SIGINT/SIGTERM or explicit call)."""
        self._stop_event.wait()
        self._logger.info('HackRFDriver run() returning — stop_event set.')

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _handle_signal(self, signum, frame) -> None:
        """Handle SIGINT/SIGTERM — trigger clean shutdown."""
        self._logger.info(f'Signal {signum} received — shutting down.')
        self.shutdown()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Stop TX, close Redis, close serial, stop pyhackrf2, unblock run()."""
        self._logger.info('HackRFDriver shutting down...')

        # TX-06: Stop TX FIRST
        if hasattr(self, '_tx_controller'):
            try:
                self._tx_controller.stop()
                self._logger.info('TXController stopped.')
            except Exception as e:
                self._logger.error(f'TXController stop failed: {e}')

        # Close RedisBridge
        if hasattr(self, '_redis_bridge'):
            try:
                self._redis_bridge.close()
                self._logger.info('RedisBridge closed.')
            except Exception as e:
                self._logger.error(f'RedisBridge close failed: {e}')

        # Close Mayhem serial
        if hasattr(self, '_mayhem') and self._mayhem is not None:
            try:
                self._mayhem.close()
                self._logger.info('MayhemClient closed.')
            except Exception as e:
                self._logger.error(f'MayhemClient close failed: {e}')

        # Cancel reconnect timer
        if self._reconnect_timer is not None:
            self._reconnect_timer.cancel()
            self._reconnect_timer = None

        # Stop pyhackrf2
        if self._hackrf is not None:
            with self._device_lock:
                if self.is_hackrf_streaming:
                    self._stop_event.set()
                    try:
                        self._hackrf.stop_rx()
                        self.is_hackrf_streaming = False
                        self._logger.info('HackRF RX stream stopped.')
                    except (RuntimeError, OSError) as e:
                        self._logger.error(f'stop_rx failed during shutdown: {e}')
                    finally:
                        self._stop_event.clear()
                try:
                    self._hackrf.close()
                    self._logger.info('HackRF device closed.')
                except (RuntimeError, OSError) as e:
                    self._logger.error(f'HackRF close() failed during shutdown: {e}')

        # Unblock run()
        self._stop_event.set()
        self._logger.info('HackRFDriver shutdown complete.')

    # ------------------------------------------------------------------
    # pyhackrf2 connection management (ported from HackRFNode)
    # ------------------------------------------------------------------

    def _try_connect(self) -> None:
        """Attempt to open HackRF. On failure, schedule retry with exponential backoff."""
        if pyhackrf2 is None:
            self._logger.warning('pyhackrf2 not available — hardware connection skipped.')
            return
        try:
            with self._device_lock:
                self._hackrf = pyhackrf2.HackRF()
                self._apply_last_params()
                self._hackrf.start_rx(self._rx_callback)
                self.is_hackrf_streaming = True
                self._reconnect_delay = _MIN_RECONNECT_DELAY
                self._last_rx_time = time.monotonic()  # Pitfall 3: reset stall timer on reconnect
                self._logger.info('HackRF connected and streaming.')
        except (RuntimeError, OSError) as e:
            self._logger.error(
                f'HackRF connect failed: {e}. Retrying in {self._reconnect_delay:.0f}s.'
            )
            self._hackrf = None
            self.is_hackrf_streaming = False
            self._reconnect_timer = threading.Timer(
                self._reconnect_delay, self._reconnect_callback
            )
            self._reconnect_timer.daemon = True
            self._reconnect_timer.start()
            self._reconnect_delay = min(self._reconnect_delay * 2, _MAX_RECONNECT_DELAY)
        except Exception as e:
            self._logger.error(
                f'Unexpected error opening HackRF: {e}. Retrying in {self._reconnect_delay:.0f}s.',
            )
            self._hackrf = None
            self.is_hackrf_streaming = False
            self._reconnect_timer = threading.Timer(
                self._reconnect_delay, self._reconnect_callback
            )
            self._reconnect_timer.daemon = True
            self._reconnect_timer.start()
            self._reconnect_delay = min(self._reconnect_delay * 2, _MAX_RECONNECT_DELAY)

    def _reconnect_callback(self) -> None:
        """Timer callback: cancel this timer, attempt reconnect."""
        if self._reconnect_timer is not None:
            self._reconnect_timer.cancel()
            self._reconnect_timer = None
        self._try_connect()

    # ------------------------------------------------------------------
    # Device configuration (ported from HackRFNode)
    # ------------------------------------------------------------------

    def _apply_last_params(self) -> None:
        """Apply stored parameters to _hackrf device. Called after reconnect."""
        self._hackrf.center_freq = int(self._last_params['center_frequency'])
        self._hackrf.sample_rate = int(self._last_params['sample_rate'])
        self._hackrf.lna_gain = int(self._last_params['lna_gain'])
        self._hackrf.vga_gain = int(self._last_params['vga_gain'])
        self._hackrf.amplifier_on = bool(self._last_params['amp_enabled'])

    def _configure_device(self) -> None:
        """Stop stream, apply _last_params to device, restart stream."""
        if not self._hackrf:
            self._logger.warning('_configure_device called but no device connected.')
            return
        with self._device_lock:
            if self.is_hackrf_streaming:
                self._stop_event.set()
                try:
                    self._hackrf.stop_rx()
                    self.is_hackrf_streaming = False
                    self._logger.info('Stopped RX stream for reconfiguration.')
                except (RuntimeError, OSError) as e:
                    self._logger.warning(f'stop_rx error (non-fatal): {e}')
                finally:
                    self._stop_event.clear()
                time.sleep(0.1)  # firmware settling (libhackrf issue #916)
            try:
                self._apply_last_params()
                self._hackrf.start_rx(self._rx_callback)
                self.is_hackrf_streaming = True
                self._reconnect_delay = _MIN_RECONNECT_DELAY
                self._logger.info(
                    f'HackRF reconfigured: freq={self._last_params["center_frequency"] / 1e6:.2f}MHz '
                    f'sr={self._last_params["sample_rate"] / 1e6:.2f}MSPS '
                    f'lna={self._last_params["lna_gain"]}dB vga={self._last_params["vga_gain"]}dB'
                )
            except (RuntimeError, OSError) as e:
                self._logger.error(f'Reconfiguration failed: {e}. Scheduling reconnect.')
                self._hackrf = None
                self.is_hackrf_streaming = False
                if self._reconnect_timer is not None:
                    self._reconnect_timer.cancel()
                self._reconnect_timer = threading.Timer(
                    self._reconnect_delay, self._reconnect_callback
                )
                self._reconnect_timer.daemon = True
                self._reconnect_timer.start()
                self._reconnect_delay = min(self._reconnect_delay * 2, _MAX_RECONNECT_DELAY)

    # ------------------------------------------------------------------
    # Parameter update (replaces ROS2 set_parameters / _on_parameter_event)
    # ------------------------------------------------------------------

    def _update_param(self, name: str, value) -> None:
        """Validate and store a parameter update; reconfigure device if needed.

        Validates against PARAM_RANGES. Updates self._last_params[name].
        Calls _configure_device() if hackrf is connected and param affects hardware.

        Args:
            name: Parameter name (e.g. 'center_frequency', 'lna_gain').
            value: New value. Will be coerced to the expected type.
        """
        if name in PARAM_RANGES:
            lo, hi = PARAM_RANGES[name]
            if not (lo <= value <= hi):
                raise HackRFConfigError(
                    f"Parameter '{name}' value {value} out of range [{lo}, {hi}]"
                )
        self._logger.info(f"Parameter '{name}' set to: {value}")
        if name in self._last_params:
            self._last_params[name] = value
            if self._hackrf:
                self._configure_device()
        if hasattr(self, '_redis_bridge'):
            self._redis_bridge.publish_state(self._build_state_dict())

    # ------------------------------------------------------------------
    # RX pipeline (ported from HackRFNode)
    # ------------------------------------------------------------------

    def _rx_callback(self, data: bytes) -> bool:
        """Bare enqueue — no numpy, no processing. Per D-05 / RX-07."""
        # REL-01 / D-02: update stall timer — float write is atomic under CPython GIL
        self._last_rx_time = time.monotonic()
        chunk = bytes(data)
        for q in (self._ros_queue, self._redis_queue):
            try:
                q.put_nowait(chunk)
            except queue.Full:
                try:
                    q.get_nowait()  # discard oldest (D-02 drop-oldest policy)
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(chunk)
                except queue.Full:
                    pass
        return self._stop_event.is_set()

    def _iq_publish_loop(self) -> None:
        """Daemon thread: drain _ros_queue and check watchdog correction queue."""
        while not self._stop_event.is_set():
            self._publish_iq()
            self._drain_watchdog_queue()  # REL-01 / D-01: drain correction queue
            time.sleep(0.005)

    def _publish_iq(self) -> None:
        """Drain _ros_queue. In standalone mode, data goes to Redis via _redis_queue.

        The _ros_queue is drained here to prevent unbounded growth when there
        is no ROS2 subscriber. The actual Redis XADD happens in RedisBridge
        daemon thread consuming _redis_queue.
        """
        if not self.is_hackrf_streaming:
            return
        try:
            while True:
                self._ros_queue.get_nowait()
        except queue.Empty:
            pass

    def _watchdog_loop(self) -> None:
        """Daemon thread: detect IQ stall and post reconnect request (REL-01 / D-01).

        Checks every 10s whether IQ data has been received recently.
        If not streaming or stale <= 10s, does nothing.
        Posts 'reconnect' to _watchdog_queue (maxsize=1) on stall detection.
        Never acquires _device_lock — lock-free detection per D-01.
        """
        while not self._stop_event.is_set():
            self._stop_event.wait(10.0)  # 10s check interval per D-02
            if self._stop_event.is_set():
                break
            if not self.is_hackrf_streaming:
                continue
            stale = time.monotonic() - self._last_rx_time
            if stale > 10.0:
                self._logger.warning(
                    f'Watchdog: no IQ data for {stale:.1f}s — posting reconnect request.'
                )
                try:
                    self._watchdog_queue.put_nowait('reconnect')
                except queue.Full:
                    pass  # previous request not yet drained — that's fine

    def _drain_watchdog_queue(self) -> None:
        """Drain watchdog correction queue — called from _iq_publish_loop (D-01).

        Checks _stop_event first to prevent reconnect during shutdown (Pitfall 2).
        If a reconnect token is present, increments _watchdog_reconnects and
        calls _try_connect() under existing _device_lock semantics.
        """
        if self._stop_event.is_set():
            return  # Pitfall 2: no reconnect during shutdown
        try:
            self._watchdog_queue.get_nowait()
        except queue.Empty:
            return
        self._logger.info('Watchdog: triggering reconnect via _try_connect().')
        self._watchdog_reconnects += 1
        self._try_connect()

    # ------------------------------------------------------------------
    # RX control (same method names as HackRFNode for dispatch compatibility)
    # ------------------------------------------------------------------

    def _start_rx_if_stopped(self) -> None:
        """Start RX streaming if not already running."""
        if not self.is_hackrf_streaming and self._hackrf:
            with self._device_lock:
                try:
                    self._hackrf.start_rx(self._rx_callback)
                    self.is_hackrf_streaming = True
                    self._logger.info('RX started via Redis command.')
                except Exception as e:
                    self._logger.error(f'_start_rx_if_stopped failed: {e}')

    def _stop_rx_if_running(self) -> None:
        """Stop RX streaming if currently running."""
        if self.is_hackrf_streaming and self._hackrf:
            with self._device_lock:
                self._stop_event.set()
                try:
                    self._hackrf.stop_rx()
                    self.is_hackrf_streaming = False
                    self._logger.info('RX stopped via Redis command.')
                except Exception as e:
                    self._logger.error(f'_stop_rx_if_running failed: {e}')
                finally:
                    self._stop_event.clear()

    # ------------------------------------------------------------------
    # Parameter setters (same method names as HackRFNode for RedisBridge dispatch)
    # ------------------------------------------------------------------

    def _set_center_frequency(self, freq_hz: float) -> None:
        """Set center_frequency via _update_param."""
        self._update_param('center_frequency', float(freq_hz))

    def _set_sample_rate(self, sample_rate: float) -> None:
        """Set sample_rate via _update_param."""
        self._update_param('sample_rate', float(sample_rate))

    def _set_lna_gain(self, lna_gain: int) -> None:
        """Set lna_gain via _update_param."""
        self._update_param('lna_gain', int(lna_gain))

    def _set_vga_gain(self, vga_gain: int) -> None:
        """Set vga_gain via _update_param."""
        self._update_param('vga_gain', int(vga_gain))

    def _set_amp_enabled(self, enabled: bool) -> None:
        """Set amp_enabled via _update_param (amp_enabled has no range check)."""
        self._last_params['amp_enabled'] = bool(enabled)
        if self._hackrf:
            self._configure_device()
        if hasattr(self, '_redis_bridge'):
            self._redis_bridge.publish_state(self._build_state_dict())

    # ------------------------------------------------------------------
    # State dict (ported from HackRFNode._build_state_dict)
    # ------------------------------------------------------------------

    def _build_state_dict(self) -> dict:
        """Build hackrf:state mapping from current driver state."""
        return {
            'center_frequency': self._last_params['center_frequency'],
            'sample_rate':      self._last_params['sample_rate'],
            'lna_gain':         self._last_params['lna_gain'],
            'vga_gain':         self._last_params['vga_gain'],
            'amp_enabled':      self._last_params['amp_enabled'],
            'is_streaming':     self.is_hackrf_streaming,
            'connected':        self._hackrf is not None,
            'uptime_s':         time.monotonic() - self._start_time,
            'active_app':       getattr(self._mayhem, '_active_app', '') if self._mayhem else '',
            'discovered_apps':  self._safe_json_apps(),
            'serial_connected': (
                self._mayhem is not None and
                getattr(self._mayhem, '_serial', None) is not None and
                getattr(self._mayhem._serial, '_is_open', False)
            ),
            'is_transmitting':  (
                hasattr(self, '_tx_controller') and self._tx_controller._is_transmitting
            ),
            'tx_freq': (
                getattr(self._tx_controller, '_last_tx_freq', 0)
                if hasattr(self, '_tx_controller') else 0
            ),
            'antenna_confirmed': (
                getattr(self._tx_controller, '_antenna_confirmed', False)
                if hasattr(self, '_tx_controller') else False
            ),
        }

    def _safe_json_apps(self) -> str:
        """Return JSON-serialisable discovered apps list as a string."""
        try:
            apps = getattr(self._mayhem, '_known_apps', []) if self._mayhem else []
            if not isinstance(apps, list):
                apps = []
            return json.dumps(apps)
        except (TypeError, ValueError):
            return '[]'
