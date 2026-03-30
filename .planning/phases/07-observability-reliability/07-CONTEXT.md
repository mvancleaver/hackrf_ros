# Phase 7: Observability & Reliability - Context

**Gathered:** 2026-03-30
**Status:** Ready for planning

<domain>
## Phase Boundary

Add operator health visibility via Redis metrics hash, preserve failed commands in a dead-letter queue for forensic review, and implement a device health watchdog that self-heals USB stalls without deadlocking.

</domain>

<decisions>
## Implementation Decisions

### Watchdog Architecture
- **D-01:** Lock-free detection only. Watchdog thread checks `is_hackrf_streaming` flag and `_redis_queue.qsize()` — never acquires `_device_lock`. If stall detected (no IQ data for 10s), posts a reconnect request to a correction queue (stdlib `queue.Queue`). The existing main loop or a dedicated drain thread processes the correction queue and calls `_try_connect()`. This avoids ABBA deadlock with `_device_lock` (Pitfall 2 from research).
- **D-02:** Watchdog runs as a daemon thread with a 10s check interval. Stall detection: compare `_last_rx_time` (updated in `_rx_callback`) against `time.monotonic()`. If delta > 10s and driver is supposed to be streaming, flag stall.

### Metrics Content
- **D-03:** `hackrf:metrics` Redis hash published at 1 Hz via Redis pipeline (not per-XADD). Fields: `iq_chunks_sec` (throughput), `iq_drops` (drop-oldest count), `rx_errors` (device errors), `cmd_errors` (dispatch errors), `queue_depth_iq` (current IQ queue size), `queue_depth_redis` (Redis queue size), `uptime_s`, `last_rx_time` (epoch), `watchdog_reconnects` (count). All values are strings (Redis hash convention).
- **D-04:** BridgeNode publishes `/hackrf/metrics` ROS2 topic (String/JSON) at 5s interval by reading `hackrf:metrics` hash — mirrors existing `/hackrf/state` pattern.

### Dead-Letter Queue
- **D-05:** Failed commands archived to `hackrf:cmd:dlq` Redis stream via XADD MAXLEN ~ 500. Entry fields: `action` (command name), `error_type` (exception class name), `error_msg` (str), `timestamp` (ISO), `args` (JSON of original command args). Auth tokens MUST be stripped before archiving — replace token value with `"[REDACTED]"`.
- **D-06:** DLQ uses MAXLEN trimming only — no per-entry EXPIRE TTL. 500 entries is sufficient forensic history; Redis stream trimming handles cleanup automatically.

### Legacy Documentation
- **D-07:** HackRFNode deprecation notice (from Phase 6 LEG-01) is already in place. Phase 7 adds no additional legacy cleanup — this phase is purely additive.

### Claude's Discretion
- Correction queue drain mechanism (D-01): whether the main loop checks it or a dedicated thread
- Metrics accumulator implementation detail (counters as atomics vs simple ints with lock)
- DLQ Redis key name confirmation (`hackrf:cmd:dlq`)
- Watchdog thread naming convention

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Watchdog Design
- `.planning/research/PITFALLS.md` — Pitfall 2 (watchdog deadlock), Pitfall 8 (metrics overhead)
- `.planning/research/SUMMARY.md` — Phase 7 section with lock-free detection recommendation
- `hackrf_driver/hackrf_driver/driver.py` lines 240-276 — Existing reconnection pattern to mirror

### Exception Dispatch (DLQ hooks here)
- `hackrf_driver/hackrf_driver/redis_bridge.py` lines 302-334 — `_dispatch_command` exception boundary from Phase 6
- `hackrf_driver/hackrf_driver/exceptions.py` — HackRFError hierarchy

### Metrics Pattern
- `hackrf_driver/hackrf_driver/redis_bridge.py` — Existing `_publish_state` HSET pattern (mirror for metrics)
- `hackrf_ros/bridge_node.py` — Existing state topic publisher (mirror for metrics topic)

### Phase 6 Context
- `.planning/phases/06-foundation-hardening/06-CONTEXT.md` — Exception hierarchy decisions, validation boundary

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `_publish_state()` in redis_bridge.py — HSET pattern to mirror for `_publish_metrics()`
- `_bridge_loop()` reconnect pattern in bridge_node.py — established exponential backoff
- `_rx_callback()` in driver.py — add `_last_rx_time` timestamp update here
- `_dispatch_command()` exception boundary — hook DLQ archiving into the except blocks

### Established Patterns
- Daemon threads with `threading.Event` stop gates
- Redis pipeline for batched operations
- BridgeNode reads Redis hash → publishes ROS2 String/JSON topic

### Integration Points
- `hackrf_driver/driver.py` — add watchdog thread start/stop in `__init__`/`shutdown()`
- `hackrf_driver/redis_bridge.py` — add `_publish_metrics()` in bridge loop, add DLQ in dispatch boundary
- `hackrf_ros/bridge_node.py` — add metrics topic timer callback

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches within the decisions above.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 07-observability-reliability*
*Context gathered: 2026-03-30*
