---
phase: 02-mayhem-serial-interface
plan: 03
subsystem: driver
tags: [ros2, serial, mayhem, hackrf, services, lifecycle]
dependency_graph:
  requires:
    - 02-01 (hackrf_ros_interfaces: AppStart.srv, SetFreq.srv)
    - 02-02 (MayhemSerial helper class)
  provides:
    - HackRFNode with integrated MayhemSerial lifecycle
    - ROS2 services: hackrf/appstart, hackrf/setfreq, hackrf/radioinfo
    - ROS2 topic: /hackrf/mayhem_status (5s interval)
    - MAY-06 mode coexistence empirical check at startup
  affects:
    - hackrf_ros package (build and runtime)
    - Any ROS2 node calling hackrf/appstart, hackrf/setfreq, hackrf/radioinfo
tech_stack:
  added: [pyserial>=3.5, hackrf_ros_interfaces, std_srvs/Trigger]
  patterns:
    - exponential backoff serial reconnect mirroring pyhackrf2 reconnect (D-10)
    - serial lifecycle wired into ROS2 node __init__ and destroy_node
    - service handler pattern with TimeoutError + Exception guard per handler
key_files:
  created: []
  modified:
    - hackrf_ros/hackrf_node.py
    - hackrf_ros/setup.py
    - hackrf_ros/package.xml
decisions:
  - "Serial lifecycle: open in __init__ via _try_serial_connect, close in destroy_node before pyhackrf2 shutdown"
  - "Exponential backoff for serial reconnect uses identical _MIN_RECONNECT_DELAY/_MAX_RECONNECT_DELAY constants as pyhackrf2 reconnect (D-10 reuse)"
  - "MAY-06 mode coexistence check called as last step in __init__ after both pyhackrf2 and serial init — provides startup empirical result"
  - "radioinfo service uses std_srvs/Trigger (no request fields needed) as specified in plan interface contract"
metrics:
  duration: 3min
  completed: "2026-03-30"
  tasks_completed: 2
  files_modified: 3
---

# Phase 02 Plan 03: HackRFNode Mayhem Integration Summary

**One-liner:** HackRFNode wired with full MayhemSerial lifecycle (open/close/exponential-backoff reconnect), three ROS2 services (appstart, setfreq, radioinfo), a 5s status topic, and MAY-06 mode coexistence check.

## What Was Built

`hackrf_ros/hackrf_node.py` — modified to integrate MayhemSerial as a first-class component of the HackRFNode lifecycle. The node now opens the serial port at startup, registers three ROS2 services for Mayhem control, publishes serial state every 5 seconds, and runs an empirical mode coexistence check confirming pyhackrf2 and serial can coexist.

`hackrf_ros/setup.py` and `hackrf_ros/package.xml` — updated to declare pyserial and hackrf_ros_interfaces as dependencies.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add MayhemSerial lifecycle, services, status topic, and mode check to HackRFNode | abf6ffa | hackrf_ros/hackrf_node.py |
| 2 | Add pyserial dependency and hackrf_ros_interfaces to package manifests | d3864b4 | setup.py, package.xml |

## Key Decisions

1. **Serial lifecycle order:** `_try_serial_connect` called in `__init__` after parameter declarations and before service registration. `destroy_node` closes serial before pyhackrf2 to avoid mode conflicts on shutdown.
2. **Backoff constant reuse:** `_serial_reconnect_delay` uses the same `_MIN_RECONNECT_DELAY` and `_MAX_RECONNECT_DELAY` module constants as pyhackrf2 reconnect — per D-10 requirement.
3. **Trigger for radioinfo:** `std_srvs/Trigger` used for `hackrf/radioinfo` as specified in plan interface contract (no request parameters needed).
4. **_publish_mayhem_status detects reconnect:** The 5s status timer also checks `_mayhem.needs_reconnect` and schedules a serial reconnect if the reader thread has died.

## MAY Requirements Coverage

| Requirement | Implementation |
|-------------|---------------|
| MAY-01 | `_try_serial_connect` opens port at startup; `destroy_node` calls `_mayhem.close()` |
| MAY-02 | `query_applist` called in `_try_serial_connect` at startup; result logged |
| MAY-03 | `_handle_appstart` service handler wired to `hackrf/appstart` |
| MAY-04 | `_handle_setfreq` service handler wired to `hackrf/setfreq` |
| MAY-05 | `_handle_radioinfo` handler; `_handle_setfreq` calls `radioinfo()` post-set to confirm |
| MAY-06 | `_verify_mode_coexistence` — logs CONFIRMED / MODE CONFLICT / partial / both unavailable |

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all methods are fully implemented. The pre-existing `_redis_queue` stub (Phase 3) was not modified by this plan.

## Verification

- `python3 -c "import ast; ast.parse(open('hackrf_ros/hackrf_node.py').read()); print('ok')"` prints "ok"
- `grep -c "MayhemSerial" hackrf_ros/hackrf_node.py` returns 7 (>= 3 required)
- `grep -c "def _handle_" hackrf_ros/hackrf_node.py` returns 3 (three service handlers)
- `grep "_verify_mode_coexistence" hackrf_ros/hackrf_node.py` returns matches
- `colcon build --packages-select hackrf_ros` exits 0 with "1 package finished"
- `grep "pyserial" setup.py` returns match
- `grep "hackrf_ros_interfaces" package.xml` returns match

## Self-Check: PASSED

Files exist:
- hackrf_ros/hackrf_node.py: FOUND
- hackrf_ros/setup.py: FOUND
- hackrf_ros/package.xml: FOUND

Commits exist:
- abf6ffa: FOUND
- d3864b4: FOUND
