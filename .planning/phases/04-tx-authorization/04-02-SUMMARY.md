---
phase: 04-tx-authorization
plan: "02"
subsystem: tx-integration
tags: [tx, authorization, redis, hackrf-node, wiring]
dependency_graph:
  requires: [04-01]
  provides: [TXController-wired, start_tx-handler, stop_tx-handler]
  affects: [hackrf_ros/hackrf_node.py, hackrf_ros/redis_bridge.py]
tech_stack:
  added: []
  patterns: [module-level handler functions, hasattr guard for graceful init failure, dual-disable filter via ROS param + Redis key]
key_files:
  created: []
  modified:
    - hackrf_ros/hackrf_node.py
    - hackrf_ros/redis_bridge.py
    - hackrf_ros/tx_controller.py
decisions:
  - "TXController instantiated after RedisBridge.open() so _redis_bridge._redis is available for antenna key read"
  - "TX stop added as first call in destroy_node before RedisBridge.close() (TX-06 shutdown order)"
  - "_last_tx_freq attribute added to TXController for state dict tracking without changing guard logic"
  - "_handle_start_tx as module-level function (not lambda) to allow Redis IQ key fetch via node._redis_bridge._redis"
  - "TXHardBlockedError not caught in _handle_start_tx — falls through to RedisBridge._dispatch_command generic error handler (it IS a programming error / unconditional block)"
metrics:
  duration: 8min
  completed_date: "2026-03-30"
  tasks: 2
  files: 3
---

# Phase 04 Plan 02: TX Integration into HackRFNode and RedisBridge Summary

**One-liner:** TXController wired into HackRFNode (init, destroy_node, state dict) and RedisBridge extended with start_tx/stop_tx command handlers that fetch IQ from Redis and dispatch through all 4 guard layers.

## What Was Built

**hackrf_ros/hackrf_node.py** — three integration points:

- **Import:** `from hackrf_ros.tx_controller import TXController, TXBlockedError, TXFreqBlockedError, TXNotAuthorizedError`
- **Parameters:** `tx_freq_filter_enabled` (bool, default True) and `tx_skip_antenna_check` (bool, default False) declared as ROS2 parameters
- **Init:** `self._tx_controller = TXController(self, self._redis_bridge._redis, self.get_logger())` instantiated after `redis_bridge.open()`; `open()` called to read antenna confirmation
- **destroy_node:** `_tx_controller.stop()` added as FIRST call before `_redis_bridge.close()` (TX-06 ordering)
- **State dict:** `is_transmitting`, `tx_freq`, `antenna_confirmed` fields added to `_build_state_dict()` using `hasattr` guards

**hackrf_ros/redis_bridge.py** — two new command handlers:

- **`_handle_start_tx(node, cmd)`:** Reads IQ bytes from Redis key (`iq_data_key`, default `hackrf:tx:iq_data`), extracts `freq_hz`, `auth_token`, `txvga_gain`; calls `node._tx_controller.start_tx()`; catches `TXBlockedError`, `TXFreqBlockedError`, `TXNotAuthorizedError` with `warning` level logging
- **`_handle_stop_tx(node, cmd)`:** Delegates to `node._tx_controller.stop_tx()`
- Both handlers registered in `_COMMAND_HANDLERS`

**hackrf_ros/tx_controller.py** — minor addition:

- `self._last_tx_freq: int = 0` added to `__init__` for state dict tracking
- `self._last_tx_freq = freq_hz` set inside `start_tx()` before hardware dispatch

## Tasks Completed

| Task | Description | Commit |
|------|-------------|--------|
| Task 1 | Integrate TXController into HackRFNode | 9c3eaa3 |
| Task 2 | Add start_tx and stop_tx to RedisBridge command handlers | f63c766 |

## Verification

```
$ python3 -m pytest test/test_redis_bridge.py test/test_mayhem_serial.py test/test_tx_controller.py -v
45 passed in 1.75s

$ python3 -c "from hackrf_ros.redis_bridge import _COMMAND_HANDLERS; assert 'start_tx' in _COMMAND_HANDLERS; assert 'stop_tx' in _COMMAND_HANDLERS; print('handlers ok')"
handlers ok

$ grep -n "_tx_controller.stop\|_redis_bridge.close" hackrf_ros/hackrf_node.py
577:            self._tx_controller.stop()
581:            self._redis_bridge.close()
```

## Decisions Made

1. **Handler placement (module-level functions, not lambdas):** `_handle_start_tx` needs to access `node._redis_bridge._redis` to fetch IQ bytes from Redis. A bare lambda cannot do this cleanly. Module-level named functions match the existing `MayhemSerial` pattern and keep the dispatch table clean.

2. **`_last_tx_freq` in TXController:** Added to `__init__` and set before hardware dispatch inside the `_tx_lock` section. This allows `_build_state_dict()` to report the last transmitted frequency without any risk of data races.

3. **`TXHardBlockedError` not caught in `_handle_start_tx`:** Hard-blocked bands (EPIRB/ADS-B) are unconditional law violations. This exception intentionally propagates to the `RedisBridge._dispatch_command` generic `except Exception` handler which logs it at `error` level — appropriate severity. Catching it at `warning` level would under-report the severity.

4. **`hasattr` guards in `_build_state_dict()`:** Consistent with existing Phase 3 pattern (`hasattr(self, '_redis_bridge')`). Allows graceful startup even if TXController init fails mid-`__init__`.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all TX pipeline behavior is fully implemented and wired.

## Self-Check: PASSED

- hackrf_ros/hackrf_node.py: EXISTS, TXController import + instantiation + destroy_node + state dict all present
- hackrf_ros/redis_bridge.py: EXISTS, start_tx + stop_tx in _COMMAND_HANDLERS
- hackrf_ros/tx_controller.py: EXISTS, _last_tx_freq attribute added
- Commits 9c3eaa3 and f63c766: FOUND in git log
- 45 tests passing
