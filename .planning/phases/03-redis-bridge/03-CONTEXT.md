# Phase 3: Redis Bridge - Context

**Gathered:** 2026-03-30
**Status:** Ready for planning

<domain>
## Phase Boundary

Stream IQ data and device state to Redis. Accept control commands from Redis. The `_redis_queue` from Phase 1 already receives IQ chunks — this phase adds the consumer that publishes to Redis Streams, a state hash, and a command subscriber.

</domain>

<decisions>
## Implementation Decisions

### Redis Connection
- **D-01:** Connect to localhost:6379, no auth. Standard Docker host networking setup.
- **D-02:** Graceful degradation: if Redis is unreachable at startup or disconnects, IQ still flows on ROS2 topics. Redis publishing disabled until reconnect. Log warning, retry with backoff.

### IQ Data Format
- **D-03:** IQ samples encoded as interleaved float32 array [I,Q,I,Q,...] in Redis Streams — same format as ROS2 topic, ready to use without conversion.
- **D-04:** MAXLEN is configurable via ROS2 parameter `redis_stream_maxlen`, default 10000 (~50s at 10 MSPS). Use approximate trimming (`~` modifier) for performance.

### Command Routing
- **D-05:** Commands are JSON messages published to `hackrf:cmd` Redis Stream. Format: `{"action": "<name>", ...params}`.
- **D-06:** Accepted actions: `setfreq`, `set_sample_rate`, `set_lna_gain`, `set_vga_gain`, `set_amp_enabled` (HackRF config), `appstart`, `serial_setfreq` (Mayhem control), `start_rx`, `stop_rx` (stream control).
- **D-07:** Command responses are not published back — callers check `hackrf:state` for confirmation. This avoids response routing complexity.

### State Schema
- **D-08:** `hackrf:state` Redis Hash contains:
  - HackRF config: `center_frequency`, `sample_rate`, `lna_gain`, `vga_gain`, `amp_enabled`
  - Streaming status: `is_streaming`, `connected`, `uptime_s`
  - Mayhem state: `active_app`, `discovered_apps` (JSON array), `serial_connected`
- **D-09:** State updated on change only — when config changes or state transitions. No periodic heartbeat. Consumers use `hackrf:state` last-modified timestamp or subscribe to keyspace notifications if they need liveness.

### Redis Architecture
- **D-10:** RedisBridge as a separate helper class owned by HackRFNode — same pattern as MayhemSerial (Phase 2 D-01).
- **D-11:** All Redis I/O runs in a dedicated daemon thread. XADD for IQ, HSET for state, and XREAD for commands all happen in this thread. Never block ROS2 executor callbacks.
- **D-12:** Use `hackrf:` namespace prefix for all Redis keys: `hackrf:iq:stream`, `hackrf:state`, `hackrf:cmd`.

### Claude's Discretion
- redis-py connection pooling configuration
- Exact thread synchronization between `_redis_queue` consumer and Redis XADD
- How command subscriber thread interacts with ROS2 parameter callbacks
- Whether to use a single daemon thread or split IQ publishing and command subscription

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Existing Driver Code
- `hackrf_ros/hackrf_node.py` — Current driver with `_redis_queue` (Phase 1), MayhemSerial integration (Phase 2)
- `hackrf_ros/mayhem_serial.py` — MayhemSerial helper class pattern to follow for RedisBridge

### Research (Project-Level)
- `.planning/research/STACK.md` — redis-py 7.4.0 + hiredis 3.3.1, Redis Streams vs Pub/Sub rationale
- `.planning/research/ARCHITECTURE.md` — RedisBridge daemon thread design, queue.Queue consumer pattern
- `.planning/research/PITFALLS.md` — Redis OOM without MAXLEN, backpressure at high rates

### Prior Phase Context
- `.planning/phases/01-rx-pipeline-correctness/01-CONTEXT.md` — D-01 dual queues, D-02 drop-oldest, D-03 maxsize=64, D-04 fixed 2048 chunks
- `.planning/phases/02-mayhem-serial-interface/02-CONTEXT.md` — D-01 helper class pattern, D-02 daemon thread pattern

### Package Config
- `setup.py` — Add redis and hiredis dependencies
- `package.xml` — Add redis dependency

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `_redis_queue` (queue.Queue, maxsize=64) — already populated by `_rx_callback` with raw IQ chunks. RedisBridge consumes this.
- MayhemSerial class pattern — daemon thread, open/close lifecycle, owned by HackRFNode. RedisBridge follows same structure.
- Exponential backoff constants `_MIN_RECONNECT_DELAY` / `_MAX_RECONNECT_DELAY` — reuse for Redis reconnection.
- `_stop_event` (threading.Event) — signal RedisBridge thread to shut down cleanly.

### Established Patterns
- Helper classes owned by HackRFNode with open/close lifecycle
- Daemon threads for I/O with `_stop_event` shutdown signaling
- Structured logging via logger reference passed to helper
- ROS2 parameters declared in `__init__` with descriptors

### Integration Points
- `HackRFNode.__init__` — instantiate RedisBridge, pass `_redis_queue`, logger, config params
- `HackRFNode.destroy_node` — close RedisBridge before MayhemSerial and pyhackrf2
- `_on_parameter_event` — trigger state update on config change
- Mayhem service handlers — trigger state update on app switch

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches for Redis Streams and daemon thread patterns.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 03-redis-bridge*
*Context gathered: 2026-03-30*
