---
phase: 01-sensor-foundations
plan: 02
subsystem: hackrf_ros
tags: [driver-upgrade, scipy-fft, spectrum-stamped, tf-broadcast, executor]
dependency_graph:
  requires: [SpectrumStamped]
  provides: [HackRFLifecycleNode-v2, /hackrf/spectrum, base_link->hackrf_antenna-TF]
  affects: [hackrf_ros, hackrf_lifecycle_node.py, launch/hackrf.launch.py]
tech_stack:
  added: [scipy.fft, tf2_ros.StaticTransformBroadcaster, rclpy.executors.MultiThreadedExecutor]
  patterns: [BEST_EFFORT QoS for streaming, static TF broadcast in on_configure, multi-threaded executor]
key_files:
  created:
    - test/test_driver_upgrade.py
  modified:
    - hackrf_ros/hackrf_lifecycle_node.py
    - launch/hackrf.launch.py
    - setup.py
    - package.xml
    - Dockerfile
decisions:
  - Used scipy.fft as drop-in replacement for numpy.fft (ARM NEON SIMD path for Jetson)
  - BEST_EFFORT QoS depth=5 for spectrum streaming, RELIABLE retained for sweep service
  - Identity quaternion for static TF (rotation correction deferred to Phase 2+)
  - Belt-and-suspenders TF — both driver on_configure and launch file broadcast the transform
metrics:
  duration: 206s
  completed: "2026-04-13T03:04:29Z"
  tasks_completed: 2
  tasks_total: 2
---

# Phase 1 Plan 02: Driver Upgrade Summary

Upgraded hackrf_lifecycle_node.py to use scipy.fft for ARM SIMD performance, publish SpectrumStamped with noise floor on BEST_EFFORT QoS, broadcast static TF from configurable parameters, and run on MultiThreadedExecutor.

## What Was Done

### Task 1: Replace numpy.fft with scipy.fft and add SpectrumStamped publish (TDD)
- **RED:** Wrote 13 failing tests in test/test_driver_upgrade.py covering scipy.fft usage, SpectrumStamped publish, TF broadcaster, and executor
- **GREEN:** Replaced `np.fft.fft/fftshift` with `scipy.fft.fft/fftshift` in `_fft_frame()` (drop-in, no arg changes)
- Replaced Float32MultiArray publisher on `/hackrf/psd` with SpectrumStamped publisher on `/hackrf/spectrum`
- QoS changed from RELIABLE to BEST_EFFORT (depth=5) for the spectrum publisher
- Removed old `/hackrf/tuning` topic (center_frequency_hz, sample_rate_hz, fft_size now in SpectrumStamped fields)
- Set `noise_floor_db = median(psd_db)` per message contract
- Added `scipy>=1.11` to setup.py install_requires
- Removed unused Float32MultiArray, MultiArrayDimension, MultiArrayLayout imports
- **Commits:** 90192e4 (RED), 0419b55 (GREEN)

### Task 2: Add TF broadcaster, parameterize frame, and upgrade executor
- Added 5 new parameters: `antenna_frame`, `parent_frame`, `antenna_x`, `antenna_y`, `antenna_z`
- StaticTransformBroadcaster created in `on_configure()` publishing parent_frame -> antenna_frame
- Identity quaternion (w=1.0) — static position only, rotation is Phase 2+ concern
- `header.frame_id` in SpectrumStamped now reads from `antenna_frame` parameter
- `main()` upgraded from `rclpy.spin(node)` to `MultiThreadedExecutor` with `executor.spin()`
- Launch file updated with `static_transform_publisher` node (belt-and-suspenders with driver TF)
- Added `geometry_msgs` and `tf2_ros` to package.xml dependencies
- Dockerfile updated with `ros-humble-tf2-ros`, `ros-humble-geometry-msgs`, and `scipy`
- **Commit:** f46d535

## Verification Results

All must_haves confirmed:
- Zero `np.fft` references in hackrf_lifecycle_node.py
- 3 `scipy.fft` references (import + 2 calls)
- SpectrumStamped published on `/hackrf/spectrum` with BEST_EFFORT QoS
- StaticTransformBroadcaster in on_configure() with configurable parent/antenna frames
- MultiThreadedExecutor in main()
- `antenna_frame` and `parent_frame` parameters declared in _declare_parameters()
- Launch file contains static_transform_publisher node
- setup.py install_requires contains scipy>=1.11
- Python syntax validates (ast.parse succeeds)
- 13/13 tests pass

## Deviations from Plan

### Auto-added (Rule 2)
**1. [Rule 2 - Missing dependency] Added geometry_msgs and tf2_ros to package.xml**
- **Found during:** Task 2
- **Issue:** Plan specified adding tf2_ros imports but did not mention package.xml dependency declarations
- **Fix:** Added `<depend>geometry_msgs</depend>` and `<depend>tf2_ros</depend>` to package.xml
- **Files modified:** package.xml

**2. [Rule 2 - Missing dependency] Added tf2_ros and scipy to Dockerfile**
- **Found during:** Task 2
- **Issue:** Plan mentioned Dockerfile for scipy but not for tf2_ros/geometry_msgs apt packages
- **Fix:** Added ros-humble-tf2-ros, ros-humble-geometry-msgs to apt install, scipy to pip install
- **Files modified:** Dockerfile

## Known Stubs

None. All fields are fully wired to runtime data.

## Decisions Made

1. Used scipy.fft as drop-in replacement (identical API to numpy.fft, but with ARM NEON SIMD path)
2. BEST_EFFORT QoS for streaming topics only; sweep service retains RELIABLE on its callback group
3. Identity quaternion for static TF — rotation parameters deferred to Phase 2+ per plan
4. Belt-and-suspenders TF broadcast: both launch file node and driver on_configure publish the same transform

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 (RED) | 90192e4 | test(01-02): add failing tests for driver upgrade |
| 1 (GREEN) | 0419b55 | feat(01-02): replace numpy.fft with scipy.fft and publish SpectrumStamped |
| 2 | f46d535 | feat(01-02): add TF broadcaster, parameterized frames, MultiThreadedExecutor |

## Self-Check: PASSED

All 7 files found. All 3 commits verified. Content checks: scipy.fft=2, np.fft=0, StaticTransformBroadcaster=2, MultiThreadedExecutor=2, static_transform_publisher=1. 13/13 tests pass.
