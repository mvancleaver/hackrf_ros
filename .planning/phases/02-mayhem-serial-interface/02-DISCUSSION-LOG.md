# Phase 2: Mayhem Serial Interface - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-30
**Phase:** 02-mayhem-serial-interface
**Areas discussed:** Serial architecture, Mode conflict, Command interface, Error handling

---

## Serial Architecture

### Relationship to HackRFNode

| Option | Description | Selected |
|--------|-------------|----------|
| Helper class (Recommended) | MayhemSerial as a separate class owned by HackRFNode | ✓ |
| Integrated | Serial logic directly in HackRFNode | |
| Separate node | MayhemSerialNode as its own ROS2 node | |

**User's choice:** Helper class
**Notes:** None

### Thread Model

| Option | Description | Selected |
|--------|-------------|----------|
| Daemon thread (Recommended) | Dedicated reader thread with readline loop and timeout=1.0 | ✓ |
| Timer callback | ROS2 timer polls serial buffer periodically | |
| You decide | Claude picks | |

**User's choice:** Daemon thread
**Notes:** None

---

## Mode Conflict

### Handling Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Test at startup | Probe both interfaces, disable serial if conflict | |
| State machine | Mode switcher between IQ mode and Control mode | |
| Assume coexist | Build assuming they work together, add fallback later | ✓ |

**User's choice:** Assume coexist
**Notes:** None

### Priority

| Option | Description | Selected |
|--------|-------------|----------|
| IQ streaming | pyhackrf2 IQ data is primary | |
| Serial control | Mayhem app control is primary | |
| User selects | Let user choose mode at runtime | |

**User's choice:** Other — "research if there is a mayhem native SDK that replaces pyhackrf2"
**Notes:** User wants the researcher to investigate whether Mayhem has its own SDK that could unify both interfaces.

---

## Command Interface

### ROS2 API

| Option | Description | Selected |
|--------|-------------|----------|
| ROS2 services | Request/response for commands | |
| ROS2 topics | Pub/sub for commands and responses | |
| Both | Services for commands, topic for status updates | ✓ |

**User's choice:** Both
**Notes:** None

### App Names

| Option | Description | Selected |
|--------|-------------|----------|
| Runtime discovery | Query applist at startup, no hardcoded names | ✓ |
| Known + discovery | Hardcode common names + discover unknown | |
| You decide | Claude picks | |

**User's choice:** Runtime discovery
**Notes:** None

---

## Error Handling

### Disconnect Behavior

| Option | Description | Selected |
|--------|-------------|----------|
| Same as USB | Exponential backoff, same as pyhackrf2 pattern | ✓ |
| Simpler retry | Fixed interval (3s) | |
| You decide | Claude picks | |

**User's choice:** Same as USB
**Notes:** None

### Command Timeout

| Option | Description | Selected |
|--------|-------------|----------|
| 2 seconds | Short timeout | |
| 5 seconds | Generous timeout | |
| Configurable | ROS2 parameter, default 3s | ✓ |

**User's choice:** Configurable
**Notes:** None

---

## Claude's Discretion

- pyserial configuration details (baud rate, parity, stop bits)
- Serial response parsing strategy
- ROS2 service message type definitions
- Command queue vs direct send
- Thread synchronization details

## Deferred Ideas

None — discussion stayed within phase scope.
