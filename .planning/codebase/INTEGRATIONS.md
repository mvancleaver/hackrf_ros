# External Integrations

**Analysis Date:** 2026-03-29

## APIs & External Services

**Hardware Control:**
- HackRF One SDR device - Software-defined radio hardware for RF signal acquisition and analysis
  - SDK/Client: `pyhackrf2` (Python bindings for libhackrf C library)
  - Connection: USB (direct device mount via `/dev` in Docker)
  - Usage: RF signal reception, dynamic parameter configuration
  - Files: `hackrf_ros/hackrf_node.py`

## Data Storage

**Databases:**
- Not detected - Application operates on streaming RF data without persistent storage

**File Storage:**
- Local filesystem only - Configuration files stored locally
  - Config location: `config/hackrf_rx.yaml`

**Caching:**
- In-memory buffers:
  - IQ sample buffer: `np.array` in `hackrf_node.py` (complex64 data)
  - Plot buffer: `deque(maxlen=8192)` in `iq_plotter_node.py` for FFT analysis

## Authentication & Identity

**Auth Provider:**
- Not applicable - No external authentication required
- Hardware access: Direct USB device access (privileged mode in Docker)

## Monitoring & Observability

**Error Tracking:**
- Not detected - No external error tracking service

**Logs:**
- ROS 2 logging framework (rclpy logger)
  - Methods: `get_logger().info()`, `get_logger().error()`, `get_logger().warn()`
  - Files: `hackrf_ros/hackrf_node.py`, `hackrf_ros/iq_plotter_node.py`
  - Output: Console/terminal (inherited from ROS 2)
  - Debug prints: Inline `print()` statements for development debugging

## CI/CD & Deployment

**Hosting:**
- Docker containerized deployment
  - Image registry: Docker Hub (empyreanlattice/hackrf_ros:humble)
  - Build context: Local Docker build from `docker/Dockerfile.hackrf_ros`

**Container Orchestration:**
- Docker Compose orchestration
  - Compose file: `docker/cookbook/docker-compose.yaml`
  - Service configuration: hackrf_ros service with:
    - Network mode: host (for ROS 2 network communication)
    - Volume mounts: X11 socket, Xauthority, /dev, config directory
    - Privileged mode: enabled (for USB device access)
    - TTY: enabled for interactive access

**CI Pipeline:**
- Not detected - No CI/CD automation configured

## Environment Configuration

**Required env vars:**
- `DISPLAY` - X11 display variable for GUI applications (passed from host in Docker)
- `QT_X11_NO_MITSHM=1` - PyQt6 X11 configuration (prevents shared memory issues)
- `ROS_DISTRO` - ROS 2 distribution identifier (set during Docker build: humble)

**Secrets location:**
- Not applicable - No secrets required

## ROS 2 Messaging & IPC

**Message Types:**
- `std_msgs/Float32MultiArray` - IQ data transmission format
  - Publisher: `hackrf_node.py` publishes on topic `/hackrf_iq_data`
  - Subscriber: `iq_plotter_node.py` subscribes to `/hackrf_iq_data`
  - Data format: Interleaved float32 (I1, Q1, I2, Q2, ...)
  - QoS: Reliable delivery with history depth of 10

**Parameter Server:**
- ROS 2 Parameter Server integration
  - Parameter scope: `/hackrf_publisher_node` namespace
  - Configurable parameters:
    - `center_frequency` (Hz) - Default: 2447e6
    - `sample_rate` (Hz) - Default: 8e6
    - `lna_gain` (dB) - Default: 16, Range: 0-40 (8dB steps)
    - `vga_gain` (dB) - Default: 20, Range: 0-62 (2dB steps)
    - `amp_enabled` (bool) - Default: False
    - `num_iq_samples_per_publish` - Default: 8192
    - `sample_rate` (for iq_plotter_node) - Default: 8000000.0 (8 MSPS)
  - Configuration file: `config/hackrf_rx.yaml`
  - Runtime updates: Parameters can be changed via `ros2 param set` command

## Webhooks & Callbacks

**Incoming:**
- HackRF RX Callback: Hardware callback for received RF samples
  - Callback function: `_rx_callback()` in `hackrf_node.py`
  - Triggered by: pyhackrf2 when new samples are available from HackRF
  - Data handling: Raw bytes from HackRF are converted to complex IQ samples and buffered

**Outgoing:**
- IQ Data Publication: Publishes processed IQ data to ROS 2 topic
  - Topic: `/hackrf_iq_data`
  - Message type: `std_msgs/Float32MultiArray`
  - Frequency: Based on timer period and data acquisition rate

**ROS 2 Callbacks:**
- Parameter change callback: `_on_parameter_event()` in `hackrf_node.py`
  - Triggered by: ROS 2 parameter server when node parameters are modified
  - Action: Reconfigures HackRF device with new settings (stops/restarts RX stream)
- Data processing callback: `iq_data_callback()` in `iq_plotter_node.py`
  - Triggered by: Subscription to `/hackrf_iq_data` topic
  - Action: Buffers incoming IQ data and updates matplotlib plots when buffer is full

---

*Integration audit: 2026-03-29*
