---
phase: 02-robot-autonomy-integration
plan: "02"
subsystem: hackrf_ros
tags: [action-server, sweep, tukey-blending, spectrum-stitching, tdd]
dependency_graph:
  requires:
    - hackrf_interfaces.action.Sweep (02-01)
    - hackrf_interfaces.msg.SpectrumStamped (Phase 1)
  provides:
    - hackrf_ros.sweep_action_node.SweepActionNode
    - /hackrf/sweep action endpoint
    - _plan_hops, _tukey_weights, _blend_hop_into_composite, _finalize_composite (module-level pure functions)
  affects:
    - 02-03-PLAN.md (iq_recorder_node — same threading pattern)
    - 02-04-PLAN.md (rf_map_node — subscribes to /hackrf/detections)
tech_stack:
  added: []
  patterns:
    - ROS2 ActionServer with ReentrantCallbackGroup + MultiThreadedExecutor
    - AsyncParametersClient for cross-node parameter writes
    - Tukey-blended linear-domain PSD stitching (alpha=0.2)
    - Module-level pure functions for testability without rclpy.init
    - threading.Event for spectrum synchronization between ROS callback and execute thread
key_files:
  created:
    - hackrf_ros/sweep_action_node.py
    - test/test_sweep_action_node.py
  modified:
    - setup.py
decisions:
  - "Pure functions (_plan_hops, _tukey_weights, _blend_hop_into_composite, _finalize_composite) placed at module level — not as methods — so tests can import them without instantiating Node (which requires rclpy.init)"
  - "Cancel test fixed: MagicMock auto-generated request fields produce freq_max==freq_min==1.0 which fails T-02-02-01 validation; test updated to explicitly set goal_handle.request.* fields"
  - "colcon build deferred to Docker (ROS2 Humble not on host) — same constraint as 02-01"
metrics:
  duration_minutes: 20
  tasks_completed: 2
  tasks_total: 2
  files_created: 2
  files_modified: 1
  completed_date: "2026-04-13T04:25:00Z"
---

# Phase 02 Plan 02: Sweep Action Server Summary

**One-liner:** SweepActionNode implements wideband sweep as a cancellable ROS2 action with Tukey-blended linear-domain PSD stitching and T-02-02-01/02/03 input validation.

## Objective Achieved

`hackrf_ros/sweep_action_node.py` fully implements SWP-01, SWP-02, SWP-03:
- Action server on `/hackrf/sweep` accepting `hackrf_interfaces/action/Sweep` goals
- Hop-by-hop retuning via `AsyncParametersClient` targeting `/hackrf_node`
- RELIABLE subscription to `/hackrf/spectrum` (SpectrumStamped) per hop
- Tukey window (alpha=0.2) applied in linear domain for smooth hop boundary blending
- Per-hop feedback with partial stitched PSD
- Cancel restores original center_frequency; partial result returned if ≥1 hop done

## Pytest Output (All Tests Passing)

```
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.3, pluggy-1.6.0
collected 12 items

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

12 passed in 0.15s
```

## Hop Planning Algorithm

| Parameter | Value | Source |
|-----------|-------|--------|
| Default step_hz | 20e6 Hz | `DEFAULT_STEP_HZ` constant |
| Usable BW fraction | 0.8 (80%) | `USABLE_BW_FRACTION` constant, matches driver |
| Effective step | `min(step_hz, usable_bw)` | prevents over-stepping usable band |
| EDGE_TRIM | 410 bins (10% each side of 4096) | matches `hackrf_lifecycle_node.py` |
| Tukey alpha | 0.2 | 10% taper per side, 80% flat top |
| Max hops | 500 | T-02-02-03 DoS guard |
| Max averaging | 64 | T-02-02-02 DoS guard |
| PLL settle delay | 0.025 s | `PLL_SETTLE_S` constant |

Example: `_plan_hops(2.4e9, 2.5e9, 20e6, step_hz=20e6)` → centers spaced 16 MHz apart
(effective_step = min(20e6, 16e6) = 16e6), covering the full 100 MHz band in ~7 hops.

## Security Mitigations Applied (Threat Register)

| Threat ID | Mitigation | Location |
|-----------|-----------|---------|
| T-02-02-01 | Validate freq_max > freq_min, both in [1 MHz, 6 GHz]; abort on violation | `_execute_sweep` top |
| T-02-02-02 | Clamp averaging to [1, 64]; 0 → DEFAULT_AVERAGING=4 | `_execute_sweep` top |
| T-02-02-03 | Abort if len(hops) > 500 with descriptive message | `_execute_sweep` after plan |
| T-02-02-04 | Restore is best-effort; WARN logged on failure | `_execute_sweep` restore block |
| T-02-02-05 | Only center_frequency written via param client | N/A (architectural) |

## AsyncParametersClient API (Humble Quirks)

`AsyncParametersClient.set_parameters()` returns a Future. `rclpy.spin_until_future_complete(node, future, timeout_sec=5.0)` blocks the calling thread until done. In unit tests this is mocked away entirely — `_set_center_freq` is replaced with `lambda hz: True`. No surprises with the Humble API.

## colcon Build

ROS2 Humble is not installed on the host machine. Build verification requires Docker:

```bash
# In Docker
source /opt/ros/humble/setup.bash
cd /ws
colcon build --packages-select hackrf_ros
source install/setup.bash
ros2 pkg executables hackrf_ros | grep sweep_action_node
python3 -c "from hackrf_ros.sweep_action_node import SweepActionNode; print('OK')"
```

Import verified on host with mocked ROS2 stubs:
```
Import OK
SweepActionNode: <class 'hackrf_ros.sweep_action_node.SweepActionNode'>
main present: True
```

setup.py entry points (7 total, 1 new):
```python
'hackrf_node = hackrf_ros.hackrf_lifecycle_node:main',
'spectrum_node = hackrf_ros.spectrum_node:main',
'sweep_node = hackrf_ros.sweep_node:main',
'sweep_action_node = hackrf_ros.sweep_action_node:main',   # NEW
'sweep_display = hackrf_ros.sweep_display:main',
'cfar_node = hackrf_ros.cfar_node:main',
'iq_recorder_node = hackrf_ros.iq_recorder_node:main',
```

## Commits

| Task | Commit | Files |
|------|--------|-------|
| Task 1: RED — failing tests | 12a3bbb | test/test_sweep_action_node.py |
| Task 1: GREEN — implementation + test fix | 738c042 | hackrf_ros/sweep_action_node.py, test/test_sweep_action_node.py |
| Task 2: Entry point | 2289574 | setup.py |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Cancel test fix: MagicMock request fields causing early abort**
- **Found during:** Task 1 GREEN phase — cancel test (Test 6) failed despite 11/12 passing
- **Issue:** `MagicMock()` auto-generates `request.freq_min_hz = request.freq_max_hz = 1.0` (via `__float__`), triggering T-02-02-01 validation (`freq_max <= freq_min`), causing `goal_handle.abort()` before any hops ran. `_set_center_freq` was never called.
- **Fix:** Test updated to explicitly set `goal_handle.request.freq_min_hz`, `freq_max_hz`, `step_hz`, `averaging` with correct values. Also corrected `get_parameter` mock to use proper `get_parameter_value().double_value` chain.
- **Files modified:** `test/test_sweep_action_node.py`
- **Commit:** 738c042

## Known Stubs

None. All data flow is wired: SpectrumStamped subscription → `_blend_hop_into_composite` → `_finalize_composite` → Sweep.Result.psd_db. No hardcoded placeholders.

## Threat Flags

None. No new network endpoints beyond the `/hackrf/sweep` action server documented in the plan's threat model. The `AsyncParametersClient` scope is limited to `center_frequency` on the driver node — no new trust boundary expansion.

## Self-Check: PASSED

- [x] `hackrf_ros/sweep_action_node.py` exists (517 lines, > 200 minimum)
- [x] `test/test_sweep_action_node.py` exists (12 tests, all passing)
- [x] `setup.py` contains `sweep_action_node = hackrf_ros.sweep_action_node:main`
- [x] All 5 original entry points still present in setup.py
- [x] `sweep_node.py` unmodified (last commit: `39417ee feat(phase1): complete Phase 1 sensor foundations`)
- [x] `flake8 hackrf_ros/sweep_action_node.py --max-line-length 99` — clean (no output)
- [x] No imports from `sweep_node.py` or `sweep_display.py`
- [x] Commits: 12a3bbb (RED), 738c042 (GREEN), 2289574 (setup.py)
