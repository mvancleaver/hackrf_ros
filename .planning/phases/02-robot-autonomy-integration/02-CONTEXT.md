# Phase 2: Robot Autonomy Integration - Context

**Gathered:** 2026-04-13
**Status:** Ready for planning
**Skip-research:** true (decisions derived from CLAUDE.md stack, Phase 1 context, existing code)

<domain>
## Phase Boundary

Wire Phase 1 sensor intelligence into robot autonomy primitives. Add action servers for
wideband sweep and IQ recording, plus an RF occupancy grid for nav stack integration.
After this phase, a robot mission planner can trigger sweeps, record IQ clips with pose
metadata, and use RF power observations to annotate its costmap.

</domain>

<decisions>
## Implementation Decisions

### Sweep Action Server (SWP-01, SWP-02, SWP-03)
- **D-01:** Convert the existing `Sweep.srv` to a proper ROS2 action (`Sweep.action`) with
  `feedback` carrying current_hop, hops_total, current_freq_hz, and partial psd_db.
  Service-based sweep already existed; upgrading to action adds cancel and progress.
- **D-02:** Sweep node (`hackrf_ros/sweep_node.py`) runs as a standalone node separate from
  the lifecycle driver. Reuses the driver's `/hackrf/spectrum` subscription; does NOT
  open the HackRF directly. Coordinates via parameter set on `/hackrf_node`.
- **D-03:** Tukey-blended hop stitching in linear domain — convert dB→linear, apply
  Tukey window overlap (α=0.2) at hop boundaries, stitch, convert back to dB.
  Avoids hard discontinuities at hop edges.
- **D-04:** Cancel handling: on goal cancel, restore `center_frequency` parameter to
  pre-sweep value via `ros2 param set`, publish partial result if ≥1 hop complete.
- **D-05:** Sweep action defined in `hackrf_interfaces/action/Sweep.action`. New file —
  existing `Sweep.srv` stays for backward compatibility but is not used by Phase 2 sweep.

### IQ Recording Action Server (REC-01, REC-02, REC-03, REC-04)
- **D-06:** `hackrf_ros/iq_recorder_node.py` — standalone node, subscribes to `/hackrf/iq`
  raw IQ queue (BEST_EFFORT, keep_last=1). Action: `RecordIQ.action` with goal
  (duration_s, output_path, trigger_reason), feedback (bytes_written, duration_elapsed),
  result (success, sigmf_data_path, sigmf_meta_path, samples_written, drops).
- **D-07:** SigMF format: `ci8` datatype (native HackRF int8 pairs), append raw bytes
  from IQ queue directly to `.sigmf-data` file. Write `.sigmf-meta` JSON once at stop.
  Pre-allocate data file with `os.posix_fallocate()` if duration is known.
- **D-08:** Robot pose from TF: at recording start, look up `base_link` → `map` transform
  via `tf2_ros.Buffer.lookup_transform()`. Store as `global_field:robot_pose` in SigMF
  global metadata. If TF unavailable, log WARN and store null.
- **D-09:** Dedicated write thread draining `recorder_q = queue.Queue(maxsize=256)`.
  RX callback does `put_nowait()` to recorder_q — drops oldest on full (WARN log).
  Write thread: drain queue → write raw bytes → loop. No blocking on disk stall.
- **D-10:** `hackrf_lifecycle_node.py` gains a second queue `_recorder_q` (None when not
  recording). RX callback fans out to both `_fft_q` and `_recorder_q` when active.
  IQ recorder node signals start/stop via a ROS2 service on the driver node.

### RF Occupancy Grid (MAP-01, MAP-02, MAP-03)
- **D-11:** `hackrf_ros/rf_map_node.py` — standalone node. Subscribes to
  `/hackrf/detections` (RELIABLE) and `/tf` (for robot odometry). Publishes
  `nav_msgs/OccupancyGrid` on `/hackrf/rf_occupancy`.
- **D-12:** Grid cells accumulate RF power in dBm via exponential moving average
  (α=0.1). Cell value = `int(clamp((power_dbm + 120) / 80 * 100, 0, 100))` —
  maps -120 dBm (noise floor) → 0, -40 dBm (strong signal) → 100.
- **D-13:** Grid parameters: `grid_resolution` (default 0.5 m/cell),
  `grid_width_m` / `grid_height_m` (default 50×50 m), `grid_frame` (default 'map'),
  `update_rate` (default 1.0 Hz). All exposed as ROS2 parameters.
- **D-14:** Robot position: lookup `base_link` → `grid_frame` TF. Drop update if TF
  unavailable. Grid origin follows robot (sliding window centered on current pose).

### Action Definitions (new files in hackrf_interfaces/action/)
- **D-15:** `Sweep.action`:
  ```
  # Goal
  float64 freq_min_hz
  float64 freq_max_hz
  float64 step_hz          # hop step size (default 20e6 = 20 MHz)
  int32   averaging        # FFT averages per hop (default 4)
  ---
  # Result
  bool    success
  string  message
  float64 freq_min_hz
  float64 freq_max_hz
  float64 bin_width_hz
  float32[] psd_db         # stitched composite
  ---
  # Feedback
  int32   current_hop
  int32   total_hops
  float64 current_freq_hz
  float32[] partial_psd_db
  ```
- **D-16:** `RecordIQ.action`:
  ```
  # Goal
  float64 duration_s       # 0 = record until cancelled
  string  output_path      # directory to write sigmf files
  string  trigger_reason   # freeform label in metadata
  ---
  # Result
  bool    success
  string  message
  string  sigmf_data_path
  string  sigmf_meta_path
  int64   samples_written
  int32   queue_drops
  ---
  # Feedback
  int64   bytes_written
  float64 duration_elapsed_s
  int32   queue_drops
  ```

### Threading & Executor
- **D-17:** All three new nodes (sweep_node, iq_recorder_node, rf_map_node) use
  `MultiThreadedExecutor` + `ReentrantCallbackGroup` — prerequisite already met in
  Phase 1. Action execute callbacks must NOT run in the same callback group as
  timer callbacks (would block).
- **D-18:** hackrf_lifecycle_node gains `_recorder_q: queue.Queue | None = None` and
  a service `/hackrf/recording/start` + `/hackrf/recording/stop` (std_srvs/Trigger)
  to activate/deactivate the recorder fan-out.

### Package Changes
- **D-19:** `hackrf_interfaces` adds `action/` directory. CMakeLists.txt updated with
  `rosidl_generate_interfaces(... "action/Sweep.action" "action/RecordIQ.action" ...)`.
  `sigmf` added to Dockerfile pip install.
- **D-20:** `setup.py` adds entry points: `sweep_action_node`, `iq_recorder_node`,
  `rf_map_node`. `sensor_pipeline.launch.py` updated or new
  `autonomy_pipeline.launch.py` created.

### Claude's Discretion
- Exact Tukey blend overlap width and alpha value
- SigMF `captures` entry strategy for multi-hop recordings
- Grid sliding window update strategy (recenter vs. expand)
- Whether to modify existing sweep_node.py or create new sweep_action_node.py

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Existing Phase 1 Code (build on these)
- `hackrf_ros/hackrf_lifecycle_node.py` — driver with IQ queue, FFT pipeline
- `hackrf_ros/cfar_node.py` — CFAR node, RFDetectionArray publisher
- `hackrf_ros/sweep_node.py` — existing sweep implementation (service-based, rewrite as action)
- `hackrf_interfaces/srv/Sweep.srv` — existing service (keep for reference, new action replaces)
- `hackrf_interfaces/CMakeLists.txt` — add action/ to rosidl_generate_interfaces

### Stack References (from CLAUDE.md)
- SigMF format: sigmf 1.7.2, ci8 datatype, streaming write pattern
- Threading: sdr-iq-pipeline-threading rules (bounded queue per sink, no blocking in RX cb)
- Action servers: rclpy ActionServer, MultiThreadedExecutor, ReentrantCallbackGroup
- Occupancy grid: nav_msgs/OccupancyGrid standard format

### Phase 1 Context
- `.planning/phases/01-sensor-foundations/01-CONTEXT.md` — threading model, QoS decisions

</canonical_refs>
