"""Radio domain commands for Mayhem firmware serial API."""
from __future__ import annotations

from typing import Callable

from pymayhem.exceptions import MayhemCommandError


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

    def setfreq(self, freq_hz: int) -> None:
        """Set the active app's frequency.

        Args:
            freq_hz: Frequency in Hz (e.g. 433_920_000). Must be int in [1e6, 6e9].

        Raises:
            ValueError: If freq_hz is not an int or is out of the [1 MHz, 6 GHz] range.
            MayhemCommandError: If the firmware returns an error response.
        """
        if not isinstance(freq_hz, int) or not (1_000_000 <= freq_hz <= 6_000_000_000):
            raise ValueError(
                f'freq_hz must be int in [1e6, 6e9], got {freq_hz!r}'
            )
        lines = self._send(f'setfreq {freq_hz}')
        if any('error' in line.lower() for line in lines):
            raise MayhemCommandError(f'setfreq {freq_hz}: {lines}')
