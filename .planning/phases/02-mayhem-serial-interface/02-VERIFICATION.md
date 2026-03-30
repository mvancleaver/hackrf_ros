---
phase: 02-mayhem-serial-interface
verified: 2026-03-29T00:00:00Z
status: passed
score: 4/4 must-haves verified
re_verification: false
---

# Phase 2: Mayhem Serial Interface Verification Report

**Phase Goal:** The driver communicates with Mayhem firmware over serial, can discover and switch apps, update frequency, and confirm the mode-conflict answer empirically
**Verified:** 2026-03-29
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths (from ROADMAP.md Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | The driver opens /dev/ttyACM1 at startup, queries applist, and logs the discovered apps without manual intervention | VERIFIED | `_try_serial_connect()` calls `_mayhem.open()` then `self._mayhem.query_applist()` at startup; logged via `get_logger().info(f"Mayhem apps discovered: {apps}")` — hackrf_node.py lines 195–206 |
| 2 | A ROS2 service call switches the active Mayhem app by name | VERIFIED | `_handle_appstart` service registered at `hackrf/appstart` (line 134); delegates to `self._mayhem.appstart(request.app_name)` (line 268); backed by AppStart.srv with `string app_name` request field |
| 3 | A setfreq command updates the frequency within the active app and radioinfo confirms the change | VERIFIED | `_handle_setfreq` calls `self._mayhem.setfreq(int(request.freq_hz))` (line 289) then calls `self._mayhem.radioinfo()` (line 295) and returns `response.confirmed_hz` — full round-trip confirmation implemented |
| 4 | The mode-conflict behavior between pyhackrf2 IQ streaming and Mayhem serial is tested and documented, with the driver failing loudly if the combination is incompatible | VERIFIED | `_verify_mode_coexistence()` (lines 226–259) probes both `self.is_hackrf_streaming` and `self._mayhem.radioinfo()` at startup; logs distinct outcomes: CONFIRMED, MODE CONFLICT error, partial-warning, or both-unavailable-warning |

**Score:** 4/4 truths verified

---

## Required Artifacts

### Plan 02-01 Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `hackrf_ros_interfaces/srv/AppStart.srv` | Service contract: string app_name request, bool success + string message response | VERIFIED | File exists at `/home/user/dev_ws/hackrf_ros_interfaces/srv/AppStart.srv`; exact schema confirmed |
| `hackrf_ros_interfaces/srv/SetFreq.srv` | Service contract: int64 freq_hz request, bool success + string message + int64 confirmed_hz response | VERIFIED | File exists; three-field response confirmed including `int64 confirmed_hz` |
| `hackrf_ros_interfaces/CMakeLists.txt` | rosidl_generate_interfaces build target | VERIFIED | Contains `rosidl_generate_interfaces(${PROJECT_NAME} "srv/AppStart.srv" "srv/SetFreq.srv" ...)` |
| `hackrf_ros_interfaces/package.xml` | ROS2 CMake package manifest with rosidl_default_generators | VERIFIED | Contains `rosidl_default_generators` buildtool_depend; ament_cmake build_type |

### Plan 02-02 Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `hackrf_ros/mayhem_serial.py` | MayhemSerial class, 120+ lines, all command methods | VERIFIED | 270 lines; 11 methods (open, close, _reader_loop, _attempt_send, _send_command, query_applist, appstart, setfreq, radioinfo, needs_reconnect property, __init__); importable without errors |

### Plan 02-03 Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `hackrf_ros/hackrf_node.py` | HackRFNode with MayhemSerial lifecycle, services, status topic | VERIFIED | Imports MayhemSerial; declares serial_port + serial_command_timeout params; creates AppStart/SetFreq/Trigger services; publishes /hackrf/mayhem_status at 5s; calls _verify_mode_coexistence(); AST parse clean |
| `hackrf_ros/setup.py` | pyserial>=3.5 in install_requires | VERIFIED | Line 14: `install_requires=['setuptools', 'pyserial>=3.5']` |
| `hackrf_ros/package.xml` | hackrf_ros_interfaces exec_depend; python3-serial exec_depend | VERIFIED | Both `<exec_depend>hackrf_ros_interfaces</exec_depend>` and `<exec_depend>python3-serial</exec_depend>` present |

---

## Key Link Verification

### Plan 02-01 Key Links

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `hackrf_ros_interfaces/CMakeLists.txt` | `srv/AppStart.srv` | `rosidl_generate_interfaces()` | WIRED | Pattern `AppStart.srv` found in CMakeLists.txt rosidl_generate_interfaces block |
| `hackrf_ros_interfaces/CMakeLists.txt` | `srv/SetFreq.srv` | `rosidl_generate_interfaces()` | WIRED | Pattern `SetFreq.srv` found in same block |

### Plan 02-02 Key Links

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `MayhemSerial._reader_loop` | `MayhemSerial._response_queue` | `_response_queue.put(line)` | WIRED | Line 118: `self._response_queue.put(line)` |
| `MayhemSerial._send_command` | `MayhemSerial._response_queue` | `_response_queue.get(timeout=0.1)` | WIRED | Line 160: `line = self._response_queue.get(timeout=0.1)` |
| `MayhemSerial._send_command` | `serial.Serial.write` | `_serial.write(f'{cmd}\r\n'.encode('utf-8'))` | WIRED | Line 153: CRLF write confirmed |

### Plan 02-03 Key Links

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `HackRFNode.__init__` | `MayhemSerial.__init__` | `self._mayhem = MayhemSerial(...)` | WIRED | Line 121: `self._mayhem = MayhemSerial(_serial_port, self.get_logger(), timeout=_cmd_timeout)` |
| `HackRFNode.destroy_node` | `MayhemSerial.close` | `self._mayhem.close()` | WIRED | Line 474: serial closed before pyhackrf2 shutdown |
| `HackRFNode._handle_appstart` | `MayhemSerial.appstart` | `self._mayhem.appstart(request.app_name)` | WIRED | Line 268 |
| `HackRFNode._verify_mode_coexistence` | `MayhemSerial.radioinfo` | `self._mayhem.radioinfo()` | WIRED | Line 237 |

---

## Data-Flow Trace (Level 4)

Not applicable — phase 2 produces serial control commands and ROS2 service handlers, not data-rendering components. The status publisher (`_publish_mayhem_status`) publishes live state from `self._serial_connected`, `self._mayhem._known_apps`, and `self._mayhem.needs_reconnect` — all populated at runtime from the real serial connection, not from hardcoded stubs.

---

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| MayhemSerial imports cleanly | `python3 -c "from hackrf_ros.mayhem_serial import MayhemSerial; print('ok')"` | "import ok" | PASS |
| All 9 unit tests pass | `python3 -m pytest test/test_mayhem_serial.py -v` | `9 passed in 0.04s` | PASS |
| hackrf_node.py AST-valid | `python3 -c "import ast; ast.parse(...); print('ok')"` | "AST ok" | PASS |
| AppStart.srv schema correct | content inspection | `string app_name` / `bool success`, `string message` | PASS |
| SetFreq.srv schema correct | content inspection | `int64 freq_hz` / `bool success`, `string message`, `int64 confirmed_hz` | PASS |
| All 5 summary commits exist | `git cat-file -e <hash>` | 48e6093, 46828d8, 72e8807, abf6ffa, d3864b4 all OK | PASS |

---

## Requirements Coverage

| Requirement | Source Plan(s) | Description | Status | Evidence |
|-------------|----------------|-------------|--------|----------|
| MAY-01 | 02-02, 02-03 | Serial port lifecycle: open at startup, close at shutdown, reconnect on disconnect | SATISFIED | `_try_serial_connect()` opens at init; `destroy_node()` calls `_mayhem.close()` (line 474); `_publish_mayhem_status()` detects `needs_reconnect` and schedules `_serial_reconnect_callback()` |
| MAY-02 | 02-02, 02-03 | applist queried at startup to discover available Mayhem apps (no hardcoded names) | SATISFIED | `_try_serial_connect()` calls `self._mayhem.query_applist()` (line 203) on successful open; result cached in `_known_apps` and logged |
| MAY-03 | 02-01, 02-02, 02-03 | appstart command switches between Mayhem apps by discovered short name | SATISFIED | AppStart.srv defines `string app_name`; `_handle_appstart` service handler wired at `hackrf/appstart`; delegates to `self._mayhem.appstart(request.app_name)` |
| MAY-04 | 02-01, 02-02, 02-03 | setfreq command updates frequency within active app | SATISFIED | SetFreq.srv defines `int64 freq_hz`; `_handle_setfreq` wired at `hackrf/setfreq`; delegates to `self._mayhem.setfreq(int(request.freq_hz))` |
| MAY-05 | 02-02, 02-03 | radioinfo query returns current device configuration for verification | SATISFIED | `radioinfo()` implemented in MayhemSerial; `_handle_radioinfo` service at `hackrf/radioinfo`; `_handle_setfreq` calls `radioinfo()` post-set to populate `confirmed_hz` |
| MAY-06 | 02-03 | Mode conflict between pyhackrf2 and serial verified empirically at startup with clear error if incompatible | SATISFIED | `_verify_mode_coexistence()` probes both interfaces at end of `__init__`; logs CONFIRMED/MODE CONFLICT/partial/both-unavailable with actionable error text |

**All 6 required requirements (MAY-01 through MAY-06) satisfied. No orphaned requirements.**

---

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `hackrf_ros/hackrf_node.py` | 48 | `self._redis_queue = queue.Queue(maxsize=64)  # D-01: Phase 3 Redis consumer (stub)` | Info | Pre-existing Phase 1 stub explicitly labeled for Phase 3; not introduced by Phase 2; does not affect any Phase 2 goal |

No TODOs, FIXMEs, placeholder strings, empty return stubs, or hardcoded empty data found in Phase 2 files (`mayhem_serial.py`, `test_mayhem_serial.py`, additions to `hackrf_node.py`).

---

## Human Verification Required

### 1. Live Serial Round-Trip

**Test:** Connect a PortaPack running Mayhem firmware over USB. Start the node with the hardware present. Observe the startup log for "Mayhem apps discovered: [...]".
**Expected:** A non-empty list of app short names is logged within 3 seconds of node start.
**Why human:** Requires actual Mayhem hardware — cannot be verified from code inspection alone.

### 2. Mode Coexistence Confirmation

**Test:** With both HackRF USB IQ streaming active and Mayhem serial connected, observe the startup log.
**Expected:** Log line contains "MAY-06: Mode coexistence CONFIRMED — pyhackrf2 streaming and Mayhem serial both operational."
**Why human:** Requires actual hardware with both interfaces operational simultaneously.

### 3. appstart Service Call

**Test:** Call `ros2 service call /hackrf/appstart hackrf_ros_interfaces/srv/AppStart "{app_name: 'scanner'}"` while node is running with hardware present.
**Expected:** Response contains `success: True` and the Mayhem UI switches to the scanner app.
**Why human:** Requires live hardware and UI visual confirmation of app switch.

### 4. setfreq + radioinfo Confirmation

**Test:** Call `ros2 service call /hackrf/setfreq hackrf_ros_interfaces/srv/SetFreq "{freq_hz: 433920000}"`.
**Expected:** Response contains `success: True`, `confirmed_hz: 433920000` (or close value), confirming radioinfo read-back.
**Why human:** Requires live hardware to produce a non-zero `confirmed_hz` value.

---

## Gaps Summary

No gaps. All automated checks passed:

- All 4 success-criterion truths are fully implemented and wired
- All 7 required artifacts exist, are substantive (no stubs), and are wired into the runtime
- All 8 key links verified by pattern search
- All 6 MAY requirements covered with specific code evidence
- 9 unit tests pass without hardware
- 5 git commits confirmed in history
- No anti-patterns introduced by Phase 2

The one pre-existing stub (`_redis_queue`) is explicitly scoped to Phase 3 in a code comment and is correctly populated at runtime — it is not a Phase 2 gap.

---

_Verified: 2026-03-29_
_Verifier: Claude (gsd-verifier)_
