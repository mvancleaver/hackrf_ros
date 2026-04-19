---
phase: 5
slug: portapack-boot-transition
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-18
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (ament_python project convention) |
| **Config file** | setup.py `tests_require` (source-level AST/text parsing tests per existing pattern) |
| **Quick run command** | `pytest test/test_portapack_transition.py -q` |
| **Full suite command** | `colcon test --packages-select hackrf_ros && colcon test-result --verbose` |
| **Estimated runtime** | ~15 seconds (source-text mocks, no hardware required) |

---

## Sampling Rate

- **After every task commit:** Run `pytest test/test_portapack_transition.py -q`
- **After every plan wave:** Run `colcon test --packages-select hackrf_ros`
- **Before `/gsd-verify-work`:** Full colcon suite must be green AND hardware checkpoint (Portapack attach, configure, diagnostics field) completed
- **Max feedback latency:** ~30 seconds

---

## Per-Task Verification Map

> Populated by the planner in step 8. Planner MUST emit one row per task referencing Plan/Wave/Requirement/Decision (D-XX) and a concrete `<automated>` pytest command or source-grep assertion. Decisions D-00 through D-16 plus assumptions A1/A2/A3 from RESEARCH.md must each appear at least once.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 5-01-01 | 01 | 1 | D-01,D-02,D-04 | — | udev rule matches Portapack VID:PID and creates `/dev/portapack` symlink | source-text | `grep -E 'SYMLINK\+?="portapack"' udev/99-portapack.rules` | ❌ W0 | ⬜ pending |

*Planner expands this row into a full matrix spanning every task in every plan.*

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `test/test_portapack_transition.py` — unit tests with mocked `serial.Serial` and `pyhackrf2.HackRF.enumerate`
- [ ] `test/conftest.py` — shared fixtures for mocking time.sleep and monotonic clocks (if not already present)
- [ ] `udev/99-portapack.rules` — udev rule file installed into the repo under `udev/`
- [ ] `scripts/hil_portapack_check.sh` (or equivalent) — hardware-in-the-loop checkpoint script for A1/A2/A3 verification

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Portapack VID:PID identification | D-01, A1 | Requires live Portapack hardware | Attach Portapack in Mayhem mode, run `udevadm info -a -n /dev/ttyACM0 \| grep -E 'idVendor\|idProduct'`, record values into udev rule |
| End-to-end transition (first boot) | D-00, D-06, D-16 | Requires USB re-enumeration event | Power-cycle Portapack into Mayhem UI; run `ros2 lifecycle set /hackrf_node configure`; verify node reaches INACTIVE and `/diagnostics` shows `last_portapack_transition: succeeded` |
| Retry-after-miss path | D-09 | Requires forcing re-enumeration miss | Artificially shorten `portapack_reenum_timeout_s` to 0.2; run configure; confirm `last_portapack_transition: retried` eventually; confirm recovery |
| Stale-symlink fallthrough | D-07, D-08 | Requires udev state desync | Leave `/dev/portapack` present but Portapack already in HackRF mode; confirm ERROR log on serial failure, configure still SUCCEEDs |
| Plain HackRF (no Portapack) backward-compat | D-07 | Cannot simulate absence of symlink in unit test | Remove Portapack; confirm `/dev/portapack` absent; configure SUCCEEDs; diagnostics show `last_portapack_transition: skipped` |
| Docker cgroup rule behavior | D-03 | Requires container launch on host with/without rules | Launch compose service; confirm `/dev/portapack` visible inside container; confirm HackRF still claimable after transition |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (test file + udev file + HIL script)
- [ ] No watch-mode flags (`--watch`, `-f`, etc.)
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter after planner populates matrix

**Approval:** pending
