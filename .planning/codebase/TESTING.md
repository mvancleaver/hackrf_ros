# Testing Patterns

**Analysis Date:** 2026-03-29

## Test Framework

**Runner:**
- pytest (via setup.py: `tests_require=['pytest']`)
- ROS 2 Ament test framework (ament_lint_auto, ament_lint_common)
- Config: `setup.py` line 20 specifies pytest as test requirement

**Assertion Library:**
- pytest assertions (simple `assert` statements)
- ROS 2 test infrastructure via ament framework

**Run Commands:**
```bash
colcon test                    # Run all tests in ROS 2 workspace
colcon test --packages-select hackrf_ros  # Run tests for this package only
pytest                         # Run pytest directly (if in package directory)
```

## Test File Organization

**Location:**
- Separate from source code
- Test directory: `test/` at package root level
- Test files in `/home/user/dev_ws/hackrf_ros/test/`
- Source files in `/home/user/dev_ws/hackrf_ros/hackrf_ros/`

**Naming:**
- Pattern: `test_{purpose}.py`
- Examples: `test_copyright.py`, `test_flake8.py`, `test_pep257.py`

**Structure:**
```
hackrf_ros/
├── hackrf_ros/                 # Source code
│   ├── __init__.py
│   ├── hackrf_node.py
│   ├── iq_plotter_node.py
│   └── plot_test.py           # Standalone test/demo script (not pytest format)
├── test/                       # Test directory
│   ├── test_copyright.py       # Copyright header validation
│   ├── test_flake8.py          # PEP 8 style linting
│   └── test_pep257.py          # Docstring conventions
└── setup.py                    # Lists pytest as test dependency
```

## Test Structure

**Suite Organization:**

From `test/test_flake8.py` (lines 19-25):
```python
@pytest.mark.flake8
@pytest.mark.linter
def test_flake8():
    rc, errors = main_with_errors(argv=[])
    assert rc == 0, \
        'Found %d code style errors / warnings:\n' % len(errors) + \
        '\n'.join(errors)
```

From `test/test_pep257.py` (lines 19-23):
```python
@pytest.mark.linter
@pytest.mark.pep257
def test_pep257():
    rc = main(argv=['.', 'test'])
    assert rc == 0, 'Found code style errors / warnings'
```

**Patterns:**
- Tests decorated with pytest markers: `@pytest.mark.flake8`, `@pytest.mark.copyright`, `@pytest.mark.linter`
- Simple assertion pattern: `assert rc == 0, 'error message'`
- Error messages formatted as strings describing what failed
- Tests wrapped in functions with `test_` prefix

## Linting Tests

**Framework:** Ament (ROS 2) linting tools
- ament_flake8: PEP 8 style enforcement
- ament_pep257: Docstring convention enforcement
- ament_copyright: Copyright header validation

**Patterns:**

**1. Flake8 Test (`test/test_flake8.py`):**
- Calls `main_with_errors(argv=[])` from ament_flake8
- Returns return code and list of errors
- Asserts return code is 0 (no errors)
- Displays found errors in assertion message

**2. PEP257 Test (`test/test_pep257.py`):**
- Calls `main(argv=['.', 'test'])` from ament_pep257
- Checks docstring conventions across source and test directories
- Asserts return code is 0

**3. Copyright Test (`test/test_copyright.py`):**
- Currently skipped with `@pytest.mark.skip(reason='...')`
- Would check for Apache 2.0 copyright header in files
- Decorator: `@pytest.mark.skip` - test is disabled until source files have proper headers

**What to Lint:**
- All source files in root and `hackrf_ros/` directory
- Test files also checked by some tools
- Configuration excluded via paths passed to linters

## Code Quality Metrics

**Current Status:**
- Flake8 enforcement: Active (pytest test runs it)
- PEP257 enforcement: Active (pytest test runs it)
- Copyright header enforcement: Skipped (no headers currently)
- Coverage measurement: Not implemented
- Type checking: Not implemented (no mypy/pyright)

## Unit Testing

**Status:** Not implemented

**Approach needed for future tests:**
- Test nodes in isolation using ROS 2 test utilities
- Mock hardware (pyhackrf2.HackRF) for testing without physical device
- Test data transformations and callback logic
- Verify parameter handling and ROS communication

**Test candidates:**
- `HackRFPuiblisherNode`: Parameter reading, HackRF configuration, data publishing
- `IQPlotterNode`: Data buffering, FFT calculations, plotting updates
- Data conversion: Complex sample conversion (int8 to float32)
- Callback functions: `_on_parameter_event()`, `iq_data_callback()`, `_read_and_publish_iq()`

## Integration Testing

**Status:** Not implemented

**Approach:**
- Would require ROS 2 test launcher
- Could test communication between nodes (HackRF publisher → IQ plotter subscriber)
- Would need to mock HackRF hardware
- Could verify message flow through ROS 2 middleware

## Mocking Strategy

**Mock Objects Needed:**
```python
# Mock HackRF device
from unittest.mock import Mock, patch

# Example pattern (not yet implemented in codebase):
@patch('hackrf_ros.hackrf_node.pyhackrf2.HackRF')
def test_hackrf_initialization(mock_hackrf_class):
    mock_hackrf = Mock()
    mock_hackrf_class.return_value = mock_hackrf
    # ... test initialization
```

**What NOT to Mock:**
- ROS 2 framework classes (Node, Parameter handling, Publishers)
- Standard library modules (numpy, collections)
- Message types (Float32MultiArray, ParameterDescriptor)

## Test Data

**Status:** Not implemented

**Test data location (when implemented):**
- Would be in `test/fixtures/` or `test/data/`
- Sample IQ data files for testing plotter
- Configuration sets for testing parameter handling

## Coverage

**Requirements:** None enforced

**View Coverage:**
- Coverage tools not configured in project
- Could implement with: `pytest --cov=hackrf_ros`

**Current gaps:**
- No unit tests for `HackRFPuiblisherNode` class methods
- No unit tests for `IQPlotterNode` class methods
- No integration tests for node communication
- Only static analysis tests (flake8, pep257, copyright)

## Test Dependencies

**From package.xml (`package.xml` lines 19-20):**
```xml
<test_depend>ament_lint_auto</test_depend>
<test_depend>ament_lint_common</test_depend>
```

**From setup.py (`setup.py` line 20):**
```python
tests_require=['pytest'],
```

**Additional implicit dependencies:**
- ament_flake8 (invoked by ament_lint_common)
- ament_pep257 (invoked by ament_lint_common)
- ament_copyright (invoked by ament_lint_common)

## Running Tests

**ROS 2 Workspace Command:**
```bash
colcon test --packages-select hackrf_ros
```

**Pytest Direct (from package directory):**
```bash
pytest test/
pytest test/test_flake8.py  # Run specific test
pytest test/test_flake8.py -v  # Verbose output
```

**View Test Results:**
```bash
colcon test-result --all  # View results from last test run
```

## Test Markers

**Current markers used:**
- `@pytest.mark.linter` - All static analysis tests marked as linters
- `@pytest.mark.copyright` - Copyright header test
- `@pytest.mark.flake8` - PEP 8 style test
- `@pytest.mark.pep257` - Docstring convention test
- `@pytest.mark.skip` - Skip test with reason

**Running tests by marker:**
```bash
pytest -m linter          # Run all linter tests
pytest -m "not skip"      # Skip skipped tests
```

## Known Testing Gaps

**Missing unit tests for:**
- `HackRFPuiblisherNode.__init__()` - Parameter declaration, device initialization
- `HackRFPuiblisherNode._configure_hackrf()` - Configuration logic
- `HackRFPuiblisherNode._rx_callback()` - Data conversion and buffering
- `HackRFPuiblisherNode._read_and_publish_iq()` - Message creation and publishing
- `HackRFPuiblisherNode._on_parameter_event()` - Parameter change handling
- `IQPlotterNode.iq_data_callback()` - Data buffering and plotting trigger
- `IQPlotterNode.update_plot()` - FFT, PSD calculation, plot updates
- Complex number conversion (int8 I/Q to complex64)
- ROS 2 message serialization

**Hardware dependencies:**
- Tests require pyhackrf2 library to be importable
- No unit tests possible without mocking pyhackrf2.HackRF class

**Implementation recommendations:**
1. Add unit tests with mocked HackRF device
2. Implement pytest fixtures for test data
3. Add coverage reporting (pytest-cov)
4. Test parameter handling with ROS 2 parameter utilities
5. Add integration tests using ROS 2 test framework

## Debugging Tests

**Debug print disabled tests (not yet a pattern):**
- Some functionality has debug prints using `print()` (e.g., `hackrf_node.py` lines 25-117)
- These should be replaced with `self.get_logger().debug()` for consistency
- Debug logging controlled by ROS 2 log level, not print statements

---

*Testing analysis: 2026-03-29*
