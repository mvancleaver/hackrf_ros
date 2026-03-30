"""Shared test fixtures for hackrf_ros tests.

Patches Redis connections to prevent tests from hitting a live Redis server
when running the full test suite together.
"""
from unittest.mock import MagicMock, patch
import pytest


@pytest.fixture(autouse=True)
def mock_redis_connection():
    """Ensure no test accidentally connects to a live Redis server."""
    with patch('hackrf_driver.redis_bridge.redis.Redis', return_value=MagicMock()) as mock_cls:
        # Default: connection succeeds, ping returns True
        mock_cls.return_value.ping.return_value = True
        yield mock_cls
