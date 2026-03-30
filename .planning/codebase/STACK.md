# Technology Stack

**Analysis Date:** 2026-03-29

## Languages

**Primary:**
- Python 3 - Primary language for ROS 2 nodes and application logic

**System:**
- C/C++ - Underlying HackRF driver library (libhackrf)

## Runtime

**Environment:**
- ROS 2 Humble - Robot Operating System 2, Humble distribution

**Package Manager:**
- pip - Python package manager for dependencies
- Colcon - ROS 2 build system for workspace management
- Ament - ROS 2 build system framework

## Frameworks

**Core:**
- ROS 2 (rclpy) - ROS 2 Python client library for node creation, messaging, and parameter management
  - Location: Base system, used throughout `hackrf_ros/hackrf_node.py` and `hackrf_ros/iq_plotter_node.py`

**Signal Processing:**
- NumPy - Numerical computing library for IQ sample processing and array operations
- Matplotlib - Data visualization library for real-time plotting of IQ data, constellation plots, and spectral analysis

**Hardware Interfacing:**
- pyhackrf2 - Python bindings for libhackrf (HackRF One hardware abstraction)

**UI:**
- PyQt6 - Qt binding for Python, used for GUI components (referenced in Dockerfile)

**Testing:**
- pytest - Python testing framework
- ament_flake8 - ROS 2 linting tool for code style checks (used in `test/test_flake8.py`)
- ament_lint_common - Common ROS 2 linting tools

**Build/Dev:**
- colcon build - ROS 2 workspace builder
- ament_python - Ament build system for Python packages

## Key Dependencies

**Critical:**
- rclpy - ROS 2 Python client library for creating nodes and handling IPC
  - Used in: `hackrf_ros/hackrf_node.py`, `hackrf_ros/iq_plotter_node.py`
- pyhackrf2 - Python wrapper for libhackrf C library
  - Used in: `hackrf_ros/hackrf_node.py` for hardware control
  - Installation: `python3 -m pip install pyhackrf2`
- numpy - Core scientific computing for signal processing
  - Used in: `hackrf_ros/hackrf_node.py` (complex64 arrays), `hackrf_ros/iq_plotter_node.py` (FFT operations)
  - Installation: `python3 -m pip install numpy`

**Infrastructure:**
- std_msgs - ROS 2 standard message types (Float32MultiArray)
  - Used for IQ data transmission between nodes
- rcl_interfaces - ROS 2 message interfaces for parameter configuration
  - Used for parameter descriptors and parameter change handling
- matplotlib - Live plotting of RF signal analysis
  - Used in: `hackrf_ros/iq_plotter_node.py`
  - Installation: `python3 -m pip install matplotlib`
- numpy-quaternion - Quaternion mathematics library
  - Installation: `python3 -m pip install numpy-quaternion`

## Configuration

**Environment:**
- ROS 2 setup scripts sourced in Docker container
  - Main ROS distro: `/opt/ros/$ROS_DISTRO/setup.bash`
  - Workspace setup: `/ros2_ws/install/local_setup.bash`
  - HackRF workspace setup: `/hackrf_ws/install/local_setup.bash`

**Build:**
- `setup.py` - Python setuptools configuration at `/home/user/dev_ws/hackrf_ros/setup.py`
  - Defines package name: `hackrf_ros`
  - Entry points (console scripts):
    - `hackrf_node = hackrf_ros.hackrf_node:main`
    - `iq_plotter_node = hackrf_ros.iq_plotter_node:main`
  - Includes standard ROS 2 ament integration
- `package.xml` - ROS 2 package manifest at `/home/user/dev_ws/hackrf_ros/package.xml`
  - Build system: ament_python
  - Core dependencies: rclpy, rclcpp, std_msgs
  - Interface generation: rosidl_default_generators
- `setup.cfg` - Setuptools configuration at `/home/user/dev_ws/hackrf_ros/setup.cfg`
  - Script directory configuration for installation

**Runtime Configuration:**
- `config/hackrf_rx.yaml` - Parameter configuration file for HackRF node
  - Timer period, sample rate, center frequency, number of samples

## Platform Requirements

**Development:**
- Python 3.x environment
- ROS 2 Humble installation
- libhackrf-dev development libraries
- libusb-1.0-0-dev for USB communication
- libfftw3-dev for FFT operations

**Production:**
- Docker container (image: empyreanlattice/hackrf_ros:humble)
  - Base image: ros:humble
  - System packages: libusb-1.0-0, libfftw3, hackrf (CLI tools)
  - Python packages: numpy, matplotlib, pyhackrf2, PyQt6, numpy-quaternion
  - X11 forwarding support for GUI display
  - Privileged mode for USB device access
  - Device volume mount: `/dev` for HackRF hardware access

**Deployment Target:**
- Docker container deployment (multi-platform support: x86_64, arm64)
- Tested on Jetson ARM64 platform (per docker-compose.yaml)
- Network: host mode for network access
- Display: X11 socket sharing for GUI applications

---

*Stack analysis: 2026-03-29*
