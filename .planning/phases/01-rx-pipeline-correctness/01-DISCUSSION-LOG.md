# Phase 1: RX Pipeline Correctness - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-29
**Phase:** 01-rx-pipeline-correctness
**Areas discussed:** Buffer strategy, Reconnection, Error behavior, Node identity

---

## Buffer Strategy

### Overflow Policy

| Option | Description | Selected |
|--------|-------------|----------|
| Drop oldest (Recommended) | Ring-buffer style — always has fresh samples, older ones silently discarded. Best for real-time SDR. | ✓ |
| Backpressure | Block the RX callback when full — no data loss but risks libusb timeout/deadlock | |
| Drop + warn | Drop oldest but log a warning each time overflow occurs | |
| You decide | Claude picks the best approach | |

**User's choice:** Drop oldest
**Notes:** None

### Queue Depth

| Option | Description | Selected |
|--------|-------------|----------|
| Small (64 chunks) | Low latency (~320ms at 10 MSPS), minimal memory, drops sooner | ✓ |
| Medium (256 chunks) | ~1.3s buffer, good balance for most use cases | |
| Large (1024 chunks) | ~5s buffer, handles long consumer stalls but uses more memory | |
| Configurable | ROS2 parameter so you can tune at runtime | |

**User's choice:** Small (64 chunks)
**Notes:** None

### Queue Architecture

| Option | Description | Selected |
|--------|-------------|----------|
| Single queue | One consumer drains the queue, then dispatches to both ROS2 and Redis | |
| Dual queues | RX callback enqueues to two separate queues — each consumer is independent (research recommended this) | ✓ |
| You decide | Claude picks based on what works best | |

**User's choice:** Dual queues
**Notes:** None

### Chunk Size

| Option | Description | Selected |
|--------|-------------|----------|
| Keep current | Use existing num_iq_samples_per_publish parameter as chunk size | |
| Fixed 2048 | Standard SDR chunk size, predictable memory/timing | ✓ |
| Configurable | ROS2 parameter, default 2048 | |

**User's choice:** Fixed 2048
**Notes:** None

---

## Reconnection

### Backoff Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Exponential backoff | Start at 1s, double each retry up to 30s max. Standard resilience pattern. | ✓ |
| Fixed interval | Retry every 5 seconds indefinitely — simpler, predictable | |
| Limited retries | Exponential backoff but give up after N attempts, require manual restart | |

**User's choice:** Exponential backoff
**Notes:** None

### State on Reconnect

| Option | Description | Selected |
|--------|-------------|----------|
| Restore all params | Re-apply last known frequency, gain, sample rate, bandwidth after reconnect | ✓ |
| Safe defaults | Start from safe defaults, require explicit reconfiguration | |
| You decide | Claude picks based on what's safest | |

**User's choice:** Restore all params
**Notes:** None

---

## Error Behavior

### Startup Without Device

| Option | Description | Selected |
|--------|-------------|----------|
| Start + retry | Node starts, logs error, enters reconnection loop. Services still respond but report 'no device'. | ✓ |
| Fail fast | Node refuses to start if no device found. Systemd/Docker can restart it. | |
| You decide | Claude picks based on deployment context | |

**User's choice:** Start + retry
**Notes:** None

### Fatal Errors

| Option | Description | Selected |
|--------|-------------|----------|
| Only HW failure | Only unrecoverable hardware failure is fatal. Everything else retries. | ✓ |
| HW + repeated | Hardware failure is fatal. Also fatal after N consecutive reconnect failures. | |
| Never fatal | Node never exits on its own — always retries | |

**User's choice:** Only HW failure
**Notes:** None

---

## Node Identity

### Class Name

| Option | Description | Selected |
|--------|-------------|----------|
| HackRFNode | Clean and generic — it does more than just publish | ✓ |
| HackRFDriverNode | Emphasizes it's a hardware driver | |
| HackRFMayhemNode | Reflects the Mayhem firmware specificity | |

**User's choice:** HackRFNode
**Notes:** None

### Topic Names

| Option | Description | Selected |
|--------|-------------|----------|
| Keep /hackrf_iq_data | No breaking change for existing subscribers | |
| Rename to /hackrf/iq | Cleaner namespacing, follows ROS2 convention | ✓ |
| You decide | Claude picks based on ROS2 conventions | |

**User's choice:** Rename to /hackrf/iq
**Notes:** None

### Plotter Node

| Option | Description | Selected |
|--------|-------------|----------|
| Update it too | Update topic subscription to /hackrf/iq, fix convention issues | ✓ |
| Leave for later | Focus on the driver node only | |
| Remove it | Plotter is obsolete once Redis consumers handle visualization | |

**User's choice:** Update it too
**Notes:** None

---

## Claude's Discretion

- Thread-safe queue implementation details
- stop_rx() deadlock mitigation approach
- Specific exception types to catch from pyhackrf2
- Parameter validation range enforcement implementation
- MultiThreadedExecutor callback group configuration

## Deferred Ideas

None — discussion stayed within phase scope.
