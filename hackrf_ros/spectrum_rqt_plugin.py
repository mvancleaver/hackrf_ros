"""rqt plugin — live HackRF spectrum display.

Embeds a matplotlib PSD plot inside rqt. Subscribes to /hackrf/spectrum
(SpectrumStamped, BEST_EFFORT) and updates via a Qt timer at 10 Hz.

Open in rqt: Plugins → HackRF → Spectrum Display
"""
from __future__ import annotations

import threading

import numpy as np

from python_qt_binding.QtWidgets import QWidget, QVBoxLayout, QLabel
from python_qt_binding.QtCore import QTimer, Qt

import matplotlib
matplotlib.use('Qt5Agg')
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from rqt_gui_py.plugin import Plugin

import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from hackrf_interfaces.msg import SpectrumStamped

_BG = '#1e1e2e'
_FG = '#cdd6f4'
_GREEN = '#00ff88'
_GRID = '#313244'


class HackRFSpectrumPlugin(Plugin):
    """rqt plugin: live spectrum from /hackrf/spectrum."""

    def __init__(self, context):
        super().__init__(context)
        self.setObjectName('HackRFSpectrum')

        # ── Widget ──────────────────────────────────────────────────────────
        self._widget = QWidget()
        self._widget.setWindowTitle('HackRF Spectrum')
        self._widget.setStyleSheet(f'background-color: {_BG};')
        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        self._widget.setLayout(layout)

        # Status label shown before first message
        self._status = QLabel('Waiting for /hackrf/spectrum …')
        self._status.setStyleSheet(f'color: {_FG}; font-size: 12px;')
        self._status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._status)

        # Matplotlib figure
        self._figure = Figure(figsize=(10, 4), facecolor=_BG)
        self._canvas = FigureCanvas(self._figure)
        self._canvas.setStyleSheet('background-color: transparent;')
        layout.addWidget(self._canvas)

        self._ax = self._figure.add_subplot(111)
        self._ax.set_facecolor(_BG)
        for spine in self._ax.spines.values():
            spine.set_color(_GRID)
        self._ax.tick_params(colors=_FG, labelsize=9)
        self._ax.set_xlabel('Frequency (MHz)', color=_FG, fontsize=10)
        self._ax.set_ylabel('Power (dBFS)', color=_FG, fontsize=10)
        self._ax.grid(True, alpha=0.3, color=_GRID)
        self._ax.set_title('Connecting…', color=_FG, fontsize=10)

        # Placeholder line
        dummy_f = np.linspace(2427.0, 2447.0, 128)
        dummy_p = np.full(128, -100.0)
        self._line, = self._ax.plot(dummy_f, dummy_p,
                                     color=_GREEN, linewidth=0.8)
        self._fill = None
        self._ax.set_xlim(dummy_f[0], dummy_f[-1])
        self._ax.set_ylim(-120, -40)

        self._figure.tight_layout()
        context.add_widget(self._widget)

        # ── ROS2 subscription in its own executor / thread ───────────────
        self._msg: SpectrumStamped | None = None
        self._lock = threading.Lock()

        self._node = rclpy.create_node('hackrf_spectrum_rqt')
        qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )
        self._node.create_subscription(
            SpectrumStamped, '/hackrf/spectrum', self._ros_cb, qos)

        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self._node)
        self._spin_thread = threading.Thread(
            target=self._executor.spin, daemon=True)
        self._spin_thread.start()

        # ── Qt timer → plot update at 10 Hz ──────────────────────────────
        self._y_min = -120.0
        self._y_max = -40.0
        self._prev_center = 0.0
        self._prev_rate = 0.0
        self._first = True

        self._timer = QTimer()
        self._timer.timeout.connect(self._update_plot)
        self._timer.start(100)

    # ── ROS callback (executor thread) ────────────────────────────────────
    def _ros_cb(self, msg: SpectrumStamped) -> None:
        with self._lock:
            self._msg = msg

    # ── Qt timer callback (GUI thread) ────────────────────────────────────
    def _update_plot(self) -> None:
        with self._lock:
            msg, self._msg = self._msg, None
        if msg is None:
            return

        psd = np.array(msg.psd_db, dtype=np.float32)
        center = msg.center_frequency_hz
        rate = msg.sample_rate_hz
        freqs_mhz = np.linspace(
            (center - rate / 2) / 1e6,
            (center + rate / 2) / 1e6,
            len(psd),
        )

        if center != self._prev_center or rate != self._prev_rate:
            self._prev_center, self._prev_rate = center, rate
            self._ax.set_xlim(freqs_mhz[0], freqs_mhz[-1])

        if self._first:
            self._first = False
            self._status.hide()

        self._line.set_xdata(freqs_mhz)
        self._line.set_ydata(psd)

        # Fill under curve
        if self._fill is not None:
            self._fill.remove()
        self._fill = self._ax.fill_between(
            freqs_mhz, self._y_min, psd, alpha=0.15, color=_GREEN)

        # Smooth Y range
        d_min = float(np.percentile(psd, 2))
        d_max = float(np.percentile(psd, 99.5))
        self._y_min += 0.15 * (d_min - 5 - self._y_min)
        self._y_max += 0.15 * (d_max + 5 - self._y_max)
        if self._y_max - self._y_min < 20:
            mid = (self._y_max + self._y_min) / 2
            self._y_min, self._y_max = mid - 10, mid + 10
        self._ax.set_ylim(self._y_min, self._y_max)

        peak_db = float(psd.max())
        peak_mhz = float(freqs_mhz[np.argmax(psd)])
        floor_db = float(msg.noise_floor_db)
        self._ax.set_title(
            f'{center / 1e6:.1f} MHz  |  {rate / 1e6:.0f} MSPS  |  '
            f'Peak: {peak_db:.1f} dBFS @ {peak_mhz:.1f} MHz  |  '
            f'Floor: {floor_db:.1f} dBFS',
            color=_FG, fontsize=10,
        )

        self._canvas.draw()

    # ── rqt lifecycle ─────────────────────────────────────────────────────
    def shutdown_plugin(self) -> None:
        self._timer.stop()
        self._executor.shutdown(timeout_sec=1.0)
        self._node.destroy_node()

    def save_settings(self, plugin_settings, instance_settings) -> None:
        pass

    def restore_settings(self, plugin_settings, instance_settings) -> None:
        pass
