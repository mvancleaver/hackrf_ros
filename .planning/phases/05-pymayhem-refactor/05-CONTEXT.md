# Phase 5: PyMayhem Refactor - Context

**Gathered:** 2026-03-30
**Status:** Ready for planning

<domain>
## Phase Boundary

Extract a standalone `pymayhem` Python package from MayhemSerial, refactor the HackRF driver to be Redis-native (no ROS2 dependency in the core driver process), and create a thin ROS2 bridge node that reads IQ/state from Redis and publishes to ROS2 topics. The existing 68 unit tests must pass after refactor.

</domain>

<decisions>
## Implementation Decisions

### Package Structure
- **D-01:** Monorepo layout — all three packages as sibling directories in this repo:
  - `pymayhem/` — standalone Mayhem serial API
  - `hackrf_driver/` — Redis-native HackRF driver (pyhackrf2 + pymayhem + redis)
  - `hackrf_ros_bridge/` — thin ROS2 node (redis -> ROS2 topics)
- **D-02:** `pymayhem` is local-only for now (`pip install -e ./pymayhem`). Set up pyproject.toml for future PyPI publishing but don't publish yet.
- **D-03:** Existing `hackrf_ros/` package becomes the ROS2 bridge. Core driver logic moves to `hackrf_driver/`.

### pymayhem API Design
- **D-04:** Domain module organization: `client.radio.info()`, `client.ui.button(1)`, `client.fs.ls('/')`, `client.sensors.gotgps(...)`, `client.system.sysinfo()`. Sub-objects on MayhemClient.
- **D-05:** Both context manager and explicit open/close: `with MayhemClient('/dev/hackrf_mayhem') as m:` and `m = MayhemClient(); m.open(); m.close()`.
- **D-06:** Dangerous commands (write_memory, flash, dfu, pmemreset, settingsreset, sd_over_usb) exposed only on `UnsafeMayhemClient` subclass — never on the base `MayhemClient`.
- **D-07:** `appstart` must handle USB reset automatically — `appstart_with_reconnect()` sends command, expects USB disconnect, polls for device reappearance (up to 10s), reconnects.
- **D-08:** Auto-detect device via udev symlink `/dev/hackrf_mayhem` or USB VID:PID `1d50:6018` scan.
- **D-09:** Firmware version detection via `info` command — report capability level (e.g., `setfreq` missing in v2.0.1).

### Driver Decoupling
- **D-10:** Layered config: YAML/JSON config file for defaults, Redis hash (`hackrf:config`) for runtime overrides. Config file loaded at startup, Redis commands override at runtime.
- **D-11:** Dual entry point: `python -m hackrf_driver` for programmatic/Docker use, `hackrf-driver --freq 433e6 --gain 20` CLI with argparse for one-off use.
- **D-12:** Core driver has NO rclpy import. Uses threading for event loop, Redis for all I/O. Replaces ROS2 timers with `threading.Timer` or a simple main loop.
- **D-13:** Core driver keeps: pyhackrf2 RX/TX pipeline, dual queue.Queue buffer, exponential backoff reconnection, parameter validation (PARAM_RANGES), RedisBridge, TXController. Imports pymayhem for serial.
- **D-14:** Redis Pub/Sub notification on IQ stream write — driver publishes to `hackrf:iq:notify` channel after each XADD so bridge can subscribe with low latency.

### ROS2 Bridge Design
- **D-15:** IQ transport uses Redis Pub/Sub: bridge subscribes to `hackrf:iq:notify`, then XREAD the latest entry from `hackrf:iq:stream`. Low latency without polling.
- **D-16:** Bridge exposes: `/hackrf/iq` (Float32MultiArray), `/hackrf/state` (String/JSON), command service (writes to `hackrf:cmd`), and Mayhem services (appstart, setfreq, radioinfo proxied through Redis).
- **D-17:** Bridge is a standard ROS2 node in `hackrf_ros_bridge/` — lightweight, only depends on rclpy + redis-py.
- **D-18:** Existing ROS2 subscribers work unchanged — same topic names, same message types.

### Claude's Discretion
- Internal threading model for the standalone driver main loop
- Redis Pub/Sub channel naming for IQ notifications
- How to migrate existing tests (split across packages vs shared test suite)
- Whether hackrf_driver uses click/typer vs argparse for CLI
- Docker image restructuring (if needed)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Current Code (refactor targets)
- `hackrf_ros/hackrf_node.py` — Monolithic node to be split into driver + bridge
- `hackrf_ros/mayhem_serial.py` — Becomes the core of `pymayhem`
- `hackrf_ros/redis_bridge.py` — Moves to `hackrf_driver/`
- `hackrf_ros/tx_controller.py` — Moves to `hackrf_driver/`
- `test/test_mayhem_serial.py` — Moves to `pymayhem/tests/`
- `test/test_redis_bridge.py` — Moves to `hackrf_driver/tests/`
- `test/test_tx_controller.py` — Moves to `hackrf_driver/tests/`
- `test/test_hackrf_node_redis.py` — Splits: driver tests + bridge tests
- `test/conftest.py` — Redis mock fixture, may need duplication

### Research
- `.planning/research/MAYHEM_API_ASSESSMENT.md` — Full command inventory, safety assessment, API design recommendations
- `.planning/research/STACK.md` — redis-py, pyserial versions

### Config / Build
- `setup.py` — Current ROS2 package setup (bridge will keep this)
- `package.xml` — ROS2 package manifest (bridge only)
- `99-hackrf-mayhem.rules` — udev rules (shared)
- `test_standalone.py` — Standalone test script (reference for driver testing pattern)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `MayhemSerial` class (270 lines) — direct extraction into pymayhem with domain module refactor
- `RedisBridge` class — moves to hackrf_driver unchanged
- `TXController` class — moves to hackrf_driver unchanged
- `PARAM_RANGES`, `CHUNK_IQ_PAIRS`, backoff constants — move to hackrf_driver
- `test_standalone.py` — pattern for driver integration testing without ROS2
- `99-hackrf-mayhem.rules` — udev rules shared across all packages

### Migration Map
- `hackrf_node.py` __init__ parameter declarations -> YAML config + Redis hash
- `hackrf_node.py` ROS2 timers -> threading.Timer or main loop
- `hackrf_node.py` ROS2 services -> Redis command handlers (already exist)
- `hackrf_node.py` ROS2 publisher -> Redis XADD (already exists)
- `hackrf_node.py` destroy_node -> signal handler (SIGINT/SIGTERM)

### Integration Points
- pymayhem is a dependency of hackrf_driver (serial control)
- hackrf_driver writes to Redis (IQ, state, notify)
- hackrf_ros_bridge reads from Redis and publishes to ROS2
- All three share the `hackrf:` Redis namespace

</code_context>

<specifics>
## Specific Ideas

- The pymayhem package should include the `test_standalone.py --serial-only` test pattern as an example
- `hackrf_driver` should be runnable in Docker without ROS2 installed — just Python + Redis
- The bridge could eventually support other middleware (MQTT, ZMQ) by swapping the bridge implementation

</specifics>

<deferred>
## Deferred Ideas

- PyPI publishing of pymayhem — set up pyproject.toml now, publish later
- MQTT/ZMQ bridge alternatives — future work
- Docker image restructuring — separate concern

</deferred>

---

*Phase: 05-pymayhem-refactor*
*Context gathered: 2026-03-30*
