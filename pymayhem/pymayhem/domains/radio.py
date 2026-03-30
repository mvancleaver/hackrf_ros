"""Radio domain commands for Mayhem firmware serial API."""
from __future__ import annotations

from typing import Callable


class RadioDomain:
    """Radio configuration domain — frequency control and radio info queries."""

    def __init__(self, send_command: Callable[[str], list[str]]) -> None:
        """Initialise RadioDomain.

        Args:
            send_command: Callable that sends a serial command and returns response lines.
        """
        self._send = send_command

    def radioinfo(self) -> dict[str, str]:
        """Query current radio configuration.

        Parses 'key: value' lines from the radioinfo response.

        Returns:
            Dict mapping lowercase key strings to value strings,
            e.g. {'freq': '433920000', 'bandwidth': '1750000'}.
        """
        lines = self._send('radioinfo')
        result: dict[str, str] = {}
        for line in lines:
            if ':' in line:
                k, _, v = line.partition(':')
                result[k.strip().lower()] = v.strip()
        return result

    def setfreq(self, freq_hz: int) -> bool:
        """Set the active app's frequency.

        Args:
            freq_hz: Frequency in Hz (e.g. 433_920_000).

        Returns:
            True if command accepted, False if response contains 'error'.
        """
        lines = self._send(f'setfreq {freq_hz}')
        return not any('error' in line.lower() for line in lines)
