# Phase 4: TX Authorization - Context

**Gathered:** 2026-03-30
**Status:** Ready for planning

<domain>
## Phase Boundary

Add safe, authorized transmission via pyhackrf2 start_tx() with one-token-per-TX authorization, a configurable frequency allowlist blocking restricted bands, explicit antenna confirmation, and automatic TX stop on shutdown. HackRF is half-duplex — RX pauses during TX and resumes after.

</domain>

<decisions>
## Implementation Decisions

### Auth Mechanism
- **D-01:** One-token-per-TX authorization via Redis GETDEL. Token set with `SET hackrf:tx:auth <uuid> EX 60` (60-second TTL). Consumed atomically with GETDEL on TX command.
- **D-02:** Upgrade Redis to 6.2+ to use native GETDEL. This is an infrastructure prerequisite for Phase 4.
- **D-03:** Token TTL is 60 seconds — forces near-immediate use after authorization but allows multi-step TX setup.

### Frequency Allowlist
- **D-04:** Block all restricted bands by default: cellular (700-900 MHz, 1700-2100 MHz, 2500-2700 MHz), aviation (108-137 MHz, 960-1215 MHz, 1030/1090 MHz), emergency/public safety (150-174 MHz, 450-470 MHz).
- **D-05:** ROS2 parameter `tx_freq_filter_enabled` (default True) as primary disable flag. Redis key `hackrf:tx:freq_filter_override` as secondary remote override. Both must agree — filter disabled only when both say "disabled".
- **D-06:** TX command targeting a restricted frequency is hard-rejected regardless of authorization token.

### Antenna Confirmation
- **D-07:** Per-session flag: set once at startup via Redis key `hackrf:tx:antenna_confirmed` or ROS2 parameter. Persists until node restart. TX blocked until confirmed.
- **D-08:** ROS2 parameter `tx_skip_antenna_check` (default False) for automated testing environments. When True, antenna confirmation is bypassed.

### TX Interface
- **D-09:** Start/stop TX model: `start_tx` command begins transmission, `stop_tx` ends it. IQ data for TX is streamed to a Redis key (`hackrf:tx:iq_data`) which the TX loop reads.
- **D-10:** Half-duplex handling: `start_tx` automatically pauses RX streaming (stop_rx), TX runs, `stop_tx` automatically resumes RX streaming (start_rx). Transparent to the caller.
- **D-11:** TX automatically stopped on node shutdown — `stop_tx()` called in `destroy_node()` before any other cleanup (TX-06).
- **D-12:** TX commands routed through Redis `hackrf:cmd` with `auth_token` field required: `{"action": "start_tx", "freq_hz": 433000000, "auth_token": "<uuid>"}`.

### Claude's Discretion
- TXController class design (helper class vs integrated into HackRFNode)
- pyhackrf2 start_tx() callback implementation details
- Exact band boundary definitions (research should verify exact MHz ranges)
- TX IQ data format and Redis key structure for streaming TX samples
- Thread model for TX data feeding

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Existing Driver Code
- `hackrf_ros/hackrf_node.py` — Current driver with RX pipeline, MayhemSerial, RedisBridge
- `hackrf_ros/redis_bridge.py` — RedisBridge class (command dispatch, `_COMMAND_HANDLERS`)
- `hackrf_ros/mayhem_serial.py` — MayhemSerial helper class

### Research (Project-Level)
- `.planning/research/STACK.md` — TX auth pattern (GETDEL), pyhackrf2 TX API
- `.planning/research/FEATURES.md` — pyhackrf2 start_tx()/stop_tx(), txvga_gain
- `.planning/research/PITFALLS.md` — TX without antenna destroys amplifier, half-duplex switching timing, legal/frequency risks
- `.planning/research/ARCHITECTURE.md` — TXController design

### Phase 3 Research
- `.planning/phases/03-redis-bridge/03-RESEARCH.md` — Redis 6.0.16 constraint (GETDEL needs 6.2+)

### Prior Phase Context
- `.planning/phases/01-rx-pipeline-correctness/01-CONTEXT.md` — _device_lock, _stop_event, lifecycle patterns
- `.planning/phases/02-mayhem-serial-interface/02-CONTEXT.md` — Helper class pattern, ROS2 services
- `.planning/phases/03-redis-bridge/03-CONTEXT.md` — Redis command format, _COMMAND_HANDLERS dispatch

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `_device_lock` (threading.RLock) — serialize TX/RX hardware access
- `_stop_event` (threading.Event) — signal TX thread to stop
- `_COMMAND_HANDLERS` dispatch table in redis_bridge.py — add `start_tx` and `stop_tx` actions
- `_start_rx_if_stopped()` / `_stop_rx_if_running()` — already exist from Phase 3 for half-duplex switching
- MayhemSerial/RedisBridge helper class pattern — follow for TXController if separate class

### Established Patterns
- Helper classes with open/close lifecycle owned by HackRFNode
- ROS2 parameters declared in `__init__` with descriptors
- Redis key namespace `hackrf:` prefix
- Exponential backoff for connection failures
- Structured logging via `self.get_logger()`

### Integration Points
- `HackRFNode.__init__` — instantiate TXController, register TX services
- `HackRFNode.destroy_node` — stop TX first, before RedisBridge/MayhemSerial/pyhackrf2
- `redis_bridge._COMMAND_HANDLERS` — add `start_tx`, `stop_tx`, `authorize_tx` commands
- `hackrf:state` — add TX-related fields (is_transmitting, tx_freq, antenna_confirmed)

</code_context>

<specifics>
## Specific Ideas

- Redis must be upgraded to 6.2+ before this phase executes — add as a prerequisite check at startup
- The frequency allowlist should be defined as a list of (min_hz, max_hz) tuples for easy extension
- Consider logging all TX attempts (authorized or rejected) for audit trail

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 04-tx-authorization*
*Context gathered: 2026-03-30*
