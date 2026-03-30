# Phase 6: Foundation Hardening - Context

**Gathered:** 2026-03-30
**Status:** Ready for planning

<domain>
## Phase Boundary

Replace silent failure modes across pymayhem and hackrf_driver with typed exceptions, add input validation at the hackrf_driver boundary, make BridgeNode survive Redis restarts, add IQ sequence numbers for gap detection, provide TX dry-run validation, expose antenna confirmation as a ROS2 service, and deprecate legacy HackRFNode.

</domain>

<decisions>
## Implementation Decisions

### Exception Hierarchy Design
- **D-01:** pymayhem exception approach is Claude's discretion. Recommended: raise on all command failures with MayhemError hierarchy, plus a compatibility wrapper or catch-at-boundary pattern for existing callers. The dispatch boundary in redis_bridge.py (ERR-05) must catch pymayhem exceptions and map to structured Redis error state — never propagate raw exceptions through the dispatch table.
- **D-02:** hackrf_driver uses a unified HackRFError tree. HackRFError is the base; HackRFConfigError (validation), HackRFDeviceError (USB/connection), and existing TX exceptions (TXBlockedError, TXFreqBlockedError, TXHardBlockedError, TXNotAuthorizedError) all reparented under HackRFError. One `except HackRFError` catches everything.
- **D-03:** pymayhem gets its own MayhemError base with subtypes: MayhemCommandError (firmware returned error), MayhemParseError (unparseable response), MayhemTimeoutError (wraps existing TimeoutError). Domain methods raise these instead of returning bool.

### Validation Boundary
- **D-04:** Single source of truth for hardware validation in hackrf_driver. PARAM_RANGES in config.py is the canonical validator. hackrf_driver raises HackRFConfigError on out-of-range values. BridgeNode and Redis dispatch trust hackrf_driver — they do NOT duplicate validation. pymayhem validates its own domain-specific inputs independently (e.g., freq_hz is int, app name is non-empty string).

### TX Dry-Run Scope
- **D-05:** validate_tx() checks guards only — antenna confirmed, freq not hard-blocked, freq filter check, auth token EXISTS (via GET, not GETDEL). Pure logic, no hardware state dependency. Returns pass/fail with which guard would block. Works even when HackRF hardware is offline.

### Sequence Number Design
- **D-06:** IQ sequence numbers use epoch + counter format: "{driver_start_epoch}:{monotonic_counter}" (e.g., "1711814400:42"). Added as `seq` field on every XADD entry. Counter starts at 0 each driver run. Consumers detect gaps (missing counter values) and restarts (epoch changes) from one field. No persistence needed — epoch is captured once at HackRFDriver.__init__.

### BridgeNode Redis Reconnection
- **D-07:** Replace the fatal `except Exception: break` in BridgeNode._bridge_loop() with an exponential backoff retry loop. On Redis error: log warning, sleep with backoff (1s-30s), attempt reconnect + resubscribe to hackrf:iq:notify Pub/Sub. Flush state immediately after successful reconnect. Pattern mirrors existing driver reconnection logic.

### Antenna Confirmation Service
- **D-08:** BridgeNode exposes `/hackrf/confirm_antenna` ROS2 service (std_srvs/Trigger). Handler sets Redis key `hackrf:tx:antenna_confirmed` to b'1'. TXController periodically re-reads this key (not just at init) — re-read interval is Claude's discretion.

### Legacy Deprecation
- **D-09:** hackrf_node.py gets a deprecation docstring and a log.warning at import time pointing to hackrf_driver + BridgeNode. File stays in place for colcon build compatibility.

### Claude's Discretion
- pymayhem exception backward compatibility strategy (D-01): whether to use safe_* wrappers, catch-at-boundary, or direct raise-everywhere
- TXController antenna re-read interval (D-08): 30s, 60s, or configurable
- Exception naming refinements within the defined hierarchies

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Exception Patterns
- `hackrf_driver/hackrf_driver/tx_controller.py` lines 29-41 — Existing TX exception classes (reparent under HackRFError)
- `pymayhem/pymayhem/_serial.py` lines 141-196 — Existing TimeoutError handling and retry pattern

### Validation
- `hackrf_driver/hackrf_driver/config.py` — PARAM_RANGES dict, canonical validation source
- `hackrf_driver/hackrf_driver/driver.py` lines 341-365 — `_update_param` current validation flow

### Redis Bridge
- `hackrf_ros/bridge_node.py` line 130 — Fatal `break` on Redis error (the bug to fix)
- `hackrf_driver/hackrf_driver/redis_bridge.py` — Existing RedisBridge pattern with daemon thread and stop events
- `hackrf_driver/hackrf_driver/driver.py` lines 240-276 — Existing reconnection pattern with exponential backoff

### Research
- `.planning/research/PITFALLS.md` — Pitfall 1 (exception API break), Pitfall 7 (bridge death), Pitfall 10 (dry-run consuming token), Pitfall 11 (dual validation)
- `.planning/research/SUMMARY.md` — Phase 6 section with implementation guidance

### Bridge Services
- `hackrf_ros/bridge_services.py` — Closure factory pattern for ROS2 service handlers (pattern for antenna service)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- TXController exception pattern (4 classes inheriting from Exception) — extend to unified HackRFError tree
- PARAM_RANGES dict in config.py — already defines valid ranges, just needs to raise instead of warn
- bridge_services.py closure factories — pattern for the new antenna confirmation service
- Driver reconnection with exponential backoff (1s-30s) — mirror for BridgeNode Redis reconnect

### Established Patterns
- Daemon threads with threading.Event stop gates (RedisBridge, BridgeNode)
- Callable injection pattern (TXController takes primitives, not node refs)
- Redis HSET for state publishing (hackrf:state)
- pubsub.get_message(timeout=0.1) polling loop in BridgeNode

### Integration Points
- pymayhem/pymayhem/exceptions.py (new) — imported by client.py, domains/*
- hackrf_driver/hackrf_driver/exceptions.py (new) — imported by driver.py, tx_controller.py, redis_bridge.py
- hackrf_ros/bridge_services.py — add antenna confirmation handler factory
- hackrf_driver/hackrf_driver/redis_bridge.py _xadd_iq() — add seq field to XADD entry

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

*Phase: 06-foundation-hardening*
*Context gathered: 2026-03-30*
