# Phase 1: RX Pipeline Correctness - Context

**Gathered:** 2026-03-29
**Status:** Ready for planning

<domain>
## Phase Boundary

Fix thread-safety bugs, add error recovery with reconnection, validate parameters against hardware ranges, and clean up code conventions in the existing IQ pipeline. No new features (Redis, serial, TX) — just make the existing RX path robust and correct.

</domain>

<decisions>
## Implementation Decisions

### Buffer Strategy
- **D-01:** Replace unsafe shared numpy buffer with dual `queue.Queue` instances — one for ROS2 publishing, one for future Redis streaming (Phase 3). RX callback enqueues to both independently.
- **D-02:** Overflow policy is drop-oldest (ring-buffer style). No backpressure — never block the RX callback.
- **D-03:** Queue depth is small: 64 chunks. Prioritize low latency over burst tolerance.
- **D-04:** Fixed chunk size of 2048 IQ samples per queue entry. Remove the configurable `num_iq_samples_per_publish` parameter.
- **D-05:** RX callback must be stripped to bare enqueue — no numpy processing, no format conversion in the callback. All processing moves to the consumer side.

### Reconnection
- **D-06:** Exponential backoff on USB disconnect: start at 1s, double each retry, cap at 30s. Retry indefinitely.
- **D-07:** On successful reconnect, restore all last-known parameters (frequency, gain, sample rate, bandwidth) automatically.

### Error Behavior
- **D-08:** Node starts even if no device is found. Enters reconnection loop, logs error. Services still respond but report "no device".
- **D-09:** Only unrecoverable hardware failure (burned/bricked device) is fatal. Everything else retries — including repeated reconnect failures.

### Node Identity
- **D-10:** Rename class from `HackRFPuiblisherNode` to `HackRFNode`. Node name becomes `hackrf_node`.
- **D-11:** Rename topic from `/hackrf_iq_data` to `/hackrf/iq` (ROS2 namespaced convention).
- **D-12:** Update `iq_plotter_node.py` in this phase — subscribe to new topic name `/hackrf/iq`, fix any convention issues.
- **D-13:** Replace all bare `print()` statements with `self.get_logger()` calls at appropriate levels. Replace deprecated `.warn()` with `.warning()`.
- **D-14:** Remove unused imports, fix exception handler formatting, use specific exception types instead of broad `except Exception`.

### Claude's Discretion
- Thread-safe queue implementation details (stdlib `queue.Queue` vs alternatives)
- stop_rx() deadlock mitigation approach (timeout guard specifics)
- Specific exception types to catch from pyhackrf2
- Parameter validation range enforcement implementation
- MultiThreadedExecutor callback group configuration

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Existing Driver Code
- `hackrf_ros/hackrf_node.py` — Current HackRF driver node (main refactor target)
- `hackrf_ros/iq_plotter_node.py` — Plotter node (update topic subscription + conventions)
- `config/hackrf_rx.yaml` — Parameter configuration file

### Codebase Analysis
- `.planning/codebase/ARCHITECTURE.md` — Current data flow and layer boundaries
- `.planning/codebase/CONCERNS.md` — Known bugs and tech debt (thread safety, buffer growth, exception handling)
- `.planning/codebase/CONVENTIONS.md` — Naming patterns and code style

### Research
- `.planning/research/PITFALLS.md` — stop_rx() deadlock, GIL contention, buffer growth risks
- `.planning/research/ARCHITECTURE.md` — queue.Queue threading model, callback group design

### Package Config
- `setup.py` — Entry points and package metadata (update class name, add proper metadata)
- `package.xml` — ROS2 package manifest
- `setup.cfg` — Setuptools configuration

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `config/hackrf_rx.yaml` — Parameter file can be extended with new validation ranges
- `hackrf_ros/__init__.py` — Package init exists
- Docker and docker-compose setup — deployment infrastructure intact

### Established Patterns
- ROS2 Node subclass with `__init__`, configuration methods, callbacks, `main()` entry
- Parameter declarations with descriptors in `__init__`
- Timer-based periodic publishing (currently 0.005s / 200Hz)
- `_on_parameter_event()` callback for runtime reconfiguration

### Integration Points
- `setup.py` entry points: `hackrf_node` and `iq_plotter_node` console scripts
- `_rx_callback()` is the pyhackrf2 USB interrupt handler — refactor target
- `_read_and_publish_iq()` is the timer callback — refactor to consume from queue
- `_configure_hackrf()` — add validation before applying params, add deadlock guard around stop_rx()

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches for queue-based threading and ROS2 conventions.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 01-rx-pipeline-correctness*
*Context gathered: 2026-03-29*
