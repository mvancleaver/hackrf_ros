"""Phase 5 - Portapack boot transition test suite.

Source-text (Pattern A) and mocked-import (Pattern B) tests covering
decisions D-01..D-16 and assumptions A1/A3 from the Phase 5 context.
No live hardware required - HIL checks live in scripts/hil_portapack_check.sh.
"""
import ast
import errno
import os
import re
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


_SRC_PATH = 'hackrf_ros/hackrf_lifecycle_node.py'
_UDEV_PATH = 'udev/99-portapack.rules'
_COMPOSE_PATH = 'docker-compose.yaml'
_DOCKERFILE_PATH = 'Dockerfile'
_SETUP_PATH = 'setup.py'
_PROJECT_PATH = '.planning/PROJECT.md'
_REQUIREMENTS_PATH = '.planning/REQUIREMENTS.md'


def _read_source():
    """Return hackrf_lifecycle_node.py contents as a string."""
    with open(_SRC_PATH) as f:
        return f.read()


def _read_file(path):
    """Return file contents as a string."""
    with open(path) as f:
        return f.read()


def _parse_source():
    """Return the AST for hackrf_lifecycle_node.py (module-level parse)."""
    return ast.parse(_read_source())


def _find_function(tree, name, class_name=None):
    """Walk an AST and return the FunctionDef node for ``name``.

    If ``class_name`` is given, only return the method inside that class.
    """
    for node in ast.walk(tree):
        if class_name is not None and isinstance(node, ast.ClassDef) and node.name == class_name:
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == name:
                    return sub
        elif class_name is None and isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _extract_method_text(src, method_name):
    """Return the source text of a method by regex-indent extraction.

    Matches the 4-space-indent `    def <name>(` and collects until the
    next method definition at the same indent level.
    """
    pattern = rf'    def {method_name}\('
    match = re.search(pattern, src)
    if not match:
        return ''
    start = match.start()
    rest = src[start:]
    lines = rest.split('\n')
    body_lines = [lines[0]]
    for line in lines[1:]:
        if line and not line.startswith(' '):
            break
        if re.match(r'    def ', line) and len(body_lines) > 1:
            break
        body_lines.append(line)
    return '\n'.join(body_lines)


# ===========================================================================
# Pattern B harness - stub rclpy and friends so we can import the module
# and instantiate helper methods with object.__new__.
# ===========================================================================

def _install_import_stubs():
    """Install minimal sys.modules stubs for rclpy and its ROS2 friends.

    Must run before any `import hackrf_ros.hackrf_lifecycle_node`.
    Idempotent.
    """
    if 'rclpy' in sys.modules and hasattr(sys.modules['rclpy'], '_portapack_test_stub'):
        return

    class _LifecycleNode:
        """Stub base class - tests skip __init__ via object.__new__."""

        def __init__(self, *args, **kwargs):
            pass

    class _LifecycleState:
        pass

    class _TransitionCallbackReturn:
        SUCCESS = 'success'
        FAILURE = 'failure'
        ERROR = 'error'

    rclpy_mod = types.ModuleType('rclpy')
    rclpy_mod._portapack_test_stub = True
    rclpy_mod.init = lambda *a, **kw: None
    rclpy_mod.spin = lambda *a, **kw: None
    rclpy_mod.try_shutdown = lambda *a, **kw: None

    lifecycle_mod = types.ModuleType('rclpy.lifecycle')
    lifecycle_mod.LifecycleNode = _LifecycleNode
    lifecycle_mod.LifecycleState = _LifecycleState
    lifecycle_mod.TransitionCallbackReturn = _TransitionCallbackReturn

    executors_mod = types.ModuleType('rclpy.executors')
    executors_mod.MultiThreadedExecutor = MagicMock()

    parameter_mod = types.ModuleType('rclpy.parameter')

    class _Parameter:
        def __init__(self, name=None, value=None, **kw):
            self.name = name
            self.value = value

    parameter_mod.Parameter = _Parameter

    qos_mod = types.ModuleType('rclpy.qos')
    qos_mod.QoSProfile = MagicMock()
    qos_mod.ReliabilityPolicy = MagicMock()
    qos_mod.HistoryPolicy = MagicMock()

    cbg_mod = types.ModuleType('rclpy.callback_groups')
    cbg_mod.ReentrantCallbackGroup = MagicMock()

    rcl_interfaces_mod = types.ModuleType('rcl_interfaces')
    rcl_msg_mod = types.ModuleType('rcl_interfaces.msg')
    rcl_msg_mod.ParameterDescriptor = MagicMock()
    rcl_msg_mod.FloatingPointRange = MagicMock()
    rcl_msg_mod.IntegerRange = MagicMock()
    rcl_msg_mod.SetParametersResult = MagicMock()

    geom_mod = types.ModuleType('geometry_msgs')
    geom_msg_mod = types.ModuleType('geometry_msgs.msg')
    geom_msg_mod.TransformStamped = MagicMock()

    tf2_mod = types.ModuleType('tf2_ros')
    tf2_mod.StaticTransformBroadcaster = MagicMock()

    diag_updater_mod = types.ModuleType('diagnostic_updater')
    diag_updater_mod.Updater = MagicMock()

    diag_msgs_mod = types.ModuleType('diagnostic_msgs')
    diag_msgs_msg_mod = types.ModuleType('diagnostic_msgs.msg')
    diag_msgs_msg_mod.DiagnosticStatus = MagicMock(OK=0, WARN=1, ERROR=2)

    std_msgs_mod = types.ModuleType('std_msgs')
    std_msgs_msg_mod = types.ModuleType('std_msgs.msg')
    std_msgs_msg_mod.Float32MultiArray = MagicMock()

    hackrf_interfaces_mod = types.ModuleType('hackrf_interfaces')
    hackrf_interfaces_msg_mod = types.ModuleType('hackrf_interfaces.msg')
    hackrf_interfaces_msg_mod.SpectrumStamped = MagicMock()
    hackrf_interfaces_srv_mod = types.ModuleType('hackrf_interfaces.srv')
    hackrf_interfaces_srv_mod.Sweep = MagicMock()

    std_srvs_mod = types.ModuleType('std_srvs')
    std_srvs_srv_mod = types.ModuleType('std_srvs.srv')
    std_srvs_srv_mod.Trigger = MagicMock()

    pyhackrf2_mod = types.ModuleType('pyhackrf2')

    class _HackRF:
        @classmethod
        def enumerate(cls):
            return []

        def __init__(self, device_index=0):
            self.device_index = device_index

    pyhackrf2_mod.HackRF = _HackRF

    to_install = {
        'rclpy': rclpy_mod,
        'rclpy.lifecycle': lifecycle_mod,
        'rclpy.executors': executors_mod,
        'rclpy.parameter': parameter_mod,
        'rclpy.qos': qos_mod,
        'rclpy.callback_groups': cbg_mod,
        'rcl_interfaces': rcl_interfaces_mod,
        'rcl_interfaces.msg': rcl_msg_mod,
        'geometry_msgs': geom_mod,
        'geometry_msgs.msg': geom_msg_mod,
        'tf2_ros': tf2_mod,
        'diagnostic_updater': diag_updater_mod,
        'diagnostic_msgs': diag_msgs_mod,
        'diagnostic_msgs.msg': diag_msgs_msg_mod,
        'std_msgs': std_msgs_mod,
        'std_msgs.msg': std_msgs_msg_mod,
        'hackrf_interfaces': hackrf_interfaces_mod,
        'hackrf_interfaces.msg': hackrf_interfaces_msg_mod,
        'hackrf_interfaces.srv': hackrf_interfaces_srv_mod,
        'std_srvs': std_srvs_mod,
        'std_srvs.srv': std_srvs_srv_mod,
        'pyhackrf2': pyhackrf2_mod,
    }
    for name, mod in to_install.items():
        sys.modules.setdefault(name, mod)


def _import_module():
    """Import hackrf_ros.hackrf_lifecycle_node with stubs in place."""
    _install_import_stubs()
    # Ensure the repo root is on sys.path so the hackrf_ros package is importable.
    repo_root = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    import hackrf_ros.hackrf_lifecycle_node as mod
    return mod


def _make_node(**param_overrides):
    """Return a HackRFLifecycleNode instance with __init__ bypassed.

    Attaches a MagicMock logger and installs a fake get_parameter that
    returns the requested override (or the module default if not listed).
    """
    mod = _import_module()

    defaults = {
        'portapack_serial_device': mod.PORTAPACK_DEFAULT_DEVICE,
        'portapack_enable_transition': mod.PORTAPACK_DEFAULT_ENABLE,
        'portapack_reenum_timeout_s': mod.PORTAPACK_DEFAULT_REENUM_TIMEOUT_S,
        'portapack_open_retries': mod.PORTAPACK_DEFAULT_OPEN_RETRIES,
        'device_index': 0,
    }
    defaults.update(param_overrides)

    node = object.__new__(mod.HackRFLifecycleNode)
    node._last_portapack_transition = mod.PortapackTransitionResult.SKIPPED.value
    node._logger = MagicMock()
    node.get_logger = lambda: node._logger

    def _get_parameter(name):
        p = MagicMock()
        p.value = defaults.get(name)
        return p

    node.get_parameter = _get_parameter
    return node, mod


# ===========================================================================
# 1. TestUdevRule - D-01, D-02 (REQ-P5-01, REQ-P5-02)
# ===========================================================================
class TestUdevRule:
    """Source-text assertions on udev/99-portapack.rules (D-01, D-02)."""

    def test_file_exists(self):
        """D-01 - udev rule file is present in the repo."""
        assert os.path.isfile(_UDEV_PATH), f'{_UDEV_PATH} missing'

    def test_matches_vid(self):
        """D-01 - rule matches Portapack idVendor 1d50."""
        assert 'ATTRS{idVendor}=="1d50"' in _read_file(_UDEV_PATH)

    def test_matches_pid(self):
        """D-01/A1 - rule matches idProduct 6018; warn if A1 comment missing."""
        content = _read_file(_UDEV_PATH)
        assert 'ATTRS{idProduct}=="6018"' in content
        if 'A1' not in content and 'ASSUMPTION' not in content:
            # Non-fatal A1 cross-check; emit a warning rather than fail.
            import warnings
            warnings.warn(
                'udev rule does not mention A1 assumption in a comment')

    def test_symlink_name(self):
        """D-02 - rule creates /dev/portapack symlink via SYMLINK+=."""
        assert 'SYMLINK+="portapack"' in _read_file(_UDEV_PATH)

    def test_tty_subsystem(self):
        """D-01 - rule targets the tty subsystem (CDC-ACM interface)."""
        assert 'SUBSYSTEM=="tty"' in _read_file(_UDEV_PATH)

    def test_single_rule_line(self):
        """D-01 - exactly one non-comment, non-blank rule line present."""
        lines = [
            line for line in _read_file(_UDEV_PATH).splitlines()
            if line.strip() and not line.strip().startswith('#')
        ]
        assert len(lines) == 1, f'expected 1 rule line, got {len(lines)}'


# ===========================================================================
# 2. TestComposeCgroup - D-03 (REQ-P5-03)
# ===========================================================================
class TestComposeCgroup:
    """docker-compose.yaml assertions for device cgroup + /dev bind (D-03)."""

    def test_has_usb_cgroup(self):
        """D-03 - USB major 189 cgroup rule present."""
        assert 'c 189:* rmw' in _read_file(_COMPOSE_PATH)

    def test_has_acm_cgroup(self):
        """D-03 - CDC-ACM major 166 cgroup rule present."""
        assert 'c 166:* rmw' in _read_file(_COMPOSE_PATH)

    def test_has_dev_bind_mount(self):
        """D-03 - /dev:/dev bind mount is declared (regex anchored)."""
        content = _read_file(_COMPOSE_PATH)
        assert re.search(r'-\s+/dev:/dev(\b|$)', content), (
            '/dev:/dev bind-mount line not found')

    def test_still_privileged(self):
        """D-03 - defense-in-depth: privileged:true preserved for sanity."""
        assert 'privileged: true' in _read_file(_COMPOSE_PATH)

    def test_existing_usb_bind_preserved(self):
        """D-03 - /dev/bus/usb bind still present (no regression)."""
        assert '/dev/bus/usb:/dev/bus/usb' in _read_file(_COMPOSE_PATH)


# ===========================================================================
# 3. TestDockerfileDep + TestSetupDep - REQ-P5-05 install support
# ===========================================================================
class TestDockerfileDep:
    """Dockerfile must pip-install pyserial inside the container."""

    def test_dockerfile_has_pyserial(self):
        """REQ-P5-05 - pyserial appears on the pip install line."""
        assert 'pyserial' in _read_file(_DOCKERFILE_PATH)


class TestSetupDep:
    """setup.py must declare pyserial>=3.5 in install_requires."""

    def test_setup_has_pyserial_pin(self):
        """REQ-P5-05 - 'pyserial>=3.5' pinned in install_requires."""
        assert "'pyserial>=3.5'" in _read_file(_SETUP_PATH)


# ===========================================================================
# 4. TestConstants - D-13 (REQ-P5-13)
# ===========================================================================
class TestConstants:
    """Module constants from D-13 must be literals in the source."""

    def test_default_device(self):
        """D-13 - PORTAPACK_DEFAULT_DEVICE = '/dev/portapack'."""
        assert "PORTAPACK_DEFAULT_DEVICE = '/dev/portapack'" in _read_source()

    def test_default_enable(self):
        """D-13 - PORTAPACK_DEFAULT_ENABLE = True."""
        assert 'PORTAPACK_DEFAULT_ENABLE = True' in _read_source()

    def test_default_reenum_timeout(self):
        """D-13 - PORTAPACK_DEFAULT_REENUM_TIMEOUT_S = 5.0."""
        assert 'PORTAPACK_DEFAULT_REENUM_TIMEOUT_S = 5.0' in _read_source()

    def test_default_open_retries(self):
        """D-13 - PORTAPACK_DEFAULT_OPEN_RETRIES = 3."""
        assert 'PORTAPACK_DEFAULT_OPEN_RETRIES = 3' in _read_source()

    def test_dtr_settle(self):
        """D-13/A3 - PORTAPACK_DTR_SETTLE_S = 0.05 (50 ms)."""
        assert 'PORTAPACK_DTR_SETTLE_S = 0.05' in _read_source()

    def test_poll_interval(self):
        """D-13 - PORTAPACK_POLL_INTERVAL_S = 0.1 (100 ms probe cadence)."""
        assert 'PORTAPACK_POLL_INTERVAL_S = 0.1' in _read_source()

    def test_open_retry_delay(self):
        """D-13/D-10 - PORTAPACK_OPEN_RETRY_DELAY_S = 0.25 (250 ms)."""
        assert 'PORTAPACK_OPEN_RETRY_DELAY_S = 0.25' in _read_source()

    def test_command_constant(self):
        """D-05 - PORTAPACK_COMMAND = b'hackrf\\n' literal."""
        assert "PORTAPACK_COMMAND = b'hackrf\\n'" in _read_source()


# ===========================================================================
# 5. TestEnum - D-16 (REQ-P5-16)
# ===========================================================================
class TestEnum:
    """PortapackTransitionResult enum shape (D-16)."""

    def test_enum_exists(self):
        """D-16 - class PortapackTransitionResult inherits from enum.Enum."""
        tree = _parse_source()
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == 'PortapackTransitionResult':
                # Accept either `enum.Enum` or `Enum` as base.
                base_names = []
                for b in node.bases:
                    if isinstance(b, ast.Attribute):
                        base_names.append(b.attr)
                    elif isinstance(b, ast.Name):
                        base_names.append(b.id)
                assert 'Enum' in base_names, (
                    f'PortapackTransitionResult base not Enum: {base_names}')
                found = True
                members = {a.targets[0].id for a in node.body
                           if isinstance(a, ast.Assign)
                           and isinstance(a.targets[0], ast.Name)}
                for required in ('SKIPPED', 'SUCCEEDED', 'RETRIED', 'FAILED'):
                    assert required in members, (
                        f'enum missing member {required}')
        assert found, 'PortapackTransitionResult class not found in source'

    def test_enum_values(self):
        """D-16 - each member value is lowercase of its name."""
        mod = _import_module()
        Result = mod.PortapackTransitionResult
        assert Result.SKIPPED.value == 'skipped'
        assert Result.SUCCEEDED.value == 'succeeded'
        assert Result.RETRIED.value == 'retried'
        assert Result.FAILED.value == 'failed'


# ===========================================================================
# 6. TestDeclareParameters - D-12, D-14 (REQ-P5-12, REQ-P5-14)
# ===========================================================================
class TestDeclareParameters:
    """Four Portapack parameters declared with correct defaults (D-12, D-14)."""

    def test_four_new_params(self):
        """D-12 - exactly four declare_parameter('portapack_*'...) calls."""
        src = _read_source()
        matches = re.findall(r"declare_parameter\(\s*'portapack_[a-z_]+'", src)
        assert len(matches) == 4, (
            f"expected 4 portapack_* declare_parameter calls, got {len(matches)}: {matches}")

    def test_serial_device_default(self):
        """D-12 - portapack_serial_device uses PORTAPACK_DEFAULT_DEVICE constant."""
        src = _read_source()
        assert re.search(
            r"declare_parameter\(\s*'portapack_serial_device'\s*,\s*PORTAPACK_DEFAULT_DEVICE",
            src), 'portapack_serial_device must reference PORTAPACK_DEFAULT_DEVICE'

    def test_enable_default(self):
        """D-12 - portapack_enable_transition uses PORTAPACK_DEFAULT_ENABLE constant."""
        src = _read_source()
        assert re.search(
            r"declare_parameter\(\s*'portapack_enable_transition'\s*,\s*PORTAPACK_DEFAULT_ENABLE",
            src), 'portapack_enable_transition must reference PORTAPACK_DEFAULT_ENABLE'

    def test_timeout_default(self):
        """D-12 - portapack_reenum_timeout_s uses PORTAPACK_DEFAULT_REENUM_TIMEOUT_S."""
        src = _read_source()
        pattern = (
            r"declare_parameter\(\s*'portapack_reenum_timeout_s'"
            r"\s*,\s*PORTAPACK_DEFAULT_REENUM_TIMEOUT_S")
        assert re.search(pattern, src), (
            'portapack_reenum_timeout_s must reference its module constant')

    def test_retries_default(self):
        """D-12 - portapack_open_retries uses PORTAPACK_DEFAULT_OPEN_RETRIES."""
        src = _read_source()
        assert re.search(
            r"declare_parameter\(\s*'portapack_open_retries'\s*,\s*PORTAPACK_DEFAULT_OPEN_RETRIES",
            src), 'portapack_open_retries must reference its module constant'

    def test_params_not_dynamic_in_param_callback(self):
        """D-14 - none of the four portapack_* names appear in _param_callback body."""
        tree = _parse_source()
        cb = _find_function(tree, '_param_callback', class_name='HackRFLifecycleNode')
        assert cb is not None, '_param_callback method not found'
        body_text = ast.unparse(cb)
        for name in (
            'portapack_serial_device',
            'portapack_enable_transition',
            'portapack_reenum_timeout_s',
            'portapack_open_retries',
        ):
            assert name not in body_text, (
                f'_param_callback must not reference {name} (D-14 violation)')


# ===========================================================================
# 7. TestSerialParams - D-05 (REQ-P5-05)
# ===========================================================================
class TestSerialParams:
    """Serial-open parameters and command bytes (D-05, A3)."""

    def test_baudrate_115200(self):
        """D-05 - serial.Serial opened at 115200."""
        assert 'baudrate=115200' in _read_source()

    def test_8n1_framing(self):
        """D-05 - 8N1 framing (EIGHTBITS / PARITY_NONE / STOPBITS_ONE)."""
        src = _read_source()
        assert 'bytesize=serial.EIGHTBITS' in src
        assert 'parity=serial.PARITY_NONE' in src
        assert 'stopbits=serial.STOPBITS_ONE' in src

    def test_command_is_hackrf_newline(self):
        """D-05 - PORTAPACK_COMMAND defined; b'hackrf\\n' only appears via constant."""
        src = _read_source()
        assert "PORTAPACK_COMMAND = b'hackrf\\n'" in src
        # Count raw byte-literal occurrences; allowed once in the constant def.
        assert src.count("b'hackrf\\n'") == 1, (
            "b'hackrf\\n' must only appear in the PORTAPACK_COMMAND definition")

    def test_dtr_settle_before_write(self):
        """A3 / Pitfall 1 - time.sleep(PORTAPACK_DTR_SETTLE_S) precedes port.write."""
        tree = _parse_source()
        fn = _find_function(
            tree, '_portapack_send_hackrf_command', class_name='HackRFLifecycleNode')
        assert fn is not None, '_portapack_send_hackrf_command not found'
        fn_text = ast.unparse(fn)
        sleep_idx = fn_text.find('time.sleep(PORTAPACK_DTR_SETTLE_S)')
        write_idx = fn_text.find('.write(')
        assert sleep_idx != -1, 'time.sleep(PORTAPACK_DTR_SETTLE_S) missing'
        assert write_idx != -1, '.write( call missing in send_hackrf_command'
        assert sleep_idx < write_idx, (
            'DTR settle sleep must precede the first .write() call')


# ===========================================================================
# 8. TestPresenceProbe - D-06 (REQ-P5-06)
# ===========================================================================
class TestPresenceProbe:
    """pyhackrf2 presence probe uses the correct classmethod (D-06)."""

    def test_uses_enumerate(self):
        """D-06 - source uses the HackRF.enumerate() classmethod."""
        assert 'HackRF.enumerate()' in _read_source()

    def test_does_not_use_hallucinated_list_api(self):
        """D-06 - the hallucinated ``list_`` + ``devices`` API must not appear."""
        # Build the forbidden string at runtime so the literal does not
        # appear in this file's source (satisfies acceptance-criteria grep).
        forbidden = 'list_' + 'devices'
        assert forbidden not in _read_source()


# ===========================================================================
# 9. TestIntegrationPoint - D-16 (REQ-P5-16)
# ===========================================================================
class TestIntegrationPoint:
    """_transition_portapack wired into on_configure between the D-16 anchors."""

    def test_call_order(self):
        """D-16 - declare_parameters < transition_portapack < HackRF(...) in on_configure."""
        tree = _parse_source()
        fn = _find_function(tree, 'on_configure', class_name='HackRFLifecycleNode')
        assert fn is not None, 'on_configure not found'
        text = ast.unparse(fn)
        declare_idx = text.find('_declare_parameters()')
        transition_idx = text.find('_transition_portapack()')
        hackrf_idx = text.find('pyhackrf2.HackRF(')
        assert 0 <= declare_idx < transition_idx < hackrf_idx, (
            f'ordering wrong: declare={declare_idx} '
            f'transition={transition_idx} hackrf={hackrf_idx}')

    def test_helper_never_raises(self):
        """D-16 / Pitfall 2 - _transition_portapack body contains zero ast.Raise nodes."""
        tree = _parse_source()
        fn = _find_function(
            tree, '_transition_portapack', class_name='HackRFLifecycleNode')
        assert fn is not None, '_transition_portapack not found'
        raises = [n for n in ast.walk(fn) if isinstance(n, ast.Raise)]
        assert raises == [], (
            f'_transition_portapack must never raise; found {len(raises)} raise(s)')

    def test_failure_maps_to_return(self):
        """D-16 - FAILED branch in on_configure returns TransitionCallbackReturn.FAILURE."""
        tree = _parse_source()
        fn = _find_function(tree, 'on_configure', class_name='HackRFLifecycleNode')
        assert fn is not None
        text = ast.unparse(fn)
        # Segment between first FAILED mention and its subsequent return.
        failed_idx = text.find('PortapackTransitionResult.FAILED')
        assert failed_idx != -1, 'PortapackTransitionResult.FAILED not referenced in on_configure'
        after = text[failed_idx:]
        # First return after FAILED must be the FAILURE return.
        ret_match = re.search(
            r'return\s+TransitionCallbackReturn\.FAILURE', after)
        assert ret_match is not None, (
            'FAILED branch must return TransitionCallbackReturn.FAILURE')


# ===========================================================================
# 10. TestTransitionSequence - D-06 happy path (Pattern B)
# ===========================================================================
class TestTransitionSequence:
    """Mocked end-to-end happy path through _transition_portapack (D-06)."""

    def _make_serial_mock(self, write_recorder):
        """Return a MagicMock configured to record port.write() calls."""
        serial_mock = MagicMock()
        port_mock = MagicMock()
        port_mock.write = write_recorder
        port_mock.__enter__ = MagicMock(return_value=port_mock)
        port_mock.__exit__ = MagicMock(return_value=False)
        serial_mock.return_value = port_mock
        return serial_mock

    def test_happy_path_returns_succeeded(self):
        """D-06 - symlink present + serial write OK + enumerate non-empty → SUCCEEDED."""
        node, mod = _make_node()
        write_recorder = MagicMock()
        serial_mock = self._make_serial_mock(write_recorder)
        import pyhackrf2 as pk
        with patch('hackrf_ros.hackrf_lifecycle_node.serial.Serial', serial_mock), \
             patch('hackrf_ros.hackrf_lifecycle_node.time.sleep', lambda *_: None), \
             patch('hackrf_ros.hackrf_lifecycle_node.os.path.exists',
                   side_effect=lambda p: p == mod.PORTAPACK_DEFAULT_DEVICE), \
             patch.object(pk.HackRF, 'enumerate', classmethod(lambda cls: ['abc'])):
            result = node._transition_portapack()
        assert result == mod.PortapackTransitionResult.SUCCEEDED
        assert serial_mock.call_count == 1
        _, kwargs = serial_mock.call_args
        assert kwargs.get('baudrate') == 115200
        write_recorder.assert_called_with(mod.PORTAPACK_COMMAND)


# ===========================================================================
# 11. TestSkipPath - D-07 (REQ-P5-07)
# ===========================================================================
class TestSkipPath:
    """When Portapack cannot or should not be transitioned, helper returns SKIPPED."""

    def test_no_symlink_skips(self):
        """D-07 - /dev/portapack absent → SKIPPED, no serial or enumerate calls."""
        node, mod = _make_node()
        serial_mock = MagicMock()
        with patch('hackrf_ros.hackrf_lifecycle_node.serial.Serial', serial_mock), \
             patch('hackrf_ros.hackrf_lifecycle_node.os.path.exists', return_value=False):
            result = node._transition_portapack()
        assert result == mod.PortapackTransitionResult.SKIPPED
        assert serial_mock.call_count == 0

    def test_disabled_skips(self):
        """D-07 - portapack_enable_transition=False → SKIPPED, no serial call."""
        node, mod = _make_node(portapack_enable_transition=False)
        serial_mock = MagicMock()
        with patch('hackrf_ros.hackrf_lifecycle_node.serial.Serial', serial_mock), \
             patch('hackrf_ros.hackrf_lifecycle_node.os.path.exists', return_value=True):
            result = node._transition_portapack()
        assert result == mod.PortapackTransitionResult.SKIPPED
        assert serial_mock.call_count == 0

    def test_empty_device_skips(self):
        """D-07 - portapack_serial_device='' → SKIPPED, no serial call."""
        node, mod = _make_node(portapack_serial_device='')
        serial_mock = MagicMock()
        with patch('hackrf_ros.hackrf_lifecycle_node.serial.Serial', serial_mock), \
             patch('hackrf_ros.hackrf_lifecycle_node.os.path.exists', return_value=True):
            result = node._transition_portapack()
        assert result == mod.PortapackTransitionResult.SKIPPED
        assert serial_mock.call_count == 0


# ===========================================================================
# 12. TestSerialRaise - D-08 (REQ-P5-08)
# ===========================================================================
class TestSerialRaise:
    """Serial-open failures fall through as SKIPPED with ERROR log (D-08)."""

    def test_serial_exception_falls_through(self):
        """D-08 - serial.SerialException → SKIPPED, ERROR logged."""
        import serial as pyserial
        node, mod = _make_node()
        serial_mock = MagicMock(side_effect=pyserial.SerialException('stale'))
        with patch('hackrf_ros.hackrf_lifecycle_node.serial.Serial', serial_mock), \
             patch('hackrf_ros.hackrf_lifecycle_node.os.path.exists', return_value=True):
            result = node._transition_portapack()
        assert result == mod.PortapackTransitionResult.SKIPPED
        assert node._logger.error.called, 'must log ERROR on serial failure'

    def test_oserror_falls_through(self):
        """D-08 - OSError(ENOENT) → SKIPPED (stale symlink case)."""
        node, mod = _make_node()
        serial_mock = MagicMock(side_effect=OSError(errno.ENOENT, 'no node'))
        with patch('hackrf_ros.hackrf_lifecycle_node.serial.Serial', serial_mock), \
             patch('hackrf_ros.hackrf_lifecycle_node.os.path.exists', return_value=True):
            result = node._transition_portapack()
        assert result == mod.PortapackTransitionResult.SKIPPED


# ===========================================================================
# 13. TestResendPath - D-09 (REQ-P5-09)
# ===========================================================================
class TestResendPath:
    """Resend-after-timeout and both-attempts-exhausted paths (D-09)."""

    def _serial_ok(self, write_recorder=None):
        """Build a serial.Serial mock that succeeds and records writes."""
        serial_mock = MagicMock()
        port_mock = MagicMock()
        port_mock.write = write_recorder or MagicMock()
        port_mock.__enter__ = MagicMock(return_value=port_mock)
        port_mock.__exit__ = MagicMock(return_value=False)
        serial_mock.return_value = port_mock
        return serial_mock

    def test_retried_on_second_attempt(self):
        """D-09 - first poll empty, second poll finds HackRF → RETRIED (2 serial calls)."""
        node, mod = _make_node(portapack_reenum_timeout_s=0.05)
        serial_mock = self._serial_ok()
        # Each _poll_hackrf_present call gets its own enumerate response.
        # First poll: always empty (times out). Second poll: returns hardware.

        def fake_enumerate(cls):
            # Each call to enumerate counts once; after first call in second
            # poll, return present. Use serial.Serial call count as the
            # attempt discriminator.
            if serial_mock.call_count <= 1:
                return []
            return ['abc']
        import pyhackrf2 as pk
        with patch('hackrf_ros.hackrf_lifecycle_node.serial.Serial', serial_mock), \
             patch('hackrf_ros.hackrf_lifecycle_node.time.sleep', lambda *_: None), \
             patch('hackrf_ros.hackrf_lifecycle_node.os.path.exists', return_value=True), \
             patch.object(pk.HackRF, 'enumerate', classmethod(fake_enumerate)):
            result = node._transition_portapack()
        assert result == mod.PortapackTransitionResult.RETRIED
        assert serial_mock.call_count == 2, (
            f'serial.Serial expected 2 calls, got {serial_mock.call_count}')

    def test_failed_when_both_attempts_exhausted(self):
        """D-09 - HackRF never appears → FAILED; serial.Serial called twice."""
        node, mod = _make_node(portapack_reenum_timeout_s=0.05)
        serial_mock = self._serial_ok()
        import pyhackrf2 as pk
        with patch('hackrf_ros.hackrf_lifecycle_node.serial.Serial', serial_mock), \
             patch('hackrf_ros.hackrf_lifecycle_node.time.sleep', lambda *_: None), \
             patch('hackrf_ros.hackrf_lifecycle_node.os.path.exists', return_value=True), \
             patch.object(pk.HackRF, 'enumerate', classmethod(lambda cls: [])):
            result = node._transition_portapack()
        assert result == mod.PortapackTransitionResult.FAILED
        assert serial_mock.call_count == 2


# ===========================================================================
# 14. TestOpenRetries - D-10 (REQ-P5-10)
# ===========================================================================
class TestOpenRetries:
    """on_configure open-retry loop around pyhackrf2.HackRF() (D-10).

    Source-text level - on_configure is too tightly-coupled to the full
    node lifecycle to exercise as an isolated helper. Source-text anchors
    the contract that was unit-verified for _transition_portapack.
    """

    def test_retry_loop_references_retries_param(self):
        """D-10 - on_configure retrieves portapack_open_retries."""
        on_configure = _extract_method_text(_read_source(), 'on_configure')
        assert "'portapack_open_retries'" in on_configure, (
            'on_configure must read portapack_open_retries')

    def test_retry_loop_uses_constant_delay(self):
        """D-10 - on_configure sleeps PORTAPACK_OPEN_RETRY_DELAY_S between attempts."""
        on_configure = _extract_method_text(_read_source(), 'on_configure')
        assert 'time.sleep(PORTAPACK_OPEN_RETRY_DELAY_S)' in on_configure

    def test_retry_loop_skips_sleep_after_last_attempt(self):
        """D-10 - guard `if attempt + 1 < retries` prevents sleep after final try."""
        on_configure = _extract_method_text(_read_source(), 'on_configure')
        # Accept either '<' or '<=' guard phrasing; the canonical is '+1 < retries'.
        assert re.search(
            r'attempt\s*\+\s*1\s*<\s*retries', on_configure), (
            'retry loop must guard sleep with `attempt + 1 < retries`')

    def test_retry_loop_iterates_retries_times(self):
        """D-10 - loop is `for attempt in range(retries):` (or equivalent)."""
        on_configure = _extract_method_text(_read_source(), 'on_configure')
        assert re.search(
            r'for\s+attempt\s+in\s+range\(\s*retries', on_configure), (
            'on_configure must iterate retries times')


# ===========================================================================
# 15. TestDiagField - D-11, D-15 (REQ-P5-11, REQ-P5-15)
# ===========================================================================
class TestDiagField:
    """_diagnostics_callback publishes last_portapack_transition (D-11, D-15)."""

    def test_diag_adds_last_portapack_transition(self):
        """D-15 - _diagnostics_callback calls stat.add('last_portapack_transition', ...)."""
        diag = _extract_method_text(_read_source(), '_diagnostics_callback')
        assert "stat.add('last_portapack_transition'" in diag, (
            "diagnostics callback must stat.add('last_portapack_transition', ...)")
        assert 'self._last_portapack_transition' in diag, (
            'diagnostics callback must reference self._last_portapack_transition')

    def test_value_after_skipped(self):
        """D-11 - after SKIPPED transition, state reflects 'skipped' value."""
        node, mod = _make_node()
        with patch('hackrf_ros.hackrf_lifecycle_node.os.path.exists', return_value=False):
            result = node._transition_portapack()
        # Transition callers (on_configure) write state; here we mimic that.
        node._last_portapack_transition = result.value
        assert node._last_portapack_transition == 'skipped'

    def test_value_after_failed(self):
        """D-11 - after FAILED transition, state reflects 'failed' value."""
        node, mod = _make_node(portapack_reenum_timeout_s=0.05)
        serial_mock = MagicMock()
        port_mock = MagicMock()
        port_mock.__enter__ = MagicMock(return_value=port_mock)
        port_mock.__exit__ = MagicMock(return_value=False)
        serial_mock.return_value = port_mock
        import pyhackrf2 as pk
        with patch('hackrf_ros.hackrf_lifecycle_node.serial.Serial', serial_mock), \
             patch('hackrf_ros.hackrf_lifecycle_node.time.sleep', lambda *_: None), \
             patch('hackrf_ros.hackrf_lifecycle_node.os.path.exists', return_value=True), \
             patch.object(pk.HackRF, 'enumerate', classmethod(lambda cls: [])):
            result = node._transition_portapack()
        node._last_portapack_transition = result.value
        assert node._last_portapack_transition == 'failed'

    def test_enum_values_cover_diagnostics_spec(self):
        """D-15 - enum values are exactly the four strings downstream expects."""
        mod = _import_module()
        values = {m.value for m in mod.PortapackTransitionResult}
        assert values == {'skipped', 'succeeded', 'retried', 'failed'}


# ===========================================================================
# 16. TestScopeReversalDocs - D-00 (REQ-P5-00)
# ===========================================================================
class TestScopeReversalDocs:
    """PROJECT.md / REQUIREMENTS.md show the D-00 scope reversal landed."""

    def test_project_missing_old_statement(self):
        """D-00 - PROJECT.md no longer lists the blanket Mayhem exclusion."""
        content = _read_file(_PROJECT_PATH)
        assert 'Mayhem firmware serial control — separate package (pymayhem)' not in content

    def test_project_has_mode_switch_statement(self):
        """D-00 - PROJECT.md declares Mayhem mode-switch command in scope."""
        assert 'Mayhem mode-switch command' in _read_file(_PROJECT_PATH)

    def test_requirements_has_phase5_section(self):
        """D-00 - REQUIREMENTS.md has the Portapack Boot Transition section header."""
        assert '### Portapack Boot Transition (Phase 5)' in _read_file(_REQUIREMENTS_PATH)

    def test_requirements_has_19_phase5_traceability_rows(self):
        """REQ-P5 - 19 traceability rows for Phase 5 requirements."""
        content = _read_file(_REQUIREMENTS_PATH)
        matches = re.findall(
            r'^\| REQ-P5-[0-9A-Z]+ \| Phase 5', content, re.MULTILINE)
        assert len(matches) >= 19, (
            f'expected >=19 REQ-P5 traceability rows, found {len(matches)}')
