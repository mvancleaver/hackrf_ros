# Phase 4: Advanced Signal Intelligence - Context

**Gathered:** 2026-04-13
**Status:** Ready for planning

<domain>
## Phase Boundary

Add four advanced capabilities to the RF sensor stack. Anomaly detection flags deviations
from a learned per-bin baseline. Emitter localization estimates RF source positions from
multi-pose RSSI observations. Cyclostationary feature extraction disambiguates WiFi, BLE,
and ZigBee in the congested 2.4 GHz ISM band. Multi-radio namespace support lets two
HackRF instances run side-by-side without topic collision. KrakenSDR AOA (HW-01) is
deferred — no hardware available.

**Requirements in scope:** ADV-01, ADV-02, ADV-03, HW-02
**Deferred:** HW-01 (KrakenSDR direction finding)

</domain>

<decisions>
## Implementation Decisions

### Anomaly Detection (ADV-01)
- **D-01:** Baseline learning strategy — rolling EMA per frequency bin. Continuously
  update the per-bin baseline with an exponential moving average. Adapts to environmental
  drift over time (e.g., WiFi router turning on permanently). EMA alpha is a ROS2
  parameter (default: 0.05 — slow adaptation, ~20 PSD frames to converge).
- **D-02:** Dual anomaly trigger — flag an anomaly when EITHER condition is true:
  (a) signal power exceeds baseline by configurable threshold (default: +10 dB),
  OR (b) a detection appears in a frequency band that had zero detections in the
  baseline window (new emitter in idle band). Both conditions are reported separately
  in the anomaly flag so downstream nodes know which kind of anomaly occurred.
- **D-03:** Anomaly flags are added to existing `RFDetectionArray` messages — add an
  `is_anomaly: bool` and `anomaly_type: string` field to `RFDetection`. Keeps
  consumers simple (one subscription for detections + anomaly info).
- **D-04:** Baseline "warmup" period: anomaly detection is disabled for the first N
  seconds (default: 30 s) while the baseline converges. Reported via `/diagnostics`
  as INFO during warmup, WARN when an anomaly fires.

### Emitter Localization (ADV-02)
- **D-05:** Model — free-space log-distance path loss: RSSI = RSSI_0 - 10n*log10(d).
  Fit RSSI_0 (reference power at 1 m) and path loss exponent n via least-squares
  over collected (robot_pose, rssi_dbm) observation pairs. Closed-form, no heavy
  dependencies.
- **D-06:** Output — custom topic `/hackrf/emitter_map` publishing a new message type
  `RFEmitterMap.msg` containing a list of `RFEmitterEstimate` entries. Each entry:
  detection_id (links to RFDetection), estimated_x, estimated_y (in map frame),
  covariance_xx, covariance_yy (diagonal, from least-squares residual), observation_count,
  last_seen stamp.
- **D-07:** Minimum observations before publishing an estimate: 3 distinct robot poses
  (as specified in success criteria). Poses must be spatially separated by at least
  0.5 m (configurable) — collinear/stationary poses don't add useful information.
- **D-08:** Localization node tracks observations per detection_id (from Phase 1
  stable IDs). When a detection_id disappears for > 10 s, its estimate is removed
  from the map.

### Cyclostationary Feature Extraction (ADV-03)
- **D-09:** Lightweight cyclic feature statistics approach — not full cyclic
  autocorrelation (too expensive on ARM64 Jetson). Extract a small set of cyclic
  features for each CFAR-detected signal:
  - **WiFi (OFDM)**: check for spectral lines at multiples of the OFDM subcarrier
    spacing (~312.5 kHz for 802.11n/ac). Strong spectral lines → WiFi.
  - **BLE**: check for frequency-hopping pattern signature (hop dwell ~625 µs,
    detected via short-time autocorrelation). Hopping + narrow bandwidth → BLE.
  - **ZigBee**: narrow bandwidth (<2 MHz) + DSSS spreading (flat spectral envelope).
    Differentiated from BLE by absence of hopping and lower power.
- **D-10:** Integration with Phase 1 band classification — cyclostationary result
  AUGMENTS, does not replace, Phase 1 classification. When a signal is detected
  in 2.4 GHz ISM, run cyclostationary features and override the Phase 1 label
  if confidence > threshold (default: 0.7). Add `cyclo_classification: string` and
  `cyclo_confidence: float32` fields to `RFDetection`.
- **D-11:** Cyclostationary runs on-demand per CFAR detection event (not continuously).
  Triggered by the CFAR node when a detection lands in 2.4 GHz ISM band. IQ burst
  is grabbed from the driver's `/hackrf/iq` stream. Compute budget: <10 ms per
  detection burst on Jetson.

### Multi-Radio Namespace Support (HW-02)
- **D-12:** Namespace-only change — parameterized launch file. No code changes to
  hackrf_lifecycle_node.py. Each HackRF instance launched with `namespace:=/hackrf_0`
  or `namespace:=/hackrf_1`. All topics, services, and parameters are automatically
  scoped under the namespace.
- **D-13:** A new `multi_radio.launch.py` accepts a `radio_count` argument (default 2)
  and launches N instances with namespaces `/hackrf_0` ... `/hackrf_{N-1}`. Each
  instance gets its own YAML config file (config/hackrf_radio_0.yaml, etc.) with
  independent frequency, gain, and frame parameters.
- **D-14:** Device index selection: add `device_index` parameter (default 0) to
  hackrf_lifecycle_node.py. `hackrf_open_by_index()` used instead of `hackrf_open()`
  when device_index > 0.

### Claude's Discretion
- Exact EMA alpha tuning and warmup duration defaults
- Least-squares solver choice (scipy.optimize.curve_fit vs numpy.linalg.lstsq)
- IQ burst length for cyclostationary analysis (1024 vs 4096 samples)
- ZigBee DSSS flatness detection threshold
- RFEmitterEstimate message field ordering

</decisions>

<specifics>
## Specific Ideas

- Anomaly detection should report two distinct types separately — power spike vs new emitter
  in idle band — so downstream nodes can react differently to each
- Emitter localization publishes a running map (not one-shot) — estimates update as more
  observations arrive, estimates expire when emitter goes silent
- Cyclostationary is a refinement of Phase 1 classification, not a replacement — the
  Phase 1 bandwidth heuristic covers 90% of cases; cyclostationary handles the 2.4 GHz ISM
  disambiguation where bandwidth alone is insufficient
- Multi-radio support is a launch-file concern only — no driver code changes needed

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase 1 & 2 Code (build on these)
- `hackrf_ros/hackrf_lifecycle_node.py` — driver with IQ queue, FFT pipeline, AGC, async param
- `hackrf_ros/cfar_node.py` — CFAR detection node, RFDetectionArray publisher (ADV-01/03 augment this)
- `hackrf_interfaces/msg/RFDetection.msg` — message to extend with anomaly + cyclo fields
- `hackrf_interfaces/msg/RFDetectionArray.msg` — existing container
- `hackrf_ros/rf_map_node.py` — existing grid node (localization is a peer, not a replacement)

### Phase 1 Context (decisions that constrain this phase)
- `.planning/phases/01-sensor-foundations/01-CONTEXT.md`
  - D-04: Rich RFDetection message — adding fields must preserve existing field layout
  - D-11: Bandwidth heuristic for 2.4 GHz ISM — cyclostationary overrides this, not replaces
  - D-14: BEST_EFFORT QoS for streaming topics

### Phase 2 Context
- `.planning/phases/02-robot-autonomy-integration/02-CONTEXT.md`
  - D-17: MultiThreadedExecutor + ReentrantCallbackGroup required for new nodes

### Stack References (from CLAUDE.md)
- scipy.fft already used for PSD — cyclostationary spectral analysis uses same library
- Jetson ARM64 compute constraints: <10 ms per detection event for cyclostationary
- CycloneDDS required (FastRTPS broken on this ARM64 system)
- Python 3 throughout — no C++ nodes

### RF Domain
- Phase 1 canonical refs: `/mnt/NAS/vault/concepts/detection-theory-np-and-cfar.md`
- Phase 1 canonical refs: `.planning/research/ARCHITECTURE.md`

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `RFDetection.msg` and `RFDetectionArray.msg`: extend with new fields rather than new message types
- `cfar_node.py` detection pipeline: anomaly detection slots in as a post-processing step
  on the detection array before publishing
- `_fft_queue` and IQ queue in driver: cyclostationary node reads `/hackrf/iq` directly
  (BEST_EFFORT subscriber, same as existing consumers)
- `rf_map_node.py` pose lookup pattern via tf2_ros: reuse for localization node's pose observation

### Established Patterns
- All new nodes: standalone Python files in hackrf_ros/, entry point in setup.py
- MultiThreadedExecutor + ReentrantCallbackGroup (Phase 1 established, Phase 2 confirmed)
- Regular publishers not lifecycle publishers (CycloneDDS ARM64 bug from Phase 1)
- ROS2 parameters for all configurable thresholds (Pfa, alpha, etc.)
- No C++, no external heavy ML frameworks — pure Python + scipy/numpy

### Integration Points
- `RFDetection.msg`: add `is_anomaly`, `anomaly_type`, `cyclo_classification`, `cyclo_confidence`
  — these fields are populated by anomaly_node and cyclo_node respectively, or left at
  defaults by cfar_node when those nodes aren't running
- Localization: new `RFEmitterMap.msg` and `RFEmitterEstimate.msg` in hackrf_interfaces/msg/
- Multi-radio: `device_index` parameter added to hackrf_lifecycle_node.py, new multi_radio.launch.py

</code_context>

<deferred>
## Deferred Ideas

- **HW-01 (KrakenSDR AOA)**: Deferred — no hardware available. When hardware is present,
  implement KrakenSDR DOA daemon HTTP API integration and publish
  PoseWithCovarianceStamped bearing estimates. Add to v2 roadmap.
- **Full cyclic autocorrelation (CAF)**: Too compute-heavy for Jetson ARM64 in real-time.
  Deferred to v2 if cyclic feature statistics prove insufficient.
- **ML-based modulation classification (CLS-01)**: Already in v2 requirements — this phase
  uses signal processing features only.

</deferred>

---

*Phase: 04-advanced-signal-intelligence*
*Context gathered: 2026-04-13*
