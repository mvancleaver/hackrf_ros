---
phase: 05-portapack-boot-transition
plan: 01
subsystem: deployment
tags: [udev, docker, deployment, portapack, pyserial]
requirements: [REQ-P5-01, REQ-P5-02, REQ-P5-03, REQ-P5-04, REQ-P5-A1]
dependency_graph:
  requires: []
  provides:
    - "udev/99-portapack.rules (repo-resident rule for /dev/portapack symlink)"
    - "scripts/install_portapack_udev.sh (host install + A1 VID:PID verification)"
    - "docker-compose.yaml cgroup rules + /dev bind-mount (container access)"
    - "Dockerfile pyserial pip dep (in-container serial handshake)"
  affects:
    - docker-compose.yaml
    - Dockerfile
tech_stack:
  added:
    - "pyserial (in-container pip) — CDC-ACM open/write for the hackrf mode-switch"
  patterns:
    - "Host-resident udev rule + repo-committed source (deployment via script)"
    - "Bash A1 hardware verification gate before rule install (fail-fast on PID mismatch)"
    - "device_cgroup_rules as auditable defense-in-depth alongside privileged:true"
key_files:
  created:
    - "udev/99-portapack.rules"
    - "scripts/install_portapack_udev.sh"
  modified:
    - "docker-compose.yaml"
    - "Dockerfile"
decisions:
  - "Rule matches VID:PID only (D-01) — single-Portapack deployment"
  - "SYMLINK+=\"portapack\" → /dev/portapack (D-02)"
  - "device_cgroup_rules added for audit trail even though privileged:true covers it (D-03)"
  - "Rule deploys to host /etc/udev/rules.d/ via sudo script, not baked into image (D-04)"
  - "Install script treats PID mismatch as fatal (exit 2) — forces explicit rule update before deploy"
metrics:
  tasks_total: 4
  tasks_completed: 3
  tasks_auto_approved: 1
  duration_minutes: 5
  completed: "2026-04-19T05:09:25Z"
---

# Phase 5 Plan 1: Portapack Deployment Surface Summary

**One-liner:** Host udev rule, A1-verifying install script, docker-compose cgroup + /dev bind-mount, and pyserial pip dep — the full deployment layer that lets the (Phase 5.2) lifecycle node open `/dev/portapack` from inside the container.

## What Shipped

| Artifact | Purpose | File |
|---|---|---|
| udev rule | Stable `/dev/portapack` symlink on CDC-ACM VID:PID match | `udev/99-portapack.rules` |
| Host install script | Captures live VID:PID (A1), installs rule, reloads udev | `scripts/install_portapack_udev.sh` |
| Compose cgroup rules | Explicit RMW on USB (189) + CDC-ACM (166) majors | `docker-compose.yaml` |
| Compose /dev bind-mount | Makes host `/dev/portapack` symlink visible in-container | `docker-compose.yaml` |
| pyserial pip dep | In-container serial handshake prerequisite for Phase 5.2 | `Dockerfile` |

## Tasks Executed

| Task | Name | Commit | Files |
|---|---|---|---|
| 1 | Create udev/99-portapack.rules | `137d1d3` | udev/99-portapack.rules |
| 2 | docker-compose cgroup+/dev, Dockerfile pyserial | `3f34a05` | docker-compose.yaml, Dockerfile |
| 3 | Host install script with A1 verify | `145b0a7` | scripts/install_portapack_udev.sh |
| 4 | Hardware checkpoint (auto-approved) | — | *(see Deferred Verifications)* |

## Decisions Made

- **VID:PID-only udev match.** Single Portapack per deployment (D-01). Multi-unit fleets are deferred to a future phase and would need `ATTRS{serial}` matching.
- **`/dev:/dev` whole-tree bind.** Docker `devices:` will not follow symlinks at bind time, so the cleaner per-device passthrough is not viable for a symlink. Accepted because `privileged: true` already grants full device visibility.
- **device_cgroup_rules kept explicit.** Redundant with `privileged: true` today, but makes the device surface auditable and survives any future tightening of the compose file.
- **Install script fails loudly on PID mismatch.** If udevadm reports anything other than 1d50:6018, the script exits 2 rather than writing a broken rule. Forces the operator to update `udev/99-portapack.rules` explicitly before re-running.
- **No pyserial version pin in Dockerfile.** The canonical pin (`pyserial>=3.5`) lives in `setup.py` (landing in plan 05-02). Keeping the Dockerfile line minimal avoids drift.

## Deviations from Plan

None — plan executed exactly as written. Task 4 (hardware checkpoint) was auto-approved per auto-mode directive; see Deferred Verifications below.

## Deferred Verifications

**Task 4: Live-hardware validation of A1 / Pitfall 5 / Pitfall 6** — AUTO-APPROVED (no hardware in this agent's environment).

This checkpoint MUST be executed by the operator on a Portapack-equipped host before Phase 5 is considered field-ready. The automated artifacts in this plan assume:

- **A1 (Portapack VID:PID = 1d50:6018)** — `udev/99-portapack.rules` and the install script both hardcode this. If live hardware reports a different PID, the install script will exit 2 and the operator must update the rule line:
  ```
  ATTRS{idProduct}=="<observed-PID>"
  ```

- **Pitfall 5 (/dev:/dev symlink visibility)** — verified textually (yaml parses, key present) but not end-to-end on a running container.

- **Pitfall 6 (udevadm reload-rules + trigger)** — the script calls both in order, but there is no hardware sanity check in-agent.

### Hardware verification results

*PLACEHOLDER — to be filled by operator after running Task 4 manually.*

| Item | Observed | Status |
|---|---|---|
| idVendor | _not yet captured_ | pending |
| idProduct | _not yet captured_ | pending |
| Host `/dev/portapack` symlink target | _not yet captured_ | pending |
| Container `ls /dev/portapack` | _not yet captured_ | pending |
| Container pyserial version | _not yet captured_ | pending |
| Kernel ACM major (from /proc/devices) | _expect 166_ | pending |

### Operator verification command sequence

Run this checklist on a host with a Portapack attached (Mayhem UI mode) and the docker image rebuilt:

```bash
# 1. Confirm ACM node present
ls /dev/ttyACM*

# 2. Capture A1 and install the rule
sudo ./scripts/install_portapack_udev.sh
# Expected: "idVendor=1d50 idProduct=6018" and "A1 CONFIRMED"

# 3. Verify host symlink
ls -la /dev/portapack

# 4. Rebuild and start container
docker compose build hackrf && docker compose up -d hackrf

# 5. Verify cgroup + /dev bind-mount inside container
docker exec hackrf_ros ls /dev/portapack
docker exec hackrf_ros python3 -c "import serial; print(serial.__version__)"
```

Record the observed values in the "Hardware verification results" table above and commit the update. Resume signal: reply with "approved" plus the captured VID:PID line, or describe any mismatch for revision.

## Threat Flags

No new security-relevant surface introduced beyond what the plan's threat model already anticipated. `privileged: true` + `/dev:/dev` bind-mount remain the widest surface; disposition was already `accept` (T-05-01-01) on the grounds that privileged grants all device access anyway.

## Verification

All automated acceptance criteria passed:

- **Task 1:** `grep` assertions for `SYMLINK+="portapack"`, `ATTRS{idVendor}=="1d50"`, `ATTRS{idProduct}=="6018"`, `SUBSYSTEM=="tty"`, `MODE="0666"`, `ASSUMPTION A1` — all PASS.
- **Task 2:** `grep` assertions for `c 189:* rmw`, `c 166:* rmw`, `- /dev:/dev`, `device_cgroup_rules`, preserved `privileged: true` and `/dev/bus/usb:/dev/bus/usb`, `pyserial` in Dockerfile alongside `pyhackrf2`/`scipy`/`sigmf`. `python3 -c "import yaml; yaml.safe_load(open('docker-compose.yaml'))"` exits 0 — valid YAML.
- **Task 3:** executable bit set (0755), `udevadm control --reload-rules`, `udevadm trigger`, `idVendor`/`idProduct` parsing, `1d50`/`6018` hardcoded check, `/etc/udev/rules.d/99-portapack.rules` destination, `bash -n` exits 0.

## Follow-Ups for Plan 05-02

- Add `pyserial>=3.5` to `setup.py` `install_requires`
- Implement `_transition_portapack()` helper that opens `/dev/portapack` and writes `hackrf\n`
- Use `pyhackrf2.HackRF.enumerate()` (NOT `list_devices()` — research correction)

## Self-Check: PASSED

**Files created/modified:**
- FOUND: `udev/99-portapack.rules`
- FOUND: `scripts/install_portapack_udev.sh` (executable)
- FOUND: `docker-compose.yaml` (modified — cgroup + /dev bind)
- FOUND: `Dockerfile` (modified — pyserial appended)

**Commits:**
- FOUND: `137d1d3` feat(05-01): add udev rule for Portapack CDC-ACM stable symlink
- FOUND: `3f34a05` feat(05-01): add Portapack device access in docker + pyserial dep
- FOUND: `145b0a7` feat(05-01): add host install script for Portapack udev rule
