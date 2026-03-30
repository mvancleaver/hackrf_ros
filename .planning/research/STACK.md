# Technology Stack

**Project:** HackRF ROS2 Driver — Milestone: Redis + Serial + TX
**Researched:** 2026-03-29
**Scope:** Additions only — Redis integration, serial Mayhem control, TX guardrails. Does not re-cover ROS2/pyhackrf2 baseline from `.planning/codebase/STACK.md`.

---

## New Dependencies

### Redis Client

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| redis-py | `>=7.4.0` | Redis client — IQ streaming, device state, command interface | Latest stable (released 2026-03-24). v7.x is the current active major series; v5/v6 are maintenance-only. Requires Python >=3.10, which is satisfied by ROS2 Humble's Python 3.10. |
| hiredis | `>=3.3.1` | C-accelerated response parser for redis-py | Latest stable (released 2026-03-16). Zero code changes required — redis-py auto-detects and uses it when installed. Provides measurable throughput improvement for high-frequency XADD calls from the IQ callback. |

**Confidence: HIGH** — Versions verified directly from PyPI (pypi.org/project/redis/, pypi.org/project/hiredis/).

**Why NOT redis-py <7:** The v5 and v6 branches are in maintenance mode. v7 introduced OpenTelemetry metrics support and has active bug fixes. Given no requirement to support Python 3.8/3.9, pinning to v7 is correct.

**Why NOT aioredis:** aioredis was merged into redis-py itself in v4.2. Do not install it as a separate package — it is abandoned.

**Why NOT walrus / pottery:** Higher-level Redis abstractions add indirection without benefit for a driver that needs explicit XADD/XREAD/SET control. Use redis-py directly.

---

### Serial Communication

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| pyserial | `>=3.5` | Serial communication with Mayhem firmware over /dev/ttyACM1 | Latest stable release. Mature, no active development needed — v3.5 has been stable since 2020 with no functional gaps for ACM device use. Ships with the Docker base image in most configurations. |

**Confidence: HIGH** — Version verified from pypi.org/project/pyserial/ and GitHub releases. No v3.6 exists as of 2026-03-29; v3.5 is the current and final stable release.

**Why NOT pyserial-asyncio:** The ROS2 node uses a dedicated reader thread (not asyncio event loop). pyserial-asyncio introduces asyncio dependency for no benefit in a threaded architecture. Stick with synchronous `serial.Serial` plus a daemon thread.

**Why NOT serial (legacy package):** `serial` on PyPI is the old package name — it redirects to pyserial but is not the canonical install target. Always use `pip install pyserial`.

---

## Mayhem Firmware Serial Protocol

**Confidence: MEDIUM** — Verified from the official Mayhem firmware GitHub wiki (portapack-mayhem/mayhem-firmware/wiki/USB-Serial-Console). TX-specific commands are not documented beyond POCSAG; other TX modes are controlled via `appstart` + `setfreq`.

### Connection Parameters

- **Device path:** `/dev/ttyACM1` (per PROJECT.md; ACM interfaces are USB CDC virtual serial — baud rate setting is ignored by the OS driver, data always transfers at USB speed)
- **Recommended baud rate in pyserial:** `115200` (conventional; has no effect on USB CDC but some tools require a non-zero value)
- **Line ending:** `\r\n` (ChibiOS/RT console convention)
- **Read timeout:** `1.0` second (prevents indefinite block on unresponsive device)

### Confirmed Serial Commands

| Command | Syntax | Purpose |
|---------|--------|---------|
| `help` | `help` | List all available commands |
| `applist` | `applist` | List apps startable via `appstart` (returns: short_name, full_name, category per line) |
| `appstart` | `appstart <short_name>` | Launch named app, stops any currently running app |
| `setfreq` | `setfreq <Hz>` | Set radio frequency in Hz for apps that support it (Capture, APRS, Pocsag, etc.) |
| `radioinfo` | `radioinfo` | Read back current frequency, bandwidth, sample rate, modulation |
| `sendpocsag` | `sendpocsag <addr> <msglen> [baud] [type] [function] [phase]` | Initiate POCSAG TX |
| `reboot` | `reboot` | Reboot PortaPack |
| `screenshot` | `screenshot` | Capture screen to SD card |
| `button` | `button <1-8>` | Simulate hardware button press |

**Important limitation:** There is no generic `settx` or `transmit` command in the Mayhem serial protocol. TX is initiated by starting the appropriate app (e.g., replay, POCSAG, SSTV) via `appstart`, then optionally setting frequency via `setfreq`. The driver must model TX as: authorize → appstart TX_app → setfreq → (TX runs) → appstart some_RX_app or reboot to stop.

---

## Redis Interface Design

### Data Structures

| Key Pattern | Redis Type | Content | Notes |
|-------------|------------|---------|-------|
| `hackrf:iq` | Stream (XADD) | `{samples: <bytes>}` | Binary IQ chunks, trimmed with MAXLEN ~= 100 entries |
| `hackrf:state` | Hash (HSET) | `{freq_hz, sample_rate, gain_db, streaming, app}` | Updated on every parameter change |
| `hackrf:cmd` | Stream (XREAD block) | `{cmd: "set_freq", value: "433920000"}` | Driver reads with blocking XREAD, consumer group optional |
| `hackrf:tx:auth` | String (SET EX) | `"1"` | TX authorization token; expires after N seconds (hard interlock) |

**Why Streams for IQ (not Pub/Sub):**
Pub/Sub drops messages if the subscriber is slow or disconnected — unacceptable for an IQ buffer where late consumers must be able to catch up. Streams with MAXLEN trimming provide a ring-buffer semantic: consumers can read at their own pace within a bounded window. Latency overhead vs Pub/Sub is ~1-2 ms, acceptable for SDR data that isn't sub-millisecond-sensitive.

**Why Streams for commands (not lists/Pub/Sub):**
XREAD BLOCK gives blocking wait with consumer group support if needed later. Consumer groups allow the driver to acknowledge processed commands, preventing duplicate execution on restart.

**Why Hash for state (not string/JSON):**
HSET allows atomic partial updates (update frequency without clobbering gain). Consumers can HGETALL or subscribe to keyspace notifications on targeted fields.

---

## TX Authorization Guardrail Design

**Confidence: MEDIUM** — No established open-source SDR TX authorization library exists. The pattern below is derived from safety-critical software interlock principles (hardware E-stop analogs), adapted for this use case. Verified as the correct implementation approach.

### Pattern: Time-bounded Authorization Token

TX must not proceed without a Redis key `hackrf:tx:auth` that is:

1. Set explicitly by the external operator (`SET hackrf:tx:auth 1 EX 30`)
2. Checked atomically before every TX operation
3. Deleted immediately after TX begins (one-shot, not persistent)

```
External operator:  SET hackrf:tx:auth 1 EX 30   # authorize for 30s window
Driver (on TX cmd):
    token = r.getdel("hackrf:tx:auth")            # atomic read+delete
    if not token:
        raise TXNotAuthorized
    # proceed with appstart TX_app
```

**Why `getdel` (atomic read+delete):**
Prevents two concurrent TX commands both seeing a valid token. `GETDEL` is a single atomic Redis operation (available since Redis 6.2, included in all target Redis versions).

**Why TTL on the token:**
An operator who sets auth and walks away cannot leave a permanently armed transmitter. The TTL forces re-authorization on any TX attempt after the window expires.

**Why NOT a ROS2 service call for auth:**
The authorization lives in Redis so external consumers (non-ROS processes) can participate in the safety model. ROS2-only auth would exclude Redis-native consumers from the safety gate.

**Secondary guardrail:** The driver maintains a Python-side `_tx_authorized: bool = False` flag that is set only when `getdel` returns a valid token and cleared at TX completion. This prevents any re-entrant TX path from bypassing the Redis check.

---

## Installation

```bash
# Add to requirements or Dockerfile pip install block:
pip install "redis>=7.4.0" "hiredis>=3.3.1" "pyserial>=3.5"
```

```dockerfile
# In Dockerfile, alongside existing pip installs:
RUN pip3 install redis hiredis pyserial
```

**Note on Docker:** The existing container runs in host network mode. Redis on the host is reachable at `host.docker.internal` (or `172.17.0.1` on Linux Docker default bridge, or `localhost` when `--network host`). With `network: host` (current docker-compose.yaml setting), `localhost` resolves correctly inside the container.

---

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Redis client | redis-py 7.4.0 | aioredis | Merged into redis-py; abandoned as standalone |
| Redis client | redis-py 7.4.0 | walrus | Abstraction overhead, no benefit for explicit stream control |
| Redis client | redis-py 7.4.0 | redis-py 5.x/6.x | Maintenance-only; v7 is active series |
| IQ transport | Redis Streams | Redis Pub/Sub | Pub/Sub loses messages on slow/disconnected consumers |
| IQ transport | Redis Streams | Redis Lists (RPUSH/BLPOP) | Streams have built-in consumer groups, ID tracking, trimming |
| Serial | pyserial 3.5 | pyserial-asyncio | No asyncio event loop in this driver; threaded model is correct |
| TX safety | Redis key + TTL | ROS2 service authorization | Redis auth allows non-ROS consumers to participate in safety gate |

---

## Sources

- redis-py PyPI: https://pypi.org/project/redis/ (verified 2026-03-29)
- hiredis PyPI: https://pypi.org/project/hiredis/ (verified 2026-03-29)
- redis-py releases: https://github.com/redis/redis-py/releases (v7.4.0, March 24, 2025)
- hiredis releases: https://github.com/redis/hiredis-py/releases (v3.3.1, March 16, 2026)
- pyserial PyPI: https://pypi.org/project/pyserial/ (v3.5, latest stable)
- Mayhem firmware USB Serial Console wiki: https://github.com/portapack-mayhem/mayhem-firmware/wiki/USB-Serial-Console
- Redis Streams vs Pub/Sub: https://dev.to/lovestaco/redis-pubsub-vs-redis-streams-a-dev-friendly-comparison-39hm
- redis-py thread safety: https://github.com/redis/redis-py/issues/3669
- redis-py connections docs: https://redis.readthedocs.io/en/stable/connections.html

---

*Stack research: 2026-03-29*
