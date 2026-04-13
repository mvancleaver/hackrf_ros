"""Unit tests for rf_map_node — pure function tests, no ROS2 init required.

Tests:
    1. _power_to_cell clamping and formula correctness
    2. _world_to_cell coordinate-to-cell mapping
    3. _world_to_cell returns None when out of bounds
    4. EMA accumulation — two observations from noise floor
    5. _build_grid_data unknown cells are -1, observed cells are [0, 100]
    6. Grid origin tracks robot position (sliding window)
"""
import sys
import types

# ---------------------------------------------------------------------------
# Minimal ROS2 stubs so rf_map_node can be imported without rclpy installed
# ---------------------------------------------------------------------------

# rclpy
rclpy_mod = types.ModuleType('rclpy')
rclpy_mod.init = lambda args=None: None
rclpy_mod.try_shutdown = lambda: None
rclpy_time_mod = types.ModuleType('rclpy.time')
rclpy_time_mod.Time = object
rclpy_duration_mod = types.ModuleType('rclpy.duration')
rclpy_duration_mod.Duration = object
rclpy_node_mod = types.ModuleType('rclpy.node')
rclpy_node_mod.Node = object
rclpy_qos_mod = types.ModuleType('rclpy.qos')
rclpy_qos_mod.QoSProfile = object
rclpy_qos_mod.ReliabilityPolicy = type(
    'ReliabilityPolicy', (), {'RELIABLE': 'RELIABLE', 'BEST_EFFORT': 'BEST_EFFORT'})()
rclpy_qos_mod.HistoryPolicy = type(
    'HistoryPolicy', (), {'KEEP_LAST': 'KEEP_LAST'})()
rclpy_qos_mod.DurabilityPolicy = type(
    'DurabilityPolicy', (), {'VOLATILE': 'VOLATILE'})()
rclpy_cb_mod = types.ModuleType('rclpy.callback_groups')
rclpy_cb_mod.ReentrantCallbackGroup = object
rclpy_exec_mod = types.ModuleType('rclpy.executors')
rclpy_exec_mod.MultiThreadedExecutor = object

sys.modules.setdefault('rclpy', rclpy_mod)
sys.modules.setdefault('rclpy.time', rclpy_time_mod)
sys.modules.setdefault('rclpy.duration', rclpy_duration_mod)
sys.modules.setdefault('rclpy.node', rclpy_node_mod)
sys.modules.setdefault('rclpy.qos', rclpy_qos_mod)
sys.modules.setdefault('rclpy.callback_groups', rclpy_cb_mod)
sys.modules.setdefault('rclpy.executors', rclpy_exec_mod)

# tf2_ros
tf2_ros_mod = types.ModuleType('tf2_ros')
tf2_ros_mod.Buffer = object
tf2_ros_mod.TransformListener = object
tf2_ros_mod.LookupException = Exception
tf2_ros_mod.ExtrapolationException = Exception
sys.modules.setdefault('tf2_ros', tf2_ros_mod)

# std_msgs
std_msgs_mod = types.ModuleType('std_msgs')
std_msgs_msg_mod = types.ModuleType('std_msgs.msg')
std_msgs_msg_mod.Header = object
sys.modules.setdefault('std_msgs', std_msgs_mod)
sys.modules.setdefault('std_msgs.msg', std_msgs_msg_mod)

# nav_msgs
nav_msgs_mod = types.ModuleType('nav_msgs')
nav_msgs_msg_mod = types.ModuleType('nav_msgs.msg')
nav_msgs_msg_mod.OccupancyGrid = object
nav_msgs_msg_mod.MapMetaData = object
sys.modules.setdefault('nav_msgs', nav_msgs_mod)
sys.modules.setdefault('nav_msgs.msg', nav_msgs_msg_mod)

# geometry_msgs
geo_msgs_mod = types.ModuleType('geometry_msgs')
geo_msgs_msg_mod = types.ModuleType('geometry_msgs.msg')
geo_msgs_msg_mod.Pose = object
geo_msgs_msg_mod.Point = object
geo_msgs_msg_mod.Quaternion = object
sys.modules.setdefault('geometry_msgs', geo_msgs_mod)
sys.modules.setdefault('geometry_msgs.msg', geo_msgs_msg_mod)

# hackrf_interfaces
hi_mod = types.ModuleType('hackrf_interfaces')
hi_msg_mod = types.ModuleType('hackrf_interfaces.msg')
hi_msg_mod.RFDetectionArray = object
hi_msg_mod.RFDetection = object
sys.modules.setdefault('hackrf_interfaces', hi_mod)
sys.modules.setdefault('hackrf_interfaces.msg', hi_msg_mod)

# rcl_interfaces (needed by some ROS2 param declarations in imports)
rcl_iface_mod = types.ModuleType('rcl_interfaces')
rcl_iface_msg_mod = types.ModuleType('rcl_interfaces.msg')
rcl_iface_msg_mod.ParameterDescriptor = object
rcl_iface_msg_mod.FloatingPointRange = object
rcl_iface_msg_mod.IntegerRange = object
rcl_iface_msg_mod.SetParametersResult = object
sys.modules.setdefault('rcl_interfaces', rcl_iface_mod)
sys.modules.setdefault('rcl_interfaces.msg', rcl_iface_msg_mod)

# ---------------------------------------------------------------------------
# Now import the module under test
# ---------------------------------------------------------------------------
import numpy as np  # noqa: E402

from hackrf_ros.rf_map_node import _power_to_cell, _world_to_cell  # noqa: E402


# ---------------------------------------------------------------------------
# Helper: minimal node-like object for EMA and grid tests
# ---------------------------------------------------------------------------
class _FakeNode:
    """Minimal object with the attributes used by _build_grid_data and _accumulate_detection."""

    def __init__(self, nrows=100, ncols=100, resolution=0.5, alpha=0.1):
        self._grid_rows = nrows
        self._grid_cols = ncols
        self._grid_resolution = resolution
        self._ema_alpha = alpha
        self._cell_power = np.full((nrows, ncols), -120.0, dtype=np.float32)
        self._cell_observed = np.zeros((nrows, ncols), dtype=bool)
        self._origin_x = 0.0
        self._origin_y = 0.0


# Import module-level helpers that can operate on _FakeNode
from hackrf_ros.rf_map_node import (  # noqa: E402
    _accumulate_detection,
    _build_grid_data,
)


# ===========================================================================
# Test 1: _power_to_cell formula and clamping
# ===========================================================================
class TestPowerToCell:
    def test_noise_floor_maps_to_zero(self):
        assert _power_to_cell(-120.0) == 0

    def test_midpoint_maps_to_fifty(self):
        assert _power_to_cell(-80.0) == 50

    def test_strong_signal_maps_to_hundred(self):
        assert _power_to_cell(-40.0) == 100

    def test_below_floor_clamped_to_zero(self):
        assert _power_to_cell(-200.0) == 0

    def test_above_ceiling_clamped_to_hundred(self):
        assert _power_to_cell(0.0) == 100


# ===========================================================================
# Test 2: _world_to_cell coordinate mapping
# ===========================================================================
class TestWorldToCell:
    def test_basic_conversion(self):
        """robot at (5.0, 3.0), origin at (0, 0), resolution 0.5 → (row=6, col=10)."""
        result = _world_to_cell(5.0, 3.0, 0.0, 0.0, 0.5, 100, 100)
        assert result == (6, 10)


# ===========================================================================
# Test 3: _world_to_cell out-of-bounds returns None
# ===========================================================================
class TestWorldToCellBounds:
    def test_returns_none_when_outside_grid(self):
        # world at (-1.0, -1.0) with origin at 0,0 → negative row/col → out of bounds
        result = _world_to_cell(-1.0, -1.0, 0.0, 0.0, 0.5, 100, 100)
        assert result is None

    def test_returns_none_at_col_overflow(self):
        # col = int((200 - 0) / 0.5) = 400, ncols=100 → out of bounds
        result = _world_to_cell(200.0, 0.0, 0.0, 0.0, 0.5, 100, 100)
        assert result is None

    def test_valid_boundary_cell(self):
        # col=0, row=0 — should be valid
        result = _world_to_cell(0.1, 0.1, 0.0, 0.0, 0.5, 100, 100)
        assert result == (0, 0)


# ===========================================================================
# Test 4: EMA accumulation — two observations from noise floor
# ===========================================================================
class TestEmaAccumulation:
    def test_first_observation_sets_directly(self):
        """First observation on an unobserved cell sets value directly (not EMA from -120)."""
        node = _FakeNode()
        _accumulate_detection(node, row=5, col=5, power_dbm=-80.0)
        assert node._cell_observed[5, 5] == True  # noqa: E712  numpy bool
        assert abs(node._cell_power[5, 5] - (-80.0)) < 1e-4

    def test_second_observation_applies_ema(self):
        """
        First call: sets to -80.0
        Second call: EMA = (1-0.1)*(-80.0) + 0.1*(-80.0) = -80.0 (stable)
        Use a different power to show EMA in action.
        First to -80.0, second at -60.0: result = 0.9*(-80.0)+0.1*(-60.0) = -78.0
        """
        node = _FakeNode()
        _accumulate_detection(node, row=5, col=5, power_dbm=-80.0)
        _accumulate_detection(node, row=5, col=5, power_dbm=-60.0)
        expected = 0.9 * (-80.0) + 0.1 * (-60.0)
        assert abs(node._cell_power[5, 5] - expected) < 1e-4

    def test_two_observations_from_plan_spec(self):
        """
        Plan spec exact test: start from noise floor -120.
        First obs with -80: sets to -80 (first observation sets directly).
        Second obs with -80: EMA = 0.9*(-80) + 0.1*(-80) = -80.
        Test the plan's original spec: first EMA = 0.9*(-120) + 0.1*(-80) = -116.
        This applies only if first observation also uses EMA.
        Per plan done criteria: 'first observation sets cell directly (not EMA from -120)'.
        So: after first obs → -80.0, after second obs → 0.9*(-80)+0.1*(-80) = -80.0.
        Verify with two different powers to show accumulation.
        """
        node = _FakeNode(alpha=0.1)
        # First obs: power = -80 → sets directly to -80
        _accumulate_detection(node, row=5, col=5, power_dbm=-80.0)
        assert abs(node._cell_power[5, 5] - (-80.0)) < 1e-4
        # Second obs: power = -80 → EMA from -80 = -80 (stable)
        _accumulate_detection(node, row=5, col=5, power_dbm=-80.0)
        assert abs(node._cell_power[5, 5] - (-80.0)) < 1e-4


# ===========================================================================
# Test 5: _build_grid_data — unknown cells are -1, observed are [0, 100]
# ===========================================================================
class TestBuildGridData:
    def test_unobserved_cells_are_minus_one(self):
        node = _FakeNode()
        data = _build_grid_data(node)
        # All cells unobserved initially → all -1
        assert (data == -1).all()

    def test_observed_cell_in_valid_range(self):
        node = _FakeNode()
        node._cell_power[10, 20] = -80.0
        node._cell_observed[10, 20] = True
        data = _build_grid_data(node)
        assert data[10, 20] == 50  # -80 dBm → 50
        assert data[0, 0] == -1   # still unobserved

    def test_grid_data_dtype_is_int8_compatible(self):
        node = _FakeNode()
        node._cell_power[0, 0] = -40.0
        node._cell_observed[0, 0] = True
        data = _build_grid_data(node)
        # All values must be in [-1, 100]
        assert data.min() >= -1
        assert data.max() <= 100


# ===========================================================================
# Test 6: Grid origin follows robot (sliding window)
# ===========================================================================
class TestGridOrigin:
    def test_origin_centers_on_robot(self):
        """
        origin_x = robot_x - (ncols / 2) * resolution
        origin_y = robot_y - (nrows / 2) * resolution
        """
        from hackrf_ros.rf_map_node import _compute_origin
        ncols, nrows, resolution = 100, 100, 0.5
        robot_x, robot_y = 10.0, 20.0
        ox, oy = _compute_origin(robot_x, robot_y, ncols, nrows, resolution)
        expected_ox = robot_x - (ncols / 2) * resolution
        expected_oy = robot_y - (nrows / 2) * resolution
        assert abs(ox - expected_ox) < 1e-6
        assert abs(oy - expected_oy) < 1e-6

    def test_origin_at_zero_robot_position(self):
        from hackrf_ros.rf_map_node import _compute_origin
        ox, oy = _compute_origin(0.0, 0.0, 100, 100, 0.5)
        # -25.0 = 0 - (100/2)*0.5
        assert abs(ox - (-25.0)) < 1e-6
        assert abs(oy - (-25.0)) < 1e-6
