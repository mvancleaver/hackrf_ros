---
status: testing
phase: 01-rx-pipeline-correctness
source: [01-01-SUMMARY.md, 01-02-SUMMARY.md, 01-03-SUMMARY.md, 01-VERIFICATION.md]
started: 2026-03-30T12:00:00Z
updated: 2026-03-30T12:00:00Z
---

## Current Test

number: 1
name: IQ Data Streaming
expected: |
  With HackRF attached, run the node and echo the topic:
  `ros2 run hackrf_ros hackrf_node` then `ros2 topic echo /hackrf/iq`
  Continuous Float32MultiArray messages appear with interleaved I/Q floats normalized to [-1, 1].
awaiting: user response

## Tests

### 1. IQ Data Streaming
expected: With HackRF attached, run `ros2 run hackrf_ros hackrf_node` then `ros2 topic echo /hackrf/iq`. Continuous Float32MultiArray messages appear with interleaved I/Q floats normalized to [-1, 1].
result: [pending]

### 2. Parameter Rejection
expected: Run `ros2 param set /hackrf_node center_frequency 8000000000.0` (8 GHz, above 6 GHz limit). The command returns failure, a warning is logged, and the device frequency stays unchanged.
result: [pending]

### 3. USB Disconnect/Reconnect
expected: Unplug HackRF during active streaming, wait 2s, replug. Node logs reconnect attempts with exponential delay visible (1s, 2s, 4s...). Device resumes streaming after replug without node restart.
result: [pending]

## Summary

total: 3
passed: 0
issues: 0
pending: 3
skipped: 0
blocked: 0

## Gaps

[none yet]
