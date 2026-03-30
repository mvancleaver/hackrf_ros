"""Configuration constants and YAML config loader for hackrf_driver.

Provides:
    CHUNK_IQ_PAIRS   — fixed IQ chunk size (D-04)
    PARAM_RANGES     — hardware parameter validation ranges (RX-03)
    load_config()    — YAML file loader merged with DEFAULT_CONFIG
"""
from __future__ import annotations

import yaml


# D-04: fixed chunk size matching hackrf_ros/hackrf_node.py
CHUNK_IQ_PAIRS = 2048

# D-06: exponential backoff bounds for Redis reconnection
_MIN_RECONNECT_DELAY = 1.0
_MAX_RECONNECT_DELAY = 30.0

# RX-03: hardware parameter ranges — values outside these are rejected
PARAM_RANGES = {
    'center_frequency': (1e6,  6e9),
    'sample_rate':      (2e6, 20e6),
    'lna_gain':         (0,    40),
    'vga_gain':         (0,    62),
}
# amp_enabled is bool — no range check needed

# Default configuration matching hackrf_ros parameter declarations
DEFAULT_CONFIG = {
    'center_frequency':     2447e6,
    'sample_rate':          8e6,
    'lna_gain':             16,
    'vga_gain':             20,
    'amp_enabled':          False,
    'serial_port':          '/dev/hackrf_mayhem',
    'redis_host':           'localhost',
    'redis_port':           6379,
    'redis_stream_maxlen':  10000,
    'tx_freq_filter_enabled':   True,
    'tx_skip_antenna_check':    False,
}


def load_config(path: str) -> dict:
    """Load YAML config file and merge with DEFAULT_CONFIG.

    Args:
        path: Filesystem path to a YAML config file.

    Returns:
        Dict with DEFAULT_CONFIG values overridden by any keys found in the
        YAML file. Returns DEFAULT_CONFIG copy if file not found or empty.
    """
    config = DEFAULT_CONFIG.copy()
    try:
        with open(path, 'r') as fh:
            loaded = yaml.safe_load(fh)
        if isinstance(loaded, dict):
            config.update(loaded)
    except FileNotFoundError:
        pass
    return config
