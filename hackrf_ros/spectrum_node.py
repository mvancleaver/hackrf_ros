"""Spectrum display node -- subscribes to /hackrf/spectrum and renders live plot.

Pure ROS2 subscriber. No hardware access. Connects to the hackrf_node driver
which publishes SpectrumStamped data on /hackrf/spectrum.

Usage:
    ros2 run hackrf_ros spectrum_node
"""
from __future__ import annotations

import threading
import time

import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from hackrf_interfaces.msg import SpectrumStamped


class SpectrumDisplayNode(Node):
    def __init__(self):
        super().__init__('spectrum_display')

        # Match publisher's BEST_EFFORT QoS
        qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )

        self._msg: SpectrumStamped | None = None
        self._lock = threading.Lock()

        self.create_subscription(
            SpectrumStamped, '/hackrf/spectrum', self._cb, qos)

        self.get_logger().info('Waiting for /hackrf/spectrum ...')

    def _cb(self, msg: SpectrumStamped) -> None:
        with self._lock:
            self._msg = msg

    def take(self) -> SpectrumStamped | None:
        with self._lock:
            msg, self._msg = self._msg, None
            return msg


def main(args=None):
    rclpy.init(args=args)
    node = SpectrumDisplayNode()

    spin_thread = threading.Thread(
        target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()

    # --- Matplotlib setup (main thread) ---
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(12, 5))
    fig.canvas.manager.set_window_title('HackRF Spectrum')

    dummy_f = np.linspace(2427, 2447, 100)
    dummy_p = np.full(100, -100.0)
    line, = ax.plot(dummy_f, dummy_p, color='#00ff88', linewidth=0.8)
    fill = ax.fill_between(dummy_f, -120, dummy_p, alpha=0.15, color='#00ff88')
    ax.set_xlabel('Frequency (MHz)', fontsize=10)
    ax.set_ylabel('Power (dBFS)', fontsize=10)
    ax.set_title('Waiting for /hackrf/spectrum ...', fontsize=11, color='#cdd6f4')
    ax.grid(True, alpha=0.15)

    plt.ion()
    fig.show()
    fig.canvas.draw()
    fig.canvas.flush_events()

    y_min_s, y_max_s = -120.0, -40.0
    prev_center = 0.0
    prev_rate = 0.0
    running = True

    def on_close(evt):
        nonlocal running
        running = False
    fig.canvas.mpl_connect('close_event', on_close)

    try:
        while running:
            msg = node.take()
            if msg is not None:
                psd = np.array(msg.psd_db, dtype=np.float32)
                center = msg.center_frequency_hz
                rate = msg.sample_rate_hz

                freqs_mhz = np.linspace(
                    (center - rate / 2) / 1e6,
                    (center + rate / 2) / 1e6,
                    len(psd),
                )

                if center != prev_center or rate != prev_rate:
                    prev_center, prev_rate = center, rate
                    ax.set_xlim(freqs_mhz[0], freqs_mhz[-1])
                    node.get_logger().info(
                        f'Receiving: {center/1e6:.1f} MHz  '
                        f'{rate/1e6:.0f} MSPS  {len(psd)} bins')

                line.set_xdata(freqs_mhz)
                line.set_ydata(psd)

                # Update fill
                for coll in list(ax.collections):
                    coll.remove()
                ax.fill_between(freqs_mhz, y_min_s, psd,
                                alpha=0.15, color='#00ff88')

                # Smooth Y range
                d_min = np.percentile(psd, 2)
                d_max = np.percentile(psd, 99.5)
                y_min_s += 0.15 * (d_min - 5 - y_min_s)
                y_max_s += 0.15 * (d_max + 5 - y_max_s)
                if y_max_s - y_min_s < 20:
                    mid = (y_max_s + y_min_s) / 2
                    y_min_s, y_max_s = mid - 10, mid + 10
                ax.set_ylim(y_min_s, y_max_s)

                peak_db = float(psd.max())
                peak_mhz = float(freqs_mhz[np.argmax(psd)])
                floor_db = float(msg.noise_floor_db)
                ax.set_title(
                    f'{center/1e6:.1f} MHz | {rate/1e6:.0f} MSPS | '
                    f'Peak: {peak_db:.1f} dBFS @ {peak_mhz:.1f} MHz | '
                    f'Floor: {floor_db:.1f} dBFS',
                    fontsize=11, color='#cdd6f4')

            fig.canvas.draw_idle()
            fig.canvas.flush_events()
            time.sleep(0.05)

    except KeyboardInterrupt:
        pass

    plt.close('all')
    node.destroy_node()
    rclpy.try_shutdown()


if __name__ == '__main__':
    main()
