---
phase: 03-redis-bridge
plan: "02"
subsystem: redis-bridge
tags: [redis, hackrf-node, mayhem-serial, tdd, state-publishing, command-dispatch]
dependency_graph:
  requires: [03-01 (RedisBridge class)]
  provides: [RedisBridge wired into HackRFNode, _active_app tracking, 9 command-dispatch methods]
  affects: [hackrf_ros/hackrf_node.py, hackrf_ros/mayhem_serial.py]
tech_stack:
  added: []
  patterns: [TDD RED/GREEN/REFACTOR, hasattr guard for graceful degradation, destroy_node ordering]
key_files:
  created:
    - test/test_hackrf_node_redis.py
  modified:
    - hackrf_ros/hackrf_node.py
    - hackrf_ros/mayhem_serial.py
decisions:
  - "RedisBridge.close() called FIRST in destroy_node before serial and pyhackrf2 (D-11 / CONTEXT.md requirement)"
  - "hasattr guard on _redis_bridge in _on_parameter_event and _handle_appstart for graceful degradation (D-02)"
  - "publish_state called in _on_parameter_event unconditionally after parameter processing (not just on reconfig) to capture serial-only state changes"
  - "_start_time recorded once in __init__ so uptime_s is always available in _build_state_dict"
metrics:
  duration: "8min"
  completed_date: "2026-03-29"
  tasks_completed: 2
  files_changed: 3
---

# Phase 3 Plan 2: Wire RedisBridge into HackRFNode Summary

**One-liner:** RedisBridge wired into HackRFNode lifecycle with _active_app tracking in MayhemSerial, _build_state_dict() exposing all 11 D-08 fields, 9 command-dispatch target methods, and publish_state() calls on parameter change and app switch — 44 unit tests all passing.

## What Was Built

- `hackrf_ros/hackrf_node.py` — RedisBridge instantiated in `__init__`, closed first in `destroy_node`, `_build_state_dict()` + 9 command-dispatch methods added, `publish_state()` wired at two call sites
- `hackrf_ros/mayhem_serial.py` — `_active_app` attribute added to `__init__`, `appstart()` updated to track active app on success
- `test/test_hackrf_node_redis.py` — 23 new unit tests covering all 9 behaviors, all passing with fully mocked ROS2/pyhackrf2/redis dependencies

## Key Implementation Details

**MayhemSerial changes (Task 1):**
- `self._active_app: str = ''` added in `__init__` after `_known_apps`
- `appstart()` now captures `ok` return and sets `self._active_app = short_name` on success
- Zero regressions: all 9 existing tests continue to pass

**HackRFNode changes (Task 2):**

**Imports added:**
- `from hackrf_ros.redis_bridge import RedisBridge`
- `import rclpy.parameter` (explicit import required for `Parameter.Type` enum access)

**`__init__` additions (in order):**
1. `self._start_time = time.monotonic()` — recorded before parameter declarations for accurate uptime
2. `redis_stream_maxlen` ROS2 parameter declared with default 10000 (D-04)
3. `self._redis_bridge = RedisBridge(self._redis_queue, self, ...)` + `self._redis_bridge.open()` — placed after MayhemSerial, before publisher creation

**Parameter event handler:**
- `publish_state(_build_state_dict())` called with `hasattr` guard after `_configure_device()` block

**Appstart handler:**
- `publish_state(_build_state_dict())` called when `response.success` is True, with `hasattr` guard

**`destroy_node` ordering (D-11):**
```python
# 1. RedisBridge.close() FIRST
if hasattr(self, '_redis_bridge'):
    self._redis_bridge.close()
# 2. then serial_reconnect_timer cancel + MayhemSerial.close()
# 3. then reconnect_timer cancel + pyhackrf2.close()
```

**New methods added:**
| Method | Purpose |
|--------|---------|
| `_build_state_dict()` | Returns all 11 D-08 fields as dict |
| `_set_center_frequency(freq_hz)` | Routes to ROS2 parameter system |
| `_set_sample_rate(sample_rate)` | Routes to ROS2 parameter system |
| `_set_lna_gain(lna_gain)` | Routes to ROS2 parameter system |
| `_set_vga_gain(vga_gain)` | Routes to ROS2 parameter system |
| `_set_amp_enabled(enabled)` | Routes to ROS2 parameter system |
| `_start_rx_if_stopped()` | Starts RX if device present and not streaming |
| `_stop_rx_if_running()` | Stops RX if device present and streaming |

**`_build_state_dict()` fields (D-08):**
```python
{
    'center_frequency', 'sample_rate', 'lna_gain', 'vga_gain', 'amp_enabled',
    'is_streaming', 'connected', 'uptime_s', 'active_app', 'discovered_apps',
    'serial_connected'
}
```

## Test Coverage

All 44 tests pass (no live hardware required):

| Test Class | Count | Covers |
|-----------|-------|--------|
| `TestHackRFNodeRedisAttributes` | 2 | Module import, class method presence |
| `TestBuildStateDict` | 7 | All D-08 fields, streaming state, uptime, active_app |
| `TestSetParameterMethods` | 5 | _set_* methods call set_parameters |
| `TestStartStopRxMethods` | 5 | _start/_stop rx no-op guards + success paths |
| `TestRedisBridgeIntegrationPoints` | 4 | Attribute presence, publish_state call sites, close |
| `TestMayhemSerialParsing` (existing) | 9 | No regressions |
| `TestRedisBridge*` (existing) | 12 | No regressions |

## Commits

- `5489724` — `feat(03-02): add _active_app tracking to MayhemSerial.appstart()`
- `3f247a2` — `test(03-02): add failing tests for HackRFNode RedisBridge integration`
- `df28f56` — `feat(03-02): wire RedisBridge into HackRFNode`

## Deviations from Plan

**1. [Rule 3 - Blocking] Worktree missing Phase 01/02 files**
- **Found during:** Task 1 setup
- **Issue:** The worktree branch was at `24263e4` (pre-Phase 01) and lacked `mayhem_serial.py`, `redis_bridge.py`, and the updated `hackrf_node.py`
- **Fix:** `git merge upstream/main` fast-forwarded the worktree to `5c89291`, bringing all Phase 01-03 planning and code artifacts
- **No code change required**

**2. [Rule 2 - Missing critical functionality] Explicit rclpy.parameter import**
- **Found during:** Task 2 implementation
- **Issue:** The plan noted to check if `rclpy.parameter` is accessible via `import rclpy` — it is not guaranteed
- **Fix:** Added `import rclpy.parameter` alongside the existing `import rclpy` (plan explicitly anticipated this)

**3. [Rule 3 - Blocking] Test framework: HackRFNode inherits from MagicMock**
- **Found during:** TDD RED phase
- **Issue:** `rclpy.node.Node` mocked as `MagicMock` caused `object.__new__(HackRFNode)` to fail since HackRFNode was not a real type
- **Fix:** Changed `sys.modules['rclpy.node'].Node` to a real Python class `_FakeNode` so `HackRFNode` inherits from a real type and `object.__new__` works correctly

## Known Stubs

None. All methods have full implementations. `_set_*` methods delegate to `self.set_parameters()` (the real ROS2 parameter system). `_build_state_dict()` reads from live node state. `_start_rx_if_stopped()` and `_stop_rx_if_running()` interact with the actual pyhackrf2 device. The full integration is exercised end-to-end when the node runs in a ROS2 environment with Redis.

## Self-Check: PASSED

Files exist:
- `hackrf_ros/hackrf_node.py` — FOUND
- `hackrf_ros/mayhem_serial.py` — FOUND
- `test/test_hackrf_node_redis.py` — FOUND

Commits exist:
- `5489724` — `feat(03-02): add _active_app tracking to MayhemSerial.appstart()`
- `3f247a2` — `test(03-02): add failing tests for HackRFNode RedisBridge integration`
- `df28f56` — `feat(03-02): wire RedisBridge into HackRFNode`

All 44 tests pass: CONFIRMED
