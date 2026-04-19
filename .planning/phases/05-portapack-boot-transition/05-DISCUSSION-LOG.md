# Phase 5: Portapack Boot Transition - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-18
**Phase:** 05-portapack-boot-transition
**Areas discussed:** Udev + Docker access, Transition handshake, Failure policy, ROS parameter surface

---

## Pre-Answered (from prior conversation before discuss-phase)

| Question | Selected |
|---|---|
| Approach: open `/dev/ttyACM1` from lifecycle node and send serial command to drop Mayhem UI into HackRF USB-SDR mode? | Yes (confirmed) |
| Fallback when Portapack serial absent | Skip silently, proceed to pyhackrf2.open() |
| Use udev symlink instead of raw `/dev/ttyACM1`? | Yes |
| Transition command to send | `hackrf\n` (confirmed via Mayhem firmware wiki, ch> prompt) |

---

## Udev + Docker Access

### Q1: Udev match strategy

| Option | Description | Selected |
|--------|-------------|----------|
| VID:PID only | ATTRS{idVendor} + ATTRS{idProduct} match. Single-Portapack deployments. | ✓ |
| VID:PID + iSerial | Adds ATTRS{serial} for multi-Portapack fleets. | |
| Interface-class match | USB device class + interface + vendor string. Rare. | |

**User's choice:** VID:PID only
**Notes:** Consistent with single-robot deployment assumption.

### Q2: Symlink name under /dev/

| Option | Description | Selected |
|--------|-------------|----------|
| /dev/portapack | Short, matches hardware name. | ✓ |
| /dev/hackrf-ctrl | Emphasizes control-plane pairing with HackRF USB-SDR. | |
| /dev/mayhem | Matches firmware name. | |

**User's choice:** /dev/portapack

### Q3: Container access mechanism

| Option | Description | Selected |
|--------|-------------|----------|
| cgroup + bind-mount /dev | `c 166:* rmw` + `/dev:/dev` — auditable and matches PITFALLS.md:540 guidance. | ✓ |
| Explicit devices mount | `-/dev/portapack:/dev/portapack` in compose. Fragile if symlink absent at compose-up. | |
| Rely on privileged: true | Existing mode; discoverable only via /dev/bus/usb walk, no symlink inside container. | |

**User's choice:** cgroup + bind-mount /dev
**Notes:** Matches the anticipatory note at PITFALLS.md:541.

---

## Transition Handshake

### Q1: Confirmation style

| Option | Description | Selected |
|--------|-------------|----------|
| Poll for re-enum | Send `hackrf\n`, poll pyhackrf2.list_devices() every 100ms until timeout. | ✓ |
| Probe serial response | Read ch> echo and parse known string. Fragile across firmware versions. | |
| Fire-and-forget | Send + fixed sleep + try open. Simplest. | |

**User's choice:** Poll for re-enum

### Q2: Already-in-HackRF-mode pre-check

| Option | Description | Selected |
|--------|-------------|----------|
| Symlink absence = skip | /dev/portapack disappears when in HackRF mode; absent symlink means already transitioned or no Portapack. | ✓ |
| USB device pre-check | pyhackrf2.list_devices() check before touching serial. | |

**User's choice:** Symlink absence = skip

### Q3: Serial parameters

| Option | Description | Selected |
|--------|-------------|----------|
| 115200 8N1 | CDC-ACM standard default. Widest compatibility. | ✓ |
| 9600 8N1 | pyserial default. Functionally equivalent on CDC-ACM. | |

**User's choice:** 115200 8N1

---

## Failure Policy

### Q1: Symlink exists but pyserial can't open /dev/portapack

| Option | Description | Selected |
|--------|-------------|----------|
| Log ERROR, fall through | Tolerates stale-symlink case (transitioned on prior boot). | ✓ |
| Log ERROR, FAIL configure | Strict — operator investigates. | |
| Log WARN, fall through | Same as option 1 but quieter. | |

**User's choice:** Log ERROR, fall through

### Q2: Transition sent but HackRF never re-enumerates within timeout

| Option | Description | Selected |
|--------|-------------|----------|
| Resend + retry once | Reopen serial, resend `hackrf\n`, poll again. Handles dropped command. | ✓ |
| Fail configure immediately | One shot only — operator power-cycles. | |
| Retry N times | Configurable count; adds latency on broken hardware. | |

**User's choice:** Resend + retry once

### Q3: pyhackrf2.open() raises after successful re-enum detection

| Option | Description | Selected |
|--------|-------------|----------|
| Retry 3× with 250 ms | Smooths USB kernel-claim race window. | ✓ |
| Retry 1× then fail | Minimal retry. | |
| Fail immediately | Strictest. | |

**User's choice:** Retry 3× with 250 ms

---

## ROS Parameter Surface

### Q1: Which parameters to expose (multiSelect)

| Option | Description | Selected |
|--------|-------------|----------|
| portapack_serial_device | str, default /dev/portapack. Empty = force-skip. | ✓ |
| portapack_enable_transition | bool, default True. Master off switch. | ✓ |
| portapack_reenum_timeout_s | float, default 5.0. | ✓ |
| portapack_open_retries | int, default 3. | ✓ |

**User's choice:** All four

### Q2: Where defaults live

| Option | Description | Selected |
|--------|-------------|----------|
| In code + yaml override | Defaults in `_declare_parameters()`; override via config/hackrf_rx.yaml. Matches device_index pattern. | ✓ |
| yaml-only | Require explicit operator configuration. | |

**User's choice:** In code + yaml override

### Q3: Diagnostics surface

| Option | Description | Selected |
|--------|-------------|----------|
| Add one-shot status | `last_portapack_transition` field in /diagnostics. | ✓ |
| Logs only | ROS2 logger at INFO/WARN/ERROR. | |

**User's choice:** Add one-shot status

---

## Claude's Discretion

- Internal helper method names (snake_case, leading underscore)
- Exact log message wording
- Whether to factor the polling loop into a small reusable helper vs. inline
- Choice between `pyhackrf2.list_devices()` and `pyusb.core.find()` for presence polling (prefer pyhackrf2 if available)
- Unit test strategy — mock pyserial and pyhackrf2.list_devices; no live-hardware requirement for unit tests

## Deferred Ideas

- Full pymayhem integration (other Mayhem serial commands)
- TX enablement path
- Runtime mode-toggle (HackRF → Mayhem)
- Multi-Portapack fleet support (ATTRS{serial} matching)
- Portapack firmware version detection via `version` serial command
