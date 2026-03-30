<!-- GSD:project-start source:PROJECT.md -->
## Project

**HackRF ROS2 Driver**

A robust ROS2 driver for the HackRF One SDR running Mayhem firmware (Portapack). It provides full device control — RX streaming, TX with authorization guardrails, and Mayhem app management — through both ROS2 topics and a Redis interface. The driver communicates via pyhackrf2 for IQ streaming and serial (/dev/ttyACM1) for Mayhem-specific commands.

**Core Value:** Reliable, safe bidirectional SDR control with IQ data streaming to Redis and TX operations gated behind explicit authorization.

### Constraints

- **Hardware**: Single HackRF One with Portapack running Mayhem firmware
- **Device path**: Serial interface at /dev/ttyACM1 (Mayhem), USB bulk via libusb (pyhackrf2)
- **Framework**: ROS2 Humble with Python (rclpy)
- **Data store**: Redis on host for IQ data, device state, and command interface
- **Safety**: TX operations must be gated behind explicit authorization — no accidental transmissions
- **Compatibility**: Must work in existing Docker deployment (empyreanlattice/hackrf_ros:humble)
<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->
## Technology Stack

## Languages
- Python 3 - Primary language for ROS 2 nodes and application logic
- C/C++ - Underlying HackRF driver library (libhackrf)
## Runtime
- ROS 2 Humble - Robot Operating System 2, Humble distribution
- pip - Python package manager for dependencies
- Colcon - ROS 2 build system for workspace management
- Ament - ROS 2 build system framework
## Frameworks
- ROS 2 (rclpy) - ROS 2 Python client library for node creation, messaging, and parameter management
- NumPy - Numerical computing library for IQ sample processing and array operations
- Matplotlib - Data visualization library for real-time plotting of IQ data, constellation plots, and spectral analysis
- pyhackrf2 - Python bindings for libhackrf (HackRF One hardware abstraction)
- PyQt6 - Qt binding for Python, used for GUI components (referenced in Dockerfile)
- pytest - Python testing framework
- ament_flake8 - ROS 2 linting tool for code style checks (used in `test/test_flake8.py`)
- ament_lint_common - Common ROS 2 linting tools
- colcon build - ROS 2 workspace builder
- ament_python - Ament build system for Python packages
## Key Dependencies
- rclpy - ROS 2 Python client library for creating nodes and handling IPC
- pyhackrf2 - Python wrapper for libhackrf C library
- numpy - Core scientific computing for signal processing
- std_msgs - ROS 2 standard message types (Float32MultiArray)
- rcl_interfaces - ROS 2 message interfaces for parameter configuration
- matplotlib - Live plotting of RF signal analysis
- numpy-quaternion - Quaternion mathematics library
## Configuration
- ROS 2 setup scripts sourced in Docker container
- `setup.py` - Python setuptools configuration at `/home/user/dev_ws/hackrf_ros/setup.py`
- `package.xml` - ROS 2 package manifest at `/home/user/dev_ws/hackrf_ros/package.xml`
- `setup.cfg` - Setuptools configuration at `/home/user/dev_ws/hackrf_ros/setup.cfg`
- `config/hackrf_rx.yaml` - Parameter configuration file for HackRF node
## Platform Requirements
- Python 3.x environment
- ROS 2 Humble installation
- libhackrf-dev development libraries
- libusb-1.0-0-dev for USB communication
- libfftw3-dev for FFT operations
- Docker container (image: empyreanlattice/hackrf_ros:humble)
- Docker container deployment (multi-platform support: x86_64, arm64)
- Tested on Jetson ARM64 platform (per docker-compose.yaml)
- Network: host mode for network access
- Display: X11 socket sharing for GUI applications
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

## Naming Patterns
- All Python source files use lowercase with underscores: `hackrf_node.py`, `iq_plotter_node.py`
- Node files follow pattern: `{name}_node.py`
- Test files follow pattern: `test_{purpose}.py`
- PascalCase for class names: `HackRFPuiblisherNode`, `IQPlotterNode`
- Classes typically inherit from base ROS 2 class `Node`
- snake_case for function names: `_configure_hackrf()`, `_on_parameter_event()`, `iq_data_callback()`
- Private/internal methods prefixed with single underscore: `_configure_hackrf()`, `_rx_callback()`, `_read_and_publish_iq()`
- Main entry point named `main()`
- snake_case for local variables and parameters: `center_freq`, `sample_rate`, `lna_gain`, `vga_gain`, `amp_enabled`, `iq_data_buffer`
- Class attributes use descriptive names without prefix: `hackrf`, `is_hackrf_streaming`, `current_samples_buffer`, `publisher_`
- Suffix underscore for ROS publisher/subscriber: `publisher_`, `subscription`
- All-caps with underscores for configuration constants: `KEEP_LAST`, `RELIABLE`
- Magic numbers extracted to named parameters: `num_iq_samples_per_publish`, `plot_buffer_size`
- ROS 2 parameters use snake_case: `center_frequency`, `sample_rate`, `lna_gain`, `vga_gain`, `amp_enabled`, `num_iq_samples_per_publish`
- ROS 2 topics use snake_case: `/hackrf_iq_data`
## Code Style
- Indentation: 4 spaces (Python standard)
- Line length: No strict limit enforced, but most lines are under 100 characters
- Multi-line statements: Continuation naturally breaks at logical points
- Tool used: flake8 (via ament_flake8)
- Config: `test/test_flake8.py` enforces via pytest
- Style checker: pep257 (via ament_pep257) - for docstring conventions
- Copyright checker: ament_copyright
- Current status: Tests are skipped for copyright (see `@pytest.mark.skip` in `test/test_copyright.py`)
- Imports placed at top of file
- Class definition follows imports
- Methods organized logically: `__init__`, configuration methods, callbacks, utilities
- Entry point `main()` function at end of file
## Import Organization
- Not used - direct imports from ROS 2 and library packages
## Error Handling
- Broad exception catching: `except Exception as e:` used for general error handling
- Log errors via ROS logger: `self.get_logger().error(f"Error message: {e}")`
- Include context in error messages with f-strings
- Graceful degradation: Node continues running even if HackRF initialization fails
- Return values for callbacks indicate success (0) or status: `_rx_callback()` returns 0 for success
## Logging
- `info()`: Status messages, state changes, configuration details
- `warn()`: Non-fatal issues, graceful degradation scenarios
- `error()`: Exception handling, critical issues
- `debug()`: Detailed diagnostic info (commented out in production)
- Excessive use of `print()` statements with "DEBUG:" prefix throughout `hackrf_node.py`
- Example: `print("DEBUG: Entering HackRFPuiblisherNode __init__")` (line 25)
- These should be replaced with `self.get_logger().debug()` calls
- Print statements interfere with ROS logging standards
## Comments
- Algorithm explanations: Present in signal processing code (FFT, windowing)
- Non-obvious logic: Parameter conversion and data type transformations
- Intent clarification: Why a particular approach was taken
- Inline clarification for complex expressions
- Triple-quoted strings used for class and method docstrings
- One-line docstrings for simple functions
- Multi-line docstrings explain purpose and behavior
- No parameter documentation (no @param style)
- No return value documentation
- Moderate to high inline comments explaining non-obvious operations
- Detailed comments for data transformation logic
- Comments explain the "why" not the "what"
## Function Design
- Methods range from 10-50 lines
- Larger methods: `_configure_hackrf()` (48 lines), `_read_and_publish_iq()` (56 lines)
- These methods could be broken into smaller units
- Methods typically take minimal parameters
- Rely on instance state (`self.hackrf`, `self.publisher_`)
- Callback functions accept required arguments: `_rx_callback(self, data, *args)`
- Use `*args` to capture variable positional arguments for flexibility
- Callbacks return status codes: `_rx_callback()` returns 0 for success
- Configuration methods return nothing (void)
- Parameter callback returns list of `SetParametersResult` objects
## Module Design
- Each module defines one main class: `HackRFPuiblisherNode`, `IQPlotterNode`
- `main()` function exported as entry point
- Setup.py defines console_scripts: `hackrf_node = hackrf_ros.hackrf_node:main`
- Not used - `__init__.py` is empty in `hackrf_ros/`
- `hackrf_node.py`: Manages HackRF device, parameter configuration, data publishing
- `iq_plotter_node.py`: Subscribes to IQ data, performs visualization
- Clear separation of concerns between publisher and subscriber nodes
## ROS 2 Patterns
- Inherit from `rclpy.node.Node`
- Declare parameters in `__init__` using `declare_parameter()`
- Create publishers/subscribers in `__init__`
- Use timers for periodic tasks: `self.create_timer(period, callback)`
- Setup in `__init__`
- Cleanup in `destroy_node()` method
- Entry point uses `rclpy.spin()` to maintain node lifecycle
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

## Pattern Overview
- Multi-node distributed communication via ROS 2 middleware
- Hardware driver node wrapping external device library (pyhackrf2)
- Publisher-Subscriber pattern for asynchronous data streaming
- Callback-based event handling for parameter updates and hardware reception
- Stateful node instances managing device lifecycle and configuration
## Layers
- Purpose: Interface with HackRF device through pyhackrf2 library
- Location: `hackrf_ros/hackrf_node.py` (lines 95-118)
- Contains: Device initialization, RX callback, stream management
- Depends on: pyhackrf2 (external library), numpy for data handling
- Used by: Configuration layer, Data acquisition layer
- Purpose: Manage ROS 2 parameters and apply them to HackRF device
- Location: `hackrf_ros/hackrf_node.py` class `HackRFPuiblisherNode._configure_hackrf()` (lines 142-191)
- Contains: Parameter declarations, validation, device reconfiguration
- Depends on: Hardware abstraction layer, rclpy parameter system
- Used by: ROS 2 parameter framework, initialization flow
- Purpose: Collect raw hardware samples into managed buffers
- Location: `hackrf_ros/hackrf_node.py` methods `_rx_callback()` (lines 193-225) and `_read_and_publish_iq()` (lines 228-283)
- Contains: Sample buffering (numpy complex arrays), format conversion (int8 to complex32)
- Depends on: Hardware abstraction layer, numpy
- Used by: Publishing layer
- Purpose: Format buffered data into ROS 2 message types and publish to topics
- Location: `hackrf_ros/hackrf_node.py` method `_read_and_publish_iq()` (lines 228-283)
- Contains: Message creation (Float32MultiArray), topic publication, sample extraction
- Depends on: Data acquisition layer, ROS 2 publisher/message system
- Used by: External ROS 2 subscribers
- Purpose: Display and analyze IQ data in real-time
- Location: `hackrf_ros/iq_plotter_node.py` class `IQPlotterNode`
- Contains: Plot generation, FFT computation, data buffering for display
- Depends on: Data publishing layer, matplotlib, numpy
- Used by: End users and analysis workflows
## Data Flow
- `hackrf`: Reference to pyhackrf2.HackRF() instance or None if initialization fails
- `is_hackrf_streaming`: Boolean flag tracking active RX stream state
- `current_samples_buffer`: Numpy complex64 array accumulating samples between publishes
- ROS 2 Parameter Store: Authoritative source for all device configuration parameters
## Key Abstractions
- Purpose: Encapsulates complete ROS 2 node for HackRF device interaction
- Examples: `hackrf_ros/hackrf_node.py` class HackRFPuiblisherNode (lines 18-303)
- Pattern: ROS 2 Node subclass with lifecycle management (init, destroy_node), parameter callbacks, timer-based processing
- Purpose: Encapsulates visualization subscriber node for real-time IQ data analysis
- Examples: `hackrf_ros/iq_plotter_node.py` class IQPlotterNode (lines 10-154)
- Pattern: ROS 2 Node subclass with subscription callback, matplotlib integration, fixed-size buffer for FFT
- Purpose: Standard interoperable representation of IQ data between nodes
- Pattern: std_msgs/Float32MultiArray with layout metadata indicating iq_samples dimension and iq_pair strides
- Data representation: Interleaved [I1, Q1, I2, Q2, ...] as flat float32 array
## Entry Points
- Location: `hackrf_ros/hackrf_node.py` function `main()` (lines 305-314)
- Triggers: Console script entry point `hackrf_node` defined in `setup.py` line 23
- Responsibilities: Initialize rclpy, instantiate HackRFPublisherNode, spin event loop, handle shutdown
- Location: `hackrf_ros/iq_plotter_node.py` function `main()` (lines 156-166)
- Triggers: Console script entry point `iq_plotter_node` defined in `setup.py` line 24
- Responsibilities: Initialize rclpy, instantiate IQPlotterNode, spin event loop, close matplotlib, handle shutdown
## Error Handling
- **Device Initialization Failure** (`hackrf_node.py` lines 112-117): Try/except around HackRF instantiation; if device unavailable, log warning but allow node to continue running without streaming. Parameter callbacks disabled for device operations.
- **Configuration Errors** (`hackrf_node.py` lines 189-191): Try/except in `_configure_hackrf()` logs errors but does not halt; stream state may be inconsistent if error occurs mid-reconfiguration.
- **Data Acquisition Errors** (`hackrf_node.py` lines 277-283): Try/except in `_read_and_publish_iq()` logs exceptions but continues looping; commented-out code suggests potential stream stop on critical error not currently enforced.
- **Callback Robustness** (`hackrf_node.py` line 193): `_rx_callback()` accepts *args to handle variable argument signatures from pyhackrf2 without failure.
- **Shutdown Cleanup** (`hackrf_node.py` lines 285-302): destroy_node() method wraps stop_rx() and close() in nested try/except blocks to ensure both operations attempt even if first fails.
## Cross-Cutting Concerns
- Approach: ROS 2 logger via `self.get_logger()` with info/warn/error/debug levels
- Usage: All significant lifecycle events, parameter changes, errors logged
- Debug prints: Scattered debug print statements (lines 25, 28, etc.) indicate active development/troubleshooting
- Approach: Minimal; relies on pyhackrf2 library to validate parameter ranges
- Current: Parameter values directly assigned to device attributes without range checking
- Risk: Out-of-range parameters may cause device errors or silent failures
- Approach: Not applicable; direct USB device access via libhackrf
- Permissions: Requires appropriate system permissions for /dev/bus/usb access
- Timer-based publication: Fixed period 0.005s ensures bounded publication rate
- Callback-based reception: Asynchronous hardware interrupts drive data buffering
- Race condition potential: current_samples_buffer accessed from both RX callback thread and timer callback thread without locking
<!-- GSD:architecture-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd:quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd:debug` for investigation and bug fixing
- `/gsd:execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd:profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
