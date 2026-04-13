---
phase: 4
plan: 4
subsystem: cyclostationary-feature-extraction
tags: [adv-03, cyclo, wifi, ble, zigbee, ism-2400, tdd, signal-processing]
requirements: [ADV-03]

dependency_graph:
  requires:
    - "04-01: RFDetection.msg extended with cyclo_classification, cyclo_confidence"
    - "01-PLAN: cfar_node.py subscriber/publisher patterns"
  provides:
    - "hackrf_ros/cyclo_node.py: CycloNode + cyclo_classify pure function"
    - "/hackrf/cyclo_detections: enriched RFDetectionArray with cyclo fields"
  affects:
    - "Any consumer of /hackrf/detections that wants protocol disambiguation in 2.4 GHz ISM"

tech_stack:
  added: []
  patterns:
    - "cyclo_classify as pure function (no ROS2 deps) — testable without runtime"
    - "ROS2 stubs in test file (setdefault pattern) matching existing test conventions"
    - "power_cv > 0.3 threshold for BLE detection (not sfm-gated)"
    - "wifi_line_ratio = pilot_bin_power / median_noise — threshold 10.0"
    - "SFM = exp(mean(log(X))) / mean(X) for ZigBee flatness"

key_files:
  created:
    - hackrf_ros/cyclo_node.py
    - test/test_cyclo_classifier.py
  modified: []

decisions:
  - "BLE classifier uses power_cv only (not sfm-gated): BLE hop windows have flat spectrum (SFM~0.56), sfm < 0.5 guard incorrectly blocks BLE detection"
  - "ZigBee discriminated from BLE by power_cv < 0.1 (ZigBee DSSS: constant power cv~0.004)"
  - "setup.py cyclo_node entry point was already registered by 04-01 — no change needed"

metrics:
  duration_minutes: 4
  completed_date: "2026-04-13"
  tasks_completed: 2
  tasks_total: 2
  files_created: 2
  files_modified: 0
---

# Phase 4 Plan 4: Cyclostationary Feature Extraction Summary

**One-liner:** Three-feature cyclostationary pipeline (WiFi pilot bins 448/1344, BLE power-CV, ZigBee SFM) classifying 2.4 GHz ISM signals with ROS2 stubs for test isolation.

## What Was Built

`hackrf_ros/cyclo_node.py` implements ADV-03: a ROS2 node that subscribes to `/hackrf/iq`
and `/hackrf/detections`, applies a three-feature cyclostationary pipeline to 2.4 GHz ISM
detections, and publishes enriched detections unconditionally on `/hackrf/cyclo_detections`.

The `cyclo_classify(iq_chunk)` pure function runs without ROS2 context:

- **WiFi:** spectral line ratio at 802.11n/ac pilot bins 448 and 1344 (subcarriers ±7 and ±21 at 312.5 kHz, 20 MHz / 4096-pt FFT). Threshold: ratio > 10.0.
- **BLE:** power coefficient of variation across 625 µs windows (12,500 samples at 20 MHz). Threshold: CV > 0.3.
- **ZigBee:** spectral flatness measure (geometric_mean / arithmetic_mean of PSD) + low power CV. Thresholds: SFM > 0.3 and CV < 0.1.

## Test Results

All 13 tests pass (GREEN):
- `TestReturnContract` (4 tests): tuple, confidence range, string label, valid values
- `TestWifiClassification` (3 tests): correct label, confidence >= 0.5, line ratio > 5.0
- `TestBleClassification` (2 tests): correct label, CV > 0.3
- `TestZigbeeClassification` (2 tests): correct label, SFM > 0.3
- `TestUnknownClassification` (1 test): single tone at bin 200 returns '' or conf < 0.5
- `TestComputeBudget` (1 test): < 10 ms per 65536-sample chunk

## Commits

| Task | Hash    | Message |
|------|---------|---------|
| 1 RED | 162aafa | test(04-04): add failing tests for cyclo_classify |
| 2 GREEN | c044d27 | feat(04-04): implement cyclo_node.py — cyclostationary feature extraction |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] BLE classifier: removed incorrect `sfm < 0.5` guard**
- **Found during:** Task 2 GREEN verification
- **Issue:** BLE hop windows use noise-like IQ — uniform random noise has a flat spectrum (SFM ~ 0.56). The `sfm < 0.5` guard prevented correct BLE classification even when power_cv = 0.81.
- **Fix:** Removed `sfm < 0.5` from the BLE condition. `power_cv > 0.3` alone is sufficient and correct — ZigBee/DSSS has constant power (cv ~ 0.004), BLE hopping has high power variance (cv > 0.8).
- **Files modified:** `hackrf_ros/cyclo_node.py`
- **Commit:** c044d27

**2. [Rule 1 - Bug] Test file: added ROS2 stubs for import isolation**
- **Found during:** Task 2 test execution
- **Issue:** `cyclo_node.py` imports `rclpy` at module level; no ROS2 runtime available in test environment (standard for this project). Import failed with `ModuleNotFoundError: No module named 'rclpy'`.
- **Fix:** Added minimal ROS2 stubs (same `types.ModuleType` + `sys.modules.setdefault` pattern as `test_rf_map_node.py`) before the `from hackrf_ros.cyclo_node import cyclo_classify` line.
- **Files modified:** `test/test_cyclo_classifier.py`
- **Commit:** c044d27

**3. [Rule 3 - Blocking] setup.py already had cyclo_node entry point**
- **Found during:** Task 2 implementation
- **Issue:** Plan says "add entry point" but 04-01 already registered it.
- **Fix:** Skipped — no change required.

## Known Stubs

None. `cyclo_classify` returns real classifications based on synthetic IQ features. The node publishes unconditionally — no stubbed/placeholder data flows to consumers.

## Threat Flags

None. `cyclo_node.py` subscribes to existing topics (`/hackrf/iq`, `/hackrf/detections`) and publishes to `/hackrf/cyclo_detections`. No new network endpoints, auth paths, file access, or trust boundary changes introduced.

## Self-Check: PASSED

- hackrf_ros/cyclo_node.py: FOUND
- test/test_cyclo_classifier.py: FOUND
- 04-04-SUMMARY.md: FOUND
- Commit 162aafa (RED): FOUND
- Commit c044d27 (GREEN): FOUND
- All 13 tests: PASSED
