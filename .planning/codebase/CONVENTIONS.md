# Coding Conventions

**Analysis Date:** 2026-03-29

## Naming Patterns

**Files:**
- All Python source files use lowercase with underscores: `hackrf_node.py`, `iq_plotter_node.py`
- Node files follow pattern: `{name}_node.py`
- Test files follow pattern: `test_{purpose}.py`

**Classes:**
- PascalCase for class names: `HackRFPuiblisherNode`, `IQPlotterNode`
- Classes typically inherit from base ROS 2 class `Node`

**Functions:**
- snake_case for function names: `_configure_hackrf()`, `_on_parameter_event()`, `iq_data_callback()`
- Private/internal methods prefixed with single underscore: `_configure_hackrf()`, `_rx_callback()`, `_read_and_publish_iq()`
- Main entry point named `main()`

**Variables:**
- snake_case for local variables and parameters: `center_freq`, `sample_rate`, `lna_gain`, `vga_gain`, `amp_enabled`, `iq_data_buffer`
- Class attributes use descriptive names without prefix: `hackrf`, `is_hackrf_streaming`, `current_samples_buffer`, `publisher_`
- Suffix underscore for ROS publisher/subscriber: `publisher_`, `subscription`

**Constants:**
- All-caps with underscores for configuration constants: `KEEP_LAST`, `RELIABLE`
- Magic numbers extracted to named parameters: `num_iq_samples_per_publish`, `plot_buffer_size`

**Parameters and Messages:**
- ROS 2 parameters use snake_case: `center_frequency`, `sample_rate`, `lna_gain`, `vga_gain`, `amp_enabled`, `num_iq_samples_per_publish`
- ROS 2 topics use snake_case: `/hackrf_iq_data`

## Code Style

**Formatting:**
- Indentation: 4 spaces (Python standard)
- Line length: No strict limit enforced, but most lines are under 100 characters
- Multi-line statements: Continuation naturally breaks at logical points

**Linting:**
- Tool used: flake8 (via ament_flake8)
- Config: `test/test_flake8.py` enforces via pytest
- Style checker: pep257 (via ament_pep257) - for docstring conventions
- Copyright checker: ament_copyright
- Current status: Tests are skipped for copyright (see `@pytest.mark.skip` in `test/test_copyright.py`)

**Code Organization:**
- Imports placed at top of file
- Class definition follows imports
- Methods organized logically: `__init__`, configuration methods, callbacks, utilities
- Entry point `main()` function at end of file

## Import Organization

**Order:**
1. Standard library imports: `import`, `from` statements for built-in modules
2. Third-party framework imports: ROS 2 (`rclpy`), NumPy, Matplotlib
3. Local module imports: Project-specific imports

**Pattern in `hackrf_node.py`:**
```python
import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from rclpy.time import Time
from rclpy.exceptions import ParameterNotDeclaredException
from rcl_interfaces.msg import SetParametersResult
from std_msgs.msg import Float32MultiArray
from std_msgs.msg import MultiArrayDimension
from rcl_interfaces.msg import ParameterDescriptor

import pyhackrf2
import numpy as np
import time
```

**Path Aliases:**
- Not used - direct imports from ROS 2 and library packages

## Error Handling

**Patterns:**
- Broad exception catching: `except Exception as e:` used for general error handling
- Log errors via ROS logger: `self.get_logger().error(f"Error message: {e}")`
- Include context in error messages with f-strings
- Graceful degradation: Node continues running even if HackRF initialization fails
- Return values for callbacks indicate success (0) or status: `_rx_callback()` returns 0 for success

**Example from `hackrf_node.py` (lines 112-117):**
```python
except Exception as e:
    self.get_logger().error(f"Failed to initialize HackRF or unexpected error: {e}")
    self.hackrf = None
    self.is_hackrf_streaming = False
    self.get_logger().warn("HackRF not connected or failed to initialize. Node will run but not publish data.")
```

## Logging

**Framework:** ROS 2 logger via `self.get_logger()`

**Levels used:**
- `info()`: Status messages, state changes, configuration details
- `warn()`: Non-fatal issues, graceful degradation scenarios
- `error()`: Exception handling, critical issues
- `debug()`: Detailed diagnostic info (commented out in production)

**Print debugging:**
- Excessive use of `print()` statements with "DEBUG:" prefix throughout `hackrf_node.py`
- Example: `print("DEBUG: Entering HackRFPuiblisherNode __init__")` (line 25)
- These should be replaced with `self.get_logger().debug()` calls
- Print statements interfere with ROS logging standards

**Logging Patterns:**
```python
self.get_logger().info(f"HackRF Publisher Node starting...")
self.get_logger().error(f"Failed to initialize HackRF or unexpected error: {e}")
self.get_logger().info(f"HackRF Configured: Freq={center_freq/1e6:.2f}MHz, SR={sample_rate/1e6:.2f}MSPS")
```

## Comments

**When to Comment:**
- Algorithm explanations: Present in signal processing code (FFT, windowing)
- Non-obvious logic: Parameter conversion and data type transformations
- Intent clarification: Why a particular approach was taken
- Inline clarification for complex expressions

**Docstring Style:**
- Triple-quoted strings used for class and method docstrings
- One-line docstrings for simple functions
- Multi-line docstrings explain purpose and behavior
- No parameter documentation (no @param style)
- No return value documentation

**Example from `hackrf_node.py` (lines 142-146):**
```python
def _configure_hackrf(self):
    """
    Applies the current ROS 2 parameter values to the HackRF device.
    This function is called during initialization and on parameter changes.
    """
```

**Comment Density:**
- Moderate to high inline comments explaining non-obvious operations
- Detailed comments for data transformation logic
- Comments explain the "why" not the "what"

## Function Design

**Size:**
- Methods range from 10-50 lines
- Larger methods: `_configure_hackrf()` (48 lines), `_read_and_publish_iq()` (56 lines)
- These methods could be broken into smaller units

**Parameters:**
- Methods typically take minimal parameters
- Rely on instance state (`self.hackrf`, `self.publisher_`)
- Callback functions accept required arguments: `_rx_callback(self, data, *args)`
- Use `*args` to capture variable positional arguments for flexibility

**Return Values:**
- Callbacks return status codes: `_rx_callback()` returns 0 for success
- Configuration methods return nothing (void)
- Parameter callback returns list of `SetParametersResult` objects

## Module Design

**Exports:**
- Each module defines one main class: `HackRFPuiblisherNode`, `IQPlotterNode`
- `main()` function exported as entry point
- Setup.py defines console_scripts: `hackrf_node = hackrf_ros.hackrf_node:main`

**Barrel Files:**
- Not used - `__init__.py` is empty in `hackrf_ros/`

**Single Responsibility:**
- `hackrf_node.py`: Manages HackRF device, parameter configuration, data publishing
- `iq_plotter_node.py`: Subscribes to IQ data, performs visualization
- Clear separation of concerns between publisher and subscriber nodes

## ROS 2 Patterns

**Node Structure:**
- Inherit from `rclpy.node.Node`
- Declare parameters in `__init__` using `declare_parameter()`
- Create publishers/subscribers in `__init__`
- Use timers for periodic tasks: `self.create_timer(period, callback)`

**QoS Profile:**
```python
qos_profile = QoSProfile(
    reliability=ReliabilityPolicy.RELIABLE,
    history=HistoryPolicy.KEEP_LAST,
    depth=10
)
```

**Lifecycle:**
- Setup in `__init__`
- Cleanup in `destroy_node()` method
- Entry point uses `rclpy.spin()` to maintain node lifecycle

---

*Convention analysis: 2026-03-29*
