---
phase: 06-foundation-hardening
plan: "02"
subsystem: exception-dispatch-iq-seq
tags: [exceptions, error-handling, validation, redis, iq-stream, sequence-numbers]
dependency_graph:
  requires: [06-01]
  provides: [config-error-raise, dispatch-exception-boundary, iq-seq-numbers]
  affects: [hackrf_driver, redis_bridge, driver]
tech_stack:
  added: []
  patterns: [raise-on-invalid-param, typed-exception-boundary, structured-error-redis, epoch-counter-seq]
key_files:
  created: []
  modified:
    - hackrf_driver/hackrf_driver/driver.py
    - hackrf_driver/hackrf_driver/redis_bridge.py
    - hackrf_driver/tests/test_driver.py
decisions:
  - "_update_param raises HackRFConfigError instead of warning+return; callers must catch or let it propagate"
  - "_dispatch_command uses two-level except: typed (MayhemError|HackRFError) → warning + publish_error; generic Exception → error + publish_error"
  - "driver_epoch passed from HackRFDriver.__init__ to RedisBridge so seq numbers are stable across reconfiguration events"
metrics:
  duration: 2min
  completed_date: "2026-03-30"
  tasks_completed: 1
  files_changed: 3
---

# Phase 06 Plan 02: Exception Dispatch Boundary and IQ Sequence Numbers Summary

**One-liner:** HackRFConfigError raised on invalid params, typed exception boundary in _dispatch_command publishes structured errors to Redis, and every IQ XADD entry now carries an `epoch:counter` seq field for gap detection.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | _update_param raises HackRFConfigError and _dispatch_command exception boundary | 9e18be9 | driver.py, redis_bridge.py, test_driver.py |

## What Was Built

### driver.py changes (ERR-04)

- Added `from hackrf_driver.exceptions import HackRFConfigError`
- Replaced silent `warning + return` in `_update_param` with `raise HackRFConfigError(f"Parameter '{name}' value {value} out of range [{lo}, {hi}]")`
- Added `self._driver_epoch: int = int(time.time())` before RedisBridge construction
- Passed `driver_epoch=self._driver_epoch` to RedisBridge constructor

### redis_bridge.py changes (ERR-05 + REL-03)

- Added imports: `import time`, `from hackrf_driver.exceptions import HackRFError`, `from pymayhem.exceptions import MayhemError`
- Updated `RedisBridge.__init__` to accept optional `driver_epoch: int = None`; initialises `self._driver_epoch` and `self._seq_counter = 0`
- Updated `_xadd_iq` to build `seq = f'{self._driver_epoch}:{self._seq_counter}'.encode()`, increment counter, and include `b'seq': seq` in the XADD entry dict
- Replaced single `except Exception` in `_dispatch_command` with two-level catch: `(MayhemError, HackRFError)` → `warning + _publish_command_error`; `Exception` → `error + _publish_command_error`
- Added `_publish_command_error(action, error_type, message)` method that writes structured fields to `hackrf:cmd:last_error` hash; no-op if Redis not connected; swallows `RedisError` so error publishing never raises

### test_driver.py (deviation fix)

- Updated `test_update_param_rejects_lna_out_of_range` to use `assertRaises(HackRFConfigError)` instead of checking the param was unchanged after a silent drop

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated test expecting old silent-return behavior**
- **Found during:** Task 1 (test run after implementation)
- **Issue:** `test_update_param_rejects_lna_out_of_range` called `_update_param('lna_gain', 999)` and asserted the param was unchanged — testing the old warning+return path
- **Fix:** Updated test to `assertRaises(HackRFConfigError)` and still asserts param unchanged on exception
- **Files modified:** `hackrf_driver/tests/test_driver.py`
- **Commit:** 9e18be9 (included in same task commit)

## Known Stubs

None — all logic is wired with real exception types and Redis calls.

## Verification Results

- `grep "raise HackRFConfigError" hackrf_driver/hackrf_driver/driver.py` — 1 match
- `grep -c "self._logger.warning.*rejected.*outside hardware range"` — 0 (old warning removed)
- `grep "except.*MayhemError.*HackRFError" hackrf_driver/hackrf_driver/redis_bridge.py` — 1 match
- `grep -c "_publish_command_error" hackrf_driver/hackrf_driver/redis_bridge.py` — 3 (definition + 2 call sites)
- `grep "b'seq'" hackrf_driver/hackrf_driver/redis_bridge.py` — 1 match
- `grep -c "_seq_counter"` — 3 (init + increment + seq build)
- `grep -c "_driver_epoch" hackrf_driver/hackrf_driver/driver.py` — 2 (assignment + kwarg)
- `grep -c "driver_epoch" hackrf_driver/hackrf_driver/redis_bridge.py` — 4 (param + assignment + docstring)
- All 52 hackrf_driver tests pass

## Self-Check: PASSED
