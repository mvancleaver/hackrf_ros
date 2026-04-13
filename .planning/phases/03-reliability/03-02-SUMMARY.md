---
phase: 03-reliability
plan: 02
subsystem: hackrf_lifecycle_node
tags: [async-param, non-blocking, threading, reliability, tdd]
dependency_graph:
  requires: [03-01]
  provides: [non-blocking param callback, _param_worker daemon thread, REL-02]
  affects: [hackrf_ros/hackrf_lifecycle_node.py]
tech_stack:
  added: []
  patterns: [queue.Queue for executor/thread handoff, daemon thread lifecycle in on_activate/on_deactivate, sentinel-based thread shutdown]
key_files:
  created:
    - test/test_async_param.py
  modified:
    - hackrf_ros/hackrf_lifecycle_node.py
decisions:
  - _param_queue bounded at maxsize=4 — param changes beyond that emit a warning and are dropped (T-03-02-01 mitigation)
  - task_done() called in _param_worker for queue.join() compatibility, even though join() is not used here
  - on_activate flushes stale queue items before starting fresh worker to avoid applying old param changes
  - Worker thread joined with timeout=2.0s on deactivate; if it times out, thread exits naturally on next sentinel (T-03-02-03 mitigation)
  - _param_callback returns successful=True before hardware confirms — accepted per ROS2 protocol limitation (T-03-02-04 accepted)
metrics:
  duration_seconds: 180
  completed_date: "2026-04-13T06:00:00Z"
  tasks_completed: 2
  files_modified: 2
---

# Phase 3 Plan 02: Async Param Summary

**One-liner:** Non-blocking `_param_callback` using a bounded `queue.Queue` and `_param_worker` daemon thread — executor thread returns in microseconds, stop/start retry logic runs off-thread.

## What Was Built

Offloaded the blocking `stop_rx` / `start_rx` sequence from the ROS2 executor thread to a background daemon thread (`_param_worker`). Previously, a `sample_rate` parameter change would hold the executor thread for up to 500 ms (three retry sleeps), stalling all timer callbacks including PSD publishing and the diagnostics updater. After this refactor, `_param_callback` validates parameter ranges, enqueues `(pending_dict, needs_restart)` onto a bounded `queue.Queue(maxsize=4)`, and returns `SetParametersResult(successful=True)` immediately. The worker thread drains the queue and applies hardware changes under `_device_lock`.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Write async param unit tests (RED) | 0a78aab | test/test_async_param.py |
| 2 | Refactor _param_callback to non-blocking (GREEN) | 48da38e | hackrf_ros/hackrf_lifecycle_node.py |

## Implementation Details

### New Instance Variables (`__init__`)

- `_param_queue: queue.Queue = queue.Queue(maxsize=4)` — bounded work queue
- `_param_worker_thread: threading.Thread | None = None` — reference to daemon thread

### New Methods

**`_param_worker()`** — Background daemon thread method:
- `queue.get(timeout=1.0)` — blocks waiting for work
- `None` sentinel causes thread to exit cleanly
- Holds `_device_lock` for all hardware access (stop_rx, _apply_pending, start_rx)
- Three-attempt retry with 0.15/0.3/0.5 s sleeps (preserved from old callback)
- Resets `_psd_accum` and `_accum_count` after each param change

### Modified Methods

**`_param_callback()`** — Reduced to:
1. Validate parameter ranges (return False immediately if out-of-range)
2. `_param_queue.put_nowait((pending, needs_restart))` with Full warning
3. Return `SetParametersResult(successful=True)` — no hardware access

**`on_activate()`** — Added:
- Flush stale queue items before starting
- `threading.Thread(target=self._param_worker, daemon=True)` started

**`on_deactivate()`** — Added:
- `_param_queue.put_nowait(None)` sentinel
- `_param_worker_thread.join(timeout=2.0)`

## Verification Results

```
test/test_async_param.py: 10/10 PASSED (GREEN)
test/test_agc.py: 12/12 PASSED (no regressions)
test/test_driver_upgrade.py: 13/13 PASSED (no regressions)
Total: 35/35 PASSED
AST syntax check: OK
_param_callback body: no time.sleep, stop_rx, or start_rx
```

## Deviations from Plan

### Pre-existing flake8 Issues (Out of Scope)

The same pre-existing E128 indentation violations in `_declare_parameters` (lines ~278-306) and E741 ambiguous variable `I` in `_fft_frame` (line ~513) remain. These were documented in 03-01-SUMMARY.md and are unrelated to this plan's changes. No new flake8 violations were introduced.

## Threat Mitigations Applied

| Threat ID | Mitigation |
|-----------|-----------|
| T-03-02-01 | Queue(maxsize=4) + put_nowait with warning log — stale param changes dropped, not queued forever |
| T-03-02-03 | on_deactivate sends sentinel + join(timeout=2.0); if timeout, thread exits on next sentinel receipt |

## Known Stubs

None — non-blocking param handling is fully wired with hardware apply in worker thread.

## Self-Check: PASSED

- test/test_async_param.py: FOUND
- hackrf_ros/hackrf_lifecycle_node.py: modified and verified
- Commit 0a78aab: test RED phase — FOUND
- Commit 48da38e: feat GREEN phase — FOUND
- 35/35 tests passing: VERIFIED
