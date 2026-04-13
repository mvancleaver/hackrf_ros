"""Phase 2 autonomy pipeline launch file.

Starts all Phase 2 nodes for headless robot RF autonomy integration:
  1. static_transform_publisher: base_link -> hackrf_antenna (TF)
  2. hackrf_node (LifecycleNode) — driver; auto-configures and activates after 1s
  3. cfar_node — CA-CFAR detector; subscribes to /hackrf/spectrum
  4. sweep_action_node — wideband sweep action server
  5. iq_recorder_node — SigMF IQ recording action server
  6. rf_map_node — RF occupancy grid publisher for Nav2 costmap integration

Usage:
    ros2 launch hackrf_ros autonomy_pipeline.launch.py

Manual lifecycle control (alternative to auto-transition):
    ros2 lifecycle set /hackrf_node configure
    ros2 lifecycle set /hackrf_node activate
"""
import os

from ament_index_python.packages import get_package_share_directory

import launch
import launch.actions
import launch.events

import launch_ros.actions
import launch_ros.events
import launch_ros.events.lifecycle
from launch_ros.actions import LifecycleNode, Node
from launch_ros.event_handlers import OnStateTransition

from lifecycle_msgs.msg import Transition


def generate_launch_description():
    pkg_dir = get_package_share_directory('hackrf_ros')
    params_file = os.path.join(pkg_dir, 'config', 'hackrf_rx.yaml')

    # --- TF: base_link -> hackrf_antenna ---
    hackrf_tf = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='hackrf_tf_publisher',
        arguments=['0', '0', '0', '0', '0', '0', 'base_link', 'hackrf_antenna'],
        output='screen',
    )

    # --- HackRF driver (lifecycle node) ---
    hackrf_node = LifecycleNode(
        package='hackrf_ros',
        executable='hackrf_node',
        name='hackrf_node',
        namespace='',
        parameters=[params_file],
        output='screen',
    )

    # --- CA-CFAR detector ---
    cfar_node = Node(
        package='hackrf_ros',
        executable='cfar_node',
        name='cfar_node',
        output='screen',
        parameters=[{
            'pfa': 1e-4,
            'guard_cells': 8,
            'train_cells': 32,
            'persistence_n': 3,
            'persistence_decay': 5,
        }],
    )

    # --- Wideband sweep action server ---
    sweep_action_node = Node(
        package='hackrf_ros',
        executable='sweep_action_node',
        name='sweep_action_node',
        output='screen',
    )

    # --- SigMF IQ recording action server ---
    iq_recorder_node = Node(
        package='hackrf_ros',
        executable='iq_recorder_node',
        name='iq_recorder_node',
        output='screen',
    )

    # --- RF occupancy grid (Nav2 costmap integration) ---
    rf_map_node = Node(
        package='hackrf_ros',
        executable='rf_map_node',
        name='rf_map_node',
        output='screen',
        parameters=[{
            'grid_resolution': 0.5,
            'grid_width_m': 50.0,
            'grid_height_m': 50.0,
            'grid_frame': 'map',
            'update_rate': 1.0,
        }],
    )

    # --- Auto-configure hackrf_node on startup (1 s delay for registration) ---
    configure_event = launch.actions.EmitEvent(
        event=launch_ros.events.lifecycle.ChangeState(
            lifecycle_node_matcher=launch.events.matches_action(hackrf_node),
            transition_id=Transition.TRANSITION_CONFIGURE,
        ),
    )

    # --- Auto-activate after configure succeeds (node reaches 'inactive') ---
    activate_on_configure = OnStateTransition(
        target_lifecycle_node=hackrf_node,
        goal_state='inactive',
        entities=[
            launch.actions.EmitEvent(
                event=launch_ros.events.lifecycle.ChangeState(
                    lifecycle_node_matcher=launch.events.matches_action(hackrf_node),
                    transition_id=Transition.TRANSITION_ACTIVATE,
                ),
            ),
        ],
    )

    return launch.LaunchDescription([
        hackrf_tf,
        hackrf_node,
        cfar_node,
        sweep_action_node,
        iq_recorder_node,
        rf_map_node,
        launch.actions.RegisterEventHandler(activate_on_configure),
        launch.actions.TimerAction(period=1.0, actions=[configure_event]),
    ])
