"""Redis I/O bridge helper class for hackrf_driver.

Provides RedisBridge — a standalone class that owns the redis-py connection,
a daemon thread for all Redis I/O, and command dispatch to a driver object.
The driver uses this class so it never calls redis-py directly.

Independently testable without ROS2.

Follows the MayhemSerial pattern exactly (D-10).
Implements D-14: publishes to hackrf:iq:notify after each XADD.
"""
from __future__ import annotations

import json
import queue
import threading

import numpy as np
import redis

from hackrf_driver.tx_controller import TXController, TXBlockedError, TXFreqBlockedError, TXNotAuthorizedError  # noqa: F401


def _handle_start_tx(driver, cmd):
    """Start TX: read IQ from Redis, validate, call TXController.start_tx (TX-07, D-12)."""
    freq_hz = int(cmd.get('freq_hz', 0))
    auth_token = cmd.get('auth_token', '')
    txvga_gain = int(cmd.get('txvga_gain', 0))
    iq_data_key = cmd.get('iq_data_key', TXController.IQ_DATA_KEY)
    # Read IQ bytes from driver._redis (driver exposes ._redis directly per D-13)
    iq_bytes = driver._redis.get(iq_data_key) or b''
    try:
        driver._tx_controller.start_tx(freq_hz, auth_token, bytes(iq_bytes), txvga_gain)
    except TXBlockedError as e:
        driver._logger.warning(f'TX BLOCKED (antenna not confirmed): {e}')
    except TXFreqBlockedError as e:
        driver._logger.warning(f'TX BLOCKED (restricted frequency {freq_hz} Hz): {e}')
    except TXNotAuthorizedError as e:
        driver._logger.warning(f'TX BLOCKED (no valid auth token): {e}')


def _handle_stop_tx(driver, cmd):
    """Stop TX and resume RX (TX-07, D-12)."""
    driver._tx_controller.stop_tx()


# Module-level command handler dispatch table (D-06)
# driver exposes same method names as HackRFNode did — interface is identical
_COMMAND_HANDLERS = {
    'setfreq':         lambda driver, p: driver._set_center_frequency(p['freq_hz']),
    'set_sample_rate': lambda driver, p: driver._set_sample_rate(p['sample_rate']),
    'set_lna_gain':    lambda driver, p: driver._set_lna_gain(p['lna_gain']),
    'set_vga_gain':    lambda driver, p: driver._set_vga_gain(p['vga_gain']),
    'set_amp_enabled': lambda driver, p: driver._set_amp_enabled(p['enabled']),
    'appstart':        lambda driver, p: driver._mayhem.appstart(p['app_name']),
    'serial_setfreq':  lambda driver, p: driver._mayhem.setfreq(int(p['freq_hz'])),
    'start_rx':        lambda driver, p: driver._start_rx_if_stopped(),
    'stop_rx':         lambda driver, p: driver._stop_rx_if_running(),
    'start_tx':        _handle_start_tx,
    'stop_tx':         _handle_stop_tx,
}


class RedisBridge:
    """Redis I/O bridge for hackrf_driver.

    Owns a daemon thread that:
      - Drains _iq_queue and XADDs IQ chunks to hackrf:iq:stream (RED-01)
      - XREADs hackrf:cmd stream and dispatches commands to the driver (RED-03)
      - Exposes publish_state() for on-change HSET to hackrf:state (RED-02)
      - Publishes to hackrf:iq:notify after each XADD (D-14)

    Thread-safe: publish_state() can be called from any thread.

    Usage::

        bridge = RedisBridge(iq_queue, driver_ref, logger, maxlen=10000)
        if bridge.open():
            bridge.publish_state({'center_frequency': 433920000})
        # on shutdown:
        bridge.close()

    Reconnect detection::

        if bridge.needs_reconnect:
            bridge.close()
            bridge.open()
    """

    STREAM_KEY = 'hackrf:iq:stream'   # RED-05 / D-12
    STATE_KEY  = 'hackrf:state'       # RED-05 / D-12
    CMD_KEY    = 'hackrf:cmd'         # RED-05 / D-12
    NOTIFY_KEY = 'hackrf:iq:notify'   # D-14: Pub/Sub notification channel

    def __init__(self, iq_queue: queue.Queue, node, logger,
                 maxlen: int = 10000) -> None:
        """Initialise RedisBridge.

        Args:
            iq_queue: Thread-safe queue of raw int8 IQ byte chunks from _rx_callback.
            node: Driver/node reference for command dispatch.
            logger: Object with .info(), .warning(), .error() methods
                    (compatible with rclpy.Logger or logging.Logger).
            maxlen: Redis Stream MAXLEN for approximate trimming (D-04).
        """
        self._iq_queue = iq_queue
        self._node = node
        self._logger = logger
        self._maxlen = maxlen

        self._redis: redis.Redis | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()   # protect concurrent publish_state() calls

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> bool:
        """Connect to Redis and start daemon thread.

        Connects to localhost:6379 with decode_responses=False (D-01, binary IQ data).
        Returns True on success. Returns False (no raise) on ConnectionError (D-02).
        """
        try:
            self._redis = redis.Redis(host='localhost', port=6379, decode_responses=False)
            self._redis.ping()
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._bridge_loop,
                daemon=True,
                name='redis_bridge',
            )
            self._thread.start()
            self._logger.info('RedisBridge connected to Redis at localhost:6379')
            return True
        except redis.exceptions.ConnectionError as e:
            self._logger.warning(
                f'RedisBridge: Redis unavailable at startup: {e}. '
                'IQ will stream on topics only.'
            )
            self._redis = None
            return False

    def close(self) -> None:
        """Signal bridge thread to stop.

        Sets _stop_event so the daemon thread exits its loop.
        Thread is a daemon — no join required; will exit with process.
        """
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Reconnect detection
    # ------------------------------------------------------------------

    @property
    def needs_reconnect(self) -> bool:
        """True when the bridge thread has lost the Redis connection.

        Driver polls this to detect when Redis has disconnected
        and a re-open is needed.
        """
        return self._stop_event.is_set() and self._thread is not None

    # ------------------------------------------------------------------
    # State publishing (thread-safe — called from any thread)
    # ------------------------------------------------------------------

    def publish_state(self, state: dict) -> None:
        """HSET hackrf:state with the given mapping.

        Thread-safe: may be called from the driver or any other thread.
        No-op if Redis is not connected (D-02 graceful degradation).

        Args:
            state: Dict of state values; all values are str-coerced before HSET.
        """
        if self._redis is None:
            return
        try:
            with self._state_lock:
                self._redis.hset(
                    self.STATE_KEY,
                    mapping={k: str(v) for k, v in state.items()}
                )
        except redis.exceptions.RedisError as e:
            self._logger.warning(f'RedisBridge: publish_state failed: {e}')

    # ------------------------------------------------------------------
    # Bridge daemon thread
    # ------------------------------------------------------------------

    def _bridge_loop(self) -> None:
        """Main daemon thread: drain IQ queue and consume commands.

        Uses a short-block XREAD (block=200ms) so _stop_event is checked
        regularly — must NOT use block=0 (RESEARCH.md anti-patterns).

        Pattern 3 (RESEARCH.md): capture last_cmd_id return value on every
        iteration to avoid replaying commands after reconnect.
        """
        last_cmd_id = b'$'   # only new commands from this point forward
        while not self._stop_event.is_set():
            self._drain_iq_queue()
            last_cmd_id = self._poll_commands(last_cmd_id)  # CRITICAL: capture return value

    def _drain_iq_queue(self) -> None:
        """Non-blocking drain of IQ queue; XADD each chunk to hackrf:iq:stream."""
        while not self._stop_event.is_set():
            try:
                chunk: bytes = self._iq_queue.get_nowait()
            except queue.Empty:
                break
            self._xadd_iq(chunk)

    def _xadd_iq(self, chunk: bytes) -> None:
        """Convert int8 IQ bytes to float32 and XADD to hackrf:iq:stream.

        Conversion (D-03): raw int8 bytes [I, Q, I, Q, ...] → float32 interleaved
        array divided by 128.0 to normalise to [-1.0, 1.0].

        D-14: publishes entry_id to hackrf:iq:notify after each XADD so bridge
        can subscribe with low latency.

        On RedisError: logs warning and sets _stop_event to trigger reconnect.
        """
        try:
            iq = np.frombuffer(chunk, dtype=np.int8).reshape(-1, 2)
            n_pairs = len(iq)
            float32_arr = np.empty(n_pairs * 2, dtype=np.float32)
            float32_arr[0::2] = iq[:, 0].astype(np.float32) / 128.0  # I channel
            float32_arr[1::2] = iq[:, 1].astype(np.float32) / 128.0  # Q channel
            entry_id = self._redis.xadd(
                self.STREAM_KEY,
                {b'data': float32_arr.tobytes()},
                maxlen=self._maxlen,
                approximate=True,
            )
            # D-14: notify subscribers that a new IQ entry is available
            self._redis.publish(self.NOTIFY_KEY, entry_id)
        except redis.exceptions.RedisError as e:
            self._logger.warning(f'RedisBridge: XADD failed: {e}')
            self._stop_event.set()   # trigger reconnect detection

    def _poll_commands(self, last_id: bytes) -> bytes:
        """XREAD hackrf:cmd with 200ms block; dispatch any commands received.

        Args:
            last_id: Stream cursor — only entries after this ID are returned.

        Returns:
            Updated cursor (last entry_id seen, or original last_id if no entries).
        """
        try:
            results = self._redis.xread({self.CMD_KEY: last_id}, count=10, block=200)
        except redis.exceptions.RedisError as e:
            self._logger.warning(f'RedisBridge: XREAD failed: {e}')
            self._stop_event.set()
            return last_id
        if not results:
            return last_id
        for _stream, entries in results:
            for entry_id, fields in entries:
                last_id = entry_id
                raw = fields.get(b'cmd', b'{}')
                try:
                    cmd = json.loads(raw.decode('utf-8'))
                    self._dispatch_command(cmd)
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    self._logger.warning(f'RedisBridge: malformed command: {e}')
        return last_id

    def _dispatch_command(self, cmd: dict) -> None:
        """Route a parsed command dict to the appropriate driver method.

        Uses the module-level _COMMAND_HANDLERS dispatch table (D-06).
        Logs warning for unknown actions. Catches all dispatch errors (D-06).

        Args:
            cmd: Parsed JSON command dict with at least an 'action' key.
        """
        action = cmd.get('action', '')
        handler = _COMMAND_HANDLERS.get(action)
        if handler is None:
            self._logger.warning(
                f'RedisBridge: unknown command action: {action!r}'
            )
            return
        try:
            handler(self._node, cmd)
        except Exception as e:
            self._logger.error(
                f'RedisBridge: command {action!r} failed: {e}'
            )
