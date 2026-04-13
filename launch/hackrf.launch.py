"""Launch HackRF lifecycle node with auto configure -> activate."""
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

    # Belt-and-suspenders TF: publishes even before driver reaches on_configure
    hackrf_tf = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='hackrf_tf_publisher',
        arguments=['0', '0', '0', '0', '0', '0', 'base_link', 'hackrf_antenna'],
        output='screen',
    )

    return launch.LaunchDescription([
        hackrf_node,
        hackrf_tf,
        launch.actions.RegisterEventHandler(activate_on_configure),
        launch.actions.TimerAction(period=1.0, actions=[configure_event]),
    ])
