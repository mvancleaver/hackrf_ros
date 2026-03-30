from setuptools import find_packages, setup

package_name = 'hackrf_ros'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools', 'pyserial>=3.5', 'redis>=7.4.0', 'hiredis>=3.3.1'],
    zip_safe=True,
    maintainer='root',
    maintainer_email='root@localhost',
    description='ROS2 driver for HackRF One SDR with IQ streaming and Mayhem serial control',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'hackrf_node = hackrf_ros.bridge_node:main',
            'iq_plotter_node = hackrf_ros.iq_plotter_node:main'
        ],
    },
)
