# Phase 7: Observability & Reliability - Research

**Researched:** 2026-03-30
**Domain:** Python threading watchdog, Redis metrics publishing, dead-letter queue, daemon thread orchestration
**Confidence:** HIGH

## Summary

Phase 7 adds three cohesive features to an already-stable three-package driver: a device health watchdog (REL-01), a Redis metrics hash at 1 Hz (OBS-01 + OBS-03), and a dead-letter queue for failed commands (OBS-02). All three are purely additive — no architectural changes, no new PyPI dependencies, no changes to existing API contracts.

The central design risk is threading. The watchdog introduces a third daemon thread to a driver whose existing lock protocol was designed for two (the pyhackrf2 RX callback thread and the `redis_bridge` daemon thread). The decisions in CONTEXT.md already resolve this correctly: lock-free detection only, corrective action via `stdlib.queue.Queue`. The remaining open question — which existing thread drains the correction queue — is answered by this research: the `_iq_publish_loop` daemon thread is the correct drain site. It already runs on a 5 ms sleep cycle, has no lock obligations, and does not touch `_device_lock`. The watchdog posts to `_watchdog_queue`; `_iq_publish_loop` drains it and calls `_try_connect()` under `_device_lock` (which `_iq_publish_loop` already does not hold).

Metrics publishing mirrors the existing `_publish_state()` HSET pattern exactly. The BridgeNode metrics topic mirrors the existing `/hackrf/state` polling pattern. DLQ is `XADD MAXLEN ~ 500` on the exception boundary already present in `_dispatch_command()`. All three features follow established codebase patterns — no new patterns need to be invented.

**Primary recommendation:** Implement in three sub-tasks per plan: (1) watchdog thread + correction queue in `driver.py`; (2) metrics accumulator + `_publish_metrics()` in `redis_bridge.py`; (3) DLQ hook in `_dispatch_command()` + metrics topic in `bridge_node.py`.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**D-01:** Lock-free detection only. Watchdog thread checks `is_hackrf_streaming` flag and `_redis_queue.qsize()` — never acquires `_device_lock`. If stall detected (no IQ data for 10s), posts a reconnect request to a correction queue (stdlib `queue.Queue`). The existing main loop or a dedicated drain thread processes the correction queue and calls `_try_connect()`. This avoids ABBA deadlock with `_device_lock` (Pitfall 2 from research).

**D-02:** Watchdog runs as a daemon thread with a 10s check interval. Stall detection: compare `_last_rx_time` (updated in `_rx_callback`) against `time.monotonic()`. If delta > 10s and driver is supposed to be streaming, flag stall.

**D-03:** `hackrf:metrics` Redis hash published at 1 Hz via Redis pipeline (not per-XADD). Fields: `iq_chunks_sec` (throughput), `iq_drops` (drop-oldest count), `rx_errors` (device errors), `cmd_errors` (dispatch errors), `queue_depth_iq` (current IQ queue size), `queue_depth_redis` (Redis queue size), `uptime_s`, `last_rx_time` (epoch), `watchdog_reconnects` (count). All values are strings (Redis hash convention).

**D-04:** BridgeNode publishes `/hackrf/metrics` ROS2 topic (String/JSON) at 5s interval by reading `hackrf:metrics` hash — mirrors existing `/hackrf/state` pattern.

**D-05:** Failed commands archived to `hackrf:cmd:dlq` Redis stream via XADD MAXLEN ~ 500. Entry fields: `action` (command name), `error_type` (exception class name), `error_msg` (str), `timestamp` (ISO), `args` (JSON of original command args). Auth tokens MUST be stripped before archiving — replace token value with `"[REDACTED]"`.

**D-06:** DLQ uses MAXLEN trimming only — no per-entry EXPIRE TTL. 500 entries is sufficient forensic history; Redis stream trimming handles cleanup automatically.

**D-07:** HackRFNode deprecation notice (from Phase 6 LEG-01) is already in place. Phase 7 adds no additional legacy cleanup — this phase is purely additive.

### Claude's Discretion

- Correction queue drain mechanism (D-01): whether the main loop checks it or a dedicated thread
- Metrics accumulator implementation detail (counters as atomics vs simple ints with lock)
- DLQ Redis key name confirmation (`hackrf:cmd:dlq`)
- Watchdog thread naming convention

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| REL-01 | Device health watchdog detects USB stall (no RX data for 10s) and triggers automatic reconnect without deadlocking `_device_lock` | Lock-free detection via `is_hackrf_streaming` + `time.monotonic()`. Correction queue drain on `_iq_publish_loop`. `_try_connect()` already handles reconnect with backoff. |
| OBS-01 | `hackrf:metrics` Redis hash publishes IQ throughput (chunks/sec), error counts, queue depths, and uptime at 1 Hz | Mirrors existing `_publish_state()` HSET pattern. Pipeline batches the hash write. Accumulator counters maintained in `RedisBridge`. |
| OBS-02 | Failed Redis commands archived to `hackrf:cmd:dlq` stream (MAXLEN=500) with error context and timestamp | Hook into existing `_dispatch_command()` exception boundary. `_publish_command_error()` already exists — DLQ is a second write in the same except block. |
| OBS-03 | BridgeNode publishes `/hackrf/metrics` ROS2 topic with same data as `hackrf:metrics` hash | Timer callback on 5s interval reading `hackrf:metrics` hgetall. Mirrors `_publish_state()` + `create_timer()` already present in BridgeNode. |
</phase_requirements>

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `stdlib threading` | 3.10 (bundled) | Watchdog daemon thread, stop gate | Already used throughout driver; no new dependency |
| `stdlib queue.Queue` | 3.10 (bundled) | Correction queue (watchdog → drain thread) | Used for all existing IQ queues; lock-free producer/consumer |
| `stdlib time` | 3.10 (bundled) | `time.monotonic()` for stall detection, `time.time()` for ISO timestamps | Already used in driver and redis_bridge |
| `redis-py` | 7.4+ (already installed) | Pipeline HSET for metrics, XADD for DLQ | Already the project's Redis client |
| `rclpy` | Humble (already installed) | `create_timer()` for metrics topic in BridgeNode | Already the project's ROS2 client |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `datetime.datetime` | stdlib | ISO timestamp for DLQ entries | DLQ `timestamp` field per D-05 |
| `json` | stdlib | Serialize command `args` for DLQ, decode metrics hash in BridgeNode | Already used in redis_bridge for command dispatch |

**No new PyPI packages required.** All features implemented with stdlib + already-present dependencies.

## Architecture Patterns

### Watchdog Thread: Lock-Free Detection + Correction Queue

**What:** Daemon thread wakes every 10s, checks `is_hackrf_streaming` and `time.monotonic() - _last_rx_time`. If stall detected, puts a sentinel object into `_watchdog_queue`. Never acquires `_device_lock`.

**Correction queue drain site:** `_iq_publish_loop` (already runs on a 5 ms sleep cycle, holds no locks). After its `_publish_iq()` call, drain `_watchdog_queue` non-blocking and call `_try_connect()` if a reconnect token is present.

**Why `_iq_publish_loop` and not a new thread:** Adding a fourth daemon thread increases complexity without benefit. `_iq_publish_loop` already wakes every 5 ms, has no lock obligations, and already calls driver-level operations. The correction queue check is 3 lines of non-blocking code.

**Why not `run()`:** `run()` is `self._stop_event.wait()` — it is a blocking sleep, not a poll loop. Modifying it to poll would change the shutdown contract.

**Why not `redis_bridge` thread:** The redis_bridge thread holds `_state_lock` during `publish_state()` calls and may have `_device_lock` contention via `_update_param` → `_configure_device` chains. Keeping watchdog drain on a thread with no lock obligations is safer.

**Re-entry prevention:** `_try_connect()` already checks `self.is_hackrf_streaming` and returns early if a reconnect timer is already scheduled. No additional guard needed — the existing `_reconnect_timer is not None` check in `_try_connect()` prevents cascading reconnect attempts.

```python
# Source: driver.py pattern (adapted)

# In HackRFDriver.__init__:
self._watchdog_queue: queue.Queue = queue.Queue(maxsize=1)
self._watchdog_reconnects: int = 0
self._last_rx_time: float = time.monotonic()  # updated in _rx_callback

self._watchdog_thread = threading.Thread(
    target=self._watchdog_loop,
    daemon=True,
    name='hackrf_watchdog',
)
self._watchdog_thread.start()

# In _rx_callback (added to existing bare-enqueue method):
self._last_rx_time = time.monotonic()

# Watchdog loop (new method):
def _watchdog_loop(self) -> None:
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

# Correction queue drain in _iq_publish_loop:
def _iq_publish_loop(self) -> None:
    while not self._stop_event.is_set():
        self._publish_iq()
        self._drain_watchdog_queue()  # NEW: drain correction queue
        time.sleep(0.005)

def _drain_watchdog_queue(self) -> None:
    try:
        self._watchdog_queue.get_nowait()
    except queue.Empty:
        return
    self._logger.info('Watchdog: triggering reconnect via _try_connect().')
    self._watchdog_reconnects += 1
    self._try_connect()
```

### Metrics Accumulator + `_publish_metrics()`

**What:** Simple int counters on `RedisBridge` instance, incremented at the right call sites. A 1 Hz publish timer (or loop-based 1 s gate) calls `_publish_metrics()` which flushes all counters to `hackrf:metrics` via a single pipeline batch.

**Accumulator implementation:** Plain `int` attributes protected by the existing `_state_lock` (already used by `publish_state()`). No need for `threading.atomic` — the lock is cheap and already present. Alternatively, the counters can be thread-local to the bridge thread (incremented only from `_bridge_loop`) with no locking needed at all — see below.

**Counter ownership:**
- `_iq_chunks_sec_count`, `_iq_drops_count`, `_rx_errors_count`, `_cmd_errors_count` — incremented in `_bridge_loop` thread only. No lock needed.
- `queue_depth_iq`, `queue_depth_redis` — read from `_iq_queue.qsize()` at publish time (queue.Queue.qsize() is thread-safe enough for metrics).
- `uptime_s` — `time.monotonic() - self._start_time` at publish time.
- `last_rx_time` — read from `driver._last_rx_time` passed as a callable or attribute reference.
- `watchdog_reconnects` — read from `driver._watchdog_reconnects`.

**1 Hz publish gate:** The `_bridge_loop` already runs a tight loop (drain IQ + poll commands with 200 ms XREAD block). A simple `time.monotonic()` gate publishes every ≥1 s without adding a new timer thread:

```python
# Source: redis_bridge.py _bridge_loop pattern (adapted)
def _bridge_loop(self) -> None:
    last_cmd_id = b'$'
    _last_metrics_t = time.monotonic()
    while not self._stop_event.is_set():
        self._drain_iq_queue()
        last_cmd_id = self._poll_commands(last_cmd_id)
        now = time.monotonic()
        if now - _last_metrics_t >= 1.0:
            self._publish_metrics()
            _last_metrics_t = now
```

**`_publish_metrics()` using Redis pipeline:**

```python
# Source: redis-py pipeline pattern + existing _publish_state() HSET pattern
def _publish_metrics(self) -> None:
    if self._redis is None:
        return
    node = self._node  # driver reference
    try:
        pipe = self._redis.pipeline(transaction=False)
        pipe.hset('hackrf:metrics', mapping={
            'iq_chunks_sec':     str(self._iq_chunks_this_sec),
            'iq_drops':          str(self._iq_drops_count),
            'rx_errors':         str(self._rx_errors_count),
            'cmd_errors':        str(self._cmd_errors_count),
            'queue_depth_iq':    str(node._redis_queue.qsize()),
            'queue_depth_redis': str(self._iq_queue.qsize()),
            'uptime_s':          str(int(time.monotonic() - self._start_time)),
            'last_rx_time':      str(getattr(node, '_last_rx_time', 0.0)),
            'watchdog_reconnects': str(getattr(node, '_watchdog_reconnects', 0)),
        })
        pipe.execute()
        self._iq_chunks_this_sec = 0  # reset per-second counter
    except redis.exceptions.RedisError as e:
        self._logger.warning(f'RedisBridge: _publish_metrics failed: {e}')
```

**`pipeline(transaction=False)`:** The Redis pipeline with `transaction=False` batches commands without a MULTI/EXEC block. For a single HSET this is equivalent to a direct HSET but avoids per-command round-trip overhead on each field. Confidence: HIGH (redis-py docs confirm `pipeline(transaction=False)` is non-transactional batching).

### Dead-Letter Queue (DLQ)

**What:** In `_dispatch_command()`, the existing `except (MayhemError, HackRFError)` and `except Exception` blocks already call `_publish_command_error()`. Add a `_archive_to_dlq()` call in the same blocks.

**Auth token stripping (D-05):** The `cmd` dict arrives as the raw JSON from the stream. Before archiving `args`, create a sanitized copy: replace the value of any key named `auth_token` with `"[REDACTED]"`. Keys are checked by exact name only.

```python
# Source: redis_bridge.py _dispatch_command pattern (adapted)
def _archive_to_dlq(self, action: str, error_type: str, error_msg: str,
                    cmd: dict) -> None:
    """XADD failed command to hackrf:cmd:dlq stream (OBS-02 / D-05)."""
    if self._redis is None:
        return
    # Strip auth tokens before archiving (D-05 security requirement)
    safe_args = {
        k: '[REDACTED]' if k == 'auth_token' else v
        for k, v in cmd.items()
        if k != 'action'  # action is stored separately
    }
    try:
        self._redis.xadd(
            'hackrf:cmd:dlq',
            {
                'action':     action,
                'error_type': error_type,
                'error_msg':  error_msg,
                'timestamp':  datetime.datetime.utcnow().isoformat(),
                'args':       json.dumps(safe_args),
            },
            maxlen=500,
            approximate=True,
        )
    except redis.exceptions.RedisError as e:
        self._logger.warning(f'RedisBridge: DLQ archive failed: {e}')
        # Never raise — DLQ is best-effort forensic
```

**XADD with `maxlen=500, approximate=True`:** Redis streams MAXLEN with `~` (approximate) trimming is the canonical pattern for bounded streams. It trims when the stream grows well beyond 500 entries rather than on every XADD, reducing per-write overhead. Confidence: HIGH (redis.io documentation).

### BridgeNode Metrics Topic (OBS-03)

**What:** `create_timer()` on a 5 s interval reads `hackrf:metrics` with `hgetall`, decodes, publishes as JSON String to `/hackrf/metrics`. Mirrors the existing `_publish_state()` + `/hackrf/state` pattern exactly.

```python
# Source: bridge_node.py _publish_state() pattern (adapted)
# In BridgeNode.__init__:
self._metrics_publisher = self.create_publisher(String, '/hackrf/metrics', 10)
self._metrics_timer = self.create_timer(5.0, self._publish_metrics_topic)

def _publish_metrics_topic(self):
    """Read hackrf:metrics hash and publish JSON to /hackrf/metrics (OBS-03 / D-04)."""
    if self._redis is None:
        return
    try:
        metrics = self._redis.hgetall('hackrf:metrics')
    except Exception as e:
        self.get_logger().warning(f'BridgeNode._publish_metrics_topic: hgetall failed: {e}')
        return
    if not metrics:
        return
    decoded = {k.decode(): v.decode() for k, v in metrics.items()}
    msg = String()
    msg.data = json.dumps(decoded)
    self._metrics_publisher.publish(msg)
```

### Anti-Patterns to Avoid

- **Watchdog acquires `_device_lock`:** Never acquire `_device_lock` from the watchdog thread. Even `_device_lock.acquire(timeout=N)` is risky because the watchdog runs on a separate thread, and RLock's reentrant property only protects the same thread. Any cross-thread acquisition competes with `_configure_device()`'s 100-300 ms hold.
- **Metrics per-XADD (Pitfall 8 from SUMMARY.md):** Do not call `_publish_metrics()` on every IQ chunk. At 8 MSPS the drain loop fires ~1000 times/second; per-XADD metrics writes would issue 1000 HSET commands/second to Redis. The 1 Hz gate in `_bridge_loop` is mandatory.
- **DLQ in `_publish_command_error()`:** Do not replace `_publish_command_error()` with DLQ. They serve different purposes: `hackrf:cmd:last_error` is the current-state "what just failed" hash (overwritten on each error); `hackrf:cmd:dlq` is the forensic history stream (appended). Both should coexist.
- **`run()` as the correction queue drain site:** `run()` is `self._stop_event.wait()` — it does not iterate. Converting it to a poll loop would break the clean shutdown signal contract and add 5-10 ms latency to every shutdown.
- **`datetime.utcnow()` deprecation:** Python 3.12 deprecated `datetime.datetime.utcnow()`. Use `datetime.datetime.now(datetime.timezone.utc).isoformat()` for forward compatibility. The ROS2 Humble container uses Python 3.10 so `utcnow()` still works, but the forward-compatible form is preferable.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Redis pipeline batching | Manual loop of `hset()` calls | `redis.pipeline(transaction=False)` | Batches all fields in one round-trip; already used in redis-py projects |
| Thread-safe correction queue | Custom Event + flag pairs | `queue.Queue(maxsize=1)` | `put_nowait` / `get_nowait` are already thread-safe; drop-new semantics with maxsize=1 |
| MAXLEN stream trimming | Manual XLEN + XTRIM | `XADD ... MAXLEN ~ N` | Approximate trimming is native to Redis Streams; cheaper than explicit XTRIM |
| Monotonic stall detection | Wall-clock comparison | `time.monotonic()` | Immune to system clock adjustments; already used by `_start_time` pattern |

## Common Pitfalls

### Pitfall 1: `_watchdog_queue` maxsize=1 vs maxsize=0 (unbounded)

**What goes wrong:** If the correction queue has no maxsize, multiple 10s watchdog firings before the drain thread wakes up can queue N reconnect requests. `_try_connect()` is called N times in rapid succession, triggering N exponential-backoff timers that compete with each other.

**Why it happens:** The watchdog fires every 10s. If `_iq_publish_loop` is blocked (e.g., during a device stress moment), the queue grows unboundedly.

**How to avoid:** `queue.Queue(maxsize=1)`. On `put_nowait()` when full, catch `queue.Full` and silently discard — the existing request is still pending. One pending reconnect is sufficient; duplicates add no value.

**Warning signs:** Log shows multiple "triggering reconnect" entries within a few seconds of each other.

### Pitfall 2: Watchdog fires during `shutdown()` — reconnect race

**What goes wrong:** `shutdown()` calls `self._stop_event.set()` and then closes the device. The watchdog thread's `self._stop_event.wait(10.0)` returns immediately when the event is set. But the watchdog still calls the stall check code. If `is_hackrf_streaming` happens to be False at that moment (already stopped by shutdown), no reconnect is triggered — this is correct. However, if the timing is tight, the watchdog may post to `_watchdog_queue` after `shutdown()` begins, causing `_drain_watchdog_queue` to call `_try_connect()` during teardown.

**How to avoid:** Check `self._stop_event.is_set()` as the first line of `_drain_watchdog_queue()`. Return immediately if stop is requested. Also check in the watchdog loop body after the `wait()` returns.

**Warning signs:** Log shows "Watchdog: triggering reconnect" during the shutdown sequence.

### Pitfall 3: `_last_rx_time` not initialized before first RX data

**What goes wrong:** `_last_rx_time` is set in `__init__` to `time.monotonic()`. If the device never connects (no hardware), `is_hackrf_streaming` is False — watchdog skips the stall check. This is correct. But if the device connects and then immediately stalls before any RX callback fires, the first watchdog check (10s after init) correctly detects the stall. No issue — this is the desired behavior.

**How to avoid:** Initialize `_last_rx_time = time.monotonic()` in `__init__`. Reset it to `time.monotonic()` in `_try_connect()` after a successful `start_rx()` call so the 10s stall timer resets on each reconnect.

**Warning signs:** Spurious watchdog reconnects immediately after startup.

### Pitfall 4: DLQ archives `auth_token` in plaintext

**What goes wrong:** The `start_tx` command includes `auth_token` in the `cmd` dict. If `_archive_to_dlq()` serializes `cmd` verbatim, the one-time auth token is preserved in the forensic log. While the token has already been consumed by Redis GETDEL, archiving it in a log contradicts the single-use intent and leaks the token value.

**How to avoid:** D-05 is explicit — replace `auth_token` value with `"[REDACTED]"` before archiving. The sanitize step must check for the key by exact name. The existing `_handle_start_tx()` function processes `auth_token` directly, so the DLQ must sanitize at the archiving step, not at the dispatch step.

**Warning signs:** `hackrf:cmd:dlq` entries for `start_tx` failures contain non-redacted `auth_token` values.

### Pitfall 5: Metrics counter `_iq_chunks_this_sec` not reset after each publish

**What goes wrong:** If `_iq_chunks_this_sec` is not reset to 0 after `_publish_metrics()`, the published `iq_chunks_sec` value grows monotonically (total chunks since start, not per-second rate). Operators reading the hash will see an ever-increasing number instead of a rate.

**How to avoid:** Reset `_iq_chunks_this_sec = 0` at the end of `_publish_metrics()` (after the pipeline execute). Increment it in `_xadd_iq()` for each successful XADD.

**Warning signs:** `hackrf:metrics` shows `iq_chunks_sec` in the millions after running for hours.

### Pitfall 6: `_dispatch_command` DLQ call uses `cmd` after mutation

**What goes wrong:** Some `_COMMAND_HANDLERS` lambdas extract values from `cmd` by key. If the cmd dict is modified before `_archive_to_dlq()` is called (e.g., by a handler that pops keys), the archived `args` may be incomplete.

**How to avoid:** Call `_archive_to_dlq()` from the `except` block where `cmd` is still the original parsed dict. The handler has already raised by the time the `except` block runs, so no mutation has occurred (the exception interrupted execution before any mutation).

## Code Examples

### Existing `_publish_state()` pattern to mirror for `_publish_metrics()`

```python
# Source: hackrf_driver/redis_bridge.py lines 178-196
def publish_state(self, state: dict) -> None:
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
```

### Existing `_dispatch_command()` exception boundary to hook DLQ

```python
# Source: hackrf_driver/redis_bridge.py lines 284-334
def _dispatch_command(self, cmd: dict) -> None:
    action = cmd.get('action', '')
    handler = _COMMAND_HANDLERS.get(action)
    if handler is None:
        self._logger.warning(f'RedisBridge: unknown command action: {action!r}')
        return
    try:
        handler(self._node, cmd)
    except (MayhemError, HackRFError) as e:
        self._logger.warning(f'RedisBridge: command {action!r} typed error: {e}')
        self._publish_command_error(action, type(e).__name__, str(e))
        # ADD: self._archive_to_dlq(action, type(e).__name__, str(e), cmd)
    except Exception as e:
        self._logger.error(f'RedisBridge: command {action!r} unexpected error: {e}')
        self._publish_command_error(action, 'UnexpectedError', str(e))
        # ADD: self._archive_to_dlq(action, 'UnexpectedError', str(e), cmd)
```

### Existing `_try_connect()` pattern — correction queue calls into this

```python
# Source: hackrf_driver/driver.py lines 245-281
# _try_connect() already handles is_hackrf_streaming check, exponential backoff,
# and threading.Timer chain. The correction queue drain simply calls self._try_connect()
# directly — no additional guard is needed because _try_connect() checks
# self._reconnect_timer is not None before scheduling another timer.
```

### Existing BridgeNode `_publish_state()` pattern to mirror for metrics topic

```python
# Source: hackrf_ros/bridge_node.py lines 175-191
def _publish_state(self):
    if self._redis is None:
        return
    try:
        state = self._redis.hgetall('hackrf:state')
    except Exception as e:
        self.get_logger().warning(f'BridgeNode._publish_state: hgetall failed: {e}')
        return
    if not state:
        return
    decoded = {k.decode(): v.decode() for k, v in state.items()}
    msg = String()
    msg.data = json.dumps(decoded)
    self._state_publisher.publish(msg)
```

## Lock Ordering Analysis

This is the key design question flagged in STATE.md and SUMMARY.md. Below is the complete thread × lock matrix after Phase 7:

| Thread | `_device_lock` | `_tx_lock` | `_state_lock` | `_watchdog_queue` |
|--------|---------------|-----------|---------------|-------------------|
| pyhackrf2 RX callback | Never | Never | Never | put_nowait (write only) |
| `redis_bridge` daemon | Never directly | Never | Held during `publish_state()` | Never |
| `_iq_publish_loop` | Held via `_try_connect()` | Never | Never | get_nowait (drain) |
| `hackrf_watchdog` | **Never** (D-01) | Never | Never | put_nowait (write only) |
| Command dispatch (bridge thread) | Held via `_configure_device()` | Held via `start_tx()` | Held via `publish_state()` | Never |

**ABBA deadlock analysis:**
- `_device_lock` → `_tx_lock`: TXController `stop_tx()` acquires `_device_lock` then `_tx_lock` (always in this order). This is the only ordering — no reverse exists.
- `_watchdog_queue` is a `queue.Queue` with no internal locks that interact with `_device_lock` or `_tx_lock`. Producer-consumer semantics only.
- Conclusion: No circular lock dependency introduced by Phase 7. The lock ordering is safe.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Per-XADD metrics write | 1 Hz gate in `_bridge_loop` | Phase 7 (this phase) | ~1000x reduction in Redis write rate for metrics |
| Failed commands silently discarded | DLQ via XADD MAXLEN=500 | Phase 7 (this phase) | Forensic history preserved for 500 failures |
| No health visibility | `hackrf:metrics` hash + `/hackrf/metrics` topic | Phase 7 (this phase) | Operators can monitor live driver health |
| No USB stall recovery | Watchdog with correction queue | Phase 7 (this phase) | Self-healing driver for USB stalls |

## Open Questions

1. **`_last_rx_time` thread safety**
   - What we know: `_last_rx_time` is written in `_rx_callback` (pyhackrf2 thread) and read in `_watchdog_loop` (watchdog thread). Python `float` assignment is atomic under CPython's GIL for simple scalar types.
   - What's unclear: Whether this is a documented guarantee or an implementation detail.
   - Recommendation: Use a `float` attribute without a lock — the GIL makes scalar float writes effectively atomic in CPython 3.10. Add a brief comment acknowledging this.

2. **Metrics `start_time` reference**
   - What we know: `HackRFDriver.__init__` already sets `self._start_time = time.monotonic()`. `RedisBridge` needs access to compute `uptime_s`.
   - What's unclear: Whether to pass `start_time` to `RedisBridge.__init__` or read it from `self._node._start_time`.
   - Recommendation: Read from `self._node._start_time` (driver reference already passed as `node`). No new constructor parameter needed.

3. **`queue_depth_redis` vs `queue_depth_iq` naming**
   - What we know: D-03 specifies both `queue_depth_iq` (IQ queue to ROS) and `queue_depth_redis` (IQ queue to Redis bridge).
   - What's unclear: `RedisBridge` owns the `_iq_queue` reference (the redis queue). The driver also has `_ros_queue`. Both are accessible.
   - Recommendation: `queue_depth_iq = _ros_queue.qsize()`, `queue_depth_redis = _iq_queue.qsize()` (the bridge's own queue). The bridge reads both from `self._node._ros_queue` and `self._iq_queue`.

## Environment Availability

Step 2.6: SKIPPED — Phase 7 is purely additive code changes. No new external tools, services, or CLIs are introduced. All dependencies (redis-py, stdlib, rclpy) are already present and confirmed by prior phases.

## Sources

### Primary (HIGH confidence)

- `hackrf_driver/hackrf_driver/driver.py` — Direct inspection of `_try_connect()`, `_configure_device()`, `_iq_publish_loop()`, `_rx_callback()`, `_device_lock` usage
- `hackrf_driver/hackrf_driver/redis_bridge.py` — Direct inspection of `_dispatch_command()`, `publish_state()`, `_bridge_loop()`, `_xadd_iq()`, `_publish_command_error()`
- `hackrf_ros/bridge_node.py` — Direct inspection of `_publish_state()`, `_bridge_loop()`, `create_timer()` patterns
- `.planning/research/SUMMARY.md` — Phase 2 (Observability) rationale, lock-free watchdog recommendation, correction queue design flag
- `.planning/research/PITFALLS.md` — Pitfall 2 (watchdog deadlock), Pitfall 8 (metrics overhead), full deadlock mechanics
- `.planning/phases/07-observability-reliability/07-CONTEXT.md` — D-01 through D-07 locked decisions

### Secondary (MEDIUM confidence)

- redis.io Redis Streams documentation — XADD MAXLEN approximate trimming semantics, pipeline batching
- redis-py documentation — `pipeline(transaction=False)` behavior

### Tertiary (LOW confidence)

- None — all findings backed by direct codebase inspection or official documentation

## Metadata

**Confidence breakdown:**
- Watchdog design: HIGH — directly derived from existing `_iq_publish_loop`, `_try_connect()`, and `_device_lock` patterns; no speculative elements
- Metrics pattern: HIGH — mirrors `publish_state()` / `_publish_state()` exactly; Redis pipeline is stdlib redis-py
- DLQ hook: HIGH — exception boundary already present in `_dispatch_command()`; XADD MAXLEN is the canonical Redis Streams pattern
- Lock ordering: HIGH — full matrix constructed from direct codebase inspection; no circular dependencies found

**Research date:** 2026-03-30
**Valid until:** 2026-04-30 (stable stdlib + redis-py patterns; no fast-moving dependencies)
