"""Tests for Plan 03-02: Async param — non-blocking parameter handling.

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
    # Collect until next method/class definition at the same or lower indent
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
# Test 1: _param_queue initialized in __init__
# ---------------------------------------------------------------------------
class TestParamQueueInit:
    def test_param_queue_initialized(self):
        src = _read_source()
        assert 'self._param_queue' in src, (
            'self._param_queue must be initialized in __init__')


# ---------------------------------------------------------------------------
# Test 2: _param_worker_thread initialized in __init__
# ---------------------------------------------------------------------------
class TestParamWorkerThreadInit:
    def test_param_worker_thread_initialized(self):
        src = _read_source()
        assert 'self._param_worker_thread' in src, (
            'self._param_worker_thread must be initialized in __init__')


# ---------------------------------------------------------------------------
# Test 3: _param_worker method exists
# ---------------------------------------------------------------------------
class TestParamWorkerMethod:
    def test_param_worker_method_exists(self):
        src = _read_source()
        assert 'def _param_worker(' in src, (
            '_param_worker method not found in source')


# ---------------------------------------------------------------------------
# Test 4: _param_callback does NOT contain time.sleep
# ---------------------------------------------------------------------------
class TestParamCallbackNoSleep:
    def test_param_callback_no_sleep(self):
        src = _read_source()
        body = _extract_method(src, '_param_callback')
        assert body, '_param_callback method not found'
        assert 'time.sleep' not in body, (
            '_param_callback must not contain time.sleep — blocking calls belong in _param_worker')


# ---------------------------------------------------------------------------
# Test 5: _param_callback does NOT contain stop_rx
# ---------------------------------------------------------------------------
class TestParamCallbackNoStopRx:
    def test_param_callback_no_stop_rx(self):
        src = _read_source()
        body = _extract_method(src, '_param_callback')
        assert body, '_param_callback method not found'
        assert 'stop_rx' not in body, (
            '_param_callback must not contain stop_rx — hardware ops belong in _param_worker')


# ---------------------------------------------------------------------------
# Test 6: _param_callback does NOT contain start_rx
# ---------------------------------------------------------------------------
class TestParamCallbackNoStartRx:
    def test_param_callback_no_start_rx(self):
        src = _read_source()
        body = _extract_method(src, '_param_callback')
        assert body, '_param_callback method not found'
        assert 'start_rx' not in body, (
            '_param_callback must not contain start_rx — hardware ops belong in _param_worker')


# ---------------------------------------------------------------------------
# Test 7: _param_callback puts work on queue
# ---------------------------------------------------------------------------
class TestParamCallbackEnqueues:
    def test_param_callback_uses_put_nowait(self):
        src = _read_source()
        body = _extract_method(src, '_param_callback')
        assert body, '_param_callback method not found'
        has_put = 'put_nowait' in body or 'put(' in body
        has_queue_ref = '_param_queue' in body
        assert has_queue_ref and has_put, (
            '_param_callback must enqueue work onto _param_queue using put_nowait or put()')


# ---------------------------------------------------------------------------
# Test 8: _param_worker contains stop_rx and start_rx
# ---------------------------------------------------------------------------
class TestParamWorkerHasHardwareOps:
    def test_param_worker_has_stop_rx_and_start_rx(self):
        src = _read_source()
        body = _extract_method(src, '_param_worker')
        assert body, '_param_worker method not found'
        assert 'stop_rx' in body, (
            '_param_worker must contain stop_rx (hardware restart logic belongs here)')
        assert 'start_rx' in body, (
            '_param_worker must contain start_rx (hardware restart logic belongs here)')


# ---------------------------------------------------------------------------
# Test 9: _param_worker is started as daemon thread in on_activate
# ---------------------------------------------------------------------------
class TestParamWorkerStartedInOnActivate:
    def test_param_worker_started_as_daemon_in_on_activate(self):
        src = _read_source()
        on_activate_body = _extract_method(src, 'on_activate')
        assert on_activate_body, 'on_activate method not found'
        has_daemon = 'daemon=True' in on_activate_body
        has_worker_ref = '_param_worker' in on_activate_body
        assert has_daemon and has_worker_ref, (
            'on_activate must start _param_worker as a daemon thread '
            '(daemon=True and _param_worker reference expected)')


# ---------------------------------------------------------------------------
# Test 10: _param_worker thread is signalled to stop in on_deactivate
# ---------------------------------------------------------------------------
class TestParamWorkerStoppedInOnDeactivate:
    def test_param_queue_sentinel_in_on_deactivate(self):
        src = _read_source()
        on_deactivate_body = _extract_method(src, 'on_deactivate')
        assert on_deactivate_body, 'on_deactivate method not found'
        assert '_param_queue' in on_deactivate_body, (
            'on_deactivate must put a sentinel on _param_queue to unblock the worker thread')
