#!/usr/bin/env python3
"""Call /hackrf/sweep and display the result. Run inside the container.

Usage:
    python3 /scripts/plot_sweep.py 1e6 6e9
    python3 /scripts/plot_sweep.py 2402e6 2480e6 --avg 8
"""
import sys
import time

import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
import numpy as np

import rclpy
from rclpy.node import Node
from hackrf_interfaces.srv import Sweep


def main():
    freq_min = float(sys.argv[1]) if len(sys.argv) > 1 else 1e6
    freq_max = float(sys.argv[2]) if len(sys.argv) > 2 else 6e9
    averaging = 2
    for i, arg in enumerate(sys.argv):
        if arg == '--avg' and i + 1 < len(sys.argv):
            averaging = int(sys.argv[i + 1])

    rclpy.init()
    node = Node('sweep_plotter')
    cli = node.create_client(Sweep, '/hackrf/sweep')

    node.get_logger().info('Waiting for /hackrf/sweep service...')
    if not cli.wait_for_service(timeout_sec=10.0):
        node.get_logger().error('Service not available')
        return

    req = Sweep.Request()
    req.freq_min = freq_min
    req.freq_max = freq_max
    req.averaging = averaging

    node.get_logger().info(
        f'Sweeping {freq_min/1e6:.0f} - {freq_max/1e6:.0f} MHz, avg={averaging}...')
    t0 = time.monotonic()
    future = cli.call_async(req)
    rclpy.spin_until_future_complete(node, future, timeout_sec=300.0)
    elapsed = time.monotonic() - t0

    resp = future.result()
    if resp is None or not resp.success:
        msg = resp.message if resp else 'timeout'
        node.get_logger().error(f'Sweep failed: {msg}')
        return

    node.get_logger().info(f'Done in {elapsed:.1f}s: {resp.message}')

    psd = np.array(resp.psd_db)
    freqs_mhz = np.linspace(resp.freq_min / 1e6, resp.freq_max / 1e6, len(psd))

    # Known band annotations
    bands = [
        (88, 108, 'FM Radio'),
        (174, 216, 'VHF TV'),
        (470, 698, 'UHF TV'),
        (698, 746, 'LTE 12/17'),
        (746, 787, 'LTE 13'),
        (824, 849, 'Cell UL'),
        (869, 894, 'Cell DL'),
        (1710, 1755, 'AWS UL'),
        (1850, 1910, 'PCS UL'),
        (1930, 1990, 'PCS DL'),
        (2110, 2155, 'AWS DL'),
        (2402, 2480, 'BT/WiFi'),
        (2496, 2690, 'BRS'),
        (3550, 3700, 'CBRS'),
        (5150, 5350, 'WiFi 5L'),
        (5470, 5725, 'WiFi 5H'),
        (5725, 5850, 'ISM 5.8'),
    ]

    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(18, 6))
    fig.canvas.manager.set_window_title(
        f'HackRF Full Sweep {freq_min/1e6:.0f}-{freq_max/1e6:.0f} MHz')

    ax.plot(freqs_mhz, psd, color='#00ff88', linewidth=0.3)

    valid = psd[psd > -99]
    if len(valid) > 0:
        y_lo = np.percentile(valid, 1) - 5
        y_hi = np.percentile(valid, 99.9) + 5
    else:
        y_lo, y_hi = -80, 0

    ax.fill_between(freqs_mhz, y_lo, psd, alpha=0.15, color='#00ff88')

    # Band annotations
    for lo, hi, label in bands:
        if lo < freq_max / 1e6 and hi > freq_min / 1e6:
            mid = (lo + hi) / 2
            ax.axvspan(lo, hi, alpha=0.06, color='#ff6688')
            ax.text(mid, y_hi - 1, label, color='#ff6688',
                    fontsize=6, ha='center', va='top', alpha=0.7,
                    rotation=90 if (hi - lo) < 50 else 0)

    ax.set_xlim(freqs_mhz[0], freqs_mhz[-1])
    ax.set_ylim(y_lo, y_hi)
    ax.set_xlabel('Frequency (MHz)', fontsize=10)
    ax.set_ylabel('Power (dB)', fontsize=10)

    peak_db = psd.max()
    peak_mhz = freqs_mhz[np.argmax(psd)]
    floor_db = np.median(valid) if len(valid) > 0 else -80
    ax.set_title(
        f'{freq_min/1e6:.0f} - {freq_max/1e6:.0f} MHz | '
        f'{len(psd)} bins | '
        f'Peak: {peak_db:.1f} dB @ {peak_mhz:.1f} MHz | '
        f'Floor: {floor_db:.1f} dB | '
        f'{elapsed:.1f}s sweep',
        fontsize=12, color='#cdd6f4')

    ax.grid(True, alpha=0.1)
    fig.tight_layout()
    plt.show()

    node.destroy_node()
    rclpy.try_shutdown()


if __name__ == '__main__':
    main()
