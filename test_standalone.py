#!/usr/bin/env python3
"""Standalone hardware integration test for HackRF + Redis + Mayhem Serial.

No ROS2 dependency. Tests the core subsystems directly:
1. HackRF IQ reception via pyhackrf2
2. Redis IQ streaming via RedisBridge
3. Mayhem serial communication via MayhemSerial
4. Redis command dispatch
5. Redis state publishing

Usage:
    python3 test_standalone.py              # Run all tests
    python3 test_standalone.py --rx-only    # Just test HackRF RX
    python3 test_standalone.py --redis-only # Just test Redis bridge
    python3 test_standalone.py --serial-only # Just test Mayhem serial
"""

import sys
import time
import queue
import threading
import logging
import json
import argparse
import numpy as np

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logger = logging.getLogger('hackrf_test')

# ─── Colors for terminal output ───
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
RESET = '\033[0m'
BOLD = '\033[1m'


def banner(text):
    print(f'\n{BOLD}{CYAN}{"=" * 60}{RESET}')
    print(f'{BOLD}{CYAN}  {text}{RESET}')
    print(f'{BOLD}{CYAN}{"=" * 60}{RESET}\n')


def result(name, passed, detail=''):
    icon = f'{GREEN}PASS{RESET}' if passed else f'{RED}FAIL{RESET}'
    print(f'  [{icon}] {name}')
    if detail:
        print(f'         {detail}')
    return passed


# ═══════════════════════════════════════════════════════════════
# TEST 1: HackRF RX via pyhackrf2
# ═══════════════════════════════════════════════════════════════

def test_hackrf_rx():
    """Test raw HackRF IQ reception without ROS2."""
    banner('TEST 1: HackRF RX via pyhackrf2')

    rx_queue = queue.Queue(maxsize=64)
    chunk_count = 0
    stop_event = threading.Event()

    def rx_callback(data, *args):
        nonlocal chunk_count
        raw = bytes(data)
        try:
            rx_queue.put_nowait(raw)
        except queue.Full:
            try:
                rx_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                rx_queue.put_nowait(raw)
            except queue.Full:
                pass
        chunk_count += 1
        return stop_event.is_set()

    try:
        import pyhackrf2
        hackrf = pyhackrf2.HackRF()
    except Exception as e:
        result('HackRF device open', False, str(e))
        return False

    r1 = result('HackRF device open', True, f'Device found')

    # Configure
    try:
        hackrf.sample_rate = int(2e6)
        hackrf.center_freq = int(100e6)  # 100 MHz FM band
        hackrf.lna_gain = 16
        hackrf.vga_gain = 20
        hackrf.amplifier_on = False
    except Exception as e:
        result('Configure parameters', False, str(e))
        return False

    r2 = result('Configure parameters', True,
                f'freq=100MHz, rate=2MSPS, lna=16, vga=20')

    # Start RX
    try:
        hackrf.start_rx(rx_callback)
    except Exception as e:
        result('Start RX streaming', False, str(e))
        return False

    r3 = result('Start RX streaming', True)

    # Collect for 2 seconds
    logger.info('Collecting IQ data for 2 seconds...')
    time.sleep(2)

    r4 = result('Chunks received', chunk_count > 0,
                f'{chunk_count} chunks in 2s')

    # Drain one chunk and verify format
    sample_ok = False
    if not rx_queue.empty():
        raw = rx_queue.get_nowait()
        samples = np.frombuffer(raw, dtype=np.int8)
        iq = samples.reshape(-1, 2).astype(np.float32) / 128.0
        complex_iq = iq[:, 0] + 1j * iq[:, 1]
        r5 = result('IQ data format', len(complex_iq) > 0,
                     f'{len(complex_iq)} complex samples, '
                     f'I range [{iq[:,0].min():.2f}, {iq[:,0].max():.2f}], '
                     f'Q range [{iq[:,1].min():.2f}, {iq[:,1].max():.2f}]')
        sample_ok = True
    else:
        r5 = result('IQ data format', False, 'Queue empty after 2s')

    # Stop RX
    stop_event.set()
    try:
        hackrf.stop_rx()
        time.sleep(0.1)
        hackrf.close()
    except Exception:
        pass

    r6 = result('Stop RX + close', True)

    return all([r1, r2, r3, r4, r5, r6])


# ═══════════════════════════════════════════════════════════════
# TEST 2: Redis Bridge
# ═══════════════════════════════════════════════════════════════

def test_redis_bridge():
    """Test Redis IQ streaming and command dispatch without ROS2."""
    banner('TEST 2: Redis Bridge')

    try:
        import redis
        r = redis.Redis(host='localhost', port=6379, decode_responses=False)
        r.ping()
    except Exception as e:
        result('Redis connection', False, str(e))
        return False

    r1 = result('Redis connection', True, 'localhost:6379')

    # Clean up any previous test data
    r.delete(b'hackrf:iq:stream', b'hackrf:state', b'hackrf:cmd')

    # Test IQ streaming via XADD
    fake_iq = np.random.randn(4096).astype(np.float32).tobytes()
    try:
        stream_id = r.xadd('hackrf:iq:stream', {'iq': fake_iq}, maxlen=100, approximate=True)
    except Exception as e:
        result('XADD IQ stream', False, str(e))
        return False

    r2 = result('XADD IQ stream', stream_id is not None,
                f'id={stream_id.decode() if isinstance(stream_id, bytes) else stream_id}')

    # Read it back
    entries = r.xrange('hackrf:iq:stream', count=1)
    read_ok = len(entries) > 0 and b'iq' in entries[0][1]
    recovered = np.frombuffer(entries[0][1][b'iq'], dtype=np.float32) if read_ok else None
    r3 = result('XREAD IQ data', read_ok and recovered is not None,
                f'{len(recovered)} float32 samples recovered' if recovered is not None else 'no data')

    # Test state hash
    state = {
        'center_frequency': '100000000',
        'sample_rate': '2000000',
        'lna_gain': '16',
        'vga_gain': '20',
        'is_streaming': 'true',
        'connected': 'true',
    }
    r.hset('hackrf:state', mapping=state)
    read_state = r.hgetall('hackrf:state')
    r4 = result('HSET/HGETALL state', len(read_state) == len(state),
                f'{len(read_state)} fields')

    # Test command dispatch
    cmd = json.dumps({'action': 'setfreq', 'value': 433000000}).encode()
    cmd_id = r.xadd('hackrf:cmd', {'payload': cmd})
    entries = r.xread({'hackrf:cmd': '0-0'}, count=10, block=500)
    cmd_received = len(entries) > 0
    r5 = result('XADD/XREAD command', cmd_received, 'Command round-trip OK')

    # Test MAXLEN trimming
    for i in range(200):
        r.xadd('hackrf:iq:stream', {'iq': b'x' * 100}, maxlen=100, approximate=True)
    stream_len = r.xlen('hackrf:iq:stream')
    r6 = result('MAXLEN trimming', stream_len <= 150,
                f'Stream length {stream_len} after 200 adds (maxlen=100)')

    # Test hackrf: namespace
    keys = [k.decode() for k in r.keys('hackrf:*')]
    all_prefixed = all(k.startswith('hackrf:') for k in keys)
    r7 = result('hackrf: namespace', all_prefixed and len(keys) >= 3,
                f'Keys: {keys}')

    # Cleanup
    r.delete(b'hackrf:iq:stream', b'hackrf:state', b'hackrf:cmd')

    return all([r1, r2, r3, r4, r5, r6, r7])


# ═══════════════════════════════════════════════════════════════
# TEST 3: Mayhem Serial
# ═══════════════════════════════════════════════════════════════

def test_mayhem_serial():
    """Test Mayhem serial communication."""
    banner('TEST 3: Mayhem Serial (/dev/ttyACM1)')

    import serial

    # Auto-detect Mayhem port by udev symlink or fallback
    port = '/dev/serial/by-id/usb-Great_Scott_Gadgets_PortaPack_Mayhem_Transceiver-if00'
    import os
    if not os.path.exists(port):
        port = '/dev/ttyACM1'  # fallback

    try:
        # Open with non-blocking first to avoid kernel-level write block
        import termios
        import os as _os
        fd = _os.open(port, _os.O_RDWR | _os.O_NOCTTY | _os.O_NONBLOCK)
        # Clear HUPCL to prevent DTR drop on close, set raw mode
        attrs = termios.tcgetattr(fd)
        attrs[2] = attrs[2] & ~termios.HUPCL  # cflag: don't drop DTR on close
        attrs[2] = attrs[2] & ~termios.CRTSCTS  # cflag: no hardware flow control
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        _os.close(fd)

        ser = serial.Serial(
            port, 115200, timeout=2.0,
            write_timeout=3.0,
            dsrdtr=False, rtscts=False,
            xonxoff=False,
            exclusive=False,
        )
        ser.reset_input_buffer()
        ser.reset_output_buffer()
    except Exception as e:
        result('Serial port open', False, f'{port}: {e}')
        return False

    r1 = result('Serial port open', True, f'{port}')

    def send_and_read(ser, cmd, wait=1.5):
        """Send command and collect response until ch> prompt or timeout."""
        ser.reset_input_buffer()
        payload = cmd.encode() + b'\r\n'
        try:
            ser.write(payload)
        except serial.SerialTimeoutException:
            # Write timed out — try raw fd write as fallback
            import os as _os
            try:
                _os.write(ser.fileno(), payload)
            except Exception:
                return '[write failed]'
        buf = b''
        deadline = time.time() + wait
        while time.time() < deadline:
            waiting = ser.in_waiting
            if waiting > 0:
                chunk = ser.read(waiting)
                if chunk:
                    buf += chunk
                    if b'ch>' in buf:
                        break
            else:
                time.sleep(0.05)
        return buf.decode('utf-8', errors='replace')

    # Send empty line to get prompt
    try:
        response = send_and_read(ser, '', wait=2.0)
        has_prompt = 'ch>' in response
    except Exception as e:
        result('Mayhem prompt', False, str(e))
        ser.close()
        return False

    r2 = result('Mayhem prompt detected', has_prompt,
                f'Got: {repr(response[:80])}')

    if not has_prompt:
        print(f'  {YELLOW}No ch> prompt — device may not have ChibiOS shell active.{RESET}')
        print(f'  {YELLOW}Check that PortaPack Mayhem is booted and shell is enabled.{RESET}')
        ser.close()
        return False

    # Test applist
    try:
        response = send_and_read(ser, 'applist', wait=3.0)
        apps = [line.strip() for line in response.split('\n')
                if line.strip() and not line.strip().startswith('ch>')
                and line.strip() != 'applist' and not line.strip().startswith('\r')]
    except Exception as e:
        result('applist command', False, str(e))
        ser.close()
        return False

    r3 = result('applist command', len(apps) > 0,
                f'Found {len(apps)} apps: {apps[:5]}...' if len(apps) > 5 else f'Apps: {apps}')

    # Test radioinfo
    try:
        response = send_and_read(ser, 'radioinfo', wait=2.0)
        has_info = len(response) > 10
    except Exception as e:
        result('radioinfo command', False, str(e))
        ser.close()
        return False

    r4 = result('radioinfo command', has_info,
                f'Response: {repr(response[:120])}')

    ser.close()
    r5 = result('Serial close', True)

    return all([r1, r2, r3, r4, r5])


# ═══════════════════════════════════════════════════════════════
# TEST 4: Mode Coexistence (pyhackrf2 + serial simultaneous)
# ═══════════════════════════════════════════════════════════════

def test_mode_coexistence():
    """Test that pyhackrf2 IQ streaming and Mayhem serial work simultaneously."""
    banner('TEST 4: Mode Coexistence (pyhackrf2 + serial)')

    import serial
    import os

    # Open serial first
    port = '/dev/serial/by-id/usb-Great_Scott_Gadgets_PortaPack_Mayhem_Transceiver-if00'
    if not os.path.exists(port):
        port = '/dev/ttyACM1'

    try:
        ser = serial.Serial(port, 115200, timeout=2.0, write_timeout=2.0,
                            dsrdtr=False, rtscts=False, xonxoff=False)
        ser.dtr = False
        time.sleep(0.1)
        ser.dtr = True
        time.sleep(0.3)
        ser.reset_input_buffer()
    except Exception as e:
        result('Serial open', False, str(e))
        return False

    r1 = result('Serial open', True, port)

    # Open HackRF
    chunk_count = 0
    stop_event = threading.Event()

    def rx_callback(data, *args):
        nonlocal chunk_count
        chunk_count += 1
        return stop_event.is_set()

    try:
        import pyhackrf2
        hackrf = pyhackrf2.HackRF()
        hackrf.sample_rate = int(2e6)
        hackrf.center_freq = int(100e6)
        hackrf.lna_gain = 16
        hackrf.vga_gain = 20
        hackrf.start_rx(rx_callback)
    except Exception as e:
        result('HackRF RX start', False, str(e))
        ser.close()
        return False

    r2 = result('HackRF RX start (while serial open)', True)

    # Now try serial command while RX streaming
    time.sleep(0.5)  # Let RX settle
    def send_and_read_coex(ser, cmd, wait=2.0):
        ser.reset_input_buffer()
        ser.write(cmd.encode() + b'\r\n')
        buf = b''
        deadline = time.time() + wait
        while time.time() < deadline:
            chunk = ser.read(ser.in_waiting or 1)
            if chunk:
                buf += chunk
                if b'ch>' in buf:
                    break
            else:
                time.sleep(0.05)
        return buf.decode('utf-8', errors='replace')

    try:
        response = send_and_read_coex(ser, 'radioinfo', wait=2.0)
        serial_works = len(response) > 5
    except Exception as e:
        serial_works = False
        response = str(e)

    r3 = result('Serial radioinfo during RX', serial_works,
                f'Response: {repr(response[:80])}')

    # Check RX still flowing
    count_before = chunk_count
    time.sleep(1)
    rx_still_flowing = chunk_count > count_before

    r4 = result('RX still streaming after serial cmd', rx_still_flowing,
                f'{chunk_count - count_before} new chunks in 1s')

    # Cleanup
    stop_event.set()
    try:
        hackrf.stop_rx()
        time.sleep(0.1)
        hackrf.close()
    except Exception:
        pass
    ser.close()

    coexist = all([r1, r2, r3, r4])
    if coexist:
        print(f'\n  {GREEN}{BOLD}MODE COEXISTENCE CONFIRMED{RESET}')
        print(f'  pyhackrf2 IQ streaming and Mayhem serial work simultaneously.')
    else:
        print(f'\n  {RED}{BOLD}MODE CONFLICT DETECTED{RESET}')
        print(f'  pyhackrf2 and Mayhem serial cannot operate simultaneously.')

    return coexist


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════���═

def main():
    parser = argparse.ArgumentParser(description='Standalone HackRF integration tests')
    parser.add_argument('--rx-only', action='store_true', help='Test HackRF RX only')
    parser.add_argument('--redis-only', action='store_true', help='Test Redis bridge only')
    parser.add_argument('--serial-only', action='store_true', help='Test Mayhem serial only')
    parser.add_argument('--coexist-only', action='store_true', help='Test mode coexistence only')
    args = parser.parse_args()

    results = {}

    if args.rx_only:
        results['HackRF RX'] = test_hackrf_rx()
    elif args.redis_only:
        results['Redis Bridge'] = test_redis_bridge()
    elif args.serial_only:
        results['Mayhem Serial'] = test_mayhem_serial()
    elif args.coexist_only:
        results['Mode Coexistence'] = test_mode_coexistence()
    else:
        # Run all tests in order
        results['HackRF RX'] = test_hackrf_rx()
        results['Redis Bridge'] = test_redis_bridge()
        results['Mayhem Serial'] = test_mayhem_serial()
        if results['HackRF RX'] and results['Mayhem Serial']:
            results['Mode Coexistence'] = test_mode_coexistence()
        else:
            print(f'\n  {YELLOW}Skipping mode coexistence test — HackRF or serial failed{RESET}')

    # Summary
    banner('RESULTS')
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    for name, ok in results.items():
        icon = f'{GREEN}PASS{RESET}' if ok else f'{RED}FAIL{RESET}'
        print(f'  [{icon}] {name}')

    print(f'\n  {BOLD}{passed}/{total} test groups passed{RESET}\n')
    return 0 if passed == total else 1


if __name__ == '__main__':
    sys.exit(main())
