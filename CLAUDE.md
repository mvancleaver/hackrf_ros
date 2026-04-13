## Project

**HackRF ROS2 Lifecycle Driver**

A focused ROS2 lifecycle node for the HackRF One SDR. Provides RX IQ streaming, hardware diagnostics, and dynamic retuning through standard ROS2 interfaces.

### Constraints

- **Hardware**: HackRF One via pyhackrf2 (libusb)
- **Framework**: ROS2 Humble with Python (rclpy)
- **Architecture**: Single lifecycle node — no Redis, no serial, no TX

## Technology Stack

- **Language**: Python 3
- **Runtime**: ROS 2 Humble, colcon, ament_python
- **Key deps**: rclpy, pyhackrf2, numpy, diagnostic_updater, lifecycle_msgs, std_msgs
- **Build**: `colcon build --packages-select hackrf_ros`

## Architecture

Single file: `hackrf_ros/hackrf_lifecycle_node.py`

- `HackRFLifecycleNode(LifecycleNode)` — the entire driver
- Lifecycle: UNCONFIGURED → on_configure (open device) → INACTIVE → on_activate (start RX) → ACTIVE
- Topic: `/hackrf/iq` (Float32MultiArray, BEST_EFFORT, ~200 Hz)
- Diagnostics: `/diagnostics` via diagnostic_updater (1 Hz)
- Parameters: center_frequency, sample_rate, lna_gain, vga_gain, amp_enabled (all dynamic)
- Launch: `launch/hackrf.launch.py` auto-transitions configure → activate

## Conventions

- 4-space indent, flake8/pep257 enforced
- PascalCase classes, snake_case everything else
- Private methods prefixed with `_`
- ROS2 logger only (no print statements)
- Entry point: `hackrf_node = hackrf_ros.hackrf_lifecycle_node:main`

<!-- GSD:project-start source:PROJECT.md -->
## Project

**HackRF ROS2 RF Sensor**

A ROS2 sensor package that turns a HackRF One SDR into a robot's primary RF awareness system. It captures IQ data, computes calibrated power spectral density, detects and classifies RF signals, and publishes actionable spectrum intelligence for autonomy nodes. Platform-agnostic — works on ground robots, drones, and stationary monitors.

**Core Value:** Reliable, calibrated RF spectrum awareness published as standard ROS2 messages that any robot autonomy stack can consume for signal detection, classification, and RF environment mapping.

### Constraints

- **Hardware**: Single HackRF One, 8-bit ADC limits dynamic range to ~50 dB
- **Bandwidth**: 20 MHz instantaneous — wideband coverage requires frequency hopping
- **Platform**: Must run in Docker on Jetson ARM64 (ARM NEON, no x86 SIMD)
- **DDS**: CycloneDDS required — FastRTPS broken on this platform
- **Real-time**: Python FFT at edge of throughput (4% of captured data processed). Performance-critical paths may need pyfftw or C++
- **Direction finding**: Requires KrakenSDR hardware (Phase 4) — single HackRF has no AOA capability
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

## Recommended Stack
### Core Framework (existing — keep)
| Technology | Version | Purpose | Confidence |
|------------|---------|---------|------------|
| ROS2 Humble | LTS | Node lifecycle, topics, services, actions, TF2 | HIGH |
| rclpy | bundled | Python client library | HIGH |
| CycloneDDS | bundled | DDS middleware — FastRTPS broken on this ARM64 system | HIGH (verified in-project) |
| pyhackrf2 | latest | HackRF One USB IQ streaming | HIGH |
### FFT / Signal Processing
| Technology | Version | Purpose | Confidence |
|------------|---------|---------|------------|
| **scipy.fft** | >=1.11 | **Primary FFT — use this instead of np.fft** | HIGH |
| pyfftw | 0.15.1 | Optional drop-in accelerator if scipy.fft is insufficient | MEDIUM |
| numpy | >=1.24 | Array ops, windowing, PSD math | HIGH |
| CuPy + cuFFT | cupy-cuda12x | GPU FFT on Jetson Orin only — large batch case | LOW |
### IQ Recording
| Technology | Version | Purpose | Confidence |
|------------|---------|---------|------------|
| **SigMF (sigmf)** | 1.7.2 | IQ recording format + metadata | HIGH |
- Native HackRF int8 samples map to SigMF datatype `ci8` — zero conversion overhead
- Write data file incrementally (append raw bytes from IQ queue)
- Write metadata file once at recording stop — avoid per-chunk JSON overhead
- Pre-allocate data file with `os.posix_fallocate()` to prevent filesystem metadata thrashing on slower storage
- Add `captures` entry per frequency hop (sweep recording) using `sample_start` offset
- Metadata must include: center_freq, sample_rate, gains, robot pose from /tf, trigger reason, ROS timestamp
- Detect dropped chunks via sequence number gap; emit new captures entry at gap
### CFAR Detection
| Technology | Version | Purpose | Confidence |
|------------|---------|---------|------------|
| **Custom CA-CFAR (numpy)** | — | Energy detection on PSD array | HIGH |
| scipy.ndimage.uniform_filter | >=1.11 | Sliding-window noise estimate | HIGH |
# Pseudocode — see docs/rf-ew-review.md for context
### ROS2 Message Transport (Large Arrays)
| Technology | Status | Notes | Confidence |
|------------|--------|-------|------------|
| Standard DDS (CycloneDDS) | Use for now | Adequate at 10 Hz PSD publish | HIGH |
| CycloneDDS + iceoryx (shared memory) | **Cannot use** for PSD/IQ messages | Variable-length float arrays disqualify zero-copy | HIGH |
| Fixed-size custom messages | Required for zero-copy if needed later | Must define fixed array size at compile time | MEDIUM |
### ROS2 Action Servers (Long-Running Operations)
| Technology | Purpose | Confidence |
|------------|---------|------------|
| rclpy ActionServer | sweep_band, record_iq, track_signal | HIGH |
| MultiThreadedExecutor | Run action execute callback in parallel with timer callbacks | HIGH |
| ReentrantCallbackGroup | Allow action server to execute while PSD timer runs | HIGH |
### Nav2 / Costmap Integration
| Technology | Approach | Confidence |
|------------|---------|------------|
| nav_msgs/OccupancyGrid | Publish RF heatmap as standard occupancy grid | HIGH |
| Static subscriber in Nav2 | Costmap subscribes to /hackrf/rf_occupancy | MEDIUM |
| C++ costmap layer plugin | Custom layer that reads /hackrf/detections | MEDIUM |
### Threading Architecture (IQ Pipeline)
- PSD processor: existing timer callback, drains `fft_q` (currently called `_iq_queue`)
- SigMF recorder: new daemon thread, drains `recorder_q` when recording active
- RX callback: `put_nowait()` to all active queues — no other work
- Watchdog: reads `_last_rx_time` float (atomic under CPython GIL) — no locks
### Supporting Libraries
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| tf2_ros | bundled ROS2 | TF frame broadcast (antenna→base_link) | Phase 1 — stamped messages |
| geometry_msgs | bundled ROS2 | Pose in SigMF metadata | Phase 2 — IQ recording |
| nav_msgs | bundled ROS2 | OccupancyGrid for RF heatmap | Phase 2 — occupancy grid |
| diagnostic_updater | bundled ROS2 | /diagnostics topic — already in use | Existing |
| scipy.stats | >=1.11 | chi2.ppf for CFAR alpha calculation | Phase 1 — CFAR detector |
| scipy.ndimage | >=1.11 | uniform_filter1d for sliding noise estimate | Phase 1 — CFAR detector |
## Alternatives Considered
| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| FFT library | scipy.fft | np.fft | np.fft uses FFTPACK, no SIMD on ARM |
| FFT library | scipy.fft | pyfftw | pyfftw requires libfftw3 build dep + wisdom; marginal gain over scipy.fft for single-size 4096-pt transforms; gate behind flag |
| FFT library | scipy.fft | CuPy/cuFFT | GPU launch overhead dominates for single small FFTs; mixed results on Jetson Orin; defer to Phase 3 |
| IQ format | SigMF (sigmf) | Raw .cf32 / .iq | No metadata → unrecoverable six months later |
| IQ format | SigMF (sigmf) | HDF5 | Heavier dependency, no SDR tool ecosystem |
| CFAR | Custom numpy | pyCFAR-lib | Radar-centric (2D), not 1D spectrum; more dep complexity than value |
| CFAR | Custom numpy | OpenRadar | mmWave radar DSP module, not SDR-spectrum applicable |
| Costmap | OccupancyGrid publish | C++ costmap layer plugin | Plugin requires C++ package; OccupancyGrid covers Phase 2 needs |
| Transport | Standard DDS | iceoryx zero-copy | Variable-length float arrays disqualify zero-copy; premature optimization |
| Executor | MultiThreadedExecutor | SingleThreadedExecutor | Single-threaded blocks PSD timer during sweep action; known ros2 bug #1609 |
## Installation Changes Required
# In Dockerfile — add to existing apt install line:
# Python deps — add to setup.py install_requires:
# scipy is the only new hard dependency for Phase 1.
# sigmf is needed for Phase 2 IQ recording action server.
## Migration Path from Current Stack
| Current | Target | Change Type | Phase |
|---------|--------|-------------|-------|
| `np.fft.fft` | `scipy.fft.fft` | Drop-in replace, 1-line per call | Phase 1 |
| `np.fft.fftshift` | `scipy.fft.fftshift` | Drop-in replace | Phase 1 |
| `Float32MultiArray` for PSD | `SpectrumStamped.msg` custom msg | New message type + custom interfaces package | Phase 1 |
| `rclpy.spin()` | `MultiThreadedExecutor` | Executor upgrade in main() | Phase 1 (before first action server) |
| Sweep service | Sweep action server | New action definition, replace service | Phase 2 |
| No IQ recording | SigMF recorder daemon thread | New thread + sigmf dep | Phase 2 |
## Sources
- scipy ARM NEON vectorization: [scipy PR #12779](https://github.com/scipy/scipy/pull/12779)
- pyFFTW v0.15.1 with ARM64 wheels: [pyFFTW releases](https://github.com/pyFFTW/pyFFTW/releases)
- CycloneDDS zero-copy constraints: [rmw_cyclonedds shared_memory_support.md](https://github.com/ros2/rmw_cyclonedds/blob/rolling/shared_memory_support.md)
- CuPy on Jetson Orin ARM64: [NVIDIA developer forums](https://forums.developer.nvidia.com/t/challenges-in-achieving-optimal-gpu-performance-for-fft-on-nvidia-jetson-agx-orin/303418), [CuPy issue #8151](https://github.com/cupy/cupy/issues/8151)
- SigMF Python library: [sigmf/sigmf-python](https://github.com/sigmf/sigmf-python), [PyPI](https://pypi.org/project/SigMF/)
- CFAR theory: O'Donoughue, *Emitter Detection and Geolocation for Electronic Warfare* (Artech 2019), Ch. 2 — per vault/concepts/detection-theory-np-and-cfar.md
- SigMF streaming write pattern: vault/concepts/sigmf-iq-recording.md
- IQ pipeline threading rules: vault/concepts/sdr-iq-pipeline-threading.md
- Nav2 costmap plugin (C++ requirement): [Nav2 docs](https://docs.nav2.org/plugin_tutorials/docs/writing_new_costmap2d_plugin.html)
- Action server MultiThreadedExecutor: [ROS2 Humble executor docs](https://docs.ros.org/en/humble/Concepts/Intermediate/About-Executors.html)
- Known action server bug: [ros2/ros2 #1609](https://github.com/ros2/ros2/issues/1609)
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, or `.github/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
