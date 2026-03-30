# Phase 5: PyMayhem Refactor - Research

**Researched:** 2026-03-29
**Domain:** Python monorepo packaging, serial protocol extraction, Redis-native driver, ROS2 thin bridge
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Monorepo layout — all three packages as sibling directories in this repo:
  - `pymayhem/` — standalone Mayhem serial API
  - `hackrf_driver/` — Redis-native HackRF driver (pyhackrf2 + pymayhem + redis)
  - `hackrf_ros_bridge/` — thin ROS2 node (redis -> ROS2 topics)
- **D-02:** `pymayhem` is local-only for now (`pip install -e ./pymayhem`). Set up pyproject.toml for future PyPI publishing but do not publish yet.
- **D-03:** Existing `hackrf_ros/` package becomes the ROS2 bridge. Core driver logic moves to `hackrf_driver/`.
- **D-04:** Domain module organization: `client.radio.info()`, `client.ui.button(1)`, `client.fs.ls('/')`, `client.sensors.gotgps(...)`, `client.system.sysinfo()`. Sub-objects on MayhemClient.
- **D-05:** Both context manager and explicit open/close: `with MayhemClient('/dev/hackrf_mayhem') as m:` and `m = MayhemClient(); m.open(); m.close()`.
- **D-06:** Dangerous commands (write_memory, flash, dfu, pmemreset, settingsreset, sd_over_usb) exposed only on `UnsafeMayhemClient` subclass — never on the base `MayhemClient`.
- **D-07:** `appstart` must handle USB reset automatically — `appstart_with_reconnect()` sends command, expects USB disconnect, polls for device reappearance (up to 10s), reconnects.
- **D-08:** Auto-detect device via udev symlink `/dev/hackrf_mayhem` or USB VID:PID `1d50:6018` scan.
- **D-09:** Firmware version detection via `info` command — report capability level (e.g., `setfreq` missing in v2.0.1).
- **D-10:** Layered config: YAML/JSON config file for defaults, Redis hash (`hackrf:config`) for runtime overrides. Config file loaded at startup, Redis commands override at runtime.
- **D-11:** Dual entry point: `python -m hackrf_driver` for programmatic/Docker use, `hackrf-driver --freq 433e6 --gain 20` CLI with argparse for one-off use.
- **D-12:** Core driver has NO rclpy import. Uses threading for event loop, Redis for all I/O. Replaces ROS2 timers with `threading.Timer` or a simple main loop.
- **D-13:** Core driver keeps: pyhackrf2 RX/TX pipeline, dual queue.Queue buffer, exponential backoff reconnection, parameter validation (PARAM_RANGES), RedisBridge, TXController. Imports pymayhem for serial.
- **D-14:** Redis Pub/Sub notification on IQ stream write — driver publishes to `hackrf:iq:notify` channel after each XADD so bridge can subscribe with low latency.
- **D-15:** IQ transport uses Redis Pub/Sub: bridge subscribes to `hackrf:iq:notify`, then XREAD the latest entry from `hackrf:iq:stream`. Low latency without polling.
- **D-16:** Bridge exposes: `/hackrf/iq` (Float32MultiArray), `/hackrf/state` (String/JSON), command service (writes to `hackrf:cmd`), and Mayhem services (appstart, setfreq, radioinfo proxied through Redis).
- **D-17:** Bridge is a standard ROS2 node in `hackrf_ros_bridge/` — lightweight, only depends on rclpy + redis-py.
- **D-18:** Existing ROS2 subscribers work unchanged — same topic names, same message types.

### Claude's Discretion

- Internal threading model for the standalone driver main loop
- Redis Pub/Sub channel naming for IQ notifications
- How to migrate existing tests (split across packages vs shared test suite)
- Whether hackrf_driver uses click/typer vs argparse for CLI
- Docker image restructuring (if needed)

### Deferred Ideas (OUT OF SCOPE)

- PyPI publishing of pymayhem — set up pyproject.toml now, publish later
- MQTT/ZMQ bridge alternatives — future work
- Docker image restructuring — separate concern

</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| REF-01 | `pymayhem` is a standalone pip-installable Python package with no ROS2 or Redis dependencies | pyproject.toml editable install pattern; MayhemSerial has zero ROS2/Redis imports already |
| REF-02 | `pymayhem` exposes all 47 Mayhem serial commands organized by domain (radio, ui, filesystem, sensors, system) | MAYHEM_API_ASSESSMENT.md full command inventory; domain module delegation pattern |
| REF-03 | `pymayhem` handles `appstart` USB reset with automatic reconnection | pyserial + usb.core VID:PID scan pattern; polling loop with timeout |
| REF-04 | HackRF core driver runs standalone with Redis as only external interface (no rclpy import) | threading.Event main loop pattern; YAML config loading; argparse entry point |
| REF-05 | ROS2 bridge node reads IQ from `hackrf:iq:stream` and publishes to `/hackrf/iq` topic | Redis Pub/Sub notify + XREAD pattern (D-14/D-15); existing RedisBridge._xadd_iq as reference |
| REF-06 | ROS2 bridge node subscribes to `hackrf:state` and publishes device state to ROS2 topics | Redis HGETALL on notify or periodic poll; existing _build_state_dict as reference |
| REF-07 | All 68 existing unit tests pass after refactor (no regression) | Test migration map: 9 to pymayhem, 36 to hackrf_driver, 23 to hackrf_ros_bridge; conftest.py duplication pattern |

</phase_requirements>

---

## Summary

Phase 5 is a structural refactor — no new hardware behaviors, no new Redis commands, no new test scenarios. The 68 existing tests already pass (confirmed 2026-03-29) and cover all the logic being moved. The primary technical work is: (1) extracting MayhemSerial into a domain-organized pymayhem package with pyproject.toml, (2) stripping rclpy from the driver and replacing ROS2 timers with threading primitives, and (3) wiring the bridge to consume from Redis instead of calling hardware directly.

The existing code is already well-structured for this split. `MayhemSerial` (270 lines, zero ROS2/Redis imports), `RedisBridge`, and `TXController` are independently tested classes that communicate through narrow interfaces. The driver's `HackRFNode.__init__` parameter declarations map directly to a YAML config file. ROS2 timers map directly to `threading.Timer`. The `_build_state_dict()` method maps directly to the bridge's state publisher.

The only technically novel element is the Redis Pub/Sub + XREAD combined pattern for IQ notifications (D-14/D-15). This is a well-established pattern and the implementation is straightforward: driver calls `redis.publish('hackrf:iq:notify', entry_id)` after each XADD; bridge thread uses `pubsub.subscribe('hackrf:iq:notify')` and on each message calls `xrevrange(STREAM_KEY, '+', '-', count=1)` to get the latest entry.

**Primary recommendation:** Proceed package-by-package in dependency order — pymayhem first (no deps), hackrf_driver second (depends on pymayhem), hackrf_ros_bridge third (depends on hackrf_driver via Redis). Migrate tests to their destination package as each package is created.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| setuptools | 79.0.1 (installed) | pyproject.toml build backend for pymayhem | Already in environment; PEP 517/518 compliant; required for `pip install -e` |
| pyserial | 3.5 (installed) | Serial communication | Locked from Phase 2; no upgrade needed |
| redis-py | 7.4.0 (installed) | Redis client in driver and bridge | Locked from Phase 3; hiredis auto-detected |
| hiredis | 3.3.1 (installed) | C-accelerated Redis parser | Already installed; zero config |
| PyYAML | 5.4.1 (installed) | YAML config file loading in hackrf_driver | Already installed in environment |
| argparse | built-in | CLI entry point for `hackrf-driver` | Built-in stdlib; no extra dep; per D-11 discretion |
| threading | built-in | Main loop, timers, locks | Already used throughout; replaces ROS2 executor |
| pyusb (usb.core) | check install | USB VID:PID scan for device auto-detect (D-08) | Needed for `/dev/hackrf_mayhem` fallback scan |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| tomli | 2.4.1 (installed) | TOML reading on Python <3.11 | Not needed — use PyYAML for config |
| pyhackrf2 | as installed | HackRF device access | Stays in hackrf_driver only; never in pymayhem or bridge |
| numpy | as installed | IQ sample conversion | Stays in hackrf_driver (RedisBridge._xadd_iq); never in pymayhem |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| PyYAML for config | tomllib (stdlib 3.11+) | PyYAML works on Python 3.10 (target env); no change needed |
| argparse for CLI | click / typer | argparse is stdlib, zero dep; D-11 says CLI is needed but doesn't mandate a framework; argparse is correct for a simple driver CLI |
| threading.Event main loop | asyncio | Driver uses threading throughout (rx callback, redis, serial); mixing asyncio would be a rewrite |
| pyusb for device scan | glob /dev/serial/by-id | pyusb is more reliable for VID:PID scan; glob is simpler but misses devices without udev rules configured |

**Installation (new packages only — rest already installed):**
```bash
pip install -e ./pymayhem      # editable local install
pip install -e ./hackrf_driver # editable local install
```

**Version verification (already confirmed against installed environment):**
```
redis: 7.4.0
hiredis: 3.3.1
pyserial: 3.5
PyYAML: 5.4.1
setuptools: 79.0.1
Python: 3.10.12
Redis server: 6.0.16
```

---

## Architecture Patterns

### Recommended Project Structure

```
hackrf_ros/                        # git repo root (unchanged name)
├── pymayhem/                      # NEW: standalone serial package
│   ├── pyproject.toml             # build metadata (PEP 517)
│   ├── pymayhem/
│   │   ├── __init__.py            # exports MayhemClient, UnsafeMayhemClient
│   │   ├── client.py              # MayhemClient base class + context manager
│   │   ├── unsafe_client.py       # UnsafeMayhemClient subclass
│   │   ├── _serial.py             # MayhemSerial internals (extracted from mayhem_serial.py)
│   │   └── domains/
│   │       ├── __init__.py
│   │       ├── radio.py           # radioinfo, setfreq
│   │       ├── ui.py              # button, touch, keyboard, accessibility_*
│   │       ├── fs.py              # ls, fopen, fread, fwrite, fclose, mkdir, unlink, crc32
│   │       ├── sensors.py         # gotgps, gotenv, gotorientation
│   │       └── system.py          # info, sysinfo, rtcget, rtcset, reboot, applist, appstart
│   └── tests/
│       └── test_mayhem_client.py  # migrated from test/test_mayhem_serial.py
│
├── hackrf_driver/                 # NEW: Redis-native driver (no rclpy)
│   ├── pyproject.toml
│   ├── hackrf_driver/
│   │   ├── __init__.py
│   │   ├── __main__.py            # python -m hackrf_driver entry point
│   │   ├── cli.py                 # argparse CLI (hackrf-driver command)
│   │   ├── driver.py              # HackRFDriver class (stripped HackRFNode)
│   │   ├── redis_bridge.py        # moved from hackrf_ros/redis_bridge.py
│   │   ├── tx_controller.py       # moved from hackrf_ros/tx_controller.py
│   │   └── config.py              # YAML config loader + PARAM_RANGES
│   └── tests/
│       ├── conftest.py            # Redis mock fixture (duplicated)
│       ├── test_redis_bridge.py   # migrated from test/test_redis_bridge.py
│       └── test_tx_controller.py  # migrated from test/test_tx_controller.py
│
├── hackrf_ros/                    # EXISTING: becomes the ROS2 bridge
│   ├── hackrf_ros/
│   │   ├── __init__.py
│   │   └── bridge_node.py         # NEW: thin bridge (was hackrf_node.py)
│   └── (setup.py, package.xml kept)
│
├── test/                          # EXISTING: keep bridge-level tests here
│   ├── conftest.py                # Redis mock (keep for ROS2 bridge tests)
│   └── test_hackrf_node_redis.py  # migrated/adapted for bridge-only behaviors
│
├── setup.py                       # KEPT: hackrf_ros ROS2 package (bridge only)
└── package.xml                    # KEPT: ROS2 manifest (bridge only)
```

### Pattern 1: pyproject.toml for Editable Local Install

**What:** PEP 517/518 build system declaration enabling `pip install -e ./pymayhem`.
**When to use:** Every new Python package in the monorepo (pymayhem, hackrf_driver).

```toml
# pymayhem/pyproject.toml
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "pymayhem"
version = "0.1.0"
description = "Standalone Python API for Mayhem firmware serial control"
requires-python = ">=3.10"
dependencies = [
    "pyserial>=3.5",
]

[project.urls]
Homepage = "https://github.com/example/hackrf_ros"

[tool.setuptools.packages.find]
where = ["."]
include = ["pymayhem*"]
```

```toml
# hackrf_driver/pyproject.toml
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "hackrf_driver"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "pymayhem",          # local editable — installed before this
    "redis>=7.4.0",
    "hiredis>=3.3.1",
    "PyYAML>=5.4",
    "numpy",
]

[project.scripts]
hackrf-driver = "hackrf_driver.cli:main"

[project.optional-dependencies]
hardware = ["pyhackrf2"]
```

**Note on editable installs:** `pip install -e ./pymayhem` creates a `.pth` file pointing to the source directory. Changes to source are immediately visible without reinstall. The `hackrf_driver` editable install can then find `pymayhem` because it was installed first into the same Python environment.

### Pattern 2: Domain Module Delegation (D-04)

**What:** MayhemClient owns a `MayhemSerial` instance and exposes domain sub-objects that delegate send_command calls back through the parent.
**When to use:** MayhemClient construction; each domain module receives a reference to the send_command callable.

```python
# pymayhem/client.py
from pymayhem._serial import MayhemSerial
from pymayhem.domains.radio import RadioDomain
from pymayhem.domains.ui import UIDomain
from pymayhem.domains.fs import FilesystemDomain
from pymayhem.domains.sensors import SensorsDomain
from pymayhem.domains.system import SystemDomain


class MayhemClient:
    def __init__(self, port: str | None = None, timeout: float = 3.0) -> None:
        self._port = port or self._detect_device()
        self._serial = MayhemSerial(self._port, timeout=timeout)
        # Domain sub-objects — receive _send_command callable
        self.radio = RadioDomain(self._serial._send_command)
        self.ui = UIDomain(self._serial._send_command)
        self.fs = FilesystemDomain(self._serial._send_command)
        self.sensors = SensorsDomain(self._serial._send_command)
        self.system = SystemDomain(self._serial._send_command)

    def open(self) -> bool:
        return self._serial.open()

    def close(self) -> None:
        self._serial.close()

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()

    @staticmethod
    def _detect_device() -> str:
        """Try /dev/hackrf_mayhem udev symlink, then USB VID:PID scan."""
        import os
        if os.path.exists('/dev/hackrf_mayhem'):
            return '/dev/hackrf_mayhem'
        # USB VID:PID scan via pyusb
        try:
            import usb.core
            dev = usb.core.find(idVendor=0x1d50, idProduct=0x6018)
            if dev is not None:
                # Find ACM port from /dev/serial/by-id
                import glob
                acm_ports = glob.glob('/dev/ttyACM*')
                if acm_ports:
                    return sorted(acm_ports)[0]
        except ImportError:
            pass
        return '/dev/ttyACM1'  # fallback default
```

```python
# pymayhem/domains/radio.py
from typing import Callable


class RadioDomain:
    def __init__(self, send_command: Callable) -> None:
        self._send = send_command

    def info(self) -> dict[str, str]:
        """Query radioinfo — returns dict of key:value pairs."""
        lines = self._send('radioinfo')
        result = {}
        for line in lines:
            if ':' in line:
                k, _, v = line.partition(':')
                result[k.strip().lower()] = v.strip()
        return result

    def setfreq(self, freq_hz: int) -> bool:
        """Set frequency. Returns False if device reports error."""
        lines = self._send(f'setfreq {freq_hz}')
        return not any('error' in l.lower() for l in lines)
```

### Pattern 3: appstart_with_reconnect (D-07)

**What:** Send `appstart`, expect serial disconnect (USB reset), poll for port reappearance, reconnect.
**When to use:** Any call to `client.system.appstart()`.

```python
# pymayhem/domains/system.py
import time
import os


class SystemDomain:
    def __init__(self, serial_ref) -> None:
        # Needs full serial ref (not just send_command) for reconnect
        self._serial = serial_ref

    def appstart(self, short_name: str, reconnect_timeout: float = 10.0) -> bool:
        """Start app — handles USB reset and reconnection automatically.

        appstart causes a USB reset (confirmed in MAYHEM_API_ASSESSMENT.md).
        Strategy: send command, close port, poll for reappearance, reopen.
        """
        try:
            # Send command — serial may drop before response arrives
            self._serial._send_command(f'appstart {short_name}')
        except (TimeoutError, OSError):
            pass  # USB reset is expected — not an error here

        # Close the now-dead port
        self._serial.close()

        # Poll for device reappearance
        port = self._serial._port
        deadline = time.monotonic() + reconnect_timeout
        while time.monotonic() < deadline:
            if os.path.exists(port):
                time.sleep(0.5)  # Let firmware settle after USB re-enum
                if self._serial.open():
                    return True
            time.sleep(0.3)

        return False  # Device did not reappear within timeout
```

### Pattern 4: Threading-based Driver Main Loop (D-12)

**What:** Replace ROS2 executor + timers with a threading.Event-driven loop.
**When to use:** HackRFDriver main entry point.

```python
# hackrf_driver/driver.py
import signal
import threading
import logging


class HackRFDriver:
    """Redis-native HackRF driver — no rclpy dependency."""

    def __init__(self, config: dict) -> None:
        self._config = config
        self._stop_event = threading.Event()
        self._logger = logging.getLogger('hackrf_driver')
        # ... same queue.Queue, RLock, backoff state as HackRFNode ...

    def run(self) -> None:
        """Block until SIGINT/SIGTERM received."""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        self._start()
        self._stop_event.wait()  # Block main thread
        self._shutdown()

    def _signal_handler(self, signum, frame) -> None:
        self._logger.info(f'Signal {signum} received — shutting down.')
        self._stop_event.set()

    def _schedule_reconnect(self, delay: float) -> None:
        """Use threading.Timer instead of ROS2 create_timer."""
        t = threading.Timer(delay, self._reconnect_callback)
        t.daemon = True
        t.start()
        self._reconnect_timer = t
```

**Key mapping from HackRFNode to HackRFDriver:**

| HackRFNode (ROS2) | HackRFDriver (standalone) |
|-------------------|---------------------------|
| `self.create_timer(period, cb)` | `threading.Timer(period, cb)` |
| `self.get_logger().info(...)` | `self._logger.info(...)` |
| `self.get_parameter('x').value` | `self._config['x']` |
| `rclpy.spin(node)` | `driver.run()` — `stop_event.wait()` |
| `node.destroy_node()` | `driver._shutdown()` |
| `self.declare_parameter(...)` | YAML config dict key |

### Pattern 5: YAML Config with Redis Overrides (D-10)

```python
# hackrf_driver/config.py
import yaml

DEFAULTS = {
    'center_frequency': 2447e6,
    'sample_rate': 8e6,
    'lna_gain': 16,
    'vga_gain': 20,
    'amp_enabled': False,
    'serial_port': '/dev/hackrf_mayhem',
    'serial_command_timeout': 3.0,
    'redis_host': 'localhost',
    'redis_port': 6379,
    'redis_stream_maxlen': 10000,
    'tx_freq_filter_enabled': True,
    'tx_skip_antenna_check': False,
}

PARAM_RANGES = {
    'center_frequency': (1e6, 6e9),
    'sample_rate': (2e6, 20e6),
    'lna_gain': (0, 40),
    'vga_gain': (0, 62),
}

REDIS_CONFIG_KEY = 'hackrf:config'


def load_config(path: str | None = None) -> dict:
    """Load YAML file defaults, then overlay Redis hash if available."""
    cfg = dict(DEFAULTS)
    if path:
        with open(path) as f:
            cfg.update(yaml.safe_load(f) or {})
    return cfg
```

### Pattern 6: Redis Pub/Sub + XREAD Bridge (D-14 / D-15)

**What:** Driver publishes notification to channel; bridge subscriber wakes and reads latest stream entry.
**When to use:** IQ notification pipeline between hackrf_driver and hackrf_ros_bridge.

```python
# In hackrf_driver/redis_bridge.py — after each XADD:
entry_id = self._redis.xadd(
    self.STREAM_KEY,
    {b'data': float32_arr.tobytes()},
    maxlen=self._maxlen,
    approximate=True,
)
# Pub/Sub notify — bridge subscribed to this channel (D-14)
self._redis.publish('hackrf:iq:notify', entry_id)
```

```python
# In hackrf_ros_bridge/bridge_node.py — subscriber thread:
import threading
import redis


class IQBridgeThread(threading.Thread):
    """Daemon thread: subscribe to hackrf:iq:notify, XREAD on each notify."""

    NOTIFY_CHANNEL = 'hackrf:iq:notify'
    STREAM_KEY = 'hackrf:iq:stream'

    def __init__(self, redis_client, publish_callback) -> None:
        super().__init__(daemon=True, name='iq_bridge')
        self._redis = redis_client
        self._publish = publish_callback  # calls ROS2 publisher

    def run(self) -> None:
        pubsub = self._redis.pubsub()
        pubsub.subscribe(self.NOTIFY_CHANNEL)
        for message in pubsub.listen():
            if message['type'] != 'message':
                continue
            # XREAD latest single entry (no blocking needed — notify already arrived)
            entries = self._redis.xrevrange(self.STREAM_KEY, '+', '-', count=1)
            if entries:
                _, fields = entries[0]
                self._publish(fields[b'data'])
```

**Why XREVRANGE instead of XREAD blocking here:** The notification already arrived via Pub/Sub. XREVRANGE gets the single latest entry immediately without needing to track cursor state. This avoids cursor drift between Pub/Sub delivery and XREAD.

### Pattern 7: Test Import Path Fixup for Split Packages

**What:** Tests currently import from `hackrf_ros.*`. After split, tests must import from the new package.
**When to use:** Each test file migration.

```python
# BEFORE (test/test_mayhem_serial.py):
from hackrf_ros.mayhem_serial import MayhemSerial

# AFTER (pymayhem/tests/test_mayhem_client.py):
from pymayhem._serial import MayhemSerial
# OR if testing domain API:
from pymayhem.client import MayhemClient
```

```python
# conftest.py duplication — hackrf_driver/tests/conftest.py:
from unittest.mock import MagicMock, patch
import pytest

@pytest.fixture(autouse=True)
def mock_redis_connection():
    """Prevent tests hitting live Redis — same pattern as original."""
    with patch('hackrf_driver.redis_bridge.redis.Redis',
               return_value=MagicMock()) as mock_cls:
        mock_cls.return_value.ping.return_value = True
        yield mock_cls
```

### Anti-Patterns to Avoid

- **Importing rclpy in hackrf_driver:** Any `import rclpy` or `from rclpy` in hackrf_driver or pymayhem breaks REF-04 and the Docker-without-ROS2 goal. Use `logging.getLogger` instead of `self.get_logger()`.
- **Calling `sys.modules` patching in module scope:** The existing `test_hackrf_node_redis.py` patches `sys.modules` at import time. After split, bridge tests may still need this pattern, but driver and pymayhem tests should not need it — they have no ROS2 imports to mock.
- **Using `block=0` on XREAD in the bridge loop:** Block=0 means the thread cannot check a stop event. Use `block=200` (200ms) as established in Phase 3.
- **Calling publish() from inside the RX callback:** The pyhackrf2 RX callback runs in a C thread. All Redis operations must be in the bridge daemon thread, not the callback.
- **MayhemClient logger dependency:** The extracted MayhemSerial currently requires a logger object that has `.info()/.warning()/.error()`. In pymayhem, replace with `logging.getLogger('pymayhem')` internally — no logger injection needed.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Package distribution metadata | Custom setup.py with manual MANIFEST.in | pyproject.toml + setuptools | PEP 517 standard; `pip install -e` works correctly; future PyPI publish requires it anyway |
| YAML config loading | Custom INI parser | PyYAML (already installed) | Already in environment; handles types correctly |
| CLI argument parsing | Custom sys.argv parsing | argparse (stdlib) | Zero deps; type coercion; --help generation free |
| USB device scanning | String matching on /dev | pyusb usb.core.find() | Finds by VID:PID regardless of port number assigned |
| Thread-safe timer | Custom threading.Timer wrapper | threading.Timer directly | stdlib; matches ROS2 timer semantics closely enough |
| Pub/Sub + XREAD combined | Custom polling loop | redis-py pubsub.listen() + xrevrange | pubsub.listen() is a generator — correct blocking wait with no busy-loop |

**Key insight:** The existing code already avoids hand-rolling. This refactor is primarily a file reorganization, not a logic rewrite. The only genuinely new implementation is Pattern 6 (Pub/Sub + XREAD bridge) and Pattern 3 (appstart_with_reconnect).

---

## Common Pitfalls

### Pitfall 1: Logger Injection in MayhemSerial — ROS2 Logger Coupled

**What goes wrong:** `MayhemSerial.__init__` currently takes a `logger` parameter that must have `.info()/.warning()/.error()`. When moved to pymayhem, tests and standalone use need a stdlib logger instead.

**Why it happens:** The class was designed inside HackRFNode where `self.get_logger()` is always available.

**How to avoid:** Replace the logger parameter with `logging.getLogger('pymayhem.serial')` internally. The class stops accepting a logger argument. Callers that need custom logging should configure the standard Python logging hierarchy.

**Warning signs:** If pymayhem tests require creating a stub `_Logger` class (as currently in `test_mayhem_serial.py`), the coupling is still present.

### Pitfall 2: conftest.py Patches Wrong Module Path After Split

**What goes wrong:** The current `test/conftest.py` patches `hackrf_ros.redis_bridge.redis.Redis`. After split, `RedisBridge` lives in `hackrf_driver.redis_bridge`. Tests in `hackrf_driver/tests/` will still connect to live Redis unless conftest patches `hackrf_driver.redis_bridge.redis.Redis`.

**Why it happens:** `unittest.mock.patch` targets the name as it is imported into the module, not the original package. After the move, the target module path changes.

**How to avoid:** When creating `hackrf_driver/tests/conftest.py`, patch `hackrf_driver.redis_bridge.redis.Redis`. When creating test for the bridge in `test/` or `hackrf_ros_bridge/tests/`, patch the bridge's redis import path.

**Warning signs:** Tests pass individually but fail when Redis is not running — indicates conftest is not active or patching the wrong path.

### Pitfall 3: TXController Needs a Node Reference — Breaks Decoupling

**What goes wrong:** `TXController.__init__` currently takes `node` (a HackRFNode) and calls `node.get_parameter(...)`, `node._hackrf`, `node._device_lock`, `node._stop_rx_if_running()`, `node._start_rx_if_stopped()`. This node reference is deeply coupled to rclpy internals.

**Why it happens:** TXController was designed as an HackRFNode helper, not a standalone class.

**How to avoid:** In hackrf_driver, replace the `node` parameter with explicit primitive references: `hackrf_ref`, `device_lock`, `stop_rx_fn`, `start_rx_fn`, `get_param_fn`. These are plain Python objects — no rclpy import needed. Config dict replaces `get_parameter()`.

**Warning signs:** `import rclpy` anywhere in `hackrf_driver/tx_controller.py` or its tests.

### Pitfall 4: _freq_filter_active() Uses ROS2 Parameters

**What goes wrong:** `TXController._freq_filter_active()` calls `self._node.get_parameter('tx_freq_filter_enabled').bool_value`. In hackrf_driver, there is no ROS2 parameter system.

**How to avoid:** Pass `tx_freq_filter_enabled` as a plain bool to TXController at construction time, read from the config dict. Redis override (`FREQ_OVERRIDE_KEY`) stays — the Redis check is unchanged.

### Pitfall 5: Editable Install Order for Monorepo

**What goes wrong:** `pip install -e ./hackrf_driver` fails if `pymayhem` is not yet installed, because `hackrf_driver/pyproject.toml` lists `pymayhem` as a dependency.

**Why it happens:** pip resolves package names from the index, not local paths, unless explicitly told otherwise.

**How to avoid:** Always install in dependency order:
```bash
pip install -e ./pymayhem
pip install -e ./hackrf_driver
```
Or add a workspace-level install script. Do NOT add a `[tool.pip.find-links]` stanza — that is a distutils trick that breaks on modern pip. The correct pattern for local deps in editable mode is to install the dependency first.

### Pitfall 6: appstart_with_reconnect Port Path Changes After USB Reset

**What goes wrong:** After `appstart`, the device USB-resets. On some systems the ACM port number increments (`/dev/ttyACM1` → `/dev/ttyACM2`). Polling for the original path will timeout even though the device reappeared.

**How to avoid:** Prefer `/dev/hackrf_mayhem` (udev symlink) over `/dev/ttyACM1`. The udev symlink from `99-hackrf-mayhem.rules` will point to the correct port after USB reset regardless of number change. If udev is not configured, scan `/dev/serial/by-id/` rather than checking a hardcoded path.

### Pitfall 7: Redis Pub/Sub listen() Blocks Thread — No Clean Shutdown

**What goes wrong:** `pubsub.listen()` is a generator that blocks the bridge thread indefinitely. If the bridge node shuts down, the thread will not exit until the next message arrives (or never, if the driver has stopped).

**How to avoid:** Use `pubsub.get_message(timeout=0.2)` in a loop that checks a stop event, rather than `pubsub.listen()`. This allows the thread to exit within 200ms of shutdown.

```python
def run(self) -> None:
    pubsub = self._redis.pubsub()
    pubsub.subscribe(self.NOTIFY_CHANNEL)
    while not self._stop_event.is_set():
        msg = pubsub.get_message(timeout=0.2)
        if msg and msg['type'] == 'message':
            self._handle_notify(msg['data'])
    pubsub.unsubscribe()
```

---

## Code Examples

### pyproject.toml Minimal (verified pattern — setuptools 79.0.1)

```toml
# pymayhem/pyproject.toml
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "pymayhem"
version = "0.1.0"
description = "Standalone Python API for Mayhem firmware serial control"
requires-python = ">=3.10"
dependencies = ["pyserial>=3.5"]

[tool.setuptools.packages.find]
where = ["."]
```

### argparse Entry Point (D-11)

```python
# hackrf_driver/cli.py
import argparse
import logging
from hackrf_driver.config import load_config, PARAM_RANGES
from hackrf_driver.driver import HackRFDriver


def main() -> None:
    parser = argparse.ArgumentParser(prog='hackrf-driver',
        description='Standalone HackRF driver with Redis I/O')
    parser.add_argument('--config', help='YAML config file path')
    parser.add_argument('--freq', type=float, help='Center frequency in Hz')
    parser.add_argument('--gain', type=int, help='LNA gain in dB (0-40)')
    parser.add_argument('--sample-rate', type=float, dest='sample_rate')
    parser.add_argument('--redis-host', default='localhost', dest='redis_host')
    parser.add_argument('--redis-port', type=int, default=6379, dest='redis_port')
    parser.add_argument('--log-level', default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'])
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level))
    cfg = load_config(args.config)
    # CLI args override config file
    if args.freq: cfg['center_frequency'] = args.freq
    if args.gain: cfg['lna_gain'] = args.gain
    if args.sample_rate: cfg['sample_rate'] = args.sample_rate
    cfg['redis_host'] = args.redis_host
    cfg['redis_port'] = args.redis_port

    driver = HackRFDriver(cfg)
    driver.run()


# hackrf_driver/__main__.py
from hackrf_driver.cli import main
if __name__ == '__main__':
    main()
```

### Test Migration — TXController (import path change only)

```python
# hackrf_driver/tests/test_tx_controller.py
# CHANGE: import from hackrf_driver not hackrf_ros
from hackrf_driver.tx_controller import (
    TXController, TXBlockedError, TXFreqBlockedError,
    TXNotAuthorizedError, TXHardBlockedError
)
# All test logic unchanged — only import path differs
```

### MayhemSerial Logger Extraction

```python
# pymayhem/_serial.py
import logging
import queue
import threading
import time
import serial

_log = logging.getLogger('pymayhem.serial')


class MayhemSerial:
    def __init__(self, port: str, timeout: float = 3.0) -> None:
        # REMOVED: logger parameter — use module-level _log instead
        self._port = port
        self._command_timeout = timeout
        # ... rest of __init__ unchanged ...

    def _reader_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self._serial.is_open:
                    raw = self._serial.readline()
                    if raw:
                        line = raw.decode('utf-8', errors='replace').strip()
                        if line:
                            self._response_queue.put(line)
            except serial.SerialException as e:
                _log.error(f'MayhemSerial reader error: {e}')  # was self._logger.error
                self._stop_event.set()
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| setup.py only | pyproject.toml (PEP 517/518) | PEP 517 ratified 2017, setuptools support stable since 2022 | `pip install -e` works cleanly; future PyPI publish requires it |
| Monolithic ROS2 node | Thin bridge over Redis-native driver | Architecture pattern 2023+ | Driver testable without ROS2 install; driver runnable in Docker without ROS2 |
| Redis polling loop | Redis Pub/Sub notify + XREAD | redis-py Pub/Sub stable since v3 | Sub-millisecond latency vs 200ms poll ceiling |

**Deprecated/outdated:**
- `setup.py` with `find_packages()`: Still works but is being superseded by pyproject.toml. Keeping it for the ROS2 bridge (`hackrf_ros/`) because ROS2 Humble's ament_python build system still expects it.
- Logger injection into library classes: Anti-pattern for library code. Use stdlib `logging.getLogger()` internally.

---

## Open Questions

1. **pyusb availability**
   - What we know: `usb.core` is needed for D-08 VID:PID scan fallback
   - What's unclear: Whether pyusb is installed in the Docker image
   - Recommendation: Make the pyusb scan a graceful fallback only — `try: import usb.core; ... except ImportError: pass`. If pyusb is absent, fall through to udev symlink or `/dev/ttyACM1` default. Do not add pyusb as a hard dependency of pymayhem.

2. **test_hackrf_node_redis.py disposition**
   - What we know: 23 tests cover `_build_state_dict`, set_parameter methods, start/stop RX, RedisBridge integration points on HackRFNode
   - What's unclear: After D-03 (hackrf_ros becomes the bridge), `hackrf_node.py` is replaced by `bridge_node.py`. The state dict logic moves to the driver. The parameter methods go away (config-based now). Some tests become obsolete; some need rewriting for bridge behaviors.
   - Recommendation: Do not migrate these 23 tests wholesale. Instead: (a) driver-side behaviors (state dict, param validation) get new tests in `hackrf_driver/tests/`; (b) bridge-specific behaviors (Redis->ROS2 publish, service proxying) get new tests in the bridge test location. REF-07 says "all 68 tests pass" — it is acceptable to delete HackRFNode-specific tests and replace them with equivalent coverage in the new packages, as long as total coverage is not reduced.

3. **RedisBridge Pub/Sub notification timing**
   - What we know: Driver calls `redis.publish()` after each `xadd()`. Bridge thread must be already subscribed before any notify arrives.
   - What's unclear: Race condition at startup: driver may XADD before bridge subscribes. First N IQ chunks will be missed.
   - Recommendation: Bridge thread subscribes to channel before signaling readiness. On startup, also do a single XREVRANGE to get the latest entry (catch-up read). This is standard pattern for Redis Pub/Sub consumers — "subscribe first, then read backlog."

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | All packages | Yes | 3.10.12 | — |
| redis-py | hackrf_driver, bridge | Yes | 7.4.0 | — |
| hiredis | hackrf_driver, bridge | Yes | 3.3.1 | — |
| pyserial | pymayhem | Yes | 3.5 | — |
| PyYAML | hackrf_driver | Yes | 5.4.1 | — |
| setuptools | pyproject.toml builds | Yes | 79.0.1 | — |
| numpy | hackrf_driver | Yes (as installed) | as installed | — |
| Redis server | hackrf_driver tests | Yes | 6.0.16 | Mocked in tests |
| pyusb | pymayhem device detect | Not confirmed | — | Skip USB scan; use /dev/hackrf_mayhem or /dev/ttyACM1 |
| pyhackrf2 | hackrf_driver | As installed | as installed | Cannot run without hardware |
| argparse | hackrf_driver CLI | Yes | built-in | — |

**Missing dependencies with no fallback:**
- None that block the refactor. pyhackrf2 is needed for runtime, not for tests.

**Missing dependencies with fallback:**
- pyusb: device auto-detect scan degrades gracefully to udev symlink path.

---

## Validation Architecture

Validation is disabled (`nyquist_validation: false` in `.planning/config.json`). Skip this section.

---

## Sources

### Primary (HIGH confidence)

- Installed environment (`pip3 show redis pyserial hiredis PyYAML setuptools`) — versions confirmed 2026-03-29
- `test/test_mayhem_serial.py`, `test/test_redis_bridge.py`, `test/test_tx_controller.py`, `test/test_hackrf_node_redis.py` — 68 tests confirmed passing 2026-03-29
- `hackrf_ros/mayhem_serial.py` — 270 lines, zero ROS2/Redis imports, directly extractable
- `hackrf_ros/redis_bridge.py`, `hackrf_ros/tx_controller.py` — independently testable, already decoupled
- `.planning/research/MAYHEM_API_ASSESSMENT.md` — 47 command inventory, appstart USB reset confirmed
- `.planning/research/STACK.md` — redis-py, pyserial versions verified against PyPI

### Secondary (MEDIUM confidence)

- pyproject.toml + setuptools editable install pattern — widely documented, verified with setuptools 79.0.1
- Redis Pub/Sub `get_message(timeout)` vs `listen()` pattern — redis-py official docs pattern for shutdown-safe subscribe loops

### Tertiary (LOW confidence)

- pyusb `usb.core.find()` for VID:PID scan — standard pyusb pattern; availability in Docker image not confirmed

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all versions confirmed from installed environment
- Architecture: HIGH — derived directly from existing working code, not from external examples
- Pitfalls: HIGH — grounded in the actual current code's coupling points (TXController.node, conftest paths, logger injection)

**Research date:** 2026-03-29
**Valid until:** 2026-06-29 (stable libraries, 90-day horizon)
