# Phase 5: PyMayhem Refactor - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.

**Date:** 2026-03-30
**Phase:** 05-pymayhem-refactor
**Areas discussed:** Package structure, pymayhem API design, Driver decoupling, ROS2 bridge design

---

## Package Structure

| Option | Description | Selected |
|--------|-------------|----------|
| Monorepo (Recommended) | All three as sibling dirs in this repo | ✓ |
| Separate repos | pymayhem own repo | |
| Two repos | pymayhem separate, rest together | |

| Option | Description | Selected |
|--------|-------------|----------|
| Yes, publish | pip install pymayhem from PyPI | |
| Local only | pip install -e ./pymayhem | ✓ |
| Prepare for PyPI | Set up pyproject.toml but don't publish | |

## pymayhem API Design

| Option | Description | Selected |
|--------|-------------|----------|
| Domain modules | client.radio, client.ui, client.fs | ✓ |
| Flat client | All methods on one class | |
| Mixed | Core client + sub-objects | |

| Option | Description | Selected |
|--------|-------------|----------|
| Context manager | with MayhemClient() as m: | |
| Explicit open/close | m.open(); m.close() | |
| Both | Support both patterns | ✓ |

| Option | Description | Selected |
|--------|-------------|----------|
| Require unsafe flag | unsafe=True per call | |
| Separate class | UnsafeMayhemClient subclass | ✓ |
| Omit entirely | Don't expose dangerous commands | |

## Driver Decoupling

| Option | Description | Selected |
|--------|-------------|----------|
| Redis hash | hackrf:config for all config | |
| Config file | YAML/JSON loaded at startup | |
| Both | Config file defaults + Redis runtime overrides | ✓ |

| Option | Description | Selected |
|--------|-------------|----------|
| Python script | python -m hackrf_driver | |
| CLI tool | hackrf-driver --freq 433e6 | |
| Both | CLI + module entry point | ✓ |

## ROS2 Bridge Design

| Option | Description | Selected |
|--------|-------------|----------|
| XREAD polling | Poll with block=100ms | |
| Pub/Sub | Subscribe for notifications, then XREAD | ✓ |
| You decide | | |

**Bridge scope (multi-select):**
- IQ topic /hackrf/iq ✓
- State topic /hackrf/state ✓
- Command service (writes to hackrf:cmd) ✓
- Mayhem services (proxied through Redis) ✓

## Claude's Discretion
- Internal threading model
- Redis Pub/Sub channel naming
- Test migration strategy
- CLI framework choice
- Docker restructuring

## Deferred Ideas
- PyPI publishing
- MQTT/ZMQ bridge alternatives
- Docker image restructuring
