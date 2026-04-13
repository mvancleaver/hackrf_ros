---
phase: 4
plan: 2
subsystem: cfar_node / anomaly_detection
tags: [anomaly-detection, ema, cfar, diagnostics, tdd]
dependency_graph:
  requires: [04-01]
  provides: [anomaly_detection_adv01]
  affects: [hackrf_ros/cfar_node.py, hackrf_interfaces/msg/RFDetection.msg]
tech_stack:
  added: [diagnostic_updater]
  patterns: [per-bin EMA baseline, median-seeded warmup, dual-trigger anomaly]
key_files:
  created:
    - test/test_anomaly_detector.py
  modified:
    - hackrf_ros/cfar_node.py
decisions:
  - "AnomalyDetector lazy-initialized on first PSD frame to avoid n_bins dependency at node init"
  - "ROS2 stubs in test file (same pattern as test_rf_map_node.py) allow pytest without rclpy"
  - "Seeding frame returns zeros (no anomaly flags) to avoid transient false alarms at baseline initialization"
metrics:
  duration: "~3 minutes"
  completed: "2026-04-13"
  tasks_completed: 2
  files_modified: 2
---

# Phase 4 Plan 2: Anomaly Detector (ADV-01) Summary

**One-liner:** Per-bin EMA anomaly detector with median-seeded 30 s warmup and dual trigger (power spike + idle-band new emitter) embedded in cfar_node.py with /diagnostics reporting.

## What Was Built

Added `AnomalyDetector` class to `hackrf_ros/cfar_node.py` as a post-processing step in
`_spectrum_callback`. Implements ADV-01 — per-bin rolling EMA baseline, 30 s warmup with
median seeding, dual trigger, and diagnostics reporting.

### AnomalyDetector Class

- **Location:** `hackrf_ros/cfar_node.py`, line 67
- **Constructor args:** `n_bins`, `alpha=0.05`, `warmup_s=30.0`, `spike_threshold_db=10.0`
- **Public API:** `.update(psd_db)` → `(spike_mask, new_emitter_mask)`, `.anomaly_count`, `.is_warmed_up`
- **Warmup:** Accumulates PSD frames in `_warmup_accum` list during `warmup_s`. On expiry, seeds
  `_baseline` from `np.median(stack, axis=0)`. Returns `(zeros, zeros)` on the seeding frame.
- **EMA update:** `baseline = (1 - alpha) * baseline + alpha * psd_db` — COMPARE THEN UPDATE order
  (comparison uses old baseline before update, per research spec)
- **Spike trigger:** `psd_db > (baseline + spike_threshold_db)` — vectorized numpy
- **New-emitter trigger:** `spike_mask & ~_has_ever_detected` — first-time spike per bin

### Integration in _spectrum_callback

- **Lines:** 472–498 (anomaly detection block)
- Lazy-initializes detector on first frame (n_bins from actual PSD)
- Maps each `confirmed` detection to its PSD bin via `bin_idx = int((det.center_frequency_hz - freq_lo) / msg.bin_width_hz)`
- Annotates `det.is_anomaly` and `det.anomaly_type` ('power_spike', 'idle_band_new_emitter', or '')
- Block placement: after band classification loop, before `arr_msg` construction

### Parameters Added to CFARNode

| Parameter | Default | Description |
|-----------|---------|-------------|
| `anomaly_alpha` | 0.05 | EMA smoothing factor (~20 frames to 90% convergence) |
| `anomaly_warmup_s` | 30.0 | Suppression window in seconds |
| `anomaly_spike_threshold_db` | 10.0 | dB above baseline to trigger power_spike |

### Diagnostics

- **Method:** `CFARNode._anomaly_diagnostics` at line 521
- Reports WARN during warmup with elapsed/total seconds
- Reports OK/WARN after warmup with `anomaly_count`
- Registered via `diagnostic_updater.Updater` in `__init__` (hardware_id: 'cfar_anomaly')

## Test Results

**14/14 tests passed — GREEN phase confirmed**

| Test | Result |
|------|--------|
| `test_cfar_node_has_anomaly_diagnostics` | PASSED |
| `TestAnomalyCountIncrements::test_anomaly_count_increments_on_spike` | PASSED |
| `TestWarmupSuppression::test_no_anomaly_during_warmup` | PASSED |
| `TestWarmupSuppression::test_warmup_accumulates_frames` | PASSED |
| `TestWarmupSuppression::test_baseline_none_during_warmup` | PASSED |
| `TestBaselineSeeding::test_baseline_seeded_from_median` | PASSED |
| `TestBaselineSeeding::test_warmup_accum_cleared_after_seeding` | PASSED |
| `TestPowerSpikeDetection::test_power_spike_detected` | PASSED |
| `TestPowerSpikeDetection::test_below_threshold_not_flagged` | PASSED |
| `TestPowerSpikeDetection::test_spike_mask_is_numpy_bool_array` | PASSED |
| `TestIdleBandNewEmitter::test_new_emitter_in_idle_band` | PASSED |
| `TestIdleBandNewEmitter::test_repeat_spike_not_new_emitter` | PASSED |
| `TestIdleBandNewEmitter::test_quiet_bin_then_spike_is_new_emitter` | PASSED |
| `TestEMAUpdate::test_ema_converges_toward_input` | PASSED |

## Deviations from Plan

### Auto-added: ROS2 stub infrastructure in test file

**[Rule 2 - Missing Critical Functionality] Added ROS2 stubs to test file**
- **Found during:** Task 2 (GREEN) — test execution failed with `ModuleNotFoundError: No module named 'rclpy'`
- **Issue:** `cfar_node.py` imports `rclpy`, `diagnostic_updater`, and `hackrf_interfaces` at module level. Tests can't run without these in a non-ROS2 environment.
- **Fix:** Added minimal Python `types.ModuleType` stubs for `rclpy`, `rclpy.node`, `rclpy.qos`, `rclpy.callback_groups`, `diagnostic_updater`, and `hackrf_interfaces.msg` at the top of `test/test_anomaly_detector.py` — same pattern used in `test/test_rf_map_node.py`.
- **Files modified:** `test/test_anomaly_detector.py`
- **Commit:** d63db58

### Note: diagnostic_updater not available outside ROS2 env

The `diagnostic_updater` package is part of the ROS2 installation and not available as a standalone pip package. The stub approach makes tests runnable without a full ROS2 install. The import in `cfar_node.py` will resolve correctly when the package is built with colcon in a sourced ROS2 workspace.

## Commits

| Hash | Message | Files |
|------|---------|-------|
| f34973c | `test(04-02): add failing tests for AnomalyDetector — RED phase` | `test/test_anomaly_detector.py` |
| d63db58 | `feat(04-02): implement AnomalyDetector and wire into cfar_node — GREEN phase` | `hackrf_ros/cfar_node.py`, `test/test_anomaly_detector.py` |

## Self-Check: PASSED

- `test/test_anomaly_detector.py` exists: FOUND
- `hackrf_ros/cfar_node.py` contains `class AnomalyDetector`: FOUND (line 67)
- `hackrf_ros/cfar_node.py` contains `_anomaly_diagnostics`: FOUND (line 521)
- Commit f34973c exists: FOUND
- Commit d63db58 exists: FOUND
- 14/14 tests pass: CONFIRMED
