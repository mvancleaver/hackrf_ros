"""Tests for Plan 03-03: USB disconnect detection and graceful recovery.

Source-level tests only — no ROS2 runtime required.
All tests read hackrf_lifecycle_node.py as text to verify structure and content.
"""
import re

import pytest


# ---------------------------------------------------------------------------
# Helpers — parse the source file as text so we don't need ROS2 runtime
# ---------------------------------------------------------------------------
_SRC_PATH = 'hackrf_ros/hackrf_lifecycle_node.py'


def _read_source():
    with open(_SRC_PATH) as f:
        return f.read()


def _extract_method(src: str, method_name: str) -> str:
    """Extract method body by finding def line and collecting until next def/class at same indent."""
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


# ---------------------------------------------------------------------------
# Test 1: DISCONNECT_ERROR_TIMEOUT constant defined at module level
# ---------------------------------------------------------------------------
class TestDisconnectErrorTimeoutConstant:
    def test_constant_defined(self):
        src = _read_source()
        match = re.search(
            r'^DISCONNECT_ERROR_TIMEOUT\s*=\s*([\d.]+)', src, re.MULTILINE)
        assert match is not None, (
            'DISCONNECT_ERROR_TIMEOUT must be defined as a module-level constant')
        value = float(match.group(1))
        assert value > 0, (
            f'DISCONNECT_ERROR_TIMEOUT={value} must be positive')


# ---------------------------------------------------------------------------
# Test 2: _usb_fault initialized in __init__
# ---------------------------------------------------------------------------
class TestUsbFaultInit:
    def test_usb_fault_initialized_false(self):
        src = _read_source()
        init_body = _extract_method(src, '__init__')
        assert 'self._usb_fault' in init_body, (
            'self._usb_fault must be initialized in __init__')
        assert 'False' in init_body, (
            'self._usb_fault must be initialized to False in __init__')


# ---------------------------------------------------------------------------
# Test 3: _usb_fault_time initialized in __init__
# ---------------------------------------------------------------------------
class TestUsbFaultTimeInit:
    def test_usb_fault_time_initialized(self):
        src = _read_source()
        init_body = _extract_method(src, '__init__')
        assert 'self._usb_fault_time' in init_body, (
            'self._usb_fault_time must be initialized in __init__')


# ---------------------------------------------------------------------------
# Test 4: _handle_usb_fault method exists
# ---------------------------------------------------------------------------
class TestHandleUsbFaultExists:
    def test_method_exists(self):
        src = _read_source()
        assert 'def _handle_usb_fault(' in src, (
            '_handle_usb_fault method must be defined')


# ---------------------------------------------------------------------------
# Test 5: _handle_usb_fault sets _usb_fault = True
# ---------------------------------------------------------------------------
class TestHandleUsbFaultSetsFlag:
    def test_sets_usb_fault_true(self):
        src = _read_source()
        body = _extract_method(src, '_handle_usb_fault')
        assert body, '_handle_usb_fault method not found'
        assert '_usb_fault = True' in body, (
            '_handle_usb_fault must set self._usb_fault = True')


# ---------------------------------------------------------------------------
# Test 6: _handle_usb_fault sets _is_streaming = False
# ---------------------------------------------------------------------------
class TestHandleUsbFaultStopsStreaming:
    def test_sets_is_streaming_false(self):
        src = _read_source()
        body = _extract_method(src, '_handle_usb_fault')
        assert body, '_handle_usb_fault method not found'
        assert '_is_streaming = False' in body, (
            '_handle_usb_fault must set self._is_streaming = False')


# ---------------------------------------------------------------------------
# Test 7: _handle_usb_fault records fault time using time.monotonic()
# ---------------------------------------------------------------------------
class TestHandleUsbFaultRecordsTime:
    def test_records_fault_time(self):
        src = _read_source()
        body = _extract_method(src, '_handle_usb_fault')
        assert body, '_handle_usb_fault method not found'
        assert '_usb_fault_time' in body, (
            '_handle_usb_fault must assign self._usb_fault_time')
        assert 'time.monotonic()' in body, (
            '_handle_usb_fault must use time.monotonic() to record fault time')


# ---------------------------------------------------------------------------
# Test 8: _rx_callback calls _handle_usb_fault on exception
# ---------------------------------------------------------------------------
class TestRxCallbackHandlesFault:
    def test_rx_callback_calls_handle_usb_fault(self):
        src = _read_source()
        body = _extract_method(src, '_rx_callback')
        assert body, '_rx_callback method not found'
        assert '_handle_usb_fault' in body, (
            '_rx_callback must call self._handle_usb_fault on RuntimeError/OSError')


# ---------------------------------------------------------------------------
# Test 9: _param_worker calls _handle_usb_fault at least twice
# ---------------------------------------------------------------------------
class TestParamWorkerHandlesFault:
    def test_param_worker_calls_handle_usb_fault_twice(self):
        src = _read_source()
        body = _extract_method(src, '_param_worker')
        assert body, '_param_worker method not found'
        count = body.count('_handle_usb_fault')
        assert count >= 2, (
            f'_param_worker must call _handle_usb_fault at least twice '
            f'(stop_rx path + start_rx exhausted-retries path), found {count}')


# ---------------------------------------------------------------------------
# Test 10: _diagnostics_callback references _usb_fault
# ---------------------------------------------------------------------------
class TestDiagnosticsReferencesUsbFault:
    def test_diagnostics_callback_references_usb_fault(self):
        src = _read_source()
        body = _extract_method(src, '_diagnostics_callback')
        assert body, '_diagnostics_callback method not found'
        assert '_usb_fault' in body, (
            '_diagnostics_callback must check self._usb_fault')


# ---------------------------------------------------------------------------
# Test 11: _diagnostics_callback references DISCONNECT_ERROR_TIMEOUT
# ---------------------------------------------------------------------------
class TestDiagnosticsReferencesTimeout:
    def test_diagnostics_callback_references_timeout(self):
        src = _read_source()
        body = _extract_method(src, '_diagnostics_callback')
        assert body, '_diagnostics_callback method not found'
        assert 'DISCONNECT_ERROR_TIMEOUT' in body, (
            '_diagnostics_callback must reference DISCONNECT_ERROR_TIMEOUT '
            'for WARN -> ERROR escalation')


# ---------------------------------------------------------------------------
# Test 12: diagnostics stat.add includes 'usb_fault' key
# ---------------------------------------------------------------------------
class TestDiagnosticsUsbFaultStatKey:
    def test_stat_add_usb_fault_key(self):
        src = _read_source()
        assert ("stat.add('usb_fault'" in src or 'stat.add("usb_fault"' in src), (
            "diagnostics must call stat.add('usb_fault', ...) to surface fault state")
