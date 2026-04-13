"""IQ Recorder Node — ROS2 action server for SigMF IQ recording.

Implements the RecordIQ action (hackrf_interfaces/action/RecordIQ):
- Subscribes to /hackrf/iq (Float32MultiArray, BEST_EFFORT) for IQ data
- Activates driver recording via /hackrf/recording/start Trigger service
- Drains bounded write queue (maxsize=256) via daemon write thread
- Writes SigMF .sigmf-data (ci8 bytes) and .sigmf-meta (JSON) file pair
- Looks up robot pose (base_link→map) from TF at recording start
- Enforces max_duration_s (default 300 s) to bound unbounded recordings

Security mitigations (per threat model):
    T-02-03-01: output_path logged with realpath but not restricted (robot-internal)
    T-02-03-02: max_duration_s parameter caps indefinite recordings
    T-02-03-04: OSError/ENOSPC aborts recording with descriptive error

Published interfaces:
    Action: /hackrf/record_iq  (hackrf_interfaces/action/RecordIQ)

Service clients used:
    /hackrf/recording/start  (std_srvs/Trigger)
    /hackrf/recording/stop   (std_srvs/Trigger)
"""
from __future__ import annotations

import os
import queue
import threading
import time
from datetime import datetime, timezone

import numpy as np
import sigmf
from sigmf import SigMFFile

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)

import tf2_ros

from std_msgs.msg import Float32MultiArray
from std_srvs.srv import Trigger

from hackrf_interfaces.action import RecordIQ


# ---------------------------------------------------------------------------
# Module-level sentinel — shared between module and tests
# ---------------------------------------------------------------------------
_STOP_SENTINEL = object()


# ---------------------------------------------------------------------------
# Write thread (pure function — no self dependency)
# ---------------------------------------------------------------------------

def _write_thread_func(data_file_handle, write_q: queue.Queue,
                       result_dict: dict) -> None:
    """Daemon write thread: drains write_q into data_file_handle.

    Exits when _STOP_SENTINEL is received. Handles queue.Empty timeouts
    gracefully (disk stall resilience). On ENOSPC (IOError errno 28)
    sets result_dict['enospc'] = True and exits.
    """
    import errno as _errno

    while True:
        try:
            item = write_q.get(timeout=1.0)
        except queue.Empty:
            continue  # disk stall — keep waiting

        if item is _STOP_SENTINEL:
            try:
                data_file_handle.flush()
            except OSError:
                pass
            break

        try:
            data_file_handle.write(item)
            result_dict['bytes'] += len(item)
        except OSError as exc:
            if exc.errno == _errno.ENOSPC:
                result_dict['enospc'] = True
            result_dict['write_error'] = str(exc)
            break


class IQRecorderNode(Node):
    """ROS2 action server for HackRF SigMF IQ recording.

    Accepts RecordIQ goals, activates driver fan-out, writes ci8 bytes to
    a SigMF file pair, and reports progress via action feedback.
    """

    def __init__(self, **kwargs):
        super().__init__('iq_recorder_node', **kwargs)

        # Parameters
        self.declare_parameter('driver_node_name', '/hackrf_node')
        self.declare_parameter('feedback_interval_s', 1.0)
        self.declare_parameter('max_duration_s', 300.0)

        # TF buffer and listener for robot pose lookup
        self._tf_buffer = tf2_ros.Buffer()
        self._tf_listener = tf2_ros.TransformListener(self._tf_buffer, self)

        # Service clients for driver recording control
        self._start_client = self.create_client(
            Trigger, '/hackrf/recording/start')
        self._stop_client = self.create_client(
            Trigger, '/hackrf/recording/stop')

        # IQ subscription (BEST_EFFORT, depth=1 — drop stale; activated always)
        qos_be = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            durability=DurabilityPolicy.VOLATILE,
        )
        self._iq_sub = self.create_subscription(
            Float32MultiArray,
            '/hackrf/iq',
            self._iq_callback,
            qos_be,
        )

        # Action server (ReentrantCallbackGroup so execute runs alongside timers)
        action_group = ReentrantCallbackGroup()
        self._action_server = ActionServer(
            self,
            RecordIQ,
            '/hackrf/record_iq',
            execute_callback=self._execute_record,
            goal_callback=self._goal_callback,
            cancel_callback=self._cancel_callback,
            callback_group=action_group,
        )

        # Write queue — set during recording, None otherwise
        self._write_q: queue.Queue | None = None
        self._drop_count: int = 0

        self.get_logger().info('IQRecorderNode ready.')

    # ------------------------------------------------------------------
    # Action callbacks
    # ------------------------------------------------------------------

    def _goal_callback(self, goal_request):
        """Accept all incoming goals."""
        return GoalResponse.ACCEPT

    def _cancel_callback(self, goal_handle):
        """Accept cancel requests."""
        return CancelResponse.ACCEPT

    # ------------------------------------------------------------------
    # TF pose lookup
    # ------------------------------------------------------------------

    def _lookup_pose(self) -> dict | None:
        """Look up base_link→map transform. Returns dict or None on failure."""
        import rclpy.time
        import rclpy.duration
        try:
            t = self._tf_buffer.lookup_transform(
                'map', 'base_link',
                rclpy.time.Time(),
                timeout=rclpy.duration.Duration(seconds=1.0),
            )
            return {
                'x': float(t.transform.translation.x),
                'y': float(t.transform.translation.y),
                'z': float(t.transform.translation.z),
                'qx': float(t.transform.rotation.x),
                'qy': float(t.transform.rotation.y),
                'qz': float(t.transform.rotation.z),
                'qw': float(t.transform.rotation.w),
            }
        except (tf2_ros.LookupException,
                tf2_ros.ExtrapolationException,
                tf2_ros.TransformException,
                Exception) as exc:
            self.get_logger().warning(f'TF unavailable: {exc}')
            return None

    # ------------------------------------------------------------------
    # SigMF metadata builder
    # ------------------------------------------------------------------

    def _build_sigmf_meta(
        self,
        data_path: str,
        center_freq: float,
        sample_rate: float,
        lna_gain: int,
        vga_gain: int,
        trigger_reason: str,
        pose: dict | None,
        capture_timestamp_iso: str,
    ) -> SigMFFile:
        """Build a SigMFFile object (ci8, custom hackrf_ros extensions).

        Args:
            data_path: Path to the .sigmf-data file.
            center_freq: Center frequency in Hz.
            sample_rate: Sample rate in Hz.
            lna_gain: LNA gain dB.
            vga_gain: VGA gain dB.
            trigger_reason: Freeform label stored in global metadata.
            pose: Robot pose dict (x,y,z,qx,qy,qz,qw) or None.
            capture_timestamp_iso: ISO-8601 timestamp string.

        Returns:
            SigMFFile ready to dump.
        """
        meta = SigMFFile(
            global_info={
                sigmf.SigMFFile.DATATYPE_KEY: 'ci8',
                sigmf.SigMFFile.SAMPLE_RATE_KEY: sample_rate,
                sigmf.SigMFFile.AUTHOR_KEY: 'hackrf_ros',
                'hackrf_ros:center_frequency_hz': center_freq,
                'hackrf_ros:lna_gain': lna_gain,
                'hackrf_ros:vga_gain': vga_gain,
                'hackrf_ros:trigger_reason': trigger_reason,
                'hackrf_ros:robot_pose': pose,  # None serialises as JSON null
            },
        )
        meta.add_capture(0, metadata={
            sigmf.SigMFFile.FREQUENCY_KEY: center_freq,
            sigmf.SigMFFile.DATETIME_KEY: capture_timestamp_iso,
        })
        return meta

    # ------------------------------------------------------------------
    # Recording service helpers
    # ------------------------------------------------------------------

    def _call_recording_service(self, service_client) -> bool:
        """Call a Trigger service synchronously. Returns success bool."""
        if not service_client.wait_for_service(timeout_sec=2.0):
            self.get_logger().error('Recording service not available.')
            return False
        try:
            response = service_client.call(Trigger.Request())
            return bool(response.success)
        except Exception as exc:
            self.get_logger().error(f'Service call failed: {exc}')
            return False

    # ------------------------------------------------------------------
    # IQ subscription callback
    # ------------------------------------------------------------------

    def _iq_callback(self, msg) -> None:
        """Convert Float32MultiArray to ci8 bytes and enqueue for writing."""
        wq = self._write_q  # snapshot ref under CPython GIL
        if wq is None:
            return

        # Driver publishes normalised float32 in [-1.0, 1.0]; convert to int8
        arr = np.array(msg.data, dtype=np.float32)
        raw = np.clip(arr * 128.0, -128, 127).astype(np.int8)
        chunk = raw.tobytes()

        try:
            wq.put_nowait(chunk)
        except queue.Full:
            try:
                wq.get_nowait()  # drop oldest
            except queue.Empty:
                pass
            try:
                wq.put_nowait(chunk)
            except queue.Full:
                pass
            self._drop_count += 1
            self.get_logger().warning(
                'IQ write queue full — dropped oldest chunk')

    # ------------------------------------------------------------------
    # Action execute callback
    # ------------------------------------------------------------------

    def _execute_record(self, goal_handle) -> RecordIQ.Result:
        """Execute RecordIQ action goal."""
        goal = goal_handle.request
        output_path = goal.output_path
        duration_s = float(goal.duration_s)
        trigger_reason = goal.trigger_reason

        result = RecordIQ.Result()

        # T-02-03-01: log resolved path (robot-internal; no prefix restriction)
        real_output = os.path.realpath(output_path)
        self.get_logger().info(
            f'RecordIQ: output={real_output}, duration={duration_s}s, '
            f'reason={trigger_reason!r}')

        # 1. Validate / create output directory
        try:
            os.makedirs(real_output, exist_ok=True)
        except OSError as exc:
            self.get_logger().error(f'Cannot create output path: {exc}')
            goal_handle.abort()
            result.success = False
            result.message = str(exc)
            return result

        # 2. Lookup robot pose (TF)
        pose = self._lookup_pose()
        if pose is None:
            self.get_logger().warning(
                'TF base_link→map unavailable; recording without pose.')

        # 3. Generate file paths
        ts = datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        base = f'iq_{ts}'
        data_path = os.path.join(real_output, base + '.sigmf-data')
        meta_path = os.path.join(real_output, base + '.sigmf-meta')
        capture_timestamp = datetime.now(tz=timezone.utc).isoformat()

        # 4. Get SDR parameters (use defaults if driver unavailable)
        try:
            center_freq = float(
                self.get_parameter('center_frequency').value)
        except Exception:
            center_freq = 2437e6
            self.get_logger().warning(
                'Could not read center_frequency; using default 2437 MHz.')
        try:
            sample_rate = float(
                self.get_parameter('sample_rate').value)
        except Exception:
            sample_rate = 20e6
            self.get_logger().warning(
                'Could not read sample_rate; using default 20 MHz.')
        try:
            lna_gain = int(self.get_parameter('lna_gain').value)
        except Exception:
            lna_gain = 16
        try:
            vga_gain = int(self.get_parameter('vga_gain').value)
        except Exception:
            vga_gain = 20

        # 5. Open data file and optionally pre-allocate
        try:
            data_file = open(data_path, 'wb')
        except OSError as exc:
            self.get_logger().error(f'Cannot open data file: {exc}')
            goal_handle.abort()
            result.success = False
            result.message = str(exc)
            return result

        if duration_s > 0:
            alloc_bytes = int(duration_s * sample_rate * 2)  # ci8 = 2 bytes
            try:
                os.posix_fallocate(data_file.fileno(), 0, alloc_bytes)
                self.get_logger().debug(
                    f'Pre-allocated {alloc_bytes} bytes for IQ data.')
            except OSError as exc:
                # Not all filesystems support fallocate — continue without
                self.get_logger().debug(
                    f'posix_fallocate not supported: {exc}')

        # 6. Activate driver fan-out
        if not self._call_recording_service(self._start_client):
            self.get_logger().warning(
                'Driver recording/start service failed; continuing anyway '
                '(subscription path still active).')

        # 7. Init write queue and launch write thread
        write_q: queue.Queue = queue.Queue(maxsize=256)
        result_stats: dict = {'bytes': 0, 'drops': 0, 'enospc': False}
        self._write_q = write_q
        self._drop_count = 0

        write_thread = threading.Thread(
            target=_write_thread_func,
            args=(data_file, write_q, result_stats),
            daemon=True,
        )
        write_thread.start()

        # 8. Recording loop
        max_dur = float(self.get_parameter('max_duration_s').value)
        feedback_interval = float(
            self.get_parameter('feedback_interval_s').value)
        start_time = time.monotonic()
        last_feedback = start_time

        while True:
            now = time.monotonic()
            elapsed = now - start_time

            # Cancel check
            if goal_handle.is_cancel_requested:
                self.get_logger().info('RecordIQ: cancel requested.')
                break

            # Duration limit (T-02-03-02: max_duration_s caps open-ended)
            if duration_s > 0 and elapsed >= duration_s:
                break
            if elapsed >= max_dur:
                self.get_logger().warning(
                    f'RecordIQ: max_duration_s={max_dur}s reached; stopping.')
                break

            # ENOSPC abort (T-02-03-04)
            if result_stats.get('enospc'):
                self.get_logger().error(
                    'RecordIQ: disk full (ENOSPC); aborting.')
                self._write_q = None
                self._call_recording_service(self._stop_client)
                try:
                    data_file.close()
                except OSError:
                    pass
                goal_handle.abort()
                result.success = False
                result.message = 'Disk full (ENOSPC)'
                return result

            # Publish feedback at interval
            if now - last_feedback >= feedback_interval:
                fb = RecordIQ.Feedback()
                fb.bytes_written = result_stats['bytes']
                fb.duration_elapsed_s = elapsed
                fb.queue_drops = self._drop_count
                goal_handle.publish_feedback(fb)
                last_feedback = now

            time.sleep(0.05)  # yield executor

        # 9. Signal write thread to stop
        self._write_q = None  # stop _iq_callback from enqueuing
        write_q.put(_STOP_SENTINEL)
        write_thread.join(timeout=5.0)
        if write_thread.is_alive():
            self.get_logger().warning('Write thread did not exit in 5 s.')

        # 10. Stop driver fan-out (best-effort)
        if not self._call_recording_service(self._stop_client):
            self.get_logger().warning(
                'Driver recording/stop service failed; continuing.')

        # 11. Close data file
        try:
            data_file.close()
        except OSError as exc:
            self.get_logger().warning(f'Data file close error: {exc}')

        # 12. Write SigMF metadata
        try:
            meta = self._build_sigmf_meta(
                data_path=data_path,
                center_freq=center_freq,
                sample_rate=sample_rate,
                lna_gain=lna_gain,
                vga_gain=vga_gain,
                trigger_reason=trigger_reason,
                pose=pose,
                capture_timestamp_iso=capture_timestamp,
            )
            meta.tofile(meta_path, skip_validate=True, overwrite=True)
        except Exception as exc:
            self.get_logger().error(f'SigMF meta write failed: {exc}')
            goal_handle.abort()
            result.success = False
            result.message = f'Meta write error: {exc}'
            return result

        # 13. Compute result statistics
        bytes_written = result_stats['bytes']
        samples_written = bytes_written // 2  # ci8 = 2 bytes per sample
        queue_drops = self._drop_count

        self.get_logger().info(
            f'RecordIQ complete: {samples_written} samples, '
            f'{queue_drops} drops → {data_path}')

        # 14. Succeed
        if goal_handle.is_cancel_requested:
            goal_handle.canceled()
        else:
            goal_handle.succeed()

        result.success = True
        result.message = (
            f'{samples_written} samples written, {queue_drops} drops')
        result.sigmf_data_path = data_path
        result.sigmf_meta_path = meta_path
        result.samples_written = samples_written
        result.queue_drops = queue_drops
        return result


def main(args=None):
    """Entry point for iq_recorder_node."""
    rclpy.init(args=args)
    node = IQRecorderNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
