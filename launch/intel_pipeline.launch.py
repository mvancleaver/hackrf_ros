"""Phase 4 intelligence pipeline launch file.

Starts:
  1. static_transform_publisher: base_link -> hackrf_antenna (TF)
  2. hackrf_node (LifecycleNode) — driver; auto-configures and activates
  3. cfar_node — CFAR detector + anomaly detection (ADV-01)
  4. emitter_loc_node — RSSI emitter localization (ADV-02)
  5. cyclo_node — cyclostationary feature classifier (ADV-03)
  6. rqt — spectrum display with RF Intel panel

Usage:
    ros2 launch hackrf_ros intel_pipeline.launch.py

With display forwarding from host:
    DISPLAY=:0 ros2 launch hackrf_ros intel_pipeline.launch.py
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

    # TF: base_link → hackrf_antenna static transform
    hackrf_tf = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='hackrf_tf_publisher',
        arguments=['0', '0', '0', '0', '0', '0', 'base_link', 'hackrf_antenna'],
        output='screen',
    )

    # Driver (lifecycle node — auto-transitions via events below)
    hackrf_node = LifecycleNode(
        package='hackrf_ros',
        executable='hackrf_node',
        name='hackrf_node',
        namespace='',
        parameters=[params_file],
        output='screen',
    )

    # CFAR detector + anomaly detection (ADV-01)
    cfar_node = Node(
        package='hackrf_ros',
        executable='cfar_node',
        name='cfar_node',
        output='screen',
        parameters=[{
            'pfa': 1e-4,
            'guard_cells': 8,
            'train_cells': 32,
            'averaging_depth': 16,
            'persistence_n': 3,
            'persistence_decay': 5,
            'anomaly_alpha': 0.05,
            'anomaly_spike_threshold_db': 10.0,
            'anomaly_warmup_s': 30.0,
        }],
    )

    # Emitter localization (ADV-02)
    emitter_loc_node = Node(
        package='hackrf_ros',
        executable='emitter_loc_node',
        name='emitter_loc_node',
        output='screen',
        parameters=[{
            'min_observations': 3,
            'min_separation_m': 0.5,
            'silence_timeout_s': 10.0,
        }],
    )

    # Sweep action server
    sweep_action_node = Node(
        package='hackrf_ros',
        executable='sweep_action_node',
        name='sweep_action_node',
        output='screen',
    )

    # Cyclostationary classifier (ADV-03)
    cyclo_node = Node(
        package='hackrf_ros',
        executable='cyclo_node',
        name='cyclo_node',
        output='screen',
        parameters=[{
            'confidence_threshold': 0.7,
            'ism_min_hz': 2.4e9,
            'ism_max_hz': 2.5e9,
        }],
    )

    # rqt with HackRF Spectrum plugin — launched after driver activates
    rqt_node = launch.actions.ExecuteProcess(
        cmd=[
            'rqt', '--force-discover',
            '-s', 'hackrf_ros/HackRFSpectrum',
        ],
        output='screen',
    )

    # Auto-configure after 1 s
    configure_event = launch.actions.EmitEvent(
        event=launch_ros.events.lifecycle.ChangeState(
            lifecycle_node_matcher=launch.events.matches_action(hackrf_node),
            transition_id=Transition.TRANSITION_CONFIGURE,
        ),
    )

    # Auto-activate after configure succeeds, then launch rqt
    activate_and_rqt = OnStateTransition(
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
            launch.actions.TimerAction(period=2.0, actions=[rqt_node]),
        ],
    )

    return launch.LaunchDescription([
        hackrf_tf,
        hackrf_node,
        cfar_node,
        emitter_loc_node,
        cyclo_node,
        sweep_action_node,
        launch.actions.RegisterEventHandler(activate_and_rqt),
        launch.actions.TimerAction(period=1.0, actions=[configure_event]),
    ])
