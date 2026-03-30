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
- [x] **REF-04**: HackRF core driver runs standalone with Redis as only external interface (no rclpy import)
- [x] **REF-05**: ROS2 bridge node reads IQ from `hackrf:iq:stream` and publishes to `/hackrf/iq` topic
- [x] **REF-06**: ROS2 bridge node subscribes to `hackrf:state` and publishes device state to ROS2 topics
- [x] **REF-07**: All 68 existing unit tests pass after refactor (no regression)

## v2 Requirements

Requirements for v2.0: Hardening, Observability & Signal Capabilities.

### Error Handling & Validation

- [x] **ERR-01**: pymayhem raises typed exceptions (MayhemError hierarchy) instead of returning bool on command failures
- [x] **ERR-02**: hackrf_driver raises typed exceptions (HackRFError hierarchy) for config, device, and TX errors
- [x] **ERR-03**: All public pymayhem methods validate input parameters and raise ValueError on out-of-range values
- [ ] **ERR-04**: All hackrf_driver config changes validate against PARAM_RANGES before touching hardware
- [ ] **ERR-05**: Exception dispatch boundary in redis_bridge catches pymayhem/hackrf exceptions and maps to structured Redis error state

### Reliability

- [ ] **REL-01**: Device health watchdog detects USB stall (no RX data for 10s) and triggers automatic reconnect without deadlocking _device_lock
- [ ] **REL-02**: BridgeNode survives Redis restart — exponential backoff retry loop with automatic resubscribe to Pub/Sub channels
- [ ] **REL-03**: Every IQ XADD entry includes a monotonic sequence number; consumers can detect dropped buffers

### Observability

- [ ] **OBS-01**: hackrf:metrics Redis hash publishes IQ throughput (chunks/sec), error counts, queue depths, and uptime at 1 Hz
- [ ] **OBS-02**: Failed Redis commands archived to hackrf:cmd:dlq stream (MAXLEN=500) with error context and timestamp
- [ ] **OBS-03**: BridgeNode publishes /hackrf/metrics ROS2 topic with same data as hackrf:metrics hash

### TX Safety

- [x] **TXS-01**: validate_tx() checks all four TX guards (antenna, hard-block, freq filter, auth existence) without consuming the auth token
- [ ] **TXS-02**: BridgeNode exposes /hackrf/confirm_antenna ROS2 service that sets the Redis antenna confirmation key
- [x] **TXS-03**: TXController periodically re-reads antenna confirmation key (not just at init)

### IQ Recording

- [ ] **REC-01**: Redis record_start command begins recording IQ to a SigMF file (.sigmf-data + .sigmf-meta) in a configurable directory
- [ ] **REC-02**: Redis record_stop command stops recording and finalizes SigMF metadata (datatype, sample_rate, frequency, datetime)
- [ ] **REC-03**: Recording runs in a dedicated thread with its own bounded queue — does not block the IQ pipeline
- [ ] **REC-04**: Sequence number gaps during recording are noted as new SigMF capture entries with correct sample_start offset

### Spectral Analysis

- [ ] **FFT-01**: Headless FFT/PSD computed in a dedicated thread at configurable rate (default 10 Hz, max 50 Hz)
- [ ] **FFT-02**: Power spectrum published to hackrf:spectrum Redis stream with float32 PSD bins
- [ ] **FFT-03**: /hackrf/spectrum ROS2 topic published by BridgeNode via Pub/Sub subscription (mirrors IQ topic pattern)
- [ ] **FFT-04**: Waterfall history maintained in hackrf:waterfall Redis list (rolling 200-row window via LPUSH + LTRIM)

### Frequency Hopping

- [ ] **HOP-01**: Redis hop_start command accepts a frequency list and dwell time (minimum 100ms) and begins programmable scanning
- [ ] **HOP-02**: Redis hop_stop command halts the hop sequence and holds current frequency
- [ ] **HOP-03**: Hop scheduler checks _is_transmitting before each hop — backs off without advancing if TX is active
- [ ] **HOP-04**: Current hop state (active, current_freq, dwell_ms, hop_index) reflected in hackrf:state hash

### Legacy Cleanup

- [ ] **LEG-01**: HackRFNode marked deprecated with docstring and log warning pointing users to hackrf_driver + BridgeNode

## v3 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Advanced Mayhem

- **MAY-10**: POCSAG TX via sendpocsag serial command
- **MAY-11**: Mayhem file replay (SD card staging + appstart Replay)
- **MAY-12**: gotgps serial injection for mobile/APRS use cases

### Architecture

- **ARCH-01**: ROS2 Lifecycle Node migration for managed state transitions
- **ARCH-02**: Redis consumer group support for multiple downstream consumers
- **ARCH-03**: Graceful degradation: continue on ROS2 topics if Redis unreachable

### Async API

- **ASYNC-01**: pymayhem async API (AsyncMayhemClient) with asyncio serial transport
- **ASYNC-02**: Event loop ownership contract for ROS2/asyncio coexistence

## Out of Scope

| Feature | Reason |
|---------|--------|
| Web UI / dashboard | Redis consumers can build their own visualization |
| Signal processing / demodulation | Out of scope for driver layer |
| Multi-device support | Single HackRF One target |
| Custom Mayhem firmware mods | Work with existing Mayhem serial protocol |
| Persistent always-on recording | 160 MB/s fills disk in minutes; trigger-based only |
| Automatic demodulation | Belongs in consumer layer, not driver |
| Frequency hopping during active TX | Half-duplex hardware constraint |
| asyncio port of hackrf_driver core | libusb callbacks are not async-safe |
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
| REF-04 | Phase 5 | Complete |
| REF-05 | Phase 5 | Complete |
| REF-06 | Phase 5 | Complete |
| REF-07 | Phase 5 | Complete |
| ERR-01 | Phase 6 | Complete |
| ERR-02 | Phase 6 | Complete |
| ERR-03 | Phase 6 | Complete |
| ERR-04 | Phase 6 | Pending |
| ERR-05 | Phase 6 | Pending |
| REL-02 | Phase 6 | Pending |
| REL-03 | Phase 6 | Pending |
| TXS-01 | Phase 6 | Complete |
| TXS-02 | Phase 6 | Pending |
| TXS-03 | Phase 6 | Complete |
| LEG-01 | Phase 6 | Pending |
| REL-01 | Phase 7 | Pending |
| OBS-01 | Phase 7 | Pending |
| OBS-02 | Phase 7 | Pending |
| OBS-03 | Phase 7 | Pending |
| REC-01 | Phase 8 | Pending |
| REC-02 | Phase 8 | Pending |
| REC-03 | Phase 8 | Pending |
| REC-04 | Phase 8 | Pending |
| FFT-01 | Phase 8 | Pending |
| FFT-02 | Phase 8 | Pending |
| FFT-03 | Phase 8 | Pending |
| FFT-04 | Phase 8 | Pending |
| HOP-01 | Phase 8 | Pending |
| HOP-02 | Phase 8 | Pending |
| HOP-03 | Phase 8 | Pending |
| HOP-04 | Phase 8 | Pending |

**Coverage:**
- v1 requirements: 32 total (all complete)
- v2 requirements: 27 total (pending)
- Mapped to phases: 32 (v1 complete), 27 (v2 pending — Phases 6, 7, 8)
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-29*
*Last updated: 2026-03-30 — v2.0 roadmap created*
