---
phase: 06-foundation-hardening
verified: 2026-03-30T23:00:00Z
status: gaps_found
score: 11/11 must-haves verified (code complete); 1 documentation gap
re_verification: false
gaps:
  - truth: "REQUIREMENTS.md checkboxes and traceability table reflect completed phase 06 work"
    status: failed
    reason: "ERR-04, ERR-05, REL-03, TXS-01, TXS-03 are marked '[ ]' (pending) and 'Pending' in traceability table despite being fully implemented and tested"
    artifacts:
      - path: ".planning/REQUIREMENTS.md"
        issue: "5 requirements still marked [ ] and Pending: ERR-04, ERR-05, REL-03, TXS-01, TXS-03"
    missing:
      - "Mark ERR-04, ERR-05, REL-03, TXS-01, TXS-03 as [x] in v2 Requirements section"
      - "Update traceability table for ERR-04, ERR-05, REL-03, TXS-01, TXS-03 from 'Pending' to 'Complete'"
human_verification:
  - test: "Redis restart survival — BridgeNode reconnect in a running container"
    expected: "After killing and restarting the redis-server process, BridgeNode reconnects within 1-30 seconds and /hackrf/iq resumes publishing"
    why_human: "Requires a live Redis and ROS2 runtime; cannot verify connection lifecycle with static analysis"
  - test: "Antenna confirmation service end-to-end"
    expected: "ros2 service call /hackrf/confirm_antenna std_srvs/srv/Trigger '{}' returns success=True and hackrf:tx:antenna_confirmed in Redis equals '1'"
    why_human: "Requires a running BridgeNode with ROS2 executor and Redis — not executable statically"
---

# Phase 06: Foundation Hardening — Verification Report

**Phase Goal:** All error paths raise typed exceptions with clear semantics, all inputs are validated before touching hardware, the Redis bridge survives a Redis restart, every IQ entry carries a sequence number for gap detection, TX can be dry-run validated without emitting RF, and the antenna confirmation is reachable from ROS2.

**Verified:** 2026-03-30T23:00:00Z
**Status:** gaps_found (1 documentation gap; all code is complete and correct)
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | pymayhem domain methods raise MayhemCommandError on firmware error instead of returning False | VERIFIED | `radio.py` setfreq, `system.py` appstart/reboot/rtcset all raise MayhemCommandError; confirmed by pytest (70 pass) |
| 2 | pymayhem domain methods raise ValueError on invalid input (wrong type, out-of-range) | VERIFIED | setfreq validates `isinstance(freq_hz, int)` and range `[1e6, 6e9]`; appstart/rtcset validate non-empty string; behavioral test passed |
| 3 | hackrf_driver has a unified HackRFError hierarchy with HackRFConfigError, HackRFDeviceError, and reparented TX exceptions | VERIFIED | `hackrf_driver/hackrf_driver/exceptions.py` contains all 7 classes; `issubclass(TXBlockedError, HackRFError)` confirmed |
| 4 | Existing callers catching TXBlockedError etc. by name still work (reparenting is compatible) | VERIFIED | TX exceptions re-exported from `tx_controller.py` via `from hackrf_driver.exceptions import ... # noqa: F401`; all 70 tests pass |
| 5 | Passing an out-of-range parameter value to _update_param raises HackRFConfigError instead of silently returning | VERIFIED | `driver.py` line 359: `raise HackRFConfigError(...)` replaces old `warning + return` pattern |
| 6 | A pymayhem or hackrf_driver exception in _dispatch_command is caught and published to hackrf:cmd:last_error Redis hash | VERIFIED | `redis_bridge.py` lines 302-311: two-level except `(MayhemError, HackRFError)` then `Exception`; `_publish_command_error` writes to `hackrf:cmd:last_error` |
| 7 | Every IQ XADD entry contains a seq field with epoch:counter format | VERIFIED | `redis_bridge.py` lines 242-246: `seq = f'{self._driver_epoch}:{self._seq_counter}'.encode()` included in XADD dict as `b'seq': seq` |
| 8 | BridgeNode survives a Redis restart and automatically reconnects with exponential backoff (1s-30s) | VERIFIED (static) | `bridge_node.py`: outer reconnect loop with `_MIN_BACKOFF=1.0`, `_MAX_BACKOFF=30.0`, `_stop_event.wait(backoff)`, `pubsub.close()` in finally; human test needed for runtime |
| 9 | After Redis reconnect, BridgeNode resubscribes to hackrf:iq:notify Pub/Sub and resumes publishing /hackrf/iq | VERIFIED (static) | `_run_pubsub_loop` extracted; `pubsub.subscribe('hackrf:iq:notify')` inside the outer loop so it re-runs on each reconnect |
| 10 | A ROS2 service call to /hackrf/confirm_antenna sets the Redis key hackrf:tx:antenna_confirmed to b'1' | VERIFIED (static) | `bridge_services.py` line 94: `redis_client.set('hackrf:tx:antenna_confirmed', b'1')` inside `_make_antenna_confirm_handler`; registered at line 143 |
| 11 | Importing hackrf_ros.hackrf_node emits a DeprecationWarning pointing to hackrf_driver + BridgeNode | VERIFIED | `hackrf_node.py` lines 33-39: `warnings.warn(..., DeprecationWarning, stacklevel=2)` at module level after imports |
| 12 | validate_tx() checks all four TX guards without consuming the auth token | VERIFIED | `tx_controller.py` lines 286-347: all four guards in correct order; guard 4 uses `self._redis.get(self.AUTH_KEY)` (not GETDEL); confirmed by code inspection |
| 13 | TXController periodically re-reads the antenna confirmation key from Redis every 30 seconds | VERIFIED | `_schedule_antenna_reread` and `_reread_antenna` methods present; `_antenna_reread_interval = 30.0`; timer cancelled in `stop_tx()` at line 356 |
| 14 | REQUIREMENTS.md accurately reflects phase 06 completion status | FAILED | 5 requirements (ERR-04, ERR-05, REL-03, TXS-01, TXS-03) still marked `[ ]` and 'Pending' in the traceability table |

**Score:** 13/14 truths verified (all code truths pass; 1 documentation truth fails)

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `pymayhem/pymayhem/exceptions.py` | MayhemError hierarchy (4 classes) | VERIFIED | All 4 classes present: MayhemError, MayhemCommandError, MayhemParseError, MayhemTimeoutError |
| `hackrf_driver/hackrf_driver/exceptions.py` | HackRFError hierarchy (7 classes) | VERIFIED | All 7 classes present with correct inheritance under HackRFError |
| `pymayhem/pymayhem/domains/radio.py` | setfreq raises on error/invalid input | VERIFIED | `-> None`, raises ValueError + MayhemCommandError, imports from exceptions |
| `pymayhem/pymayhem/domains/system.py` | appstart/reboot/rtcset raise on error | VERIFIED | All 3 methods raise MayhemCommandError; appstart_with_reconnect keeps `-> bool` |
| `hackrf_driver/hackrf_driver/tx_controller.py` | TX exceptions reparented; validate_tx; antenna timer | VERIFIED | Exceptions imported from exceptions.py; validate_tx() present; _reread_antenna + _schedule_antenna_reread present |
| `hackrf_driver/hackrf_driver/driver.py` | _update_param raises HackRFConfigError; _driver_epoch | VERIFIED | raise at line 359; `self._driver_epoch = int(time.time())` at line 107; passed to RedisBridge |
| `hackrf_driver/hackrf_driver/redis_bridge.py` | _dispatch_command boundary; _seq_counter; _publish_command_error | VERIFIED | All three present: two-level except, seq build at line 242, _publish_command_error at line 313 |
| `hackrf_ros/bridge_node.py` | Reconnect loop with backoff; _run_pubsub_loop | VERIFIED | _MIN_BACKOFF/MAX_BACKOFF constants; outer while loop; _run_pubsub_loop method extracted; pubsub.close() in finally |
| `hackrf_ros/bridge_services.py` | _make_antenna_confirm_handler; /hackrf/confirm_antenna | VERIFIED | Factory at line 90; registered at line 140-145 |
| `hackrf_ros/hackrf_node.py` | DeprecationWarning at import | VERIFIED | warnings.warn at module level lines 33-39 |
| `.planning/REQUIREMENTS.md` | Phase 06 requirements marked complete | FAILED | ERR-04, ERR-05, REL-03, TXS-01, TXS-03 still `[ ]` / Pending |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `pymayhem/domains/radio.py` | `pymayhem/exceptions.py` | `from pymayhem.exceptions import MayhemCommandError` | WIRED | Import at line 6; `raise MayhemCommandError` at line 53 |
| `hackrf_driver/tx_controller.py` | `hackrf_driver/exceptions.py` | `from hackrf_driver.exceptions import ...` | WIRED | Import at lines 24-29 (re-exports via noqa:F401) |
| `hackrf_driver/driver.py` | `hackrf_driver/exceptions.py` | `from hackrf_driver.exceptions import HackRFConfigError` | WIRED | Import at line 27; raise at line 359 |
| `hackrf_driver/redis_bridge.py` | `hackrf_driver/exceptions.py` | `from hackrf_driver.exceptions import HackRFError` | WIRED | Import at line 22; used in except at line 302 |
| `hackrf_driver/redis_bridge.py` | `pymayhem/exceptions.py` | `from pymayhem.exceptions import MayhemError` | WIRED | Import at line 24; used in except at line 302 |
| `hackrf_ros/bridge_node.py` | redis pubsub | `pubsub.subscribe('hackrf:iq:notify')` in reconnect loop | WIRED | At line 127 inside outer reconnect while loop |
| `hackrf_ros/bridge_services.py` | redis | `redis_client.set('hackrf:tx:antenna_confirmed', b'1')` | WIRED | In `_make_antenna_confirm_handler` closure at line 94 |
| `hackrf_driver/tx_controller.py` | redis (validate_tx) | `self._redis.get(self.AUTH_KEY)` in validate_tx | WIRED | Line 330 — GET not GETDEL confirmed |
| `hackrf_driver/tx_controller.py` | redis (_reread_antenna) | `self._redis.get(self.ANTENNA_KEY)` in _reread_antenna | WIRED | Line 192 inside `_reread_antenna` method |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `bridge_node.py` | `arr` (IQ float32) | `self._redis.xrevrange('hackrf:iq:stream', '+', '-', count=1)` | Real Redis stream read | FLOWING |
| `redis_bridge.py` | `b'seq'` in XADD | `f'{self._driver_epoch}:{self._seq_counter}'.encode()` | Epoch + incrementing counter | FLOWING |
| `bridge_services.py` | antenna_confirmed | `redis_client.set('hackrf:tx:antenna_confirmed', b'1')` | Direct Redis write on service call | FLOWING |
| `tx_controller.py` validate_tx | `val` (auth token check) | `self._redis.get(self.AUTH_KEY)` | Real Redis GET | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| pymayhem exception hierarchy importable | `python3 -c "from pymayhem.exceptions import MayhemError, MayhemCommandError; assert issubclass(MayhemCommandError, MayhemError)"` | Pass | PASS |
| hackrf_driver exception hierarchy importable | `python3 -c "from hackrf_driver.exceptions import HackRFError, TXBlockedError; assert issubclass(TXBlockedError, HackRFError)"` | Pass | PASS |
| setfreq raises ValueError on non-int | `python3 -c "RadioDomain(lambda _: []).setfreq('bad')"` | ValueError raised | PASS |
| setfreq raises MayhemCommandError on firmware error | `python3 -c "RadioDomain(lambda _: ['error: bad']).setfreq(433920000)"` | MayhemCommandError raised | PASS |
| validate_tx uses GET not GETDEL | Code inspection of validate_tx executable lines | `self._redis.get(AUTH_KEY)` used; no GETDEL call | PASS |
| _antenna_reread_timer cancelled in stop_tx | Code inspection | `self._antenna_reread_timer.cancel()` at line 356 before `_tx_lock` | PASS |
| All 70 unit tests pass | `python3 -m pytest hackrf_driver/tests/ pymayhem/tests/ -x -q` | 70 passed in 3.75s | PASS |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| ERR-01 | 06-01 | pymayhem raises typed exceptions instead of bool | SATISFIED | radio.py + system.py raise MayhemCommandError; REQUIREMENTS.md marked [x] |
| ERR-02 | 06-01 | hackrf_driver raises typed HackRFError hierarchy | SATISFIED | exceptions.py 7-class hierarchy; REQUIREMENTS.md marked [x] |
| ERR-03 | 06-01 | pymayhem public methods validate input, raise ValueError | SATISFIED | setfreq, appstart, rtcset, appstart_with_reconnect all validate; REQUIREMENTS.md marked [x] |
| ERR-04 | 06-02 | hackrf_driver config changes validate against PARAM_RANGES | SATISFIED (code) — DOCUMENTATION GAP | `raise HackRFConfigError` at driver.py line 359; but REQUIREMENTS.md still `[ ]` |
| ERR-05 | 06-02 | Exception dispatch boundary in redis_bridge with structured Redis error | SATISFIED (code) — DOCUMENTATION GAP | Two-level except + _publish_command_error at redis_bridge.py lines 302-334; but REQUIREMENTS.md still `[ ]` |
| REL-02 | 06-03 | BridgeNode survives Redis restart with exponential backoff | SATISFIED | Outer reconnect loop in bridge_node.py; REQUIREMENTS.md marked [x] |
| REL-03 | 06-02 | Every IQ XADD entry includes monotonic sequence number | SATISFIED (code) — DOCUMENTATION GAP | seq field at redis_bridge.py line 246; but REQUIREMENTS.md still `[ ]` |
| TXS-01 | 06-04 | validate_tx() checks all guards without consuming auth token | SATISFIED (code) — DOCUMENTATION GAP | validate_tx() method at tx_controller.py line 286; GET not GETDEL; but REQUIREMENTS.md still `[ ]` |
| TXS-02 | 06-03 | BridgeNode exposes /hackrf/confirm_antenna ROS2 service | SATISFIED | bridge_services.py line 143; REQUIREMENTS.md marked [x] |
| TXS-03 | 06-04 | TXController periodically re-reads antenna confirmation key | SATISFIED (code) — DOCUMENTATION GAP | _reread_antenna + _schedule_antenna_reread; 30s timer; but REQUIREMENTS.md still `[ ]` |
| LEG-01 | 06-03 | HackRFNode marked deprecated with warning | SATISFIED | warnings.warn at hackrf_node.py line 33; REQUIREMENTS.md marked [x] |

**Documentation gap note:** ERR-04, ERR-05, REL-03, TXS-01, TXS-03 are fully implemented and tested, but REQUIREMENTS.md was not updated after the phase completed. The checkboxes and traceability table entries for these 5 requirements still read `[ ]` and `Pending`. This is a documentation-only gap — the code satisfies every requirement.

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `hackrf_driver/driver.py` | 34,37 | `_PYHACKRF2_AVAILABLE = True/False` | Info | Availability flag for optional hardware library; not a stub — intentional graceful degradation |

No blockers or warnings found. The `_PYHACKRF2_AVAILABLE` flag is a legitimate optional-import pattern, not a stub.

---

### Human Verification Required

#### 1. Redis Restart Survival

**Test:** In a running container with BridgeNode active, stop redis-server (`sudo service redis-server stop`), wait 5 seconds, then restart it (`sudo service redis-server start`). Monitor BridgeNode logs.

**Expected:** BridgeNode logs warn about Redis error with backoff delay (e.g., "Retrying in 1s"), then reconnects and logs "subscribed to hackrf:iq:notify". /hackrf/iq topic resumes publishing after reconnect.

**Why human:** Requires live Redis and ROS2 executor; cannot test connection lifecycle statically.

#### 2. Antenna Confirmation Service End-to-End

**Test:** With BridgeNode running: `ros2 service call /hackrf/confirm_antenna std_srvs/srv/Trigger '{}'` then check `redis-cli GET hackrf:tx:antenna_confirmed`.

**Expected:** Service returns `success: True, message: 'Antenna confirmed - TX guard cleared'`. Redis key returns `"1"`.

**Why human:** Requires a running ROS2 node with registered services and live Redis connection.

---

### Gaps Summary

**All phase 06 code is complete and correct.** Every requirement (ERR-01 through TXS-03, REL-02, REL-03, LEG-01) is implemented in the codebase and verified by either code inspection, behavioral tests, or the full pytest suite (70 tests pass).

**The single gap is a documentation update:** REQUIREMENTS.md was not updated after plans 02 and 04 completed. Five requirements (ERR-04, ERR-05, REL-03, TXS-01, TXS-03) still show `[ ]` and `Pending` in the traceability table despite having verified implementations. This gap must be closed before the phase can be considered fully documented.

The implementations for these 5 requirements are:
- **ERR-04**: `driver.py` line 359 — `raise HackRFConfigError` in `_update_param`
- **ERR-05**: `redis_bridge.py` lines 302-334 — two-level exception boundary + `_publish_command_error`
- **REL-03**: `redis_bridge.py` lines 242-246 — `b'seq': epoch:counter` in every XADD call
- **TXS-01**: `tx_controller.py` lines 286-347 — `validate_tx()` with GET not GETDEL
- **TXS-03**: `tx_controller.py` lines 179-206 — `_schedule_antenna_reread`/`_reread_antenna` 30s timer chain

---

_Verified: 2026-03-30T23:00:00Z_
_Verifier: Claude (gsd-verifier)_
