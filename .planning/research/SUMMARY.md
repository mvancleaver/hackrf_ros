# Project Research Summary

**Project:** HackRF ROS2 Driver — v2.0 Hardening, Observability & Signal Capabilities
**Domain:** SDR hardware driver with ROS2 bridge and Redis data plane
**Researched:** 2026-03-30
**Confidence:** HIGH

## Executive Summary

The v2.0 milestone extends a fully functional v1.x driver (three-package architecture: pymayhem, hackrf_driver, hackrf_ros) with production hardening and signal capabilities. The existing system streams IQ data via pyhackrf2 to Redis and ROS2, controls Mayhem serial interface via pyserial, and gates TX behind a four-guard authorization system. It is operationally functional but not production-ready: errors are swallowed silently, the Redis bridge dies permanently on disconnect, the IQ stream has no gap detection, and there is no operator health visibility. v2.0 addresses all of this.

The recommended approach is strictly additive: no architectural changes to the three-package split, no asyncio migration of the core driver, no new infrastructure dependencies. New features tap into existing queue and threading patterns — dedicated daemon threads with bounded queues for recording and spectral analysis — extend the existing Redis namespace (hackrf:metrics, hackrf:spectrum, hackrf:cmd:dlq), and harden existing call sites with typed exceptions, input validation, and reconnect retry loops. The only new PyPI dependency that is truly required is `sigmf>=1.7.2` for standards-compliant IQ recording; scipy is optional, and all other features use stdlib or already-present packages.

The primary risks are threading-related: the watchdog, hop scheduler, IQ recorder, and spectrum analyzer all add concurrent paths to a driver whose existing lock protocol was designed for two threads. Every new daemon thread must be designed with explicit lock acquisition rules before a line of code is written. The Pitfalls research identified six critical failure modes — all deadlock or silent-data-loss class — that must be addressed in phase design, not during implementation.

---

## Key Findings

### Recommended Stack

The v1.x stack (redis-py 7.4+, hiredis, pyserial, pyhackrf2, numpy, rclpy) is validated and unchanged. v2.0 adds exactly three new PyPI packages across the full system:

- `sigmf>=1.7.2` — SigMF-format IQ recording; the only correct Python implementation; depends only on numpy (already present). Hard dependency for IQ recording feature.
- `scipy>=1.11,<1.16` — Welch PSD and spectrogram; constrained to <1.16 because scipy 1.16+ requires Python 3.11 while ROS2 Humble ships Python 3.10. Optional extra (`hackrf_driver[spectral]`).
- `pyserial-asyncio-fast>=0.16` — asyncio serial transport for pymayhem async API. Optional extra (`pymayhem[async]`). Deferred to v3.0+.

All observability (metrics, DLQ, watchdog) uses existing redis-py and stdlib threading. No Prometheus, no statsd, no external monitoring infrastructure.

**Core technologies:**
- `sigmf 1.7.2`: IQ recording to open standard — enables replay in GNU Radio, IQEngine, inspectrum with no sidecar README
- `numpy.fft` (base) / `scipy 1.15.x` (optional): Headless spectral analysis — numpy is sufficient for single-shot FFT; scipy adds Welch averaging only if needed
- Redis HSET pattern: Observability metrics — same pattern as existing `hackrf:state`; zero new dependencies
- stdlib `threading.Thread` + `threading.Event`: Watchdog, recorder thread, spectrum thread — no new concurrency libraries needed

### Expected Features

**Must have — v2.0 Core Hardening (P1, ship first):**
- Custom exception hierarchy (pymayhem: MayhemError tree; hackrf_driver: HackRFError tree) — callers cannot distinguish error types today
- Input validation in every public method entry point with `HackRFConfigError` on rejection — silent bad-param hardware corruption today
- Device health watchdog — USB stall detection with automatic reconnect
- IQ sequence numbers on every XADD entry — consumers detect dropped buffers
- Redis reconnection in BridgeNode — Redis restart currently kills the bridge permanently
- Observability metrics hash (`hackrf:metrics`) — operators have zero health visibility today
- Dead-letter queue (`hackrf:cmd:dlq`) — failed commands silently discarded today
- TX dry-run validation via non-consuming `validate_tx()` — safe pre-flight without RF emission
- Antenna confirmation ROS2 service — BridgeNode exposes TX gate to ROS2 orchestrators

**Should have — v2.0 Signal Capabilities (P2, ship after P1 stable):**
- IQ recording to SigMF (trigger-based, dedicated recorder thread)
- Raw IQ fallback recording (no sigmf dependency path)
- Headless FFT/PSD to Redis stream (`hackrf:spectrum`) + `/hackrf/spectrum` ROS2 topic
- Waterfall history in Redis list (`hackrf:waterfall`, rolling 200-row window)
- Frequency hopping scheduler with Redis command interface
- Legacy HackRFNode documentation and deprecation notice

**Defer to v3.0+:**
- pymayhem async API (asyncio) — high complexity, event loop ownership unresolved in ROS2 context
- ROS2 Lifecycle Node migration — correct long-term architecture but full rewrite scope

**Anti-features (do not build):**
- Persistent always-on disk recording (160 MB/s fills disk in minutes)
- Automatic demodulation (belongs in consumer layer)
- Web UI for waterfall (explicitly excluded from scope)
- asyncio port of hackrf_driver core (libusb callbacks are not async-safe)
- Frequency hopping during active TX (half-duplex hardware constraint)

### Architecture Approach

The existing three-package architecture (pymayhem → hackrf_driver → hackrf_ros) is preserved exactly. New features integrate at four well-defined tap points: (1) the `_rx_callback` path in `HackRFDriver` gets two additional `put_nowait()` calls feeding a `_record_queue` and `_fft_queue`; (2) `RedisBridge._xadd_iq()` gains a metrics accumulator and spectrum queue post; (3) `BridgeNode._bridge_loop()` gains reconnect retry logic and a second pubsub subscription for spectrum notifications; (4) `pymayhem._serial._send_command()` raises typed exceptions instead of returning booleans. The critical architectural rule: every new I/O-heavy operation (disk writes, FFT computation) runs in its own daemon thread with a bounded queue — never inline in the IQ drain loop.

**Major components added in v2.0:**
1. `IQRecorder` (hackrf_driver) — daemon thread draining `_record_queue`; writes SigMF or raw binary to disk; lifecycle controlled via Redis commands
2. `SpectrumAnalyzer` (hackrf_driver) — daemon thread draining `_fft_queue` at configurable decimation rate; publishes power spectra to `hackrf:spectrum` Redis stream
3. `FreqHopper` (hackrf_driver) — `threading.Timer` chain calling `_set_center_frequency()` per schedule; TX-aware (checks `_is_transmitting` before each hop)
4. `exceptions.py` modules (pymayhem + hackrf_driver) — typed exception hierarchies replacing bare `Exception` propagation
5. Watchdog timer in `HackRFDriver` — checks `_last_rx_time` every 10s; triggers `_try_connect()` on stall via correction queue

**New Redis namespace:**
- `hackrf:metrics` — Hash, updated at 1 Hz via pipeline
- `hackrf:spectrum` — Stream, float32 PSD entries
- `hackrf:spectrum:notify` — Pub/Sub channel (mirrors existing `hackrf:iq:notify` pattern)
- `hackrf:cmd:dlq` — Stream, failed command entries, MAXLEN=500

### Critical Pitfalls

1. **Custom exceptions break existing `bool`-return callers** — pymayhem domain methods currently return `bool`; adding raised exceptions is a public API break. Fix: catch pymayhem exceptions at the `_dispatch_command` boundary in redis_bridge.py, map to structured Redis error state. Never propagate raw exceptions through the dispatch table.

2. **Watchdog deadlock with `_device_lock`** — the watchdog runs in a separate daemon thread; if it acquires `_device_lock` while `_configure_device()` holds it (100–300 ms), the driver deadlocks. Fix: watchdog uses lock-free health signals only (check `is_hackrf_streaming` flag and `_redis_queue.qsize()`); corrective action posts a reconnect request to a queue rather than directly calling `_try_connect()`.

3. **IQ recorder blocks the Redis bridge thread on fsync** — writing to disk inline in `_xadd_iq()` blocks the drain loop; on Jetson SD card, a single fsync takes 50–200 ms, which overflows the 64-entry `_redis_queue` in ~65 ms. Fix: dedicated recorder thread with its own bounded queue; SigMF metadata written only on stop, not incrementally.

4. **FFT inline in `_drain_iq_queue` saturates CPU** — at 8 MSPS, the drain loop fires ~1000 times/second; adding numpy FFT inline increases per-iteration cost 5–10x, causing IQ drop-oldest. Fix: spectrum analysis in a dedicated thread consuming from a separate `_fft_queue` at a throttled rate (10–50 Hz), not per-chunk.

5. **Frequency hopping + TX = ABBA deadlock** — hop scheduler calls `_configure_device()` (holds `_device_lock`); TX holds `_tx_lock` and waits for `_device_lock`. Fix: hop scheduler checks `_is_transmitting` before every hop; single reconfiguration queue serializes all device reconfiguration requests to prevent concurrent lock acquisition.

6. **Redis reconnection in BridgeNode silently kills `/hackrf/iq`** — current `except Exception: break` exits the bridge loop permanently; ROS2 shows node alive but topic goes silent. Fix: replace `break` with exponential-backoff retry loop; resubscribe to `hackrf:iq:notify` after reconnect; flush state immediately after reconnect.

---

## Implications for Roadmap

A four-phase structure is indicated by the dependency graph in FEATURES.md: custom exceptions must exist before any other feature raises them; Redis reconnection must be stable before observability metrics can be reliably published; IQ sequence numbers must exist before recording or FFT can note gap events.

### Phase 1: Foundation Hardening

**Rationale:** All other v2.0 features depend on typed exceptions (to raise them) and Redis reconnection (to publish health data). These are pure-logic changes with no new dependencies, low implementation risk, and high leverage. This phase is also the most disruptive to existing API contracts (bool returns → exceptions) and must be locked before anything else touches pymayhem domain methods.

**Delivers:** Production-safe error handling; bridge that survives Redis restart; IQ gap detection; TX pre-flight validation

**Implements:**
- Custom exception hierarchy in pymayhem (`exceptions.py`) and hackrf_driver (`exceptions.py`)
- Input validation with `HackRFConfigError` on out-of-range values (single source of truth in `config.py`)
- IQ sequence numbers (`seq` field) added to every XADD entry
- Redis reconnection retry loop in BridgeNode with exponential backoff and state flush on reconnect
- TX dry-run via non-consuming `validate_tx()` path (does not call GETDEL on auth token)
- Antenna confirmation ROS2 service via BridgeNode / bridge_services.py

**Avoids:** Pitfall 1 (exception API break at dispatch boundary), Pitfall 7 (silent bridge death on Redis disconnect), Pitfall 10 (dry-run consuming auth token), Pitfall 11 (dual validation divergence between BridgeNode and hackrf_driver), Pitfall 12 (sequence number string comparison trap)

**Research flag:** Standard patterns — no phase research needed. Exception hierarchies and Redis reconnection are fully specified in STACK.md and PITFALLS.md.

### Phase 2: Observability and Reliability

**Rationale:** Depends on Phase 1 (Redis reconnection must be stable before metrics publishing is reliable; exceptions must exist before watchdog can raise them). These features are cohesive: watchdog detects failures, metrics surface them, DLQ preserves the evidence.

**Delivers:** Operator health visibility via `hackrf:metrics`; automatic USB reconnect; failed command forensics in DLQ

**Implements:**
- Device health watchdog (lock-free detection: check `is_hackrf_streaming` + `_redis_queue.qsize()`; corrective action via correction queue, not direct `_try_connect()`)
- Observability metrics hash (`hackrf:metrics`) published at 1 Hz via Redis pipeline
- Dead-letter queue (`hackrf:cmd:dlq` stream) with MAXLEN=500; strips auth tokens before XADD
- Legacy HackRFNode documentation and deprecation notice pointing to hackrf_driver

**Avoids:** Pitfall 2 (watchdog deadlock — lock-free detection path), Pitfall 8 (metrics at XADD rate — throttle to 1 Hz via pipeline), Pitfall 9 (unbounded DLQ — MAXLEN=500 + EXPIRE TTL)

**Research flag:** Watchdog correction queue design needs explicit specification before coding — document which thread drains the correction queue and how it avoids re-entry with `_configure_device()`.

### Phase 3: Signal Capabilities

**Rationale:** Depends on Phase 1 (IQ sequence numbers for gap notation in recordings; validated parameters for recording trigger). IQ recording and spectral analysis share the dedicated-thread pattern and should be built together. Frequency hopping is a separate subsystem but shares the `_configure_device()` lock concern and belongs in the same phase.

**Delivers:** SigMF-format recordings; real-time spectrum to Redis and ROS2; programmable frequency scanning

**Implements:**
- `IQRecorder` daemon thread with `_record_queue` tap in `_rx_callback`; SigMF lifecycle via Redis `record_start` / `record_stop` commands; metadata file written only on stop
- `SpectrumAnalyzer` daemon thread with `_fft_queue` tap; numpy FFT with Hann window; publishing to `hackrf:spectrum` stream + Pub/Sub notify
- Waterfall history in `hackrf:waterfall` Redis list (rolling 200 rows, LPUSH + LTRIM)
- `/hackrf/spectrum` ROS2 topic via BridgeNode second pubsub subscription (mirrors existing IQ topic pattern)
- `FreqHopper` class with TX-aware `threading.Timer` chain; Redis `hop_start` / `hop_stop` command handlers; checks `_is_transmitting` before every hop

**Avoids:** Pitfall 3 (recorder blocking bridge thread — dedicated recorder thread), Pitfall 4 (FFT inline CPU saturation — dedicated spectrum thread throttled to ≤50 Hz), Pitfall 5 (hop + TX deadlock — `_is_transmitting` check + single reconfiguration queue)

**Research flag:** SigMF gap entry byte offset calculation (sample_start after a sequence gap) needs concrete specification in the phase plan. Frequency hopping lock order between `_device_lock` and `_tx_lock` must be explicitly documented before code is written.

### Phase 4: pymayhem Async API (v3.0 candidate)

**Rationale:** Deferred from v2.0. High implementation complexity (event loop ownership, ROS2 context incompatibility), and the deployment target (ROS2 nodes with rclpy threading model) does not benefit from async pymayhem. The executor-wrapper pattern (Option A in ARCHITECTURE.md: `AsyncMayhemClient` wraps `MayhemClient` with `run_in_executor`) is the correct approach if ever built, requiring zero new dependencies. Only build if an asyncio-native consumer (FastAPI, home automation system) needs direct Mayhem control.

**Delivers:** `AsyncMayhemClient` with `await client.radio.setfreq(...)` syntax for asyncio callers; zero impact on sync `MayhemClient` or hackrf_driver

**Avoids:** Pitfall 6 (event loop ownership — `asyncio.run()` must never appear inside library code; caller provides loop)

**Research flag:** Needs phase research — event loop ownership contract when pymayhem is used from a ROS2 node that may use rclpy's async executor is not fully resolved. Research asyncio/ROS2 executor interaction before writing any async code.

### Phase Ordering Rationale

- **Exceptions before everything:** Every new feature in phases 2–4 raises `HackRFError` or `MayhemError` subtypes. The exception modules must exist first or every feature uses bare `Exception`.
- **Reconnection before metrics:** If BridgeNode's Redis connection can die silently, metrics are unreliable. Phase 1 must fix the bridge before Phase 2 adds metrics publishing through it.
- **Sequence numbers before recording:** SigMF recordings note gap events using sequence number discontinuities. Phase 1 sequence numbers are a prerequisite for Phase 3 recording accuracy.
- **Watchdog before signal capabilities:** The watchdog's reconnect path is exercised heavily during frequency hopping (rapid device reconfiguration). Having the watchdog working and tested before hopping reduces Phase 3 risk.
- **Dedicated threads as a pattern before I/O:** The architecture rule — never inline I/O in `_drain_iq_queue` — must be established as an explicit design constraint in Phase 3 before a line of recording or FFT code is written.

### Research Flags

**Needs deeper research during planning:**
- **Phase 2 (watchdog):** Lock acquisition order between `_device_lock`, `_tx_lock`, and watchdog correction queue must be explicitly designed. The pitfalls research identifies the deadlock risk but does not name a canonical fix — the phase plan must resolve this before implementation.
- **Phase 3 (recorder thread):** SigMF gap handling (when to emit a new `captures` entry, how to compute `sample_start` from chunk count) needs a concrete specification in the phase plan.
- **Phase 4 (async pymayhem):** Event loop ownership in a mixed threading/asyncio context, specifically when pymayhem is used from a ROS2 node that may or may not use rclpy's async executor, is unresolved. Phase research required.

**Standard patterns (skip research-phase):**
- **Phase 1 (exceptions + validation):** Python exception hierarchies and input validation are stdlib patterns. Exact exception names and valid parameter ranges are fully specified in STACK.md and PITFALLS.md.
- **Phase 1 (Redis reconnection):** Exponential backoff retry with ping-and-resubscribe is a standard Redis client pattern. ~20 lines of implementation.
- **Phase 2 (metrics):** Redis HSET at 1 Hz via pipeline is identical to the existing `hackrf:state` pattern. No design research needed.
- **Phase 2 (DLQ):** `XADD MAXLEN ~ 500` is the canonical Redis Streams DLQ pattern. Fully specified in FEATURES.md.
- **Phase 3 (spectrum analyzer):** numpy FFT on complex float32 with Hann window is a solved problem. Thread architecture follows the same pattern as the existing `RedisBridge` daemon thread.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All new packages verified on PyPI with exact versions; scipy/Python 3.10 compatibility constraint confirmed against scipy release matrix |
| Features | HIGH | Features directly derived from identified gaps in the existing codebase; no speculative requirements; dependency graph is precise |
| Architecture | HIGH | Based on direct codebase inspection of v1.x Phase 5 end state; integration points are concrete with specific file/method names, not design sketches |
| Pitfalls | HIGH | All critical pitfalls identified by mechanistic inspection of existing threading model and lock protocol; deadlock scenarios are fully traced |

**Overall confidence: HIGH**

### Gaps to Address

- **Watchdog correction queue design:** PITFALLS.md says "post to a queue rather than directly calling `_try_connect()`" but does not specify which thread drains that correction queue. The Phase 2 plan must name the thread and specify how it avoids re-entrant reconfiguration.
- **SigMF gap entry timing:** When a sequence gap is detected mid-recording, the SigMF spec requires a new `captures` entry at the correct `sample_start` offset. The exact calculation (chunk count × chunk size) must be specified in the Phase 3 plan before the IQRecorder is implemented.
- **Hop rate floor on Jetson:** The minimum practical hop dwell of ~100 ms is from community reports, not the official HackRF spec. If sub-100 ms hopping is required, this needs hardware validation on the target Jetson ARM64 platform.
- **Jetson ARM64 FFT performance:** PITFALLS.md recommends `OPENBLAS_NUM_THREADS=1` to prevent OpenBLAS thread pool contention. This mitigation is not tested against actual throughput numbers. Phase 3 plan should include a performance benchmark gate before declaring spectrum analysis complete.

---

## Sources

### Primary (HIGH confidence)
- SigMF specification v1.x (github.com/sigmf/SigMF/blob/sigmf-v1.x/sigmf-spec.md) — IQ recording format requirements
- sigmf-python PyPI v1.7.2 (pypi.org/project/SigMF/) — dependency verification
- pyserial-asyncio-fast PyPI v0.16 (pypi.org/project/pyserial-asyncio-fast/) — async serial dependency
- scipy release matrix (docs.scipy.org/doc/scipy/release.html) — Python 3.10 version ceiling confirmed
- Redis Streams documentation (redis.io) — DLQ pattern, MAXLEN semantics, pipeline batching
- HackRF Tools documentation (hackrf.readthedocs.io) — hackrf_sweep, retune latency context

### Secondary (MEDIUM confidence)
- PySDR Guide — IQ Files and SigMF (pysdr.org/content/iq_files.html) — format overview and SigMF field semantics
- PySDR Guide — HackRF in Python (pysdr.org/content/hackrf.html) — retune latency estimates
- pyserial-asyncio docs (pyserial-asyncio.readthedocs.io) — StreamReader/StreamWriter pattern
- Community reports — HackRF USB retune latency ~10–50 ms; 100 ms minimum dwell is a conservative estimate

### Tertiary (LOW confidence)
- None — all findings in this summary are backed by primary or secondary sources

---

*Research completed: 2026-03-30*
*Ready for roadmap: yes*
