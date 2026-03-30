"""UI domain commands for Mayhem firmware serial API."""
from __future__ import annotations

from typing import Callable


class UIDomain:
    """UI control domain — button presses, touch input, keyboard."""

    def __init__(self, send_command: Callable[[str], list[str]]) -> None:
        """Initialise UIDomain.

        Args:
            send_command: Callable that sends a serial command and returns response lines.
        """
        self._send = send_command

    def button(self, n: int) -> bool:
        """Send a button press event.

        Args:
            n: Button number.

        Returns:
            True if command accepted, False if response contains 'error'.
        """
        lines = self._send(f'button {n}')
        return not any('error' in line.lower() for line in lines)

    def touch(self, x: int, y: int) -> bool:
        """Send a touch screen event.

        Args:
            x: X coordinate.
            y: Y coordinate.

        Returns:
            True if command accepted, False if response contains 'error'.
        """
        lines = self._send(f'touch {x} {y}')
        return not any('error' in line.lower() for line in lines)

    def keyboard(self, text: str) -> bool:
        """Send keyboard text input.

        Args:
            text: Text to input.

        Returns:
            True if command accepted, False if response contains 'error'.
        """
        lines = self._send(f'keyboard {text}')
        return not any('error' in line.lower() for line in lines)
