---
phase: 05-pymayhem-refactor
plan: "05"
subsystem: test-migration
tags: [test-imports, regression, pymayhem, hackrf_driver, no-regression, sys-modules-isolation]

# Dependency graph
requires:
  - phase: 05-pymayhem-refactor
    plan: "04"
    provides: BridgeNode, bridge_services, all three packages complete
provides:
  - test/test_mayhem_serial.py importing from pymayhem._serial
  - test/test_redis_bridge.py importing from hackrf_driver.redis_bridge
  - test/test_tx_controller.py importing from hackrf_driver.tx_controller (new interface)
  - test/conftest.py patching hackrf_driver.redis_bridge.redis.Redis
  - test/test_hackrf_node_redis.py with setUpModule/tearDownModule sys.modules isolation
  - 135 total tests passing across all three packages
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - setUpModule/tearDownModule for sys.modules lifecycle management in test files that mock whole modules

# Key files
key-files:
  created: []
  modified:
    - test/test_mayhem_serial.py
    - test/test_redis_bridge.py
    - test/test_tx_controller.py
    - test/conftest.py
    - test/test_hackrf_node_redis.py
  deleted:
    - pymayhem/tests/__init__.py

# Key decisions
decisions:
  - setUpModule/tearDownModule pattern in test_hackrf_node_redis.py replaces module-level sys.modules patching to prevent cross-test session pollution — alphabetical collection order caused redis module to be replaced before other test files imported it
  - pymayhem/tests/__init__.py removed to allow pytest to collect tests from workspace root without module resolution errors
  - test/test_tx_controller.py fully rewritten to use module-level _make_ctrl() helper with hackrf_driver.tx_controller interface (TXController takes redis_mock, logger, hackrf_getter, device_lock, stop_rx_fn, start_rx_fn, freq_filter_enabled, skip_antenna_check — no node reference)

# Metrics
metrics:
  duration: 25min
  completed: 2026-03-30
  tasks_completed: 1
  files_modified: 6
---

# Phase 05 Plan 05: Test Import Migration Summary

Test files updated from old hackrf_ros.* import paths to their new package homes in pymayhem and hackrf_driver. Full 135-test suite passes with 0 failures. Two pre-existing bugs fixed as deviations.

## What Was Built

The final plan in the pymayhem-refactor phase: migrated the original `test/` suite imports to use the new package locations produced by Plans 01-04:

- `test/test_mayhem_serial.py`: `from pymayhem._serial import MayhemSerial`
- `test/test_redis_bridge.py`: `from hackrf_driver.redis_bridge import RedisBridge`
- `test/test_tx_controller.py`: `from hackrf_driver.tx_controller import TXController` with new primitive-callables interface
- `test/conftest.py`: autouse fixture patching `hackrf_driver.redis_bridge.redis.Redis`
- `test/test_hackrf_node_redis.py`: sys.modules lifecycle moved to setUpModule/tearDownModule

## Test Results

| Suite | Tests | Result |
|-------|-------|--------|
| pymayhem/tests/ | 18 | all pass |
| hackrf_driver/tests/ | 52 | all pass |
| test/ | 65 | all pass |
| **Total** | **135** | **0 failures** |

## Package Isolation Verified

- pymayhem: no rclpy, redis, pyhackrf2 imports
- hackrf_driver: no rclpy imports
- bridge_node.py: no pyhackrf2, mayhem_serial, tx_controller imports (comment only)
- hackrf_node.py: still exists (colcon build safe)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] MayhemSerial constructor signature mismatch in test/test_mayhem_serial.py**
- **Found during:** Task 1
- **Issue:** Old test used `MayhemSerial('/dev/null', self.logger, timeout=1.0)` with injected logger — new pymayhem._serial API uses stdlib logging and only accepts `(port, timeout)`. Two tests checked `self.logger.warnings` which no longer worked.
- **Fix:** Updated constructor call, replaced warning capture with stdlib `logging.getLogger('pymayhem.serial')` + `_WarningHandler` pattern (matching pymayhem/tests/test_mayhem_client.py). Removed warning assertion from `test_send_command_raises_on_double_timeout` to match pymayhem's canonical version.
- **Files modified:** test/test_mayhem_serial.py
- **Commit:** 28c857e

**2. [Rule 1 - Bug] sys.modules pollution in test_hackrf_node_redis.py causing cross-test failures**
- **Found during:** Task 1 verification
- **Issue:** Module-level `sys.modules['redis'] = MagicMock()` ran at pytest collection time (alphabetical order: h < r, t). When test_redis_bridge.py and test_tx_controller.py were collected after, `import redis` got the MagicMock. Two tests that relied on `redis.exceptions.ResponseError` as a real exception class failed: `test_open_returns_false_on_connection_error` and `test_native_getdel_first_fallback_to_lua`. This was a pre-existing bug not visible when running tests individually.
- **Fix:** Moved sys.modules patching from module-level to `setUpModule()` function; added `tearDownModule()` to restore original modules. Now patching only happens at execution time, not collection time.
- **Files modified:** test/test_hackrf_node_redis.py
- **Commit:** 28c857e

**3. [Rule 1 - Bug] pymayhem/tests/__init__.py causing collection error from workspace root**
- **Found during:** Task 1 full-suite verification
- **Issue:** When running pytest with workspace root as rootdir and pymayhem/tests/ as argument, the empty `__init__.py` caused pytest to try to import tests as `tests.test_mayhem_client` (no parent package on path).
- **Fix:** Removed the empty `__init__.py` so pytest treats test files as standalone rootless modules. Tests still pass when run from either pymayhem/ or workspace root.
- **Files modified:** pymayhem/tests/__init__.py (deleted)
- **Commit:** 28c857e

## Known Stubs

None — all test imports wired to their real package implementations. No placeholder data.

## Self-Check: PASSED

- SUMMARY.md created at .planning/phases/05-pymayhem-refactor/05-05-SUMMARY.md
- Commit 28c857e exists
- test/test_mayhem_serial.py, test/test_redis_bridge.py, test/test_tx_controller.py all exist
- hackrf_node.py still exists (colcon build safe)
- 135 tests pass, 0 failures
