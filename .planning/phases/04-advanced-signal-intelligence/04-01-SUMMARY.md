---
phase: 4
plan: "04-01"
subsystem: message-schema
tags: [schema, multi-radio, device-index, emitter-map, anomaly, cyclo]
dependency_graph:
  requires: []
  provides:
    - hackrf_interfaces/msg/RFDetection.msg (extended with anomaly + cyclo fields)
    - hackrf_interfaces/msg/RFEmitterEstimate.msg
    - hackrf_interfaces/msg/RFEmitterMap.msg
    - device_index parameter in hackrf_lifecycle_node.py
    - launch/multi_radio.launch.py
  affects:
    - 04-02 (anomaly detection — builds on is_anomaly, anomaly_type fields)
    - 04-03 (emitter localization — builds on RFEmitterEstimate/Map messages)
    - 04-04 (cyclostationary classifier — builds on cyclo_classification, cyclo_confidence fields)
tech_stack:
  added: []
  patterns:
    - OpaqueFunction for dynamic node count in launch files
    - Per-radio YAML configs scoped under namespace (/hackrf_N/hackrf_node)
key_files:
  created:
    - hackrf_interfaces/msg/RFEmitterEstimate.msg
    - hackrf_interfaces/msg/RFEmitterMap.msg
    - launch/multi_radio.launch.py
    - config/hackrf_radio_0.yaml
    - config/hackrf_radio_1.yaml
  modified:
    - hackrf_interfaces/msg/RFDetection.msg
    - hackrf_interfaces/CMakeLists.txt
    - hackrf_ros/hackrf_lifecycle_node.py
    - setup.py
decisions:
  - "device_index declared via declare_parameter() in _declare_parameters(), read in on_configure() before HackRF() call"
  - "emitter_loc_node and cyclo_node entry points registered now so 04-02/04-04 plans can implement without setup.py changes"
  - "multi_radio.launch.py uses OpaqueFunction to dynamically generate N nodes at launch time"
metrics:
  duration: "~25 minutes"
  tasks_completed: 3
  files_modified: 4
  files_created: 5
  completed_date: "2026-04-13T06:25:00Z"
---

# Phase 4 Plan 01: Message Schema and Multi-Radio Infrastructure Summary

**One-liner:** Extended RFDetection.msg with four anomaly/cyclo fields, added RFEmitterEstimate/Map message types, plumbed device_index into the HackRF driver, and created a parameterized multi-radio launch file with per-radio YAML configs.

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| Task 1 | 1ea46a1 | Add anomaly + cyclo fields to RFDetection.msg, add RFEmitterEstimate/Map messages |
| Task 2 | 8936730 | Add device_index param to hackrf_lifecycle_node |
| Task 3 | e50f3de | Add multi_radio.launch.py, per-radio configs, and entry points |

## Fields Added to RFDetection.msg

| Field | Type | Purpose |
|-------|------|---------|
| is_anomaly | bool | True when anomaly detector fires (either trigger type) |
| anomaly_type | string | 'power_spike', 'idle_band_new_emitter', or '' |
| cyclo_classification | string | Override label from cyclostationary node (wifi_2_4g, ble, zigbee, '') |
| cyclo_confidence | float32 | Confidence score [0.0, 1.0] for cyclo_classification |

## New Message Types

**RFEmitterEstimate.msg** — per-emitter position estimate with covariance:
- `detection_id`: links to RFDetection.detection_id
- `estimated_x`, `estimated_y`: emitter position in map frame (meters)
- `covariance_xx`, `covariance_yy`: diagonal covariance from optimizer residual (m^2)
- `observation_count`: distinct robot-pose observations used
- `last_seen`: ROS stamp of most recent contributing observation

**RFEmitterMap.msg** — container for all tracked emitter estimates:
- `header`: frame_id='map', stamp=time of last update
- `estimates`: list of RFEmitterEstimate entries

## device_index Parameter

Location: `hackrf_ros/hackrf_lifecycle_node.py`
- Declaration: `_declare_parameters()` method (~line 301)
- Usage: `on_configure()` method (~line 122-131) — reads value and passes to `HackRF(device_index=device_index)`
- Default: 0 (single-radio default, backwards compatible)

## multi_radio.launch.py

- `radio_count` argument: default 2, launches N instances
- Namespace pattern: `/hackrf_0`, `/hackrf_1`, ..., `/hackrf_{N-1}`
- Each instance: reads `config/hackrf_radio_{i}.yaml`, overrides `device_index` with i
- Config radio_0: center_frequency=2.437 GHz (WiFi 2.4 GHz), antenna_frame=hackrf_antenna_0
- Config radio_1: center_frequency=5.8 GHz (drone/FPV band), antenna_frame=hackrf_antenna_1

## Deviations from Plan

None — plan executed exactly as written. The `emitter_loc_node` and `cyclo_node` entry points were added in Task 3 as specified in the plan overview's execution rules.

## Self-Check

PASSED — all plan acceptance criteria met:
- hackrf_interfaces builds cleanly (colcon exit 0)
- hackrf_ros builds cleanly (colcon exit 0)
- RFDetection has is_anomaly, anomaly_type, cyclo_classification, cyclo_confidence (verified via Python import)
- RFEmitterEstimate and RFEmitterMap are importable Python types
- hackrf_lifecycle_node.py contains device_index parameter declaration and HackRF(device_index=device_index) call
- multi_radio.launch.py exists with radio_count default=2 and /hackrf_{i} namespace pattern
- Per-radio YAML configs exist with device_index: 0 and device_index: 1
- setup.py registers multi_radio.launch.py in data_files
