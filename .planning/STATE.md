---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: Completed 05-pymayhem-refactor 05-01-PLAN.md
last_updated: "2026-03-30T09:12:53.958Z"
last_activity: 2026-03-30
progress:
  total_phases: 5
  completed_phases: 4
  total_plans: 15
  completed_plans: 11
  percent: 0
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-29)

**Core value:** Reliable, safe bidirectional SDR control with IQ data streaming to Redis and TX operations gated behind explicit authorization.
**Current focus:** Phase 05 — pymayhem-refactor

## Current Position

Phase: 05 (pymayhem-refactor) — EXECUTING
Plan: 2 of 5
Status: Ready to execute
Last activity: 2026-03-30

Progress: [░░░░░░░░░░] 0%

## Performance Metrics

**Velocity:**

- Total plans completed: 0
- Average duration: -
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**

- Last 5 plans: -
- Trend: -

*Updated after each plan completion*
| Phase 01-rx-pipeline-correctness P01 | 2min | 1 tasks | 1 files |
| Phase 01-rx-pipeline-correctness P02 | 2min | 1 tasks | 1 files |
| Phase 01-rx-pipeline-correctness P03 | 4min | 2 tasks | 4 files |
| Phase 02-mayhem-serial-interface P02 | 2min | 2 tasks | 2 files |
| Phase 02-mayhem-serial-interface P01 | 5min | 2 tasks | 4 files |
| Phase 02-mayhem-serial-interface P03 | 3min | 2 tasks | 3 files |
| Phase 03-redis-bridge P01 | 3min | 2 tasks | 4 files |
| Phase 03-redis-bridge P02 | 8min | 2 tasks | 3 files |
| Phase 04-tx-authorization P01 | 3min | 2 tasks | 2 files |
| Phase 04-tx-authorization P02 | 8min | 2 tasks | 3 files |
| Phase 04-tx-authorization P02 | 10min | 3 tasks | 2 files |
| Phase 05-pymayhem-refactor P01 | 6min | 2 tasks | 14 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- (pre-execution)
- [Phase 01-rx-pipeline-correctness]: Dual queue.Queue(maxsize=64) replace shared numpy buffer; drop-oldest overflow; bool return from _rx_callback; topic renamed to /hackrf/iq; CHUNK_IQ_PAIRS=2048 constant (Plan 01-01)
- [Phase 01-rx-pipeline-correctness]: _device_lock (RLock) serialises stop/start; _last_params dict restores params on reconnect; exponential backoff 1-30s; destroy_node cancels timer before touching device (Plan 01-02)
- [Phase 01-rx-pipeline-correctness]: PARAM_RANGES dict at module level rejects out-of-range values before device touch; amp_enabled omitted (bool, no range); per-param SetParametersResult list maintains 1:1 correspondence (Plan 01-03)
- [Phase 02-mayhem-serial-interface]: pyserial 3.5 pattern: Serial() without port arg, set .port, call .open() to defer open until open() is called
- [Phase 02-mayhem-serial-interface]: _attempt_send() helper separates raw write+collect from retry logic; _serial_lock held in _send_command() across both attempts (D-12)
- [Phase 02-mayhem-serial-interface]: hackrf_ros_interfaces as separate CMake package with ament_cmake + rosidl_default_generators for .srv compilation (standard ROS2 pattern for Python nodes needing custom services)
- [Phase 02-mayhem-serial-interface]: Serial lifecycle: open in __init__ via _try_serial_connect, close in destroy_node before pyhackrf2 shutdown
- [Phase 02-mayhem-serial-interface]: MAY-06 mode coexistence check called as last step in __init__ — provides startup empirical result for pyhackrf2 + serial coexistence
- [Phase 03-redis-bridge]: _COMMAND_HANDLERS at module level; last_cmd_id captured as return value from _poll_commands (Pattern 3); decode_responses=False for binary IQ safety; block=200ms on XREAD
- [Phase 03-redis-bridge]: RedisBridge.close() called FIRST in destroy_node before serial and pyhackrf2 (D-11 / CONTEXT.md requirement)
- [Phase 03-redis-bridge]: hasattr guard on _redis_bridge calls in _on_parameter_event and _handle_appstart for graceful degradation (D-02)
- [Phase 03-redis-bridge]: _start_time recorded once in __init__ for stable uptime_s in _build_state_dict
- [Phase 04-tx-authorization]: ALWAYS_BLOCKED_BANDS checked unconditionally — EPIRB/ADS-B cannot be bypassed; guard order: antenna->hard-block->freq-filter->auth token; Lua GETDEL fallback for Redis 6.0.16; _tx_lock is Lock (not RLock); txvga_gain default=0
- [Phase 04-tx-authorization]: TXController wired into HackRFNode after RedisBridge.open(); TX stop first in destroy_node (TX-06); _handle_start_tx as module-level function to access Redis IQ key
- [Phase 04-tx-authorization]: _handle_start_tx as module-level function to access Redis IQ key via node._redis_bridge._redis
- [Phase 04-tx-authorization]: TXHardBlockedError not caught in _handle_start_tx — propagates to generic error handler at error level (EPIRB/ADS-B blocks are unconditional law violations, not warnings)
- [Phase 04-tx-authorization]: _last_tx_freq attribute added to TXController.start_tx() for state dict tracking without changing guard logic
- [Phase 05-pymayhem-refactor]: pymayhem uses stdlib logging.getLogger('pymayhem.serial') instead of injected logger; domain objects receive _send_command callable at construction via callable injection pattern (D-04)
- [Phase 05-pymayhem-refactor]: setup.cfg added alongside pyproject.toml for legacy editable install compatibility with system pip 22.0.2; UnsafeMayhemClient methods return raw list[str] for dangerous commands (caller controls error handling)

### Pending Todos

None yet.

### Blockers/Concerns

- **Phase 2 (serial):** Concurrent pyhackrf2 + Mayhem serial access is undocumented — empirical test required at phase start. If they cannot coexist, a mode-switch state machine is needed (~1 extra plan of complexity).
- **Phase 4 (TX):** pyhackrf2 start_tx() API has thin documentation. Half-duplex RX/TX switching timing needs empirical validation before writing TXController.

## Session Continuity

Last session: 2026-03-30T09:12:53.954Z
Stopped at: Completed 05-pymayhem-refactor 05-01-PLAN.md
Resume file: None
