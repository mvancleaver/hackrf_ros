# Phase 4: TX Authorization - Research

**Researched:** 2026-03-29
**Domain:** pyhackrf2 TX API, Redis GETDEL (6.2+ upgrade path), TXController class design, frequency allowlist, half-duplex switching
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** One-token-per-TX authorization via Redis GETDEL. Token set with `SET hackrf:tx:auth <uuid> EX 60` (60-second TTL). Consumed atomically with GETDEL on TX command.
- **D-02:** Upgrade Redis to 6.2+ to use native GETDEL. This is an infrastructure prerequisite for Phase 4.
- **D-03:** Token TTL is 60 seconds — forces near-immediate use after authorization but allows multi-step TX setup.
- **D-04:** Block all restricted bands by default: cellular (700-900 MHz, 1700-2100 MHz, 2500-2700 MHz), aviation (108-137 MHz, 960-1215 MHz, 1030/1090 MHz), emergency/public safety (150-174 MHz, 450-470 MHz).
- **D-05:** ROS2 parameter `tx_freq_filter_enabled` (default True) as primary disable flag. Redis key `hackrf:tx:freq_filter_override` as secondary remote override. Both must agree — filter disabled only when both say "disabled".
- **D-06:** TX command targeting a restricted frequency is hard-rejected regardless of authorization token.
- **D-07:** Per-session flag: set once at startup via Redis key `hackrf:tx:antenna_confirmed` or ROS2 parameter. Persists until node restart. TX blocked until confirmed.
- **D-08:** ROS2 parameter `tx_skip_antenna_check` (default False) for automated testing environments. When True, antenna confirmation is bypassed.
- **D-09:** Start/stop TX model: `start_tx` command begins transmission, `stop_tx` ends it. IQ data for TX is streamed to a Redis key (`hackrf:tx:iq_data`) which the TX loop reads.
- **D-10:** Half-duplex handling: `start_tx` automatically pauses RX streaming (stop_rx), TX runs, `stop_tx` automatically resumes RX streaming (start_rx). Transparent to the caller.
- **D-11:** TX automatically stopped on node shutdown — `stop_tx()` called in `destroy_node()` before any other cleanup (TX-06).
- **D-12:** TX commands routed through Redis `hackrf:cmd` with `auth_token` field required: `{"action": "start_tx", "freq_hz": 433000000, "auth_token": "<uuid>"}`.

### Claude's Discretion

- TXController class design (helper class vs integrated into HackRFNode)
- pyhackrf2 start_tx() callback implementation details
- Exact band boundary definitions (research should verify exact MHz ranges)
- TX IQ data format and Redis key structure for streaming TX samples
- Thread model for TX data feeding

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.

</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TX-01 | TX via pyhackrf2 start_tx() with explicit half-duplex RX-to-TX mode switch | Verified: `hackrf.start_tx()` uses `self.buffer` (bytearray); stop_rx then set buffer then start_tx sequence confirmed from source code |
| TX-02 | Frequency allowlist blocks transmission on restricted bands (cellular, aviation, emergency) | Verified: band boundaries from ITU/FCC; implemented as list of (min_hz, max_hz) tuples |
| TX-03 | Configurable flag to disable frequency allowlist for authorized testing environments | Architecture: dual-disable model (D-05); both ROS2 param AND Redis key must agree to disable |
| TX-04 | Antenna confirmation required before any TX operation (explicit user acknowledgment) | Architecture: per-session flag via Redis key or ROS2 param (D-07); skip flag for testing (D-08) |
| TX-05 | One-token-per-TX authorization via Redis GETDEL (no persistent armed state) | Verified: Redis 6.0.16 on host does NOT support GETDEL — upgrade to 6.2+ required; Lua script fallback confirmed working on 6.0.16 |
| TX-06 | TX automatically stopped on node shutdown (stop_tx() in destroy_node) | Architecture: TXController.stop() called first in destroy_node — before RedisBridge.close() |
| TX-07 | TX commands routed through Redis command interface with authorization field required | Architecture: `_COMMAND_HANDLERS` in redis_bridge.py extended with `start_tx` and `stop_tx` entries |

</phase_requirements>

---

## Summary

Phase 4 implements safe, authorized TX using pyhackrf2's `start_tx()` via a `TXController` helper class. The pyhackrf2 1.0.3 source has been directly inspected and verified: `start_tx()` takes no callback argument — it reads IQ data from `hackrf.buffer` (a bytearray). The internal `_tx_callback` feeds `hackrf.buffer` in 1MB chunks to the USB transfer pipeline until the buffer is empty. This means TX is buffer-based, not streaming: the full IQ payload must be loaded into `hackrf.buffer` before `start_tx()` is called.

The critical infrastructure prerequisite is the Redis version. The running system has Redis 6.0.16, which does NOT support GETDEL (requires 6.2+). Decision D-02 mandates upgrading Redis before this phase executes. Two upgrade paths exist: (1) snap install redis (8.6.2 available), (2) Lua script atomic GET+DEL fallback that works on 6.0.16. The Lua path has been live-tested and confirmed working. The plan should implement the Lua fallback as Plan Wave 0 and the Redis upgrade as optional enhancement, since both paths are available.

The HackRF is strictly half-duplex at the hardware level. `start_tx()` and `start_rx()` are mutually exclusive — calling `start_tx()` while RX is active will fail or corrupt state. The driver already has `_stop_rx_if_running()` and `_start_rx_if_stopped()` methods (implemented in Phase 3) that TXController must invoke for the half-duplex mode switch. A 100ms firmware settle delay is required between stop_rx and start_tx (confirmed pattern from Phase 1 and libhackrf issue #916).

**Primary recommendation:** Implement `TXController` as a standalone helper class in `hackrf_ros/tx_controller.py` following the `MayhemSerial`/`RedisBridge` pattern exactly. Use the Lua GETDEL fallback so Phase 4 is deployable without a Redis upgrade. Document the Redis upgrade path as a follow-on step.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pyhackrf2 | 1.0.3 | HackRF TX via `start_tx()` / `stop_tx()` | Installed in Docker image; source-verified TX API |
| redis-py | 7.4.0 (installed) | GETDEL token consumption (Lua fallback or native) | Already installed; Lua `r.eval()` confirmed working on Redis 6.0.16 |
| threading.RLock | stdlib | Protect TXController state | RLock allows re-entrant calls from same thread; consistent with `_device_lock` pattern |
| queue.Queue | stdlib | TX IQ data feed from Redis key to `hackrf.buffer` | Thread-safe; already used throughout driver |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| uuid | stdlib | Generate auth token UUIDs | Callers generate UUID, set `hackrf:tx:auth`; driver validates |
| numpy | project dep | Convert float32 IQ back to int8 for `hackrf.buffer` | pyhackrf2 TX expects raw int8 signed bytes interleaved [I,Q,I,Q,...] |
| time | stdlib | 100ms settle delay between stop_rx and start_tx | Firmware settling per libhackrf issue #916 |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Lua GETDEL fallback | Native `r.getdel()` | Native requires Redis 6.2+; Lua works on 6.0.16 and is equally atomic. Use Lua now, switch to native after upgrade |
| snap Redis 8.6.2 | Upgrade via redis.io PPA | Snap is simpler single-command install; PPA requires apt-key setup. Either works; snap is less invasive |
| TXController as helper class | Inline in HackRFNode | Helper class pattern is established (MayhemSerial, RedisBridge); testable without ROS2; cleaner separation |
| Buffer-based TX (current pyhackrf2 model) | Callback-based streaming TX | pyhackrf2 1.0.3 has no streaming TX callback hook — only buffer model exists in current version |

**Installation:** pyhackrf2 already installed in Docker. No new pip packages required for Phase 4.

---

## pyhackrf2 TX API (Source-Verified)

**Confidence: HIGH** — Directly inspected pyhackrf2 1.0.3 wheel (`pyhackrf2/__init__.py`).

### start_tx() — No Callback Parameter

```python
# pyhackrf2 1.0.3 source: pyhackrf2/__init__.py line 304
def start_tx(self) -> None:
    """Send data from self.buffer to HackRF."""
    self._transceiver_mode = TransceiverMode.HACKRF_TRANSCEIVER_MODE_TRANSMIT
    self._check_error(
        libhackrf.hackrf_start_tx(
            self._device_pointer,
            self._cfunc_tx_callback,  # internal callback only, not user-configurable
            None,
        )
    )
```

`start_tx()` takes **zero arguments**. There is no user-facing callback parameter. The TX transfer is driven by the internal `_tx_callback` which feeds `self.buffer` in 1MB chunks.

### _tx_callback — Internal Buffer Feed Mechanism

```python
# pyhackrf2 1.0.3 source: pyhackrf2/__init__.py line 286
def _tx_callback(self, hackrf_transfer) -> int:
    CHUNK_SIZE = 1000000  # 1 MB per USB transfer
    chunk, self.buffer = self.buffer[0:CHUNK_SIZE], self.buffer[CHUNK_SIZE:]
    hackrf_transfer.contents.buffer = (c_byte * len(chunk)).from_buffer(bytearray(chunk))
    hackrf_transfer.contents.valid_length = len(chunk)
    if not len(self.buffer):
        self._transceiver_mode = TransceiverMode.HACKRF_TRANSCEIVER_MODE_OFF
        return 1  # signal libhackrf: stop TX
    return 0
```

**Key behaviors:**
- Feeds `hackrf.buffer` in 1MB USB transfer chunks
- When buffer is exhausted, returns 1 (TX stops automatically)
- `hackrf.buffer` is consumed destructively — each chunk is removed as it transmits
- TX completes when buffer is empty; no looping/repeat without re-assigning buffer

### Buffer Data Format for TX

TX requires raw **interleaved int8 signed bytes** in `hackrf.buffer`:

```python
# To transmit float32 IQ data (format used in hackrf:iq:stream):
# Convert float32 [-1.0, 1.0] back to int8 [-127, 127]
float32_iq = np.frombuffer(redis_bytes, dtype=np.float32)  # [I,Q,I,Q,...] float32
int8_iq = (float32_iq * 127.0).clip(-127, 127).astype(np.int8)
hackrf.buffer = bytearray(int8_iq.tobytes())
```

This is the inverse of what `_rx_callback` does in the existing driver (`/ 128.0` for RX).

### stop_tx()

```python
def stop_tx(self) -> None:
    self._transceiver_mode = TransceiverMode.HACKRF_TRANSCEIVER_MODE_OFF
    self._bias_tee_on = False
    self._check_error(libhackrf.hackrf_stop_tx(self._device_pointer))
```

Safe to call even if TX is not running (returns error code -1004 `HACKRF_ERROR_STREAMING_EXIT_CALLED`, which `_check_error` explicitly ignores — code `-1004` is in the passthrough list).

### TXVGA Gain

```python
# pyhackrf2 1.0.3 source: __init__.py line 466
@txvga_gain.setter
def txvga_gain(self, value: int) -> None:
    value = min(value, 47)
    value = max(value, 0)
    # NOTE: NO quantization step for txvga — every integer 0-47 is valid
    self._check_error(libhackrf.hackrf_set_txvga_gain(self._device_pointer, value))
    self._txvga_gain = value
```

TX VGA gain: 0–47 dB, any integer value (no quantization step, unlike LNA gain which rounds to multiples of 8). Default in pyhackrf2 `__init__`: 10 dB. For Phase 4, default should be **0 dB** (most conservative — requires explicit override to increase power).

---

## Architecture Patterns

### Recommended Project Structure

```
hackrf_ros/
├── hackrf_node.py       # HackRFNode: integrate TXController, register TX commands
├── mayhem_serial.py     # MayhemSerial (existing, no changes)
├── redis_bridge.py      # RedisBridge: add start_tx, stop_tx to _COMMAND_HANDLERS
└── tx_controller.py     # TXController (NEW — this phase)
```

### Pattern 1: TXController Helper Class (MayhemSerial Analog)

**What:** Standalone class with `open()` / `close()` / `stop()` lifecycle. Owns TX state: antenna confirmed flag, frequency filter config, is_transmitting flag. Uses `_device_lock` borrowed from HackRFNode for exclusive hardware access.

**When to use:** Always — established helper class pattern (CONTEXT.md reusable assets).

```python
# hackrf_ros/tx_controller.py
import threading
import time

class TXController:
    """TX authorization gate and pyhackrf2 TX dispatcher.

    Enforces: antenna confirmation, frequency allowlist,
    one-token-per-TX GETDEL auth, half-duplex RX pause/resume.

    Usage::
        tx = TXController(hackrf_ref, node_ref, redis_ref, logger)
        tx.open()          # reads ROS2 params, checks Redis antenna flag
        # on TX command:
        tx.start_tx(freq_hz, auth_token, iq_bytes)
        tx.stop_tx()
        # on shutdown:
        tx.stop()          # stops any active TX; called FIRST in destroy_node
    """

    # Restricted bands: (min_hz, max_hz) — D-04
    RESTRICTED_BANDS = [
        (108_000_000,  137_000_000),   # Aviation VHF nav/comm
        (150_000_000,  174_000_000),   # Emergency/public safety
        (406_000_000,  406_100_000),   # EPIRB distress (NEVER transmit)
        (450_000_000,  470_000_000),   # Emergency/public safety UHF
        (700_000_000,  900_000_000),   # Cellular bands (LTE 700/850/900)
        (960_000_000, 1215_000_000),   # Aviation DME/TACAN
        (1_030_000_000, 1_030_000_000 + 1),  # ATC Mode C (1030 MHz exact)
        (1_090_000_000, 1_090_000_000 + 1),  # ATC Mode S (1090 MHz exact)
        (1_164_000_000, 1_215_000_000),  # GPS L5 / aviation GNSS
        (1_559_000_000, 1_610_000_000),  # GPS L1/L2 + GLONASS
        (1_700_000_000, 2_100_000_000),  # Cellular AWS/PCS
        (2_500_000_000, 2_700_000_000),  # Cellular Band 41
    ]

    AUTH_KEY = 'hackrf:tx:auth'
    ANTENNA_KEY = 'hackrf:tx:antenna_confirmed'
    FREQ_OVERRIDE_KEY = 'hackrf:tx:freq_filter_override'
    IQ_DATA_KEY = 'hackrf:tx:iq_data'

    # Lua script: atomic GETDEL for Redis < 6.2
    # Falls back gracefully when Redis 6.2+ native GETDEL is unavailable
    _LUA_GETDEL = """
local val = redis.call('GET', KEYS[1])
if val then
    redis.call('DEL', KEYS[1])
end
return val
"""
```

### Pattern 2: Authorization Flow

```
Caller sets:
    SET hackrf:tx:auth <uuid> EX 60      # 60s TTL token

Caller sends Redis command:
    XADD hackrf:cmd * cmd '{"action": "start_tx", "freq_hz": 433000000, "auth_token": "<uuid>"}'

TXController.start_tx(freq_hz, auth_token, iq_bytes):
    1. Check _antenna_confirmed — raise TXBlockedError if False
    2. Check freq allowlist — raise TXFreqBlockedError if restricted (D-06: hard reject)
    3. Consume token: result = r.eval(LUA_GETDEL, 1, AUTH_KEY)
       - If result is None: raise TXNotAuthorizedError
       - If result != auth_token (bytes): raise TXTokenMismatchError
    4. Acquire _device_lock (blocks until hardware available)
    5. Call node._stop_rx_if_running()   # half-duplex: RX off
    6. time.sleep(0.1)                   # firmware settle (libhackrf #916)
    7. Set hackrf.center_freq = freq_hz
    8. Set hackrf.txvga_gain = txvga_gain (default 0)
    9. hackrf.buffer = bytearray(iq_bytes)  # load int8 IQ data
    10. hackrf.start_tx()                # non-blocking: USB transfers begin
    11. Set _is_transmitting = True
    12. Log: AUDIT: TX started freq=... token=... len(iq)=...
    13. Release _device_lock
    14. Return (TX runs asynchronously via _tx_callback until buffer exhausted)
```

### Pattern 3: Half-Duplex Shutdown and Reconnect

TX is non-blocking after `start_tx()`. The USB transfer thread feeds buffer chunks until empty, then TX stops automatically (internal `_tx_callback` returns 1). `TXController` must poll `hackrf._transceiver_mode` or call `stop_tx()` + `_start_rx_if_stopped()` to restore RX.

**`stop_tx` flow:**

```python
def stop_tx(self):
    with self._tx_lock:
        if not self._is_transmitting:
            return
        try:
            self._hackrf.stop_tx()
        except RuntimeError:
            pass  # stop_tx raises if not active; ok per _check_error passthrough of -1004
        self._is_transmitting = False
        time.sleep(0.1)  # settle before restarting RX
        self._node._start_rx_if_stopped()  # half-duplex: RX back on
        self._logger.info("TX stopped; RX resumed.")
```

### Pattern 4: Antenna Confirmation Check

```python
# In TXController.open():
# Check 1: ROS2 parameter tx_skip_antenna_check
if node.get_parameter('tx_skip_antenna_check').get_parameter_value().bool_value:
    self._antenna_confirmed = True
    logger.warning("TX antenna check BYPASSED (tx_skip_antenna_check=True). "
                   "ENSURE ANTENNA IS CONNECTED.")
    return

# Check 2: Redis key hackrf:tx:antenna_confirmed
val = redis.get(self.ANTENNA_KEY)
if val and val.lower() in (b'1', b'true', b'yes'):
    self._antenna_confirmed = True
    logger.info("TX antenna confirmation received from Redis.")
else:
    self._antenna_confirmed = False
    logger.warning("TX BLOCKED: antenna not confirmed. "
                   "Set hackrf:tx:antenna_confirmed=1 in Redis or "
                   "set tx_skip_antenna_check=True parameter.")
```

### Pattern 5: Frequency Allowlist Check

```python
def _is_freq_restricted(self, freq_hz: int) -> bool:
    """Returns True if freq_hz falls in any restricted band."""
    for min_hz, max_hz in self.RESTRICTED_BANDS:
        if min_hz <= freq_hz <= max_hz:
            return True
    return False

def _freq_filter_active(self) -> bool:
    """Returns True if frequency filter is active.
    Filter disabled ONLY when BOTH ROS2 param AND Redis key say disabled (D-05).
    """
    ros2_enabled = self._node.get_parameter('tx_freq_filter_enabled').get_parameter_value().bool_value
    if ros2_enabled:
        return True  # ROS2 param says enabled: filter is active regardless of Redis
    # ROS2 param says disabled — now check Redis override
    redis_val = self._redis.get(self.FREQ_OVERRIDE_KEY)
    if redis_val and redis_val.lower() in (b'disabled', b'0', b'false'):
        return False  # Both agree: filter disabled
    return True  # Redis key absent or not "disabled": filter stays active
```

### Anti-Patterns to Avoid

- **Calling `start_tx()` before `stop_rx()`:** pyhackrf2 will raise `HACKRF_ERROR_BUSY (-6)`. RX must be stopped first, with 100ms settle time.
- **Leaving TX running at shutdown:** `stop_tx()` MUST be called in `destroy_node()` BEFORE RedisBridge.close() — D-11 / TX-06.
- **Consuming auth token before frequency check:** Token consumed is a one-time event. Always validate frequency BEFORE calling GETDEL, so a rejected frequency does not consume a valid token.
- **Setting txvga_gain > 0 as default:** Default must be 0 dB. Operators must explicitly set higher gain. Conservative default prevents unintended high-power TX.
- **Assuming TX completion is synchronous:** `start_tx()` returns immediately. TX runs on the USB interrupt thread until buffer is empty. Use `hackrf._transceiver_mode` to poll for completion or call `stop_tx()` explicitly.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Atomic token consume | GET + DEL in separate calls | Lua script or native GETDEL | Race: two callers could both GET before either DEL; token consumed twice |
| TX IQ buffer | Custom USB write loop | `hackrf.buffer` + `start_tx()` | pyhackrf2 already manages USB transfer chunking (1MB per call); hand-rolling duplicates libhackrf |
| Half-duplex switching | Custom RX state machine | `_stop_rx_if_running()` + `_start_rx_if_stopped()` | Already implemented in hackrf_node.py (Phase 3); reuse |
| Frequency band database | Hard-coded single-number checks | List of `(min_hz, max_hz)` tuples | Ranges are ITU/FCC bands; need range semantics, not point comparisons |
| TX completion detection | Polling `hackrf._transceiver_mode` | `stop_tx()` + `sleep(0.1)` | For controlled shutdown: explicit stop is safer than waiting for buffer drain; avoid polling internal attrs |

**Key insight:** pyhackrf2's buffer-based TX model means the IQ payload must be pre-loaded. For a production streaming TX (D-09), the implementation drains `hackrf:tx:iq_data` from Redis, assembles the full bytearray, then calls `start_tx()` once. This is simpler than a streaming callback model.

---

## Redis GETDEL: Upgrade Path and Fallback

**Confidence: HIGH** — Live-tested on running Redis 6.0.16.

### Current State

Redis 6.0.16 is installed from Ubuntu 22.04 (Jammy) apt. This is the maximum version available from apt on this OS. GETDEL requires Redis 6.2+.

```
$ redis-cli GETDEL hackrf:test:key
ERR unknown command `GETDEL`, with args beginning with: `hackrf:test:key`
```

### Option 1: Lua GETDEL Fallback (Confirmed Working)

Live-tested on Redis 6.0.16:

```python
_LUA_GETDEL = """
local val = redis.call('GET', KEYS[1])
if val then
    redis.call('DEL', KEYS[1])
end
return val
"""

def _consume_auth_token(self, redis_client, expected_token: str) -> bool:
    """Atomically consume auth token. Returns True if token matches."""
    try:
        # Try native GETDEL first (Redis 6.2+)
        result = redis_client.getdel(self.AUTH_KEY)
    except redis.exceptions.ResponseError:
        # Fallback: Lua atomic GET+DEL (Redis < 6.2)
        result = redis_client.eval(self._LUA_GETDEL, 1, self.AUTH_KEY)
    if result is None:
        return False
    return result == expected_token.encode()
```

Confidence: HIGH — `r.eval(lua_getdel, 1, key)` confirmed atomically returning value and deleting key in live test.

### Option 2: Snap Redis Upgrade (Non-Destructive)

Snap Redis 8.6.2 is available:

```bash
# Install snap Redis (does not affect apt redis-server)
sudo snap install redis
# Snap Redis runs on :6379 by default — conflicts with apt Redis
sudo systemctl stop redis-server
sudo systemctl disable redis-server
sudo snap start redis
# Verify
redis-cli --version  # may point to snap version
redis-cli GETDEL test:key  # should work
```

**Risk:** Snap Redis uses a different socket path and data directory. Existing data in apt Redis will not migrate automatically. For a dev driver where Redis is ephemeral (IQ stream, state, commands — no persistent data), this is acceptable.

**Recommendation for Plan:** Implement Lua fallback as the primary code path (no infrastructure prerequisite). Document snap upgrade as Wave 0 optional step with instructions. The Lua path makes Phase 4 deployable immediately on the existing Redis 6.0.16.

---

## Restricted Frequency Bands (D-04 Band Boundaries)

**Confidence: MEDIUM** — ITU Radio Regulations and FCC Part 15/Part 97 allocations. Exact boundaries vary by jurisdiction; listed values are conservative US-centric defaults.

| Band | Range | Why Blocked |
|------|-------|-------------|
| Aviation VHF nav/comm | 108–137 MHz | ILS, VOR, AM air-ground voice; safety-critical |
| Emergency/public safety | 150–174 MHz | NOAA weather, P25 public safety |
| EPIRB emergency | 406.0–406.1 MHz | International distress beacon; NEVER transmit |
| Public safety UHF | 450–470 MHz | P25 police/fire/EMS |
| Cellular 700/850/900 | 700–900 MHz | LTE bands 12/13/17/5/8; FCC licensed carriers |
| Aviation DME/TACAN | 960–1215 MHz | Distance Measuring Equipment (aircraft navigation) |
| ATC Mode C | 1030 MHz | Secondary surveillance radar (uplink) |
| ATC Mode S | 1090 MHz | ADS-B out + Mode S transponder; interfering is felony-level |
| GPS L5 / GNSS | 1164–1215 MHz | GPS/GNSS navigation; jamming is federal crime |
| GPS L1 + GLONASS | 1559–1610 MHz | GPS L1 1575.42 MHz; GLONASS 1602 MHz |
| Cellular AWS/PCS | 1700–2100 MHz | LTE bands 4/1/2/25; AWS-1 and PCS |
| Cellular Band 41 | 2500–2700 MHz | LTE/5G TDD band 41 |

**Implementation:**
```python
RESTRICTED_BANDS: list[tuple[int, int]] = [
    (108_000_000,   137_000_000),
    (150_000_000,   174_000_000),
    (406_000_000,   406_100_000),   # EPIRB — always blocked regardless of override
    (450_000_000,   470_000_000),
    (700_000_000,   900_000_000),
    (960_000_000,  1_215_000_000),
    (1_030_000_000, 1_031_000_000),
    (1_090_000_000, 1_091_000_000),
    (1_164_000_000, 1_215_000_000),
    (1_559_000_000, 1_610_000_000),
    (1_700_000_000, 2_100_000_000),
    (2_500_000_000, 2_700_000_000),
]

# EPIRB band is ALWAYS blocked — not subject to freq_filter_override
ALWAYS_BLOCKED: list[tuple[int, int]] = [
    (406_000_000, 406_100_000),
    (1_090_000_000, 1_091_000_000),   # ADS-B out
]
```

**Safe ISM test bands (allowed by default):**
- 433.05–434.79 MHz (ISM, Region 1 — available, low power)
- 902–928 MHz (ISM, US)
- 2400–2483.5 MHz (ISM, worldwide)
- 5725–5875 MHz (ISM, worldwide)

---

## Common Pitfalls

### Pitfall 1: TX While RX Active — HACKRF_ERROR_BUSY
**What goes wrong:** Calling `hackrf.start_tx()` while `hackrf.start_rx()` is active raises `RuntimeError("Device busy (HACKRF_ERROR_BUSY)")`. pyhackrf2's `_check_error` will trigger `close()` and raise. The device closes unexpectedly.
**Why it happens:** HackRF is hardware half-duplex. libhackrf enforces this at the C level — `hackrf_start_tx` returns -6 (BUSY) if any transfer is active.
**How to avoid:** `stop_rx()` + 100ms sleep BEFORE `start_tx()`. The existing `_stop_rx_if_running()` method handles this, but the sleep must be explicit.
**Warning signs:** `RuntimeError: Device busy (HACKRF_ERROR_BUSY)` on `start_tx()`.

### Pitfall 2: Auth Token Consumed Before Frequency Check
**What goes wrong:** If GETDEL is called before `_is_freq_restricted()` check, a valid token is consumed for a TX that is then rejected on frequency grounds. The caller must generate a new token and retry.
**Why it happens:** Wrong order of validation steps.
**How to avoid:** Strict validation order: (1) antenna confirmed, (2) freq not restricted, (3) consume token, (4) acquire lock, (5) call stop_rx + start_tx. GETDEL is step 3, not step 1.
**Warning signs:** Caller receives "TX rejected: restricted frequency" after already consuming their token. Audit log shows token consumed with no TX.

### Pitfall 3: hackrf.buffer Is a bytearray — Not Thread-Safe Assignment
**What goes wrong:** `hackrf.buffer` is a class-level `bytearray()` attribute (not instance-level in Python class definition sense). The `_tx_callback` reads and destructively modifies it in the libusb thread. If another thread reassigns `hackrf.buffer` mid-transmission, the pointer in the running transfer becomes invalid.
**Why it happens:** Python class-level attribute definition; libusb thread modifies buffer via ctypes while Python thread reassigns.
**How to avoid:** Only assign `hackrf.buffer` while holding `_device_lock` AND when TX is not active. Never modify buffer after `start_tx()` is called. For repeat TX: call `stop_tx()` first, then reassign buffer, then `start_tx()` again.
**Warning signs:** Segfault in libhackrf (rare in Python but possible via ctypes); garbage data in transmission.

### Pitfall 4: pyhackrf2 Default txvga_gain Is 10 dB
**What goes wrong:** The pyhackrf2 `__init__` sets `txvga_gain = 10` by default. If TXController does not explicitly set `txvga_gain = 0` before TX, the device transmits at 10 dB without operator awareness.
**Why it happens:** pyhackrf2 default; undocumented assumption.
**How to avoid:** TXController always sets `hackrf.txvga_gain = 0` (or the ROS2-parameterized value) before every TX. Log the actual gain value used in the audit log.
**Warning signs:** TX at unexpected power level despite operator not specifying gain.

### Pitfall 5: stop_tx() Is Safe to Call When TX Not Active
**What goes wrong:** False alarm — `stop_tx()` does NOT raise if TX is not running. pyhackrf2 `_check_error` passes through error code -1004 (`HACKRF_ERROR_STREAMING_EXIT_CALLED`) silently. Calling `stop_tx()` in `destroy_node()` when TX was never started is safe.
**Why it happens:** This is actually correct behavior, documented here to prevent defensive code that would be unnecessary.
**How to avoid:** Call `stop_tx()` unconditionally in `destroy_node()` — no need to guard with `is_transmitting` check (though tracking the flag is still useful for logging).

### Pitfall 6: TX IQ Data Must Be int8, Not float32
**What goes wrong:** `hackrf.buffer` expects raw signed int8 bytes (as stored in a bytearray). If float32 data from `hackrf:tx:iq_data` is assigned directly without conversion, transmitted signal is garbage.
**Why it happens:** The Redis IQ stream (`hackrf:iq:stream`) uses float32 encoding per D-03. TX path requires int8. The conversion direction is opposite to the RX path.
**How to avoid:** Explicit conversion in TXController:
```python
float32_arr = np.frombuffer(iq_bytes, dtype=np.float32)
int8_arr = (float32_arr * 127.0).clip(-127, 127).astype(np.int8)
hackrf.buffer = bytearray(int8_arr.tobytes())
```
**Warning signs:** TX LED active but spectrum analyzer shows noise floor only (wrong data format).

### Pitfall 7: TX Token from Redis Is bytes, Not str
**What goes wrong:** `redis.get()` with `decode_responses=False` (project standard) returns `bytes`. The auth_token field in the Redis command JSON is decoded as a Python `str`. Direct comparison `token_bytes == auth_token_str` always fails.
**Why it happens:** RedisBridge uses `decode_responses=False` for binary IQ safety.
**How to avoid:** Encode the expected token before comparison:
```python
result == expected_token.encode('utf-8')
```
Or decode the Redis result:
```python
result.decode('utf-8') == expected_token
```
**Warning signs:** All TX auth attempts fail with "token mismatch" even with correct token.

---

## Code Examples

### Complete start_tx Sequence (Source-Verified)

```python
# Source: pyhackrf2 1.0.3 __init__.py (direct inspection) + live Redis 6.0.16 test

# Step 1: Prepare IQ data (float32 from Redis → int8 for buffer)
iq_redis_bytes = r.get('hackrf:tx:iq_data')  # float32 bytes
float32_arr = np.frombuffer(iq_redis_bytes, dtype=np.float32)
int8_arr = (float32_arr * 127.0).clip(-127, 127).astype(np.int8)

# Step 2: Acquire lock, stop RX, settle, configure, transmit
with device_lock:
    hackrf.stop_rx()          # must stop RX first
    time.sleep(0.1)           # 100ms firmware settle (libhackrf issue #916)
    hackrf.center_freq = freq_hz
    hackrf.txvga_gain = 0     # conservative default
    hackrf.buffer = bytearray(int8_arr.tobytes())
    hackrf.start_tx()         # non-blocking; TX runs until buffer empty
```

### Atomic Token Consumption (Lua GETDEL Fallback)

```python
# Source: live test on Redis 6.0.16 — confirmed atomic GET+DELETE behavior
_LUA_GETDEL = """
local val = redis.call('GET', KEYS[1])
if val then
    redis.call('DEL', KEYS[1])
end
return val
"""

def _consume_token(self, expected_token: str) -> bool:
    try:
        result = self._redis.getdel('hackrf:tx:auth')  # Redis 6.2+ native
    except redis.exceptions.ResponseError:
        result = self._redis.eval(_LUA_GETDEL, 1, 'hackrf:tx:auth')  # fallback
    if result is None:
        return False
    return result == expected_token.encode('utf-8')
```

### Dual-Disable Frequency Filter Check

```python
# D-05: filter disabled ONLY when both ROS2 param AND Redis key agree
def _freq_filter_active(self) -> bool:
    ros2_enabled = self._node.get_parameter(
        'tx_freq_filter_enabled').get_parameter_value().bool_value
    if ros2_enabled:
        return True  # ROS2 says on → filter active regardless of Redis
    val = self._redis.get('hackrf:tx:freq_filter_override')
    return not (val and val.lower() in (b'disabled', b'0', b'false'))
```

### destroy_node TX Stop Order

```python
# In HackRFNode.destroy_node() — TX stopped FIRST, before all other cleanup
def destroy_node(self) -> None:
    self.get_logger().info("HackRFNode shutting down...")
    # FIRST: stop TX (before Redis or device cleanup) — D-11 / TX-06
    if hasattr(self, '_tx_controller'):
        self._tx_controller.stop()
        self.get_logger().info("TXController stopped.")
    # SECOND: close RedisBridge
    if hasattr(self, '_redis_bridge'):
        self._redis_bridge.close()
    # ... rest of existing shutdown sequence
```

### Redis Command Format for TX (D-12)

```python
# Producer (external caller) sets auth token:
r.set('hackrf:tx:auth', 'my-uuid-here', ex=60)

# Producer sends TX command to hackrf:cmd stream:
r.xadd('hackrf:cmd', {
    b'cmd': json.dumps({
        'action': 'start_tx',
        'freq_hz': 433_920_000,
        'auth_token': 'my-uuid-here',
        'txvga_gain': 0,          # optional; default 0
    }).encode()
})

# _COMMAND_HANDLERS entry (in redis_bridge.py):
_COMMAND_HANDLERS = {
    ...existing entries...,
    'start_tx': lambda node, p: node._tx_controller.start_tx(
        p['freq_hz'], p['auth_token'],
        node._redis.get('hackrf:tx:iq_data'),
        txvga_gain=p.get('txvga_gain', 0)
    ),
    'stop_tx': lambda node, p: node._tx_controller.stop_tx(),
}
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| pyhackrf (dressel) streaming callback TX | pyhackrf2 buffer-only TX | 2021 (eizemazal fork) | No user TX callback; must pre-load full buffer |
| Redis GET + DEL (two commands, not atomic) | Redis GETDEL (atomic, Redis 6.2+) | Redis 6.2 (2021) | Eliminates race where two callers both see valid token |
| Redis 6.0.16 (Ubuntu 22.04 apt) | Redis 6.2+ or 7.x (snap) | Available now | Enables native GETDEL; Lua fallback bridges the gap |
| Manual antenna check in log message | Per-session Redis flag + ROS2 param | Phase 4 | Enforces confirmation programmatically, not just advisory |

**Deprecated/outdated:**
- `pyhackrf` (dressel/pyhackrf): Old library; `pyhackrf2` (eizemazal) is the current active project used in this Docker image.
- TX authorization as a persistent session: CONTEXT.md anti-feature — per-command auth only.

---

## Open Questions

1. **IQ data size limit for TX buffer**
   - What we know: `hackrf.buffer` is a `bytearray`. At 2 MSPS, 1 second of TX = 4MB of int8 data. `_tx_callback` feeds in 1MB USB chunks.
   - What's unclear: Is there a practical max buffer size before USB transfer reliability degrades?
   - Recommendation: Cap `hackrf:tx:iq_data` read at 32MB (8 seconds at 2 MSPS) as a reasonable default. Add a ROS2 parameter `tx_max_iq_bytes` for configuration. Log a warning if the payload exceeds the cap.

2. **TX completion notification**
   - What we know: `start_tx()` is non-blocking. TX completes when `_tx_callback` exhausts `hackrf.buffer` and returns 1. `hackrf._transceiver_mode` transitions to OFF.
   - What's unclear: Should TXController poll `hackrf._transceiver_mode` and call `_start_rx_if_stopped()` when TX auto-completes? Or should callers always send explicit `stop_tx`?
   - Recommendation: For Phase 4, require explicit `stop_tx` command. A future enhancement could add a watchdog thread that monitors `hackrf._transceiver_mode` and auto-resumes RX. Simpler to require explicit stop for now.

3. **Redis connection in TXController vs reuse from RedisBridge**
   - What we know: RedisBridge owns the Redis connection. TXController also needs Redis access (GETDEL, antenna flag, IQ data fetch, freq override).
   - What's unclear: Should TXController share the RedisBridge connection or open its own?
   - Recommendation: Pass the Redis client reference from HackRFNode to TXController. The `redis.Redis` object backed by ConnectionPool is thread-safe per redis-py docs. One shared client is fine. Do not open a separate connection.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| pyhackrf2 | TX hardware control | In Docker only | 1.0.3 | — (must be in Docker) |
| Redis server | Auth token, antenna flag, IQ data | Yes (apt) | 6.0.16 | — |
| GETDEL command | Auth token consumption (D-01) | No (needs 6.2+) | — | Lua atomic GET+DEL (confirmed working) |
| snap redis 8.6.2 | Redis upgrade path | Available | 8.6.2 | Lua fallback if not upgraded |
| redis-py 7.4.0 | Python Redis client | Yes | 7.4.0 | — |
| numpy | IQ format conversion (float32 ↔ int8) | Yes | installed | — |

**Missing dependencies with no fallback:**
- pyhackrf2 (only available in Docker container — but this is expected; the driver runs in Docker).

**Missing dependencies with fallback:**
- GETDEL (Redis 6.2+): Lua atomic GET+DEL works on Redis 6.0.16. Plan implements Lua fallback as default; snap Redis upgrade documented as optional Wave 0 step.

---

## Project Constraints (from CLAUDE.md)

| Directive | Source | Applies To Phase 4 |
|-----------|--------|--------------------|
| File names: `lowercase_with_underscores.py` | CONVENTIONS.md | `tx_controller.py` (correct) |
| Class names: PascalCase | CONVENTIONS.md | `TXController` (correct) |
| Private methods: single underscore prefix | CONVENTIONS.md | `_consume_token`, `_is_freq_restricted`, `_freq_filter_active` |
| Logging via `self._logger.info/warning/error()` — no bare `print()` | CONVENTIONS.md | All TXController log calls |
| Error handling: broad `except Exception as e:` with logger | CONVENTIONS.md | Command dispatch errors in `_COMMAND_HANDLERS` |
| Methods organized: `__init__`, config, lifecycle, callbacks, utilities | CONVENTIONS.md | TXController class method order |
| `destroy_node()` cleanup in HackRFNode | CONVENTIONS.md | `tx_controller.stop()` FIRST, before RedisBridge, serial, pyhackrf2 |
| ROS2 parameters declared in `__init__` with descriptors | CONVENTIONS.md | `tx_freq_filter_enabled`, `tx_skip_antenna_check`, `txvga_gain` |
| GSD workflow enforcement | CLAUDE.md | All file edits via `/gsd:execute-phase` |
| TX safety: gated behind explicit authorization | CONSTRAINTS | Enforced by TXController; no TX path bypasses auth/freq/antenna checks |
| Docker deployment target | CONSTRAINTS | pyhackrf2 available in Docker; Redis on host via `localhost` / host network mode |

---

## Sources

### Primary (HIGH confidence)
- pyhackrf2 1.0.3 wheel (downloaded from PyPI, extracted and inspected directly) — `__init__.py` and `cinterface.py` source code
- Live Redis 6.0.16 tests: GETDEL confirmed absent; Lua GETDEL fallback confirmed working; pipeline GET+DEL confirmed
- `.planning/phases/04-tx-authorization/04-CONTEXT.md` — locked decisions D-01 through D-12
- `hackrf_ros/hackrf_node.py` (direct read) — existing `_stop_rx_if_running`, `_start_rx_if_stopped`, `_device_lock`, `destroy_node` patterns
- `hackrf_ros/redis_bridge.py` (direct read) — `_COMMAND_HANDLERS` dispatch table to extend
- `hackrf_ros/mayhem_serial.py` (direct read) — helper class pattern to replicate

### Secondary (MEDIUM confidence)
- `.planning/research/PITFALLS.md` — TX without antenna hardware damage (Pitfall 3), restricted frequencies (Pitfall 5), gain quantization (Pitfall 12)
- `.planning/research/ARCHITECTURE.md` — TXController design, thread model, module layout
- `.planning/research/STACK.md` — GETDEL pattern, Lua alternative, TX auth token design
- `.planning/phases/03-redis-bridge/03-RESEARCH.md` — Redis 6.0.16 GETDEL constraint (Pitfall 2)
- ITU Radio Regulations / FCC frequency allocation charts — restricted band boundaries

### Tertiary (LOW confidence)
- `apt-cache madison redis-server` output — confirms Redis 6.0.16 is max apt version on Ubuntu 22.04
- `snap info redis` output — confirms snap Redis 8.6.2 available for upgrade

---

## Metadata

**Confidence breakdown:**
- pyhackrf2 TX API: HIGH — directly inspected source from PyPI wheel; no inference
- Redis GETDEL / Lua fallback: HIGH — live-tested on running Redis 6.0.16
- Architecture (TXController class): HIGH — follows established helper class pattern verified in existing codebase
- Frequency band boundaries: MEDIUM — ITU/FCC-based; exact dB/MHz boundaries may vary by jurisdiction; conservative defaults used
- Half-duplex switching: HIGH — existing `_stop_rx_if_running` / `_start_rx_if_stopped` already implemented and tested in Phase 3

**Research date:** 2026-03-29
**Valid until:** 2026-04-29 (pyhackrf2 API is stable; Redis upgrade path is stable)
