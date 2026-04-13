---
phase: 4
plan: 3
subsystem: emitter-localization
tags: [tdd, localization, path-loss, scipy, nelder-mead, tf2]
dependency_graph:
  requires: [04-01]
  provides: [ADV-02, emitter_loc_node, /hackrf/emitter_map]
  affects: [autonomy-stack]
tech_stack:
  added: [scipy.optimize.minimize (Nelder-Mead)]
  patterns: [multi-start-optimization, rssi-weighted-centroid-init, soft-penalty-bounds, ros2-stubs-in-tests]
key_files:
  created:
    - hackrf_ros/emitter_loc_node.py
    - test/test_emitter_loc.py
  modified: []
decisions:
  - "Multi-start Nelder-Mead with RSSI-weighted centroid initial guess (not strongest-RSSI pose) — centroid better handles symmetric geometries where equal-distance poses make the system underdetermined"
  - "Soft penalty (1e6*(n-bound)^2) enforces n in [1.5, 6.0] within Nelder-Mead cost function — bounds not natively supported by Nelder-Mead"
  - "ROS2 stubs added to test file following test_rf_map_node.py pattern — enables pytest without rclpy installed"
  - "continue (not return) in dedup block — critical for processing all detections in array"
metrics:
  duration: "7m 40s"
  completed: "2026-04-13T06:35:42Z"
  tasks_completed: 2
  files_created: 2
  files_modified: 0
  tests_passed: 12
  tests_total: 12
---

# Phase 4 Plan 3: Emitter Localization Node (ADV-02) Summary

**One-liner:** Log-distance path loss emitter localization via multi-start Nelder-Mead with RSSI-weighted centroid initialization, SVD collinearity rejection, and 1 Hz RFEmitterMap publishing.

## What Was Built

`hackrf_ros/emitter_localization_node.py` — a standalone ROS2 node implementing ADV-02:

**Helper functions (pure, testable without ROS):**

- `_poses_well_spread(poses, min_sep_m)` — SVD-based collinearity detection. Rejects poses with max pairwise distance < min_sep_m or smallest singular value <= min_sep_m * 0.3 (collinear).
- `_estimate_emitter(observations)` — multi-start Nelder-Mead minimization of log-distance path loss residuals. Returns `(xe, ye, rssi0, n, cov_xx, cov_yy)` or `None`.

**Node — EmitterLocNode:**

- Subscribes to `/hackrf/detections` (RFDetectionArray, RELIABLE)
- TF2 lookup (base_link → map) at each detection callback
- Accumulates `{detection_id: {'obs': [(x, y, rssi)], 'last_seen': float}}` state
- Deduplication: if robot moved < `min_separation_m * 0.3` m since last obs, update RSSI in-place and `continue` (not `return`)
- 1 Hz timer: expires stale emitters (> `silence_timeout_s`), fits model, publishes RFEmitterMap
- `main()` uses `MultiThreadedExecutor` per Phase 2 D-17

## Optimizer Design

**Problem:** Nelder-Mead has no native bounds support. Standard single-start with initial guess at strongest-RSSI robot pose fails for symmetric observation geometries (4/5 poses at equal distance → same RSSI → n unidentifiable from that start).

**Solution adopted:**
1. Initial position: RSSI-weighted centroid of robot poses (pulls toward emitter region)
2. Candidate positions: centroid ± 2 m offset + midpoints toward each robot pose
3. n seeds: [2.0, 2.5] per candidate position (16 starts total)
4. Cost function: soft barrier penalty `1e6*(n-bound)^2` keeps Nelder-Mead in [1.5, 6.0]
5. Select best converged result with n ∈ [1.5, 6.0] by lowest cost

**Convergence time observed:** ~10-15 ms per call (16 starts × 5000 max iter each, typically converges in <200 iter)

## Test Results

All 12 tests pass (pytest):

| Class | Test | Result |
|-------|------|--------|
| TestPosesWellSpread | test_collinear_poses_rejected | PASS |
| TestPosesWellSpread | test_three_well_spread_poses_accepted | PASS |
| TestPosesWellSpread | test_two_close_poses_rejected | PASS |
| TestPosesWellSpread | test_minimum_count_two_rejected | PASS |
| TestPosesWellSpread | test_empty_poses_rejected | PASS |
| TestPosesWellSpread | test_spread_above_min_threshold | PASS |
| TestEstimateEmitter | test_minimum_observations_not_met | PASS |
| TestEstimateEmitter | test_returns_six_tuple | PASS |
| TestEstimateEmitter | test_position_within_1m_of_true | PASS |
| TestEstimateEmitter | test_path_loss_exponent_reasonable | PASS |
| TestEstimateEmitter | test_covariance_nonnegative | PASS |
| TestEstimateEmitter | test_high_residual_returns_none | PASS |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added ROS2 stubs to test file**
- **Found during:** Task 1 RED verification
- **Issue:** Test file imported `hackrf_ros.emitter_loc_node` which imports `rclpy` at module level; pytest fails without ROS2 environment. Project pattern (test_rf_map_node.py) uses sys.modules stubs.
- **Fix:** Added minimal ROS2 stubs (rclpy, tf2_ros, hackrf_interfaces) to top of test file before import, matching the established test_rf_map_node.py pattern.
- **Files modified:** `test/test_emitter_loc.py`
- **Commit:** 5f5c373

**2. [Rule 1 - Bug] Fixed optimizer convergence for degenerate observation geometries**
- **Found during:** Task 2 GREEN verification
- **Issue:** Plan's initial guess strategy (emitter at strongest-RSSI robot pose) caused Nelder-Mead to converge to n < 1.5 for symmetric observation geometries (e.g., 4/5 robot poses at equal distance from emitter → identical RSSI → system underdetermined from any single-pose start). Both `test_returns_six_tuple` and `test_position_within_1m_of_true` returned None.
- **Fix:** Replaced single-start from strongest-RSSI pose with multi-start from RSSI-weighted centroid of all poses + offset candidates + midpoints. Added soft penalty (1e6) to cost function to keep Nelder-Mead in physical n range [1.5, 6.0].
- **Files modified:** `hackrf_ros/emitter_loc_node.py` (`_estimate_emitter` function)
- **Commit:** 5f5c373

## Known Stubs

None — node publishes real RFEmitterMap messages with actual optimization results. No placeholder data flows to topic.

## Threat Flags

None — emitter_loc_node is a subscriber/publisher only. No new network endpoints, auth paths, file access, or schema changes. TF2 lookup follows existing rf_map_node.py pattern.

## Self-Check: PASSED

Files verified:
- `/home/mvc/dev_ws/hackrf_ros/hackrf_ros/emitter_loc_node.py` — EXISTS
- `/home/mvc/dev_ws/hackrf_ros/test/test_emitter_loc.py` — EXISTS

Commits verified:
- `0f2738f` — test(04-03): add failing tests for emitter localization helpers
- `5f5c373` — feat(04-03): implement emitter localization node (ADV-02)
