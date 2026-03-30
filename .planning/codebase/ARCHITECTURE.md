# Architecture

**Analysis Date:** 2026-03-29

## Pattern Overview

**Overall:** ROS 2 Pub/Sub Node Architecture with Device Driver Integration

**Key Characteristics:**
- Multi-node distributed communication via ROS 2 middleware
- Hardware driver node wrapping external device library (pyhackrf2)
- Publisher-Subscriber pattern for asynchronous data streaming
- Callback-based event handling for parameter updates and hardware reception
- Stateful node instances managing device lifecycle and configuration

## Layers

**Hardware Abstraction Layer:**
- Purpose: Interface with HackRF device through pyhackrf2 library
- Location: `hackrf_ros/hackrf_node.py` (lines 95-118)
- Contains: Device initialization, RX callback, stream management
- Depends on: pyhackrf2 (external library), numpy for data handling
- Used by: Configuration layer, Data acquisition layer

**Configuration Layer:**
- Purpose: Manage ROS 2 parameters and apply them to HackRF device
- Location: `hackrf_ros/hackrf_node.py` class `HackRFPuiblisherNode._configure_hackrf()` (lines 142-191)
- Contains: Parameter declarations, validation, device reconfiguration
- Depends on: Hardware abstraction layer, rclpy parameter system
- Used by: ROS 2 parameter framework, initialization flow

**Data Acquisition & Buffering Layer:**
- Purpose: Collect raw hardware samples into managed buffers
- Location: `hackrf_ros/hackrf_node.py` methods `_rx_callback()` (lines 193-225) and `_read_and_publish_iq()` (lines 228-283)
- Contains: Sample buffering (numpy complex arrays), format conversion (int8 to complex32)
- Depends on: Hardware abstraction layer, numpy
- Used by: Publishing layer

**Publishing Layer:**
- Purpose: Format buffered data into ROS 2 message types and publish to topics
- Location: `hackrf_ros/hackrf_node.py` method `_read_and_publish_iq()` (lines 228-283)
- Contains: Message creation (Float32MultiArray), topic publication, sample extraction
- Depends on: Data acquisition layer, ROS 2 publisher/message system
- Used by: External ROS 2 subscribers

**Visualization Layer:**
- Purpose: Display and analyze IQ data in real-time
- Location: `hackrf_ros/iq_plotter_node.py` class `IQPlotterNode`
- Contains: Plot generation, FFT computation, data buffering for display
- Depends on: Data publishing layer, matplotlib, numpy
- Used by: End users and analysis workflows

## Data Flow

**Initialization Flow:**

1. Node instantiation calls `__init__()` which declares ROS 2 parameters with defaults
2. HackRF device opened via `pyhackrf2.HackRF()` initialization
3. Parameters applied to device via `_configure_hackrf()`
4. Device RX stream started with callback registered via `hackrf.start_rx(self._rx_callback)`
5. Timer created with period 0.005s to trigger periodic data publication
6. Parameter event handler registered to detect runtime parameter changes

**Data Acquisition Flow:**

1. HackRF hardware asynchronously calls `_rx_callback()` with raw int8 byte data
2. Raw bytes reshaped from flat array to (N, 2) I/Q pairs
3. I/Q samples normalized (int8 / 128) and converted to complex64
4. Complex samples appended to `current_samples_buffer` (numpy array)
5. On timer tick (every 0.005s), `_read_and_publish_iq()` checks buffer size
6. If buffer has sufficient samples (configurable via parameter), samples extracted
7. Complex samples interleaved to float32 array [I1, Q1, I2, Q2, ...]
8. Float32MultiArray message created with layout metadata
9. Message published to `/hackrf_iq_data` topic
10. Published samples removed from buffer

**Parameter Update Flow:**

1. ROS 2 parameter change detected by `_on_parameter_event()` callback
2. Changed parameter logged and evaluated for hardware reconfiguration need
3. If hardware-affecting parameter changed (frequency, gain, sample rate, etc.) and device available:
   - `_configure_hackrf()` called
   - Current RX stream stopped if streaming
   - New parameter values fetched from ROS 2 parameter store
   - Device attributes directly assigned (e.g., `hackrf.center_freq = int(center_freq)`)
   - New RX stream started with same callback

**Visualization Flow:**

1. IQPlotterNode subscribes to `/hackrf_iq_data` Float32MultiArray messages
2. Message received in `iq_data_callback()`
3. Interleaved float32 data unpacked to I and Q components
4. Complex samples appended to fixed-size deque (plot_buffer_size=8192)
5. When buffer full, `update_plot()` triggered:
   - Time domain plot: I and Q real/imag components vs sample index
   - Constellation plot: Scatter of Q vs I samples
   - PSD plot: Hanning-windowed FFT converted to dB scale with frequency axis
6. Matplotlib canvas redrawn with all three plots

**State Management:**

- `hackrf`: Reference to pyhackrf2.HackRF() instance or None if initialization fails
- `is_hackrf_streaming`: Boolean flag tracking active RX stream state
- `current_samples_buffer`: Numpy complex64 array accumulating samples between publishes
- ROS 2 Parameter Store: Authoritative source for all device configuration parameters

## Key Abstractions

**HackRFPublisherNode:**
- Purpose: Encapsulates complete ROS 2 node for HackRF device interaction
- Examples: `hackrf_ros/hackrf_node.py` class HackRFPuiblisherNode (lines 18-303)
- Pattern: ROS 2 Node subclass with lifecycle management (init, destroy_node), parameter callbacks, timer-based processing

**IQPlotterNode:**
- Purpose: Encapsulates visualization subscriber node for real-time IQ data analysis
- Examples: `hackrf_ros/iq_plotter_node.py` class IQPlotterNode (lines 10-154)
- Pattern: ROS 2 Node subclass with subscription callback, matplotlib integration, fixed-size buffer for FFT

**ROS 2 Message Format:**
- Purpose: Standard interoperable representation of IQ data between nodes
- Pattern: std_msgs/Float32MultiArray with layout metadata indicating iq_samples dimension and iq_pair strides
- Data representation: Interleaved [I1, Q1, I2, Q2, ...] as flat float32 array

## Entry Points

**hackrf_node:**
- Location: `hackrf_ros/hackrf_node.py` function `main()` (lines 305-314)
- Triggers: Console script entry point `hackrf_node` defined in `setup.py` line 23
- Responsibilities: Initialize rclpy, instantiate HackRFPublisherNode, spin event loop, handle shutdown

**iq_plotter_node:**
- Location: `hackrf_ros/iq_plotter_node.py` function `main()` (lines 156-166)
- Triggers: Console script entry point `iq_plotter_node` defined in `setup.py` line 24
- Responsibilities: Initialize rclpy, instantiate IQPlotterNode, spin event loop, close matplotlib, handle shutdown

## Error Handling

**Strategy:** Graceful degradation with defensive initialization

**Patterns:**

- **Device Initialization Failure** (`hackrf_node.py` lines 112-117): Try/except around HackRF instantiation; if device unavailable, log warning but allow node to continue running without streaming. Parameter callbacks disabled for device operations.

- **Configuration Errors** (`hackrf_node.py` lines 189-191): Try/except in `_configure_hackrf()` logs errors but does not halt; stream state may be inconsistent if error occurs mid-reconfiguration.

- **Data Acquisition Errors** (`hackrf_node.py` lines 277-283): Try/except in `_read_and_publish_iq()` logs exceptions but continues looping; commented-out code suggests potential stream stop on critical error not currently enforced.

- **Callback Robustness** (`hackrf_node.py` line 193): `_rx_callback()` accepts *args to handle variable argument signatures from pyhackrf2 without failure.

- **Shutdown Cleanup** (`hackrf_node.py` lines 285-302): destroy_node() method wraps stop_rx() and close() in nested try/except blocks to ensure both operations attempt even if first fails.

## Cross-Cutting Concerns

**Logging:**
- Approach: ROS 2 logger via `self.get_logger()` with info/warn/error/debug levels
- Usage: All significant lifecycle events, parameter changes, errors logged
- Debug prints: Scattered debug print statements (lines 25, 28, etc.) indicate active development/troubleshooting

**Validation:**
- Approach: Minimal; relies on pyhackrf2 library to validate parameter ranges
- Current: Parameter values directly assigned to device attributes without range checking
- Risk: Out-of-range parameters may cause device errors or silent failures

**Authentication:**
- Approach: Not applicable; direct USB device access via libhackrf
- Permissions: Requires appropriate system permissions for /dev/bus/usb access

**Timing & Synchronization:**
- Timer-based publication: Fixed period 0.005s ensures bounded publication rate
- Callback-based reception: Asynchronous hardware interrupts drive data buffering
- Race condition potential: current_samples_buffer accessed from both RX callback thread and timer callback thread without locking

---

*Architecture analysis: 2026-03-29*
