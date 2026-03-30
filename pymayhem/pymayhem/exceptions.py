"""Custom exception hierarchy for pymayhem.

All exceptions raised by pymayhem domain methods derive from MayhemError,
allowing callers to catch the base class or any specific subclass.
"""


class MayhemError(Exception):
    """Base for all pymayhem errors."""


class MayhemCommandError(MayhemError):
    """Firmware returned an error response."""


class MayhemParseError(MayhemError):
    """Unparseable firmware response."""


class MayhemTimeoutError(MayhemError):
    """Serial command timed out."""
