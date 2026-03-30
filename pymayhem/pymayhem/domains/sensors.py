"""Sensors domain commands for Mayhem firmware serial API."""
from __future__ import annotations

from typing import Callable


class SensorsDomain:
    """Sensors domain — GPS, environment, and orientation input injection."""

    def __init__(self, send_command: Callable[[str], list[str]]) -> None:
        """Initialise SensorsDomain.

        Args:
            send_command: Callable that sends a serial command and returns response lines.
        """
        self._send = send_command

    def gotgps(
        self,
        lat: float,
        lon: float,
        alt: float,
        speed: float,
    ) -> bool:
        """Inject GPS data into the device.

        Args:
            lat: Latitude in degrees.
            lon: Longitude in degrees.
            alt: Altitude in meters.
            speed: Speed in m/s.

        Returns:
            True if command accepted, False if response contains 'error'.
        """
        lines = self._send(f'gotgps {lat} {lon} {alt} {speed}')
        return not any('error' in line.lower() for line in lines)

    def gotenv(self, temp: float, humidity: float) -> bool:
        """Inject environmental sensor data.

        Args:
            temp: Temperature in degrees Celsius.
            humidity: Relative humidity percentage.

        Returns:
            True if command accepted, False if response contains 'error'.
        """
        lines = self._send(f'gotenv {temp} {humidity}')
        return not any('error' in line.lower() for line in lines)

    def gotorientation(self, yaw: float, pitch: float, roll: float) -> bool:
        """Inject orientation/IMU data.

        Args:
            yaw: Yaw angle in degrees.
            pitch: Pitch angle in degrees.
            roll: Roll angle in degrees.

        Returns:
            True if command accepted, False if response contains 'error'.
        """
        lines = self._send(f'gotorientation {yaw} {pitch} {roll}')
        return not any('error' in line.lower() for line in lines)
