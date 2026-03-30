# Architecture Patterns — v2.0 Integration

**Domain:** ROS2 SDR driver — hardening, observability, and signal capability extensions
**Researched:** 2026-03-30
**Overall confidence:** HIGH (based on direct codebase inspection, not speculation)

---

## Context: Existing Architecture (v1 / Phase 5 end state)

The codebase after Phase 5 has three packages with clean dependency boundaries:

```
pymayhem/               pip-installable, no ROS2/Redis dependency
  pymayhem/
    _serial.py          MayhemSerial — daemon reader thread, queue, _send_command
    client.py           MayhemClient — domain sub-objects (radio, system, ui, fs, sensors)
    domains/            RadioDomain, SystemDomain, UIDomain, FsDomain, SensorsDomain
    unsafe_client.py    UnsafeMayhemClient — raw command passthrough

hackrf_driver/          pip-installable, no rclpy dependency
  hackrf_driver/
    config.py           PARAM_RANGES, DEFAULT_CONFIG, load_config()
    driver.py           HackRFDriver — main loop, threading.Event stop gate, Timer reconnect
    redis_bridge.py     RedisBridge — daemon thread, XADD/XREAD/HSET/Pub-Sub
    tx_controller.py    TXController — 4-gate guard with custom exceptions
    cli.py              CLI entry point

hackrf_ros/             ROS2 package, no hardware imports
  hackrf_ros/
    bridge_node.py      BridgeNode — Redis Pub-Sub -> /hackrf/iq, /hackrf/state
    bridge_services.py  closure factories for ROS2 services -> Redis cmds
    hackrf_node.py      legacy, to be removed
```

**Existing data flow:**
```
pyhackrf2 USB CB
  -> _rx_callback(raw_bytes)
       -> _ros_queue.put_nowait()   # drained + discarded by _iq_publish_loop
       -> _redis_queue.put_nowait() # consumed by RedisBridge._drain_iq_queue

RedisBridge daemon thread
  -> _xadd_iq(chunk) : int8 bytes -> float32 -> XADD hackrf:iq:stream
  -> redis.publish('hackrf:iq:notify', entry_id)   # notify BridgeNode

BridgeNode daemon thread
  -> pubsub.get_message(timeout=0.1)
  -> xrevrange(hackrf:iq:stream, count=1)
  -> publish Float32MultiArray -> /hackrf/iq
  -> _publish_state() -> HGETALL hackrf:state -> /hackrf/state (JSON)
```

**Existing Redis key namespace:**
- `hackrf:iq:stream`       — Stream, float32 IQ chunks
- `hackrf:iq:notify`       — Pub/Sub channel (entry_id after each XADD)
- `hackrf:state`           — Hash, device state
- `hackrf:cmd`             — Stream, JSON commands -> XREAD by RedisBridge
- `hackrf:tx:auth`         — String, one-time TX auth token
- `hackrf:tx:antenna_confirmed` — String, '1' = confirmed
- `hackrf:tx:freq_filter_override` — String, 'disabled' = bypass filter
- `hackrf:tx:iq_data`      — String (bytes), TX IQ payload

---

## New Feature Integration Points

### Feature 1: IQ Recording (SigMF / raw files)

**Where it taps in:** The `_redis_queue` consumer path inside `hackrf_driver`.

Currently `_redis_queue` feeds `RedisBridge._drain_iq_queue()` → XADD. Recording is a
second consumer of the same raw int8 byte stream. Two implementation options:

**Option A — IQRecorder as third queue consumer (recommended):**
Add a `_record_queue = queue.Queue(maxsize=64)` in `HackRFDriver.__init__`. Modify
`_rx_callback` to also put chunks to `_record_queue` (same put_nowait / drop-oldest
pattern as the existing two queues). An `IQRecorder` object owns `_record_queue` and a
daemon thread that writes to disk.

```
_rx_callback
  -> _ros_queue.put_nowait()      # existing
  -> _redis_queue.put_nowait()    # existing
  -> _record_queue.put_nowait()   # NEW (only when recording active)
```

This keeps recording fully off the hot USB callback path — the callback just enqueues.
The recorder thread handles all file I/O, SigMF metadata serialization, and rotation.

**Option B — Record from Redis stream (not recommended):**
A separate process XREADs `hackrf:iq:stream`. This adds Redis round-trip latency, wastes
bandwidth storing data twice, and is less suited to continuous capture.

**New component:** `hackrf_driver/hackrf_driver/iq_recorder.py`

```python
class IQRecorder:
    """Daemon thread: drain _record_queue, write raw int8 or SigMF to disk."""
    def __init__(self, record_queue, logger): ...
    def start_recording(self, path, fmt='sigmf', metadata=None): ...
    def stop_recording(self): ...
    def is_recording(self) -> bool: ...
```

Recording lifecycle is controlled via Redis commands. Add two handlers to
`_COMMAND_HANDLERS` in `redis_bridge.py`:

```python
'start_recording': lambda driver, p: driver._iq_recorder.start_recording(
    p['path'], p.get('fmt', 'sigmf'), p.get('metadata', {})),
'stop_recording':  lambda driver, p: driver._iq_recorder.stop_recording(),
```

State dict extension (add to `_build_state_dict`):
```python
'recording':       self._iq_recorder.is_recording(),
'record_path':     self._iq_recorder.current_path or '',
```

**SigMF format:** `sigmf-meta` JSON + `sigmf-data` raw int8 binary (interleaved I/Q).
The recorder writes the data file during capture and finalizes the metadata file on stop.
Use stdlib `json` + `open(..., 'wb')` — no new dependency required.

**Modifications to existing files:**
- `hackrf_driver/driver.py` — add `_record_queue`, instantiate `IQRecorder`, add to
  `_rx_callback`, call `_iq_recorder.stop_recording()` in `shutdown()`
- `hackrf_driver/redis_bridge.py` — add `start_recording` / `stop_recording` handlers

**New files:**
- `hackrf_driver/hackrf_driver/iq_recorder.py`

---

### Feature 2: Spectral Analysis (FFT + waterfall)

**Where it taps in:** Post-conversion float32 data, NOT the raw int8 callback path.

FFT should operate on float32 complex data (already normalized). Two integration points:

**Option A — Tap in RedisBridge._xadd_iq() after conversion (recommended):**
After `_xadd_iq` converts `int8 -> float32`, the array is available. A shared
`_fft_queue` receives every Nth chunk (configurable decimation to limit CPU). A
`SpectrumAnalyzer` daemon thread reads from this queue, computes FFT via `numpy.fft`,
and publishes results to Redis.

```
RedisBridge._xadd_iq(chunk)
  1. int8 -> float32 conversion  (existing)
  2. XADD hackrf:iq:stream       (existing)
  3. _fft_queue.put_nowait(float32_arr)  # NEW (if fft enabled, every N chunks)
```

**Option B — Run FFT in BridgeNode after reading from stream:**
Read float32 from Redis, compute FFT, publish to ROS2. This is simpler to isolate but
doubles the Redis bandwidth consumption for spectral data and adds round-trip latency.
Use only if ROS2 consumers need the spectrum topic and you want to avoid hackrf_driver
knowing about spectrum.

Recommendation: Use Option A for low latency. Option B is acceptable if the spectrum
consumer is ROS2-only.

**New component:** `hackrf_driver/hackrf_driver/spectrum_analyzer.py`

```python
class SpectrumAnalyzer:
    """Daemon thread: drain _fft_queue, compute FFT, publish to Redis."""
    def __init__(self, fft_queue, redis_client, logger, fft_size=1024): ...
    def start(self): ...
    def stop(self): ...
    def set_decimation(self, n: int): ...  # only process every Nth chunk
```

Redis publishing: `XADD hackrf:spectrum` with fields:
- `data` — float32 power spectrum (dB) as raw bytes
- `center_freq` — current center frequency (Hz)
- `sample_rate` — current sample rate (Hz/s)
- `fft_size` — number of bins

Pub/Sub notification: `PUBLISH hackrf:spectrum:notify <entry_id>` after each XADD
(mirrors the existing `hackrf:iq:notify` pattern).

**BridgeNode extension** for `/hackrf/spectrum` ROS2 topic:
Add a second pubsub subscription to `hackrf:spectrum:notify` in `BridgeNode`. On each
notification, XREVRANGE `hackrf:spectrum` and publish `Float32MultiArray` to
`/hackrf/spectrum`. This follows the exact same pattern as the existing IQ bridge loop.

**New Redis keys:**
- `hackrf:spectrum`         — Stream, float32 power spectrum entries
- `hackrf:spectrum:notify`  — Pub/Sub channel

**Modifications to existing files:**
- `hackrf_driver/redis_bridge.py` — add `_fft_queue` injection, call
  `_fft_queue.put_nowait()` in `_xadd_iq()` when enabled
- `hackrf_driver/driver.py` — instantiate `SpectrumAnalyzer`, pass `_fft_queue`
- `hackrf_ros/bridge_node.py` — add second pubsub subscription + `/hackrf/spectrum`
  publisher

**New files:**
- `hackrf_driver/hackrf_driver/spectrum_analyzer.py`

**FFT dependency:** `numpy.fft` is already available (numpy is a required dependency).
No additional packages needed.

---

### Feature 3: Frequency Hopping Scheduler

**Where it taps in:** Calls `driver._set_center_frequency()` on a timer. Must not
conflict with active TX.

Frequency hopping is a stateful scheduler that runs inside `hackrf_driver`. It is NOT
a separate process — it must hold a reference to the driver to call frequency changes,
and it must query `_tx_controller._is_transmitting` before each hop to avoid hopping
during TX.

**New component:** `hackrf_driver/hackrf_driver/freq_hopper.py`

```python
class FreqHopper:
    """Programmable frequency hopping scheduler using threading.Timer chain."""
    def __init__(self, set_freq_fn, is_tx_fn, logger): ...
    def start(self, freqs: list[int], dwell_ms: int): ...
    def stop(self): ...
    def is_active(self) -> bool: ...
    @property
    def current_freq(self) -> int: ...
```

Implementation: single `threading.Timer` that reschedules itself. On each tick:
1. Check `is_tx_fn()` — if transmitting, skip hop (do not reschedule for dwell_ms,
   use a shorter retry_ms to check again)
2. Call `set_freq_fn(next_freq)` — this calls `driver._set_center_frequency()` which
   calls `_configure_device()` under `_device_lock`
3. Schedule next timer

**Redis command integration** — add to `_COMMAND_HANDLERS`:
```python
'start_hopping': lambda driver, p: driver._freq_hopper.start(
    p['freqs'], p.get('dwell_ms', 100)),
'stop_hopping':  lambda driver, p: driver._freq_hopper.stop(),
```

**TX interaction:** Frequency hopping and TX are mutually exclusive at the hardware
level (half-duplex). The `FreqHopper` must pause when TX is active. The existing
`TXController._stop_rx_fn` / `_start_rx_fn` pattern handles RX pause/resume for TX.
Hopping must NOT call `_set_center_frequency()` while `_tx_controller._is_transmitting`
is True, because `_configure_device()` would call `stop_rx()` / `start_rx()` on a
device in TX mode, which corrupts the TX state machine.

**Guard:** In the timer callback, check `_tx_controller._is_transmitting` before hop.
If True, back off 50ms and check again without advancing the freq index.

**State dict extension:**
```python
'hopping_active':   self._freq_hopper.is_active(),
'hopping_freq':     self._freq_hopper.current_freq,
```

**Modifications to existing files:**
- `hackrf_driver/driver.py` — instantiate `FreqHopper` with lambdas for
  `set_freq_fn=self._set_center_frequency` and `is_tx_fn=lambda: self._tx_controller._is_transmitting`
- `hackrf_driver/redis_bridge.py` — add `start_hopping` / `stop_hopping` handlers

**New files:**
- `hackrf_driver/hackrf_driver/freq_hopper.py`

---

### Feature 4: Async pymayhem API (asyncio)

**Where it taps in:** New `AsyncMayhemClient` class in `pymayhem`. Does NOT touch
`hackrf_driver` or `hackrf_ros` — the async API is an additive layer in pymayhem only.

**Design constraint:** `MayhemSerial` uses blocking `serial.readline()` in a daemon
thread. asyncio cannot share a thread with blocking serial I/O. The async client must
either:

**Option A — Run sync client in executor (recommended for minimal change):**
`AsyncMayhemClient` wraps `MayhemClient` and runs every command in
`asyncio.get_event_loop().run_in_executor(None, ...)`. This dispatches blocking calls
to the default `ThreadPoolExecutor` and returns awaitables. All existing sync logic
(retry, lock, queue) is preserved.

```python
class AsyncMayhemClient:
    def __init__(self, port, timeout=3.0):
        self._sync = MayhemClient(port, timeout)
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    async def open(self) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self._sync.open)

    async def close(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, self._sync.close)

    # Domain sub-objects return async wrappers
    @property
    def radio(self) -> AsyncRadioDomain: ...
    @property
    def system(self) -> AsyncSystemDomain: ...
    # etc.
```

**Option B — Native asyncio serial (asyncio.StreamReader + serial_asyncio):**
Requires `serial_asyncio` library and a rewritten reader loop. More work, more
dependencies, no benefit unless the caller is already a pure asyncio application.

Use Option A. It adds zero new dependencies to pymayhem and composes correctly with
`hackrf_driver` (which uses threads, not asyncio).

**New files in pymayhem:**
- `pymayhem/pymayhem/async_client.py` — `AsyncMayhemClient`
- `pymayhem/pymayhem/domains/async_radio.py` — `AsyncRadioDomain`
- `pymayhem/pymayhem/domains/async_system.py` — `AsyncSystemDomain`
- (async wrappers for ui, fs, sensors if needed)

**Existing files unchanged:** `_serial.py`, `client.py`, all sync domain files.

The `hackrf_driver`'s `HackRFDriver` continues to use the sync `MayhemClient` directly
via `self._mayhem = MayhemClient(serial_port)`. No change to the driver is needed.

---

### Feature 5: Observability Metrics

**Where it taps in:** `RedisBridge` daemon thread publishes to a new `hackrf:metrics`
hash on a periodic tick (e.g. every 1s).

Metrics to collect:
- `rx_chunks_total` — monotonic counter of RX chunks processed
- `redis_queue_depth` — current `_redis_queue.qsize()`
- `ros_queue_depth` — current `_ros_queue.qsize()`
- `xadd_errors_total` — count of RedisError on XADD
- `cmd_dispatched_total` — count of commands dispatched
- `uptime_s` — from `driver._start_time`
- `iq_rate_hz` — chunks per second (rolling 1s window)

**Implementation:** Add a `_metrics` dict to `RedisBridge`. Increment counters at
relevant call sites. Add a `_publish_metrics()` call inside `_bridge_loop()` on a
1-second timer using `time.monotonic()` comparison (no new thread needed).

```python
def _bridge_loop(self) -> None:
    last_metrics_t = time.monotonic()
    while not self._stop_event.is_set():
        self._drain_iq_queue()
        last_cmd_id = self._poll_commands(last_cmd_id)
        now = time.monotonic()
        if now - last_metrics_t >= 1.0:
            self._publish_metrics()
            last_metrics_t = now
```

**New Redis key:** `hackrf:metrics` — Hash, updated every 1s via `HSET`.

**BridgeNode extension:** Add a timer callback (e.g. 5s period) that reads
`hackrf:metrics` and publishes `String` (JSON) to `/hackrf/metrics` ROS2 topic.
This is lower frequency than IQ — no Pub/Sub needed, a simple timer is sufficient.

**Modifications to existing files:**
- `hackrf_driver/redis_bridge.py` — add `_metrics` dict, counter increments,
  `_publish_metrics()`, metrics call in `_bridge_loop()`
- `hackrf_ros/bridge_node.py` — add 5s timer + `/hackrf/metrics` publisher

---

### Feature 6: Hardening (exceptions, validation, watchdog, sequence numbers)

**Where it taps in:** Multiple existing files with targeted additions.

**Custom exceptions (pymayhem):** Add `pymayhem/pymayhem/exceptions.py`:
```python
class MayhemError(Exception): ...
class MayhemTimeoutError(MayhemError): ...
class MayhemSerialError(MayhemError): ...
class MayhemCommandError(MayhemError): ...
```
`MayhemSerial._send_command()` raises typed exceptions instead of propagating
`TimeoutError` (stdlib) directly. Domain methods catch and re-raise as typed errors.

**Custom exceptions (hackrf_driver):** Add `hackrf_driver/hackrf_driver/exceptions.py`:
```python
class HackRFError(Exception): ...
class HackRFConnectionError(HackRFError): ...
class HackRFConfigError(HackRFError): ...
```
`HackRFDriver._update_param()` raises `HackRFConfigError` on validation failure
instead of silently returning.

**Input validation:** `_update_param()` currently logs and returns. Change to raise
`HackRFConfigError` so Redis command handlers can log the error with the rejected value.

**IQ sequence numbers:** Add a monotonic `_iq_seq` counter to `HackRFDriver`. Increment
in `_rx_callback`. Include in each XADD entry:
```python
entry_id = self._redis.xadd(
    self.STREAM_KEY,
    {b'data': float32_arr.tobytes(), b'seq': str(self._iq_seq).encode()},
    ...
)
```
This lets consumers detect dropped chunks without adding overhead to the hot path.

**Device health watchdog:** Add `_watchdog_timer` in `HackRFDriver`. Every 10s, check:
- `is_hackrf_streaming` is True
- `_last_rx_time` is within last 5s (add `_last_rx_time = time.monotonic()` update in
  `_rx_callback`)

If stale, log error and call `_try_connect()` (triggers reconnect).

**Redis reconnection in BridgeNode:** `BridgeNode._bridge_loop()` currently exits on
`get_message` error and does not recover. Add a retry loop with backoff that calls
`self._redis.ping()` and re-subscribes to `hackrf:iq:notify`.

**Dead-letter queue:** Add `hackrf:cmd:dlq` Stream. In `RedisBridge._dispatch_command()`,
on exception, `XADD hackrf:cmd:dlq {cmd: ..., error: ..., ts: ...}`. This replaces the
current behavior of logging and discarding. DLQ entries are capped with `MAXLEN=500`.

**TX dry-run validation:** Add `dry_run` field to `start_tx` command handler. If
`cmd.get('dry_run') == True`, run all 4 guards but do NOT call `hackrf.start_tx()`.
Return result via `HSET hackrf:tx:dryrun_result {freq_hz: ..., passed: 'true'/'false', reason: ...}`.

---

## New Components Summary

| Component | Package | Type | New File |
|-----------|---------|------|----------|
| `IQRecorder` | hackrf_driver | Class + daemon thread | `iq_recorder.py` |
| `SpectrumAnalyzer` | hackrf_driver | Class + daemon thread | `spectrum_analyzer.py` |
| `FreqHopper` | hackrf_driver | Class + Timer chain | `freq_hopper.py` |
| `AsyncMayhemClient` | pymayhem | Class (executor wrapper) | `async_client.py` |
| Async domain wrappers | pymayhem | Classes | `domains/async_*.py` |
| `exceptions.py` | hackrf_driver | Module | `exceptions.py` |
| `exceptions.py` | pymayhem | Module | `exceptions.py` |

---

## Modified Components Summary

| File | What Changes |
|------|-------------|
| `hackrf_driver/driver.py` | Add `_record_queue`, `_fft_queue`, `_iq_recorder`, `_spectrum_analyzer`, `_freq_hopper`, `_watchdog_timer`, `_iq_seq`, `_last_rx_time`; extend `_rx_callback`, `_build_state_dict`, `shutdown()` |
| `hackrf_driver/redis_bridge.py` | Add metrics counters, `_publish_metrics()`, metrics call in loop; add `start_recording`, `stop_recording`, `start_hopping`, `stop_hopping` handlers; add DLQ XADD on dispatch error; add `b'seq'` field to XADD |
| `hackrf_ros/bridge_node.py` | Add spectrum pubsub + `/hackrf/spectrum` publisher; add 5s metrics timer + `/hackrf/metrics` publisher; add Redis reconnect loop with backoff |
| `pymayhem/_serial.py` | Raise typed exceptions from `_send_command()`; import from `exceptions.py` |
| `pymayhem/client.py` | No structural change; typed exceptions propagate through |

---

## Updated Data Flow Diagram

```
pyhackrf2 USB CB
  -> _rx_callback(raw_bytes)
       _iq_seq += 1
       _last_rx_time = now
       -> _ros_queue.put_nowait()       # BridgeNode via _iq_publish_loop
       -> _redis_queue.put_nowait()     # RedisBridge daemon
       -> _record_queue.put_nowait()    # IQRecorder daemon (if recording)

RedisBridge daemon thread
  -> _drain_iq_queue()
       _xadd_iq(chunk)
         int8 -> float32
         XADD hackrf:iq:stream {data, seq}
         PUBLISH hackrf:iq:notify entry_id
         _fft_queue.put_nowait(float32_arr)  # SpectrumAnalyzer (every N)
  -> _poll_commands(last_id)
       XREAD hackrf:cmd
       _dispatch_command() / DLQ on error
  -> every 1s: _publish_metrics()
       HSET hackrf:metrics {rx_chunks, queue_depth, uptime_s, ...}

IQRecorder daemon thread
  -> drain _record_queue
  -> write raw int8 to sigmf-data file
  -> finalize sigmf-meta JSON on stop()

SpectrumAnalyzer daemon thread
  -> drain _fft_queue (every Nth chunk)
  -> numpy.fft.fft(complex_arr)
  -> XADD hackrf:spectrum {data (power dB, float32), center_freq, sample_rate}
  -> PUBLISH hackrf:spectrum:notify entry_id

FreqHopper Timer chain
  -> check _tx_controller._is_transmitting
  -> if not TX: _set_center_frequency(next_freq)
  -> reschedule Timer(dwell_ms)

BridgeNode daemon thread (IQ)
  -> pubsub.get_message() on hackrf:iq:notify
  -> XREVRANGE hackrf:iq:stream
  -> publish Float32MultiArray -> /hackrf/iq
  -> _publish_state() -> /hackrf/state

BridgeNode daemon thread (spectrum — new)
  -> pubsub.get_message() on hackrf:spectrum:notify
  -> XREVRANGE hackrf:spectrum
  -> publish Float32MultiArray -> /hackrf/spectrum

BridgeNode 5s timer (metrics — new)
  -> HGETALL hackrf:metrics
  -> publish String (JSON) -> /hackrf/metrics
```

---

## Updated Redis Key Namespace

| Key | Type | Owner | Purpose |
|-----|------|-------|---------|
| `hackrf:iq:stream` | Stream | RedisBridge | float32 IQ chunks (existing) |
| `hackrf:iq:notify` | Pub/Sub | RedisBridge | XADD notification (existing) |
| `hackrf:state` | Hash | RedisBridge | device state (existing) |
| `hackrf:cmd` | Stream | RedisBridge reader | JSON commands (existing) |
| `hackrf:cmd:dlq` | Stream | RedisBridge | dead-letter for failed commands (new) |
| `hackrf:tx:auth` | String | TXController | one-time auth token (existing) |
| `hackrf:tx:antenna_confirmed` | String | TXController | '1' = confirmed (existing) |
| `hackrf:tx:freq_filter_override` | String | TXController | 'disabled' = bypass (existing) |
| `hackrf:tx:iq_data` | String | TXController | TX IQ payload (existing) |
| `hackrf:tx:dryrun_result` | Hash | TXController | dry-run validation result (new) |
| `hackrf:spectrum` | Stream | SpectrumAnalyzer | float32 power spectrum (new) |
| `hackrf:spectrum:notify` | Pub/Sub | SpectrumAnalyzer | spectrum XADD notification (new) |
| `hackrf:metrics` | Hash | RedisBridge | operational metrics (new) |

---

## Thread Model (v2.0)

```
Thread                 Owns                          Shares (protected by)
──────────────────────────────────────────────────────────────────────────
pyhackrf2 USB CB       raw byte enqueue              _ros_queue, _redis_queue,
                       _iq_seq counter               _record_queue (queue.Queue)
                       _last_rx_time update

iq_publish_loop        drain _ros_queue              _ros_queue (queue.Queue)
daemon thread          (discard in standalone mode)

redis_bridge daemon    XADD, XREAD, HSET, PUBLISH    _redis_queue (queue.Queue)
                       metrics tick                   _fft_queue (queue.Queue)
                                                      _state_lock (Lock)

iq_recorder daemon     drain _record_queue            _record_queue (queue.Queue)
(when active)          write to disk                  _record_lock (Lock)

spectrum_analyzer      drain _fft_queue               _fft_queue (queue.Queue)
daemon                 numpy FFT + XADD

freq_hopper Timer      read _last_params              _device_lock (RLock, via
chain                  call _set_center_frequency     _configure_device)

watchdog Timer         read is_hackrf_streaming       no new locks needed
                       check _last_rx_time

BridgeNode daemon      pubsub IQ + spectrum           Redis client (connection-safe)
(ROS2 process)         xrevrange + publish

BridgeNode timer       HGETALL metrics                Redis client
(ROS2 process)         publish /hackrf/metrics
```

---

## Dependency Diagram (v2.0 New Components)

```
pymayhem
  exceptions.py          <- _serial.py, domains/*
  async_client.py        <- client.py (wraps MayhemClient)
  domains/async_*.py     <- async_client.py

hackrf_driver
  exceptions.py          <- driver.py, redis_bridge.py
  iq_recorder.py         <- driver.py (instantiated, queue injected)
  spectrum_analyzer.py   <- redis_bridge.py (queue injected at construction)
  freq_hopper.py         <- driver.py (instantiated with lambda callables)
  driver.py              <- iq_recorder, spectrum_analyzer, freq_hopper (new)
  redis_bridge.py        <- spectrum_analyzer (fft_queue injection)

hackrf_ros
  bridge_node.py         <- spectrum Pub/Sub + metrics timer (extended)
```

No new external packages are introduced. All new functionality uses:
- `numpy.fft` (already imported via numpy)
- `asyncio` + `concurrent.futures` (stdlib)
- `json`, `threading`, `queue`, `time` (stdlib)
- `redis-py` (already present)

---

## Suggested Build Order

Dependencies flow upward. Each phase can be tested independently before the next starts.

### Phase 1: Hardening and Exception Infrastructure
**What:** Custom exceptions, input validation, watchdog, sequence numbers, Redis
reconnection in BridgeNode, dead-letter queue, TX dry-run.
**Why first:** These are correctness fixes to existing code. They establish exception
types that new components will use. No new components needed — all are modifications
to existing files.
**Test surface:** Unit tests for exception types; property-based tests for validation;
integration test for DLQ behavior.
**Files changed:** `driver.py`, `redis_bridge.py`, `bridge_node.py`,
`pymayhem/_serial.py`; new `exceptions.py` in both packages.

### Phase 2: Observability Metrics
**What:** `hackrf:metrics` HSET in RedisBridge every 1s; `/hackrf/metrics` topic in
BridgeNode every 5s.
**Why second:** Zero new components, low risk, high diagnostic value. Makes Phase 3+
observable during development. Depends on Phase 1 hardening (uses same `_bridge_loop`
and reconnect path).
**Test surface:** Mock Redis in RedisBridge tests; assert metrics fields present and
incrementing.
**Files changed:** `redis_bridge.py`, `bridge_node.py`.

### Phase 3: IQ Recording
**What:** `IQRecorder` class with daemon thread; `start_recording`/`stop_recording`
Redis command handlers; SigMF format output.
**Why third:** Self-contained new component; `_record_queue` is a simple addition to
`_rx_callback`; no interaction with spectrum or hopping. SigMF is just JSON + raw bytes.
**Test surface:** Unit test `IQRecorder` with mock queue; integration test writes file
and validates SigMF metadata.
**New file:** `iq_recorder.py`.
**Files changed:** `driver.py`, `redis_bridge.py`.

### Phase 4: Spectral Analysis
**What:** `SpectrumAnalyzer` class; `_fft_queue` injection in `RedisBridge._xadd_iq`;
`hackrf:spectrum` Stream + notify; `/hackrf/spectrum` topic in BridgeNode.
**Why fourth:** Requires `_xadd_iq` to be stable (Phase 1 hardening done). The fft_queue
tap in `_xadd_iq` is a small addition; the heavier logic is isolated in `SpectrumAnalyzer`.
BridgeNode extension mirrors existing IQ pattern exactly.
**Test surface:** Mock fft_queue in RedisBridge tests; unit test SpectrumAnalyzer with
synthetic float32 data; verify BridgeNode publishes on spectrum notify.
**New file:** `spectrum_analyzer.py`.
**Files changed:** `redis_bridge.py`, `driver.py`, `bridge_node.py`.

### Phase 5: Frequency Hopping
**What:** `FreqHopper` class; `start_hopping`/`stop_hopping` Redis command handlers;
TX exclusion guard.
**Why fifth:** Calls `_set_center_frequency()` which uses `_configure_device()` under
`_device_lock`. Must come after hardening (Phase 1) confirms lock behavior is correct.
TX interaction requires TXController to be stable (already is, but tested in Phase 1
integration tests). The TX exclusion guard reads `_tx_controller._is_transmitting`
directly — a simple attribute read, no new synchronization needed.
**Test surface:** Unit test timer chain; test TX exclusion with mock tx controller;
test freq index advancement.
**New file:** `freq_hopper.py`.
**Files changed:** `driver.py`, `redis_bridge.py`.

### Phase 6: Async pymayhem
**What:** `AsyncMayhemClient` and async domain wrappers; executor-based wrapping of
sync `MayhemClient`.
**Why last:** Completely isolated in `pymayhem`. No interaction with `hackrf_driver` or
`hackrf_ros`. Can be built and tested in a pure asyncio test environment. Order within
the milestone is flexible — could move earlier if async API is a higher priority.
**Test surface:** pytest-asyncio tests; verify each domain method is awaitable;
verify sync client behavior is preserved (existing tests still pass).
**New files:** `async_client.py`, `domains/async_*.py`.
**No files changed** in existing packages.

---

## Legacy Cleanup: hackrf_node.py

`hackrf_ros/hackrf_node.py` (28 KB, the original monolith) is still present but is no
longer the entry point (`setup.py` now points to `bridge_node:main`). It should be
removed in Phase 1 to eliminate confusion. Before removal:
1. Audit for any tests that import from it (`test/test_hackrf_node_redis.py` was
   already migrated in Phase 5).
2. Remove from `setup.py` console_scripts if still listed.
3. The legacy `redis_bridge.py` and `tx_controller.py` in `hackrf_ros/` (copies from
   before the Phase 5 refactor) can be removed at the same time.

---

## Anti-Patterns to Avoid in v2.0

### Anti-Pattern 1: FFT on the USB Callback Thread
**What goes wrong:** Computing FFT inside `_rx_callback` blocks the pyhackrf2 USB
interrupt. At 8 MSPS, 1024-point FFT takes ~0.5ms — more than the USB callback budget.
**Instead:** Enqueue float32 to `_fft_queue`, compute in the `SpectrumAnalyzer` daemon
thread. Accept that spectrum lags IQ by one queue depth.

### Anti-Pattern 2: Recording Queue Full = Drop TX Data
**What goes wrong:** If `IQRecorder` write latency spikes (slow disk), `_record_queue`
fills. The put_nowait / drop-oldest policy discards the oldest IQ. For recording, this
creates gaps in the capture file. Unlike the Redis queue (streaming), gaps in a recording
file are corrupted data.
**Instead:** Use a larger queue (e.g. maxsize=512 instead of 64) for the record queue,
since disk I/O latency is the bottleneck, not CPU. Log a warning on each dropped chunk
so the user knows the capture has gaps. Consider adding a gap marker to the SigMF
metadata.

### Anti-Pattern 3: FreqHopper Hops During TX
**What goes wrong:** `_configure_device()` calls `stop_rx()` / `start_rx()` on the
pyhackrf2 device. If called while `hackrf.start_tx()` is in progress, the device state
machine is corrupted — pyhackrf2 raises RuntimeError and the device may require a USB
reset.
**Instead:** FreqHopper timer callback checks `_tx_controller._is_transmitting` before
calling `_set_center_frequency()`. Back off 50ms and retry without advancing the hop
index.

### Anti-Pattern 4: Async pymayhem with asyncio.run() Inside Daemon Thread
**What goes wrong:** If `AsyncMayhemClient` is called from a context that already has
a running event loop (e.g. from a ROS2 async service callback), `asyncio.run()` raises
RuntimeError: "cannot run nested event loop".
**Instead:** `AsyncMayhemClient` does not manage its own event loop. It provides
coroutines that the caller awaits. The caller (ROS2 service, test, CLI) is responsible
for the event loop. Use `asyncio.get_event_loop().run_in_executor()` inside the
coroutine body, not `asyncio.run()`.

### Anti-Pattern 5: Metrics Blocking the Bridge Loop
**What goes wrong:** `_publish_metrics()` calls `HSET hackrf:metrics`. If Redis is
temporarily slow, this blocks the bridge loop, which stalls IQ XADD for the duration.
**Instead:** Metrics HSET uses the same error-handling pattern as `publish_state()`:
catch `RedisError`, log warning, continue. Never raise from `_publish_metrics()`. If
Redis is unhealthy, metric updates are silently skipped — this is acceptable because
metrics are diagnostic, not operational.

---

## Sources

- Direct codebase inspection: `hackrf_driver/`, `pymayhem/`, `hackrf_ros/` — HIGH confidence
- numpy.fft API: stdlib, no version concern — HIGH confidence
- asyncio run_in_executor pattern: [Python asyncio docs](https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.run_in_executor) — HIGH confidence
- SigMF specification: [SigMF GitHub](https://github.com/sigmf/SigMF/blob/sigmf-v1.0.0/sigmf-spec.md) — MEDIUM confidence (format is stable, exact field names need verification at implementation time)
- pyhackrf2 half-duplex constraint: inferred from existing `stop_rx`/`start_rx` pattern in `TXController` — MEDIUM confidence (no pyhackrf2 API docs, but pattern is established in existing code)

---

*Architecture research: 2026-03-30*
