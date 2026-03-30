# Codebase Structure

**Analysis Date:** 2026-03-29

## Directory Layout

```
hackrf_ros/
├── hackrf_ros/                 # Package source code
│   ├── __init__.py            # Empty package marker
│   ├── hackrf_node.py         # Main HackRF device driver node
│   ├── iq_plotter_node.py     # IQ data visualization node
│   └── plot_test.py           # Standalone plotting test/demo script
├── test/                       # Test files and test utilities
│   ├── test_copyright.py      # Copyright header validation tests
│   ├── test_flake8.py         # PEP8 style compliance tests
│   └── test_pep257.py         # Docstring convention tests
├── config/                     # Configuration files
│   └── hackrf_rx.yaml         # Example parameter configuration for nodes
├── docker/                     # Containerization files
│   ├── Dockerfile.hackrf_ros  # Docker image build specification
│   └── cookbook/              # Docker compose and examples
│       └── docker-compose.yaml
├── resource/                   # ROS 2 metadata resources
│   └── hackrf_ros             # Package metadata file
├── .planning/                 # Planning and documentation (generated)
│   └── codebase/              # Codebase analysis documents
├── package.xml                # ROS 2 package manifest with dependencies
├── setup.py                   # Python package setup configuration
├── setup.cfg                  # Setup tools configuration
├── README.md                  # Project overview and usage instructions
├── LICENSE                    # License information
└── .git/                      # Git repository metadata
```

## Directory Purposes

**hackrf_ros/ (Package Root):**
- Purpose: Main Python package containing executable ROS 2 nodes
- Contains: Two standalone ROS 2 node classes, one test/demo script
- Key files: `hackrf_node.py`, `iq_plotter_node.py`

**test/:**
- Purpose: Automated testing for code quality and standards compliance
- Contains: Linting, style, and docstring validation tests
- Key files: test_copyright.py, test_flake8.py, test_pep257.py
- Note: Tests are declarative style (ament_lint based) for ROS 2 packages

**config/:**
- Purpose: Example parameter configurations for node instances
- Contains: YAML files with default parameter values
- Key files: `hackrf_rx.yaml` with sample_rate, center_freq, num_samples settings

**docker/:**
- Purpose: Container definitions for standardized environment deployment
- Contains: Dockerfile and docker-compose orchestration
- Key files: `Dockerfile.hackrf_ros` with full dependency stack

**resource/:**
- Purpose: ROS 2 metadata and package discovery
- Contains: Package metadata files for colcon/ament_index
- Key files: `hackrf_ros` (no extension, metadata marker)

**.planning/codebase/:**
- Purpose: Machine-readable codebase analysis documents
- Contains: ARCHITECTURE.md, STRUCTURE.md, and other analysis docs
- Generated: Yes (created by GSD mapping tools)
- Committed: Typically yes, to maintain consistency across team

## Key File Locations

**Entry Points:**
- `hackrf_ros/hackrf_node.py`: Main HackRF publisher node (line 305-314 main() function)
- `hackrf_ros/iq_plotter_node.py`: IQ plotter subscriber node (line 156-166 main() function)

**Configuration:**
- `package.xml`: ROS 2 package dependencies and build metadata
- `setup.py`: Python package entry points and build configuration (lines 21-26)
- `config/hackrf_rx.yaml`: Runtime parameter defaults

**Core Logic:**
- `hackrf_ros/hackrf_node.py`: 318 lines, class HackRFPublisherNode (lines 18-303)
  - Device initialization and lifecycle management
  - Parameter declaration and callback handling
  - Hardware configuration application
  - Sample reception and buffering
  - Message publication
- `hackrf_ros/iq_plotter_node.py`: 169 lines, class IQPlotterNode (lines 10-154)
  - Subscription to IQ data topic
  - Real-time visualization with matplotlib
  - FFT and spectral analysis

**Testing:**
- `test/test_copyright.py`: Copyright header validation
- `test/test_flake8.py`: PEP 8 compliance checking
- `test/test_pep257.py`: Docstring convention checking

## Naming Conventions

**Files:**
- `{name}_node.py`: ROS 2 node executable files (hackrf_node.py, iq_plotter_node.py)
- `test_{feature}.py`: Test files validating specific code quality aspects
- `*.yaml`: Configuration files in YAML format
- `Dockerfile.{purpose}`: Docker build specifications with descriptive suffix

**Directories:**
- `hackrf_ros/`: Lowercase package name matching setup.py package_name
- `test/`: Standard Python testing directory
- `config/`: Configuration files directory
- `docker/`: Container-related files directory
- `resource/`: ROS 2 metadata resources directory

**Python Modules:**
- Node classes: PascalCase (HackRFPublisherNode, IQPlotterNode)
- Methods: snake_case (hackrf_node.py uses _on_parameter_event, _configure_hackrf, _rx_callback, _read_and_publish_iq, destroy_node)
- Private methods: Prefix with single underscore (e.g., _configure_hackrf)
- Public methods: No underscore prefix (main, __init__)

**ROS 2 Topics & Parameters:**
- Topics: lowercase_with_underscores prefixed with `/` (e.g., /hackrf_iq_data)
- Parameters: lowercase_with_underscores (center_frequency, sample_rate, lna_gain, vga_gain, amp_enabled, num_iq_samples_per_publish)

## Where to Add New Code

**New Feature (Additional Node):**
- Primary code: Create new file `hackrf_ros/{feature}_node.py`
- Main class: Inherit from rclpy.node.Node
- Entry point: Add new console_scripts entry in `setup.py` (lines 21-26)
- Tests: Add new test file in `test/test_{feature}.py`

**New Component/Module (Shared Utility):**
- Implementation: Create new file `hackrf_ros/{component}.py`
- Import usage: Other nodes import via `from hackrf_ros.{component} import ...`
- Note: Avoid circular imports between node files

**Utilities and Helpers:**
- Shared helpers: `hackrf_ros/utils.py` or `hackrf_ros/helpers.py` (not currently used)
- Device abstraction: Consider `hackrf_ros/device_driver.py` to wrap pyhackrf2 interactions
- Data processing: Consider `hackrf_ros/iq_processing.py` for sample conversion logic

**Configuration Files:**
- New parameter sets: Add to `config/` directory with descriptive name (e.g., `config/hackrf_uhf.yaml`)
- Format: YAML with node name namespace and ros__parameters section

**Tests:**
- Unit tests: `test/test_{module_name}.py` following ament_lint patterns
- Test discovery: Automatic via test pattern `test_*.py` in test/ directory

## Special Directories

**node_modules / __pycache__ / .git:**
- Purpose: Version control, package cache, build artifacts
- Generated: Yes (by git, Python, colcon)
- Committed: No (.gitignore excludes these)

**build / install / log:**
- Purpose: ROS 2 colcon build outputs and logs
- Generated: Yes (by colcon during `colcon build`)
- Committed: No (created during build process)

**.planning/codebase/:**
- Purpose: GSD mapping and analysis documents (ARCHITECTURE.md, STRUCTURE.md, etc.)
- Generated: Yes (by gsd:map-codebase tools)
- Committed: Yes (referenced by gsd:plan-phase and gsd:execute-phase)

**docker/cookbook/:**
- Purpose: Docker compose recipes and deployment examples
- Generated: No (hand-maintained)
- Committed: Yes

---

*Structure analysis: 2026-03-29*
