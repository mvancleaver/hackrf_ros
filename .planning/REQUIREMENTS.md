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

- [ ] **RED-01**: IQ samples published to Redis Stream via XADD with configurable MAXLEN trimming
- [ ] **RED-02**: Device state published to Redis Hash (hackrf:state) with current frequency, gain, sample rate, streaming status
- [ ] **RED-03**: Command interface via Redis subscriber (hackrf:cmd) accepts frequency, gain, sample rate, and bandwidth changes
- [ ] **RED-04**: Redis I/O runs in dedicated daemon thread, never blocking ROS2 executor callbacks
- [ ] **RED-05**: Redis key schema uses hackrf: namespace prefix consistently

### Mayhem Serial Control

- [ ] **MAY-01**: Serial port lifecycle: open /dev/ttyACM1 at startup, close at shutdown, reconnect on disconnect
- [ ] **MAY-02**: applist queried at startup to discover available Mayhem apps (no hardcoded names)
- [ ] **MAY-03**: appstart command switches between Mayhem apps by discovered short name
- [ ] **MAY-04**: setfreq command updates frequency within active app (validates app supports it)
- [ ] **MAY-05**: radioinfo query returns current device configuration for verification
- [ ] **MAY-06**: Mode conflict between pyhackrf2 and serial verified empirically at startup with clear error if incompatible

### TX Control

- [ ] **TX-01**: TX via pyhackrf2 start_tx() with explicit half-duplex RX-to-TX mode switch
- [ ] **TX-02**: Frequency allowlist blocks transmission on restricted bands (cellular, aviation, emergency)
- [ ] **TX-03**: Configurable flag to disable frequency allowlist for authorized testing environments
- [ ] **TX-04**: Antenna confirmation required before any TX operation (explicit user acknowledgment)
- [ ] **TX-05**: One-token-per-TX authorization via Redis GETDEL (no persistent armed state)
- [ ] **TX-06**: TX automatically stopped on node shutdown (stop_tx() in destroy_node)
- [ ] **TX-07**: TX commands routed through Redis command interface with authorization field required

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
| MAY-01 | Phase 2 | Pending |
| MAY-02 | Phase 2 | Pending |
| MAY-03 | Phase 2 | Pending |
| MAY-04 | Phase 2 | Pending |
| MAY-05 | Phase 2 | Pending |
| MAY-06 | Phase 2 | Pending |
| RED-01 | Phase 3 | Pending |
| RED-02 | Phase 3 | Pending |
| RED-03 | Phase 3 | Pending |
| RED-04 | Phase 3 | Pending |
| RED-05 | Phase 3 | Pending |
| TX-01 | Phase 4 | Pending |
| TX-02 | Phase 4 | Pending |
| TX-03 | Phase 4 | Pending |
| TX-04 | Phase 4 | Pending |
| TX-05 | Phase 4 | Pending |
| TX-06 | Phase 4 | Pending |
| TX-07 | Phase 4 | Pending |

**Coverage:**
- v1 requirements: 25 total
- Mapped to phases: 25
- Unmapped: 0

---
*Requirements defined: 2026-03-29*
*Last updated: 2026-03-29 after roadmap creation*
