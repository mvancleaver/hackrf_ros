---
phase: 03-redis-bridge
plan: "01"
subsystem: redis-bridge
tags: [redis, streaming, daemon-thread, tdd, iq-encoding]
dependency_graph:
  requires: []
  provides: [RedisBridge class, redis_bridge.py, test_redis_bridge.py]
  affects: [hackrf_ros/hackrf_node.py (Phase 03-02 wiring)]
tech_stack:
  added: [redis>=7.4.0, hiredis>=3.3.1]
  patterns: [MayhemSerial helper class pattern, daemon thread with stop_event, module-level dispatch table]
key_files:
  created:
    - hackrf_ros/redis_bridge.py
    - test/test_redis_bridge.py
  modified:
    - setup.py
    - package.xml
decisions:
  - "_COMMAND_HANDLERS dict at module level (not class attribute) for clean dispatch without self reference"
  - "last_cmd_id captured as return value from _poll_commands in _bridge_loop (Pattern 3 — no lost commands)"
  - "_state_lock retained in publish_state() for safety against concurrent calls even though redis-py ConnectionPool is thread-safe"
  - "pyserial>=3.5 preserved in setup.py install_requires alongside new redis/hiredis entries"
metrics:
  duration: "3min"
  completed_date: "2026-03-29"
  tasks_completed: 2
  files_changed: 4
---

# Phase 3 Plan 1: RedisBridge Class Summary

**One-liner:** RedisBridge helper class with open/close/publish_state lifecycle, float32 IQ XADD to hackrf:iq:stream, XREAD command dispatch, and 12 unit tests — all Redis mocked, no live Redis required.

## What Was Built

- `hackrf_ros/redis_bridge.py` — `RedisBridge` class following the `MayhemSerial` pattern exactly (D-10)
- `test/test_redis_bridge.py` — 12 unit tests covering all public methods and internal helpers
- `setup.py` — `redis>=7.4.0` and `hiredis>=3.3.1` added to `install_requires`
- `package.xml` — `python3-redis` `exec_depend` added

## Key Implementation Details

**Class constants (RED-05 / D-12):**
- `STREAM_KEY = 'hackrf:iq:stream'`
- `STATE_KEY  = 'hackrf:state'`
- `CMD_KEY    = 'hackrf:cmd'`

**Thread model (D-11):** Single daemon thread named `redis_bridge` owns all Redis I/O. `publish_state()` borrows a connection from the ConnectionPool under `_state_lock`.

**IQ encoding (D-03):** Raw int8 bytes from `_iq_queue` converted to float32 interleaved `[I/128.0, Q/128.0, ...]` before XADD with `maxlen=10000, approximate=True` (D-04).

**Command dispatch:** Module-level `_COMMAND_HANDLERS` dict maps 9 action names (D-06) to lambda handlers calling HackRFNode methods. Unknown actions log a warning; dispatch exceptions log an error without crashing the loop.

**Critical correctness:** `_bridge_loop` captures the return value of `_poll_commands(last_cmd_id)` — RESEARCH.md Pattern 3. Without this, `last_cmd_id` never advances and commands are replayed on every iteration.

**Anti-patterns avoided:**
- `decode_responses=False` — binary IQ data safety
- `block=200` (not `block=0`) — `_stop_event` checked every 200ms
- Initial cursor `b'$'` (not `'0'`) — no historical command replay
- Single Redis connection created in `open()`, reused throughout thread lifetime

## Decisions Made

1. `_COMMAND_HANDLERS` dict placed at module level (not class attribute): lambdas reference `node` via parameter, not `self`, keeping the dispatch table clean and testable.

2. `last_cmd_id` captured as return value from `_poll_commands` per RESEARCH.md Pattern 3 — prevents command cursor from stagnating and replaying the same entry.

3. `_state_lock` retained in `publish_state()` as defensive coding against concurrent callers, even though redis-py's `ConnectionPool` is inherently thread-safe.

4. `pyserial>=3.5` preserved in `setup.py` alongside new redis/hiredis entries (the worktree's `setup.py` had been reset to bare `setuptools`; restoring both pyserial and redis).

## Test Coverage

All 12 tests pass with mocked Redis (no live Redis required):

| # | Test | Covers |
|---|------|--------|
| 1 | `test_open_returns_false_on_connection_error` | `open()` graceful degradation |
| 2 | `test_open_returns_true_and_starts_thread_when_ping_succeeds` | `open()` success path |
| 3 | `test_drain_iq_queue_calls_xadd_with_float32_bytes` | `_drain_iq_queue` + `_xadd_iq` |
| 4 | `test_float32_encoding_is_correct` | int8 (64, -128) → float32 (0.5, -1.0) |
| 5 | `test_poll_commands_parses_valid_json_and_calls_dispatch` | `_poll_commands` valid JSON path |
| 6 | `test_poll_commands_logs_warning_on_malformed_json` | `_poll_commands` malformed JSON |
| 7 | `test_dispatch_command_routes_setfreq_to_node` | `_dispatch_command` known action |
| 8 | `test_dispatch_command_logs_warning_for_unknown_action` | `_dispatch_command` unknown action |
| 9 | `test_publish_state_calls_hset_when_connected` | `publish_state()` connected path |
| 10 | `test_publish_state_is_noop_when_redis_is_none` | `publish_state()` disconnected |
| 11 | `test_close_sets_stop_event` | `close()` |
| 12 | `test_needs_reconnect_true_when_stop_event_set_and_thread_not_none` | `needs_reconnect` property |

## Commits

- `4800a5d` — `feat(03-01): implement RedisBridge class with full TDD coverage`
- `f1027eb` — `chore(03-01): add redis and hiredis dependencies to setup.py and package.xml`

## Deviations from Plan

None — plan executed exactly as written.

The worktree's `setup.py` had a slightly different starting state (no `pyserial>=3.5` in `install_requires`) compared to the main repo version shown in the plan. This was auto-corrected by including both `pyserial>=3.5` and the new redis/hiredis entries to match the intended state.

## Known Stubs

None. `RedisBridge` is a complete, independently testable class. All methods have real implementations. The node reference (`_node`) is only called during command dispatch — methods like `_set_center_frequency` will be provided by `HackRFNode` when wired in Phase 03-02.

## Self-Check: PASSED

Files exist:
- `hackrf_ros/redis_bridge.py` — FOUND
- `test/test_redis_bridge.py` — FOUND

Commits exist:
- `4800a5d` — FOUND
- `f1027eb` — FOUND

All 12 tests pass: CONFIRMED
