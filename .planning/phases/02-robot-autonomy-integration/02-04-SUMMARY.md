---
phase: 02-robot-autonomy-integration
plan: "04"
subsystem: hackrf_ros
tags: [occupancy-grid, nav2, ema-accumulation, tf2, sliding-window, tdd]
dependency_graph:
  requires:
    - hackrf_interfaces.msg.RFDetectionArray (Phase 1)
    - nav_msgs (02-01)
    - tf2_ros (Phase 1)
  provides:
    - hackrf_ros.rf_map_node.RFMapNode
    - /hackrf/rf_occupancy OccupancyGrid publisher
    - _power_to_cell, _world_to_cell, _compute_origin, _accumulate_detection,
      _build_grid_data (module-level pure functions)
  affects: []
tech_stack:
  added: []
  patterns:
    - EMA accumulation (alpha=0.1) with first-obs direct-set (not EMA from floor)
    - Module-level pure functions for Node-free unit testing
    - Sliding window grid origin following robot TF position
    - np.vectorize with otypes for empty-mask safety
    - Robust multi-file pytest stub augmentation (setdefault + hasattr patch)
key_files:
  created:
    - hackrf_ros/rf_map_node.py
    - test/test_rf_map_node.py
    - launch/autonomy_pipeline.launch.py
  modified:
    - setup.py
decisions:
  - "First observation sets cell directly (not EMA from -120 noise floor) per plan done criteria — produces faster convergence to observed signal level"
  - "np.vectorize otypes=['int16'] guards against ValueError on empty mask (all-unobserved grid)"
  - "Multi-file pytest stub augmentation: setdefault() + hasattr() patch ensures stubs from other test files do not shadow required attributes (Header, OccupancyGrid, RFDetectionArray)"
  - "Grid robot-anchor: single HackRF has no direction-of-arrival; robot position used as spatial anchor per Phase 2 scope — deliberate simplification documented in code"
metrics:
  duration_minutes: 4
  tasks_completed: 2
  tasks_total: 2
  files_created: 3
  files_modified: 2
  completed_date: "2026-04-13T04:15:17Z"
---

# Phase 02 Plan 04: RF Occupancy Map Node Summary

**One-liner:** RFMapNode EMA grid accumulation publishes nav_msgs/OccupancyGrid from /hackrf/detections with sliding-window TF-anchored origin; autonomy_pipeline.launch.py bundles all 6 Phase 2 nodes.

## Objective Achieved

`hackrf_ros/rf_map_node.py` fully implements MAP-01, MAP-02, MAP-03:
- RELIABLE subscriber to `/hackrf/detections` (RFDetectionArray)
- EMA power accumulation per cell (alpha=0.1); first observation sets directly
- Sliding window: grid re-centred on robot position each detection callback
- TF `base_link → grid_frame` lookup; update dropped silently on failure
- RELIABLE publisher for `/hackrf/rf_occupancy` (OccupancyGrid, 1 Hz timer)
- Configurable: grid_resolution, grid_width_m, grid_height_m, grid_frame, update_rate, ema_alpha
- 17 unit tests — all pure function tests, no rclpy.init required

`launch/autonomy_pipeline.launch.py` bundles all Phase 2 nodes with auto-configure/activate.

## Grid Dimensions (Default Parameters)

| Parameter | Value | Cells |
|-----------|-------|-------|
| grid_width_m | 50.0 m | 100 cols (50/0.5) |
| grid_height_m | 50.0 m | 100 rows (50/0.5) |
| grid_resolution | 0.5 m/cell | — |
| Total cells | — | 10,000 (40 KB float32) |
| Memory budget | — | 40 KB << Jetson ARM64 limit |

## Cell Value Formula

```python
def _power_to_cell(power_dbm: float) -> int:
    return int(max(0, min(100, (power_dbm + 120.0) / 80.0 * 100.0)))
# -120 dBm -> 0 (noise floor)
# -80  dBm -> 50
# -40  dBm -> 100 (strong signal)
# Outside range: clamped to [0, 100]
```

## EMA Accumulation Formula

- **alpha:** 0.1 (configurable via `ema_alpha` parameter)
- **First observation:** `cell_power[r, c] = power_dbm` (direct set, not EMA from -120)
- **Subsequent observations:** `cell_power[r, c] = (1 - 0.1) * cell_power[r, c] + 0.1 * power_dbm`

## Sliding Window Grid Origin

```python
origin_x = robot_x - (ncols / 2.0) * resolution
origin_y = robot_y - (nrows / 2.0) * resolution
```

Robot always at grid centre. Origin re-computed on each `_detections_callback` from TF.

## Pytest Output — All Phase 2 Tests (41 tests)

```
============================= test session info ==============================
platform linux -- Python 3.12.3, pytest-9.0.3, pluggy-1.6.0
collected 41 items

test/test_sweep_action_node.py::TestPlanHops::test_plan_hops_covers_band PASSED
test/test_sweep_action_node.py::TestPlanHops::test_plan_hops_spacing PASSED
test/test_sweep_action_node.py::TestPlanHops::test_plan_hops_zero_step_uses_usable_bw PASSED
test/test_sweep_action_node.py::TestBlendHop::test_blend_flat_psd PASSED
test/test_sweep_action_node.py::TestTukeyWeights::test_length PASSED
test/test_sweep_action_node.py::TestTukeyWeights::test_middle_is_one PASSED
test/test_sweep_action_node.py::TestTukeyWeights::test_ramp_down PASSED
test/test_sweep_action_node.py::TestTukeyWeights::test_ramp_up PASSED
test/test_sweep_action_node.py::TestFinalizeComposite::test_empty_bins_get_sentinel PASSED
test/test_sweep_action_node.py::TestFinalizeComposite::test_weighted_average_in_overlap PASSED
test/test_sweep_action_node.py::TestExecuteSweep::test_execute_sweep_success PASSED
test/test_sweep_action_node.py::TestCancelPath::test_cancel_restores_center_freq_and_returns_partial PASSED
test/test_iq_recorder_driver.py::TestRxCallbackFanOut::test_drop_oldest_when_recorder_queue_full PASSED
test/test_iq_recorder_driver.py::TestRxCallbackFanOut::test_fanout_to_both_queues_when_recorder_active PASSED
test/test_iq_recorder_driver.py::TestRxCallbackFanOut::test_no_fanout_when_recorder_inactive PASSED
test/test_iq_recorder_driver.py::TestRecordingStartService::test_recording_start_creates_queue PASSED
test/test_iq_recorder_driver.py::TestRecordingStopService::test_recording_stop_clears_queue PASSED
test/test_iq_recorder_node.py::TestWriteThread::test_sentinel_exits_cleanly PASSED
test/test_iq_recorder_node.py::TestWriteThread::test_write_thread_drains_queue PASSED
test/test_iq_recorder_node.py::TestBuildSigmfMeta::test_meta_pose_none_stored_as_null PASSED
test/test_iq_recorder_node.py::TestBuildSigmfMeta::test_meta_with_pose PASSED
test/test_iq_recorder_node.py::TestIqCallback::test_iq_callback_no_write_when_queue_none PASSED
test/test_iq_recorder_node.py::TestIqCallback::test_iq_callback_puts_to_write_queue PASSED
test/test_iq_recorder_node.py::TestInvalidDirectory::test_bad_output_path_returns_failure PASSED
test/test_rf_map_node.py::TestPowerToCell::test_noise_floor_maps_to_zero PASSED
test/test_rf_map_node.py::TestPowerToCell::test_midpoint_maps_to_fifty PASSED
test/test_rf_map_node.py::TestPowerToCell::test_strong_signal_maps_to_hundred PASSED
test/test_rf_map_node.py::TestPowerToCell::test_below_floor_clamped_to_zero PASSED
test/test_rf_map_node.py::TestPowerToCell::test_above_ceiling_clamped_to_hundred PASSED
test/test_rf_map_node.py::TestWorldToCell::test_basic_conversion PASSED
test/test_rf_map_node.py::TestWorldToCellBounds::test_returns_none_when_outside_grid PASSED
test/test_rf_map_node.py::TestWorldToCellBounds::test_returns_none_at_col_overflow PASSED
test/test_rf_map_node.py::TestWorldToCellBounds::test_valid_boundary_cell PASSED
test/test_rf_map_node.py::TestEmaAccumulation::test_first_observation_sets_directly PASSED
test/test_rf_map_node.py::TestEmaAccumulation::test_second_observation_applies_ema PASSED
test/test_rf_map_node.py::TestEmaAccumulation::test_two_observations_from_plan_spec PASSED
test/test_rf_map_node.py::TestBuildGridData::test_unobserved_cells_are_minus_one PASSED
test/test_rf_map_node.py::TestBuildGridData::test_observed_cell_in_valid_range PASSED
test/test_rf_map_node.py::TestBuildGridData::test_grid_data_dtype_is_int8_compatible PASSED
test/test_rf_map_node.py::TestGridOrigin::test_origin_centers_on_robot PASSED
test/test_rf_map_node.py::TestGridOrigin::test_origin_at_zero_robot_position PASSED

41 passed in 0.28s
```

## autonomy_pipeline.launch.py — Node List

| # | Node | Type | Key Config |
|---|------|------|-----------|
| 1 | hackrf_tf_publisher | static_transform_publisher | base_link → hackrf_antenna |
| 2 | hackrf_node | LifecycleNode | hackrf_rx.yaml, auto-configure/activate |
| 3 | cfar_node | Node | pfa=1e-4, guard=8, train=32 |
| 4 | sweep_action_node | Node | default sweep params |
| 5 | iq_recorder_node | Node | default recording params |
| 6 | rf_map_node | Node | 50m x 50m @ 0.5m, frame=map, 1 Hz |

Auto-configure/activate: same `OnStateTransition` lifecycle pattern as `sensor_pipeline.launch.py`.

## colcon Build

ROS2 Humble is not installed on the host machine. Build verification requires Docker:

```bash
# In Docker
source /opt/ros/humble/setup.bash
cd /ws
colcon build --packages-select hackrf_ros
source install/setup.bash
ros2 pkg executables hackrf_ros
```

Entry point syntax verified on host: `setup.py` has 8 console_scripts entries (all correctly formatted):

```
hackrf_node = hackrf_ros.hackrf_lifecycle_node:main
spectrum_node = hackrf_ros.spectrum_node:main
sweep_node = hackrf_ros.sweep_node:main
sweep_action_node = hackrf_ros.sweep_action_node:main
sweep_display = hackrf_ros.sweep_display:main
cfar_node = hackrf_ros.cfar_node:main
iq_recorder_node = hackrf_ros.iq_recorder_node:main
rf_map_node = hackrf_ros.rf_map_node:main
```

## Commits

| Task | Phase | Commit | Files |
|------|-------|--------|-------|
| Task 1 | RED | 97db249 | test/test_rf_map_node.py |
| Task 1 | GREEN | ce87d31 | hackrf_ros/rf_map_node.py, test/test_rf_map_node.py |
| Task 2 | feat | 0a254fd | launch/autonomy_pipeline.launch.py, setup.py, test/test_rf_map_node.py |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] np.vectorize ValueError on empty observed mask**
- **Found during:** Task 1 GREEN — `test_unobserved_cells_are_minus_one` fails with `ValueError: cannot call vectorize on size 0 inputs unless otypes is set`
- **Issue:** `np.vectorize(_power_to_cell)(empty_array)` raises `ValueError` when no cells are observed (all-False mask → empty selection).
- **Fix:** Added `otypes=[np.int16]` to `np.vectorize()` call, and guarded with `if mask.any()` to skip the call entirely when no cells are observed.
- **Files modified:** `hackrf_ros/rf_map_node.py`
- **Commit:** ce87d31

**2. [Rule 1 - Bug] numpy bool `np.True_ is True` assertion failure**
- **Found during:** Task 1 GREEN — `test_first_observation_sets_directly` fails with `assert np.True_ is True`
- **Issue:** Python's `is` identity check fails for `np.True_` vs Python `True`. Correct comparison uses `==`.
- **Fix:** Changed `assert node._cell_observed[5, 5] is True` to `assert node._cell_observed[5, 5] == True` in test.
- **Files modified:** `test/test_rf_map_node.py`
- **Commit:** ce87d31

**3. [Rule 1 - Bug] Multi-file pytest stub collision: Header missing from std_msgs.msg**
- **Found during:** Task 2 verification — full `pytest test/test_sweep_action_node.py test/test_iq_recorder_driver.py test/test_iq_recorder_node.py test/test_rf_map_node.py` fails with `ImportError: cannot import name 'Header' from 'std_msgs.msg'`
- **Issue:** `test_iq_recorder_node.py` registers `std_msgs.msg` without `Header` via `sys.modules.setdefault()`. When collected first (alphabetically before `test_rf_map_node.py`), its stub wins and `rf_map_node.py`'s `from std_msgs.msg import Header` fails at collection time.
- **Fix:** After `setdefault()` in `test_rf_map_node.py`, added `hasattr` guards that augment any already-registered stub: `if not hasattr(sys.modules['std_msgs.msg'], 'Header'): sys.modules['std_msgs.msg'].Header = object`. Same pattern applied for `OccupancyGrid` and `RFDetectionArray`.
- **Files modified:** `test/test_rf_map_node.py`
- **Commit:** 0a254fd

## Known Stubs

None. All data flow is wired:
- `/hackrf/detections` → `_detections_callback` → EMA grid accumulation → `_publish_grid` → `/hackrf/rf_occupancy`
- TF lookup provides robot position for grid origin
- All cell value and EMA formulas use real math (no placeholders)

## Threat Flags

None. No new network endpoints beyond `/hackrf/rf_occupancy` documented in the plan's threat model. All threat mitigations present:

| Threat ID | Mitigation | Status |
|-----------|-----------|--------|
| T-02-04-01 | `_power_to_cell` clamps to [0, 100]; EMA limits rate of change | Implemented |
| T-02-04-02 | RELIABLE QoS depth=10 buffers bursts | Implemented |
| T-02-04-03 | Robot-internal topic, not externally published | By design |
| T-02-04-04 | 10,000 cells × 4 bytes = 40 KB — well within budget | Verified |
| T-02-04-05 | TF security is system-level concern | Accepted |

## Self-Check: PASSED

- [x] `hackrf_ros/rf_map_node.py` exists (332 lines, > 150 minimum)
- [x] `test/test_rf_map_node.py` exists (17 tests, all passing)
- [x] `launch/autonomy_pipeline.launch.py` exists with all 6 nodes
- [x] `setup.py` contains `rf_map_node = hackrf_ros.rf_map_node:main`
- [x] `setup.py` contains `launch/autonomy_pipeline.launch.py` in data_files
- [x] All 8 original + new entry points preserved in setup.py
- [x] All 41 Phase 2 tests pass together in one pytest session
- [x] No matplotlib in `autonomy_pipeline.launch.py`
- [x] `flake8 hackrf_ros/rf_map_node.py --max-line-length 99` — clean
- [x] Commits: 97db249 (RED), ce87d31 (GREEN), 0a254fd (Task 2)
