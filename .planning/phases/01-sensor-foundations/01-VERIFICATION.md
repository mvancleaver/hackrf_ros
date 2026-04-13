---
phase: 01-sensor-foundations
verified: 2026-04-13T03:48:18Z
status: human_needed
score: 5/5
overrides_applied: 0
re_verification: false
human_verification:
  - test: "Confirm rqt plugin loads under Plugins -> HackRF -> Spectrum Display and renders live spectrum data"
    expected: "Plugin appears in Plugins menu under HackRF group; spectrum waterfall displays live PSD from /hackrf/spectrum"
    why_human: "Qt/rqt plugin loading cannot be verified programmatically; requires display environment and running hardware"
  - test: "Confirm /hackrf/spectrum QoS is RELIABLE at 18 Hz and pipeline is stable"
    expected: "ros2 topic info /hackrf/spectrum -v shows RELIABLE; ros2 topic hz shows >= 9.5 Hz sustained"
    why_human: "User-verified during session at 18-19 Hz. Documenting as human checkpoint since QoS differs from plan (BEST_EFFORT in plan, RELIABLE in code — both publisher and subscriber use RELIABLE, so QoS is compatible and pipeline is not broken)"
  - test: "Confirm dynamic retune works without restart"
    expected: "ros2 param set /hackrf_node center_frequency 2441000000.0 completes in < 100 ms, spectrum shifts to new center, detections update"
    why_human: "User-verified during session (2441 MHz retune to Bluetooth band confirmed); requires live hardware to repeat"
  - test: "Confirm TF tree shows base_link -> hackrf_antenna"
    expected: "ros2 run tf2_tools view_frames produces PDF/SVG with base_link -> hackrf_antenna edge"
    why_human: "TF tree inspection requires running ROS2 environment; static transform is wired in both launch file and on_configure"
---

# Phase 1: Sensor Foundations — Verification Report

**Phase Goal:** Any robot node can subscribe to calibrated, timestamped RF detections with stable IDs, band labels, and TF frame metadata
**Verified:** 2026-04-13T03:48:18Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (from ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | A subscriber to `/hackrf/spectrum` receives `SpectrumStamped` messages with valid `header.stamp` and `header.frame_id` matching the configured antenna frame | VERIFIED | `hackrf_lifecycle_node.py` line 447: `msg.header.frame_id = self.get_parameter('antenna_frame').value`; line 446: `msg.header.stamp = self.get_clock().now().to_msg()`. Published via `self._spectrum_pub.publish(msg)` (line 454). User-confirmed at 18-19 Hz with 2441 MHz center. |
| 2 | `ros2 run tf2_tools view_frames` shows a static `base_link -> hackrf_antenna` transform in the TF tree | VERIFIED (code) / HUMAN NEEDED (runtime) | `sensor_pipeline.launch.py` lines 37-42: `static_transform_publisher` with `base_link -> hackrf_antenna`. `hackrf_lifecycle_node.py` lines 114-123: `StaticTransformBroadcaster` in `on_configure()` with configurable `parent_frame` (default `base_link`) and `antenna_frame` (default `hackrf_antenna`). Belt-and-suspenders: both launch file node and driver publish the same transform. Runtime confirmation requires human. |
| 3 | A subscriber to `/hackrf/detections` receives `RFDetectionArray` messages where each detection carries centroid frequency, 3 dB bandwidth, integrated power, SNR, and a band label | VERIFIED | `cfar_node.py` `_group_detections()` (lines 179-224) computes centroid_hz, bw_hz (3 dB bandwidth), peak_dbm, snr_db. `_classify_band()` (lines 314-328) assigns label from 18-entry table. All fields written to `RFDetection` and published via `self._detections_pub.publish(arr_msg)` (line 381). User-confirmed: zigbee at 2440/2448 MHz detected during session. |
| 4 | Detections on `/hackrf/detections` have stable IDs across consecutive frames — the same emitter receives the same ID until it disappears | VERIFIED | `_update_persistence()` (lines 232-308): frequency-proximity matching via `tolerance = 0.5 * max(track['bw_hz'], bw_hz)`, power-weighted center averaging on update, `_next_id` counter for new tracks, `absent` counter with `persistence_decay` expiry. Detection ID assigned at track creation and carried through `det.detection_id = tid` (line 305). |
| 5 | PSD publishing sustains 10 Hz on the Jetson ARM64 target | VERIFIED (human confirmed) | `scipy.fft` used (line 379: `scipy.fft.fftshift(scipy.fft.fft(windowed))`). Timer at 0.05s (20 Hz poll, line 161), averaging at 16 frames (line 54). User-confirmed 18-19 Hz in hardware session. |

**Score:** 5/5 ROADMAP truths verified

### Notable Deviation: QoS Settings (Not a ROADMAP gap)

The ROADMAP success criteria do not specify QoS. However, MSG-05 (REQUIREMENTS.md) and PLAN must_haves require BEST_EFFORT for streaming topics. The actual code uses RELIABLE throughout:

- `/hackrf/spectrum`: driver publishes RELIABLE (line 106), rqt plugin subscribes RELIABLE (line 91)
- `/hackrf/detections`: CFAR publishes RELIABLE (line 114)
- `/hackrf/rf_environment`: CFAR publishes RELIABLE (line 119) — this matches plan requirement

The CFAR node subscribes to `/hackrf/spectrum` as RELIABLE (line 106), which is compatible with the driver's RELIABLE publisher. The pipeline works correctly at 18 Hz as confirmed by the user.

**Assessment:** Publisher and subscriber QoS are consistent (both RELIABLE), so DDS will not drop messages due to QoS mismatch. The pipeline is functional. This deviates from MSG-05 in REQUIREMENTS.md and the plan must_haves, but does not block any ROADMAP success criterion. For strict requirement compliance, see REQUIREMENTS.md MSG-05.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `hackrf_interfaces/msg/SpectrumStamped.msg` | Stamped PSD message type with std_msgs/Header | VERIFIED | 7 fields: header, center_frequency_hz, sample_rate_hz, bin_width_hz, fft_size, psd_db[], noise_floor_db |
| `hackrf_interfaces/msg/RFDetection.msg` | Per-signal detection type with detection_id | VERIFIED | 7 fields including detection_id (uint32) |
| `hackrf_interfaces/msg/RFDetectionArray.msg` | Array of detections with frame metadata | VERIFIED | header, noise_floor_dbm, occupancy_pct, hackrf_interfaces/RFDetection[] detections |
| `hackrf_interfaces/msg/RFEnvironment.msg` | 1 Hz environment summary with emitter_count | VERIFIED | header, emitter_count, occupancy_pct, noise_floor_dbm, peak_power_dbm, peak_frequency_hz |
| `hackrf_ros/hackrf_lifecycle_node.py` | Upgraded driver — SpectrumStamped, TF, scipy.fft, MultiThreadedExecutor | VERIFIED | 636 lines; scipy.fft at line 379; SpectrumStamped published at line 454; StaticTransformBroadcaster at line 114; MultiThreadedExecutor at line 623 |
| `hackrf_ros/cfar_node.py` | CA-CFAR detector — persistence + classification | VERIFIED | 425 lines (>= 200 requirement); convolve1d at line 168; ndimage_label at line 194; Holik T_m at line 171; 18-entry band table |
| `hackrf_ros/spectrum_rqt_plugin.py` | rqt plugin for live spectrum display | VERIFIED | HackRFSpectrumPlugin class; subscribes to /hackrf/spectrum; matplotlib PSD plot with Qt timer |
| `hackrf_interfaces/CMakeLists.txt` | rosidl_generate_interfaces with all 4 msg types | VERIFIED | Lines 8-15: all 4 .msg files registered alongside Sweep.srv |
| `launch/sensor_pipeline.launch.py` | One-command launch for complete pipeline | VERIFIED | 98 lines; hackrf_tf + hackrf_node + cfar_node; auto-configure via TimerAction(1s) + OnStateTransition activate |
| `plugin.xml` | rqt plugin descriptor registered in package.xml | VERIFIED | class name=HackRFSpectrum, type=hackrf_ros.spectrum_rqt_plugin.HackRFSpectrumPlugin; registered in package.xml line 27 |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `hackrf_lifecycle_node.py _fft_frame` | `scipy.fft` | `scipy.fft.fftshift(scipy.fft.fft(windowed))` | WIRED | Line 379; import at line 20; zero numpy.fft references |
| `hackrf_lifecycle_node.py _process_and_publish_inner` | `SpectrumStamped` on `/hackrf/spectrum` | `self._spectrum_pub.publish(msg)` | WIRED | Publisher created line 110-111; publish called line 454; all 7 fields populated |
| `hackrf_lifecycle_node.py on_configure` | `tf2_ros.StaticTransformBroadcaster` | `self._tf_broadcaster.sendTransform(tf_msg)` | WIRED | Lines 114-123; configurable parent_frame and antenna_frame parameters |
| `main()` | `MultiThreadedExecutor` | `executor.spin()` | WIRED | Lines 622-631; executor.add_node(node); executor.spin() |
| `cfar_node.py _ca_cfar` | `scipy.ndimage.convolve1d` | sliding noise estimate kernel | WIRED | Lines 161-168; kernel built, convolve1d called with mode='nearest' |
| `cfar_node.py _spectrum_callback` | `_ca_cfar` | `mask = self._ca_cfar(psd_db, ...)` | WIRED | Line 355 |
| `cfar_node.py _spectrum_callback` | `self._detections_pub.publish` | RFDetectionArray built and published | WIRED | Lines 373-381 |
| `cfar_node.py _spectrum_callback` | `_classify_band` | `det.classification = self._classify_band(...)` | WIRED | Lines 367-370; classification written to each confirmed detection |
| `cfar_node.py _env_timer_callback` | `self._env_pub.publish` | RFEnvironment built from `_last_arr` | WIRED | Lines 389-408; timer at 1.0s |
| `launch/sensor_pipeline.launch.py` | `hackrf_ros.hackrf_lifecycle_node` | `executable='hackrf_node'` | WIRED | Line 48; registered in setup.py console_scripts |
| `launch/sensor_pipeline.launch.py` | `hackrf_ros.cfar_node` | `executable='cfar_node'` | WIRED | Line 57; registered in setup.py console_scripts |
| `plugin.xml` | `hackrf_ros.spectrum_rqt_plugin.HackRFSpectrumPlugin` | class type attribute | WIRED | package.xml line 27 exports plugin.xml; setup.py line 12 installs it to share/ |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|-------------------|--------|
| `hackrf_lifecycle_node.py` `_process_and_publish_inner` | `psd_db` (float array) | `_iq_queue` drained from HackRF RX callback -> `_process_raw` -> `_fft_frame` (FFT on real IQ bytes) | Yes — real ADC samples from hardware via `pyhackrf2.HackRF.start_rx` | FLOWING |
| `cfar_node.py` `_spectrum_callback` | `msg.psd_db` (from `/hackrf/spectrum`) | SpectrumStamped subscription -> ca_cfar -> cluster grouping -> persistence update | Yes — derived from live spectrum data | FLOWING |
| `cfar_node.py` `_env_timer_callback` | `self._last_arr` | Cached from most recent `_spectrum_callback` execution | Yes — updated every spectrum callback | FLOWING |
| `spectrum_rqt_plugin.py` `_update_plot` | `self._msg` (SpectrumStamped) | `/hackrf/spectrum` ROS subscription in background thread | Yes — live subscription; `_ros_cb` sets `self._msg` under lock | FLOWING |

### Behavioral Spot-Checks

| Behavior | Check | Result | Status |
|----------|-------|--------|--------|
| scipy.fft used (not numpy.fft) | `grep -c "scipy.fft" hackrf_lifecycle_node.py` | 3 matches (import + 2 calls) | PASS |
| No numpy.fft usage | `grep -c "np.fft\|numpy.fft" hackrf_lifecycle_node.py` | 0 matches | PASS |
| cfar_node importable (syntax) | `python3 -m py_compile hackrf_ros/cfar_node.py` | Not runnable without ROS env; 425-line file, no syntax issues visible | SKIP |
| MultiThreadedExecutor in main() | grep check | Lines 622-631; executor.add_node + executor.spin | PASS |
| plugin.xml type matches module path | `grep type plugin.xml` | `hackrf_ros.spectrum_rqt_plugin.HackRFSpectrumPlugin` matches file location | PASS |
| rqt loads under Plugins -> HackRF | Requires display/hardware | Not testable programmatically | SKIP (human item #1) |

### Requirements Coverage

| Requirement | Plan | Description | Status | Evidence |
|-------------|------|-------------|--------|----------|
| MSG-01 | 01-01, 01-02 | SpectrumStamped published on /hackrf/spectrum | SATISFIED | Publisher wired at line 110-111; all 7 fields populated |
| MSG-02 | 01-02 | Static TF broadcast base_link -> hackrf_antenna | SATISFIED | StaticTransformBroadcaster in on_configure() + launch file |
| MSG-03 | 01-01, 01-03 | RFDetection message type + published on /hackrf/detections | SATISFIED | Message defined; CFAR publishes at line 381 |
| MSG-04 | 01-01, 01-03 | RFEnvironment published at 1 Hz | SATISFIED | 1.0s timer at cfar_node.py line 124; published at line 408 |
| MSG-05 | 01-02, 01-03 | All streaming topics use BEST_EFFORT QoS | PARTIAL | Driver publishes RELIABLE (line 106); CFAR publishes RELIABLE for detections. QoS is internally consistent (both ends RELIABLE) so DDS delivers messages without mismatch. Functional deviation from requirement wording. |
| MSG-06 | 01-02 | MultiThreadedExecutor with ReentrantCallbackGroup | SATISFIED | Lines 622-631 + ReentrantCallbackGroup for sweep service (line 127) |
| DET-01 | 01-03 | CA-CFAR energy detection | SATISFIED | `_ca_cfar()` with Holik T_m formula at line 171 |
| DET-02 | 01-03 | Signal clustering from adjacent bins | SATISFIED | `_group_detections()` using `ndimage_label` at line 194 |
| DET-03 | 01-03 | Persistence tracking | SATISFIED | `_update_persistence()` with frequency-proximity matching |
| DET-04 | 01-03 | Stable detection IDs across frames | SATISFIED | `_next_id` counter, ID preserved in `self._tracks[tid]` |
| DET-05 | 01-03 | Band classification table | SATISFIED | 18-entry `_BAND_TABLE` covering WiFi/BLE/ZigBee/LTE/ISM/FM/GPS |
| DET-06 | 01-03 | 2.4 GHz ISM disambiguation by bandwidth | SATISFIED | Lines 52-55: wifi (>15 MHz), ble (1-15 MHz), zigbee (<1 MHz), ism_2400 (wildcard) |
| PERF-01 | 01-02 | scipy.fft for ARM NEON SIMD | SATISFIED | `import scipy.fft` at line 20; used in `_fft_frame()` line 379 |
| PERF-02 | 01-04 | PSD publishing >= 9.5 Hz sustained | SATISFIED (human confirmed) | User verified 18-19 Hz with hardware |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `spectrum_rqt_plugin.py` | 4 | Docstring says "BEST_EFFORT" but subscription at line 91 uses RELIABLE | Info | Documentation mismatch; functionally fine since publisher is also RELIABLE |
| `hackrf_lifecycle_node.py` | 5 | Docstring says "BEST_EFFORT QoS, depth=5" but code at line 106 uses RELIABLE | Info | Documentation mismatch; functionally fine since all subscribers use RELIABLE |
| `cfar_node.py` | 90-93 | `qos_best_effort` QoSProfile defined but never used (dead variable) | Warning | Unused QoS object; was likely intended for `_detections_pub` but `qos_reliable` was applied instead |

No blocker anti-patterns found. No TODO/FIXME/placeholder patterns found in any key file.

### Human Verification Required

#### 1. rqt Spectrum Plugin Load

**Test:** Run `rqt`, navigate to Plugins -> HackRF -> Spectrum Display
**Expected:** Plugin appears and shows live spectrum waterfall updating at ~10 Hz. Title bar shows center frequency, sample rate, peak power, and noise floor.
**Why human:** Qt plugin registration and rendering requires a display environment and running hardware.

#### 2. QoS RELIABLE Compatibility at Runtime

**Test:** With pipeline running: `ros2 topic info /hackrf/spectrum -v` and `ros2 topic info /hackrf/detections -v`
**Expected:** Both show RELIABLE (consistent pub/sub). Confirm no "incompatible QoS" warnings in node logs. `ros2 topic hz /hackrf/spectrum` shows >= 9.5 Hz.
**Why human:** User confirmed 18 Hz in session. Documenting as checkpoint to confirm no QoS negotiation warnings surface in a fresh run.

#### 3. TF Tree Visualization

**Test:** With pipeline running: `ros2 run tf2_tools view_frames`
**Expected:** PDF/image shows `base_link -> hackrf_antenna` edge with correct translation (0, 0, 0 default).
**Why human:** TF tree output requires running ROS2 environment; static transform is wired in code but needs visual confirmation.

#### 4. Dynamic Retune Without Restart

**Test:** `ros2 param set /hackrf_node center_frequency 2441000000.0` while streaming
**Expected:** Completes without timeout; `/hackrf/spectrum` `center_frequency_hz` field shifts to 2441 MHz within 2 frames; `/hackrf/detections` updates accordingly.
**Why human:** User confirmed during session. Requires hardware to re-verify.

### Gaps Summary

No blocking gaps found against the ROADMAP success criteria. All 5 ROADMAP success criteria are satisfied by the implementation.

One requirement deviation noted (MSG-05 BEST_EFFORT) does not block phase goal. The QoS deviation is internally consistent (all nodes use RELIABLE), so the pipeline functions correctly. If strict MSG-05 compliance is required, all three files need one-line changes to swap RELIABLE -> BEST_EFFORT in the streaming topic QoS profile.

Phase goal is achieved: robot nodes can subscribe to calibrated, timestamped RF detections with stable IDs, band labels, and TF frame metadata.

---

_Verified: 2026-04-13T03:48:18Z_
_Verifier: Claude (gsd-verifier)_
