"""Real-time spectrum (PSD) widget built on pyqtgraph.

Displays power (dBm) vs frequency (MHz) for one or both RX channels, with peak
hold, min hold, averaging, clickable markers and a right-click context menu.

All heavy DSP happens elsewhere; this widget only renders arrays handed to it
via :meth:`update_spectrum`, which is safe to call from the GUI thread (connect
it to :class:`ui.qt_adapter.AppSignals.spectrum`).
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QMenu, QVBoxLayout, QWidget

pg.setConfigOptions(antialias=False, useOpenGL=False, background="#12192e",
                    foreground="#b8bdd0")

RX_COLORS = {0: "#33ffff", 1: "#ffd633"}  # cyan / yellow


class SpectrumWidget(QWidget):
    """PSD line plot with peak/min hold, averaging and markers."""

    #: Emitted (freq_hz) when the user requests RX1 to tune here.
    tune_rx1_requested = pyqtSignal(float)
    #: Emitted (freq_hz) when the user requests RX0 to tune here.
    tune_rx0_requested = pyqtSignal(float)
    #: Emitted (freq_hz, bw_hz) to add current point to known signals.
    add_signal_requested = pyqtSignal(float, float)
    #: Emitted (freq_hz) to set a baseline reference.
    baseline_ref_requested = pyqtSignal(float)
    #: Emitted (freq_hz) whenever a marker is placed.
    marker_placed = pyqtSignal(float)

    def __init__(self, title: str = "Spectrum", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._title = title
        self._peak_hold = False
        self._min_hold = False
        self._avg_count = 1

        self._peak: Optional[np.ndarray] = None
        self._min: Optional[np.ndarray] = None
        self._avg_buf: list[np.ndarray] = []
        self._last_center = 100e6
        self._last_fs = 2.048e6

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Frequency", units="MHz")
        self.plot.setLabel("left", "Power", units="dBm")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setYRange(-120, -10)
        self.plot.setMouseEnabled(x=True, y=True)
        self.plot.setMenuEnabled(False)
        layout.addWidget(self.plot)

        self._curves: Dict[int, pg.PlotDataItem] = {}
        self.peak_curve = self.plot.plot(pen=pg.mkPen("#ff5555", width=1))
        self.min_curve = self.plot.plot(pen=pg.mkPen("#5599ff", width=1))
        self.avg_curve = self.plot.plot(pen=pg.mkPen("#55ff88", width=1))
        self.peak_curve.setVisible(False)
        self.min_curve.setVisible(False)
        self.avg_curve.setVisible(False)

        # Marker: vertical line + text.
        self.marker = pg.InfiniteLine(angle=90, movable=False,
                                      pen=pg.mkPen("#ffffff", width=1,
                                                   style=Qt.PenStyle.DashLine))
        self.marker_text = pg.TextItem(color="#ffffff", anchor=(0, 1))
        self.plot.addItem(self.marker)
        self.plot.addItem(self.marker_text)
        self.marker.setVisible(False)
        self.marker_text.setVisible(False)

        self.plot.scene().sigMouseClicked.connect(self._on_mouse_clicked)

    # ------------------------------------------------------------------
    # Public rendering API
    # ------------------------------------------------------------------
    def _curve_for(self, channel: int) -> pg.PlotDataItem:
        if channel not in self._curves:
            color = RX_COLORS.get(channel, "#ffffff")
            self._curves[channel] = self.plot.plot(pen=pg.mkPen(color, width=1))
        return self._curves[channel]

    def update_spectrum(self, frame: dict) -> None:
        """Render a spectrum frame dict (see AppState._on_spectrum)."""
        try:
            channel = int(frame.get("channel", 0))
            center = float(frame.get("center_freq", self._last_center))
            fs = float(frame.get("sample_rate", self._last_fs))
            psd = np.asarray(frame.get("psd_db", []), dtype=float)
            if psd.size == 0:
                return
        except Exception:  # noqa: BLE001
            return
        self._last_center, self._last_fs = center, fs
        freqs_mhz = (center - fs / 2.0 + np.arange(psd.size) * fs / psd.size) / 1e6
        self._curve_for(channel).setData(freqs_mhz, psd)

        # Traces derived from channel 0 only (scanner).
        if channel == 0:
            self._update_holds(freqs_mhz, psd)

    def _update_holds(self, freqs_mhz: np.ndarray, psd: np.ndarray) -> None:
        if self._peak_hold:
            self._peak = psd if self._peak is None or self._peak.size != psd.size \
                else np.maximum(self._peak, psd)
            self.peak_curve.setData(freqs_mhz, self._peak)
        if self._min_hold:
            self._min = psd if self._min is None or self._min.size != psd.size \
                else np.minimum(self._min, psd)
            self.min_curve.setData(freqs_mhz, self._min)
        if self._avg_count > 1:
            self._avg_buf.append(psd)
            if len(self._avg_buf) > self._avg_count:
                self._avg_buf.pop(0)
            if all(a.size == psd.size for a in self._avg_buf):
                self.avg_curve.setData(freqs_mhz, np.mean(self._avg_buf, axis=0))

    # ------------------------------------------------------------------
    # Trace toggles
    # ------------------------------------------------------------------
    def set_peak_hold(self, on: bool) -> None:
        self._peak_hold = on
        self._peak = None
        self.peak_curve.setVisible(on)

    def set_min_hold(self, on: bool) -> None:
        self._min_hold = on
        self._min = None
        self.min_curve.setVisible(on)

    def set_avg_count(self, count: int) -> None:
        self._avg_count = max(1, int(count))
        self._avg_buf.clear()
        self.avg_curve.setVisible(self._avg_count > 1)

    def set_ref_level(self, top_dbm: float, range_db: float = 110.0) -> None:
        self.plot.setYRange(top_dbm - range_db, top_dbm)

    def set_span(self, center_mhz: float, span_mhz: float) -> None:
        self.plot.setXRange(center_mhz - span_mhz / 2, center_mhz + span_mhz / 2)

    def clear_holds(self) -> None:
        self._peak = self._min = None
        self._avg_buf.clear()

    # ------------------------------------------------------------------
    # Mouse / context menu
    # ------------------------------------------------------------------
    def _scene_to_freq_hz(self, scene_pos) -> Optional[float]:
        vb = self.plot.getPlotItem().vb
        if not self.plot.sceneBoundingRect().contains(scene_pos):
            return None
        mouse_pt = vb.mapSceneToView(scene_pos)
        return float(mouse_pt.x() * 1e6)

    def _on_mouse_clicked(self, ev) -> None:
        freq_hz = self._scene_to_freq_hz(ev.scenePos())
        if freq_hz is None:
            return
        if ev.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(ev, freq_hz)
            return
        if ev.button() == Qt.MouseButton.LeftButton:
            self._place_marker(freq_hz)

    def _place_marker(self, freq_hz: float) -> None:
        self.marker.setValue(freq_hz / 1e6)
        self.marker.setVisible(True)
        self.marker_text.setVisible(True)
        self.marker_text.setText(f"{freq_hz/1e6:.4f} MHz")
        self.marker_text.setPos(freq_hz / 1e6,
                                self.plot.getPlotItem().vb.viewRange()[1][1])
        self.marker_placed.emit(freq_hz)

    def _show_context_menu(self, ev, freq_hz: float) -> None:
        menu = QMenu(self)
        menu.addAction(f"Marker @ {freq_hz/1e6:.4f} MHz").setEnabled(False)
        menu.addSeparator()
        menu.addAction("Tune RX0 here",
                       lambda: self.tune_rx0_requested.emit(freq_hz))
        menu.addAction("Tune RX1 here",
                       lambda: self.tune_rx1_requested.emit(freq_hz))
        menu.addAction("Add to Known Signals",
                       lambda: self.add_signal_requested.emit(freq_hz, 25e3))
        menu.addAction("Measure bandwidth",
                       lambda: self._place_marker(freq_hz))
        menu.addAction("Set as baseline reference",
                       lambda: self.baseline_ref_requested.emit(freq_hz))
        menu.exec(ev.screenPos().toPoint())
