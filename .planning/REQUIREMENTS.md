# Requirements: HackRF ROS2 Driver

**Defined:** 2026-03-29
**Core Value:** Reliable, safe bidirectional SDR control with IQ data streaming to Redis and TX operations gated behind explicit authorization.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### RX Pipeline

- [x] **RX-01**: IQ sample buffer uses thread-safe queue (queue.Queue) replacing shared numpy array
- [x] **RX-02**: USB error recovery with exponential backoff reconnection when device disconnects
- [x] **RX-03**: Parameter validation enforces hardware ranges (freq: 1 MHz-6 GHz, LNA gain: 0-40 dB, VGA gain: 0-62 dB, sample rate: 2-20 MSPS)
- [x] **RX-04**: Class renamed from HackRFPuiblisherNode to HackRFNode with structured logging (no bare print statements)
- [x] **RX-05**: stop_rx() deadlock mitigated with timeout guard during parameter reconfiguration
- [x] **RX-06**: Clean lifecycle management: startup initializes device, shutdown stops streaming and closes device
- [x] **RX-07**: RX callback stripped to bare enqueue operation to minimize GIL contention

### Redis Integration

- [x] **RED-01**: IQ samples published to Redis Stream via XADD with configurable MAXLEN trimming
- [x] **RED-02**: Device state published to Redis Hash (hackrf:state) with current frequency, gain, sample rate, streaming status
- [x] **RED-03**: Command interface via Redis subscriber (hackrf:cmd) accepts frequency, gain, sample rate, and bandwidth changes
- [x] **RED-04**: Redis I/O runs in dedicated daemon thread, never blocking ROS2 executor callbacks
- [x] **RED-05**: Redis key schema uses hackrf: namespace prefix consistently

### Mayhem Serial Control

- [x] **MAY-01**: Serial port lifecycle: open /dev/ttyACM1 at startup, close at shutdown, reconnect on disconnect
- [x] **MAY-02**: applist queried at startup to discover available Mayhem apps (no hardcoded names)
- [x] **MAY-03**: appstart command switches between Mayhem apps by discovered short name
- [x] **MAY-04**: setfreq command updates frequency within active app (validates app supports it)
- [x] **MAY-05**: radioinfo query returns current device configuration for verification
- [x] **MAY-06**: Mode conflict between pyhackrf2 and serial verified empirically at startup with clear error if incompatible

### TX Control

- [x] **TX-01**: TX via pyhackrf2 start_tx() with explicit half-duplex RX-to-TX mode switch
- [x] **TX-02**: Frequency allowlist blocks transmission on restricted bands (cellular, aviation, emergency)
- [x] **TX-03**: Configurable flag to disable frequency allowlist for authorized testing environments
- [x] **TX-04**: Antenna confirmation required before any TX operation (explicit user acknowledgment)
- [x] **TX-05**: One-token-per-TX authorization via Redis GETDEL (no persistent armed state)
- [x] **TX-06**: TX automatically stopped on node shutdown (stop_tx() in destroy_node)
- [x] **TX-07**: TX commands routed through Redis command interface with authorization field required

### Refactor

- [x] **REF-01**: `pymayhem` is a standalone pip-installable Python package with no ROS2 or Redis dependencies
- [x] **REF-02**: `pymayhem` exposes all 47 Mayhem serial commands organized by domain (radio, ui, filesystem, sensors, system)
- [x] **REF-03**: `pymayhem` handles `appstart` USB reset with automatic reconnection
- [ ] **REF-04**: HackRF core driver runs standalone with Redis as only external interface (no rclpy import)
- [ ] **REF-05**: ROS2 bridge node reads IQ from `hackrf:iq:stream` and publishes to `/hackrf/iq` topic
- [ ] **REF-06**: ROS2 bridge node subscribes to `hackrf:state` and publishes device state to ROS2 topics
- [ ] **REF-07**: All 68 existing unit tests pass after refactor (no regression)

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Advanced Mayhem

- **MAY-10**: POCSAG TX via sendpocsag serial command
- **MAY-11**: Mayhem file replay (SD card staging + appstart Replay)
- **MAY-12**: gotgps serial injection for mobile/APRS use cases

### Architecture

- **ARCH-01**: ROS2 Lifecycle Node migration for managed state transitions
- **ARCH-02**: Redis consumer group support for multiple downstream consumers
- **ARCH-03**: Graceful degradation: continue on ROS2 topics if Redis unreachable

### Observability

- **OBS-01**: Redis-based metrics (sample rate, buffer depth, drop count)
- **OBS-02**: Health check endpoint via Redis key with TTL heartbeat

## Out of Scope

| Feature | Reason |
|---------|--------|
| Web UI / dashboard | Redis consumers can build their own visualization |
| Signal processing / demodulation | Out of scope for driver layer |
| Multi-device support | Single HackRF One target |
| Custom Mayhem firmware mods | Work with existing Mayhem serial protocol |
| pyserial-asyncio | Driver uses threads, not asyncio event loop |
| OAuth / web auth for TX | Redis GETDEL token is sufficient for single-user driver |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| RX-01 | Phase 1 | Complete |
| RX-02 | Phase 1 | Complete |
| RX-03 | Phase 1 | Complete |
| RX-04 | Phase 1 | Complete |
| RX-05 | Phase 1 | Complete |
| RX-06 | Phase 1 | Complete |
| RX-07 | Phase 1 | Complete |
| MAY-01 | Phase 2 | Complete |
| MAY-02 | Phase 2 | Complete |
| MAY-03 | Phase 2 | Complete |
| MAY-04 | Phase 2 | Complete |
| MAY-05 | Phase 2 | Complete |
| MAY-06 | Phase 2 | Complete |
| RED-01 | Phase 3 | Complete |
| RED-02 | Phase 3 | Complete |
| RED-03 | Phase 3 | Complete |
| RED-04 | Phase 3 | Complete |
| RED-05 | Phase 3 | Complete |
| TX-01 | Phase 4 | Complete |
| TX-02 | Phase 4 | Complete |
| TX-03 | Phase 4 | Complete |
| TX-04 | Phase 4 | Complete |
| TX-05 | Phase 4 | Complete |
| TX-06 | Phase 4 | Complete |
| TX-07 | Phase 4 | Complete |
| REF-01 | Phase 5 | Complete |
| REF-02 | Phase 5 | Complete |
| REF-03 | Phase 5 | Complete |
| REF-04 | Phase 5 | Pending |
| REF-05 | Phase 5 | Pending |
| REF-06 | Phase 5 | Pending |
| REF-07 | Phase 5 | Pending |

**Coverage:**
- v1 requirements: 25 total (all complete)
- Refactor requirements: 7 total (Phase 5)
- Mapped to phases: 32
- Unmapped: 0

---
*Requirements defined: 2026-03-29*
*Last updated: 2026-03-30 after Phase 5 addition*
