"""MayhemClient — high-level domain-organized API for Mayhem firmware serial control."""
from __future__ import annotations


def _version_gte(v: str, target: str) -> bool:
    """Return True if version string v is >= target (e.g. '2.1.0' >= '2.1.0').

    Splits on '.' and compares numerically. Returns False on any parse error.
    """
    try:
        v_parts = [int(x) for x in v.split('.')]
        t_parts = [int(x) for x in target.split('.')]
        # Pad shorter list with zeros
        length = max(len(v_parts), len(t_parts))
        v_parts += [0] * (length - len(v_parts))
        t_parts += [0] * (length - len(t_parts))
        return v_parts >= t_parts
    except (ValueError, AttributeError):
        return False


class MayhemClient:
    """High-level Mayhem firmware client with domain sub-objects.

    Provides domain-organized access to all Mayhem serial commands:
    - client.radio: frequency control, radio info
    - client.system: app management, system info, reboot, RTC
    - client.ui: button presses, touch input
    - client.fs: filesystem operations
    - client.sensors: GPS, environment, orientation injection

    Usage::

        with MayhemClient('/dev/hackrf_mayhem') as client:
            client.system.appstart('capture')
            client.radio.setfreq(433_920_000)
            info = client.radio.radioinfo()

    Or explicit open/close::

        client = MayhemClient('/dev/hackrf_mayhem')
        client.open()
        ...
        client.close()
    """

    def __init__(
        self,
        port: str = '/dev/hackrf_mayhem',
        timeout: float = 3.0,
    ) -> None:
        """Initialise MayhemClient.

        Args:
            port: Serial device path. Defaults to '/dev/hackrf_mayhem'.
            timeout: Command response timeout in seconds.
        """
        from pymayhem._serial import MayhemSerial
        from pymayhem.domains.radio import RadioDomain
        from pymayhem.domains.system import SystemDomain
        from pymayhem.domains.ui import UIDomain
        from pymayhem.domains.fs import FsDomain
        from pymayhem.domains.sensors import SensorsDomain

        self._serial = MayhemSerial(port, timeout=timeout)

        # Domain sub-objects — each receives the _send_command callable
        self.radio = RadioDomain(self._serial._send_command)
        self.ui = UIDomain(self._serial._send_command)
        self.fs = FsDomain(self._serial._send_command)
        self.sensors = SensorsDomain(self._serial._send_command)
        # system needs self reference for appstart_with_reconnect
        self.system = SystemDomain(self._serial._send_command, self)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> bool:
        """Open the serial connection.

        Returns:
            True on success, False if port unavailable.
        """
        return self._serial.open()

    def close(self) -> None:
        """Close the serial connection."""
        self._serial.close()

    def __enter__(self) -> 'MayhemClient':
        """Context manager entry — opens serial connection."""
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit — closes serial connection."""
        self.close()

    # ------------------------------------------------------------------
    # Command delegation (used by domain objects)
    # ------------------------------------------------------------------

    def _send_command(self, cmd: str) -> list[str]:
        """Delegate to underlying serial object."""
        return self._serial._send_command(cmd)

    # ------------------------------------------------------------------
    # Capabilities (D-09)
    # ------------------------------------------------------------------

    @property
    def capabilities(self) -> dict:
        """Report firmware feature flags based on info() response.

        Returns a dict with:
            - firmware_version (str): firmware version string or 'unknown'
            - supports_setfreq (bool): True if firmware >= 2.1.0 or 'setfreq' in info keys
            - supports_appstart (bool): True if firmware version is known

        If info() raises or fails, returns safe defaults with firmware_version='unknown'.
        """
        try:
            info = self.system.info()
        except Exception:
            return {
                'firmware_version': 'unknown',
                'supports_setfreq': False,
                'supports_appstart': False,
            }
        fw = info.get('version', 'unknown')
        return {
            'firmware_version': fw,
            'supports_setfreq': 'setfreq' in info or _version_gte(fw, '2.1.0'),
            'supports_appstart': 'appstart' in info or fw != 'unknown',
        }
