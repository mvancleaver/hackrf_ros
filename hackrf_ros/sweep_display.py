"""Live sweep display -- calls /hackrf/sweep in a loop, renders composite PSD.

Usage:
    ros2 run hackrf_ros sweep_display --ros-args \
        -p freq_min:=2400e6 -p freq_max:=2500e6 -p averaging:=4
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
from rclpy.executors import SingleThreadedExecutor

from hackrf_interfaces.srv import Sweep

# WiFi 2.4 GHz channels
WIFI_CHANNELS = {
    1: 2412, 2: 2417, 3: 2422, 4: 2427, 5: 2432, 6: 2437,
    7: 2442, 8: 2447, 9: 2452, 10: 2457, 11: 2462, 12: 2467,
    13: 2472, 14: 2484,
}


class SweepDisplayNode(Node):
    def __init__(self):
        super().__init__('sweep_display')
        self.declare_parameter('freq_min', 2400e6)
        self.declare_parameter('freq_max', 2500e6)
        self.declare_parameter('averaging', 4)

        self.freq_min = self.get_parameter('freq_min').value
        self.freq_max = self.get_parameter('freq_max').value
        self.averaging = int(self.get_parameter('averaging').value)

        self._cli = self.create_client(Sweep, '/hackrf/sweep')
        self.latest_psd = None
        self.latest_freqs = None
        self.sweep_count = 0
        self.sweep_time = 0.0


def main(args=None):
    rclpy.init(args=args)
    node = SweepDisplayNode()

    executor = SingleThreadedExecutor()
    executor.add_node(node)

    # --- Plot setup (main thread, before any ROS spinning) ---
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(14, 5))
    fig.canvas.manager.set_window_title('HackRF WiFi 2.4 GHz Sweep')

    # Placeholder
    dummy_x = np.linspace(node.freq_min / 1e6, node.freq_max / 1e6, 100)
    line, = ax.plot(dummy_x, np.full(100, -80), color='#00ff88', linewidth=0.8)
    ax.set_xlim(dummy_x[0], dummy_x[-1])
    ax.set_ylim(-100, -30)
    ax.set_xlabel('Frequency (MHz)', fontsize=10)
    ax.set_ylabel('Power (dB)', fontsize=10)
    ax.grid(True, alpha=0.15)

    # WiFi channel markers
    for ch, freq in WIFI_CHANNELS.items():
        if node.freq_min / 1e6 <= freq <= node.freq_max / 1e6:
            ax.axvline(freq, color='#ff6688', alpha=0.25,
                       linewidth=0.5, linestyle='--')
            ax.text(freq, -32, str(ch), color='#ff6688',
                    fontsize=8, ha='center', alpha=0.6)

    plt.ion()
    fig.show()
    fig.canvas.draw()
    fig.canvas.flush_events()

    y_min_s, y_max_s = -100.0, -30.0
    running = True

    def on_close(evt):
        nonlocal running
        running = False
    fig.canvas.mpl_connect('close_event', on_close)

    # Wait for service
    node.get_logger().info('Waiting for /hackrf/sweep ...')
    while not node._cli.wait_for_service(timeout_sec=1.0):
        fig.canvas.flush_events()
        if not running:
            break
    if not running:
        return

    node.get_logger().info(
        f'Sweeping {node.freq_min/1e6:.0f}-{node.freq_max/1e6:.0f} MHz, '
        f'avg={node.averaging}')

    # --- Sweep + display loop ---
    sweep_pending = False
    future = None
    t0 = 0.0

    try:
        while running:
            # Kick off a new sweep if not pending
            if not sweep_pending:
                req = Sweep.Request()
                req.freq_min = node.freq_min
                req.freq_max = node.freq_max
                req.averaging = node.averaging
                future = node._cli.call_async(req)
                sweep_pending = True
                t0 = time.monotonic()

            # Spin ROS to process the service response
            executor.spin_once(timeout_sec=0.05)

            # Check if sweep completed
            if sweep_pending and future.done():
                resp = future.result()
                sweep_pending = False
                elapsed = time.monotonic() - t0

                if resp is not None and resp.success:
                    node.sweep_count += 1
                    node.sweep_time = elapsed

                    psd = np.array(resp.psd_db, dtype=np.float32)
                    freqs = np.linspace(
                        resp.freq_min / 1e6, resp.freq_max / 1e6, len(psd))

                    # Update line data
                    line.set_xdata(freqs)
                    line.set_ydata(psd)

                    # Update fill
                    for c in list(ax.collections):
                        c.remove()
                    ax.fill_between(freqs, y_min_s, psd,
                                    alpha=0.15, color='#00ff88')

                    # Auto-scale Y
                    valid = psd[psd > -99]
                    if len(valid) > 10:
                        d_min = np.percentile(valid, 1)
                        d_max = np.percentile(valid, 99.5)
                        y_min_s += 0.2 * (d_min - 5 - y_min_s)
                        y_max_s += 0.2 * (d_max + 5 - y_max_s)
                        if y_max_s - y_min_s < 20:
                            mid = (y_max_s + y_min_s) / 2
                            y_min_s, y_max_s = mid - 10, mid + 10
                        ax.set_ylim(y_min_s, y_max_s)

                    peak_db = psd.max()
                    peak_mhz = freqs[np.argmax(psd)]
                    floor_db = np.median(valid) if len(valid) > 0 else -80
                    ax.set_title(
                        f'WiFi 2.4 GHz | '
                        f'{resp.freq_min/1e6:.0f}-{resp.freq_max/1e6:.0f} MHz | '
                        f'Peak: {peak_db:.1f} dB @ {peak_mhz:.1f} MHz | '
                        f'Floor: {floor_db:.1f} dB | '
                        f'{elapsed:.1f}s/sweep | '
                        f'#{node.sweep_count}',
                        fontsize=11, color='#cdd6f4')

                    fig.canvas.draw_idle()
                else:
                    msg = resp.message if resp else 'timeout'
                    node.get_logger().warning(f'Sweep failed: {msg}')

            fig.canvas.flush_events()

    except KeyboardInterrupt:
        pass

    plt.close('all')
    executor.shutdown()
    node.destroy_node()
    rclpy.try_shutdown()


if __name__ == '__main__':
    main()
