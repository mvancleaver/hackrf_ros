---
phase: 03-reliability
plan: 03
subsystem: hackrf_lifecycle_node
tags: [usb-disconnect, fault-detection, reliability, diagnostics, tdd]
dependency_graph:
  requires: [03-01, 03-02]
  provides: [USB fault detection, WARN/ERROR diagnostics escalation, crash-safe rx_callback, REL-03]
  affects: [hackrf_ros/hackrf_lifecycle_node.py]
tech_stack:
  added: []
  patterns: [try/except guard on C-thread callback, CPython GIL atomic bool write, WARN->ERROR escalation with monotonic timer]
key_files:
  created:
    - test/test_usb_disconnect.py
  modified:
    - hackrf_ros/hackrf_lifecycle_node.py
decisions:
  - DISCONNECT_ERROR_TIMEOUT=2.0 provides a WARN window before escalating to ERROR — gives operator time to identify transient vs permanent disconnect
  - _handle_usb_fault sets _is_streaming=False so stall detection in diagnostics also fires (belt and suspenders)
  - _rx_callback wraps entire body in try/except — safer than guarding individual pyhackrf2 calls since C thread may raise before or during callback
  - _usb_fault bool write is atomic under CPython GIL — no lock needed for cross-thread flag
  - After _usb_fault=True the fault is sticky — node must be lifecycle-restarted via `ros2 lifecycle set /hackrf_node cleanup` then `configure` to clear (T-03-03-02: accepted by design)
metrics:
  duration_seconds: 480
  completed_date: "2026-04-13T05:21:35Z"
  tasks_completed: 2
  files_modified: 2
---

# Phase 3 Plan 03: USB Disconnect Detection Summary

**One-liner:** Crash-safe `_rx_callback` with full try/except wrap, `_handle_usb_fault()` shared fault recorder, and WARN-to-ERROR escalation in diagnostics after `DISCONNECT_ERROR_TIMEOUT` seconds.

## What Was Built

USB disconnection detection for the HackRF lifecycle driver. pyhackrf2 raises `RuntimeError` or `OSError` when the USB device is unplugged mid-stream. Previously these exceptions could propagate uncaught from `_rx_callback` (called from the pyhackrf2 C thread) or from `stop_rx`/`start_rx` in the `_param_worker` thread.

After this plan:
- `_rx_callback` wraps its entire body in `try/except (RuntimeError, OSError)` — the C thread always sees a clean `False` return
- `_param_worker` stop_rx and start_rx (attempt==2) except blocks call `_handle_usb_fault`
- `_handle_usb_fault(source)` sets `_usb_fault=True`, records `_usb_fault_time`, clears `_is_streaming`, and logs an error
- `_diagnostics_callback` checks `_usb_fault` first: if True and age < 2.0 s → WARN; if age >= 2.0 s → ERROR; always surfaces `usb_fault` as a stat key

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Write USB disconnect unit tests (RED) | a8ea6c3 | test/test_usb_disconnect.py |
| 2 | Implement USB disconnect detection (GREEN) | a75bc6a | hackrf_ros/hackrf_lifecycle_node.py |

## Implementation Details

### New Constants (module-level)

```python
DISCONNECT_ERROR_TIMEOUT = 2.0   # seconds before USB fault escalates WARN -> ERROR
```

### New Instance Variables (`__init__`)

- `_usb_fault: bool = False` — set True on first USB exception
- `_usb_fault_time: float = 0.0` — monotonic timestamp of fault detection

### New Methods

**`_handle_usb_fault(source: str)`** — shared fault recorder:
- Sets `_usb_fault = True`, `_usb_fault_time = time.monotonic()`, `_is_streaming = False`
- Logs `error()` with the caller's source label

### Modified Methods

**`_rx_callback()`** — entire body wrapped in `try/except (RuntimeError, OSError)`:
- On exception: calls `_handle_usb_fault('rx_callback')`, returns `False`
- No logic inside the try block was altered

**`_param_worker()`** — two fault hook additions:
- `stop_rx` except block: `self._handle_usb_fault('param_worker:stop_rx')`
- `start_rx` attempt==2 except block: `self._handle_usb_fault('param_worker:start_rx')`

**`_diagnostics_callback()`** — USB-aware path added at top of method:
- Early-return path when `_usb_fault` is True (WARN or ERROR based on fault age)
- Normal path unchanged; normal path also adds `stat.add('usb_fault', 'false')`

## Verification Results

```
test/test_usb_disconnect.py: 12/12 PASSED (GREEN)
test/test_async_param.py: 10/10 PASSED (no regressions)
test/test_agc.py: 12/12 PASSED (no regressions)
test/test_driver_upgrade.py: 13/13 PASSED (no regressions)
Total: 47/47 PASSED
AST syntax check: OK
_handle_usb_fault grep: 4 occurrences (definition + 3 call sites)
DISCONNECT_ERROR_TIMEOUT grep: 2 occurrences (definition + diagnostics use)
_usb_fault grep: 10+ occurrences
_rx_callback try: block confirmed
```

## Phase 3 Success Criteria Verified

- REL-01: `_agc_tick` adjusts gains on clip events, `/diagnostics` reports `agc_last_action` — CONFIRMED
- REL-02: `_param_callback` returns immediately; worker thread handles stop/start — CONFIRMED
- REL-03: USB fault sets `_usb_fault`, diagnostics transitions WARN -> ERROR within 2.0 s — CONFIRMED

## Deviations from Plan

### Pre-existing flake8 Issues (Out of Scope)

The pre-existing E128 indentation violations in `_declare_parameters` and E741 ambiguous variable `I` in `_fft_frame` remain from prior plans. No new violations introduced by this plan.

## Threat Mitigations Applied

| Threat ID | Mitigation |
|-----------|-----------|
| T-03-03-01 | Entire `_rx_callback` body wrapped in `try/except (RuntimeError, OSError)` — C thread always sees clean `False` return |
| T-03-03-04 | `DISCONNECT_ERROR_TIMEOUT=2.0` s WARN window before ERROR escalation — transient errors don't immediately alarm |

## Known Stubs

None — USB fault detection is fully wired to diagnostics.

## Threat Flags

None — no new trust boundaries introduced. USB exception handling is at an existing boundary (pyhackrf2 C thread → Python callback).

## Self-Check: PASSED

- test/test_usb_disconnect.py: FOUND
- hackrf_ros/hackrf_lifecycle_node.py: modified and verified
- Commit a8ea6c3: FOUND (test RED phase)
- Commit a75bc6a: FOUND (feat GREEN phase)
- 47/47 tests passing: VERIFIED
