"""rqt plugin — HackRF spectrum display with tuning, sweep control, IQ recording.

Features:
  - Center-frequency control (left panel) with live param set
  - Live PSD from /hackrf/spectrum with detection bounding boxes
  - Sweep action client: set min/max/step freq, single or continuous sweep
  - Clear-sweep button
  - IQ recorder action client: configure path + duration, start/stop

Open in rqt: Plugins → HackRF → Spectrum Display
"""
from __future__ import annotations

import queue
import threading
import time

import numpy as np

from python_qt_binding.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QDoubleSpinBox, QSpinBox, QProgressBar, QFileDialog,
    QGroupBox, QSizePolicy,
)
from python_qt_binding.QtCore import QTimer, Qt
from python_qt_binding.QtGui import QColor

import matplotlib
matplotlib.use('Qt5Agg')
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

from rqt_gui_py.plugin import Plugin

import rclpy
from rclpy.action import ActionClient
from rclpy.executors import MultiThreadedExecutor
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from rclpy.parameter import Parameter
from rcl_interfaces.srv import SetParameters

from hackrf_interfaces.msg import SpectrumStamped, RFDetectionArray, RFEmitterMap
from hackrf_interfaces.action import Sweep as SweepAction
from hackrf_interfaces.action import RecordIQ as RecordIQAction

# ── Theme ─────────────────────────────────────────────────────────────────────
_BG   = '#1e1e2e'
_FG   = '#cdd6f4'
_GRN  = '#00ff88'
_ORG  = '#fab387'
_GRID = '#313244'
_BTN  = '#45475a'
_RED  = '#f38ba8'
_YEL  = '#f9e2af'
_BLUE = '#89b4fa'

# Detection classification → matplotlib color
_CLASSIFY_COLOR: dict[str, str] = {
    'wifi_2_4g':   '#89dceb',
    'wifi_5g':     '#89b4fa',
    'ble':         '#74c7ec',
    'zigbee':      '#94e2d5',
    'lte_b12_b13': '#fab387',
    'lte_b13':     '#fab387',
    'lte_b4_b66':  '#fab387',
    'lte_b2':      '#fab387',
    'lte_b5':      '#fab387',
    'lte_b7':      '#fab387',
    'ism_900':     '#f9e2af',
    'ism_2400':    '#f9e2af',
    'ism_5800':    '#f9e2af',
    'fm_radio':    '#cba6f7',
    'gps_l1':      '#a6e3a1',
    'dect':        '#f5c2e7',
    'cdma_cellular': '#eba0ac',
    'unknown':     '#6c7086',
}


def _btn_style(color: str = _BTN) -> str:
    return (
        f'background-color: {color}; color: {_FG}; '
        f'border: 1px solid #585b70; border-radius: 4px; padding: 4px 10px;'
    )


class HackRFSpectrumPlugin(Plugin):
    """rqt plugin: live spectrum + tuning + sweep control + IQ recorder."""

    def __init__(self, context):
        super().__init__(context)
        self.setObjectName('HackRFSpectrum')

        # ── ROS2 ─────────────────────────────────────────────────────
        self._node = rclpy.create_node('hackrf_spectrum_rqt')
        qos_rel = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST, depth=5)
        self._node.create_subscription(
            SpectrumStamped, '/hackrf/spectrum', self._spectrum_cb, qos_rel)
        self._node.create_subscription(
            RFDetectionArray, '/hackrf/detections', self._detections_cb, qos_rel)
        self._node.create_subscription(
            RFEmitterMap, '/hackrf/emitter_map', self._emitter_map_cb, qos_rel)
        self._node.create_subscription(
            RFDetectionArray, '/hackrf/cyclo_detections',
            self._cyclo_detections_cb, qos_rel)

        self._sweep_client = ActionClient(
            self._node, SweepAction, '/hackrf/sweep')
        self._record_client = ActionClient(
            self._node, RecordIQAction, '/hackrf/record_iq')

        # SetParameters service for center-frequency control
        self._param_client = self._node.create_client(
            SetParameters, '/hackrf_node/set_parameters')

        self._executor = MultiThreadedExecutor()
        self._executor.add_node(self._node)
        self._spin_thread = threading.Thread(
            target=self._executor.spin, daemon=True)
        self._spin_thread.start()

        # ── Thread-safe data ─────────────────────────────────────────
        self._lock = threading.Lock()
        self._spectrum_msg: SpectrumStamped | None = None
        self._detections_msg: RFDetectionArray | None = None
        self._emitter_map_msg: RFEmitterMap | None = None
        self._cyclo_detections_msg: RFDetectionArray | None = None
        # Sweep result: (freqs_mhz ndarray, psd_db ndarray) | None
        self._sweep_result: tuple | None = None
        # Feedback queue: (current_hop, total_hops, current_freq_hz)
        self._sweep_feedback_q: queue.Queue = queue.Queue(maxsize=4)

        # ── State ────────────────────────────────────────────────────
        self._recording = False
        self._record_start: float = 0.0
        self._sweep_goal_handle = None
        self._sweeping = False
        self._sweep_continuous = False   # continuous sweep mode
        self._sweep_in_flight = False    # a single sweep goal is active
        self._hide_live_psd = False      # True after sweep until tune/clear

        # ── Plot state ───────────────────────────────────────────────
        self._y_min = -120.0
        self._y_max = -40.0
        self._prev_center = 0.0
        self._prev_rate = 0.0
        self._det_patches: list = []
        self._det_labels: list = []
        self._sweep_line = None
        self._sweep_fill = None

        # ── Widget ───────────────────────────────────────────────────
        self._widget = QWidget()
        self._widget.setWindowTitle('HackRF Spectrum')
        self._widget.setStyleSheet(f'background-color: {_BG}; color: {_FG};')

        root = QVBoxLayout()
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)
        self._widget.setLayout(root)

        # Control row: [tune] [sweep] [recorder] [intel]
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)
        ctrl.addWidget(self._build_tune_group())
        ctrl.addWidget(self._build_sweep_group())
        ctrl.addWidget(self._build_recorder_group())
        ctrl.addWidget(self._build_intel_group())
        root.addLayout(ctrl)

        # Plot
        root.addWidget(self._build_plot())

        context.add_widget(self._widget)

        # ── Timers ───────────────────────────────────────────────────
        self._plot_timer = QTimer()
        self._plot_timer.timeout.connect(self._update_plot)
        self._plot_timer.start(80)   # ~12 Hz

        self._rec_timer = QTimer()
        self._rec_timer.timeout.connect(self._update_rec_label)
        self._rec_timer.start(1000)

    # ── Widget builders ───────────────────────────────────────────────────────

    def _build_tune_group(self) -> QGroupBox:
        g = QGroupBox('Tune')
        g.setStyleSheet(
            f'QGroupBox {{ color: {_FG}; border: 1px solid #585b70; '
            f'border-radius: 4px; margin-top: 6px; padding-top: 4px; }}'
            f'QGroupBox::title {{ subcontrol-origin: margin; left: 8px; }}')
        lay = QVBoxLayout()
        lay.setSpacing(6)

        # Center frequency spinbox
        freq_row = QHBoxLayout()
        freq_row.addWidget(QLabel('Center (MHz):'))
        self._tune_freq = QDoubleSpinBox()
        self._tune_freq.setRange(1, 6000)
        self._tune_freq.setValue(2437)
        self._tune_freq.setDecimals(3)
        self._tune_freq.setSingleStep(1.0)
        self._tune_freq.setMinimumWidth(110)
        self._tune_freq.setStyleSheet(
            f'background:{_BTN}; color:{_FG}; border:1px solid #585b70;')
        freq_row.addWidget(self._tune_freq)
        lay.addLayout(freq_row)

        # Tune button
        self._tune_btn = QPushButton('Set Frequency')
        self._tune_btn.setStyleSheet(_btn_style(_BLUE))
        self._tune_btn.clicked.connect(self._on_tune_clicked)
        lay.addWidget(self._tune_btn)

        self._tune_status = QLabel('Ready')
        self._tune_status.setStyleSheet(f'color: {_FG}; font-size: 10px;')
        self._tune_status.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._tune_status)

        g.setLayout(lay)
        return g

    def _build_sweep_group(self) -> QGroupBox:
        g = QGroupBox('Sweep')
        g.setStyleSheet(
            f'QGroupBox {{ color: {_FG}; border: 1px solid #585b70; '
            f'border-radius: 4px; margin-top: 6px; padding-top: 4px; }}'
            f'QGroupBox::title {{ subcontrol-origin: margin; left: 8px; }}')
        lay = QVBoxLayout()
        lay.setSpacing(4)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel('Min (MHz):'))
        self._sweep_min = QDoubleSpinBox()
        self._sweep_min.setRange(1, 6000)
        self._sweep_min.setValue(2400)
        self._sweep_min.setDecimals(1)
        self._sweep_min.setStyleSheet(
            f'background:{_BTN}; color:{_FG}; border:1px solid #585b70;')
        row1.addWidget(self._sweep_min)

        row1.addWidget(QLabel('Max (MHz):'))
        self._sweep_max = QDoubleSpinBox()
        self._sweep_max.setRange(1, 6000)
        self._sweep_max.setValue(2500)
        self._sweep_max.setDecimals(1)
        self._sweep_max.setStyleSheet(
            f'background:{_BTN}; color:{_FG}; border:1px solid #585b70;')
        row1.addWidget(self._sweep_max)

        row1.addWidget(QLabel('Step (MHz):'))
        self._sweep_step = QDoubleSpinBox()
        self._sweep_step.setRange(1, 20)
        self._sweep_step.setValue(20)
        self._sweep_step.setDecimals(0)
        self._sweep_step.setStyleSheet(
            f'background:{_BTN}; color:{_FG}; border:1px solid #585b70;')
        row1.addWidget(self._sweep_step)

        row1.addWidget(QLabel('Avg:'))
        self._sweep_avg = QSpinBox()
        self._sweep_avg.setRange(1, 32)
        self._sweep_avg.setValue(4)
        self._sweep_avg.setStyleSheet(
            f'background:{_BTN}; color:{_FG}; border:1px solid #585b70;')
        row1.addWidget(self._sweep_avg)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        self._sweep_btn = QPushButton('Sweep')
        self._sweep_btn.setStyleSheet(_btn_style(_BTN))
        self._sweep_btn.clicked.connect(self._on_sweep_clicked)
        row2.addWidget(self._sweep_btn)

        self._sweep_cont_btn = QPushButton('Continuous')
        self._sweep_cont_btn.setStyleSheet(_btn_style(_BTN))
        self._sweep_cont_btn.clicked.connect(self._on_sweep_continuous_clicked)
        row2.addWidget(self._sweep_cont_btn)

        self._sweep_clear_btn = QPushButton('Clear')
        self._sweep_clear_btn.setStyleSheet(_btn_style(_BTN))
        self._sweep_clear_btn.clicked.connect(self._on_sweep_clear)
        row2.addWidget(self._sweep_clear_btn)

        self._sweep_progress = QProgressBar()
        self._sweep_progress.setValue(0)
        self._sweep_progress.setFormat('%v / %m hops')
        self._sweep_progress.setStyleSheet(
            f'QProgressBar {{ background: {_BTN}; border: 1px solid #585b70; '
            f'border-radius:3px; color:{_FG}; }}'
            f'QProgressBar::chunk {{ background: {_GRN}; }}')
        row2.addWidget(self._sweep_progress)

        self._sweep_status = QLabel('Ready')
        self._sweep_status.setStyleSheet(f'color: {_FG}; font-size: 11px;')
        row2.addWidget(self._sweep_status)
        lay.addLayout(row2)

        g.setLayout(lay)
        return g

    def _build_recorder_group(self) -> QGroupBox:
        g = QGroupBox('IQ Recorder')
        g.setStyleSheet(
            f'QGroupBox {{ color: {_FG}; border: 1px solid #585b70; '
            f'border-radius: 4px; margin-top: 6px; padding-top: 4px; }}'
            f'QGroupBox::title {{ subcontrol-origin: margin; left: 8px; }}')
        lay = QVBoxLayout()
        lay.setSpacing(4)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel('Save to:'))
        self._rec_path = QLineEdit('/tmp/hackrf_recording')
        self._rec_path.setStyleSheet(
            f'background:{_BTN}; color:{_FG}; border:1px solid #585b70;')
        row1.addWidget(self._rec_path)
        browse_btn = QPushButton('…')
        browse_btn.setFixedWidth(28)
        browse_btn.setStyleSheet(_btn_style())
        browse_btn.clicked.connect(self._browse_path)
        row1.addWidget(browse_btn)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel('Duration (s, 0=∞):'))
        self._rec_duration = QDoubleSpinBox()
        self._rec_duration.setRange(0, 3600)
        self._rec_duration.setValue(0)
        self._rec_duration.setDecimals(0)
        self._rec_duration.setStyleSheet(
            f'background:{_BTN}; color:{_FG}; border:1px solid #585b70;')
        row2.addWidget(self._rec_duration)

        self._rec_btn = QPushButton('● Record')
        self._rec_btn.setStyleSheet(_btn_style())
        self._rec_btn.clicked.connect(self._on_record_clicked)
        row2.addWidget(self._rec_btn)

        self._rec_label = QLabel('Idle')
        self._rec_label.setStyleSheet(
            f'color: {_FG}; font-size: 11px; min-width: 80px;')
        row2.addWidget(self._rec_label)
        lay.addLayout(row2)

        g.setLayout(lay)
        return g

    def _build_intel_group(self) -> QGroupBox:
        g = QGroupBox('RF Intel')
        g.setStyleSheet(
            f'QGroupBox {{ color: {_FG}; border: 1px solid #585b70; '
            f'border-radius: 4px; margin-top: 6px; padding-top: 4px; }}'
            f'QGroupBox::title {{ subcontrol-origin: margin; left: 8px; }}')
        lay = QVBoxLayout()
        lay.setSpacing(4)

        # Anomaly status
        self._intel_anomaly_lbl = QLabel('⚠ Anomalies: —')
        self._intel_anomaly_lbl.setStyleSheet(f'color: {_FG}; font-size: 11px;')
        lay.addWidget(self._intel_anomaly_lbl)

        # Cyclo classification summary
        self._intel_cyclo_lbl = QLabel('Cyclo: —')
        self._intel_cyclo_lbl.setStyleSheet(f'color: {_FG}; font-size: 11px;')
        lay.addWidget(self._intel_cyclo_lbl)

        # Emitter map list
        self._intel_emitter_lbl = QLabel('Emitters: none')
        self._intel_emitter_lbl.setStyleSheet(
            f'color: {_FG}; font-size: 10px; font-family: monospace;')
        self._intel_emitter_lbl.setWordWrap(True)
        self._intel_emitter_lbl.setMinimumWidth(180)
        lay.addWidget(self._intel_emitter_lbl)

        lay.addStretch()
        g.setLayout(lay)
        return g

    def _build_plot(self) -> FigureCanvas:
        self._figure = Figure(figsize=(12, 4), facecolor=_BG)
        self._canvas = FigureCanvas(self._figure)
        self._canvas.setStyleSheet('background-color: transparent;')
        self._ax = self._figure.add_subplot(111)
        self._ax.set_facecolor(_BG)
        for sp in self._ax.spines.values():
            sp.set_color(_GRID)
        self._ax.tick_params(colors=_FG, labelsize=9)
        self._ax.set_xlabel('Frequency (MHz)', color=_FG, fontsize=10)
        self._ax.set_ylabel('Power (dBFS)', color=_FG, fontsize=10)
        self._ax.grid(True, alpha=0.25, color=_GRID)
        self._ax.set_title('Waiting for /hackrf/spectrum …', color=_FG, fontsize=10)

        dummy_f = np.linspace(2427, 2447, 128)
        dummy_p = np.full(128, -100.0)
        self._live_line, = self._ax.plot(
            dummy_f, dummy_p, color=_GRN, linewidth=0.9, label='Live PSD')
        self._live_fill = None
        self._ax.set_xlim(dummy_f[0], dummy_f[-1])
        self._ax.set_ylim(-120, -40)

        self._figure.tight_layout(pad=0.8)
        return self._canvas

    # ── ROS callbacks (executor thread) ──────────────────────────────────────

    def _spectrum_cb(self, msg: SpectrumStamped) -> None:
        with self._lock:
            self._spectrum_msg = msg

    def _detections_cb(self, msg: RFDetectionArray) -> None:
        with self._lock:
            self._detections_msg = msg

    def _emitter_map_cb(self, msg: RFEmitterMap) -> None:
        with self._lock:
            self._emitter_map_msg = msg

    def _cyclo_detections_cb(self, msg: RFDetectionArray) -> None:
        with self._lock:
            self._cyclo_detections_msg = msg

    # ── Tune control ──────────────────────────────────────────────────────────

    def _on_tune_clicked(self) -> None:
        freq_hz = self._tune_freq.value() * 1e6
        self._tune_status.setText('Setting…')
        self._tune_btn.setEnabled(False)
        threading.Thread(
            target=self._send_tune, args=(freq_hz,), daemon=True).start()

    def _send_tune(self, freq_hz: float) -> None:
        try:
            if not self._param_client.wait_for_service(timeout_sec=3.0):
                self._tune_status.setText('No driver')
                self._tune_btn.setEnabled(True)
                return
            req = SetParameters.Request()
            p = Parameter('center_frequency', Parameter.Type.DOUBLE, freq_hz)
            req.parameters = [p.to_parameter_msg()]

            done_event = threading.Event()
            result_holder: list = [None]

            def _done(fut):
                result_holder[0] = fut
                done_event.set()

            future = self._param_client.call_async(req)
            future.add_done_callback(_done)
            if done_event.wait(timeout=5.0) and result_holder[0]:
                res = result_holder[0].result()
                if res and res.results and res.results[0].successful:
                    self._tune_status.setText(f'{freq_hz/1e6:.3f} MHz')
                    self._hide_live_psd = False
                else:
                    reason = (res.results[0].reason if res and res.results
                              else 'no response')
                    self._tune_status.setText(f'Rejected: {reason[:20]}')
            else:
                self._tune_status.setText('Timeout')
        except Exception as exc:
            self._tune_status.setText(f'Error: {str(exc)[:20]}')
        finally:
            self._tune_btn.setEnabled(True)

    # ── Sweep control ─────────────────────────────────────────────────────────

    def _on_sweep_clicked(self) -> None:
        """Single sweep — cancel if in progress."""
        if self._sweeping:
            self._cancel_sweep()
            return
        self._sweep_continuous = False
        self._start_sweep()

    def _on_sweep_continuous_clicked(self) -> None:
        """Toggle continuous sweep mode."""
        if self._sweeping:
            # Stop continuous loop
            self._sweep_continuous = False
            self._cancel_sweep()
            self._sweep_cont_btn.setText('Continuous')
            self._sweep_cont_btn.setStyleSheet(_btn_style(_BTN))
            return
        self._sweep_continuous = True
        self._sweep_cont_btn.setText('Stop')
        self._sweep_cont_btn.setStyleSheet(_btn_style(_RED))
        self._start_sweep()

    def _start_sweep(self) -> None:
        self._sweeping = True
        self._sweep_in_flight = True
        self._hide_live_psd = True
        self._sweep_btn.setText('Cancel')
        self._sweep_btn.setStyleSheet(_btn_style(_RED))
        self._sweep_status.setText('Connecting…')
        self._sweep_progress.setValue(0)
        self._sweep_progress.setMaximum(1)

        goal = SweepAction.Goal()
        goal.freq_min_hz = self._sweep_min.value() * 1e6
        goal.freq_max_hz = self._sweep_max.value() * 1e6
        goal.step_hz     = self._sweep_step.value() * 1e6
        goal.averaging   = self._sweep_avg.value()

        threading.Thread(
            target=self._send_sweep_goal, args=(goal,), daemon=True).start()

    def _cancel_sweep(self) -> None:
        self._sweep_continuous = False
        self._sweeping = False
        if self._sweep_goal_handle is not None:
            self._sweep_goal_handle.cancel_goal_async()
        self._sweep_btn.setText('Sweep')
        self._sweep_btn.setStyleSheet(_btn_style(_BTN))
        self._sweep_status.setText('Cancelled')

    def _on_sweep_clear(self) -> None:
        """Remove sweep overlay and restore live PSD."""
        if self._sweep_line is not None:
            self._sweep_line.remove()
            self._sweep_line = None
        if self._sweep_fill is not None:
            self._sweep_fill.remove()
            self._sweep_fill = None
        self._hide_live_psd = False
        self._live_line.set_visible(True)
        if self._live_fill is not None:
            self._live_fill.set_visible(True)
        if self._ax.get_legend():
            self._ax.legend_.remove()
        self._canvas.draw()

    def _send_sweep_goal(self, goal: SweepAction.Goal) -> None:
        if not self._sweep_client.wait_for_server(timeout_sec=5.0):
            self._sweep_status.setText('No sweep server')
            self._sweeping = False
            self._sweep_in_flight = False
            return
        send_future = self._sweep_client.send_goal_async(
            goal, feedback_callback=self._on_sweep_feedback_cb)
        send_future.add_done_callback(self._on_sweep_accepted)

    def _on_sweep_accepted(self, future) -> None:
        self._sweep_goal_handle = future.result()
        if not self._sweep_goal_handle.accepted:
            self._sweep_status.setText('Goal rejected')
            self._sweeping = False
            self._sweep_in_flight = False
            return
        result_future = self._sweep_goal_handle.get_result_async()
        result_future.add_done_callback(self._on_sweep_result)

    def _on_sweep_feedback_cb(self, feedback_msg) -> None:
        fb = feedback_msg.feedback
        try:
            self._sweep_feedback_q.put_nowait(
                (fb.current_hop, fb.total_hops, fb.current_freq_hz))
        except queue.Full:
            pass

    def _on_sweep_result(self, future) -> None:
        self._sweep_in_flight = False
        result = future.result().result
        if not result.success:
            self._sweep_status.setText(f'Failed: {result.message[:30]}')
            self._sweeping = False
            self._sweep_continuous = False
            return

        n = len(result.psd_db)
        freqs_mhz = np.linspace(
            result.freq_min_hz / 1e6,
            result.freq_max_hz / 1e6,
            n,
        )
        psd = np.array(result.psd_db, dtype=np.float32)
        with self._lock:
            self._sweep_result = (freqs_mhz, psd)

        # Relaunch immediately if continuous mode is still active
        if self._sweep_continuous and self._sweeping:
            goal = SweepAction.Goal()
            goal.freq_min_hz = self._sweep_min.value() * 1e6
            goal.freq_max_hz = self._sweep_max.value() * 1e6
            goal.step_hz     = self._sweep_step.value() * 1e6
            goal.averaging   = self._sweep_avg.value()
            self._sweep_in_flight = True
            threading.Thread(
                target=self._send_sweep_goal, args=(goal,), daemon=True).start()
        else:
            self._sweeping = False

    # ── IQ recorder control ───────────────────────────────────────────────────

    def _browse_path(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self._widget, 'Select recording directory',
            self._rec_path.text())
        if path:
            self._rec_path.setText(path)

    def _on_record_clicked(self) -> None:
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        self._recording = True
        self._record_start = time.monotonic()
        self._rec_btn.setText('■ Stop')
        self._rec_btn.setStyleSheet(_btn_style(_RED))
        self._rec_label.setText('⏱ 00:00')

        goal = RecordIQAction.Goal()
        goal.duration_s     = float(self._rec_duration.value())
        goal.output_path    = self._rec_path.text()
        goal.trigger_reason = 'rqt_manual'

        threading.Thread(
            target=self._send_record_goal, args=(goal,), daemon=True).start()

    def _send_record_goal(self, goal: RecordIQAction.Goal) -> None:
        if not self._record_client.wait_for_server(timeout_sec=3.0):
            self._recording = False
            self._rec_label.setText('No recorder')
            self._rec_btn.setText('● Record')
            self._rec_btn.setStyleSheet(_btn_style())
            return
        send_future = self._record_client.send_goal_async(goal)
        send_future.add_done_callback(self._on_record_accepted)

    def _on_record_accepted(self, future) -> None:
        self._record_goal_handle = future.result()
        if not self._record_goal_handle.accepted:
            self._recording = False
            self._rec_label.setText('Rejected')
            return
        result_future = self._record_goal_handle.get_result_async()
        result_future.add_done_callback(self._on_record_result)

    def _on_record_result(self, future) -> None:
        result = future.result().result
        self._recording = False
        if result.success:
            self._rec_label.setText(f'Saved {result.samples_written:,} smp')
        else:
            self._rec_label.setText(f'Error: {result.message[:20]}')

    def _stop_recording(self) -> None:
        self._recording = False
        self._rec_btn.setText('● Record')
        self._rec_btn.setStyleSheet(_btn_style())
        if hasattr(self, '_record_goal_handle') and self._record_goal_handle:
            self._record_goal_handle.cancel_goal_async()

    def _update_rec_label(self) -> None:
        if self._recording:
            elapsed = int(time.monotonic() - self._record_start)
            m, s = divmod(elapsed, 60)
            self._rec_label.setText(f'⏱ {m:02d}:{s:02d}')
        else:
            self._rec_btn.setText('● Record')
            self._rec_btn.setStyleSheet(_btn_style())

    # ── Qt timer — plot update ────────────────────────────────────────────────

    def _update_plot(self) -> None:
        with self._lock:
            smsg = self._spectrum_msg
            self._spectrum_msg = None
            dmsg = self._detections_msg
            emitter_map = self._emitter_map_msg
            cyclo_msg = self._cyclo_detections_msg
            sweep_res = self._sweep_result
            self._sweep_result = None

        redraw = False

        # ── Sweep button / status ──────────────────────────────────────────
        while True:
            try:
                hop, total, freq_hz = self._sweep_feedback_q.get_nowait()
                self._sweep_progress.setMaximum(max(total, 1))
                self._sweep_progress.setValue(hop)
                self._sweep_status.setText(f'{freq_hz/1e6:.0f} MHz')
            except queue.Empty:
                break

        if not self._sweeping:
            self._sweep_btn.setText('Sweep')
            self._sweep_btn.setStyleSheet(_btn_style(_BTN))
            if not self._sweep_status.text().startswith(('No', 'Fail', 'Cancel')):
                self._sweep_status.setText(
                    'Done' if self._sweep_progress.value() > 0 else 'Ready')

        # ── Continuous label ───────────────────────────────────────────────
        if not self._sweeping and self._sweep_cont_btn.text() == 'Stop':
            self._sweep_cont_btn.setText('Continuous')
            self._sweep_cont_btn.setStyleSheet(_btn_style(_BTN))

        # Hide live PSD while sweeping and until tune/clear resets the flag
        live_visible = not self._hide_live_psd
        if self._live_line.get_visible() != live_visible:
            self._live_line.set_visible(live_visible)
            if self._live_fill is not None:
                self._live_fill.set_visible(live_visible)
            redraw = True

        # ── Draw sweep overlay ─────────────────────────────────────────────
        if sweep_res is not None:
            freqs_mhz, psd = sweep_res
            if self._sweep_line is not None:
                self._sweep_line.remove()
            if self._sweep_fill is not None:
                self._sweep_fill.remove()
            self._sweep_line, = self._ax.plot(
                freqs_mhz, psd, color=_ORG, linewidth=0.8,
                alpha=0.85, label='Sweep', zorder=3)
            self._sweep_fill = self._ax.fill_between(
                freqs_mhz, -140, psd, alpha=0.08, color=_ORG, zorder=2)
            self._ax.legend(facecolor=_BG, edgecolor=_GRID, labelcolor=_FG,
                            fontsize=8, loc='upper right')
            self._ax.set_xlim(freqs_mhz[0], freqs_mhz[-1])
            redraw = True

        # ── Live PSD (only update data when visible) ───────────────────────
        if smsg is not None and live_visible:
            psd = np.array(smsg.psd_db, dtype=np.float32)
            center = smsg.center_frequency_hz
            rate   = smsg.sample_rate_hz
            freqs_mhz = np.linspace(
                (center - rate / 2) / 1e6,
                (center + rate / 2) / 1e6,
                len(psd))

            if center != self._prev_center or rate != self._prev_rate:
                self._prev_center, self._prev_rate = center, rate
                if self._sweep_line is None:
                    self._ax.set_xlim(freqs_mhz[0], freqs_mhz[-1])

            self._live_line.set_xdata(freqs_mhz)
            self._live_line.set_ydata(psd)

            if self._live_fill is not None:
                self._live_fill.remove()
            self._live_fill = self._ax.fill_between(
                freqs_mhz, self._y_min, psd,
                alpha=0.12, color=_GRN, zorder=1,
                visible=live_visible)

            d_min = float(np.percentile(psd, 2))
            d_max = float(np.percentile(psd, 99.5))
            self._y_min += 0.15 * (d_min - 5 - self._y_min)
            self._y_max += 0.15 * (d_max + 5 - self._y_max)
            if self._y_max - self._y_min < 20:
                mid = (self._y_max + self._y_min) / 2
                self._y_min, self._y_max = mid - 10, mid + 10
            self._ax.set_ylim(self._y_min, self._y_max)

            floor = float(smsg.noise_floor_db)
            peak_db = float(psd.max())
            peak_mhz = float(freqs_mhz[np.argmax(psd)])
            self._ax.set_title(
                f'{center/1e6:.1f} MHz  |  {rate/1e6:.0f} MSPS  |  '
                f'Peak: {peak_db:.1f} dBFS @ {peak_mhz:.1f} MHz  |  '
                f'Floor: {floor:.1f} dBFS',
                color=_FG, fontsize=10)
            redraw = True

        # ── Detection bounding boxes ───────────────────────────────────────
        if dmsg is not None:
            for p in self._det_patches:
                p.remove()
            for t in self._det_labels:
                t.remove()
            self._det_patches.clear()
            self._det_labels.clear()

            y_bot = self._y_min
            y_top = self._y_max
            span_h = y_top - y_bot

            # Build cyclo lookup: detection_id → (classification, confidence)
            cyclo_by_id: dict[int, tuple[str, float]] = {}
            if cyclo_msg is not None:
                for cd in cyclo_msg.detections:
                    if cd.cyclo_classification:
                        cyclo_by_id[cd.detection_id] = (
                            cd.cyclo_classification, cd.cyclo_confidence)

            anomaly_types: list[str] = []
            cyclo_counts: dict[str, int] = {}

            for det in dmsg.detections:
                c_mhz  = det.center_frequency_hz / 1e6
                bw_mhz = max(det.bandwidth_hz / 1e6, 0.1)
                lo = c_mhz - bw_mhz / 2
                hi = c_mhz + bw_mhz / 2

                is_anomaly = getattr(det, 'is_anomaly', False)
                anomaly_type = getattr(det, 'anomaly_type', '')

                # Anomaly: RED border, higher opacity; normal: classification color
                if is_anomaly:
                    clr = _RED
                    rect_alpha = 0.28
                    edge_lw = 2.0
                    if anomaly_type:
                        anomaly_types.append(anomaly_type)
                else:
                    clr = _CLASSIFY_COLOR.get(det.classification, '#6c7086')
                    rect_alpha = 0.18
                    edge_lw = 1.2

                rect = Rectangle(
                    (lo, y_bot), hi - lo, span_h,
                    linewidth=edge_lw, edgecolor=clr, facecolor=clr,
                    alpha=rect_alpha, zorder=4)
                self._ax.add_patch(rect)
                self._det_patches.append(rect)

                edge = self._ax.axvline(
                    c_mhz, color=clr, linewidth=0.8 if not is_anomaly else 1.2,
                    alpha=0.6, zorder=5)
                self._det_patches.append(edge)

                # Label: base classification, cyclo override, anomaly flag
                cyclo_info = cyclo_by_id.get(det.detection_id)
                if cyclo_info:
                    cyclo_cls, cyclo_conf = cyclo_info
                    cyclo_counts[cyclo_cls] = cyclo_counts.get(cyclo_cls, 0) + 1
                    band_line = f'{cyclo_cls} ({cyclo_conf:.0%})'
                else:
                    band_line = det.classification

                prefix = '⚠ ' if is_anomaly else ''
                lbl_text = f'{prefix}{band_line}\n{det.power_dbm:.0f} dBm'

                lbl = self._ax.text(
                    c_mhz, y_top - span_h * 0.06,
                    lbl_text,
                    color=clr, fontsize=7, ha='center', va='top',
                    zorder=6, clip_on=True,
                    bbox=dict(boxstyle='round,pad=0.2', facecolor=_BG,
                              edgecolor=clr, alpha=0.8, linewidth=0.8))
                self._det_labels.append(lbl)

            # ── Intel panel: anomaly summary ───────────────────────────
            n_anom = len(anomaly_types)
            if n_anom == 0:
                anom_txt = 'Anomalies: none'
                self._intel_anomaly_lbl.setStyleSheet(
                    f'color: {_FG}; font-size: 11px;')
            else:
                unique = ', '.join(sorted(set(anomaly_types)))
                anom_txt = f'⚠ Anomalies: {n_anom}  [{unique}]'
                self._intel_anomaly_lbl.setStyleSheet(
                    f'color: {_RED}; font-size: 11px; font-weight: bold;')
            self._intel_anomaly_lbl.setText(anom_txt)

            # ── Intel panel: cyclo summary ─────────────────────────────
            if cyclo_counts:
                parts = [f'{k}×{v}' for k, v in sorted(cyclo_counts.items())]
                self._intel_cyclo_lbl.setText('Cyclo: ' + '  '.join(parts))
            elif cyclo_msg is not None:
                self._intel_cyclo_lbl.setText('Cyclo: no ISM signals')
            else:
                self._intel_cyclo_lbl.setText('Cyclo: —')

            redraw = True

        # ── Intel panel: emitter map ───────────────────────────────────────
        if emitter_map is not None:
            ests = emitter_map.estimates
            if not ests:
                self._intel_emitter_lbl.setText('Emitters: none')
            else:
                lines = [f'Emitters: {len(ests)} located']
                for e in ests:
                    lines.append(
                        f'  #{e.detection_id}: '
                        f'({e.estimated_x:.1f}, {e.estimated_y:.1f}) m'
                        f'  n={e.observation_count}')
                self._intel_emitter_lbl.setText('\n'.join(lines))

        if redraw:
            self._canvas.draw()

    # ── rqt lifecycle ─────────────────────────────────────────────────────────

    def shutdown_plugin(self) -> None:
        self._sweep_continuous = False
        self._plot_timer.stop()
        self._rec_timer.stop()
        self._executor.shutdown(timeout_sec=1.0)
        self._node.destroy_node()

    def save_settings(self, plugin_settings, instance_settings) -> None:
        instance_settings.set_value('tune_freq',   self._tune_freq.value())
        instance_settings.set_value('sweep_min',   self._sweep_min.value())
        instance_settings.set_value('sweep_max',   self._sweep_max.value())
        instance_settings.set_value('sweep_step',  self._sweep_step.value())
        instance_settings.set_value('rec_path',    self._rec_path.text())

    def restore_settings(self, plugin_settings, instance_settings) -> None:
        self._tune_freq.setValue(float(instance_settings.value('tune_freq', 2437)))
        self._sweep_min.setValue(float(instance_settings.value('sweep_min', 2400)))
        self._sweep_max.setValue(float(instance_settings.value('sweep_max', 2500)))
        self._sweep_step.setValue(float(instance_settings.value('sweep_step', 20)))
        self._rec_path.setText(instance_settings.value('rec_path', '/tmp/hackrf_recording'))
