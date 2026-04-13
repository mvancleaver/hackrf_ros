"""RF Occupancy Map Node.

Subscribes to /hackrf/detections (RFDetectionArray, RELIABLE) and accumulates
signal power observations per grid cell using an exponential moving average (EMA).
Publishes a nav_msgs/OccupancyGrid on /hackrf/rf_occupancy at a configurable rate
(default 1 Hz) for Nav2 costmap integration.

Grid cell value formula (D-12):
    cell_value = int(clamp((power_dbm + 120) / 80 * 100, 0, 100))
    -120 dBm -> 0  (noise floor)
    -80  dBm -> 50
    -40  dBm -> 100 (strong signal)

EMA accumulation:
    First observation: cell_power[r, c] = power_dbm
    Subsequent:        cell_power[r, c] = (1 - alpha) * cell_power[r, c]
                                          + alpha * power_dbm

Sliding window: grid origin follows robot position so the robot is always at
the centre of the grid (re-centred on every detection callback).

TF requirement: base_link -> grid_frame must be available. Updates are silently
dropped if the TF lookup fails (D-14).
"""
from __future__ import annotations

import numpy as np

import rclpy
import rclpy.time
import rclpy.duration
from rclpy.node import Node
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

import tf2_ros

from nav_msgs.msg import OccupancyGrid
from std_msgs.msg import Header

from hackrf_interfaces.msg import RFDetectionArray


# ---------------------------------------------------------------------------
# Module-level pure functions (imported directly by tests — no Node needed)
# ---------------------------------------------------------------------------

def _power_to_cell(power_dbm: float) -> int:
    """Map dBm power to OccupancyGrid cell value in [0, 100].

    -120 dBm -> 0 (noise floor)
    -80  dBm -> 50
    -40  dBm -> 100 (strong signal)
    Values outside range are clamped.
    """
    return int(max(0, min(100, (power_dbm + 120.0) / 80.0 * 100.0)))


def _world_to_cell(
    wx: float,
    wy: float,
    origin_x: float,
    origin_y: float,
    resolution: float,
    nrows: int,
    ncols: int,
) -> tuple[int, int] | None:
    """Convert world coordinates to grid (row, col) indices.

    Returns (row, col) if the cell is within grid bounds, else None.

    Args:
        wx: World x coordinate (metres).
        wy: World y coordinate (metres).
        origin_x: Grid origin x (metres) — world position of cell (0, 0).
        origin_y: Grid origin y (metres) — world position of cell (0, 0).
        resolution: Metres per cell.
        nrows: Number of grid rows.
        ncols: Number of grid columns.
    """
    col = int((wx - origin_x) / resolution)
    row = int((wy - origin_y) / resolution)
    if 0 <= row < nrows and 0 <= col < ncols:
        return (row, col)
    return None


def _compute_origin(
    robot_x: float,
    robot_y: float,
    ncols: int,
    nrows: int,
    resolution: float,
) -> tuple[float, float]:
    """Compute grid origin so the robot sits at the centre of the grid.

    Returns (origin_x, origin_y) in world metres.
    """
    origin_x = robot_x - (ncols / 2.0) * resolution
    origin_y = robot_y - (nrows / 2.0) * resolution
    return origin_x, origin_y


def _accumulate_detection(node: object, row: int, col: int, power_dbm: float) -> None:
    """Apply EMA accumulation for a single detection at grid cell (row, col).

    First observation sets the cell value directly. Subsequent observations
    apply EMA with node._ema_alpha.

    Modifies node._cell_power and node._cell_observed in place.
    """
    if not node._cell_observed[row, col]:
        node._cell_power[row, col] = power_dbm
        node._cell_observed[row, col] = True
    else:
        alpha = node._ema_alpha
        node._cell_power[row, col] = (
            (1.0 - alpha) * node._cell_power[row, col] + alpha * power_dbm
        )


def _build_grid_data(node: object) -> np.ndarray:
    """Build int16 grid data array from node state.

    Returns a 2-D numpy array (int16) with values:
        -1   for unobserved cells (OccupancyGrid unknown convention)
        0-100 for observed cells mapped from power_dbm via _power_to_cell
    """
    data = np.full((node._grid_rows, node._grid_cols), -1, dtype=np.int16)
    mask = node._cell_observed
    # Vectorised mapping for observed cells (otypes required for empty-mask case)
    if mask.any():
        data[mask] = np.vectorize(_power_to_cell, otypes=[np.int16])(
            node._cell_power[mask]
        )
    return data


# ---------------------------------------------------------------------------
# RFMapNode
# ---------------------------------------------------------------------------

class RFMapNode(Node):
    """Accumulates RF detections into a sliding OccupancyGrid and publishes it.

    Parameters:
        grid_resolution (float, default 0.5): Metres per cell.
        grid_width_m    (float, default 50.0): Total grid width in metres.
        grid_height_m   (float, default 50.0): Total grid height in metres.
        grid_frame      (str,   default 'map'): TF frame for grid origin.
        update_rate     (float, default 1.0):  OccupancyGrid publish rate in Hz.
        ema_alpha       (float, default 0.1):  EMA smoothing factor.
    """

    def __init__(self) -> None:
        super().__init__('rf_map_node')

        # --- Declare parameters ---
        self.declare_parameter('grid_resolution', 0.5)
        self.declare_parameter('grid_width_m', 50.0)
        self.declare_parameter('grid_height_m', 50.0)
        self.declare_parameter('grid_frame', 'map')
        self.declare_parameter('update_rate', 1.0)
        self.declare_parameter('ema_alpha', 0.1)

        # --- Read parameters ---
        self._grid_resolution = self.get_parameter(
            'grid_resolution').get_parameter_value().double_value
        self._grid_width_m = self.get_parameter(
            'grid_width_m').get_parameter_value().double_value
        self._grid_height_m = self.get_parameter(
            'grid_height_m').get_parameter_value().double_value
        self._grid_frame = self.get_parameter(
            'grid_frame').get_parameter_value().string_value
        self._update_rate = self.get_parameter(
            'update_rate').get_parameter_value().double_value
        self._ema_alpha = self.get_parameter(
            'ema_alpha').get_parameter_value().double_value

        # --- Derive grid dimensions ---
        self._grid_cols = int(self._grid_width_m / self._grid_resolution)
        self._grid_rows = int(self._grid_height_m / self._grid_resolution)

        # --- Grid state ---
        self._cell_power = np.full(
            (self._grid_rows, self._grid_cols), -120.0, dtype=np.float32)
        self._cell_observed = np.zeros(
            (self._grid_rows, self._grid_cols), dtype=bool)

        # --- Grid origin (world coords of cell [0,0]) ---
        self._origin_x: float = 0.0
        self._origin_y: float = 0.0
        self._last_robot_x: float = 0.0
        self._last_robot_y: float = 0.0

        # --- TF ---
        self._tf_buffer = tf2_ros.Buffer()
        self._tf_listener = tf2_ros.TransformListener(self._tf_buffer, self)

        # --- Callback group for concurrent callbacks ---
        self._cb_group = ReentrantCallbackGroup()

        # --- Subscriber: /hackrf/detections (RELIABLE) ---
        qos_reliable = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )
        self._detections_sub = self.create_subscription(
            RFDetectionArray,
            '/hackrf/detections',
            self._detections_callback,
            qos_reliable,
            callback_group=self._cb_group,
        )

        # --- Publisher: /hackrf/rf_occupancy (RELIABLE) ---
        qos_pub = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )
        self._grid_pub = self.create_publisher(
            OccupancyGrid,
            '/hackrf/rf_occupancy',
            qos_pub,
        )

        # --- Timer for periodic grid publish ---
        timer_period = 1.0 / self._update_rate
        self._pub_timer = self.create_timer(
            timer_period,
            self._publish_grid,
            callback_group=self._cb_group,
        )

        self.get_logger().info(
            f'RFMapNode started: {self._grid_rows}x{self._grid_cols} grid '
            f'({self._grid_height_m}m x {self._grid_width_m}m @ '
            f'{self._grid_resolution}m/cell), '
            f'frame={self._grid_frame}, rate={self._update_rate} Hz, '
            f'alpha={self._ema_alpha}'
        )

    # ------------------------------------------------------------------
    # Detection callback
    # ------------------------------------------------------------------

    def _detections_callback(self, msg: RFDetectionArray) -> None:
        """Process an RFDetectionArray message and update the grid."""
        # --- TF lookup: base_link -> grid_frame ---
        try:
            t = self._tf_buffer.lookup_transform(
                self._grid_frame,
                'base_link',
                rclpy.time.Time(),
                timeout=rclpy.duration.Duration(seconds=0.1),
            )
            robot_x = t.transform.translation.x
            robot_y = t.transform.translation.y
        except (tf2_ros.LookupException, tf2_ros.ExtrapolationException):
            # Drop update silently — D-14
            return

        # --- Sliding window: re-centre grid on current robot position ---
        origin_x, origin_y = _compute_origin(
            robot_x, robot_y, self._grid_cols, self._grid_rows, self._grid_resolution
        )
        self._origin_x = origin_x
        self._origin_y = origin_y
        self._last_robot_x = robot_x
        self._last_robot_y = robot_y

        # --- Accumulate each detection at the robot's current position ---
        # A single HackRF has no direction-of-arrival capability; the best
        # spatial anchor is the robot's own position (Phase 2 deliberate scope).
        for detection in msg.detections:
            cell = _world_to_cell(
                robot_x, robot_y,
                origin_x, origin_y,
                self._grid_resolution,
                self._grid_rows, self._grid_cols,
            )
            if cell is None:
                continue
            row, col = cell
            _accumulate_detection(self, row, col, detection.power_dbm)

    # ------------------------------------------------------------------
    # Grid publish callback
    # ------------------------------------------------------------------

    def _publish_grid(self) -> None:
        """Build and publish the OccupancyGrid message."""
        msg = OccupancyGrid()
        msg.header = Header()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self._grid_frame

        msg.info.resolution = self._grid_resolution
        msg.info.width = self._grid_cols
        msg.info.height = self._grid_rows
        msg.info.origin.position.x = self._origin_x
        msg.info.origin.position.y = self._origin_y
        msg.info.origin.position.z = 0.0
        msg.info.origin.orientation.w = 1.0

        grid_data = _build_grid_data(self)
        msg.data = grid_data.flatten().tolist()

        self._grid_pub.publish(msg)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args=None) -> None:
    """Start the RFMapNode."""
    rclpy.init(args=args)
    node = RFMapNode()
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
