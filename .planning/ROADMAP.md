# Roadmap: HackRF ROS2 Driver

## Overview

This milestone transforms a fully functional v1.x driver into a production-reliable system. The work is ordered strictly by dependency: typed exceptions and input validation are established first (every subsequent feature raises them), then Redis reconnection and IQ sequence numbers are locked in (bridge stability and gap detection are prerequisites for observability and recording respectively), and finally signal capabilities are layered on top of that hardened foundation.

Three-package architecture (pymayhem → hackrf_driver → hackrf_ros) is preserved exactly. All new features integrate at well-defined tap points: two new queue taps in `_rx_callback`, a metrics accumulator in `RedisBridge`, a reconnect retry loop in `BridgeNode`, and exception modules in both pymayhem and hackrf_driver.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: RX Pipeline Correctness** - Fix thread-safety bugs and add error recovery to the existing IQ pipeline (completed 2026-03-30)
- [x] **Phase 2: Mayhem Serial Interface** - Build serial control for Mayhem firmware and resolve the mode-conflict question (completed 2026-03-30)
- [x] **Phase 3: Redis Bridge** - Stream IQ data and device state to Redis; accept control commands via Redis (completed 2026-03-30)
- [x] **Phase 4: TX Authorization** - Add safe, authorized transmission with frequency allowlist and hardware guardrails (completed 2026-03-30)
- [x] **Phase 5: PyMayhem Refactor** - Extract standalone pymayhem package, Redis-native driver, ROS2 bridge plugin (completed 2026-03-30)
- [ ] **Phase 6: Foundation Hardening** - Typed exceptions, input validation, Redis reconnection, IQ sequence numbers, TX dry-run, antenna service, legacy deprecation
- [ ] **Phase 7: Observability & Reliability** - Device health watchdog, metrics hash, dead-letter queue
- [ ] **Phase 8: Signal Capabilities** - IQ recording to SigMF, headless spectral analysis, programmable frequency hopping

## Phase Details

### Phase 1: RX Pipeline Correctness
**Goal**: The RX pipeline is thread-safe, recovers from USB errors, validates all parameters, and produces clean structured logs
**Depends on**: Nothing (first phase)
**Requirements**: RX-01, RX-02, RX-03, RX-04, RX-05, RX-06, RX-07
**Success Criteria** (what must be TRUE):
  1. IQ samples flow continuously without crashes or data corruption when the ROS2 timer and USB callback run concurrently
  2. When the HackRF is unplugged and replugged, the driver reconnects automatically without a node restart
  3. Setting frequency, gain, or sample rate to an out-of-range value produces a clear error log and leaves the device unchanged
  4. The node starts up, streams IQ, and shuts down cleanly with no deadlocks and no bare print statements in the log
**Plans**: 3 plans

Plans:
- [x] 01-01-PLAN.md — Replace buffer with dual queues, strip RX callback, fix publish consumer (RX-01, RX-07)
- [x] 01-02-PLAN.md — Reconnect loop with exponential backoff, deadlock guard, lifecycle shutdown (RX-02, RX-05, RX-06)
- [x] 01-03-PLAN.md — Parameter validation, class rename, logging cleanup, plotter/config/setup alignment (RX-03, RX-04)

### Phase 2: Mayhem Serial Interface
**Goal**: The driver communicates with Mayhem firmware over serial, can discover and switch apps, update frequency, and confirm the mode-conflict answer empirically
**Depends on**: Phase 1
**Requirements**: MAY-01, MAY-02, MAY-03, MAY-04, MAY-05, MAY-06
**Success Criteria** (what must be TRUE):
  1. The driver opens /dev/ttyACM1 at startup, queries applist, and logs the discovered apps without manual intervention
  2. A ROS2 service call switches the active Mayhem app by name (e.g., capture -> scanner)
  3. A setfreq command updates the frequency within the active app and radioinfo confirms the change
  4. The mode-conflict behavior between pyhackrf2 IQ streaming and Mayhem serial is tested and documented, with the driver failing loudly if the combination is incompatible
**Plans**: 3 plans

Plans:
- [x] 02-01-PLAN.md — Create hackrf_ros_interfaces CMake package with AppStart.srv and SetFreq.srv (MAY-03, MAY-04)
- [x] 02-02-PLAN.md — Implement MayhemSerial helper class with serial lifecycle, command methods, and unit tests (MAY-01, MAY-02, MAY-03, MAY-04, MAY-05)
- [x] 02-03-PLAN.md — Wire MayhemSerial into HackRFNode: services, status topic, mode coexistence check (MAY-01, MAY-03, MAY-04, MAY-05, MAY-06)

### Phase 3: Redis Bridge
**Goal**: IQ samples and device state are published to Redis continuously, and external callers can reconfigure the device via Redis commands
**Depends on**: Phase 2
**Requirements**: RED-01, RED-02, RED-03, RED-04, RED-05
**Success Criteria** (what must be TRUE):
  1. An external Redis client can read live IQ samples from hackrf:iq:stream without causing the ROS2 executor to block or stall
  2. hackrf:state reflects current frequency, gain, sample rate, and streaming status and updates within one second of any configuration change
  3. Writing a valid command to hackrf:cmd changes the device configuration and the change is visible in hackrf:state
  4. All Redis keys use the hackrf: namespace prefix consistently; the stream is trimmed by MAXLEN and does not grow unboundedly
**Plans**: 2 plans

Plans:
- [x] 03-01-PLAN.md — RedisBridge class with TDD, package config (RED-01, RED-04, RED-05)
- [x] 03-02-PLAN.md — Wire RedisBridge into HackRFNode; MayhemSerial _active_app; state/command integration (RED-02, RED-03, RED-04, RED-05)

### Phase 4: TX Authorization
**Goal**: The driver can transmit signals via pyhackrf2, gated behind one-token-per-TX authorization, a frequency allowlist, and an explicit antenna confirmation
**Depends on**: Phase 3
**Requirements**: TX-01, TX-02, TX-03, TX-04, TX-05, TX-06, TX-07
**Success Criteria** (what must be TRUE):
  1. A TX command with no authorization token is rejected before any transmission occurs
  2. A TX command targeting a restricted frequency (cellular, aviation, emergency) is hard-rejected regardless of authorization
  3. An authorized TX command on a permitted frequency proceeds only after the antenna confirmation flag is set, then the auth token is consumed and cannot be reused
  4. On node shutdown, any in-progress TX is stopped and the device returns to a known safe state
**Plans**: 2 plans

Plans:
- [x] 04-01-PLAN.md — TXController class: frequency allowlist, antenna confirmation, Lua GETDEL auth token (TX-02, TX-03, TX-04, TX-05)
- [x] 04-02-PLAN.md — Wire TXController into HackRFNode and RedisBridge command handlers; TX shutdown (TX-01, TX-06, TX-07)

### Phase 5: PyMayhem Refactor
**Goal**: Extract a standalone `pymayhem` Python package from the Mayhem serial code, refactor the HackRF driver to be Redis-native (no ROS2 dependency in core), and create a thin ROS2 bridge node that reads IQ from Redis and publishes to ROS2 topics
**Depends on**: Phase 4
**Requirements**: REF-01, REF-02, REF-03, REF-04, REF-05, REF-06, REF-07
**Success Criteria** (what must be TRUE):
  1. `pymayhem` is a standalone pip-installable package that controls the PortaPack via serial without any ROS2 or Redis dependency — `pip install pymayhem && python -c "from pymayhem import MayhemClient"` works
  2. The HackRF driver runs standalone with Redis as its only external interface — no rclpy import in the core driver process
  3. A separate ROS2 bridge node reads IQ from `hackrf:iq:stream` Redis Stream and publishes to `/hackrf/iq` — existing ROS2 subscribers work unchanged
  4. All 68 existing unit tests pass after the refactor (no regression)
**Plans**: 5 plans

Plans:
- [x] 05-01-PLAN.md — Extract pymayhem package: _serial.py, domain modules, MayhemClient, UnsafeMayhemClient, pyproject.toml, 12 tests (REF-01, REF-02, REF-03)
- [x] 05-02-PLAN.md — hackrf_driver scaffold: config.py, move RedisBridge + TXController, decouple TXController from node ref, 36 tests (REF-04, REF-07)
- [x] 05-03-PLAN.md — HackRFDriver main loop: driver.py, cli.py, __main__.py, threading replaces ROS2 timers (REF-04)
- [x] 05-04-PLAN.md — ROS2 bridge node: bridge_node.py reads Redis Pub/Sub, publishes /hackrf/iq and /hackrf/state (REF-05, REF-06)
- [x] 05-05-PLAN.md — Test migration: update test/ imports to new package homes, full 68-test regression check (REF-07)

### Phase 6: Foundation Hardening
**Goal**: All error paths raise typed exceptions with clear semantics, all inputs are validated before touching hardware, the Redis bridge survives a Redis restart, every IQ entry carries a sequence number for gap detection, TX can be dry-run validated without emitting RF, and the antenna confirmation is reachable from ROS2
**Depends on**: Phase 5
**Requirements**: ERR-01, ERR-02, ERR-03, ERR-04, ERR-05, REL-02, REL-03, TXS-01, TXS-02, TXS-03, LEG-01
**Success Criteria** (what must be TRUE):
  1. A pymayhem command failure raises a `MayhemError` subclass that a caller can catch by type — no silent bool returns for error conditions
  2. Passing an out-of-range parameter to hackrf_driver raises `HackRFConfigError` before any hardware state changes
  3. Restarting Redis while the driver is running causes BridgeNode to reconnect automatically and resume publishing `/hackrf/iq` within the backoff window — the topic does not go permanently silent
  4. Every entry in `hackrf:iq:stream` contains a `seq` field; a consumer reading two consecutive entries can detect any dropped buffer by checking for gaps in the sequence number
  5. Calling `validate_tx()` with a valid auth token returns True without consuming the token — a subsequent real TX can still use the same token
  6. A ROS2 service call to `/hackrf/confirm_antenna` sets the Redis antenna confirmation key and is acknowledged before any TX is attempted
**Plans**: 4 plans

Plans:
- [x] 06-01-PLAN.md — Exception hierarchies for pymayhem and hackrf_driver; domain method conversion to raise-on-error (ERR-01, ERR-02, ERR-03)
- [ ] 06-02-PLAN.md — _update_param raises HackRFConfigError; _dispatch_command exception boundary; IQ sequence numbers (ERR-04, ERR-05, REL-03)
- [ ] 06-03-PLAN.md — BridgeNode reconnect loop; antenna confirmation service; legacy deprecation (REL-02, TXS-02, LEG-01)
- [ ] 06-04-PLAN.md — validate_tx() dry-run method; periodic antenna re-read timer (TXS-01, TXS-03)

### Phase 7: Observability & Reliability
**Goal**: Operators can observe live driver health through Redis metrics, failed commands are preserved for forensic review, and the driver self-heals from USB stalls without manual intervention
**Depends on**: Phase 6
**Requirements**: REL-01, OBS-01, OBS-02, OBS-03
**Success Criteria** (what must be TRUE):
  1. `hackrf:metrics` hash updates at 1 Hz with current IQ throughput, error counts, queue depths, and uptime — readable from any Redis client without touching ROS2
  2. A command that fails dispatch (e.g., bad frequency, missing auth) appears in `hackrf:cmd:dlq` with its error context and timestamp, and the DLQ does not grow beyond 500 entries
  3. If the HackRF produces no IQ data for 10 seconds (simulated by blocking the USB path), the watchdog triggers reconnection and IQ flow resumes — all without deadlocking `_device_lock`
  4. `/hackrf/metrics` ROS2 topic publishes the same data as `hackrf:metrics` hash — a ROS2 subscriber can observe driver health without a Redis client
**Plans**: TBD

### Phase 8: Signal Capabilities
**Goal**: The driver can record IQ to standards-compliant SigMF files, publish real-time power spectra to Redis and ROS2, and execute programmable frequency hopping sequences
**Depends on**: Phase 6
**Requirements**: REC-01, REC-02, REC-03, REC-04, FFT-01, FFT-02, FFT-03, FFT-04, HOP-01, HOP-02, HOP-03, HOP-04
**Success Criteria** (what must be TRUE):
  1. A `record_start` Redis command begins writing IQ to a `.sigmf-data` + `.sigmf-meta` file pair; `record_stop` closes the file with correct SigMF metadata (datatype, sample_rate, frequency, datetime) — the resulting file opens in GNU Radio or inspectrum without errors
  2. Sequence number gaps during recording appear as separate `captures` entries in the SigMF metadata with correct `sample_start` offsets — no samples are silently omitted or misattributed
  3. `hackrf:spectrum` stream updates at configurable rate (default 10 Hz, up to 50 Hz) with float32 PSD bins; `hackrf:waterfall` list maintains a rolling 200-row history
  4. `/hackrf/spectrum` ROS2 topic publishes spectrum data matching `hackrf:spectrum` — existing ROS2 visualization nodes can subscribe without Redis
  5. A `hop_start` Redis command with a frequency list and dwell time begins scanning; the hop scheduler never advances while TX is active; `hop_stop` halts scanning and holds the current frequency; current hop state is visible in `hackrf:state`
**Plans**: TBD
**UI hint**: yes

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. RX Pipeline Correctness | 3/3 | Complete | 2026-03-30 |
| 2. Mayhem Serial Interface | 3/3 | Complete | 2026-03-30 |
| 3. Redis Bridge | 2/2 | Complete | 2026-03-30 |
| 4. TX Authorization | 2/2 | Complete | 2026-03-30 |
| 5. PyMayhem Refactor | 5/5 | Complete | 2026-03-30 |
| 6. Foundation Hardening | 1/4 | In Progress|  |
| 7. Observability & Reliability | 0/TBD | Not started | - |
| 8. Signal Capabilities | 0/TBD | Not started | - |
