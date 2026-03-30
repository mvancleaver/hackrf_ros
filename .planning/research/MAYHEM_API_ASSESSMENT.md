# Mayhem Serial API Assessment

**Tested:** 2026-03-30
**Firmware:** v2.0.1 (ChibiOS/RT 2.6.8, GCC 9.2.1, ARMv6-M Cortex-M0)
**Device:** PortaPack on HackRF One R1-R8

## Command Inventory

### Fully Working (safe, no USB disruption)

| Command | Args | Response | Notes |
|---------|------|----------|-------|
| `help` | none | Full command list | 47 commands available |
| `info` | none | Kernel, compiler, board, Mayhem version | Version detection |
| `sysinfo` | none | Heap, stack, CPU%, uptime | Resource monitoring |
| `radioinfo` | none | RX/TX freq, bandwidth, sample rate, modulation | Radio state query |
| `rtcget` | none | Current date/time | RTC starts at 1980-01-01 without battery |
| `rtcset` | `YYYY MM DD HH MM SS` | Sets RTC | Time synchronization |
| `applist` | none | All installed apps with tags [RX]/[TX]/[UTIL] | 34 apps on this build |
| `button` | `<n>` (1-8) | `ok` | UI button simulation |
| `touch` | `<x> <y>` | `ok` | Touchscreen simulation |
| `keyboard` | `<chars>` | `ok` | Text input injection |
| `gotgps` | `<lat> <lon> [alt] [speed] [sats]` | `ok` | GPS coordinate injection |
| `gotenv` | `<temp> [humidity] [pressure] [light]` | `ok` | Environment sensor injection |
| `gotorientation` | `<angle> [tilt]` | `ok` | Orientation injection |
| `accessibility_readall` | none | All UI widget labels | Screen reader |
| `accessibility_readcurr` | none | Current focused widget | Requires active widget |
| `ls` | `<path>` | Directory listing | SD card filesystem |
| `filesize` | `<path>` | File size in bytes | |
| `fopen` | `<path>` | Opens file handle | Sequential file I/O |
| `fread` | `<n>` | Read n bytes | Binary data retrieval |
| `fwrite` | data | Write to open file | |
| `frb` | `<n>` | Read n bytes as binary | Raw binary read |
| `fwb` | data | Write binary | Raw binary write |
| `fseek` | `<pos>` | Seek in open file | |
| `ftell` | none | Current file position | |
| `fclose` | none | Close file handle | |
| `ftruncate` | `<size>` | Truncate file | |
| `mkdir` | `<path>` | Create directory | |
| `unlink` | `<path>` | Delete file | |
| `crc32` | `<path>` | CRC32 of file | Integrity verification |
| `screenshot` | none | Pixel data dump | Full screen capture |
| `screenframe` | none | Frame buffer data | Raw pixel stream |
| `screenframeshort` | none | Compressed frame | Smaller pixel dump |

### Dangerous / Disruptive

| Command | Issue | Severity |
|---------|-------|----------|
| `appstart <name>` | Causes USB reset — device disappears from bus, serial connection lost | **HIGH** — requires USB reconnect |
| `cpld_info portapack` | Causes USB I/O error, device becomes unresponsive | **HIGH** — requires replug |
| `cpld_read` | Likely same as cpld_info | **HIGH** |
| `hackrf` | Enters HackRF mode — changes USB personality | **HIGH** |
| `sd_over_usb` | Switches to USB mass storage mode | **HIGH** |
| `flash` | Firmware flash mode | **CRITICAL** |
| `dfu` | DFU bootloader mode | **CRITICAL** |
| `read_memory` | Direct memory read — could crash if wrong address | **MEDIUM** |
| `write_memory` | Direct memory write — could brick device | **CRITICAL** |
| `pmemreset` | Resets persistent memory to defaults | **MEDIUM** |
| `settingsreset` | Resets all settings | **MEDIUM** |
| `reboot` | Reboots device — USB reconnect needed | **MEDIUM** |

## API Richness Assessment

### Feature Categories

**1. Radio Control (MEDIUM)**
- Can query full radio state via `radioinfo`
- Cannot directly set frequency/gain/sample rate via serial (only via `appstart` which crashes USB)
- No `setfreq` command in this firmware version (despite wiki docs)

**2. UI Automation (HIGH)**
- Full button simulation (8 buttons)
- Touchscreen coordinate input
- Keyboard text injection
- Screen reading via accessibility
- Screenshot/frame capture

**3. Sensor Injection (HIGH)**
- GPS coordinates with altitude, speed, satellites
- Environmental: temperature, humidity, pressure, light
- Orientation: angle and tilt
- Useful for testing location-aware apps without real GPS

**4. Filesystem (HIGH)**
- Full SD card access: list, read, write, delete, mkdir
- Binary file I/O (fread/frb, fwrite/fwb)
- CRC32 integrity checks
- Can manage frequency lists, captures, settings files

**5. System Management (MEDIUM)**
- Version/build info detection
- Resource monitoring (heap, stack, CPU)
- RTC get/set for time synchronization
- Reboot (but loses USB connection)

**6. App Management (LOW — broken)**
- Can list all 34 apps with categories
- `appstart` causes USB reset — effectively unusable for programmatic app switching
- No way to query which app is currently running (accessibility_readcurr doesn't reliably report)

## Standalone API Verdict

### YES — Worth building a standalone `pymayhem` API

**Rationale:**
1. **47 commands** is substantial — this isn't a toy serial console
2. **UI automation** alone justifies a standalone API — remote control of PortaPack without touching it
3. **Filesystem access** enables automated capture management, frequency list updates, settings backup/restore
4. **Sensor injection** enables automated testing of GPS/weather apps
5. **Screen capture** enables automated visual verification and monitoring
6. The API is **device-agnostic** — works with any PortaPack Mayhem build, not just our HackRF ROS driver

**Architecture recommendation:**
- `pymayhem` as a standalone Python package (no ROS2 dependency)
- `hackrf_ros` imports `pymayhem` as a dependency (replaces current `MayhemSerial`)
- Clean separation: `pymayhem` handles serial protocol, `hackrf_ros` handles ROS2 integration

### Critical Design Considerations

1. **`appstart` USB reset** — the API must handle reconnection after `appstart`. Add a `appstart_with_reconnect()` that:
   - Sends `appstart <name>`
   - Expects USB disconnect
   - Polls for device reappearance (up to 10s)
   - Reconnects and verifies app is running

2. **Command blocklist** — dangerous commands (`flash`, `dfu`, `write_memory`, `sd_over_usb`) should be behind an explicit `unsafe=True` flag

3. **`setfreq` missing** — this firmware (v2.0.1) doesn't have `setfreq`. The wiki documents it for newer builds. The API should detect firmware version and report capability level

4. **Thread safety** — serial port needs locking for concurrent access (same as current MayhemSerial)

5. **Device detection** — use udev symlink `/dev/hackrf_mayhem` or scan by USB VID:PID `1d50:6018`

---
*Assessment date: 2026-03-30*
*Firmware tested: Mayhem v2.0.1*
