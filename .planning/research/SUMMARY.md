# Project Research Summary

**Project:** HackRF ROS2 Driver — Redis + Serial + TX Milestone
**Domain:** Embedded SDR driver with Redis streaming, Mayhem serial control, and safe TX
**Researched:** 2026-03-29
**Confidence:** HIGH (stack and architecture from official sources; pitfalls validated against upstream issue trackers)

---

## Executive Summary

This project extends a working HackRF One ROS2 prototype into a production-grade driver with three new subsystems: a Redis streaming bridge for IQ data and device state, a serial interface to the Portapack Mayhem firmware for app control, and a TX capability gated behind explicit authorization guardrails. The recommended approach is an incremental, dependency-ordered build starting with correctness fixes to the existing RX pipeline, then layering each new subsystem on stable foundations. The architecture is a single ROS2 node (`HackRFNode`) that owns all hardware state, with Redis, serial, and TX logic isolated in dedicated helper classes and a background thread — no additional ROS2 nodes are needed for the new subsystems.

The most important architectural insight from combined research is that the current prototype has two latent bugs that will corrupt all downstream work if not fixed first: the shared `current_samples_buffer` numpy array has no thread lock between the pyhackrf2 USB callback and the ROS2 timer, and the `_configure_hackrf()` function calls `stop_rx()` with no timeout or thread guard, creating a known deadlock path. Both must be resolved before any Redis or serial code is written. The replacement is a `queue.Queue`-based producer/consumer split, which is thread-safe by design and correctly separates the USB interrupt thread from the ROS2 executor thread.

The primary risk in this milestone is hardware safety: transmitting without an antenna physically destroys the HackRF's front-end amplifier (no repair path), and transmitting on protected frequencies carries legal liability. The TX authorization design must enforce a frequency allowlist and explicit antenna confirmation — these are not optional polish items. A secondary risk is the unresolved question of whether pyhackrf2 IQ streaming (HackRF USB bulk mode) and Mayhem serial console (`/dev/ttyACM1`) can operate simultaneously or require a mode switch; this must be tested empirically before either integration is written.

---

## Key Findings

### Recommended Stack

The new dependencies are minimal and well-chosen. `redis-py >= 7.4.0` with `hiredis >= 3.3.1` provides the Redis interface: v7 is the active series, hiredis auto-accelerates response parsing with no code changes, and the asyncio variant (`aioredis`) is obsolete and merged. `pyserial >= 3.5` (the only stable release) handles serial communication with the Mayhem console over `/dev/ttyACM1`; pyserial-asyncio is inappropriate because this driver uses threads, not an asyncio event loop. All three packages are compatible with the Python 3.10 environment shipped by ROS2 Humble.

The Redis data model uses Streams (`XADD`) for IQ data and commands, and a Hash (`HSET`) for device state. This is the correct choice: Pub/Sub drops messages on slow or disconnected consumers (unacceptable for IQ data), while Streams provide ring-buffer semantics with MAXLEN trimming. The TX authorization pattern uses a time-bounded Redis token with atomic `GETDEL` — no persistent "armed" state and no session model.

**Core technologies:**
- `redis-py 7.4.0` + `hiredis 3.3.1`: Redis client for IQ streaming and control — v7 is active series, hiredis is zero-config performance boost
- `pyserial 3.5`: Serial communication with Mayhem console — synchronous, daemon-thread model, no asyncio needed
- `queue.Queue` (stdlib): Thread-safe handoff between pyhackrf2 USB callback and ROS2/Redis threads — replaces unsafe numpy buffer
- `MultiThreadedExecutor` (rclpy): Two callback groups (device + service) enabling service calls to overlap IQ publication

### Expected Features

See `.planning/research/FEATURES.md` for full table with complexity and dependency details.

**Must have (table stakes):**
- Thread-safe IQ buffer (replace `np.append` + unprotected list with `queue.Queue`) — existing code has a live race condition
- Error recovery and USB reconnection with backoff — single USB hiccup currently kills the session permanently
- Parameter validation with explicit hardware ranges (freq: 1 MHz–6 GHz, LNA: 0–40 dB, VGA: 0–62 dB)
- Class naming fix (`HackRFPuiblisherNode` → `HackRFPublisherNode`) and structured logging (no bare `print`)
- Redis IQ stream publish (`XADD hackrf:iq:stream` with MAXLEN) — primary external data interface
- Redis device state hash (`HSET hackrf:state`) — consumers need queryable current config
- Redis command interface (`SUBSCRIBE hackrf:cmd`) — closes the external control loop
- Serial port lifecycle (open at startup, close at shutdown) with Mayhem `applist`/`appstart`/`setfreq`/`radioinfo`
- TX authorization gate (one token per transmission, frequency allowlist, antenna confirmation)
- TX stop on node shutdown (calling `stop_tx()` in `destroy_node()` is an RF safety requirement)

**Should have (differentiators):**
- Redis Streams over Pub/Sub for IQ (persistent window for late-joining consumers)
- `setfreq` via serial for in-app frequency updates (faster than stop/reconfigure/restart)
- `radioinfo` query to verify config round-trip after `setfreq`
- TX via pyhackrf2 `start_tx()` for custom IQ waveform transmission
- `gotgps` serial injection for mobile/APRS use cases

**Defer to v2+:**
- Mayhem POCSAG TX via `sendpocsag` — requires careful testing; establish TX baseline first
- Mayhem file replay (SD card staging + `appstart Replay`) — high complexity, low frequency of use
- ROS2 Lifecycle Node migration — correct long-term architecture but is a full node rewrite; plan as dedicated milestone
- Mayhem screenshot bridge to ROS2 Image topic — niche, low value for current use case

**Anti-features (do not build):**
- Persistent TX authorization session (creates an always-armed transmitter window)
- Automatic frequency hopping (conflicts with Mayhem app state)
- Signal demodulation (driver layer ends at IQ delivery)
- Web UI / dashboard (Redis consumers build their own)

### Architecture Approach

The architecture is a single `HackRFNode` owning all hardware, with three helper classes — `MayhemSerial`, `RedisBridge`, and `TXController` — operating in dedicated daemon threads. A `MultiThreadedExecutor` with two `MutuallyExclusive` callback groups handles the ROS2 side. IQ data flows from the pyhackrf2 USB callback into two separate `queue.Queue` instances (one for ROS2 publication, one for Redis), preventing either consumer from stalling the other. Redis writes happen exclusively in the Redis bridge thread; the ROS2 executor never touches the Redis client. All device reconfiguration is protected by a single `_device_lock (RLock)`, serial writes by `_serial_lock (Lock)`, and TX state by an `RLock` in `TXController`.

**Major components:**
1. `HackRFNode` (`hackrf_node.py`) — owns all hardware state; contains pyhackrf2 client, parameter handling, and callback groups
2. `MayhemSerial` (`mayhem_serial.py`) — line-oriented serial interface to Mayhem console; single in-flight command model with reconnect loop
3. `RedisBridge` (`redis_bridge.py`) — daemon thread draining the redis queue, publishing IQ stream and state hash, consuming command channel
4. `TXController` (`tx_controller.py`) — in-memory auth token model; one token per transmission; all methods protected by RLock
5. `IQPlotterNode` (`iq_plotter_node.py`) — visualization node, unchanged

**Key patterns:**
- Two separate queues (not a shared queue with multiple consumers) for IQ → ROS2 and IQ → Redis
- `queue.Queue` with `maxsize=100`, drop-on-full (not block-on-full) to prevent OOM
- `XADD key MAXLEN ~ 1000` on every Redis IQ publish to cap stream memory
- One auth token = one TX transmission (no session model)

### Critical Pitfalls

See `.planning/research/PITFALLS.md` for full detail including detection signals and phase assignments.

1. **`stop_rx()` called from inside the RX callback causes a deadlock** — The pyhackrf2 callback must only return `0` (continue) or non-zero (signal stop); use a `threading.Event` to request stop from the main thread. Add a timeout watchdog around every `stop_rx()` call. Fix this before writing any other code.

2. **Unbounded IQ buffer + Redis writes in the same thread = OOM** — At 8 MSPS, `np.append()` in the callback generates ~32 MB/s of float32 data; any Redis latency spike causes unbounded growth. Replace with `queue.Queue(maxsize=100)` drop-on-full and move all Redis writes to the bridge thread.

3. **TX without antenna physically destroys the HackRF amplifier** — There is no hardware interlock. The TX authorization flow must include an explicit antenna confirmation step, log a prominent warning on every TX authorization, and default TX VGA gain to 0 dB requiring explicit override.

4. **Mayhem serial console and pyhackrf2 IQ streaming may be mutually exclusive** — The Mayhem firmware wiki warns against entering HackRF mode while the serial console is active. Whether these two interfaces can coexist concurrently on this firmware version must be tested empirically before either integration layer is written. If they cannot coexist, the driver needs an explicit mode-switch state machine.

5. **TX on protected frequencies carries legal liability** — HackRF covers 1 MHz–6 GHz with no hardware locks. The TX path must validate frequency against an allowlist (default: ISM bands only) and hard-reject transmissions on emergency, aviation, GPS, and maritime distress frequencies regardless of authorization level.

---

## Implications for Roadmap

Based on the combined dependency graph from FEATURES.md, the build order from ARCHITECTURE.md, and the phase assignments from PITFALLS.md, a 4-phase structure is strongly indicated.

### Phase 1: RX Pipeline Correctness

**Rationale:** All subsequent phases depend on a reliable, thread-safe IQ pipeline. The two live bugs (unsafe buffer, stop_rx deadlock risk) will manifest as intermittent crashes when Redis and serial are added. Fix these first at zero dependency cost — no new packages required.

**Delivers:** A production-stable RX pipeline with correct threading, parameter validation, reconnection logic, and readable logs.

**Addresses:** Thread-safe IQ buffer, error recovery/reconnection, parameter validation with ranges, class naming fix, structured logging, `stop_rx()` 100ms settling delay.

**Avoids:** Pitfall 1 (stop_rx deadlock), Pitfall 6 (GIL contention in callback), Pitfall 10 (rapid start/stop firmware corruption), Pitfall 14 (`tolist()` performance).

**Research flag:** Standard patterns — `queue.Queue`, ROS2 `MultiThreadedExecutor`, libhackrf threading rules are all well-documented. No phase research needed.

---

### Phase 2: Mayhem Serial Interface

**Rationale:** Serial must be built and tested before Redis and TX, because: (a) the mode-conflict question (Pitfall 4) must be resolved empirically before any integration is written, (b) TX via Mayhem depends entirely on `MayhemSerial`, and (c) serial testing can be done with a terminal before Redis exists. This phase has an isolated test surface.

**Delivers:** A `MayhemSerial` helper class with `appstart`, `applist`, `setfreq`, `radioinfo`, and reconnect loop; serial lifecycle wired into `HackRFNode`; ROS2 service `hackrf/mayhem_cmd`; empirical answer to the mode-conflict question.

**Addresses:** Serial port lifecycle, `applist` query, `appstart`/`setfreq`/`radioinfo` commands.

**Avoids:** Pitfall 4 (mode conflict — resolve empirically in this phase), Pitfall 7 (device path instability — use `/dev/serial/by-id/` symlinks), Pitfall 9 (response parsing — implement proper `ch>` prompt reader with per-command timeouts).

**Research flag:** The Mayhem serial protocol is documented but the concurrent-access question is not. Empirical testing is required at the start of this phase. The `MayhemSerial` class design (command/response framing) may need adjustment based on actual firmware behavior.

---

### Phase 3: Redis Bridge

**Rationale:** Redis integration depends on the Phase 1 queue refactor (needs `queue.Queue` as the handoff mechanism) but does not depend on serial. It can proceed in parallel with Phase 2 logically, but sequential execution (after Phase 2) ensures the mode-conflict answer is known before Redis command dispatch routes to `MayhemSerial`.

**Delivers:** `RedisBridge` daemon thread publishing IQ stream (`XADD hackrf:iq:stream MAXLEN ~ 1000`) and device state hash (`HSET hackrf:state`); Redis command subscriber dispatching `set_frequency`, `set_gain`, and (via MayhemSerial) `app_start`; configurable `redis_url`, `redis_iq_stream_key`, `redis_state_key` ROS parameters.

**Uses:** `redis-py 7.4.0`, `hiredis 3.3.1`, `queue.Queue` from Phase 1 refactor.

**Addresses:** Redis IQ stream publish, Redis device state hash, Redis command interface, IQ data format metadata in stream fields.

**Avoids:** Pitfall 2 (unbounded buffer — queue is bounded and Redis bridge runs in dedicated thread), Pitfall 8 (Redis stream memory growth — MAXLEN on every XADD), Pitfall 11 (IQ format undocumented — include format/sample_rate/center_freq fields in stream entry).

**Research flag:** Standard patterns — Redis Streams and redis-py are well-documented. No additional research needed if team is familiar with the API.

---

### Phase 4: TX Authorization and Transmission

**Rationale:** TX is the highest-risk phase (hardware damage + legal liability) and must be last. It requires Phase 1 (stable RX pipeline), Phase 2 (MayhemSerial for app dispatch), and Phase 3 (Redis command routing) to be solid before adding transmit paths.

**Delivers:** `TXController` with in-memory auth token model (one token = one TX), frequency allowlist validation, antenna confirmation warning, TX gain quantization with audit logging, `hackrf/authorize_tx` and `hackrf/transmit` ROS2 services, `stop_tx()` in `destroy_node()`, TX state published to `/hackrf/tx_state` and `hackrf:state` Redis hash.

**Addresses:** TX authorization gate, TX stop on shutdown, TX via pyhackrf2 `start_tx()`, parameter validation for TX gain values.

**Avoids:** Pitfall 3 (antenna damage — prominent warning + conservative defaults), Pitfall 5 (protected frequencies — frequency allowlist hard-coded for ISM bands, hard rejections for distress/aviation/GPS), Pitfall 12 (TX gain quantization — quantize and log actual applied value).

**Research flag:** The pyhackrf2 `start_tx()` API is lightly documented (medium confidence). Half-duplex mode switching behavior from RX to TX and back needs empirical validation. Recommend a brief research-phase at the start of this phase covering: `start_tx()` callback contract, half-duplex switching timing, and TX error recovery.

---

### Phase Ordering Rationale

- Phase 1 before everything: The queue refactor is a foundational prerequisite. Any code written on top of the broken buffer will need to be rewritten.
- Phase 2 before Phase 4: `TXController` calls `MayhemSerial`; serial must exist and be tested first.
- Phase 2 before Phase 3 (in this order): The mode-conflict question (Pitfall 4) affects what Phase 3's command dispatcher can safely route to serial. Knowing the answer first reduces rework.
- Phase 4 last: Irreversible hardware risk and legal risk — build on the most stable foundation possible before introducing any transmit path.

### Research Flags

Needs phase research before implementation:
- **Phase 2 (serial):** Concurrent pyhackrf2 + Mayhem serial access is undocumented. Empirical testing required at phase start to determine mode architecture.
- **Phase 4 (TX):** `pyhackrf2.start_tx()` API has medium-confidence documentation. Half-duplex RX/TX switching timing needs empirical validation before writing TX controller logic.

Standard patterns (skip research-phase):
- **Phase 1 (RX correctness):** `queue.Queue` threading, libhackrf stop_rx rules, and rclpy MultiThreadedExecutor are all officially documented.
- **Phase 3 (Redis bridge):** Redis Streams, XADD/MAXLEN, and redis-py are all thoroughly documented with official examples.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All versions verified directly from PyPI and GitHub releases on 2026-03-29 |
| Features | MEDIUM-HIGH | Mayhem serial commands from official wiki (HIGH); pyhackrf2 TX API has thin documentation (MEDIUM) |
| Architecture | HIGH | Codebase inspected directly; all patterns from official ROS2 and Python stdlib docs |
| Pitfalls | HIGH | Critical pitfalls confirmed against libhackrf issue tracker, Mayhem wiki, and Redis official docs |

**Overall confidence:** HIGH

### Gaps to Address

- **Mayhem serial + pyhackrf2 concurrent access:** The Mayhem wiki warns against this but does not document per-firmware-version behavior. Must test empirically at the start of Phase 2. If they cannot coexist, the architecture needs a mode-switch state machine (adds ~1 sprint of complexity).

- **`pyhackrf2.start_tx()` callback contract:** The pyhackrf2 library has minimal documentation; TX behavior is inferred from libhackrf. Half-duplex switching timing and error recovery behavior need empirical testing at the start of Phase 4.

- **TX gain quantization values:** LNA TX gain valid values (documented as 0 dB and 14 dB) should be verified against the pyhackrf2 source before implementing the quantization validator.

- **Redis security model:** The Redis command interface has no authentication by default. The current design is documented as "trusted local environment only." If deployment environment changes, Redis ACL / requirepass configuration is needed.

---

## Sources

### Primary (HIGH confidence)

- Mayhem firmware USB Serial Console wiki — confirmed serial commands, mode interaction warning
  https://github.com/portapack-mayhem/mayhem-firmware/wiki/USB-Serial-Console
- redis-py official docs — Streams API, ConnectionPool, thread safety
  https://redis.readthedocs.io/en/stable/
- Redis XADD official docs — MAXLEN trimming behavior
  https://redis.io/docs/latest/commands/xadd/
- ROS2 Humble callback groups — MultiThreadedExecutor design
  https://docs.ros.org/en/humble/How-To-Guides/Using-callback-groups.html
- Python queue module — thread-safe Queue guarantees
  https://docs.python.org/3/library/queue.html
- HackRF One official docs — hardware specs and limits
  https://hackrf.readthedocs.io/en/latest/hackrf_one.html
- rclpy issue tracker — GIL and MultiThreadedExecutor performance (issues #1025, #1452)
  https://github.com/ros2/rclpy/issues/

### Secondary (MEDIUM confidence)

- redis-py PyPI (v7.4.0 release date and changelog) — https://pypi.org/project/redis/
- hiredis PyPI (v3.3.1 release date) — https://pypi.org/project/hiredis/
- pyserial PyPI (v3.5 current stable) — https://pypi.org/project/pyserial/
- libhackrf issue #1570 — stop_rx deadlock in Python — https://github.com/greatscottgadgets/hackrf/issues/1570
- libhackrf issue #916 — rapid start/stop RX corruption — https://github.com/greatscottgadgets/hackrf/issues/916
- pyhackrf2 GitHub — TX API inferred from source — https://github.com/eizemazal/pyhackrf2

### Tertiary (LOW confidence / needs validation)

- Mayhem concurrent serial + pyhackrf2 access behavior — not documented, empirical test required
- pyhackrf2 `start_tx()` half-duplex switching timing — inferred from libhackrf, not tested

---

*Research completed: 2026-03-29*
*Ready for roadmap: yes*
