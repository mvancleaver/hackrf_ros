---
phase: 03-reliability
plan: 01
subsystem: hackrf_lifecycle_node
tags: [agc, gain-control, reliability, diagnostics, tdd]
dependency_graph:
  requires: []
  provides: [AGC feedback loop, clip-rate-based gain reduction, diagnostics agc_last_action]
  affects: [hackrf_ros/hackrf_lifecycle_node.py]
tech_stack:
  added: []
  patterns: [source-level TDD without rclpy, ROS2 set_parameters + hardware register dual-write]
key_files:
  created:
    - test/test_agc.py
  modified:
    - hackrf_ros/hackrf_lifecycle_node.py
decisions:
  - AGC_LNA_STEP=8 matches HackRF firmware LNA hardware step granularity (not configurable below 8 dB)
  - AGC_VGA_STEP=4 (2 x minimum 2 dB step) balances responsiveness vs oscillation
  - LNA reduced first (before VGA) because LNA sets noise figure — reducing LNA first preserves signal-to-noise ratio better than VGA reduction
  - AGC writes both ROS2 param store (set_parameters) and hardware register (device_lock + hackrf.lna_gain) to keep them in sync
  - AGC hold counter decrements on every publish cycle (including hold cycles) — snapshot refreshed during hold to avoid false trigger on resume
metrics:
  duration_seconds: 240
  completed_date: "2026-04-13T05:13:59Z"
  tasks_completed: 2
  files_modified: 2
---

# Phase 3 Plan 01: AGC (Automatic Gain Control) Summary

**One-liner:** AGC loop with LNA-first reduction, clip-count hysteresis (10 cycles), and diagnostics reporting via `_agc_tick()` called each PSD publish cycle.

## What Was Built

Automatic gain control for the HackRF lifecycle driver. When the ADC clip rate (samples hitting ±127) exceeds 5 clips within a publish window, the driver reduces LNA gain first (by 8 dB), then VGA gain (by 4 dB) if LNA is already at 0. A 10-cycle hold timer prevents oscillation. All AGC activity is surfaced in `/diagnostics` via the `agc_last_action` key-value entry.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Write AGC unit tests (RED) | 4dbe301 | test/test_agc.py |
| 2 | Implement AGC in driver (GREEN) | e10a2ee | hackrf_ros/hackrf_lifecycle_node.py |

## Implementation Details

### New Constants (module-level)

```python
AGC_HOLD_FRAMES = 10   # publish cycles before AGC may fire again
AGC_CLIP_WINDOW = 5    # clips within this many cycles triggers reduction
AGC_LNA_STEP = 8       # dB — HackRF LNA steps in 8 dB increments
AGC_VGA_STEP = 4       # dB — reduce VGA by 4 dB
```

### New Instance Variables (`__init__`)

- `_agc_hold_counter: int = 0` — cycles remaining in hold period
- `_agc_clip_snapshot: int = 0` — clip count at last AGC check
- `_agc_cycle_counter: int = 0` — publish cycles since activation
- `_agc_last_action: str = 'none'` — human-readable last AGC event

### Wiring

- `_agc_tick()` called at end of `_process_and_publish_inner()` each publish cycle
- `_diagnostics_callback()` reports `agc_last_action` via `stat.add()`

## Verification Results

```
test/test_agc.py: 12/12 PASSED (GREEN)
test/test_driver_upgrade.py: 13/13 PASSED (no regressions)
AST syntax check: OK
```

## Deviations from Plan

### Pre-existing flake8 Issues (Out of Scope)

The plan specified `flake8 exits 0 (line length <=99)`. With `--max-line-length=99`, there are 15 pre-existing flake8 violations in lines 253-281 (`_declare_parameters` E128 indentation) and line 464 (`_fft_frame` E741 ambiguous variable name `I`). These exist in the original file before this plan and are unrelated to AGC changes. None of the new AGC code (lines 64-67, 87-93, 406-452, 552, 703) introduces any new flake8 violations. Logged to deferred-items.

## Threat Mitigations Applied

| Threat ID | Mitigation |
|-----------|-----------|
| T-03-01-01 | AGC_HOLD_FRAMES=10 enforces minimum 10 publish cycles between reductions |
| T-03-01-02 | `max(0, gain - step)` clamps all reductions to PARAM_RANGES lower bound |

## Known Stubs

None — AGC is fully wired to hardware registers and ROS2 param store.

## Self-Check: PASSED

- test/test_agc.py: FOUND
- hackrf_ros/hackrf_lifecycle_node.py: modified and verified
- Commit 4dbe301: FOUND (test RED phase)
- Commit e10a2ee: FOUND (feat GREEN phase)
