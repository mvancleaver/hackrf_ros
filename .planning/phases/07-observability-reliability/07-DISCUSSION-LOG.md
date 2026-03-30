# Phase 7: Observability & Reliability - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-30
**Phase:** 07-observability-reliability
**Areas discussed:** All deferred to Claude's discretion

---

## Gray Area Selection

| Option | Description | Selected |
|--------|-------------|----------|
| Watchdog architecture | Lock-free detection vs lock-acquiring probe, correction queue design | |
| Metrics content | What fields in hackrf:metrics, per-command latency vs counters | |
| DLQ design | MAXLEN, token stripping, error fields, TTL | |
| You decide on all | Phase is well-defined by research. Claude handles all design choices. | ✓ |

**User's choice:** Claude's discretion on all areas
**Notes:** Phase has only 4 requirements, patterns are well-established by research. User trusts Claude to make appropriate design choices.

---

## Claude's Discretion

All design decisions for this phase were delegated to Claude:
- Watchdog: lock-free detection, correction queue, 10s interval
- Metrics: 1 Hz pipeline HSET, 9 fields, 5s ROS2 topic
- DLQ: MAXLEN=500 stream, auth tokens redacted, no per-entry TTL
- Legacy: no additional cleanup (Phase 6 already handled)

## Deferred Ideas

None — discussion stayed within phase scope.
