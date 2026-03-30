# HackRF ROS2 Driver

## What This Is

A robust ROS2 driver for the HackRF One SDR running Mayhem firmware (Portapack). It provides full device control — RX streaming, TX with authorization guardrails, and Mayhem app management — through both ROS2 topics and a Redis interface. The driver communicates via pyhackrf2 for IQ streaming and serial (/dev/ttyACM1) for Mayhem-specific commands.

## Core Value

Reliable, safe bidirectional SDR control with IQ data streaming to Redis and TX operations gated behind explicit authorization.

## Requirements

### Validated

- v ROS2 node publishes IQ data from HackRF — existing
- v ROS2 parameter-based device configuration (frequency, gain, sample rate) — existing
- v Real-time IQ visualization node (plotter) — existing
- v Docker deployment with USB device passthrough — existing
- v Robust RX pipeline: thread-safe dual-queue buffering, USB error recovery with exponential backoff, automatic reconnection — Phase 1
- v Proper conventions: HackRFNode class, structured logging, parameter validation (hardware ranges), no bare print() — Phase 1
- v Graceful lifecycle management: clean startup without device, ordered shutdown, deadlock-safe reconfiguration — Phase 1

### Active

- [ ] Redis IQ publishing: stream raw IQ samples to Redis on the host
- [ ] Redis device state: publish device configuration and status (frequency, gain, streaming state) to Redis
- [ ] Redis command interface: control HackRF configuration (frequency, gain, sample rate, bandwidth) via Redis
- [ ] Serial Mayhem control: communicate with Mayhem firmware over /dev/ttyACM1 for app switching and device management
- [ ] TX capability: transmit signals via Mayhem firmware with configurable parameters
- [ ] TX authorization guardrails: TX commands require explicit authorization before execution
- [ ] Mayhem app management: start/stop Mayhem apps (capture, replay, scanner, etc.) via serial commands

### Out of Scope

- Web UI or dashboard — Redis consumers can build their own
- Signal processing / demodulation — out of scope for the driver layer
- Multi-device support — single HackRF One target
- Custom Mayhem firmware modifications — work with existing Mayhem serial protocol

## Context

- **Existing codebase**: Working prototype ROS2 driver using pyhackrf2 with IQ publisher and plotter nodes
- **Mayhem firmware**: Portapack runs Mayhem firmware exposing serial interface at /dev/ttyACM1 alongside standard USB
- **Dual interface**: pyhackrf2 handles IQ bulk transfer (libusb), serial handles Mayhem-specific commands (app control, TX)
- **Known issues**: Class name typo (HackRFPuiblisherNode), no thread safety on sample buffer, minimal parameter validation, race conditions between RX callback and timer threads
- **Deployment**: Docker container on host with USB passthrough, tested on x86_64 and Jetson ARM64
- **Redis**: Running on the host, used as the primary data and command interface for external consumers

## Constraints

- **Hardware**: Single HackRF One with Portapack running Mayhem firmware
- **Device path**: Serial interface at /dev/ttyACM1 (Mayhem), USB bulk via libusb (pyhackrf2)
- **Framework**: ROS2 Humble with Python (rclpy)
- **Data store**: Redis on host for IQ data, device state, and command interface
- **Safety**: TX operations must be gated behind explicit authorization — no accidental transmissions
- **Compatibility**: Must work in existing Docker deployment (empyreanlattice/hackrf_ros:humble)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Keep pyhackrf2 for IQ streaming | Already working, uses efficient USB bulk transfers via libhackrf | -- Pending |
| Serial for Mayhem control | Mayhem firmware exposes ACM serial interface for app/TX control | -- Pending |
| Redis as external interface | User wants IQ data + state + commands accessible outside ROS2 | -- Pending |
| TX requires authorization | Safety critical — prevent accidental transmissions | -- Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? -> Move to Out of Scope with reason
2. Requirements validated? -> Move to Validated with phase reference
3. New requirements emerged? -> Add to Active
4. Decisions to log? -> Add to Key Decisions
5. "What This Is" still accurate? -> Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-03-30 after Phase 1 completion*
