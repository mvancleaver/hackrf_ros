# Phase 1: Sensor Foundations - Context

**Gathered:** 2026-04-13
**Status:** Ready for planning

<domain>
## Phase Boundary

Convert the existing PSD-publishing lifecycle driver into a complete RF sensor. Add stamped messages with TF frame, CFAR energy detection with persistence tracking, band classification, and upgrade FFT performance. After this phase, any ROS2 node can subscribe to calibrated, timestamped RF detections with stable IDs and band labels.

</domain>

<decisions>
## Implementation Decisions

### CFAR Detection Strategy
- **D-01:** Pfa and persistence are exposed as ROS2 parameters. Default Pfa=1e-4, persistence=3 frames. Robot behavior planner can switch between conservative (fewer false alarms) and sensitive (more detections) at runtime.
- **D-02:** Adaptive guard cells — start with small guard (8 cells), then after initial bin grouping, re-estimate noise excluding the full detected signal width. Two-pass approach handles both narrowband and wideband signals.
- **D-03:** Reference cells: 32 per side (64 total). CA-CFAR sliding window. Threshold factor derived from Pfa via chi-squared inverse CDF (scipy.stats.chi2.ppf).

### Detection Message Design
- **D-04:** Rich detection messages — each RFDetection carries: center_frequency_hz, bandwidth_hz, power_dbm, snr_db, classification (string), detection_id (uint32), persistence_frames (uint32), confidence (float32).
- **D-05:** RFDetectionArray message includes header (stamp + frame_id), noise_floor_dbm, and array of RFDetection.
- **D-06:** 1 Hz /hackrf/rf_environment summary topic — emitter count, band occupancy percentage, noise floor, strongest signal info. Cheap for behavior planner to poll.

### TF Frame Convention
- **D-07:** frame_id is a ROS2 parameter (default 'hackrf_antenna'). Parent frame also parameterized (default 'base_link'). Both configurable for any platform.
- **D-08:** Static transform only — published once via StaticTransformBroadcaster in the driver node. Transform values (xyz, rpy) configurable via parameters with zero defaults.
- **D-09:** All stamped messages use the configured frame_id. SpectrumStamped, RFDetectionArray, and RFEnvironment all carry header.stamp at IQ capture time and header.frame_id matching the antenna frame.

### Band Classification
- **D-10:** Common bands lookup table (~30 entries): WiFi 2.4/5, Bluetooth/BLE, ZigBee, LTE bands 2/4/7/12/13/66, ISM 900/2400/5800, FM radio, GPS. Covers what a robot typically encounters.
- **D-11:** Bandwidth heuristic for overlapping bands: 20 MHz wide = WiFi, 1-2 MHz = BLE/ZigBee, <200 kHz = narrowband ISM. Simple, fast, ~90% correct. Phase 4 cyclostationary features handle the remaining 10%.

### Performance
- **D-12:** Replace all numpy.fft calls with scipy.fft (drop-in, ARM NEON SIMD). Add scipy>=1.11 to Dockerfile.
- **D-13:** Upgrade main() to MultiThreadedExecutor with ReentrantCallbackGroup. Hard prerequisite for Phase 2 action servers.

### QoS
- **D-14:** All streaming topics (spectrum, detections) use BEST_EFFORT QoS. /rf_environment summary uses RELIABLE at 1 Hz.

### Claude's Discretion
- Exact CFAR sliding window implementation (scipy.ndimage.uniform_filter1d vs manual loop)
- Signal bin grouping algorithm (connected-component labeling approach)
- Detection ID assignment strategy (frequency-proximity matching between frames)
- SpectrumStamped.msg field naming conventions

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Signal Processing & Detection Theory
- `docs/rf-ew-review.md` — PSD normalization, averaging, DC/IQ correction (all fixes already applied)
- `docs/rf-sensor-roadmap.md` — Full feature roadmap with architecture details
- `/mnt/NAS/vault/concepts/detection-theory-np-and-cfar.md` — NP lemma, CFAR theory, detector decision tree
- `/mnt/NAS/vault/projects/radar-ew-matlab-simulink/concepts/target-detection-modeling.md` — CA/GO/SO/OS-CFAR implementations, guard cells, Pd/Pfa formulas

### Architecture & Threading
- `/mnt/NAS/vault/concepts/sdr-iq-pipeline-threading.md` — Bounded queue per sink rule
- `.planning/research/ARCHITECTURE.md` — Node separation, message design, TF conventions

### Stack
- `.planning/research/STACK.md` — scipy.fft, SigMF, CFAR implementation approach
- `.planning/research/PITFALLS.md` — Lifecycle publisher bug, executor blocking, CFAR miscalibration

### Existing Code
- `hackrf_ros/hackrf_lifecycle_node.py` — Current driver (all changes build on this)
- `hackrf_interfaces/srv/Sweep.srv` — Existing service definition (new msgs go in same package)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `_fft_frame()` method: Blackman window, DC removal, IQ correction, proper PSD normalization — reuse for CFAR input
- `_process_raw()` method: 50% overlapping FFT frames — provides the PSD the CFAR operates on
- `_handle_sweep()`: Sweep stitching with Tukey blending — sweep service stays as-is, action conversion is Phase 2
- `hackrf_interfaces` CMake package: Already builds Sweep.srv — add new .msg definitions here

### Established Patterns
- Timer callback at 50ms drains IQ queue, computes PSD, publishes
- Regular publishers (not lifecycle) due to CycloneDDS ARM64 bug
- Device lock (threading.RLock) for hardware access
- ADC clip detection in rx_callback

### Integration Points
- CFAR node subscribes to `/hackrf/spectrum` (the SpectrumStamped upgrade of current `/hackrf/psd`)
- New messages added to `hackrf_interfaces/msg/` and `CMakeLists.txt`
- TF broadcaster added in `on_configure()` alongside existing publishers
- Executor upgrade in `main()` function

</code_context>

<specifics>
## Specific Ideas

- CFAR parameters should be tunable at runtime so the robot's behavior planner can switch detection sensitivity
- Two-pass adaptive guard approach for handling both WiFi (4000 bins wide) and narrowband signals
- Band classification uses simple frequency + bandwidth lookup — no ML, no cyclostationary features (that's Phase 4)
- All message timestamps should reflect IQ capture time, not processing time

</specifics>

<deferred>
## Deferred Ideas

- Sweep action server (Phase 2) — existing service works for now
- IQ recording (Phase 2) — needs SigMF library and dedicated write thread
- RF occupancy grid (Phase 2) — needs detection pipeline from this phase first
- AGC loop (Phase 3) — clip detection exists, feedback loop deferred
- Protocol-level classification (Phase 4) — cyclostationary features

</deferred>

---

*Phase: 01-sensor-foundations*
*Context gathered: 2026-04-13*
