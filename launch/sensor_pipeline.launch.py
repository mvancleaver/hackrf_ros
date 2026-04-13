"""Phase 1 sensor pipeline launch file.

Starts:
  1. static_transform_publisher: base_link -> hackrf_antenna (belt-and-suspenders TF)
  2. hackrf_node (LifecycleNode) — driver; auto-configures and activates after 1s
  3. cfar_node — CFAR detector; subscribes to /hackrf/spectrum

Usage:
    ros2 launch hackrf_ros sensor_pipeline.launch.py

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

    # Belt-and-suspenders TF: publishes even before driver reaches on_configure
    hackrf_tf = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='hackrf_tf_publisher',
        arguments=['0', '0', '0', '0', '0', '0', 'base_link', 'hackrf_antenna'],
        output='screen',
    )

    hackrf_node = LifecycleNode(
        package='hackrf_ros',
        executable='hackrf_node',
        name='hackrf_node',
        namespace='',
        parameters=[params_file],
        output='screen',
    )

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

    # Auto-configure on startup (1s delay for node to fully register)
    configure_event = launch.actions.EmitEvent(
        event=launch_ros.events.lifecycle.ChangeState(
            lifecycle_node_matcher=launch.events.matches_action(hackrf_node),
            transition_id=Transition.TRANSITION_CONFIGURE,
        ),
    )

    # Auto-activate after configure succeeds (node reaches 'inactive')
    activate_on_configure = OnStateTransition(
        target_lifecycle_node=hackrf_node,
        goal_state='inactive',
        entities=[
            launch.actions.EmitEvent(
                event=launch_ros.events.lifecycle.ChangeState(
                    lifecycle_node_matcher=launch.events.matches_action(
                        hackrf_node),
                    transition_id=Transition.TRANSITION_ACTIVATE,
                ),
            ),
        ],
    )

    return launch.LaunchDescription([
        hackrf_tf,
        hackrf_node,
        cfar_node,
        launch.actions.RegisterEventHandler(activate_on_configure),
        launch.actions.TimerAction(period=1.0, actions=[configure_event]),
    ])
