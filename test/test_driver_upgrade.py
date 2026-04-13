"""Tests for Plan 01-02: Driver upgrade — scipy.fft, SpectrumStamped, TF, executor."""
import ast
import inspect
import re
import textwrap

import pytest


# ---------------------------------------------------------------------------
# Helpers — parse the source file as text so we don't need ROS2 runtime
# ---------------------------------------------------------------------------
_SRC_PATH = 'hackrf_ros/hackrf_lifecycle_node.py'


def _read_source():
    with open(_SRC_PATH) as f:
        return f.read()


# ---------------------------------------------------------------------------
# Task 1: scipy.fft replaces numpy.fft
# ---------------------------------------------------------------------------
class TestScipyFft:
    def test_no_numpy_fft(self):
        src = _read_source()
        assert 'np.fft' not in src, "numpy.fft references must be removed"

    def test_scipy_fft_import(self):
        src = _read_source()
        assert 'import scipy.fft' in src or 'from scipy import fft' in src

    def test_scipy_fft_usage(self):
        src = _read_source()
        assert src.count('scipy.fft') >= 2, "Need at least 2 scipy.fft calls (fft + fftshift)"


# ---------------------------------------------------------------------------
# Task 1: SpectrumStamped publish
# ---------------------------------------------------------------------------
class TestSpectrumStamped:
    def test_import_spectrum_stamped(self):
        src = _read_source()
        assert 'SpectrumStamped' in src

    def test_topic_name(self):
        src = _read_source()
        assert '/hackrf/spectrum' in src, "Topic must be /hackrf/spectrum"

    def test_no_old_psd_topic(self):
        src = _read_source()
        assert '/hackrf/psd' not in src, "Old /hackrf/psd topic must be removed"

    def test_best_effort_qos(self):
        src = _read_source()
        assert 'BEST_EFFORT' in src, "Spectrum publisher must use BEST_EFFORT QoS"

    def test_noise_floor_db_field(self):
        src = _read_source()
        assert 'noise_floor_db' in src, "SpectrumStamped must set noise_floor_db"


# ---------------------------------------------------------------------------
# Task 2: TF broadcaster and executor
# ---------------------------------------------------------------------------
class TestTfBroadcaster:
    def test_static_transform_broadcaster_import(self):
        src = _read_source()
        assert 'StaticTransformBroadcaster' in src

    def test_antenna_frame_param(self):
        src = _read_source()
        assert 'antenna_frame' in src

    def test_parent_frame_param(self):
        src = _read_source()
        assert 'parent_frame' in src


class TestMultiThreadedExecutor:
    def test_executor_import(self):
        src = _read_source()
        assert 'MultiThreadedExecutor' in src

    def test_executor_usage_in_main(self):
        src = _read_source()
        # executor.spin() or executor.add_node() should appear
        assert 'executor' in src.lower() and 'spin' in src
