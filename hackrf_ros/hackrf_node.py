#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from rclpy.time import Time
from rclpy.exceptions import ParameterNotDeclaredException
from rcl_interfaces.msg import SetParametersResult
from std_msgs.msg import Float32MultiArray # Using standard message for IQ data
from std_msgs.msg import MultiArrayDimension # For Float32MultiArray layout
from rcl_interfaces.msg import ParameterDescriptor # For parameter descriptions

import pyhackrf2 # The Python binding for libhackrf
import numpy as np
import time

class HackRFPuiblisherNode(Node):
    """
    A ROS 2 node to interface with HackRF One, dynamically update its
    parameters, and publish acquired IQ data using standard messages.
    """

    def __init__(self):
        print("DEBUG: Entering HackRFPuiblisherNode __init__") # DEBUG PRINT
        super().__init__('hackrf_publisher_node')
        self.get_logger().info("HackRF Publisher Node starting...")
        print("DEBUG: Node super().__init__ called.") # DEBUG PRINT

        self.hackrf = None
        self.is_hackrf_streaming = False
        self.current_samples_buffer = np.array([], dtype=np.complex64) # Buffer to accumulate samples

        # --- 1. Declare ROS 2 Parameters for HackRF Configuration ---
        print("DEBUG: Declaring parameters...") # DEBUG PRINT

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
  
        # Number of samples to read per acquisition cycle (I/Q pairs)
        self.declare_parameter(
            'num_iq_samples_per_publish',
            8192,
            ParameterDescriptor(description='Number of IQ pairs to publish per message', read_only=False)
        )

        print("DEBUG: Parameters declared.") # DEBUG PRINT

        # --- 2. Setup Parameter Event Handler ---
        self.add_on_set_parameters_callback(self._on_parameter_event)

        # --- 3. Create ROS 2 Publisher ---
        qos_profile = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE, 
            history=HistoryPolicy.KEEP_LAST,
            depth=10 # Keep last 10 messages
        )
        self.publisher_ = self.create_publisher(Float32MultiArray, 'hackrf_iq_data', qos_profile)
        self.get_logger().info("Publisher created on topic '/hackrf_iq_data' (std_msgs/msg/Float32MultiArray).")
        print("DEBUG: Publisher created.") # DEBUG PRINT

# --- 4. Initialize HackRF Device ---
        print("DEBUG: Attempting to initialize HackRF device...") # DEBUG PRINT
        try:
            self.hackrf = pyhackrf2.HackRF() # Ensure this is HackRF (capital F)
            self.get_logger().info("HackRF device opened successfully.") # ADDED a generic success message
            print("DEBUG: HackRF device initialized successfully.") # DEBUG PRINT

            # Apply initial parameters to HackRF
            print("DEBUG: Calling _configure_hackrf...") # DEBUG PRINT
            self._configure_hackrf() # This call already starts the RX stream
            print("DEBUG: _configure_hackrf returned.") # DEBUG PRINT

            # --- 5. Create a Timer for Data Acquisition ---
            self.timer_period = 0.005 # seconds
            self.timer = self.create_timer(self.timer_period, self._read_and_publish_iq) # UNCOMMENT THIS
            self.get_logger().info(f"Data acquisition timer set to {self.timer_period} seconds.") # UNCOMMENT THIS
            print("DEBUG: Timer created. Node initialization complete.") # DEBUG PRINT

        except Exception as e: # Catching a general Exception as determined from dir(pyhackrf2)
            self.get_logger().error(f"Failed to initialize HackRF or unexpected error: {e}")
            self.hackrf = None
            self.is_hackrf_streaming = False
            self.get_logger().warn("HackRF not connected or failed to initialize. Node will run but not publish data.")
            print(f"DEBUG: General Exception caught during HackRF init: {e}") # DEBUG PRINT

    def _on_parameter_event(self, params):
        """
        Callback for when ROS 2 parameters are changed.
        Reconfigures the HackRF device with the new parameters.
        """
        successful_results = [] # Renamed for clarity, will hold SetParametersResult objects
        needs_reconfig = False

        for param in params:
            self.get_logger().info(f"Parameter '{param.name}' changed to: {param.value}")
            # Check if this parameter change requires HackRF reconfiguration
            if param.name in ['center_frequency', 'sample_rate', 'lna_gain', 'vga_gain', 'amp_enabled', 'num_iq_samples_per_publish']:
                needs_reconfig = True
            # For now, assume all parameter sets are successful
            successful_results.append(SetParametersResult(successful=True, reason=''))

        if needs_reconfig and self.hackrf:
            self.get_logger().info("Reconfiguring HackRF with updated parameters...")
            self._configure_hackrf()
            self.get_logger().info("HackRF reconfigured.")

        return successful_results # Return a list of SetParametersResult objects

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
                self.current_samples_buffer = np.array([], dtype=np.complex64) # Ensure this is complex64 if it holds complex numbers

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

    def _rx_callback(self, data, *args): # Use *args to catch extra positional arguments
        """
        Callback function for HackRF RX stream.
        This function is called by pyhackrf2 whenever new samples are available.
        'data' is the raw bytes from the HackRF.
        *args will catch any additional arguments (like rx_length, rx_status)
        that pyhackrf2 might pass depending on its internal logic or version.
        """
        # If you need rx_length or rx_status, you can try to extract them from *args like this:
        # rx_length = args[0] if len(args) > 0 else None
        # rx_status = args[1] if len(args) > 1 else None
        # self.get_logger().debug(f"RX Callback: Length={rx_length}, Status={rx_status}")

        # Convert bytes to numpy array of complex floats
        # HackRF returns 8-bit signed I and Q samples
        iq_array = np.frombuffer(data, dtype=np.int8)

        # Reshape to (num_iq_pairs, 2) where column 0 is I and column 1 is Q
        iq_array = iq_array.reshape(-1, 2)

        # Convert to complex floats (e.g., float32 for ROS message)
        # Normalize to -1.0 to 1.0 range
        complex_samples = (iq_array[:, 0] / 128.0) + 1j * (iq_array[:, 1] / 128.0)

        # Append to buffer
        # Ensure the buffer is explicitly complex for the append
        if self.current_samples_buffer.size == 0:
            # Initialize with complex type if it's empty
            self.current_samples_buffer = np.array(complex_samples, dtype=np.complex64)
        else:
            self.current_samples_buffer = np.append(self.current_samples_buffer, complex_samples)

        return 0 # Return 0 for success, non-zero to stop streaming


    def _read_and_publish_iq(self):
        """
        Timer callback to extract samples from the internal buffer
        and publish them as a Float32MultiArray message.
        """
        if not self.hackrf or not self.is_hackrf_streaming:
            # self.get_logger().warn("HackRF not streaming. Skipping data publication.")
            return

        try:
            # Number of IQ pairs to publish (each pair is 2 float32 values)
            num_iq_pairs_to_publish = self.get_parameter('num_iq_samples_per_publish').get_parameter_value().integer_value
            # We need enough complex samples in the buffer for num_iq_pairs_to_publish
            required_buffer_size = num_iq_pairs_to_publish

            if self.current_samples_buffer.size < required_buffer_size:
                # Not enough samples accumulated yet
                # self.get_logger().debug(f"Buffer has {self.current_samples_buffer.size} samples, need {required_buffer_size}. Waiting.")
                return

            # Extract the required number of complex samples from the buffer
            samples_to_publish_complex = self.current_samples_buffer[:required_buffer_size]

            # Convert complex samples to interleaved I/Q floats for the message
            # The .real and .imag properties are float32 (or float64) as per numpy complex types
            # Create an empty array to hold interleaved I/Q data
            interleaved_iq_data = np.empty(samples_to_publish_complex.size * 2, dtype=np.float32)
            interleaved_iq_data[0::2] = samples_to_publish_complex.real # Place I samples at even indices
            interleaved_iq_data[1::2] = samples_to_publish_complex.imag # Place Q samples at odd indices

            # Create the Float32MultiArray message
            msg = Float32MultiArray()

            # Define the layout for the array (optional but good practice)
            # This indicates it's a 1D array of complex IQ pairs
            # The 'size' for the 'iq_samples' dimension should be the total number of *float* values
            msg.layout.dim.append(MultiArrayDimension(label='iq_samples', size=interleaved_iq_data.size, stride=interleaved_iq_data.size))
            msg.layout.dim.append(MultiArrayDimension(label='iq_pair', size=2, stride=2)) # I, Q
            msg.layout.data_offset = 0

            # Assign the raw interleaved I/Q samples (now a flat list of floats)
            msg.data = interleaved_iq_data.tolist()

            self.publisher_.publish(msg)
            # self.get_logger().info(f"Published {num_iq_pairs_to_publish} IQ pairs.")

            # Remove the published samples from the buffer
            self.current_samples_buffer = self.current_samples_buffer[required_buffer_size:]

        except Exception as e:
            self.get_logger().error(f"An unexpected error occurred during data acquisition or publication: {e}")
            # Consider stopping the stream or resetting if critical error
            # self.get_logger().error("Critical error during data publication. Stopping stream.")
            # if self.hackrf and self.is_hackrf_streaming:
            #     self.hackrf.stop_rx()
            #     self.is_hackrf_streaming = False

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
                except Exception as e:                    self.get_logger().error(f"Error stopping HackRF RX stream: {e}")
            try:
                self.hackrf.close()
                self.get_logger().info("HackRF device closed.")
            except Exception as e:                self.get_logger().error(f"Error closing HackRF device: {e}")
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
