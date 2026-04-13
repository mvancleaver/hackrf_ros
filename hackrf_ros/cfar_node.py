"""CA-CFAR RF Detection Node.

Subscribes to /hackrf/spectrum (SpectrumStamped), runs CA-CFAR energy detection,
groups adjacent bins into signal clusters, applies persistence tracking and band
classification, publishes RFDetectionArray and RFEnvironment.

References:
    - Holik eq 11.39: T_m = M * (Pfa^(-1/M) - 1)
    - scipy.ndimage.convolve1d for sliding noise estimate
    - scipy.ndimage.label for connected-component bin grouping
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import convolve1d, label as ndimage_label

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from rclpy.callback_groups import ReentrantCallbackGroup

from hackrf_interfaces.msg import (
    SpectrumStamped, RFDetection, RFDetectionArray, RFEnvironment,
)

# ---------------------------------------------------------------------------
# Band classification lookup table (D-10, D-11, DET-05, DET-06)
# (freq_lo_hz, freq_hi_hz, bw_min_hz, bw_max_hz, label)
# Entries are checked in order; first match wins.  None = wildcard.
# ---------------------------------------------------------------------------
_BAND_TABLE: list[tuple[float, float, float | None, float | None, str]] = [
    # FM broadcast
    (87.5e6,   108e6,     50e3,    300e3,   'fm_radio'),
    (87.5e6,   108e6,     None,    None,    'fm_radio'),
    # LTE low bands
    (698e6,    758e6,     None,    None,    'lte_b12_b13'),
    (758e6,    787e6,     None,    None,    'lte_b13'),
    # CDMA cellular
    (824e6,    894e6,     None,    None,    'cdma_cellular'),
    # ISM 900 / LTE B5
    (902e6,    928e6,     None,    500e3,   'ism_900'),
    (902e6,    928e6,     500e3,   None,    'lte_b5'),
    # GPS L1
    (1573.42e6, 1577.42e6, None,  None,    'gps_l1'),
    # DECT (narrowband check first)
    (1880e6,   1900e6,    None,    1e6,     'dect'),
    # LTE mid bands
    (1710e6,   2100e6,    None,    None,    'lte_b4_b66'),
    (1850e6,   1990e6,    None,    None,    'lte_b2'),
    # 2.4 GHz ISM: WiFi vs BLE vs ZigBee via bandwidth heuristic (D-11)
    (2400e6,   2483.5e6,  15e6,    None,    'wifi_2_4g'),
    (2400e6,   2483.5e6,  1e6,     15e6,    'ble'),
    (2400e6,   2483.5e6,  None,    1e6,     'zigbee'),
    (2400e6,   2483.5e6,  None,    None,    'ism_2400'),
    # LTE B7
    (2500e6,   2700e6,    None,    None,    'lte_b7'),
    # WiFi 5 GHz
    (5150e6,   5850e6,    None,    None,    'wifi_5g'),
    # ISM 5.8 GHz
    (5725e6,   5875e6,    None,    None,    'ism_5800'),
]


class CFARNode(Node):
    """ROS2 node implementing CA-CFAR detection with persistence and classification."""

    def __init__(self) -> None:
        super().__init__('cfar_node')

        # ---- ROS2 parameters (D-01) ----
        self.declare_parameter('pfa', 1e-4)
        self.declare_parameter('guard_cells', 8)
        self.declare_parameter('train_cells', 32)
        self.declare_parameter('persistence_n', 3)
        self.declare_parameter('persistence_decay', 5)
        self.declare_parameter('antenna_frame', 'hackrf_antenna')

        # Cache params
        self._pfa: float = self.get_parameter('pfa').value
        self._guard: int = int(self.get_parameter('guard_cells').value)
        self._train: int = int(self.get_parameter('train_cells').value)

        # ---- Persistence tracker state (DET-03, DET-04) ----
        self._tracks: dict[int, dict] = {}  # id -> {center_hz, bw_hz, frames, absent}
        self._next_id: int = 1

        # ---- QoS profiles ----
        cb_group = ReentrantCallbackGroup()

        qos_best_effort = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )
        qos_reliable = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )

        # ---- Subscription ----
        self._spectrum_sub = self.create_subscription(
            SpectrumStamped,
            '/hackrf/spectrum',
            self._spectrum_callback,
            qos_reliable,
            callback_group=cb_group,
        )

        # ---- Publishers ----
        self._detections_pub = self.create_publisher(
            RFDetectionArray,
            '/hackrf/detections',
            qos_reliable,
        )
        self._env_pub = self.create_publisher(
            RFEnvironment,
            '/hackrf/rf_environment',
            qos_reliable,
        )

        # ---- 1 Hz environment timer ----
        self._last_arr: RFDetectionArray | None = None
        self.create_timer(1.0, self._env_timer_callback, callback_group=cb_group)

        self.get_logger().info(
            f'CFAR node started  pfa={self._pfa}  guard={self._guard}  '
            f'train={self._train}'
        )

    # ------------------------------------------------------------------
    # CA-CFAR core (DET-01)
    # ------------------------------------------------------------------
    @staticmethod
    def _ca_cfar(
        psd_db: np.ndarray,
        guard: int,
        train: int,
        pfa: float,
    ) -> np.ndarray:
        """Cell-averaging CFAR detector.

        Args:
            psd_db: Power spectral density in dB (1-D float64 array).
            guard: Number of guard cells on each side of CUT.
            train: Number of training (reference) cells on each side.
            pfa: Probability of false alarm.

        Returns:
            Boolean mask of detected bins (same length as psd_db).
        """
        n = len(psd_db)
        min_len = 2 * (train + guard) + 1
        if n < min_len:
            return np.zeros(n, dtype=bool)

        # Work in linear domain
        psd_lin = np.power(10.0, psd_db / 10.0)

        # Build sliding-window kernel: [train ... | guard | CUT | guard | ... train]
        kernel = np.zeros(min_len, dtype=np.float64)
        kernel[:train] = 1.0
        kernel[-train:] = 1.0
        m_total = 2 * train  # M — total reference cells
        kernel /= m_total

        # Sliding noise estimate
        mu = convolve1d(psd_lin, kernel, mode='nearest')

        # Threshold multiplier — Holik eq 11.39
        T_m = m_total * (pfa ** (-1.0 / m_total) - 1.0)

        return psd_lin > (T_m * mu)

    # ------------------------------------------------------------------
    # Signal clustering (DET-02)
    # ------------------------------------------------------------------
    @staticmethod
    def _group_detections(
        mask: np.ndarray,
        psd_db: np.ndarray,
        bin_width_hz: float,
        center_freq_hz: float,
        noise_floor_db: float,
    ) -> list[tuple[float, float, float, float]]:
        """Group adjacent detected bins into signal clusters.

        Returns:
            List of (centroid_hz, bandwidth_hz, peak_dbm, snr_db) tuples.
        """
        if not np.any(mask):
            return []

        labeled, n_clusters = ndimage_label(mask)
        n_bins = len(psd_db)
        half = n_bins // 2

        results: list[tuple[float, float, float, float]] = []
        for cid in range(1, n_clusters + 1):
            bins = np.where(labeled == cid)[0]
            if len(bins) == 0:
                continue

            # Frequency axis (DC-centered: bin 0 = lowest freq)
            freqs = (bins.astype(np.float64) - half) * bin_width_hz + center_freq_hz

            # Peak power
            peak_idx = bins[np.argmax(psd_db[bins])]
            peak_dbm = float(psd_db[peak_idx])

            # Power-weighted centroid frequency
            weights = np.power(10.0, psd_db[bins] / 10.0)
            centroid_hz = float(np.sum(freqs * weights) / np.sum(weights))

            # 3 dB bandwidth: bins within 3 dB of peak
            bw_mask = psd_db[bins] >= (peak_dbm - 3.0)
            bw_bins_count = max(1, int(np.sum(bw_mask)))
            bw_hz = float(bw_bins_count * bin_width_hz)

            # SNR relative to noise floor
            snr_db = peak_dbm - noise_floor_db

            results.append((centroid_hz, bw_hz, peak_dbm, snr_db))

        return results

    # ------------------------------------------------------------------
    # Persistence tracker (DET-03, DET-04)
    # ------------------------------------------------------------------
    def _update_persistence(
        self, raw_detections: list[tuple[float, float, float, float]],
    ) -> list[RFDetection]:
        """Match raw detections to existing tracks, manage IDs and persistence.

        Args:
            raw_detections: List of (centroid_hz, bw_hz, peak_dbm, snr_db).

        Returns:
            List of RFDetection messages for confirmed (persistent) detections.
        """
        persistence_n = int(self.get_parameter('persistence_n').value)
        persistence_decay = max(1, int(self.get_parameter('persistence_decay').value))

        matched_track_ids: set[int] = set()

        for centroid_hz, bw_hz, peak_dbm, snr_db in raw_detections:
            best_id: int | None = None
            best_dist = float('inf')

            for tid, track in self._tracks.items():
                tolerance = 0.5 * max(track['bw_hz'], bw_hz)
                dist = abs(track['center_hz'] - centroid_hz)
                if dist < tolerance and dist < best_dist:
                    best_dist = dist
                    best_id = tid

            if best_id is not None:
                # Update existing track with power-weighted center
                t = self._tracks[best_id]
                old_w = np.power(10.0, t.get('peak_dbm', peak_dbm) / 10.0)
                new_w = np.power(10.0, peak_dbm / 10.0)
                total_w = old_w + new_w
                t['center_hz'] = (t['center_hz'] * old_w + centroid_hz * new_w) / total_w
                t['bw_hz'] = bw_hz
                t['peak_dbm'] = peak_dbm
                t['snr_db'] = snr_db
                t['frames'] += 1
                t['absent'] = 0
                matched_track_ids.add(best_id)
            else:
                # New track
                tid = self._next_id
                self._next_id += 1
                self._tracks[tid] = {
                    'center_hz': centroid_hz,
                    'bw_hz': bw_hz,
                    'peak_dbm': peak_dbm,
                    'snr_db': snr_db,
                    'frames': 1,
                    'absent': 0,
                }
                matched_track_ids.add(tid)

        # Decay unmatched tracks
        remove_ids: list[int] = []
        for tid, track in self._tracks.items():
            if tid not in matched_track_ids:
                track['absent'] += 1
                if track['absent'] >= persistence_decay:
                    remove_ids.append(tid)
        for tid in remove_ids:
            del self._tracks[tid]

        # Build confirmed detections (frames >= persistence_n)
        confirmed: list[RFDetection] = []
        for tid, track in self._tracks.items():
            if track['frames'] >= persistence_n:
                det = RFDetection()
                det.center_frequency_hz = track['center_hz']
                det.bandwidth_hz = track['bw_hz']
                det.power_dbm = float(track['peak_dbm'])
                det.snr_db = float(track['snr_db'])
                det.classification = ''  # filled by caller
                det.persistence_frames = track['frames']
                det.detection_id = tid
                confirmed.append(det)

        return confirmed

    # ------------------------------------------------------------------
    # Band classification (DET-05, DET-06)
    # ------------------------------------------------------------------
    @staticmethod
    def _classify_band(center_hz: float, bw_hz: float) -> str:
        """Classify a detection by frequency and bandwidth using lookup table.

        Returns:
            Band label string (e.g. 'wifi_2_4g', 'ble', 'unknown').
        """
        for freq_lo, freq_hi, bw_min, bw_max, label in _BAND_TABLE:
            if not (freq_lo <= center_hz <= freq_hi):
                continue
            if bw_min is not None and bw_hz < bw_min:
                continue
            if bw_max is not None and bw_hz > bw_max:
                continue
            return label
        return 'unknown'

    # ------------------------------------------------------------------
    # Spectrum callback — main pipeline
    # ------------------------------------------------------------------
    def _spectrum_callback(self, msg: SpectrumStamped) -> None:
        """Process incoming spectrum: CFAR -> cluster -> persist -> publish."""
        # Refresh params
        self._pfa = self.get_parameter('pfa').value
        self._guard = int(self.get_parameter('guard_cells').value)
        self._train = int(self.get_parameter('train_cells').value)

        psd_db = np.array(msg.psd_db, dtype=np.float64)

        # T-03-01: guard against zero-length psd_db
        if len(psd_db) == 0:
            return

        # T-03-02: guard against train+guard exceeding array
        if len(psd_db) <= 2 * (self._train + self._guard) + 1:
            self.get_logger().warn(
                f'PSD length {len(psd_db)} too short for '
                f'train={self._train} guard={self._guard}; skipping'
            )
            return

        # Pass 1: CA-CFAR
        mask = self._ca_cfar(psd_db, self._guard, self._train, self._pfa)

        # Group detected bins into signal clusters
        raw_dets = self._group_detections(
            mask, psd_db, msg.bin_width_hz, msg.center_frequency_hz,
            msg.noise_floor_db,
        )

        # Persistence tracking
        confirmed = self._update_persistence(raw_dets)

        # Band classification
        for det in confirmed:
            det.classification = self._classify_band(
                det.center_frequency_hz, det.bandwidth_hz,
            )

        # Build and publish RFDetectionArray
        arr_msg = RFDetectionArray()
        arr_msg.header.stamp = msg.header.stamp
        arr_msg.header.frame_id = msg.header.frame_id
        arr_msg.noise_floor_dbm = float(msg.noise_floor_db)
        n_bins = len(psd_db)
        det_bins = int(np.sum(mask))
        arr_msg.occupancy_pct = float(det_bins / n_bins * 100.0) if n_bins > 0 else 0.0
        arr_msg.detections = confirmed
        self._detections_pub.publish(arr_msg)

        # Cache for 1 Hz environment publisher
        self._last_arr = arr_msg

    # ------------------------------------------------------------------
    # 1 Hz RFEnvironment timer (D-06)
    # ------------------------------------------------------------------
    def _env_timer_callback(self) -> None:
        """Publish periodic RF environment summary."""
        if self._last_arr is None:
            return
        arr = self._last_arr

        env = RFEnvironment()
        env.header.stamp = self.get_clock().now().to_msg()
        env.header.frame_id = self.get_parameter('antenna_frame').value
        env.emitter_count = len(arr.detections)
        env.occupancy_pct = arr.occupancy_pct
        env.noise_floor_dbm = float(arr.noise_floor_dbm)

        if arr.detections:
            powers = [d.power_dbm for d in arr.detections]
            idx = int(np.argmax(powers))
            env.peak_power_dbm = powers[idx]
            env.peak_frequency_hz = arr.detections[idx].center_frequency_hz

        self._env_pub.publish(env)


def main(args=None):
    """Entry point for cfar_node."""
    rclpy.init(args=args)
    node = CFARNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
