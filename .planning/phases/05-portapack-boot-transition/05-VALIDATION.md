---
phase: 5
slug: portapack-boot-transition
status: planner-populated
nyquist_compliant: true
wave_0_complete: false
created: 2026-04-18
updated: 2026-04-18
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (ament_python project convention) |
| **Config file** | setup.py `tests_require=['pytest']` — source-level AST/text parsing tests per existing pattern (see `test/test_driver_upgrade.py`, `test/test_usb_disconnect.py`, `test/test_agc.py`) |
| **Quick run command** | `pytest test/test_portapack_transition.py -q` |
| **Full suite command** | `colcon test --packages-select hackrf_ros && colcon test-result --verbose` |
| **Estimated runtime** | ~15 seconds (source-text grep + mocked pyserial + mocked pyhackrf2; no hardware required) |

---

## Sampling Rate

- **After every task commit:** Run `pytest test/test_portapack_transition.py -q`
- **After every plan wave:** Run `colcon test --packages-select hackrf_ros`
- **Before `/gsd-verify-work`:** Full colcon suite must be green AND hardware checkpoint (05-01 Task 4 + 05-04 Task 3) completed with captured VID:PID, terminator, and DTR settle success rate recorded in SUMMARYs
- **Max feedback latency:** ~30 seconds (hard cap — no suite may exceed this; sleep patches enforce the cap in mocked-behaviour tests)

---

## Per-Task Verification Map

Every Phase 5 task is mapped below. Each row lists the decision (D-XX) or assumption (A-X) it addresses, the automated command that gates commit, and whether a live artifact (`File Exists`) is required before the test can run.

| Task ID | Plan | Wave | Requirement | Decision | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|----------|------------|-----------------|-----------|-------------------|-------------|--------|
| 5-01-01 | 01 | 1 | REQ-P5-01, REQ-P5-02 | D-01, D-02 | — | udev rule matches Portapack VID:PID and creates `/dev/portapack` symlink | source-text | `grep -q 'SYMLINK+="portapack"' udev/99-portapack.rules && grep -q 'ATTRS{idVendor}=="1d50"' udev/99-portapack.rules && grep -q 'ATTRS{idProduct}=="6018"' udev/99-portapack.rules` | udev/99-portapack.rules | ⬜ pending |
| 5-01-02 | 01 | 1 | REQ-P5-03 | D-03 | T-05-01-01, T-05-01-03 | cgroup rules for USB (189) and CDC-ACM (166) + /dev bind exposes host symlink into container + pyserial in image | source-text | `grep -q 'c 189:\* rmw' docker-compose.yaml && grep -q 'c 166:\* rmw' docker-compose.yaml && grep -qE '^\s+-\s+/dev:/dev$' docker-compose.yaml && grep -q pyserial Dockerfile` | docker-compose.yaml, Dockerfile | ⬜ pending |
| 5-01-03 | 01 | 1 | REQ-P5-04, REQ-P5-A1 | D-04, A1 | — | Host-side install script verifies A1 and installs the rule with udev reload | source-text + bash | `bash -n scripts/install_portapack_udev.sh && grep -q 'udevadm control --reload-rules' scripts/install_portapack_udev.sh && grep -q idVendor scripts/install_portapack_udev.sh && grep -q 1d50 scripts/install_portapack_udev.sh` | scripts/install_portapack_udev.sh | ⬜ pending |
| 5-01-04 | 01 | 1 | REQ-P5-A1 | A1 | T-05-01-02 | Hardware confirms VID:PID, /dev/portapack reachable from container, pyserial importable | hardware checkpoint | `# operator: sudo scripts/install_portapack_udev.sh && docker exec hackrf_ros ls /dev/portapack && docker exec hackrf_ros python3 -c "import serial"` | live hardware | ⬜ pending |
| 5-02-01 | 02 | 1 | REQ-P5-13, REQ-P5-16 | D-13, D-16 | — | Module constants + enum + state init at correct file locations | source-text | `python3 -c "import ast; ast.parse(open('hackrf_ros/hackrf_lifecycle_node.py').read())" && grep -q PORTAPACK_DEFAULT_DEVICE hackrf_ros/hackrf_lifecycle_node.py && grep -q 'class PortapackTransitionResult' hackrf_ros/hackrf_lifecycle_node.py && grep -q '_last_portapack_transition = PortapackTransitionResult.SKIPPED.value' hackrf_ros/hackrf_lifecycle_node.py` | hackrf_lifecycle_node.py | ⬜ pending |
| 5-02-02 | 02 | 1 | REQ-P5-05, REQ-P5-06, REQ-P5-07, REQ-P5-08, REQ-P5-09, REQ-P5-12, REQ-P5-14, REQ-P5-A3 | D-05..D-09, D-12, D-14, A3 | T-05-02-01, T-05-02-02 | _transition_portapack + sub-helpers implement exact sequence from D-06, return enum per D-16, never raise per Pitfall 2, 50 ms DTR settle per A3 | source-text + unit (mock) | `pytest test/test_portapack_transition.py::TestTransitionSequence -x && pytest test/test_portapack_transition.py::TestSkipPath -x && pytest test/test_portapack_transition.py::TestSerialRaise -x && pytest test/test_portapack_transition.py::TestResendPath -x && pytest test/test_portapack_transition.py::TestDeclareParameters -x && pytest test/test_portapack_transition.py::TestSerialParams -x && pytest test/test_portapack_transition.py::TestPresenceProbe -x` | hackrf_lifecycle_node.py, test file | ⬜ pending |
| 5-02-03 | 02 | 1 | REQ-P5-10, REQ-P5-11, REQ-P5-15, REQ-P5-16 | D-10, D-11, D-15, D-16 | T-05-02-03 | on_configure wired with retry loop, diagnostics field published, helper called between declare_parameters and HackRF() | source-text + unit (mock) | `pytest test/test_portapack_transition.py::TestIntegrationPoint -x && pytest test/test_portapack_transition.py::TestOpenRetries -x && pytest test/test_portapack_transition.py::TestDiagField -x && grep -q 'pyserial>=3.5' setup.py` | hackrf_lifecycle_node.py, setup.py, test file | ⬜ pending |
| 5-03-01 | 03 | 1 | REQ-P5-00 | D-00 | T-05-03-02 | PROJECT.md scope reversal present; old out-of-scope statement absent | source-text | `! grep -q 'Mayhem firmware serial control — separate package (pymayhem)' .planning/PROJECT.md && grep -q 'Mayhem mode-switch command' .planning/PROJECT.md` | .planning/PROJECT.md | ⬜ pending |
| 5-03-02 | 03 | 1 | REQ-P5-00 | D-00 | T-05-03-02 | REQUIREMENTS.md Out of Scope row updated to bounded variant | source-text | `! grep -qE '^\| Mayhem firmware control \| Separate package concern' .planning/REQUIREMENTS.md && grep -q 'IN SCOPE since Phase 5' .planning/REQUIREMENTS.md` | .planning/REQUIREMENTS.md | ⬜ pending |
| 5-03-03 | 03 | 1 | REQ-P5-00 through REQ-P5-16, REQ-P5-A1, REQ-P5-A3 | all D-XX, A1, A3 | T-05-03-01 | REQ-P5-NN minted; 19 traceability rows exist; coverage totals updated | source-text | `grep -q '### Portapack Boot Transition (Phase 5)' .planning/REQUIREMENTS.md && [ "$(grep -cE '^\| REQ-P5-[0-9A-Z]+ \| Phase 5' .planning/REQUIREMENTS.md)" -eq 19 ] && grep -q 'v1 requirements: 51 total' .planning/REQUIREMENTS.md` | .planning/REQUIREMENTS.md | ⬜ pending |
| 5-04-01 | 04 | 2 | all REQ-P5-NN | all D-XX | T-05-04-01, T-05-04-02 | Automated test suite covers every D-XX; runs in <30 s | unit + source-text | `pytest test/test_portapack_transition.py -x --tb=short` | test/test_portapack_transition.py | ⬜ pending |
| 5-04-02 | 04 | 2 | REQ-P5-A1, REQ-P5-A3 | A1, A2, A3 | T-05-04-03 | HIL script syntactically valid and labelled for all three assumptions | source-text + bash | `test -x scripts/hil_portapack_check.sh && bash -n scripts/hil_portapack_check.sh && grep -q 'A1 —' scripts/hil_portapack_check.sh && grep -q 'A2 —' scripts/hil_portapack_check.sh && grep -q 'A3 —' scripts/hil_portapack_check.sh` | scripts/hil_portapack_check.sh | ⬜ pending |
| 5-04-03 | 04 | 2 | REQ-P5-A1, REQ-P5-A3 | A1, A2, A3 | — | Hardware run confirms Portapack VID:PID, terminator, and first-attempt success rate | hardware checkpoint | `# operator: scripts/hil_portapack_check.sh; grep "[PASS] A1" /tmp/hil_portapack_*.log && grep "[PASS] A2" /tmp/hil_portapack_*.log && grep "[PASS] A3" /tmp/hil_portapack_*.log` | live hardware | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] `test/test_portapack_transition.py` — created by 05-04 Task 1 (Wave 2); interim plans can still commit because source-text assertions inside the file reference artifacts from 05-01 and 05-02, which exist by the time the test file itself is created
- [x] `udev/99-portapack.rules` — created by 05-01 Task 1 (Wave 1)
- [x] `scripts/install_portapack_udev.sh` — created by 05-01 Task 3 (Wave 1)
- [x] `scripts/hil_portapack_check.sh` — created by 05-04 Task 2 (Wave 2)
- [x] No new framework install needed — pytest bundled; pyserial available on host per RESEARCH.md §Environment Availability

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Portapack VID:PID identification | REQ-P5-A1, D-01 | Requires live Portapack hardware | `sudo scripts/install_portapack_udev.sh` with Portapack attached; script prints observed VID:PID and exits non-zero on mismatch |
| End-to-end first-boot transition | D-00, D-06, D-16 | Requires USB re-enumeration event | Power-cycle Portapack into Mayhem UI; run container; `ros2 lifecycle set /hackrf_node configure`; verify node reaches INACTIVE and `/diagnostics` shows `last_portapack_transition: succeeded` |
| Retry-after-miss path | REQ-P5-09, D-09 | Requires forcing a re-enumeration miss | Artificially shorten `portapack_reenum_timeout_s` to 0.2; run configure; confirm `last_portapack_transition: retried` and recovery |
| Stale-symlink fallthrough | REQ-P5-07, REQ-P5-08, D-07, D-08 | Requires udev state desync | Leave `/dev/portapack` present but device already in HackRF mode; confirm ERROR log on serial failure, configure still SUCCEEDs via pyhackrf2 direct open |
| Plain HackRF (no Portapack) backward-compat | REQ-P5-07, D-07 | Cannot simulate absence of symlink in unit test cleanly | Remove Portapack; confirm `/dev/portapack` absent; configure SUCCEEDs; diagnostics show `last_portapack_transition: skipped` |
| Docker cgroup rule behavior | REQ-P5-03, D-03 | Requires container launch on host | `docker compose up` with rules applied; confirm `/dev/portapack` visible inside container; confirm HackRF still claimable after transition |
| Parameter dynamic handling (D-14) | REQ-P5-14, D-14 | Requires live node + ros2 CLI | `ros2 param set /hackrf_node portapack_enable_transition false` — documented as no-op (takes effect only on next configure) |
| HIL A2 terminator verification | REQ-P5-05, A2 | Requires Portapack hardware | `scripts/hil_portapack_check.sh` tests `\n` and, on failure, retries `\r\n` automatically |
| HIL A3 DTR settle sufficiency | REQ-P5-A3, A3 | Requires Portapack hardware | `scripts/hil_portapack_check.sh` runs 10 configures and reports first-attempt success rate |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify (even the two hardware checkpoints are bookended by automated suites)
- [x] Wave 0 covers all MISSING references (test file + udev file + HIL script + install script)
- [x] No watch-mode flags (`--watch`, `-f`, etc.)
- [x] Feedback latency < 30s (enforced by sleep patching in Pattern B tests)
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** planner-populated (awaiting execution)
