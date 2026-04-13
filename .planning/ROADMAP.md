# Roadmap: HackRF ROS2 RF Sensor

## Overview

Starting from an existing lifecycle driver with calibrated PSD publishing, this roadmap builds four sequential capabilities. Phase 1 adds the sensor intelligence layer — stamped messages, TF, CFAR detection, band classification, and FFT performance — converting raw spectrum data into actionable RFDetection messages any robot can consume. Phase 2 wires that intelligence into robot autonomy via action servers for sweep and IQ recording, plus an RF occupancy grid for nav stack integration. Phase 3 hardens the runtime — AGC, async parameter handling, and USB disconnect recovery — before introducing advanced processing in Phase 4: wideband anomaly detection, emitter localization, cyclostationary feature extraction, and multi-radio architecture.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Sensor Foundations** - Add stamped messages, TF, CFAR detection + classification, and FFT performance to make the driver a complete RF sensor
- [ ] **Phase 2: Robot Autonomy Integration** - Action servers for sweep and IQ recording, plus RF occupancy grid for nav stack
- [ ] **Phase 3: Reliability** - AGC, async parameter retuning, and USB disconnect recovery to harden the runtime
- [ ] **Phase 4: Advanced Signal Intelligence** - Wideband anomaly detection, emitter localization, cyclostationary features, and multi-radio architecture

## Phase Details

### Phase 1: Sensor Foundations
**Goal**: Any robot node can subscribe to calibrated, timestamped RF detections with stable IDs, band labels, and TF frame metadata
**Depends on**: Nothing (first phase)
**Requirements**: MSG-01, MSG-02, MSG-03, MSG-04, MSG-05, MSG-06, DET-01, DET-02, DET-03, DET-04, DET-05, DET-06, PERF-01, PERF-02
**Success Criteria** (what must be TRUE):
  1. A subscriber to `/hackrf/spectrum` receives `SpectrumStamped` messages with valid `header.stamp` and `header.frame_id` matching the configured antenna frame
  2. `ros2 run tf2_tools view_frames` shows a static `base_link -> hackrf_antenna` transform in the TF tree
  3. A subscriber to `/hackrf/detections` receives `RFDetectionArray` messages where each detection carries centroid frequency, 3 dB bandwidth, integrated power, SNR, and a band label (e.g., WiFi, BLE, ZigBee, LTE, ISM, unknown)
  4. Detections on `/hackrf/detections` have stable IDs across consecutive frames — the same emitter receives the same ID until it disappears
  5. PSD publishing sustains 10 Hz on the Jetson ARM64 target (measured via `ros2 topic hz /hackrf/spectrum`)
**Plans**: 4 plans
Plans:
- [ ] 01-01-PLAN.md — Define 4 custom message types (SpectrumStamped, RFDetection, RFDetectionArray, RFEnvironment) and register in hackrf_interfaces
- [ ] 01-02-PLAN.md — Upgrade driver: scipy.fft, SpectrumStamped publish, TF broadcaster, BEST_EFFORT QoS, MultiThreadedExecutor
- [ ] 01-03-PLAN.md — Create cfar_node.py: CA-CFAR detection, persistence tracking, band classification, RFDetectionArray/RFEnvironment publish
- [ ] 01-04-PLAN.md — Create sensor_pipeline.launch.py and verify complete pipeline with hardware checkpoint
**UI hint**: no

### Phase 2: Robot Autonomy Integration
**Goal**: A robot autonomy stack can trigger wideband sweeps, record IQ files with pose metadata, and consume an RF occupancy grid for navigation
**Depends on**: Phase 1
**Requirements**: SWP-01, SWP-02, SWP-03, REC-01, REC-02, REC-03, REC-04, MAP-01, MAP-02, MAP-03
**Success Criteria** (what must be TRUE):
  1. Calling the sweep action server returns per-hop progress feedback and a stitched composite PSD; sending a cancel goal aborts the sweep and restores the original center frequency
  2. Calling the IQ recording action server produces a valid SigMF file pair (`.sigmf-data` + `.sigmf-meta`) containing robot pose from TF at capture start and all SDR parameters
  3. IQ recording does not drop samples under sustained write load — the bounded write-thread queue absorbs disk stalls without blocking the ROS executor
  4. A nav_msgs/OccupancyGrid topic is published with RF power observations spatially integrated by grid cell, at a configurable resolution and update rate
**Plans**: TBD
**UI hint**: no

### Phase 3: Reliability
**Goal**: The driver self-corrects gain, never blocks the executor on parameter changes, and surfaces USB hardware faults through diagnostics
**Depends on**: Phase 2
**Requirements**: REL-01, REL-02, REL-03
**Success Criteria** (what must be TRUE):
  1. When ADC clip rate exceeds threshold, LNA/VGA gains are automatically reduced within one feedback cycle — observable via the `/diagnostics` topic showing gain adjustment events
  2. A `ros2 param set` command on a streaming node completes in under 100 ms and does not stall PSD publishing (verifiable via `ros2 topic hz` remaining stable during the set)
  3. Physically unplugging the HackRF while streaming causes the `/diagnostics` topic to transition to WARN then ERROR state within 2 seconds, without crashing the node
**Plans**: TBD
**UI hint**: no

### Phase 4: Advanced Signal Intelligence
**Goal**: The system detects RF environment anomalies, estimates emitter positions, disambiguates co-channel signals, and supports multiple simultaneous SDR instances
**Depends on**: Phase 3
**Requirements**: ADV-01, ADV-02, ADV-03, HW-01, HW-02
**Success Criteria** (what must be TRUE):
  1. A new strong signal appearing in a frequency band that was idle in the learned baseline causes an anomaly flag on the detections topic within one update cycle
  2. After collecting power measurements at three or more distinct robot poses, the emitter localization node publishes a `PoseWithCovarianceStamped` estimate at the RF source's approximate location
  3. In a 2.4 GHz ISM environment with simultaneous WiFi and BLE transmitters, cyclostationary feature extraction correctly labels each detection with the appropriate modulation class
  4. Two HackRF instances launched under `/hackrf_0/*` and `/hackrf_1/*` namespaces publish independent `SpectrumStamped` streams without topic collision
**Plans**: TBD
**UI hint**: no

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Sensor Foundations | 0/4 | In progress | - |
| 2. Robot Autonomy Integration | 0/? | Not started | - |
| 3. Reliability | 0/? | Not started | - |
| 4. Advanced Signal Intelligence | 0/? | Not started | - |
