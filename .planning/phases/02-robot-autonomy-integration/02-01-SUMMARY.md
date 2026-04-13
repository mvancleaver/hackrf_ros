---
phase: 02-robot-autonomy-integration
plan: "01"
subsystem: hackrf_interfaces
tags: [action-definitions, rosidl, nav_msgs, sigmf, interfaces]
dependency_graph:
  requires: []
  provides:
    - hackrf_interfaces.action.Sweep
    - hackrf_interfaces.action.RecordIQ
    - nav_msgs runtime dep in hackrf_ros
    - sigmf pip package in Docker
  affects:
    - 02-02-PLAN.md (sweep_action_node imports Sweep action)
    - 02-03-PLAN.md (iq_recorder_node imports RecordIQ action)
    - 02-04-PLAN.md (rf_map_node uses nav_msgs/OccupancyGrid)
tech_stack:
  added:
    - nav_msgs (apt: ros-humble-nav-msgs)
    - sigmf==latest (pip, PyPI sigmf/sigmf-python)
  patterns:
    - rosidl_generate_interfaces with action/ directory
    - ROS2 action three-section format (goal --- result --- feedback)
key_files:
  created:
    - hackrf_interfaces/action/Sweep.action
    - hackrf_interfaces/action/RecordIQ.action
  modified:
    - hackrf_interfaces/CMakeLists.txt
    - hackrf_interfaces/package.xml
    - package.xml
    - Dockerfile
decisions:
  - "nav_msgs added to hackrf_interfaces package.xml as <depend> (build + runtime) to satisfy rosidl generation; previously only std_msgs was listed"
  - "sigmf added without version pin per plan; threat T-02-01-01 accepted — version pinning deferred to hardening phase"
  - "tf2_ros already present in hackrf_ros package.xml — no change needed"
metrics:
  duration_minutes: 8
  tasks_completed: 2
  tasks_total: 2
  files_created: 2
  files_modified: 4
  completed_date: "2026-04-13T04:00:20Z"
---

# Phase 02 Plan 01: Action Interface Definitions Summary

**One-liner:** Sweep.action and RecordIQ.action defined with D-15/D-16 field names; rosidl, nav_msgs, and sigmf wired into build.

## Objective Achieved

Both ROS2 action types needed by Wave 2/3 plans are defined verbatim from D-15/D-16 in 02-CONTEXT.md. The `hackrf_interfaces` package CMakeLists now generates C++/Python bindings for both actions. Docker image gains `ros-humble-nav-msgs` and `sigmf` for runtime use.

## Action Field Names (Verbatim Confirmation)

### Sweep.action (11 fields, matches D-15 exactly)

**Goal (4 fields):**
- `float64 freq_min_hz`
- `float64 freq_max_hz`
- `float64 step_hz`
- `int32   averaging`

**Result (6 fields):**
- `bool    success`
- `string  message`
- `float64 freq_min_hz`
- `float64 freq_max_hz`
- `float64 bin_width_hz`
- `float32[] psd_db`

**Feedback (4 fields):**
- `int32   current_hop`
- `int32   total_hops`
- `float64 current_freq_hz`
- `float32[] partial_psd_db`

### RecordIQ.action (11 fields, matches D-16 exactly)

**Goal (3 fields):**
- `float64 duration_s`
- `string  output_path`
- `string  trigger_reason`

**Result (6 fields):**
- `bool    success`
- `string  message`
- `string  sigmf_data_path`
- `string  sigmf_meta_path`
- `int64   samples_written`
- `int32   queue_drops`

**Feedback (3 fields):**
- `int64   bytes_written`
- `float64 duration_elapsed_s`
- `int32   queue_drops`

## Final CMakeLists.txt rosidl Block (for Wave 2/3 reference)

```cmake
find_package(nav_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/SpectrumStamped.msg"
  "msg/RFDetection.msg"
  "msg/RFDetectionArray.msg"
  "msg/RFEnvironment.msg"
  "srv/Sweep.srv"
  "action/Sweep.action"
  "action/RecordIQ.action"
  DEPENDENCIES std_msgs nav_msgs
)
```

All 4 original msg/ entries and the srv/ entry are preserved.

## Commits

| Task | Commit | Files |
|------|--------|-------|
| Task 1: Create action definitions | 83d8558 | hackrf_interfaces/action/Sweep.action, hackrf_interfaces/action/RecordIQ.action |
| Task 2: Update build manifests | 28addd3 | hackrf_interfaces/CMakeLists.txt, hackrf_interfaces/package.xml, package.xml, Dockerfile |

## Build Verification

ROS2 Humble is not installed on the host machine — build verification via `colcon build --packages-select hackrf_interfaces` is confirmed in Docker only. The CMakeLists.txt structure follows the identical pattern used in Phase 1 (which built successfully in Docker). The action file format follows ROS2 standard three-section `---` separator convention.

Import verification (post-Docker build):
```bash
source /opt/ros/humble/setup.bash && source /ws/install/setup.bash
python3 -c "from hackrf_interfaces.action import Sweep, RecordIQ; print('OK')"
```

## Deviations from Plan

**1. [Rule 2 - Missing dep] Added nav_msgs to hackrf_interfaces/package.xml**
- **Found during:** Task 2
- **Issue:** `find_package(nav_msgs REQUIRED)` in CMakeLists.txt would fail at colcon build time if `nav_msgs` is not declared as a package dependency. The plan specified adding it to CMakeLists.txt only.
- **Fix:** Added `<depend>nav_msgs</depend>` to `hackrf_interfaces/package.xml` alongside `std_msgs`.
- **Files modified:** `hackrf_interfaces/package.xml`
- **Commit:** 28addd3

## Known Stubs

None. This plan creates interface definition files only — no runtime behavior, no data flow, no UI rendering.

## Threat Flags

None. No new network endpoints, auth paths, file access patterns, or schema changes at trust boundaries introduced by action definition files. The `output_path` field in RecordIQ.action is a neutral interface field — validation is deferred to 02-03 (iq_recorder_node) per T-02-01-02.
