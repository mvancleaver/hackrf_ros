---
phase: 04-tx-authorization
plan: "01"
subsystem: tx-controller
tags: [tdd, tx, authorization, safety, frequency-filter, lua, redis]
dependency_graph:
  requires: []
  provides: [TXController, TXBlockedError, TXFreqBlockedError, TXHardBlockedError, TXNotAuthorizedError]
  affects: [hackrf_ros/hackrf_node.py]
tech_stack:
  added: [threading.Lock for TX re-entry prevention, Lua GETDEL fallback]
  patterns: [TDD red-green, helper-class lifecycle, dual-disable filter model, atomic token consumption]
key_files:
  created:
    - hackrf_ros/tx_controller.py
    - test/test_tx_controller.py
  modified: []
decisions:
  - "ALWAYS_BLOCKED_BANDS checked unconditionally before filter-gated and token checks — EPIRB/ADS-B cannot be bypassed by any configuration"
  - "Guard order: antenna -> hard-block -> freq-filter -> auth token; freq rejected before token consumed"
  - "_tx_lock is threading.Lock (not RLock) — TX cannot re-enter itself"
  - "Lua GETDEL fallback (_LUA_GETDEL) supports Redis 6.0.16 via eval; native GETDEL tried first"
  - "txvga_gain default=0 (safest) — callers may override up to 47 dB"
metrics:
  duration: 3min
  completed_date: "2026-03-30"
  tasks: 2
  files: 2
---

# Phase 04 Plan 01: TXController Guard Logic Summary

**One-liner:** TXController with 4-level guard chain, Lua GETDEL fallback, dual-disable filter, and hard-blocked EPIRB/ADS-B bands that cannot be bypassed by any configuration.

## What Was Built

`hackrf_ros/tx_controller.py` — standalone helper class with:

- **4 custom exceptions:** TXBlockedError, TXFreqBlockedError, TXHardBlockedError, TXNotAuthorizedError
- **ALWAYS_BLOCKED_BANDS** class constant: EPIRB 406–406.1 MHz and ADS-B 1090 MHz checked unconditionally in `start_tx()` before filter-gated and token checks
- **RESTRICTED_BANDS** list: 12 filter-gated bands covering aviation VHF/DME/ATC, cellular LTE/AWS/PCS/Band41, GPS L1/L2/L5, emergency/public safety
- **start_tx()** guard order: antenna confirmed → hard-block → freq-filter active+restricted → auth token consumed → hardware dispatch
- **_consume_auth_token()**: tries native GETDEL first; catches ResponseError and falls back to Lua eval (`_LUA_GETDEL`) for Redis 6.0.16 compatibility
- **_freq_filter_active()**: dual-disable model — filter only off when BOTH `tx_freq_filter_enabled=False` ROS param AND `hackrf:tx:freq_filter_override=b'disabled'` Redis key agree
- **stop_tx()**: acquires `_tx_lock`, calls `hackrf.stop_tx()` with RuntimeError guard, sets `_is_transmitting=False`, calls `node._start_rx_if_stopped()`, logs info
- **open()**: reads `hackrf:tx:antenna_confirmed` Redis key; bypass with `tx_skip_antenna_check=True` (logs WARNING)
- **stop()**: alias for `stop_tx()`, safe to call from `destroy_node()`
- Audit logging on successful TX: `AUDIT: TX started freq={freq_hz} token_hint={token[:8]}... iq_len={n}`

`test/test_tx_controller.py` — 24 test methods covering all spec behaviors; no live Redis or HackRF required.

## Tasks Completed

| Task | Description | Commit |
|------|-------------|--------|
| TDD RED | Wrote 24 failing tests covering all spec behaviors | 5262069 |
| TDD GREEN | Implemented TXController; all 24 tests pass; flake8 clean | 9cb90c1 |

## Verification

```
$ python3 -m pytest test/test_tx_controller.py -v
24 passed in 0.09s

$ python3 -m flake8 hackrf_ros/tx_controller.py --max-line-length=120
(no output — clean)
```

## Decisions Made

1. **ALWAYS_BLOCKED_BANDS unconditional check**: Guard 2 (hard-block) runs before Guard 3 (filter-gated) and Guard 4 (token consumption). EPIRB/ADS-B rejection cannot be suppressed by any configuration. This matches plan requirement and `must_haves.truths`.

2. **Guard ordering enforces token protection**: Frequency checks (both hard-block and filter-gated) occur before `_consume_auth_token()`. A rejected TX request does NOT consume the caller's authorization token, allowing re-attempt at a valid frequency.

3. **Lua GETDEL fallback**: `_LUA_GETDEL` handles Redis 6.0.16 (Phase 3 environment) without requiring infrastructure upgrade. Native GETDEL is attempted first for efficiency on Redis 6.2+.

4. **threading.Lock (not RLock) for _tx_lock**: TX is not re-entrant. Using `Lock` prevents accidental nested TX starts that could leave hardware in undefined state.

5. **txvga_gain default=0**: Safest default — prevents accidental high-power TX. Callers may explicitly set up to 47 dB.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all behavior is fully implemented. Plan 02 will wire TXController into HackRFNode.

## Self-Check: PASSED
