# Phase 6: Foundation Hardening - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-30
**Phase:** 06-foundation-hardening
**Areas discussed:** Exception hierarchy design, Validation boundary, TX dry-run scope, Sequence number design

---

## Exception Hierarchy Design

### pymayhem Exception Approach

| Option | Description | Selected |
|--------|-------------|----------|
| Raise on all failures | Every command failure raises MayhemCommandError. Callers must try/except. Clean contract but breaks every existing caller. | |
| Raise + bool compat layer | Domain methods raise by default. Add a safe_* wrapper or context manager for bool returns. Migration path. | |
| Raise only on unrecoverable | TimeoutError and SerialException raise. 'error' firmware responses still return False. Minimal API break. | |
| You decide | Claude picks the best approach based on codebase analysis | ✓ |

**User's choice:** Claude's discretion
**Notes:** User trusts Claude to pick the best approach based on the codebase. Recommended: raise on failures with catch-at-boundary for backward compat.

### hackrf_driver Exception Hierarchy

| Option | Description | Selected |
|--------|-------------|----------|
| Unified tree | HackRFError base, all exceptions reparented under it. One except catches all. | ✓ |
| Separate trees | Keep TX exceptions separate. Add HackRFConfigError and HackRFDeviceError independently. | |
| You decide | Claude picks based on existing pattern analysis | |

**User's choice:** Unified tree
**Notes:** HackRFError as common base enables broad catching while preserving granular handling.

---

## Validation Boundary

| Option | Description | Selected |
|--------|-------------|----------|
| Single source in hackrf_driver | hackrf_driver validates via PARAM_RANGES + raises. BridgeNode and Redis dispatch trust it. pymayhem validates its own inputs. | ✓ |
| Validate at every boundary | Triple validation at pymayhem, hackrf_driver, and BridgeNode. Defense in depth but risks divergence. | |
| You decide | Claude picks based on architecture analysis | |

**User's choice:** Single source in hackrf_driver
**Notes:** Avoids Pitfall 11 (dual-validation divergence). pymayhem validates its own domain inputs independently.

---

## TX Dry-Run Scope

| Option | Description | Selected |
|--------|-------------|----------|
| Guards only | Checks antenna, hard-blocked, freq filter, auth EXISTS (not consumed). Pure logic, works offline. | ✓ |
| Guards + device state | Also checks device connected, not transmitting, not reconnecting. More complete but couples to hardware. | |
| You decide | Claude picks based on TX controller analysis | |

**User's choice:** Guards only
**Notes:** Pure logic validation. Works even when hardware is offline. Returns pass/fail with which guard would block.

---

## Sequence Number Design

| Option | Description | Selected |
|--------|-------------|----------|
| Reset on restart | Simple monotonic counter starting at 0. Consumers detect restart by seq going backward. | |
| Persist across restarts | Store last seq in Redis key. Resume after restart. No backward jumps. | |
| Epoch + counter | Combine driver start timestamp + monotonic counter (e.g., "1711814400:42"). Detect gaps AND restarts from one field. | ✓ |

**User's choice:** Epoch + counter
**Notes:** "{driver_start_epoch}:{monotonic_counter}" format. No persistence needed. Consumers detect gaps (missing counter values) and restarts (epoch change) from a single field.

---

## Claude's Discretion

- pymayhem exception backward compatibility strategy
- TXController antenna re-read interval
- Exception naming refinements within defined hierarchies

## Deferred Ideas

None — discussion stayed within phase scope.
