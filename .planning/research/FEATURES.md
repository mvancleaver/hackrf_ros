# Feature Landscape: Robot RF Sensor System

**Domain:** Robot RF awareness — SDR-based spectrum sensing for autonomous platforms
**Researched:** 2026-04-12
**Confidence:** HIGH for detection/classification/architecture (supported by EW literature + RF/EW review). MEDIUM for ROS ecosystem survey (thin — no dominant open-source ROS2 SDR sensor package exists). LOW for ML-based classification approaches (training data stale, rapidly moving field).

---

## Context: What This Package Is

A ROS2 sensor package that turns a HackRF One into a robot's primary RF awareness subsystem. The engineering review (docs/rf-sensor-roadmap.md) makes the gap precise: the current package is a spectrum analyzer, not a sensor. The distinction matters for feature scoping:

- A spectrum analyzer outputs: `float32[4096]` power arrays at 10 Hz
- A robot RF sensor outputs: "WiFi signal at 2437 MHz, -40 dBm, 20 MHz wide, bearing 045 deg, persisted 3 seconds"

Every feature decision flows from this gap.

---

## What Open-Source RF Sensor Packages for ROS/ROS2 Exist?

**The honest answer: almost nothing production-ready.**

Survey findings (MEDIUM confidence — WebSearch only, no authoritative registry):

- **No dominant ROS2 SDR sensor package exists.** The ROS ecosystem has mmWave radar drivers (TI IWR family), WiFi RSSI monitors, and GPS. SDR-based spectrum sensing is an unoccupied niche.
- **GNU Radio / ROS2 bridge** (GRCon 2025 paper): A research paper "Bridging ROS 2 and GNU Radio for Connected Robotics" exists, implementing GNU Radio source/sink blocks that emit ROS messages. Focuses on custom PHY/MAC protocol experimentation, not spectrum sensing for autonomy. Unshipped as a packaged tool.
- **Deadspottr** (TurtleBot 3 + ROS): WiFi RSSI monitoring robot — monitors signal strength, not spectrum. Single-metric, no detection pipeline.
- **GNSS-SDR** (gnss-sdr/gnss-sdr): GPS receiver implemented in software — not a generic RF sensor.
- **RFwatch** (Pranav-d33/RFwatch): HackRF + GNU Radio spectrum monitor with signal detection by power threshold. No ROS integration, no robot context.
- **KrakenSDR DOA** (krakenrf/krakensdr_doa): 5-channel coherent RTL-SDR direction finding with GNU Radio blocks. Open source. No ROS integration. Outputs azimuth estimates to a web interface and optional TCP stream.

**Implication:** This package is building into a gap. There is no prior art to fork or compete with. Standard patterns must come from the EW/radar signal processing literature, not from a ROS package registry.

---

## Table Stakes

Features users (autonomy engineers, field roboticists) expect. Missing = package is not usable as a sensor.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Timestamped spectrum messages | Every ROS2 sensor publishes stamped data. Without `Header.stamp`, data cannot be correlated with pose, lidar, or anything else. | Low | `SpectrumStamped.msg` — add `std_msgs/Header` to existing PSD output. Blocked on custom msg package. |
| TF frame integration | Antenna position relative to `base_link` must be known for any spatial reasoning. Without it, detections have no reference frame. | Low | Static transform, configurable `frame_id` parameter. No calibration needed for a single fixed antenna. |
| CFAR energy detection | Converts raw PSD float arrays into "signal detected" events. This is the entire gap between spectrum analyzer and sensor. Without it, every downstream node must re-implement detection. | Medium | CA-CFAR on 1D PSD. Sliding window, noise estimate per bin, threshold = alpha * noise_est. ~200 lines of Python. Runs at 10 Hz trivially. |
| Signal parameter estimation | Center frequency, bandwidth, power dBm, SNR dB per detected signal. A detection without parameters is a boolean alarm, not a measurement. | Low-Med | Peak grouping on CFAR output. Bandwidth from -3 dB or -10 dB rolloff. Power from integrated PSD. |
| Signal persistence tracking | Multi-frame confirmation eliminates transient noise spikes. Assignment of stable detection IDs enables downstream track-before-detect. | Medium | Sliding window or frame count per detection bin. Min 2-3 frame confirmation before publishing. M-of-N binary integration (Holik §11.9) is the principled form. |
| Band classification lookup | Map detected center_frequency + bandwidth to human-readable label: WiFi 2.4 GHz, BLE, ZigBee, LTE Band 4, DECT, ISM 433, etc. | Low | Static lookup table. Bandwidth heuristics: <200 kHz = narrowband, 1-5 MHz = ISM/BLE, 20 MHz = WiFi ch, 40+ MHz = LTE. No ML needed for coarse classification. |
| Hardware diagnostics | `/diagnostics` topic with device state, ADC clip rate, stream health, gain settings. Required for field deployment — operators need to know if the sensor is working. | Low | Exists in skeleton form. Extend with clip rate metric, stream uptime, and current tuning params. |
| QoS configuration | PSD stream must be BEST_EFFORT — a RELIABLE stream back-pressures the pipeline when a subscriber is slow, causing the driver node to stall. | Low | Single-line QoS change. Non-negotiable for multi-subscriber deployments. |
| BEST_EFFORT sweep non-blocking | Sweep service currently blocks PSD publishing during execution. Downstream nodes lose data. | Medium | Convert to Action server so sweep runs concurrently with monitoring. |

---

## Differentiators

Features that meaningfully differentiate this package from "just run GNU Radio." Not expected by default, but high value once table stakes are met.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| RF occupancy grid (2D heatmap) | Maps spectrum power to robot's spatial map — enables RF-aware navigation, interference avoidance, emitter approach routes. Integrates as a Nav2 costmap layer. No equivalent exists in open-source ROS. | Medium-High | OccupancyGrid msg, frequency-keyed layers. Robot must be moving (or have known positions for static measurements). Power-at-position from `base_link` + TF. |
| IQ recording with pose metadata (SigMF) | Triggered IQ capture with robot pose, GPS (if available), SDR params, mission ID embedded in SigMF metadata. Enables post-mission analysis, dataset creation for ML, replay for debugging. SigMF is the standard format — every RF tool reads it. | Medium | SigMF (.sigmf-data + .sigmf-meta). 20 MSPS * 2 bytes = 40 MB/s. Triggered on detection event with configurable ring buffer pre-trigger. Jetson NVMe handles throughput trivially. |
| Wideband anomaly detection | Baseline PSD learned from N-second average, deviation flagged when current PSD exceeds baseline by threshold. Detects new emitters without knowing what they are. Useful for "something changed" alerting. | Medium | Running mean + std of PSD per bin. Z-score or chi-square test per frame. No prior signal knowledge required. |
| Sweep action server with progress | Wideband survey with cancel support, progress feedback, per-band results streaming. Enables mission planning nodes to issue RF surveys and receive structured reports. | Medium | ROS2 action server wrapping existing sweep service. Progress = (current_freq - start_freq) / (end_freq - start_freq). |
| Automatic gain control (AGC) | Adapt LNA/VGA to local RF environment — prevent ADC clipping in high-power environments, maximize sensitivity in low-power ones. | Medium | Clip rate feedback loop. If ADC clip rate > threshold, reduce VGA. If noise floor at top 10% of dynamic range, increase LNA. Simple PID or bang-bang is sufficient. |
| Multi-observation emitter localization | Combine power measurements at known robot positions to triangulate emitter location. Power-based ranging (path loss model) gives a circle, not a bearing — intersection of circles from 3+ positions gives a fix. | High | Requires robot motion, known antenna gain pattern, calibrated path loss exponent. Accuracy limited by HackRF's +/- 1 dB power measurement stability. Useful as coarse estimate (~10-50m). |
| Cyclostationary feature extraction | Distinguish WiFi from BLE from ZigBee in the 2.4 GHz band using cyclic autocorrelation at known chip rates. Resolves ambiguity that bandwidth heuristics cannot. | High | SCF (Spectral Correlation Function) at candidate cyclic frequencies. Computationally expensive. Apply only to detections already coarsely classified as 2.4 GHz ISM. O'Donoughue Ch. 4: works only when input SNR > 0 dB and code is short — not a universal solution. |
| KrakenSDR direction finding integration | True angle-of-arrival using 5-channel coherent SDR. Single HackRF has no AOA capability whatsoever — cannot be synthesized from a single receiver. KrakenSDR outputs azimuth estimates; this package would consume them and fuse with power measurements. | High | Separate hardware required ($400). KrakenSDR has no ROS integration — bridge would need to parse its TCP output stream. Phase 4 work. |

---

## Anti-Features

Things to deliberately not build. Each entry has a reason.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| Real-time protocol demodulation | Demodulating WiFi, LTE, BLE, ZigBee in real-time requires full PHY implementation or GNU Radio flow graphs. Massive complexity, separate compute budget, mostly illegal to intercept, and not needed for RF awareness. The robot needs "WiFi present here," not the packet contents. | Detect and classify by bandwidth/frequency heuristics. That's enough for navigation and awareness. |
| Full SIGINT capability | Signal intercept, recording of specific transmissions, content capture. Legal liability on any platform deployed in civilian environments. Scope creep that takes this from sensor to surveillance tool. | IQ recording is limited to raw wideband capture for debugging and dataset creation — not targeted signal intercept. SigMF recordings should have a clear purpose and retention policy. |
| Matched filter / waveform-specific detection | Optimal detection of a specific known waveform requires knowing the waveform (code, modulation, timing). Robot RF sensor never knows the waveform a priori. Energy detection (CFAR) is the correct tool when waveform is unknown. O'Donoughue Ch. 3: energy detector is the Bayes-optimal receiver for unknown signals in AWGN. | Use CFAR energy detection. It requires only noise power estimate, not signal structure. |
| ML-based signal classification | Deep learning classifiers (AMC — automatic modulation classification) require: labeled training data matching your hardware, retraining when environment changes, significant inference compute, and produce confidences that are hard to calibrate. For coarse robot RF awareness, bandwidth + frequency heuristics get you 80% of the value at 1% of the cost. | Bandwidth heuristics + band lookup table. Add cyclostationary features (Phase 3) for the 2.4 GHz disambiguation case where heuristics fail. |
| TX / jamming capability | Accidental or intentional RF transmission from an autonomous robot is a safety and legal hazard. HackRF can transmit — the package must not expose this. It was deliberately removed from scope during rescoping (see PROJECT.md). | TX is a separate package with explicit authorization gates (see pymayhem project). |
| Redis/pubsub IQ streaming | Replaced by ROS2 topics during rescope. Redis adds a dependency, bypasses DDS QoS, and breaks the standard ROS2 tooling chain (rosbag, RViz, rqt, Foxglove). | Publish IQ as sensor_msgs/PointCloud2 or Float32MultiArray on /hackrf/iq for the rare subscriber that needs raw IQ. |
| Frequency hopping / DSSS emitter tracking | Tracking spread-spectrum or frequency-hopping emitters requires either knowing the hopping sequence (cooperative) or very long integration times. O'Donoughue Ch. 4: energy detector on DSSS pays full spreading penalty — a 1 GHz DSSS signal is 30 dB harder to detect than the same signal in 1 MHz. This is an EW research problem, not a robot sensor feature. | Detect the aggregate energy of hopping emitters if dwell is long enough. Flag as "unclassified wideband source." |
| GUI applications as core nodes | Matplotlib display nodes are optional analysis tools, not part of the sensor pipeline. They cannot run headlessly, block the executor, and add PyQt/display dependencies to the core package. | Keep display nodes as optional subscribers in a separate package or launch file. They subscribe to standard topics and add zero coupling to the sensor pipeline. |

---

## Feature Dependencies

```
Hardware driver (existing)
    └── Calibrated PSD publishing (existing)
            ├── Timestamped SpectrumStamped.msg  ←── ALL features below depend on this
            │       └── TF frame integration
            │
            ├── CFAR energy detector
            │       └── Signal parameter estimation (center_freq, BW, power, SNR)
            │               └── Signal persistence tracker (multi-frame IDs)
            │                       ├── Band classification lookup  ←── table stakes complete here
            │                       │
            │                       ├── Wideband anomaly detection (baseline diff)
            │                       │
            │                       ├── RF occupancy grid  ←── requires TF + robot position
            │                       │
            │                       └── IQ recording (SigMF)  ←── triggered by persistent detection
            │                               └── Pose metadata  ←── requires /tf at record time
            │
            └── Sweep action server  ←── requires CFAR to interpret sweep results
                    └── Multi-observation emitter localization  ←── requires sweep + robot motion

KrakenSDR hardware (separate)
    └── DOA estimates (TCP stream)
            └── AOA + power fusion  ←── requires detection IDs to associate measurements
```

---

## CFAR Selection Guide (informed by EW literature)

The detection approach determines what signals are detectable and what the false alarm rate is. Based on O'Donoughue (Ch. 3, 4) and Holik (Ch. 11):

| Variant | Use When | Strength | Failure Mode |
|---------|----------|----------|-------------|
| CA-CFAR | Default for spectrum sensing in a clean environment | Lowest loss (<1 dB at M=50), simple, closed-form Pfa control | Multi-target masking: adjacent signals raise threshold, weaker signal missed |
| GO-CFAR | Near band edges or clutter transitions | Suppresses false alarms at edges | Masks closely-spaced signals |
| OS-CFAR | Dense multi-signal environments (e.g. 2.4 GHz with 3+ overlapping networks) | Robust to interferers in reference window | Computationally heavier (sort) |

For the HackRF's 8-bit ADC (50 dB dynamic range), CA-CFAR is the correct starting point. Upgrade to OS-CFAR if the 2.4 GHz ISM band produces excessive masking in testing.

**M-of-N binary integration** (signal persistence) is the complement to CFAR: per-frame CFAR gives binary detection, and M-of-N (e.g., 3 of 5 frames) gives the final confirmed detection. This separates the noise statistics from the temporal persistence logic cleanly.

---

## Detection Limits (Hardware Constraints)

These limits inform which features are achievable and which are aspirational:

| Constraint | Impact |
|-----------|--------|
| 8-bit ADC, ~50 dB dynamic range | Strong signals mask weaker ones in the same instantaneous bandwidth. High-power WiFi AP at close range can mask BLE in same 2.4 GHz capture. |
| 20 MHz instantaneous bandwidth | Wideband awareness requires frequency hopping. Coverage of 1 MHz–6 GHz at 20 MHz steps = ~300 hops. At 10 ms/hop (sweep): ~3 second full survey. |
| Single receiver, no phase coherence | Zero AOA capability natively. Interferometric DF requires phase-coherent multi-channel hardware (KrakenSDR is correct choice). Power-based localization (multi-position) is the only option with a single HackRF. |
| 4% FFT processing rate (Python) | Adequate for PSD monitoring. CFAR adds ~1ms per frame (sliding window convolution). If GPU acceleration needed: CuPy + cuFFT on Jetson gives 10-50x for batched FFTs. |
| DSSS/FH signals | Energy detector pays full spreading penalty. A GPS signal (43 dB processing gain) is undetectable. Spread-spectrum communications are severely attenuated in detection probability unless dwell is very long. Do not claim DSSS detection capability. |

---

## MVP Recommendation

Prioritize in this order to get from "spectrum analyzer" to "robot RF sensor":

**Phase 1 — Make it a real sensor (non-negotiable)**
1. `SpectrumStamped.msg` + `std_msgs/Header` — unlock all downstream correlation
2. TF frame (`hackrf_antenna` → `base_link`) — unlock spatial reference
3. CFAR energy detector node → `RFDetectionArray` — unlock semantic interpretation
4. Signal parameter estimation (center_freq, BW, power, SNR) — make detections informative
5. Signal persistence tracker (M-of-N confirmation, stable IDs) — make detections reliable
6. QoS cleanup (BEST_EFFORT for PSD) — make it work in multi-subscriber deployments
7. Band classification lookup table — give detections human-readable labels

**Phase 2 — Robot autonomy integration (high value)**
8. RF occupancy grid for Nav2 — the killer app for robot RF awareness
9. Sweep action server (cancel + progress) — make surveys useful for mission planning
10. IQ recording action server (SigMF + pose) — enable post-mission analysis

**Phase 3 — Advanced processing (if Phase 2 is solid)**
11. Automatic gain control — adapt to environment, reduce operator burden
12. Wideband anomaly detection — "something new appeared" alerting
13. Cyclostationary features — 2.4 GHz disambiguation (WiFi vs BLE vs ZigBee)
14. Multi-observation emitter localization — coarse source location from robot motion

**Phase 4 — Hardware expansion**
15. KrakenSDR AOA integration — true bearing requires separate hardware

**Defer permanently:**
- Protocol demodulation — out of scope, see anti-features
- ML-based AMC classification — cost/benefit unfavorable vs heuristics for this application
- DSSS/FH tracking — EW research problem, not sensor feature

---

## Sources

- O'Donoughue, *Emitter Detection and Geolocation for Electronic Warfare* (Artech 2019) — Ch. 3 (energy detection), Ch. 4 (SS detectors, cyclostationary)
- Holik, *Radar and EW Modeling in MATLAB and SIMULINK* — Ch. 11 (CFAR variants, M-of-N, Swerling)
- Adamy, *EW 101* Ch. 8 — DF techniques (interferometric, Doppler, TDOA), accuracy budgets
- `docs/rf-sensor-roadmap.md` — RF/EW engineering review, signal processing stage gap analysis
- `.planning/PROJECT.md` — hardware constraints, validated requirements, key decisions
- SigMF specification (sigmf.org) — IQ recording format
- KrakenSDR DOA (github.com/krakenrf/krakensdr_doa) — 5-channel coherent DOA, GNU Radio blocks, no ROS integration
- WebSearch: GNU Radio / ROS2 bridge paper (GRCon 2025), RFwatch, ecosystem survey
