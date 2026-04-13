"""Emitter localization node (ADV-02).

Subscribes to /hackrf/detections, accumulates (robot_pose, RSSI) observations
per detection_id via TF2 lookups, and publishes /hackrf/emitter_map when
sufficient spatially-spread observations are available.

Model: RSSI = RSSI_0 - 10*n*log10(d)  [log-distance path loss, D-05]
Solver: scipy.optimize.minimize with Nelder-Mead [research rec, nonlinear problem]
"""
from __future__ import annotations

import time
from typing import Optional

import numpy as np
from scipy.optimize import minimize

import rclpy
import rclpy.time
import rclpy.duration
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor

import tf2_ros
from tf2_ros import LookupException, ExtrapolationException

from hackrf_interfaces.msg import RFDetectionArray, RFEmitterMap, RFEmitterEstimate


# ---------------------------------------------------------------------------
# Pure helper functions (importable by tests without ROS context)
# ---------------------------------------------------------------------------

def _poses_well_spread(
    poses: list[tuple[float, float]],
    min_sep_m: float = 0.5,
) -> bool:
    """Check that a list of (x, y) poses is non-collinear and spatially spread.

    Rejects:
    - Fewer than 3 poses
    - All poses closer than min_sep_m from each other
    - Collinear poses (smallest singular value near zero)

    Uses SVD collinearity detection. Pattern from emitter localization research.
    [VERIFIED: np.linalg.svd on this system]
    """
    if len(poses) < 3:
        return False

    P = np.array(poses, dtype=np.float64)

    # Max pairwise distance must exceed min_sep_m
    max_dist = 0.0
    for i in range(len(P)):
        for j in range(i + 1, len(P)):
            d = float(np.linalg.norm(P[i] - P[j]))
            if d > max_dist:
                max_dist = d
    if max_dist < min_sep_m:
        return False

    # SVD collinearity check: smallest singular value must exceed threshold
    centered = P - P.mean(axis=0)
    sv = np.linalg.svd(centered, compute_uv=False)
    # sv[-1] is the smallest singular value; ~0 means collinear
    return float(sv[-1]) > min_sep_m * 0.3


def _estimate_emitter(
    observations: list[tuple[float, float, float]],
) -> Optional[tuple[float, float, float, float, float, float]]:
    """Estimate emitter position from (robot_x, robot_y, rssi_dbm) observations.

    Uses Nelder-Mead nonlinear least-squares on the log-distance path loss model:
        RSSI_predicted = RSSI_0 - 10*n*log10(distance)

    Multi-start: tries n_init in [2.0, 2.5, 3.0] from strongest-RSSI pose.
    Cost function applies soft penalty for n outside [1.5, 6.0] to keep
    Nelder-Mead in the physically meaningful region (bounds not natively
    supported by Nelder-Mead; penalty achieves the same effect).
    Returns the best converged result with n in [1.5, 6.0].

    Rejects solutions with high residuals (sum_sq > 100 * n_obs indicating
    >10 dB RMS error per observation — suggests degenerate geometry or
    inconsistent measurements).

    Initial guess: emitter at robot pose with strongest RSSI, n=2.0.

    Args:
        observations: list of (robot_x, robot_y, rssi_dbm)

    Returns:
        (xe, ye, rssi0, n, cov_xx, cov_yy) or None if optimization failed/unreliable.
    """
    if len(observations) < 3:
        return None

    xs = np.array([o[0] for o in observations])
    ys = np.array([o[1] for o in observations])
    rssi_vals = np.array([o[2] for o in observations])

    # Heuristic initial guess: RSSI-weighted centroid of robot poses as emitter candidate.
    # Stronger RSSI → robot is closer to emitter → centre of mass of strong-signal poses.
    rssi_shifted = rssi_vals - rssi_vals.min() + 1.0
    weights = rssi_shifted / rssi_shifted.sum()
    centroid_x = float(np.sum(weights * xs))
    centroid_y = float(np.sum(weights * ys))
    rssi_max = float(rssi_vals.max())

    def cost(params: list[float]) -> float:
        """Cost function with soft penalty to keep n in physical range [1.5, 6.0]."""
        xe, ye, rssi0, n = params
        # Soft barrier penalty for unphysical path loss exponent
        penalty = 0.0
        if n < 1.5:
            penalty = 1e6 * (1.5 - n) ** 2
        elif n > 6.0:
            penalty = 1e6 * (n - 6.0) ** 2
        total = penalty
        for rx, ry, rssi_obs in observations:
            d = np.sqrt((rx - xe) ** 2 + (ry - ye) ** 2)
            d = max(d, 0.1)  # avoid log(0), prevents degenerate d=0 minimum
            rssi_pred = rssi0 - 10.0 * n * np.log10(d)
            total += (rssi_obs - rssi_pred) ** 2
        return total

    # Multi-start: try several (position, n) seeds to escape local minima.
    # Includes centroid and midpoints between centroid and per-robot poses.
    candidate_positions = [
        (centroid_x, centroid_y),
        (centroid_x, centroid_y + 2.0),
        (centroid_x, centroid_y - 2.0),
    ]
    # Also try the midpoint between each robot pose and the centroid
    for rx, ry in zip(xs, ys):
        candidate_positions.append(((rx + centroid_x) / 2, (ry + centroid_y) / 2))

    best_result = None
    for xe0, ye0 in candidate_positions:
        for n_init in [2.0, 2.5, 3.0]:
            x0 = [xe0, ye0, rssi_max, n_init]
            res = minimize(
                cost,
                x0=x0,
                method='Nelder-Mead',
                options={'xatol': 0.05, 'fatol': 0.05, 'maxiter': 5000},
            )
            xe_r, ye_r, rssi0_r, n_r = res.x
            if 1.5 <= n_r <= 6.0:
                if best_result is None or res.fun < best_result.fun:
                    best_result = res

    if best_result is None:
        return None

    xe, ye, rssi0, n = best_result.x

    # Reject physically unreasonable path loss exponent (final guard)
    if not (1.5 <= n <= 6.0):
        return None

    # Reject high-residual solutions (>10 dB RMS across observations = unreliable)
    n_obs = len(observations)
    if best_result.fun > 100.0 * n_obs:
        return None

    # Position covariance via Cramér-Rao approximation for RSSI-based positioning.
    # sigma_pos ~ (ln(10)/10) * (sigma_rssi_db / n_path_loss) * d_mean
    # This propagates RSSI uncertainty through the log-distance path loss Jacobian
    # to give position uncertainty in m² (the correct units for a covariance field).
    # Previously sigma2 was in dB² which is dimensionally incorrect for m².
    import math as _math
    sigma_rssi_db = _math.sqrt(max(best_result.fun / max(n_obs - 2, 1), 1e-10))
    ln10_over_10 = _math.log(10.0) / 10.0  # ≈ 0.2303
    dists = [
        max(_math.sqrt((ox - xe) ** 2 + (oy - ye) ** 2), 0.1)
        for ox, oy, _ in observations
    ]
    d_mean = float(np.mean(dists))
    cov_pos = (ln10_over_10 * sigma_rssi_db / n * d_mean) ** 2
    cov_xx = float(cov_pos)
    cov_yy = float(cov_pos)

    return float(xe), float(ye), float(rssi0), float(n), cov_xx, cov_yy


# ---------------------------------------------------------------------------
# ROS2 Node
# ---------------------------------------------------------------------------

class EmitterLocNode(Node):
    """Emitter localization node.

    Subscribes to /hackrf/detections (RFDetectionArray).
    Looks up robot pose via TF2 at detection time.
    Accumulates observations per detection_id.
    Publishes /hackrf/emitter_map (RFEmitterMap) at 1 Hz.
    Removes stale estimates after silence_timeout_s (D-08).
    """

    def __init__(self) -> None:
        """Initialize node, parameters, TF2, pub/sub, and timer."""
        super().__init__('emitter_loc_node')

        # Parameters (D-07, D-08)
        self.declare_parameter('min_observations', 3)
        self.declare_parameter('min_separation_m', 0.5)
        self.declare_parameter('silence_timeout_s', 10.0)
        self.declare_parameter('map_frame', 'map')
        self.declare_parameter('robot_frame', 'base_link')

        # TF2 buffer and listener (pattern from rf_map_node.py)
        self._tf_buffer = tf2_ros.Buffer()
        self._tf_listener = tf2_ros.TransformListener(self._tf_buffer, self)

        cb_group = ReentrantCallbackGroup()

        qos_reliable = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )

        # Subscriber: detections
        self._det_sub = self.create_subscription(
            RFDetectionArray,
            '/hackrf/detections',
            self._detections_callback,
            qos_reliable,
            callback_group=cb_group,
        )

        # Publisher: emitter map
        self._map_pub = self.create_publisher(
            RFEmitterMap,
            '/hackrf/emitter_map',
            qos_reliable,
        )

        # State: observations per detection_id
        # Format: {detection_id: {'obs': [(x, y, rssi)], 'last_seen': float}}
        self._observations: dict[int, dict] = {}

        # 1 Hz publish timer
        self.create_timer(1.0, self._publish_map, callback_group=cb_group)

        self.get_logger().info('EmitterLocNode started')

    def _detections_callback(self, msg: RFDetectionArray) -> None:
        """Process detection array, accumulate observations per detection_id."""
        map_frame = self.get_parameter('map_frame').value
        robot_frame = self.get_parameter('robot_frame').value

        # Look up robot pose in map frame at detection time
        try:
            # Non-blocking lookup (timeout=0): drop the detection immediately if
            # TF is not yet available rather than blocking the executor thread.
            # A 50 ms blocking lookup on a RELIABLE callback wastes an executor
            # thread slot during TF initialization (M-ROS-7 fix).
            tf_stamped = self._tf_buffer.lookup_transform(
                map_frame, robot_frame,
                rclpy.time.Time(),
                timeout=rclpy.duration.Duration(seconds=0),
            )
        except (LookupException, ExtrapolationException) as e:
            self.get_logger().warn(f'TF lookup failed: {e}', throttle_duration_sec=30.0)
            return

        robot_x = tf_stamped.transform.translation.x
        robot_y = tf_stamped.transform.translation.y
        now = time.monotonic()

        for det in msg.detections:
            det_id = det.detection_id
            rssi = float(det.power_dbm)
            if det_id not in self._observations:
                self._observations[det_id] = {'obs': [], 'last_seen': now}
            entry = self._observations[det_id]
            entry['last_seen'] = now
            # Append observation (deduplicate by proximity — if robot barely moved, skip)
            obs = entry['obs']
            if obs:
                last_x, last_y, _ = obs[-1]
                sep = np.sqrt((robot_x - last_x) ** 2 + (robot_y - last_y) ** 2)
                min_sep = float(self.get_parameter('min_separation_m').value)
                if sep < min_sep * 0.3:
                    # Robot has not moved enough — update RSSI of last entry instead
                    obs[-1] = (robot_x, robot_y, rssi)
                    continue  # skip to next detection, do NOT exit the callback
            obs.append((robot_x, robot_y, rssi))

    def _publish_map(self) -> None:
        """Publish /hackrf/emitter_map with current estimates."""
        min_obs = int(self.get_parameter('min_observations').value)
        min_sep = float(self.get_parameter('min_separation_m').value)
        silence_s = float(self.get_parameter('silence_timeout_s').value)
        now = time.monotonic()

        # Expire stale detection IDs (D-08)
        stale = [
            did for did, entry in self._observations.items()
            if now - entry['last_seen'] > silence_s
        ]
        for did in stale:
            del self._observations[did]
            self.get_logger().info(f'Expired emitter estimate for detection_id={did}')

        estimates: list[RFEmitterEstimate] = []
        for det_id, entry in self._observations.items():
            obs = entry['obs']
            if len(obs) < min_obs:
                continue
            poses = [(x, y) for x, y, _ in obs]
            if not _poses_well_spread(poses, min_sep_m=min_sep):
                continue
            result = _estimate_emitter(obs)
            if result is None:
                continue
            xe, ye, rssi0, n, cov_xx, cov_yy = result

            est = RFEmitterEstimate()
            est.detection_id = int(det_id)
            est.estimated_x = xe
            est.estimated_y = ye
            est.covariance_xx = cov_xx
            est.covariance_yy = cov_yy
            est.observation_count = len(obs)
            est.last_seen = self.get_clock().now().to_msg()
            estimates.append(est)

        emitter_map = RFEmitterMap()
        emitter_map.header.stamp = self.get_clock().now().to_msg()
        emitter_map.header.frame_id = self.get_parameter('map_frame').value
        emitter_map.estimates = estimates
        self._map_pub.publish(emitter_map)


def main(args=None):
    """Entry point for emitter_loc_node."""
    rclpy.init(args=args)
    node = EmitterLocNode()
    # MultiThreadedExecutor required — node uses ReentrantCallbackGroup
    # (Phase 2 D-17, CONTEXT.md canonical refs; rclpy.spin() uses
    # SingleThreadedExecutor which would block the publish timer during
    # TF2 lookups in the detections callback)
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
