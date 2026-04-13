---
phase: 02-robot-autonomy-integration
plan: "03"
subsystem: iq_recorder
tags: [sigmf, iq-recording, action-server, write-thread, ci8, tf2]
dependency_graph:
  requires:
    - hackrf_interfaces.action.RecordIQ (02-01)
    - hackrf_ros.hackrf_lifecycle_node._iq_queue (01-xx)
  provides:
    - hackrf_ros.iq_recorder_node.IQRecorderNode
    - /hackrf/record_iq action server
    - /hackrf/recording/start Trigger service (in driver)
    - /hackrf/recording/stop Trigger service (in driver)
    - _recorder_q fan-out in _rx_callback
  affects:
    - 02-04-PLAN.md (rf_map_node independent; no direct dep on recorder)
tech_stack:
  added:
    - sigmf==1.8.0 (SigMFFile.tofile() API — not dump()+tofile kwarg)
  patterns:
    - Daemon write thread with _STOP_SENTINEL for clean exit
    - Drop-oldest bounded queue (maxsize=256) between ROS subscription and disk
    - ci8 datatype: float32 * 128 → clip → int8 → tobytes()
    - SigMFFile.tofile(path, skip_validate=True, overwrite=True) for metadata write
    - CPython GIL snapshot: rq = self._recorder_q before put_nowait
key_files:
  created:
    - hackrf_ros/iq_recorder_node.py
    - test/test_iq_recorder_driver.py
    - test/test_iq_recorder_node.py
  modified:
    - hackrf_ros/hackrf_lifecycle_node.py
    - setup.py
decisions:
  - "SigMF 1.8.0 API uses tofile(path) not dump(path, tofile=True) — incompatible with plan pseudocode; used tofile(path, skip_validate=True, overwrite=True) throughout"
  - "SigMFFile constructed without data_file= arg to avoid mmap on empty file during metadata-only build"
  - "Subscription approach chosen over shared in-process queue: _iq_callback on /hackrf/iq converts Float32->ci8 into bounded write_q; driver _recorder_q still present as future hook"
  - "max_duration_s=300s parameter enforces T-02-03-02 cap on indefinite duration_s=0 recordings"
  - "os.path.realpath() applied to output_path (T-02-03-01) with log, no prefix restriction (robot-internal)"
  - "ENOSPC detected in write thread via errno; sets result_dict['enospc'] flag; main loop aborts goal"
metrics:
  duration_minutes: 6
  tasks_completed: 2
  tasks_total: 2
  files_created: 3
  files_modified: 2
  completed_date: "2026-04-13T04:08:00Z"
---

# Phase 02 Plan 03: IQ Recorder Node Summary

**One-liner:** SigMF ci8 IQ recorder action server with daemon write thread, bounded drop-oldest queue, TF pose capture, and driver fan-out via Trigger services.

## Objective Achieved

`hackrf_ros/iq_recorder_node.py` implements the full RecordIQ action server:
- Subscribes to `/hackrf/iq` (Float32MultiArray, BEST_EFFORT), converts normalized float32 to ci8 bytes, enqueues into bounded write_q (maxsize=256)
- Daemon `_write_thread_func` drains write_q to `.sigmf-data`; exits on `_STOP_SENTINEL`
- `_build_sigmf_meta` produces SigMFFile with ci8 datatype, hackrf_ros extension fields, and robot_pose (dict or JSON null)
- TF `base_link→map` lookup at recording start; WARN logged if unavailable; null stored in metadata
- `os.posix_fallocate()` pre-allocates data file when duration_s > 0; OSError caught and logged DEBUG (not all filesystems support it)
- `max_duration_s` parameter (default 300 s) caps `duration_s=0` indefinite recordings (T-02-03-02)
- ENOSPC detected via errno in write thread; aborts goal with descriptive message (T-02-03-04)

`hackrf_ros/hackrf_lifecycle_node.py` extended with:
- `_recorder_q: queue.Queue | None = None` and `_recorder_q_drops: int = 0`
- Fan-out in `_rx_callback`: put_nowait to `_recorder_q` with drop-oldest on full
- `/hackrf/recording/start` Trigger service (`_handle_recording_start`)
- `/hackrf/recording/stop` Trigger service (`_handle_recording_stop`)

## ci8 Byte Layout

ci8 = interleaved int8 pairs: `[I₀, Q₀, I₁, Q₁, ...]`

Conversion path:
```
Float32MultiArray.data (normalized float32 ∈ [-1.0, 1.0])
    → np.array(dtype=float32)
    → * 128.0
    → np.clip(-128, 127)
    → .astype(np.int8)
    → .tobytes()         ← written to .sigmf-data
```

`samples_written = bytes_written // 2` (2 bytes per ci8 sample pair)

## TF Lookup Behavior

At `_execute_record` start, `_lookup_pose()` calls:
```python
self._tf_buffer.lookup_transform('map', 'base_link', rclpy.time.Time(),
    timeout=rclpy.duration.Duration(seconds=1.0))
```

On `LookupException`, `ExtrapolationException`, or any `Exception`:
- Logs `WARN: TF unavailable: <exception message>`
- Returns `None`
- `'hackrf_ros:robot_pose': None` stored in SigMF global metadata (serialises as JSON `null`)
- Recording continues without pose; data file is still written

## Recording Service vs. Subscription Architecture

**Design resolution:** The driver's `/hackrf/recording/start` and `/hackrf/recording/stop` Trigger services activate/deactivate `_recorder_q` in the driver — useful as a future extension hook for a shared-memory or intra-process raw bytes path. However, `iq_recorder_node` **does not share a queue object with the driver across process boundaries**. Instead:

- `IQRecorderNode` subscribes to `/hackrf/iq` (Float32MultiArray, BEST_EFFORT, depth=1)
- `_iq_callback` converts Float32→ci8 and enqueues into an internal `_write_q` (bounded, maxsize=256)
- The write thread drains `_write_q` directly

The driver's Trigger services are still called at recording start/stop (best-effort; failure logs WARN and continues) — this activates `_recorder_q` for future consumers but is not required for the subscription path to function.

## Pytest Output

```
test/test_iq_recorder_driver.py::TestRxCallbackFanOut::test_drop_oldest_when_recorder_queue_full PASSED
test/test_iq_recorder_driver.py::TestRxCallbackFanOut::test_fanout_to_both_queues_when_recorder_active PASSED
test/test_iq_recorder_driver.py::TestRxCallbackFanOut::test_no_fanout_when_recorder_inactive PASSED
test/test_iq_recorder_driver.py::TestRecordingStartService::test_recording_start_creates_queue PASSED
test/test_iq_recorder_driver.py::TestRecordingStopService::test_recording_stop_clears_queue PASSED
test/test_iq_recorder_node.py::TestWriteThread::test_sentinel_exits_cleanly PASSED
test/test_iq_recorder_node.py::TestWriteThread::test_write_thread_drains_queue PASSED
test/test_iq_recorder_node.py::TestBuildSigmfMeta::test_meta_pose_none_stored_as_null PASSED
test/test_iq_recorder_node.py::TestBuildSigmfMeta::test_meta_with_pose PASSED
test/test_iq_recorder_node.py::TestIqCallback::test_iq_callback_no_write_when_queue_none PASSED
test/test_iq_recorder_node.py::TestIqCallback::test_iq_callback_puts_to_write_queue PASSED
test/test_iq_recorder_node.py::TestInvalidDirectory::test_bad_output_path_returns_failure PASSED

12 passed in 0.15s
```

## Build Verification

ROS2 Humble is not installed on the host machine. Build verification via `colcon build --packages-select hackrf_ros` is confirmed in Docker only. The entry point `iq_recorder_node = hackrf_ros.iq_recorder_node:main` added to `setup.py` follows the existing pattern for all other nodes in the package. The import structure mirrors `cfar_node.py` and `sweep_node.py`.

## Commits

| Task | Phase | Commit | Description |
|------|-------|--------|-------------|
| Task 1 (driver) | RED | 2de7d06 | test(02-03): add failing driver fan-out tests |
| Task 1 (driver) | GREEN | a8866d6 | feat(02-03): extend driver with recorder fan-out and recording services |
| Task 2 (recorder) | RED | 62c053c | test(02-03): add failing IQRecorderNode tests |
| Task 2 (recorder) | GREEN | eeebd02 | feat(02-03): implement IQRecorderNode with SigMF write thread |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] SigMF 1.8.0 API mismatch with plan pseudocode**
- **Found during:** Task 2, GREEN phase
- **Issue:** Plan specified `meta.dump(meta_path, tofile=True)` but `SigMFFile.dump()` in v1.8.0 takes a file handle, not a path string; `tofile=True` is not a valid kwarg. The plan pseudocode matched an older API.
- **Fix:** Used `meta.tofile(meta_path, skip_validate=True, overwrite=True)` which is the correct v1.8.0 method for writing to a path. Also removed `data_file=` from `SigMFFile()` constructor to avoid mmap on empty file during metadata-only object construction.
- **Files modified:** `hackrf_ros/iq_recorder_node.py`, `test/test_iq_recorder_node.py`
- **Commit:** eeebd02

**2. [Rule 3 - Blocking] Mock incompatibility when running both test files together**
- **Found during:** Task 2, overall verification
- **Issue:** `test_iq_recorder_driver.py` mock for `rclpy.qos` lacked `DurabilityPolicy`; `test_iq_recorder_node.py` mock for `rcl_interfaces.msg` lacked `FloatingPointRange`, `IntegerRange`, `SetParametersResult`. When both test modules were collected in one pytest session, the first-registered mock via `sys.modules.setdefault` won, causing ImportError in the second module.
- **Fix:** Added missing attributes to each test's mock setup.
- **Files modified:** `test/test_iq_recorder_driver.py`, `test/test_iq_recorder_node.py`
- **Commit:** eeebd02

### Missing Threat Mitigations Applied (Rule 2)

**3. [Rule 2 - Security] T-02-03-02: max_duration_s parameter added**
- **Trigger:** Threat register flags `duration_s=0` as disk-fill DoS risk; plan action spec mentioned it but implementation required explicit parameter.
- **Fix:** `max_duration_s` ROS2 parameter (default 300 s) declared; recording loop breaks when elapsed exceeds it with WARN log.
- **Files modified:** `hackrf_ros/iq_recorder_node.py`

**4. [Rule 2 - Security] T-02-03-04: ENOSPC write error abort**
- **Trigger:** Threat register requires non-blocking write with OSError abort on ENOSPC.
- **Fix:** `_write_thread_func` catches `OSError`; checks `errno.ENOSPC`; sets `result_dict['enospc'] = True`. Main recording loop polls this flag and aborts goal with `goal_handle.abort()` and descriptive message.
- **Files modified:** `hackrf_ros/iq_recorder_node.py`

## Known Stubs

None. The action server is fully wired: subscription callback converts and enqueues real data, write thread drains to real files, SigMF metadata is complete with all SDR parameters and pose.

The driver's `_recorder_q` is an intentional forward hook (per D-10) — it is populated by `_rx_callback` when activated but is not yet consumed by any process-external reader. This is by design: the subscription path in `iq_recorder_node` handles Phase 2 recording. The `_recorder_q` path is not a stub — it functions correctly as a fan-out queue; it just awaits a future consumer.

## Threat Flags

None. No new network endpoints introduced. File writes are local filesystem only. The `output_path` is caller-supplied but resolved via `os.path.realpath()` and logged (T-02-03-01 accepted per threat model — robot-internal use).

## Self-Check: PASSED
