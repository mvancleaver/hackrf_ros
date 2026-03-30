"""Custom exception hierarchy for hackrf_driver.

All exceptions raised by hackrf_driver derive from HackRFError,
allowing callers to catch the base class or any specific subclass.

TX exceptions (TXBlockedError etc.) are defined here so they can be
imported from hackrf_driver.exceptions without importing from tx_controller.
This avoids circular imports and provides a single canonical source.
"""


class HackRFError(Exception):
    """Base for all hackrf_driver errors."""


class HackRFConfigError(HackRFError):
    """Parameter out of hardware range."""


class HackRFDeviceError(HackRFError):
    """USB/connection-level device failure."""


class TXBlockedError(HackRFError):
    """Antenna not confirmed — TX blocked until operator confirms antenna connected."""


class TXFreqBlockedError(HackRFError):
    """Frequency is on a restricted band and the frequency filter is active."""


class TXHardBlockedError(HackRFError):
    """Frequency is on an always-blocked band — NO bypass path exists."""


class TXNotAuthorizedError(HackRFError):
    """No valid one-time authorization token found in Redis."""
