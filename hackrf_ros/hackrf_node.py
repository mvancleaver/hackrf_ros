#!/usr/bin/env python3

import json
import rclpy
import rclpy.parameter
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from rcl_interfaces.msg import SetParametersResult
from std_msgs.msg import Float32MultiArray  # Using standard message for IQ data
from std_msgs.msg import String
from rcl_interfaces.msg import ParameterDescriptor  # For parameter descriptions
from std_srvs.srv import Trigger
from hackrf_ros_interfaces.srv import AppStart, SetFreq
from hackrf_ros.mayhem_serial import MayhemSerial
from hackrf_ros.redis_bridge import RedisBridge
from hackrf_ros.tx_controller import TXController, TXBlockedError, TXFreqBlockedError, TXNotAuthorizedError  # noqa: F401

import pyhackrf2  # The Python binding for libhackrf
import numpy as np
import time
import queue
import threading

CHUNK_IQ_PAIRS = 2048  # D-04: fixed chunk size
_MIN_RECONNECT_DELAY = 1.0   # D-06: exponential backoff start
_MAX_RECONNECT_DELAY = 30.0  # D-06: exponential backoff ceiling

# RX-03: hardware parameter ranges — values outside these are rejected
PARAM_RANGES = {
    'center_frequency': (1e6,  6e9),
    'sample_rate':      (2e6, 20e6),
    'lna_gain':         (0,    40),
    'vga_gain':         (0,    62),
}
# amp_enabled is bool — no range check needed


class HackRFNode(Node):
    """
    A ROS 2 node to interface with HackRF One, dynamically update its
    parameters, and publish acquired IQ data using standard messages.
    """

    def __init__(self):
        super().__init__('hackrf_node')
        self.get_logger().info("HackRF Node starting...")

        self._hackrf = None
        self.is_hackrf_streaming = False
        self._ros_queue = queue.Queue(maxsize=64)    # D-01: ROS2 publisher consumer
        self._redis_queue = queue.Queue(maxsize=64)  # D-01: Phase 3 Redis consumer (stub)
        self._stop_event = threading.Event()         # deadlock guard — RX-05

        # Reconnect loop state
        self._device_lock = threading.RLock()
        self._reconnect_delay = _MIN_RECONNECT_DELAY
        self._reconnect_timer = None

        # Serial reconnect state (D-10: mirrors pyhackrf2 backoff)
        self._serial_reconnect_delay = _MIN_RECONNECT_DELAY
        self._serial_reconnect_timer = None
        self._serial_connected = False
        self._start_time = time.monotonic()  # for uptime_s in state dict (D-08)

        # --- 1. Declare ROS 2 Parameters for HackRF Configuration ---
        # Center Frequency (Hz)
        self.declare_parameter(
            'center_frequency',
            2447e6,
            ParameterDescriptor(description='Center frequency in Hz', read_only=False)
        )

        # Sample Rate (Hz)
        self.declare_parameter(
            'sample_rate',
            8e6,
            ParameterDescriptor(description='Sample rate in Hz', read_only=False)
        )

        # LNA Gain (dB) - 0 to 40 dB in 8 dB steps
        self.declare_parameter(
            'lna_gain',
            16,
            ParameterDescriptor(description='LNA gain in dB (0-40, 8dB steps)', read_only=False)
        )

        # VGA Gain (dB) - 0 to 62 dB in 2 dB steps
        self.declare_parameter(
            'vga_gain',
            20,
            ParameterDescriptor(description='VGA gain in dB (0-62, 2dB steps)', read_only=False)
        )

        # RF Amplifier Enabled
        self.declare_parameter(
            'amp_enabled',
            False,
            ParameterDescriptor(description='RF amplifier enabled (true/false)', read_only=False)
        )

        self.declare_parameter(
            'serial_port', '/dev/ttyACM1',
            ParameterDescriptor(description='Mayhem serial port path (D-03)', read_only=False)
        )
        self.declare_parameter(
            'serial_command_timeout', 3.0,
            ParameterDescriptor(description='Mayhem command timeout in seconds (D-11)', read_only=False)
        )

        # Populate _last_params from declared parameters (D-07)
        self._last_params = {
            'center_frequency': self.get_parameter('center_frequency').get_parameter_value().double_value,
            'sample_rate': self.get_parameter('sample_rate').get_parameter_value().double_value,
            'lna_gain': self.get_parameter('lna_gain').get_parameter_value().integer_value,
            'vga_gain': self.get_parameter('vga_gain').get_parameter_value().integer_value,
            'amp_enabled': self.get_parameter('amp_enabled').get_parameter_value().bool_value,
        }

        # --- 2. Setup Parameter Event Handler ---
        self.add_on_set_parameters_callback(self._on_parameter_event)

        # --- Phase 2: MayhemSerial (D-01) ---
        _serial_port = self.get_parameter('serial_port').get_parameter_value().string_value
        _cmd_timeout = self.get_parameter('serial_command_timeout').get_parameter_value().double_value
        self._mayhem = MayhemSerial(_serial_port, self.get_logger(), timeout=_cmd_timeout)
        self._try_serial_connect()

        # --- Phase 3: RedisBridge (D-10, D-11) ---
        self.declare_parameter(
            'redis_stream_maxlen', 10000,
            ParameterDescriptor(description='Redis Stream MAXLEN for hackrf:iq:stream (D-04)')
        )
        self.declare_parameter(
            'tx_freq_filter_enabled', True,
            ParameterDescriptor(description='Enable TX frequency allowlist (D-05, TX-02/TX-03)')
        )
        self.declare_parameter(
            'tx_skip_antenna_check', False,
            ParameterDescriptor(description='Skip antenna confirmation for automated testing (D-08, TX-04)')
        )
        _maxlen = self.get_parameter('redis_stream_maxlen').get_parameter_value().integer_value
        self._redis_bridge = RedisBridge(
            self._redis_queue, self, self.get_logger(), maxlen=_maxlen
        )
        self._redis_bridge.open()   # D-02: returns False if Redis unreachable — node continues

        # --- Phase 4: TXController (TX-01 through TX-06) ---
        self._tx_controller = TXController(
            self, self._redis_bridge._redis, self.get_logger()
        )
        self._tx_controller.open()  # reads antenna confirmed flag, filter param

        # --- 3. Create ROS 2 Publisher ---
        qos_profile = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10  # Keep last 10 messages
        )
        self.publisher_ = self.create_publisher(Float32MultiArray, '/hackrf/iq', qos_profile)
        self.get_logger().info("Publisher created on topic '/hackrf/iq' (std_msgs/msg/Float32MultiArray).")

        # Phase 2: services (D-07)
        self._srv_appstart = self.create_service(AppStart, 'hackrf/appstart', self._handle_appstart)
        self._srv_setfreq = self.create_service(SetFreq, 'hackrf/setfreq', self._handle_setfreq)
        self._srv_radioinfo = self.create_service(Trigger, 'hackrf/radioinfo', self._handle_radioinfo)
        self.get_logger().info("Mayhem services registered: hackrf/appstart, hackrf/setfreq, hackrf/radioinfo")

        # Phase 2: status topic (D-08)
        self._mayhem_status_pub = self.create_publisher(String, '/hackrf/mayhem_status', 10)
        self._status_timer = self.create_timer(5.0, self._publish_mayhem_status)

        # --- 4. Initialize HackRF Device (via reconnect loop) ---
        self._try_connect()

        # --- 5. Create a Timer for Data Acquisition (unconditional) ---
        self.timer_period = 0.005  # seconds
        self.timer = self.create_timer(self.timer_period, self._publish_iq)
        self.get_logger().info(f"Data acquisition timer set to {self.timer_period} seconds.")

        # Phase 2: MAY-06 empirical mode coexistence check
        self._verify_mode_coexistence()

    def _try_connect(self) -> None:
        """Attempt to open HackRF. On failure, schedule retry with exponential backoff. (D-06, D-08)"""
        try:
            with self._device_lock:
                self._hackrf = pyhackrf2.HackRF()
                self._apply_last_params()
                self._hackrf.start_rx(self._rx_callback)
                self.is_hackrf_streaming = True
                self._reconnect_delay = _MIN_RECONNECT_DELAY  # reset on success
                self.get_logger().info("HackRF connected and streaming.")
        except (RuntimeError, OSError) as e:
            self.get_logger().error(
                f"HackRF connect failed: {e}. Retrying in {self._reconnect_delay:.0f}s."
            )
            self._hackrf = None
            self.is_hackrf_streaming = False
            self._reconnect_timer = self.create_timer(
                self._reconnect_delay,
                self._reconnect_callback,
            )
            self._reconnect_delay = min(self._reconnect_delay * 2, _MAX_RECONNECT_DELAY)
        except Exception as e:
            self.get_logger().error(
                f"Unexpected error opening HackRF: {e}. Retrying in {self._reconnect_delay:.0f}s.",
                exc_info=True,
            )
            self._hackrf = None
            self.is_hackrf_streaming = False
            self._reconnect_timer = self.create_timer(
                self._reconnect_delay,
                self._reconnect_callback,
            )
            self._reconnect_delay = min(self._reconnect_delay * 2, _MAX_RECONNECT_DELAY)

    def _reconnect_callback(self) -> None:
        """Timer callback: cancel this timer, attempt reconnect. (D-06)"""
        if self._reconnect_timer:
            self._reconnect_timer.cancel()
            self._reconnect_timer = None
        self._try_connect()

    def _try_serial_connect(self) -> None:
        """Open serial port; on failure schedule retry with exponential backoff (D-10, MAY-01)."""
        if self._mayhem.open():
            self._serial_connected = True
            self._serial_reconnect_delay = _MIN_RECONNECT_DELAY  # reset on success
            self.get_logger().info("MayhemSerial connected.")
            # D-09: query applist at startup, no hardcoded names
            try:
                apps = self._mayhem.query_applist()
                self.get_logger().info(f"Mayhem apps discovered: {apps}")
            except Exception as e:
                self.get_logger().warning(f"applist query failed at startup: {e}")
        else:
            self._serial_connected = False
            self.get_logger().error(
                f"Serial open failed. Retrying in {self._serial_reconnect_delay:.0f}s."
            )
            self._serial_reconnect_timer = self.create_timer(
                self._serial_reconnect_delay, self._serial_reconnect_callback
            )
            self._serial_reconnect_delay = min(
                self._serial_reconnect_delay * 2, _MAX_RECONNECT_DELAY
            )

    def _serial_reconnect_callback(self) -> None:
        """Timer callback: cancel this timer, attempt serial reconnect (D-10)."""
        if self._serial_reconnect_timer:
            self._serial_reconnect_timer.cancel()
            self._serial_reconnect_timer = None
        self._try_serial_connect()

    def _verify_mode_coexistence(self) -> None:
        """MAY-06: Empirically confirm pyhackrf2 and Mayhem serial coexist at startup.

        Logs CONFIRMED if both operational; logs MODE CONFLICT error if incompatible.
        Per D-06: never silently degrade.
        """
        serial_ok = False
        hackrf_ok = self.is_hackrf_streaming

        if self._serial_connected:
            try:
                info = self._mayhem.radioinfo()
                serial_ok = bool(info)
            except Exception as e:
                self.get_logger().error(f"MAY-06: serial radioinfo probe failed: {e}")

        if hackrf_ok and serial_ok:
            self.get_logger().info(
                "MAY-06: Mode coexistence CONFIRMED — pyhackrf2 streaming and Mayhem serial both operational."
            )
        elif hackrf_ok and not serial_ok:
            self.get_logger().error(
                "MAY-06: MODE CONFLICT — pyhackrf2 is streaming but Mayhem serial is unresponsive. "
                "Device may be in HackRF mode. Reboot PortaPack; do NOT press the HackRF hardware button."
            )
        elif not hackrf_ok and serial_ok:
            self.get_logger().warning(
                "MAY-06: pyhackrf2 not yet streaming (reconnecting) but serial is operational. "
                "Mode conflict cannot be confirmed until pyhackrf2 reconnects."
            )
        else:
            self.get_logger().warning(
                "MAY-06: Both interfaces unavailable at startup — cannot assess mode conflict."
            )

    def _handle_appstart(self, request, response):
        """ROS2 service handler for hackrf/appstart (D-07, MAY-03)."""
        if not self._serial_connected:
            response.success = False
            response.message = "Serial not connected"
            return response
        try:
            ok = self._mayhem.appstart(request.app_name)
            response.success = ok
            response.message = f"appstart {request.app_name}: {'ok' if ok else 'error from device'}"
        except TimeoutError as e:
            response.success = False
            response.message = f"appstart timeout: {e}"
            self.get_logger().warning(f"_handle_appstart timeout: {e}")
        except Exception as e:
            response.success = False
            response.message = f"appstart error: {e}"
            self.get_logger().error(f"_handle_appstart unexpected error: {e}")
        if response.success and hasattr(self, '_redis_bridge'):
            self._redis_bridge.publish_state(self._build_state_dict())
        return response

    def _handle_setfreq(self, request, response):
        """ROS2 service handler for hackrf/setfreq (D-07, MAY-04)."""
        if not self._serial_connected:
            response.success = False
            response.message = "Serial not connected"
            response.confirmed_hz = 0
            return response
        try:
            ok = self._mayhem.setfreq(int(request.freq_hz))
            response.success = ok
            response.message = f"setfreq {request.freq_hz}: {'ok' if ok else 'error from device'}"
            # Confirm by querying radioinfo (MAY-05 — verify after set)
            if ok:
                try:
                    info = self._mayhem.radioinfo()
                    confirmed = int(info.get('freq', 0)) if info else 0
                except Exception:
                    confirmed = 0
                response.confirmed_hz = confirmed
            else:
                response.confirmed_hz = 0
        except TimeoutError as e:
            response.success = False
            response.message = f"setfreq timeout: {e}"
            response.confirmed_hz = 0
            self.get_logger().warning(f"_handle_setfreq timeout: {e}")
        except Exception as e:
            response.success = False
            response.message = f"setfreq error: {e}"
            response.confirmed_hz = 0
            self.get_logger().error(f"_handle_setfreq unexpected error: {e}")
        return response

    def _handle_radioinfo(self, request, response):
        """ROS2 service handler for hackrf/radioinfo (D-07, MAY-05)."""
        if not self._serial_connected:
            response.success = False
            response.message = "Serial not connected"
            return response
        try:
            info = self._mayhem.radioinfo()
            response.success = bool(info)
            response.message = json.dumps(info)
        except TimeoutError as e:
            response.success = False
            response.message = f"radioinfo timeout: {e}"
            self.get_logger().warning(f"_handle_radioinfo timeout: {e}")
        except Exception as e:
            response.success = False
            response.message = f"radioinfo error: {e}"
            self.get_logger().error(f"_handle_radioinfo unexpected error: {e}")
        return response

    def _build_state_dict(self) -> dict:
        """Build hackrf:state mapping from current node state. (RED-02 / D-08, D-09)"""
        return {
            'center_frequency': self._last_params['center_frequency'],
            'sample_rate':      self._last_params['sample_rate'],
            'lna_gain':         self._last_params['lna_gain'],
            'vga_gain':         self._last_params['vga_gain'],
            'amp_enabled':      self._last_params['amp_enabled'],
            'is_streaming':     self.is_hackrf_streaming,
            'connected':        self._hackrf is not None,
            'uptime_s':         time.monotonic() - self._start_time,
            'active_app':       getattr(self._mayhem, '_active_app', ''),
            'discovered_apps':  json.dumps(getattr(self._mayhem, '_known_apps', [])),
            'serial_connected': self._serial_connected,
            'is_transmitting':  hasattr(self, '_tx_controller') and self._tx_controller._is_transmitting,
            'tx_freq':          getattr(self._tx_controller, '_last_tx_freq', 0) if hasattr(self, '_tx_controller') else 0,
            'antenna_confirmed': getattr(self._tx_controller, '_antenna_confirmed', False) if hasattr(self, '_tx_controller') else False,
        }

    def _set_center_frequency(self, freq_hz: float) -> None:
        """Set center_frequency via ROS2 parameter system (RED-03 / D-06)."""
        self.set_parameters([rclpy.parameter.Parameter(
            'center_frequency', rclpy.parameter.Parameter.Type.DOUBLE, float(freq_hz)
        )])

    def _set_sample_rate(self, sample_rate: float) -> None:
        """Set sample_rate via ROS2 parameter system (RED-03 / D-06)."""
        self.set_parameters([rclpy.parameter.Parameter(
            'sample_rate', rclpy.parameter.Parameter.Type.DOUBLE, float(sample_rate)
        )])

    def _set_lna_gain(self, lna_gain: int) -> None:
        """Set lna_gain via ROS2 parameter system (RED-03 / D-06)."""
        self.set_parameters([rclpy.parameter.Parameter(
            'lna_gain', rclpy.parameter.Parameter.Type.INTEGER, int(lna_gain)
        )])

    def _set_vga_gain(self, vga_gain: int) -> None:
        """Set vga_gain via ROS2 parameter system (RED-03 / D-06)."""
        self.set_parameters([rclpy.parameter.Parameter(
            'vga_gain', rclpy.parameter.Parameter.Type.INTEGER, int(vga_gain)
        )])

    def _set_amp_enabled(self, enabled: bool) -> None:
        """Set amp_enabled via ROS2 parameter system (RED-03 / D-06)."""
        self.set_parameters([rclpy.parameter.Parameter(
            'amp_enabled', rclpy.parameter.Parameter.Type.BOOL, bool(enabled)
        )])

    def _start_rx_if_stopped(self) -> None:
        """Start RX streaming if not already running (RED-03 / D-06 start_rx command)."""
        if not self.is_hackrf_streaming and self._hackrf:
            with self._device_lock:
                try:
                    self._hackrf.start_rx(self._rx_callback)
                    self.is_hackrf_streaming = True
                    self.get_logger().info("RX started via Redis command.")
                except Exception as e:
                    self.get_logger().error(f"_start_rx_if_stopped failed: {e}")

    def _stop_rx_if_running(self) -> None:
        """Stop RX streaming if currently running (RED-03 / D-06 stop_rx command)."""
        if self.is_hackrf_streaming and self._hackrf:
            with self._device_lock:
                self._stop_event.set()
                try:
                    self._hackrf.stop_rx()
                    self.is_hackrf_streaming = False
                    self.get_logger().info("RX stopped via Redis command.")
                except Exception as e:
                    self.get_logger().error(f"_stop_rx_if_running failed: {e}")
                finally:
                    self._stop_event.clear()

    def _publish_mayhem_status(self) -> None:
        """Timer callback: publish Mayhem state to /hackrf/mayhem_status every 5s (D-08, D-09)."""
        status = {
            "serial_connected": self._serial_connected,
            "known_apps": self._mayhem._known_apps if self._serial_connected else [],
            "needs_reconnect": self._mayhem.needs_reconnect if self._serial_connected else False,
        }
        msg = String()
        msg.data = json.dumps(status)
        self._mayhem_status_pub.publish(msg)
        # Trigger reconnect if reader thread died (D-10)
        if self._serial_connected and self._mayhem.needs_reconnect:
            self.get_logger().warning("MayhemSerial reader thread died — scheduling reconnect.")
            self._serial_connected = False
            self._serial_reconnect_timer = self.create_timer(
                self._serial_reconnect_delay, self._serial_reconnect_callback
            )
            self._serial_reconnect_delay = min(self._serial_reconnect_delay * 2, _MAX_RECONNECT_DELAY)

    def _apply_last_params(self) -> None:
        """Apply stored parameters to _hackrf device. Called after reconnect. (D-07)"""
        self._hackrf.center_freq = int(self._last_params['center_frequency'])
        self._hackrf.sample_rate = int(self._last_params['sample_rate'])
        self._hackrf.lna_gain = int(self._last_params['lna_gain'])
        self._hackrf.vga_gain = int(self._last_params['vga_gain'])
        self._hackrf.amplifier_on = bool(self._last_params['amp_enabled'])

    def _on_parameter_event(self, params):
        """Validate and store parameter updates; reconfigure device if needed. (RX-03)"""
        results = []
        needs_reconfig = False
        for param in params:
            if param.name in PARAM_RANGES:
                lo, hi = PARAM_RANGES[param.name]
                if not (lo <= param.value <= hi):
                    self.get_logger().warning(
                        f"Parameter '{param.name}' value {param.value} rejected: "
                        f"outside hardware range [{lo}, {hi}]"
                    )
                    results.append(SetParametersResult(
                        successful=False,
                        reason=f"{param.name} value {param.value} outside [{lo}, {hi}]"
                    ))
                    continue
            self.get_logger().info(f"Parameter '{param.name}' set to: {param.value}")
            if param.name in self._last_params:
                self._last_params[param.name] = param.value
                needs_reconfig = True
            results.append(SetParametersResult(successful=True, reason=''))
        if needs_reconfig and self._hackrf:
            self._configure_device()
        if hasattr(self, '_redis_bridge'):
            self._redis_bridge.publish_state(self._build_state_dict())
        return results

    def _configure_device(self) -> None:
        """Stop stream, apply _last_params to device, restart stream. Holds _device_lock. (RX-05)"""
        if not self._hackrf:
            self.get_logger().warning("_configure_device called but no device connected.")
            return
        with self._device_lock:
            if self.is_hackrf_streaming:
                self._stop_event.set()
                try:
                    self._hackrf.stop_rx()
                    self.is_hackrf_streaming = False
                    self.get_logger().info("Stopped RX stream for reconfiguration.")
                except (RuntimeError, OSError) as e:
                    self.get_logger().warning(f"stop_rx error (non-fatal): {e}")
                finally:
                    self._stop_event.clear()
                time.sleep(0.1)   # Pitfall B: firmware settling (libhackrf issue #916)
            try:
                self._apply_last_params()
                self._hackrf.start_rx(self._rx_callback)
                self.is_hackrf_streaming = True
                self._reconnect_delay = _MIN_RECONNECT_DELAY  # reset backoff on successful reconfiguration
                self.get_logger().info(
                    f"HackRF reconfigured: freq={self._last_params['center_frequency']/1e6:.2f}MHz "
                    f"sr={self._last_params['sample_rate']/1e6:.2f}MSPS "
                    f"lna={self._last_params['lna_gain']}dB vga={self._last_params['vga_gain']}dB"
                )
            except (RuntimeError, OSError) as e:
                self.get_logger().error(f"Reconfiguration failed: {e}. Scheduling reconnect.")
                self._hackrf = None
                self.is_hackrf_streaming = False
                if self._reconnect_timer:
                    self._reconnect_timer.cancel()  # cancel any pending timer before creating a new one
                self._reconnect_timer = self.create_timer(
                    self._reconnect_delay, self._reconnect_callback
                )
                self._reconnect_delay = min(self._reconnect_delay * 2, _MAX_RECONNECT_DELAY)

    def _rx_callback(self, data: bytes) -> bool:
        """Bare enqueue — no numpy, no processing. Per D-05 / RX-07."""
        chunk = bytes(data)
        for q in (self._ros_queue, self._redis_queue):
            try:
                q.put_nowait(chunk)
            except queue.Full:
                try:
                    q.get_nowait()   # discard oldest (D-02 drop-oldest policy)
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(chunk)
                except queue.Full:
                    pass
        return self._stop_event.is_set()

    def _publish_iq(self):
        """Timer callback: drain _ros_queue, convert int8 bytes to float32, publish."""
        if not self.is_hackrf_streaming:
            return
        chunks = []
        try:
            while True:
                chunks.append(self._ros_queue.get_nowait())
        except queue.Empty:
            pass
        if not chunks:
            return
        raw = b''.join(chunks)
        iq = np.frombuffer(raw, dtype=np.int8).reshape(-1, 2)
        complex_samples = (iq[:, 0].astype(np.float32) / 128.0 +
                           1j * iq[:, 1].astype(np.float32) / 128.0)
        interleaved = np.empty(complex_samples.size * 2, dtype=np.float32)
        interleaved[0::2] = complex_samples.real
        interleaved[1::2] = complex_samples.imag
        msg = Float32MultiArray()
        msg.data = interleaved.tolist()
        self.publisher_.publish(msg)

    def destroy_node(self) -> None:
        """Stop streaming, close device, cancel timers. (RX-06)"""
        self.get_logger().info("HackRFNode shutting down...")
        # TX-06: Stop TX FIRST — before Redis, serial, or pyhackrf2
        if hasattr(self, '_tx_controller'):
            self._tx_controller.stop()
            self.get_logger().info("TXController stopped.")
        # Close RedisBridge FIRST of the rest (existing D-11 comment)
        if hasattr(self, '_redis_bridge'):
            self._redis_bridge.close()
            self.get_logger().info("RedisBridge closed.")
        # Close serial before pyhackrf2 (CONTEXT.md integration point)
        if self._serial_reconnect_timer:
            self._serial_reconnect_timer.cancel()
            self._serial_reconnect_timer = None
        if hasattr(self, '_mayhem') and self._serial_connected:
            try:
                self._mayhem.close()
                self.get_logger().info("MayhemSerial closed.")
            except Exception as e:
                self.get_logger().error(f"MayhemSerial close failed: {e}")
        if self._reconnect_timer:
            self._reconnect_timer.cancel()
            self._reconnect_timer = None
        if self._hackrf:
            with self._device_lock:
                if self.is_hackrf_streaming:
                    self._stop_event.set()
                    try:
                        self._hackrf.stop_rx()
                        self.is_hackrf_streaming = False
                        self.get_logger().info("HackRF RX stream stopped.")
                    except (RuntimeError, OSError) as e:
                        self.get_logger().error(f"stop_rx failed during shutdown: {e}")
                    finally:
                        self._stop_event.clear()
                try:
                    self._hackrf.close()
                    self.get_logger().info("HackRF device closed.")
                except (RuntimeError, OSError) as e:
                    self.get_logger().error(f"close() failed during shutdown: {e}")
        super().destroy_node()
        self.get_logger().info("HackRFNode destroyed.")


def main(args=None):
    rclpy.init(args=args)
    node = HackRFNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info("KeyboardInterrupt detected. Shutting down.")
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
