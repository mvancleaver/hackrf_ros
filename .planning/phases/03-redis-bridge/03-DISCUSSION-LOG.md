# Phase 3: Redis Bridge - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.

**Date:** 2026-03-30
**Phase:** 03-redis-bridge
**Areas discussed:** Redis connection, IQ data format, Command routing, State schema

---

## Redis Connection

| Option | Description | Selected |
|--------|-------------|----------|
| localhost:6379 | Standard local Redis, no auth | ✓ |
| Configurable | ROS2 params for host/port/password | |
| Let me specify | Custom setup | |

**User's choice:** localhost:6379

| Option | Description | Selected |
|--------|-------------|----------|
| Degrade gracefully | IQ still on ROS2, Redis disabled until reconnect | ✓ |
| Require Redis | Fail loud if Redis down | |

**User's choice:** Degrade gracefully

---

## IQ Data Format

| Option | Description | Selected |
|--------|-------------|----------|
| Raw bytes | Binary int8 I/Q pairs | |
| Float32 array | Interleaved float32, same as ROS2 topic | ✓ |
| Base64 of raw | Text-safe encoding | |

**User's choice:** Float32 array

| Option | Description | Selected |
|--------|-------------|----------|
| 1000 (~5s) | Short buffer | |
| 10000 (~50s) | Medium buffer | |
| Configurable | ROS2 param, default 10000 | ✓ |

**User's choice:** Configurable

---

## Command Routing

| Option | Description | Selected |
|--------|-------------|----------|
| JSON messages | {"action": "setfreq", "value": 433000000} | ✓ |
| Simple key=value | "setfreq 433000000" | |

**User's choice:** JSON messages

**Command scope (multi-select):**
- HackRF config (center_frequency, sample_rate, gains) ✓
- Mayhem control (appstart, setfreq) ✓
- Stream control (start_rx, stop_rx) ✓

---

## State Schema

**State fields (multi-select):**
- HackRF config ✓
- Streaming status ✓
- Mayhem state ✓
- Buffer stats (not selected)

| Option | Description | Selected |
|--------|-------------|----------|
| On change only | Update when config/state changes | ✓ |
| Periodic 1s | Refresh every second | |
| Both | On change + periodic heartbeat | |

**User's choice:** On change only

---

## Claude's Discretion
- redis-py connection pooling
- Thread synchronization details
- Command subscriber thread design
- Single vs split daemon threads

## Deferred Ideas
None
