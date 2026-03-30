#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from rclpy.time import Time
from rclpy.exceptions import ParameterNotDeclaredException
from rcl_interfaces.msg import SetParametersResult
from std_msgs.msg import Float32MultiArray  # Using standard message for IQ data
from std_msgs.msg import MultiArrayDimension  # For Float32MultiArray layout
from rcl_interfaces.msg import ParameterDescriptor  # For parameter descriptions

import pyhackrf2  # The Python binding for libhackrf
import numpy as np
import time
import queue
import threading

CHUNK_IQ_PAIRS = 2048  # D-04: fixed chunk size


class HackRFPuiblisherNode(Node):
    """
    A ROS 2 node to interface with HackRF One, dynamically update its
    parameters, and publish acquired IQ data using standard messages.
    """

    def __init__(self):
        print("DEBUG: Entering HackRFPuiblisherNode __init__")  # DEBUG PRINT
        super().__init__('hackrf_publisher_node')
        self.get_logger().info("HackRF Publisher Node starting...")
        print("DEBUG: Node super().__init__ called.")  # DEBUG PRINT

        self.hackrf = None
        self.is_hackrf_streaming = False
        self._ros_queue = queue.Queue(maxsize=64)    # D-01: ROS2 publisher consumer
        self._redis_queue = queue.Queue(maxsize=64)  # D-01: Phase 3 Redis consumer (stub)
        self._stop_event = threading.Event()         # deadlock guard — used in Plan 02

        # --- 1. Declare ROS 2 Parameters for HackRF Configuration ---
        print("DEBUG: Declaring parameters...")  # DEBUG PRINT

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

        print("DEBUG: Parameters declared.")  # DEBUG PRINT

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
        print("DEBUG: Publisher created.")  # DEBUG PRINT

# --- 4. Initialize HackRF Device ---
        print("DEBUG: Attempting to initialize HackRF device...")  # DEBUG PRINT
        try:
            self.hackrf = pyhackrf2.HackRF()  # Ensure this is HackRF (capital F)
            self.get_logger().info("HackRF device opened successfully.")  # ADDED a generic success message
            print("DEBUG: HackRF device initialized successfully.")  # DEBUG PRINT

            # Apply initial parameters to HackRF
            print("DEBUG: Calling _configure_hackrf...")  # DEBUG PRINT
            self._configure_hackrf()  # This call already starts the RX stream
            print("DEBUG: _configure_hackrf returned.")  # DEBUG PRINT

            # --- 5. Create a Timer for Data Acquisition ---
            self.timer_period = 0.005  # seconds
            self.timer = self.create_timer(self.timer_period, self._publish_iq)  # UNCOMMENT THIS
            self.get_logger().info(f"Data acquisition timer set to {self.timer_period} seconds.")  # UNCOMMENT THIS
            print("DEBUG: Timer created. Node initialization complete.")  # DEBUG PRINT

        except Exception as e:  # Catching a general Exception as determined from dir(pyhackrf2)
            self.get_logger().error(f"Failed to initialize HackRF or unexpected error: {e}")
            self.hackrf = None
            self.is_hackrf_streaming = False
            self.get_logger().warn("HackRF not connected or failed to initialize. Node will run but not publish data.")
            print(f"DEBUG: General Exception caught during HackRF init: {e}")  # DEBUG PRINT

    def _on_parameter_event(self, params):
        """
        Callback for when ROS 2 parameters are changed.
        Reconfigures the HackRF device with the new parameters.
        """
        successful_results = []  # Renamed for clarity, will hold SetParametersResult objects
        needs_reconfig = False

        for param in params:
            self.get_logger().info(f"Parameter '{param.name}' changed to: {param.value}")
            # Check if this parameter change requires HackRF reconfiguration
            if param.name in ['center_frequency', 'sample_rate', 'lna_gain', 'vga_gain', 'amp_enabled']:
                needs_reconfig = True
            # For now, assume all parameter sets are successful
            successful_results.append(SetParametersResult(successful=True, reason=''))

        if needs_reconfig and self.hackrf:
            self.get_logger().info("Reconfiguring HackRF with updated parameters...")
            self._configure_hackrf()
            self.get_logger().info("HackRF reconfigured.")

        return successful_results  # Return a list of SetParametersResult objects

    def _configure_hackrf(self):
        """
        Applies the current ROS 2 parameter values to the HackRF device.
        This function is called during initialization and on parameter changes.
        """
        if not self.hackrf:
            self.get_logger().warn("HackRF device not initialized. Cannot configure.")
            return

        try:
            center_freq = self.get_parameter('center_frequency').get_parameter_value().double_value
            sample_rate = self.get_parameter('sample_rate').get_parameter_value().double_value
            lna_gain = self.get_parameter('lna_gain').get_parameter_value().integer_value
            vga_gain = self.get_parameter('vga_gain').get_parameter_value().integer_value
            amp_enabled = self.get_parameter('amp_enabled').get_parameter_value().bool_value

            # --- ADD THIS DEBUG PRINT TO CONFIRM READ VALUES ---
            self.get_logger().info(f"DEBUG_CONFIG_READ: Freq={center_freq}, SR={sample_rate}, LNA={lna_gain}, VGA={vga_gain}, Amp={amp_enabled}")
            # --- END DEBUG PRINT ---

            self.get_logger().info(f"Attempting to configure HackRF with: Freq={center_freq/1e6:.2f}MHz, "
                                   f"SR={sample_rate/1e6:.2f}MSPS, LNA={lna_gain}dB, "
                                   f"VGA={vga_gain}dB, Amp={'ON' if amp_enabled else 'OFF'}")

            # Stop streaming temporarily if already streaming to apply new settings
            if self.is_hackrf_streaming:
                self.hackrf.stop_rx()
                self.is_hackrf_streaming = False
                self.get_logger().info("Stopped RX stream to apply new settings.")

            # --- CORRECTED LINES: Use direct attribute assignment ---
            self.hackrf.center_freq = int(center_freq)
            self.hackrf.sample_rate = int(sample_rate)
            self.hackrf.lna_gain = lna_gain
            self.hackrf.vga_gain = vga_gain
            self.hackrf.amplifier_on = amp_enabled

            self.get_logger().info(f"HackRF Configured: Freq={center_freq/1e6:.2f}MHz, "
                                   f"SR={sample_rate/1e6:.2f}MSPS, LNA={lna_gain}dB, "
                                   f"VGA={vga_gain}dB, Amp={'ON' if amp_enabled else 'OFF'}")

            # Restart streaming after applying new settings
            self.hackrf.start_rx(self._rx_callback)
            self.is_hackrf_streaming = True
            self.get_logger().info("Restarted RX stream with new settings.")

        except Exception as e:
            self.get_logger().error(f"Error configuring HackRF or unexpected error: {e}")
            # ... (rest of the exception handling) ...

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

    def destroy_node(self):
        """
        Cleans up HackRF resources when the node is destroyed.
        Ensures the HackRF stream is stopped and the device is closed.
        """
        self.get_logger().info("Shutting down HackRF Publisher Node...")
        if self.hackrf:
            if self.is_hackrf_streaming:
                try:
                    self.hackrf.stop_rx()
                    self.get_logger().info("HackRF RX stream stopped.")
                except Exception as e:
                    self.get_logger().error(f"Error stopping HackRF RX stream: {e}")
            try:
                self.hackrf.close()
                self.get_logger().info("HackRF device closed.")
            except Exception as e:
                self.get_logger().error(f"Error closing HackRF device: {e}")
        super().destroy_node()
        self.get_logger().info("HackRF Publisher Node destroyed.")


def main(args=None):
    rclpy.init(args=args)
    node = HackRFPuiblisherNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info("KeyboardInterrupt detected. Shutting down.")
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
