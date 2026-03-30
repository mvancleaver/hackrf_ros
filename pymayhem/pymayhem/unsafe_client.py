"""UnsafeMayhemClient — dangerous command extension of MayhemClient."""
from __future__ import annotations

from pymayhem.client import MayhemClient


class UnsafeMayhemClient(MayhemClient):
    """MayhemClient subclass exposing dangerous low-level commands.

    WARNING: These commands can permanently damage firmware, corrupt memory,
    or brick the device. Use only in development/recovery scenarios.

    Dangerous commands (per D-06):
    - write_memory: Write arbitrary bytes to device memory
    - flash: Flash firmware directly
    - dfu: Enter Device Firmware Update mode
    - pmemreset: Reset persistent memory (factory reset)
    - settingsreset: Reset all settings to defaults
    - sd_over_usb: Expose SD card as USB mass storage
    """

    def write_memory(self, address: str, data: str) -> list[str]:
        """Write arbitrary data to device memory address.

        Args:
            address: Memory address (hex string, e.g. '0x20000000').
            data: Data hex string to write.

        Returns:
            Raw response lines from device.
        """
        return self._send_command(f'write_memory {address} {data}')

    def flash(self, firmware_path: str = '') -> list[str]:
        """Flash firmware to device.

        Args:
            firmware_path: Optional path to firmware file on device SD card.

        Returns:
            Raw response lines from device.
        """
        cmd = f'flash {firmware_path}' if firmware_path else 'flash'
        return self._send_command(cmd)

    def dfu(self) -> list[str]:
        """Enter Device Firmware Update (DFU) mode.

        Returns:
            Raw response lines from device.
        """
        return self._send_command('dfu')

    def pmemreset(self) -> list[str]:
        """Reset persistent memory (factory reset equivalent).

        Returns:
            Raw response lines from device.
        """
        return self._send_command('pmemreset')

    def settingsreset(self) -> list[str]:
        """Reset all device settings to factory defaults.

        Returns:
            Raw response lines from device.
        """
        return self._send_command('settingsreset')

    def sd_over_usb(self) -> list[str]:
        """Expose SD card as USB mass storage device.

        Returns:
            Raw response lines from device.
        """
        return self._send_command('sd_over_usb')
