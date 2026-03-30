# Architecture Patterns

**Domain:** ROS2 SDR driver with dual hardware interface, Redis streaming, and safe TX control
**Researched:** 2026-03-29
**Overall confidence:** HIGH (existing codebase is known, patterns are well-established)

---

## Recommended Architecture

The system is organized as a single ROS2 Python process containing one primary driver node
(`HackRFNode`) that owns all hardware interfaces, plus a thin visualization node
(`IQPlotterNode`) that remains unchanged. A background thread inside the driver node runs
the Redis bridge. The serial Mayhem interface lives inside the same node as a dedicated
`MayhemSerial` helper object, not a separate node — avoiding IPC overhead and keeping
hardware lifecycle management in one place.

```
┌─────────────────────────────────────────────────────────────────┐
│  Docker Container                                               │
│                                                                 │
│  ┌──────────────────────── HackRFNode (rclpy.Node) ──────────┐  │
│  │                                                           │  │
│  │  ┌─────────────────┐   thread-safe Queue                 │  │
│  │  │  RX Pipeline    │──────────────────────────────────┐  │  │
│  │  │  (pyhackrf2 CB) │                                  │  │  │
│  │  └─────────────────┘                                  ▼  │  │
│  │                                            ┌──────────────┐ │  │
│  │  ┌─────────────────┐                       │ Redis Bridge │ │  │
│  │  │  Config Layer   │                       │  (thread)    │ │  │
│  │  │  (ROS2 params)  │                       └──────┬───────┘ │  │
│  │  └─────────────────┘                             │          │  │
│  │                                                  │  XADD    │  │
│  │  ┌──────────────────────────────────┐            │          │  │
│  │  │  TX Controller                   │            │          │  │
│  │  │  (auth gate + serial dispatch)   │            │          │  │
│  │  └────────────┬─────────────────────┘            │          │  │
│  │               │                                  │          │  │
│  │  ┌────────────▼─────────────┐                    │          │  │
│  │  │  MayhemSerial            │                    │          │  │
│  │  │  (/dev/ttyACM1)          │                    │          │  │
│  │  └──────────────────────────┘                    │          │  │
│  │                                                  │          │  │
│  └──────────────────────────────────────────────────┼──────────┘  │
│                                                     │             │
│  ┌──────────────────────────────────────────────────┼──────────┐  │
│  │  IQPlotterNode (optional, visualization only)    │          │  │
│  │  Subscribes /hackrf/iq_data ─────────────────────┘          │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
         │ USB (libusb/pyhackrf2)        │ TCP
         ▼                              ▼
    HackRF One                    Redis (host)
    + Portapack                   ├── XADD iq:raw (stream)
    /dev/ttyACM1                  ├── SET  hackrf:state (hash)
                                  └── SUBSCRIBE hackrf:cmd (pubsub)
```

---

## Component Boundaries

### RX Pipeline

| Attribute | Detail |
|-----------|--------|
| Responsibility | Receive raw IQ bytes from pyhackrf2 callback, convert int8 pairs to complex64, deposit normalized samples into the shared Queue |
| Lives in | `HackRFNode._rx_callback()` (pyhackrf2 callback thread) |
| Outputs | `queue.Queue` entries of `numpy.ndarray` (complex64 chunks) |
| Thread | pyhackrf2 USB interrupt thread — not the ROS2 executor thread |
| Must NOT | Block, allocate large buffers, or call ROS2 APIs directly |

The existing buffer approach (`current_samples_buffer`) is replaced with `queue.Queue` to
eliminate the unprotected shared-state race condition identified in the existing codebase.
`queue.Queue` is thread-safe by design in CPython without an explicit lock.

### ROS2 Publisher (timer callback)

| Attribute | Detail |
|-----------|--------|
| Responsibility | Drain the Queue on a timer, build `Float32MultiArray`, publish to `/hackrf/iq_data` |
| Lives in | `HackRFNode._publish_iq()` (ROS2 timer callback, executor thread) |
| Inputs | `queue.Queue` |
| Outputs | ROS2 topic `/hackrf/iq_data` |
| Thread | ROS2 executor (SingleThreadedExecutor is sufficient here) |

### Redis Bridge

| Attribute | Detail |
|-----------|--------|
| Responsibility | Forward IQ chunks to Redis Stream; publish device state; consume Redis command channel |
| Lives in | `HackRFNode._redis_bridge_loop()` running in a `threading.Thread` |
| Inputs | Separate `queue.Queue` cloned from RX pipeline (or second put from _rx_callback); Redis SUBSCRIBE on `hackrf:cmd` |
| Outputs | `XADD hackrf:iq` (binary numpy bytes), `HSET hackrf:state`, acts on commands by calling node methods |
| Thread | Dedicated daemon thread, runs `redis-py` blocking calls |
| Must NOT | Call ROS2 publisher/subscriber directly — use a thread-safe shared state object or post work back via a thread-safe callback approach |

IQ data is serialized as `numpy.ndarray.tobytes()` (raw little-endian float32 interleaved
I/Q). The consumer reconstructs with `numpy.frombuffer(data, dtype=numpy.float32)`.

Redis command dispatch (e.g., `set_frequency`) calls node methods protected by a
`threading.Lock` to guard device reconfiguration state shared with the ROS2 timer thread.

### TX Controller

| Attribute | Detail |
|-----------|--------|
| Responsibility | Gate all transmit commands behind explicit authorization; dispatch authorized commands to `MayhemSerial` |
| Lives in | `TXController` helper class inside `hackrf_ros/tx_controller.py` |
| Authorization model | Two-step: (1) `authorize_tx(duration_s, frequency_hz)` stores a time-bounded token; (2) `transmit(...)` checks token validity before serial dispatch |
| Token lifetime | Configurable, default 10 seconds — expires even if not used |
| Inputs | ROS2 service calls (`hackrf/authorize_tx`, `hackrf/transmit`) or Redis commands routed through Redis Bridge |
| Outputs | Commands to `MayhemSerial`; publishes TX state to `/hackrf/tx_state` topic |
| Thread safety | All public methods protected by a single `threading.RLock` |

Authorization token is an in-memory object. It is never persisted. Node restart clears all
authorization. This is the correct safety model: accidental transmissions require both an
explicit authorize call AND a transmit call within the window.

### Mayhem Serial Interface

| Attribute | Detail |
|-----------|--------|
| Responsibility | Send and receive Mayhem serial console commands over `/dev/ttyACM1` |
| Lives in | `MayhemSerial` helper class inside `hackrf_ros/mayhem_serial.py` |
| Protocol | Line-oriented text commands over ACM serial (115200 baud default) |
| Supported commands | `appstart <name>`, `applist`, `setfreq <hz>`, `sendpocsag`, `radioinfo`, `reboot`, `hackrf`, `sysinfo` |
| Thread safety | Single `threading.Lock` — only one command in-flight at a time |
| Error model | Timeout (2s default) + retry (1 attempt) + structured exception; caller decides recovery |
| Device failure | Reconnect loop with exponential backoff, max 30s |

The `setfreq` command only works in specific Mayhem apps (Audio, Capture, APRS, etc.). The
TX Controller must ensure the correct app is running before calling `setfreq` or TX commands.

### Config Layer

| Attribute | Detail |
|-----------|--------|
| Responsibility | Declare, validate, and apply ROS2 parameters to the HackRF device |
| Lives in | `HackRFNode._configure_device()` |
| Validation | Explicit range checks before assignment (frequency: 1 MHz–6 GHz, LNA: 0–40 dB, VGA: 0–62 dB, sample rate: 2–20 MHz) |
| Parameter change | Handled in `_on_parameter_event()` — must stop RX stream, reconfigure, restart RX stream, holding `_device_lock` throughout |

---

## Data Flow

### RX: IQ Samples from Hardware to Redis

```
pyhackrf2 USB interrupt
  └─> _rx_callback(raw_bytes)
        ├─> reshape + normalize to complex64
        ├─> iq_queue.put(chunk)           # to ROS2 publisher
        └─> redis_queue.put(chunk)        # to Redis bridge

Timer callback (5ms)
  └─> _publish_iq()
        ├─> drain iq_queue (up to N samples)
        ├─> interleave to float32 array
        └─> publish Float32MultiArray -> /hackrf/iq_data

Redis bridge thread
  └─> _redis_bridge_loop()
        ├─> drain redis_queue
        ├─> chunk.tobytes() -> XADD hackrf:iq {data: <bytes>, ts: <ns>}
        └─> HSET hackrf:state {freq, gain, sample_rate, streaming}
```

Using two separate queues (not one shared queue with multiple consumers) avoids a consumer
stalling the other. IQ data volume at 10 MSPS complex64 is ~80 MB/s — both queues must be
sized and drained fast enough to avoid unbounded growth. Default max queue depth: 100 chunks.
If a queue is full, the oldest entry is discarded (not blocked) to prevent memory exhaustion.

### Commands: Redis to Device

```
Redis SUBSCRIBE hackrf:cmd
  └─> Redis bridge thread receives message
        ├─> parse command JSON
        ├─> validate parameters
        └─> dispatch:
              set_frequency -> HackRFNode._set_frequency() [_device_lock]
              set_gain      -> HackRFNode._set_gain()      [_device_lock]
              authorize_tx  -> TXController.authorize_tx() [tx_lock]
              transmit      -> TXController.transmit()     [tx_lock]
              app_start     -> MayhemSerial.app_start()    [serial_lock]
```

### TX: Authorization to Transmission

```
Caller (Redis or ROS2 service)
  └─> authorize_tx(frequency_hz, duration_s)
        └─> TXController stores AuthToken(freq, expiry, used=False)

Caller
  └─> transmit(params)
        ├─> check: token exists?         -> TXAuthError if not
        ├─> check: token not expired?    -> TXAuthError if expired
        ├─> check: freq matches token?   -> TXAuthError if mismatch
        ├─> mark token used=True
        ├─> MayhemSerial.app_start("tx_app")
        ├─> MayhemSerial.set_frequency(frequency_hz)
        └─> MayhemSerial.send_tx_command(params)
```

One token = one transmission. A new `authorize_tx` call is required for every TX operation.
There is no "stay authorized" mode.

---

## Thread Model

```
Thread                    Owns                           Shares (protected by)
─────────────────────────────────────────────────────────────────────────────
pyhackrf2 USB CB          raw byte conversion            iq_queue (queue.Queue)
                                                         redis_queue (queue.Queue)

ROS2 executor             timer, param, service CBs      _device_lock (RLock)
                                                         _tx_controller (RLock)

Redis bridge thread       redis-py client                redis_queue (queue.Queue)
                                                         _device_lock (RLock) — acquired briefly
                                                         _tx_controller (via method calls)

MayhemSerial (called      serial.Serial object           _serial_lock (Lock)
 by TX controller or
 Redis bridge)
```

Key rules:
1. The pyhackrf2 callback must never hold `_device_lock` — it only puts to queues.
2. `_device_lock` is an `RLock` so the ROS2 executor thread can call `_configure_device` recursively if needed.
3. The Redis bridge thread acquires `_device_lock` only for device reconfiguration commands — never for IQ publishing.
4. `MayhemSerial._serial_lock` prevents concurrent serial writes from Redis bridge and TX controller.

---

## ROS2 Executor and Callback Groups

Use `MultiThreadedExecutor` with two callback groups:

| Callback Group | Type | Members |
|----------------|------|---------|
| `device_cbg` | MutuallyExclusive | timer (publish IQ), parameter event callback |
| `service_cbg` | MutuallyExclusive | `authorize_tx` service, `transmit` service |

Two MutuallyExclusive groups running in a `MultiThreadedExecutor(num_threads=2)` means
device callbacks and service callbacks can overlap, but within each group they are serialized.
This prevents a service call from blocking IQ publication.

Do NOT use Reentrant groups — the device and serial resources are not reentrant-safe.

---

## Suggested Build Order

Dependencies flow from bottom to top:

```
Phase 1: Foundation (no new dependencies)
  ├─> Fix HackRFNode class naming and thread safety (replace buffer with queue.Queue)
  ├─> Add parameter validation with explicit range checks
  └─> Add structured logging + lifecycle management (reconnect loop)

Phase 2: MayhemSerial (depends on: clean HackRFNode)
  ├─> Implement MayhemSerial helper class
  ├─> Wire into HackRFNode (serial open/close in node lifecycle)
  └─> Expose ROS2 service: hackrf/mayhem_cmd

Phase 3: Redis Bridge (depends on: queue.Queue refactor from Phase 1)
  ├─> Implement Redis bridge thread (IQ stream + state hash)
  ├─> Implement Redis command subscriber
  └─> Add config parameter: redis_url, redis_iq_stream_key, redis_state_key

Phase 4: TX Controller (depends on: MayhemSerial from Phase 2)
  ├─> Implement TXController with auth token model
  ├─> Expose ROS2 services: hackrf/authorize_tx, hackrf/transmit
  └─> Wire TX commands from Redis bridge through TXController
```

Each phase is independently testable. Phase 1 can be validated with the existing plotter node.
Phase 2 can be tested with a serial terminal before Redis exists. Phase 3 can be tested with
redis-cli before TX exists.

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Shared Numpy Buffer Without Lock

**What goes wrong:** The existing `current_samples_buffer` is written by the pyhackrf2
callback thread and read by the ROS2 timer thread without synchronization. Under CPython's
GIL this is unlikely to corrupt memory but IS subject to torn reads on large arrays.

**Instead:** Use `queue.Queue` — atomicity is guaranteed by Queue's internal lock, and the
producer-consumer separation is explicit.

### Anti-Pattern 2: Blocking Redis Calls Inside ROS2 Callbacks

**What goes wrong:** `redis.xadd(...)` inside a timer callback blocks the ROS2 executor.
At high IQ sample rates (10 MSPS) and Redis on localhost, typical XADD latency is <1ms but
network hiccups cause callback jitter and missed publication cycles.

**Instead:** Redis writes happen exclusively in the dedicated Redis bridge thread. The ROS2
executor never touches the Redis client.

### Anti-Pattern 3: TX Without Explicit Authorization Per-Transmission

**What goes wrong:** A "session authorization" model (authorize once, transmit many times)
allows runaway transmissions if the transmit path is triggered erroneously.

**Instead:** One token = one transmission. Each call to `transmit()` consumes the token.

### Anti-Pattern 4: Serial Writes from Multiple Threads Without Lock

**What goes wrong:** If the Redis bridge thread and the TX controller both call
`MayhemSerial` concurrently, commands are interleaved on the serial wire and responses
are mis-parsed.

**Instead:** All `MayhemSerial` public methods acquire `_serial_lock` at entry.

### Anti-Pattern 5: Blocking Serial Read in Main Thread

**What goes wrong:** `serial.readline()` with a 2-second timeout blocks the entire
ROS2 executor if placed in a timer callback, starving parameter updates and publishers.

**Instead:** Serial reads happen inside `MayhemSerial._send_command()`, which is only
called from the Redis bridge thread or TX controller thread — never from a ROS2 callback.

---

## Module Layout (Proposed)

```
hackrf_ros/
  hackrf_node.py          # HackRFNode (refactored) — main entry point
  mayhem_serial.py        # MayhemSerial helper class
  redis_bridge.py         # RedisBridge thread class
  tx_controller.py        # TXController with auth token model
  iq_plotter_node.py      # Unchanged visualization node
```

Each helper is a plain Python class (not a ROS2 node), instantiated and owned by
`HackRFNode`. This keeps the ROS2 node count at 1 (driver) + 1 (optional plotter),
avoiding the overhead of ROS2 IPC for intra-process communication.

---

## Scalability Considerations

| Concern | Current (prototype) | After milestone |
|---------|---------------------|-----------------|
| IQ throughput | Single numpy buffer, no back-pressure | Two bounded queues, drop-on-full |
| Redis stream size | N/A | Use `MAXLEN ~` trim (approximate, fast) to cap stream at ~10k entries |
| Serial reliability | N/A | Reconnect loop with backoff |
| TX races | N/A | RLock + token model prevents concurrent TX |
| Memory | Unbounded buffer growth possible | Bounded queues (100 chunks default) |

---

## Sources

- Mayhem firmware serial console commands: [USB Serial Console Wiki](https://github.com/portapack-mayhem/mayhem-firmware/wiki/usb-serial-console) — HIGH confidence (official wiki)
- ROS2 callback groups and MultiThreadedExecutor: [ROS2 Humble Callback Groups](https://docs.ros.org/en/humble/How-To-Guides/Using-callback-groups.html) — HIGH confidence (official docs)
- Redis Streams with redis-py: [redis-py Stream Examples](https://redis.readthedocs.io/en/stable/examples/redis-stream-example.html) — HIGH confidence (official docs)
- Python thread-safe queue: [Python queue module](https://docs.python.org/3/library/queue.html) — HIGH confidence (official docs)
- Existing codebase analysis: `.planning/codebase/ARCHITECTURE.md` — HIGH confidence (direct code inspection)

---

*Architecture research: 2026-03-29*
