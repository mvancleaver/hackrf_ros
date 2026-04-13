#!/usr/bin/env python3
"""Live side-by-side: old PSD pipeline vs RF/EW review fixes.

Single process, single HackRF. Captures IQ, runs both algorithms on the
same data, updates both plots continuously.
"""
import queue
import threading
import time

import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
import numpy as np

import rclpy
from rclpy.executors import SingleThreadedExecutor
from hackrf_ros.hackrf_lifecycle_node import HackRFLifecycleNode

WIFI_CH = {1: 2412, 6: 2437, 11: 2462}
BAND_LO, BAND_HI = 2400e6, 2500e6

# === OLD pipeline constants ===
FFT_O = 2048
WIN_O = np.hanning(FFT_O)
AVG_O = 8
TRIM_O = int(FFT_O * 0.05)

# === NEW pipeline constants ===
FFT_N = 4096
WIN_N = np.blackman(FFT_N)
WIN_S2 = np.sum(WIN_N ** 2)
AVG_N = 16
TRIM_N = int(FFT_N * 0.10)


def process_old(raw, sr):
    """Original: Hann, /N, dB averaging, no corrections, no overlap."""
    samples = np.frombuffer(raw, dtype=np.int8).astype(np.float32) / 128.0
    n = FFT_O * 2
    accum = np.zeros(FFT_O)
    count = 0
    for i in range(0, len(samples) - n + 1, n):
        frame = samples[i:i + n]
        iq = frame[0::2] + 1j * frame[1::2]
        spec = np.fft.fftshift(np.fft.fft(iq * WIN_O))
        accum += 10.0 * np.log10(np.maximum(np.abs(spec) ** 2 / FFT_O, 1e-20))
        count += 1
    return accum / max(count, 1)


def process_new(raw, sr):
    """Corrected: Blackman, V^2/Hz, linear avg, DC/IQ correction, 50% overlap."""
    samples = np.frombuffer(raw, dtype=np.int8).astype(np.float32) / 128.0
    n = FFT_N * 2
    step = n // 2
    accum = np.zeros(FFT_N)
    count = 0
    for i in range(0, len(samples) - n + 1, step):
        frame = samples[i:i + n]
        iq = frame[0::2] + 1j * frame[1::2]
        iq -= np.mean(iq)
        I, Q = iq.real, iq.imag
        Ip, Qp = np.mean(I ** 2), np.mean(Q ** 2)
        if Ip > 0 and Qp > 0:
            alpha = np.sqrt(Qp / Ip)
            phi = np.mean(I * Q) / np.sqrt(Ip * Qp)
            Q = (Q - phi * I) / alpha
            iq = I + 1j * Q
        spec = np.fft.fftshift(np.fft.fft(iq * WIN_N))
        accum += np.abs(spec) ** 2 / (sr * WIN_S2)
        count += 1
    if count == 0:
        return np.full(FFT_N, -100.0)
    return 10.0 * np.log10(np.maximum(accum / count, 1e-20))


def plan_hops(sr, usable):
    bw = sr * usable
    hops = []
    f = BAND_LO + sr / 2
    while f - sr / 2 < BAND_HI:
        hops.append(f)
        f += bw
    if hops:
        last = BAND_HI - sr / 2
        if last > hops[-1]:
            hops.append(last)
        else:
            hops[-1] = max(last, hops[-1])
    return hops


def stitch_old(hop_data, centers, sr):
    bw = sr / FFT_O
    n = max(int((BAND_HI - BAND_LO) / bw), 1)
    freqs = np.linspace(BAND_LO / 1e6, BAND_HI / 1e6, n)
    psd = np.full(n, -100.0)
    for db, c in zip(hop_data, centers):
        if db is None:
            continue
        hf = np.linspace(c - sr / 2, c + sr / 2, FFT_O)
        t = db[TRIM_O:-TRIM_O]
        tf = hf[TRIM_O:-TRIM_O]
        i0 = np.searchsorted(freqs, tf[0] / 1e6)
        i1 = min(i0 + len(t), n)
        nc = i1 - i0
        if nc > 0:
            psd[i0:i1] = np.interp(
                np.linspace(0, 1, nc), np.linspace(0, 1, len(t)), t)
    return freqs, psd


def stitch_new(hop_data, centers, sr):
    bw = sr / FFT_N
    freqs_hz = np.arange(BAND_LO, BAND_HI, bw)
    n = len(freqs_hz)
    comp = np.zeros(n)
    wt = np.zeros(n)
    for db, c in zip(hop_data, centers):
        if db is None:
            continue
        lin = 10.0 ** (db / 10.0)
        t = lin[TRIM_N:-TRIM_N]
        hf = np.linspace(c - sr / 2, c + sr / 2, FFT_N)
        tf = hf[TRIM_N:-TRIM_N]
        blend = np.ones(len(t))
        tp = max(int(len(blend) * 0.1), 1)
        blend[:tp] = np.linspace(0, 1, tp)
        blend[-tp:] = np.linspace(1, 0, tp)
        indices = ((tf - BAND_LO) / bw).astype(int)
        mask = (indices >= 0) & (indices < n)
        np.add.at(comp, indices[mask], t[mask] * blend[mask])
        np.add.at(wt, indices[mask], blend[mask])
    valid = wt > 0
    comp[valid] /= wt[valid]
    out = np.full(n, -100.0)
    out[valid] = 10.0 * np.log10(np.maximum(comp[valid], 1e-20))
    return freqs_hz / 1e6, out


def main():
    rclpy.init()
    hackrf = HackRFLifecycleNode()
    executor = SingleThreadedExecutor()
    executor.add_node(hackrf)
    threading.Thread(target=executor.spin, daemon=True).start()

    # --- Plot setup FIRST ---
    plt.style.use('dark_background')
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(20, 6))
    fig.canvas.manager.set_window_title(
        'PSD Comparison: Before vs After RF/EW Review')
    fig.subplots_adjust(wspace=0.25)

    # Placeholders
    x_o = np.linspace(2400, 2500, 100)
    x_n = np.linspace(2400, 2500, 100)
    line_o, = ax1.plot(x_o, np.full(100, -40), color='#f9e2af', lw=0.6)
    line_n, = ax2.plot(x_n, np.full(100, -80), color='#a6e3a1', lw=0.6)

    for ax in (ax1, ax2):
        for ch, freq in WIFI_CH.items():
            ax.axvline(freq, color='#ff6688', alpha=0.25, lw=0.5, ls='--')
            ax.text(freq, 0, str(ch), color='#ff6688', fontsize=8,
                    ha='center', alpha=0.6, transform=ax.get_xaxis_transform())
        ax.set_xlim(2400, 2500)
        ax.set_xlabel('Frequency (MHz)', fontsize=10)
        ax.grid(True, alpha=0.15)

    ax1.set_ylabel('Power (dB, arbitrary)', fontsize=10)
    ax1.set_title('BEFORE\nHann 2048 | dB avg | /N | no corrections',
                   fontsize=10, color='#f9e2af')
    ax2.set_ylabel('Power (dB/Hz)', fontsize=10)
    ax2.set_title('AFTER\nBlackman 4096 | linear avg | V\u00b2/Hz | corrected',
                   fontsize=10, color='#a6e3a1')

    plt.ion()
    fig.show()
    fig.canvas.draw()
    fig.canvas.flush_events()

    # --- Now start hardware ---
    hackrf.trigger_configure()
    hackrf.trigger_activate()
    time.sleep(0.5)

    sr = hackrf.get_parameter('sample_rate').value
    hops_o = plan_hops(sr, 0.9)
    hops_n = plan_hops(sr, 0.8)
    all_centers = sorted(set(hops_o + hops_n))

    y1_lo, y1_hi = -50.0, -10.0
    y2_lo, y2_hi = -90.0, -50.0
    running = True
    sweep_num = 0

    def on_close(evt):
        nonlocal running
        running = False
    fig.canvas.mpl_connect('close_event', on_close)

    iq_queue = hackrf._iq_queue

    try:
        while running:
            sweep_num += 1
            t0 = time.monotonic()

            # Capture raw IQ at each hop center
            hop_raw = {}
            for center in all_centers:
                if not running:
                    break
                with hackrf._device_lock:
                    if hackrf._hackrf:
                        hackrf._hackrf.center_freq = int(center)
                time.sleep(0.005)
                while not iq_queue.empty():
                    try:
                        iq_queue.get_nowait()
                    except queue.Empty:
                        break
                time.sleep(0.03)
                chunks = []
                dl = time.monotonic() + 0.3
                while time.monotonic() < dl and len(chunks) < 4:
                    try:
                        chunks.append(iq_queue.get(timeout=0.1))
                    except queue.Empty:
                        continue
                hop_raw[center] = chunks

            if not running:
                break

            # Process both pipelines on same data
            old_psds, old_ctrs = [], []
            for c in hops_o:
                closest = min(all_centers, key=lambda h: abs(h - c))
                chunks = hop_raw.get(closest, [])
                if chunks:
                    accum = np.zeros(FFT_O)
                    cnt = 0
                    for raw in chunks:
                        accum += process_old(raw, sr)
                        cnt += 1
                    old_psds.append(accum / cnt)
                else:
                    old_psds.append(None)
                old_ctrs.append(c)

            new_psds, new_ctrs = [], []
            for c in hops_n:
                closest = min(all_centers, key=lambda h: abs(h - c))
                chunks = hop_raw.get(closest, [])
                if chunks:
                    accum = np.zeros(FFT_N)
                    cnt = 0
                    for raw in chunks:
                        accum += process_new(raw, sr)
                        cnt += 1
                    new_psds.append(accum / cnt)
                else:
                    new_psds.append(None)
                new_ctrs.append(c)

            # Stitch
            freqs_o, psd_o = stitch_old(old_psds, old_ctrs, sr)
            freqs_n, psd_n = stitch_new(new_psds, new_ctrs, sr)
            elapsed = time.monotonic() - t0

            # Update left plot (OLD)
            line_o.set_xdata(freqs_o)
            line_o.set_ydata(psd_o)
            for c in list(ax1.collections):
                c.remove()
            v_o = psd_o[psd_o > -99]
            if len(v_o) > 10:
                y1_lo += 0.2 * (np.percentile(v_o, 1) - 3 - y1_lo)
                y1_hi += 0.2 * (np.percentile(v_o, 99.5) + 3 - y1_hi)
                if y1_hi - y1_lo < 15:
                    mid = (y1_hi + y1_lo) / 2
                    y1_lo, y1_hi = mid - 7.5, mid + 7.5
            ax1.fill_between(freqs_o, y1_lo, psd_o, alpha=0.15, color='#f9e2af')
            ax1.set_ylim(y1_lo, y1_hi)
            pk_o = psd_o.max()
            fl_o = np.median(v_o) if len(v_o) > 0 else -40
            ax1.set_title(
                f'BEFORE (Original)\n'
                f'Hann 2048 | dB avg | /N | 5% trim | no corrections\n'
                f'Peak: {pk_o:.1f} dB | Floor: {fl_o:.1f} dB | '
                f'DR: {pk_o - fl_o:.1f} dB',
                fontsize=9, color='#f9e2af')

            # Update right plot (NEW)
            line_n.set_xdata(freqs_n)
            line_n.set_ydata(psd_n)
            for c in list(ax2.collections):
                c.remove()
            v_n = psd_n[psd_n > -99]
            if len(v_n) > 10:
                y2_lo += 0.2 * (np.percentile(v_n, 1) - 3 - y2_lo)
                y2_hi += 0.2 * (np.percentile(v_n, 99.5) + 3 - y2_hi)
                if y2_hi - y2_lo < 15:
                    mid = (y2_hi + y2_lo) / 2
                    y2_lo, y2_hi = mid - 7.5, mid + 7.5
            ax2.fill_between(freqs_n, y2_lo, psd_n, alpha=0.15, color='#a6e3a1')
            ax2.set_ylim(y2_lo, y2_hi)
            pk_n = psd_n.max()
            fl_n = np.median(v_n) if len(v_n) > 0 else -80
            ax2.set_title(
                f'AFTER (RF/EW Review)\n'
                f'Blackman 4096 | linear avg | V\u00b2/Hz | '
                f'10% trim | DC+IQ corrected\n'
                f'Peak: {pk_n:.1f} dB/Hz | Floor: {fl_n:.1f} dB/Hz | '
                f'DR: {pk_n - fl_n:.1f} dB',
                fontsize=9, color='#a6e3a1')

            fig.suptitle(
                f'Sweep #{sweep_num} | {elapsed:.1f}s | '
                f'{len(all_centers)} hops | '
                f'Same IQ data, different processing',
                fontsize=11, color='#cdd6f4', y=0.02)

            fig.canvas.draw_idle()
            fig.canvas.flush_events()

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
