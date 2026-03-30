---
phase: 01-rx-pipeline-correctness
plan: "02"
subsystem: driver
tags: [threading, reconnect, device-lock, exponential-backoff, lifecycle, pyhackrf2, ros2]

# Dependency graph
requires:
  - 01-01  # dual queues, _stop_event, _rx_callback bare enqueue pattern
provides:
  - _device_lock (threading.RLock) protecting all stop/start sequencing
  - _last_params dict storing current parameter values for reconnect restore
  - _try_connect() with exponential backoff retry on device open failure
  - _reconnect_callback() timer callback cancelling itself before retrying
  - _apply_last_params() restoring frequency/gain/sample-rate after reconnect
  - _configure_device() replacing _configure_hackrf with deadlock-safe stop/sleep/start
  - destroy_node() safely cancelling timer, stopping stream, closing device
  - Node starts without HackRF and enters reconnect loop (D-08)
affects:
  - 01-03 (class rename plan can now refactor HackRFPuiblisherNode -> HackRFNode with this cleaner base)

# Tech tracking
tech-stack:
  added: [threading.RLock (stdlib)]
  patterns:
    - Exponential backoff via rclpy timer doubling (D-06): _reconnect_delay doubles on each failure, capped at 30s
    - _device_lock (RLock) serialises all stop_rx/start_rx sequences — never call stop_rx from callback thread (RX-05)
    - 100ms firmware settling sleep inside _device_lock after stop_rx (Pitfall B / libhackrf #916)
    - _last_params dict populated from declared ROS parameters; updated on each _on_parameter_event (D-07)
    - destroy_node cancels reconnect timer before touching device (RX-06)

key-files:
  created: []
  modified:
    - hackrf_ros/hackrf_node.py

key-decisions:
  - "_device_lock is threading.RLock (not Lock) — _configure_device can be called from paths that already hold the lock during init without deadlock"
  - "_last_params populated after declare_parameter calls to ensure get_parameter succeeds; ordering matters in __init__"
  - "Timer for _publish_iq created unconditionally after _try_connect — always exists regardless of device availability (D-08)"
  - "Stale timer cancel guard in _configure_device failure path — cancel before create to prevent timer accumulation (Pitfall F)"
  - "self.hackrf renamed to self._hackrf throughout — private prefix per D-14 / CONVENTIONS.md"

# Metrics
duration: 2min
completed: 2026-03-30
---

# Phase 01 Plan 02: Reconnect Loop and Lifecycle Management Summary

**Reconnect loop with exponential backoff (1s-30s), _device_lock for stop/start serialisation, _last_params restore on reconnect, and fully safe destroy_node replacing the original _configure_hackrf and bare destroy_node**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-30T03:54:47Z
- **Completed:** 2026-03-30T03:56:43Z
- **Tasks:** 1
- **Files modified:** 1

## Accomplishments

- Replaced inline `try: self.hackrf = pyhackrf2.HackRF()` block with `_try_connect()` call — node now starts without a device and enters a reconnect loop (D-08)
- Added `_device_lock = threading.RLock()` protecting all stop_rx/start_rx sequences to eliminate the deadlock path (RX-05)
- Added `_last_params` dict populated from declared ROS parameters — restored automatically on every reconnect (D-07)
- Implemented exponential backoff: `_reconnect_delay` starts at 1s, doubles each failure, caps at 30s (D-06)
- Added `_reconnect_callback()` that cancels its own timer before calling `_try_connect()` (Pitfall F guard)
- Added `_apply_last_params()` applying center_freq / sample_rate / lna_gain / vga_gain / amplifier_on to device
- Replaced `_configure_hackrf` with `_configure_device`: holds `_device_lock`, uses `_stop_event.set()` before `stop_rx()`, sleeps 100ms, then restarts (Pitfall A + Pitfall B)
- `_configure_device` resets `_reconnect_delay` on success and cancels stale timer before creating new one on failure (Pitfall F)
- Replaced `destroy_node` with fully safe version: cancels reconnect timer first, then acquires `_device_lock` to stop stream and close device (RX-06)
- Renamed `self.hackrf` → `self._hackrf` throughout (D-14 private naming convention)
- Timer for `_publish_iq` moved to unconditional position after `_try_connect()`

## Task Commits

Each task was committed atomically:

1. **Task 1: Add _device_lock, _last_params, and replace _configure_hackrf with _configure_device** - `c43a0d0` (feat)

**Plan metadata:** (docs commit follows)

## Files Created/Modified

- `hackrf_ros/hackrf_node.py` — Reconnect loop, device lock, safe lifecycle management

## Decisions Made

- `threading.RLock` chosen over `threading.Lock` — allows safe nested acquisition if `_configure_device` is called from an already-locked path during init
- `_last_params` initialised after `declare_parameter` calls to ensure `get_parameter()` succeeds; parameter declaration must precede any `get_parameter` calls
- `_publish_iq` timer always created unconditionally after `_try_connect` so it runs even when no device is present — satisfies D-08
- Stale timer cancel guard in `_configure_device` failure path: `if self._reconnect_timer: self._reconnect_timer.cancel()` before `create_timer` — prevents Pitfall F timer accumulation
- `self.hackrf` renamed to `self._hackrf` to match the private attribute prefix convention (D-14)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

One ordering issue identified and fixed inline: the plan spec showed `_last_params` initialization before `declare_parameter` calls, which would cause `get_parameter` to raise `ParameterNotDeclaredException`. Fixed by moving `_last_params` population to after all `declare_parameter` calls. No impact on behaviour.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Plan 03 can immediately build on the clean `_configure_device` / `_last_params` foundation for parameter validation
- `_hackrf` private attribute naming is now consistent — Plan 03 class rename (`HackRFPuiblisherNode` → `HackRFNode`) has a clean base to work from
- `destroy_node` is fully safe — no lifecycle concerns carried forward
- All AST verification passes; file compiles cleanly with `python3 -m py_compile`

## Self-Check: PASSED

- hackrf_ros/hackrf_node.py: FOUND
- 01-02-SUMMARY.md: FOUND
- Commit c43a0d0: FOUND

---
*Phase: 01-rx-pipeline-correctness*
*Completed: 2026-03-30*
