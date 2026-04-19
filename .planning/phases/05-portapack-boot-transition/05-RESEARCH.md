# Phase 5: Portapack Boot Transition - Research

**Researched:** 2026-04-18
**Domain:** USB CDC-ACM serial handshake → HackRF USB re-enumeration in a ROS2 lifecycle `on_configure` callback, inside Docker on Jetson ARM64
**Confidence:** HIGH on API surfaces, MEDIUM on Mayhem firmware behavioral details (requires hardware verification)

## Summary

Phase 5 narrowly reverses the project's prior "no Mayhem" scope decision to add a single, bounded capability: sending the `hackrf` serial command to a Portapack-equipped HackRF during `on_configure`, waiting for USB re-enumeration, and opening the device. The implementation is well-supported by existing libraries (pyserial 3.5, pyhackrf2 1.0.3) and existing project patterns (`_declare_parameters`, `_diag_updater.add`, `TransitionCallbackReturn.FAILURE`). No architectural change is required.

Three findings are load-bearing for the plan:

1. **The pyhackrf2 enumeration API is `HackRF.enumerate()` — a classmethod returning a list of serial-number strings** — *not* `list_devices()` as the CONTEXT.md prose speculated. The planner must direct implementers to `pyhackrf2.HackRF.enumerate()`. `[VERIFIED: github.com/eizemazal/pyhackrf2 README]`
2. **Returning `TransitionCallbackReturn.FAILURE` from `on_configure` leaves the node in `UNCONFIGURED`; raising an exception sends it to `ErrorProcessing`.** The helper must *return* status, not raise, so the lifecycle state machine behaves predictably for retry via `trigger_configure()`. `[VERIFIED: design.ros2.org/articles/node_lifecycle.html]`
3. **On Linux, opening a CDC-ACM port via pyserial unavoidably toggles DTR/RTS at the kernel layer** — this is fine for our open-write-close pattern (we want the device to accept the command and then reset into HackRF mode), but it means the first few bytes after open may be lost. The implementation must include a tiny post-open settle sleep (~50 ms) before the `write()`. `[VERIFIED: pyserial issue #124, qsantos.fr "Linux always toggles DTR & RTS"]`

**Primary recommendation:** Implement `_transition_portapack()` as a pure-Python helper that returns an enum (`SKIPPED | SUCCEEDED | RETRIED | FAILED`). Let `on_configure` map `FAILED` → `TransitionCallbackReturn.FAILURE` and every other result → fall-through to `pyhackrf2.HackRF(device_index=...)`. Unit-test the helper by mocking `serial.Serial`, `os.path.exists`, and `pyhackrf2.HackRF.enumerate` at module scope.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Scope-Decision Reversal**
- **D-00:** This phase reverses the prior project-level decision in `PROJECT.md:45` and `REQUIREMENTS.md:88` that placed Mayhem firmware control out of scope. The reversal is **bounded** — only the single mode-switch command required to make the HackRF USB endpoint claimable is added. All other Mayhem functionality remains out of scope. PROJECT.md and REQUIREMENTS.md must be updated on phase completion to replace "Mayhem firmware control — separate package (pymayhem)" with a scoped statement acknowledging the mode-switch is in-package.

**Udev + Docker Access**
- **D-01:** Udev rule matches on USB `ATTRS{idVendor}` + `ATTRS{idProduct}` (VID:PID only). A single Portapack per deployment is assumed; multi-Portapack fleets are out of scope.
- **D-02:** Symlink name is `/dev/portapack`. The udev rule sets `SYMLINK+="portapack"` on the CDC-ACM interface.
- **D-03:** `docker-compose.yaml` gains `device_cgroup_rules: ['c 189:* rmw', 'c 166:* rmw']` (USB + ACM majors per `.planning/research/PITFALLS.md:540`) and bind-mounts `/dev:/dev` so the host symlink `/dev/portapack` resolves inside the container. The existing `privileged: true` covers it today, but the explicit rules make the device surface auditable and survive a future tightening of the compose file.
- **D-04:** Udev rule file installed to `/etc/udev/rules.d/99-portapack.rules` on the host (not baked into the container image — udev runs on the host, not in the container).

**Transition Handshake**
- **D-05:** Serial device opened with `pyserial` at `115200 8N1`. CDC-ACM ignores the baud wire-side but pyserial requires a value.
- **D-06:** Transition sequence: (1) open `/dev/portapack`, (2) write `hackrf\n`, (3) close serial, (4) poll `pyhackrf2.list_devices()` (or equivalent USB VID:PID presence check) every 100 ms for up to `portapack_reenum_timeout_s` seconds, (5) call `pyhackrf2.HackRF(device_index=...)` with retries.
- **D-07:** Startup pre-check uses symlink presence as the sole signal: if `/dev/portapack` does not exist, the node assumes the device is already in HackRF mode or no Portapack is attached. Transition is skipped, and the node proceeds directly to `pyhackrf2.open()`.

**Failure Policy**
- **D-08:** If `/dev/portapack` exists but `pyserial.Serial(...)` raises, log an **ERROR** including the pyserial exception and fall through to `pyhackrf2.open()` anyway. This tolerates the stale-symlink case (e.g. Portapack transitioned on a prior boot but udev hasn't re-evaluated).
- **D-09:** If the serial write succeeds but the HackRF never appears within `portapack_reenum_timeout_s`, reopen the serial device, resend `hackrf\n`, and poll for one more full timeout window. If still absent, return `TransitionCallbackReturn.FAILURE` from `on_configure` with a clear log message instructing the operator to power-cycle the Portapack.
- **D-10:** If `pyhackrf2.HackRF(...)` raises after successful re-enumeration detection, retry up to `portapack_open_retries` times with 250 ms delay between attempts. This absorbs the USB kernel-claim race window. If all retries fail, return `FAILURE`.
- **D-11:** All failure paths update the diagnostics field `last_portapack_transition` so operators can diagnose via `/diagnostics` without tailing logs.

**ROS Parameter Surface**
- **D-12:** Four new parameters declared in `_declare_parameters()`:
  - `portapack_serial_device` — `str`, default `/dev/portapack`. Empty string force-skips the transition regardless of symlink presence (useful for pure-HackRF CI).
  - `portapack_enable_transition` — `bool`, default `True`. Master off switch.
  - `portapack_reenum_timeout_s` — `float`, default `5.0`. Per-attempt re-enumeration timeout.
  - `portapack_open_retries` — `int`, default `3`. pyhackrf2 open retry count.
- **D-13:** Defaults live as module constants at the top of `hackrf_lifecycle_node.py`, declared in `_declare_parameters()` (same pattern as `device_index` in Phase 4). Operators override via `config/hackrf_rx.yaml`.
- **D-14:** None of the Portapack params are declared dynamic — a mid-run mode switch has no legitimate use case. They are read once in `on_configure` before the transition runs.

**Diagnostics**
- **D-15:** Extend `_diagnostics_callback` with an additional `last_portapack_transition` key whose value is one of: `skipped`, `succeeded`, `retried`, `failed`.

**Integration Point**
- **D-16:** The transition code runs in `on_configure` **between** `self._declare_parameters()` and the `pyhackrf2.HackRF(...)` call. New helper: `self._transition_portapack()` — returns a tri-state enum (SKIPPED, SUCCEEDED, FAILED) that feeds the diagnostics field and determines whether to FAIL or continue.

### Claude's Discretion

- Naming of the new helper method(s) and any internal state variables (snake_case, leading underscore per existing convention)
- Exact log message wording (ROS2 logger, no print; severity matches the policy decisions above)
- Whether to factor the polling loop into a small reusable helper vs. inline
- Whether to use `pyusb` for the presence check or `pyhackrf2.list_devices()` (prefer the latter if available on the installed pyhackrf2 version; pyusb is already transitively present via libusb)
- Unit test strategy (prefer mocking `pyserial.Serial` and `pyhackrf2.list_devices`; no live-hardware requirement for unit tests)

### Deferred Ideas (OUT OF SCOPE)

- **Full `pymayhem` integration** — other Mayhem serial commands (UI navigation, app launch, DFU, file transfer)
- **TX enablement path** — safety concern per `PROJECT.md:44`
- **Runtime mode-toggle** — switching back to Mayhem UI at runtime
- **Multi-Portapack fleets** — D-01 assumes one Portapack per deployment
- **Portapack firmware version detection** — `version` serial command gating
</user_constraints>

## Project Constraints (from CLAUDE.md)

- **Indent:** 4 spaces; flake8 / pep257 enforced (see `test/test_flake8.py`, `test/test_pep257.py`)
- **Naming:** PascalCase classes, snake_case everything else, leading `_` for private
- **Logging:** ROS2 logger only (`self.get_logger().info/warning/error(...)`); no `print()`
- **Lifecycle:** Enforced via `rclpy.lifecycle.LifecycleNode` with `TransitionCallbackReturn` return values
- **ARM64 / CycloneDDS:** No change required for Phase 5 (transition runs before any publisher is created)
- **No new top-level services:** Phase 5 only changes `on_configure` and `_diagnostics_callback`

## Phase Requirements

Phase 5 introduces an implementation-level capability (boot transition) rather than a v1 functional requirement from REQUIREMENTS.md. No existing `REQ-XX` ID maps to this work; the phase is tracked by its D-00 … D-16 decisions. The planner should derive task IDs directly from the D-XX decisions (e.g., plan 05-02 task addressing D-03 + D-04).

The Validation Architecture section maps each D-XX to one or more testable acceptance criteria in lieu of a REQUIREMENTS.md traceability table.

## Standard Stack

### Core (new in Phase 5)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pyserial | 3.5 | Open `/dev/portapack` CDC-ACM, write `hackrf\n`, close | De-facto Python serial library; ubiquitous on Jetson/ROS images; already on host `[VERIFIED: pip3 show pyserial on host]` |
| pyhackrf2 | 1.0.3 | `HackRF.enumerate()` for presence probe; `HackRF(device_index=)` for open | Already used by driver — same API extended `[VERIFIED: pypi.org/pypi/pyhackrf2/json, 2026-04-18]` |

### Core (already present — no change)

| Library | Version | Purpose |
|---------|---------|---------|
| rclpy | ROS2 Humble bundled | Lifecycle node, `TransitionCallbackReturn` |
| diagnostic_updater | ROS2 Humble bundled | `Updater.add()` + `_diagnostics_callback` |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `pyhackrf2.HackRF.enumerate()` | `pyusb` + `usb.core.find(idVendor=0x1d50, idProduct=0x6089)` | `pyusb` requires libusb backend claim — identical to what `pyhackrf2` does under the hood. Use only as fallback if `enumerate()` is ever unavailable. |
| `pyserial` | Raw `open('/dev/portapack','wb')` | Works, but hides baud/parity setup. `pyserial` is the ecosystem standard and already installs with base Python on Debian/Ubuntu. |
| Full `pymayhem` library | — | Out of scope per D-00: only one command is added |

**Installation:**

```dockerfile
# Dockerfile line 24 — append to existing pip install:
RUN pip3 install --no-cache-dir pyhackrf2 "numpy<2" matplotlib scipy sigmf pyserial
```

```python
# setup.py line 27:
install_requires=['setuptools', 'scipy>=1.11', 'pyserial>=3.5'],
```

**Version verification:**

```bash
# Host check already run during research:
#   pyserial 3.5  (installed in /usr/lib/python3/dist-packages)
#   pyhackrf2 latest 1.0.3 on PyPI (no 1.x+ breaking change since 1.0.0)
pip3 index versions pyhackrf2     # → 1.0.3, 1.0.2, 1.0.1, 1.0.0
curl -s https://pypi.org/pypi/pyserial/json | jq '.info.version'  # → 3.5
```

Both package versions verified 2026-04-18 against PyPI `[VERIFIED: pypi.org JSON API]`.

## Architecture Patterns

### Integration Structure

```
hackrf_ros/
└── hackrf_lifecycle_node.py      # SINGLE FILE MODIFIED
    ├── module constants (top)    # PORTAPACK_DEFAULT_DEVICE, PORTAPACK_REENUM_TIMEOUT_S, ...
    ├── _declare_parameters()     # + 4 new declare_parameter() calls
    ├── on_configure()            # + call to self._transition_portapack() between
    │                             #   _declare_parameters() and pyhackrf2.HackRF(...)
    ├── _transition_portapack()   # NEW helper — returns PortapackTransitionResult enum
    ├── _portapack_send_and_wait()# NEW private helper — inner handshake + poll loop
    └── _diagnostics_callback()   # + stat.add('last_portapack_transition', self._last_portapack_transition)

test/
└── test_portapack_transition.py  # NEW — mocks serial.Serial + HackRF.enumerate

launch/                           # NO CHANGE
config/hackrf_rx.yaml             # + optional overrides for 4 new params
Dockerfile                        # + pyserial in pip install line
docker-compose.yaml               # + device_cgroup_rules
/etc/udev/rules.d/99-portapack.rules  # NEW host file (deployment step, not build artifact)
```

### Pattern 1: Pre-open Serial Handshake via Context Manager

**What:** Open CDC-ACM, write command, close — done by pyserial context manager so failures don't leak file descriptors.

**When to use:** Every entry to `_transition_portapack()` that proceeds past the symlink-exists check.

**Example:**

```python
# Source: github.com/pyserial/pyserial (canonical usage), verified against issue #124
import serial
import time

def _portapack_send_hackrf_command(self, device_path: str) -> bool:
    """Send 'hackrf\\n' to the Portapack. Returns True on write success."""
    try:
        with serial.Serial(
            port=device_path,
            baudrate=115200,
            timeout=1.0,
            write_timeout=1.0,
        ) as port:
            # Linux kernel toggles DTR/RTS on open (unavoidable, pyserial #124).
            # Short settle gives the Mayhem shell time to re-establish the
            # prompt before we write — otherwise the first bytes may be lost.
            time.sleep(0.05)
            port.write(b'hackrf\n')
            port.flush()
        return True
    except (serial.SerialException, OSError) as exc:
        self.get_logger().error(
            f'Portapack serial write failed on {device_path}: {exc}')
        return False
```

Rationale for the 50 ms settle: `[VERIFIED: pyserial issue #124, qsantos.fr "Linux always toggles DTR & RTS" 2025-05-03]` — on Linux the kernel toggles DTR/RTS when the CDC-ACM device node is opened. This causes initial bytes to be dropped on some CDC-ACM firmwares (pyserial issue #410). 50 ms is generous for any realistic USB CDC FIFO flush.

Note on `\n` vs `\r\n`: the Mayhem shell uses `shell_getchar` and accepts either `\n` or `\r` as a line terminator. `hackrf\n` is sufficient. `[CITED: portapack-mayhem/mayhem-firmware wiki — prompt ch>; command `hackrf` Starts the original HackRF firmware]` `[ASSUMED: hackrf\n specifically vs hackrf\r\n — the wiki does not specify; both should work. Plan should default to \n and allow 1-line change if hardware test reveals otherwise.]`

### Pattern 2: Re-enumeration Poll Loop

**What:** After sending `hackrf\n`, the Portapack shuts down the M4 core (Mayhem UI) and re-enumerates as HackRF One. The USB VID:PID changes from `1d50:6018` (Portapack CDC-ACM) to `1d50:6089` (HackRF One). We poll for the HackRF with a deadline.

**When to use:** Immediately after a successful `_portapack_send_hackrf_command()` return.

**Example:**

```python
# Source: pyhackrf2 README — HackRF.enumerate() returns list of serial strings
from pyhackrf2 import HackRF

def _poll_hackrf_present(self, timeout_s: float, poll_interval_s: float = 0.1) -> bool:
    """Return True as soon as HackRF.enumerate() returns a non-empty list,
    or False on timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            serials = HackRF.enumerate()  # classmethod; list of serial-number strings
            if serials:
                return True
        except Exception as exc:  # libusb may transiently raise during re-enum
            self.get_logger().debug(
                f'HackRF.enumerate transient error (likely during re-enum): {exc}')
        time.sleep(poll_interval_s)
    return False
```

Source for the API: `[VERIFIED: github.com/eizemazal/pyhackrf2 README]` — `HackRF.enumerate()` is a classmethod returning `list[str]` of serial numbers.

### Pattern 3: Lifecycle Helper Returns Enum, Never Raises

**What:** `_transition_portapack()` is a classic "result-object" function — it classifies the outcome and returns an enum. The caller (`on_configure`) decides whether to `return TransitionCallbackReturn.FAILURE` or continue.

**Why:** Raising from `on_configure` sends the lifecycle node to `ErrorProcessing` (see Pitfall 2). Returning `FAILURE` leaves it in `UNCONFIGURED` — retryable via `trigger_configure()`. `[VERIFIED: design.ros2.org/articles/node_lifecycle.html]`

**Example:**

```python
import enum

class PortapackTransitionResult(enum.Enum):
    SKIPPED = 'skipped'       # symlink absent or enable=False
    SUCCEEDED = 'succeeded'   # first attempt worked
    RETRIED = 'retried'       # second attempt worked (resend path, D-09)
    FAILED = 'failed'         # both attempts exhausted, or serial write failed fatally
    # Note: the "serial open failed but we'll fall through anyway" case per D-08
    # returns SUCCEEDED-with-warning — see comment in _transition_portapack.


def _transition_portapack(self) -> PortapackTransitionResult:
    """Drive the Portapack from Mayhem UI into HackRF USB-SDR mode.

    Never raises — all error paths are classified and returned as an enum
    so on_configure can map to TransitionCallbackReturn without entering
    the lifecycle ErrorProcessing state.
    """
    import os
    enable = bool(self.get_parameter('portapack_enable_transition').value)
    device = str(self.get_parameter('portapack_serial_device').value)
    if not enable or not device or not os.path.exists(device):
        return PortapackTransitionResult.SKIPPED

    timeout_s = float(self.get_parameter('portapack_reenum_timeout_s').value)
    # Attempt 1
    if self._portapack_send_hackrf_command(device):
        if self._poll_hackrf_present(timeout_s):
            return PortapackTransitionResult.SUCCEEDED
    else:
        # D-08 — stale symlink path. Treat as skipped-success (fall through),
        # NOT as failure, because the HackRF may already be enumerated.
        return PortapackTransitionResult.SKIPPED

    # Attempt 2 (D-09)
    self.get_logger().warning(
        'Portapack did not re-enumerate in %.1f s — resending hackrf command' % timeout_s)
    if self._portapack_send_hackrf_command(device):
        if self._poll_hackrf_present(timeout_s):
            return PortapackTransitionResult.RETRIED

    return PortapackTransitionResult.FAILED
```

### Anti-Patterns to Avoid

- **Raising from the transition helper** → sends the lifecycle node to `ErrorProcessing` on the first `ImportError`/`SerialException`. Return a value instead.
- **Keeping the serial port open** after writing `hackrf\n` → the Portapack M4 shuts down and the CDC-ACM endpoint disappears; pyserial will raise on the implicit close. Close it ourselves immediately.
- **Using `os.path.exists('/dev/ttyACM*')`** via glob rather than the symlink path → on Jetson the ACM node number is unstable; the whole point of the udev rule is to give us a stable name.
- **Mock `rclpy` at the test level** when a pure-Python helper would do → Phase 2/3/4 tests already demonstrate the pattern: keep `_transition_portapack` dependency-injectable and unit test it without `rclpy.init()`.
- **Dynamic parameter declaration of Portapack params** → D-14 explicitly forbids this; a runtime re-trigger has no sensible semantics.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| USB VID:PID enumeration | `subprocess.run(['lsusb'])` parsing | `pyhackrf2.HackRF.enumerate()` | Already transitively available; parses libhackrf's own device list which is authoritative |
| CDC-ACM open/write | `os.open` + `os.write` on raw fd | `serial.Serial(...)` context manager | Handles baud/parity/flow control + cleanup + timeouts; stdlib-like API |
| Async poll-with-timeout | `threading.Event` + wait | Simple `while time.monotonic() < deadline` loop | Overhead of threading for a 5s poll dwarfs the loop cost; also: already blocking in `on_configure` |
| Docker device major lookup | Hardcode from `/proc/devices` at runtime | Hardcoded cgroup rules `c 189:* rmw` + `c 166:* rmw` | Majors are kernel-stable: 189=USB, 166=USB CDC-ACM. PITFALLS.md:540 already confirms. `[VERIFIED: Linux Documentation/admin-guide/devices.txt]` |
| Udev symlink management | Manage from inside container | Install `.rules` file on host via deployment script | udev runs on the host — there is no userspace equivalent inside the container |

**Key insight:** This phase is primarily a *configuration and orchestration* task. The temptation to add abstraction layers (a pymayhem-like wrapper class, a generic serial-handshake framework) is misplaced — this is one command to one device. Inline the 3-line `serial.Serial` context manager and the 10-line poll loop.

## Runtime State Inventory

This phase is additive — it does NOT rename or refactor anything. Runtime-state categories:

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | None | — |
| Live service config | udev rules on the *host* filesystem: `/etc/udev/rules.d/99-portapack.rules` is a new artifact that must be deployed out-of-band (not bundled in the Docker image). Plan MUST include a deployment note. | Manual `sudo cp` + `sudo udevadm control --reload-rules && sudo udevadm trigger` as a deployment task (or documented in README) |
| OS-registered state | None initially. After deployment, udev will have a rule registered; survives reboot. | One-time install |
| Secrets/env vars | None | — |
| Build artifacts | `pyserial` added as a pip dep — the new egg in the site-packages is a build artifact, but no stale artifacts exist (greenfield addition) | None |

**The canonical question — "after every file is updated, what runtime state still holds the old config?"** — Answer: only the udev rule, which is by design host-resident and tracked as a deployment step.

## Common Pitfalls

### Pitfall 1: Linux CDC-ACM DTR/RTS Toggle Loses First Bytes

**What goes wrong:** `serial.Serial(port, ...)` on Linux causes the kernel to toggle DTR and RTS low-then-high at the moment the device node is opened. Some CDC-ACM firmwares drop the first bytes written immediately after open.

**Why it happens:** `[VERIFIED: qsantos.fr/2025/05/03/linux-always-toggles-dtr-rts]` — on Linux, `open(2)` on a TTY unconditionally asserts modem-control lines. Unlike on macOS/Windows, pyserial cannot suppress this without a kernel patch.

**How to avoid:** Sleep ≥ 50 ms after open, before write. Ignore any bytes read during that window.

**Warning signs:** In hardware testing, the `hackrf` command is written but the Portapack never switches modes — suggests the command string was truncated (e.g., device received `ackrf\n`, which is not a valid command). Fix: bump the settle to 100 ms.

### Pitfall 2: Raising from `on_configure` Sends Node to ErrorProcessing

**What goes wrong:** If `_transition_portapack()` (or any code inside `on_configure`) raises an uncaught exception, the ROS2 lifecycle state machine invokes `on_error`. `on_error` by default returns `FAILURE`, which sends the node to `FINALIZED` (a terminal state from which only `destroy` works).

**Why it happens:** Per `[VERIFIED: design.ros2.org/articles/node_lifecycle.html]`: *"If the `onConfigure` callback raises or results in any other result code the node will transition to `ErrorProcessing`."*

**How to avoid:** The helper returns an enum. All `try/except` happens inside the helper. `on_configure` maps `PortapackTransitionResult.FAILED` → `return TransitionCallbackReturn.FAILURE` (stays in UNCONFIGURED).

**Warning signs:** `ros2 lifecycle get /hackrf_node` reports state `finalized` after one failed configure; operator cannot retry without restarting the process.

### Pitfall 3: CDC-ACM Device Node Number Instability Across Reboots

**What goes wrong:** With no udev rule, the Portapack appears at `/dev/ttyACM0` on one boot and `/dev/ttyACM1` on another (depending on cold-plug order with other CDC-ACM devices).

**Why it happens:** Kernel assigns ACM minor numbers in enumeration order.

**How to avoid:** The udev rule (D-02) creates `/dev/portapack` as a stable symlink. The ROS parameter `portapack_serial_device` references that symlink, not the raw ACM node. Documented `[CITED: docs.clearpathrobotics.com Udev Rules tutorial]`.

**Warning signs:** Transition works on dev machine but fails in CI / on second robot — classic "device node moved" symptom.

### Pitfall 4: USB Re-enumeration Race — pyhackrf2 Open Before libusb Sees the Device

**What goes wrong:** `HackRF.enumerate()` returns non-empty, but `HackRF(device_index=0)` fails with `HACKRF_ERROR_NOT_FOUND`. The libhackrf internal cache updates slightly after libusb sees the device.

**Why it happens:** Kernel USB enumeration, libusb device list refresh, and libhackrf's internal device-handle cache do not all update in the same atomic step.

**How to avoid:** The retry loop in D-10 (250 ms × 3) is the specified mitigation. Document in the code: "absorbs the USB kernel-claim race window."

**Warning signs:** First open attempt fails with `NOT_FOUND`, second attempt 250 ms later succeeds.

### Pitfall 5: Docker Container Doesn't See Host Symlink

**What goes wrong:** udev creates `/dev/portapack` on the host, but the container sees only `/dev/bus/usb/...` — the symlink doesn't appear.

**Why it happens:** The existing compose bind-mount is `/dev/bus/usb:/dev/bus/usb` — it only mounts the USB subtree, not the whole `/dev`.

**How to avoid:** D-03 mandates `/dev:/dev` bind-mount (whole-directory), which exposes all host-managed device nodes and symlinks. `[VERIFIED: docs.docker.com/compose device volume semantics]`. Alternatively, add an explicit `devices: - /dev/portapack:/dev/portapack` entry.

**Warning signs:** `docker exec hackrf_ros ls /dev/portapack` returns "No such file"; `docker exec hackrf_ros ls /dev/bus/usb/...` works.

### Pitfall 6: udev Rule Exists But Not Loaded

**What goes wrong:** `.rules` file written to `/etc/udev/rules.d/` but `/dev/portapack` never appears even after plugging in Portapack.

**Why it happens:** udev does not hot-reload rule files. New rules apply to subsequent add events only after `udevadm control --reload-rules`.

**How to avoid:** Deployment procedure:
```bash
sudo cp 99-portapack.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger   # re-fires add events for already-connected devices
```
Verify with `udevadm info /dev/portapack` (should show the matching rule).

**Warning signs:** `ls -la /dev/portapack` → "No such file", but `lsusb` shows 1d50:6018.

## Code Examples

Verified patterns from official sources.

### udev Rule — 99-portapack.rules

```udev
# /etc/udev/rules.d/99-portapack.rules
# Stable symlink for the PortaPack Mayhem CDC-ACM serial interface.
# Vendor 1d50 = Great Scott Gadgets / OpenMoko family (shared by HackRF).
# Product 6018 = PortaPack Mayhem CDC-ACM (VERIFY ON HARDWARE — see plan task).
# When Mayhem exits to HackRF mode, the ACM interface disappears and the
# device re-enumerates as 1d50:6089 (HackRF One) — that interface is handled
# by the existing libusb path and does NOT need a udev rule here.
SUBSYSTEM=="tty", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="6018", SYMLINK+="portapack", MODE="0666", GROUP="dialout"
```

Structure sourced from `[CITED: docs.clearpathrobotics.com/docs/ros1noetic/ros/ros/tutorials/ros101/intermediate/udev_rules/]`; modifier keys (`SYMLINK+=`, `MODE`, `GROUP`) are standard udev. `MODE="0666"` mirrors the existing project convention for SDR devices per `[CITED: github.com/jopohl/urh/wiki/SDR-udev-rules]`. The plan MUST include a task to verify `1d50:6018` against a live device; if a newer Mayhem firmware uses a different product ID, update this rule.

### docker-compose.yaml delta

```yaml
services:
  hackrf:
    # ...existing keys unchanged...
    privileged: true       # existing — redundant with device_cgroup_rules but kept
    devices:
      - /dev/bus/usb:/dev/bus/usb      # existing (HackRF direct USB)
    volumes:
      - /dev:/dev                      # NEW — exposes host /dev/portapack symlink
      - /tmp/.X11-unix:/tmp/.X11-unix:rw
    device_cgroup_rules:               # NEW (D-03)
      - 'c 189:* rmw'                  # USB (HackRF One at 1d50:6089)
      - 'c 166:* rmw'                  # CDC-ACM (Portapack at 1d50:6018)
```

Note: with `privileged: true` present, `device_cgroup_rules` is technically redundant — *privileged grants all device access anyway*. D-03 justifies adding the rules explicitly so the compose file remains correct if `privileged: true` is later tightened. `[VERIFIED: docs.docker.com/reference/compose-file/services/#device_cgroup_rules]`

### on_configure integration (diff-style)

```python
# hackrf_lifecycle_node.py, in on_configure, around line 137-142:

def on_configure(self, state: LifecycleState) -> TransitionCallbackReturn:
    self.get_logger().info('Configuring...')
    self._declare_parameters()

    # === NEW: Phase 5 Portapack boot transition (D-16) ===
    transition_result = self._transition_portapack()
    self._last_portapack_transition = transition_result.value  # for diagnostics
    if transition_result == PortapackTransitionResult.FAILED:
        self.get_logger().error(
            'Portapack transition failed — power-cycle Portapack and retry configure.')
        return TransitionCallbackReturn.FAILURE
    # === END NEW ===

    try:
        import pyhackrf2
        device_index = int(self.get_parameter('device_index').value)
        # D-10: retry to absorb USB kernel-claim race
        retries = int(self.get_parameter('portapack_open_retries').value)
        last_exc = None
        for attempt in range(max(1, retries)):
            try:
                self._hackrf = pyhackrf2.HackRF(device_index=device_index)
                break
            except (RuntimeError, OSError) as e:
                last_exc = e
                time.sleep(0.25)
        else:
            self.get_logger().error(
                f'Failed to open HackRF after {retries} retries: {last_exc}')
            return TransitionCallbackReturn.FAILURE
    except ImportError:
        self.get_logger().error('pyhackrf2 not installed.')
        return TransitionCallbackReturn.FAILURE

    # ...rest of on_configure unchanged...
```

### _declare_parameters additions

```python
# In _declare_parameters(), append after existing declare_parameter calls:

# Portapack boot transition (D-12)
self.declare_parameter('portapack_serial_device', PORTAPACK_DEFAULT_DEVICE,
    ParameterDescriptor(description=(
        'Path to Portapack CDC-ACM device (stable symlink). '
        'Empty string disables the transition.')))
self.declare_parameter('portapack_enable_transition', True,
    ParameterDescriptor(description=(
        'Master off-switch for the Portapack Mayhem→HackRF mode transition.')))
self.declare_parameter('portapack_reenum_timeout_s',
    PORTAPACK_DEFAULT_REENUM_TIMEOUT_S,
    ParameterDescriptor(description=(
        'Per-attempt USB re-enumeration timeout after sending hackrf command.')))
self.declare_parameter('portapack_open_retries',
    PORTAPACK_DEFAULT_OPEN_RETRIES,
    ParameterDescriptor(description=(
        'Number of retries when opening HackRF after re-enumeration (250 ms spacing).')))
```

### Module constants (top of file, near line 59)

```python
# Portapack boot transition defaults (Phase 5, D-13)
PORTAPACK_DEFAULT_DEVICE = '/dev/portapack'
PORTAPACK_DEFAULT_REENUM_TIMEOUT_S = 5.0
PORTAPACK_DEFAULT_OPEN_RETRIES = 3
PORTAPACK_DEFAULT_ENABLE = True
PORTAPACK_DTR_SETTLE_S = 0.05   # pyserial DTR/RTS toggle mitigation
PORTAPACK_POLL_INTERVAL_S = 0.1 # re-enumeration probe cadence
PORTAPACK_OPEN_RETRY_DELAY_S = 0.25  # USB kernel-claim race (D-10)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Mayhem control in separate `pymayhem` package | Single command inlined in driver | 2026-04-18 (D-00) | Eliminates first-boot manual step on Portapack hardware |
| `pyhackrf2.list_devices()` (hypothesis in CONTEXT) | `pyhackrf2.HackRF.enumerate()` (actual) | N/A (correction) | Use the right API name |
| pyhackrf (original) | pyhackrf2 v1.0.3 | pyhackrf2 v1.0.0 forked 2021 | Existing project already uses pyhackrf2 — no change |

**Deprecated/outdated:**
- `pyhackrf` (unmaintained original, `[CITED: pypi.org/project/pyhackrf/]`) — do not use.
- Any reference to `list_devices()` in CONTEXT prose is speculative; the real method is `enumerate()`.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Portapack CDC-ACM VID:PID is `1d50:6018` | Code Examples (udev rule) | udev rule matches nothing → `/dev/portapack` never appears → transition always SKIPPED on Portapack hardware. **Mitigation: plan MUST include a one-line hardware-verify task (`udevadm info /dev/ttyACM* | grep -iE "vendor|product"`).** |
| A2 | `hackrf\n` (single `\n`) is the correct terminator for the Mayhem shell parser | Code Examples, Pattern 1 | Command ignored → transition always FAILED on Portapack hardware. Mitigation: if hardware test fails, try `hackrf\r\n` as a one-line fix. |
| A3 | 50 ms DTR/RTS settle is sufficient; 100 ms is the fallback | Pitfall 1 | First bytes dropped → `hackrf` command truncated → Mayhem ignores it. Mitigation: empirical check in HIL test. |
| A4 | The existing `privileged: true` in docker-compose already provides both cgroup permissions; the new `device_cgroup_rules` lines are documentation/defense-in-depth, not a functional requirement | Pattern: docker-compose, Pitfall 5 | None if `privileged: true` stays. If future hardening drops privileged, the rules become load-bearing. |
| A5 | `HackRF.enumerate()` may transiently raise during USB re-enumeration | Pattern 2 poll loop | If it raises uncaught, the poll aborts early. Mitigation: `try/except Exception` with logger.debug inside the loop (already specified). |
| A6 | Symlink-absent sufficiently distinguishes "plain HackRF" from "Portapack already transitioned" (D-07) | D-07 / Pattern 3 | Both cases map to SKIPPED, which is the correct behavior. Confirmed by user in CONTEXT. |
| A7 | USB major 189 and ACM major 166 are kernel-stable on Jetson | docker-compose, Don't Hand-Roll | Low — these majors are fixed in the kernel's static device table. `[VERIFIED: Linux Documentation/admin-guide/devices.txt]` confirms. |

**Items A1–A3 REQUIRE a hardware-verify task in the plan** — they are the only items where Claude cannot be confident without live hardware.

## Environment Availability

| Dependency | Required By | Available (host) | Version | Fallback |
|------------|------------|------------------|---------|----------|
| pyserial | Phase 5 serial handshake | ✓ (host) | 3.5 | — |
| pyhackrf2 | Presence probe + open | ✗ (host), ✓ (Docker image) | 1.0.3 on PyPI | — |
| Docker / docker-compose v2 | `device_cgroup_rules` syntax | Assumed ✓ (existing deployment) | — | — |
| udev on host | Rule evaluation | Assumed ✓ (Jetson L4T ships udev) | systemd-udevd | Manual `mknod` — not viable for production |
| Physical Portapack | HIL verification of A1–A3 | Unknown to researcher | — | Unit tests cover the code; A1–A3 checkpoint deferred to implementer |

**Missing dependencies with no fallback:** None blocking implementation. A1–A3 assumptions gate HIL validation but not plan execution.

**Missing dependencies with fallback:** pyhackrf2 not installable on host outside Docker — fine, all testing is either source-level (pytest in-repo) or inside the container.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (bundled with Python; tests use `import pytest`) |
| Config file | None — tests run from project root via `pytest test/` or via colcon test |
| Quick run command | `cd /ws && pytest test/test_portapack_transition.py -x -v` |
| Full suite command | `cd /ws && pytest test/ -x` (or `colcon test --packages-select hackrf_ros`) |
| Existing pattern | Source-level AST/text tests (see `test/test_driver_upgrade.py`, `test/test_usb_disconnect.py`) plus mocked-rclpy tests (see `test/test_agc.py`) |

Existing test patterns verified `[VERIFIED: file contents of test/test_*.py, read 2026-04-18]`.

### Phase Requirements → Test Map

Each decision D-XX is mapped to at least one unit test, hardware checkpoint, or diagnostics observation. Decisions that cannot be unit-tested are explicitly flagged HIL.

| Decision | Behavior | Test Type | Automated Command | File |
|----------|----------|-----------|-------------------|------|
| D-00 | PROJECT.md and REQUIREMENTS.md no longer list "Mayhem firmware control" as out-of-scope in the old form | source-text | `grep -L "Mayhem firmware control — separate package" .planning/PROJECT.md .planning/REQUIREMENTS.md` | N/A (docs change) |
| D-01 | udev rule matches on `ATTRS{idVendor}` + `ATTRS{idProduct}` | source-text | `pytest test/test_portapack_transition.py::TestUdevRule::test_matches_vid_pid` | udev-rules/99-portapack.rules (content check) |
| D-02 | udev rule uses `SYMLINK+="portapack"` | source-text | `pytest ...::TestUdevRule::test_symlink_name` | same |
| D-03 | docker-compose.yaml has `device_cgroup_rules` with `c 189:*` and `c 166:*`, and `/dev:/dev` bind | source-text (YAML parse) | `pytest ...::TestComposeCgroup` | docker-compose.yaml |
| D-04 | Udev rule installed to host filesystem — deployment doc, not an automated test | manual checkpoint | `# manual: ls /etc/udev/rules.d/99-portapack.rules on target` | Deployment README |
| D-05 | pyserial opened at 115200 8N1 | source-text | `pytest ...::TestSerialParams::test_baudrate_115200` | test_portapack_transition.py |
| D-06 | Exactly the sequence open→write(`hackrf\n`)→close→poll→open is emitted by the helper when symlink present | unit (mock) | `pytest ...::TestTransitionSequence::test_happy_path` | test_portapack_transition.py |
| D-06 | `HackRF.enumerate()` (not `list_devices()`) is used for the presence probe | source-text | `pytest ...::TestPresenceProbe::test_uses_enumerate` | test_portapack_transition.py |
| D-07 | When `/dev/portapack` absent, helper returns SKIPPED and never opens serial | unit (mock) | `pytest ...::TestSkipPath::test_no_symlink_skips` | test_portapack_transition.py |
| D-08 | When `serial.Serial` raises, helper returns SKIPPED (not FAILED) and logs ERROR | unit (mock) | `pytest ...::TestSerialRaise::test_falls_through` | test_portapack_transition.py |
| D-09 | On first timeout, helper resends command; on second timeout, returns FAILED | unit (mock) | `pytest ...::TestResendPath::test_retried`, `::test_exhausted` | test_portapack_transition.py |
| D-10 | `on_configure` retries `pyhackrf2.HackRF(...)` up to `portapack_open_retries` times | source-text + unit (mock) | `pytest ...::TestOpenRetries::test_three_retries` | test_portapack_transition.py |
| D-11 | Failure paths write to `self._last_portapack_transition` before returning | unit (state check) | `pytest ...::TestDiagField::test_value_after_failure` | test_portapack_transition.py |
| D-12 | Four parameters declared with correct defaults | source-text | `pytest ...::TestDeclareParameters` | test_portapack_transition.py |
| D-13 | Module constants exist at file top | source-text | `pytest ...::TestConstants` | test_portapack_transition.py |
| D-14 | Parameter changes for `portapack_*` are NOT accepted via `_param_callback` after configure (or have no hardware effect) | manual checkpoint | `# manual: ros2 param set /hackrf_node portapack_enable_transition false` — documented as no-op | none |
| D-15 | `_diagnostics_callback` adds `last_portapack_transition` with one of the four values | source-text | `pytest ...::TestDiagCallback::test_includes_field` | test_portapack_transition.py |
| D-16 | `_transition_portapack()` called in `on_configure` between `_declare_parameters` and `pyhackrf2.HackRF(...)` | source-text (AST) | `pytest ...::TestIntegrationPoint::test_call_order` | test_portapack_transition.py |
| A1 (assumption) | Portapack VID:PID matches udev rule | **hardware checkpoint** | `udevadm info /dev/ttyACM0 \| grep -iE "vendor\|product"` with Portapack in Mayhem mode | HIL test script |
| A2 (assumption) | `hackrf\n` terminates correctly | **hardware checkpoint** | Manual — confirm `/dev/portapack` disappears within ~2 s of `echo hackrf > /dev/portapack` | HIL test script |
| A3 (assumption) | 50 ms DTR settle is sufficient | **hardware checkpoint** | Run node with LOG_LEVEL=debug, observe logs across 10 successive configures | HIL test script |
| Backward compat | Plain HackRF path still works (no Portapack attached) | regression | Existing `test/test_driver_upgrade.py` passes unchanged + manual hardware checkpoint | N/A (regression) |

### Sampling Rate

- **Per task commit:** `pytest test/test_portapack_transition.py -x`
- **Per wave merge:** `pytest test/ -x` (full source-level suite)
- **Phase gate:** Full suite green + all three hardware checkpoints (A1, A2, A3) executed with `/dev/portapack` present and `/dev/portapack` absent

### Wave 0 Gaps

- [ ] `test/test_portapack_transition.py` — covers D-01 through D-16 except the HIL-only A1/A2/A3. This is the single new test file.
- [ ] HIL test script (manual, not automated): `scripts/hil_portapack_transition.sh` — runs the three hardware checkpoints and records pass/fail. Not a pytest target; executed by the implementer on a Portapack-equipped rig.
- [ ] No framework install needed — pytest is already bundled.

### Failure-Mode Acceptance Criteria (for D-07 through D-11)

For the planner: each failure mode below must map to at least one unit test OR one diagnostics observation.

| Failure mode | Unit testable | HIL observable | Diagnostics signature |
|---|---|---|---|
| No Portapack attached (D-07) | ✓ (mock `os.path.exists=False`) | ✓ | `last_portapack_transition=skipped` |
| Symlink exists but serial open raises (D-08) | ✓ (mock `serial.Serial` raises) | ✓ (unplug mid-boot) | `last_portapack_transition=skipped` + ERROR log |
| Serial write OK but first timeout (D-09, first attempt) | ✓ (mock `enumerate=[]` × N then `[serial]`) | ✓ (Portapack slow boot) | `last_portapack_transition=retried` (if second attempt works) |
| Both attempts exhausted (D-09, fatal) | ✓ | ✓ (wedged Portapack) | `last_portapack_transition=failed` + `TransitionCallbackReturn.FAILURE` |
| HackRF.enumerate returns non-empty but HackRF() raises (D-10) | ✓ (mock `HackRF()` raises × N) | ✓ (USB race) | `last_portapack_transition=succeeded` + FAILURE from `on_configure` with open-retry error |

## Sources

### Primary (HIGH confidence)

- `github.com/eizemazal/pyhackrf2` README — `HackRF.enumerate()` classmethod returning list of serial-number strings; `HackRF(device_index=...)` constructor `[VERIFIED]`
- `pypi.org/pypi/pyhackrf2/json` — pyhackrf2 1.0.3 is the current version as of 2026-04-18 `[VERIFIED]`
- `pypi.org/pypi/pyserial/json` — pyserial 3.5 current `[VERIFIED]`
- `design.ros2.org/articles/node_lifecycle.html` — FAILURE → UNCONFIGURED; exception → ErrorProcessing `[VERIFIED]`
- `.planning/research/PITFALLS.md:525-541` — USB major 189, ACM major 166, cgroup rule syntax `[VERIFIED in-repo]`
- `github.com/pyserial/pyserial/issues/124` — DTR/RTS toggle on open, Linux specific `[VERIFIED]`
- `qsantos.fr/2025/05/03/linux-always-toggles-dtr-rts/` — kernel-level explanation of DTR/RTS toggle on CDC-ACM open `[VERIFIED]`
- `docs.docker.com/reference/compose-file/services/#device_cgroup_rules` — cgroup rule syntax `c MAJOR:MINOR PERMS` `[VERIFIED]`
- `hackrf_ros/hackrf_lifecycle_node.py` — existing `_declare_parameters`, `_diag_updater.add`, `TransitionCallbackReturn` patterns (lines 135, 202-204, 343) `[VERIFIED in-repo]`
- `test/test_driver_upgrade.py`, `test/test_usb_disconnect.py` — established test conventions (source-level text parse) `[VERIFIED in-repo]`

### Secondary (MEDIUM confidence)

- `github.com/portapack-mayhem/mayhem-firmware/wiki/USB-Serial-Console` — documents that `hackrf` command "Starts the original HackRF firmware"; prompt is `ch>`; does NOT specify line terminator or VID:PID `[CITED]`
- Mayhem firmware source — `cmd_hackrf()` calls `m4_request_shutdown()` + `EventDispatcher::request_stop()` (controlled shutdown, not hardware reset); command matched via `strncmp(argv[0], "hackrf", 5)` (lowercase required) `[CITED via web fetch]`
- Clearpath udev rule tutorial — canonical `SUBSYSTEM`/`ATTRS`/`SYMLINK+=` syntax for ttyACM devices `[CITED]`

### Tertiary (LOW confidence, flagged as assumptions)

- A1: Portapack CDC-ACM VID:PID = `1d50:6018` — project context suggests this, but not verified against canonical Mayhem source or a live device. Hardware verify required. `[ASSUMED]`
- A2: `hackrf\n` (single `\n`) sufficient — Mayhem wiki does not specify terminator. `[ASSUMED]`
- A3: 50 ms DTR settle sufficient — literature-informed estimate, not empirically measured on this hardware. `[ASSUMED]`

## Metadata

**Confidence breakdown:**
- Standard stack (pyserial, pyhackrf2): HIGH — versions verified against PyPI
- Architecture patterns (result-enum helper, open-write-close, poll loop): HIGH — direct application of documented ROS2 lifecycle semantics and pyserial idioms
- Pitfalls (DTR toggle, ErrorProcessing, race): HIGH — all verified against primary sources
- Mayhem protocol specifics (exact terminator, exact VID:PID): MEDIUM — documented command + prompt, but terminator and VID:PID not fully pinned down in docs. Three assumptions A1/A2/A3 are explicitly flagged and have hardware-checkpoint tasks.
- Test strategy: HIGH — matches existing project conventions exactly

**Research date:** 2026-04-18
**Valid until:** 2026-05-18 (30 days — stable domain; pyhackrf2 and pyserial have slow release cadences, and the Mayhem firmware mode-switch command has been stable for years)

## RESEARCH COMPLETE

**Phase:** 5 - Portapack Boot Transition
**Confidence:** HIGH (with 3 flagged assumptions A1/A2/A3 requiring hardware verification during implementation)

### Key Findings

- **Correct pyhackrf2 API is `HackRF.enumerate()` classmethod** (returns list of serial-number strings) — NOT `list_devices()` as speculated in CONTEXT. The planner MUST surface this correction.
- **Helper must RETURN a status enum, never RAISE** — raising from `on_configure` sends the node to `ErrorProcessing` → `FINALIZED`, which is terminal. Returning `TransitionCallbackReturn.FAILURE` keeps the node in `UNCONFIGURED` for retry.
- **Linux CDC-ACM DTR/RTS toggle on open is unavoidable** — requires a 50 ms settle between `serial.Serial(...)` and `write()`. Fine for our open-write-close pattern.
- **udev rule content is standard** — `SUBSYSTEM=="tty", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="6018", SYMLINK+="portapack"`; Portapack VID:PID flagged as assumption A1 pending live-hardware verification.
- **Docker compose changes are defense-in-depth** — `privileged: true` already covers cgroup access; the explicit `device_cgroup_rules` lines (189 and 166) make the device surface auditable per D-03.

### File Created

`/home/mvc/dev_ws/hackrf_ros/.planning/phases/05-portapack-boot-transition/05-RESEARCH.md`

### Confidence Assessment

| Area | Level | Reason |
|------|-------|--------|
| Standard Stack | HIGH | Versions verified against PyPI JSON API today |
| Architecture | HIGH | Direct application of documented ROS2 lifecycle semantics; existing project patterns re-used |
| Pitfalls | HIGH | Each pitfall backed by a primary source (pyserial issue, ROS2 design doc, PITFALLS.md) |
| Mayhem protocol | MEDIUM | Command name and prompt documented; line terminator and VID:PID are assumptions A1/A2 pending HIL |
| Test strategy | HIGH | Matches existing test/*.py conventions exactly |

### Open Questions (resolved via assumptions + HIL checkpoints)

1. **Exact Portapack VID:PID** — handled via A1 hardware-verify task in plan
2. **`\n` vs `\r\n`** — handled via A2; default `\n`, one-line change if HIL reveals otherwise
3. **DTR settle duration** — handled via A3; default 50 ms with 100 ms fallback

### Ready for Planning

Research complete. Planner can now create PLAN.md files. Recommended plan structure:

- **05-01-PLAN.md:** udev rule + docker-compose changes (D-01, D-02, D-03, D-04) — deployment infrastructure
- **05-02-PLAN.md:** `_transition_portapack()` helper + params + constants (D-05 through D-14, D-16) — core code
- **05-03-PLAN.md:** diagnostics field + PROJECT.md/REQUIREMENTS.md scope updates (D-00, D-11, D-15) — documentation + observability
- **05-04-PLAN.md:** Unit tests (all D-XX source-level tests) + HIL checkpoint script (A1, A2, A3) — validation
