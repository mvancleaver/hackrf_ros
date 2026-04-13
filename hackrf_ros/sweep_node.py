"""Sweep spectrum analyzer -- hops across a band, stitches, and displays live.

Cycles the HackRF through multiple center frequencies to build a composite
PSD wider than a single 20 MHz capture. Displays the stitched result in a
live matplotlib window.

Retunes the hardware directly (bypassing set_parameters) to avoid the
stop_rx/start_rx cycle that causes libhackrf segfaults on fast hops.
Instead, the stream stays running and we just change center_freq between
reads -- HackRF firmware supports changing frequency while streaming.
"""
from __future__ import annotations

import queue
import threading
import time

import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
import numpy as np
import scipy.fft

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.parameter import Parameter

from hackrf_ros.hackrf_lifecycle_node import HackRFLifecycleNode


FFT_SIZE = 2048
AVERAGING = 4
UPDATE_HZ = 5


def _format_freq(hz):
    if hz >= 1e9:
        return f'{hz/1e9:.3f} GHz'
    return f'{hz/1e6:.1f} MHz'


def _plan_hops(band_start, band_stop, sample_rate):
    usable_bw = sample_rate * 0.9
    centers = []
    f = band_start + sample_rate / 2
    while f - sample_rate / 2 < band_stop:
        centers.append(f)
        f += usable_bw
    if centers:
        last = band_stop - sample_rate / 2
        if last > centers[-1]:
            centers.append(last)
        else:
            centers[-1] = last
    return centers


class SweepEngine:
    def __init__(self, hackrf, band_start, band_stop, sample_rate, dwell_ms):
        self._hackrf = hackrf
        self._sample_rate = sample_rate
        self.dwell_s = dwell_ms / 1000.0

        self.hops = _plan_hops(band_start, band_stop, sample_rate)
        self.n_hops = len(self.hops)
        self._hop_idx = 0

        self._window = np.hanning(FFT_SIZE)
        self._psd_accum = np.zeros(FFT_SIZE)
        self._accum_count = 0

        self.sweep_count = 0
        self._sweep_start = time.monotonic()

        bin_width = sample_rate / FFT_SIZE
        n_bins = int((band_stop - band_start) / bin_width)
        self.comp_freqs_mhz = np.linspace(
            band_start / 1e6, band_stop / 1e6, n_bins)
        self.comp_psd = np.full(n_bins, -100.0)

        trim = int(FFT_SIZE * 0.05)
        self._hop_slices = []
        for center in self.hops:
            hop_freqs = np.linspace(
                center - sample_rate / 2,
                center + sample_rate / 2,
                FFT_SIZE)
            trimmed_freqs = hop_freqs[trim:-trim]
            idx_start = np.searchsorted(
                self.comp_freqs_mhz, trimmed_freqs[0] / 1e6)
            idx_stop = min(
                idx_start + len(trimmed_freqs), len(self.comp_freqs_mhz))
            self._hop_slices.append((trim, FFT_SIZE - trim, idx_start, idx_stop))

    @property
    def sweeps_per_sec(self):
        elapsed = time.monotonic() - self._sweep_start
        return self.sweep_count / max(elapsed, 0.1)

    def process_iq(self):
        iq_queue = self._hackrf._iq_queue
        while True:
            try:
                raw = iq_queue.get_nowait()
            except queue.Empty:
                break
            samples = np.frombuffer(raw, dtype=np.int8).astype(np.float32)
            samples *= (1.0 / 128.0)
            n = FFT_SIZE * 2
            for i in range(0, len(samples) - n + 1, n):
                frame = samples[i:i + n]
                iq = frame[0::2] + 1j * frame[1::2]
                windowed = iq * self._window
                spectrum = scipy.fft.fftshift(scipy.fft.fft(windowed))
                power = np.abs(spectrum) ** 2 / FFT_SIZE
                self._psd_accum += 10.0 * np.log10(np.maximum(power, 1e-20))
                self._accum_count += 1

    def has_enough(self):
        return self._accum_count >= AVERAGING

    def finalize_hop(self):
        if self._accum_count == 0:
            return False
        psd_avg = self._psd_accum / self._accum_count
        self._psd_accum = np.zeros(FFT_SIZE)
        self._accum_count = 0

        trim_lo, trim_hi, idx_start, idx_stop = self._hop_slices[self._hop_idx]
        trimmed = psd_avg[trim_lo:trim_hi]
        n_comp = idx_stop - idx_start
        if n_comp > 0 and len(trimmed) > 0:
            self.comp_psd[idx_start:idx_stop] = np.interp(
                np.linspace(0, 1, n_comp),
                np.linspace(0, 1, len(trimmed)),
                trimmed)
        return True

    def advance_hop(self):
        """Retune by changing center_freq directly -- no stop/start_rx."""
        self._hop_idx = (self._hop_idx + 1) % self.n_hops
        sweep_done = (self._hop_idx == 0)
        if sweep_done:
            self.sweep_count += 1

        new_center = self.hops[self._hop_idx]

        # Change frequency while streaming -- HackRF firmware supports this
        with self._hackrf._device_lock:
            if self._hackrf._hackrf is not None:
                self._hackrf._hackrf.center_freq = int(new_center)

        # Flush stale IQ from previous frequency
        time.sleep(0.02)
        q = self._hackrf._iq_queue
        while not q.empty():
            try:
                q.get_nowait()
            except queue.Empty:
                break
        return sweep_done


def main(args=None):
    rclpy.init(args=args)

    hackrf = HackRFLifecycleNode(publish_iq=False)
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(hackrf)
    spin_thread = threading.Thread(target=executor.spin, daemon=True)
    spin_thread.start()

    # Defaults for Bluetooth band
    band_start = 2402e6
    band_stop = 2480e6
    sweep_rate = 20e6
    dwell_ms = 80.0

    sweep = SweepEngine(hackrf, band_start, band_stop, sweep_rate, dwell_ms)

    # --- Plot setup FIRST ---
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(14, 5))
    fig.canvas.manager.set_window_title('HackRF Bluetooth Sweep')

    line, = ax.plot(sweep.comp_freqs_mhz, sweep.comp_psd,
                    color='#00ff88', linewidth=0.8)
    ax.set_xlim(sweep.comp_freqs_mhz[0], sweep.comp_freqs_mhz[-1])
    ax.set_ylim(-80, 0)
    ax.set_xlabel('Frequency (MHz)', fontsize=10)
    ax.set_ylabel('Power (dB)', fontsize=10)
    ax.grid(True, alpha=0.15)
    for ch in (0, 20, 40, 60, 78):
        ax.axvline(2402 + ch, color='#ff6688', alpha=0.2,
                   linewidth=0.5, linestyle='--')

    plt.ion()
    fig.show()
    fig.canvas.draw()
    fig.canvas.flush_events()

    # --- Now init hardware ---
    hackrf.trigger_configure()
    hackrf.trigger_activate()
    time.sleep(0.5)

    # Set sample rate (triggers stop/start once)
    hackrf.set_parameters([
        Parameter('sample_rate', Parameter.Type.DOUBLE, sweep_rate)])
    time.sleep(0.5)

    # Set initial center freq (triggers stop/start once)
    hackrf.set_parameters([
        Parameter('center_frequency', Parameter.Type.DOUBLE, sweep.hops[0])])
    time.sleep(0.5)

    hackrf.get_logger().info(
        f'Sweep: {_format_freq(band_start)} - {_format_freq(band_stop)}, '
        f'{sweep.n_hops} hops, {dwell_ms:.0f}ms dwell')

    y_min_s, y_max_s = -80.0, 0.0
    running = True
    hop_start = time.monotonic()

    def on_close(evt):
        nonlocal running
        running = False
    fig.canvas.mpl_connect('close_event', on_close)

    try:
        while running:
            sweep.process_iq()

            if time.monotonic() - hop_start >= sweep.dwell_s:
                if sweep.has_enough():
                    sweep.finalize_hop()
                    sweep_done = sweep.advance_hop()
                    hop_start = time.monotonic()

                    if sweep_done:
                        psd = sweep.comp_psd
                        valid = psd[psd > -99]

                        line.set_ydata(psd)
                        for c in list(ax.collections):
                            c.remove()
                        ax.fill_between(sweep.comp_freqs_mhz, y_min_s, psd,
                                        alpha=0.15, color='#00ff88')

                        if len(valid) > 0:
                            d_min = np.percentile(valid, 2)
                            d_max = np.percentile(valid, 99.5)
                            y_min_s += 0.2 * (d_min - 5 - y_min_s)
                            y_max_s += 0.2 * (d_max + 5 - y_max_s)
                            if y_max_s - y_min_s < 20:
                                mid = (y_max_s + y_min_s) / 2
                                y_min_s, y_max_s = mid - 10, mid + 10
                            ax.set_ylim(y_min_s, y_max_s)

                        peak_db = psd.max()
                        peak_mhz = sweep.comp_freqs_mhz[np.argmax(psd)]
                        floor_db = np.median(valid) if len(valid) > 0 else -80
                        ax.set_title(
                            f'Bluetooth 2402-2480 MHz | '
                            f'{sweep.n_hops} hops | '
                            f'Peak: {peak_db:.1f} dB @ {peak_mhz:.1f} MHz | '
                            f'Floor: {floor_db:.1f} dB | '
                            f'{sweep.sweeps_per_sec:.1f} sweeps/s',
                            fontsize=11, color='#cdd6f4')

                        fig.canvas.draw_idle()

            fig.canvas.flush_events()
            time.sleep(0.005)

    except KeyboardInterrupt:
        pass

    plt.close('all')
    try:
        hackrf.trigger_deactivate()
    except Exception:
        pass
    try:
        hackrf.trigger_shutdown()
    except Exception:
        pass
    executor.shutdown()
    hackrf.destroy_node()
    rclpy.try_shutdown()


if __name__ == '__main__':
    main()
