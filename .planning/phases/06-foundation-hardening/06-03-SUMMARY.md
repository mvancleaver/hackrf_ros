---
phase: 06-foundation-hardening
plan: 03
subsystem: bridge
tags: [redis, ros2, pubsub, reconnect, exponential-backoff, tx-authorization]

# Dependency graph
requires:
  - phase: 05-pymayhem-refactor
    provides: BridgeNode with bridge_services.py service pattern, hackrf_driver package

provides:
  - BridgeNode._bridge_loop with exponential backoff reconnect loop (1s-30s)
  - BridgeNode._run_pubsub_loop inner loop that raises on Redis error to trigger reconnect
  - /hackrf/confirm_antenna Trigger service setting hackrf:tx:antenna_confirmed = b'1'
  - hackrf_node.py DeprecationWarning at import time pointing to hackrf_driver + BridgeNode

affects: [07-watchdog-observability, 08-signal-capabilities, 06-04]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Outer reconnect loop with _stop_event.wait(backoff) for responsive shutdown
    - Inner pubsub loop raises on Redis error to trigger outer reconnect
    - State flush (_publish_state) immediately after each successful reconnect
    - Service factory closure pattern for antenna confirm (same as _make_mayhem_handler)
    - Module-level warnings.warn for deprecation at import time

key-files:
  created: []
  modified:
    - hackrf_ros/bridge_node.py
    - hackrf_ros/bridge_services.py
    - hackrf_ros/hackrf_node.py

key-decisions:
  - "D-07: exponential backoff 1s-30s in outer reconnect loop; _stop_event.wait(backoff) makes shutdown responsive during wait"
  - "_run_pubsub_loop raises on Redis error (no try/except) so outer loop catches and reconnects"
  - "_publish_state() flushed immediately after successful subscribe to avoid stale state on reconnect"
  - "hackrf_node.py retained as-is for colcon build compatibility — deprecation warning only, no code removal"

patterns-established:
  - "Reconnect pattern: outer while loop with exponential backoff, inner loop raises on error"
  - "Service factory: closure capturing redis_client, same pattern as _make_mayhem_handler"

requirements-completed: [REL-02, TXS-02, LEG-01]

# Metrics
duration: 5min
completed: 2026-03-30
---

# Phase 6 Plan 03: BridgeNode Resilience and Antenna Service Summary

**BridgeNode Redis reconnect with exponential backoff (1s-30s), /hackrf/confirm_antenna Trigger service, and hackrf_node.py DeprecationWarning at import**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-30T22:07:00Z
- **Completed:** 2026-03-30T22:11:27Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Replaced fatal `break` on Redis error with outer reconnect loop using exponential backoff (1s-30s)
- Extracted `_run_pubsub_loop` as inner loop that raises on Redis error to trigger reconnect
- Added `/hackrf/confirm_antenna` Trigger service that sets `hackrf:tx:antenna_confirmed = b'1'`
- Added module-level `DeprecationWarning` to hackrf_node.py pointing to hackrf_driver + BridgeNode
- State flushed immediately after each successful Redis reconnect

## Task Commits

Each task was committed atomically:

1. **Task 1: BridgeNode reconnect loop replacing fatal break** - `fc563aa` (feat)
2. **Task 2: Antenna confirmation service and legacy deprecation** - `9dc557d` (feat)

## Files Created/Modified
- `hackrf_ros/bridge_node.py` - Added `_MIN_BACKOFF`/`_MAX_BACKOFF` constants, rewrote `_bridge_loop` as outer reconnect loop, extracted `_run_pubsub_loop`
- `hackrf_ros/bridge_services.py` - Added `_make_antenna_confirm_handler` factory, registered `/hackrf/confirm_antenna` service
- `hackrf_ros/hackrf_node.py` - Added DEPRECATED module docstring, `import warnings`, `warnings.warn(DeprecationWarning)` at module level

## Decisions Made
- `_stop_event.wait(backoff)` used for backoff sleep so shutdown is responsive even during the wait interval
- `_run_pubsub_loop` intentionally has no try/except on `get_message` or `xrevrange` — errors propagate up to the outer loop to trigger reconnect
- `_publish_state()` called immediately after subscribe to flush stale state after any Redis restart
- hackrf_node.py retained without code removal for colcon build backward compatibility (LEG-01)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Worktree branch was at commit 24263e4 (old base), behind main. Ran `git rebase main` to bring it current before beginning. No code conflicts.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- BridgeNode now survives Redis restarts — ready for watchdog/observability work in Phase 7
- `/hackrf/confirm_antenna` service enables ROS2 callers to clear the TX antenna guard
- hackrf_node.py deprecated but still compiles — safe to remove in a future cleanup phase

---
*Phase: 06-foundation-hardening*
*Completed: 2026-03-30*
