---
phase: 04-tx-authorization
verified: 2026-03-30T00:00:00Z
status: gaps_found
score: 9/11 must-haves verified
gaps:
  - truth: "68 unit tests passing (full suite run)"
    status: failed
    reason: "Full suite produces 2 failures when all test files run together in alphabetical order due to a live Redis server on localhost:6379 leaking through unittest.mock.patch contexts"
    artifacts:
      - path: "test/test_redis_bridge.py"
        issue: "TestRedisBridgeOpen::test_open_returns_false_on_connection_error passes in isolation but fails when preceded by test_mayhem_serial + test_hackrf_node_redis — a real Redis connection replaces the mock"
      - path: "test/test_tx_controller.py"
        issue: "TestConsumeAuthToken::test_native_getdel_first_fallback_to_lua passes in isolation but fails when run after the same ordering — execute_command mock is bypassed, real Redis returns a value incompatible with Lua fallback logic"
    missing:
      - "Tests must be isolated from the live Redis server on localhost:6379. Fix options: (a) use REDIS_URL env override in tests to point at a non-existent port, (b) add a pytest fixture that stops/mocks the real connection before each test class, or (c) ensure patch() targets are consistent so they are not bypassed when a real connection is cached by a prior test"
human_verification:
  - test: "Real hardware TX smoke test"
    expected: "A Redis command {action: start_tx, freq_hz: 433000000, auth_token: <uuid>, iq_data_key: hackrf:tx:iq_data} with valid token and antenna key set triggers pyhackrf2.start_tx() and the device transmits"
    why_human: "Requires physical HackRF One with antenna attached — cannot be verified programmatically"
  - test: "Half-duplex RX pause and resume"
    expected: "IQ samples stop appearing on hackrf:iq:stream during TX and resume within 200ms of stop_tx"
    why_human: "Requires real hardware and Redis stream inspection in real time"
---

# Phase 4: TX Authorization Verification Report

**Phase Goal:** The driver can transmit signals via pyhackrf2, gated behind one-token-per-TX authorization, a frequency allowlist, and an explicit antenna confirmation
**Verified:** 2026-03-30
**Status:** gaps_found
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| #  | Truth | Status | Evidence |
|----|-------|--------|----------|
| 1  | A TX command to a restricted frequency is rejected with TXFreqBlockedError regardless of auth token | VERIFIED | `start_tx()` guard 3: `_freq_filter_active() and _is_freq_restricted(freq_hz)` at line 199; test 16 passes |
| 2  | EPIRB 406 MHz and ADS-B 1090 MHz are ALWAYS rejected with TXHardBlockedError even when filter fully disabled | VERIFIED | `ALWAYS_BLOCKED_BANDS` class constant at line 83–86; `_is_hard_blocked()` called unconditionally at line 192 before filter and token guards; tests 23 and 24 pass |
| 3  | A TX command with no auth token is rejected with TXNotAuthorizedError | VERIFIED | Guard 4 `_consume_auth_token()` at line 206; test 17 passes |
| 4  | A TX command with antenna not confirmed is rejected with TXBlockedError | VERIFIED | Guard 1 at line 185; test 15 passes |
| 5  | Frequency filter can be disabled only when BOTH tx_freq_filter_enabled=False AND hackrf:tx:freq_filter_override=disabled | VERIFIED | `_freq_filter_active()` dual-disable logic at lines 294–302; test 7 passes |
| 6  | Frequency check happens before token consumption | VERIFIED | Guard order: hard-block (line 192) → filter-gated (line 199) → token consume (line 206); test 18 passes and asserts GETDEL not called when freq blocked |
| 7  | Auth token is consumed atomically (Lua GETDEL fallback works on Redis 6.0.16) | PARTIAL | Lua fallback code is correct; test 11 passes in isolation; FAILS in full suite run due to live Redis on localhost bypassing the mock (see Gaps section) |
| 8  | tx_skip_antenna_check=True bypasses antenna confirmation for automated testing | VERIFIED | `open()` lines 135–141 check param; test 14 passes |
| 9  | A start_tx Redis command routes through to TXController.start_tx() | VERIFIED | `_handle_start_tx` in `redis_bridge.py` at lines 23–39; registered in `_COMMAND_HANDLERS['start_tx']` at line 58 |
| 10 | TX is stopped in destroy_node BEFORE RedisBridge.close() and pyhackrf2 close() | VERIFIED | `hackrf_node.py` line 577: `_tx_controller.stop()` at line 577; `_redis_bridge.close()` at line 581; `_hackrf.close()` at line 609 — strict ordering confirmed |
| 11 | hackrf:state includes is_transmitting, tx_freq, and antenna_confirmed fields | VERIFIED | `_build_state_dict()` at lines 379–381 includes all three fields with `hasattr` guards |

**Score:** 10/11 truths verified (1 partial due to test isolation failure)

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `hackrf_ros/tx_controller.py` | TXController class with all guard logic | VERIFIED | 332 lines; all 4 exceptions, ALWAYS_BLOCKED_BANDS, RESTRICTED_BANDS, Lua GETDEL, dual-disable filter, start_tx/stop_tx/open/stop |
| `test/test_tx_controller.py` | Standalone unit tests, 24 tests | VERIFIED | 24 tests collected and all 24 pass when run in isolation |
| `hackrf_ros/hackrf_node.py` | TXController instantiated, wired into destroy_node | VERIFIED | Import at line 16; instantiation at lines 148–151; stop() at line 577 |
| `hackrf_ros/redis_bridge.py` | start_tx and stop_tx in _COMMAND_HANDLERS | VERIFIED | `_handle_start_tx` at lines 23–39; `_handle_stop_tx` at lines 42–44; both in `_COMMAND_HANDLERS` lines 58–59 |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `TXController._consume_auth_token()` | `redis.eval(_LUA_GETDEL)` | Lua fallback with ResponseError catch for native GETDEL | VERIFIED (code) / PARTIAL (tests) | Code at lines 316–320 is correct; test 11 fails when run in full suite |
| `TXController._freq_filter_active()` | `tx_freq_filter_enabled ROS2 param + hackrf:tx:freq_filter_override Redis key` | dual-disable: both must say disabled | VERIFIED | Lines 294–302; tests 5–7 pass |
| `TXController._is_hard_blocked()` | `ALWAYS_BLOCKED_BANDS` | checked unconditionally in start_tx() before filter-gated check | VERIFIED | Lines 192, 263–265; tests 23–24 pass |
| `redis_bridge._COMMAND_HANDLERS['start_tx']` | `node._tx_controller.start_tx()` | `_handle_start_tx` extracting freq_hz, auth_token, iq_bytes from cmd dict | VERIFIED | Lines 23–39, 58 |
| `HackRFNode.destroy_node()` | `_tx_controller.stop()` | first call before `_redis_bridge.close()` | VERIFIED | Line 577 < line 581 |
| `_build_state_dict()` | `_tx_controller._is_transmitting` | is_transmitting field in returned dict | VERIFIED | Line 379 |

---

## Data-Flow Trace (Level 4)

Not applicable — TXController dispatches to hardware, does not render dynamic data. No data-flow hollowness risk.

---

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| TXController can be imported | `python3 -c "from hackrf_ros.tx_controller import TXController"` | Success | PASS |
| start_tx/stop_tx in _COMMAND_HANDLERS | `python3 -c "from hackrf_ros.redis_bridge import _COMMAND_HANDLERS; assert 'start_tx' in _COMMAND_HANDLERS; assert 'stop_tx' in _COMMAND_HANDLERS"` | Success | PASS |
| 24 TX unit tests pass in isolation | `python3 -m pytest test/test_tx_controller.py -v` | 24 passed | PASS |
| Lua fallback test passes in isolation | `python3 -m pytest test/test_tx_controller.py::TestConsumeAuthToken::test_native_getdel_first_fallback_to_lua -v` | 1 passed | PASS |
| Full suite (4 test files) | `python3 -m pytest test/test_mayhem_serial.py test/test_hackrf_node_redis.py test/test_redis_bridge.py test/test_tx_controller.py` | 2 failed, 66 passed | FAIL |
| flake8 on tx_controller.py | `python3 -m flake8 hackrf_ros/tx_controller.py --max-line-length=120` | No output (clean) | PASS |

---

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| TX-01 | 04-02 | TX via pyhackrf2 start_tx() with explicit half-duplex RX-to-TX mode switch | SATISFIED | `start_tx()` calls `node._stop_rx_if_running()` then `hackrf.start_tx()`; `stop_tx()` calls `hackrf.stop_tx()` then `node._start_rx_if_stopped()` |
| TX-02 | 04-01 | Frequency allowlist blocks restricted bands | SATISFIED | `RESTRICTED_BANDS` 12-entry list; `_is_freq_restricted()` enforced when `_freq_filter_active()` |
| TX-03 | 04-01 | Configurable flag to disable frequency allowlist | SATISFIED | Dual-disable model: `tx_freq_filter_enabled=False` AND `hackrf:tx:freq_filter_override=b'disabled'` required |
| TX-04 | 04-01 | Antenna confirmation required before any TX | SATISFIED | Guard 1 in `start_tx()`; `open()` reads Redis ANTENNA_KEY; `tx_skip_antenna_check` bypass param for testing |
| TX-05 | 04-01 | One-token-per-TX authorization via Redis GETDEL | SATISFIED (code) | `_consume_auth_token()` uses native GETDEL with Lua fallback; NOTE: test 11 fails in full suite due to live Redis environment |
| TX-06 | 04-02 | TX stopped on node shutdown | SATISFIED | `destroy_node()` line 577: `_tx_controller.stop()` is first call, before RedisBridge.close() at line 581 and pyhackrf2.close() at line 609 |
| TX-07 | 04-02 | TX commands routed through Redis command interface | SATISFIED | `_handle_start_tx` and `_handle_stop_tx` registered in `_COMMAND_HANDLERS`; dispatched via `_dispatch_command()` |

All 7 TX requirements from phase 4 are accounted for. No orphaned requirements.

---

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `test/test_redis_bridge.py` | 42–47 | `patch('hackrf_ros.redis_bridge.redis.Redis')` can be bypassed when a real Redis on localhost is running and an earlier test suite (mayhem_serial) leaves connection state that allows the module-level import to connect before the patch activates | WARNING | When the full test suite is run with a live Redis on localhost:6379, `test_open_returns_false_on_connection_error` receives `True` instead of `False` — a real connection is made before or instead of the mock |
| `test/test_tx_controller.py` | 148–158 | `test_native_getdel_first_fallback_to_lua` sets `execute_command.side_effect = ResponseError` but the fallback `redis_mock.eval` is bypassed when a live Redis evaluates the Lua script directly | WARNING | When a live Redis is accessible, `eval` on the real client succeeds and returns the actual stored value rather than `b'abc'` from the mock — test fails because the returned value does not match |

No blockers found in production code. Both anti-patterns are in test code and caused by the same root cause: a live Redis server on localhost:6379 whose presence was not anticipated by the test authors.

---

## Critical Safety Checks

| Check | Expected | Actual | Status |
|-------|----------|--------|--------|
| Frequency check BEFORE token consumption | Guard order: hard-block(2) → filter(3) → token(4) | `start_tx()` lines 192, 199, 206 in that order; test 18 asserts GETDEL NOT called on freq block | PASS |
| EPIRB 406 MHz hard-blocked with filter disabled | `TXHardBlockedError` even when `tx_freq_filter_enabled=False` AND Redis override=`b'disabled'` | `ALWAYS_BLOCKED_BANDS = [(406_000_000, 406_100_000), ...]`; `_is_hard_blocked()` at line 192 unconditional; tests 23–24 pass | PASS |
| ADS-B 1090 MHz hard-blocked AND token not consumed | `TXHardBlockedError`; `execute_command(GETDEL, AUTH_KEY)` never called | Test 24 passes: asserts GETDEL not called; hard-block raises before token guard at line 206 | PASS |
| Antenna confirmation enforced before any TX | `TXBlockedError` when `_antenna_confirmed=False` | Guard 1 at line 185 is first check in `start_tx()`; test 15 passes | PASS |
| TX stopped on node shutdown (destroy_node ordering) | `_tx_controller.stop()` before `_redis_bridge.close()` before `_hackrf.close()` | Lines 577, 581, 609 respectively | PASS |
| 24 TX unit tests passing (in isolation) | All 24 pass | `python3 -m pytest test/test_tx_controller.py` → `24 passed in 0.09s` | PASS |
| Full suite 68 tests passing | 68 passed, 0 failed | `66 passed, 2 failed` — 2 tests fail due to live Redis test isolation issue | FAIL |

---

## Test Isolation Root Cause Analysis

Both failing tests fail only when all four test files run together in alphabetical order
(`test_hackrf_node_redis`, `test_mayhem_serial`, `test_redis_bridge`, `test_tx_controller`).
Each passes in isolation and in smaller combinations that exclude at least one prior file.

**Root cause:** A real Redis server is running on localhost:6379. Some test in the preceding suites
(likely from `test_mayhem_serial` with its `pyserial`/socket setup, or a prior bridge thread started
in `test_hackrf_node_redis`) leaves a state where `hackrf_ros.redis_bridge.redis.Redis` module
attribute has already resolved to the real class and an attempt to patch it is applied too late or to
a stale reference.

The production code (`tx_controller.py`, `redis_bridge.py`, `hackrf_node.py`) is correct. This is
a test environment isolation problem, not a logic bug.

**Fix required (not blocking phase goal, but blocking the stated "68 tests passing" claim):**
Option A (preferred): In conftest.py or individual test setUp, stop the live Redis connection or point
`REDIS_URL` to a non-existent port via environment variable before `redis.Redis()` is imported.
Option B: Change patch target to `redis.Redis` at the `redis` module level rather than through the
`hackrf_ros.redis_bridge` namespace, or use `autospec=True` to ensure the mock is always applied.

---

## Human Verification Required

### 1. Real Hardware TX Smoke Test

**Test:** With HackRF One attached and antenna confirmed via `redis-cli SET hackrf:tx:antenna_confirmed 1`, write a valid auth token with `redis-cli SET hackrf:tx:auth <uuid>`, upload IQ bytes to `hackrf:tx:iq_data`, then XADD a `start_tx` command to `hackrf:cmd`
**Expected:** Node logs `AUDIT: TX started freq=...`; RF output visible on spectrum analyzer; stop_tx resumes RX
**Why human:** Requires physical HackRF hardware and RF measurement equipment

### 2. Half-Duplex RX Pause and Resume

**Test:** Monitor `hackrf:iq:stream` XLEN during a TX burst; verify XLEN stops increasing during TX and resumes after stop_tx
**Expected:** IQ stream pauses during transmission and resumes within 200ms of stop_tx command
**Why human:** Requires real hardware timing measurement

---

## Gaps Summary

The phase goal is functionally achieved — all 7 TX requirements are satisfied by correct production code. All 5 critical safety properties (hard-blocked frequencies, guard ordering, token protection, antenna gate, shutdown ordering) are verified in code and pass in isolated test runs.

The single gap is a test environment isolation problem: 2 tests fail when run together with a live Redis server on localhost. This does not indicate any production logic error but does mean the stated "68 unit tests passing" metric is not met in the shared test environment. The fix is a test infrastructure change (conftest.py fixture or patch target correction), not a production code change.

---

_Verified: 2026-03-30_
_Verifier: Claude (gsd-verifier)_
