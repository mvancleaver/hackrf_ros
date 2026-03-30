---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: Completed 06-04-PLAN.md
last_updated: "2026-03-30T22:11:25.741Z"
last_activity: 2026-03-30
progress:
  total_phases: 8
  completed_phases: 5
  total_plans: 19
  completed_plans: 17
  percent: 0
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-30)

**Core value:** Reliable, safe bidirectional SDR control with IQ data streaming to Redis and TX operations gated behind explicit authorization.
**Current focus:** Phase 06 — foundation-hardening

## Current Position

Phase: 06 (foundation-hardening) — EXECUTING
Plan: 3 of 4
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
| Phase 05-pymayhem-refactor P02 | 7min | 2 tasks | 10 files |
| Phase 05-pymayhem-refactor P03 | 5min | 2 tasks | 4 files |
| Phase 05-pymayhem-refactor P04 | 4min | 3 tasks | 4 files |
| Phase 05-pymayhem-refactor P05 | 25min | 1 tasks | 6 files |
| Phase 06-foundation-hardening P01 | 3min | 2 tasks | 6 files |
| Phase 06-foundation-hardening P04 | 2min | 2 tasks | 1 files |

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
- [Phase 05-pymayhem-refactor]: TXController takes hackrf_getter Callable, device_lock RLock, stop_rx_fn, start_rx_fn, freq_filter_enabled bool — no rclpy node reference
- [Phase 05-pymayhem-refactor]: D-14 implemented: xadd returns entry_id, then redis.publish('hackrf:iq:notify', entry_id) in _xadd_iq()
- [Phase 05-pymayhem-refactor]: HackRFDriver uses threading.Event stop gate and threading.Timer reconnect — no rclpy dependency
- [Phase 05-pymayhem-refactor]: TXController wired with primitive callables: hackrf_getter lambda, device_lock, stop_rx_fn, start_rx_fn replacing HackRFNode node reference
- [Phase 05-pymayhem-refactor]: BridgeNode uses pubsub.get_message(timeout=0.1) polling loop to check _stop_event regularly; bridge_services.py closure factories capture node/redis at construction time
- [Phase 05-pymayhem-refactor]: hackrf_node entry point updated to bridge_node:main; bridge has no pyhackrf2/serial imports
- [Phase 05-pymayhem-refactor]: setUpModule/tearDownModule pattern for sys.modules isolation in test_hackrf_node_redis.py
- [Phase 05-pymayhem-refactor]: pymayhem/tests/__init__.py removed to fix pytest collection from workspace root
- [Phase 06-foundation-hardening]: TX exceptions reparented under HackRFError via new exceptions.py; re-exported from tx_controller for backward compatibility
- [Phase 06-foundation-hardening]: pymayhem domain bool returns converted to raise MayhemCommandError; appstart_with_reconnect keeps bool return (reconnect timeout, not command error)
- [Phase 06-foundation-hardening]: validate_tx uses GET (not GETDEL) for auth token — read-only pre-flight check leaves token intact for subsequent start_tx
- [Phase 06-foundation-hardening]: Antenna re-read timer set daemon=True and cancelled at top of stop_tx before _tx_lock acquisition

### Pending Todos

- Phase 6 plan: Exception dispatch boundary (ERR-05) must catch pymayhem/hackrf exceptions at _dispatch_command boundary — never propagate raw exceptions through the dispatch table (research Pitfall 1)
- Phase 7 plan: Watchdog correction queue design — name the thread that drains the correction queue and specify how it avoids re-entry with _configure_device() (research flag from SUMMARY.md)
- Phase 8 plan: SigMF gap entry calculation — when a sequence gap is detected, the exact formula (chunk_count * chunk_size) for sample_start must be specified before IQRecorder is implemented

### Blockers/Concerns

- **Phase 7 (watchdog):** Lock acquisition order between _device_lock, _tx_lock, and watchdog correction queue must be explicitly designed. Deadlock risk is fully traced in research SUMMARY.md — phase plan must resolve before implementation.
- **Phase 8 (spectrum):** OPENBLAS_NUM_THREADS=1 mitigation for Jetson ARM64 is not tested against actual throughput numbers. Phase plan should include a performance benchmark gate.

## Session Continuity

Last session: 2026-03-30T22:11:25.738Z
Stopped at: Completed 06-04-PLAN.md
Resume file: None
