from setuptools import find_packages, setup

package_name = 'hackrf_ros'

setup(
    name=package_name,
    version='1.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml', 'plugin.xml']),
        ('share/' + package_name + '/launch', [
            'launch/hackrf.launch.py',
            'launch/spectrum.launch.py',
            'launch/sensor_pipeline.launch.py',
            'launch/autonomy_pipeline.launch.py',
            'launch/multi_radio.launch.py',
            'launch/intel_pipeline.launch.py',
        ]),
        ('share/' + package_name + '/config', [
            'config/hackrf_rx.yaml',
            'config/hackrf_radio_0.yaml',
            'config/hackrf_radio_1.yaml',
        ]),
    ],
    install_requires=['setuptools', 'scipy>=1.11'],
    zip_safe=True,
    maintainer='root',
    maintainer_email='root@localhost',
    description='ROS2 lifecycle driver for HackRF One SDR with IQ streaming and diagnostics',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'hackrf_node = hackrf_ros.hackrf_lifecycle_node:main',
            'spectrum_node = hackrf_ros.spectrum_node:main',
            'sweep_node = hackrf_ros.sweep_node:main',
            'sweep_action_node = hackrf_ros.sweep_action_node:main',
            'sweep_display = hackrf_ros.sweep_display:main',
            'cfar_node = hackrf_ros.cfar_node:main',
            'iq_recorder_node = hackrf_ros.iq_recorder_node:main',
            'rf_map_node = hackrf_ros.rf_map_node:main',
            'emitter_loc_node = hackrf_ros.emitter_loc_node:main',
            'cyclo_node = hackrf_ros.cyclo_node:main',
        ],
    },
)
