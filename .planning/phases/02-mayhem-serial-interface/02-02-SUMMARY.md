---
phase: 02-mayhem-serial-interface
plan: 02
subsystem: serial
tags: [mayhem, serial, pyserial, threading, unit-tests]
dependency_graph:
  requires: []
  provides: [MayhemSerial]
  affects: [hackrf_node.py]
tech_stack:
  added: [pyserial>=3.5]
  patterns: [daemon-reader-thread, queue-based-response, retry-on-timeout]
key_files:
  created:
    - hackrf_ros/mayhem_serial.py
    - test/test_mayhem_serial.py
  modified: []
decisions:
  - "pyserial 3.5 pattern: Serial() without port arg, set .port, call .open() — avoids immediate open in constructor"
  - "_attempt_send() helper separates single write+collect from retry logic in _send_command()"
  - "_serial_lock acquired in _send_command(), not in _attempt_send() — caller holds lock across retry"
  - "D-12 retry: TimeoutError on first attempt logged as warning, command retried once before propagating"
metrics:
  duration: 2min
  completed: "2026-03-30"
  tasks_completed: 2
  files_created: 2
---

# Phase 02 Plan 02: MayhemSerial Helper Class Summary

**One-liner:** Standalone MayhemSerial class with daemon reader thread, CRLF command protocol, ch> prompt detection, and single-retry timeout handling (D-12).

## What Was Built

`hackrf_ros/mayhem_serial.py` — a self-contained serial interface to Mayhem firmware over `/dev/ttyACM1`. The class uses pyserial 3.5, a daemon reader thread feeding a `queue.Queue`, and a `threading.Lock` for command serialization. HackRFNode (Plan 03) instantiates this class and never touches pyserial directly.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Implement MayhemSerial class | 46828d8 | hackrf_ros/mayhem_serial.py |
| 2 | Unit tests for MayhemSerial command parsing | 72e8807 | test/test_mayhem_serial.py |

## Key Decisions

1. **pyserial constructor pattern**: `serial.Serial()` with no port arg, then `.port = ...`, `.open()` — required by pyserial 3.5 to defer open until `open()` is called.
2. **`_attempt_send()` helper**: Separates the raw write+collect cycle from retry logic, so `_send_command()` calls it twice cleanly (D-12 retry design).
3. **Lock placement**: `_serial_lock` acquired in `_send_command()`, not in `_attempt_send()`. The caller holds the lock across both attempts to prevent interleaving.
4. **ch> prompt detection**: `line.lstrip().startswith('ch>')` handles any leading whitespace in ChibiOS prompt.
5. **Pitfall 4 mitigation**: `time.sleep(0.05)` after `thread.start()` in `open()` ensures reader thread is ready before first command.

## Deviations from Plan

### Auto-fixed Issues

None — plan executed exactly as written.

### Additional Tests

Added `test_send_command_raises_on_double_timeout` (9th test) beyond the plan's 7-minimum to verify that when _both_ attempts time out, the TimeoutError propagates and the warning was still logged once. This directly tests the double-failure path of D-12.

## Test Results

```
9 passed in 0.04s
```

All 9 tests pass. Tests mock pyserial and inject responses via `_response_queue` — no serial hardware required.

## Known Stubs

None — all methods are fully implemented.

## Verification

- `python3 -c "from hackrf_ros.mayhem_serial import MayhemSerial; print('ok')"` prints "ok"
- `python3 -m pytest test/test_mayhem_serial.py -v` — 9 passed
- `grep -c "def " hackrf_ros/mayhem_serial.py` returns 11 (>= 9 required)

## Self-Check: PASSED

Files exist:
- hackrf_ros/mayhem_serial.py: FOUND
- test/test_mayhem_serial.py: FOUND

Commits exist:
- 46828d8: FOUND
- 72e8807: FOUND
