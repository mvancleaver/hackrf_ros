"""Launch HackRF driver + spectrum display (separate processes, same container)."""
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

    hackrf_node = LifecycleNode(
        package='hackrf_ros',
        executable='hackrf_node',
        name='hackrf_node',
        namespace='',
        parameters=[params_file],
        output='screen',
    )

    spectrum_node = Node(
        package='hackrf_ros',
        executable='spectrum_node',
        name='spectrum_display',
        namespace='',
        output='screen',
    )

    configure_event = launch.actions.EmitEvent(
        event=launch_ros.events.lifecycle.ChangeState(
            lifecycle_node_matcher=launch.events.matches_action(hackrf_node),
            transition_id=Transition.TRANSITION_CONFIGURE,
        ),
    )

    # On configure success: activate + start spectrum display
    activate_and_display = OnStateTransition(
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
            # Start spectrum display after a brief delay
            launch.actions.TimerAction(
                period=2.0, actions=[spectrum_node]),
        ],
    )

    return launch.LaunchDescription([
        hackrf_node,
        launch.actions.RegisterEventHandler(activate_and_display),
        launch.actions.TimerAction(period=1.0, actions=[configure_event]),
    ])
