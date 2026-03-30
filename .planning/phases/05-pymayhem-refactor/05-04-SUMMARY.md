---
phase: 05-pymayhem-refactor
plan: "04"
subsystem: bridge
tags: [bridge_node, ros2, redis, bridge_services, tdd, no-pyhackrf2]

# Dependency graph
requires:
  - phase: 05-pymayhem-refactor
    plan: "03"
    provides: HackRFDriver, hackrf_driver package with RedisBridge publishing hackrf:iq:notify
provides:
  - BridgeNode class: thin ROS2 bridge reading Redis IQ stream, publishing Float32MultiArray
  - bridge_services.py: register_bridge_services() with /hackrf/cmd and /hackrf/mayhem/* services
  - Updated setup.py entry point pointing hackrf_node to bridge_node:main
  - 20 bridge-unit tests (no hardware required)
affects:
  - 05-05 (integration tests will test BridgeNode against running HackRFDriver)

# Tech tracking
tech-stack:
  added:
    - BridgeNode using threading.Event stop gate and daemon thread _bridge_loop
    - Redis Pub/Sub polling pattern: pubsub.get_message(timeout=0.1) in while loop
    - bridge_services.py: closures for /hackrf/cmd subscription and Trigger service handlers
  patterns:
    - Redis pubsub.get_message(timeout=0.1) polling avoids blocking on pubsub.listen()
    - Closure factory pattern (_make_cmd_handler, _make_mayhem_handler, _make_setfreq_handler)
    - No pyhackrf2, no serial, no MayhemSerial, no TXController in bridge layer

key-files:
  created:
    - hackrf_ros/bridge_node.py
    - hackrf_ros/bridge_services.py
  modified:
    - setup.py
    - test/test_hackrf_node_redis.py

key-decisions:
  - "BridgeNode uses pubsub.get_message(timeout=0.1) in while loop to check _stop_event regularly"
  - "bridge_services.py closure factories keep node/redis references without global state"
  - "_FakeNode stub in tests needs destroy_node() method for patch.object compatibility"
  - "All bridge services (Task 3) implemented in Task 1 along with bridge_node.py (forward-planned)"

# Metrics
duration: 4min
completed: "2026-03-30"
---

# Phase 05 Plan 04: Thin ROS2 Bridge Node Summary

**BridgeNode subscribes to hackrf:iq:notify Redis Pub/Sub, publishes Float32MultiArray to /hackrf/iq and JSON to /hackrf/state, exposes /hackrf/cmd subscription and /hackrf/mayhem/* Trigger services — all with no pyhackrf2 or serial imports, 20 tests pass**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-30T09:35:00Z
- **Completed:** 2026-03-30T09:39:00Z
- **Tasks:** 3 (Task 3 folded into Task 1)
- **Files modified:** 4 (2 created, 2 updated)

## Accomplishments

- bridge_node.py: BridgeNode(Node) with Redis Pub/Sub IQ loop, Float32MultiArray publisher on /hackrf/iq, JSON String publisher on /hackrf/state, destroy_node lifecycle, main() entry point
- bridge_services.py: register_bridge_services() attaches /hackrf/cmd String subscription and /hackrf/mayhem/{appstart,setfreq,radioinfo} Trigger services, all writing to hackrf:cmd Redis list via RPUSH
- setup.py: hackrf_node entry point updated from hackrf_node:main to bridge_node:main
- test_hackrf_node_redis.py: 20 bridge-specific tests replacing old HackRFNode tests — all pass
- flake8 passes on bridge_node.py and bridge_services.py with max-line-length=120
- BridgeNode has zero pyhackrf2, serial, MayhemSerial, TXController imports (verified)

## Task Commits

1. **Task 1+3: Create BridgeNode and bridge_services** - `b749fde` (feat)
2. **Task 2: Update setup.py entry point and adapt test_hackrf_node_redis.py** - `fdb8c4f` (feat)

## Files Created/Modified

- `hackrf_ros/bridge_node.py` — BridgeNode class (175 lines): Redis Pub/Sub loop, publisher setup, bridge_services wiring, destroy_node, main()
- `hackrf_ros/bridge_services.py` — register_bridge_services() (120 lines): /hackrf/cmd subscription handler, appstart/setfreq/radioinfo Trigger services
- `setup.py` — entry_points updated: hackrf_node now points to bridge_node:main
- `test/test_hackrf_node_redis.py` — 20 bridge-level unit tests (240 lines)

## Decisions Made

- BridgeNode uses pubsub.get_message(timeout=0.1) polling loop to allow _stop_event checks on each iteration (avoids blocking indefinitely on pubsub.listen())
- bridge_services.py uses closure factories that capture node/redis references at construction time
- _FakeNode test stub needed a destroy_node() method to support patch.object() in TestDestroyNode
- Task 3 (services) was implemented as part of Task 1 since bridge_node.py and bridge_services.py were created together; tests were added in Task 2 as specified

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] _FakeNode stub missing destroy_node() method**
- **Found during:** Task 2 test run
- **Issue:** TestDestroyNode used mock_lib.patch.object(_FakeNode, 'destroy_node') but _FakeNode had no destroy_node attribute, causing AttributeError
- **Fix:** Added destroy_node() no-op method to _FakeNode stub class
- **Files modified:** test/test_hackrf_node_redis.py
- **Commit:** fdb8c4f

**2. [Scope - Design] Tasks 1 and 3 folded together**
- bridge_services.py was specified in both Task 1 (create file) and Task 3 (add services). Both were implemented in Task 1 to avoid partial creation; no functional deviation from plan.

## Known Stubs

None — bridge_node.py is fully wired for Redis Pub/Sub IQ and state publishing. The bridge only receives data from the hackrf_driver process (plan 05-03); it does not generate test data internally. When no hackrf_driver is running, the bridge loop simply receives no messages — this is correct behavior, not a stub.

## Self-Check

All files verified:
- hackrf_ros/bridge_node.py — FOUND
- hackrf_ros/bridge_services.py — FOUND
- setup.py — FOUND (updated)
- test/test_hackrf_node_redis.py — FOUND (20 tests pass)
- .planning/phases/05-pymayhem-refactor/05-04-SUMMARY.md — THIS FILE

All commits verified:
- b749fde feat(05-04): create BridgeNode and bridge_services — thin ROS2-Redis bridge — FOUND
- fdb8c4f feat(05-04): update setup.py entry point and adapt test_hackrf_node_redis.py for bridge — FOUND

## Self-Check: PASSED
