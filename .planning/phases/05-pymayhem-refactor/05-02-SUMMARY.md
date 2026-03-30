---
phase: 05-pymayhem-refactor
plan: "02"
subsystem: driver
tags: [hackrf_driver, redis, tx_controller, redis_bridge, decoupling, packaging, tdd]

# Dependency graph
requires:
  - phase: 05-pymayhem-refactor
    plan: "01"
    provides: pymayhem standalone package installed via .pth
provides:
  - hackrf_driver package installable (pip install -e ./hackrf_driver via .pth workaround)
  - TXController decoupled from rclpy — takes primitive callables and bools
  - RedisBridge adapted for hackrf_driver with D-14 Pub/Sub notification
  - config.py with PARAM_RANGES, CHUNK_IQ_PAIRS, load_config()
  - 36 unit tests passing (12 redis_bridge + 24 tx_controller)
affects:
  - 05-03 (HackRFDriver main loop uses these decoupled classes)
  - hackrf_ros (original tx_controller and redis_bridge remain unchanged for now)

# Tech tracking
tech-stack:
  added:
    - hackrf_driver package (new sibling package with setup.cfg for legacy pip)
    - D-14: redis.publish('hackrf:iq:notify', entry_id) after each xadd
  patterns:
    - Callable injection: hackrf_getter, stop_rx_fn, start_rx_fn passed at construction
    - Bool injection: freq_filter_enabled, skip_antenna_check replace node.get_parameter()
    - .pth file workaround: pip 22.0.2 cannot do editable installs via pyproject.toml alone

key-files:
  created:
    - hackrf_driver/pyproject.toml
    - hackrf_driver/setup.cfg
    - hackrf_driver/hackrf_driver/__init__.py
    - hackrf_driver/hackrf_driver/config.py
    - hackrf_driver/hackrf_driver/redis_bridge.py
    - hackrf_driver/hackrf_driver/tx_controller.py
    - hackrf_driver/tests/__init__.py
    - hackrf_driver/tests/conftest.py
    - hackrf_driver/tests/test_redis_bridge.py
    - hackrf_driver/tests/test_tx_controller.py
  modified: []

key-decisions:
  - "TXController takes hackrf_getter: Callable, device_lock: RLock, stop_rx_fn, start_rx_fn plus freq_filter_enabled/skip_antenna_check bools — zero node reference"
  - "_freq_filter_active() reads self._freq_filter_enabled bool directly (not node.get_parameter)"
  - "RedisBridge._handle_start_tx reads IQ from driver._redis directly (not node._redis_bridge._redis)"
  - "D-14 implemented: entry_id = self._redis.xadd(...) then self._redis.publish('hackrf:iq:notify', entry_id)"
  - "setup.cfg added alongside pyproject.toml for legacy pip 22.0.2; .pth file at user site-packages"

patterns-established:
  - "Callable injection for hardware: hackrf_getter=lambda: self._hackrf replaces self._node._hackrf"
  - "Bool injection for config: freq_filter_enabled=True replaces get_parameter().bool_value"
  - "D-14 Pub/Sub pattern: XADD returns entry_id -> publish(NOTIFY_KEY, entry_id)"

requirements-completed:
  - REF-04
  - REF-07

# Metrics
duration: 7min
completed: "2026-03-30"
---

# Phase 05 Plan 02: hackrf_driver Package — RedisBridge + TXController Summary

**hackrf_driver package scaffold with TXController decoupled from rclpy (callable injection), RedisBridge adapted for standalone driver with D-14 Pub/Sub notification, config.py with PARAM_RANGES/load_config, and 36 passing unit tests — zero rclpy imports**

## Performance

- **Duration:** 7 min
- **Started:** 2026-03-30T09:14:40Z
- **Completed:** 2026-03-30T09:21:12Z
- **Tasks:** 2
- **Files modified:** 10 created

## Accomplishments

- hackrf_driver package scaffold with pyproject.toml + setup.cfg for legacy pip compatibility
- config.py with CHUNK_IQ_PAIRS=2048, PARAM_RANGES dict, DEFAULT_CONFIG, load_config() YAML loader
- TXController decoupled from rclpy: __init__ takes hackrf_getter Callable, device_lock RLock, stop_rx_fn, start_rx_fn, freq_filter_enabled bool, skip_antenna_check bool — no node reference anywhere
- RedisBridge adapted: import updated to hackrf_driver.tx_controller, _handle_start_tx uses driver._redis directly
- D-14 implemented: _xadd_iq() captures xadd return value and calls publish('hackrf:iq:notify', entry_id)
- 36 unit tests pass: 12 redis_bridge + 24 tx_controller (TDD: RED then GREEN)

## Task Commits

Each task was committed atomically:

1. **Task 1: Create hackrf_driver scaffold with config.py, pyproject.toml** - `d7f70e2` (feat)
2. **Task 2: Move and decouple RedisBridge+TXController; migrate 36 tests** - `c50ec38` (feat)

## Files Created/Modified

- `hackrf_driver/pyproject.toml` - PEP 517 metadata, name=hackrf_driver, pymayhem/redis/PyYAML/numpy deps
- `hackrf_driver/setup.cfg` - Legacy editable install compatibility (pip 22.0.2)
- `hackrf_driver/hackrf_driver/__init__.py` - Empty package marker
- `hackrf_driver/hackrf_driver/config.py` - PARAM_RANGES, CHUNK_IQ_PAIRS, DEFAULT_CONFIG, load_config()
- `hackrf_driver/hackrf_driver/redis_bridge.py` - RedisBridge adapted for hackrf_driver with D-14
- `hackrf_driver/hackrf_driver/tx_controller.py` - TXController decoupled (no rclpy, callable injection)
- `hackrf_driver/tests/__init__.py` - Empty test package marker
- `hackrf_driver/tests/conftest.py` - Redis mock fixture with hackrf_driver.redis_bridge patch target
- `hackrf_driver/tests/test_redis_bridge.py` - 12 migrated tests
- `hackrf_driver/tests/test_tx_controller.py` - 24 migrated tests with new _make_ctrl() helper

## Decisions Made

- TXController __init__ signature replaces `node` arg with: hackrf_getter: Callable, device_lock: threading.RLock, stop_rx_fn: Callable, start_rx_fn: Callable, freq_filter_enabled: bool=True, skip_antenna_check: bool=False
- `_freq_filter_active()` uses `self._freq_filter_enabled` bool directly (no node.get_parameter() call)
- `open()` uses `self._skip_antenna_check` bool directly (no node.get_parameter() call)
- `stop_tx()` calls `self._hackrf_getter()` at call time (not cached) to get current device
- RedisBridge._handle_start_tx: `driver._redis.get(iq_data_key)` — driver exposes ._redis directly
- D-14: `entry_id = self._redis.xadd(...)` then `self._redis.publish(self.NOTIFY_KEY, entry_id)`
- setup.cfg + .pth file workaround: same pattern as Plan 01 (pip 22.0.2 cannot editable install via pyproject.toml)

## Deviations from Plan

### Auto-fixed Issues

None — plan executed exactly as specified. The test for `stop_tx_stops_hardware_and_resumes_rx` required setting `ctrl._is_transmitting = True` before the call (matching original test pattern), which worked cleanly with the new callable injection approach.

## Known Stubs

None — all imports and module-level functionality wired. The hackrf_driver CLI entry point (`hackrf_driver.cli:main`) referenced in pyproject.toml is not yet implemented but that is Plan 03's responsibility (the driver main loop). No stub affects the plan's goal of decoupled TXController and RedisBridge with passing tests.

## Issues Encountered

- System pip 22.0.2 cannot do editable installs from pyproject.toml; same workaround as Plan 01: created `.pth` file at `/home/user/.local/lib/python3.10/site-packages/hackrf_driver-dev.pth` pointing to `/home/user/dev_ws/hackrf_ros/hackrf_driver`
- pymayhem .pth file pointed to other worktree (`agent-afd7eafd`); corrected to `/home/user/dev_ws/hackrf_ros/pymayhem`

## Next Phase Readiness

- `hackrf_driver` package is importable: `from hackrf_driver.tx_controller import TXController`
- TXController new interface: `TXController(redis, logger, hackrf_getter, device_lock, stop_rx_fn, start_rx_fn)`
- RedisBridge ready for driver integration with D-14 Pub/Sub notification
- 36 tests pass; zero rclpy imports in hackrf_driver/
- Plan 03 (HackRFDriver main loop) can now build on these decoupled classes

---
*Phase: 05-pymayhem-refactor*
*Completed: 2026-03-30*
