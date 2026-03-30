# Phase 4: TX Authorization - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.

**Date:** 2026-03-30
**Phase:** 04-tx-authorization
**Areas discussed:** Auth mechanism, Freq allowlist, Antenna confirm, TX interface

---

## Auth Mechanism

| Option | Description | Selected |
|--------|-------------|----------|
| GET + DEL (two ops) | Not atomic but works on 6.0 | |
| Lua script | Atomic via EVAL, any Redis version | |
| Upgrade Redis | Upgrade to 6.2+ for native GETDEL | ✓ |
| You decide | | |

**User's choice:** Upgrade Redis

| Option | Description | Selected |
|--------|-------------|----------|
| 30 seconds | Short window | |
| 60 seconds | Reasonable buffer | ✓ |
| Configurable | ROS2 param | |

**User's choice:** 60 seconds

---

## Frequency Allowlist

**Blocked bands (multi-select):** All of the above (cellular + aviation + emergency)

| Option | Description | Selected |
|--------|-------------|----------|
| ROS2 param (Recommended) | tx_freq_filter_enabled, default True | |
| Redis key | Remote override capability | |
| Both | ROS2 param primary + Redis override | ✓ |

**User's choice:** Both

---

## Antenna Confirmation

| Option | Description | Selected |
|--------|-------------|----------|
| Per-session flag | Set once, persists until restart | ✓ |
| Per-TX confirmation | Every TX must include confirmation | |
| Startup check | Node asks at boot | |

| Option | Description | Selected |
|--------|-------------|----------|
| Yes, with param | tx_skip_antenna_check, default False | ✓ |
| No, always require | Never skippable | |
| You decide | | |

**User's choice:** Per-session + skippable param for testing

---

## TX Interface

| Option | Description | Selected |
|--------|-------------|----------|
| Freq + IQ data | Single command with base64 IQ | |
| Freq + duration | Carrier/tone for duration | |
| Start/stop TX | Stream IQ to Redis key, TX reads it | ✓ |
| You decide | | |

| Option | Description | Selected |
|--------|-------------|----------|
| Pause RX | Auto stop/resume RX around TX | ✓ |
| Explicit switch | Caller manages RX/TX state | |
| You decide | | |

**User's choice:** Start/stop with auto RX pause

---

## Claude's Discretion
- TXController class design
- pyhackrf2 start_tx() callback details
- Exact band boundaries
- TX IQ data format/Redis key structure
- Thread model for TX feeding

## Deferred Ideas
None
