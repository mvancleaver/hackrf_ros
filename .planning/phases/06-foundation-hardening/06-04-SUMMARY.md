---
phase: 06-foundation-hardening
plan: 04
subsystem: tx-authorization
tags: [tx-controller, redis, threading, validation, dry-run]

# Dependency graph
requires:
  - phase: 06-01
    provides: exception hierarchy (TXBlockedError, TXHardBlockedError, etc.)
  - phase: 04-tx-authorization
    provides: TXController base with start_tx guards and _consume_auth_token
provides:
  - validate_tx() dry-run method checking all four TX guards without consuming auth token
  - periodic antenna confirmation re-read via threading.Timer every 30 seconds
affects: [hackrf_driver, bridge_node, 06-03]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "validate_tx uses GET (not GETDEL) for auth token — read-only pre-flight"
    - "threading.Timer with daemon=True for periodic background re-read"
    - "cancel timer before _tx_lock in stop_tx to prevent timer-lock ordering issues"

key-files:
  created: []
  modified:
    - hackrf_driver/hackrf_driver/tx_controller.py

key-decisions:
  - "validate_tx uses self._redis.get(AUTH_KEY) not _consume_auth_token — token must remain for subsequent start_tx"
  - "Antenna re-read timer set daemon=True so it does not block clean process exit"
  - "Timer cancelled in stop_tx before _tx_lock to avoid lock inversion with timer callback"
  - "Timer reschedules itself in _reread_antenna regardless of Redis success/failure (resilient to transient errors)"

patterns-established:
  - "validate_tx pattern: same guard order as start_tx but read-only (no side effects)"
  - "Periodic re-read: threading.Timer chain (each callback schedules next) with cancellation on shutdown"

requirements-completed: [TXS-01, TXS-03]

# Metrics
duration: 2min
completed: 2026-03-30
---

# Phase 06 Plan 04: TX Dry-Run Validation and Antenna Re-Read Summary

**validate_tx() dry-run with GET-not-GETDEL auth check and 30-second threading.Timer antenna confirmation re-read loop added to TXController**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-30T22:08:38Z
- **Completed:** 2026-03-30T22:10:32Z
- **Tasks:** 2
- **Files modified:** 1

## Accomplishments
- Added `validate_tx(freq_hz)` method that runs all four TX guards in order (antenna, hard_block, freq_filter, auth_token) and returns a `{valid, blocking_guard, detail}` dict — token is never consumed (GET, not GETDEL)
- Added `_schedule_antenna_reread()` and `_reread_antenna()` private methods implementing a self-rescheduling 30-second threading.Timer loop for runtime antenna state detection
- Timer cancellation added to `stop_tx()` before acquiring `_tx_lock` to prevent timer-lock ordering issues on shutdown

## Task Commits

Each task was committed atomically:

1. **Task 1: Add validate_tx() dry-run method** - `eabece0` (feat)
2. **Task 2: Add periodic antenna confirmation re-read timer** - `5967359` (feat)

**Plan metadata:** (pending docs commit)

## Files Created/Modified
- `hackrf_driver/hackrf_driver/tx_controller.py` - Added validate_tx() method and antenna re-read timer infrastructure

## Decisions Made
- validate_tx() uses `self._redis.get(self.AUTH_KEY)` (GET) not `_consume_auth_token()` (GETDEL) — the whole point of the dry-run is to leave the token in place for a subsequent start_tx()
- `threading.Timer.daemon = True` ensures the background re-read timer doesn't block process shutdown if stop_tx() is never called
- Timer cancelled at the top of `stop_tx()` before `_tx_lock` is acquired — the timer callback also tries to reschedule, so cancellation must happen before any lock acquisition to avoid ordering issues
- `_schedule_antenna_reread()` returns immediately if `skip_antenna_check=True` — no point re-reading when the check is bypassed

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- During test run, `test_driver.py::TestHackRFDriverUpdateParam::test_update_param_rejects_lna_out_of_range` failed — investigation showed this is caused by unstaged changes to `driver.py` from other parallel agents (not from our changes). TXController-specific tests (`test_tx_controller.py`) all pass (24/24).

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- validate_tx() is ready for use by command dispatch layer (06-03) and any caller needing pre-flight TX validation
- Antenna re-read timer ensures runtime antenna confirmation/revocation is detected without driver restart
- All TXController tests pass (24/24)

---
*Phase: 06-foundation-hardening*
*Completed: 2026-03-30*
