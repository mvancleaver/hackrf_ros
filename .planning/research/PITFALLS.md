# Pitfalls Research

**Domain:** HackRF ROS2 Driver v2.0 — Adding hardening, observability, and signal capabilities to existing three-package system
**Researched:** 2026-03-30
**Confidence:** HIGH (based on direct codebase inspection + confirmed against Python docs, Redis docs, libhackrf tracker, asyncio dev guide)

---

## Critical Pitfalls

Mistakes that cause deadlocks, data corruption, silent regressions, or hardware damage.

---

### Pitfall 1: Custom Exceptions Break Existing Callers That Catch `Exception` or `bool`-Check Return Values

**What goes wrong:**
The existing pymayhem domain methods (`appstart`, `setfreq`, `radioinfo`) return `bool` or `dict` on success and silently swallow errors. When v2.0 replaces these returns with raised custom exceptions (`MayhemCommandError`, `MayhemTimeoutError`, etc.), any existing caller that does `if client.radio.setfreq(...)` or uses a bare `try/except Exception` will either silently pass on a raised exception it does not catch, or will be broken by a `bool` return that never arrived.

The HackRFDriver `_COMMAND_HANDLERS` dispatch table calls these methods directly via lambda (e.g., `lambda driver, p: driver._mayhem.setfreq(int(p['freq_hz']))`). If `setfreq` raises instead of returning False, the lambda propagates the exception up to `_dispatch_command`, which has a generic `except Exception` catch — this is actually fine — but the Redis state is never updated to reflect the failure. Silent success from the Redis consumer's perspective despite a hardware failure.

**Why it happens:**
Phase 5 refactored pymayhem into a clean standalone package with `bool` return contracts. v2.0 wants richer error signaling via exceptions, which is correct design. But the calling code in `hackrf_driver/redis_bridge.py`, `hackrf_driver/driver.py`, and any downstream scripts written by users against the v1 API all have implicit contracts with the `bool` return. The change looks like a clean internal refactor but it is actually a public API break.

**How to avoid:**
- Decide the exception boundary at the `_dispatch_command` level: catch pymayhem exceptions, map them to structured error responses in Redis (e.g., `hackrf:cmd:last_error`), and keep the lambda dispatch table unchanged. The domain methods raise; the bridge layer catches and translates.
- Do not change the `bool`-return surface of domain methods if external scripts already depend on it. Instead add a separate `strict=True` mode that raises, or make exceptions the default only in a new major version of pymayhem.
- Add regression tests that call domain methods via the old `bool`-check pattern and verify they still work after the exception refactor.
- Update all internal `_COMMAND_HANDLERS` lambdas to wrap calls in `try/except` and publish error state to Redis rather than propagating bare exceptions to the generic handler.

**Warning signs:**
- Redis consumers see commands dispatched with no state update and no error log entry.
- Tests that mock `_send_command` to return `['error: unknown app']` still pass even after exception conversion (because the mock path doesn't exercise the raise path).
- `hackrf:state` shows stale `active_app` after a failed `appstart`.

**Phase to address:** Custom exceptions phase (first v2.0 phase). Must be locked before any other phase modifies pymayhem domain return contracts.

---

### Pitfall 2: Watchdog Thread Acquires `_device_lock` and Deadlocks With `_configure_device`

**What goes wrong:**
`HackRFDriver._configure_device()` holds `_device_lock` (RLock) for the duration of `stop_rx()` + `time.sleep(0.1)` + `start_rx()` — a window of at least 100–300 ms. If the health watchdog thread also acquires `_device_lock` to call `hackrf.get_rssi()` or similar health probe, it will block for up to 300 ms every time a reconfiguration fires.

More dangerous: if the watchdog detects a hung device and calls `_try_connect()` while `_configure_device` holds the lock, the watchdog thread blocks indefinitely. Because `_configure_device` is called from the Redis bridge thread (via command dispatch), and the watchdog fires on a `threading.Timer`, neither thread is the main thread. The GIL cannot help here — this is a pure Python lock contention deadlock with no timeout.

The existing `_device_lock` is an RLock (reentrant), so the SAME thread can re-enter it. But the watchdog runs in a DIFFERENT thread. RLock does not protect against cross-thread blocking.

**Why it happens:**
The watchdog is a new daemon thread added to an existing threading model that was designed for two threads (RX callback + IQ publish loop). Adding a third thread that touches the device changes the lock contention profile. The natural first implementation of a watchdog — "check device health, take corrective action" — requires device access, which means acquiring the lock.

**How to avoid:**
- Give the watchdog a read-only health signal path that does NOT acquire `_device_lock`. The simplest approach: check `is_hackrf_streaming` and whether the `_redis_queue` is draining (i.e., `_redis_queue.qsize()` is nonzero) rather than probing the hardware directly.
- If the watchdog must touch the device, use `_device_lock.acquire(timeout=0.5)` and treat a timeout as a "driver is busy" signal, not a failure. Log and skip the health check rather than blocking.
- Separate the watchdog's "detect unhealthy" path (lock-free: check queues, timers, event flags) from its "take corrective action" path (which posts a reconnect request to a queue rather than directly calling `_try_connect`). The main loop drains the correction queue.
- Add an explicit watchdog design decision in the phase plan before writing any code.

**Warning signs:**
- `_configure_device` takes longer than 500 ms (watchdog blocked waiting for the lock).
- Watchdog fires but `is_hackrf_streaming` returns to True slower than expected after a simulated failure.
- Log shows watchdog "device probe" entries interleaved with reconfiguration entries.

**Phase to address:** Device health watchdog phase. Design the lock protocol before writing the watchdog thread.

---

### Pitfall 3: IQ Recording Blocks the `_redis_queue` Drain Thread and Causes Drop-Oldest Data Loss

**What goes wrong:**
The IQ pipeline is: `_rx_callback` → `_redis_queue.put_nowait()` → `RedisBridge._drain_iq_queue()` → `_xadd_iq()`. The `_drain_iq_queue` loop runs in the `redis_bridge` daemon thread. If IQ recording writes to disk inside this same thread, any file I/O stall (buffer flush, ext4 journal commit, NFS, tmpfs exhaustion) blocks the drain loop. While it is blocked, `_rx_callback` runs `_redis_queue.get_nowait()` + `put_nowait()` (drop-oldest) and discards samples. The recording file will have gaps even though no error was logged.

At 8 MSPS with float32, the Redis queue fills in approximately 64 × 8192-byte chunks = ~524 KB, which takes about 65 ms to overflow at full rate. A single `fsync()` or journal commit on a spinning disk takes 5–20 ms. On an SD card (Jetson Nano), this can be 50–200 ms. The Jetson ARM64 target in docker-compose.yaml makes this more likely, not less.

**Why it happens:**
The natural place to add recording is "after XADD, also write to file" — it is one line of code. It is invisible in testing on an SSD development machine with a warm buffer cache. It only fails on the actual Jetson hardware with a real SD card under continuous load.

**How to avoid:**
- Introduce a dedicated recorder thread with its own bounded queue (`recording_queue = queue.Queue(maxsize=128)`). The `_xadd_iq` path posts a copy of the raw bytes to `recording_queue` in addition to XADD. The recorder thread drains `recording_queue` and writes to disk — independently of the Redis bridge thread.
- Use non-blocking I/O: `O_NONBLOCK` on Linux file writes is unreliable, but writing to a `BytesIO` accumulator and flushing periodically (every N seconds) reduces the number of fsync calls.
- Pre-allocate the recording file with `fallocate()` before the first write to avoid filesystem metadata updates during the stream.
- For SigMF: write the `.sigmf-data` file first using buffered writes; write the `.sigmf-meta` JSON file only on recording stop (not incrementally during streaming).
- Test explicitly on the target hardware with `dd if=/dev/zero of=/tmp/test bs=4K count=10000` running concurrently to verify the pipeline handles I/O pressure.

**Warning signs:**
- `recording_queue.qsize()` stays at 0 (never fills) on SSD but spikes to maxsize on Jetson SD card.
- SigMF file has discontinuous timestamps (sample count resets or jumps).
- `_redis_queue.qsize()` spikes to 64 (maxsize) during recording sessions but not during non-recording sessions.
- Process RSS grows during recording (accumulator not being flushed).

**Phase to address:** IQ recording (SigMF) phase. Separate recording thread from the Redis bridge thread before writing any disk I/O.

---

### Pitfall 4: Continuous FFT on the `_redis_queue` Drain Thread Saturates the CPU and Collapses IQ Throughput

**What goes wrong:**
`np.fft.fft()` on 8192 samples at 8 MSPS runs approximately every 1 ms (8192 / 8,000,000 = ~1 ms per chunk). On Python 3.10+ with GIL, `numpy.fft.fft()` releases the GIL internally for its C-level computation. However, the per-call Python overhead (array allocation, input validation, output array creation) is still subject to the GIL. At 1000 FFT calls/second, this overhead amounts to 10–30% GIL hold time on a Cortex-A57 (Jetson Nano), starving the RX callback thread.

More concretely: the existing `_drain_iq_queue` loop runs `_xadd_iq()` in a tight loop. If FFT is added inline here, each iteration now takes 1–2 ms instead of ~0.2 ms (XADD only). The loop slows down 5–10x. The queue fills faster than it drains. Drop-oldest kicks in. IQ data is lost even though no error is raised.

Waterfall computation (sliding window FFT + magnitude + log10 + clip) is 3–5x more expensive than a single FFT. If the waterfall is computed for every chunk it is unusable.

**Why it happens:**
Spectral analysis is added as "post-processing on existing data" — the IQ data is already there in the drain loop. Adding FFT inline feels like a one-liner. It is, until the CPU budget is exhausted.

**How to avoid:**
- Spectral analysis runs in a DEDICATED thread, consuming from a separate bounded queue (`spectrum_queue = queue.Queue(maxsize=32)`). The `_xadd_iq` path posts to `spectrum_queue` only when spectral analysis is enabled and the queue is not full (non-blocking put, discard if full).
- Throttle the FFT rate independently of the IQ rate. At 8 MSPS a useful spectrum update rate is 10–50 Hz (not 1000 Hz). The spectrum thread sleeps between updates: `time.sleep(1.0 / spectrum_update_hz)`.
- Use `numpy.fft.rfft()` instead of `fft()` for real-valued signals — it is 2x faster for IQ magnitude spectra.
- Pre-allocate the FFT input/output buffers once; reuse them instead of allocating per-call.
- On Jetson: profile first. `np.fft.fft` may call OpenBLAS routines that create their own thread pools. Limit OpenBLAS threads: `os.environ['OPENBLAS_NUM_THREADS'] = '1'` before the first numpy import.

**Warning signs:**
- `spectrum_queue.qsize()` is always 0 (spectrum thread is slower than producer — dropping all spectra).
- CPU usage jumps from ~20% to ~90% when spectral analysis is enabled (drain thread saturated).
- `_redis_queue.qsize()` spikes when spectral analysis is running (RX callback cannot drain).
- XADD latency increases from <1 ms to >5 ms during spectral analysis.

**Phase to address:** Spectral analysis phase. Dedicated thread architecture must be specified in the phase plan before writing FFT code.

---

### Pitfall 5: Frequency Hopping + TX Guards = Double Lock Contention or Skipped Guard Check

**What goes wrong:**
Frequency hopping calls `_set_center_frequency()` + `_configure_device()` in rapid succession. `_configure_device()` holds `_device_lock` for 100–300 ms per hop. If hop intervals are shorter than 300 ms (e.g., 100 ms hop schedule), hops queue behind the lock and execute late — the hop scheduler drifts, and the actual frequency sequence no longer matches the planned schedule. The scheduler cannot detect this drift because `threading.Timer` fires once and does not self-correct for accumulated delay.

More dangerous: if a hop fires during an active TX, the hop scheduler calls `_set_center_frequency()`, which calls `_configure_device()`, which calls `stop_rx()`. But TX holds `_tx_lock` and is waiting for `_start_rx_fn` to complete. Depending on how the TX stop/start path is wired, this can create a classic ABBA lock deadlock:
- Hop thread: holds `_device_lock`, waiting for TX to finish
- TX thread: holds `_tx_lock`, waiting for `_device_lock` (to start RX after TX)

**Why it happens:**
The hop scheduler is a new time-driven subsystem layered on top of an existing device reconfiguration path that was designed for infrequent, user-initiated changes. The lock protocol was never designed to handle sub-second reconfiguration cycles, and it was never designed to account for a third concurrent path (TX) that also holds device locks.

**How to avoid:**
- The hop scheduler must check `TXController._is_transmitting` before scheduling any hop. If TX is active, defer the hop (log a warning, reschedule after TX completes + safety margin).
- Use a single reconfiguration queue: hop requests, Redis `setfreq` commands, and parameter updates all post to the same `config_queue`. A single worker thread drains the queue and calls `_configure_device()` serially, preventing concurrent reconfiguration attempts.
- Hop timing: use `time.perf_counter()` to measure actual hop completion time and compute drift. If the realized hop rate is more than 20% slower than requested, log a warning and automatically reduce the hop rate to what the hardware can sustain.
- `threading.Timer` precision on Linux is ~10–50 ms jitter due to OS scheduling. A 100 ms hop interval has ±50% jitter. The hop scheduler must account for this — do not assume `threading.Timer(0.1, fn)` fires at exactly 100 ms.

**Warning signs:**
- Hop log entries show timestamps further apart than the configured interval.
- `is_hackrf_streaming` flips to False during a hop and never returns to True (deadlock).
- `_tx_lock` acquire times out (if a timeout is set) during a hop cycle.
- Redis `hackrf:state.center_frequency` shows stale frequency values during active hopping.

**Phase to address:** Frequency hopping phase. TX guard interaction must be documented as a constraint in the phase plan. Lock ordering must be explicitly defined.

---

### Pitfall 6: pymayhem Async API Creates a Background Event Loop Thread That Fights the Existing `_serial_lock`

**What goes wrong:**
`MayhemSerial._send_command()` acquires `_serial_lock` (a `threading.Lock()`) and blocks for up to `_command_timeout` seconds (default 3 s). An async wrapper around this (`async def setfreq_async(self, freq_hz)`) that does `await asyncio.to_thread(self._serial._send_command, ...)` correctly offloads the blocking call to a thread. But the asyncio event loop for pymayhem must live in its own thread (since the main driver is not async). This means:

1. `asyncio.to_thread()` submits the serial call to the event loop's default `ThreadPoolExecutor`.
2. The serial call blocks in the executor thread, holding `_serial_lock`.
3. Any other coroutine that calls a domain method while the lock is held will also block in the executor — which is correct — but if two coroutines fire simultaneously, both may try to acquire `_serial_lock` in executor threads. The `ThreadPoolExecutor` default pool size is `min(32, os.cpu_count() + 4)`. On a Jetson with 4 cores, that is 8 threads. Two blocking serial calls × up to 3 s each = the pool can appear healthy while serial is serialized at the lock level.
4. The async API will have lower throughput than the synchronous API for burst command sequences, because coroutine overhead adds latency per command.

The bigger risk: if the pymayhem async library is used from code that already has an asyncio event loop running (e.g., future ROS2 nodes using rclpy's async executors), calling `asyncio.run()` to launch a new loop raises `RuntimeError: This event loop is already running`. Users of the async API must be told to use `asyncio.get_event_loop().run_until_complete()` or `asyncio.ensure_future()` from within an existing loop — but these are deprecated/dangerous patterns.

**Why it happens:**
The async API is added as a convenience wrapper around the synchronous blocking serial implementation. The underlying serial I/O is still blocking pyserial. Wrapping blocking I/O in async is safe via `asyncio.to_thread()`, but the event loop lifecycle question ("who owns the loop?") is never answered, and callers in different contexts make different assumptions.

**How to avoid:**
- Make the pymayhem async API a separate class (`AsyncMayhemClient`) that explicitly documents: "You must bring your own event loop. Use `asyncio.to_thread()` internally. Never call `asyncio.run()` internally."
- Provide a clear example showing usage from three contexts: (a) standalone script with `asyncio.run()`, (b) inside an existing loop with `await asyncio.to_thread()`, (c) from a thread with `asyncio.run_coroutine_threadsafe()`.
- Do NOT have the async API internally call `asyncio.run()` or `asyncio.get_event_loop()`. These are time bombs in library code.
- The sync `MayhemSerial._send_command` with `_serial_lock` remains unchanged. The async API is purely a thin scheduling layer; it does not refactor the serial implementation.
- Test explicitly: run `AsyncMayhemClient` from within an `asyncio.run()` context AND from within a `threading.Thread` that calls `asyncio.run_coroutine_threadsafe()` to verify both work.

**Warning signs:**
- `RuntimeError: This event loop is already running` in any test.
- `asyncio.get_event_loop()` deprecation warning in Python 3.10+ logs.
- Command throughput of async API is lower than sync API for sequential calls (overhead dominates).
- `_serial_lock` contention visible in thread profiler when two async clients are used concurrently.

**Phase to address:** pymayhem async API phase. Document the event loop ownership contract in the phase plan before writing any async code.

---

### Pitfall 7: Redis Reconnection Gap in BridgeNode Publishes Stale or Missing State to ROS2

**What goes wrong:**
`BridgeNode._bridge_loop()` subscribes to `hackrf:iq:notify` via `pubsub.get_message()`. When Redis disconnects and reconnects, the pubsub subscription is silently dropped. The bridge loop exits on the `except Exception: break` path and does NOT restart. The `_stop_event` is never set, so `destroy_node()` is not called, and ROS2 does not know the bridge is no longer publishing. The `/hackrf/iq` and `/hackrf/state` topics go silent with no error published to ROS2 diagnostics.

The current `RedisBridge.needs_reconnect` property is designed for the driver to poll. `BridgeNode` does not poll `needs_reconnect` — it has its own independent Redis connection that uses `pubsub.get_message()`. If the BridgeNode's Redis connection drops:
1. `pubsub.get_message()` raises an exception.
2. The bridge loop breaks (exits).
3. The bridge thread dies silently.
4. `/hackrf/iq` stops publishing.
5. ROS2 subscribers see no data and no error.
6. The ROS2 node shows as "alive" in `ros2 node list`.

**Why it happens:**
The BridgeNode was built with the assumption that Redis is always available (it is a local connection). Reconnection logic was deferred to "a later hardening phase" (i.e., v2.0). The `try/except` in the bridge loop breaks out instead of retrying. This is the correct graceful degradation behavior for startup, but wrong for a mid-session disconnect.

**How to avoid:**
- Replace the `break` in the bridge loop exception handler with a reconnect retry loop: close the pubsub, close the Redis connection, wait with exponential backoff (1–30 s), attempt `redis.Redis.ping()`, resubscribe to `hackrf:iq:notify`, reset `last_cmd_id = b'$'` to avoid replaying old commands.
- Publish a ROS2 diagnostic message (`/hackrf/diagnostics` or similar) that reports `redis_connected: False` when the reconnect loop is active.
- After reconnect, call `_publish_state()` immediately to push the last known state to ROS2 subscribers (avoiding stale state from before the disconnect).
- The state after reconnect is: driver may have changed frequency, started/stopped RX, or changed gain while Redis was disconnected. BridgeNode must fetch current state with `HGETALL hackrf:state` after reconnect and publish it before resuming IQ forwarding.
- Add a liveness timer: if no IQ message has been published in >5 s during an active session, log a warning. This is the canary for silent bridge thread death.

**Warning signs:**
- ROS2 topic `/hackrf/iq` has 0 Hz publish rate after a Redis restart.
- `ros2 node list` still shows `hackrf_bridge_node` as alive.
- `redis-cli PUBSUB CHANNELS` shows no `hackrf:iq:notify` subscribers.
- Bridge thread is not in `threading.enumerate()` output.

**Phase to address:** Redis reconnection hardening phase (first v2.0 phase alongside custom exceptions).

---

## Moderate Pitfalls

---

### Pitfall 8: Observability Metrics Published at IQ Rate Saturate Redis With Tiny Messages

**What goes wrong:**
If observability metrics (queue depths, drop counts, RX latency) are published to Redis on every `_xadd_iq()` call — which fires at approximately 1000 times/second — each metric write is a separate `HSET hackrf:metrics ...`. At 1000 HSET/s, each taking ~0.3 ms, the Redis bridge thread spends 300 ms/s on metric writes alone — 30% of its CPU budget. This is in addition to the XADD calls.

At this rate, Redis also receives 1000 small writes/s with no pipelining, causing significant round-trip overhead on localhost (typically 0.1–0.5 ms per command).

**How to avoid:**
- Publish metrics at a fixed low rate (1–5 Hz) using a separate timer, not on every XADD. Use `time.monotonic()` to throttle: `if now - last_metrics_publish > 0.2: publish_metrics()`.
- Batch metric updates using a Redis pipeline: `pipe = redis.pipeline(transaction=False); pipe.hset(...); pipe.execute()`. One round-trip for all metrics.
- Maintain in-memory counters (drop count, XADD count, queue high watermark) as Python integers; flush them to Redis atomically on the timer, not on each increment.
- Use `EXPIRE` on metric keys with a TTL of 10–30 s to avoid stale metrics persisting after the driver stops.

**Warning signs:**
- Redis CPU usage visible in `redis-cli INFO stats` under `total_commands_processed` growing at >1000/s.
- Bridge thread CPU usage increases from ~5% to >30% when metrics are enabled.
- IQ stream latency (time from `_rx_callback` to XADD) increases when metrics are enabled.

**Phase to address:** Observability metrics phase.

---

### Pitfall 9: Dead-Letter Queue Grows Unboundedly Because No Discard or Archive Policy Is Defined

**What goes wrong:**
A dead-letter queue for failed commands (commands that raised exceptions in `_dispatch_command`) is written to Redis as a stream: `hackrf:cmd:dead`. Without a `MAXLEN` on the XADD, it grows without bound. Failed commands may accumulate at high rates if, for example, a buggy Redis consumer sends malformed commands in a loop (e.g., a script with wrong JSON escaping).

More subtle: if the dead-letter queue is meant to be "reviewed by an operator," it will never be reviewed in practice. It silently fills Redis memory over days/weeks. The driver does not alert when the DLQ grows. The driver does not have a cleanup job.

**How to avoid:**
- Always use `XADD hackrf:cmd:dead MAXLEN ~ 500 * ...` — 500 entries is enough for forensic review; anything older is not useful.
- Define a discard policy in the phase plan: DLQ is forensic only. It is NOT retried. Messages are discarded after 500 entries. An operator alert fires when DLQ size > 100 (early warning before overflow).
- Log every DLQ write to the driver logger at WARNING level with the full command body. The logger is the primary notification path; the Redis DLQ is secondary.
- Add a `EXPIRE hackrf:cmd:dead <TTL>` on each write to enforce maximum retention time (e.g., 24 hours) regardless of MAXLEN.

**Warning signs:**
- `XLEN hackrf:cmd:dead` growing beyond 500 in normal operation (indicates something is wrong with a command producer).
- Redis `used_memory` growing slowly over hours even when IQ stream is stable.
- DLQ entries with identical `error` fields (same bug, repeated indefinitely).

**Phase to address:** Dead-letter queue phase.

---

### Pitfall 10: TX Dry-Run Validation Is Bypassed Because It Shares the Same Guard Code Path as Live TX

**What goes wrong:**
A TX dry-run mode that validates frequency, IQ data shape, and gain settings without emitting RF is useful for automated testing. The natural implementation is: "call `start_tx()` with a `dry_run=True` flag, skip the `hackrf.start_tx()` hardware call but execute all guards." This seems safe.

But guards 1–4 in `TXController.start_tx()` consume the auth token (Guard 4: GETDEL). A dry-run that consumes the auth token makes the actual TX fail with `TXNotAuthorizedError`. Developers who test with dry-run first will be confused when the live TX fails immediately after.

If the dry-run path skips the auth token consumption, it is no longer testing the complete guard sequence. It is testing 3 of 4 guards, which gives false confidence.

**How to avoid:**
- Dry-run mode must explicitly NOT consume the auth token. This must be documented as a design decision, not left implicit.
- Provide a separate `validate_tx(freq_hz, iq_bytes, txvga_gain)` method that checks all guard conditions except the token (which is a one-time resource). The caller checks `validate_tx()` first (idempotent, repeatable), then calls `start_tx()` with the token.
- Document clearly: `validate_tx()` cannot detect an expired or absent token; it only checks frequency bands, antenna confirmation, and IQ data shape.
- Add a test that calls `validate_tx()` then `start_tx()` and verifies the token is consumed exactly once.

**Warning signs:**
- Test suite calls `start_tx()` in dry-run mode and the auth token is missing after the test run.
- Integration test for live TX fails with `TXNotAuthorizedError` even though a token was set (it was consumed by a prior dry-run).

**Phase to address:** TX dry-run / hardening phase.

---

### Pitfall 11: Input Validation in `hackrf_driver` Rejects Values That the Existing ROS2 Parameter Range Already Accepts

**What goes wrong:**
`PARAM_RANGES` in `hackrf_driver/config.py` defines hardware-level bounds for `center_frequency`, `sample_rate`, `lna_gain`, `vga_gain`. The BridgeNode forwards ROS2 parameter changes to the driver via Redis commands. If v2.0 tightens the validation in `hackrf_driver` (e.g., quantizing LNA gain to 8 dB steps), a ROS2 parameter set for `lna_gain=18` will be silently rejected by the driver (18 is not a multiple of 8) while the ROS2 parameter store reports success.

The discrepancy: ROS2 parameter callback returns `SetParametersResult(successful=True)` (legacy `hackrf_ros/hackrf_node.py` validation only checks the old range, not the new quantization). The driver silently ignores the value. `hackrf:state` shows `lna_gain: 16` while the ROS2 parameter store shows `lna_gain: 18`. Users see a state divergence with no error.

**Why it happens:**
Validation is added at the driver level (correct architecture) but the ROS2 bridge is not updated to reflect the new constraints. The two validation layers go out of sync.

**How to avoid:**
- Validation must live in ONE place: `hackrf_driver/config.py` PARAM_RANGES. The BridgeNode forwards values to the driver without pre-validation. The driver rejects and logs. The Redis state reflects the driver's actual accepted value.
- After any `set_param` command, the driver publishes the accepted value back to `hackrf:state`. The BridgeNode reads the state and publishes it to `/hackrf/state`. The ROS2 node can read the actual applied value from the state topic.
- Remove duplicate validation from `hackrf_ros/hackrf_node.py` (or update it to match exactly) to avoid divergence.
- For quantization (LNA gain steps): log the quantized value at INFO level: "lna_gain 18 quantized to 16 (nearest valid step)".

**Warning signs:**
- `hackrf:state.lna_gain` differs from the ROS2 parameter `lna_gain` after a parameter set.
- No error is logged but the device is not at the requested gain.

**Phase to address:** Input validation phase (first v2.0 phase).

---

### Pitfall 12: IQ Sequence Numbers Overflow Python `int` and Are Serialized as Strings Into Redis — Consumer Comparison Breaks

**What goes wrong:**
If IQ sequence numbers are added as a monotonic counter (e.g., `_iq_seq_num` incremented per `_xadd_iq` call), at 8 MSPS with 2048-sample chunks there are approximately 3900 chunks/second. A `uint64` wraps at 2^64 ≈ 1.8 × 10^19. Even `int32` wraps in about 6 hours at this rate (~2^31 / 3900 ≈ 600,000 seconds — actually fine for int32 at this chunk rate). But if the counter is serialized to Redis as a string via `str(v)` (as `publish_state` does with all values: `{k: str(v) for k, v in state.items()}`), Redis consumers that receive the sequence number as a string must do `int(seq_num_str)` before comparison.

If consumers naively compare sequence number strings lexicographically (`"9" > "10"` is True in string comparison), they will incorrectly detect gaps where none exist or miss actual gaps.

This is not a Python overflow issue (Python ints are arbitrary precision) — it is a serialization contract issue that breaks consumers who do string comparisons.

**How to avoid:**
- Document the sequence number type in `hackrf:iq:format` Redis key: `seq_num_type: uint64_as_string`.
- Sequence numbers included in IQ stream entries as a field (`XADD hackrf:iq:stream * data <bytes> seq <n>`), not only in `hackrf:state`. This allows stream consumers to use Redis entry IDs for ordering (which are already monotonic) rather than a separate counter.
- If consumers are external Python scripts, provide a reference consumer example that does `int(fields[b'seq'])` before comparison.
- Test with sequences that cross the 10^6, 10^7 and 10^9 boundaries to verify string-vs-integer comparison is not used anywhere.

**Warning signs:**
- Consumer logs show "gap detected" at sequence 9999 → 10000 (string comparison: "9999" > "10000" is True lexicographically).
- Sequence number in `hackrf:state` is read back as a string by the consumer and compared directly.

**Phase to address:** IQ sequence number phase.

---

## Technical Debt Patterns

Shortcuts that seem reasonable but create long-term problems.

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Add FFT inline in `_drain_iq_queue` loop | One-file change, simple | Saturates bridge thread, causes IQ drops | Never — dedicated thread is required |
| Write disk I/O in `_xadd_iq` path | No new thread needed | Blocks Redis bridge on fsync, silent data loss on Jetson SD | Never for recording; OK for small metadata writes |
| Publish metrics on every XADD | Simplest metering | 1000 HSET/s, 30% CPU waste on metrics alone | Never — throttle to ≤5 Hz |
| Use `threading.Timer` for sub-100ms hop intervals | No scheduler library needed | ±50ms OS jitter makes precise hopping impossible | Only for hop intervals >500 ms |
| Share the same Redis connection for IQ stream + DLQ + metrics | Fewer objects | Single connection failure kills all three paths | Never — use separate connections for IQ stream vs. control plane |
| `asyncio.run()` inside pymayhem async methods | Simplest async wrapper | `RuntimeError` when called from existing event loop | Never in library code |
| Skip `validate_tx()` and go straight to `start_tx()` in tests | Faster test setup | Consumes auth token, subsequent tests fail | Only if test explicitly provisions a fresh token per test |

---

## Integration Gotchas

Common mistakes when connecting to external services or crossing subsystem boundaries.

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| pymayhem domain methods → custom exceptions | Assume `except Exception` in dispatch table catches everything correctly | Catch specific pymayhem exceptions, map to Redis error state, continue dispatch |
| BridgeNode pubsub → Redis reconnect | `except Exception: break` exits the loop permanently | Retry loop with exponential backoff; resubscribe after reconnect; flush state |
| Hop scheduler → TXController | Call `_set_center_frequency()` without checking `_is_transmitting` | Gate all hops on `not _tx_controller._is_transmitting` before scheduling |
| SigMF recorder → Redis bridge thread | Write to file in `_xadd_iq()` | Dedicated recorder thread with its own bounded queue |
| Spectrum analysis → IQ pipeline | Compute FFT in `_drain_iq_queue` | Separate spectrum thread consuming from `spectrum_queue` |
| Observability → Redis | HSET on every XADD | In-memory counters, flush at ≤5 Hz via pipeline |
| Async pymayhem → existing event loop | `asyncio.run()` inside library | `asyncio.to_thread()` wrapper, caller provides event loop |
| Input validation → dual codebases | Validate in both BridgeNode and hackrf_driver | Single source of truth in hackrf_driver; BridgeNode passes through without pre-validation |

---

## Performance Traps

Patterns that work in development but fail on the Jetson ARM64 target.

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| FFT inline in IQ bridge thread | CPU 90%, IQ drops spike | Dedicated spectrum thread, throttle to ≤50 Hz | Immediately at 8 MSPS on Cortex-A57 |
| File I/O in IQ bridge thread | Silent data gaps in SigMF recording | Dedicated recorder thread | On SD card, first fsync >50 ms |
| Metric HSET on every XADD | Redis CPU 30%, bridge latency up | Throttle to 5 Hz, use pipeline | At 3900 chunks/s (constant) |
| `threading.Timer` for <100 ms hops | Hops arrive late, schedule drifts | Warn and reduce hop rate; use `perf_counter` for drift measurement | Immediately on any loaded Linux system |
| Waterfall computation per-chunk | 3–5x FFT cost, CPU fully saturated | Downsample to ≤50 Hz waterfall rate | At 8 MSPS on Jetson (constant) |
| `np.tolist()` for Redis IQ publish | GC pressure from millions of Python float objects | Write raw bytes directly | Above 1 MSPS (confirmed from prior pitfalls research) |
| Shared Redis connection for IQ + control | Single connection failure silences all paths | Separate connections for IQ stream vs. control/state | On any Redis restart |

---

## Security Mistakes

Domain-specific security issues for v2.0 features.

| Mistake | Risk | Prevention |
|---------|------|------------|
| DLQ readable by any Redis consumer | Sensitive command parameters (auth tokens, TX frequencies) exposed in DLQ | Strip auth tokens from DLQ entries before XADD |
| Dry-run mode exposes frequency filter bypass | Attacker sends dry-run to probe which frequencies are blocked | Dry-run uses same frequency filter as live TX — no bypass |
| Observability metrics reveal TX patterns | TX timing and frequency in metrics stream visible to any Redis reader | Document that Redis is a trusted local interface; do not publish raw TX frequencies in public-accessible metrics |
| Async API accepts arbitrary serial commands | `AsyncMayhemClient._send_command()` can inject arbitrary ChibiOS shell commands | `UnsafeMayhemClient` already isolates raw commands; async API exposes only domain methods, same as sync |

---

## "Looks Done But Isn't" Checklist

Things that appear complete but are missing critical pieces.

- [ ] **Custom exceptions:** Exception types defined and raised in domain methods — but verify `_dispatch_command` catches them and publishes error to Redis (not just logs).
- [ ] **Watchdog:** Thread starts and logs "device healthy" — but verify it uses `_device_lock.acquire(timeout=0.5)`, not a blocking acquire.
- [ ] **SigMF recording:** Files are written to disk — but verify on Jetson SD card with `_redis_queue` saturation test running concurrently.
- [ ] **Spectral analysis:** FFT values appear in Redis — but verify `spectrum_queue` is a separate queue and that disabling spectral analysis does not affect IQ throughput.
- [ ] **Frequency hopping:** Hops fire at the correct frequency — but verify `_is_transmitting` guard prevents hops during active TX.
- [ ] **Async pymayhem:** `AsyncMayhemClient` works in a standalone `asyncio.run()` script — but verify it also works from within an existing event loop without `RuntimeError`.
- [ ] **DLQ:** Dead-letter entries appear in `hackrf:cmd:dead` — but verify `XLEN hackrf:cmd:dead` is bounded by MAXLEN and that auth tokens are stripped.
- [ ] **Observability:** Metrics appear in Redis — but verify metric publish rate is ≤5 Hz and bridge thread CPU is not affected.
- [ ] **Redis reconnection:** BridgeNode reconnects after `redis-cli SHUTDOWN` — but verify `/hackrf/state` is published with current state immediately after reconnect.
- [ ] **Input validation:** Invalid gain is rejected — but verify `hackrf:state` shows the accepted (possibly quantized) value, not the rejected requested value.

---

## Recovery Strategies

When pitfalls occur despite prevention, how to recover.

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Custom exception breaks existing caller | LOW | Add `except MayhemCommandError as e: return False` at dispatch boundary; deploy; restore bool contract |
| Watchdog deadlock | HIGH | Process restart required; add `acquire(timeout=0.5)` in watchdog; redeploy |
| SigMF data gaps on SD card | MEDIUM | Re-record; add recorder thread; pre-allocate file with `fallocate`; re-run |
| FFT saturates bridge thread, IQ drops | MEDIUM | Disable spectral analysis via config flag; move FFT to dedicated thread; re-enable |
| Hop+TX deadlock | HIGH | Process restart; add `_is_transmitting` guard before any hop; add lock ordering doc |
| Async API `RuntimeError` in existing event loop | LOW | Replace `asyncio.run()` with `asyncio.to_thread()` in library; no caller changes needed |
| BridgeNode silent after Redis restart | MEDIUM | Restart BridgeNode; then add reconnect retry loop in bridge thread |
| DLQ unbounded growth | LOW | `redis-cli XTRIM hackrf:cmd:dead MAXLEN 500`; add MAXLEN to XADD call |
| Metric HSET saturates Redis | LOW | Disable metric publish temporarily; add throttle; re-enable |

---

## Pitfall-to-Phase Mapping

How roadmap phases should address these pitfalls.

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Custom exceptions break callers (Pitfall 1) | Phase: Custom exceptions + input validation | Run existing tests against new exceptions; verify `_dispatch_command` maps to Redis error |
| Watchdog deadlock with `_device_lock` (Pitfall 2) | Phase: Device health watchdog | Stress test: rapid reconfiguration + watchdog running simultaneously; verify <500 ms lock wait |
| IQ recording blocks bridge thread (Pitfall 3) | Phase: IQ recording (SigMF) | Test on Jetson SD card with concurrent disk pressure; verify queue sizes stay bounded |
| FFT saturates bridge thread (Pitfall 4) | Phase: Spectral analysis | CPU usage ≤30% with spectral analysis enabled at 8 MSPS; IQ drop count unchanged |
| Hop + TX deadlock (Pitfall 5) | Phase: Frequency hopping | TX active + hop scheduled simultaneously; verify hop deferred, not deadlocked |
| Async API event loop conflict (Pitfall 6) | Phase: pymayhem async API | Test from existing event loop context; verify no `RuntimeError` |
| BridgeNode silent after Redis disconnect (Pitfall 7) | Phase: Redis reconnection | Kill Redis, wait 30 s, restart Redis; verify BridgeNode resumes publishing without restart |
| Metrics overhead (Pitfall 8) | Phase: Observability metrics | Enable metrics; measure bridge thread CPU; confirm ≤5 Hz publish rate |
| DLQ unbounded growth (Pitfall 9) | Phase: Dead-letter queue | Send 1000 bad commands; verify `XLEN hackrf:cmd:dead` ≤ 500 |
| Dry-run consumes auth token (Pitfall 10) | Phase: TX dry-run validation | Call `validate_tx()` then `start_tx()`; verify token consumed exactly once |
| Dual-layer validation divergence (Pitfall 11) | Phase: Input validation | Set `lna_gain=18` via ROS2; verify `hackrf:state.lna_gain = 16` and no silent accept |
| Sequence number string comparison (Pitfall 12) | Phase: IQ sequence numbers | Consumer script reads sequence numbers as strings; verify gap detection uses `int()` |

---

## Sources

- Direct codebase inspection: `hackrf_driver/driver.py`, `hackrf_driver/redis_bridge.py`, `hackrf_driver/tx_controller.py`, `pymayhem/_serial.py`, `hackrf_ros/bridge_node.py` (all inspected 2026-03-30)
- [Python asyncio development guide — thread safety](https://docs.python.org/3/library/asyncio-dev.html) — HIGH confidence (official Python docs)
- [Python asyncio: running blocking I/O in executor](https://docs.python.org/3/library/asyncio-eventloop.html) — HIGH confidence (official Python docs)
- [Python threading.Timer precision and OS scheduling jitter](https://bugs.python.org/issue41299) — MEDIUM confidence (Python issue tracker, documented jitter >10 ms)
- [Redis fast data ingest pipeline with Streams](https://redis.io/tutorials/fast-data-ingest-pipeline-with-redis/) — HIGH confidence (official Redis docs)
- [Redis dead letter queue with Streams — MAXLEN and retention](https://oneuptime.com/blog/post/2026-01-21-redis-dead-letter-queue/view) — MEDIUM confidence (community blog, consistent with official Redis docs)
- [Redis latency causes and prevention](https://redis.io/docs/latest/operate/oss_and_stack/management/optimization/latency/) — HIGH confidence (official Redis docs)
- [numpy thread safety documentation](https://numpy.org/doc/stable/reference/thread_safety.html) — HIGH confidence (official numpy docs)
- [SigMF specification and file format](https://github.com/sigmf/SigMF) — HIGH confidence (official SigMF repo)
- [pyhackrf2 GitHub — thin documentation, behavior inferred from libhackrf](https://github.com/eizemazal/pyhackrf2) — MEDIUM confidence
- [PySDR IQ Files and SigMF](https://pysdr.org/content/iq_files.html) — MEDIUM confidence (community reference, consistent with SigMF spec)
- libhackrf issue #916: rapid stop/start RX state corruption — MEDIUM confidence (confirmed in prior pitfalls research, still applies to v2.0 watchdog design)
- `.planning/research/PITFALLS.md` (v1.0 pitfalls from prior milestone) — HIGH confidence (previously researched and verified)

---
*Pitfalls research for: HackRF ROS2 Driver v2.0 — hardening, observability, and signal capabilities added to existing three-package system*
*Researched: 2026-03-30*
