# RF Sensor Engineering Review -- hackrf_ros for Robotics

**Date**: 2026-04-13
**Context**: HackRF One as primary RF sensor on a mobile robot (Jetson ARM64)

---

## Architecture Assessment

**Current state**: Well-implemented spectrum analyzer. Lifecycle node with PSD publishing and sweep service. Corrected signal processing (Blackman window, linear averaging, DC removal, I/Q correction, ADC clip detection).

**Gap**: The package is a spectrum analyzer, not a sensor. A robot needs "there is a WiFi signal at 2437 MHz, -40 dBm, 20 MHz wide" -- not a 4096-element float array. No timestamps, no spatial reference, no semantic interpretation.

**Structural issues**:
- Single node couples hardware driver + signal processing. Should be separated.
- Sweep service blocks PSD publishing (downstream nodes lose data during sweeps).
- No concept of an "observation" (timestamped, geo-referenced RF snapshot).
- No sensor operational modes (monitor / sweep / track / record).

---

## Missing Signal Processing Stages

| Stage | Status | What It Does |
|-------|--------|-------------|
| 1. Conditioning | Done | DC removal, I/Q correction, clipping detection |
| 2. Spectral estimation | Done | Blackman-windowed Welch PSD, 4096-pt FFT |
| 3. Detection | **Missing** | CFAR energy detection, peak grouping |
| 4. Parameter estimation | **Missing** | Center freq, bandwidth, power per signal |
| 5. Classification | **Missing** | Bandwidth heuristics, frequency band lookup |
| 6. Direction finding | **Missing** | Requires additional hardware (KrakenSDR) |

### CFAR Detection (highest priority missing piece)
- Cell-Averaging CFAR (CA-CFAR) on the PSD array
- Sliding-window local noise estimate per bin
- Threshold = alpha * noise_estimate (alpha from target Pfa)
- Group adjacent detected bins into signal detections
- ~200 lines of Python, runs trivially at 10 Hz

### Signal Classification (high value, low cost)
- Bandwidth heuristics: <200 kHz = narrowband, 1-5 MHz = ISM/BLE, 20 MHz = WiFi, 40+ = LTE
- Frequency band lookup table: map center freq to known allocations
- Duty cycle analysis: continuous vs pulsed
- No ML required for coarse classification

---

## Required Data Products

### Topics

| Topic | Type | Rate | Purpose |
|-------|------|------|---------|
| `/hackrf/spectrum` | `SpectrumStamped` | 10 Hz | Timestamped PSD with header + tuning |
| `/hackrf/detections` | `RFDetectionArray` | 10 Hz | Detected signals with freq, BW, power, class |
| `/hackrf/rf_environment` | `RFEnvironment` | 1 Hz | Summary: emitter count, occupancy %, noise floor |
| `/diagnostics` | `DiagnosticArray` | 1 Hz | Exists |

### Services

| Service | Purpose |
|---------|---------|
| `/hackrf/sweep` | Wideband scan (exists, should become Action) |
| `/hackrf/set_mode` | Switch monitor/sweep/track/record |
| `/hackrf/get_signal_report` | On-demand analysis of specific frequency |

### Actions (for long-running ops)

| Action | Purpose |
|--------|---------|
| `/hackrf/survey_band` | Sweep with progress + cancel |
| `/hackrf/track_signal` | Lock onto signal, report params |
| `/hackrf/record_iq` | Record raw IQ to SigMF with progress |

### Custom Messages Needed

```
# RFDetection.msg
std_msgs/Header header
float64 center_frequency_hz
float64 bandwidth_hz
float32 power_dbm
float32 snr_db
string classification
uint32 persistence_frames
uint32 detection_id

# RFDetectionArray.msg
std_msgs/Header header
float64 noise_floor_dbm
RFDetection[] detections

# SpectrumStamped.msg
std_msgs/Header header
float64 center_frequency_hz
float64 sample_rate_hz
float64 bin_width_hz
uint32 fft_size
float32[] psd_db
```

---

## IQ Logging

**Format**: SigMF (`.sigmf-data` + `.sigmf-meta`)
- Native int8 format = zero conversion overhead
- JSON metadata travels with data
- Every RF tool reads SigMF

**Throughput**: 20 MSPS * 2 bytes = 40 MB/s (2.4 GB/min)
- NVMe on Jetson Orin: trivial
- SD card on Jetson Nano: bottleneck at 80 MB/s

**Compression**: zstd level 1 for streaming (~1.5:1 on IQ, negligible CPU)

**Triggered recording**: Ring buffer of last N seconds, start recording on detection event

**Metadata per capture**: Robot pose from /tf, GPS if available, all SDR params, mission/waypoint ID, trigger reason

---

## Real-Time Performance on Jetson ARM64

**Current processing**: ~320 FFTs/sec (4% of captured data). Adequate for PSD monitoring.

**Bottlenecks**:
1. `np.fft.fft` uses FFTPACK (no SIMD). Switch to `scipy.fft` or `pyfftw` for 3-5x speedup.
2. `bytes(data)` in rx_callback copies entire buffer. Consider memoryview.
3. `astype(np.float32)` allocates new array per frame. Pre-allocate + copyto.
4. I/Q imbalance correction runs per-frame. Cache coefficients, update every ~100 frames.
5. Sweep stitching per-sample Python loop. Vectorize with np.searchsorted.

**If GPU needed**: CuPy + cuFFT on Jetson GPU gives 10-50x for batched FFTs.

---

## Missing ROS2 Integration

| Pattern | Status | Impact |
|---------|--------|--------|
| TF frame (`base_link -> hackrf_antenna`) | **Missing** | No spatial reference for any measurement |
| Stamped messages (Header with stamp + frame_id) | **Missing** | No temporal/spatial correlation possible |
| Automatic error recovery (USB disconnect) | **Missing** | Node stays ACTIVE with stale data |
| Action server for sweep | **Missing** | Service blocks, no cancel/progress |
| Composable nodes | **Missing** | Extra process overhead on Jetson |
| QoS: BEST_EFFORT for PSD stream | **Missing** | RELIABLE back-pressures pipeline |
| Parameter callback non-blocking | **Missing** | sleep() blocks executor, risks DDS lease expiry |

---

## Feature Roadmap (prioritized)

### Phase 1: Make it a real sensor (1-2 weeks)
1. **Stamped messages + TF frame** -- minimum viable ROS2 integration
2. **CFAR energy detector node** -- converts PSD to actionable detections
3. **Signal persistence tracker** -- eliminates transient false alarms
4. **QoS + message cleanup** -- BEST_EFFORT, remove bare Float32MultiArray

### Phase 2: Robot autonomy integration (2-3 weeks)
5. **RF occupancy grid** -- 2D heatmap for nav stack (like costmap layer)
6. **Sweep action server** -- cancel + progress feedback
7. **IQ recording action server** -- SigMF with pose metadata
8. **Band classification lookup** -- frequency + bandwidth heuristics

### Phase 3: Advanced processing (3-4 weeks)
9. **Automatic gain control** -- adapt LNA/VGA to environment
10. **Multi-observation emitter localization** -- power + position -> emitter fix
11. **Wideband anomaly detection** -- baseline PSD, flag deviations
12. **Cyclostationary features** -- disambiguate WiFi/BLE/ZigBee in 2.4 GHz

### Phase 4: Hardware expansion
13. **KrakenSDR for direction finding** -- 4-channel coherent AOA
14. **Multi-radio architecture** -- HackRF sweeps, second SDR tracks

---

## Critical Path

The single highest-impact change: **stamped messages with TF frames** (makes data usable by any ROS2 node) followed by **CFAR detection** (converts power arrays into "signal detected" events). The signal processing math is solid -- the gap is the robotics integration layer between raw spectral data and actionable autonomy inputs.
