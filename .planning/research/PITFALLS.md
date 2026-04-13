# Domain Pitfalls: RF Sensor ROS2 on ARM64 Jetson

**Domain:** ROS2 lifecycle SDR driver + RF signal processing on embedded ARM64
**Researched:** 2026-04-12
**Sources:** PROJECT.md, docs/rf-ew-review.md, docs/rf-sensor-roadmap.md,
hackrf_lifecycle_node.py, vault/concepts/sdr-iq-pipeline-threading.md,
vault/concepts/detection-theory-np-and-cfar.md, vault/projects/hackrf-ros/hackrf-ros.md

---

## Critical Pitfalls

Mistakes that cause incorrect results, node crashes, or rewrites.

---

### Pitfall 1: Lifecycle Publishers Do Not Transmit Over CycloneDDS on This ARM64 System

**What goes wrong:** `rclpy.lifecycle.LifecyclePublisher` instances created inside a
`LifecycleNode` do not deliver messages to inter-process subscribers when using CycloneDDS on
this Jetson ARM64 deployment. The publisher activates without error; `publish()` returns
without raising; downstream nodes receive nothing. The failure is silent.

**Why it happens:** CycloneDDS's rclpy binding on Humble ARM64 does not correctly handle
the lifecycle publisher's internal `on_activate()` state change for the DDS DataWriter.
The DataWriter is created but its matching history is never enabled for discovery. This is
a platform-specific interaction between the rmw_cyclonedds layer and LifecyclePublisher
state management — it does not manifest on x86_64 with FastRTPS in its tested configurations.

**Consequences:** Every phase that adds a new publisher will rediscover this silently if
the team forgets the rule. Detection requires end-to-end subscriber checks, not just
`ros2 topic list`.

**Prevention:** Use `rclpy.node.Node.create_publisher()` (regular publisher) inside
LifecycleNode. Do not use `self.create_lifecycle_publisher()`. This is already done in
`hackrf_lifecycle_node.py` and must be enforced for all future publishers added to this
node (SpectrumStamped, RFDetectionArray, rf_environment).

**Detection:** Run `ros2 topic echo /hackrf/psd` from a second process after activating
the node. If no messages arrive within 2 seconds of activation, a lifecycle publisher has
been introduced.

**Status:** Known and mitigated in current implementation. Regression risk for every new
publisher added.

---

### Pitfall 2: FastRTPS Broken for Inter-Process Communication on This ARM64 Jetson

**What goes wrong:** `rmw_fastrtps_cpp` fails to establish inter-process DDS discovery
on the specific Jetson ARM64 Docker deployment used by this project. Nodes using FastRTPS
appear to start, advertise topics, and publish without errors, but subscribers in separate
processes never receive messages. The failure does not reproduce on x86_64.

**Why it happens:** FastRTPS inter-process communication relies on shared memory transport
or UDP multicast for participant discovery. On this Jetson system, the shared memory
segment size configured by the default rmw_fastrtps profile exceeds the system-allowed
limit, and the UDP multicast fallback fails due to Docker network isolation in host mode
conflicting with the ARM64-specific default DDS participant locators.

**Consequences:** If any future phase introduces a new Docker service or tries to run
without the explicit `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp` environment variable set,
communication silently fails. New developers will spend days debugging "node appears
running, topic exists, no data."

**Prevention:**
- `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp` must appear in every docker-compose service,
  every launch file environment block, and every test fixture that spawns a node.
- Add a startup assertion in the node: `assert 'cyclone' in os.environ.get('RMW_IMPLEMENTATION', '')`.
- Document this in docker-compose.yaml with a comment, not just in the project README.

**Detection:** `ros2 doctor` reports rmw mismatch. `ros2 topic echo` from a second
terminal receives no data despite `ros2 topic hz` showing publisher rate.

**Status:** Known. CycloneDDS enforced in existing deployment. No test guards it.

---

### Pitfall 3: pyhackrf2 Segfaults on close() After USB Disconnect or Rapid stop_rx/start_rx

**What goes wrong:** Calling `hackrf.close()` after a USB disconnect, or calling
`stop_rx()` immediately followed by `start_rx()` without adequate settling time, causes
libhackrf to free a libusb transfer that is still in-flight. Python raises a segfault
(SIGSEGV in libusb's async completion handler), killing the entire ROS2 node process
without any Python exception or ROS2 diagnostic.

**Why it happens:** libhackrf queues multiple async USB bulk transfers. `hackrf_stop_rx()`
signals the device to stop, but the host-side libusb event loop may still hold references
to pending transfer buffers. `hackrf_close()` frees device memory before all completions
have fired. The pyhackrf2 wrapper does not add additional synchronization on top of
libhackrf's own (minimal) wait logic. The libhackrf issue tracker documents a related
concurrency fix for rapid stop/start sequences (Issue #1042: "USB streaming can stall
after first 16K transfer following RX restart").

**Consequences:** Node dies silently with SIGSEGV. ROS2 lifecycle manager sees the node
go offline but has no crash report. The robot loses RF awareness with no recovery path
unless the launch system restarts the entire node.

**Prevention:**
- Never call `stop_rx()` and then `start_rx()` without a sleep of at least 150 ms.
  Current `_param_callback` already implements a staged retry with 150/300/500 ms delays
  for `_STREAM_RESTART_PARAMS`. This must not be reduced.
- For USB disconnect recovery: do not call `close()` on a disconnected device. Instead,
  set `self._hackrf = None` and let the garbage collector handle it, or wrap `close()`
  in a signal alarm that kills a stuck call after 2 seconds.
- Flush the IQ queue before and after every stop_rx call.
- Do not call any pyhackrf2 method from inside the RX callback (the USB callback thread).
  All device operations must happen from the main thread or a thread that holds
  `_device_lock`.

**Detection:** Node process exits with no Python traceback. `dmesg` shows
`general protection fault` or `segfault at` for the Python process.

**Status:** Partially mitigated by existing restart settle delays. USB disconnect
recovery path (automatic reconnect after disconnect) is not yet implemented.

---

### Pitfall 4: Averaging or Computing in dB Domain Instead of Linear Power

**What goes wrong:** Averaging PSD values that are already in dB introduces a systematic
bias of approximately -2.5 dB on the noise floor. This is not a rounding error — it is
structural. The dB of the average power is not the average of the dBs. The bias is
signal-level-dependent (larger for higher-variance signals), making calibration impossible.

**Why it happens:** dB is a logarithmic scale. Averaging log values computes the geometric
mean of the underlying linear values, not the arithmetic mean. `mean(10*log10(x_i))` equals
`10*log10(geometric_mean(x_i))`, which is always less than or equal to `10*log10(arithmetic_mean(x_i))`.

**Consequences:** Noise floor appears 2-3 dB lower than reality. SNR estimates are
inflated. CFAR thresholds derived from a biased noise floor will produce more false alarms
than the target Pfa predicts.

**Prevention:** Accumulate in linear power (`psd_accum += frame_linear`). Divide by count
in linear. Convert to dB once at the very end (`10 * np.log10(avg_linear)`). This is
already implemented correctly in `_fft_frame()` and `_process_and_publish_inner()`. Any
new averaging path (CFAR background estimator, sweep stitching, band monitor) must follow
the same rule.

**Detection:** Compute the noise floor with N=1 (single frame) and with N=16 using the
same data. If the 16-frame average is 2-3 dB lower than the single frame, averaging is
happening in dB.

**Status:** Fixed in current implementation. High regression risk for CFAR development
(noise floor estimator) and band monitor (baseline averaging).

---

### Pitfall 5: Thread Safety — pyhackrf2 USB Callback vs. ROS2 Executor

**What goes wrong:** The pyhackrf2 RX callback fires from a libusb internal thread (not
the Python main thread, not the ROS2 executor thread). Any shared state touched by both
the callback and the ROS2 timer/service callbacks requires explicit synchronization.
Without it, partial writes and torn reads occur. On ARM64, memory reordering is more
aggressive than on x86_64, making races that are benign on x86 visible on Jetson.

**Specific race patterns that appear:**

1. **Float attribute read/write:** `_last_rx_time` written in the USB callback and read
   in the timer callback. Under CPython's GIL, single float attribute writes are atomic.
   This is safe only in CPython 3.10 (Humble's Python). Not safe in PyPy or future
   free-threaded Python.

2. **Queue access:** `self._iq_queue.put_nowait()` in the USB callback and
   `self._iq_queue.get_nowait()` in the timer callback. `queue.Queue` is
   thread-safe by design. This is correct.

3. **Device method calls from the callback:** Calling any pyhackrf2 method
   (including reading properties like `center_freq`) from inside the RX callback
   while the main thread holds `_device_lock` will deadlock indefinitely. The USB
   callback cannot acquire `_device_lock` because the main thread won't release it
   until a blocking USB operation completes, which can't complete because the callback
   is blocked.

4. **numpy array mutation:** `self._psd_accum` written in the timer callback and
   never written in the USB callback. Safe because the USB callback only writes to
   `_iq_queue`. If any future feature writes `_psd_accum` from a second thread, add
   a lock around all accum reads and writes.

**Prevention:**
- All pyhackrf2 method calls must happen under `_device_lock`, from the main thread
  or the executor thread, never from the USB callback.
- The RX callback must only call `_iq_queue.put_nowait()`. No device access, no
  logging that requires mutex acquisition, no numpy operations that allocate.
- Follow the "dedicated thread per sink" pattern from `sdr-iq-pipeline-threading.md`:
  every new consumer of IQ data gets its own bounded queue and drain thread.

**Status:** Current implementation follows these rules. Risk increases when IQ recorder
and CFAR nodes are added — the temptation to process inline will be strong.

---

### Pitfall 6: PSD Normalization — Dividing by N Instead of Fs * S2

**What goes wrong:** The PSD formula `|X[k]|^2 / N` produces values that are
scale-dependent (change with FFT size) and in the wrong units. The result is not in
V^2/Hz and cannot be compared across different FFT sizes or sample rates.

**Why it happens:** The DFT sum `X[k] = sum(x[n])` accumulates N samples.
Dividing by N gives the squared amplitude of the windowed signal normalized to the
window length. This is an amplitude spectrum, not a power spectral density. The
correct PSD formula accounts for the sample rate (converts per-sample energy to
power per Hz) and the window's integrated power (S2 = sum(window^2)).

**Consequences:** Absolute power readings are wrong by `Fs / N` (approximately 4883
at 20 MSPS with FFT_SIZE=4096). CFAR thresholds calibrated from wrong absolute values
will produce false alarm rates wildly different from the target Pfa.

**Prevention:** Use `|X[k]|^2 / (Fs * S2)`. Already implemented correctly in
`_fft_frame()`. Any new FFT path must use the same formula.

**Status:** Fixed. Critical to preserve through any FFT refactor.

---

## Moderate Pitfalls

Mistakes that degrade performance, increase false alarms, or waste development time.

---

### Pitfall 7: Python FFT Throughput on ARM64 — When It Breaks and When to Move to C++

**What goes wrong:** `numpy.fft.fft()` on ARM64 (Cortex-A57 Jetson Nano) uses PocketFFT
(since NumPy 1.17), which does not use ARM NEON SIMD. Per the engineering review, the
current implementation processes approximately 320 FFTs/sec, which covers 4% of captured
data at 20 MSPS. This is adequate for 10 Hz PSD monitoring.

**When it becomes a problem:**
- CFAR node running at 10 Hz: reads 4096-float PSD, applies sliding window of 32-64
  cells. This is trivial and will not bottleneck at 10 Hz.
- IQ recorder at 20 MSPS: `bytes(data)` in the RX callback copies the entire buffer.
  At 20 MSPS * 2 bytes = 40 MB/s, this is 40 MB/s of extra copies. On Jetson Nano
  with SD card, this will cause queue overflow. Use `memoryview` instead.
- Overlapping FFT frames: each chunk at 20 MSPS contains ~262144 samples. With 50%
  overlap and FFT_SIZE=4096, that is 63 FFT calls per chunk. At 1 chunk per 13 ms,
  that is ~4800 FFTs/sec — 15x the measured throughput. The timer callback will fall
  behind. The queue will overflow silently (current implementation uses drop-oldest
  semantics, so data is lost, not accumulated).
- Second CFAR detection node consuming PSD at 10 Hz: no issue.
- Cyclostationary feature extraction: computationally heavy. Will require pyfftw or C++.

**Prevention / Migration path:**
1. Replace `np.fft.fft` with `scipy.fft.fft` (uses pocketfft with threading hints) or
   `pyfftw.interfaces.numpy_fft.fft` (FFTW3 with ARM NEON via libfftw3-neon). Expect
   3-5x speedup for the single-thread case on ARM64.
2. Set `OPENBLAS_NUM_THREADS=1` in the Docker environment to prevent OpenBLAS thread
   pool contention on multi-core ARM before any numpy import.
3. Pre-allocate output buffers. `astype(np.float32)` inside `_process_raw()` allocates
   a new array per call. Use `np.empty()` + `np.copyto()` or pre-allocate and reuse.
4. C++ composable node: if cyclostationary features or multi-band simultaneous detection
   are needed, a C++ component with cuFFT (Jetson GPU) gives 10-50x for batched FFTs.
   The architectural boundary is well-defined: the C++ component subscribes to
   `/hackrf/spectrum` (SpectrumStamped) and publishes `/hackrf/features`. No hardware
   access from C++.

**Current state:** Adequate for Phases 1-2. Will hit the wall during Phase 3
(cyclostationary features). Flag Phase 3 for deeper research.

---

### Pitfall 8: DDS Transport Limits for Large Messages — The Float32MultiArray Problem

**What goes wrong:** DDS over UDP has a maximum unfragmented message size of 64 KB
(one Ethernet MTU-limited RTPS submessage). A Float32MultiArray with 4096 float32 bins
is 4096 * 4 = 16,384 bytes — comfortably under the limit. However, once the sweep
service returns a wideband composite (e.g., 1 MHz to 6 GHz at 4.9 kHz resolution),
the array has ~1 million bins * 4 bytes = 4 MB. This will be fragmented across ~62 UDP
packets, each of which must arrive in order for the message to be delivered.

**Measured behavior in this project:** The existing Float32MultiArray-based PSD topic
(16 KB) works reliably. The sweep service response (potentially MB-scale) has not been
stress-tested. Over WiFi, fragmented large messages show 1-3% loss rates even at low
message rates. On the Jetson wired Ethernet, this is less of an issue but not zero.

**Prevention:**
- Keep the `/hackrf/spectrum` continuous topic at 10 Hz with 4096 bins (16 KB). This
  is well within limits.
- For the sweep action server (Phase 2), send the result in chunks using the action
  feedback mechanism rather than a single large result message.
- Switch to `SpectrumStamped` custom message with `float32[]` (not nested objects),
  which has lower serialization overhead than Float32MultiArray with layout metadata.
- If sweep results must be large, write to a file and publish the path as a string
  topic (sidecar pattern), or use shared memory (rclcpp_components IPC — only within
  one process).

**DDS tuning for CycloneDDS if larger messages are needed:**
```xml
<CycloneDDS><Domain><General>
  <MaxMessageSize>65536B</MaxMessageSize>
  <FragmentSize>4092B</FragmentSize>
</General></Domain></CycloneDDS>
```
Set `CYCLONEDDS_URI` to point to this config file in docker-compose.

---

### Pitfall 9: Queue Overflow and Silent Data Loss at High Sample Rates

**What goes wrong:** At 20 MSPS, the RX callback fires approximately every 1-13 ms
(depending on libusb transfer size configured in pyhackrf2, typically 8-16 KB chunks).
The current queue has `maxsize=64`. At 16 KB per chunk, that is ~1 MB buffer, covering
approximately 65 ms of backlog. Any processing stall longer than 65 ms causes the
drop-oldest path in `_rx_callback()` to discard frames silently. `_rx_overflow_count`
increments, but no alarm is raised and downstream PSD quality degrades without any
explicit error.

**Scenarios that cause >65 ms stalls:**
- Sweep service holding `_sweep_lock` while doing hop-by-hop collection. Current code
  has `_process_and_publish()` check `_sweep_lock.acquire(blocking=False)` and return
  early. IQ is queued but not drained during sweeps. After a 6-hop sweep (6 * 25 ms
  settle + 6 * averaging frames), the queue overflows for long sweeps.
- `time.sleep()` inside `_param_callback` (the staged restart: 150 ms + 300 ms + 500 ms).
  The ROS2 executor is blocked during this callback. IQ arrives but timer callbacks
  cannot fire to drain it. Queue overflows after 65 ms.
- SD card fsync stall in IQ recorder (future feature): 50-200 ms on Jetson Nano SD,
  guaranteed overflow at 20 MSPS.

**Prevention:**
- Move `_param_callback` heavy work (stop_rx/start_rx with sleeps) to a background
  thread. Return `SetParametersResult(successful=True)` immediately, apply the change
  asynchronously. Current code runs sleep in the executor — this blocks all callbacks.
- The IQ recorder must run in a dedicated daemon thread with its own bounded queue,
  not inline in the drain loop. See `sdr-iq-pipeline-threading.md`.
- Add a diagnostic threshold: if `_rx_overflow_count` increases by more than 10 per
  second, publish a WARN diagnostic. Currently the count is accumulated but only
  reported on request.
- For the sweep service, use a larger settling-only queue (or separate sweep queue)
  so that normal PSD publishing is isolated from sweep data collection.

---

### Pitfall 10: CFAR Parameter Sensitivity — Guard Cells, Reference Cells, and Pfa Calibration

**What goes wrong:** CA-CFAR on a PSD array behaves very differently from radar CFAR
because the "noise" background is not white Gaussian — it is a shaped spectrum with
slowly-varying noise floor plus narrowband interferers. Choosing CA-CFAR parameters
without calibrating against the actual deployment environment produces either:
- Too few guard cells: signal energy leaks into the reference window, the noise estimate
  is inflated, the threshold rises, and real signals are masked (missed detections).
- Too many reference cells: the window spans frequency regions with different noise floors
  (e.g., the 2.4 GHz ISM band edge next to a quieter region), the noise estimate is
  the average of two different floors, and the threshold is wrong for both.
- Pfa set too optimistically (e.g., 1e-6): at 4096 bins and 10 Hz, the expected false
  alarms per second is 4096 * 10 * 1e-6 = 0.04/sec — seemingly fine. But this assumes
  white Gaussian noise. Colored noise (shaped by the MAX2837 analog filter, DC spike,
  I/Q images) inflates the effective Pfa by 3-10x. Real false alarm rate will be
  0.1-0.4/sec before the persistence tracker.

**Non-obvious issues from O'Donoughue CFAR theory:**
- CA-CFAR divides by `K*(M-1)`, not `K*M`, to produce an unbiased noise estimate.
  Using `M` instead inflates Pfa at small reference window sizes. This shows up as
  the apparent Pfa being consistently lower than the set value (more detections than
  expected) when using small M.
- Adjacent strong signals cause the reference window to include signal power, inflating
  the noise estimate and masking the cell under test (the "target masking" problem).
  In dense RF environments (2.4 GHz ISM), every WiFi channel can mask its neighbor.
- chi-squared DoF for complex IQ is 2M (two real Gaussians per complex sample), not M.
  Threshold calculations using the wrong DoF will be off by approximately sqrt(2).

**Prevention / Recommended starting parameters:**
- Guard cells: 4 per side (prevents signal energy from 20 MHz WiFi signal spanning
  ~4 bins at 4.9 kHz resolution from leaking into reference window).
- Reference cells: 32 per side (covers ~160 kHz, enough to estimate noise floor but
  not so wide as to span ISM band structure).
- Pfa: 1e-4 (not 1e-6) for initial deployment. The persistence tracker (multi-frame
  confirmation) is the false-alarm-reduction mechanism, not a tight Pfa.
- Validate against recorded IQ from the actual deployment environment before setting
  production parameters. Parameters tuned on a lab bench will perform poorly in a
  warehouse with active WiFi.

**Detection of misconfiguration:** Track `detections_per_second` as a diagnostic metric.
In a quiet environment, it should be near zero. In a busy WiFi environment, it should
be 3-10/sec before persistence filtering. If it is >50/sec, guard cells are too small
or Pfa is too high.

---

### Pitfall 11: Blocking sleep() in Parameter Callback Risks DDS Lease Expiry

**What goes wrong:** `_param_callback()` in the current implementation calls
`time.sleep(0.15)`, `time.sleep(0.3)`, and `time.sleep(0.5)` for the staged RX restart
retry loop. This callback runs in the ROS2 executor thread. During these sleeps (up to
950 ms total in the failure path), the executor cannot:
- Fire the 50 ms timer callback (PSD publishing stops).
- Respond to service calls.
- Send DDS liveliness heartbeats.

If the DDS lease duration (default: 20 seconds for CycloneDDS) is shorter than the
callback duration, the publisher is declared dead and subscribers unsubscribe. For
the current 950 ms maximum, this is not an issue with the 20 s default lease. However,
if a user reduces the lease duration for faster failure detection (e.g., 2 s for
watchdog purposes), a single parameter set with frequency change will trigger a spurious
disconnect.

**Secondary consequence:** Any subscriber that sets its history depth to KEEP_LAST(1)
misses all PSD frames published during the parameter callback — typically 19 frames at
50 ms intervals.

**Prevention:**
- Move stop_rx/start_rx and all associated sleeps to a background thread. Return
  `SetParametersResult(successful=True)` immediately from the callback, apply the
  hardware change asynchronously. The ROS2 parameter API does not provide a way to
  return a pending result, so the accepted pattern is: validate in the callback,
  enqueue the hardware action, return success.
- Alternatively, accept that retuning causes a ~1 second PSD gap and document it
  rather than fixing it immediately. The sweep service already causes PSD gaps.

---

### Pitfall 12: IQ Recording Throughput — SD Card vs NVMe on Jetson

**What goes wrong:** At 20 MSPS * 2 bytes, IQ recording writes 40 MB/s to disk.

- Jetson Orin NVMe: ~3 GB/s sequential write. Trivial.
- Jetson Nano/Xavier SD card: ~80 MB/s sequential, but with filesystem overhead and
  fsync, sustained throughput drops to 20-40 MB/s. At 40 MB/s input, the system is
  at or over capacity. A single fsync stall (50-200 ms) causes the IQ queue to overflow.
- Docker volume mount on SD card: adds a small overhead but the bigger issue is that
  Docker overlayfs on SD card has terrible random write performance. Use a bind mount
  to a tmpfs or directly to the SD card path, not the overlayfs layer.

**zstd compression:** Level 1 compression on int8 IQ data gives approximately 1.3-1.5:1
ratio (IQ noise is near-random, does not compress well). This reduces write rate to
27-31 MB/s, which is achievable on SD card but leaves very little margin.

**Prevention:**
- Detect the storage medium at startup and report expected throughput in diagnostics.
- Use a ring buffer approach: keep last N seconds in memory, write to disk only when a
  detection event triggers recording. At N=5 seconds, that is 200 MB in memory — feasible
  on Jetson Orin (8-16 GB), not on Jetson Nano (4 GB with OS overhead).
- The IQ recorder must have its own daemon thread and bounded queue (see Pitfall 9).
  If the queue overflows, log the gap with timestamps so the SigMF metadata reflects
  the actual recorded interval, not the commanded interval.

---

## Minor Pitfalls

Mistakes that are annoying or cause confusion but don't corrupt data.

---

### Pitfall 13: DC Offset Spike at Every Hop Center Frequency

**What goes wrong:** HackRF produces a DC offset in both I and Q channels due to LO
leakage through the MAX2837 mixer. Without correction, this appears as a spike at bin 0
(DC, the center frequency of each hop) that is typically 10-20 dB above the noise floor.
In a wideband sweep, this creates an artifact at every hop center frequency, spaced by
the usable bandwidth (~16 MHz at 20 MSPS with 80% usable BW).

**Prevention:** Subtract the per-frame mean before windowing: `iq -= np.mean(iq)`. Already
implemented in `_fft_frame()`. The flush/retune/wait/flush sweep sequence also helps by
discarding transition frames that have elevated DC due to PLL settling.

**Status:** Fixed. Regression check: compute FFT without DC removal and verify the DC
spike reappears.

---

### Pitfall 14: I/Q Imbalance Creates Ghost Images 25-35 dB Below Real Signals

**What goes wrong:** HackRF's direct-conversion receiver has gain and phase mismatch
between I and Q channels. Without correction, every real signal at +f_offset appears as
a ghost image at -f_offset. For a signal at -50 dBm relative to full scale, the ghost is
at -75 to -85 dBm — above the noise floor for a signal this strong. Ghost images mimic
real signals and will cause CFAR false detections that the persistence tracker cannot
distinguish from legitimate emitters.

**Prevention:** First-order correction is implemented in `_fft_frame()`. It estimates
gain ratio `alpha` and phase error `phi` from the current frame's statistics. This
improves image rejection from 25-35 dB to 40-50 dB.

**Limitation:** The correction is computed per frame, which wastes CPU. For stable
hardware parameters, the correction coefficients change slowly. Cache the coefficients
and recompute every 100 frames. More critically: per-frame estimation is noisy when
the signal is strong (a strong CW tone dominates the I*Q correlation). Consider a
moving average of the coefficients across frames.

---

### Pitfall 15: Sweep Stitching — Interpolation in dB Instead of Linear Produces Nonlinear Blending Errors

**What goes wrong:** When blending overlapping frequency bins from adjacent hops using
dB-valued PSD, the Tukey-weighted blend computes the geometric mean of power rather than
the arithmetic mean. At hop boundaries, this introduces a -1 to -3 dB dip in the
stitched composite.

**Prevention:** Convert to linear power before blending, blend in linear, convert to dB
once at the end. Already implemented in `_handle_sweep()` with `psd_lin = 10.0 ** (psd_db / 10.0)`.

**Status:** Fixed. Regression check: generate a flat spectrum from a signal generator
and verify the stitched output has <0.5 dB ripple at hop boundaries.

---

### Pitfall 16: OPENBLAS_NUM_THREADS Contention on Multi-Core ARM

**What goes wrong:** When `numpy` is imported on a multi-core ARM system, OpenBLAS
(the default BLAS backend) spawns a thread pool sized to the number of available cores.
On Jetson Xavier (8 cores), this creates 8 OpenBLAS threads. For the small matrix
operations in `_fft_frame()` (element-wise multiply, mean, FFT), OpenBLAS thread
creation overhead exceeds computation time. The pyhackrf2 RX callback fires every ~1 ms.
If an OpenBLAS worker thread is scheduled on the same core as the USB callback thread
when the callback fires, the callback is delayed, causing a USB transfer to be missed.

**Prevention:** Set `OPENBLAS_NUM_THREADS=1` in the Docker environment (or in the
launch script) before any Python process starts. This must be set before `import numpy`,
not after.

```yaml
# docker-compose.yaml
environment:
  - OPENBLAS_NUM_THREADS=1
  - RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
```

**Status:** Not currently set in the deployment. Add to docker-compose.yaml.

---

### Pitfall 17: Docker USB Passthrough — Device Nodes Change on Reconnect

**What goes wrong:** HackRF appears as `/dev/bus/usb/<bus>/<device>`. The `<device>`
number increments on every USB connect/disconnect cycle. If the docker-compose volume
mount specifies a fixed device path (e.g., `/dev/bus/usb/001/003`), it will work until
the first USB reconnect, after which the device is at `/dev/bus/usb/001/004` and the
container cannot see it.

**Prevention:** Use a device cgroup rule instead of a fixed path:
```yaml
devices:
  - /dev/bus/usb:/dev/bus/usb
device_cgroup_rules:
  - 'c 189:* rmw'   # USB devices (major 189)
```
This allows the container to access any USB device without specifying the exact minor
number. The `189` major corresponds to all USB device nodes.

**Note:** The Mayhem PortaPack serial port (`/dev/ttyACM1`) is a different device class
(major 166, ACM). If the serial interface is ever added back, add `c 166:* rmw` as well.

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| Stamped messages + TF frame | Lifecycle publisher regression (Pitfall 1) | End-to-end subscriber check in CI |
| CFAR detector node | dB-domain averaging (Pitfall 4), CFAR Pfa miscalibration (Pitfall 10) | Unit test with known-SNR synthetic PSD |
| QoS cleanup (BEST_EFFORT) | QoS mismatch between publisher and subscriber causes silent no-data | ros2 topic info shows policy; mismatched policy = zero messages |
| IQ recorder (SigMF) | Queue overflow on SD card (Pitfall 9, 12), inline I/O blocking drain loop (Pitfall 5) | Dedicated recorder thread; measure throughput on target hardware first |
| Sweep action server | Large result message fragmentation (Pitfall 8), IQ queue overflow during sweep (Pitfall 9) | Chunked feedback; flush-and-drain IQ during sweep |
| AGC (automatic gain control) | stop_rx/start_rx segfault (Pitfall 3), blocking param callback (Pitfall 11) | Retune without stop/start; async hardware apply |
| Cyclostationary features | Python FFT throughput wall (Pitfall 7) | Needs pyfftw or C++ component; plan the research spike before the phase |
| Multi-radio architecture | OPENBLAS contention (Pitfall 16), DDS message size (Pitfall 8) | Separate node per radio; shared memory IPC if same process |

---

## Sources

- PROJECT.md: confirmed CycloneDDS vs FastRTPS decision, lifecycle publisher bug, retune-without-stop
- docs/rf-ew-review.md: dB averaging bias, DC spike, I/Q imbalance, normalization formula
- docs/rf-sensor-roadmap.md: throughput numbers (320 FFTs/sec, 4%), SD card vs NVMe, blocking param callback
- hackrf_lifecycle_node.py: drop-oldest queue semantics, restart settle delays, sweep lock behavior
- vault/concepts/sdr-iq-pipeline-threading.md: dedicated thread pattern, watchdog deadlock, bounded queue rationale
- vault/concepts/detection-theory-np-and-cfar.md: CA-CFAR Bessel correction, DoF count, chi-squared tails
- vault/projects/hackrf-ros/hackrf-ros.md: USB disconnect patterns, libhackrf concurrent stop/start fixes
- github.com/greatscottgadgets/hackrf issue #1042: USB streaming stall after RX restart
- docs.ros.org/en/humble/How-To-Guides/DDS-tuning.html: CycloneDDS MTU, fragmentation limits
- github.com/ros2/rmw_cyclonedds issue #346: large array serialization overhead
