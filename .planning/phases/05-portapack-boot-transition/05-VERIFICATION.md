---
phase: 05-portapack-boot-transition
verified: 2026-04-18T00:00:00Z
status: human_needed
score: 6/6 must-haves verified (automated); 2 deferred HIL items await operator sign-off
overrides_applied: 0
re_verification:
  previous_status: none
  previous_score: n/a
  gaps_closed: []
  gaps_remaining: []
  regressions: []
human_verification:
  - test: "Hardware verification A1 — Portapack VID:PID capture"
    expected: "With a Portapack attached in Mayhem UI mode, `sudo scripts/install_portapack_udev.sh` reports `idVendor=1d50 idProduct=6018` and `A1 CONFIRMED`; `/dev/portapack` symlink appears on host; `docker exec hackrf_ros ls /dev/portapack` succeeds; `docker exec hackrf_ros python3 -c 'import serial; print(serial.__version__)'` prints >=3.5"
    why_human: "Requires physical Portapack hardware; agent environment has no device attached. 05-01-SUMMARY.md Task 4 was auto-approved under --auto chain and is documented under 'Deferred Verifications'."
  - test: "Hardware verification A2+A3 — end-to-end HIL run"
    expected: "`scripts/hil_portapack_check.sh` prints `[PASS] A1`, `[PASS] A2`, `[PASS] A3`; first-attempt success rate >=1/10 for A3; `hackrf\\n` terminator works (or `\\r\\n` fallback triggers and PORTAPACK_COMMAND is updated); /diagnostics topic shows `last_portapack_transition: succeeded` on a Portapack-equipped boot"
    why_human: "HIL script must run against live Portapack hardware; 05-04-SUMMARY.md Task 3 was auto-approved under --auto chain and is documented under 'Deferred Verifications'."
---

# Phase 5: Portapack Boot Transition Verification Report

**Phase Goal:** Lifecycle node reliably transitions the Portapack from Mayhem UI mode into HackRF USB-SDR mode during `on_configure`, so `pyhackrf2.HackRF(...)` succeeds on a Portapack-equipped device at first launch, with graceful fallback when the Portapack serial interface is absent (plain HackRF or already-transitioned device).

**Verified:** 2026-04-18
**Status:** human_needed — all automated checks pass; 2 HIL items deferred by design under --auto chain and require operator sign-off before phase can be called field-complete
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #   | Truth                                                                                                                                                          | Status             | Evidence                                                                                                                                                                                                                                                                                      |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Portapack in Mayhem UI + `/dev/portapack` present → `on_configure` transitions device and `pyhackrf2.HackRF(...)` succeeds with no operator action             | ✓ VERIFIED (code)  | `_transition_portapack()` at hackrf_lifecycle_node.py:502-534 implements D-05/D-06 open→write→close→poll→open sequence; wired into `on_configure` at line 170 before the `pyhackrf2.HackRF(...)` call at line 192. D-10 retry loop at lines 190-197 absorbs USB kernel-claim race. HIL deferred. |
| 2   | Plain HackRF One (no Portapack) → `on_configure` completes unchanged (no regression)                                                                           | ✓ VERIFIED         | D-07 skip path: `_transition_portapack` returns `SKIPPED` when `os.path.exists(device)` is False (line 512-513). Covered by `TestSkipPath::test_no_symlink_skips`, `test_disabled_skips`, `test_empty_device_skips` — all pass.                                                                |
| 3   | Stale `/dev/portapack` symlink (already transitioned) → serial failure logged, `on_configure` still succeeds                                                   | ✓ VERIFIED         | D-08 fallthrough: `_portapack_send_hackrf_command` catches `(serial.SerialException, OSError)`, logs ERROR (line 475-477), returns False; caller returns `SKIPPED` (not FAILED) at line 519-521. Covered by `TestSerialRaise` (2 tests) — all pass.                                            |
| 4   | Transition failure after retry → `on_configure` returns `TransitionCallbackReturn.FAILURE`, `/diagnostics` shows `last_portapack_transition: failed`           | ✓ VERIFIED         | D-09/D-11: second attempt at line 527-534 returns `FAILED`; `on_configure` writes `self._last_portapack_transition = transition_result.value` at line 171 THEN returns `FAILURE` at line 176. Covered by `TestResendPath::test_failed_when_both_attempts_exhausted` and `TestDiagField::test_value_after_failed` — all pass. |
| 5   | All four new ROS2 params declared with exact D-12 defaults, overridable via `config/hackrf_rx.yaml`                                                            | ✓ VERIFIED         | `declare_parameter` calls at lines 433, 437, 440, 443 reference module constants `PORTAPACK_DEFAULT_DEVICE='/dev/portapack'`, `PORTAPACK_DEFAULT_ENABLE=True`, `PORTAPACK_DEFAULT_REENUM_TIMEOUT_S=5.0`, `PORTAPACK_DEFAULT_OPEN_RETRIES=3` (lines 80-83). `config/hackrf_rx.yaml` lines 8-12 document commented-out overrides for all four. |
| 6   | `PROJECT.md` and `REQUIREMENTS.md` updated per D-00 bounded scope reversal                                                                                     | ✓ VERIFIED         | PROJECT.md:45-46 replaces the unconditional pymayhem exclusion with bounded scope (`Mayhem mode-switch command ... in-package since Phase 5 (bounded scope per CONTEXT.md D-00)` and `All other Mayhem firmware control ... still separate package concern (pymayhem)`). REQUIREMENTS.md §Portapack Boot Transition (line 67) + 19 traceability rows (lines 152-170) + coverage block updated to 51 requirements. |

**Score:** 6/6 truths verified via automated evidence

### Required Artifacts

| Artifact                                        | Expected                                                                                           | Status     | Details                                                                                                                                                                                                                               |
| ----------------------------------------------- | -------------------------------------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `hackrf_ros/hackrf_lifecycle_node.py`           | Module constants, enum, 4 params, `_transition_portapack` helper, on_configure retry, diag field   | ✓ VERIFIED | 1127 lines total; grep confirms all 8 `PORTAPACK_*` constants (lines 80-87), `PortapackTransitionResult` enum (line 90), 3 helpers (`_portapack_send_hackrf_command` 451, `_poll_hackrf_present` 479, `_transition_portapack` 502), diag field at line 1079. |
| `config/hackrf_rx.yaml`                         | Commented-out overrides for all 4 portapack_* params                                               | ✓ VERIFIED | Lines 8-12: 4 `# portapack_*` overrides with D-12 defaults.                                                                                                                                                                           |
| `setup.py`                                      | `pyserial>=3.5` in install_requires                                                                | ✓ VERIFIED | Line 27: `install_requires=['setuptools', 'scipy>=1.11', 'pyserial>=3.5']`.                                                                                                                                                           |
| `udev/99-portapack.rules`                       | VID:PID match + `SYMLINK+="portapack"`                                                             | ✓ VERIFIED | Line 15: `SUBSYSTEM=="tty", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="6018", SYMLINK+="portapack", MODE="0666", GROUP="dialout"`. A1 assumption comment at lines 6-8.                                                               |
| `docker-compose.yaml`                           | `device_cgroup_rules` for majors 189 + 166, `/dev:/dev` bind-mount                                 | ✓ VERIFIED | Lines 11-13: both cgroup rules. Line 20: `/dev:/dev`. `privileged: true` preserved at line 8 (defense-in-depth per D-03).                                                                                                            |
| `Dockerfile`                                    | `pyserial` in pip install                                                                          | ✓ VERIFIED | Line 24 pip install line includes `pyserial`.                                                                                                                                                                                        |
| `scripts/install_portapack_udev.sh`             | Host install with A1 VID:PID capture + udevadm reload/trigger                                      | ✓ VERIFIED | 65 lines, mode 0755, `bash -n` clean. Captures A1, fails (exit 2) on VID:PID mismatch.                                                                                                                                               |
| `scripts/hil_portapack_check.sh`                | A1/A2/A3 HIL validator with tee log                                                                | ✓ VERIFIED | 93 lines, mode 0755, `bash -n` clean. Covers A1 VID:PID, A2 `\n`/`\r\n` terminator, A3 10x configure first-attempt success rate.                                                                                                     |
| `test/test_portapack_transition.py`             | 17 test classes × ≥40 tests covering D-01..D-16 + A1/A3                                            | ✓ VERIFIED | 852 lines, 58 tests across 17 classes, **58 passed in 0.41s** (well under 30s VALIDATION.md cap).                                                                                                                                    |
| `.planning/PROJECT.md`                          | Bounded D-00 reversal language                                                                     | ✓ VERIFIED | Lines 45-46 replace blanket exclusion.                                                                                                                                                                                               |
| `.planning/REQUIREMENTS.md`                     | REQ-P5-00..16 + REQ-P5-A1/A3, 19 traceability rows, coverage updated to 51                         | ✓ VERIFIED | Line 67 section header; lines 72-90 requirement block with D-XX citations; lines 152-170 traceability (19 rows); lines 182, 186 coverage + timestamp.                                                                                 |

### Key Link Verification

| From                                      | To                                              | Via                                                            | Status     | Details                                                                                                                                                 |
| ----------------------------------------- | ----------------------------------------------- | -------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `on_configure`                            | `_transition_portapack()`                       | Direct call between `_declare_parameters()` and `pyhackrf2.HackRF(...)` | ✓ WIRED    | AST-verified order: `_declare_parameters()` (byte 132) < `_transition_portapack()` (byte 463) < `pyhackrf2.HackRF(` (byte 1468) inside `on_configure` body. |
| `_transition_portapack()`                 | `pyhackrf2.HackRF.enumerate()`                  | Presence poll loop in `_poll_hackrf_present`                   | ✓ WIRED    | Line 493 calls `HackRF.enumerate()`. Forbidden `list_devices` absent from entire file (grep verified).                                                 |
| `_transition_portapack()`                 | `self._last_portapack_transition`               | State write before return (D-11)                               | ✓ WIRED    | Line 171 writes `self._last_portapack_transition = transition_result.value` immediately after `_transition_portapack()` return, before any FAILURE branch. |
| `_diagnostics_callback`                   | `self._last_portapack_transition`               | `stat.add('last_portapack_transition', ...)`                   | ✓ WIRED    | Line 1079.                                                                                                                                              |
| Host udev rule                            | `/dev/portapack` symlink                        | `SYMLINK+="portapack"` directive                               | ✓ WIRED    | Present in udev/99-portapack.rules:15.                                                                                                                  |
| docker-compose `/dev:/dev` bind-mount     | In-container `/dev/portapack` visibility        | Whole-`/dev` mount                                             | ✓ WIRED    | Line 20. Pitfall 5 mitigated.                                                                                                                           |
| Dockerfile pyserial install               | In-container `import serial`                    | pip package at runtime                                         | ✓ WIRED    | Line 24 pip install includes pyserial.                                                                                                                  |

### Behavioral Spot-Checks

| Behavior                                                       | Command                                                              | Result                              | Status    |
| -------------------------------------------------------------- | -------------------------------------------------------------------- | ----------------------------------- | --------- |
| Full Phase 5 test suite passes                                 | `python3 -m pytest test/test_portapack_transition.py -x --tb=short`  | `58 passed in 0.41s`                | ✓ PASS    |
| Module parses (AST valid) after all Phase 5 edits              | `python3 -c "import ast; ast.parse(open('hackrf_ros/hackrf_lifecycle_node.py').read())"` | exit 0                     | ✓ PASS    |
| `_transition_portapack` contains zero `ast.Raise` nodes        | AST walk of function subtree                                         | 0 raise nodes                       | ✓ PASS    |
| `on_configure` call ordering enforced                          | AST substring check inside `on_configure` body                       | `_declare_parameters < _transition_portapack < pyhackrf2.HackRF` | ✓ PASS |
| Forbidden `list_devices` API absent                            | `grep list_devices hackrf_ros/hackrf_lifecycle_node.py`              | no match                            | ✓ PASS    |
| docker-compose.yaml is valid YAML                              | `python3 -c "import yaml; yaml.safe_load(open('docker-compose.yaml'))"` | exit 0                           | ✓ PASS    |
| Bash scripts syntactically valid                               | `bash -n scripts/install_portapack_udev.sh && bash -n scripts/hil_portapack_check.sh` | exit 0                      | ✓ PASS    |

### Requirements Coverage

| Requirement  | Source Plan       | Description                                                        | Status         | Evidence                                                                                                                                                                                                                                                          |
| ------------ | ----------------- | ------------------------------------------------------------------ | -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| REQ-P5-00    | 05-03             | Scope-decision reversal (D-00)                                     | ✓ SATISFIED    | PROJECT.md:45-46 + REQUIREMENTS.md:113 bounded statement replaces blanket exclusion.                                                                                                                                                                           |
| REQ-P5-01    | 05-01             | Udev VID:PID match (D-01)                                          | ✓ SATISFIED    | udev/99-portapack.rules:15 — `ATTRS{idVendor}=="1d50"`, `ATTRS{idProduct}=="6018"`. TestUdevRule::test_matches_vid/pid pass.                                                                                                                                   |
| REQ-P5-02    | 05-01             | Symlink `/dev/portapack` (D-02)                                    | ✓ SATISFIED    | udev/99-portapack.rules:15 — `SYMLINK+="portapack"`. TestUdevRule::test_symlink_name pass.                                                                                                                                                                       |
| REQ-P5-03    | 05-01             | device_cgroup_rules + /dev bind (D-03)                             | ✓ SATISFIED    | docker-compose.yaml:11-13 + 20. TestComposeCgroup (5 tests) pass.                                                                                                                                                                                              |
| REQ-P5-04    | 05-01             | Host install script (D-04)                                         | ✓ SATISFIED    | scripts/install_portapack_udev.sh 0755, installs to /etc/udev/rules.d/.                                                                                                                                                                                         |
| REQ-P5-05    | 05-02             | pyserial 115200 8N1 + `b'hackrf\\n'` (D-05)                        | ✓ SATISFIED    | hackrf_lifecycle_node.py:460-472 (serial.Serial config + PORTAPACK_COMMAND constant b'hackrf\\n' at line 87). TestSerialParams (4 tests) pass.                                                                                                                  |
| REQ-P5-06    | 05-02             | open→write→close→poll `HackRF.enumerate()`→open sequence (D-06)    | ✓ SATISFIED    | _transition_portapack lines 502-534 + _poll_hackrf_present 479-500 uses HackRF.enumerate() (line 493). TestPresenceProbe (2 tests), TestTransitionSequence pass. No `list_devices` anywhere.                                                                 |
| REQ-P5-07    | 05-02             | Skip when symlink absent or disabled (D-07)                        | ✓ SATISFIED    | Line 512-513 `if not enable or not device or not os.path.exists(device): return SKIPPED`. TestSkipPath (3 tests) pass.                                                                                                                                          |
| REQ-P5-08    | 05-02             | Serial raise → log ERROR + SKIPPED (D-08)                          | ✓ SATISFIED    | Lines 475-477 (except SerialException, OSError; log ERROR). Line 519-521 `return SKIPPED`. TestSerialRaise (2 tests) pass.                                                                                                                                    |
| REQ-P5-09    | 05-02             | Resend + 2-attempt FAILED terminal (D-09)                          | ✓ SATISFIED    | Lines 525-534 implement resend + RETRIED/FAILED. TestResendPath (2 tests) pass.                                                                                                                                                                                  |
| REQ-P5-10    | 05-02             | pyhackrf2 open retry loop 0.25s × portapack_open_retries (D-10)    | ✓ SATISFIED    | on_configure lines 190-197 retry loop; PORTAPACK_OPEN_RETRY_DELAY_S = 0.25 at line 86. TestOpenRetries (4 tests) pass.                                                                                                                                       |
| REQ-P5-11    | 05-02             | Diagnostics `last_portapack_transition` written on every configure (D-11) | ✓ SATISFIED | Line 171 writes state before FAILURE return. Line 1079 diag callback. TestDiagField (4 tests) pass.                                                                                                                                                             |
| REQ-P5-12    | 05-02             | 4 ROS params with defaults (D-12)                                  | ✓ SATISFIED    | declare_parameter at lines 433/437/440/443 with PORTAPACK_DEFAULT_* constants. TestDeclareParameters (6 tests) pass.                                                                                                                                           |
| REQ-P5-13    | 05-02             | Module-level constants for defaults (D-13)                         | ✓ SATISFIED    | Lines 80-87: 8 PORTAPACK_* constants. TestConstants (8 tests) pass.                                                                                                                                                                                              |
| REQ-P5-14    | 05-02             | Params NOT dynamic (D-14)                                          | ✓ SATISFIED    | TestDeclareParameters::test_params_not_dynamic_in_param_callback passes (AST walk confirms portapack_* absent from _param_callback).                                                                                                                              |
| REQ-P5-15    | 05-02             | Enum diag values exactly `skipped`/`succeeded`/`retried`/`failed` (D-15) | ✓ SATISFIED | PortapackTransitionResult enum lines 90-95 with lowercase string values. TestEnum + TestDiagField::test_enum_values_cover_diagnostics_spec pass.                                                                                                              |
| REQ-P5-16    | 05-02             | on_configure ordering + never-raise helper (D-16, Pitfall 2)       | ✓ SATISFIED    | AST order check confirmed; `ast.walk(_transition_portapack)` returns 0 ast.Raise nodes. TestIntegrationPoint (3 tests) pass.                                                                                                                                     |
| REQ-P5-A1    | 05-01, 05-04      | Portapack VID:PID verified against live hardware (A1)              | ? NEEDS HUMAN  | Install script enforces 1d50:6018 and exits 2 on mismatch (fail-safe). HIL script A1 check present. **Live verification deferred** per 05-01-SUMMARY.md 'Deferred Verifications'.                                                                               |
| REQ-P5-A3    | 05-02, 05-04      | 50 ms DTR/RTS settle empirically sufficient (A3)                   | ? NEEDS HUMAN  | PORTAPACK_DTR_SETTLE_S = 0.05 at line 84, written before port.write at line 470-471. HIL script A3 runs 10 configures and counts first-attempt successes. **Live verification deferred** per 05-04-SUMMARY.md 'Deferred Verifications'.                        |

All 19 requirement IDs declared in phase frontmatter are accounted for. 17/19 satisfied via automated evidence; 2 (REQ-P5-A1, REQ-P5-A3) deferred to operator HIL run as explicitly documented in SUMMARY.md "Deferred Verifications" sections.

### Anti-Patterns Found

Informational items only — from REVIEW.md (4 warnings, 7 info) and independent scan:

| File                                          | Line    | Pattern                                                        | Severity | Impact                                                                                 |
| --------------------------------------------- | ------- | -------------------------------------------------------------- | -------- | -------------------------------------------------------------------------------------- |
| scripts/hil_portapack_check.sh                | 73      | `2>&1 >/dev/null` redirect order wrong (stderr still leaks)    | ⚠️ Warning | HIL loop noise; does not affect correctness. Non-blocking.                             |
| hackrf_ros/hackrf_lifecycle_node.py           | 440-442 | `portapack_reenum_timeout_s` lacks FloatingPointRange          | ⚠️ Warning | User-set negative/zero silently yields immediate FAILED. Validation hardening, not a goal failure. |
| udev/99-portapack.rules                       | 15      | `MODE="0666"` world-writable                                   | ⚠️ Warning | Accepted in 05-01 threat model (T-05-01-02, dialout group gate). Hardening opportunity. |
| docker-compose.yaml                           | 7-13, 18-20 | 4 overlapping access-control layers (privileged + devices + cgroup + /dev) | ⚠️ Warning | Redundant but functional; flagged in 05-01 threat model as accepted (T-05-01-01). Clarity issue. |
| scripts/install_portapack_udev.sh             | 43, 45, 58 | Unicode glyphs `✓` `⚠`                                        | ℹ️ Info   | Project convention prefers ASCII; cosmetic.                                            |
| scripts/install_portapack_udev.sh, hil script | 30, 11  | `ls` output parsing (SC2012)                                   | ℹ️ Info   | Works for `/dev/ttyACM*` in practice.                                                  |
| test/test_portapack_transition.py             | 675+    | Unused `mod` binding in several tests                          | ℹ️ Info   | flake8 F841 if strict mode enabled.                                                    |
| setup.py, Dockerfile                          | 27, 24  | `pyserial>=3.5` no upper bound                                 | ℹ️ Info   | Reproducibility concern, not correctness.                                              |
| hackrf_ros/hackrf_lifecycle_node.py           | 510-515 | 4 separate get_parameter calls in _transition_portapack        | ℹ️ Info   | Readability.                                                                           |
| hackrf_ros/hackrf_lifecycle_node.py           | 496     | bare `except Exception` in _poll_hackrf_present                | ℹ️ Info   | Intentional (libusb transient errors); broader than needed.                            |

**None of these are blockers.** They are pre-declared review follow-ups (REVIEW.md line 21 `status: issues_found` with 0 critical). The phase goal is achieved irrespective of these hardening opportunities.

### Human Verification Required

#### 1. Hardware verification A1 — Portapack VID:PID capture

**Test:**
```bash
# On a host with a Portapack attached in Mayhem UI mode:
sudo scripts/install_portapack_udev.sh
ls -la /dev/portapack
docker compose build hackrf && docker compose up -d hackrf
docker exec hackrf_ros ls /dev/portapack
docker exec hackrf_ros python3 -c "import serial; print(serial.__version__)"
```
**Expected:** `idVendor=1d50 idProduct=6018` printed, `A1 CONFIRMED` line, `/dev/portapack` resolves to `/dev/ttyACMN` both on host and in container, pyserial version prints (≥3.5). If VID:PID mismatch, install script exits 2; operator must update `udev/99-portapack.rules` before re-running.

**Why human:** Requires physical Portapack hardware. 05-01-SUMMARY.md Task 4 was auto-approved under --auto chain and is documented under "Deferred Verifications" pending operator execution.

#### 2. Hardware verification A2 + A3 — End-to-end HIL run

**Test:**
```bash
# On the host (A1 + A2):
sudo scripts/hil_portapack_check.sh
# Inside container (A3 needs ros2 CLI):
docker exec -it hackrf_ros /ws/scripts/hil_portapack_check.sh
less /tmp/hil_portapack_*.log
```
**Expected:** `[PASS] A1`, `[PASS] A2`, `[PASS] A3` all printed. A3 reports ≥1/10 first-attempt successes. If `\n` fails, script auto-falls back to `\r\n` and operator must update `PORTAPACK_COMMAND` to `b'hackrf\r\n'`. If A3 reports 0/10 first-attempt successes, operator must bump `PORTAPACK_DTR_SETTLE_S` from 0.05 to 0.10.

**Why human:** HIL script must run against live Portapack hardware and observe USB re-enumeration timing. 05-04-SUMMARY.md Task 3 was auto-approved under --auto chain and is documented under "Deferred Verifications" pending operator execution.

### Gaps Summary

No gaps. All 6 observable truths are backed by automated evidence in the production code, test suite (58/58 green in 0.41s), and deployment artifacts. All 19 requirement IDs declared in the phase frontmatter are accounted for in REQUIREMENTS.md and verified in the codebase, with the 2 hardware-verification assumptions (REQ-P5-A1, REQ-P5-A3) explicitly documented as deferred human-verification items under the --auto chain.

The status is `human_needed` (not `passed`) strictly because the two HIL items require operator sign-off with live hardware before Phase 5 can be called field-complete. The automated contract — code paths exist, tests green, scope-reversal docs consistent, deployment artifacts committed — is fully satisfied.

---

_Verified: 2026-04-18_
_Verifier: Claude (gsd-verifier)_
