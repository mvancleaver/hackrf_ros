# Phase 2: Mayhem Serial Interface - Context

**Gathered:** 2026-03-30
**Status:** Ready for planning

<domain>
## Phase Boundary

Build serial communication with Mayhem firmware over /dev/ttyACM1. Discover apps at startup, switch between them, update frequency, verify configuration via radioinfo, and document mode-conflict behavior. Expose Mayhem commands through ROS2 services and status via topics.

</domain>

<decisions>
## Implementation Decisions

### Serial Architecture
- **D-01:** MayhemSerial is a separate helper class owned by HackRFNode — clean separation, independently testable. HackRFNode manages its lifecycle (open at startup, close at shutdown).
- **D-02:** Dedicated daemon reader thread with `readline()` loop and `timeout=1.0`. Not a ROS2 timer callback — serial needs its own thread for responsiveness.
- **D-03:** Serial port path is `/dev/ttyACM1` — configurable via ROS2 parameter.

### Mode Conflict
- **D-04:** Assume pyhackrf2 and Mayhem serial coexist. Build integration assuming both work simultaneously. Add fallback later if empirical testing proves otherwise.
- **D-05:** RESEARCH DIRECTIVE: Investigate whether Mayhem firmware has a native SDK or API that could replace pyhackrf2 entirely. If a Mayhem-native IQ streaming interface exists over serial or another path, it would resolve the mode conflict by using a single interface. This is a critical research question for the phase researcher.
- **D-06:** If coexistence fails at runtime, log a clear error explaining the conflict. Do not silently degrade.

### Command Interface
- **D-07:** ROS2 services for commands (appstart, setfreq, radioinfo) — request/response pattern with structured responses.
- **D-08:** ROS2 topic for status updates — publish current app state, discovered apps, and radioinfo periodically.
- **D-09:** Runtime discovery only — query `applist` at startup to discover available Mayhem apps. No hardcoded app names. Expose discovered names through the status topic.

### Error Handling
- **D-10:** Same exponential backoff reconnection pattern as pyhackrf2 USB (from Phase 1: 1s -> 30s cap, retry indefinitely). Consistent behavior across both interfaces.
- **D-11:** Command timeout is configurable via ROS2 parameter, default 3 seconds. Some commands (appstart) may take longer.
- **D-12:** Garbled or incomplete responses are logged as warnings and the command is retried once. If retry also fails, return error to the service caller.

### Claude's Discretion
- Exact pyserial configuration (baud rate, parity, stop bits for ACM)
- Serial response parsing strategy (line-oriented with \r\n termination)
- ROS2 service message type definitions (custom vs std_srvs)
- Whether to use a command queue or direct send from service callbacks
- Thread synchronization between serial reader and command sender

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Existing Driver Code
- `hackrf_ros/hackrf_node.py` — Current HackRF node with Phase 1 refactors (queue.Queue, reconnection, validation). MayhemSerial integrates here.
- `setup.py` — Entry points (may need update if new node added)
- `package.xml` — Dependencies (add pyserial)

### Research (Project-Level)
- `.planning/research/STACK.md` — pyserial 3.5 recommendation, Mayhem serial protocol reference
- `.planning/research/FEATURES.md` — Mayhem serial command reference (applist, appstart, setfreq, radioinfo, sendpocsag)
- `.planning/research/ARCHITECTURE.md` — MayhemSerial helper class design, thread model
- `.planning/research/PITFALLS.md` — Mayhem mode conflict, serial path instability in Docker, response parsing quirks

### Phase 1 Context
- `.planning/phases/01-rx-pipeline-correctness/01-CONTEXT.md` — Prior decisions: exponential backoff pattern, error behavior, node identity

### Codebase Analysis
- `.planning/codebase/ARCHITECTURE.md` — Current data flow and layers
- `.planning/codebase/CONCERNS.md` — Known issues

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `_try_connect` / `_reconnect_callback` pattern from Phase 1 — reuse for serial reconnection
- `_device_lock` (threading.RLock) — may coordinate serial vs pyhackrf2 access
- `PARAM_RANGES` validation pattern — reuse for serial command parameter validation
- Exponential backoff constants `_MIN_RECONNECT_DELAY` / `_MAX_RECONNECT_DELAY`

### Established Patterns
- Helper classes owned by the node (MayhemSerial follows this pattern)
- Daemon threads for I/O (serial reader follows `_rx_callback` threading pattern)
- Structured logging via `self.get_logger()` — MayhemSerial should accept a logger reference
- `_stop_event` (threading.Event) for clean shutdown signaling

### Integration Points
- `HackRFNode.__init__` — instantiate MayhemSerial, pass logger and serial path
- `HackRFNode.destroy_node` — close MayhemSerial before closing pyhackrf2
- ROS2 service registration in `__init__` for Mayhem commands
- ROS2 publisher for Mayhem status topic

</code_context>

<specifics>
## Specific Ideas

- Research whether Mayhem has a native SDK that replaces pyhackrf2 — this could fundamentally change the architecture if a unified interface exists
- Serial baud rate is likely 115200 (standard ACM default) but needs empirical verification on the target device

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 02-mayhem-serial-interface*
*Context gathered: 2026-03-30*
