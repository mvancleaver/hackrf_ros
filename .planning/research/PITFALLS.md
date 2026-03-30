# Domain Pitfalls

**Domain:** HackRF One SDR ROS2 driver — Redis integration, Mayhem serial control, TX capabilities
**Researched:** 2026-03-29
**Confidence:** HIGH (confirmed against libhackrf issue tracker, Mayhem wiki, Redis docs, rclpy issues)

---

## Critical Pitfalls

Mistakes that cause deadlocks, data loss, hardware damage, or legal liability.

---

### Pitfall 1: Calling hackrf_stop_rx From Inside the RX Callback (Deadlock)

**What goes wrong:** `hackrf_stop_rx()` (and by extension `pyhackrf2`'s `stop_rx()`) must be called from the main thread. The libhackrf transfer callback runs in a libusb async thread. If any code path inside `_rx_callback` calls `stop_rx()` — directly or via a signal to the ROS node — it will deadlock: the transfer thread waits for itself to finish.

**Why it happens:** libhackrf's internal transfer cancellation uses a pthread condition variable. The callback thread cannot cancel itself. The existing codebase already has `*args` and commented-out paths that suggest confusion about when to stop; future TX/serial integration increases the risk of adding inadvertent cross-thread stop calls.

**Consequences:** The node hangs indefinitely. The HackRF RX LED stays on. The only recovery is killing the process and power-cycling the device. This is a confirmed, closed upstream issue — maintainers explicitly do not recommend Python for start/stop cycling.

**Prevention:**
- The `_rx_callback` must return `0` (continue) or a non-zero value (signal stop) — it must NEVER call `stop_rx()` directly.
- Use a `threading.Event` to signal the main/timer thread that a stop is needed; let that thread call `stop_rx()`.
- Minimize start/stop cycles. Prefer keeping RX running continuously and gating publication instead of stopping the stream on reconfiguration.
- Add a timeout wrapper around any `stop_rx()` call (e.g., `signal.alarm` or a watchdog thread) so a deadlock does not hang the process permanently.

**Detection:**
- RX LED remains lit after `stop_rx()` is called.
- Node becomes unresponsive; `destroy_node()` never returns.
- Observed during parameter reconfiguration: `_configure_hackrf()` currently calls `stop_rx()` in a `try` block with no timeout — this is the immediate live risk.

**Phase:** RX pipeline robustness (first active milestone phase). Fix before adding Redis or serial.

---

### Pitfall 2: Unbounded IQ Buffer + Redis Writes in the Same Thread = Guaranteed Data Loss

**What goes wrong:** The current `_rx_callback` appends to `current_samples_buffer` using `np.append()` (which allocates a new array on every call). The timer at 5 ms polls the buffer. At 8 MSPS, pyhackrf2 delivers roughly 16,000 bytes per callback invocation — thousands of calls per second. If Redis `XADD` is called inside the same timer callback that drains the buffer, any Redis latency spike (network, serialization, key eviction) causes the buffer to grow unboundedly.

**Why it happens:** `np.append()` is O(n) per call. At 8 MSPS the buffer grows at approximately 32 MB/s of float32 data before any Redis drain occurs. A 100 ms Redis round-trip jitter = ~3.2 MB of buffer spike. Without a cap, this becomes an OOM in minutes.

**Consequences:** Python process killed by OOM killer (silent in Docker). Data loss with no warning. Potentially unstable ROS node that takes the whole container down.

**Prevention:**
- Replace `np.append()` with a `collections.deque(maxlen=N)` or a pre-allocated `numpy` ring buffer. The maxlen acts as the backpressure mechanism — oldest samples are dropped rather than memory growing.
- Use `XADD key MAXLEN ~ 1000 * ...` (approximate trimming) so Redis also caps its own memory from this stream.
- Dedicate a separate thread (not the ROS timer) for Redis writes, using a thread-safe queue (`queue.Queue(maxsize=N)`) as the handoff. The RX callback enqueues; the Redis thread dequeues and publishes.
- Never block the RX callback or ROS timer waiting on a network call.

**Detection:**
- Container memory grows monotonically under `docker stats`.
- Buffer size log crosses 100K samples with no sign of draining.
- Redis publish latency measured above 10 ms consistently.

**Phase:** Redis IQ publishing. Must be designed correctly from the start — retrofitting backpressure after the pipeline is built is expensive.

---

### Pitfall 3: TX Without Antenna Physically Damages the HackRF

**What goes wrong:** Transmitting with no antenna (or a severely mismatched one) causes the output amplifier (U25, MGA-81563) to receive full reflected power. The component is rated to approximately +13 dBm input; a mismatched load at even modest TX power can exceed this, permanently destroying the front-end amplifier. The HackRF will still enumerate over USB but TX output will be near zero.

**Why it happens:** The software has no way to detect whether an antenna is connected. There is no hardware interlock. The TX authorization guardrail in the project requirements gates commands behind user intent — but it does not prevent hardware damage from a careless operator who authorizes TX without attaching an antenna.

**Consequences:** Permanent hardware damage. No repair path without component-level rework. The only HackRF in this project is a single device — destroying it halts the entire project.

**Prevention:**
- The TX authorization flow must include a checklist prompt that explicitly asks the operator to confirm antenna is connected, not just "do you want to transmit?".
- Log a persistent `WARNING` to the ROS logger and Redis state key every time TX is authorized, with the message "CONFIRM ANTENNA CONNECTED BEFORE TX".
- Set a conservative default TX gain (VGA TX gain 0 dB) that requires an explicit override, rather than defaulting to a higher power level.
- Document the hardware damage failure mode in the node's startup banner.

**Detection:** No warning sign before damage occurs. Post-damage: TX LED activates but spectrum analyzer shows near-zero output. Prevention is the only option.

**Phase:** TX capability phase. Address in the authorization guardrail design, not as an afterthought.

---

### Pitfall 4: Mayhem Serial Mode Conflict — Serial Console and HackRF Bulk Transfer Are Mutually Exclusive

**What goes wrong:** The Mayhem firmware wiki states explicitly: "You SHOULD NOT enter HackRF mode when using the serial console." When the PortaPack is put into HackRF mode (the blue-screen USB passthrough mode), the serial ACM interface becomes unavailable or unreliable. The project relies on pyhackrf2 (libusb bulk transfer, which requires HackRF mode) AND serial `/dev/ttyACM1` (which requires Portapack mode) simultaneously.

**Why it happens:** The PortaPack has two distinct USB personalities depending on firmware mode. In Portapack mode, USB exposes a CDC-ACM serial interface for the ChibiOS shell. In HackRF mode, it exposes the standard HackRF USB bulk interface for libusb/pyhackrf2. These are not the same mode.

**Consequences:** If the driver tries to open `pyhackrf2.HackRF()` while the device is in Portapack mode, libusb will fail to find the expected USB endpoints, giving `HACKRF_ERROR_LIBUSB (-1000)`. If the driver tries to open serial while in HackRF mode, the ACM device may not enumerate at all.

**Prevention:**
- Treat the two interfaces as separate device states and define a state machine: "Portapack mode" for serial commands (app control, TX via Mayhem), "HackRF mode" for raw IQ streaming (pyhackrf2).
- Understand whether this firmware version allows concurrent access or requires a mode switch via the `hackrf` serial command. Test empirically with the target device before writing the integration layer.
- Design the driver to detect which mode the device is in on connection and communicate the constraint clearly in error messages.
- Consider whether the project actually needs simultaneous IQ streaming and Mayhem serial control, or whether these are sequential operations.

**Detection:**
- `pyhackrf2.HackRF()` raises `HACKRF_ERROR_LIBUSB` when the device is in Portapack shell mode.
- `/dev/ttyACM1` is absent or unresponsive when the device is in HackRF mode.
- USB enumeration shows different VID/PID or interface descriptors between modes.

**Phase:** Serial Mayhem control phase and RX pipeline phase. Resolve the mode architecture question before writing either integration.

---

### Pitfall 5: Accidental Transmission on Protected Frequencies

**What goes wrong:** HackRF covers 1 MHz to 6 GHz. Without frequency validation in the TX path, a bug or malformed Redis command could trigger transmission on emergency services (700/800 MHz), GPS (1.575 GHz), aviation (108–137 MHz), or cellular bands — all of which carry serious legal liability under FCC Part 97/Part 15 and equivalents in other jurisdictions. HackRF does not have hardware frequency locks.

**Why it happens:** The Redis command interface accepts parameters from external consumers with no inherent validation. A consumer sending a malformed frequency value, or a developer testing with a "convenient" frequency, can inadvertently transmit on a protected band.

**Consequences:** FCC enforcement action, fines, criminal liability for deliberate interference. Amateur radio bands require a license. The driver's TX authorization guardrail must validate frequency, not just intent.

**Prevention:**
- Maintain an allowlist of safe test frequencies (ISM bands: 915 MHz, 2.4 GHz, 5.8 GHz) and require explicit override to transmit elsewhere.
- Implement a frequency validation function that rejects TX requests outside explicitly permitted bands: reject anything not in a declared safe zone.
- Log every TX authorization with timestamp, frequency, power, and duration to a persistent Redis key — creates an audit trail.
- Make the allowlist configurable but default to the narrowest safe set (e.g., 2.4 GHz ISM only) rather than permissive.
- Never permit TX in the 406–406.1 MHz EPIRB band, 121.5 MHz (aviation distress), 156.8 MHz (maritime distress), or GPS L1/L2 regardless of authorization.

**Detection:** No hardware interlock will catch this. Frequency validation must be enforced in software. An absent allowlist check is the detection trigger during code review.

**Phase:** TX authorization guardrails phase. This must be in the first TX implementation — cannot be deferred to a later hardening phase.

---

## Moderate Pitfalls

---

### Pitfall 6: Python GIL Limits True Parallelism Between RX Callback and ROS Executor

**What goes wrong:** pyhackrf2's RX callback runs in a libusb-spawned C thread. When Python code executes in that callback (the `_rx_callback` function), it must acquire the GIL. The rclpy `spin()` executor also holds the GIL for callback dispatch. At high sample rates (8+ MSPS), these two GIL contenders create measurable latency spikes — the RX callback is delayed waiting for the executor, and ROS timer callbacks are delayed waiting for the RX callback to release.

**Why it happens:** rclpy's MultiThreadedExecutor has documented performance issues with high-frequency callbacks. At 500 Hz timer rates the CPU load is already problematic. The RX callback at 8 MSPS fires orders of magnitude more frequently.

**Consequences:** Samples dropped at the libhackrf level (libhackrf has internal USB transfer buffers; if the callback is too slow, transfers overflow). Jitter in Redis publish times. ROS timer callbacks miss their deadlines.

**Prevention:**
- Keep `_rx_callback` as short as possible — only enqueue data to a `queue.Queue`, nothing else. Do not process, convert, or append numpy arrays in the callback.
- Move all numpy conversion, Redis publishing, and ROS publishing to the timer or a dedicated thread that dequeues from the callback's queue.
- Consider `numpy.frombuffer` (zero-copy) instead of array construction in the callback path.
- Profile with `py-spy` before optimizing — verify the GIL is the actual bottleneck.

**Detection:**
- libhackrf reports overflow: callback returns `HACKRF_ERROR_STREAMING_THREAD_ERR`.
- Dropped samples visible as discontinuities in IQ data (phase jumps, dropped chirps).
- `py-spy` flame graph shows GIL wait time in RX callback thread.

**Phase:** RX pipeline robustness phase.

---

### Pitfall 7: Serial Port Device Path Instability in Docker

**What goes wrong:** `/dev/ttyACM1` is assigned dynamically by the kernel. On container restart, host reboot, or device re-plug, the device may appear as `/dev/ttyACM0`, `/dev/ttyACM2`, or under a different path entirely. Docker volume mounts for `/dev/ttyACM1` that worked at first will silently fail after a replug. The container sees a stale device file.

**Why it happens:** Linux ACM device numbering is assigned in enumeration order. If another USB serial device is present, or the HackRF is unplugged and replugged, the number can change. Docker does not automatically update device mappings after container start.

**Consequences:** Serial connection silently fails; the node logs no error if pyserial opens the path but the device was reassigned. The node runs but serial commands are dropped with no feedback.

**Prevention:**
- Use `/dev/serial/by-id/` symlinks (stable across reboots) rather than `/dev/ttyACM1` in Docker device mappings and in code defaults.
- Make the serial port path a ROS parameter and a configurable Docker environment variable.
- Implement `pyserial` open-with-retry: if the port is not found, retry every 5 seconds with a clear log message rather than failing silently.
- In Docker Compose, bind the stable by-id path: `devices: - /dev/serial/by-id/usb-Great_Scott_Gadgets_...: /dev/ttyACM1`.

**Detection:**
- Serial opens successfully but no response to commands (device path exists but wrong device is behind it).
- `dmesg` on host shows ACM re-enumeration after container start.
- `ls -la /dev/serial/by-id/` shows the actual stable path.

**Phase:** Serial Mayhem control phase.

---

### Pitfall 8: Redis Stream Memory Growth Without MAXLEN

**What goes wrong:** At 8 MSPS with 8192-sample publish chunks, the IQ data rate is approximately 32 MB/s (float32 interleaved). If Redis `XADD` is called without `MAXLEN`, the stream grows without bound. Redis is an in-memory store — the host will run out of RAM in under a minute of streaming if no consumer is reading or trimming.

**Why it happens:** Redis Streams default to unbounded growth. Developers focus on the producer path and assume consumers will keep up. In practice, consumers may disconnect, restart, or be slower than the producer.

**Consequences:** Host Redis OOM → eviction policy kicks in (if configured) or Redis crashes. If Redis crashes, the ROS node's Redis publish calls start failing, producing a flood of error logs and potentially blocking the publisher thread.

**Prevention:**
- Always use `XADD key MAXLEN ~ N * field value` — the `~` (approximate trim) is critical for performance; exact trimming is expensive.
- For IQ data streams, a `MAXLEN` of 100–500 entries (depending on chunk size) is typically appropriate — this is a rolling window, not a history buffer.
- Add Redis connection error handling with exponential backoff in the publisher thread, not bare `try/except` that silently swallows failures.
- Publish IQ data using interleaved float16 or raw int8 bytes rather than float32 lists to reduce payload size by 2–4x.

**Detection:**
- `redis-cli INFO memory` shows `used_memory` growing monotonically.
- `XLEN iq_stream_key` returns a very large number.
- Redis latency increases as memory pressure builds.

**Phase:** Redis IQ publishing phase. Include MAXLEN from the first `XADD` call — do not add it in a follow-up.

---

### Pitfall 9: Mayhem Serial Command Response Parsing — No Guaranteed Prompt/Terminator

**What goes wrong:** The Mayhem ChibiOS shell returns responses with a `ch>` prompt and `\r\n` line endings. There is no standardized response envelope — commands like `appstart`, `setfreq`, and `radioinfo` return different formats. Reading "until prompt" with a fixed timeout is fragile: if the command takes longer than expected (e.g., app startup involves firmware loading), the read times out mid-response, leaving partial data in the serial buffer that corrupts the next command's response.

**Why it happens:** The Mayhem serial interface is a human-facing ChibiOS shell, not a machine protocol. It was designed for interactive terminal use, not programmatic parsing. There is no documented request-response framing.

**Consequences:** Serial command handler enters a corrupted state. Subsequent commands receive partial previous responses. The only recovery is closing and reopening the serial port — which may itself fail due to Pitfall 7.

**Prevention:**
- Implement a proper serial command class with: send command, read until `ch>` prompt (not a fixed timeout), discard echo of the sent command, parse response lines.
- Add a flush/resync routine: send an empty newline and drain the port before every command sequence.
- Set a generous but bounded timeout (e.g., 5 seconds for `appstart`, 500 ms for `setfreq`) with explicit timeout error handling.
- Test each command's response format against the target firmware version before writing the parser.
- Treat the serial channel as stateful: track whether a command is in-flight and reject new commands until the previous one completes.

**Detection:**
- Responses contain fragments of previous command outputs.
- `appstart` returns success but the wrong app is running.
- Serial buffer has data remaining between command cycles.

**Phase:** Serial Mayhem control phase.

---

### Pitfall 10: stop_rx / start_rx Rapid Cycling Causes HackRF Firmware State Corruption

**What goes wrong:** Calling `stop_rx()` immediately followed by `start_rx()` in rapid succession (e.g., on every parameter change) causes an intermittent state where the RX LED stays lit but the callback is never triggered. This is distinct from the deadlock in Pitfall 1 — it is a firmware-level issue where the HackRF's USB transfer state does not fully reset between cycles.

**Why it happens:** The HackRF USB transfer pipeline requires a brief settling period after `stop_rx()` before `start_rx()` is safe to call. The current `_configure_hackrf()` calls `stop_rx()` followed immediately by attribute assignment and `start_rx()` with no delay. Issue #916 in the libhackrf tracker confirms this as a known problem.

**Consequences:** The stream appears to start (no exception) but no callbacks fire. `is_hackrf_streaming` is True but no data is published. The node runs silently and publishes nothing.

**Prevention:**
- Add a minimum 100 ms sleep between `stop_rx()` and `start_rx()` in `_configure_hackrf()`. This is a known workaround from the libhackrf issue tracker (confirmed by upstream reporters).
- Debounce parameter changes: if multiple parameters are changed within a 500 ms window (e.g., via a Redis command batch), coalesce them into a single reconfiguration rather than cycling stop/start once per parameter.
- After `start_rx()`, verify the callback fires within 1 second (watchdog timer); if not, attempt one recovery cycle.

**Detection:**
- `is_hackrf_streaming` is True but `current_samples_buffer` size is not growing.
- RX LED status on the device appears correct but no data flows.
- Only manifests after a parameter change triggers `_configure_hackrf()`.

**Phase:** RX pipeline robustness phase. Fix before adding Redis or serial layers.

---

## Minor Pitfalls

---

### Pitfall 11: IQ Data Format Undocumented — Redis Consumers Will Misinterpret

**What goes wrong:** The current code publishes interleaved float32 I/Q with no metadata about format, sample rate, or center frequency in the same message. Redis consumers that receive the IQ stream have no way to know whether samples are float32, float16, int8, or complex64, or what the current tuning is without reading a separate state key.

**Prevention:**
- Publish IQ chunks to Redis with a header field in the stream entry: `XADD iq_stream * format int8_iq center_freq 2447000000 sample_rate 8000000 chunk <bytes>`.
- Alternatively, publish device state to a separate Redis hash that consumers can read once on connection.
- Document the format in code comments and in a Redis key `hackrf:iq:format` that describes the schema.

**Phase:** Redis IQ publishing phase.

---

### Pitfall 12: TX VGA Gain Quantization Silently Accepts Invalid Values

**What goes wrong:** HackRF TX VGA gain is valid in 1 dB steps from 0–47 dB. LNA TX gain has only two values: 0 dB and 14 dB. If the driver accepts arbitrary gain values and passes them directly to libhackrf, the hardware silently rounds to the nearest valid step. A TX command requesting 15 dB LNA gain will transmit at 14 dB with no error — the authorization system logs a different power level than actually transmitted.

**Prevention:**
- Validate and quantize all gain values before applying them. Round to the nearest valid step and log the actual value applied, not the requested value.
- For the authorization audit log, record the quantized (actual) value, not the requested value.

**Phase:** TX capability phase.

---

### Pitfall 13: Redis Command Interface Has No Authentication — Accepts Commands from Any Host

**What goes wrong:** Redis on the host (default configuration) has no authentication. Any process on the host network can publish to the `hackrf:commands` stream and trigger frequency changes, gain changes, or TX authorization. In a shared lab or CI environment, this is an unintended control surface.

**Prevention:**
- Configure Redis with `requirepass` or use Redis ACLs to restrict write access to the command stream.
- The ROS node should verify that commands carry a shared secret token field before acting on them.
- Document the security model explicitly: this driver is designed for a trusted local environment, not network-exposed deployment.

**Phase:** Redis command interface phase. At minimum, document the assumption; implement token validation if the deployment environment is not fully trusted.

---

### Pitfall 14: `interleaved_iq_data.tolist()` Is Extremely Slow for Large Buffers

**What goes wrong:** The current code calls `interleaved_iq_data.tolist()` to populate the `Float32MultiArray.data` field. For 8192 IQ pairs, this creates a Python list of 16,384 float objects. `tolist()` is O(n) and allocates a new Python object per element — at 200 Hz publish rate this means over 3 million float object allocations per second.

**Prevention:**
- Use `msg.data = interleaved_iq_data.tobytes()` with a custom message type, or assign the numpy array directly if the ROS message type supports it.
- Alternatively, for Redis publishing, write raw bytes directly (`int8` from the HackRF with no conversion) — this eliminates all float conversion overhead in the hot path.

**Phase:** RX pipeline robustness phase (performance hardening).

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| RX pipeline robustness | Pitfall 1 (stop_rx deadlock), Pitfall 10 (rapid cycle corruption), Pitfall 6 (GIL contention), Pitfall 14 (tolist overhead) | Fix stop_rx threading before any other integration; replace np.append and tolist in callback |
| Redis IQ publishing | Pitfall 2 (unbounded buffer), Pitfall 8 (stream memory growth), Pitfall 11 (format documentation) | Design with MAXLEN and backpressure queue from day one |
| Redis command interface | Pitfall 13 (no auth) | Document security model; add token validation if needed |
| Serial Mayhem control | Pitfall 4 (mode conflict), Pitfall 7 (device path instability), Pitfall 9 (response parsing) | Resolve mode architecture question first; use by-id paths; implement proper shell reader |
| TX capability | Pitfall 3 (no antenna hardware damage), Pitfall 5 (protected frequencies), Pitfall 12 (gain quantization) | Antenna confirmation in auth flow; frequency allowlist; quantize and log actual gains |
| TX authorization guardrails | Pitfall 5 (frequency), Pitfall 3 (antenna), Pitfall 13 (Redis command injection) | Allowlist + antenna confirmation + command authentication are all required together |

---

## Sources

- [libhackrf Issue #1570: Deadlock in hackrf_stop_rx](https://github.com/greatscottgadgets/hackrf/issues/1570) — MEDIUM confidence (closed, Python-specific, maintainers redirected but issue is real)
- [libhackrf Issue #916: Repeated Start/Stop RX Produces Error](https://github.com/greatscottgadgets/hackrf/issues/916) — MEDIUM confidence
- [libhackrf Issue #1075: Possible data race without locking](https://github.com/greatscottgadgets/hackrf/issues/1075) — MEDIUM confidence
- [Mayhem Firmware Wiki: USB Serial Console](https://github.com/portapack-mayhem/mayhem-firmware/wiki/USB-Serial-Console) — HIGH confidence (official documentation)
- [HackRF Documentation: HackRF One](https://hackrf.readthedocs.io/en/latest/hackrf_one.html) — HIGH confidence (official)
- [How Not to Break Your SDR Hardware](https://www.onesdr.com/2020/01/29/how-not-to-break-your-software-defined-radio-hardware/) — MEDIUM confidence (community, consistent with official specs)
- [Redis XADD Documentation: MAXLEN trimming](https://redis.io/docs/latest/commands/xadd/) — HIGH confidence (official Redis docs)
- [rclpy Issue #1025: GIL in MultiThreadedExecutor](https://github.com/ros2/rclpy/issues/1025) — HIGH confidence (official ROS2 issue tracker)
- [rclpy Issue #1452: MultiThreadedExecutor performance](https://github.com/ros2/rclpy/issues/1452) — HIGH confidence (official ROS2 issue tracker)
- [pyhackrf2 GitHub](https://github.com/eizemazal/pyhackrf2) — MEDIUM confidence (thin documentation, behavior inferred from libhackrf)
- [Docker serial device path instability](https://forums.docker.com/t/exposed-usb-serial-device-dev-ttyacm0-in-container-only-sometimes-accessible/126309) — MEDIUM confidence (community forum, consistent with known Linux behavior)
