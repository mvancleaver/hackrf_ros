# HackRF ROS2 RF Sensor

## What This Is

A ROS2 sensor package that turns a HackRF One SDR into a robot's primary RF awareness system. It captures IQ data, computes calibrated power spectral density, detects and classifies RF signals, and publishes actionable spectrum intelligence for autonomy nodes. Platform-agnostic — works on ground robots, drones, and stationary monitors.

## Core Value

Reliable, calibrated RF spectrum awareness published as standard ROS2 messages that any robot autonomy stack can consume for signal detection, classification, and RF environment mapping.

## Requirements

### Validated

- ✓ Lifecycle driver node with pyhackrf2 (configure/activate/deactivate/shutdown) — existing
- ✓ Calibrated PSD publishing (Blackman window, linear averaging, V^2/Hz, DC/IQ correction) — existing
- ✓ Dynamic retuning via ROS2 parameters (center_freq, sample_rate, gains) — existing
- ✓ Wideband sweep service with Tukey-blended stitching — existing
- ✓ ADC clipping detection and frame discard — existing
- ✓ Hardware diagnostics via /diagnostics topic — existing
- ✓ Live spectrum display nodes — existing
- ✓ Docker deployment with CycloneDDS on Jetson ARM64 — existing

### Active

- [ ] Stamped messages (SpectrumStamped.msg) with header.stamp and frame_id
- [ ] TF frame integration (configurable antenna frame)
- [ ] CFAR energy detector node publishing RFDetectionArray
- [ ] Signal persistence tracker (multi-frame confirmation, ID assignment)
- [ ] Band classification lookup (frequency + bandwidth heuristics)
- [ ] QoS cleanup (BEST_EFFORT for PSD stream, non-blocking param callback)
- [ ] RF occupancy grid (2D heatmap for nav stack integration)
- [ ] Sweep action server (progress feedback, cancel support)
- [ ] IQ recording action server (SigMF format with robot pose metadata)
- [ ] Automatic gain control (adapt LNA/VGA to environment)
- [ ] Multi-observation emitter localization (power + position estimates)
- [ ] Wideband anomaly detection (baseline PSD, flag deviations)
- [ ] Cyclostationary feature extraction (WiFi/BLE/ZigBee disambiguation)
- [ ] KrakenSDR direction finding integration (4-channel coherent AOA)
- [ ] Multi-radio architecture (HackRF sweeps, second SDR tracks)

### Out of Scope

- TX transmission — removed during rescope, separate safety concern
- Mayhem mode-switch command (exit Mayhem UI → HackRF USB-SDR) — in-package since Phase 5 (bounded scope per CONTEXT.md D-00)
- All other Mayhem firmware control (UI navigation, app launch, DFU, file transfer, TX apps) — still separate package concern (pymayhem)
- Redis IQ streaming — replaced by ROS2 topics
- GUI applications — display nodes are optional subscribers, not core

## Context

- **Hardware**: HackRF One (1 MHz–6 GHz, 20 MSPS, 8-bit ADC), Portapack with Mayhem firmware
- **Platform**: Jetson ARM64 in Docker, ROS2 Humble, CycloneDDS
- **Prior work**: Started as monolithic ROS2 driver with Redis/Mayhem/TX. Rescoped to focused lifecycle node. RF/EW engineering review corrected PSD pipeline (see docs/rf-ew-review.md)
- **RF/EW review findings**: Fixed PSD normalization, averaging domain, DC offset, I/Q imbalance, window choice, sweep stitching. Optimized gain parameters.
- **Inter-process DDS**: FastRTPS fails on this ARM64 system. CycloneDDS works. Lifecycle publishers don't transmit over DDS — use regular publishers.
- **Robot platforms**: Must support ground robots, drones, and stationary monitors. TF frame and message stamps enable platform-agnostic integration.

## Constraints

- **Hardware**: Single HackRF One, 8-bit ADC limits dynamic range to ~50 dB
- **Bandwidth**: 20 MHz instantaneous — wideband coverage requires frequency hopping
- **Platform**: Must run in Docker on Jetson ARM64 (ARM NEON, no x86 SIMD)
- **DDS**: CycloneDDS required — FastRTPS broken on this platform
- **Real-time**: Python FFT at edge of throughput (4% of captured data processed). Performance-critical paths may need pyfftw or C++
- **Direction finding**: Requires KrakenSDR hardware (Phase 4) — single HackRF has no AOA capability

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Rescope from monolith to lifecycle node | Original 2800-line codebase too complex, mixed concerns | ✓ Good |
| Drop Redis/Mayhem/TX from core | Separate concerns, reduce complexity | ✓ Good |
| Blackman window over Hann | -58 dB sidelobes for wideband survey dynamic range | ✓ Good |
| LNA=16, VGA=20, amp=off defaults | RF/EW review: prevents ADC saturation, optimal dynamic range | ✓ Good |
| Regular publishers over lifecycle publishers | Lifecycle publishers don't transmit over CycloneDDS on ARM64 | ✓ Good |
| Retune while streaming (no stop/start_rx) | Avoids libhackrf segfault on rapid frequency changes | ✓ Good |
| CycloneDDS over FastRTPS | FastRTPS broken for inter-process on this ARM64 Jetson | ✓ Good |
| SigMF for IQ recording | Standard format, zero conversion overhead, tooling support | — Pending |
| CFAR for detection | Standard ES technique, low compute, works on PSD directly | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-04-13 after project initialization*
