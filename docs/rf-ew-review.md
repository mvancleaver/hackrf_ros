# RF/EW Engineering Review — HackRF PSD Implementation

**Date**: 2026-04-12
**Hardware**: HackRF One (1 MHz - 6 GHz, 20 MSPS max, 8-bit ADC)

## Issues Found by Severity

### Critical (incorrect results)

| # | Issue | Impact | Fix |
|---|-------|--------|-----|
| 1 | PSD normalization divides by N instead of Fs * S2 | Power levels wrong and scale-dependent | Use `\|X[k]\|^2 / (Fs * S2)` where S2 = sum(window^2) |
| 2 | Averaging in dB domain (geometric mean) | Systematic -2.5 dB bias on noise floor | Accumulate linear power, convert to dB once at the end |
| 3 | No DC offset removal | False spike at every hop center frequency | Subtract per-frame mean: `iq -= np.mean(iq)` |

### Important (significant artifacts)

| # | Issue | Impact | Fix |
|---|-------|--------|-----|
| 4 | No I/Q imbalance correction | Ghost images 25-35 dB down from real signals | First-order correction estimating gain/phase from data |
| 5 | Edge trim too small (5% vs 10%) | Amplitude rolloff at hop boundaries from MAX2837 filter | Trim 10% per side, use 80% usable BW |
| 6 | Interpolation in dB domain during stitching | Nonlinear blending errors at hop boundaries | Interpolate in linear power, convert to dB after |
| 7 | No overlap blending between hops | Discontinuities at hop boundaries | Tukey-shaped crossfade weights in overlap region |

### Moderate (reduced performance)

| # | Issue | Impact | Fix |
|---|-------|--------|-----|
| 8 | No ADC clipping detection | Clipped frames produce broadband spectral splatter | Detect >0.5% at +/-127 rails, discard frame |
| 9 | Non-overlapping FFT frames | 50% of window-edge samples wasted | Use 50% overlap (doubles averages for free) |
| 10 | Hann window sidelobes (-31.5 dB) | Insufficient for high dynamic range survey | Blackman window (-58 dB) or Blackman-Harris (-92 dB) |
| 11 | Fragile PLL settle/flush sequence | Potential stale data from previous frequency in hop | Flush, retune, wait 5ms PLL lock, flush again |

### Minor

| # | Issue | Impact | Fix |
|---|-------|--------|-----|
| 12 | Composite grid uses linspace vs arange | Off-by-one bin spacing | Use `np.arange(freq_min, freq_max, bin_width)` |
| 13 | No frequency response calibration | 10-15 dB variation across 1-6 GHz uncorrected | Per-frequency gain table from datasheet |
| 14 | No timestamp on queue entries | No definitive frequency association for IQ data | Tag chunks with sequence/timestamp |

## Optimized Parameters for Low-Noise Wideband Sweeps

| Parameter | Previous | Recommended | Rationale |
|-----------|----------|-------------|-----------|
| sample_rate | 20 MSPS | 20 MSPS | Maximum BW per hop, minimizes total hops |
| lna_gain | 32 dB | **16 dB** | Prevents ADC saturation from strong signals. 8-bit ADC clips at ~-10 dBm at SMA |
| vga_gain | 40 dB | **20 dB** | Keeps noise figure dominated by LNA, not baseband. Total 36 dB is a good compromise |
| amp_enabled | true | **false** | Adds ~11 dB gain but also noise and intermodulation. Only for weak narrowband signals |
| FFT_SIZE | 2048 | **4096** | ~4.9 kHz resolution, +3 dB processing gain. Negligible compute cost |
| PSD_AVERAGING | 8 | **16** | 16 Welch segments reduce variance by 16x. Only needs ~3.3 ms of data at 20 MSPS |
| edge_trim | 5% | **10%** | Matches MAX2837 analog filter rolloff. 80% usable BW |
| window | Hann | **Blackman** | -58 dB sidelobes vs -31.5 dB. Better for wideband survey dynamic range |

### Expected Noise Floor

```
kTB = -174 dBm/Hz + 10*log10(4883 Hz bin) = -137.1 dBm/bin
+ NF (15 dB)                               = -122.1 dBm/bin
+ quantization noise (~2 dB)               = -120.1 dBm/bin
```

With 16-average Welch method: approximately +/- 1 dB ripple on noise floor.

## Correct PSD Formula

```python
# Single FFT frame, properly normalized:
window = np.blackman(FFT_SIZE)
S2 = np.sum(window ** 2)

iq -= np.mean(iq)                    # DC offset removal
windowed = iq * window
spectrum = np.fft.fftshift(np.fft.fft(windowed))
psd = |spectrum|^2 / (Fs * S2)      # V^2/Hz (linear)
psd_db = 10 * log10(psd)            # dB/Hz

# Averaging: accumulate LINEAR power, divide, then log
accum += psd_linear                  # NOT dB
psd_avg = accum / count
psd_db = 10 * log10(psd_avg)        # convert once
```

## Sweep Stitching Procedure

1. **Flush** IQ queue (drain pre-retune data)
2. **Retune** center frequency (while streaming — no stop/start)
3. **Wait 5 ms** for PLL lock
4. **Flush** again (discard transition data)
5. **Collect** averaging frames with 50% overlapping FFTs
6. **Trim** 10% per side (discard filter rolloff)
7. **Blend** into composite using Tukey-shaped weights in linear domain
8. **Repeat** for all hops
9. **Convert** final composite from linear to dB

## I/Q Imbalance Correction (First-Order)

```python
I = iq.real
Q = iq.imag
alpha = sqrt(mean(Q^2) / mean(I^2))     # gain ratio
phi = mean(I*Q) / sqrt(mean(I^2)*mean(Q^2))  # phase error
Q_corrected = (Q - phi*I) / alpha
iq = I + 1j * Q_corrected
```

Improves image rejection from 25-35 dB to 40-50 dB.
