# Requirements: HackRF ROS2 RF Sensor

**Defined:** 2026-04-13
**Core Value:** Reliable, calibrated RF spectrum awareness published as standard ROS2 messages for robot autonomy

## v1 Requirements

### Messages & Integration

- [x] **MSG-01**: PSD published as SpectrumStamped.msg with header.stamp and frame_id
- [x] **MSG-02**: Static TF transform published for configurable antenna frame (base_link -> hackrf_antenna)
- [x] **MSG-03**: RF detections published as RFDetectionArray.msg with noise floor, per-signal freq/BW/power/SNR/classification/ID
- [x] **MSG-04**: RF environment summary published at 1 Hz with emitter count, band occupancy, strongest signal
- [x] **MSG-05**: All streaming topics use BEST_EFFORT QoS (no back-pressure on pipeline)
- [x] **MSG-06**: Executor upgraded to MultiThreadedExecutor with ReentrantCallbackGroup

### Detection & Classification

- [x] **DET-01**: CA-CFAR energy detector operates on PSD array with configurable guard cells, reference cells, and Pfa
- [x] **DET-02**: Adjacent detected bins grouped into signal detections with centroid frequency, 3 dB bandwidth, and integrated power
- [x] **DET-03**: Persistence tracker confirms detections across N consecutive frames before publishing (configurable N)
- [x] **DET-04**: Each detection assigned a stable ID that persists across frames via frequency-proximity matching
- [x] **DET-05**: Band classification assigns label to each detection based on center frequency + bandwidth lookup table
- [x] **DET-06**: Classification covers at minimum: WiFi, Bluetooth/BLE, ZigBee, LTE, ISM narrowband, unknown

### Sweep & Survey

- [x] **SWP-01**: Sweep implemented as ROS2 action server with per-hop progress feedback
- [x] **SWP-02**: Sweep action supports cancel (abort mid-sweep, restore original frequency)
- [x] **SWP-03**: Sweep returns stitched composite PSD with Tukey-blended hop boundaries in linear domain

### IQ Recording

- [x] **REC-01**: IQ recording implemented as ROS2 action server with start/stop and progress feedback
- [x] **REC-02**: IQ written in SigMF format (ci8 datatype, JSON metadata sidecar)
- [x] **REC-03**: Recording metadata includes robot pose from TF at capture start, all SDR parameters, trigger reason
- [x] **REC-04**: Recorder uses dedicated write thread with bounded queue (no IQ drops from disk stalls)

### RF Mapping

- [x] **MAP-01**: RF occupancy grid published as nav_msgs/OccupancyGrid from detections + robot odometry
- [x] **MAP-02**: Occupancy grid integrates signal power observations by spatial cell
- [x] **MAP-03**: Grid resolution and update rate configurable via parameters

### Reliability

- [ ] **REL-01**: Automatic gain control adjusts LNA/VGA based on ADC clip rate feedback
- [ ] **REL-02**: Parameter callback retune offloaded to background thread (no executor blocking)
- [ ] **REL-03**: USB disconnect detected and reported via diagnostics (WARN/ERROR state)

### Performance

- [x] **PERF-01**: FFT uses scipy.fft with ARM NEON SIMD (replace numpy.fft)
- [x] **PERF-02**: PSD publishing maintains 10 Hz update rate on Jetson ARM64

### Advanced Processing

- [ ] **ADV-01**: Wideband anomaly detection flags deviations from learned baseline PSD per frequency
- [ ] **ADV-02**: Multi-observation emitter localization estimates emitter position from power measurements at multiple robot poses
- [x] **ADV-03**: Cyclostationary feature extraction disambiguates WiFi vs BLE vs ZigBee in 2.4 GHz ISM band

### Hardware Expansion

- [ ] **HW-01**: KrakenSDR direction finding integration publishes AOA estimates as PoseWithCovarianceStamped
- [ ] **HW-02**: Multi-radio architecture supports namespace-separated instances (/hackrf_0/*, /hackrf_1/*)

## v2 Requirements

### Advanced Classification
- **CLS-01**: ML-based automatic modulation classification for fine-grained signal identification
- **CLS-02**: Protocol-level demodulation for WiFi/BLE beacon parsing

### Multi-Platform
- **PLT-01**: ARM32 support (Raspberry Pi 4)
- **PLT-02**: x86_64 desktop development without Docker

## Out of Scope

| Feature | Reason |
|---------|--------|
| TX transmission | Safety concern, removed during rescope |
| Protocol demodulation | High complexity, low ROI for robot autonomy |
| ML-based AMC | Heuristics sufficient for coarse classification; ML adds training/inference cost |
| DSSS/FHSS signal tracking | 43 dB processing gain loss makes energy detection ineffective |
| Redis IQ streaming | Replaced by ROS2 topics and SigMF recording |
| GUI applications in core | Display nodes are optional downstream subscribers |
| Real-time signal decode | Not needed for spectrum awareness use case |
| Mayhem firmware control | Separate package concern (pymayhem) |

## Traceability

| Requirement | Phase | Phase Name | Status |
|-------------|-------|------------|--------|
| MSG-01 | Phase 1 | Sensor Foundations | Pending |
| MSG-02 | Phase 1 | Sensor Foundations | Pending |
| MSG-03 | Phase 1 | Sensor Foundations | Pending |
| MSG-04 | Phase 1 | Sensor Foundations | Pending |
| MSG-05 | Phase 1 | Sensor Foundations | Pending |
| MSG-06 | Phase 1 | Sensor Foundations | Pending |
| DET-01 | Phase 1 | Sensor Foundations | Pending |
| DET-02 | Phase 1 | Sensor Foundations | Pending |
| DET-03 | Phase 1 | Sensor Foundations | Pending |
| DET-04 | Phase 1 | Sensor Foundations | Pending |
| DET-05 | Phase 1 | Sensor Foundations | Pending |
| DET-06 | Phase 1 | Sensor Foundations | Pending |
| PERF-01 | Phase 1 | Sensor Foundations | Pending |
| PERF-02 | Phase 1 | Sensor Foundations | Pending |
| SWP-01 | Phase 2 | Robot Autonomy Integration | Pending |
| SWP-02 | Phase 2 | Robot Autonomy Integration | Pending |
| SWP-03 | Phase 2 | Robot Autonomy Integration | Pending |
| REC-01 | Phase 2 | Robot Autonomy Integration | Pending |
| REC-02 | Phase 2 | Robot Autonomy Integration | Pending |
| REC-03 | Phase 2 | Robot Autonomy Integration | Pending |
| REC-04 | Phase 2 | Robot Autonomy Integration | Pending |
| MAP-01 | Phase 2 | Robot Autonomy Integration | Pending |
| MAP-02 | Phase 2 | Robot Autonomy Integration | Pending |
| MAP-03 | Phase 2 | Robot Autonomy Integration | Pending |
| REL-01 | Phase 3 | Reliability | Pending |
| REL-02 | Phase 3 | Reliability | Pending |
| REL-03 | Phase 3 | Reliability | Pending |
| ADV-01 | Phase 4 | Advanced Signal Intelligence | Pending |
| ADV-02 | Phase 4 | Advanced Signal Intelligence | Pending |
| ADV-03 | Phase 4 | Advanced Signal Intelligence | Pending |
| HW-01 | Phase 4 | Advanced Signal Intelligence | Pending |
| HW-02 | Phase 4 | Advanced Signal Intelligence | Pending |

**Coverage:**
- v1 requirements: 32 total
- Mapped to phases: 32
- Unmapped: 0

**Phase distribution:**
- Phase 1 (Sensor Foundations): 14 requirements (MSG-01..06, DET-01..06, PERF-01..02)
- Phase 2 (Robot Autonomy Integration): 10 requirements (SWP-01..03, REC-01..04, MAP-01..03)
- Phase 3 (Reliability): 3 requirements (REL-01..03)
- Phase 4 (Advanced Signal Intelligence): 5 requirements (ADV-01..03, HW-01..02)

---
*Requirements defined: 2026-04-13*
*Last updated: 2026-04-13 after roadmap creation — traceability finalized*
