"""CLI entry point for hackrf_driver standalone mode (D-11).

Provides `hackrf-driver` console script and `python -m hackrf_driver` entry point.
Loads config via load_config(), applies CLI overrides, then starts HackRFDriver.

Usage::

    python -m hackrf_driver --help
    hackrf-driver --freq 433e6 --lna-gain 24 --vga-gain 30
    hackrf-driver --config /etc/hackrf/driver.yaml --serial-port /dev/ttyACM0
"""
import argparse
import sys


def main():
    """Parse CLI arguments, build config, and run HackRFDriver."""
    parser = argparse.ArgumentParser(
        prog='hackrf-driver',
        description='Standalone HackRF driver with Redis interface (no ROS2)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  hackrf-driver --freq 433e6
  hackrf-driver --freq 915e6 --lna-gain 24 --vga-gain 30
  hackrf-driver --config /etc/hackrf/driver.yaml
  hackrf-driver --redis-host 192.168.1.100 --no-freq-filter
        """,
    )
    parser.add_argument(
        '--config',
        default=None,
        metavar='PATH',
        help='YAML config file path (merged with defaults)',
    )
    parser.add_argument(
        '--freq',
        type=float,
        default=None,
        metavar='HZ',
        help='Center frequency in Hz (e.g. 433e6)',
    )
    parser.add_argument(
        '--lna-gain',
        type=int,
        default=None,
        dest='lna_gain',
        metavar='DB',
        help='LNA gain in dB (0-40)',
    )
    parser.add_argument(
        '--vga-gain',
        type=int,
        default=None,
        dest='vga_gain',
        metavar='DB',
        help='VGA gain in dB (0-62)',
    )
    parser.add_argument(
        '--sample-rate',
        type=float,
        default=None,
        dest='sample_rate',
        metavar='HZ',
        help='Sample rate in Hz (2e6-20e6)',
    )
    parser.add_argument(
        '--serial-port',
        default=None,
        dest='serial_port',
        metavar='DEV',
        help='Mayhem serial port path (default: /dev/hackrf_mayhem)',
    )
    parser.add_argument(
        '--redis-host',
        default=None,
        dest='redis_host',
        metavar='HOST',
        help='Redis server hostname (default: localhost)',
    )
    parser.add_argument(
        '--redis-port',
        type=int,
        default=None,
        dest='redis_port',
        metavar='PORT',
        help='Redis server port (default: 6379)',
    )
    parser.add_argument(
        '--no-freq-filter',
        action='store_true',
        dest='no_freq_filter',
        help='Disable TX frequency allowlist (requires Redis override too)',
    )
    parser.add_argument(
        '--skip-antenna-check',
        action='store_true',
        dest='skip_antenna_check',
        help='Skip antenna confirmation check (automated test environments only)',
    )

    args = parser.parse_args()

    from hackrf_driver.config import load_config
    config = load_config(args.config or '')

    # Apply CLI overrides
    if args.freq is not None:
        config['center_frequency'] = args.freq
    if args.lna_gain is not None:
        config['lna_gain'] = args.lna_gain
    if args.vga_gain is not None:
        config['vga_gain'] = args.vga_gain
    if args.sample_rate is not None:
        config['sample_rate'] = args.sample_rate
    if args.serial_port is not None:
        config['serial_port'] = args.serial_port
    if args.redis_host is not None:
        config['redis_host'] = args.redis_host
    if args.redis_port is not None:
        config['redis_port'] = args.redis_port
    if args.no_freq_filter:
        config['tx_freq_filter_enabled'] = False
    if args.skip_antenna_check:
        config['tx_skip_antenna_check'] = True

    import logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(name)s %(levelname)s %(message)s',
    )

    from hackrf_driver.driver import HackRFDriver
    driver = HackRFDriver(config)
    driver.run()
