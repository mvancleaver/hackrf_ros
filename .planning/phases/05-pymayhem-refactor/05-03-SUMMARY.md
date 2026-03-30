---
phase: 05-pymayhem-refactor
plan: "03"
subsystem: driver
tags: [hackrf_driver, driver, cli, threading, tdd, no-rclpy]

# Dependency graph
requires:
  - phase: 05-pymayhem-refactor
    plan: "02"
    provides: TXController, RedisBridge, config.py decoupled from rclpy
provides:
  - HackRFDriver class: standalone main loop, pyhackrf2, MayhemClient, RedisBridge, TXController
  - hackrf_driver.cli:main — argparse CLI with --freq, --lna-gain, --vga-gain flags
  - python -m hackrf_driver entry point
  - 16 driver unit tests (no hardware required)
affects:
  - 05-04 (ROS2 bridge will consume hackrf_driver as dependency)
  - 05-05 (integration tests against running HackRFDriver)

# Tech tracking
tech-stack:
  added:
    - HackRFDriver class using threading.Event main loop gate
    - threading.Timer for reconnect backoff (replaces ROS2 create_timer)
    - signal.signal(SIGINT/SIGTERM) handlers
    - argparse CLI (D-11 dual entry point)
  patterns:
    - TDD: test_driver.py written first (RED), then driver.py to make them pass (GREEN)
    - pyhackrf2 import guarded with try/except ImportError for test mockability
    - MayhemClient import guarded with try/except ImportError for environments without pymayhem

key-files:
  created:
    - hackrf_driver/hackrf_driver/driver.py
    - hackrf_driver/hackrf_driver/cli.py
    - hackrf_driver/hackrf_driver/__main__.py
    - hackrf_driver/tests/test_driver.py
  modified: []

key-decisions:
  - "HackRFDriver.__init__ order: _mayhem, _redis_bridge, _tx_controller, signal, _try_connect, _iq_thread, _mayhem.open()"
  - "_safe_json_apps() guards json.dumps against mock/non-list _known_apps attribute"
  - "_set_amp_enabled() bypasses PARAM_RANGES (amp_enabled is bool — no range) and calls _configure_device directly"
  - "IQ publish loop drains _ros_queue only to prevent unbounded growth; Redis XADD done by RedisBridge consuming _redis_queue"
  - "CLI uses lazy imports (inside main()) to allow --help without hardware/redis deps"

# Metrics
duration: 5min
completed: "2026-03-30"
---

# Phase 05 Plan 03: HackRFDriver Main Loop Summary

**HackRFDriver standalone class replacing HackRFNode with zero rclpy imports, threading.Event main loop, threading.Timer reconnect, TXController/RedisBridge wiring via primitive callables, argparse CLI, and 16 passing unit tests**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-30T09:25:57Z
- **Completed:** 2026-03-30T09:31:57Z
- **Tasks:** 2
- **Files modified:** 4 created

## Accomplishments

- driver.py: HackRFDriver class with zero rclpy imports; threading.Event stop gate, threading.Timer reconnect backoff, SIGINT/SIGTERM signal handlers, run()/shutdown() lifecycle
- TXController wired with primitive callables: hackrf_getter=lambda, device_lock=RLock, stop_rx_fn, start_rx_fn, freq_filter_enabled bool, skip_antenna_check bool
- RedisBridge wired with redis_queue and self reference; driver exposes _redis directly for _handle_start_tx
- Same method surface as HackRFNode for RedisBridge _COMMAND_HANDLERS dispatch: _set_center_frequency, _set_sample_rate, _set_lna_gain, _set_vga_gain, _set_amp_enabled, _start_rx_if_stopped, _stop_rx_if_running, _build_state_dict, _mayhem
- cli.py: argparse CLI with --freq, --lna-gain, --vga-gain, --sample-rate, --serial-port, --redis-host, --redis-port, --no-freq-filter, --skip-antenna-check
- __main__.py: `python -m hackrf_driver` entry point
- 16 driver unit tests pass (no hardware required): TDD RED → GREEN

## Task Commits

Each task was committed atomically:

1. **Task 1: Build HackRFDriver class with wiring and shutdown** - `31771f9` (feat)
2. **Task 2: Build CLI entry point and __main__.py** - `e79f5cc` (feat)

## Files Created/Modified

- `hackrf_driver/hackrf_driver/driver.py` - HackRFDriver class (370 lines): standalone pyhackrf2 driver, threading main loop, RedisBridge+TXController wiring, SIGINT/SIGTERM handlers
- `hackrf_driver/hackrf_driver/cli.py` - argparse CLI with all D-11 flags (139 lines)
- `hackrf_driver/hackrf_driver/__main__.py` - `python -m hackrf_driver` entry point (3 lines)
- `hackrf_driver/tests/test_driver.py` - 16 unit tests covering init, _update_param, _build_state_dict, shutdown, and _set_* methods (211 lines)

## Decisions Made

- HackRFDriver.__init__ initializes _mayhem before _redis_bridge so _build_state_dict() can safely access self._mayhem from the start
- _safe_json_apps() helper guards json.dumps against MagicMock or non-list _known_apps (required for test reliability)
- _set_amp_enabled() operates directly on _last_params (amp_enabled has no PARAM_RANGES entry — bool, no range check needed)
- IQ publish loop (_iq_publish_loop) drains only _ros_queue; _redis_queue is consumed by RedisBridge daemon thread for XADD
- pyhackrf2 and MayhemClient imports guarded with try/except ImportError for test mockability and deployment flexibility
- CLI uses lazy imports inside main() so `--help` works without hardware/Redis available at import time

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] _safe_json_apps() added to handle non-JSON-serializable mock attributes**
- **Found during:** Task 1 GREEN phase
- **Issue:** `json.dumps(getattr(self._mayhem, '_known_apps', []))` raises TypeError when `_known_apps` returns a MagicMock from test mocking
- **Fix:** Added `_safe_json_apps()` helper that checks `isinstance(apps, list)` and catches TypeError/ValueError
- **Files modified:** hackrf_driver/hackrf_driver/driver.py
- **Commit:** 31771f9

**2. [Rule 3 - Blocking] driver.py Write had structural corruption from Edit tool**
- **Found during:** Task 1 GREEN phase first run
- **Issue:** Edit tool inserted `_safe_json_apps()` method mid-`__init__`, causing `AttributeError: 'HackRFDriver' object has no attribute '_mayhem'` at runtime
- **Fix:** Rewrote driver.py from scratch with correct indentation and method placement
- **Files modified:** hackrf_driver/hackrf_driver/driver.py
- **Commit:** 31771f9

## Known Stubs

None — driver.py is fully wired. The `_ros_queue` drains without ROS2 publishing (intentional: standalone mode has no ROS2 publisher, data goes to Redis only via RedisBridge consuming `_redis_queue`). This is documented behavior per D-12/D-13, not a stub.

## Self-Check

All files verified:
- hackrf_driver/hackrf_driver/driver.py — FOUND
- hackrf_driver/hackrf_driver/cli.py — FOUND
- hackrf_driver/hackrf_driver/__main__.py — FOUND
- hackrf_driver/tests/test_driver.py — FOUND
- .planning/phases/05-pymayhem-refactor/05-03-SUMMARY.md — FOUND

All commits verified:
- 31771f9 feat(05-03): build HackRFDriver class with wiring and unit tests — FOUND
- e79f5cc feat(05-03): build CLI entry point and __main__.py for hackrf_driver — FOUND

## Self-Check: PASSED
