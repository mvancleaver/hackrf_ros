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

- [x] **ADV-01**: Wideband anomaly detection flags deviations from learned baseline PSD per frequency
- [x] **ADV-02**: Multi-observation emitter localization estimates emitter position from power measurements at multiple robot poses
- [x] **ADV-03**: Cyclostationary feature extraction disambiguates WiFi vs BLE vs ZigBee in 2.4 GHz ISM band

### Hardware Expansion

- [ ] **HW-01**: KrakenSDR direction finding integration publishes AOA estimates as PoseWithCovarianceStamped
- [ ] **HW-02**: Multi-radio architecture supports namespace-separated instances (/hackrf_0/*, /hackrf_1/*)

### Portapack Boot Transition (Phase 5)

Phase 5 minted these IDs during planning. Each maps 1:1 to a CONTEXT.md D-XX decision
or a RESEARCH.md assumption requiring hardware verification. All Pending on creation.

- [ ] **REQ-P5-00**: Scope-decision reversal — this REQUIREMENTS file AND PROJECT.md replace the blanket "Mayhem firmware control out of scope" statement with a bounded one that in-scopes only the single mode-switch command (D-00)
- [ ] **REQ-P5-01**: Udev rule matches Portapack via `ATTRS{idVendor}` + `ATTRS{idProduct}` (VID:PID only) (D-01)
- [ ] **REQ-P5-02**: Udev rule creates stable symlink `/dev/portapack` on the CDC-ACM interface via `SYMLINK+="portapack"` (D-02)
- [ ] **REQ-P5-03**: docker-compose.yaml declares `device_cgroup_rules: ['c 189:* rmw', 'c 166:* rmw']` and bind-mounts `/dev:/dev` (D-03)
- [ ] **REQ-P5-04**: Udev rule is installed on the host filesystem at `/etc/udev/rules.d/99-portapack.rules` via a deployment script (NOT baked into the container image) (D-04)
- [ ] **REQ-P5-05**: Serial handshake opens `/dev/portapack` with pyserial at 115200 8N1 and writes `b'hackrf\n'` as the mode-switch command (D-05)
- [ ] **REQ-P5-06**: Transition sequence is open→write(`hackrf\n`)→close→poll `pyhackrf2.HackRF.enumerate()` until a HackRF appears or timeout elapses→open HackRF (D-06)
- [ ] **REQ-P5-07**: When `/dev/portapack` is absent or `portapack_enable_transition=False`, the helper returns SKIPPED and proceeds directly to `pyhackrf2.HackRF(...)` (D-07)
- [ ] **REQ-P5-08**: When `serial.Serial(...)` raises on an existing symlink (stale symlink case), the helper logs ERROR and returns SKIPPED (fall-through, not FAILED) (D-08)
- [ ] **REQ-P5-09**: When the first re-enumeration attempt times out, the helper resends the hackrf command and polls once more; if still absent, returns FAILED and `on_configure` returns `TransitionCallbackReturn.FAILURE` (D-09)
- [ ] **REQ-P5-10**: `on_configure` retries `pyhackrf2.HackRF(...)` up to `portapack_open_retries` times with `PORTAPACK_OPEN_RETRY_DELAY_S` (0.25 s) spacing to absorb the USB kernel-claim race (D-10)
- [ ] **REQ-P5-11**: `_diagnostics_callback` publishes the `last_portapack_transition` field with one of `skipped`, `succeeded`, `retried`, `failed`; state is written on every configure before returning (D-11)
- [ ] **REQ-P5-12**: Four new ROS2 parameters declared: `portapack_serial_device` (str, default `/dev/portapack`), `portapack_enable_transition` (bool, default True), `portapack_reenum_timeout_s` (float, default 5.0), `portapack_open_retries` (int, default 3) (D-12)
- [ ] **REQ-P5-13**: Defaults for those parameters exist as module constants at the top of `hackrf_lifecycle_node.py` (D-13)
- [ ] **REQ-P5-14**: None of the portapack_* parameters are dynamic; they are read once in `on_configure` and a mid-run change has no hardware effect (D-14)
- [ ] **REQ-P5-15**: The diagnostics field values are exactly `skipped`, `succeeded`, `retried`, `failed` (enum-driven; format guarantee for downstream autonomy observers) (D-15)
- [ ] **REQ-P5-16**: `_transition_portapack()` is called in `on_configure` between `self._declare_parameters()` and `pyhackrf2.HackRF(...)`, returns a `PortapackTransitionResult` enum, and never raises (D-16, Pitfall 2)
- [ ] **REQ-P5-A1**: Portapack CDC-ACM VID:PID verified against live hardware (assumed `1d50:6018`, confirmed via `udevadm info -a -n /dev/ttyACM*` during deployment) (A1)
- [ ] **REQ-P5-A3**: Post-open DTR/RTS settle of at least 50 ms is empirically sufficient (first bytes after `serial.Serial(...)` open not dropped), or `PORTAPACK_DTR_SETTLE_S` is raised to 100 ms (A3)

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
| Mayhem mode-switch command (exit Mayhem UI → HackRF USB-SDR) | IN SCOPE since Phase 5 (Portapack Boot Transition). Bounded reversal of prior decision per CONTEXT.md D-00. See REQ-P5-00. |
| All other Mayhem firmware control (UI nav, DFU, file transfer, TX apps) | Separate package concern (pymayhem). Phase 5 narrowly excepts the mode-switch only. |

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
| REQ-P5-00 | Phase 5 | Portapack Boot Transition | Pending |
| REQ-P5-01 | Phase 5 | Portapack Boot Transition | Pending |
| REQ-P5-02 | Phase 5 | Portapack Boot Transition | Pending |
| REQ-P5-03 | Phase 5 | Portapack Boot Transition | Pending |
| REQ-P5-04 | Phase 5 | Portapack Boot Transition | Pending |
| REQ-P5-05 | Phase 5 | Portapack Boot Transition | Pending |
| REQ-P5-06 | Phase 5 | Portapack Boot Transition | Pending |
| REQ-P5-07 | Phase 5 | Portapack Boot Transition | Pending |
| REQ-P5-08 | Phase 5 | Portapack Boot Transition | Pending |
| REQ-P5-09 | Phase 5 | Portapack Boot Transition | Pending |
| REQ-P5-10 | Phase 5 | Portapack Boot Transition | Pending |
| REQ-P5-11 | Phase 5 | Portapack Boot Transition | Pending |
| REQ-P5-12 | Phase 5 | Portapack Boot Transition | Pending |
| REQ-P5-13 | Phase 5 | Portapack Boot Transition | Pending |
| REQ-P5-14 | Phase 5 | Portapack Boot Transition | Pending |
| REQ-P5-15 | Phase 5 | Portapack Boot Transition | Pending |
| REQ-P5-16 | Phase 5 | Portapack Boot Transition | Pending |
| REQ-P5-A1 | Phase 5 | Portapack Boot Transition | Pending |
| REQ-P5-A3 | Phase 5 | Portapack Boot Transition | Pending |

**Coverage:**
- v1 requirements: 51 total (32 original + 19 Phase 5)
- Mapped to phases: 51
- Unmapped: 0

**Phase distribution:**
- Phase 1 (Sensor Foundations): 14 requirements (MSG-01..06, DET-01..06, PERF-01..02)
- Phase 2 (Robot Autonomy Integration): 10 requirements (SWP-01..03, REC-01..04, MAP-01..03)
- Phase 3 (Reliability): 3 requirements (REL-01..03)
- Phase 4 (Advanced Signal Intelligence): 5 requirements (ADV-01..03, HW-01..02)
- Phase 5 (Portapack Boot Transition): 19 requirements (REQ-P5-00..16, REQ-P5-A1, REQ-P5-A3)

---
*Requirements defined: 2026-04-13*
*Last updated: 2026-04-18 after Phase 5 requirement mint (REQ-P5-00..16 + REQ-P5-A1, A3)*
