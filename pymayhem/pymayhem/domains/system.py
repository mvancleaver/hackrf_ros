"""System domain commands for Mayhem firmware serial API."""
from __future__ import annotations

import os
import time
from typing import Callable


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

    def appstart(self, short_name: str) -> bool:
        """Start a Mayhem app by its short name.

        Args:
            short_name: App short name from applist (e.g. 'capture').

        Returns:
            True if command accepted, False if response contains 'error'.
        """
        lines = self._send(f'appstart {short_name}')
        ok = not any('error' in line.lower() for line in lines)
        if ok and self._client is not None:
            self._client._serial._active_app = short_name
        return ok

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
        """
        if self._client is None:
            return self.appstart(short_name)

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

    def reboot(self) -> bool:
        """Send reboot command.

        Returns:
            True if command sent without error in response.
        """
        lines = self._send('reboot')
        return not any('error' in line.lower() for line in lines)

    def rtcget(self) -> str:
        """Get the current RTC time string.

        Returns:
            First response line as the RTC time string.
        """
        lines = self._send('rtcget')
        return lines[0] if lines else ''

    def rtcset(self, datetime_str: str) -> bool:
        """Set the RTC time.

        Args:
            datetime_str: Datetime string in firmware-accepted format.

        Returns:
            True if command accepted, False if response contains 'error'.
        """
        lines = self._send(f'rtcset {datetime_str}')
        return not any('error' in line.lower() for line in lines)
