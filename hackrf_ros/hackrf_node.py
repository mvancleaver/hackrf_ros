#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from rcl_interfaces.msg import SetParametersResult
from std_msgs.msg import Float32MultiArray  # Using standard message for IQ data
from rcl_interfaces.msg import ParameterDescriptor  # For parameter descriptions

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

        # --- 3. Create ROS 2 Publisher ---
        qos_profile = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10  # Keep last 10 messages
        )
        self.publisher_ = self.create_publisher(Float32MultiArray, '/hackrf/iq', qos_profile)
        self.get_logger().info("Publisher created on topic '/hackrf/iq' (std_msgs/msg/Float32MultiArray).")

        # --- 4. Initialize HackRF Device (via reconnect loop) ---
        self._try_connect()

        # --- 5. Create a Timer for Data Acquisition (unconditional) ---
        self.timer_period = 0.005  # seconds
        self.timer = self.create_timer(self.timer_period, self._publish_iq)
        self.get_logger().info(f"Data acquisition timer set to {self.timer_period} seconds.")

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
