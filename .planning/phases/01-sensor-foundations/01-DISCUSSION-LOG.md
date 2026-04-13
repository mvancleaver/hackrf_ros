# Phase 1: Sensor Foundations - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-13
**Phase:** 01-sensor-foundations
**Areas discussed:** CFAR tuning strategy, Detection message design, TF frame convention, Band classification scope

---

## CFAR Tuning Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Conservative | Pfa=1e-4, persistence=3. Fewer false alarms, best for autonomy. | |
| Sensitive | Pfa=1e-3, persistence=2. More detections, more false alarms. | |
| Parameterized | Expose Pfa and persistence as ROS2 parameters. Runtime switchable. | selected |

**User's choice:** Parameterized
**Notes:** Robot behavior planner can switch between modes at runtime.

| Option | Description | Selected |
|--------|-------------|----------|
| Adaptive guard | Small guard (8), re-estimate noise excluding detected signal width. Two-pass. | selected |
| Fixed wide guard | Guard=64 cells. Simple but wastes reference cells on narrowband. | |
| You decide | Claude picks based on wiki CFAR theory. | |

**User's choice:** Adaptive guard
**Notes:** Two-pass approach handles both wideband WiFi and narrowband signals.

---

## Detection Message Design

| Option | Description | Selected |
|--------|-------------|----------|
| Rich | Full info: freq, BW, power, SNR, class, ID, persistence, confidence | selected |
| Minimal | freq, power, class only. Simpler but less useful downstream. | |
| Layered | Minimal + optional detail topic. Two message types. | |

**User's choice:** Rich
**Notes:** Downstream nodes can ignore fields they don't need.

| Option | Description | Selected |
|--------|-------------|----------|
| Include rf_environment | 1 Hz summary with emitter count, occupancy, noise floor. | selected |
| Skip for Phase 1 | Detection array sufficient. | |
| You decide | Claude decides. | |

**User's choice:** Include it

---

## TF Frame Convention

| Option | Description | Selected |
|--------|-------------|----------|
| Parameterized | frame_id and parent_frame as ROS2 parameters. | selected |
| Fixed convention | Always 'hackrf_antenna' -> 'base_link'. | |
| No TF in driver | User provides own static_transform_publisher. | |

**User's choice:** Parameterized

| Option | Description | Selected |
|--------|-------------|----------|
| Static only | Published once. Covers 99% of use cases. | selected |
| Support dynamic | Allow TF updates for pan/tilt mounts. | |

**User's choice:** Static only

---

## Band Classification Scope

| Option | Description | Selected |
|--------|-------------|----------|
| Common bands | ~30 entries: WiFi, BT, LTE, ISM, FM, GPS. | selected |
| Comprehensive ITU | Full US allocation table. Hundreds of entries. | |
| Minimal + extensible | WiFi, BT, LTE, unknown + YAML config for custom bands. | |

**User's choice:** Common bands

| Option | Description | Selected |
|--------|-------------|----------|
| Bandwidth heuristic | 20 MHz=WiFi, 1-2 MHz=BLE/ZigBee, <200 kHz=ISM narrowband. | selected |
| Primary label only | Label the band, not the protocol. | |
| You decide | Claude picks. | |

**User's choice:** Bandwidth heuristic

---

## Claude's Discretion

- CFAR sliding window implementation details
- Signal bin grouping algorithm
- Detection ID assignment strategy
- SpectrumStamped.msg field naming

## Deferred Ideas

- Sweep action server (Phase 2)
- IQ recording (Phase 2)
- RF occupancy grid (Phase 2)
- AGC loop (Phase 3)
- Protocol-level classification (Phase 4)
