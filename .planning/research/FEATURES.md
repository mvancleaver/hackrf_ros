# Feature Landscape

**Domain:** HackRF One ROS2 Driver — Redis integration, Mayhem serial control, TX capability
**Researched:** 2026-03-29
**Confidence:** MEDIUM-HIGH (Mayhem serial protocol verified from official wiki; Redis patterns from official docs; TX API from pyhackrf2 repo)

---

## Table Stakes

Features that must exist or the system is unreliable/incomplete. Missing any of these makes the driver unfit for its stated purpose.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Thread-safe IQ sample buffer | Race condition between RX callback (libusb thread) and timer callback (ROS2 thread) will cause data corruption or crashes under load | Med | Fix `current_samples_buffer` with `threading.Lock`; current code has no lock |
| Reconnection / error recovery for RX stream | USB devices disconnect; driver must survive and reconnect without operator intervention | Med | Requires retry loop with backoff in startup; monitor `is_hackrf_streaming` state |
| Redis IQ stream publish | Primary external data interface; downstream consumers cannot use ROS2 topics directly | Med | Use Redis Streams (`XADD`) not Pub/Sub — messages persist for late-joining consumers; key: `hackrf:iq:stream` |
| Redis device state hash | External consumers need to query current config without subscribing to a stream | Low | `HSET hackrf:state frequency <hz> gain <db> sample_rate <sps> streaming <bool>` — updated on every config change |
| Redis command interface (RX config) | Consumers must be able to change frequency, gain, sample rate, bandwidth without modifying ROS2 params directly | Med | Subscribe to Redis channel `hackrf:cmd` or poll a list; validate and apply to device |
| Serial port open/close lifecycle | `/dev/ttyACM1` must be opened at startup and closed cleanly at shutdown; unclosed port blocks reuse | Low | Use `pyserial` with explicit open in `configure()`, close in `destroy_node()` |
| Mayhem `appstart` command | Starting/stopping Mayhem apps (Capture, Replay, Scanner) is the primary Mayhem control action | Low | `appstart <short_name>\r\n` via serial; read response line to confirm |
| Mayhem `applist` query | Driver must know which apps are available before issuing `appstart`; avoids invalid commands | Low | Issue at startup; cache result; format is `<short_name> <full_name> <category>` per line |
| TX authorization gate | No accidental transmission. TX must require an explicit `authorized: true` flag in every command, checked immediately before `start_tx()` | Low | Stateless check — no persistent auth state that expires; authorization is per-command, not a session |
| TX stop on node shutdown | `stop_tx()` must be called in `destroy_node()` if TX is active; leaving TX running after process exit is an RF safety violation | Low | Add `is_transmitting` flag; `destroy_node()` calls `stop_tx()` then `stop_rx()` |
| Parameter validation with ranges | Out-of-range frequency/gain silently corrupts device state; HackRF One is 1 MHz–6 GHz, TXVGA 0–47 dB, RXVGA 0–62 dB, LNA 0–40 dB (8 dB steps) | Low | Validate in `_configure_hackrf()` before assigning to device; raise `ParameterException` on invalid |
| Class naming fix (`HackRFPuiblisherNode`) | Broken class name causes import errors, confusing logs, and bad tooling behavior | Trivial | Rename to `HackRFPublisherNode`; update all references and entry points |
| Structured logging (no bare `print`) | Debug `print` statements bypass ROS2 logger, disappear in deployment, and cannot be filtered by severity | Trivial | Replace all `print` with `self.get_logger().debug/info/warn/error` |

---

## Differentiators

Features that distinguish this driver from a basic HackRF ROS2 publisher. Not universally expected, but high value for this project's use case.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Redis Streams for IQ (not Pub/Sub) | Consumers that miss a burst do not lose data; replay is possible; consumer groups enable multiple independent readers | Low | `XADD hackrf:iq:stream * i_data <bytes> q_data <bytes> timestamp <ns>`; use `MAXLEN ~` to cap memory |
| `setfreq` via serial (app-aware) | Mayhem's `setfreq` adjusts frequency in running apps without restarting them; faster than stop/reconfigure/restart cycle | Low | Works in: Audio, Capture, ERT, Pocsag, APRS, Level, Looking Glass, Sonde, SubGHZd, Weather — document incompatible apps |
| `radioinfo` serial query | Read back actual device state from Mayhem (frequency, bandwidth, sample rate, modulation) to verify config was applied | Low | Issue after `setfreq`; parse response to confirm round-trip |
| TX via pyhackrf2 `start_tx()` | Direct IQ waveform transmission without requiring a Mayhem app; useful for custom signal generation from ROS2 nodes | High | Requires: 1) authorization gate, 2) IQ waveform data source (from Redis or ROS2 topic), 3) half-duplex mode switch from RX |
| Mayhem POCSAG TX via `sendpocsag` | Allows text message transmission through Mayhem without building a custom modulator | Med | `sendpocsag <addr> <msglen> [baud] [type] [function] [phase]`; requires auth gate before issuing |
| Mayhem file replay via serial + SD | Stage a `.C16`/`.C8` capture file to Mayhem SD card, then `appstart Replay` to retransmit | High | Requires: SD-over-USB file transfer (`fopen`/`fwrite`/`fclose`) then app launch; complex multi-step sequence |
| ROS2 Lifecycle Node migration | Managed states (Unconfigured → Inactive → Active) enable clean hardware attach/detach without process restart | High | Requires rearchitecting `__init__` into `on_configure`/`on_activate`/`on_deactivate`/`on_cleanup`; deferred unless rebuild budget allows |
| Redis `hackrf:cmd` command topics | Named topics per command type (`hackrf:cmd:frequency`, `hackrf:cmd:tx`) allow consumers to subscribe to specific control channels without parsing a shared command bus | Low | Use Redis Pub/Sub for commands (fire-and-forget is acceptable; commands missing during downtime are acceptable); use LPUSH/BRPOP list pattern if guaranteed delivery needed |
| Mayhem `screenshot` / `screenframe` bridge to ROS2 | Publish Mayhem display state as a ROS2 Image topic for remote monitoring | High | Niche; only useful if someone needs remote UI visibility; deferred |
| `gotgps` serial injection | Feed GPS coordinates from ROS2 nav stack into Mayhem for location-aware apps (e.g., APRS) | Low | Single serial command; useful if this driver runs on a mobile platform |

---

## Anti-Features

Features to deliberately NOT build. Building these creates scope creep, maintenance burden, or safety risk.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| Web UI / dashboard | PROJECT.md explicitly excludes this; Redis consumers build their own | Document Redis key schema clearly so consumers can self-serve |
| Signal processing / demodulation | Driver layer responsibility ends at IQ delivery; demodulation belongs in consumer nodes | Publish raw IQ to Redis; let downstream nodes demodulate |
| Multi-device support | Single HackRF One is the target; multi-device adds device indexing complexity with no current user | Hard-code single device; `pyhackrf2.HackRF()` defaults to first device |
| Persistent TX authorization session | A time-limited "TX enabled" mode creates a window where accidental TX is possible if the session isn't revoked | Per-command authorization only — every TX command must carry `authorized: true` |
| Automatic frequency hopping | Would conflict with Mayhem app state and create unpredictable RF behavior | Expose `setfreq` control; let consumers orchestrate hopping externally |
| Custom Mayhem firmware modifications | Explicitly out of scope per PROJECT.md; adds firmware dependency management overhead | Work with the existing serial protocol; document which commands are available per firmware version |
| Signal recording to local disk | Mayhem's Capture app handles on-device recording; duplicating in ROS2 creates dual storage management | Expose Redis Streams with a long `MAXLEN`; consumers persist to disk if needed |
| ROS2 Lifecycle Node (in this milestone) | Correct long-term architecture but is a full rewrite of the node init flow; too much risk for a milestone focused on Redis + TX + serial | Fix thread safety and error recovery in current node pattern; log lifecycle migration as future work |
| TX waveform generation | Driver should transmit given IQ data, not generate waveforms itself | Accept IQ bytes from Redis or a ROS2 topic; do not implement modulators |

---

## Feature Dependencies

```
Parameter validation (ranges)
  ← Required by: TX via pyhackrf2 (must validate freq/gain before transmit)
  ← Required by: Redis command interface (must validate before applying)

Thread-safe IQ buffer
  ← Required by: Redis IQ stream publish (cannot read buffer safely without lock)

Serial port lifecycle (open/close)
  ← Required by: applist query (must have open port at startup)
  ← Required by: appstart command
  ← Required by: setfreq serial command
  ← Required by: sendpocsag TX

applist query (cached at startup)
  ← Required by: appstart (need valid short names)

TX authorization gate
  ← Required by: TX via pyhackrf2 start_tx()
  ← Required by: sendpocsag
  ← Required by: Mayhem replay TX

Redis device state hash
  ← Required by: Redis command interface (state reflects applied config)

TX stop on shutdown
  ← Required by: TX via pyhackrf2 (must stop TX in destroy_node)
```

---

## MVP Recommendation

For this milestone, prioritize in order:

1. **Thread-safe IQ buffer** — Foundational; every other feature builds on reliable data acquisition. Implement `threading.Lock` around `current_samples_buffer`.

2. **Error recovery and reconnection** — Makes the driver production-grade; without it a single USB event kills the session.

3. **Parameter validation** — Low effort, prevents subtle bugs in all downstream features.

4. **Class naming + logging cleanup** — Zero functional risk; enables readable logs for debugging all subsequent work.

5. **Redis IQ stream publish** — Core new feature; use `redis-py` with `XADD` to `hackrf:iq:stream`; cap with `MAXLEN ~ 10000`.

6. **Redis device state hash** — Cheap to add alongside IQ publish; consumers need it.

7. **Redis command interface** — Closes the control loop; consumers can now drive the radio.

8. **Serial port lifecycle + applist** — Foundation for all Mayhem control.

9. **appstart / setfreq / radioinfo** — Primary Mayhem control commands; implement as thin wrappers around serial write/readline.

10. **TX authorization gate + pyhackrf2 TX** — Implement last; requires all above to be solid first. Gate is mandatory before any transmit path goes live.

Defer to future milestones:
- **Mayhem POCSAG TX via sendpocsag** — Useful but requires careful testing; TX baseline first.
- **Mayhem file replay** — Multi-step SD card workflow; high complexity, low frequency of use.
- **ROS2 Lifecycle Node migration** — Correct architecture but full rewrite scope; plan as a dedicated refactor milestone.

---

## Mayhem Serial Protocol Reference

Commands confirmed from official Mayhem firmware wiki (HIGH confidence):

| Command | Format | Notes |
|---------|--------|-------|
| `applist` | `applist\r\n` | Returns `<short_name> <full_name> <category>` per line |
| `appstart` | `appstart <short_name>\r\n` | Only one app runs at a time; previous app stops |
| `setfreq` | `setfreq <freq_hz>\r\n` | Works in specific apps only (Audio, Capture, ERT, POCSAG, APRS, Level, Looking Glass, Sonde, SubGHZd, Weather) |
| `radioinfo` | `radioinfo\r\n` | Returns current frequency, bandwidth, sample rate, modulation |
| `sendpocsag` | `sendpocsag <addr> <msglen> [baud] [type] [func] [phase]\r\n` | baud: 512/1200/2400; type: 0/1/2; func: A/B/C/D; phase: P/N |
| `screenshot` | `screenshot\r\n` | Saves screenshot to SD card |
| `rtcget` | `rtcget\r\n` | Returns current device time |
| `sysinfo` | `sysinfo\r\n` | Returns heap, stack, CPU usage |
| `button` | `button <1-8>\r\n` | Simulates button: 1=Right, 2=Left, 3=Down, 4=Up, 5=Select |
| `touch` | `touch <x> <y>\r\n` | Simulates touchscreen tap |
| `gotgps` | `gotgps <lat> <lon> [alt] [speed] [sats]\r\n` | Injects GPS data into running apps |

**Serial settings:** Standard USB-ACM; no special baud rate needed (virtual COM port). Terminate commands with `\r\n`. Read response until newline.

---

## Redis Key Schema

Recommended key naming (colon-separated hierarchy, standard Redis convention):

| Key | Type | Content | When Updated |
|-----|------|---------|--------------|
| `hackrf:iq:stream` | Stream | Fields: `data` (raw bytes), `timestamp_ns` (int64) | Every IQ publish tick |
| `hackrf:state` | Hash | `frequency`, `sample_rate`, `lna_gain`, `vga_gain`, `txvga_gain`, `bandwidth`, `streaming`, `tx_active`, `connected` | On every config change and state transition |
| `hackrf:cmd` | Pub/Sub channel | JSON: `{"cmd": "setfreq", "value": 433920000}` | Written by consumers; read by driver |
| `hackrf:mayhem:state` | Hash | `active_app`, `serial_connected`, `last_cmd`, `last_response` | On every serial interaction |
| `hackrf:mayhem:cmd` | Pub/Sub channel | JSON: `{"cmd": "appstart", "app": "Capture"}` | Written by consumers |

---

## Sources

- [Mayhem Firmware USB Serial Console Wiki](https://github.com/portapack-mayhem/mayhem-firmware/wiki/USB-Serial-Console) — HIGH confidence, official docs
- [pyhackrf2 GitHub Repository](https://github.com/eizemazal/pyhackrf2) — HIGH confidence, official library
- [Redis Streams Documentation](https://redis.io/docs/latest/develop/data-types/streams/) — HIGH confidence, official docs
- [Redis Pub/Sub Documentation](https://redis.io/docs/latest/develop/pubsub/) — HIGH confidence, official docs
- [How to Use Redis Streams vs Pub/Sub](https://oneuptime.com/blog/post/2026-01-21-redis-streams-vs-pubsub/view) — MEDIUM confidence
- [Redis Key Design and Naming Conventions](https://oneuptime.com/blog/post/2026-01-21-redis-key-design-naming/view) — MEDIUM confidence
- [ROS2 Lifecycle Node Design](https://design.ros2.org/articles/node_lifecycle.html) — HIGH confidence, official ROS2 docs
- [MayhemHub Web Interface](https://github.com/portapack-mayhem/MayhemHub) — MEDIUM confidence (protocol details not publicly documented in repo)
- [Portapack Mayhem Firmware](https://github.com/portapack-mayhem/mayhem-firmware) — HIGH confidence, source of truth
