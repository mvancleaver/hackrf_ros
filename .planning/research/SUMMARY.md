# Research Summary — HackRF ROS2 RF Sensor

## Cross-Cutting Findings

All four research threads converge on the same conclusion: the signal processing is solid, the gap is robotics integration. Every downstream feature blocks on two changes:

1. **`SpectrumStamped.msg` with `std_msgs/Header`** — timestamp + TF frame_id makes data usable by any ROS2 node
2. **CA-CFAR detector** — converts float[4096] PSD arrays into `RFDetection` messages with freq, bandwidth, power, classification

The ROS2 SDR sensor niche is empty. No dominant open-source package exists. This project builds into a genuine gap.

## Stack Decisions

| Decision | Choice | Confidence | Rationale |
|----------|--------|------------|-----------|
| FFT library | `scipy.fft` (replace `np.fft`) | HIGH | ARM NEON SIMD, drop-in API, 1.5-2.5x speedup |
| DDS middleware | CycloneDDS (already in use) | HIGH | FastRTPS broken on this ARM64 Jetson |
| Publishers | Regular `create_publisher()` | HIGH | Lifecycle publishers silently fail on CycloneDDS ARM64 |
| Executor | `MultiThreadedExecutor` | HIGH | Required before any action server (ros2 bug #1609) |
| CFAR implementation | Custom numpy (~50 lines) | HIGH | No suitable library; theory in Obsidian wiki |
| IQ recording format | SigMF `ci8` | HIGH | Zero conversion, standard tooling, wiki docs |
| Nav2 integration | `nav_msgs/OccupancyGrid` | MEDIUM | C++ plugin deferred; OccupancyGrid publish sufficient for Phase 2 |
| Zero-copy transport | Not applicable | HIGH | Variable-length float[] disqualifies iceoryx |
| Phase 3 FFT upgrade | pyfftw or C++ component | MEDIUM | Deferred; scipy.fft sufficient through Phase 2 |

## Feature Priorities

### Table Stakes (Phase 1)
1. SpectrumStamped.msg with header (stamp + frame_id)
2. Static TF: `base_link` -> `hackrf_antenna`
3. CA-CFAR energy detector -> RFDetectionArray
4. Signal parameter estimation (center_freq, bandwidth, power, SNR)
5. Signal persistence tracker (M-of-N, stable IDs)
6. Band classification lookup (frequency + bandwidth heuristics)
7. QoS: BEST_EFFORT for all streaming topics
8. MultiThreadedExecutor upgrade

### Differentiators (Phase 2)
- RF occupancy grid for Nav2 (no open-source equivalent exists)
- Sweep action server (cancel + progress feedback)
- IQ recording action server (SigMF + robot pose metadata)

### Reliability (Phase 3)
- Async param callback (unblock executor)
- USB disconnect recovery
- AGC loop (clip-rate feedback)

### Advanced (Phase 4)
- Wideband anomaly detection (baseline PSD deviation)
- Cyclostationary features (WiFi/BLE/ZigBee)
- Multi-observation emitter localization

### Anti-Features (never build)
- Protocol demodulation, ML-based AMC, TX/jamming, DSSS tracking, Redis IQ streaming

## Architecture

Six node roles, only driver is LifecycleNode:

```
hackrf_driver_node (Lifecycle) -> /hackrf/iq_raw
                                        |
psd_node (Regular)             -> /hackrf/spectrum (SpectrumStamped, 10 Hz)
                                        |
cfar_node (Regular)            -> /hackrf/detections (RFDetectionArray, 10 Hz)
                                -> /hackrf/rf_environment (1 Hz)

sweep_node (ActionServer)      <- /hackrf/spectrum + driver retune service
recorder_node (ActionServer)   <- /hackrf/iq_raw (bounded queue, dedicated thread)
```

Phase 1: Add SpectrumStamped + CFAR to existing monolith (no node split yet)
Phase 2: Split monolith into driver + psd_node, add action servers

## Critical Pitfalls

| # | Pitfall | Severity | Phase | Mitigation |
|---|---------|----------|-------|------------|
| 1 | Lifecycle publishers don't transmit on CycloneDDS ARM64 | CRITICAL | All | Use `create_publisher()`, add integration test |
| 2 | pyhackrf2 segfaults on rapid stop/start or disconnect | CRITICAL | All | 150ms settle, no device calls from callback thread |
| 3 | `time.sleep()` in param callback blocks executor | HIGH | 1 | Move to background thread in Phase 3 |
| 4 | dB-domain averaging (-2.5 dB bias) | HIGH | 1 | Fixed; regression surfaces: CFAR noise estimator, sweep stitch |
| 5 | CFAR params require empirical calibration | HIGH | 1 | Start Pfa=1e-4, use persistence, calibrate in deployment env |
| 6 | IQ recorder will overflow queue on SD card | HIGH | 2 | Dedicated write thread with own bounded queue |
| 7 | Missing `OPENBLAS_NUM_THREADS=1` in Docker | MODERATE | 1 | Add to docker-compose environment |

## Open Questions

- CFAR guard cell sizing for 20 MHz WiFi signals spanning ~4000 bins
- Nav2 costmap behavior under 10 Hz OccupancyGrid updates from RF sensor
- SigMF pose extension format (no standard exists)
- USB disconnect recovery path in current pyhackrf2 version
- Cyclostationary FFT compute budget on target Jetson (Phase 4 gate)

## Build Order

1. `hackrf_interfaces` — add SpectrumStamped.msg, RFDetection.msg, RFDetectionArray.msg
2. `hackrf_ros` driver — replace Float32MultiArray with SpectrumStamped, add TF, fix QoS
3. `hackrf_ros` cfar_node — new node: CA-CFAR + persistence + classification
4. `hackrf_ros` executor — MultiThreadedExecutor in main()
5. `hackrf_ros` sweep_action — convert service to action server
6. `hackrf_ros` recorder — SigMF action server with dedicated write thread
7. `hackrf_ros` rf_occupancy — OccupancyGrid publisher from detections + odometry

## Wiki References

Research drew on these Obsidian vault pages:
- [[detection-theory-np-and-cfar]] — NP lemma, CFAR theory, detector decision tree
- [[target-detection-modeling]] — CA/GO/SO/OS-CFAR implementations, Pd/Pfa formulas
- [[cw-and-spread-spectrum-signal-detection]] — Energy detector optimality for unknown signals
- [[sdr-iq-pipeline-threading]] — Bounded queue per sink, threading discipline
- [[sigmf-iq-recording]] — SigMF format spec, ci8 datatype
- [[rf-emitter-location-techniques]] — DF and geolocation for Phase 4
- [[hackrf-ros]] — Original project architecture notes
