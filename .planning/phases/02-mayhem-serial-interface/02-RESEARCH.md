# Phase 2: Mayhem Serial Interface - Research

**Researched:** 2026-03-29
**Domain:** pyserial, Mayhem firmware USB serial protocol, ROS2 services, pyhackrf2 mode coexistence
**Confidence:** HIGH (serial protocol from official Mayhem wiki; pyserial from official docs; mode conflict empirically documented in official wiki)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** MayhemSerial is a separate helper class owned by HackRFNode — clean separation, independently testable. HackRFNode manages its lifecycle (open at startup, close at shutdown).
- **D-02:** Dedicated daemon reader thread with `readline()` loop and `timeout=1.0`. Not a ROS2 timer callback — serial needs its own thread for responsiveness.
- **D-03:** Serial port path is `/dev/ttyACM1` — configurable via ROS2 parameter.
- **D-04:** Assume pyhackrf2 and Mayhem serial coexist. Build integration assuming both work simultaneously. Add fallback later if empirical testing proves otherwise.
- **D-05:** RESEARCH DIRECTIVE: Investigate whether Mayhem firmware has a native SDK or API that could replace pyhackrf2 entirely. If a Mayhem-native IQ streaming interface exists over serial or another path, it would resolve the mode conflict by using a single interface.
- **D-06:** If coexistence fails at runtime, log a clear error explaining the conflict. Do not silently degrade.
- **D-07:** ROS2 services for commands (appstart, setfreq, radioinfo) — request/response pattern with structured responses.
- **D-08:** ROS2 topic for status updates — publish current app state, discovered apps, and radioinfo periodically.
- **D-09:** Runtime discovery only — query `applist` at startup to discover available Mayhem apps. No hardcoded app names. Expose discovered names through the status topic.
- **D-10:** Same exponential backoff reconnection pattern as pyhackrf2 USB (from Phase 1: 1s -> 30s cap, retry indefinitely). Consistent behavior across both interfaces.
- **D-11:** Command timeout is configurable via ROS2 parameter, default 3 seconds. Some commands (appstart) may take longer.
- **D-12:** Garbled or incomplete responses are logged as warnings and the command is retried once. If retry also fails, return error to the service caller.

### Claude's Discretion

- Exact pyserial configuration (baud rate, parity, stop bits for ACM)
- Serial response parsing strategy (line-oriented with \r\n termination)
- ROS2 service message type definitions (custom vs std_srvs)
- Whether to use a command queue or direct send from service callbacks
- Thread synchronization between serial reader and command sender

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| MAY-01 | Serial port lifecycle: open /dev/ttyACM1 at startup, close at shutdown, reconnect on disconnect | pyserial `serial.Serial` open/close; exponential backoff from Phase 1 pattern |
| MAY-02 | applist queried at startup to discover available Mayhem apps (no hardcoded names) | `applist\r\n` command confirmed from official Mayhem wiki; response format documented |
| MAY-03 | appstart command switches between Mayhem apps by discovered short name | `appstart <short_name>\r\n` command confirmed from official Mayhem wiki |
| MAY-04 | setfreq command updates frequency within active app (validates app supports it) | `setfreq <hz>\r\n` documented; app compatibility list known and documented here |
| MAY-05 | radioinfo query returns current device configuration for verification | `radioinfo\r\n` confirmed from official Mayhem wiki; returns freq, bandwidth, sample rate, modulation |
| MAY-06 | Mode conflict between pyhackrf2 and serial verified empirically at startup with clear error if incompatible | Mode conflict architecture documented; empirical test pattern specified |
</phase_requirements>

---

## Summary

Phase 2 adds a `MayhemSerial` helper class (new file: `hackrf_ros/mayhem_serial.py`) that owns a pyserial connection to `/dev/ttyACM1`, a daemon reader thread, and a send/receive locking pattern. The class is instantiated and lifecycle-managed by `HackRFNode` alongside the existing pyhackrf2 interface. Three ROS2 services (appstart, setfreq, radioinfo) and one status topic are wired into `HackRFNode.__init__`.

**Critical D-05 Finding:** Mayhem firmware has NO native IQ streaming interface over serial or any other path that could replace pyhackrf2. The serial console is a human-facing ChibiOS shell for control commands only. IQ streaming requires the HackRF bulk USB interface via libusb/pyhackrf2. The two-interface architecture is mandatory, not optional.

**Critical mode conflict finding:** The official Mayhem wiki states explicitly: "You SHOULD NOT enter HackRF mode when using the serial console." HackRF mode (entered via the `hackrf` serial command or the hardware button) re-enumerates the device as a plain HackRF One and makes the ACM serial interface unavailable. **However**, in normal PortaPack operation (not HackRF mode), pyhackrf2 uses libusb to access the HackRF's bulk transfer endpoints, which are separate USB endpoints from the CDC-ACM serial interface. D-04's assumption — that both coexist in normal PortaPack mode — is the correct architecture to build. The empirical test (MAY-06) must confirm this on the actual hardware.

**Primary recommendation:** Build `MayhemSerial` as a standalone class with `_send_command()` using a threading.Lock, a daemon reader thread with `readline(timeout=1.0)`, and the `applist`-at-startup pattern. Wire into `HackRFNode` with the same exponential backoff reconnect as Phase 1. Use `std_srvs/srv/Trigger` for `radioinfo` and custom `.srv` files for `appstart` and `setfreq`.

---

## D-05 Finding: No Native Mayhem IQ Streaming SDK

**Confidence: HIGH** — Verified from official Mayhem firmware wiki and complete command enumeration.

### What Was Investigated

The D-05 directive asked: does Mayhem firmware have a native SDK or API that could replace pyhackrf2 entirely for IQ streaming?

### Finding: No Such Interface Exists

The complete Mayhem serial command set was reviewed from the official wiki. The commands are:

```
help, exit, info, systime, reboot, dfu, hackrf, sysinfo, radioinfo
screenshot, screenframe, screenframeshort
write_memory, read_memory, pmemreset
ls, mkdir, unlink, filesize, fopen, fclose, fseek, ftell, fread, frb, fwrite, fwb, ftruncate, crc32
button, touch, keyboard
setfreq
rtcget, rtcset
cpld_info, cpld_read, cpld_write
applist, appstart, sendpocsag
gotgps, gotorientation, gotenv, gotlight
accessibility_readall, accessibility_readcurr
sd_over_usb, flash, settingsreset, asyncmsg
```

**None of these commands stream IQ data.** The `screenframe`/`screenframeshort` commands stream pixel data, not RF samples. The `fread`/`frb` commands read SD card files (captured `.C16`/`.C8` files), not live RF. There is no `iqstream`, `rxdata`, `capture_serial`, or equivalent command.

**The `hackrf` command** (in the command list) is the mechanism for entering HackRF mode. It re-enumerates the device as a plain HackRF and destroys the serial interface — it is the mode switch that the wiki warns against, not a streaming API.

### Implication for Architecture

pyhackrf2 cannot be replaced. The architecture remains:
- `/dev/ttyACM1` (CDC-ACM serial) → control commands (app management, frequency, config)
- libusb bulk transfer via pyhackrf2 → IQ sample streaming

The mode conflict (D-04/MAY-06) is about the `hackrf` mode switch — not about normal PortaPack operation. In normal mode, the USB device exposes both the CDC-ACM interface and the HackRF bulk interface simultaneously. pyhackrf2 accesses the bulk endpoints without invoking HackRF mode.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pyserial | 3.5 | Serial communication with /dev/ttyACM1 | Only mature Python serial library; ships with Docker image; no v3.6 exists |
| rclpy (already present) | Humble | ROS2 services and topics | Project standard |
| threading (stdlib) | Python 3.10 | Daemon reader thread, Lock for command serialization | No external dep; Queue.Queue used in Phase 1 |
| queue (stdlib) | Python 3.10 | Thread-safe response passing from reader thread to command sender | Same pattern as Phase 1 iq_queue |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| std_srvs | Humble | `Trigger` service for `radioinfo` (no request params needed) | When a command takes no arguments |
| Custom .srv files | Humble | `appstart` (name param), `setfreq` (freq_hz param) | When request/response needs structured fields |
| std_msgs/String | Humble | Status topic for current app, discovered apps | Simple string payload for status updates |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Custom .srv files | std_srvs/SetBool, std_srvs/Empty | SetBool and Trigger cannot carry string (app name) or int64 (freq_hz) request fields — custom srv is required for appstart/setfreq |
| Daemon reader thread | ROS2 timer callback | Serial readline() with timeout=1.0 blocks for up to 1s; inside a ROS2 timer callback this starves the executor. Daemon thread is correct. |
| threading.Lock for send | asyncio | Driver uses threads not asyncio; asyncio adds dependency for no benefit |

**Installation:**

```bash
pip install "pyserial>=3.5"
```

The package is already available in the Docker image but must be added to `setup.py` `install_requires` and optionally to `package.xml` as an exec dependency note.

**Version verification:** pyserial 3.5 confirmed as current stable from pypi.org/project/pyserial/. No v3.6 exists as of 2026-03-29.

---

## Architecture Patterns

### Recommended Project Structure

```
hackrf_ros/
  hackrf_node.py          # HackRFNode — add serial lifecycle, services, status publisher
  mayhem_serial.py        # NEW: MayhemSerial helper class (this phase)
  iq_plotter_node.py      # Unchanged
```

No new ROS2 node. `MayhemSerial` is a plain Python class, not a node.

New ROS2 service definition files (if custom srv chosen):

```
hackrf_ros/srv/
  AppStart.srv            # request: string app_name / response: bool success, string message
  SetFreq.srv             # request: int64 freq_hz / response: bool success, string message, int64 confirmed_hz
```

### Pattern 1: MayhemSerial Helper Class Structure

**What:** Standalone class that wraps pyserial, owns the reader thread, and exposes high-level command methods with structured responses.

**When to use:** All serial operations go through this class. No direct serial.Serial calls from HackRFNode.

```python
# Source: .planning/research/ARCHITECTURE.md + pyserial 3.5 official docs
import serial
import threading
import queue
import time

class MayhemSerial:
    """
    Serial interface to Mayhem firmware over /dev/ttyACM1.
    Thread-safe: all public methods acquire _serial_lock.
    """

    def __init__(self, port: str, logger, timeout: float = 3.0):
        self._port = port
        self._logger = logger
        self._command_timeout = timeout
        self._serial: serial.Serial | None = None
        self._serial_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._response_queue: queue.Queue = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        self._known_apps: list[str] = []

    def open(self) -> bool:
        """Open serial port and start reader thread. Returns True on success."""
        try:
            self._serial = serial.Serial(
                port=self._port,
                baudrate=115200,    # Conventional for USB-CDC; has no effect on ACM but required non-zero
                timeout=1.0,        # D-02: readline() timeout
                write_timeout=2.0,
            )
            self._stop_event.clear()
            self._reader_thread = threading.Thread(
                target=self._reader_loop, daemon=True, name="mayhem_serial_reader"
            )
            self._reader_thread.start()
            return True
        except serial.SerialException as e:
            self._logger.error(f"MayhemSerial: failed to open {self._port}: {e}")
            return False

    def close(self) -> None:
        """Signal reader thread to stop and close port."""
        self._stop_event.set()
        if self._serial and self._serial.is_open:
            self._serial.close()

    def _reader_loop(self) -> None:
        """Daemon thread: continuously readline() and post to _response_queue."""
        while not self._stop_event.is_set():
            try:
                if self._serial and self._serial.is_open:
                    line = self._serial.readline()  # blocks up to timeout=1.0s
                    if line:
                        self._response_queue.put(line.decode('utf-8', errors='replace').strip())
            except serial.SerialException as e:
                self._logger.error(f"MayhemSerial reader error: {e}")
                self._stop_event.set()  # Signal reconnect needed

    def _send_command(self, cmd: str) -> list[str]:
        """
        Send command, collect response lines until 'ch>' prompt.
        Returns list of response lines (excluding command echo and prompt).
        Raises TimeoutError on timeout, SerialException on port error.
        """
        with self._serial_lock:
            # Drain any stale data before sending
            while not self._response_queue.empty():
                try:
                    self._response_queue.get_nowait()
                except queue.Empty:
                    break
            self._serial.write(f"{cmd}\r\n".encode('utf-8'))
            lines = []
            deadline = time.monotonic() + self._command_timeout
            while time.monotonic() < deadline:
                try:
                    line = self._response_queue.get(timeout=0.1)
                    if line == cmd:          # Skip command echo
                        continue
                    if line.startswith('ch>'):
                        return lines         # Prompt signals end of response
                    if line:
                        lines.append(line)
                except queue.Empty:
                    continue
            raise TimeoutError(f"Command '{cmd}' timed out after {self._command_timeout}s")

    def query_applist(self) -> list[str]:
        """Issue applist, return list of short app names. Caches result."""
        lines = self._send_command('applist')
        # Each line: "<short_name> <full_name> <category>"
        self._known_apps = [line.split()[0] for line in lines if line.strip()]
        return self._known_apps

    def appstart(self, short_name: str) -> bool:
        """Start named app. Returns True if command accepted."""
        lines = self._send_command(f'appstart {short_name}')
        return not any('error' in l.lower() for l in lines)

    def setfreq(self, freq_hz: int) -> bool:
        """Set frequency. Returns True if command accepted."""
        lines = self._send_command(f'setfreq {freq_hz}')
        return not any('error' in l.lower() for l in lines)

    def radioinfo(self) -> dict:
        """Query radioinfo. Returns parsed dict of current radio state."""
        lines = self._send_command('radioinfo')
        # Parse: key: value format per line
        result = {}
        for line in lines:
            if ':' in line:
                k, _, v = line.partition(':')
                result[k.strip().lower()] = v.strip()
        return result
```

### Pattern 2: Integration in HackRFNode

**What:** `MayhemSerial` lifecycle wired into `HackRFNode.__init__` and `destroy_node`. Services registered in `__init__`.

```python
# Source: .planning/phases/02-mayhem-serial-interface/02-CONTEXT.md — integration points
class HackRFNode(Node):
    def __init__(self):
        super().__init__('hackrf_node')
        # ... existing Phase 1 init ...

        # Phase 2: serial parameter
        self.declare_parameter('serial_port', '/dev/ttyACM1')
        self.declare_parameter('serial_command_timeout', 3.0)

        # Phase 2: MayhemSerial (D-01)
        serial_port = self.get_parameter('serial_port').get_parameter_value().string_value
        cmd_timeout = self.get_parameter('serial_command_timeout').get_parameter_value().double_value
        self._mayhem = MayhemSerial(serial_port, self.get_logger(), timeout=cmd_timeout)
        self._serial_connected = False
        self._try_serial_connect()

        # Phase 2: services (D-07)
        self._srv_appstart = self.create_service(AppStart, 'hackrf/appstart', self._handle_appstart)
        self._srv_setfreq  = self.create_service(SetFreq,  'hackrf/setfreq',  self._handle_setfreq)
        self._srv_radioinfo = self.create_service(Trigger,  'hackrf/radioinfo', self._handle_radioinfo)

        # Phase 2: status topic (D-08)
        self._mayhem_status_pub = self.create_publisher(String, '/hackrf/mayhem_status', 10)
        self._status_timer = self.create_timer(5.0, self._publish_mayhem_status)

    def _try_serial_connect(self) -> None:
        """Open serial port; on failure schedule retry with exponential backoff (D-10)."""
        if self._mayhem.open():
            self._serial_connected = True
            self._serial_reconnect_delay = _MIN_RECONNECT_DELAY
            self.get_logger().info("MayhemSerial connected.")
            # D-09: query applist at startup
            try:
                apps = self._mayhem.query_applist()
                self.get_logger().info(f"Mayhem apps discovered: {apps}")
            except Exception as e:
                self.get_logger().warning(f"applist query failed: {e}")
        else:
            self._serial_connected = False
            self.get_logger().error(
                f"Serial open failed. Retrying in {self._serial_reconnect_delay:.0f}s."
            )
            self.create_timer(self._serial_reconnect_delay, self._serial_reconnect_callback)
            self._serial_reconnect_delay = min(
                self._serial_reconnect_delay * 2, _MAX_RECONNECT_DELAY
            )

    def destroy_node(self) -> None:
        # Close serial BEFORE closing pyhackrf2 (CONTEXT.md integration point)
        if self._serial_connected:
            self._mayhem.close()
        # ... existing Phase 1 destroy logic ...
        super().destroy_node()
```

### Pattern 3: Mode Conflict Empirical Test (MAY-06)

**What:** At startup, after both pyhackrf2 and serial are opened, run a validation check that confirms both interfaces respond.

```python
# Source: .planning/research/PITFALLS.md — Pitfall 4 pattern
def _verify_mode_coexistence(self) -> None:
    """MAY-06: Confirm pyhackrf2 and serial both work simultaneously."""
    serial_ok = False
    hackrf_ok = self.is_hackrf_streaming

    if self._serial_connected:
        try:
            info = self._mayhem.radioinfo()
            serial_ok = bool(info)
        except Exception as e:
            self.get_logger().error(f"Mode conflict check: serial radioinfo failed: {e}")

    if hackrf_ok and serial_ok:
        self.get_logger().info(
            "MAY-06: Mode coexistence CONFIRMED — pyhackrf2 streaming and serial both operational."
        )
    elif hackrf_ok and not serial_ok:
        self.get_logger().error(
            "MAY-06: MODE CONFLICT — pyhackrf2 is streaming but Mayhem serial is unresponsive. "
            "The device may be in HackRF mode. Serial commands will fail. "
            "Reboot PortaPack and do NOT press the HackRF hardware button."
        )
    elif not hackrf_ok and serial_ok:
        self.get_logger().warning(
            "MAY-06: pyhackrf2 not streaming (reconnecting) but serial is operational. "
            "Mode conflict cannot yet be confirmed — recheck after pyhackrf2 reconnects."
        )
    else:
        self.get_logger().warning("MAY-06: Both interfaces unavailable at startup — cannot assess mode conflict.")
```

### Pattern 4: Custom Service Definition Files

Since this is an `ament_python` package (no CMakeLists.txt), custom `.srv` files require an additional ROS2 package or the use of standard types. There are two paths:

**Path A (recommended for simplicity — Claude's discretion):** Use `std_srvs/srv/Trigger` for `radioinfo` (no request needed), and define thin services using existing message types. For `appstart` and `setfreq`, create a separate `hackrf_ros_interfaces` CMake package to hold the `.srv` definitions.

**Path B (simpler but less structured):** Use `std_srvs/srv/SetBool` + a ROS2 parameter for the string argument. This is hacky and not recommended.

**Path C (correct long-term — Claude's discretion):** Add a CMake-based interfaces package `hackrf_ros_interfaces` alongside the existing Python package. This is the standard ROS2 pattern for Python nodes that need custom services. It adds one more package to the workspace but is the proper approach.

**Recommendation (Path C, detailed):**

```
dev_ws/
  hackrf_ros/           # existing Python package (unchanged structure)
  hackrf_ros_interfaces/ # NEW: CMake package for .srv files
    CMakeLists.txt
    package.xml
    srv/
      AppStart.srv
      SetFreq.srv
      MayhemStatus.msg  # optional, for status topic
```

`AppStart.srv`:
```
string app_name
---
bool success
string message
```

`SetFreq.srv`:
```
int64 freq_hz
---
bool success
string message
int64 confirmed_hz
```

### Anti-Patterns to Avoid

- **Blocking serial.readline() in a ROS2 timer callback:** Causes executor starvation for up to `timeout` seconds per tick. Always use the daemon reader thread.
- **Sending commands from multiple threads without _serial_lock:** Interleaves bytes on the wire; responses are mis-routed to the wrong command's response queue.
- **Parsing responses with a fixed line count:** The Mayhem shell prompt (`ch>`) is the only reliable terminator. Do not assume a fixed number of response lines per command.
- **Calling the `hackrf` serial command from the driver:** This command switches the device into HackRF-only mode and destroys the serial interface. Never issue it programmatically.
- **Assuming the `ch>` prompt appears on its own line:** The ChibiOS shell may include the command echo on the same line or prefix the prompt. Drain until the `ch>` prefix is seen anywhere on a line.
- **Opening serial.Serial with port= in the constructor:** Pass `port=None` and call `open()` separately to allow retry logic without reconstructing the object each time.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Serial port I/O | Custom USB CDC read/write | `pyserial.Serial` | ACM flow control, timeout handling, platform normalization |
| Thread-safe response routing | Custom pipe between threads | `queue.Queue` (stdlib) | Already used in Phase 1; same pattern; proven thread-safe under CPython |
| Exponential backoff reconnect | Custom timer math | Reuse `_MIN_RECONNECT_DELAY` / `_MAX_RECONNECT_DELAY` + pattern from Phase 1 | Identical problem; consistent behavior requirement from D-10 |
| ROS2 service boilerplate | Custom request/response | `create_service()` + `.srv` files | ROS2 standard; handles serialization, introspection, `ros2 service call` CLI compatibility |
| Serial command echo stripping | Custom line prefix detection | `_send_command()` echo-skip by comparing to sent command | ChibiOS echoes every command; stripping on match is the correct pattern |

**Key insight:** The ChibiOS shell is designed for humans, not programs. The `_send_command()` pattern (drain stale data → write → collect until `ch>`) is the only reliable way to drive it programmatically. Every shortcut (fixed timeouts, line counts, prefix matching) produces false positives in edge cases.

---

## Runtime State Inventory

This is not a rename/refactor phase. No runtime state inventory required.

---

## Common Pitfalls

### Pitfall 1: Mode Conflict — `hackrf` Command Destroys Serial Interface

**What goes wrong:** The `hackrf` serial command re-enumerates the PortaPack as a plain HackRF One. The ACM serial interface disappears. Calling pyhackrf2 while in this mode works, but serial is dead.

**Why it happens:** HackRF mode is a distinct USB personality. The CDC-ACM and HackRF bulk interfaces are not simultaneously available in HackRF mode.

**How to avoid:** Never issue the `hackrf` serial command from the driver. Build under D-04's assumption (normal PortaPack mode exposes both interfaces). MAY-06 empirical test confirms coexistence on the actual device.

**Warning signs:** `/dev/ttyACM1` disappears from `ls /dev/serial/by-id/` while `hackrf_info` on the host succeeds.

### Pitfall 2: ChibiOS Shell Response Has No Fixed Terminator (Except the Prompt)

**What goes wrong:** Parsing responses with `serial.read(N)` or assuming N lines per command produces partial/combined responses when commands overlap or take variable time.

**Why it happens:** The Mayhem serial interface is a ChibiOS interactive shell, not a protocol. Response length varies by command and app state.

**How to avoid:** Read until `ch>` prompt. Implement a bounded timeout (D-11: default 3s, configurable). On timeout, log warning and retry once (D-12). If retry fails, return error to service caller.

**Warning signs:** Response lines contain fragments of previous command outputs. `appstart` returns success but wrong app is running.

### Pitfall 3: Serial Device Path Instability in Docker

**What goes wrong:** `/dev/ttyACM1` is assigned by kernel enumeration order. Replug or container restart can produce `/dev/ttyACM0` or `/dev/ttyACM2`.

**Why it happens:** ACM numbering is assigned at USB enumeration time, not statically.

**How to avoid:** Use `/dev/serial/by-id/` symlinks in Docker device mappings for stable paths. Expose serial port as a ROS2 parameter (D-03) so it can be overridden without code changes. The reconnect loop (D-10) handles transient path failures.

**Warning signs:** Serial opens successfully (no exception) but commands time out — wrong device behind the path.

### Pitfall 4: Command Sent Before Reader Thread Is Ready

**What goes wrong:** If `query_applist()` is called immediately after `open()` before the reader thread has started, the response arrives before the queue is being drained and is missed.

**Why it happens:** Thread startup is not instantaneous. There is a small window between `thread.start()` and the first `readline()` call in the reader loop.

**How to avoid:** Add a brief thread-readiness signal in `_reader_loop`: set a `threading.Event` after the first successful read or after a small sleep. Alternatively, drain the response queue with a short initial timeout before sending the first command.

### Pitfall 5: Service Callbacks Block If _send_command() Holds _serial_lock

**What goes wrong:** If a ROS2 service callback calls `_mayhem.appstart()` and `_send_command()` is already blocked waiting for a response from a previous command (e.g., reconnect probe), the service callback hangs. With a `SingleThreadedExecutor`, this starves all other callbacks.

**Why it happens:** `_serial_lock` serializes all commands. If the current command is slow (e.g., `appstart` loading a new app), the lock hold time can exceed the service client's timeout.

**How to avoid:** Use a `MultiThreadedExecutor` with a dedicated callback group for serial services (architecture research from `.planning/research/ARCHITECTURE.md` already specifies this). Service callbacks run in the executor thread pool, not the main ROS2 thread. This means service callbacks can block without starving the IQ publisher timer.

### Pitfall 6: setfreq Sent to Incompatible App

**What goes wrong:** `setfreq` only works in specific Mayhem apps. Sending it while an incompatible app is running returns an error or silently fails.

**Why it happens:** The command is app-context-dependent, not a global radio setter.

**How to avoid:** The `setfreq` service handler should check (or log) the current active app. Document the compatible apps. Do not reject the command — let Mayhem return the error and propagate it to the service response.

**Apps that support setfreq:** Audio, Capture, ERT, POCSAG, APRS, Level, Looking Glass, Sonde, SubGHZd, Weather (from `.planning/research/FEATURES.md`).

---

## Code Examples

### Opening Serial Port (pyserial 3.5)

```python
# Source: pyserial 3.5 official docs — https://pyserial.readthedocs.io/en/latest/pyserial_api.html
import serial

ser = serial.Serial()
ser.port = '/dev/ttyACM1'
ser.baudrate = 115200    # Conventional for USB-CDC; USB ACM ignores baud, transfers at USB speed
ser.timeout = 1.0        # readline() returns after 1s even if no newline received
ser.write_timeout = 2.0
ser.open()               # Separate open() allows retry without reconstructing object
```

### Daemon Reader Thread with queue.Queue

```python
# Source: Python 3 queue module docs + pyserial 3.5 docs
import threading
import queue

response_queue = queue.Queue()

def reader_loop(ser, stop_event, rq):
    while not stop_event.is_set():
        try:
            line = ser.readline()      # blocks up to timeout=1.0s
            if line:
                rq.put(line.decode('utf-8', errors='replace').strip())
        except serial.SerialException:
            stop_event.set()           # Signal reconnect

t = threading.Thread(target=reader_loop, args=(ser, stop_event, response_queue), daemon=True)
t.start()
```

### Collecting Response Until ch> Prompt

```python
# Source: analysis of ChibiOS shell behavior from Mayhem wiki and community usage
def _send_command(self, cmd: str) -> list[str]:
    with self._serial_lock:
        # Drain stale data (D-12: garbled response protection)
        while not self._response_queue.empty():
            try:
                self._response_queue.get_nowait()
            except queue.Empty:
                break
        self._serial.write(f"{cmd}\r\n".encode('utf-8'))
        lines = []
        deadline = time.monotonic() + self._command_timeout
        while time.monotonic() < deadline:
            try:
                line = self._response_queue.get(timeout=0.1)
                if line == cmd:          # Strip echo of sent command
                    continue
                if 'ch>' in line:        # ChibiOS shell prompt = end of response
                    return lines
                if line:
                    lines.append(line)
            except queue.Empty:
                continue
        raise TimeoutError(f"'{cmd}' no prompt within {self._command_timeout}s")
```

### ROS2 Service Handler Pattern

```python
# Source: ROS2 Humble tutorial — https://docs.ros.org/en/humble/Tutorials/Beginner-Client-Libraries/Writing-A-Simple-Py-Service-And-Client.html
from std_srvs.srv import Trigger

def _handle_radioinfo(self, request, response):
    """Service handler: issue radioinfo, return parsed state."""
    try:
        info = self._mayhem.radioinfo()
        response.success = bool(info)
        response.message = str(info)
    except TimeoutError as e:
        response.success = False
        response.message = f"radioinfo timeout: {e}"
    except Exception as e:
        response.success = False
        response.message = f"radioinfo error: {e}"
    return response
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Manual serial read loops | pyserial readline() + daemon thread | Established pattern | No migration needed; pyserial 3.5 is current |
| Custom ROS2 service message packages | std_srvs when applicable, custom .srv for typed params | ROS2 Humble | Use Trigger for no-arg commands; custom srv for parameterized commands |
| Polling serial in timer callback | Daemon reader thread feeding queue.Queue | Phase 1 established queue pattern | Consistent with Phase 1 architecture |

**Deprecated/outdated:**
- pyserial-asyncio: Irrelevant — driver is threaded, not asyncio. REQUIREMENTS.md explicitly calls this out-of-scope.
- pyserial v2.x: Ancient, not present in any current environment.

---

## Open Questions

1. **Does the actual hardware expose both USB interfaces simultaneously in normal PortaPack mode?**
   - What we know: The official wiki only warns against entering HackRF MODE (via the `hackrf` command or button). In normal PortaPack operation, both USB interfaces logically exist on the USB composite device.
   - What's unclear: Whether pyhackrf2's libusb calls interfere with the ACM serial interface at the OS driver level. This depends on the kernel's USB composite device handling and was not confirmed by official docs.
   - Recommendation: MAY-06 empirical test is the resolution. Build under D-04's assumption; the empirical test is the gate. If coexistence fails, log clearly per D-06 and document a mode-switch state machine as future work.

2. **Exact format of `applist` response lines**
   - What we know: Documented as `<short_name> <full_name> <category>` per line, terminated by `ch>` prompt.
   - What's unclear: Whether short_name contains spaces (would break `split()[0]` parsing), whether some lines have a different format (header line?), what the exact app short names are on the target firmware version.
   - Recommendation: Log the raw `applist` output at INFO level on first boot. Parse defensively: take everything before the first space as short_name, log the full line.

3. **Custom srv package placement**
   - What we know: ament_python packages cannot define `.srv` files; a CMake package is required for rosidl generation.
   - What's unclear: Whether the planner should create a new `hackrf_ros_interfaces` package in the same workspace or find another approach.
   - Recommendation: Create `hackrf_ros_interfaces/` as a sibling package. It is a one-time setup cost and the standard ROS2 pattern.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| pyserial | MAY-01 through MAY-05 | Yes | 3.5 | None needed |
| Python 3.10 | All | Yes | 3.10.12 | — |
| /dev/ttyACM1 | MAY-01 | Not verifiable in CI | — | Reconnect loop handles absence at startup |
| pyhackrf2 | MAY-06 (coexistence test) | Present (Phase 1 dependency) | — | — |
| std_srvs | MAY-03, MAY-04, MAY-05 | Yes (ROS2 Humble) | Humble | — |
| rosidl (for custom srv) | MAY-03, MAY-04 (if custom srv) | Yes (package.xml already has rosidl_default_generators) | Humble | Use string-encoded commands if interfaces package cannot be created |

**Missing dependencies with no fallback:** None — all required software is present.

**Note on /dev/ttyACM1:** The device is not available in the development environment. All serial code must be written to handle `serial.SerialException` on open and use the reconnect loop. Unit tests must mock `serial.Serial`.

---

## Project Constraints (from CLAUDE.md)

The following directives from `CLAUDE.md` apply to Phase 2 planning and implementation:

| Directive | Applies To |
|-----------|-----------|
| Python 3, rclpy, ament_python | `mayhem_serial.py` is a Python module; no new C/C++ |
| Node files: `{name}_node.py` | `mayhem_serial.py` is a helper class, not a node file — name is correct |
| PascalCase for classes | `MayhemSerial` is correct |
| snake_case for methods | `query_applist`, `_send_command`, `_reader_loop` are correct |
| Private methods prefixed with `_` | `_reader_loop`, `_send_command`, `_serial_lock` are correct |
| `self.get_logger()` for logging (no bare print) | MayhemSerial accepts a logger reference; no print statements |
| Broad `except Exception as e` with f-string context | Applied in all service handlers and serial error paths |
| Graceful degradation: node continues if serial fails | Serial failure does not stop ROS2 node; reconnect loop handles it |
| ROS2 parameters use snake_case | `serial_port`, `serial_command_timeout` are correct |
| Cleanup in `destroy_node()` | `self._mayhem.close()` called before `super().destroy_node()` |
| GSD workflow enforcement | All code changes via `/gsd:execute-phase` |
| Docker deployment: empyreanlattice/hackrf_ros:humble | pyserial must be in pip install block in Dockerfile |
| `_stop_event` (threading.Event) for clean shutdown | MayhemSerial uses the same pattern; reader loop checks `_stop_event` |

---

## Sources

### Primary (HIGH confidence)

- Official Mayhem firmware USB Serial Console wiki: https://github.com/portapack-mayhem/mayhem-firmware/wiki/USB-Serial-Console — complete command list, mode conflict warning, connection parameters
- pyserial 3.5 API docs: https://pyserial.readthedocs.io/en/latest/pyserial_api.html — Serial constructor, readline, timeout semantics
- ROS2 Humble service tutorial: https://docs.ros.org/en/humble/Tutorials/Beginner-Client-Libraries/Writing-A-Simple-Py-Service-And-Client.html — create_service pattern
- std_srvs Humble docs: https://docs.ros.org/en/humble/p/std_srvs/ — Trigger, SetBool service definitions
- .planning/research/STACK.md — pyserial version, Mayhem protocol, serial connection params
- .planning/research/ARCHITECTURE.md — MayhemSerial design, thread model, lock strategy
- .planning/research/PITFALLS.md — mode conflict (Pitfall 4), serial path instability (Pitfall 7), response parsing (Pitfall 9)
- .planning/research/FEATURES.md — complete command reference, setfreq-compatible apps

### Secondary (MEDIUM confidence)

- Mayhem HackRF wiki page: https://github.com/portapack-mayhem/mayhem-firmware/wiki/HackRF — HackRF mode behavior (hardware button/serial command distinction)
- MayhemHub README: https://github.com/portapack-mayhem/MayhemHub/blob/main/README.md — uses Web Serial API; confirms no higher-level protocol beyond the serial console

### Tertiary (LOW confidence)

- Community: "portapack stuck in HackRF mode" issue #340 — hardware failure context; limited protocol info

---

## Metadata

**Confidence breakdown:**

- Standard stack: HIGH — pyserial 3.5 confirmed from PyPI; stdlib threading/queue; ROS2 Humble std_srvs from official docs
- Architecture: HIGH — follows established Phase 1 patterns (queue.Queue, daemon threads, exponential backoff); MayhemSerial design from project architecture research
- Mayhem protocol: HIGH — command list from official wiki; baud rate from established convention for USB-CDC ACM
- Mode conflict: MEDIUM — official wiki confirms HackRF mode is incompatible with serial; normal PortaPack mode coexistence is architecturally sound but requires empirical hardware test (MAY-06)
- D-05 finding (no native SDK): HIGH — complete command enumeration confirms no IQ streaming over serial

**Research date:** 2026-03-29
**Valid until:** 2026-06-29 (pyserial 3.5 stable; Mayhem protocol stable; ROS2 Humble LTS)
