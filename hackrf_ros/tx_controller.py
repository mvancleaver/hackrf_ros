"""TX guard and dispatch helper class for HackRF ROS2 driver.

Provides TXController — a standalone class that enforces all TX safety
guardrails before any hardware access:

  1. Antenna confirmation (per-session Redis key or skip parameter)
  2. Hard-blocked bands (EPIRB 406 MHz, ADS-B 1090 MHz) — NO bypass path
  3. Frequency allowlist (filter-gated, dual-disable: ROS param + Redis override)
  4. One-token-per-TX authorization (atomic GETDEL with Lua fallback for Redis < 6.2)

HackRFNode calls start_tx() and stop_tx(); TXController handles all guard
logic internally so HackRFNode contains no untested business rules.

Independently testable without ROS2.
"""
from __future__ import annotations

import threading
import time

import redis


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class TXBlockedError(Exception):
    """Antenna not confirmed — TX blocked until operator confirms antenna connected."""


class TXFreqBlockedError(Exception):
    """Frequency is on a restricted band and the frequency filter is active."""


class TXHardBlockedError(Exception):
    """Frequency is on an always-blocked band — NO bypass path exists."""


class TXNotAuthorizedError(Exception):
    """No valid one-time authorization token found in Redis."""


# ---------------------------------------------------------------------------
# Lua GETDEL fallback (Redis < 6.2 compatibility)
# Per RESEARCH.md Pattern 1 — atomic get-and-delete via Lua eval.
# ---------------------------------------------------------------------------

_LUA_GETDEL = """
local val = redis.call('GET', KEYS[1])
if val ~= false then
    redis.call('DEL', KEYS[1])
end
return val
"""


class TXController:
    """TX safety guard and pyhackrf2 dispatch controller.

    Enforces all TX guardrails in the correct order:
      1. Antenna confirmed?
      2. Hard-blocked band? (unconditional — no bypass)
      3. Frequency filter active AND restricted? (filter-gated)
      4. Auth token valid? (consumed atomically)

    Usage::

        ctrl = TXController(node, redis_client, logger)
        ctrl.open()           # read antenna confirmation from Redis
        ctrl.start_tx(freq_hz, auth_token, iq_bytes)
        ctrl.stop_tx()        # called automatically on node shutdown
    """

    # Class constants — Redis key names
    AUTH_KEY = 'hackrf:tx:auth'
    ANTENNA_KEY = 'hackrf:tx:antenna_confirmed'
    FREQ_OVERRIDE_KEY = 'hackrf:tx:freq_filter_override'
    IQ_DATA_KEY = 'hackrf:tx:iq_data'

    # Hard-blocked bands — checked UNCONDITIONALLY, no bypass path.
    # International law prohibitions: EPIRB distress + ADS-B aviation safety.
    ALWAYS_BLOCKED_BANDS = [
        (406_000_000, 406_100_000),        # EPIRB distress — international law
        (1_090_000_000, 1_090_000_001),    # ADS-B 1090ES — aviation safety
    ]

    # Filter-gated restricted bands (enforced only when _freq_filter_active()).
    # Source: D-04 / RESEARCH.md exact values.
    RESTRICTED_BANDS = [
        (108_000_000, 137_000_000),        # Aviation VHF
        (150_000_000, 174_000_000),        # Emergency/public safety
        (406_000_000, 406_100_000),        # EPIRB distress (also in ALWAYS_BLOCKED_BANDS)
        (450_000_000, 470_000_000),        # Emergency/public safety UHF
        (700_000_000, 900_000_000),        # Cellular LTE
        (960_000_000, 1_215_000_000),      # Aviation DME/TACAN
        (1_030_000_000, 1_030_000_001),    # ATC Mode C exact
        (1_090_000_000, 1_090_000_001),    # ATC Mode S exact (also in ALWAYS_BLOCKED_BANDS)
        (1_164_000_000, 1_215_000_000),    # GPS L5
        (1_559_000_000, 1_610_000_000),    # GPS L1/L2
        (1_700_000_000, 2_100_000_000),    # Cellular AWS/PCS
        (2_500_000_000, 2_700_000_000),    # Cellular Band 41
    ]

    def __init__(self, node, redis_client, logger) -> None:
        """Initialise TXController.

        Args:
            node: HackRFNode reference (provides _hackrf, _device_lock,
                  _stop_rx_if_running, _start_rx_if_stopped, get_parameter).
            redis_client: redis.Redis instance (decode_responses=False).
            logger: Object with .info(), .warning(), .error() methods.
        """
        self._node = node
        self._redis = redis_client
        self._logger = logger

        self._antenna_confirmed: bool = False
        self._is_transmitting: bool = False
        # TX cannot re-enter itself — use Lock not RLock
        self._tx_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Read antenna confirmation state from Redis or ROS2 parameter.

        Decision D-07/D-08:
          - If tx_skip_antenna_check=True: confirm immediately, log WARNING.
          - Else: check Redis ANTENNA_KEY (b'1' = confirmed).
        """
        if self._node.get_parameter('tx_skip_antenna_check').bool_value:
            self._antenna_confirmed = True
            self._logger.warning(
                'TXController: tx_skip_antenna_check=True — antenna confirmation bypassed. '
                'Only use this in automated test environments.'
            )
            return

        val = self._redis.get(self.ANTENNA_KEY)
        self._antenna_confirmed = (val == b'1')
        if self._antenna_confirmed:
            self._logger.info('TXController: antenna confirmation received from Redis.')
        else:
            self._logger.warning(
                'TXController: antenna NOT confirmed (Redis key absent or not b"1"). '
                'TX will be blocked until antenna is confirmed. '
                f'Set Redis key {self.ANTENNA_KEY!r} to "1" to confirm.'
            )

    def stop(self) -> None:
        """Alias for stop_tx(). Called from destroy_node — safe to call any time."""
        self.stop_tx()

    # ------------------------------------------------------------------
    # TX dispatch
    # ------------------------------------------------------------------

    def start_tx(self, freq_hz: int, auth_token: str, iq_bytes: bytes,
                 txvga_gain: int = 0) -> None:
        """Attempt to start TX after all guard checks pass.

        Guards are checked in this strict order:
          1. Antenna confirmed  → TXBlockedError
          2. Hard-blocked band  → TXHardBlockedError  (NO bypass, token NOT consumed)
          3. Filter-gated check → TXFreqBlockedError  (token NOT consumed)
          4. Auth token valid   → TXNotAuthorizedError

        Args:
            freq_hz: Center frequency in Hz.
            auth_token: One-time authorization token (must match Redis AUTH_KEY).
            iq_bytes: Raw IQ bytes to transmit (int8 interleaved I/Q).
            txvga_gain: TX VGA gain 0-47 dB (default 0 — safest default).

        Raises:
            TXBlockedError: Antenna not confirmed.
            TXHardBlockedError: Frequency is always blocked (EPIRB/ADS-B).
            TXFreqBlockedError: Frequency restricted and filter is active.
            TXNotAuthorizedError: Auth token absent, expired, or wrong.
        """
        # Guard 1: antenna confirmed
        if not self._antenna_confirmed:
            raise TXBlockedError(
                f'TX blocked: antenna not confirmed. '
                f'Set Redis key {self.ANTENNA_KEY!r} to "1" or use tx_skip_antenna_check.'
            )

        # Guard 2: hard-blocked bands — UNCONDITIONAL, no bypass
        if self._is_hard_blocked(freq_hz):
            raise TXHardBlockedError(
                f'TX blocked: {freq_hz} Hz is on an always-blocked band '
                f'(EPIRB/ADS-B). No bypass path exists. Token NOT consumed.'
            )

        # Guard 3: filter-gated frequency check — token NOT consumed on reject
        if self._freq_filter_active() and self._is_freq_restricted(freq_hz):
            raise TXFreqBlockedError(
                f'TX blocked: {freq_hz} Hz is on a restricted band and '
                f'the frequency filter is active. Token NOT consumed.'
            )

        # Guard 4: consume one-time auth token atomically
        if not self._consume_auth_token(auth_token):
            raise TXNotAuthorizedError(
                f'TX blocked: auth token {auth_token[:8]!r}... not found, '
                f'expired, or already consumed.'
            )

        # All guards passed — execute TX
        with self._tx_lock:
            self._node._stop_rx_if_running()
            time.sleep(0.1)  # firmware settle per libhackrf #916

            with self._node._device_lock:
                hackrf = self._node._hackrf
                hackrf.center_freq = freq_hz
                hackrf.txvga_gain = txvga_gain
                hackrf.buffer = bytearray(iq_bytes)
                hackrf.start_tx()

            self._is_transmitting = True
            self._logger.info(
                f'AUDIT: TX started freq={freq_hz} '
                f'token_hint={auth_token[:8]}... '
                f'iq_len={len(iq_bytes)}'
            )

    def stop_tx(self) -> None:
        """Stop active transmission and resume RX.

        Safe to call at any time — no-op when not transmitting.
        Called from destroy_node; must not raise.
        """
        with self._tx_lock:
            if not self._is_transmitting:
                return
            try:
                self._node._hackrf.stop_tx()
            except RuntimeError:
                pass
            self._is_transmitting = False
            time.sleep(0.1)  # settle before RX resume
            self._node._start_rx_if_stopped()
            self._logger.info('TXController: TX stopped; RX resumed.')

    # ------------------------------------------------------------------
    # Internal guard helpers
    # ------------------------------------------------------------------

    def _is_hard_blocked(self, freq_hz: int) -> bool:
        """Return True if freq_hz is in ALWAYS_BLOCKED_BANDS (unconditional check).

        Args:
            freq_hz: Frequency to check in Hz.

        Returns:
            True if frequency falls within any always-blocked band.
        """
        for min_hz, max_hz in self.ALWAYS_BLOCKED_BANDS:
            if min_hz <= freq_hz < max_hz:
                return True
        return False

    def _is_freq_restricted(self, freq_hz: int) -> bool:
        """Return True if freq_hz is in RESTRICTED_BANDS.

        This check is filter-gated — only enforced when _freq_filter_active() is True.

        Args:
            freq_hz: Frequency to check in Hz.

        Returns:
            True if frequency falls within any restricted band.
        """
        for min_hz, max_hz in self.RESTRICTED_BANDS:
            if min_hz <= freq_hz < max_hz:
                return True
        return False

    def _freq_filter_active(self) -> bool:
        """Return True if the frequency filter is currently active.

        Dual-disable model (D-05): filter is ONLY inactive when BOTH:
          - ROS2 parameter tx_freq_filter_enabled == False, AND
          - Redis key FREQ_OVERRIDE_KEY == b'disabled'

        Returns:
            True if filter is active (default), False only when both disable.
        """
        ros_param_enabled = self._node.get_parameter('tx_freq_filter_enabled').bool_value
        if ros_param_enabled:
            return True
        # ROS param says disabled — check Redis override
        redis_val = self._redis.get(self.FREQ_OVERRIDE_KEY)
        if redis_val == b'disabled':
            return False
        # Redis key absent or not b'disabled' — filter remains active
        return True

    def _consume_auth_token(self, expected_token: str) -> bool:
        """Atomically consume the auth token from Redis and verify it matches.

        Tries native GETDEL first (Redis 6.2+). Falls back to Lua eval on
        ResponseError (Redis < 6.2, e.g. 6.0.16).

        Args:
            expected_token: The token string to compare against the stored value.

        Returns:
            True if token was present and matched; False otherwise.
        """
        try:
            stored = self._redis.execute_command('GETDEL', self.AUTH_KEY)
        except redis.exceptions.ResponseError:
            # Fallback: Lua GETDEL for Redis < 6.2
            stored = self._redis.eval(_LUA_GETDEL, 1, self.AUTH_KEY)

        if stored is None:
            return False

        # Decode bytes if needed for comparison
        if isinstance(stored, bytes):
            stored_str = stored.decode('utf-8')
        else:
            stored_str = str(stored)

        return stored_str == expected_token
