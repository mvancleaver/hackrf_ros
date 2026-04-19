# Phase 5: Portapack Boot Transition - Context

**Gathered:** 2026-04-18
**Status:** Ready for planning

<domain>
## Phase Boundary

The `hackrf_lifecycle_node` drives a Portapack-equipped HackRF from its default Mayhem UI boot state into HackRF USB-SDR mode during `on_configure`, so the existing `pyhackrf2.HackRF(device_index=...)` call succeeds at first launch without operator intervention.

In scope:
- Pre-open serial handshake to `/dev/portapack` (CDC-ACM) sending the `hackrf\n` command
- Polling for USB re-enumeration of the HackRF One before calling `pyhackrf2.open()`
- Graceful no-op when the Portapack serial interface is absent (plain HackRF, already-transitioned device, or Portapack not attached)
- Udev rule that names the Portapack CDC-ACM interface `/dev/portapack`
- Docker changes required to surface the ACM device inside the container
- ROS2 parameters governing the transition (device path, enable flag, timeouts, retries)
- Diagnostics field reporting the last transition outcome

Out of scope for this phase (belongs in a later phase or `pymayhem`):
- Any other Mayhem serial commands beyond the mode-switch
- Runtime re-entry into Mayhem UI mode from HackRF mode
- TX/jamming enablement, firmware upload, DFU bootloader interactions
- Full `pymayhem` library integration — this phase inlines only the single serial command

</domain>

<decisions>
## Implementation Decisions

### Scope-Decision Reversal

- **D-00:** This phase reverses the prior project-level decision in `PROJECT.md:45` and `REQUIREMENTS.md:88` that placed Mayhem firmware control out of scope. The reversal is **bounded** — only the single mode-switch command required to make the HackRF USB endpoint claimable is added. All other Mayhem functionality remains out of scope. PROJECT.md and REQUIREMENTS.md must be updated on phase completion to replace "Mayhem firmware control — separate package (pymayhem)" with a scoped statement acknowledging the mode-switch is in-package.

### Udev + Docker Access

- **D-01:** Udev rule matches on USB `ATTRS{idVendor}` + `ATTRS{idProduct}` (VID:PID only). A single Portapack per deployment is assumed; multi-Portapack fleets are out of scope.
- **D-02:** Symlink name is `/dev/portapack`. The udev rule sets `SYMLINK+="portapack"` on the CDC-ACM interface.
- **D-03:** `docker-compose.yaml` gains `device_cgroup_rules: ['c 189:* rmw', 'c 166:* rmw']` (USB + ACM majors per `.planning/research/PITFALLS.md:540`) and bind-mounts `/dev:/dev` so the host symlink `/dev/portapack` resolves inside the container. The existing `privileged: true` covers it today, but the explicit rules make the device surface auditable and survive a future tightening of the compose file.
- **D-04:** Udev rule file installed to `/etc/udev/rules.d/99-portapack.rules` on the host (not baked into the container image — udev runs on the host, not in the container).

### Transition Handshake

- **D-05:** Serial device opened with `pyserial` at `115200 8N1`. CDC-ACM ignores the baud wire-side but pyserial requires a value.
- **D-06:** Transition sequence: (1) open `/dev/portapack`, (2) write `hackrf\n`, (3) close serial, (4) poll `pyhackrf2.list_devices()` (or equivalent USB VID:PID presence check) every 100 ms for up to `portapack_reenum_timeout_s` seconds, (5) call `pyhackrf2.HackRF(device_index=...)` with retries.
- **D-07:** Startup pre-check uses symlink presence as the sole signal: if `/dev/portapack` does not exist, the node assumes the device is already in HackRF mode or no Portapack is attached. Transition is skipped, and the node proceeds directly to `pyhackrf2.open()`.

### Failure Policy

- **D-08:** If `/dev/portapack` exists but `pyserial.Serial(...)` raises, log an **ERROR** including the pyserial exception and fall through to `pyhackrf2.open()` anyway. This tolerates the stale-symlink case (e.g. Portapack transitioned on a prior boot but udev hasn't re-evaluated).
- **D-09:** If the serial write succeeds but the HackRF never appears within `portapack_reenum_timeout_s`, reopen the serial device, resend `hackrf\n`, and poll for one more full timeout window. If still absent, return `TransitionCallbackReturn.FAILURE` from `on_configure` with a clear log message instructing the operator to power-cycle the Portapack.
- **D-10:** If `pyhackrf2.HackRF(...)` raises after successful re-enumeration detection, retry up to `portapack_open_retries` times with 250 ms delay between attempts. This absorbs the USB kernel-claim race window. If all retries fail, return `FAILURE`.
- **D-11:** All failure paths update the diagnostics field `last_portapack_transition` so operators can diagnose via `/diagnostics` without tailing logs.

### ROS Parameter Surface

- **D-12:** Four new parameters declared in `_declare_parameters()`:
  - `portapack_serial_device` — `str`, default `/dev/portapack`. Empty string force-skips the transition regardless of symlink presence (useful for pure-HackRF CI).
  - `portapack_enable_transition` — `bool`, default `True`. Master off switch.
  - `portapack_reenum_timeout_s` — `float`, default `5.0`. Per-attempt re-enumeration timeout.
  - `portapack_open_retries` — `int`, default `3`. pyhackrf2 open retry count.
- **D-13:** Defaults live as module constants at the top of `hackrf_lifecycle_node.py`, declared in `_declare_parameters()` (same pattern as `device_index` in Phase 4 — see `hackrf_lifecycle_node.py` D-04-01 note in STATE.md line 88). Operators override via `config/hackrf_rx.yaml`.
- **D-14:** None of the Portapack params are declared dynamic — a mid-run mode switch has no legitimate use case. They are read once in `on_configure` before the transition runs.

### Diagnostics

- **D-15:** Extend `_diagnostics_callback` (currently at `hackrf_lifecycle_node.py` around line 204 — `_diag_updater.add('hackrf_status', ...)`) with an additional `last_portapack_transition` key whose value is one of:
  - `skipped` — `portapack_enable_transition=False` or `/dev/portapack` absent
  - `succeeded` — transition completed on first attempt
  - `retried` — transition succeeded on resend
  - `failed` — both attempts exhausted (node will have already returned FAILURE from configure, but the last value is observable via the preceding state)

### Integration Point

- **D-16:** The transition code runs in `on_configure` **between** `self._declare_parameters()` (current line 137) and the `pyhackrf2.HackRF(...)` call (current line 142). New helper: `self._transition_portapack()` — returns a tri-state enum (SKIPPED, SUCCEEDED, FAILED) that feeds the diagnostics field and determines whether to FAIL or continue.

### Claude's Discretion

- Naming of the new helper method(s) and any internal state variables (snake_case, leading underscore per existing convention)
- Exact log message wording (ROS2 logger, no print; severity matches the policy decisions above)
- Whether to factor the polling loop into a small reusable helper vs. inline
- Whether to use `pyusb` for the presence check or `pyhackrf2.list_devices()` (prefer the latter if available on the installed pyhackrf2 version; pyusb is already transitively present via libusb)
- Unit test strategy (prefer mocking `pyserial.Serial` and `pyhackrf2.list_devices`; no live-hardware requirement for unit tests)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Mayhem firmware

- [PortaPack Mayhem USB Serial Console wiki](https://github.com/portapack-mayhem/mayhem-firmware/wiki/USB-Serial-Console) — `hackrf` is the documented command to exit Mayhem UI and enter HackRF USB-SDR mode; prompt is `ch>`

### Project decisions and pitfalls

- `.planning/research/PITFALLS.md` §USB device cgroup (lines 525–541) — USB major 189 and ACM major 166 cgroup rules; the PITFALLS entry explicitly anticipated this phase ("If the serial interface is ever added back")
- `.planning/PROJECT.md` §Out of Scope (line 45) — prior scope decision being reversed by D-00
- `.planning/REQUIREMENTS.md` §Out of Scope (line 88) — same reversal target
- `.planning/STATE.md` §Roadmap Evolution — records the 2026-04-18 addition of this phase

### Existing code this phase modifies

- `hackrf_ros/hackrf_lifecycle_node.py` §`on_configure` (line 135 onward) — integration point
- `hackrf_ros/hackrf_lifecycle_node.py` §`_declare_parameters` (line 343) — new parameter declarations
- `hackrf_ros/hackrf_lifecycle_node.py` §diagnostics callback (~line 204) — new diagnostics field
- `Dockerfile` — pip install line at line 24 (add `pyserial`), apt line (no change required — udev is host-side)
- `docker-compose.yaml` — add `device_cgroup_rules` and ensure `/dev` bind-mount
- `setup.py` line 27 — add `pyserial>=3.5` to `install_requires`
- `launch/hackrf.launch.py` — no changes required (existing configure/activate flow absorbs the new helper)
- `config/hackrf_rx.yaml` — optional overrides for the four new parameters

### External

- `pyserial` docs — `serial.Serial(port, baudrate=115200, timeout=1.0)` is the canonical pattern
- `pyhackrf2` API — confirm `list_devices()` (or equivalent presence probe) is exposed by the installed version; if not, fall back to `pyusb.core.find(idVendor=0x1d50, idProduct=0x6089)` (HackRF One VID:PID) for the poll loop

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- **`_declare_parameters()` pattern**: Phase 4's `device_index` (STATE.md line 88) established the convention — declare constant defaults at module top, `declare_parameter(name, default)` in `_declare_parameters()`, read with `self.get_parameter(name).value` inside `on_configure`. New Portapack params follow this exactly.
- **`_diag_updater.add(key, callback)` pattern**: Already wired at `on_configure` line 202-204. New diagnostics field composes into the same callback.
- **ROS2 logger pattern**: `self.get_logger().info/warn/error(...)` used throughout. No prints.
- **Lifecycle transition return contract**: `TransitionCallbackReturn.SUCCESS` / `FAILURE` — the transition helper must not raise; it maps errors to SUCCESS-with-fallthrough or FAILURE explicitly.

### Established Patterns

- **No stop/start of USB transfers during a configure cycle** (`_STREAM_RESTART_PARAMS`, line 59) — the Portapack transition happens before any HackRF transfer is started, so this rule is satisfied naturally.
- **Regular (not lifecycle) publishers for CycloneDDS ARM64** — not affected by this phase; only `on_configure` wiring changes.
- **4-space indent, snake_case, PascalCase classes, leading `_` for private** — Phase 5 code must match.

### Integration Points

- `on_configure` is the only function that changes. Everything else (activate, deactivate, parameter callback, timers, recorder) is untouched.
- Docker and udev changes are deployment-layer — tested via a hardware checkpoint that boots a Portapack-equipped rig and confirms configure succeeds without manual intervention.

</code_context>

<specifics>
## Specific Ideas

- User explicitly validated the failure-mode sequence: "symlink present but serial fails → log-and-fallthrough" reflects the "device may have transitioned from a previous run" case (the earlier conversation note).
- The cgroup rule `c 166:* rmw` is specifically called out in `.planning/research/PITFALLS.md:541` as the expected addition when the serial interface comes back — this phase is the "if it is ever added back" case that PITFALLS anticipated.
- Portapack VID:PID must be confirmed against a live device during implementation (likely `1d50:6018` for the CDC-ACM interface — HackRF family vendor 1d50 — but should not be hardcoded from memory). Planner must include a one-line hardware-check task: attach Portapack in Mayhem mode, run `udevadm info /dev/ttyACM*` to capture the exact VID:PID pair.

</specifics>

<deferred>
## Deferred Ideas

- **Full `pymayhem` integration** — other Mayhem serial commands (UI navigation, app launch, DFU, file transfer). Belongs in a separate `pymayhem` library as originally scoped; this phase is the narrow exception for the mode-switch only.
- **TX enablement path** — Mayhem firmware also has TX apps; explicitly out of scope per the Key Decision in `PROJECT.md:44` (safety concern).
- **Runtime mode-toggle** — switching back to Mayhem UI at runtime has no robot autonomy use case. If a field-operator ever wants this, it would be a new phase.
- **Multi-Portapack fleets** — D-01 (VID:PID-only udev match) assumes one Portapack per deployment. Multi-unit fleets need `ATTRS{serial}` matching and per-unit symlinks; that is a future phase if the deployment model changes.
- **Portapack firmware version detection** — the `version` serial command exists and could be used to gate behavior on specific Mayhem builds. Not needed for v1 — the `hackrf` command has been stable across Mayhem versions.

</deferred>

---

*Phase: 05-portapack-boot-transition*
*Context gathered: 2026-04-18*
