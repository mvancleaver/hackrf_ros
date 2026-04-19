---
status: partial
phase: 05-portapack-boot-transition
source: [05-VERIFICATION.md]
started: 2026-04-19T05:30:00Z
updated: 2026-04-19T05:30:00Z
---

## Current Test

[awaiting human testing on live Portapack hardware]

## Tests

### 1. A1 — Portapack CDC-ACM VID:PID capture + udev install
expected: Running `sudo scripts/install_portapack_udev.sh` with a Portapack attached in Mayhem UI mode prints `idVendor=1d50 idProduct=6018` (or captures the actual VID:PID if they differ), installs `/etc/udev/rules.d/99-portapack.rules`, reloads udev, and confirms `/dev/portapack` symlink materializes. Console output contains "A1 CONFIRMED" marker.
result: [pending]

### 2. A2 + A3 — HIL checkpoint (terminator and DTR settle)
expected: Running `scripts/hil_portapack_check.sh` on the deployment target (host for A1/A2 phases, container for A3 phase) prints `[PASS]` markers for A1, A2, and A3 sections. A3 section reports ≥1/10 first-attempt successes with the 50ms DTR settle. Log written to `/tmp/hil_portapack_*.log`.
result: [pending]

### 3. End-to-end first-boot transition
expected: Power-cycle a Portapack-equipped rig so Mayhem UI comes up fresh; launch the hackrf ROS2 container; run `ros2 lifecycle set /hackrf_node configure`; node reaches INACTIVE state (not ERROR); `ros2 topic echo /diagnostics` shows `last_portapack_transition: succeeded` within 10 seconds.
result: [pending]

### 4. Plain-HackRF backward compatibility
expected: Same procedure as #3 but with a plain HackRF One (no Portapack attached). `/dev/portapack` should be absent. Node reaches INACTIVE; diagnostics show `last_portapack_transition: skipped`. No regression from Phase 4 behavior.
result: [pending]

## Summary

total: 4
passed: 0
issues: 0
pending: 4
skipped: 0
blocked: 0

## Gaps
