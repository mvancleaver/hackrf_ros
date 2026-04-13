"""Multi-radio launch file.

Launches N HackRF instances under /hackrf_0 ... /hackrf_{N-1} namespaces.
Each instance reads its own YAML config and is assigned device_index=N.

Usage:
    ros2 launch hackrf_ros multi_radio.launch.py radio_count:=2
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
import os
from ament_index_python.packages import get_package_share_directory


def _make_radio_nodes(context, *args, **kwargs):
    n = int(LaunchConfiguration('radio_count').perform(context))
    pkg_share = get_package_share_directory('hackrf_ros')
    nodes = []
    for i in range(n):
        config_path = os.path.join(pkg_share, 'config', f'hackrf_radio_{i}.yaml')
        nodes.append(Node(
            package='hackrf_ros',
            executable='hackrf_node',
            namespace=f'/hackrf_{i}',
            name='hackrf_node',
            parameters=[config_path, {'device_index': i}],
            output='screen',
        ))
    return nodes


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            'radio_count',
            default_value='2',
            description='Number of HackRF instances to launch',
        ),
        OpaqueFunction(function=_make_radio_nodes),
    ])
