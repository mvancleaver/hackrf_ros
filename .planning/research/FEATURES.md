# Feature Research

**Domain:** HackRF ROS2 Driver v2.0 — Hardening, Observability & Signal Capabilities
**Researched:** 2026-03-30
**Confidence:** MEDIUM-HIGH

---

## Context

This research covers **new features only** for the v2.0 milestone. v1.0 features (RX pipeline,
Mayhem serial control, Redis bridge, TX authorization, standalone packages, 135 tests) are
already built and not re-evaluated here.

Target additions:
- Custom exceptions and input validation
- Device health watchdog and IQ sequence numbers
- Redis reconnection in BridgeNode, antenna confirmation ROS2 service
- Observability metrics published to Redis
- Dead-letter queue, TX dry-run validation
- Legacy HackRFNode cleanup and documentation
- IQ recording to SigMF/raw files
- Headless spectral analysis (FFT + waterfall) to Redis and /hackrf/spectrum ROS2 topic
- Programmable frequency hopping scheduler
- pymayhem async API (asyncio)

---

## Feature Landscape

### Table Stakes (Users Expect These)

Features the driver must have to be considered production-ready or that the v2.0 milestone
explicitly targets. Missing any of these makes the system incomplete for its stated purpose.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Custom exception hierarchy | Callers cannot distinguish USB errors from validation errors from serial errors without typed exceptions; bare `Exception` forces string parsing | LOW | `HackRFError → HackRFConnectionError, HackRFValidationError, HackRFTXError`; `MayhemError → MayhemSerialError, MayhemCommandError`; mirrors established SDR library patterns |
| Input validation with clear error messages | `setfreq(0)` and `setfreq(7e9)` silently fail or corrupt device state today; validation must reject out-of-range values before they reach hardware | LOW | Validate in every public method entry point; raise `HackRFValidationError(field, value, allowed_range)`; HackRF One: 1 MHz–6 GHz, TXVGA 0–47 dB, RXVGA 0–62 dB, LNA 0–40 dB (8 dB steps) |
| Device health watchdog | USB devices disconnect without notice; driver must detect stalled IQ streams and trigger reconnect automatically | MEDIUM | Background thread checks `last_rx_timestamp`; if delta exceeds threshold (e.g. 5 s), trigger reconnect sequence; emit `hackrf:metrics` event on reconnect |
| IQ sequence numbers | Consumers need to detect dropped buffers; a monotonic sequence counter on every IQ chunk makes gaps visible | LOW | Add `seq` field to `hackrf:iq:stream` XADD entries; increment in RX callback; consumer can detect `seq - prev_seq > 1` as a gap |
| Redis reconnection in BridgeNode | `BridgeNode` today has no reconnect logic; a Redis restart kills the bridge permanently until the container is restarted | LOW | Catch `redis.exceptions.ConnectionError`; back off and retry `redis.Redis.ping()`; resume XREAD after reconnect; emit log warning per attempt |
| Observability metrics to Redis | Operators cannot monitor driver health without introspecting process state; Redis metrics hash gives external dashboards a query point | MEDIUM | Publish `hackrf:metrics` hash: `rx_packets_total`, `rx_bytes_total`, `rx_drops`, `tx_packets_total`, `rx_reconnects`, `uptime_s`, `last_error`; update on each publish tick and error event |
| Dead-letter queue for failed commands | Commands that fail (invalid params, device not ready) are silently dropped today; DLQ preserves them for inspection and retry | MEDIUM | On command dispatch failure, `XADD hackrf:cmd:dlq * cmd <original_json> error <msg> attempts <n>`; cap with `MAXLEN ~ 500`; document consumer-side retry contract |
| TX dry-run validation | TX commands must be validated (freq in allowlist, waveform data present, antenna confirmed) before any RF is emitted; dry-run confirms gate would pass without transmitting | LOW | Add `dry_run: true` flag to TX command JSON; driver runs all authorization checks and returns pass/fail with reason; does not call `start_tx()` |
| Antenna confirmation ROS2 service | Existing TX guard requires antenna confirmation per session; it must be exposed as a ROS2 service so orchestration nodes can confirm programmatically | LOW | `hackrf_ros/services/antenna_confirm.py`; service type `std_srvs/Trigger`; BridgeNode calls through to `hackrf:cmd` stream; returns confirmation token |
| Legacy HackRFNode documentation | `hackrf_ros/hackrf_node.py` is still the entry point for Docker users; it needs clear docstrings, deprecation notice pointing to `hackrf_driver`, and inline explanation of the bridge pattern | TRIVIAL | No functional change; doc-only work; prevents confusion for new users |

---

### Differentiators (Competitive Advantage)

Features that go beyond typical SDR driver libraries and provide unique operational value
for this deployment context.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| IQ recording to SigMF | SigMF is the de-facto open standard for SDR recordings; recordings made with metadata (frequency, sample rate, timestamp, hardware) are replayable and interoperable with GNU Radio, inspectrum, IQEngine | MEDIUM | Use `sigmf-python` (`pip install SigMF`); required fields: `core:datatype` (cf32_le for complex float32), `core:version`, `core:sample_rate`, `core:frequency` (in captures), `core:datetime` (ISO-8601); trigger via Redis command `{"cmd": "record_start", "duration_s": 10, "filename": "capture.sigmf"}`; write to filesystem path configured at startup |
| Raw IQ recording fallback | SigMF adds a dependency; raw binary complex64 recording (`.iq` / `.c32`) is a lower-complexity option with no new dependencies, and is readable by most SDR tools | LOW | `numpy.ndarray.tofile()`; write interleaved float32 [I, Q, I, Q, ...]; emit sidecar `.json` with frequency/rate/timestamp; alternative when SigMF is disabled |
| Headless spectral analysis (FFT) | Real-time spectrum data published to Redis enables external dashboards and automated detection without a GUI process; headless = no matplotlib, no display required | MEDIUM | `numpy.fft.fft()` on each IQ buffer; compute power spectral density (PSD) in dBFS; serialize as float32 bytes or JSON array; publish to `hackrf:spectrum` Redis stream; also publish on `/hackrf/spectrum` ROS2 topic (Float32MultiArray with freq_bin layout) |
| Waterfall history in Redis | Redis list `hackrf:waterfall` as a rolling window of PSD rows; consumers get temporal spectrum history without subscribing to every event | LOW | `LPUSH hackrf:waterfall <psd_bytes>`; `LTRIM hackrf:waterfall 0 <max_rows>`; configurable depth (e.g. 200 rows = ~10 s at 20 fps); each entry is msgpack or raw float32 bytes |
| Frequency hopping scheduler | Timed frequency changes are useful for scanning, monitoring, and survey workflows; a built-in scheduler removes the need for consumers to manage timing | MEDIUM | `FrequencyHopper` class in `hackrf_driver`; accepts list of `(freq_hz, dwell_ms)` tuples; uses `threading.Timer` or `asyncio.sleep` loop; calls `_set_center_frequency()` on each hop; emits state update to `hackrf:state` on each hop; honors TX guard (no hopping during active TX) |
| Frequency hopping via Redis command | Consumers trigger hopping schedules without coding against the driver API; Redis command interface is the natural control plane | LOW | `{"cmd": "hop_start", "schedule": [{"freq": 433920000, "dwell_ms": 500}, ...], "loops": -1}` and `{"cmd": "hop_stop"}`; schedule stored in driver state; -1 loops = infinite |
| pymayhem async API (asyncio) | Async callers (e.g. FastAPI web backends, asyncio-native control systems) cannot use the synchronous pymayhem API without a `run_in_executor` wrapper; native asyncio avoids thread overhead and enables `await client.setfreq(...)` syntax | HIGH | New `pymayhem.async_client.AsyncMayhemClient` using `pyserial-asyncio` (StreamReader/StreamWriter pattern); mirrors synchronous `MayhemClient` method surface; all domain methods become coroutines; retain sync `MayhemClient` for ROS2/threading callers; pyserial-asyncio 0.6 is the standard library for this pattern (Linux posix I/O, no polling) |

---

### Anti-Features (Commonly Requested, Often Problematic)

Features to deliberately NOT build in v2.0.

| Anti-Feature | Why Requested | Why Problematic | Alternative |
|--------------|---------------|-----------------|-------------|
| Persistent TX authorization session (time-based) | "Enable TX for 30 seconds" feels convenient | Creates a window where accidental TX is possible if session isn't revoked on crash; one-token-per-TX already built and sufficient | Keep per-command auth token; TX dry-run gives callers a way to pre-validate without risk |
| Automatic demodulation in spectral analysis | "Give me FM audio not IQ" | Demodulation belongs in consumer layer; adding it conflates the driver with a signal processor; increases complexity massively | Publish IQ + PSD to Redis; consumers run demodulators |
| SigMF annotations auto-detection | "Detect signals in the recording automatically" | Signal detection is ML/DSP research territory; false positives in auto-annotations would corrupt recordings | Record metadata only; consumers annotate with their detectors |
| Frequency hopping during active TX | "Hop to the next channel and keep transmitting" | HackRF One is half-duplex; frequency changes during TX require stop/retune/restart; race condition risk | Hop scheduler must check `is_transmitting` and refuse or queue hops |
| Persistent recording to disk (always-on) | "Record everything to disk by default" | 20 MHz × 8 bytes/sample = 160 MB/s; fills disk in minutes; Mayhem Capture app handles on-device recording | Trigger-based recording only; consumers set duration and filename explicitly |
| Web UI for spectrum/waterfall display | "Show me the waterfall in a browser" | PROJECT.md explicitly excludes this; adds frontend dependency | Publish to Redis; consumers (e.g. IQEngine, Grafana) pull from Redis |
| Multi-frequency simultaneous capture | "Sample two bands at once" | HackRF One has a single tunable front end; simultaneous multi-band requires multiple devices | Frequency hopping scanner is the correct single-device approximation |
| asyncio port of hackrf_driver core | "Make the whole driver async" | pyhackrf2 callbacks come from a libusb thread; bridging to asyncio event loop correctly requires careful thread-to-loop scheduling; high risk of deadlocks | Keep driver core threaded; expose async API only in pymayhem where it's natural |

---

## Feature Dependencies

```
Custom exceptions
    required-by --> Input validation (raises typed exceptions)
    required-by --> Device watchdog (raises HackRFConnectionError on stall)
    required-by --> TX dry-run (raises HackRFTXError with reason)

Input validation
    required-by --> Frequency hopping scheduler (validates each hop target)
    required-by --> IQ recording (validates sample rate, duration params)

Device watchdog
    required-by --> Observability metrics (watchdog increments rx_reconnects counter)

IQ sequence numbers
    required-by --> Observability metrics (rx_drops = gaps detected in sequence)

Redis reconnection (BridgeNode)
    required-by --> Observability metrics (bridge must be alive to publish metrics)

Observability metrics
    enhances --> Device watchdog (metrics surface health events externally)

IQ recording (SigMF)
    required-by --> Raw IQ fallback (SigMF wraps raw file; fallback skips sigmf-python)
    depends-on --> IQ sequence numbers (recording should note gap events in metadata)

Headless FFT/PSD
    depends-on --> IQ sequence numbers (know if input had gaps before computing PSD)
    produces --> Waterfall history (PSD rows accumulated into Redis list)

Frequency hopping scheduler
    depends-on --> Input validation (validates each target frequency)
    conflicts-with --> Active TX (cannot hop while transmitting)
    enhances --> Spectral analysis (hop + scan enables wideband PSD survey)

TX dry-run
    depends-on --> TX authorization gate (already built; dry-run exercises same gate)
    required-by --> Antenna confirmation service (service uses dry-run to pre-validate)

Antenna confirmation ROS2 service
    depends-on --> BridgeNode (service is exposed through the bridge)
    depends-on --> Redis reconnection (bridge must be up for service to work)

pymayhem async API
    depends-on --> pyserial-asyncio (new dependency; pip install pyserial-asyncio)
    depends-on --> pymayhem sync client (async client mirrors sync method surface)
    conflicts-with --> asyncio port of hackrf_driver core (don't port the driver; only pymayhem)

Dead-letter queue
    depends-on --> Redis reconnection (DLQ writes to Redis; bridge must be connected)
    enhances --> Observability metrics (DLQ depth is a metric)
```

### Dependency Notes

- **Custom exceptions before everything else:** All new features raise typed errors; the hierarchy must exist first or every feature uses bare `Exception`.
- **IQ sequence numbers before recording and FFT:** Recordings should note gap events; FFT should skip or flag gapped buffers.
- **Redis reconnection before observability metrics:** Metrics are published to Redis; if BridgeNode can't reconnect, metrics are lost.
- **Frequency hopping conflicts with active TX:** `FrequencyHopper` must check `is_transmitting` flag (already in `TXController`) before issuing a retune.
- **pymayhem async is additive:** Sync `MayhemClient` stays unchanged; `AsyncMayhemClient` is a new class in a new module; no breaking changes.

---

## MVP Definition

### Launch With (v2.0 core — hardening and observability)

Minimum viable for a "production-ready" milestone. These resolve reliability gaps and make
the system observable.

- [ ] Custom exception hierarchy — every error is typed and catchable
- [ ] Input validation across pymayhem and hackrf_driver — no silent bad-param bugs
- [ ] Device watchdog — stalled IQ stream triggers reconnect automatically
- [ ] IQ sequence numbers — consumers can detect gaps
- [ ] Redis reconnection in BridgeNode — Redis restart no longer kills the bridge
- [ ] Observability metrics hash (`hackrf:metrics`) — operators have a health query point
- [ ] Dead-letter queue — failed commands are preserved, not silently dropped
- [ ] TX dry-run — safe pre-validation before any RF emission
- [ ] Antenna confirmation ROS2 service — BridgeNode exposes TX gate to ROS2 callers

### Add After Core (v2.0 signal capabilities)

Signal features that depend on the hardened core being stable first.

- [ ] IQ recording to SigMF — trigger-based; uses `sigmf-python`
- [ ] Raw IQ fallback recording — simpler option without sigmf dependency
- [ ] Headless FFT/PSD to Redis (`hackrf:spectrum` stream)
- [ ] `/hackrf/spectrum` ROS2 topic (Float32MultiArray)
- [ ] Waterfall history in Redis list (`hackrf:waterfall`)
- [ ] Frequency hopping scheduler with Redis command interface
- [ ] Legacy HackRFNode documentation and deprecation notice

### Future Consideration (v3.0+)

Deferred because they require significant new dependencies or architectural changes.

- [ ] pymayhem async API (asyncio) — high value but `pyserial-asyncio` introduces a new dependency class; design needs careful review of event loop ownership in ROS2 context
- [ ] ROS2 Lifecycle Node migration — correct long-term architecture but full rewrite scope; plan as dedicated refactor milestone

---

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Custom exceptions | HIGH | LOW | P1 |
| Input validation | HIGH | LOW | P1 |
| Device watchdog | HIGH | MEDIUM | P1 |
| IQ sequence numbers | MEDIUM | LOW | P1 |
| Redis reconnection (Bridge) | HIGH | LOW | P1 |
| Observability metrics | HIGH | MEDIUM | P1 |
| Dead-letter queue | MEDIUM | MEDIUM | P1 |
| TX dry-run | HIGH | LOW | P1 |
| Antenna confirmation service | MEDIUM | LOW | P1 |
| IQ recording (SigMF) | HIGH | MEDIUM | P2 |
| Raw IQ fallback recording | MEDIUM | LOW | P2 |
| Headless FFT/PSD | HIGH | MEDIUM | P2 |
| Waterfall history (Redis) | MEDIUM | LOW | P2 |
| Frequency hopping scheduler | HIGH | MEDIUM | P2 |
| Legacy HackRFNode docs | LOW | TRIVIAL | P2 |
| pymayhem async API | MEDIUM | HIGH | P3 |

**Priority key:**
- P1: Core hardening — must ship in v2.0 to call the driver production-ready
- P2: Signal capabilities — high value, ship in v2.0 after P1 is stable
- P3: Architecture investment — defer to v3.0 or dedicated refactor milestone

---

## Expected Behavior Per Feature

### IQ Recording (SigMF)

**Trigger:** Redis command `{"cmd": "record_start", "duration_s": 30, "filename": "capture"}`

**Files produced:**
- `<filename>.sigmf-data` — raw complex float32 binary (interleaved I/Q, little-endian)
- `<filename>.sigmf-meta` — JSON metadata file

**Required SigMF metadata fields:**
- `core:datatype`: `"cf32_le"` (complex float32, little-endian)
- `core:version`: `"1.0.0"`
- `core:sample_rate`: from driver config at time of record
- `captures[0].core:frequency`: center frequency in Hz
- `captures[0].core:datetime`: ISO-8601 UTC timestamp at first sample
- `captures[0].core:sample_start`: 0

**Stop conditions:** duration elapsed, Redis command `{"cmd": "record_stop"}`, or driver shutdown.

**Sequence gap behavior:** If IQ sequence gap detected during recording, append a new captures entry at the gap sample index with updated datetime. This preserves SigMF validity.

**Dependency:** `pip install SigMF` (sigmf-python); verified on PyPI, compatible with Python 3.7–3.14.

**Confidence:** HIGH — SigMF spec is stable (v1.0), sigmf-python is the official implementation.

---

### Spectral Analysis (Headless FFT/PSD)

**Computation:** Welch-style per-buffer FFT using `numpy.fft.fft()` on each IQ chunk after
applying a Hann window (`numpy.hanning()`); compute power `|FFT|^2`; convert to dBFS
`10 * log10(power / max_power)`.

**Redis output:**
- `XADD hackrf:spectrum * bins <float32_bytes> center_freq <hz> sample_rate <sps> timestamp_ns <int>`
- `LPUSH hackrf:waterfall <float32_psd_row_bytes>` + `LTRIM hackrf:waterfall 0 199`

**ROS2 topic:** `/hackrf/spectrum` as `std_msgs/Float32MultiArray` with layout label `psd_dbfs`
and stride equal to FFT size. Published at same rate as IQ publish timer.

**FFT size:** Configurable (default 1024 bins); must be power of 2.

**Enabled by:** Config flag `spectral_analysis_enabled: true` (default false — CPU cost is
non-trivial on Jetson ARM64 at high sample rates).

**Confidence:** HIGH — numpy FFT is well-established; Redis binary storage of float32 arrays
is a standard pattern.

---

### Frequency Hopping Scheduler

**Class:** `hackrf_driver.hopper.FrequencyHopper`

**Schedule format:** List of `{"freq": <hz>, "dwell_ms": <int>}` dicts.

**Behavior:**
1. Check `is_transmitting` before each hop; skip (or pause schedule) if TX is active.
2. Call `driver._set_center_frequency(freq)` — uses existing validated setter.
3. Sleep `dwell_ms` milliseconds using `threading.Event.wait(timeout)` so stop is responsive.
4. Update `hackrf:state` on each hop (existing Redis state update path).
5. Emit log at DEBUG level per hop.

**Redis commands:**
- `{"cmd": "hop_start", "schedule": [...], "loops": -1}` — -1 = infinite
- `{"cmd": "hop_stop"}` — stop after current dwell completes

**Limitations:**
- HackRF USB retune latency is ~10–50 ms (USB round-trip + PLL lock); minimum practical
  dwell is ~100 ms to ensure stable samples before moving.
- `hackrf_sweep` firmware mode achieves 8 GHz/s sweep by handling retune inside the device
  without host round-trips; this scheduler does not use sweep mode — it is a general-purpose
  hop controller, not a wideband scanner.

**Conflicts:** Hop must not fire during active TX. `FrequencyHopper` checks
`TXController.is_transmitting` before each hop; if True, it logs a warning and extends dwell.

**Confidence:** MEDIUM — HackRF retune latency figures are from community reports (not
official spec); 100 ms minimum dwell is conservative.

---

### pymayhem Async API

**Module:** `pymayhem.async_client.AsyncMayhemClient`

**Library:** `pyserial-asyncio` 0.6 (PyPI: `pyserial-asyncio`); StreamReader/StreamWriter
pattern (not Protocol-based); integrates with Linux posix I/O via event loop fd monitoring.

**Method surface:** Mirrors `MayhemClient` exactly:
- `async def setfreq(self, freq_hz: int) -> str`
- `async def appstart(self, app_name: str) -> str`
- `async def radioinfo(self) -> dict`
- All domain sub-clients (`radio`, `ui`, `fs`, `sensors`, `system`) exposed as properties
  returning async-capable domain objects.

**Sync client unchanged:** `MayhemClient` remains the default for ROS2 and threading callers.

**Event loop ownership:** `AsyncMayhemClient` does not create its own event loop; the caller
provides it. This is correct for FastAPI/asyncio callers. For ROS2 callers, use sync client.

**Caveats:**
- pyserial-asyncio is Linux/macOS only for efficient I/O (uses posix fd); on Windows it
  falls back to polling (not relevant for this deployment).
- The async client cannot share the serial port with the sync `MayhemClient`; callers must
  use one or the other, not both simultaneously.

**Confidence:** MEDIUM — pyserial-asyncio is well-established (0.6 is current); async serial
wrappers are a standard pattern; complexity is in correct event loop handoff.

---

### Observability Metrics

**Redis key:** `hackrf:metrics` (Hash)

| Field | Type | Description |
|-------|------|-------------|
| `rx_packets_total` | int | IQ buffers published since startup |
| `rx_bytes_total` | int | Total IQ bytes published |
| `rx_drops` | int | Sequence gaps detected (proxy for dropped buffers) |
| `tx_packets_total` | int | TX operations completed |
| `rx_reconnects` | int | USB reconnect events triggered by watchdog |
| `bridge_reconnects` | int | Redis reconnect events in BridgeNode |
| `dlq_depth` | int | Current dead-letter queue entry count |
| `uptime_s` | float | Seconds since driver started |
| `last_error` | string | Last error message (empty if no errors) |
| `last_error_ts` | int | Unix timestamp of last error (ns) |

**Update frequency:** On every IQ publish tick (same timer as IQ stream) and on every
error/reconnect event.

**Confidence:** HIGH — Redis HSET pattern is the standard approach for metric key-values;
no external monitoring dependency required.

---

### Device Watchdog

**Mechanism:** Background daemon thread; checks `time.monotonic() - last_rx_timestamp_s`
every `watchdog_interval_s` (default 2.0 s). If delta exceeds `watchdog_stall_threshold_s`
(default 5.0 s), calls `driver._reconnect()` and increments `rx_reconnects` metric.

**Stall detection:** `last_rx_timestamp_s` updated in the RX callback on every received
buffer. If the callback stops firing (USB stall, device disconnect, libusb error), the
watchdog detects the silence.

**Reconnect sequence:** Same exponential-backoff reconnect already implemented in v1.0
(`_MIN_RECONNECT_DELAY`, `_MAX_RECONNECT_DELAY` in `config.py`); watchdog calls into the
same code path.

**Confidence:** HIGH — watchdog-plus-timestamp pattern is universal in hardware driver design.

---

### Dead-Letter Queue

**Redis key:** `hackrf:cmd:dlq` (Stream)

**Entry fields:**
- `cmd`: original command JSON string
- `error`: error message
- `attempts`: how many times the command was tried
- `ts_ns`: Unix timestamp when moved to DLQ

**Policy:** Command dispatch failure on first attempt → move to DLQ immediately (no inline
retry; retry is consumer responsibility). `MAXLEN ~ 500` prevents unbounded growth.

**Consumer contract:** Consumers can XREAD from `hackrf:cmd:dlq` to inspect failed commands;
re-issue manually or build automated retry. Driver does not auto-retry from DLQ.

**Confidence:** HIGH — Redis Streams DLQ pattern is well-documented in official Redis tutorials.

---

## Competitor Feature Analysis

| Feature | GNU Radio | DragonOS | rtl_433 / rtl-sdr | Our Approach |
|---------|-----------|----------|-------------------|--------------|
| IQ recording | SigMF block (built-in) | Depends on backend | Raw binary only | SigMF via sigmf-python with Redis trigger |
| Spectral analysis | Waterfall sink (GUI) | GUI tools | rtl_power (headless) | Headless numpy FFT; Redis stream + ROS2 topic |
| Frequency hopping | Flowgraph-based | Manual | Not native | Built-in scheduler; Redis command interface |
| Async serial API | N/A | N/A | N/A | asyncio via pyserial-asyncio (new in v2.0) |
| Observability | N/A | N/A | N/A | Redis metrics hash (unique to this driver) |

---

## Sources

- [SigMF Specification (v1.x)](https://github.com/sigmf/SigMF/blob/sigmf-v1.x/sigmf-spec.md) — HIGH confidence, official spec
- [sigmf-python on PyPI](https://pypi.org/project/SigMF/) — HIGH confidence, official library
- [IQ Files and SigMF — PySDR Guide](https://pysdr.org/content/iq_files.html) — MEDIUM confidence, well-maintained educational resource
- [HackRF One in Python — PySDR Guide](https://pysdr.org/content/hackrf.html) — MEDIUM confidence
- [pyserial-asyncio documentation](https://pyserial-asyncio.readthedocs.io/en/latest/shortintro.html) — HIGH confidence, official docs
- [pyserial-asyncio on PyPI](https://pypi.org/project/pyserial-asyncio/) — HIGH confidence
- [Redis Streams — Dead Letter Queue Pattern](https://redis.io/tutorials/redis-backed-job-queue-for-background-workers/) — HIGH confidence, official Redis tutorial
- [How to Implement Dead Letter Queues with Redis Streams](https://oneuptime.com/blog/post/2026-01-21-redis-dead-letter-queue/view) — MEDIUM confidence
- [SciPy spectrogram documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.spectrogram.html) — HIGH confidence, official docs
- [HackRF Tools — hackrf_sweep](https://hackrf.readthedocs.io/en/latest/hackrf_tools.html) — HIGH confidence, official HackRF docs

---

*Feature research for: HackRF ROS2 Driver v2.0 — Hardening, Observability & Signal Capabilities*
*Researched: 2026-03-30*
