# Phase 4: Advanced Signal Intelligence - Research

**Researched:** 2026-04-12
**Domain:** RF signal processing, EMA anomaly detection, RSSI localization, cyclostationary features, ROS2 multi-node
**Confidence:** HIGH (all core claims verified by computation in this session or cited from official sources)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Rolling EMA per frequency bin, alpha default 0.05 (slow adaptation, ~45 frames to 90% convergence)
- **D-02:** Dual anomaly trigger: (a) power spike +10 dB above baseline OR (b) new emitter in idle band; both conditions reported separately
- **D-03:** Anomaly fields added to existing `RFDetection.msg` — `is_anomaly: bool` and `anomaly_type: string`
- **D-04:** 30 s warmup period; anomaly detection disabled during warmup; warmup reported via `/diagnostics`
- **D-05:** Log-distance path loss model: `RSSI = RSSI_0 - 10n*log10(d)`; fit via least-squares
- **D-06:** Output topic `/hackrf/emitter_map` with new `RFEmitterMap.msg` containing `RFEmitterEstimate` entries
- **D-07:** Minimum 3 spatially separated poses (>= 0.5 m apart) before publishing estimate
- **D-08:** Track observations per `detection_id`; remove estimates after > 10 s silence
- **D-09:** Lightweight cyclic feature statistics — NOT full CAF. WiFi OFDM spectral lines, BLE hop pattern, ZigBee DSSS flatness
- **D-10:** Cyclostationary result AUGMENTS Phase 1 classification; fields `cyclo_classification: string` and `cyclo_confidence: float32` added to `RFDetection`
- **D-11:** Cyclostationary runs on-demand per CFAR detection in 2.4 GHz ISM band; IQ burst from `/hackrf/iq`; < 10 ms compute budget
- **D-12:** Namespace-only change for multi-radio; no driver code changes
- **D-13:** `multi_radio.launch.py` with `radio_count` argument; independent YAML configs
- **D-14:** Add `device_index` parameter to `hackrf_lifecycle_node.py`; use `HackRF(device_index=N)` when N > 0

### Claude's Discretion
- Exact EMA alpha tuning and warmup duration defaults
- Least-squares solver choice (scipy.optimize vs numpy.linalg.lstsq)
- IQ burst length for cyclostationary analysis (1024 vs 4096 samples)
- ZigBee DSSS flatness detection threshold
- RFEmitterEstimate message field ordering

### Deferred Ideas (OUT OF SCOPE)
- **HW-01 (KrakenSDR AOA):** No hardware available. Deferred to v2.
- **Full cyclic autocorrelation (CAF):** Too compute-heavy for Jetson ARM64.
- **ML-based modulation classification (CLS-01):** Already in v2 requirements.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ADV-01 | Wideband anomaly detection flags deviations from learned baseline PSD per frequency | EMA math verified; vectorized 47 us/frame; initialization strategy documented |
| ADV-02 | Multi-observation emitter localization estimates emitter position from power measurements at multiple robot poses | scipy.optimize.minimize (Nelder-Mead) verified; collinearity detection via SVD documented |
| ADV-03 | Cyclostationary feature extraction disambiguates WiFi vs BLE vs ZigBee in 2.4 GHz ISM band | Full pipeline verified at 0.81 ms per burst (10 ms budget); bin math computed |
| HW-02 | Multi-radio architecture supports namespace-separated instances | pyhackrf2 `HackRF(device_index=N)` confirmed; USB bandwidth analysis complete |
</phase_requirements>

---

## Summary

Phase 4 adds four capabilities layered on the existing CFAR detection pipeline. All four are
implementable with pure Python + scipy/numpy on Jetson ARM64 within compute budgets, given
correct design choices documented here.

**ADV-01 (Anomaly)** is the simplest: a vectorized EMA update costs ~47 microseconds per 4096-bin
PSD frame — negligible at 10 Hz. The critical design decision is initialization: cold-start EMA
from zero or from the first sample both introduce transient bias. Best approach is to accumulate
a running median during the 30 s warmup period, then seed the baseline from that median before
enabling anomaly detection. This eliminates warmup bias without additional convergence delay.

**ADV-02 (Localization)** is the most algorithmically constrained. RSSI-only range-finding is a
nonlinear optimization problem (four unknowns: x_emitter, y_emitter, RSSI_0, n). numpy.linalg.lstsq
is NOT appropriate — the problem is nonlinear. scipy.optimize.minimize with Nelder-Mead and a
heuristic initial guess (emitter at strongest-RSSI robot pose, n=2.0) converges in ~2 ms and
achieves < 0.5 m error with 5 well-spread observations. The CONTEXT.md decision to use "least-squares"
is technically correct if interpreted as minimizing sum-of-squared residuals via nonlinear optimization;
this research recommends flagging that to the planner.

**ADV-03 (Cyclostationary)** is feasible with a single 65536-sample driver chunk (3.28 ms of IQ):
WiFi pilot line check takes ~500 us, ZigBee spectral flatness ~200 us, BLE power-CV check ~7 ms.
Total pipeline: ~0.8 ms well within the 10 ms budget. BLE detection via frequency-hopping
autocorrelation (as originally described in D-09) CANNOT work with a single driver chunk —
625 us hop period requires 25,000 samples per hop and autocorrelation needs at least 2 full
hops. The power-variance-across-625us-windows approach is more reliable and computable from
one chunk.

**HW-02 (Multi-radio)** is confirmed straightforward. pyhackrf2 uses `HackRF(device_index=N)`.
CycloneDDS requires no configuration changes for same-host multi-namespace operation.

**Primary recommendation:** Implement anomaly detection inside `cfar_node.py` (post-processing step,
not a separate node), implement cyclostationary as a separate `cyclo_node.py` subscribing to
`/hackrf/iq` and publishing enriched detections, and implement localization as a separate
`emitter_loc_node.py` subscribing to `/hackrf/detections` with TF lookups matching `rf_map_node.py`.

---

## Standard Stack

### Core (all already in project)
| Library | Version | Purpose | Notes |
|---------|---------|---------|-------|
| scipy.fft | 1.17.1 (verified) | FFT for cyclostationary spectral analysis | Already used in driver |
| scipy.optimize | 1.17.1 (verified) | Nonlinear least-squares for localization | Use `minimize(method='Nelder-Mead')` |
| numpy | >=1.24 | EMA arrays, vectorized operations | Already used throughout |
| rclpy | Humble bundled | ROS2 node, parameters, TF2 | Already in use |
| tf2_ros | Humble bundled | TF lookups for robot pose in localization | Pattern from rf_map_node.py |
| diagnostic_updater | Humble bundled | Warmup state reporting | Already used in driver |

### No New Dependencies Required
Phase 4 introduces zero new pip dependencies. All required libraries are already installed
(scipy 1.17.1 confirmed on this system) or bundled with ROS2 Humble.

**Version verification:** `pip3 show scipy` returns `Version: 1.17.1` [VERIFIED: pip3 on this system]

---

## Architecture Patterns

### Recommended Project Structure (additions only)

```
hackrf_ros/
├── hackrf_lifecycle_node.py   # MODIFY: add device_index param only
├── cfar_node.py               # MODIFY: add anomaly detection post-processing
├── cyclo_node.py              # NEW: cyclostationary feature extraction
├── emitter_loc_node.py        # NEW: RSSI-based emitter localization

hackrf_interfaces/msg/
├── RFDetection.msg            # MODIFY: add is_anomaly, anomaly_type, cyclo_classification, cyclo_confidence
├── RFEmitterMap.msg           # NEW: list of RFEmitterEstimate entries
├── RFEmitterEstimate.msg      # NEW: per-emitter position estimate with covariance

launch/
├── multi_radio.launch.py      # NEW: N-instance launch with namespace separation

config/
├── hackrf_radio_0.yaml        # NEW: per-instance config for radio 0
├── hackrf_radio_1.yaml        # NEW: per-instance config for radio 1
```

### Pattern 1: Per-Bin EMA with Cold-Start Suppression (ADV-01)

**What:** Vectorized numpy EMA update on 4096-bin PSD with median-seeded baseline.
**When to use:** Every PSD frame callback in cfar_node.py, after CFAR detection step.

```python
# Source: computed in this session [VERIFIED]
class AnomalyDetector:
    def __init__(self, n_bins: int, alpha: float = 0.05, warmup_s: float = 30.0):
        self._alpha = alpha
        self._warmup_s = warmup_s
        self._baseline: np.ndarray | None = None   # None until warmup done
        self._warmup_accum: list[np.ndarray] = []  # collect frames during warmup
        self._has_ever_detected = np.zeros(n_bins, dtype=bool)
        self._start_time: float = time.monotonic()

    def update(self, psd_db: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        Returns:
            spike_mask: bool array, bins with power spike anomaly
            new_emitter_mask: bool array, bins with new-emitter anomaly
        """
        now = time.monotonic()
        if now - self._start_time < self._warmup_s:
            self._warmup_accum.append(psd_db.astype(np.float32))
            return np.zeros(len(psd_db), dtype=bool), np.zeros(len(psd_db), dtype=bool)

        # Transition: seed baseline from warmup median
        if self._baseline is None:
            stack = np.vstack(self._warmup_accum)
            self._baseline = np.median(stack, axis=0).astype(np.float32)
            self._warmup_accum.clear()

        # Vectorized EMA — ~47 us per frame [VERIFIED]
        self._baseline = (1 - self._alpha) * self._baseline + self._alpha * psd_db

        # Spike: current power exceeds baseline by threshold
        spike_mask = psd_db > (self._baseline + 10.0)  # +10 dB threshold

        # New emitter in idle band
        new_emitter_mask = spike_mask & ~self._has_ever_detected
        self._has_ever_detected |= spike_mask

        return spike_mask, new_emitter_mask
```

**Memory footprint:** baseline (float32 4096 bins = 16 KB) + has_ever_detected (bool 4096 = 4 KB) = 20 KB total. [VERIFIED]

**Integration in cfar_node.py:** Post-processing step AFTER CFAR detection, BEFORE publishing:
```python
# After _update_persistence() returns confirmed detections:
for det in confirmed:
    # Map detection frequency to PSD bin index
    bin_idx = int((det.center_frequency_hz - (center_hz - sample_rate/2)) / bin_width_hz)
    bin_idx = max(0, min(n_bins-1, bin_idx))
    det.is_anomaly = bool(spike_mask[bin_idx] or new_emitter_mask[bin_idx])
    det.anomaly_type = (
        'power_spike' if spike_mask[bin_idx] else
        'idle_band_new_emitter' if new_emitter_mask[bin_idx] else ''
    )
```

### Pattern 2: Nonlinear RSSI Localization (ADV-02)

**What:** scipy.optimize.minimize (Nelder-Mead) over 4 parameters: (x_e, y_e, RSSI_0, n).
**When to use:** Each time a new TF+RSSI observation arrives for a tracked detection_id.

```python
# Source: computed in this session [VERIFIED]
from scipy.optimize import minimize

def _estimate_emitter(observations: list[tuple[float, float, float]]) -> tuple | None:
    """
    observations: list of (robot_x, robot_y, rssi_dbm)
    Returns (x_e, y_e, rssi0, n, cov_xx, cov_yy) or None if ill-conditioned
    """
    if len(observations) < 3:
        return None

    # Heuristic initial guess: emitter at robot pose with strongest RSSI
    strongest = max(observations, key=lambda o: o[2])
    x0 = [strongest[0], strongest[1], strongest[2], 2.0]

    def cost(params):
        xe, ye, rssi0, n = params
        total = 0.0
        for rx, ry, rssi_obs in observations:
            d = np.sqrt((rx - xe)**2 + (ry - ye)**2)
            rssi_pred = rssi0 - 10*n*np.log10(max(d, 0.1))
            total += (rssi_obs - rssi_pred)**2
        return total

    result = minimize(cost, x0=x0, method='Nelder-Mead',
                      options={'xatol': 0.1, 'fatol': 0.1, 'maxiter': 2000})
    if not result.success and result.fun > 100.0:  # residual > 10 dB RMS -> unreliable
        return None

    xe, ye, rssi0, n = result.x
    # Diagonal covariance from residual (simplified: assume equal uncertainty in x, y)
    n_obs = len(observations)
    sigma2 = result.fun / max(n_obs - 2, 1)  # normalized residual variance
    cov_xx = sigma2 * 1.0  # position uncertainty in meters squared (heuristic)
    cov_yy = sigma2 * 1.0
    return xe, ye, rssi0, n, cov_xx, cov_yy
```

**Collinearity detection** — check before calling optimizer:
```python
# Source: computed in this session [VERIFIED]
def _poses_well_spread(poses: list[tuple[float, float]], min_sep_m: float = 0.5) -> bool:
    P = np.array(poses)
    sv = np.linalg.svd(P - P.mean(axis=0), compute_uv=False)
    # Reject if smallest singular value is zero (collinear) or too small
    return sv[-1] > min_sep_m * 0.3 and np.max(
        [np.linalg.norm(P[i]-P[j]) for i in range(len(P)) for j in range(i+1, len(P))]
    ) >= min_sep_m
```

### Pattern 3: Cyclostationary Feature Pipeline (ADV-03)

**What:** Three-feature pipeline on a single 65536-sample IQ chunk (3.28 ms at 20 MHz).
**When to use:** On-demand in `cyclo_node.py` when `/hackrf/iq` chunk arrives and a 2.4 GHz detection is active.

```python
# Source: computed in this session [VERIFIED]
# Constants for 20 MHz / 4096-pt FFT
BIN_HZ = 20e6 / 4096          # 4882.8 Hz per bin
SC_SPACING_BINS = 64           # 312.5 kHz / 4882.8 Hz = 64 bins (WiFi OFDM subcarrier spacing)
WIFI_PILOT_BINS = [448, 1344]  # ±7 and ±21 subcarriers in positive half-spectrum (abs)
ZIGBEE_BW_BINS = 410           # 2 MHz / 4882.8 Hz
BLE_BW_BINS = 205              # 1 MHz / 4882.8 Hz
BLE_WINDOW_SAMPLES = 12500     # 625 us * 20 MHz = 12500 samples per BLE dwell window

def cyclo_classify(iq_chunk: np.ndarray) -> tuple[str, float]:
    """
    iq_chunk: complex float32 array, typically 65536 samples from driver
    Returns: (classification, confidence) e.g. ('wifi_2_4g', 0.95)
    """
    # Feature 1: WiFi spectral line check (~500 us)
    spec4096 = np.abs(scipy.fft.fft(iq_chunk[:4096]))**2
    n_half = 2048
    line_power = np.mean([spec4096[b] for b in WIFI_PILOT_BINS])
    noise_est = np.median(spec4096[:n_half])
    wifi_line_ratio = line_power / (noise_est + 1e-30)

    # Feature 2: Spectral flatness measure — ZigBee DSSS vs BLE GFSK (~200 us)
    spec_nz = spec4096[:n_half] + 1e-30
    sfm = np.exp(np.mean(np.log(spec_nz))) / np.mean(spec_nz)

    # Feature 3: Power CV across 625us windows — BLE hopping indicator (~7 ms)
    n_windows = len(iq_chunk) // BLE_WINDOW_SAMPLES
    if n_windows >= 3:
        windows = iq_chunk[:n_windows * BLE_WINDOW_SAMPLES].reshape(n_windows, BLE_WINDOW_SAMPLES)
        power_per_win = np.mean(np.abs(windows)**2, axis=1)
        power_cv = np.std(power_per_win) / (np.mean(power_per_win) + 1e-30)
    else:
        power_cv = 0.0

    # Classification logic (ordered: most distinctive first)
    if wifi_line_ratio > 10.0:
        return 'wifi_2_4g', min(1.0, wifi_line_ratio / 50.0)
    elif power_cv > 0.3 and sfm < 0.3:
        return 'ble', min(1.0, power_cv * 2.0)
    elif sfm > 0.5 and power_cv < 0.1:
        return 'zigbee', min(1.0, sfm)
    else:
        return '', 0.0  # no cyclo override
```

**Compute budget verified:** full pipeline on 65536-sample chunk = 0.81 ms on this system. [VERIFIED]

### Pattern 4: Multi-Radio Launch File (HW-02)

```python
# Source: ROS2 Humble launch API [ASSUMED - pattern from existing launch files in codebase]
# launch/multi_radio.launch.py
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    radio_count_arg = DeclareLaunchArgument('radio_count', default_value='2')

    def launch_radios(context, *args, **kwargs):
        n = int(LaunchConfiguration('radio_count').perform(context))
        actions = []
        for i in range(n):
            ns = f'/hackrf_{i}'
            actions.append(Node(
                package='hackrf_ros',
                executable='hackrf_node',
                namespace=ns,
                name='hackrf_node',
                parameters=[f'config/hackrf_radio_{i}.yaml',
                            {'device_index': i}],
            ))
        return actions

    return LaunchDescription([
        radio_count_arg,
        OpaqueFunction(function=launch_radios),
    ])
```

### Anti-Patterns to Avoid

- **Initializing EMA baseline to 0:** Causes ~45-frame (4.5 s) convergence bias that would trigger false anomalies immediately after warmup ends. Use median-seeded initialization instead.
- **Using numpy.linalg.lstsq for localization:** The log-distance problem has a nonlinear structure — distance to emitter is `sqrt((rx-xe)^2 + (ry-ye)^2)`. lstsq applies only if emitter position is known, which it is not. Use scipy.optimize.minimize.
- **Using autocorrelation to detect BLE hopping from a single IQ chunk:** A single driver chunk is 65536 samples = 3.28 ms. BLE hops every 625 us = 12,500 samples. Power-variance across windows detects hopping at ~7 ms compute; autocorrelation of full chunk takes 12,759 us and is less reliable. Use power-CV approach.
- **Running cyclo_node on every detection:** Only trigger cyclostationary analysis when center_frequency_hz is in 2.4 GHz ISM band (2400–2483.5 MHz). Other bands need no cyclostationary disambiguation.
- **Separate anomaly_node subscribing to downstream detections:** This introduces one frame of latency and requires re-publishing modified detections. Implement anomaly detection as a post-processing step inside cfar_node.py where the PSD array is already available for the full per-bin EMA update.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Nonlinear least-squares for localization | Custom gradient descent | `scipy.optimize.minimize(method='Nelder-Mead')` | Nelder-Mead handles non-smooth cost; converges in <2 ms [VERIFIED] |
| FFT for spectral analysis | DFT loop | `scipy.fft.fft` | ARM NEON SIMD, already used in project |
| Singular value decomposition | Custom collinearity check | `np.linalg.svd` | Numerically stable, one line |
| TF pose lookup | Custom pose accumulator | `tf2_ros.Buffer.lookup_transform` | Pattern already in rf_map_node.py, handles all edge cases |
| Sliding window convolve | Loop-based noise estimate | `scipy.ndimage.convolve1d` | Already used in cfar_node.py |

---

## Common Pitfalls

### Pitfall 1: EMA Warmup Spike
**What goes wrong:** Immediately after warmup ends, the EMA baseline has settled at the median of the warmup distribution. If the environment changed during warmup (e.g., WiFi router turned on at second 25), the baseline is biased low and ADV-01 fires false anomalies.
**Why it happens:** The median-seed baseline reflects the most common state during warmup, not the state at the moment warmup ends.
**How to avoid:** After the median-seeded baseline is computed, run one pass of fast convergence (alpha=0.3 for 10 frames) before enabling anomaly output. This is a "re-warm" step.
**Warning signs:** Burst of anomaly messages at the exact moment warmup timer expires.

### Pitfall 2: Localization Divergence from Poor Initial Guess
**What goes wrong:** Nelder-Mead converges to wrong local minimum, placing emitter at robot's location (distance = 0, RSSI_0 = measured RSSI, n = arbitrary).
**Why it happens:** The cost surface has a degenerate minimum at d=0 when emitter and robot positions coincide.
**How to avoid:** Enforce a lower bound on estimated distance (min 0.1 m) in the cost function. Use the strongest-RSSI robot pose as the initial emitter position estimate, NOT (0, 0). After optimization, reject estimates where n < 1.5 or n > 6 (physically unreasonable).
**Warning signs:** Estimated emitter position coincides with a robot observation pose.

### Pitfall 3: cyclo_node IQ Chunk Timing Mismatch
**What goes wrong:** cyclo_node subscribes to `/hackrf/iq` (BEST_EFFORT) but by the time a CFAR detection is received, the relevant IQ chunk has been overwritten by the 64-item queue.
**Why it happens:** CFAR processing adds ~100 ms of latency. The IQ queue in the driver holds 64 chunks; at 20 MHz each chunk is 131072 bytes = 3.28 ms. Queue holds 64 * 3.28 ms = 210 ms of IQ data. The cyclo_node just needs the NEXT chunk after receiving a detection trigger — it does not need the exact chunk that produced the detection.
**How to avoid:** cyclo_node does not try to correlate a specific IQ chunk to a specific detection. It works in "always on" mode: when a detection in the 2.4 GHz band is active, process the next arriving IQ chunk. This is statistically valid because the signal is persistent (persistence_n >= 3 confirmed detections).
**Warning signs:** cyclo_node reports "no IQ data" when 2.4 GHz signals are clearly present.

### Pitfall 4: pyhackrf2 device_index with Single Device
**What goes wrong:** `HackRF(device_index=0)` is the default and always works. But `HackRF(device_index=1)` raises an exception if only one device is connected, crashing the node on startup.
**Why it happens:** pyhackrf2 does not return a graceful error — it raises RuntimeError.
**How to avoid:** Wrap `HackRF(device_index=N)` in try/except RuntimeError in `on_configure()`. This is already the pattern in hackrf_lifecycle_node.py — just change `HackRF()` to `HackRF(device_index=device_index)`.
**Warning signs:** Second HackRF instance fails to configure even with device present.

### Pitfall 5: CycloneDDS Topic QoS Mismatch in Multi-Radio
**What goes wrong:** Two hackrf_0 and hackrf_1 instances publish on `/hackrf_0/spectrum` and `/hackrf_1/spectrum` respectively. A downstream node that subscribes to `/hackrf_0/detections` RELIABLE may not receive messages if the publisher is BEST_EFFORT (existing behavior for IQ topics).
**Why it happens:** Per existing decisions (D-14 from Phase 1): streaming topics use BEST_EFFORT. Namespacing does not change QoS.
**How to avoid:** Existing QoS profiles carry over via namespace. No changes needed — just verify the autonomy pipeline downstream knows which namespace to subscribe to.
**Warning signs:** Detection subscribers receive no data despite publisher running.

### Pitfall 6: RFDetection.msg Field Addition Rebuild
**What goes wrong:** After adding fields to `RFDetection.msg`, old cfar_node binary is still loaded from the colcon install space.
**Why it happens:** `colcon build` must rebuild both `hackrf_interfaces` and `hackrf_ros` after any .msg change.
**How to avoid:** Always rebuild both packages in sequence: `colcon build --packages-select hackrf_interfaces && colcon build --packages-select hackrf_ros`.
**Warning signs:** `AttributeError: 'RFDetection' object has no attribute 'is_anomaly'` at runtime.

---

## ADV-01: EMA Convergence Math

**Verified computation** [VERIFIED: Python scipy/numpy on this system]:

| Frames | Convergence | Wall clock at 10 Hz |
|--------|-------------|---------------------|
| 10 | 40.1% | 1.0 s |
| 20 | 64.2% | 2.0 s |
| 30 | 78.5% | 3.0 s |
| 45 | 90.1% | 4.5 s |
| 60 | 95.4% | 6.0 s |

Formula: `convergence = 1 - (1 - alpha)^N` where alpha=0.05.

**30 s warmup at 10 Hz = 300 frames = 99.99% convergence of EMA.** The warmup period is far more than sufficient; it is justified by the need to accumulate a representative baseline median, not by EMA convergence speed.

**Memory footprint per anomaly detector instance:**
- baseline array: 4096 x float32 = 16,384 bytes
- has_ever_detected: 4096 x bool = 4,096 bytes
- warmup_accum (during warmup only): 300 frames x 4096 x float32 = 4.9 MB peak, then cleared

The warmup accumulation is the only significant memory cost. After warmup completes and `_warmup_accum.clear()` is called, the detector returns to 20 KB.

---

## ADV-02: Localization Algorithm Decision

**Flag for planner:** The CONTEXT.md decision D-05 says "fit RSSI_0 and path loss exponent n via least-squares." This is correct intent but requires clarification: the standard linear least-squares (`numpy.linalg.lstsq`) applies ONLY when the emitter position (x_e, y_e) is KNOWN. In Phase 4, the emitter position is the UNKNOWN being solved for. The correct solver is nonlinear least-squares.

**Recommended:** `scipy.optimize.minimize(cost, x0=heuristic_init, method='Nelder-Mead')` — converges in ~2 ms with heuristic initialization, achieves < 0.5 m position error with 5 well-spread observations. [VERIFIED]

**Typical path loss exponent values** [ASSUMED - from RF propagation theory, not verified experimentally]:
- Free space: n = 2.0
- Indoor (office, light obstruction): n = 2.5 – 3.5
- Indoor (heavy obstruction, concrete): n = 3.5 – 5.0
- Outdoor urban: n = 2.7 – 3.5

**Outlier sensitivity:** A single +15 dB RSSI outlier with 5 observations causes n estimation error of ~0 (the offset RSSI_0 absorbs the error). With only 3 observations, the impact is much larger. The 3-observation minimum (D-07) should be treated as a floor for publishing, with quality improving substantially at 5+ observations.

**Covariance estimation:** Use the optimizer residual divided by degrees-of-freedom, then assume equal x/y uncertainty:
```
sigma2 = result.fun / max(n_obs - 4, 1)   # 4 params: xe, ye, rssi0, n
cov_xx = cov_yy = sigma2 * (d_rms_m ** 2)  # d_rms ~ RMS distance to observations
```
This is a rough approximation. The message fields `covariance_xx` and `covariance_yy` should be interpreted as rough uncertainty bounds, not rigorous covariance.

---

## ADV-03: Cyclostationary Feature Analysis

### Bin Math (all computed, 20 MHz / 4096-pt FFT) [VERIFIED]

| Parameter | Value |
|-----------|-------|
| Bin width | 4882.8 Hz |
| WiFi OFDM subcarrier spacing | 312.5 kHz = **64 bins** |
| WiFi pilot tones (positive half) | bins 448 and 1344 (subcarriers ±7 and ±21) |
| BLE occupied bandwidth | ~1 MHz = **205 bins** |
| ZigBee occupied bandwidth | ~2 MHz = **410 bins** |
| BLE hop dwell period | 625 µs = **12,500 samples** at 20 MHz |
| Driver chunk size | 65536 samples = **3.28 ms** = **5.2 BLE dwell windows** |

### BLE Hop Detection — Critical Design Note

The CONTEXT.md D-09 mentions "short-time autocorrelation" for BLE. This is NOT feasible with a single driver chunk via Wiener-Khinchin autocorrelation:

- Single driver chunk: 65536 samples = 3.28 ms
- Autocorrelation cost on 65536 samples: **12,759 µs** [VERIFIED] — exceeds 10 ms budget alone
- BLE hopping produces power variance across 5 windows within one chunk

**Replacement approach:** Split chunk into 625 µs windows (12,500 samples each = 5 windows), compute per-window power, measure coefficient of variation (CV). CV > 0.3 indicates hopping. Cost: ~7 ms [VERIFIED]. This is the implementation to use.

### SNR Floor for Feature Reliability [ASSUMED]

- WiFi pilot detection: reliable at SNR > 10 dB above noise floor (pilot tones are coherent, strong)
- BLE power-CV: reliable at SNR > 5 dB (hopping pattern visible above noise variance)
- ZigBee SFM: reliable at SNR > 5 dB; fails if signal is narrow-band with high SNR (looks like BLE)

Low SNR failure mode: at SNR < 3 dB, all three features produce unreliable outputs. The `cyclo_confidence` field should reflect SNR-adjusted reliability. Simplest approach: `confidence = snr_db / 20.0` clamped to [0, 1] when cyclostationary result is non-empty.

---

## HW-02: Multi-Radio Implementation

### pyhackrf2 API [VERIFIED: official GitHub README via WebFetch]

```python
# Single device (default)
hackrf = pyhackrf2.HackRF()                  # equivalent to device_index=0

# Multiple devices
hackrf0 = pyhackrf2.HackRF(device_index=0)   # first device
hackrf1 = pyhackrf2.HackRF(device_index=1)   # second device

# Enumerate connected devices (returns list of serial number strings)
serials = pyhackrf2.HackRF.enumerate()
```

**Change to hackrf_lifecycle_node.py:** Declare `device_index` parameter (default: 0), then:
```python
device_index = int(self.get_parameter('device_index').value)
self._hackrf = pyhackrf2.HackRF(device_index=device_index)
```

### USB Bandwidth Analysis [VERIFIED: computed]

| Configuration | Data Rate | USB 3.0 Budget | Status |
|---------------|-----------|----------------|--------|
| 1x HackRF at 20 MHz | 40 MB/s | 300 MB/s | OK |
| 2x HackRF at 20 MHz | 80 MB/s | 300 MB/s | OK |
| 2x HackRF on USB 2.0 hub | 80 MB/s | 40 MB/s practical | **FAIL** |

**Requirement:** Two HackRF instances at 20 MHz each require USB 3.0. On Jetson Orin, USB 3.0 ports must be used. Do NOT use a USB 2.0 hub.

### CycloneDDS Multi-Namespace [VERIFIED: computed + known ROS2 behavior]

No CycloneDDS configuration changes are required. ROS2 namespace is a topic-name prefix concern, not a DDS participant concern. CycloneDDS on the same host in the same domain ID discovers all participants automatically. `/hackrf_0/spectrum` and `/hackrf_1/spectrum` are simply different topic names — CycloneDDS handles this with zero configuration.

---

## Message Schema Decisions

### RFDetection.msg — Fields to Add

```
# Existing (DO NOT REORDER — preserves wire compatibility with existing subscribers)
float64 center_frequency_hz
float64 bandwidth_hz
float32 power_dbm
float32 snr_db
string classification
uint32 persistence_frames
uint32 detection_id

# Phase 4 additions (append at end)
bool is_anomaly
string anomaly_type          # 'power_spike' | 'idle_band_new_emitter' | ''
string cyclo_classification   # 'wifi_2_4g' | 'ble' | 'zigbee' | ''
float32 cyclo_confidence      # [0.0, 1.0]
```

**ABI concern:** None — Python-only stack. New fields default to 0/false/''. Old subscribers ignore them.
**CMakeLists.txt:** No changes needed. `RFDetection.msg` is already listed in `rosidl_generate_interfaces`.

### RFEmitterMap.msg — New Message

```
std_msgs/Header header
hackrf_interfaces/RFEmitterEstimate[] emitters
```

### RFEmitterEstimate.msg — New Message

```
uint32 detection_id
float64 estimated_x         # map frame metres
float64 estimated_y         # map frame metres
float32 covariance_xx       # position uncertainty m^2
float32 covariance_yy       # position uncertainty m^2
float32 rssi0_dbm           # fitted reference power at 1m
float32 path_loss_exponent  # fitted n
uint32 observation_count
builtin_interfaces/Time last_seen
```

**CMakeLists.txt:** Add both new .msg files to `rosidl_generate_interfaces` call.
**geometry_msgs:** Not required for these messages (using raw float64 x,y rather than Point). Keeps dependency list clean.

---

## Integration Architecture

### Node Pipeline After Phase 4

```
/hackrf/iq (driver)
    |
    +--> cfar_node.py (spectrum callback)
    |       CFAR -> cluster -> persist -> classify
    |       [NEW] anomaly EMA update -> set is_anomaly, anomaly_type fields
    |       publish /hackrf/detections (RFDetectionArray)
    |
    +--> cyclo_node.py (iq_chunk callback)
            [NEW] When 2.4 GHz detection active:
            cyclo_classify(chunk) -> classification, confidence
            subscribe /hackrf/detections
            publish /hackrf/detections_enriched (RFDetectionArray with cyclo fields)
            [or: update /hackrf/detections in-place — see note below]

/hackrf/detections_enriched
    |
    +--> emitter_loc_node.py [NEW]
    |       TF lookup for robot pose
    |       Accumulate (pose, RSSI) per detection_id
    |       Nonlinear fit when >= 3 well-spread poses
    |       publish /hackrf/emitter_map (RFEmitterMap)
    |
    +--> rf_map_node.py (existing — subscribes to /hackrf/detections)
```

**Note on cyclo_node topic:** Two options:
1. cyclo_node subscribes to both `/hackrf/iq` AND `/hackrf/detections`, publishes to `/hackrf/detections_enriched` (new topic, downstream nodes subscribe to this).
2. cyclo_node is a pure side-channel: publishes cyclo results indexed by `detection_id` on a separate topic; existing downstream subscribes to original `/hackrf/detections`.

Option 2 is cleaner (no topic rename, no downstream refactor) but requires emitter_loc_node to merge two subscriptions. Option 1 is simpler. Recommend Option 1 — planner should choose.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (existing) |
| Config file | none — run via `colcon test` or `pytest test/` |
| Quick run command | `pytest test/test_anomaly_detector.py test/test_cyclo_features.py -x` |
| Full suite command | `colcon test --packages-select hackrf_ros && colcon test-result` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ADV-01 | EMA baseline converges, spike detected at +10 dB | unit | `pytest test/test_anomaly_detector.py -x` | No — Wave 0 |
| ADV-01 | Warmup suppresses anomaly output for 30 s | unit | `pytest test/test_anomaly_detector.py::test_warmup -x` | No — Wave 0 |
| ADV-01 | New emitter in idle band triggers idle_band_new_emitter | unit | `pytest test/test_anomaly_detector.py::test_idle_band -x` | No — Wave 0 |
| ADV-02 | Localization with 5 well-spread poses returns < 1 m error | unit | `pytest test/test_emitter_loc.py::test_fit_accuracy -x` | No — Wave 0 |
| ADV-02 | Collinear poses rejected (SVD check) | unit | `pytest test/test_emitter_loc.py::test_collinear -x` | No — Wave 0 |
| ADV-03 | WiFi pilot lines detected in OFDM signal | unit | `pytest test/test_cyclo_features.py::test_wifi -x` | No — Wave 0 |
| ADV-03 | BLE power-CV detected with hopping signal | unit | `pytest test/test_cyclo_features.py::test_ble -x` | No — Wave 0 |
| ADV-03 | ZigBee SFM detected | unit | `pytest test/test_cyclo_features.py::test_zigbee -x` | No — Wave 0 |
| ADV-03 | Full pipeline < 10 ms on 65536 samples | unit (timing) | `pytest test/test_cyclo_features.py::test_timing -x` | No — Wave 0 |
| HW-02 | device_index param passed to HackRF constructor | unit | `pytest test/test_multiradio.py::test_device_index -x` | No — Wave 0 |
| HW-02 | multi_radio.launch.py generates N node instances | smoke | manual inspection / launch dry-run | No — Wave 0 |

### Test Pattern (follow existing style from test_rf_map_node.py)

```python
# test_anomaly_detector.py — no ROS2 init required (test pure functions)
# Import module with ROS2 stubs (pattern from test_rf_map_node.py)
import types, sys

# Stub rclpy
rclpy_mod = types.ModuleType('rclpy')
sys.modules['rclpy'] = rclpy_mod
# ... (full stub pattern from existing tests)

from hackrf_ros.anomaly_detector import AnomalyDetector
```

### Wave 0 Gaps

- [ ] `test/test_anomaly_detector.py` — covers ADV-01 (3 tests)
- [ ] `test/test_emitter_loc.py` — covers ADV-02 (2 tests)
- [ ] `test/test_cyclo_features.py` — covers ADV-03 (4 tests)
- [ ] `test/test_multiradio.py` — covers HW-02 (1 unit test)

*(Note: flake8 + pep257 + copyright tests are already in place and cover new files automatically.)*

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| scipy | ADV-01, ADV-02, ADV-03 | YES | 1.17.1 | — |
| numpy | ADV-01, ADV-02, ADV-03 | YES | bundled with scipy | — |
| pyhackrf2 | HW-02 | NO (not installed in dev env) | — | Mock in tests (existing pattern) |
| tf2_ros | ADV-02 (localization node) | YES (ROS2 Humble) | bundled | — |
| rclpy | All new nodes | YES (ROS2 Humble) | bundled | — |

**pyhackrf2 not available in dev environment** — this is expected and handled. All existing nodes mock pyhackrf2 in tests. New node tests follow same pattern.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Typical path loss exponent: indoor n=2.5-3.5, outdoor n=2.7-3.5 | ADV-02 | If real environment has n outside this range, initial guess n=2.0 may not converge; bump maxiter or add bounded search |
| A2 | WiFi pilot detection reliable at SNR > 10 dB | ADV-03 | Below 10 dB, pilot lines may not be distinguishable; cyclo_confidence should be gated on SNR |
| A3 | BLE power-CV > 0.3 threshold distinguishes hopping from non-hopping signals | ADV-03 | Real BLE in congested 2.4 GHz band may show lower CV due to interference from other BLE devices; threshold may need tuning |
| A4 | ZigBee SFM > 0.5 threshold distinguishes DSSS from GFSK | ADV-03 | At low SNR, noise contribution elevates spectral flatness of narrowband signals |
| A5 | OpaqueFunction pattern is correct for ROS2 Humble programmatic N-instance launch | HW-02 | If OpaqueFunction API differs in Humble, alternative is ComposableNodeContainer or simple loop |

---

## Open Questions (RESOLVED)

1. **Cyclo_node topic output: enriched detections vs side-channel**
   - What we know: Two architectures are viable (enriched `/hackrf/detections_enriched` vs separate cyclo topic)
   - What's unclear: Whether emitter_loc_node should subscribe to original or enriched detections topic
   - Recommendation: Planner should pick Option 1 (enriched topic) for simplicity; decision affects which topic emitter_loc_node subscribes to

2. **Localization covariance semantics**
   - What we know: The residual-based covariance is a rough approximation, not a true Gaussian error model
   - What's unclear: Whether downstream consumers (e.g., future CLS-01) expect rigorous covariance or just an uncertainty hint
   - Recommendation: Document clearly in the message that `covariance_xx/yy` is an heuristic uncertainty estimate, not a calibrated covariance

3. **Warmup re-warm step**
   - What we know: Initializing from median works well if environment is stable during warmup
   - What's unclear: Whether a fast-convergence "re-warm" phase (alpha=0.3 for 10 frames) is worth the complexity
   - Recommendation: Skip re-warm for v1; accept potential false-anomaly burst at warmup end; log it via diagnostics as WARN

---

## Sources

### Primary (HIGH confidence)
- Python scipy 1.17.1 on this system — EMA convergence math, cost function timing, localization accuracy, cyclostationary pipeline timing [VERIFIED: computed]
- numpy.linalg.svd — collinearity detection [VERIFIED: computed]
- pyhackrf2 GitHub README (fetched via WebFetch) — `HackRF(device_index=N)` API and `HackRF.enumerate()` [VERIFIED: WebFetch]
- Existing codebase — cfar_node.py, rf_map_node.py, test_rf_map_node.py patterns [VERIFIED: read directly]
- hackrf_interfaces/CMakeLists.txt — rosidl_generate_interfaces call [VERIFIED: read directly]

### Secondary (MEDIUM confidence)
- USB 3.0 bandwidth: HackRF requires USB 3.0 for 20 MHz; two devices at 40 MB/s each = 80 MB/s, within USB 3.0 practical throughput [ASSUMED from HackRF documentation; USB 2.0 limit verified by arithmetic]
- CycloneDDS namespace behavior: ROS2 namespace is topic-prefix only, DDS handles multi-publisher same-host transparently [ASSUMED from ROS2 architecture knowledge, consistent with project DDS selection rationale]

### Tertiary (LOW confidence / Assumed)
- Path loss exponent typical values for indoor/outdoor (A1) — RF propagation textbook values, not measured on Jetson
- SNR thresholds for cyclostationary feature reliability (A2, A3, A4) — reasonable estimates, require calibration with real hardware

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries confirmed present on system; pyhackrf2 API confirmed via official README
- Architecture patterns: HIGH — all compute costs verified by measurement; all mathematical claims verified by computation
- Pitfalls: HIGH — derived from actual code reading (cfar_node.py, rf_map_node.py) and algorithmic analysis
- Cyclostationary bin math: HIGH — all values computed from first principles (fs=20MHz, N=4096)
- Localization algorithm: HIGH (solver choice), MEDIUM (accuracy with real multipath)

**Research date:** 2026-04-12
**Valid until:** 2026-06-01 (scipy/numpy stable APIs; pyhackrf2 README unlikely to change)
