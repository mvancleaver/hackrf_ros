---
phase: 06-foundation-hardening
plan: "01"
subsystem: exception-hierarchy
tags: [exceptions, error-handling, pymayhem, hackrf_driver, input-validation]
dependency_graph:
  requires: []
  provides: [pymayhem-exceptions, hackrf-driver-exceptions, raise-on-error-domains]
  affects: [pymayhem, hackrf_driver, redis_bridge, tx_controller]
tech_stack:
  added: []
  patterns: [typed-exception-hierarchy, raise-on-error, input-validation-guard]
key_files:
  created:
    - pymayhem/pymayhem/exceptions.py
    - hackrf_driver/hackrf_driver/exceptions.py
  modified:
    - hackrf_driver/hackrf_driver/tx_controller.py
    - hackrf_driver/hackrf_driver/redis_bridge.py
    - pymayhem/pymayhem/domains/radio.py
    - pymayhem/pymayhem/domains/system.py
decisions:
  - "TX exceptions reparented under HackRFError via new exceptions.py; re-exported from tx_controller for backward compatibility"
  - "pymayhem domain bool returns converted to raise MayhemCommandError; appstart_with_reconnect keeps bool return (reconnect timeout, not command error)"
metrics:
  duration: 3min
  completed_date: "2026-03-30"
  tasks_completed: 2
  files_modified: 6
---

# Phase 06 Plan 01: Exception Hierarchies and Domain Raise-on-Error Summary

**One-liner:** Typed exception hierarchies for pymayhem (MayhemError tree) and hackrf_driver (HackRFError tree with TX exceptions reparented), plus bool-to-raise conversion for all pymayhem domain command methods.

## What Was Built

### Task 1: Create exception modules and reparent TX exceptions

Created `pymayhem/pymayhem/exceptions.py` with a four-class hierarchy:
- `MayhemError` (base)
- `MayhemCommandError` — firmware returned error response
- `MayhemParseError` — unparseable firmware response
- `MayhemTimeoutError` — serial command timed out

Created `hackrf_driver/hackrf_driver/exceptions.py` with a seven-class hierarchy:
- `HackRFError` (base)
- `HackRFConfigError` — parameter out of hardware range
- `HackRFDeviceError` — USB/connection-level failure
- `TXBlockedError`, `TXFreqBlockedError`, `TXHardBlockedError`, `TXNotAuthorizedError` — all now under `HackRFError`

Removed the four inline exception class definitions from `tx_controller.py` and replaced with imports from `hackrf_driver.exceptions` (re-exported via `# noqa: F401` for backward compatibility). Updated `redis_bridge.py` to import TX exceptions from `hackrf_driver.exceptions` instead of `tx_controller`.

### Task 2: Convert pymayhem domain methods to raise-on-error with input validation

Updated `pymayhem/pymayhem/domains/radio.py`:
- `setfreq(freq_hz)`: now `-> None`, raises `ValueError` if `freq_hz` is not int in `[1e6, 6e9]`, raises `MayhemCommandError` on firmware error response

Updated `pymayhem/pymayhem/domains/system.py`:
- `appstart(short_name)`: now `-> None`, validates non-empty string, raises `MayhemCommandError` on error response; `_active_app` assignment only happens on success
- `appstart_with_reconnect(short_name)`: keeps `-> bool` (returns True/False for reconnect timeout, not command error); validates non-empty string arg
- `reboot()`: now `-> None`, raises `MayhemCommandError` on error response
- `rtcset(datetime_str)`: now `-> None`, validates non-empty string, raises `MayhemCommandError` on error response

## Test Results

All 70 existing tests pass. No tests broken by changes (MayhemSerial's own appstart/setfreq still return bool — separate from domain objects).

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None.

## Self-Check: PASSED

Files verified:
- pymayhem/pymayhem/exceptions.py: EXISTS
- hackrf_driver/hackrf_driver/exceptions.py: EXISTS
- pymayhem/pymayhem/domains/radio.py: setfreq raises MayhemCommandError and ValueError
- pymayhem/pymayhem/domains/system.py: appstart, reboot, rtcset raise MayhemCommandError

Commits verified:
- 61b3ca7: feat(06-01): create exception modules and reparent TX exceptions
- 2f4eaa8: feat(06-01): convert pymayhem domain methods to raise-on-error with input validation
