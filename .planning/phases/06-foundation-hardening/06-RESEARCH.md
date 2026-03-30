# Phase 06: Foundation Hardening - Research

**Researched:** 2026-03-30
**Domain:** Python exception hierarchies, Redis reconnection, IQ sequence numbers, TX dry-run, ROS2 services
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** pymayhem exception approach: raise on all command failures with MayhemError hierarchy, plus a catch-at-boundary pattern for existing callers. The dispatch boundary in redis_bridge.py (ERR-05) must catch pymayhem exceptions and map to structured Redis error state — never propagate raw exceptions through the dispatch table.
- **D-02:** hackrf_driver uses a unified HackRFError tree. HackRFError is the base; HackRFConfigError (validation), HackRFDeviceError (USB/connection), and existing TX exceptions (TXBlockedError, TXFreqBlockedError, TXHardBlockedError, TXNotAuthorizedError) all reparented under HackRFError. One `except HackRFError` catches everything.
- **D-03:** pymayhem gets its own MayhemError base with subtypes: MayhemCommandError (firmware returned error), MayhemParseError (unparseable response), MayhemTimeoutError (wraps existing TimeoutError). Domain methods raise these instead of returning bool.
- **D-04:** Single source of truth for hardware validation in hackrf_driver. PARAM_RANGES in config.py is the canonical validator. hackrf_driver raises HackRFConfigError on out-of-range values. BridgeNode and Redis dispatch trust hackrf_driver — they do NOT duplicate validation. pymayhem validates its own domain-specific inputs independently (e.g., freq_hz is int, app name is non-empty string).
- **D-05:** validate_tx() checks guards only — antenna confirmed, freq not hard-blocked, freq filter check, auth token EXISTS (via GET, not GETDEL). Pure logic, no hardware state dependency. Returns pass/fail with which guard would block. Works even when HackRF hardware is offline.
- **D-06:** IQ sequence numbers use epoch + counter format: "{driver_start_epoch}:{monotonic_counter}" (e.g., "1711814400:42"). Added as `seq` field on every XADD entry. Counter starts at 0 each driver run. Consumers detect gaps (missing counter values) and restarts (epoch changes) from one field. No persistence needed — epoch is captured once at HackRFDriver.__init__.
- **D-07:** Replace the fatal `except Exception: break` in BridgeNode._bridge_loop() with an exponential backoff retry loop. On Redis error: log warning, sleep with backoff (1s-30s), attempt reconnect + resubscribe to hackrf:iq:notify Pub/Sub. Flush state immediately after successful reconnect. Pattern mirrors existing driver reconnection logic.
- **D-08:** BridgeNode exposes `/hackrf/confirm_antenna` ROS2 service (std_srvs/Trigger). Handler sets Redis key `hackrf:tx:antenna_confirmed` to b'1'. TXController periodically re-reads this key (not just at init) — re-read interval is Claude's discretion.
- **D-09:** hackrf_node.py gets a deprecation docstring and a log.warning at import time pointing to hackrf_driver + BridgeNode. File stays in place for colcon build compatibility.

### Claude's Discretion

- pymayhem exception backward compatibility strategy (D-01): whether to use safe_* wrappers, catch-at-boundary, or direct raise-everywhere
- TXController antenna re-read interval (D-08): 30s, 60s, or configurable
- Exception naming refinements within the defined hierarchies

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.

</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ERR-01 | pymayhem raises typed exceptions (MayhemError hierarchy) instead of returning bool on command failures | D-03: MayhemError base + MayhemCommandError, MayhemParseError, MayhemTimeoutError. Domain methods in radio.py, system.py raise instead of `return False`. |
| ERR-02 | hackrf_driver raises typed exceptions (HackRFError hierarchy) for config, device, and TX errors | D-02: HackRFError base + HackRFConfigError, HackRFDeviceError. Existing TX exceptions reparented. exceptions.py (new file) in hackrf_driver package. |
| ERR-03 | All public pymayhem methods validate input parameters and raise ValueError on out-of-range values | D-04: pymayhem validates domain inputs independently — freq_hz type/range, non-empty app name etc. Standard Python ValueError for bad args. |
| ERR-04 | All hackrf_driver config changes validate against PARAM_RANGES before touching hardware | D-04: PARAM_RANGES in config.py is canonical; _update_param currently warns and returns — change to raise HackRFConfigError. |
| ERR-05 | Exception dispatch boundary in redis_bridge catches pymayhem/hackrf exceptions and maps to structured Redis error state | D-01: _dispatch_command wraps handler call; catches MayhemError/HackRFError; writes structured error to hackrf:cmd:last_error HSET. |
| REL-02 | BridgeNode survives Redis restart — exponential backoff retry loop with automatic resubscribe to Pub/Sub channels | D-07: Replace `break` on line 130 of bridge_node.py with backoff retry (1s-30s); resubscribe after reconnect; flush state. |
| REL-03 | Every IQ XADD entry includes a monotonic sequence number; consumers can detect dropped buffers | D-06: epoch+counter format in `seq` field. Counter in HackRFDriver.__init__; incremented in _xadd_iq(). |
| TXS-01 | validate_tx() checks all four TX guards without consuming the auth token | D-05: New method on TXController. Uses GET (not GETDEL) for auth check. Returns dict with pass/fail and blocking guard name. |
| TXS-02 | BridgeNode exposes /hackrf/confirm_antenna ROS2 service that sets the Redis antenna confirmation key | D-08: New closure factory in bridge_services.py; std_srvs/Trigger; sets hackrf:tx:antenna_confirmed to b'1'. |
| TXS-03 | TXController periodically re-reads antenna confirmation key (not just at init) | D-08: timer-based re-read; interval is Claude's discretion (recommended: 30s). |
| LEG-01 | HackRFNode marked deprecated with docstring and log warning pointing users to hackrf_driver + BridgeNode | D-09: Module-level docstring + warnings.warn at import. File stays; no code deletion. |

</phase_requirements>

---

## Summary

Phase 6 is a hardening sprint across three packages (pymayhem, hackrf_driver, hackrf_ros). There are no new external dependencies — all work is additive within the existing codebase. The phase breaks into six independent work streams that share no runtime coupling: (1) pymayhem exception hierarchy, (2) hackrf_driver exception hierarchy, (3) Redis dispatch error boundary, (4) BridgeNode reconnect loop, (5) IQ sequence numbers, and (6) TX dry-run + antenna service. Each stream can be planned, implemented, and tested in isolation.

The critical architectural constraint from CONTEXT.md is **catch-at-boundary**: pymayhem domain methods raise; the `_dispatch_command` boundary in `redis_bridge.py` catches and translates to Redis state. This means no changes to the `_COMMAND_HANDLERS` dispatch table lambdas themselves — only `_dispatch_command` gains the exception catch-and-map logic. The existing 68 tests must continue to pass because the domain method interfaces otherwise remain unchanged (domain objects still receive `_send_command` callable at construction via callable injection).

The recommended implementation order: exceptions.py files first (foundation for all other streams), then _update_param raise conversion, then _dispatch_command boundary, then sequence numbers (isolated _xadd_iq change), then BridgeNode reconnect (most complex), then validate_tx + antenna service, finally LEG-01 deprecation notice. Each deliverable is independently testable.

**Primary recommendation:** Create `pymayhem/pymayhem/exceptions.py` and `hackrf_driver/hackrf_driver/exceptions.py` as the first task. All other streams import from these modules, so creating them first eliminates circular dependency risk and lets all other tasks proceed in parallel.

---

## Standard Stack

### Core (no new packages required)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| redis-py | 7.4+ (existing) | Redis reconnect + pubsub resubscribe | Already present; `redis.exceptions.RedisError` is the catch target |
| std_srvs | ROS2 Humble (existing) | `/hackrf/confirm_antenna` Trigger service | Already used in bridge_services.py for other Trigger services |
| threading | stdlib | Antenna re-read timer in TXController | Already used throughout codebase |
| time | stdlib | Exponential backoff sleep in BridgeNode | Already used in driver.py reconnect |

**No new PyPI packages required for Phase 6.**

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| warnings | stdlib | `warnings.warn` for LEG-01 deprecation | Module-level import of hackrf_node.py |
| typing | stdlib | Type annotations on exception classes, validate_tx return | All new code follows existing typing patterns |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| epoch+counter seq format | Redis INCR atomic counter | INCR requires an extra Redis call per XADD; epoch+counter is purely in-process, survives Redis restarts without a Redis call |
| threading.Timer for antenna re-read | Polling inside bridge_loop | Timer is cleaner (periodic, decoupled from bridge loop tick); polling adds latency coupling |
| catch-at-boundary pattern (D-01) | Raise-everywhere + caller adapts | Catch-at-boundary preserves existing caller contracts; raise-everywhere would break _COMMAND_HANDLERS lambdas that do not catch |

---

## Architecture Patterns

### Recommended Project Structure (new files only)

```
pymayhem/pymayhem/
├── exceptions.py         # NEW: MayhemError, MayhemCommandError, MayhemParseError, MayhemTimeoutError

hackrf_driver/hackrf_driver/
├── exceptions.py         # NEW: HackRFError, HackRFConfigError, HackRFDeviceError

hackrf_ros/
└── bridge_node.py        # MODIFY: reconnect loop replaces break on line 130
    bridge_services.py    # MODIFY: add _make_antenna_confirm_handler factory

hackrf_ros/hackrf_ros/
└── hackrf_node.py        # MODIFY: add deprecation docstring + warnings.warn
```

### Pattern 1: Exception Hierarchy (Python stdlib pattern)

**What:** Single base exception per package; typed subtypes for each failure mode.

**When to use:** Any `raise` in pymayhem domain methods or hackrf_driver.

```python
# pymayhem/pymayhem/exceptions.py
class MayhemError(Exception):
    """Base for all pymayhem errors."""

class MayhemCommandError(MayhemError):
    """Firmware returned an error response."""

class MayhemParseError(MayhemError):
    """Unparseable firmware response."""

class MayhemTimeoutError(MayhemError):
    """Serial command timed out (wraps stdlib TimeoutError)."""
```

```python
# hackrf_driver/hackrf_driver/exceptions.py
class HackRFError(Exception):
    """Base for all hackrf_driver errors."""

class HackRFConfigError(HackRFError):
    """Parameter out of hardware range."""

class HackRFDeviceError(HackRFError):
    """USB/connection-level device failure."""

# tx_controller.py — reparent existing classes:
class TXBlockedError(HackRFError): ...
class TXFreqBlockedError(HackRFError): ...
class TXHardBlockedError(HackRFError): ...
class TXNotAuthorizedError(HackRFError): ...
```

**Backward compatibility:** Existing callers catching `TXBlockedError` directly still work — reparenting under `HackRFError` is fully compatible. Callers catching `Exception` also still work. No existing callers catch `TXBlockedError as HackRFError`, so the reparent is safe.

### Pattern 2: Domain Method Exception Conversion

**What:** Replace `return not any('error' ...)` with explicit raise.

**Current (radio.py setfreq):**
```python
def setfreq(self, freq_hz: int) -> bool:
    lines = self._send(f'setfreq {freq_hz}')
    return not any('error' in line.lower() for line in lines)
```

**New pattern:**
```python
def setfreq(self, freq_hz: int) -> None:
    if not isinstance(freq_hz, int) or not (1_000_000 <= freq_hz <= 6_000_000_000):
        raise ValueError(f'freq_hz must be int in [1e6, 6e9], got {freq_hz!r}')
    lines = self._send(f'setfreq {freq_hz}')
    if any('error' in line.lower() for line in lines):
        raise MayhemCommandError(f'setfreq {freq_hz}: firmware error: {lines}')
```

**Return type changes:** `bool` → `None` (void) for all domain methods that currently return bool. Methods that return structured data (radioinfo → dict, applist → list) keep their return types. This is the API break — handled by catch-at-boundary in _dispatch_command.

### Pattern 3: Catch-at-Boundary in _dispatch_command

**What:** All pymayhem/hackrf exceptions caught at the dispatch boundary; translated to Redis error state.

**Current _dispatch_command (redis_bridge.py lines 275-296):**
```python
def _dispatch_command(self, cmd: dict) -> None:
    action = cmd.get('action', '')
    handler = _COMMAND_HANDLERS.get(action)
    if handler is None:
        self._logger.warning(f'RedisBridge: unknown command action: {action!r}')
        return
    try:
        handler(self._node, cmd)
    except Exception as e:
        self._logger.error(f'RedisBridge: command {action!r} failed: {e}')
```

**New pattern (add structured error publishing before generic catch):**
```python
    try:
        handler(self._node, cmd)
    except (MayhemError, HackRFError) as e:
        self._logger.warning(f'RedisBridge: command {action!r} typed error: {e}')
        self._publish_command_error(action, type(e).__name__, str(e))
    except Exception as e:
        self._logger.error(f'RedisBridge: command {action!r} unexpected error: {e}')
        self._publish_command_error(action, 'UnexpectedError', str(e))

def _publish_command_error(self, action: str, error_type: str, message: str) -> None:
    """Write structured error to hackrf:cmd:last_error hash."""
    try:
        self._redis.hset('hackrf:cmd:last_error', mapping={
            'action': action,
            'error_type': error_type,
            'message': message,
            'timestamp': str(time.time()),
        })
    except redis.exceptions.RedisError:
        pass  # error publishing must never raise
```

**Note:** The `_COMMAND_HANDLERS` lambdas are NOT modified. Exceptions propagate from domain methods through the lambda, through `handler(self._node, cmd)`, to `_dispatch_command`'s try/except. This is the correct and minimal change.

### Pattern 4: _update_param Raises HackRFConfigError

**Current (driver.py lines 351-358):**
```python
if name in PARAM_RANGES:
    lo, hi = PARAM_RANGES[name]
    if not (lo <= value <= hi):
        self._logger.warning(f"Parameter '{name}' value {value} rejected: ...")
        return  # SILENT: caller never knows validation failed
```

**New pattern:**
```python
if name in PARAM_RANGES:
    lo, hi = PARAM_RANGES[name]
    if not (lo <= value <= hi):
        raise HackRFConfigError(
            f"Parameter '{name}' value {value} out of range [{lo}, {hi}]"
        )
```

**Impact:** All callers of `_update_param` must be audited. In `redis_bridge._dispatch_command`, the `except (MayhemError, HackRFError)` boundary already catches `HackRFConfigError`. In direct tests, tests that pass invalid values will now need `assertRaises` rather than asserting return value.

### Pattern 5: IQ Sequence Number in _xadd_iq

**What:** Add `seq` field to every XADD entry. Counter lives in `RedisBridge`; epoch in `HackRFDriver`.

**Implementation — HackRFDriver.__init__ addition:**
```python
import time as _time
self._driver_epoch: int = int(_time.time())  # captured once at init
```

**Implementation — pass epoch to RedisBridge constructor:**
```python
# RedisBridge.__init__ signature gains driver_epoch: int
self._driver_epoch = driver_epoch
self._seq_counter = 0
```

**Implementation — _xadd_iq:**
```python
seq = f'{self._driver_epoch}:{self._seq_counter}'
self._seq_counter += 1
entry_id = self._redis.xadd(
    self.STREAM_KEY,
    {b'data': float32_arr.tobytes(), b'seq': seq.encode()},
    maxlen=self._maxlen,
    approximate=True,
)
```

**Consumer gap detection:** Compare counter from `seq.split(':')[1]` between successive entries. A jump > 1 means dropped buffers. An epoch change means driver restarted.

### Pattern 6: BridgeNode Reconnect Loop

**What:** Replace the fatal `break` on line 130 with exponential backoff + resubscribe.

**Current (bridge_node.py lines 125-130):**
```python
while not self._stop_event.is_set():
    try:
        msg = pubsub.get_message(timeout=0.1)
    except Exception as e:
        self.get_logger().warning(f'BridgeNode._bridge_loop: get_message error: {e}')
        break  # BUG: permanent death
```

**New pattern (mirrors driver.py lines 240-276):**
```python
_MIN_BACKOFF = 1.0
_MAX_BACKOFF = 30.0

def _bridge_loop(self):
    if self._redis is None:
        return
    backoff = _MIN_BACKOFF
    while not self._stop_event.is_set():
        try:
            pubsub = self._redis.pubsub()
            pubsub.subscribe('hackrf:iq:notify')
            self.get_logger().info('BridgeNode: subscribed to hackrf:iq:notify')
            backoff = _MIN_BACKOFF  # reset on successful connect
            self._run_pubsub_loop(pubsub)
        except Exception as e:
            self.get_logger().warning(
                f'BridgeNode: Redis error: {e}. Retrying in {backoff:.0f}s.'
            )
            try:
                pubsub.close()
            except Exception:
                pass
            if self._stop_event.wait(backoff):
                break  # stop requested during backoff
            backoff = min(backoff * 2, _MAX_BACKOFF)
    self.get_logger().info('BridgeNode: bridge loop exited.')

def _run_pubsub_loop(self, pubsub):
    """Inner loop — raises on Redis error to trigger outer reconnect."""
    while not self._stop_event.is_set():
        msg = pubsub.get_message(timeout=0.1)  # raises on Redis error
        if msg is None:
            continue
        if msg['type'] != 'message':
            continue
        # ... existing IQ + state publish logic ...
```

**Key: outer reconnect loop wraps inner pubsub loop.** `pubsub.get_message()` raises `redis.exceptions.ConnectionError` on disconnect, which propagates to the outer `except Exception` block, triggering backoff + resubscribe.

**Flush state after reconnect:** Call `self._publish_state()` immediately after successful `pubsub.subscribe()` to push current hackrf:state to ROS2 topic before the next IQ notification arrives.

### Pattern 7: validate_tx() Method

**What:** Non-consuming preflight check of all four TX guards.

```python
def validate_tx(self, freq_hz: int) -> dict:
    """Check all TX guards without consuming auth token or touching hardware.

    Returns:
        dict with keys:
            'valid': bool — True if all guards would pass
            'blocking_guard': str | None — name of first failing guard, or None
            'detail': str — human-readable explanation
    """
    # Guard 1: antenna
    if not self._antenna_confirmed:
        return {'valid': False, 'blocking_guard': 'antenna', 'detail': 'Antenna not confirmed'}
    # Guard 2: hard-blocked
    if self._is_hard_blocked(freq_hz):
        return {'valid': False, 'blocking_guard': 'hard_block',
                'detail': f'{freq_hz} Hz is on an always-blocked band'}
    # Guard 3: freq filter
    if self._freq_filter_active() and self._is_freq_restricted(freq_hz):
        return {'valid': False, 'blocking_guard': 'freq_filter',
                'detail': f'{freq_hz} Hz is restricted and filter is active'}
    # Guard 4: auth token EXISTS (GET, not GETDEL)
    val = self._redis.get(self.AUTH_KEY)
    if val is None:
        return {'valid': False, 'blocking_guard': 'auth_token',
                'detail': 'No auth token present in Redis'}
    return {'valid': True, 'blocking_guard': None, 'detail': 'All guards pass'}
```

**Critical difference from start_tx:** Guard 4 uses `GET` not `GETDEL` — token is NOT consumed. This is the core dry-run property (D-05).

### Pattern 8: Antenna Confirmation Service

**What:** Add `_make_antenna_confirm_handler` factory to bridge_services.py — identical pattern to existing `_make_mayhem_handler`.

```python
def _make_antenna_confirm_handler(redis_client):
    """Return a Trigger service callback that sets the antenna confirmation key."""
    def _handler(request, response):
        try:
            redis_client.set('hackrf:tx:antenna_confirmed', b'1')
            response.success = True
            response.message = 'Antenna confirmed — TX guard cleared'
        except Exception as e:
            response.success = False
            response.message = f'Redis error: {e}'
        return response
    return _handler
```

**Register in register_bridge_services:**
```python
node.create_service(
    Trigger,
    '/hackrf/confirm_antenna',
    _make_antenna_confirm_handler(redis_client),
)
```

### Pattern 9: TXController Periodic Antenna Re-read

**What:** `threading.Timer` that periodically re-reads `ANTENNA_KEY` from Redis.

**Recommended interval:** 30 seconds. Rationale: operator workflows (connecting antenna, launching ROS2 service) typically take minutes; 30s is responsive without excessive Redis polling.

```python
# In TXController.__init__:
self._antenna_reread_interval = 30.0  # seconds; Claude's discretion
self._antenna_reread_timer: threading.Timer | None = None

# Start after open():
def open(self) -> None:
    ...  # existing logic
    self._schedule_antenna_reread()

def _schedule_antenna_reread(self) -> None:
    self._antenna_reread_timer = threading.Timer(
        self._antenna_reread_interval, self._reread_antenna
    )
    self._antenna_reread_timer.daemon = True
    self._antenna_reread_timer.start()

def _reread_antenna(self) -> None:
    val = self._redis.get(self.ANTENNA_KEY)
    confirmed = (val == b'1')
    if confirmed and not self._antenna_confirmed:
        self._logger.info('TXController: antenna confirmation received (periodic re-read).')
    self._antenna_confirmed = confirmed
    self._schedule_antenna_reread()  # reschedule
```

**Cancel on shutdown:** Add `if self._antenna_reread_timer: self._antenna_reread_timer.cancel()` to `stop_tx()` or a new `close()` method.

### Pattern 10: LEG-01 Deprecation

**What:** Module-level deprecation in hackrf_node.py.

```python
"""HackRFNode — DEPRECATED.

This module is retained for colcon build compatibility only.
Use hackrf_driver.HackRFDriver + hackrf_ros.BridgeNode instead.

See: hackrf_driver/hackrf_driver/driver.py
     hackrf_ros/hackrf_ros/bridge_node.py
"""
import warnings
warnings.warn(
    'hackrf_ros.hackrf_node is deprecated. '
    'Use hackrf_driver.HackRFDriver + hackrf_ros.BridgeNode. '
    'This module will be removed in v3.0.',
    DeprecationWarning,
    stacklevel=2,
)
```

### Anti-Patterns to Avoid

- **Duplicating validation in BridgeNode:** PARAM_RANGES lives in hackrf_driver. BridgeNode must not re-validate — it must trust hackrf_driver to raise HackRFConfigError (D-04).
- **Consuming auth token in validate_tx:** Must use `GET`, never `GETDEL` (D-05 — Pitfall 10 from PITFALLS.md).
- **Modifying _COMMAND_HANDLERS lambdas:** The catch-at-boundary lives in `_dispatch_command`, not in each lambda. Adding try/except to every lambda is duplication and defeats the purpose of the boundary pattern.
- **Catching bare Exception in domain methods:** Domain methods should raise typed exceptions and let the boundary catch. Using `except Exception: return False` in domain methods defeats the hierarchy.
- **Blocking BridgeNode reconnect on `_stop_event.wait()` with no timeout check:** Must use `self._stop_event.wait(backoff)` and check return value to exit cleanly on shutdown.
- **Not cancelling antenna re-read timer on shutdown:** Timer is a daemon thread but still needs explicit cancel to avoid spurious Redis calls after Redis is closed.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Exception base classes | Custom metaclass or exception registry | Plain `class MayhemError(Exception)` | Python built-in exception system is sufficient; no registry needed |
| Exponential backoff | Custom sleep with manual doubling | Mirror existing `driver.py` lines 259-264 pattern | Identical pattern already proven in codebase; copy it verbatim |
| Redis pubsub resubscribe | Custom resubscribe protocol | `redis.pubsub()` + `subscribe()` on new object each reconnect | Creating a new pubsub object on each reconnect is simpler and avoids stale state from the old object |
| Auth token existence check | Custom token store | `self._redis.get(self.AUTH_KEY)` | Already the pattern — just swap GETDEL for GET in validate_tx |

**Key insight:** Every pattern needed in Phase 6 already exists in this codebase. Research confirms this is a "extend and harden existing patterns" phase, not a "design new patterns" phase.

---

## Common Pitfalls

### Pitfall 1: Domain Method API Break Breaks Existing Callers (PITFALLS.md Pitfall 1)

**What goes wrong:** pymayhem domain methods currently return `bool`. Changing to `raise MayhemCommandError` means callers that do `if client.radio.setfreq(...)` silently pass on the raised exception (if it's caught by a broader `except`), or fail with an unhandled exception (if nothing catches it).

**Why it happens:** The `_COMMAND_HANDLERS` lambdas call domain methods directly. When `setfreq` raises instead of returning False, the lambda propagates the exception to `_dispatch_command`'s generic `except Exception` — currently only logs, never publishes Redis error state. The Redis consumer sees no state update and no error.

**How to avoid:** (1) The exception boundary in `_dispatch_command` catches `MayhemError` BEFORE the generic `except Exception` and publishes `hackrf:cmd:last_error`. (2) Do not change lambda call signatures. (3) Add a regression test that calls `serial_setfreq` with a mock `_send_command` that returns `['error: bad freq']` and verifies that `hackrf:cmd:last_error` is set.

**Warning signs:** Redis consumers see commands with no state update, no error log. `hackrf:state.active_app` shows stale value after failed `appstart`.

### Pitfall 2: validate_tx Consuming Auth Token (PITFALLS.md Pitfall 10)

**What goes wrong:** If `validate_tx` uses GETDEL instead of GET for Guard 4, calling it consumes the one-time token. The subsequent real `start_tx` call fails with `TXNotAuthorizedError` even though validation passed.

**Why it happens:** Reflex to reuse `_consume_auth_token()` helper in validate_tx rather than writing a separate GET call.

**How to avoid:** `validate_tx` must use `self._redis.get(self.AUTH_KEY)` directly — never call `_consume_auth_token()`. Document the distinction explicitly in the docstring.

### Pitfall 3: BridgeNode Break Creates Silent Topic Death (PITFALLS.md Pitfall 7)

**What goes wrong:** Line 130 of bridge_node.py has `break` on `get_message` error. This exits the bridge loop permanently. ROS2 shows node alive but `/hackrf/iq` topic goes silent with no diagnostics.

**Why it happens:** The `break` was a correct "safe graceful startup failure" that was never updated for mid-session disconnects.

**How to avoid:** Outer reconnect loop with `_stop_event.wait(backoff)` instead of `break`. Inner loop raises on Redis error to trigger outer reconnect. Backoff uses `_stop_event.wait()` so shutdown is still responsive.

### Pitfall 4: Antenna Re-read Timer Not Cancelled on Shutdown

**What goes wrong:** `threading.Timer` fires after `BridgeNode.destroy_node()` closes Redis connection, causing a Redis call that raises on a closed connection.

**How to avoid:** In `TXController.stop()` (already called from shutdown), cancel the antenna re-read timer before Redis connection closes. Use `timer.cancel(); timer = None` pattern.

### Pitfall 5: Dual Validation Breaking Single Source of Truth (PITFALLS.md Pitfall 11)

**What goes wrong:** BridgeNode or `_dispatch_command` adds its own PARAM_RANGES check before forwarding to driver, creating two validation points that can drift out of sync.

**How to avoid:** D-04 is locked — BridgeNode and Redis dispatch do NOT validate. Only hackrf_driver raises HackRFConfigError. All validation done by `_update_param` in driver.py using PARAM_RANGES from config.py. The dispatch boundary catches the resulting exception and maps it to Redis error state.

### Pitfall 6: seq Counter Thread Safety

**What goes wrong:** `_seq_counter` in RedisBridge incremented from the bridge daemon thread. If `publish_state()` is also called from other threads, there's a potential race on `_seq_counter`. However, `_seq_counter` is ONLY incremented in `_xadd_iq()`, which runs exclusively in the bridge daemon thread. No race exists.

**How to avoid:** Keep `_seq_counter` increment exclusively in `_xadd_iq()`. Do not expose `_seq_counter` to external threads. This is already guaranteed by the existing single-thread-owns-the-bridge design.

---

## Code Examples

Verified patterns from direct codebase inspection:

### Existing Exception Pattern to Extend (tx_controller.py lines 29-41)
```python
# Current — these 4 classes currently inherit from Exception
class TXBlockedError(Exception): ...
class TXFreqBlockedError(Exception): ...
class TXHardBlockedError(Exception): ...
class TXNotAuthorizedError(Exception): ...

# Phase 6 change — reparent all under HackRFError (from new exceptions.py):
from hackrf_driver.exceptions import HackRFError
class TXBlockedError(HackRFError): ...
# (tx_controller.py keeps exception definitions for import compatibility;
#  OR they move to exceptions.py and tx_controller.py imports from there)
```

### Existing Reconnect Pattern to Mirror (driver.py lines 259-264)
```python
self._reconnect_timer = threading.Timer(
    self._reconnect_delay, self._reconnect_callback
)
self._reconnect_timer.daemon = True
self._reconnect_timer.start()
self._reconnect_delay = min(self._reconnect_delay * 2, _MAX_RECONNECT_DELAY)
```

BridgeNode reconnect uses `_stop_event.wait(backoff)` instead of threading.Timer (simpler because we want blocking wait in a loop, not a fire-and-forget timer).

### Existing Service Factory Pattern to Extend (bridge_services.py lines 40-51)
```python
def _make_mayhem_handler(redis_client, cmd_name):
    def _handler(request, response):
        try:
            redis_client.rpush('hackrf:cmd', json.dumps({'cmd': cmd_name, 'args': {}}))
            response.success = True
            response.message = f'{cmd_name} queued'
        except Exception as e:
            response.success = False
            response.message = f'{cmd_name} failed: {e}'
        return response
    return _handler
```

`_make_antenna_confirm_handler` follows identical structure — closure over `redis_client`, returns Trigger-compatible handler.

### Existing XADD Pattern to Extend (redis_bridge.py lines 235-242)
```python
entry_id = self._redis.xadd(
    self.STREAM_KEY,
    {b'data': float32_arr.tobytes()},
    maxlen=self._maxlen,
    approximate=True,
)
self._redis.publish(self.NOTIFY_KEY, entry_id)
```

Phase 6 adds `b'seq': seq.encode()` to the fields dict. No other change to this path.

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `return bool` from pymayhem domain methods | `raise MayhemCommandError` | Phase 6 | Callers must handle exception at boundary; structured error in Redis |
| `_update_param` silently returns on invalid value | `raise HackRFConfigError` | Phase 6 | Redis bridge surfaces rejection as structured error state |
| BridgeNode dies on Redis disconnect | Exponential backoff reconnect loop | Phase 6 | Bridge auto-recovers; `/hackrf/iq` topic resumes after Redis restart |
| IQ XADD entries have no sequence info | `seq: {epoch}:{counter}` field | Phase 6 | Consumer gap detection enabled for Phase 8 SigMF recording |
| TX antenna check only at TXController.open() | Periodic re-read every 30s | Phase 6 | ROS2 service confirmation reflected in TXController without restart |

---

## Open Questions

1. **TX exception placement in exceptions.py vs tx_controller.py**
   - What we know: TXBlockedError etc. currently defined in tx_controller.py and imported from there in redis_bridge.py and tests.
   - What's unclear: Should they move to exceptions.py (centralizing all HackRFError subtypes) or stay in tx_controller.py with reparenting?
   - Recommendation: Move them to exceptions.py for the canonical hierarchy. Update tx_controller.py and redis_bridge.py imports. Update tests to import from new location. This is a single mechanical change; grep the codebase for all import sites first.

2. **RedisBridge constructor signature change for driver_epoch**
   - What we know: RedisBridge.__init__ currently takes `(iq_queue, node, logger, maxlen)`. Adding `driver_epoch` requires a parameter addition.
   - What's unclear: Whether to pass it as a required positional arg or optional kwarg with default `int(time.time())`.
   - Recommendation: Optional kwarg with default `int(time.time())`. Keeps existing test setUp code unmodified. Tests that want to verify seq format can pass a fixed epoch.

3. **BridgeNode Redis connection object reuse on reconnect**
   - What we know: BridgeNode creates one `redis.Redis` instance in `__init__`. On disconnect, redis-py connection pool auto-reconnects on the next command — no explicit new Redis object is needed.
   - What's unclear: Whether `pubsub` object from the old disconnected session needs explicit close before creating a new `pubsub()`.
   - Recommendation: Always call `pubsub.close()` in the except handler before creating a new `pubsub = self._redis.pubsub()`. This ensures stale subscriptions are cleaned up. The `self._redis` connection pool auto-reconnects transparently.

---

## Environment Availability

Step 2.6: SKIPPED — Phase 6 is purely code/config changes within existing packages. No new external tools, services, runtimes, or CLI utilities required. All dependencies (redis-py, rclpy, std_srvs, threading) are already present in the deployment environment.

---

## Sources

### Primary (HIGH confidence)
- Direct codebase inspection — hackrf_ros/bridge_node.py lines 125-130 (the break bug, D-07 target)
- Direct codebase inspection — hackrf_driver/hackrf_driver/tx_controller.py lines 29-41 (exception classes to reparent)
- Direct codebase inspection — hackrf_driver/hackrf_driver/config.py (PARAM_RANGES canonical source)
- Direct codebase inspection — hackrf_driver/hackrf_driver/redis_bridge.py lines 218-296 (_xadd_iq and _dispatch_command)
- Direct codebase inspection — hackrf_driver/hackrf_driver/driver.py lines 240-276 (reconnect pattern to mirror)
- Direct codebase inspection — hackrf_ros/bridge_services.py (closure factory pattern for antenna service)
- Direct codebase inspection — pymayhem/pymayhem/domains/radio.py and system.py (bool return methods to convert)
- .planning/research/PITFALLS.md — Pitfalls 1, 7, 10, 11 (all verified against actual code)
- .planning/research/SUMMARY.md — Phase 6 section, architecture approach, critical pitfalls list
- .planning/phases/06-foundation-hardening/06-CONTEXT.md — All 9 locked decisions

### Secondary (MEDIUM confidence)
- Python stdlib documentation — `warnings.warn` with DeprecationWarning (standard deprecation pattern, stable since Python 2.6)
- redis-py documentation pattern — pubsub object creation per session is the documented reconnect pattern

---

## Metadata

**Confidence breakdown:**
- Exception hierarchies: HIGH — direct codebase inspection of all files to be modified; Python exception inheritance is stable stdlib
- BridgeNode reconnect: HIGH — break bug confirmed on line 130; reconnect pattern confirmed in driver.py lines 240-276; identical pattern already proven
- IQ sequence numbers: HIGH — _xadd_iq confirmed in redis_bridge.py; epoch+counter format is pure Python arithmetic
- validate_tx: HIGH — all four guards confirmed in TXController.start_tx(); GET vs GETDEL distinction confirmed from Redis docs
- Antenna service: HIGH — identical to existing _make_mayhem_handler pattern; std_srvs/Trigger already used
- Deprecation: HIGH — standard Python warnings.warn pattern; hackrf_node.py confirmed in-place

**Research date:** 2026-03-30
**Valid until:** 2026-04-30 (stable internal codebase — no external dependencies changing)
