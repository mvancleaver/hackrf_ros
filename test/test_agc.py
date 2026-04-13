"""Tests for Plan 03-01: AGC — automatic gain control.

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


# ---------------------------------------------------------------------------
# Test 1: AGC constants present
# ---------------------------------------------------------------------------
class TestAGCConstants:
    def test_agc_hold_frames_defined(self):
        src = _read_source()
        match = re.search(r'^AGC_HOLD_FRAMES\s*=\s*(\d+)', src, re.MULTILINE)
        assert match is not None, 'AGC_HOLD_FRAMES constant not defined'
        value = int(match.group(1))
        assert value >= 5, f'AGC_HOLD_FRAMES={value} must be >= 5 to prevent oscillation'

    def test_agc_clip_window_defined(self):
        src = _read_source()
        match = re.search(r'^AGC_CLIP_WINDOW\s*=\s*(\d+)', src, re.MULTILINE)
        assert match is not None, 'AGC_CLIP_WINDOW constant not defined'
        value = int(match.group(1))
        assert value >= 3, f'AGC_CLIP_WINDOW={value} must be >= 3'

    def test_agc_lna_step_defined(self):
        src = _read_source()
        match = re.search(r'^AGC_LNA_STEP\s*=\s*(\d+)', src, re.MULTILINE)
        assert match is not None, 'AGC_LNA_STEP constant not defined'
        value = int(match.group(1))
        assert value == 8, f'AGC_LNA_STEP={value} must be 8 (HackRF LNA steps in 8 dB increments)'

    def test_agc_vga_step_defined(self):
        src = _read_source()
        match = re.search(r'^AGC_VGA_STEP\s*=\s*(\d+)', src, re.MULTILINE)
        assert match is not None, 'AGC_VGA_STEP constant not defined'
        value = int(match.group(1))
        assert 2 <= value <= 6, f'AGC_VGA_STEP={value} must be in [2, 6]'


# ---------------------------------------------------------------------------
# Test 2: _agc_tick method exists in source
# ---------------------------------------------------------------------------
class TestAGCMethod:
    def test_agc_tick_method_exists(self):
        src = _read_source()
        assert 'def _agc_tick(' in src, '_agc_tick method not found in source'

    # Test 3: _agc_tick reduces lna_gain first (before vga_gain)
    def test_agc_tick_reduces_lna_before_vga(self):
        src = _read_source()
        # Find _agc_tick method body
        match = re.search(r'def _agc_tick\(.*?\n(.*?)(?=\n    def |\Z)', src, re.DOTALL)
        assert match is not None, '_agc_tick method body not found'
        body = match.group(0)
        lna_pos = body.find('lna_gain')
        vga_pos = body.find('vga_gain')
        assert lna_pos != -1, "'lna_gain' not found in _agc_tick body"
        assert vga_pos != -1, "'vga_gain' not found in _agc_tick body"
        assert lna_pos < vga_pos, (
            f'lna_gain (pos {lna_pos}) must appear before vga_gain (pos {vga_pos}) in _agc_tick')

    # Test 4: _agc_tick clamps gain to minimum 0
    def test_agc_tick_clamps_to_zero(self):
        src = _read_source()
        assert 'max(0,' in src or 'max(0 ,' in src, (
            '_agc_tick must use max(0, ...) to clamp gain reductions to minimum 0')

    # Test 5: _agc_tick only fires when not in hold period
    def test_agc_tick_checks_hold_counter(self):
        src = _read_source()
        match = re.search(r'def _agc_tick\(.*?\n(.*?)(?=\n    def |\Z)', src, re.DOTALL)
        assert match is not None, '_agc_tick method body not found'
        body = match.group(0)
        assert '_agc_hold_counter' in body, (
            '_agc_tick must check _agc_hold_counter for hysteresis')


# ---------------------------------------------------------------------------
# Test 6: _diagnostics_callback reports AGC last action
# ---------------------------------------------------------------------------
class TestAGCDiagnostics:
    def test_diagnostics_reports_agc_last_action(self):
        src = _read_source()
        # Find _diagnostics_callback method body
        match = re.search(
            r'def _diagnostics_callback\(.*?\n(.*?)(?=\n    def |\Z)', src, re.DOTALL)
        assert match is not None, '_diagnostics_callback not found'
        body = match.group(0)
        assert 'agc_last_action' in body, (
            "_diagnostics_callback must report 'agc_last_action' via stat.add()")


# ---------------------------------------------------------------------------
# Test 7: _process_and_publish_inner calls _agc_tick
# ---------------------------------------------------------------------------
class TestAGCWiring:
    def test_process_and_publish_inner_calls_agc_tick(self):
        src = _read_source()
        match = re.search(
            r'def _process_and_publish_inner\(.*?\n(.*?)(?=\n    def |\Z)', src, re.DOTALL)
        assert match is not None, '_process_and_publish_inner not found'
        body = match.group(0)
        assert '_agc_tick' in body, (
            '_process_and_publish_inner must call self._agc_tick()')


# ---------------------------------------------------------------------------
# Test 8: AGC state variables initialized in __init__
# ---------------------------------------------------------------------------
class TestAGCInit:
    def test_agc_hold_counter_initialized(self):
        src = _read_source()
        assert 'self._agc_hold_counter' in src, (
            'self._agc_hold_counter must be initialized in __init__')

    def test_agc_last_action_initialized(self):
        src = _read_source()
        assert 'self._agc_last_action' in src, (
            'self._agc_last_action must be initialized in __init__')
