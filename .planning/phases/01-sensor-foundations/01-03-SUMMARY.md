---
phase: 01-sensor-foundations
plan: 03
subsystem: hackrf_ros
tags: [cfar, detection, persistence, classification, signal-processing]
dependency_graph:
  requires: [SpectrumStamped, RFDetection, RFDetectionArray, RFEnvironment]
  provides: [CFARNode, /hackrf/detections, /hackrf/rf_environment]
  affects: [hackrf_ros]
tech_stack:
  added: [scipy.ndimage.convolve1d, scipy.ndimage.label]
  patterns: [CA-CFAR sliding window, connected-component labeling, frequency-proximity tracking]
key_files:
  created:
    - hackrf_ros/cfar_node.py
  modified:
    - setup.py
decisions:
  - CA-CFAR operates in linear domain (10^(psd_db/10)) with kernel convolution, not Python loops
  - Band classification table ordered by frequency with bandwidth heuristics for 2.4 GHz ISM disambiguation
  - Two-pass adaptive guard deferred to in-place upgrade (single-pass guard=8 sufficient for Phase 1)
  - Persistence tracker uses power-weighted center averaging for smooth track updates
metrics:
  duration: 287s
  completed: "2026-04-13T03:06:04Z"
  tasks_completed: 2
  tasks_total: 2
---

# Phase 1 Plan 03: CFAR Detection Node Summary

CA-CFAR energy detection node with scipy.ndimage sliding window, persistence tracking with stable IDs, and 18-entry band classification lookup table covering WiFi/BLE/LTE/FM/GPS/ISM bands.

## What Was Done

### Task 1: Implement CA-CFAR detection and bin clustering

- Created `hackrf_ros/cfar_node.py` (425 lines) with CFARNode class
- `_ca_cfar()` static method: converts PSD to linear domain, builds symmetric kernel with train/guard/CUT layout, uses `scipy.ndimage.convolve1d(mode='nearest')` for sliding noise estimate, applies threshold multiplier `T_m = M * (Pfa^(-1/M) - 1)` per Holik eq 11.39
- `_group_detections()` static method: uses `scipy.ndimage.label` for connected-component bin grouping, computes power-weighted centroid frequency, 3 dB bandwidth, peak power, and SNR for each cluster
- ROS2 parameters declared: pfa (1e-4), guard_cells (8), train_cells (32), persistence_n (3), persistence_decay (5), antenna_frame
- Publishers: `/hackrf/detections` (BEST_EFFORT, depth=5), `/hackrf/rf_environment` (RELIABLE, depth=1)
- Subscriber: `/hackrf/spectrum` (BEST_EFFORT)
- 1 Hz timer for RFEnvironment publishing
- Threat mitigations: T-03-01 (zero-length PSD guard), T-03-02 (array size validation), T-03-05 (decay param clamped to min 1)
- **Commit:** c71f17a

### Task 2: Add entry point and verify build

- Added `cfar_node = hackrf_ros.cfar_node:main` to setup.py console_scripts
- Verified colcon build exits 0 for both hackrf_interfaces and hackrf_ros in Docker
- Ran CFAR self-test: synthetic -60 dBm tone on -100 dBm noise floor detected correctly; noise bins clean
- Band classification verified: wifi_2_4g (2.45 GHz, 20 MHz BW), ble (2.44 GHz, 1.5 MHz BW), fm_radio (100 MHz, 200 kHz BW), unknown (50 MHz)
- **Commit:** 5db1718

## Verification Results

All success criteria confirmed:
- hackrf_ros/cfar_node.py exists, 425 lines (>= 200 requirement)
- python3 -m py_compile exits 0 (no syntax errors)
- colcon build --packages-select hackrf_ros exits 0
- CFAR self-test: tone at -60 dBm on -100 dBm floor detected; noise bin not detected
- `convolve1d` used for sliding window (no Python loop)
- `_BAND_TABLE` present with 18 entries
- `persistence_n` parameter declared
- `cfar_node` registered in setup.py console_scripts

## Deviations from Plan

### Auto-fixed Issues

None -- plan executed exactly as written.

### Design Simplifications

**1. Two-pass adaptive guard deferred (D-02)**
- Plan noted "For Phase 1, Pass 1 with empirically chosen guard=8 is sufficient"
- Single-pass CA-CFAR implemented; the `_ca_cfar` interface supports per-call guard override so two-pass can be added in-place without breaking anything

**2. Combined Task 1 and Task 2 code**
- The plan's TDD structure split CFAR core (Task 1) from persistence/classification (Task 2)
- Since cfar_node.py is a single new file with tightly coupled methods, all code was written in Task 1 commit to avoid partial-file state; Task 2 focused on setup.py wiring and full verification

## Known Stubs

None. All methods are fully implemented with real logic.

## Decisions Made

1. CA-CFAR operates in linear domain with kernel convolution -- matches target-detection-modeling.md section 7.1
2. Band classification table uses first-match ordering with frequency then bandwidth checks -- 18 entries covering all bands from D-10
3. Persistence tracker uses power-weighted center averaging (not simple moving average) for smoother track updates across frames
4. Two-pass adaptive guard reserved for in-place upgrade -- single guard=8 handles typical signals

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | c71f17a | feat(01-03): implement CA-CFAR detection node with bin clustering and persistence |
| 2 | 5db1718 | feat(01-03): register cfar_node entry point in setup.py |

## Self-Check: PASSED

All 8 checks verified: both files exist, both commits found, cfar_node in setup.py, convolve1d present, _BAND_TABLE present, persistence_n present, 425 lines (>= 200).
