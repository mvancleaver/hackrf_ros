---
phase: 05-portapack-boot-transition
plan: 02
subsystem: driver-lifecycle
tags: [python, lifecycle, serial, pyserial, portapack, pyhackrf2, ros2, diagnostics]

requires:
  - phase: 05-portapack-boot-transition
    provides: 05-01 Docker image with pyserial pre-installed and udev rules producing /dev/portapack symlink
provides:
  - PortapackTransitionResult enum classifying the four outcomes of the Mayhem->HackRF USB mode switch
  - _transition_portapack() helper that never raises (returns an enum) — callable from on_configure
  - _portapack_send_hackrf_command() sub-helper (115200 8N1 + 50 ms DTR settle + write b'hackrf\n')
  - _poll_hackrf_present() sub-helper using pyhackrf2.HackRF.enumerate() classmethod
  - Four non-dynamic ROS parameters: portapack_serial_device, portapack_enable_transition, portapack_reenum_timeout_s, portapack_open_retries
  - on_configure retry loop around pyhackrf2.HackRF(device_index=...) with 250 ms spacing
  - Diagnostics field last_portapack_transition on /diagnostics
  - pyserial>=3.5 declared in setup.py install_requires
affects: [05-03-PLAN (launch-file integration), 05-04-PLAN (unit tests), 05-VALIDATION]

tech-stack:
  added: [pyserial]
  patterns:
    - "Never-raise-from-lifecycle-transition (Pitfall 2): return FAILURE, preserve UNCONFIGURED for retry"
    - "Enum-return-value classification for soft error paths (SKIPPED/SUCCEEDED/RETRIED/FAILED)"
    - "Module-level Portapack constants with PORTAPACK_ prefix (D-13)"
    - "Two-attempt re-enumeration with single resend (D-09)"

key-files:
  created: []
  modified:
    - hackrf_ros/hackrf_lifecycle_node.py
    - config/hackrf_rx.yaml
    - setup.py

key-decisions:
  - "Use pyhackrf2.HackRF.enumerate() classmethod per RESEARCH.md correction (not the hallucinated list_devices)"
  - "Portapack parameters are non-dynamic: read once in on_configure (D-14) — not included in _param_callback path"
  - "Include 50 ms PORTAPACK_DTR_SETTLE_S before first serial write to mitigate Linux CDC-ACM DTR/RTS toggle (Pitfall 1, A3)"
  - "FAILED path maps to TransitionCallbackReturn.FAILURE (not raise) so node stays in UNCONFIGURED and is retryable"
  - "Stale-symlink fallthrough (D-08): when serial open fails, return SKIPPED (not FAILED) so pyhackrf2 open still gets a chance"

patterns-established:
  - "Lifecycle helper returns enum; caller maps to TransitionCallbackReturn — isolates error-classification from lifecycle control flow"
  - "Module constants centralize tunable knobs (default + unit comment); ROS parameters reference the constants to avoid literal duplication"

requirements-completed: [REQ-P5-05, REQ-P5-06, REQ-P5-07, REQ-P5-08, REQ-P5-09, REQ-P5-10, REQ-P5-11, REQ-P5-12, REQ-P5-13, REQ-P5-14, REQ-P5-15, REQ-P5-16, REQ-P5-A3]

duration: 2.5min
completed: 2026-04-19
---

# Phase 05 Plan 02: Implement Portapack Boot Transition Summary

**Portapack Mayhem->HackRF USB mode switch via pyserial write + pyhackrf2.HackRF.enumerate() poll, wired into on_configure with enum-based soft error handling and a 3x 250 ms libhackrf open retry loop.**

## Performance

- **Duration:** 2.5 min
- **Started:** 2026-04-19T05:07:57Z
- **Completed:** 2026-04-19T05:10:26Z
- **Tasks:** 3
- **Files modified:** 3

## Accomplishments

- Implemented `_transition_portapack()` helper that classifies Mayhem->HackRF boot transition outcomes via the `PortapackTransitionResult` enum — the helper never raises (Pitfall 2 mitigation)
- Added `_portapack_send_hackrf_command()` with the 50 ms DTR/RTS settle required on Linux CDC-ACM (Pitfall 1, A3) and `_poll_hackrf_present()` using the correct `pyhackrf2.HackRF.enumerate()` classmethod (RESEARCH.md correction)
- Wired the helper into `on_configure` strictly between `_declare_parameters()` and `pyhackrf2.HackRF(...)` (D-16), mapping `FAILED` -> `TransitionCallbackReturn.FAILURE` so the node remains retryable in `UNCONFIGURED`
- Added four non-dynamic ROS parameters (`portapack_serial_device`, `portapack_enable_transition`, `portapack_reenum_timeout_s`, `portapack_open_retries`) read once at configure time (D-12, D-14)
- Added a `pyhackrf2.HackRF(device_index=...)` retry loop with `PORTAPACK_OPEN_RETRY_DELAY_S = 0.25 s` spacing to absorb the USB kernel-claim race after re-enumeration (D-10)
- Extended `_diagnostics_callback` with `last_portapack_transition` for operator observability (D-15)
- Added `pyserial>=3.5` to `setup.py install_requires` so the ament_python package declares its runtime dep

## Task Commits

Each task was committed atomically with `--no-verify` (parallel-worktree protocol):

1. **Task 1: Add imports, module constants, enum, and __init__ state field** — `10c4e4a` (feat)
2. **Task 2: Implement _transition_portapack + sub-helpers + four ROS params + config overrides** — `e5ff366` (feat)
3. **Task 3: Wire _transition_portapack into on_configure + retry + diagnostics + setup.py dep** — `ad1e7d2` (feat)

## Files Created/Modified

- `hackrf_ros/hackrf_lifecycle_node.py` — +71 lines of Portapack boot-transition code, no deletions of pre-existing logic:
  - Imports: `enum`, `os`, `serial` added
  - Constants block (lines 78-86): `PORTAPACK_*` defaults, timings, command bytes
  - `class PortapackTransitionResult(enum.Enum)` (lines 89-94): four members
  - `__init__` state (line 123): `self._last_portapack_transition = PortapackTransitionResult.SKIPPED.value`
  - `on_configure` (lines 162-202): **the 05-04 AST test target — transition call at line 170, HackRF open at line 192, retry loop lines 190-197**
  - `_declare_parameters` (lines 414-429): four new `declare_parameter(...)` calls
  - `_portapack_send_hackrf_command` (lines 451-477): serial write helper
  - `_poll_hackrf_present` (lines 479-500): enumerate poll loop
  - `_transition_portapack` (lines 502-535): top-level helper
  - `_diagnostics_callback` (line 1079): `stat.add('last_portapack_transition', ...)`
- `config/hackrf_rx.yaml` — appended four commented-out `# portapack_*:` override examples
- `setup.py` — `install_requires` extended from `['setuptools', 'scipy>=1.11']` to `['setuptools', 'scipy>=1.11', 'pyserial>=3.5']`

### Helper Function Signatures (for 05-04 unit tests to reference)

```python
def _portapack_send_hackrf_command(self, device_path: str) -> bool: ...
def _poll_hackrf_present(self, timeout_s: float) -> bool: ...
def _transition_portapack(self) -> PortapackTransitionResult: ...
```

### on_configure Integration Line Ranges (for 05-04 AST test)

- `on_configure` definition: line 162
- `self._declare_parameters()` call: line 164
- `self._transition_portapack()` call: line 170 (first call site inside `on_configure`)
- `self._last_portapack_transition = transition_result.value`: line 171
- FAILED -> FAILURE branch: lines 172-176
- `pyhackrf2.HackRF(device_index=...)` constructor call: line 192
- Retry loop (`for attempt in range(retries):`): lines 190-197
- `self._apply_params_to_device()` (end of Phase 5 edit region, unchanged below): line 204

## Decisions Made

- **Non-dynamic Portapack parameters (D-14 reinforced):** Deliberately omitted from the `_param_callback` path — a live mid-run USB mode switch has no valid operational use case and would re-enter the kernel-claim race window.
- **`HackRF.enumerate()` over `list_devices`:** Used the real classmethod documented in 05-RESEARCH.md (correction to the CONTEXT.md speculation). The plan's `must_haves` enforces the correct API and forbids `list_devices` anywhere in the file — both are verified by grep.
- **Soft-fail on stale symlink (D-08):** When `serial.Serial(...)` raises (stale `/dev/portapack` symlink, permission denied, device unplugged mid-configure), `_portapack_send_hackrf_command` returns False and `_transition_portapack` returns `SKIPPED`, not `FAILED`. This lets pyhackrf2 still attempt to open an already-HackRF-mode device. The pyserial exception is logged at ERROR level so operators see what happened.
- **250 ms retry spacing (D-10):** The kernel-claim race between libusb re-enumeration completion and libhackrf's device-handle cache invalidation is in the low-hundreds-of-ms range on Jetson; 3 x 250 ms covers the worst observed case from 05-RESEARCH.md without pathological delay.
- **Retry-sleep-between-attempts-only:** The `if attempt + 1 < retries:` guard skips the sleep after the final attempt, so a FAILURE path reaches the operator ~750 ms sooner when retries are truly exhausted.

## Deviations from Plan

None — plan executed exactly as written. All three tasks landed with their exact verification text matching the plan's `<automated>` grep assertions and AST checks. The `<interfaces>` section of the plan listed specific line ranges; the actual post-edit line numbers drift slightly because Task 1's constants block adds 20 lines before the class, but all insertion anchors (`DISCONNECT_ERROR_TIMEOUT`, `self._clip_count = 0`, `antenna_z`, `stat.add('agc_last_action', ...)`, pyhackrf2 open block) matched exactly.

## Issues Encountered

- Worktree base-branch verification found HEAD at `e456ad3` (one commit ahead of the expected `8f83869`). Performed `git reset --soft 8f83869...` as directed by the `<worktree_branch_check>` protocol. The reset staged a phase-directory deletion (the worktree's tracked files differ across the two commits); restored via `git restore --staged .planning/ && git checkout -- .planning/phases/05-portapack-boot-transition/`. Net effect: clean base, all phase plan files intact. No task deviation, no code impact.

## Quality Gates

- **AST parse:** `python3 -c "import ast; ast.parse(...)"` exits 0 after every task
- **grep assertions:** All plan `<automated>` greps verified OK (Task 1: 12/12, Task 2: 15/15 plus absence of `list_devices`, Task 3: 7/7 plus ordering AST check)
- **AST ordering check:** `_declare_parameters()` (line 164) < `_transition_portapack()` (line 170) < `pyhackrf2.HackRF(...)` (line 192) inside `on_configure` — verified via `ast.walk`
- **Raise-absence check:** `ast.walk` of `_transition_portapack` body finds zero `ast.Raise` nodes (Pitfall 2 invariant enforced)
- **flake8 scan:** No new warnings in the 71 added lines. Pre-existing warnings elsewhere in the file (E241, E128, E741, E226) are out of scope (SCOPE BOUNDARY) and untouched.

## User Setup Required

None — no external service configuration required for this plan. The `/dev/portapack` udev symlink was provisioned by 05-01; operators optionally override parameters via `config/hackrf_rx.yaml` using the commented-out examples added in this plan.

## Next Phase Readiness

- **05-03 (launch integration):** Ready. `portapack_*` parameters are live in `_declare_parameters` and overrides are documented in `config/hackrf_rx.yaml`. The launch file can pass overrides directly without further driver changes.
- **05-04 (unit tests):** Ready. Helper signatures and exact on_configure line ranges documented above. `_portapack_send_hackrf_command`, `_poll_hackrf_present`, and `_transition_portapack` are mockable via `serial.Serial` and `pyhackrf2.HackRF.enumerate` patch points. The enum has exact string values (`'skipped'`, `'succeeded'`, `'retried'`, `'failed'`) that source-text tests can assert against.
- **No blockers.** No stubs, no TODOs, no deferred items.

## Self-Check: PASSED

- [x] `hackrf_ros/hackrf_lifecycle_node.py` FOUND (git-tracked at 10c4e4a + e5ff366 + ad1e7d2)
- [x] `config/hackrf_rx.yaml` FOUND (modified in e5ff366)
- [x] `setup.py` FOUND (modified in ad1e7d2)
- [x] Commit `10c4e4a` FOUND in git log
- [x] Commit `e5ff366` FOUND in git log
- [x] Commit `ad1e7d2` FOUND in git log

---
*Phase: 05-portapack-boot-transition*
*Completed: 2026-04-19*
