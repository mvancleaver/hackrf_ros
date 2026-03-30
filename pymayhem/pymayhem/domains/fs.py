"""Filesystem domain commands for Mayhem firmware serial API."""
from __future__ import annotations

from typing import Callable


class FsDomain:
    """Filesystem domain — file and directory operations."""

    def __init__(self, send_command: Callable[[str], list[str]]) -> None:
        """Initialise FsDomain.

        Args:
            send_command: Callable that sends a serial command and returns response lines.
        """
        self._send = send_command

    def ls(self, path: str = '/') -> list[str]:
        """List directory contents.

        Args:
            path: Directory path to list.

        Returns:
            List of entry strings from the directory listing.
        """
        lines = self._send(f'ls {path}')
        return [line for line in lines if line.strip()]

    def fopen(self, path: str, mode: str = 'r') -> bool:
        """Open a file on the device.

        Args:
            path: File path on device.
            mode: Open mode string.

        Returns:
            True if command accepted, False if response contains 'error'.
        """
        lines = self._send(f'fopen {path} {mode}')
        return not any('error' in line.lower() for line in lines)

    def fread(self, length: int) -> list[str]:
        """Read from the currently open file.

        Args:
            length: Number of bytes to read.

        Returns:
            Response lines containing file data.
        """
        return self._send(f'fread {length}')

    def fwrite(self, data: str) -> bool:
        """Write to the currently open file.

        Args:
            data: Data string to write.

        Returns:
            True if command accepted, False if response contains 'error'.
        """
        lines = self._send(f'fwrite {data}')
        return not any('error' in line.lower() for line in lines)

    def fclose(self) -> bool:
        """Close the currently open file.

        Returns:
            True if command accepted, False if response contains 'error'.
        """
        lines = self._send('fclose')
        return not any('error' in line.lower() for line in lines)

    def mkdir(self, path: str) -> bool:
        """Create a directory.

        Args:
            path: Directory path to create.

        Returns:
            True if command accepted, False if response contains 'error'.
        """
        lines = self._send(f'mkdir {path}')
        return not any('error' in line.lower() for line in lines)

    def unlink(self, path: str) -> bool:
        """Delete a file.

        Args:
            path: File path to delete.

        Returns:
            True if command accepted, False if response contains 'error'.
        """
        lines = self._send(f'unlink {path}')
        return not any('error' in line.lower() for line in lines)

    def crc32(self, path: str) -> str:
        """Compute CRC32 of a file.

        Args:
            path: File path.

        Returns:
            CRC32 value string, or empty string on error.
        """
        lines = self._send(f'crc32 {path}')
        return lines[0] if lines else ''
