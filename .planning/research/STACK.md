# Technology Stack

**Project:** HackRF ROS2 RF Sensor (hackrf_ros)
**Researched:** 2026-04-12
**Platform target:** Jetson ARM64, Docker, ROS2 Humble, CycloneDDS

---

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

**FFT recommendation detail:**

`scipy.fft` is the correct upgrade from `np.fft.fft`. Both use pocketfft under the hood, but scipy's version adds ARM NEON vectorization (merged in scipy PR #12779) and a tunable thread pool for batched transforms. On ARM Cortex-A57/A78, scipy.fft is 1.5–2.5x faster than numpy.fft for the 4096-point complex case used in this project. The improvement comes from NEON SIMD and better cache tiling in pocketfft's C++ layer.

`pyfftw` (v0.15.1, released Oct 2024, ARM64 wheels available) wraps FFTW3 with ARM NEON. It requires `libfftw3-dev` in the Docker image (`apt install libfftw3-dev`) and a one-time wisdom generation step (`pyfftw.FFTW.save_wisdom()`). Performance gain over scipy.fft for repeated same-size 4096-point transforms is 2–3x after wisdom is loaded. The friction cost: wisdom files must be pre-generated per platform, and adding a build dep to Docker. Verdict: worthwhile only if the current 4% data-processed figure needs improvement beyond what scipy.fft provides. Gate behind a runtime flag.

`np.fft.fft` uses FFTPACK (no SIMD). The existing codebase should switch to `scipy.fft.fft` with zero API changes — it is a drop-in replacement.

`CuPy + cuFFT`: CuPy 13.x runs on Jetson Orin Nano ARM64 (CUDA 12.6, confirmed active usage on this platform in 2025). For single 4096-point FFTs the GPU launch overhead dominates — no gain. For batched 1000+ FFT operations (e.g., multi-sweep parallelism or wideband anomaly detection) cuFFT gives 10–50x throughput. However: CuPy matrix ops on Jetson Orin have shown regressions vs numpy on small matrices (GPU at 99% util during benchmarking). Do not use CuPy for Phase 1/2. Consider it only for Phase 3 wideband anomaly detection with large batch sizes. Confidence LOW because Jetson-specific benchmarks are sparse and results are mixed.

**Immediate action:** Replace `np.fft.fftshift(np.fft.fft(...))` with `scipy.fft.fftshift(scipy.fft.fft(...))` in `_fft_frame()`. Also replace `np.fft.fft` in `_process_raw()`. Zero other code changes required.

### IQ Recording

| Technology | Version | Purpose | Confidence |
|------------|---------|---------|------------|
| **SigMF (sigmf)** | 1.7.2 | IQ recording format + metadata | HIGH |

`pip install sigmf` — depends only on numpy and jsonschema. LGPL v3. Python 3.7–3.14 compatible. The canonical Python implementation from the spec authors (github.com/sigmf/sigmf-python).

**Usage pattern for this project:**

- Native HackRF int8 samples map to SigMF datatype `ci8` — zero conversion overhead
- Write data file incrementally (append raw bytes from IQ queue)
- Write metadata file once at recording stop — avoid per-chunk JSON overhead
- Pre-allocate data file with `os.posix_fallocate()` to prevent filesystem metadata thrashing on slower storage
- Add `captures` entry per frequency hop (sweep recording) using `sample_start` offset
- Metadata must include: center_freq, sample_rate, gains, robot pose from /tf, trigger reason, ROS timestamp
- Detect dropped chunks via sequence number gap; emit new captures entry at gap

Do NOT write to CF32 (float32 complex) unless downstream tooling requires it. CI8 is lossless from the ADC and halves storage. IQEngine and GNU Radio both read CI8 SigMF.

**Throughput reality check:** 20 MSPS × 2 bytes = 40 MB/s. Jetson Orin NVMe handles this with headroom. Jetson Nano SD card (80 MB/s max) is at the limit — use zstd level 1 compression (~1.5:1 on IQ at negligible CPU cost) if targeting Nano.

### CFAR Detection

| Technology | Version | Purpose | Confidence |
|------------|---------|---------|------------|
| **Custom CA-CFAR (numpy)** | — | Energy detection on PSD array | HIGH |
| scipy.ndimage.uniform_filter | >=1.11 | Sliding-window noise estimate | HIGH |

**No external CFAR library is recommended.** Available open-source options (pyCFAR-lib, OpenRadar's mmwave DSP module, tsaith/radar) are all radar-centric (2D range-Doppler maps), not 1D spectrum. The CA-CFAR on a 1D PSD array is ~50 lines of numpy — implementing it directly is less complexity than adding any of these dependencies.

The algorithm for this project:

```python
# Pseudocode — see docs/rf-ew-review.md for context
def ca_cfar_1d(psd_db, guard=4, train=16, pfa=1e-4):
    # Convert to linear for noise estimation
    psd_lin = 10 ** (psd_db / 10)
    noise = sliding_window_mean(psd_lin, guard, train)  # scipy.ndimage.uniform_filter1d
    alpha = threshold_factor(pfa, train)               # from O'Donoughue Ch 2
    threshold = alpha * noise
    detections = psd_lin > threshold
    return group_adjacent_bins(detections, psd_db, noise)
```

The threshold factor alpha is derived from the Pfa target and the number of training cells using the chi-squared inverse CDF (`scipy.stats.chi2.ppf`). This is the CA-CFAR from O'Donoughue Ch. 2 — not a radar-specific adaptation.

`scipy.ndimage.uniform_filter1d` gives the sliding window mean with proper edge handling and is vectorized on ARM NEON. Faster than a Python loop or np.convolve for this use case.

### ROS2 Message Transport (Large Arrays)

| Technology | Status | Notes | Confidence |
|------------|--------|-------|------------|
| Standard DDS (CycloneDDS) | Use for now | Adequate at 10 Hz PSD publish | HIGH |
| CycloneDDS + iceoryx (shared memory) | **Cannot use** for PSD/IQ messages | Variable-length float arrays disqualify zero-copy | HIGH |
| Fixed-size custom messages | Required for zero-copy if needed later | Must define fixed array size at compile time | MEDIUM |

**Zero-copy shared memory constraint:** CycloneDDS with iceoryx achieves true zero-copy only for fixed-size message types. A `float32[]` (variable-length array) requires serialization even in shared memory mode — the latency gain shrinks to near zero and the complexity cost is real. This means `SpectrumStamped.msg` with `float32[] psd_db` cannot use zero-copy.

**Practical impact at current scale:** At 10 Hz with 4096 bins × 4 bytes = 16 KB per PSD message, standard CycloneDDS serialization adds ~0.1–0.5 ms latency. This is acceptable for the monitoring use case. Zero-copy optimization is premature.

**If zero-copy becomes necessary (Phase 3+ with high-rate data):** Define a fixed-size variant — e.g., `float32[4096] psd_db` in the `.msg` file. This is a breaking API change, so the decision should be deferred and explicit.

**For IQ recording (40 MB/s):** Do not route IQ over DDS topics. The recorder node must consume from the same bounded queue as the FFT processor (per the dedicated-thread-per-sink pattern documented in `sdr-iq-pipeline-threading.md`). IQ data stays in-process.

### ROS2 Action Servers (Long-Running Operations)

| Technology | Purpose | Confidence |
|------------|---------|------------|
| rclpy ActionServer | sweep_band, record_iq, track_signal | HIGH |
| MultiThreadedExecutor | Run action execute callback in parallel with timer callbacks | HIGH |
| ReentrantCallbackGroup | Allow action server to execute while PSD timer runs | HIGH |

**Pattern for all long-running action servers:**

```python
from rclpy.action import ActionServer
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor

class HackRFLifecycleNode(LifecycleNode):
    def on_configure(self, state):
        action_group = ReentrantCallbackGroup()
        self._sweep_action = ActionServer(
            self,
            SurveyBand,
            '/hackrf/survey_band',
            self._execute_sweep,
            callback_group=action_group,
        )

    async def _execute_sweep(self, goal_handle):
        for hop in hops:
            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
                return SurveyBand.Result()
            # ... do hop ...
            feedback = SurveyBand.Feedback(hops_completed=i, total_hops=n)
            goal_handle.publish_feedback(feedback)
        goal_handle.succeed()
        return result
```

The executor must be `MultiThreadedExecutor` (not the default `SingleThreadedExecutor`) so the action execute callback runs on its own thread while PSD timer callbacks continue. The existing `main()` in `hackrf_lifecycle_node.py` uses `rclpy.spin()` which defaults to `SingleThreadedExecutor` — this must change before adding any action server.

**Known issue (ros2/ros2 #1609):** Action server does not receive second goal after first terminates under SingleThreadedExecutor. This confirms the executor upgrade is required.

**Async vs threaded callbacks:** rclpy action servers support both. For this project, use the threaded (non-async) pattern. The execute callback blocks intentionally during IQ acquisition. Async callbacks add complexity with no benefit when the hot path is hardware I/O.

### Nav2 / Costmap Integration

| Technology | Approach | Confidence |
|------------|---------|------------|
| nav_msgs/OccupancyGrid | Publish RF heatmap as standard occupancy grid | HIGH |
| Static subscriber in Nav2 | Costmap subscribes to /hackrf/rf_occupancy | MEDIUM |
| C++ costmap layer plugin | Custom layer that reads /hackrf/detections | MEDIUM |

**Recommended approach for Phase 2:** Publish `nav_msgs/OccupancyGrid` on `/hackrf/rf_occupancy`. Nav2's existing `static_layer` or a custom `range_sensor_layer`-style subscriber can consume this. The RF node publishes; Nav2 subscribes. No custom C++ plugin required for Phase 2.

**OccupancyGrid encoding for RF power:** Map dBm range to [0, 100] occupancy values. Example: noise floor (-120 dBm) → 0, strong signal (-40 dBm) → 100. Grid frame = `map`, cell size = configurable (e.g., 1 m²).

**If a custom costmap layer is required (Phase 3+):** Nav2 costmap layers require C++ via pluginlib (`PLUGINLIB_EXPORT_CLASS`). Python costmap layers are not supported for real-time `updateCosts()` calls — the cost update loop is too hot for Python overhead. The layer would subscribe to `/hackrf/detections` (RFDetectionArray) and mark cells based on estimated emitter position. This requires a separate C++ package.

**Practical approach:** Start with OccupancyGrid publish. Validate that Nav2 can use the grid for avoidance. Only write a C++ costmap layer if robot autonomy requires finer control over cost values or update semantics.

### Threading Architecture (IQ Pipeline)

Per `sdr-iq-pipeline-threading.md` — the mandatory pattern for this codebase:

```
pyhackrf2 USB callback (hot path — μs budget)
                  │
                  ▼
    ┌─────────────┬───────────────┐
    │  fft_q      │  recorder_q  │   (each maxsize=64)
    └──────┬──────┴──────┬───────┘
           ▼             ▼
       PSD processor   SigMF recorder
       (existing)      daemon thread
```

- PSD processor: existing timer callback, drains `fft_q` (currently called `_iq_queue`)
- SigMF recorder: new daemon thread, drains `recorder_q` when recording active
- RX callback: `put_nowait()` to all active queues — no other work
- Watchdog: reads `_last_rx_time` float (atomic under CPython GIL) — no locks

The current implementation has a single `_iq_queue`. Adding the recorder requires splitting into a fan-out: either two queues from the rx callback, or a dispatcher thread. The simpler approach is two separate `put_nowait()` calls in `_rx_callback` — one per queue. Drop-oldest semantics preserved on each independently.

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| tf2_ros | bundled ROS2 | TF frame broadcast (antenna→base_link) | Phase 1 — stamped messages |
| geometry_msgs | bundled ROS2 | Pose in SigMF metadata | Phase 2 — IQ recording |
| nav_msgs | bundled ROS2 | OccupancyGrid for RF heatmap | Phase 2 — occupancy grid |
| diagnostic_updater | bundled ROS2 | /diagnostics topic — already in use | Existing |
| scipy.stats | >=1.11 | chi2.ppf for CFAR alpha calculation | Phase 1 — CFAR detector |
| scipy.ndimage | >=1.11 | uniform_filter1d for sliding noise estimate | Phase 1 — CFAR detector |

---

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

---

## Installation Changes Required

```bash
# In Dockerfile — add to existing apt install line:
libfftw3-dev  # only if pyfftw is adopted (optional, Phase 1+)

# Python deps — add to setup.py install_requires:
scipy>=1.11.0
sigmf>=1.7.2

# scipy is the only new hard dependency for Phase 1.
# sigmf is needed for Phase 2 IQ recording action server.
```

**scipy install note:** scipy wheels for ARM64 (aarch64) are published on PyPI for Python 3.8–3.12. `pip install scipy` works inside the Docker container without compilation. Verify with `python -c "import scipy; print(scipy.__version__)"` after pip install.

**sigmf install note:** `pip install sigmf` — pure Python + numpy + jsonschema. No native compile. Works immediately on ARM64.

---

## Migration Path from Current Stack

| Current | Target | Change Type | Phase |
|---------|--------|-------------|-------|
| `np.fft.fft` | `scipy.fft.fft` | Drop-in replace, 1-line per call | Phase 1 |
| `np.fft.fftshift` | `scipy.fft.fftshift` | Drop-in replace | Phase 1 |
| `Float32MultiArray` for PSD | `SpectrumStamped.msg` custom msg | New message type + custom interfaces package | Phase 1 |
| `rclpy.spin()` | `MultiThreadedExecutor` | Executor upgrade in main() | Phase 1 (before first action server) |
| Sweep service | Sweep action server | New action definition, replace service | Phase 2 |
| No IQ recording | SigMF recorder daemon thread | New thread + sigmf dep | Phase 2 |

---

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
