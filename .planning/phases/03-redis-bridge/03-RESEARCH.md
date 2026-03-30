# Phase 3: Redis Bridge - Research

**Researched:** 2026-03-29
**Domain:** redis-py 7.4.0, Redis Streams, daemon thread pattern (MayhemSerial analog)
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Connect to localhost:6379, no auth. Standard Docker host networking setup.
- **D-02:** Graceful degradation: if Redis is unreachable at startup or disconnects, IQ still flows on ROS2 topics. Redis publishing disabled until reconnect. Log warning, retry with backoff.
- **D-03:** IQ samples encoded as interleaved float32 array [I,Q,I,Q,...] in Redis Streams — same format as ROS2 topic, ready to use without conversion.
- **D-04:** MAXLEN is configurable via ROS2 parameter `redis_stream_maxlen`, default 10000. Use approximate trimming (`~` modifier) for performance.
- **D-05:** Commands are JSON messages published to `hackrf:cmd` Redis Stream. Format: `{"action": "<name>", ...params}`.
- **D-06:** Accepted actions: `setfreq`, `set_sample_rate`, `set_lna_gain`, `set_vga_gain`, `set_amp_enabled` (HackRF config), `appstart`, `serial_setfreq` (Mayhem control), `start_rx`, `stop_rx` (stream control).
- **D-07:** Command responses are not published back — callers check `hackrf:state` for confirmation.
- **D-08:** `hackrf:state` Redis Hash contains: HackRF config (`center_frequency`, `sample_rate`, `lna_gain`, `vga_gain`, `amp_enabled`), streaming status (`is_streaming`, `connected`, `uptime_s`), Mayhem state (`active_app`, `discovered_apps` JSON array, `serial_connected`).
- **D-09:** State updated on change only — no periodic heartbeat.
- **D-10:** RedisBridge as a separate helper class owned by HackRFNode — same pattern as MayhemSerial.
- **D-11:** All Redis I/O runs in a dedicated daemon thread. Never block ROS2 executor callbacks.
- **D-12:** Use `hackrf:` namespace prefix: `hackrf:iq:stream`, `hackrf:state`, `hackrf:cmd`.

### Claude's Discretion

- redis-py connection pooling configuration
- Exact thread synchronization between `_redis_queue` consumer and Redis XADD
- How command subscriber thread interacts with ROS2 parameter callbacks
- Whether to use a single daemon thread or split IQ publishing and command subscription

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.

</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| RED-01 | IQ samples published to Redis Stream via XADD with configurable MAXLEN trimming | Verified: `r.xadd(name, fields, maxlen=N, approximate=True)` — confirmed working on Redis 6.0.16 with redis-py 7.4.0 |
| RED-02 | Device state published to Redis Hash (hackrf:state) with current frequency, gain, sample rate, streaming status | Verified: `r.hset(name, mapping={...})` with all D-08 fields confirmed working |
| RED-03 | Command interface via Redis subscriber (hackrf:cmd) accepts frequency, gain, sample rate, and bandwidth changes | Verified: `r.xread({'hackrf:cmd': last_id}, count=N, block=timeout_ms)` returns empty list on timeout, parses JSON payload |
| RED-04 | Redis I/O runs in dedicated daemon thread, never blocking ROS2 executor callbacks | Architecture: single daemon thread owns all Redis operations; queue.Queue handoff from RX callback; confirmed pattern from MayhemSerial |
| RED-05 | Redis key schema uses hackrf: namespace prefix consistently | Locked by D-12: hackrf:iq:stream, hackrf:state, hackrf:cmd |

</phase_requirements>

---

## Summary

Phase 3 adds `RedisBridge` — a helper class that mirrors the `MayhemSerial` pattern exactly: instantiated by `HackRFNode`, owns a single daemon thread, communicates via the existing `_redis_queue`, and shuts down cleanly via `_stop_event`. The class is independently testable without ROS2.

The daemon thread performs three functions in one loop: (1) drain `_redis_queue` and XADD IQ chunks to `hackrf:iq:stream`, (2) XREAD with a short block timeout from `hackrf:cmd` and dispatch commands, and (3) HSET `hackrf:state` when state-changing operations are called by the node. All three fit in a single thread because XREAD blocking timeout bounds the loop iteration (100–500 ms), and IQ queue draining is non-blocking (`get_nowait`).

The environment has redis-py 7.4.0 and hiredis 3.3.1 already installed. Redis 6.0.16 is live on localhost:6379. All three Redis API patterns (XADD approximate, HSET mapping, XREAD block) were live-tested and confirmed working. One constraint: the running Redis 6.0.16 does not support `GETDEL` (requires 6.2+), which affects Phase 4's TX auth token — irrelevant to Phase 3 but worth noting in the plan.

**Primary recommendation:** Implement `RedisBridge` in `hackrf_ros/redis_bridge.py` following the `MayhemSerial` class structure exactly. Use a single daemon thread with a loop that alternates non-blocking IQ drain and short-block XREAD for commands.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| redis-py | 7.4.0 (installed) | Redis client — XADD, XREAD, HSET | Active v7 series; auto-uses hiredis if present |
| hiredis | 3.3.1 (installed) | C parser for redis-py response parsing | Zero code changes; installed and detected automatically |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| queue.Queue | stdlib | Thread-safe IQ handoff from `_rx_callback` to bridge thread | Already in hackrf_node.py as `_redis_queue`; just consume it |
| threading.Thread | stdlib | Daemon bridge thread | Same pattern as MayhemSerial `_reader_thread` |
| threading.Event | stdlib | Shutdown signaling | Reuse `_stop_event` passed from HackRFNode |
| json | stdlib | Serialize/deserialize Redis commands | Commands are JSON dicts per D-05 |
| numpy | stdlib (project dep) | tobytes() for IQ binary serialization | Zero-copy: raw float32 bytes go directly to Redis |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Single loop with XREAD block timeout | Two threads (one IQ, one cmd) | Split thread adds complexity, Lock needed for shared Redis client; single thread simpler given XREAD block=200ms bounded |
| `decode_responses=False` (binary) | `decode_responses=True` (strings) | IQ data is binary bytes — must keep `decode_responses=False`; decode command JSON manually |

**Installation:** Already installed in this environment. For deployment:

```bash
pip install "redis>=7.4.0" "hiredis>=3.3.1"
```

`setup.py` `install_requires` must add `redis>=7.4.0` and `hiredis>=3.3.1`.
`package.xml` must add `<exec_depend>python3-redis</exec_depend>`.

---

## Architecture Patterns

### Recommended Project Structure

```
hackrf_ros/
├── hackrf_node.py       # HackRFNode: instantiates RedisBridge, passes _redis_queue
├── redis_bridge.py      # RedisBridge helper class (NEW — this phase)
├── mayhem_serial.py     # MayhemSerial (existing, no changes)
└── tx_controller.py     # TXController (Phase 4)
```

### Pattern 1: RedisBridge Class (MayhemSerial Analog)

**What:** Standalone helper class with `open()` / `close()` lifecycle, a daemon thread, and a `needs_reconnect` property. HackRFNode instantiates it, passes `_redis_queue` and a logger reference.

**When to use:** Always — matches project's established helper class pattern (CONTEXT.md D-10).

```python
# hackrf_ros/redis_bridge.py
import queue
import threading
import time
import json
import numpy as np
import redis

class RedisBridge:
    """Redis I/O bridge for HackRF ROS2 driver.

    Owns a daemon thread that:
      - Drains _iq_queue and XADDs IQ chunks to hackrf:iq:stream
      - XREADs hackrf:cmd stream and dispatches commands to HackRFNode
      - Exposes publish_state() for on-change HSET to hackrf:state

    Thread-safe: publish_state() can be called from any thread.
    Never blocks the ROS2 executor.

    Usage::
        bridge = RedisBridge(iq_queue, node_ref, logger, maxlen=10000)
        if bridge.open():
            bridge.publish_state({...})
        # on shutdown:
        bridge.close()
    """

    STREAM_KEY  = 'hackrf:iq:stream'
    STATE_KEY   = 'hackrf:state'
    CMD_KEY     = 'hackrf:cmd'

    def __init__(self, iq_queue: queue.Queue, node, logger, maxlen: int = 10000) -> None:
        self._iq_queue = iq_queue
        self._node = node          # HackRFNode reference for command dispatch
        self._logger = logger
        self._maxlen = maxlen

        self._redis: redis.Redis | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()   # protect publish_state() from any thread

    def open(self) -> bool:
        """Connect to Redis and start daemon thread. Returns True on success."""
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
            self._logger.warning(f'RedisBridge: Redis unavailable at startup: {e}. '
                                 'IQ will stream on ROS2 topics only.')
            self._redis = None
            return False

    def close(self) -> None:
        """Signal bridge thread to stop."""
        self._stop_event.set()
        # Thread is daemon — no join needed; will exit with process

    def publish_state(self, state: dict) -> None:
        """HSET hackrf:state with the given mapping. Call on any config change.

        Thread-safe: may be called from ROS2 executor or any other thread.
        No-op if Redis is not connected.
        """
        if self._redis is None:
            return
        try:
            with self._state_lock:
                self._redis.hset(self.STATE_KEY, mapping={k: str(v) for k, v in state.items()})
        except redis.exceptions.RedisError as e:
            self._logger.warning(f'RedisBridge: publish_state failed: {e}')

    @property
    def needs_reconnect(self) -> bool:
        """True when the bridge thread has lost the Redis connection."""
        return self._stop_event.is_set() and self._thread is not None

    def _bridge_loop(self) -> None:
        """Main daemon thread: IQ publish + command consumer."""
        last_cmd_id = b'$'   # read only new commands from now
        while not self._stop_event.is_set():
            self._drain_iq_queue()
            self._poll_commands(last_cmd_id)

    def _drain_iq_queue(self) -> None:
        """Non-blocking drain of IQ queue; XADD each chunk."""
        while not self._stop_event.is_set():
            try:
                chunk: bytes = self._iq_queue.get_nowait()
            except queue.Empty:
                break
            self._xadd_iq(chunk)

    def _xadd_iq(self, chunk: bytes) -> None:
        """XADD one IQ chunk to hackrf:iq:stream."""
        try:
            self._redis.xadd(
                self.STREAM_KEY,
                {b'data': chunk},
                maxlen=self._maxlen,
                approximate=True,
            )
        except redis.exceptions.RedisError as e:
            self._logger.warning(f'RedisBridge: XADD failed: {e}')
            self._stop_event.set()

    def _poll_commands(self, last_id: bytes) -> bytes:
        """XREAD with 200ms block; dispatch any commands received. Returns updated last_id."""
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
```

**Note:** `_poll_commands` must store `last_id` as a loop variable in `_bridge_loop`, not discard the return value. See Pattern 3 for the corrected loop structure.

### Pattern 2: HackRFNode Integration Points

**What:** RedisBridge is instantiated in `__init__`, declared with ROS2 parameter, and closed in `destroy_node` before MayhemSerial.

```python
# In HackRFNode.__init__ (after MayhemSerial setup):
self.declare_parameter(
    'redis_stream_maxlen', 10000,
    ParameterDescriptor(description='Redis Stream MAXLEN for hackrf:iq:stream (D-04)')
)
_maxlen = self.get_parameter('redis_stream_maxlen').get_parameter_value().integer_value
self._redis_bridge = RedisBridge(
    self._redis_queue, self, self.get_logger(), maxlen=_maxlen
)
self._redis_bridge.open()   # graceful degradation: returns False if Redis unavailable

# In HackRFNode.destroy_node (before MayhemSerial close):
if hasattr(self, '_redis_bridge'):
    self._redis_bridge.close()
```

### Pattern 3: Bridge Loop with Correct last_id Tracking

```python
def _bridge_loop(self) -> None:
    last_cmd_id = b'$'
    while not self._stop_event.is_set():
        self._drain_iq_queue()
        last_cmd_id = self._poll_commands(last_cmd_id)
```

### Pattern 4: State Update on Config Change

`publish_state()` is called from `_on_parameter_event` and from Mayhem service handlers whenever state changes (D-09: on change only, no heartbeat):

```python
# In HackRFNode._on_parameter_event, after applying params:
if hasattr(self, '_redis_bridge'):
    self._redis_bridge.publish_state(self._build_state_dict())

def _build_state_dict(self) -> dict:
    """Build the full hackrf:state mapping from current node state."""
    return {
        'center_frequency': self._last_params['center_frequency'],
        'sample_rate':      self._last_params['sample_rate'],
        'lna_gain':         self._last_params['lna_gain'],
        'vga_gain':         self._last_params['vga_gain'],
        'amp_enabled':      self._last_params['amp_enabled'],
        'is_streaming':     self.is_hackrf_streaming,
        'connected':        self._hackrf is not None,
        'uptime_s':         time.monotonic() - self._start_time,
        'active_app':       getattr(self._mayhem, '_active_app', ''),
        'discovered_apps':  json.dumps(self._mayhem._known_apps),
        'serial_connected': self._serial_connected,
    }
```

`HackRFNode.__init__` must record `self._start_time = time.monotonic()` for uptime calculation.

### Pattern 5: IQ Encoding (Verified)

The `_rx_callback` deposits raw int8 bytes into `_redis_queue`. The bridge thread receives them as `bytes`. The ROS2 publisher path converts int8 → float32 for the topic. The Redis path publishes raw int8 bytes directly for throughput, OR converts to float32 per D-03.

**Decision constraint (D-03):** CONTEXT.md specifies float32 interleaved encoding in Redis Streams. This means the bridge thread must convert the raw int8 bytes before publishing to Redis, the same way the ROS2 publisher does:

```python
def _xadd_iq(self, chunk: bytes) -> None:
    iq = np.frombuffer(chunk, dtype=np.int8).reshape(-1, 2)
    float32 = np.empty(len(chunk), dtype=np.float32)
    float32[0::2] = iq[:, 0].astype(np.float32) / 128.0   # I
    float32[1::2] = iq[:, 1].astype(np.float32) / 128.0   # Q
    self._redis.xadd(
        self.STREAM_KEY,
        {b'data': float32.tobytes()},
        maxlen=self._maxlen,
        approximate=True,
    )
```

**Performance note:** At 8 MSPS with 2048-pair chunks, this is ~8192 float32 ops per call at ~6400 calls/second. This is modest for NumPy but worth profiling if bridge thread falls behind queue fill rate. If profiling shows bottleneck: switch to raw int8 bytes and update D-03 (requires user approval since D-03 is locked).

### Anti-Patterns to Avoid

- **Calling `r.xadd()` inside `_rx_callback` or `_publish_iq`:** Blocks the pyhackrf2 USB interrupt or ROS2 executor. All Redis I/O in the bridge thread only (D-11).
- **Using `decode_responses=True` on the Redis client:** IQ data is binary bytes; `decode_responses=True` would break binary field values. Keep `decode_responses=False` and decode command JSON manually.
- **Using `XREAD` with `block=0` (infinite block):** If Redis goes away, the thread hangs forever. Use `block=200` (200ms) so `_stop_event` is checked regularly.
- **`XREAD` with starting ID `'0'` instead of `'$'` on startup:** Would replay all historical commands in the stream. Start with `b'$'` to consume only new commands.
- **Creating a new `redis.Redis()` connection per XADD call:** Creates a new TCP connection each time. Create once in `open()` and reuse throughout the thread lifetime. The single connection is safe because the bridge thread is the only writer.
- **Ignoring `RedisError` in the bridge loop:** Swallowing errors hides Redis disconnects. On `RedisError` in XADD or XREAD, set `_stop_event` to trigger reconnect detection.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Thread-safe queue handoff from pyhackrf2 callback | Custom ring buffer, deque, or lock | `queue.Queue(maxsize=64)` (already exists as `_redis_queue`) | Already implemented in Phase 1; thread-safe, drop-oldest overflow |
| Redis stream trimming | Periodic DEL + XADD, custom size tracking | `XADD maxlen ~ N` (approximate=True) | Amortized O(1); built into Redis protocol |
| Binary serialization of float32 IQ | Custom base64, pickle, JSON float array | `numpy.ndarray.tobytes()` / `numpy.frombuffer()` | Zero-copy; preserves IEEE 754 exactly |
| JSON command parsing | Custom line protocol, regex | `json.loads()` | Commands are already specified as JSON (D-05) |

**Key insight:** The queue handoff, Redis trimming, and binary serialization are all solved problems in this stack. Every hand-rolled alternative introduces a bug surface.

---

## Common Pitfalls

### Pitfall 1: float32 Conversion in Bridge Thread at High Rate
**What goes wrong:** D-03 requires float32 in the Redis stream. The `_rx_callback` deposits raw int8 bytes to both queues (ROS and Redis). At 8 MSPS the bridge thread must convert ~6400 chunks/second from int8 to float32. If NumPy conversion time exceeds queue fill rate, the `_redis_queue` fills and data is dropped.
**Why it happens:** Float32 conversion is not free. Two conversions (one for ROS topic, one for Redis) run concurrently but in different threads.
**How to avoid:** Benchmark the bridge thread at the target sample rate. If the queue depth grows monotonically, consider whether the ROS publisher should do the float32 conversion and share the result — but this would require architectural changes. As designed, two independent conversions are the simplest correct approach.
**Warning signs:** `_redis_queue.qsize()` at `maxsize=64` consistently; log warning when drop-oldest fires.

### Pitfall 2: Redis Server Version — No GETDEL
**What goes wrong:** The running Redis server is 6.0.16. `GETDEL` requires Redis 6.2+. Phase 4 (TX auth) relies on `GETDEL` per STACK.md.
**Why it happens:** Ubuntu 20.04's default Redis package is 6.0.x.
**How to avoid:** Phase 3 does not use `GETDEL` — no impact here. But Phase 4 must either: (a) upgrade Redis, (b) use a Lua script atomic equivalent, or (c) use `GET` + `DEL` in a pipeline (not strictly atomic but acceptable for single-consumer scenarios). Flag for Phase 4.
**Warning signs:** Phase 3 unaffected. Phase 4 plan must note Redis version requirement.

### Pitfall 3: Command last_id Not Persisted Across Reconnects
**What goes wrong:** If RedisBridge reconnects (open/close/open cycle), `last_cmd_id` resets to `b'$'`. Any commands sent during the disconnection window are permanently missed.
**Why it happens:** `last_cmd_id` is a local variable in `_bridge_loop()`. On reconnect, a new thread starts with `b'$'`.
**How to avoid:** This is acceptable behavior per D-07 (command responses not published; callers check hackrf:state). Document the trade-off: commands are fire-and-forget; callers must retry if the bridge was disconnected.
**Warning signs:** None — this is a documented behavioral choice, not a bug.

### Pitfall 4: `publish_state()` Called Frequently Under ROS2 Executor
**What goes wrong:** `_on_parameter_event` may fire multiple times in rapid succession (e.g., a Redis command changes frequency, which triggers a ROS2 parameter update, which triggers `_on_parameter_event`). Each call does an HSET over the bridge thread's shared connection.
**Why it happens:** `publish_state()` acquires `_state_lock` and calls `hset` directly on the shared `_redis` object. The `_state_lock` prevents corruption, but the frequency of HSET calls may cause lock contention with the bridge thread's XADD.
**How to avoid:** Per D-09, state is updated on change only — no periodic heartbeat. The `_state_lock` is sufficient. If profiling shows contention, use a separate Redis connection for state updates (the ConnectionPool supports this pattern).
**Warning signs:** Lock wait time visible in profiling; ROS2 executor callback latency spikes on state updates.

### Pitfall 5: `_redis_queue` Is Shared Between Bridge Reconnect Cycles
**What goes wrong:** If the bridge disconnects and reconnects, the existing `_redis_queue` may contain stale IQ chunks from before the reconnect. These will be published to Redis after reconnect with incorrect timestamps relative to the stream.
**Why it happens:** `_redis_queue` is owned by HackRFNode and populated continuously by `_rx_callback`. It is not drained or reset during a bridge reconnect.
**How to avoid:** Acceptable behavior — Redis Streams use server-assigned timestamps (auto-ID `*`). Consumers care about data order relative to the stream, not absolute wall time. Publish whatever is in the queue on reconnect.
**Warning signs:** None — this is correct behavior for a ring-buffer streaming architecture.

---

## Code Examples

Verified against redis-py 7.4.0 and Redis 6.0.16 running locally.

### XADD with Approximate MAXLEN (Verified Live)

```python
# Source: live test with redis-py 7.4.0 on Redis 6.0.16
import numpy as np
data = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
entry_id = r.xadd(
    'hackrf:iq:stream',
    {b'data': data.tobytes()},
    maxlen=10000,
    approximate=True,   # '~' modifier — amortized O(1) trim
)
# entry_id: b'1774848243024-0'  (auto-assigned by Redis)
```

### HSET Mapping for State Hash (Verified Live)

```python
# Source: live test with redis-py 7.4.0 on Redis 6.0.16
r.hset('hackrf:state', mapping={
    'center_frequency': '2447000000',
    'sample_rate': '8000000',
    'lna_gain': '16',
    'vga_gain': '20',
    'amp_enabled': 'false',
    'is_streaming': 'true',
    'connected': 'true',
    'uptime_s': '42.5',
    'active_app': 'capture',
    'discovered_apps': '["capture", "scanner"]',
    'serial_connected': 'true',
})
```

### XREAD with Block Timeout for Command Consumer (Verified Live)

```python
# Source: live test with redis-py 7.4.0 on Redis 6.0.16
# block=200 means wait up to 200ms then return [] if no new entries
last_id = b'$'   # only new commands from now
results = r.xread({'hackrf:cmd': last_id}, count=10, block=200)
if results:
    stream_name, entries = results[0]
    for entry_id, fields in entries:
        last_id = entry_id   # advance cursor
        cmd = json.loads(fields[b'cmd'].decode('utf-8'))
        # dispatch cmd...
```

### Connection Error Handling for Graceful Degradation

```python
# Source: live test — exception type for unreachable Redis
try:
    r = redis.Redis(host='localhost', port=6379, decode_responses=False)
    r.ping()
except redis.exceptions.ConnectionError as e:
    logger.warning(f'Redis unavailable: {e}')
    # continue without Redis — ROS2 topic still works
```

### Command Dispatch Pattern

```python
# D-06 accepted actions routed to HackRFNode methods
_COMMAND_HANDLERS = {
    'setfreq':         lambda node, p: node._set_center_frequency(p['freq_hz']),
    'set_sample_rate': lambda node, p: node._set_sample_rate(p['sample_rate']),
    'set_lna_gain':    lambda node, p: node._set_lna_gain(p['lna_gain']),
    'set_vga_gain':    lambda node, p: node._set_vga_gain(p['vga_gain']),
    'set_amp_enabled': lambda node, p: node._set_amp_enabled(p['enabled']),
    'appstart':        lambda node, p: node._mayhem.appstart(p['app_name']),
    'serial_setfreq':  lambda node, p: node._mayhem.setfreq(int(p['freq_hz'])),
    'start_rx':        lambda node, p: node._start_rx_if_stopped(),
    'stop_rx':         lambda node, p: node._stop_rx_if_running(),
}

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

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| aioredis (separate package) | Merged into redis-py v4.2+ | 2022 | Do not install aioredis; use redis-py directly |
| redis-py Pub/Sub for command channel | Redis Streams + XREAD BLOCK | Project decision | Streams preserve history; XREAD BLOCK is more reliable than Pub/Sub for reconnect scenarios |
| redis-py ConnectionPool explicit config | Default ConnectionPool (maxconn=2^31) | redis-py v4+ | Default pool is fine for a single-thread bridge; no explicit pool config needed |

**Deprecated/outdated:**
- `redis-py` `StrictRedis`: Merged into `Redis` class in v3.0. Use `redis.Redis()` only.
- `aioredis`: Abandoned as standalone package. Merged into redis-py.

---

## Open Questions

1. **MayhemSerial `_active_app` attribute missing**
   - What we know: `_build_state_dict()` references `self._mayhem._active_app`. MayhemSerial does not currently track the active app.
   - What's unclear: Should `appstart()` in MayhemSerial update an `_active_app` attribute?
   - Recommendation: Add `self._active_app: str = ''` to MayhemSerial, updated in `appstart()` on success. Small change to mayhem_serial.py.

2. **`_start_rx_if_stopped` and `_stop_rx_if_running` helpers on HackRFNode**
   - What we know: D-06 includes `start_rx` and `stop_rx` as accepted Redis commands. HackRFNode has `_configure_device()` and `_try_connect()` but no thin wrappers for start/stop only.
   - What's unclear: Should these be new HackRFNode methods, or should the dispatcher call `_configure_device()` with unchanged params?
   - Recommendation: Add `_start_rx_if_stopped()` and `_stop_rx_if_running()` as thin methods on HackRFNode that acquire `_device_lock` and call `start_rx`/`stop_rx` safely.

3. **Thread safety of `publish_state()` vs bridge thread's Redis connection**
   - What we know: redis-py `Redis()` backed by `ConnectionPool` is thread-safe — each call borrows a connection from the pool. Concurrent calls from two threads are safe.
   - What's unclear: Default pool is effectively unbounded (`max_connections=2^31`). With one bridge thread and one ROS2 executor thread calling `publish_state()`, two connections will be borrowed concurrently at peak.
   - Recommendation: The default pool handles this correctly. `_state_lock` in `publish_state()` can be removed — it was defensive coding, not required. The planner should decide whether to keep it (safer, slight overhead) or remove it (simpler).

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Redis server | IQ streaming, state, commands | Yes | 6.0.16 | — |
| redis-py | RedisBridge client | Yes | 7.4.0 | — |
| hiredis | redis-py parser acceleration | Yes | 3.3.1 | redis-py uses pure Python parser (slower but functional) |
| Python 3.10 | redis-py 7.x requirement | Yes | 3.10 (ROS2 Humble) | — |

**Missing dependencies with no fallback:** None — all required dependencies are available.

**Redis version constraint:** Running Redis is 6.0.16. `GETDEL` (needed by Phase 4 TX auth) requires 6.2+. Phase 3 is unaffected — it does not use `GETDEL`. Phase 4 planner must address this.

---

## Project Constraints (from CLAUDE.md)

Directives extracted from CLAUDE.md that the planner must verify:

| Directive | Source | Applies To |
|-----------|--------|------------|
| File names: `lowercase_with_underscores.py` | CONVENTIONS.md | `redis_bridge.py` (correct) |
| Class names: PascalCase | CONVENTIONS.md | `RedisBridge` (correct) |
| Private methods: single underscore prefix | CONVENTIONS.md | `_bridge_loop`, `_drain_iq_queue`, etc. |
| Logging via `self._logger.info/warning/error()` — no bare `print()` | CONVENTIONS.md | All bridge log calls |
| Error handling: broad `except Exception` with logger | CONVENTIONS.md | Command dispatch errors |
| Methods organized: `__init__`, config, lifecycle, callbacks, utilities | CONVENTIONS.md | RedisBridge class method order |
| `destroy_node()` cleanup in HackRFNode | CONVENTIONS.md | `bridge.close()` before `mayhem.close()` |
| ROS2 parameters declared in `__init__` with descriptors | CONVENTIONS.md | `redis_stream_maxlen` parameter |
| Graceful degradation: node continues if Redis unavailable | CONSTRAINTS / D-02 | `open()` returns False, no exception propagation |
| All Redis I/O in dedicated thread | D-11 | `publish_state()` uses `_state_lock` and direct `hset`; acceptable since it borrows from pool; bridge loop has no ROS2 calls |
| GSD workflow enforcement | CLAUDE.md | All file edits via `/gsd:execute-phase` |

---

## Sources

### Primary (HIGH confidence)

- Live redis-py 7.4.0 tests on Redis 6.0.16 — XADD, XREAD, HSET all verified working
- redis-py API: `inspect.signature()` on `xadd`, `xread`, `hset`, `ConnectionPool.__init__`
- Existing codebase: `hackrf_ros/hackrf_node.py` (direct read — `_redis_queue`, reconnect pattern, `_stop_event`)
- Existing codebase: `hackrf_ros/mayhem_serial.py` (direct read — class structure to replicate)
- `.planning/phases/03-redis-bridge/03-CONTEXT.md` (locked decisions D-01 through D-12)

### Secondary (MEDIUM confidence)

- `.planning/research/STACK.md` — redis-py version rationale, Redis Streams vs Pub/Sub reasoning
- `.planning/research/ARCHITECTURE.md` — RedisBridge daemon thread design
- `.planning/research/PITFALLS.md` — Redis OOM without MAXLEN, backpressure pitfalls

### Tertiary (LOW confidence)

None — all findings backed by PRIMARY or SECONDARY sources verified against live environment.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — versions installed and live-tested
- Architecture: HIGH — MayhemSerial pattern directly applicable; confirmed via code inspection
- Pitfalls: HIGH — Pitfall 2 (Redis version) confirmed by live `redis-cli --version`; others from established patterns
- API patterns: HIGH — all redis-py calls live-tested against running Redis 6.0.16

**Research date:** 2026-03-29
**Valid until:** 2026-04-29 (redis-py is stable; Redis 6.0 API unchanged)
