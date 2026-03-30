"""System domain commands for Mayhem firmware serial API."""
from __future__ import annotations

import os
import time
from typing import Callable

from pymayhem.exceptions import MayhemCommandError


class SystemDomain:
    """System control domain — app management, system info, reboot, RTC."""

    def __init__(
        self,
        send_command: Callable[[str], list[str]],
        client=None,
    ) -> None:
        """Initialise SystemDomain.

        Args:
            send_command: Callable that sends a serial command and returns response lines.
            client: Parent MayhemClient instance (needed for appstart_with_reconnect).
        """
        self._send = send_command
        self._client = client

    def applist(self) -> list[str]:
        """Issue 'applist' command and return short app names.

        Returns:
            List of short app name strings (e.g. ['capture', 'scanner']).
        """
        lines = self._send('applist')
        return [line.split()[0] for line in lines if line.strip()]

    def appstart(self, short_name: str) -> None:
        """Start a Mayhem app by its short name.

        Args:
            short_name: App short name from applist (e.g. 'capture').

        Raises:
            ValueError: If short_name is not a non-empty string.
            MayhemCommandError: If the firmware returns an error response.
        """
        if not isinstance(short_name, str) or not short_name.strip():
            raise ValueError(
                f'short_name must be non-empty string, got {short_name!r}'
            )
        lines = self._send(f'appstart {short_name}')
        if any('error' in line.lower() for line in lines):
            raise MayhemCommandError(f'appstart {short_name}: {lines}')
        if self._client is not None:
            self._client._serial._active_app = short_name

    def appstart_with_reconnect(
        self,
        short_name: str,
        reconnect_timeout: float = 10.0,
    ) -> bool:
        """Start app — handles USB reset and reconnection automatically.

        appstart causes a USB reset. Strategy: send command, close port,
        poll for device reappearance, reopen (per D-07).

        Args:
            short_name: App short name (e.g. 'capture').
            reconnect_timeout: Max seconds to wait for USB reappearance.

        Returns:
            True if app started and serial reconnected, False on timeout.

        Raises:
            ValueError: If short_name is not a non-empty string.
        """
        if not isinstance(short_name, str) or not short_name.strip():
            raise ValueError(
                f'short_name must be non-empty string, got {short_name!r}'
            )
        if self._client is None:
            self.appstart(short_name)
            return True

        serial_ref = self._client._serial
        port = serial_ref._port

        try:
            # Send command — serial may drop before response arrives (USB reset expected)
            self._send(f'appstart {short_name}')
        except (TimeoutError, OSError):
            pass  # USB reset is expected — not an error here

        # Close the now-dead port
        serial_ref.close()

        # Poll for device reappearance
        deadline = time.monotonic() + reconnect_timeout
        while time.monotonic() < deadline:
            if os.path.exists(port):
                time.sleep(0.5)  # Let firmware settle after USB re-enum
                if serial_ref.open():
                    serial_ref._active_app = short_name
                    return True
            time.sleep(0.3)

        return False  # Device did not reappear within timeout

    def info(self) -> dict[str, str]:
        """Query firmware info via 'info' command.

        Returns:
            Dict mapping lowercase keys to value strings.
        """
        lines = self._send('info')
        result: dict[str, str] = {}
        for line in lines:
            if ':' in line:
                k, _, v = line.partition(':')
                result[k.strip().lower()] = v.strip()
        return result

    def sysinfo(self) -> dict[str, str]:
        """Query system info via 'sysinfo' command.

        Returns:
            Dict mapping lowercase keys to value strings.
        """
        lines = self._send('sysinfo')
        result: dict[str, str] = {}
        for line in lines:
            if ':' in line:
                k, _, v = line.partition(':')
                result[k.strip().lower()] = v.strip()
        return result

    def reboot(self) -> None:
        """Send reboot command.

        Raises:
            MayhemCommandError: If the firmware returns an error response.
        """
        lines = self._send('reboot')
        if any('error' in line.lower() for line in lines):
            raise MayhemCommandError(f'reboot: {lines}')

    def rtcget(self) -> str:
        """Get the current RTC time string.

        Returns:
            First response line as the RTC time string.
        """
        lines = self._send('rtcget')
        return lines[0] if lines else ''

    def rtcset(self, datetime_str: str) -> None:
        """Set the RTC time.

        Args:
            datetime_str: Datetime string in firmware-accepted format.

        Raises:
            ValueError: If datetime_str is not a non-empty string.
            MayhemCommandError: If the firmware returns an error response.
        """
        if not isinstance(datetime_str, str) or not datetime_str.strip():
            raise ValueError(
                f'datetime_str must be non-empty string, got {datetime_str!r}'
            )
        lines = self._send(f'rtcset {datetime_str}')
        if any('error' in line.lower() for line in lines):
            raise MayhemCommandError(f'rtcset {datetime_str}: {lines}')
