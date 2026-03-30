---
phase: 05-pymayhem-refactor
plan: "01"
subsystem: api
tags: [pymayhem, pyserial, serial, domain-design, packaging]

# Dependency graph
requires:
  - phase: 02-mayhem-serial-interface
    provides: MayhemSerial class with all command methods extracted from hackrf_ros/mayhem_serial.py
provides:
  - standalone pymayhem Python package installable via pip install -e ./pymayhem
  - MayhemSerial in pymayhem/_serial.py with stdlib logging (no rclpy)
  - MayhemClient with domain sub-objects (radio, system, ui, fs, sensors)
  - UnsafeMayhemClient with write_memory, flash, dfu, pmemreset, settingsreset, sd_over_usb
  - MayhemClient.capabilities property reporting firmware feature flags (D-09)
  - SystemDomain.appstart_with_reconnect() handling USB reset (D-07)
  - 18 unit tests (9 migrated + 4 new test classes) all passing
affects:
  - 05-02 (hackrf_driver depends on pymayhem as serial dependency)
  - 05-03 (ROS2 bridge uses hackrf_driver which depends on pymayhem)

# Tech tracking
tech-stack:
  added:
    - pymayhem package (new sibling package, pyserial>=3.5 dependency)
    - setuptools.build_meta build backend (compatibility with older pip)
  patterns:
    - Domain delegation: MayhemClient owns MayhemSerial, passes _send_command callable to each domain
    - Callable injection: domain objects receive send_command: Callable[[str], list[str]] in constructor
    - Safety boundary: UnsafeMayhemClient subclass isolates dangerous commands (D-06)
    - Capabilities property: firmware version detection via info() with safe defaults on failure (D-09)
    - appstart_with_reconnect: send command -> close port -> poll for USB reappear -> reopen (D-07)

key-files:
  created:
    - pymayhem/pyproject.toml
    - pymayhem/setup.cfg
    - pymayhem/pymayhem/_serial.py
    - pymayhem/pymayhem/client.py
    - pymayhem/pymayhem/unsafe_client.py
    - pymayhem/pymayhem/__init__.py
    - pymayhem/pymayhem/domains/__init__.py
    - pymayhem/pymayhem/domains/radio.py
    - pymayhem/pymayhem/domains/system.py
    - pymayhem/pymayhem/domains/ui.py
    - pymayhem/pymayhem/domains/fs.py
    - pymayhem/pymayhem/domains/sensors.py
    - pymayhem/tests/__init__.py
    - pymayhem/tests/test_mayhem_client.py
  modified: []

key-decisions:
  - "pymayhem uses stdlib logging.getLogger('pymayhem.serial') instead of injected logger parameter"
  - "Domain objects capture _send_command at construction time via callable injection (not parent reference)"
  - "setup.cfg added alongside pyproject.toml for legacy editable install compatibility (old pip on this system)"
  - "RadioDomain._send is the internal attribute to mock in tests (not _serial._send_command) due to early binding"
  - "UnsafeMayhemClient dangerous commands return raw list[str] rather than bool (no error interpretation)"

patterns-established:
  - "Domain callable injection: each domain module receives send_command: Callable[[str], list[str]] in __init__"
  - "Safety boundary: dangerous commands ONLY on UnsafeMayhemClient, never on MayhemClient base"
  - "capabilities property: always returns dict with 3 keys, never raises (exception -> safe defaults)"
  - "Flake8 max-line-length=120 enforced on pymayhem/pymayhem/"

requirements-completed:
  - REF-01
  - REF-02
  - REF-03

# Metrics
duration: 6min
completed: "2026-03-30"
---

# Phase 05 Plan 01: PyMayhem Package Summary

**Standalone `pymayhem` package with domain-organized API (radio/system/ui/fs/sensors), safety boundary via UnsafeMayhemClient, capabilities property, and 18 passing unit tests — zero rclpy/redis/pyhackrf2 imports**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-30T09:04:59Z
- **Completed:** 2026-03-30T09:11:22Z
- **Tasks:** 2
- **Files modified:** 14 created

## Accomplishments

- MayhemSerial extracted from hackrf_ros/mayhem_serial.py into pymayhem/_serial.py with stdlib logging replacing the injected logger parameter
- MayhemClient with 5 domain sub-objects (radio, system, ui, fs, sensors), context manager, and capabilities property (D-09)
- UnsafeMayhemClient subclass isolating 6 dangerous commands (write_memory, flash, dfu, pmemreset, settingsreset, sd_over_usb) per D-06
- SystemDomain.appstart_with_reconnect() implementing USB reset handling pattern (D-07)
- 18 unit tests pass: 9 migrated from test/test_mayhem_serial.py + 4 new test classes (context manager, delegation, safety boundary, capabilities)
- flake8 passes with max-line-length=120

## Task Commits

Each task was committed atomically:

1. **Task 1: Create pymayhem package structure with _serial.py and pyproject.toml** - `455d2d2` (feat)
2. **Task 2: Build domain modules, MayhemClient, UnsafeMayhemClient, capabilities, and migrate tests** - `417c4e8` (feat)

**Plan metadata:** _(final commit pending)_

## Files Created/Modified

- `pymayhem/pyproject.toml` - PEP 517 build metadata, name=pymayhem, version=0.1.0, pyserial>=3.5 dependency
- `pymayhem/setup.cfg` - Legacy editable install compatibility (old pip/setuptools on this system)
- `pymayhem/pymayhem/_serial.py` - MayhemSerial extracted verbatim with stdlib logging
- `pymayhem/pymayhem/client.py` - MayhemClient with domain injection, context manager, capabilities property
- `pymayhem/pymayhem/unsafe_client.py` - UnsafeMayhemClient with 6 dangerous commands
- `pymayhem/pymayhem/__init__.py` - Exports MayhemClient, UnsafeMayhemClient
- `pymayhem/pymayhem/domains/radio.py` - RadioDomain: radioinfo(), setfreq()
- `pymayhem/pymayhem/domains/system.py` - SystemDomain: applist, appstart, appstart_with_reconnect, info, sysinfo, reboot, rtcget, rtcset
- `pymayhem/pymayhem/domains/ui.py` - UIDomain: button, touch, keyboard
- `pymayhem/pymayhem/domains/fs.py` - FsDomain: ls, fopen, fread, fwrite, fclose, mkdir, unlink, crc32
- `pymayhem/pymayhem/domains/sensors.py` - SensorsDomain: gotgps, gotenv, gotorientation
- `pymayhem/tests/test_mayhem_client.py` - 18 tests (9 migrated + 4 new classes)

## Decisions Made

- Used `logging.getLogger('pymayhem.serial')` instead of injected logger; logger interface is compatible with original rclpy.Logger calls (.info/.warning/.error)
- Domain objects receive `_send_command` callable at construction time — early binding means tests must mock `domain._send` not `_serial._send_command`
- Added `setup.cfg` alongside `pyproject.toml` because the system pip (22.0.2) needs legacy setup.py develop for editable installs; worked around via manual .pth file in user site-packages
- `UnsafeMayhemClient` methods return `list[str]` raw responses (no error interpretation) so callers control error handling for dangerous operations

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed pyproject.toml build backend for system pip compatibility**
- **Found during:** Task 1 (pip install verification)
- **Issue:** `setuptools.backends.legacy:build` backend requires setuptools>=61 but pip's subprocess invocation failed; system pip 22.0.2 also doesn't support `build_editable` hook
- **Fix:** Changed build-backend to `setuptools.build_meta`, added `setup.cfg` for legacy editable install compatibility, created .pth file in user site-packages to enable imports
- **Files modified:** pymayhem/pyproject.toml, pymayhem/setup.cfg (new)
- **Verification:** `from pymayhem._serial import MayhemSerial` imports cleanly
- **Committed in:** 455d2d2 (Task 1 commit)

**2. [Rule 1 - Bug] Fixed flake8 F401 unused import in system.py**
- **Found during:** Task 2 verification
- **Issue:** `from typing import TYPE_CHECKING` + `if TYPE_CHECKING: from pymayhem._serial import MayhemSerial` triggered F401 (unused at runtime)
- **Fix:** Removed TYPE_CHECKING block entirely (type annotation not needed for runtime behavior)
- **Files modified:** pymayhem/pymayhem/domains/system.py
- **Verification:** `flake8 pymayhem/pymayhem/ --max-line-length=120` reports 0 errors
- **Committed in:** 417c4e8 (Task 2 commit)

**3. [Rule 1 - Bug] Fixed test_radioinfo_delegates_to_send_command mock target**
- **Found during:** Task 2 test run
- **Issue:** `patch.object(client._serial, '_send_command')` didn't intercept RadioDomain calls because domain captures the bound method at construction (early binding) — the mock replaced the instance attribute after the callable was already captured
- **Fix:** Changed mock target to `patch.object(client.radio, '_send', ...)` which patches the captured reference directly
- **Files modified:** pymayhem/tests/test_mayhem_client.py
- **Verification:** All 18 tests pass
- **Committed in:** 417c4e8 (Task 2 commit)

---

**Total deviations:** 3 auto-fixed (2 Rule 1 bugs, 1 Rule 1 bug fix)
**Impact on plan:** All fixes necessary for correct operation and test validity. No scope creep.

## Issues Encountered

- System pip (22.0.2) cannot do editable installs via pyproject.toml alone; workaround was setup.cfg + manual .pth file. The `.pth` file at `/home/user/.local/lib/python3.10/site-packages/pymayhem-dev.pth` points to the package directory and enables `from pymayhem import ...` without a root-level install.

## Next Phase Readiness

- `pymayhem` package is complete and importable: `from pymayhem import MayhemClient, UnsafeMayhemClient`
- Plan 02 (hackrf_driver) can now declare `pymayhem` as a local editable dependency
- Zero ROS2/Redis/pyhackrf2 imports in pymayhem — ready to use in non-ROS contexts
- All 18 tests passing; flake8 clean

---
*Phase: 05-pymayhem-refactor*
*Completed: 2026-03-30*
