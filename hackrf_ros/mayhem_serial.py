"""Mayhem serial interface helper class for HackRF ROS2 driver.

Provides MayhemSerial — a standalone class that owns the pyserial connection to
Mayhem firmware over /dev/ttyACM1, a daemon reader thread, and all command
methods (applist, appstart, setfreq, radioinfo). HackRFNode uses this class
so it never calls pyserial directly.

Independently testable without ROS2.
"""
from __future__ import annotations

import queue
import threading
import time

import serial


class MayhemSerial:
    """Serial interface to Mayhem firmware over a CDC-ACM serial port.

    Thread-safe: all public command methods acquire _serial_lock before
    sending. A dedicated daemon reader thread feeds _response_queue which
    _send_command drains with a deadline.

    Usage::

        ms = MayhemSerial('/dev/ttyACM1', logger, timeout=3.0)
        if ms.open():
            apps = ms.query_applist()
            ms.appstart('capture')
            ms.setfreq(433_920_000)
            info = ms.radioinfo()
            ms.close()

    Reconnect detection::

        if ms.needs_reconnect:
            ms.close()
            ms.open()
    """

    def __init__(self, port: str, logger, timeout: float = 3.0) -> None:
        """Initialise MayhemSerial.

        Args:
            port: Serial device path (e.g. '/dev/ttyACM1').
            logger: Object with .info(), .warning(), .error() methods
                    (compatible with rclpy.Logger or logging.Logger).
            timeout: Command response timeout in seconds (D-11).
        """
        self._port = port
        self._logger = logger
        self._command_timeout = timeout

        # pyserial 3.5 pattern: construct without port to defer open()
        self._serial = serial.Serial()
        self._serial.port = port
        self._serial.baudrate = 115200   # Required non-zero; ACM ignores baud rate
        self._serial.timeout = 1.0       # readline() returns after 1s even with no newline
        self._serial.write_timeout = 2.0

        self._serial_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._response_queue: queue.Queue[str] = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        self._known_apps: list[str] = []
        self._active_app: str = ''

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> bool:
        """Open serial port and start daemon reader thread.

        Returns:
            True on success, False if SerialException raised (port unavailable).
        """
        try:
            self._serial.open()
            self._stop_event.clear()
            self._reader_thread = threading.Thread(
                target=self._reader_loop,
                daemon=True,
                name='mayhem_serial_reader',
            )
            self._reader_thread.start()
            # Pitfall 4: brief pause so reader thread is ready before first command
            time.sleep(0.05)
            return True
        except serial.SerialException as e:
            self._logger.error(f'MayhemSerial: failed to open {self._port}: {e}')
            return False

    def close(self) -> None:
        """Signal reader thread to stop and close the serial port."""
        self._stop_event.set()
        if self._serial.is_open:
            self._serial.close()

    # ------------------------------------------------------------------
    # Reader thread
    # ------------------------------------------------------------------

    def _reader_loop(self) -> None:
        """Daemon thread: continuously readline() and post decoded lines to _response_queue.

        On SerialException, sets _stop_event to signal that reconnect is needed.
        HackRFNode checks needs_reconnect to schedule re-open.
        """
        while not self._stop_event.is_set():
            try:
                if self._serial.is_open:
                    raw = self._serial.readline()  # blocks up to timeout=1.0s
                    if raw:
                        line = raw.decode('utf-8', errors='replace').strip()
                        if line:
                            self._response_queue.put(line)
            except serial.SerialException as e:
                self._logger.error(f'MayhemSerial reader error: {e}')
                self._stop_event.set()  # Pattern 3: signal reconnect needed

    # ------------------------------------------------------------------
    # Command layer
    # ------------------------------------------------------------------

    def _attempt_send(self, cmd: str) -> list[str]:
        """Perform one write-and-collect cycle (no retry logic).

        Caller MUST hold _serial_lock before calling this method.

        Drains _response_queue of stale data, writes the command with CRLF,
        then collects lines until a 'ch>' prompt is seen or the deadline
        passes.

        Args:
            cmd: Command string without trailing \\r\\n (e.g. 'applist').

        Returns:
            List of response lines, excluding the command echo and ch> prompt.

        Raises:
            TimeoutError: If the ch> prompt is not seen within _command_timeout.
        """
        # Drain stale data before sending
        while not self._response_queue.empty():
            try:
                self._response_queue.get_nowait()
            except queue.Empty:
                break

        # Write command with CRLF termination (Mayhem/ChibiOS protocol)
        self._serial.write(f'{cmd}\r\n'.encode('utf-8'))

        lines: list[str] = []
        deadline = time.monotonic() + self._command_timeout

        while time.monotonic() < deadline:
            try:
                line = self._response_queue.get(timeout=0.1)
                if line == cmd:
                    # Skip command echo from ChibiOS
                    continue
                if line.lstrip().startswith('ch>'):
                    # ch> prompt signals end of response
                    return lines
                if line:
                    lines.append(line)
            except queue.Empty:
                continue

        raise TimeoutError(f"Command '{cmd}' timed out after {self._command_timeout}s")

    def _send_command(self, cmd: str) -> list[str]:
        """Send a command and return response lines.

        Acquires _serial_lock. If _attempt_send raises TimeoutError on the
        first try, logs a warning and retries once (D-12: garbled/incomplete
        responses are logged as warnings and retried once before propagating
        the error to the caller).

        Args:
            cmd: Command string without trailing \\r\\n.

        Returns:
            List of response lines (excluding echo and prompt).

        Raises:
            TimeoutError: If both the first attempt and the retry time out.
        """
        with self._serial_lock:
            try:
                return self._attempt_send(cmd)
            except TimeoutError:
                self._logger.warning(
                    f"Command '{cmd}' timed out, retrying once"
                )
                # D-12: single automatic retry — let TimeoutError propagate if retry fails
                return self._attempt_send(cmd)

    # ------------------------------------------------------------------
    # Public command methods
    # ------------------------------------------------------------------

    def query_applist(self) -> list[str]:
        """Issue 'applist' command and return short app names.

        Parses each response line as '<short_name> <full_name> <category>'.
        Caches result in _known_apps.

        Returns:
            List of short app name strings (e.g. ['capture', 'scanner']).
        """
        lines = self._send_command('applist')
        self._known_apps = [line.split()[0] for line in lines if line.strip()]
        return self._known_apps

    def appstart(self, short_name: str) -> bool:
        """Start a Mayhem app by its short name.

        Args:
            short_name: App short name from applist (e.g. 'capture').

        Returns:
            True if command accepted, False if response contains 'error'.
        """
        lines = self._send_command(f'appstart {short_name}')
        ok = not any('error' in l.lower() for l in lines)
        if ok:
            self._active_app = short_name
        return ok

    def setfreq(self, freq_hz: int) -> bool:
        """Set the active app's frequency.

        Args:
            freq_hz: Frequency in Hz (e.g. 433_920_000).

        Returns:
            True if command accepted, False if response contains 'error'.
        """
        lines = self._send_command(f'setfreq {freq_hz}')
        return not any('error' in l.lower() for l in lines)

    def radioinfo(self) -> dict[str, str]:
        """Query current radio configuration.

        Parses 'key: value' lines from the radioinfo response.

        Returns:
            Dict mapping lowercase key strings to value strings,
            e.g. {'freq': '433920000', 'bandwidth': '1750000'}.
        """
        lines = self._send_command('radioinfo')
        result: dict[str, str] = {}
        for line in lines:
            if ':' in line:
                k, _, v = line.partition(':')
                result[k.strip().lower()] = v.strip()
        return result

    # ------------------------------------------------------------------
    # Reconnect detection
    # ------------------------------------------------------------------

    @property
    def needs_reconnect(self) -> bool:
        """True when the reader thread has signaled a serial error.

        HackRFNode polls this to detect when the serial port has disconnected
        and a re-open is needed.
        """
        return self._stop_event.is_set()
