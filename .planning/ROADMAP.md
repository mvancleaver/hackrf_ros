# Roadmap: HackRF ROS2 Driver

## Overview

This milestone transforms a working but fragile ROS2 HackRF prototype into a production-grade driver. The work is ordered by dependency and risk: correctness bugs in the RX pipeline are fixed first (foundation for everything else), then Mayhem serial control is built and empirically tested (resolves the mode-conflict question before integration), then Redis streaming is layered on (uses the Phase 1 queue refactor, informed by Phase 2 mode answer), and finally TX authorization is added last (highest hardware and legal risk, requires all prior phases stable).

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: RX Pipeline Correctness** - Fix thread-safety bugs and add error recovery to the existing IQ pipeline
- [ ] **Phase 2: Mayhem Serial Interface** - Build serial control for Mayhem firmware and resolve the mode-conflict question
- [x] **Phase 3: Redis Bridge** - Stream IQ data and device state to Redis; accept control commands via Redis (completed 2026-03-30)
- [x] **Phase 4: TX Authorization** - Add safe, authorized transmission with frequency allowlist and hardware guardrails (completed 2026-03-30)
- [ ] **Phase 5: PyMayhem Refactor** - Extract standalone pymayhem package, Redis-native driver, ROS2 bridge plugin

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

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. RX Pipeline Correctness | 3/3 | Complete | 2026-03-30 |
| 2. Mayhem Serial Interface | 3/3 | Complete | 2026-03-30 |
| 3. Redis Bridge | 2/2 | Complete | 2026-03-30 |
| 4. TX Authorization | 2/2 | Complete | 2026-03-30 |
| 5. PyMayhem Refactor | 3/5 | In Progress|  |

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
