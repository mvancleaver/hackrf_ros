#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32MultiArray
import numpy as np
import matplotlib.pyplot as plt
from collections import deque # To store recent samples efficiently

class IQPlotterNode(Node):
    def __init__(self):
        super().__init__('iq_plotter_node')
        self.get_logger().info("IQ Plotter Node starting...")

        # --- Declare Parameter for Sample Rate ---
        # The sample rate is crucial for correctly scaling the frequency axis of the PSD.
        # You can set this via `ros2 param set /iq_plotter_node sample_rate 10000000.0`
        # or it will default to 8 MSPS.
        self.declare_parameter('sample_rate', 8000000.0) # Default to 8 MSPS
        self.sample_rate = self.get_parameter('sample_rate').get_parameter_value().double_value
        self.get_logger().info(f"Using sample rate: {self.sample_rate / 1e6:.2f} MSPS for PSD.")

        # --- Subscriber Setup ---
        self.subscription = self.create_subscription(
            Float32MultiArray,
            '/hackrf_iq_data',
            self.iq_data_callback,
            10 # QoS history depth
        )
        self.subscription  # Prevent unused variable warning

        # --- Data Buffering for Plotting ---
        # Store a fixed number of recent complex IQ samples for FFT and plotting.
        # A power of 2 is good for FFT. 8192 or 16384 are common.
        self.plot_buffer_size = 8192 # Number of complex samples for FFT and plotting
        self.iq_data_buffer = deque(maxlen=self.plot_buffer_size)

        # --- Matplotlib Setup for Live Plotting ---
        plt.ion() # Turn on interactive mode for live updates
        self.fig, self.axs = plt.subplots(3, 1, figsize=(10, 12)) # Now 3 subplots
        self.fig.suptitle('HackRF IQ Data & Power Spectral Density')

        # 1. Time Domain Plot (I and Q vs. Sample Index)
        self.line_i, = self.axs[0].plot([], [], label='I Samples', color='blue')
        self.line_q, = self.axs[0].plot([], [], label='Q Samples', color='red')
        self.axs[0].set_title('Time Domain IQ')
        self.axs[0].set_xlabel('Sample Index')
        self.axs[0].set_ylabel('Amplitude')
        self.axs[0].legend()
        self.axs[0].grid(True)
        self.axs[0].set_xlim(0, self.plot_buffer_size) # X-axis for all buffered samples

        # 2. Constellation Plot (Q vs. I)
        self.scatter_constellation = self.axs[1].scatter([], [], s=5, alpha=0.7)
        self.axs[1].set_title('Constellation Plot (Q vs I)')
        self.axs[1].set_xlabel('I Sample')
        self.axs[1].set_ylabel('Q Sample')
        self.axs[1].set_aspect('equal', adjustable='box') # Keep aspect ratio square
        self.axs[1].grid(True)
        self.axs[1].set_xlim(-1.1, 1.1) # Assuming normalized IQ from -1 to 1
        self.axs[1].set_ylim(-1.1, 1.1)

        # 3. Power Spectral Density (PSD) Plot
        self.line_psd, = self.axs[2].plot([], [], label='PSD (dB)', color='green')
        self.axs[2].set_title('Power Spectral Density')
        self.axs[2].set_xlabel('Frequency (Hz)')
        self.axs[2].set_ylabel('Power (dB)')
        self.axs[2].grid(True)
        # Initial frequency limits (centered at 0 Hz for baseband data)
        self.axs[2].set_xlim(-self.sample_rate / 2, self.sample_rate / 2)
        self.axs[2].set_ylim(-100, 0) # Example dB range, adjust as needed

        plt.tight_layout() # Adjust layout to prevent overlapping titles/labels


    def iq_data_callback(self, msg):
        """
        Callback function for incoming Float32MultiArray IQ data.
        """
        # Convert the interleaved list of floats to a numpy array
        iq_interleaved = np.array(msg.data, dtype=np.float32)

        # Reshape into I and Q components and create complex samples
        # Assuming data is [I1, Q1, I2, Q2, ...]
        i_samples = iq_interleaved[0::2]
        q_samples = iq_interleaved[1::2]
        complex_samples_batch = i_samples + 1j * q_samples

        # Extend the buffer with new complex samples
        for complex_val in complex_samples_batch:
            self.iq_data_buffer.append(complex_val)

        # Only update plot if enough data is in the buffer for a meaningful FFT
        if len(self.iq_data_buffer) == self.plot_buffer_size:
            self.update_plot()


    def update_plot(self):
        """
        Updates the matplotlib plots with the current data in the buffer.
        """
        # Ensure buffer is full before plotting PSD for consistent FFT size
        if len(self.iq_data_buffer) < self.plot_buffer_size:
            return

        buffered_complex_samples = np.array(self.iq_data_buffer)

        # --- Update Time Domain Plot ---
        self.line_i.set_ydata(np.real(buffered_complex_samples))
        self.line_q.set_ydata(np.imag(buffered_complex_samples))
        x_data_time = np.arange(len(buffered_complex_samples))
        self.line_i.set_xdata(x_data_time)
        self.line_q.set_xdata(x_data_time)
        # Auto-scale Y-axis for time domain
        self.axs[0].set_ylim(min(np.min(np.real(buffered_complex_samples)), np.min(np.imag(buffered_complex_samples))) - 0.1,
                             max(np.max(np.real(buffered_complex_samples)), np.max(np.imag(buffered_complex_samples))) + 0.1)


        # --- Update Constellation Plot ---
        constellation_data = np.vstack((np.real(buffered_complex_samples), np.imag(buffered_complex_samples))).T
        self.scatter_constellation.set_offsets(constellation_data)


        # --- Update PSD Plot ---
        # 1. Apply a window function to reduce spectral leakage (optional but recommended)
        window = np.hanning(len(buffered_complex_samples))
        windowed_samples = buffered_complex_samples * window

        # 2. Perform FFT
        fft_output = np.fft.fft(windowed_samples)

        # 3. Shift the zero-frequency component to the center of the spectrum
        fft_output_shifted = np.fft.fftshift(fft_output)

        # 4. Calculate Power Spectral Density (in dB)
        # Add a small epsilon (1e-10) to avoid log(0) for very small values
        psd_db = 20 * np.log10(np.abs(fft_output_shifted) + 1e-10)

        # 5. Generate frequency axis
        freqs = np.fft.fftfreq(len(buffered_complex_samples), d=1/self.sample_rate)
        freqs_shifted = np.fft.fftshift(freqs)

        # 6. Update the PSD plot
        self.line_psd.set_xdata(freqs_shifted)
        self.line_psd.set_ydata(psd_db)

        # Auto-scale Y-axis for PSD if needed (uncomment to enable)
        # self.axs[2].set_ylim(np.min(psd_db) - 5, np.max(psd_db) + 5)


        # --- Redraw the canvas ---
        self.fig.canvas.draw_idle()
        self.fig.canvas.flush_events()


def main(args=None):
    rclpy.init(args=args)
    node = IQPlotterNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info("IQ Plotter Node shutting down...")
    finally:
        plt.close(node.fig) # Close the matplotlib figure
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()