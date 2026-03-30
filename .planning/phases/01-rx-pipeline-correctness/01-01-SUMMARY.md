---
phase: 01-rx-pipeline-correctness
plan: "01"
subsystem: driver
tags: [queue, threading, pyhackrf2, numpy, ros2, iq-streaming]

# Dependency graph
requires: []
provides:
  - Dual queue.Queue(maxsize=64) buffer replacing unsafe shared numpy array in hackrf_node.py
  - Stripped _rx_callback with bool return and drop-oldest overflow policy
  - _publish_iq timer callback handling all numpy int8-to-float32 conversion
  - /hackrf/iq topic replacing legacy hackrf_iq_data
  - CHUNK_IQ_PAIRS = 2048 fixed-chunk constant
  - _stop_event threading.Event for Plan 02 shutdown path
affects:
  - 01-02 (reconnect loop builds on _stop_event and _ros_queue established here)
  - 01-03 (class rename plan, uses HackRFPuiblisherNode skeleton established here)

# Tech tracking
tech-stack:
  added: [queue (stdlib), threading (stdlib)]
  patterns:
    - Drop-oldest bounded queue for RX callback buffering (D-02)
    - Bare enqueue callback pattern — all processing deferred to consumer

key-files:
  created: []
  modified:
    - hackrf_ros/hackrf_node.py

key-decisions:
  - "Dual queues (_ros_queue, _redis_queue) replace shared numpy buffer — eliminates RX/timer race condition (D-01)"
  - "Drop-oldest overflow policy: get_nowait then put_nowait on queue.Full (D-02)"
  - "Fixed 2048 IQ-pair chunk size via CHUNK_IQ_PAIRS constant; num_iq_samples_per_publish parameter removed (D-04)"
  - "RX callback returns self._stop_event.is_set() (bool) satisfying pyhackrf2 contract; legacy return 0 removed"
  - "Topic renamed to /hackrf/iq per ROS2 namespaced convention (D-11)"

patterns-established:
  - "Pattern 1: Bare enqueue in hardware callback — zero numpy, all processing in consumer (D-05 / RX-07)"
  - "Pattern 2: Drop-oldest queue drain — try get_nowait then put_nowait on Full (D-02)"

requirements-completed: [RX-01, RX-07]

# Metrics
duration: 2min
completed: 2026-03-30
---

# Phase 01 Plan 01: Buffer Replacement and RX Callback Strip Summary

**Dual queue.Queue(maxsize=64) buffers replace shared numpy buffer, stripping _rx_callback to a bare enqueue with bool return and moving all numpy IQ conversion into a new _publish_iq timer callback**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-30T03:50:55Z
- **Completed:** 2026-03-30T03:52:28Z
- **Tasks:** 1
- **Files modified:** 1

## Accomplishments
- Replaced unsafe `current_samples_buffer` numpy array with `_ros_queue` and `_redis_queue` (queue.Queue maxsize=64) eliminating the RX/timer race condition
- Stripped `_rx_callback` to 12 lines of bare enqueue with drop-oldest overflow policy and bool return satisfying pyhackrf2 contract
- Renamed `_read_and_publish_iq` to `_publish_iq` and moved all numpy frombuffer/reshape/normalize/interleave operations into the timer consumer
- Fixed topic to `/hackrf/iq`, removed `num_iq_samples_per_publish` parameter, added `CHUNK_IQ_PAIRS = 2048` constant

## Task Commits

Each task was committed atomically:

1. **Task 1: Replace buffer with dual queues and strip RX callback** - `54c4770` (feat)

**Plan metadata:** (docs commit follows)

## Files Created/Modified
- `hackrf_ros/hackrf_node.py` - Refactored with dual queues, stripped callback, renamed method, fixed topic

## Decisions Made
- Dual `queue.Queue(maxsize=64)` instances provide GIL-safe concurrent access without locks
- Drop-oldest overflow: discard the front item on `queue.Full`, then retry `put_nowait` — avoids blocking the pyhackrf2 USB callback thread
- `_stop_event.is_set()` return value bridges Plan 01 callback signature to Plan 02's shutdown mechanism
- `CHUNK_IQ_PAIRS = 2048` placed at module level per plan spec (not inside method)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Plan 02 can immediately build on `_stop_event` for the reconnection loop shutdown signal
- `_redis_queue` is a stub ready for Phase 3 Redis consumer wiring
- `_configure_hackrf` is preserved intact for Plan 02's rework
- All AST verification passes; file compiles cleanly with `python3 -m py_compile`

---
*Phase: 01-rx-pipeline-correctness*
*Completed: 2026-03-30*
