# Architecture Patterns: ROS2 RF Sensor Package

**Project:** hackrf_ros — HackRF One as a ROS2 RF sensor
**Researched:** 2026-04-12
**Confidence:** HIGH (grounded in existing codebase, RF/EW review, and verified ROS2 patterns)

---

## Current State

The existing `HackRFLifecycleNode` is a single node that couples:

- Hardware driver (pyhackrf2 lifecycle, IQ queue, device lock)
- Signal processing (FFT, Blackman window, PSD averaging)
- Sweep logic (multi-hop frequency planning, stitching, blending)
- Diagnostics

It publishes `Float32MultiArray` on `/hackrf/psd` and `/hackrf/tuning` with no timestamps, no
frame_id, and no semantic interpretation of the spectrum data. The sweep operation is a blocking
service (with a reentrant callback group as a partial workaround), but it still starves the
`_process_and_publish` timer during its execution because both contend on `_sweep_lock`.

The architecture gap is not the signal processing — that is correct. The gap is the robotics
integration layer: no timestamps mean no temporal correlation, no frame_id means no spatial
reference, and no detection layer means autonomy nodes cannot consume the data without writing
their own spectrum interpretation code.

---

## Recommended Node Separation

### Layer 1 — Hardware Driver Node (LifecycleNode)

**Name:** `hackrf_driver_node`
**Type:** LifecycleNode (UNCONFIGURED → INACTIVE → ACTIVE lifecycle)
**Responsibility:** Own the pyhackrf2 device, manage the IQ queue, publish raw IQ chunks to an
intra-process topic. Nothing else. No FFT, no sweep logic.

**Rationale for Lifecycle:** Hardware driver maps naturally to lifecycle states. INACTIVE = device
open but not streaming (safe default on startup). ACTIVE = streaming. Lifecycle lets a supervisor
(nav2_lifecycle_manager or a launch file) sequence startup and handle hardware failures by cycling
the driver through deactivate → cleanup → configure → activate without restarting the process.

**Note on CycloneDDS:** The existing codebase discovered that lifecycle publishers do not transmit
over CycloneDDS DDS inter-process transport on this ARM64 Jetson target. The workaround is to use
regular publishers created in `on_configure` (not lifecycle-managed publishers), which the current
`hackrf_lifecycle_node.py` already does correctly. This pattern must be preserved.

**Publishes:**
- `/hackrf/iq_raw` — raw IQ chunks as `sensor_msgs/msg/ByteMultiArray` (or a custom
  `IQChunk.msg`), BEST_EFFORT QoS, depth=10. This topic is primarily for intra-process consumers.
  Rate: 1 kHz (USB callback rate).

**Services (ACTIVE only):**
- `/hackrf/set_tuning` — synchronous parameter change (center_freq, sample_rate, gains). Replaces
  the ROS2 parameter callback for tuning so the callback isn't blocked on `time.sleep()` during
  stream restart. Returns immediately; actual retune happens in the IQ drain thread.

**Parameters:** center_frequency, sample_rate, lna_gain, vga_gain, amp_enabled (as today)

**Threading model (unchanged from today):**
- USB callback thread: `put_nowait()` to bounded `_iq_queue(maxsize=64)`, drop-oldest on full
- Timer callback thread: drain queue, accumulate PSD (or forward raw chunks)
- Device lock: RLock for all device method calls
- No new logic added to either existing thread — any new sink (recorder, detector) gets its own
  bounded queue per the `sdr-iq-pipeline-threading` rule

---

### Layer 2 — Spectral Estimation Node (Regular Node)

**Name:** `psd_node`
**Type:** Regular rclpy.Node (not lifecycle)
**Responsibility:** Subscribe to `/hackrf/iq_raw`, compute Blackman-windowed PSD with linear
averaging, publish `SpectrumStamped` on `/hackrf/spectrum`. Stateless between frames.

**Rationale for Regular Node:** The PSD computation has no hardware dependency and no meaningful
lifecycle states. If the driver goes INACTIVE, this node simply stops receiving messages and
publishes nothing. Lifecycle machinery adds complexity without benefit here.

**Subscribes:** `/hackrf/iq_raw` — BEST_EFFORT QoS
**Publishes:**
- `/hackrf/spectrum` — `SpectrumStamped` (custom message), BEST_EFFORT QoS, depth=5, ~10 Hz

**Key change from today:** The `SpectrumStamped` message includes `header.stamp` (time of the
last IQ chunk contributing to this PSD frame, from `self.get_clock().now()`) and `header.frame_id`
(configurable, default `hackrf_antenna`). This is the single change that makes all downstream
nodes time- and space-aware.

**Performance note:** The FFT processing (~320 FFTs/sec for current 4096-pt Blackman/Welch) stays
in Python with `np.fft.fft`. If throughput becomes a bottleneck on ARM64, replace with
`scipy.fft` (uses pocketfft, SIMD-aware) or `pyfftw`. Do not optimize prematurely — 4% of
captured data processed is adequate for 10 Hz PSD publishing.

**Alternative separation considered:** Keeping PSD inside the driver node. Rejected because it
couples signal processing algorithm choices to the hardware driver, makes the driver harder to
test, and prevents swapping the FFT implementation without touching driver logic.

---

### Layer 3 — Detection Node (Regular Node)

**Name:** `cfar_node`
**Type:** Regular rclpy.Node
**Responsibility:** Subscribe to `/hackrf/spectrum` (SpectrumStamped), run CA-CFAR energy
detection, group adjacent detected bins into signal detections, publish `RFDetectionArray`.

**Subscribes:** `/hackrf/spectrum` — BEST_EFFORT QoS
**Publishes:**
- `/hackrf/detections` — `RFDetectionArray`, BEST_EFFORT QoS, depth=5, ~10 Hz
- `/hackrf/rf_environment` — `RFEnvironment` summary (emitter count, occupancy %), RELIABLE, 1 Hz

**CFAR implementation approach:**
- CA-CFAR (Cell-Averaging): sliding window of guard cells + reference cells per bin
- Threshold = alpha * mean(reference cells), alpha tuned for target Pfa (~1e-4)
- Adjacent threshold-exceeding bins grouped into detections (contiguous-bin merge)
- Each detection: center_frequency_hz (power-weighted centroid), bandwidth_hz (bin span *
  bin_width), power_dbm (peak), snr_db (peak - local noise estimate)
- ~200 lines of Python, runs trivially inside a 100 ms timer callback at 10 Hz

**Persistence tracker:** The detector accumulates a frame counter per detection ID (assigned by
frequency proximity matching). A detection becomes a confirmed signal after N_confirm frames
(default 3). Detections that disappear for N_decay frames (default 5) are removed. This eliminates
transient false alarms from spectral noise spikes without requiring any ML.

**Classification:** A static lookup table maps (center_frequency, bandwidth) pairs to known
allocations. Heuristics: <200 kHz = narrowband/analog, 1-5 MHz = ISM/BLE/ZigBee, 20 MHz = WiFi
2.4G/5G, 40+ MHz = LTE/NR. This is built as a Python dict keyed by frequency range — no ML, no
external dependencies. Band classification is added to `RFDetection.classification` string.

---

### Layer 4 — Sweep Action Server (Regular Node or within driver node)

**Name:** `sweep_node` (or a separate action server node)
**Type:** Regular rclpy.Node with an action server
**Responsibility:** Implement `/hackrf/survey_band` action with goal (freq_min, freq_max,
averaging), feedback (current hop index, current hop center, progress_pct), result (full
aggregated PSD as SpectrumStamped plus summary stats). Supports cancel at any hop boundary.

**Why Action, not Service:** The existing sweep service has no cancel path, no progress feedback,
and blocks PSD publishing during execution (the `_sweep_lock` mechanism). An action server runs
the sweep in a dedicated thread (per ROS2 action server design), publishes feedback at each hop,
and checks `goal_handle.is_cancel_requested` at each hop boundary before committing to the next
tune. This is the correct ROS2 pattern for long-running, preemptible operations.

**Separation from driver:** The sweep action server communicates with the driver through the
existing `/hackrf/set_tuning` service (or parameter set) to retune between hops, and consumes
`/hackrf/spectrum` messages from the PSD node for each hop's data. This decouples the sweep
algorithm from the hardware driver entirely. The driver stays responsible for device state; the
sweep action server is responsible for hop planning and result aggregation.

**Goal.action:**
```
# SurveyBand.action — Goal
float64 freq_min_hz
float64 freq_max_hz
int32 averaging          # PSD frames per hop, default 4
---
# Result
hackrf_interfaces/msg/SpectrumStamped spectrum   # aggregated full band PSD
float32 peak_power_dbm
float64 peak_frequency_hz
int32 hops_completed
---
# Feedback
int32 current_hop
int32 total_hops
float64 current_center_hz
float32 progress_pct
```

---

### Layer 5 — IQ Recording Action Server (Regular Node)

**Name:** `recorder_node`
**Type:** Regular rclpy.Node with an action server
**Responsibility:** Implement `/hackrf/record_iq` action. Subscribes to `/hackrf/iq_raw`,
writes SigMF files (`.sigmf-data` + `.sigmf-meta`), queries `/tf` for robot pose at recording
start. Supports cancel (flushes buffers, closes file cleanly).

**Threading:** The file write path runs in a dedicated daemon thread with a bounded queue (the
`sdr-iq-pipeline-threading` pattern — same rule as in the original driver). The action executor
thread only enqueues chunks; the writer thread blocks on disk I/O. This isolates SD card or NVMe
stalls from affecting the IQ capture queue.

**SigMF metadata fields:** center_frequency, sample_rate, hardware (`hackrf_one`), datatype
(`ci8` — interleaved int8 complex), robot pose (from `/tf` snapshot at start), timestamp (ISO
8601 from `header.stamp`), trigger reason (manual / detection-triggered).

**Record.action:**
```
# RecordIQ.action — Goal
string output_path
float64 duration_sec      # 0 = record until cancel
string trigger_reason
---
# Result
string output_path
uint64 bytes_written
uint64 samples_written
bool sigmf_valid
---
# Feedback
float64 elapsed_sec
uint64 samples_written
float32 queue_fullness_pct  # overflow warning signal
```

---

### Layer 6 — Optional Display Nodes (Regular Nodes, not core)

**Name:** `spectrum_display_node`, `waterfall_node`
**Type:** Regular rclpy.Node
**Responsibility:** Subscribe to `/hackrf/spectrum` or `/hackrf/detections`, render live plots
via matplotlib/TkAgg. No hardware access, no data transformation responsibility. These are
visualization consumers — they may be dropped or replaced without affecting the sensor pipeline.

The existing `spectrum_node.py` already follows this pattern correctly and requires only a
subscription update from `Float32MultiArray` to `SpectrumStamped`.

---

## Full Node Graph

```
[HackRF Hardware]
        |
        | pyhackrf2 USB callback
        v
+-------------------+    /hackrf/iq_raw         +-------------+    /hackrf/spectrum
| hackrf_driver_node |  --------------------->  |  psd_node   |  ----------------->
|  (LifecycleNode)  |   ByteMultiArray           |  (Regular)  |  SpectrumStamped
|                   |   BEST_EFFORT              |             |  BEST_EFFORT, 10 Hz
|  owns: device,    |                            | FFT, avg,   |
|  IQ queue, lock   |                            | corrections |
|  diagnostics      |                            +-------------+
+-------------------+                                  |
        |                                              |
        | /diagnostics (DiagnosticArray, 1 Hz)        | /hackrf/spectrum
        v                                              v
  /diagnostics bus                         +------------------+   /hackrf/detections
                                           |   cfar_node      |  ----------------->
                                           |   (Regular)      |  RFDetectionArray
                                           |                  |  BEST_EFFORT, 10 Hz
                                           | CA-CFAR,         |
                                           | persistence,     |   /hackrf/rf_environment
                                           | classification   |  ----------------->
                                           +------------------+  RFEnvironment, 1 Hz
                                                  |
                               /hackrf/detections |
                                                  |
                    +-----------------------------+-----------------------------+
                    |                             |                            |
             +-------------+             +------------------+         +--------------+
             | sweep_node  |             | recorder_node    |         | display_node |
             | (action srv)|             | (action server)  |         | (subscriber) |
             | SurveyBand  |             | RecordIQ         |         | spectrum vis |
             +-------------+             +------------------+         +--------------+
```

---

## Message Flow: IQ to Classification

```
IQ bytes (int8, 40 MB/s)
  → IQ queue (64-chunk bounded, drop-oldest)
  → /hackrf/iq_raw (ByteMultiArray, BEST_EFFORT)
  → PSD computation (Blackman FFT, linear avg, 16 frames)
  → /hackrf/spectrum (SpectrumStamped, float32[4096] in dB, 10 Hz)
  → CA-CFAR detection (sliding window threshold, bin grouping)
  → persistence filter (N_confirm=3 frames, N_decay=5 frames)
  → band classification (freq+BW lookup table)
  → /hackrf/detections (RFDetectionArray, 10 Hz)
  → /hackrf/rf_environment (RFEnvironment, 1 Hz aggregation)
```

Latency budget: IQ → published detection ≈ 150–300 ms (dominated by PSD averaging at 10 Hz
frame rate, 16 frames * 50 ms = 800 ms worst case; in practice the rolling average cuts this to
~100 ms since frames overlap).

---

## Custom Message Design

### `SpectrumStamped.msg`

```
std_msgs/Header header         # stamp = time of last IQ chunk; frame_id = "hackrf_antenna"
float64 center_frequency_hz
float64 sample_rate_hz
float64 bin_width_hz           # sample_rate_hz / fft_size
uint32 fft_size
float32[] psd_db               # length = fft_size, FFT-shifted (DC at center)
float32 noise_floor_db         # median of psd_db, updated per frame
```

**Design rationale:** `psd_db` as `float32[]` not `Float64MultiArray` — float32 is sufficient
for dB-domain values (0.001 dB precision far exceeds the ~1 dB measurement uncertainty of this
hardware). Header stamp enables time correlation with robot pose in TF. `noise_floor_db` is
cheap to compute here (one `np.median` call) and avoids redundant computation in all downstream
nodes.

### `RFDetection.msg`

```
std_msgs/Header header         # stamp = time of detection confirmation; frame_id inherited from spectrum
float64 center_frequency_hz
float64 bandwidth_hz
float32 power_dbm              # peak power within detection bandwidth
float32 snr_db                 # peak above local noise estimate
string classification          # e.g. "wifi_2_4g", "ble", "zigbee", "lte", "unknown"
uint32 persistence_frames      # how many consecutive frames this detection has existed
uint32 detection_id            # monotonically increasing, stable across frames
```

**Design rationale:** `detection_id` stability across frames is the key property — it lets
downstream nodes track "signal 42 has been seen for 8 frames, power trending up" without
re-association logic. Assign IDs by frequency proximity: if a new detection is within 0.5 *
bandwidth_hz of an existing detection's center, it inherits the existing ID. New center is
power-weighted average.

### `RFDetectionArray.msg`

```
std_msgs/Header header
float64 noise_floor_dbm        # system noise floor estimate (from SpectrumStamped)
float32 occupancy_pct          # fraction of monitored band with detections above noise
RFDetection[] detections
```

**Design rationale:** Array-level header allows a subscriber to get the frame timestamp once
without iterating detections. `occupancy_pct` is a useful single-number summary for autonomy
(e.g., "RF environment is 67% occupied, reduce transmission power").

### `RFEnvironment.msg` (1 Hz summary)

```
std_msgs/Header header
uint32 emitter_count           # confirmed detections (persistence >= N_confirm)
float32 occupancy_pct
float32 noise_floor_dbm
float32 peak_power_dbm
float64 peak_frequency_hz
```

### Action Definitions

`SurveyBand.action` and `RecordIQ.action` — see Layer 4 and Layer 5 above.

### Service Definitions

The existing `Sweep.srv` becomes `SurveyBand.action`. The current `Sweep.srv` can remain for
backward compatibility but should not be the primary interface.

New service: `SetTuning.srv` — takes (center_frequency, sample_rate, lna_gain, vga_gain,
amp_enabled) with field-present booleans or optional fields, returns (success, message). Preferred
over the ROS2 parameter callback for tuning because it does not block the executor during
`time.sleep()` stream restart.

---

## Shared Memory / Zero-Copy Transport

**Context:** CycloneDDS supports iceoryx-based shared memory transport on Linux, which can
eliminate the copy between publisher and subscriber when both run in separate processes on the
same machine. This is relevant for `/hackrf/iq_raw` at 40 MB/s.

**Assessment (MEDIUM confidence):**

CycloneDDS + iceoryx zero-copy requires:
1. Both nodes in separate processes on Linux (satisfied — Docker on Jetson).
2. Message type must be fixed-size (problematic — `float32[]` is variable-length, `ByteMultiArray`
   with variable `data` field is also variable-length).
3. iceoryx requires a RouDi daemon running (`iox-roudi`).
4. Loaned messages API has a limit of 8 simultaneous loans per publisher.

**The variable-length constraint is the blocker.** IQ chunks from pyhackrf2 are fixed in practice
(libhackrf delivers 131072-byte blocks), but the ROS2 message wrapping them (`ByteMultiArray.data`)
is a variable-length array that prevents iceoryx zero-copy at the DDS layer.

**Recommended approach:** Do not use iceoryx for now. Use ROS2 intra-process communication
(IPC) instead.

**Intra-process communication:** When `psd_node` and `hackrf_driver_node` run in the same
process via a `ComposableNodeContainer`, ROS2's intra-process IPC passes a `shared_ptr` instead
of serializing the message. This is effectively zero-copy and requires no iceoryx configuration.
This is the correct approach for this pipeline:

```python
# launch/hackrf.launch.py — composable container for driver + psd
ComposableNodeContainer(
    name='hackrf_container',
    namespace='',
    package='rclcpp_components',
    executable='component_container',
    composable_node_descriptions=[
        ComposableNode(package='hackrf_ros', plugin='hackrf_ros::HackRFDriverNode', ...),
        ComposableNode(package='hackrf_ros', plugin='hackrf_ros::PSDNode', ...),
    ],
    parameters=[{'use_intra_process_comms': True}],
)
```

**Caveat:** ROS2 composable nodes require `rclcpp` (C++) for the component registration
mechanism. Python nodes (`rclpy`) cannot be loaded as composable nodes into a C++ container.
For this Python-only codebase, intra-process IPC is not available via the composable node
mechanism. The practical alternative is to keep the driver and PSD node as separate Python
processes and accept the DDS serialization cost on `/hackrf/iq_raw`. At 40 MB/s raw IQ, this is
real overhead, but PSD is the actual bottleneck (4096-pt FFT), not the transport.

**Final recommendation:** Keep driver and PSD node in the same Python process initially,
sharing data through a module-level queue (or sub-node pattern) rather than DDS. This is not
idiomatic ROS2 but matches what the current monolith already does. Separate into proper processes
only when debugging tools (ros2 topic echo on /hackrf/iq_raw) are needed. The separation at the
message interface level (defining `/hackrf/iq_raw` as a real topic) preserves the architectural
boundary even if the transport is initially in-process.

---

## Lifecycle Management: Which Nodes Are Lifecycle

| Node | Type | Rationale |
|------|------|-----------|
| `hackrf_driver_node` | LifecycleNode | Hardware device with meaningful INACTIVE state (open but not streaming). Supports ordered startup and recovery by external supervisor. |
| `psd_node` | Regular Node | No hardware dependency. Stateless between frames. Lifecycle adds no value. |
| `cfar_node` | Regular Node | Pure computation on incoming data. No lifecycle states needed. |
| `sweep_node` | Regular Node | Action server owns its own state machine via the action protocol. |
| `recorder_node` | Regular Node | Action server owns its own state machine via the action protocol. |
| `spectrum_display_node` | Regular Node | Optional viewer, no lifecycle semantics. |

**Lifecycle supervisor:** Use `nav2_lifecycle_manager` pattern or a simple launch-file
`OnStateTransition` event handler (as the existing `hackrf.launch.py` already implements) to
auto-configure and auto-activate the driver on startup. The driver is the only node that needs
managed lifecycle.

---

## TF Frame Conventions for RF Sensors

RF sensors do not produce geometric observations directly (unlike lidar or camera), but their
measurements are spatially referenced to the antenna position. Two conventions apply:

**REP-105 frames in use:**
- `base_link` — robot body origin (standard)
- `hackrf_antenna` — phase center of the HackRF antenna, expressed as a static transform from
  `base_link`. Default: identity (antenna at base_link origin). Configurable via parameter
  `antenna_frame_id`.

**Static transform:** Published by a `static_transform_publisher` in the launch file:
```python
# Antenna at base_link + 0.1m vertical offset, no rotation
Node(package='tf2_ros', executable='static_transform_publisher',
     arguments=['0', '0', '0.1', '0', '0', '0', 'base_link', 'hackrf_antenna'])
```

**frame_id in messages:** All stamped messages (`SpectrumStamped`, `RFDetectionArray`) set
`header.frame_id = 'hackrf_antenna'`. This allows a listener to call
`tf_buffer.lookup_transform('map', 'hackrf_antenna', msg.header.stamp)` to get the antenna pose
at the time of measurement — the prerequisite for emitter localization (Phase 3+).

**Comparison with IMU conventions (REP-145):** IMU sensors follow REP-145 which requires
`header.frame_id` to be the sensor frame and all angular quantities expressed in that frame.
RF sensors have no angular payload, so REP-145 does not apply. The pattern is analogous to how
GPS uses `gps_link` or `gnss_antenna_link` — the frame marks the measurement origin, not a
coordinate system for the data values.

**Coordinate system note:** RF measurements are scalar (power) or 1D (frequency/bandwidth).
The TF frame provides spatial context (where was the antenna when this was measured) not a
coordinate transform for the measurement values themselves. Emitter localization in Phase 3
uses the antenna position history + power measurements to triangulate — it reads from `/tf`,
not from a rotation matrix embedded in the message.

---

## Action Server vs Service Decision

| Operation | Interface | Rationale |
|-----------|-----------|-----------|
| Single-hop PSD query (fast, <1s) | Service (`GetSpectrum.srv`) | Atomic, immediate response |
| Wideband sweep (multi-hop, 2–60s) | Action (`SurveyBand.action`) | Needs cancel, per-hop feedback |
| IQ recording (duration-based) | Action (`RecordIQ.action`) | Needs cancel, progress bytes/sec |
| Gain control (atomic set) | Service (`SetTuning.srv`) or parameter set | Immediate |
| Mode switch | Service (`SetMode.srv`) | Immediate |

**Rule:** Use a service when the operation completes in under ~2 seconds and has a definite
synchronous response. Use an action when the operation has meaningful intermediate states,
benefits from cancel support, or may take unbounded time. The current sweep service violates
this rule (a 2.4–6 GHz sweep at 20 MHz steps takes 20+ seconds) and must become an action.

**Python action server thread model:** In rclpy, action server goal callbacks execute in the
executor thread by default. For long-running operations, spawn a `threading.Thread` inside
`execute_callback` and return from the callback immediately. The thread does the work, calls
`goal_handle.publish_feedback()` at checkpoints, and checks `goal_handle.is_cancel_requested`
before each hop. This is the standard rclpy pattern for preemptible actions.

---

## Build Order and Package Dependencies

### Package Structure

```
hackrf_interfaces/      (ament_cmake, rosidl_generate_interfaces)
  msg/
    SpectrumStamped.msg
    RFDetection.msg
    RFDetectionArray.msg
    RFEnvironment.msg
  srv/
    Sweep.srv           (existing — keep for compat)
    SetTuning.srv       (new)
    SetMode.srv         (new)
  action/
    SurveyBand.action   (new)
    RecordIQ.action     (new)

hackrf_ros/             (ament_python)
  hackrf_ros/
    hackrf_lifecycle_node.py    (driver — refactored to not do FFT)
    psd_node.py                 (new — FFT, SpectrumStamped)
    cfar_node.py                (new — CA-CFAR, RFDetectionArray)
    sweep_node.py               (existing sweep.py refactored as action server)
    recorder_node.py            (new — SigMF RecordIQ action)
    spectrum_node.py            (existing — update subscription type)
    sweep_display.py            (existing — unchanged)
  launch/
    hackrf.launch.py            (update to add static_transform_publisher)
    sensor_pipeline.launch.py   (new — driver + psd + cfar in one launch)
```

**Build order constraint:** `hackrf_interfaces` must be built before `hackrf_ros`. Colcon
resolves this from `package.xml` dependencies — `hackrf_ros/package.xml` must declare
`<depend>hackrf_interfaces</depend>`. The existing `hackrf_interfaces/CMakeLists.txt` uses
`rosidl_generate_interfaces()` with `ament_cmake` build type — this is the correct pattern
and needs no changes except adding new `.msg` and `.action` files.

**Adding `.action` files to hackrf_interfaces:**
```cmake
rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/SpectrumStamped.msg"
  "msg/RFDetection.msg"
  "msg/RFDetectionArray.msg"
  "msg/RFEnvironment.msg"
  "srv/Sweep.srv"
  "srv/SetTuning.srv"
  "action/SurveyBand.action"
  "action/RecordIQ.action"
  DEPENDENCIES std_msgs
)
```

**Dependencies chain:**
```
pyhackrf2 (pip)
  └── hackrf_lifecycle_node.py

hackrf_interfaces (ament_cmake, built first)
  └── hackrf_ros (ament_python)
        ├── hackrf_lifecycle_node.py (imports hackrf_interfaces.srv)
        ├── psd_node.py              (imports hackrf_interfaces.msg)
        ├── cfar_node.py             (imports hackrf_interfaces.msg)
        └── sweep_node.py            (imports hackrf_interfaces.action)
```

---

## QoS Profiles by Topic

| Topic | Reliability | History | Depth | Rationale |
|-------|-------------|---------|-------|-----------|
| `/hackrf/iq_raw` | BEST_EFFORT | KEEP_LAST | 10 | High rate, drop old rather than back-pressure |
| `/hackrf/spectrum` | BEST_EFFORT | KEEP_LAST | 5 | Continuous stream, latest frame is what matters |
| `/hackrf/detections` | BEST_EFFORT | KEEP_LAST | 5 | Real-time detections, staleness is worse than loss |
| `/hackrf/rf_environment` | RELIABLE | KEEP_LAST | 1 | 1 Hz summary, reliable delivery preferred |
| `/hackrf/tuning` | RELIABLE | KEEP_LAST | 5 | Configuration state, should not drop |
| `/diagnostics` | RELIABLE | KEEP_LAST | 5 | Diagnostic data, existing pattern |

**Key change from today:** `/hackrf/psd` (becomes `/hackrf/spectrum`) is currently `RELIABLE`,
which creates back-pressure. The RF/EW review identified this as a problem — a slow subscriber
can cause the PSD publisher's send queue to fill, blocking the executor. Change to `BEST_EFFORT`.

---

## Anti-Patterns to Avoid

### 1. New Processing Logic Inside the IQ Queue Drain Loop

The `_process_and_publish_inner` method in the current driver drains the IQ queue and runs FFT.
Adding recorder logic, detection logic, or any I/O here violates the threading rule. Each new
sink (recorder, external FFT consumer) gets its own bounded queue that the drain loop posts to
non-blocking. See `sdr-iq-pipeline-threading` concept.

### 2. Sweep Service Holding `_sweep_lock` Across Hops

The current `_handle_sweep` holds `_sweep_lock` for the entire sweep duration (tens of seconds
for wideband scans). During this time `_process_and_publish` skips every timer tick, leaving PSD
subscribers without data. The action server architecture eliminates this: the sweep action server
retunes via a service call to the driver and reads spectrum data from `/hackrf/spectrum` topic
subscription. The driver never holds a sweep lock.

### 3. `time.sleep()` in Parameter Callback

The current `_param_callback` calls `time.sleep(settle)` during stream restart. This blocks the
ROS2 executor thread, potentially causing DDS lease expiry warnings and stalling all other
callbacks. The retune logic should be dispatched to a background thread with `threading.Thread`
or via a command queue drained by a dedicated tuning thread.

### 4. Lifecycle Publishers for DDS Inter-Process

Known broken on CycloneDDS + ARM64 Jetson. Always create publishers with `self.create_publisher()`
in `on_configure()`, not `self.create_lifecycle_publisher()`. The current code already follows
this correctly.

### 5. `Float32MultiArray` for Semantic Data

`Float32MultiArray` with no header, no units, no field names is opaque to any generic ROS2 tool.
`ros2 topic echo /hackrf/psd` produces an unlabeled float array. Custom messages with named fields
and a Header are required for any serious integration. All new topics must use custom messages.

### 6. Tight Coupling of Sweep Algorithm to Driver

The sweep algorithm (hop planning, Tukey blending, stitching) is business logic, not hardware
control. It belongs in the sweep action server node, which calls the driver to retune and reads
spectrum data from the PSD node. This allows the sweep algorithm to be modified, tested in
isolation with mock spectrum data, or replaced with a different sweep strategy (e.g., contiguous
vs. sparse hopping) without touching the driver.

---

## Scalability Considerations

| Concern | Single HackRF (now) | Multi-Radio (Phase 4) |
|---------|--------------------|-----------------------|
| IQ throughput | 40 MB/s, single USB | Multiple processes, one per radio |
| PSD computation | One FFT thread, Python | One psd_node per radio |
| Detection | Single CFAR node | Merge RFDetectionArray across radios |
| TF frames | `hackrf_antenna` | `hackrf_antenna_0`, `hackrf_antenna_1` |
| Namespacing | `/hackrf/*` | `/hackrf_0/*`, `/hackrf_1/*` |

**Namespace convention for multi-radio:** Use node namespace parameter at launch time
(`--ros-args -r __ns:=/hackrf_0`) rather than hardcoding. All topic names stay relative.
This allows the same node code to serve multiple radios without modification.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Node separation rationale | HIGH | Grounded in existing codebase problems and RF/EW review |
| Message design | HIGH | Verified against RF/EW review requirements; field choices are engineering decisions, not guesses |
| Lifecycle which nodes | HIGH | Standard ROS2 patterns, verified against nav2 precedent |
| TF frame conventions | HIGH | REP-105, REP-145 verified; pattern consistent with IMU/GPS drivers |
| Action vs service decision | HIGH | Standard ROS2 design rule, sweep timing makes the case unambiguous |
| CycloneDDS zero-copy | MEDIUM | iceoryx integration verified as available but variable-length message constraint is a real blocker; intra-process IPC workaround is standard but Python composable node limitation is a constraint |
| Build order | HIGH | rosidl pattern verified, standard for any custom interface package |
| CFAR implementation scope | MEDIUM | 200-line estimate is a reasonable planning figure; actual CFAR tuning (guard cells, reference cells, alpha) requires empirical calibration against real HackRF noise floor |

---

## Sources

- Existing codebase: `/home/mvc/dev_ws/hackrf_ros/hackrf_ros/hackrf_lifecycle_node.py`
- RF/EW engineering review: `/home/mvc/dev_ws/hackrf_ros/docs/rf-sensor-roadmap.md`
- Project goals: `/home/mvc/dev_ws/hackrf_ros/.planning/PROJECT.md`
- Threading architecture: `/mnt/NAS/vault/concepts/sdr-iq-pipeline-threading.md`
- Original project architecture: `/mnt/NAS/vault/projects/hackrf-ros/hackrf-ros.md`
- ROS2 lifecycle design: https://design.ros2.org/articles/node_lifecycle.html
- ROS2 actions design: https://design.ros2.org/articles/actions.html
- ROS2 zero-copy loaned messages: https://design.ros2.org/articles/zero_copy.html
- CycloneDDS shared memory support: https://github.com/ros2/rmw_cyclonedds/blob/rolling/shared_memory_support.md
- ApexAI ros2_shm_demo: https://github.com/ApexAI/ros2_shm_demo
- ROS2 composable nodes Foxglove: https://foxglove.dev/blog/ros-2-composable-nodes
- REP-105 coordinate frames: https://www.ros.org/reps/rep-0105.html
- REP-145 IMU conventions: https://www.ros.org/reps/rep-0145.html
- ROS2 Humble custom messages: https://docs.ros.org/en/humble/Tutorials/Beginner-Client-Libraries/Custom-ROS2-Interfaces.html
- ros2_shm_msgs for high-throughput: https://github.com/ZhenshengLee/ros2_shm_msgs
