# Technology Stack

**Project:** HackRF ROS2 Driver — Milestone: v2.0 Hardening, Observability & Signal Capabilities
**Researched:** 2026-03-30
**Scope:** New additions only for v2.0. Does not re-cover the validated v1.x stack (redis-py, hiredis, pyserial, pyhackrf2, numpy, rclpy).
**Confidence:** HIGH (PyPI verified), MEDIUM (design patterns from stdlib/community)

---

## Previously Validated Stack (Do Not Re-Research)

| Package | Version | Role |
|---------|---------|------|
| redis-py | >=7.4.0 | IQ streaming, state hash, command dispatch |
| hiredis | >=3.3.1 | C-accelerated redis-py parser |
| pyserial | >=3.5 | Mayhem serial CDC-ACM |
| pyhackrf2 | latest | IQ USB bulk transfer via libhackrf |
| numpy | pinned by scipy below | IQ conversion, signal math |
| rclpy / std_msgs / std_srvs | ROS2 Humble | BridgeNode messaging |
| pytest | >=7.x | 135-test suite |

---

## New Dependencies for v2.0

### IQ Recording — SigMF

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| sigmf | `>=1.7.2` | Write SigMF-format IQ recordings (.sigmf-data + .sigmf-meta) | The canonical Python SigMF implementation from the spec authors. v1.7.2 released 2026-03-20 — current stable. Depends only on numpy (already present) and jsonschema. LGPL v3 license, Python 3.7–3.14 compatible. No other Python library writes standards-compliant SigMF without custom JSON construction. |
| jsonschema | `>=4.0` | Transitive dep of sigmf — SigMF metadata validation | Pulled in automatically by sigmf. No explicit pin needed unless jsonschema conflicts arise with other ROS2 tools. |

**Confidence: HIGH** — Version verified from PyPI (pypi.org/project/SigMF/) and GitHub releases (sigmf/sigmf-python). Dependencies confirmed from pyproject.toml inspection.

**Why NOT raw numpy.tofile():** Writing raw .cf32 or .cs8 files loses all metadata (center frequency, sample rate, hardware info, capture time). SigMF pairs the binary data file with a JSON metadata file, enabling any SigMF-aware tool (GNU Radio, SigMF Inspector, IQEngine) to open recordings without a README.

**Why NOT GNURadio sigmf blocks:** GNURadio is not in the stack and has a multi-hundred-MB install footprint. The standalone `sigmf` pip package is sufficient.

**SigMF API surface used:**
```python
import sigmf
from sigmf import SigMFFile

meta = SigMFFile(
    global_info={
        SigMFFile.SAMPLE_RATE_KEY: 10_000_000,     # 10 MSPS
        SigMFFile.HW_KEY: "HackRF One + PortaPack",
        SigMFFile.DATATYPE_KEY: "cf32_le",          # complex float32 little-endian
    },
    data_file="recording.sigmf-data"
)
meta.add_capture(0, metadata={SigMFFile.FREQUENCY_KEY: 433_920_000})
meta.tofile("recording.sigmf-meta")
# IQ samples written separately: np.array(iq, dtype=np.complex64).tofile("recording.sigmf-data")
```

---

### Spectral Analysis — FFT / Waterfall

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| scipy | `>=1.11,<2.0` | `scipy.signal.welch`, `scipy.signal.spectrogram`, windowing functions | Provides higher-quality FFT routines than numpy.fft alone (Welch PSD, configurable windowing, spectrogram stacking). numpy.fft works for single-shot FFT but scipy.signal.spectrogram gives the time-frequency 2D array needed for waterfall data. v1.17.1 is current (Feb 2026) but requires Python >=3.11 — see compatibility note. |

**Compatibility note:** scipy 1.17.x requires Python >=3.11 and numpy >=1.26.4. ROS2 Humble ships Python 3.10. This is a constraint.

**Resolution:** Use `scipy>=1.11,<1.16` (1.15.x is the last series supporting Python 3.10) OR use `numpy.fft` directly with manual windowing. Given that the Docker container can control its Python environment independently of the ROS2 system Python, and hackrf_driver runs without rclpy, installing scipy 1.15.x in the driver venv is safe.

**Fallback (no scipy):** numpy.fft is stdlib-compatible and sufficient for single-shot PSD:
```python
# numpy-only waterfall bin
fft_out = np.fft.fftshift(np.fft.fft(samples * np.hanning(len(samples))))
psd_db = 20 * np.log10(np.abs(fft_out) + 1e-12)
```
For the initial implementation, use numpy.fft with a Hann window. Add scipy only if Welch averaging or scipy.signal.spectrogram is specifically required.

**Recommendation:** Start with numpy.fft + manual windowing in hackrf_driver. Add `scipy>=1.11,<1.16` as an optional extra (`pip install hackrf_driver[spectral]`) to avoid forcing a numpy version pin on consumers.

**Confidence: HIGH** — scipy version matrix confirmed from scipy.org/news and pypi.org/project/scipy/.

**Why NOT matplotlib for headless spectrum:** matplotlib is for display — it has no business in hackrf_driver which is a headless data producer. Spectrum data goes to Redis as a JSON-encoded array; visualization is a consumer concern.

---

### pymayhem Async API — asyncio Serial

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| pyserial-asyncio-fast | `>=0.16` | asyncio Transport/Protocol/StreamReader over pyserial | Drop-in asyncio layer for pyserial. v0.16 released 2025-03-27, requires Python >=3.9 (satisfied). Implements "eager writes" that reduce overhead vs the original pyserial-asyncio. Maintained by Home Assistant core team — high confidence in ongoing support. Provides `open_serial_connection()` returning asyncio.StreamReader/StreamWriter — the exact interface needed for an async pymayhem API. |

**Confidence: HIGH** — Version verified from PyPI (pypi.org/project/pyserial-asyncio-fast/).

**Why pyserial-asyncio-fast over pyserial-asyncio:** pyserial-asyncio (v0.6, last release 2022) is unmaintained. pyserial-asyncio-fast is the actively maintained fork used in Home Assistant (a large production codebase). Eager-write optimization matters for low-latency command dispatch.

**Why NOT aioserial:** aioserial (PyPI) wraps pyserial with threading under the hood rather than native asyncio Transport. pyserial-asyncio-fast uses a proper asyncio selector-based Transport.

**Why NOT trio or anyio:** pymayhem has no dependency on a specific async framework. asyncio is stdlib, zero new deps for consumers who use asyncio. Trio/anyio would add mandatory transitive deps.

**Integration pattern for pymayhem:**
```python
# pymayhem/mayhem_async.py
import asyncio
import serial_asyncio_fast  # package name differs from install name

class AsyncMayhemSerial:
    async def connect(self, port: str, baudrate: int = 115200):
        self._reader, self._writer = await serial_asyncio_fast.open_serial_connection(
            url=port, baudrate=baudrate
        )

    async def send_command(self, cmd: str) -> str:
        self._writer.write((cmd + "\r\n").encode())
        line = await asyncio.wait_for(self._reader.readline(), timeout=2.0)
        return line.decode().strip()
```

**pymayhem setup.cfg addition:**
```ini
[options.extras_require]
async =
    pyserial-asyncio-fast>=0.16
```

Keeping async as an optional extra means the base pymayhem package stays pyserial-only (no asyncio transport dep for users who only need synchronous operation).

---

### Observability Metrics

**Decision: No new package needed.**

Publish metrics directly to Redis using existing redis-py. A dedicated metrics library (prometheus_client, statsd, etc.) would require a separate scrape endpoint, Prometheus infrastructure, or a statsd server — none of which are in the deployment target.

**Pattern:** Write counters/gauges to a Redis hash on a 1-second timer:
```
HSET hackrf:metrics iq_frames_total 12345 tx_count 3 rx_errors 0 watchdog_restarts 1
```

External consumers (Grafana via redis-datasource, custom dashboards) can read this hash. A `hackrf:metrics` keyspace notification can trigger alerting.

**Why NOT prometheus_client:** Adds a background HTTP server thread, scrape port, and requires a Prometheus instance in the deployment. Overkill for a single-device driver.

**Why NOT statsd:** Requires a statsd server. Same problem.

**Confidence: HIGH** — Redis HSET is already used for `hackrf:state`; metrics follow the same pattern at no additional dependency cost.

---

### Error Handling Hardening — Custom Exceptions

**Decision: No new package needed.**

Custom exception hierarchies are pure Python stdlib. A well-structured exception module is sufficient:

```python
# hackrf_driver/exceptions.py
class HackRFError(Exception):          # base
    pass

class HackRFDeviceError(HackRFError):  # hardware-level
    pass

class HackRFConfigError(HackRFError):  # parameter validation
    pass

class HackRFTXAuthError(HackRFError):  # TX authorization denied
    pass

class HackRFWatchdogError(HackRFError): # watchdog timeout
    pass
```

**For pymayhem:**
```python
# pymayhem/exceptions.py
class MayhemError(Exception):
    pass

class MayhemTimeoutError(MayhemError):
    pass

class MayhemCommandError(MayhemError):
    pass
```

**Why NOT pydantic for input validation:** pydantic (v2, ~1.5 MB wheel) is well-suited for API boundaries but is heavy for a hardware driver that validates 6 numeric parameters. Use Python's built-in type checking + `ValueError` raises with `@dataclass` for structured config. This keeps pymayhem's dependency list clean (pyserial only).

**Why NOT cerberus / voluptuous:** Same argument — external validation libraries for internal numeric range checks add unnecessary weight.

---

### Device Watchdog — IQ Sequence Numbers

**Decision: No new package needed.**

The watchdog is a daemon threading.Thread that checks a `last_rx_timestamp` float, restarting the USB stream if it goes stale beyond a configurable timeout. IQ sequence numbers are integer counters incremented in the RX callback and published in the Redis stream entry alongside the IQ samples.

```python
# Sequence number in XADD payload
r.xadd("hackrf:iq:stream", {"seq": str(self._seq), "samples": iq_bytes})
self._seq += 1  # gaps detectable by consumers
```

No external package needed. `threading.Thread`, `time.monotonic()`, and `threading.Event` from stdlib handle the watchdog loop.

---

### Dead-Letter Queue and TX Dry-Run

**Decision: No new package needed.**

Dead-letter queue uses existing redis-py: failed command entries are XADD'd to `hackrf:cmd:dlq` with error context. TX dry-run validation is a pure Python pre-flight function that validates parameters against the allowlist before touching hardware.

---

### ROS2 Spectrum Topic

| Decision | Rationale |
|----------|-----------|
| Use `std_msgs/Float32MultiArray` for `/hackrf/spectrum` initially | No standard `sensor_msgs/Spectrum` type exists in ROS2 Humble. Creating a custom message package adds build-system overhead. Float32MultiArray with layout metadata (center_freq, bin_width, nfft in the header) is consistent with the existing `/hackrf/iq` pattern and can be promoted to a custom message in a future milestone without breaking the Redis-side pipeline. |

**Note:** The ROS2 docs and community recommend custom message types for semantic clarity, but the tradeoff at this stage — avoiding a new `hackrf_msgs` package with CMakeLists, package.xml, build infrastructure — favors reusing Float32MultiArray. Revisit if downstream consumers need strongly typed spectrum messages.

---

## Full v2.0 Dependency Delta

### hackrf_driver/setup.cfg additions

```ini
[options]
install_requires =
    pymayhem
    redis>=7.4.0
    hiredis>=3.3.1
    PyYAML>=5.4
    numpy
    sigmf>=1.7.2          # NEW: IQ recording

[options.extras_require]
hardware =
    pyhackrf2
spectral =
    scipy>=1.11,<1.16     # NEW: Welch PSD / spectrogram (Python 3.10 compatible)
test =
    pytest
```

### pymayhem/setup.cfg additions

```ini
[options.extras_require]
async =
    pyserial-asyncio-fast>=0.16   # NEW: asyncio serial transport
test =
    pytest
```

### hackrf_ros/setup.py (no changes needed)

BridgeNode has no new hardware dependencies — it reads from Redis only.

---

## Installation

```bash
# Core driver with IQ recording
pip install "sigmf>=1.7.2"

# Optional: spectral analysis (Welch PSD, spectrogram)
pip install "scipy>=1.11,<1.16"

# Optional: async pymayhem
pip install "pyserial-asyncio-fast>=0.16"
```

```dockerfile
# In Dockerfile, add to existing pip install block:
RUN pip3 install "sigmf>=1.7.2" "pyserial-asyncio-fast>=0.16"
# scipy is optional — add only if spectral features are enabled
# RUN pip3 install "scipy>=1.11,<1.16"
```

---

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| IQ file format | sigmf 1.7.2 | Raw numpy .tofile() | Loses all metadata; no interoperability with SDR tools |
| IQ file format | sigmf 1.7.2 | CDIF/BLUE | Niche format; no Python first-class support |
| FFT/spectral | numpy.fft (base), scipy optional | matplotlib.mlab.psd | Requires display backend; matplotlib is for visualization not headless computation |
| FFT/spectral | numpy.fft (base), scipy optional | pyfftw | FFTW C binding adds compile-time dependency; numpy.fft sufficient for 10 MSPS |
| Async serial | pyserial-asyncio-fast 0.16 | pyserial-asyncio 0.6 | Unmaintained since 2022; no eager-write optimization |
| Async serial | pyserial-asyncio-fast 0.16 | aioserial | Uses threading under asyncio hood, not native Transport |
| Metrics | Redis HSET pattern | prometheus_client | Requires Prometheus infrastructure; no scrape target in deployment |
| Input validation | stdlib ValueError + dataclass | pydantic v2 | 1.5 MB wheel overkill for 6 numeric hardware parameters |
| Custom exceptions | stdlib Exception hierarchy | tenacity (retry) | Retry logic is device-specific; tenacity adds dep for what is 10 lines of code |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| scipy >=1.16 | Requires Python >=3.11; ROS2 Humble uses Python 3.10 | scipy >=1.11,<1.16 or numpy.fft |
| pyserial-asyncio (original) | Unmaintained since 2022; slower writes | pyserial-asyncio-fast |
| aioredis | Merged into redis-py v4.2; abandoned standalone | redis-py 7.x async client (already in stack) |
| pyfftw | FFTW C-library build dep; no ARM64 wheel guarantee | numpy.fft (vectorized, sufficient throughput) |
| GNURadio Python bindings | 500+ MB install; not in Docker target | Direct pyhackrf2 + numpy.fft |
| prometheus_client | Requires a separate Prometheus server to be useful | Redis HSET for metrics (hackrf:metrics hash) |

---

## Version Compatibility

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| sigmf 1.7.2 | numpy any, Python 3.7–3.14 | No version conflict with existing numpy pin |
| scipy 1.15.x | numpy >=1.23, Python 3.10–3.13 | Last series supporting Python 3.10 (Humble) |
| pyserial-asyncio-fast 0.16 | pyserial >=3.5, Python >=3.9 | Compatible with existing pyserial 3.5 pin |
| sigmf 1.7.2 | jsonschema >=4.0 | jsonschema not currently in stack; no known conflicts |

---

## Sources

- SigMF PyPI: https://pypi.org/project/SigMF/ (v1.7.2, verified 2026-03-30)
- sigmf-python GitHub: https://github.com/sigmf/sigmf-python (pyproject.toml deps: numpy, jsonschema)
- pyserial-asyncio-fast PyPI: https://pypi.org/project/pyserial-asyncio-fast/ (v0.16, 2025-03-27)
- pyserial-asyncio-fast GitHub: https://github.com/home-assistant-libs/pyserial-asyncio-fast
- scipy PyPI: https://pypi.org/project/scipy/ (v1.17.1 current; v1.15.x last Python 3.10 series)
- scipy release notes: https://docs.scipy.org/doc/scipy/release.html
- numpy PyPI: https://pypi.org/project/numpy/ (v2.4.4, 2026-03-29)
- PySDR IQ Files guide: https://pysdr.org/content/iq_files.html (SigMF format overview)
- ROS2 Humble std_msgs: https://docs.ros.org/en/ros2_packages/humble/api/std_msgs/ (Float32MultiArray)

---

*Stack research for: HackRF ROS2 Driver v2.0 new signal capabilities, hardening, observability*
*Researched: 2026-03-30*
