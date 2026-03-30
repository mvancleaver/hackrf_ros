# Phase 1: RX Pipeline Correctness - Research

**Researched:** 2026-03-29
**Domain:** ROS2 Python driver — thread-safe USB callback, exponential-backoff reconnection, parameter validation, structured logging
**Confidence:** HIGH (all core findings verified against source code, official docs, and project research)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Buffer Strategy**
- D-01: Replace unsafe shared numpy buffer with dual `queue.Queue` instances — one for ROS2 publishing, one for future Redis streaming (Phase 3). RX callback enqueues to both independently.
- D-02: Overflow policy is drop-oldest (ring-buffer style). No backpressure — never block the RX callback.
- D-03: Queue depth is small: 64 chunks. Prioritize low latency over burst tolerance.
- D-04: Fixed chunk size of 2048 IQ samples per queue entry. Remove the configurable `num_iq_samples_per_publish` parameter.
- D-05: RX callback must be stripped to bare enqueue — no numpy processing, no format conversion in the callback. All processing moves to the consumer side.

**Reconnection**
- D-06: Exponential backoff on USB disconnect: start at 1s, double each retry, cap at 30s. Retry indefinitely.
- D-07: On successful reconnect, restore all last-known parameters (frequency, gain, sample rate, bandwidth) automatically.

**Error Behavior**
- D-08: Node starts even if no device is found. Enters reconnection loop, logs error. Services still respond but report "no device".
- D-09: Only unrecoverable hardware failure (burned/bricked device) is fatal. Everything else retries — including repeated reconnect failures.

**Node Identity**
- D-10: Rename class from `HackRFPuiblisherNode` to `HackRFNode`. Node name becomes `hackrf_node`.
- D-11: Rename topic from `/hackrf_iq_data` to `/hackrf/iq` (ROS2 namespaced convention).
- D-12: Update `iq_plotter_node.py` in this phase — subscribe to new topic name `/hackrf/iq`, fix any convention issues.
- D-13: Replace all bare `print()` statements with `self.get_logger()` calls at appropriate levels. Replace deprecated `.warn()` with `.warning()`.
- D-14: Remove unused imports, fix exception handler formatting, use specific exception types instead of broad `except Exception`.

### Claude's Discretion

- Thread-safe queue implementation details (stdlib `queue.Queue` vs alternatives)
- stop_rx() deadlock mitigation approach (timeout guard specifics)
- Specific exception types to catch from pyhackrf2
- Parameter validation range enforcement implementation
- MultiThreadedExecutor callback group configuration

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| RX-01 | IQ sample buffer uses thread-safe queue (queue.Queue) replacing shared numpy array | D-01/D-05 + stdlib queue.Queue is GIL-safe; drop-oldest pattern via bounded maxsize + get_nowait drain loop |
| RX-02 | USB error recovery with exponential backoff reconnection when device disconnects | D-06/D-07 + threading.Event stop signal; `_reconnect_loop()` in a daemon thread; pyhackrf2 raises RuntimeError/OSError on open failure |
| RX-03 | Parameter validation enforces hardware ranges (freq: 1 MHz-6 GHz, LNA gain: 0-40 dB, VGA gain: 0-62 dB, sample rate: 2-20 MSPS) | Range constants defined once; validation in `_on_parameter_event()` returning SetParametersResult(successful=False) on violation |
| RX-04 | Class renamed from HackRFPuiblisherNode to HackRFNode with structured logging (no bare print statements) | D-10/D-13/D-14 + direct code audit of all 11 print() sites and 2 .warn() sites |
| RX-05 | stop_rx() deadlock mitigated with timeout guard during parameter reconfiguration | threading.Event `_stop_requested` + watchdog thread calling stop_rx() from main thread context; never call stop_rx() from RX callback thread |
| RX-06 | Clean lifecycle management: startup initializes device, shutdown stops streaming and closes device | `destroy_node()` acquires `_device_lock`, cancels reconnect timer, calls stop_rx() with timeout, calls close() |
| RX-07 | RX callback stripped to bare enqueue operation to minimize GIL contention | Callback enqueues raw bytes directly; all numpy ops (frombuffer, reshape, normalize) move to timer consumer |
</phase_requirements>

---

## Summary

This phase is a focused refactor of a single file (`hackrf_node.py`) with a one-line update to `iq_plotter_node.py`. No new dependencies are required — every tool needed (stdlib `queue.Queue`, `threading`, pyhackrf2, rclpy) is already in the container. The changes are well-scoped and can be sequenced as: (1) replace the buffer with dual queues and strip the RX callback, (2) add reconnection loop, (3) add parameter validation, (4) clean up naming and logging conventions.

The deepest technical risk is the `stop_rx()` deadlock (Pitfall 1 from project research). The fix requires a `threading.Event` approach: the RX callback signals intent to stop via an event; the ROS2 executor thread (or a dedicated watchdog) calls `stop_rx()` after detecting the event. The callback must never call `stop_rx()` itself. A secondary risk is rapid stop/start cycling corrupting HackRF firmware state — the fix is a mandatory 100 ms sleep between `stop_rx()` and `start_rx()` on reconfiguration.

The pyhackrf2 RX callback contract differs from the current code: the library passes `(data: bytes)` and expects a `bool` return (`False` = continue, `True` = stop). The current `*args` signature and `return 0` are compatible but non-idiomatic. After the refactor, the callback body should be 3-4 lines.

**Primary recommendation:** Implement changes in four discrete passes — queue/callback, reconnect loop, validation, conventions cleanup — committing after each to keep diffs reviewable and rollback safe.

---

## Standard Stack

### Core (all already installed — no new dependencies)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `queue.Queue` | stdlib | Thread-safe bounded FIFO between USB callback thread and ROS2 timer thread | GIL-safe internally; `maxsize` enables bounded overflow; `put_nowait` + `except Full` is the canonical drop-oldest pattern |
| `threading.Event` | stdlib | Signal the stop intent from any thread to the executor thread that calls `stop_rx()` | Never call `stop_rx()` from the USB callback thread — this is the deadlock prevention mechanism |
| `threading.RLock` | stdlib | Serialize device reconfiguration (stop_rx / set params / start_rx) against concurrent parameter events | RLock (not Lock) because `_configure_device()` may be called from a path that already holds the lock during init |
| `time.sleep` | stdlib | 100 ms mandatory pause between `stop_rx()` and `start_rx()` to prevent HackRF firmware state corruption (Pitfall 10) | Known workaround from libhackrf issue #916 |
| `rclpy.callback_groups` | rclpy (installed) | `MutuallyExclusiveCallbackGroup` to keep device callbacks and any future service callbacks separated | Prevents service calls from blocking the IQ publish timer |
| `pyhackrf2` | 1.0.3 (installed) | HackRF USB interface | Only Python binding used in this project |

### No New Installs Required

This phase requires zero new packages. `queue`, `threading`, and `time` are stdlib. `rclpy`, `pyhackrf2`, and `numpy` are already in the Docker image.

---

## Architecture Patterns

### Recommended Class Structure After Refactor

```
HackRFNode
├── __init__()
│   ├── declare parameters (with validation ranges in descriptors)
│   ├── create publisher on /hackrf/iq
│   ├── add_on_set_parameters_callback(_on_parameter_event)
│   ├── _ros_queue = queue.Queue(maxsize=64)          # ROS2 publisher consumer
│   ├── _redis_queue = queue.Queue(maxsize=64)        # Phase 3 Redis consumer (stub)
│   ├── _device_lock = threading.RLock()
│   ├── _stop_event = threading.Event()               # deadlock guard
│   ├── _last_params = {}                             # for reconnect restore
│   └── _try_connect()  → starts reconnect loop if device absent
│
├── _try_connect()         # attempt pyhackrf2.HackRF(); if fail, schedule retry
├── _reconnect_loop()      # exponential backoff timer callback
├── _configure_device()    # stop_rx + sleep(0.1) + set attrs + start_rx (holds _device_lock)
├── _rx_callback(data)     # ONLY: put raw bytes to both queues, drop if full
├── _publish_iq()          # timer cb: drain _ros_queue, convert, publish Float32MultiArray
├── _on_parameter_event()  # validate ranges → reject or store + reconfigure
├── destroy_node()         # cancel timers, stop_rx with timeout, close device
└── main()
```

### Pattern 1: Drop-Oldest Bounded Queue

**What:** Two `queue.Queue(maxsize=64)` instances. RX callback uses `put_nowait()`; on `queue.Full`, discard the oldest item then re-put.

**When to use:** Whenever a producer (USB callback) must never block and a consumer (ROS2 timer) may fall behind.

**Example:**
```python
# Source: Python stdlib docs - queue.Queue
import queue

def _rx_callback(self, data: bytes) -> bool:
    chunk = bytes(data)  # copy raw bytes; no numpy here
    for q in (self._ros_queue, self._redis_queue):
        try:
            q.put_nowait(chunk)
        except queue.Full:
            try:
                q.get_nowait()   # discard oldest
            except queue.Empty:
                pass
            q.put_nowait(chunk)  # insert newest
    return False  # pyhackrf2: False = continue, True = stop
```

**Key insight:** The callback holds the GIL for under 10 microseconds with this approach. No numpy, no format conversion.

### Pattern 2: stop_rx Deadlock Guard via threading.Event

**What:** RX callback returns `True` (stop signal) to pyhackrf2 instead of calling `stop_rx()` directly. The timer thread polls a `threading.Event` and calls `stop_rx()` from the executor context.

**When to use:** Any code path that needs to stop streaming — parameter reconfiguration, reconnect, shutdown.

**Example:**
```python
# Source: libhackrf issue analysis + PITFALLS.md
def _configure_device(self):
    with self._device_lock:
        if self.is_hackrf_streaming:
            self._stop_event.set()           # signal callback to return True
            # stop_rx() called from THIS thread (executor), not callback thread
            try:
                self._hackrf.stop_rx()
            except (RuntimeError, OSError) as e:
                self.get_logger().warning(f"stop_rx error (non-fatal): {e}")
            finally:
                self._stop_event.clear()
                self.is_hackrf_streaming = False
            time.sleep(0.1)                  # firmware settling (Pitfall 10)

        # apply parameters...
        self._hackrf.center_freq = int(self._last_params['center_frequency'])
        # ...
        self._hackrf.start_rx(self._rx_callback)
        self.is_hackrf_streaming = True
```

**Note:** `_stop_event` can also be checked inside `_rx_callback` to return `True` early if a stop is in progress, providing a cooperative termination path.

### Pattern 3: Exponential Backoff Reconnect Loop

**What:** A `_reconnect_delay` float that doubles on each failed attempt (capped at 30s). A `rclpy.Timer` fires after the delay; on each fire, attempts `pyhackrf2.HackRF()`.

**When to use:** D-06 mandates this. Also handles D-08 (start without device).

**Example:**
```python
# Source: project CONTEXT.md D-06/D-07
_MIN_RECONNECT_DELAY = 1.0
_MAX_RECONNECT_DELAY = 30.0

def _try_connect(self):
    try:
        with self._device_lock:
            self._hackrf = pyhackrf2.HackRF()
            self._apply_last_params()          # D-07: restore frequency, gain, etc.
            self._hackrf.start_rx(self._rx_callback)
            self.is_hackrf_streaming = True
            self._reconnect_delay = self._MIN_RECONNECT_DELAY  # reset backoff
            self.get_logger().info("HackRF connected and streaming.")
    except (RuntimeError, OSError) as e:
        self.get_logger().error(f"HackRF connect failed: {e}. Retrying in {self._reconnect_delay:.0f}s.")
        self._reconnect_timer = self.create_timer(
            self._reconnect_delay,
            self._reconnect_callback,
        )
        self._reconnect_delay = min(self._reconnect_delay * 2, self._MAX_RECONNECT_DELAY)

def _reconnect_callback(self):
    self._reconnect_timer.cancel()
    self._try_connect()
```

### Pattern 4: Parameter Validation with SetParametersResult Rejection

**What:** `_on_parameter_event()` validates every hardware-affecting parameter against documented HackRF ranges. Returns `SetParametersResult(successful=False, reason='...')` for any out-of-range value.

**When to use:** RX-03 mandates this. Validation happens before the device is touched.

**Hardware Ranges (verified against REQUIREMENTS.md RX-03):**
| Parameter | Min | Max | Step | ROS2 type |
|-----------|-----|-----|------|-----------|
| center_frequency | 1e6 Hz | 6e9 Hz | any | double |
| sample_rate | 2e6 Hz | 20e6 Hz | any | double |
| lna_gain | 0 dB | 40 dB | 8 dB | integer |
| vga_gain | 0 dB | 62 dB | 2 dB | integer |
| amp_enabled | false | true | — | bool |

**Example:**
```python
# Source: REQUIREMENTS.md RX-03, rclpy parameter docs
PARAM_RANGES = {
    'center_frequency': (1e6,  6e9),
    'sample_rate':      (2e6, 20e6),
    'lna_gain':         (0,    40),
    'vga_gain':         (0,    62),
}

def _on_parameter_event(self, params):
    results = []
    needs_reconfig = False
    for param in params:
        if param.name in PARAM_RANGES:
            lo, hi = PARAM_RANGES[param.name]
            if not (lo <= param.value <= hi):
                results.append(SetParametersResult(
                    successful=False,
                    reason=f"{param.name} value {param.value} outside [{lo}, {hi}]"
                ))
                continue
        needs_reconfig = True
        self._last_params[param.name] = param.value
        results.append(SetParametersResult(successful=True))
    if needs_reconfig and self._hackrf:
        self._configure_device()
    return results
```

### Pattern 5: Timer Consumer — Queue Drain and Publish

**What:** Timer at 0.005s drains `_ros_queue`, converts raw bytes to interleaved float32, publishes `Float32MultiArray`.

**When to use:** All numpy work is here, never in the callback (RX-07).

**Example:**
```python
# Source: codebase/ARCHITECTURE.md data flow
import numpy as np

CHUNK_IQ_PAIRS = 2048  # D-04

def _publish_iq(self):
    if not self.is_hackrf_streaming:
        return
    chunks = []
    try:
        while not self._ros_queue.empty():
            chunks.append(self._ros_queue.get_nowait())
    except queue.Empty:
        pass
    if not chunks:
        return

    raw = b''.join(chunks)
    iq = np.frombuffer(raw, dtype=np.int8).reshape(-1, 2)
    complex_samples = (iq[:, 0].astype(np.float32) / 128.0 +
                       1j * iq[:, 1].astype(np.float32) / 128.0)

    interleaved = np.empty(complex_samples.size * 2, dtype=np.float32)
    interleaved[0::2] = complex_samples.real
    interleaved[1::2] = complex_samples.imag

    msg = Float32MultiArray()
    msg.data = interleaved.tolist()
    self.publisher_.publish(msg)
```

**Note on tolist() overhead (Pitfall 14):** At 2048 IQ pairs per chunk and 200 Hz publish rate, `tolist()` creates ~800K float objects/second. This is acceptable for Phase 1 (ROS2 local publish). Phase 3 Redis work should use `tobytes()` instead. Do not optimize prematurely.

### Anti-Patterns to Avoid

- **Calling `stop_rx()` inside `_rx_callback`:** Deadlock — USB callback thread cannot cancel itself. See Pitfall 1.
- **`np.append()` in the RX callback:** O(n) allocation per call; at 8+ MSPS this causes OOM. Replaced by queue.
- **`self.get_logger()` calls inside `_rx_callback`:** Logger acquires Python objects; GIL contention in the hot path.
- **`time.sleep()` without a lock guard before `start_rx`:** The 100 ms settling sleep must be inside `_device_lock` to prevent concurrent reconfigurations.
- **Calling `stop_rx()` without a try/except:** The device may already be stopped (idempotency not guaranteed in pyhackrf2).

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Thread-safe bounded FIFO | Custom ring buffer with Lock | `queue.Queue(maxsize=N)` | Internally lock-guarded, GIL-safe, `put_nowait` + `get_nowait` implement drop-oldest in 4 lines |
| Exponential backoff timer | Sleep loop in a thread | `create_timer()` + double `_reconnect_delay` each call | ROS2 timer integrates with executor, cancellable cleanly, no extra threads |
| Parameter range validation | Custom validator class | Dict of `(min, max)` tuples + inline check in `_on_parameter_event` | 5 lines; `SetParametersResult` rejects the change without touching hardware |
| stop_rx deadlock prevention | Watchdog process | `threading.Event` + call `stop_rx()` from executor thread | stdlib, zero overhead, prevents deadlock by ensuring stop is always called from the correct thread context |

**Key insight:** Every sub-problem in this phase has a 5–15 line stdlib solution. No new abstractions needed.

---

## Common Pitfalls

### Pitfall A: stop_rx() Deadlock (Critical)

**What goes wrong:** `stop_rx()` called from inside `_rx_callback` deadlocks permanently. The libhackrf USB thread cannot cancel itself.

**Why it happens:** `_configure_hackrf()` currently calls `stop_rx()` immediately when a parameter changes. If `_rx_callback` is executing and that path somehow triggers reconfiguration, deadlock occurs.

**How to avoid:** `_rx_callback` must NEVER call `stop_rx()`. Use `threading.Event`: callback returns `True` (pyhackrf2 stop signal) OR the executor thread calls `stop_rx()` directly. Both paths are safe. Only the executor thread path is needed here.

**Warning signs:** RX LED stays lit after `stop_rx()` call. Node becomes unresponsive. `destroy_node()` hangs.

### Pitfall B: Rapid stop/start Corrupts HackRF Firmware State (Critical)

**What goes wrong:** `stop_rx()` immediately followed by `start_rx()` without a delay causes the HackRF to appear streaming (no exception) but fire zero callbacks.

**Why it happens:** HackRF USB transfer pipeline needs brief settling time. Confirmed issue #916 in libhackrf.

**How to avoid:** `time.sleep(0.1)` inside `_configure_device()` after `stop_rx()` and before attribute assignment. Must be inside `_device_lock` to avoid races.

**Warning signs:** `is_hackrf_streaming` is True but `_ros_queue` never fills. Only manifests after a parameter reconfiguration.

### Pitfall C: pyhackrf2 Callback Return Value Convention

**What goes wrong:** Current code `return 0`. pyhackrf2 1.0.3 expects `bool` return. `return False` = continue, `return True` = stop. Returning `0` (falsy) currently works but is non-idiomatic and fragile.

**How to avoid:** Return `bool` explicitly. Use `return self._stop_event.is_set()` to cooperatively stop when a stop is signaled.

### Pitfall D: `_on_parameter_event` Must Return a List, Not a Single Result

**What goes wrong:** If `_on_parameter_event` returns a single `SetParametersResult` instead of a list matching the input `params` list length, rclpy raises an internal error.

**How to avoid:** Build `results = []` and append one `SetParametersResult` per param. Return the list.

### Pitfall E: Queue Drain Loop Can Stall if Producer Is Faster Than Consumer

**What goes wrong:** `while not self._ros_queue.empty()` in the timer callback is a TOCTOU race: the queue can become non-empty between the `empty()` check and `get_nowait()`. This raises `queue.Empty`.

**How to avoid:** Always wrap `get_nowait()` in `try/except queue.Empty: break`. The pattern is standard and correct.

### Pitfall F: Reconnect Loop Timer Accumulation

**What goes wrong:** If `_try_connect()` creates a new timer each call without cancelling the previous one, timers accumulate and `_try_connect` is called multiple times per interval after several retries.

**How to avoid:** Store reconnect timer as `self._reconnect_timer`. Always call `self._reconnect_timer.cancel()` at the start of `_reconnect_callback()` before creating a new one.

---

## Code Examples

### Bare Enqueue Callback (RX-07 compliant)

```python
# Source: D-05, pyhackrf2 1.0.3 callback contract (pypi.org/project/pyhackrf2)
def _rx_callback(self, data: bytes) -> bool:
    chunk = bytes(data)
    for q in (self._ros_queue, self._redis_queue):
        try:
            q.put_nowait(chunk)
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(chunk)
            except queue.Full:
                pass
    return self._stop_event.is_set()
```

### destroy_node() with Timeout Guard

```python
# Source: PITFALLS.md Pitfall 1 + D-06
def destroy_node(self):
    self.get_logger().info("HackRFNode shutting down...")
    if hasattr(self, '_reconnect_timer') and self._reconnect_timer:
        self._reconnect_timer.cancel()
    if self._hackrf:
        with self._device_lock:
            if self.is_hackrf_streaming:
                self._stop_event.set()
                try:
                    self._hackrf.stop_rx()
                    self.is_hackrf_streaming = False
                except (RuntimeError, OSError) as e:
                    self.get_logger().error(f"stop_rx failed during shutdown: {e}")
                finally:
                    self._stop_event.clear()
            try:
                self._hackrf.close()
            except (RuntimeError, OSError) as e:
                self.get_logger().error(f"close failed during shutdown: {e}")
    super().destroy_node()
    self.get_logger().info("HackRFNode destroyed.")
```

### Exception Types to Catch from pyhackrf2

```python
# Source: pypi.org/project/pyhackrf2, WebSearch (LOW confidence on exact types)
# pyhackrf2 1.0.3 raises RuntimeError for device-not-found and OSError for libusb failures.
# Broad exception catch is acceptable ONLY in the reconnect loop (to handle unknown failure modes).
# In _configure_device, catch (RuntimeError, OSError) specifically.
try:
    self._hackrf = pyhackrf2.HackRF()
except (RuntimeError, OSError) as e:
    # Expected: device not plugged in, permissions issue, libusb error
    self.get_logger().error(f"HackRF open failed: {e}")
    return False
except Exception as e:
    # Unexpected: log with traceback for debugging
    self.get_logger().error(f"Unexpected error opening HackRF: {e}", exc_info=True)
    return False
```

**Confidence note:** Specific exception types from pyhackrf2 are LOW confidence — the library has thin documentation. The `(RuntimeError, OSError)` pattern is consistent with WebSearch findings and how similar ctypes wrappers fail. If a different exception escapes, the broad `except Exception` second clause ensures reconnect still fires.

### iq_plotter_node.py Topic Update (D-12)

```python
# Source: direct code inspection iq_plotter_node.py line 26 + D-11
# Change:
self.subscription = self.create_subscription(
    Float32MultiArray,
    '/hackrf_iq_data',   # OLD
    ...
)
# To:
self.subscription = self.create_subscription(
    Float32MultiArray,
    '/hackrf/iq',        # NEW — D-11
    ...
)
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `np.append()` in RX callback (O(n) per call) | `queue.Queue.put_nowait()` (O(1)) | Phase 1 | Eliminates OOM risk at high sample rates |
| Shared `current_samples_buffer` (race condition) | Dual bounded queues | Phase 1 | Thread safety without explicit locking in callback |
| `stop_rx()` called synchronously in parameter handler | `threading.Event` + executor-thread `stop_rx()` | Phase 1 | Eliminates deadlock |
| `return 0` in RX callback | `return False` or `return self._stop_event.is_set()` | Phase 1 | Correct pyhackrf2 protocol |
| Bare `print()` debug statements | `self.get_logger().debug/info/warning/error()` | Phase 1 | ROS2-native log level control |
| `.warn()` (deprecated) | `.warning()` | Phase 1 | Avoids deprecation warnings in ROS2 |

**Deprecated/outdated patterns being removed:**
- `HackRFPuiblisherNode` (typo) → `HackRFNode`
- Node name `hackrf_publisher_node` → `hackrf_node` (D-10)
- Topic `/hackrf_iq_data` → `/hackrf/iq` (D-11)
- `num_iq_samples_per_publish` parameter → fixed 2048 (D-04)
- `current_samples_buffer` numpy array → two `queue.Queue` instances (D-01)

---

## Files to Modify

| File | Changes Required | Scope |
|------|-----------------|-------|
| `hackrf_ros/hackrf_node.py` | Full refactor: class rename, dual queues, reconnect loop, param validation, logging cleanup | Primary target |
| `hackrf_ros/iq_plotter_node.py` | One line: topic `/hackrf_iq_data` → `/hackrf/iq`; fix `.warn()` → `.warning()` if present | Minor update |
| `config/hackrf_rx.yaml` | Update parameter names to match new schema (remove `num_samples`, align keys) | Config alignment |
| `setup.py` | Update entry points if class name changes entry; fill TODO placeholders (maintainer, license) | Package metadata |

---

## Open Questions

1. **pyhackrf2 exact exception types**
   - What we know: `RuntimeError` and `OSError` are consistent with WebSearch findings and ctypes wrapper patterns
   - What's unclear: The library source is not inspectable in this environment; no official exception documentation exists
   - Recommendation: Use `(RuntimeError, OSError)` as the primary catch with a fallback `except Exception` second clause; log with `exc_info=True` so the full traceback surfaces if an unknown exception fires. Adjust after first hardware test run.

2. **`_on_parameter_event` return type — list vs. single result**
   - What we know: Current code returns a list of `SetParametersResult`; this is correct per rclpy
   - What's unclear: Whether rclpy Humble enforces length == len(params) strictly or accepts shorter lists
   - Recommendation: Always return a list with one result per input parameter (same length as `params`). This is the safe, documented behavior.

3. **RX callback: does pyhackrf2 1.0.3 pass bytes or bytearray?**
   - What we know: PyPI docs say `data: bytes`. Current code uses `np.frombuffer(data, dtype=np.int8)` directly.
   - What's unclear: Whether the object is mutable (bytearray) or immutable (bytes). `np.frombuffer` on a bytearray creates a read-write view; on bytes it creates a read-only view.
   - Recommendation: `bytes(data)` in the callback (copies once, immutable) before enqueuing. This is correct regardless of the underlying type.

---

## Environment Availability

Step 2.6: SKIPPED for the environment audit portion — this phase has no external dependencies beyond what is already installed in the Docker container. The refactor uses only stdlib modules (`queue`, `threading`, `time`) and packages already present (`pyhackrf2`, `rclpy`, `numpy`).

| Dependency | Required By | Available | Notes |
|------------|------------|-----------|-------|
| `queue.Queue` | RX-01, RX-07 | ✓ | Python stdlib |
| `threading.Event`, `threading.RLock` | RX-05 | ✓ | Python stdlib |
| `pyhackrf2` | All RX requirements | ✓ | In Docker image |
| `rclpy` | All RX requirements | ✓ | ROS2 Humble base image |
| `numpy` | RX-07 consumer side | ✓ | In Docker image |

No missing dependencies. No fallbacks needed.

---

## Project Constraints (from CLAUDE.md)

- **Framework:** ROS2 Humble with Python (rclpy) — no framework changes
- **Hardware target:** Single HackRF One with Portapack/Mayhem — no multi-device abstractions
- **Docker compatibility:** Must work in `empyreanlattice/hackrf_ros:humble` — no new pip installs
- **GSD workflow enforcement:** Changes must go through GSD execute-phase, not direct edits
- **Naming conventions:** snake_case methods, PascalCase classes, `_private` prefix for internals, `publisher_` suffix for ROS publishers
- **Error handling:** Specific exceptions preferred; include context in f-strings; graceful degradation (node continues if HackRF absent)
- **Logging:** `self.get_logger()` only — no `print()`; use debug/info/warning/error levels appropriately
- **Test infrastructure:** flake8, pep257, copyright tests exist; no unit tests yet (acceptable per CONCERNS.md — this phase does not add test infrastructure)

---

## Sources

### Primary (HIGH confidence)
- `.planning/research/PITFALLS.md` — Pitfalls 1, 6, 10, 14 directly apply to this phase; confirmed against libhackrf issue tracker
- `.planning/research/ARCHITECTURE.md` — Queue threading model, callback group design, data flow
- `.planning/codebase/ARCHITECTURE.md` — Current data flow, layer boundaries, exact line numbers
- `.planning/codebase/CONCERNS.md` — All 11 `print()` sites, 2 `.warn()` sites, all `except Exception` sites with line numbers
- `hackrf_ros/hackrf_node.py` — Direct code inspection (318 lines)
- `hackrf_ros/iq_plotter_node.py` — Direct code inspection (topic name, subscriber line)
- `.planning/phases/01-rx-pipeline-correctness/01-CONTEXT.md` — Locked decisions D-01 through D-14
- Python stdlib docs — `queue.Queue`, `threading.Event`, `threading.RLock` (HIGH confidence, stable API)

### Secondary (MEDIUM confidence)
- [pyhackrf2 on PyPI](https://pypi.org/project/pyhackrf2/) — version 1.0.3, callback signature `(data: bytes) -> bool`
- [ROS2 Callback Groups docs](https://docs.ros.org/en/galactic/How-To-Guides/Using-callback-groups.html) — `MutuallyExclusiveCallbackGroup` behavior
- [rclpy GIL issue #1025](https://github.com/ros2/rclpy/issues/1025) — GIL contention in MultiThreadedExecutor

### Tertiary (LOW confidence)
- WebSearch: pyhackrf2 exception types — `RuntimeError`/`OSError` consistent with search results but not officially documented; verify on first hardware test run

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — stdlib queue/threading are stable; pyhackrf2 and rclpy are confirmed installed
- Architecture: HIGH — based on direct code inspection and CONTEXT.md locked decisions
- Pitfalls: HIGH — sourced from project PITFALLS.md which was confirmed against libhackrf issue tracker
- Exception types: LOW — pyhackrf2 has no formal exception documentation; inferred from wrapper patterns

**Research date:** 2026-03-29
**Valid until:** Stable (no fast-moving libraries; pyhackrf2 1.0.3 has been stable since July 2023)
