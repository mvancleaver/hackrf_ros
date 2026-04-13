# hackrf_ros

ROS2 Humble lifecycle driver that turns a HackRF One SDR into a robot RF sensor. Publishes calibrated power spectral density, CFAR signal detections, cyclostationary signal classifications, and emitter position estimates as standard ROS2 messages.

## Architecture

```
HackRF One (USB)
      │
      ▼
hackrf_node  (lifecycle driver)
  ├── /hackrf/spectrum   (SpectrumStamped, 10 Hz)
  └── /hackrf/iq         (Float32MultiArray, 10 Hz)
      │                       │
      ▼                       ▼
cfar_node              cyclo_node
  └── /hackrf/detections     └── /hackrf/cyclo_detections
            │
            ▼
   emitter_loc_node
     └── /hackrf/emitter_location
            │
            ▼
   sweep_action_node   (wideband survey action server)
```

## Topics

| Topic | Type | Rate | Description |
|-------|------|------|-------------|
| `/hackrf/spectrum` | `SpectrumStamped` | 10 Hz | Calibrated PSD (dBFS), 4096-bin, Blackman windowed |
| `/hackrf/iq` | `Float32MultiArray` | 10 Hz | Interleaved float32 I/Q, normalized ±1.0 |
| `/hackrf/detections` | `RFDetectionArray` | 10 Hz | CA-CFAR signal detections with freq, BW, power, class |
| `/hackrf/cyclo_detections` | `RFDetectionArray` | 10 Hz | WiFi/BLE/ZigBee classifications via cyclostationary features |
| `/hackrf/emitter_location` | `PoseWithCovarianceStamped` | on detection | Multi-observation RSSI emitter position estimate |
| `/diagnostics` | `DiagnosticArray` | 1 Hz | Hardware state, gain, overflow count, IQ stall detection |

## Services and Actions

| Interface | Type | Description |
|-----------|------|-------------|
| `/hackrf/sweep_sync` | Service | Blocking single-frequency spectrum request |
| `/hackrf/sweep_band` | Action | Wideband survey with progress feedback and cancel |

## Parameters (dynamic — all tunable at runtime)

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `center_frequency` | 2437e6 | 1e6–6e9 | Center frequency (Hz) |
| `sample_rate` | 20e6 | 2e6–20e6 | Sample rate (Hz) |
| `lna_gain` | 16 | 0–40 | LNA gain (dB, 8 dB steps) |
| `vga_gain` | 20 | 0–62 | VGA gain (dB, 2 dB steps) |
| `amp_enabled` | false | — | RF amplifier |

Gain is managed automatically by the bidirectional AGC — reduces on ADC clipping, recovers after 30 quiet publish cycles.

## Quick Start

### Docker (recommended)

```bash
# Build
docker build -t hackrf_ros .

# Run with hardware
docker run --rm \
  --privileged \
  --network host \
  --device /dev/bus/usb:/dev/bus/usb \
  -e RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
  hackrf_ros \
  ros2 launch hackrf_ros hackrf.launch.py
```

### Build from Source

```bash
# Dependencies
sudo apt install ros-humble-diagnostic-updater ros-humble-lifecycle-msgs \
                 ros-humble-tf2-ros ros-humble-rmw-cyclonedds-cpp
pip3 install pyhackrf2 numpy scipy sigmf

# Build
cd ~/dev_ws
colcon build --packages-select hackrf_interfaces hackrf_ros
source install/setup.bash

# Launch
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
ros2 launch hackrf_ros hackrf.launch.py
```

### Manual Lifecycle Control

```bash
ros2 run hackrf_ros hackrf_node &
ros2 lifecycle set /hackrf_node configure
ros2 lifecycle set /hackrf_node activate

# Monitor
ros2 topic hz /hackrf/spectrum
ros2 topic echo /hackrf/detections
ros2 run rqt_plot rqt_plot /hackrf/spectrum/noise_floor_db
```

### Full Intel Pipeline

```bash
ros2 launch hackrf_ros intel_pipeline.launch.py
```

Starts: `hackrf_node` + `cfar_node` + `cyclo_node` + `emitter_loc_node` + `sweep_action_node`.

## Launch Files

| Launch file | Nodes started | Use case |
|-------------|---------------|----------|
| `hackrf.launch.py` | hackrf_node | Driver only — use when consuming spectrum elsewhere |
| `sensor_pipeline.launch.py` | hackrf_node + cfar_node | Detection pipeline |
| `intel_pipeline.launch.py` | All nodes | Full RF intelligence stack |
| `spectrum.launch.py` | hackrf_node + spectrum_node | Live spectrum visualizer |

## Signal Processing

The driver applies a calibrated IQ processing chain before publishing:

1. **DC removal** — subtract per-frame complex mean
2. **I/Q imbalance correction** — first-order gain/phase correction estimated from data
3. **ADC clip detection** — discard frames where >0.5% of samples hit ±127 rails
4. **Blackman window** — -58 dB sidelobes (vs -31.5 dB Hann)
5. **4096-pt FFT** (scipy.fft — ARM NEON vectorized on Jetson)
6. **Linear power accumulation** — average 16 frames in linear domain, convert to dB once (avoids geometric-mean bias)
7. **CA-CFAR detection** — beta-distribution threshold accounting for PSD averaging depth

## Platform Notes

- **CycloneDDS required** — set `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`. FastRTPS is broken on this ARM64 platform.
- **scipy.fft** is used throughout (not numpy.fft) for ARM NEON vectorization.
- Tested on Jetson Orin AGX and x86 Ubuntu 22.04.

## Hardware Limits

| Constraint | Value |
|------------|-------|
| Frequency range | 1 MHz – 6 GHz |
| Max bandwidth | 20 MHz instantaneous |
| ADC resolution | 8-bit (~50 dB dynamic range) |
| Direction finding | Requires KrakenSDR (Phase 4) |

## Documentation

| Doc | Description |
|-----|-------------|
| [`docs/review-2026-04-13.md`](docs/review-2026-04-13.md) | RF/RTOS/ROS2 team review findings and fix status |
| [`docs/rf-ew-review.md`](docs/rf-ew-review.md) | RF/EW signal processing review |
| [`docs/rf-sensor-roadmap.md`](docs/rf-sensor-roadmap.md) | Feature roadmap and architecture assessment |
