"""Oscilloscope-style time-domain IQ display + constellation + analysis views.

Used in the "dual signal view" mode where both RX are parked on specific
signals. Renders:

* I (blue) and Q (red) time traces with a trigger line and 4 movable
  measurement markers (with delta-time / delta-freq readouts),
* a selectable analysis plot: constellation (with persistence), eye diagram,
  instantaneous phase, or FM frequency deviation,
* a windowed FFT with automatic peak annotations,
* live measurements (dominant frequency, period, amplitude, RMS).

The public API is ``update_iq(iq, fs)``; everything else is internal.
"""
from __future__ import annotations

from collections import deque
from typing import List, Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
                             QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                             QVBoxLayout, QWidget)


class ScopeWidget(QWidget):
    """Time-domain IQ scope with constellation, eye, phase and freq-dev views."""

    MARKER_COLORS = ["#ffcc00", "#00ffcc", "#ff66cc", "#66ccff"]

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._fs = 2.048e6
        self._trigger_mode = "auto"
        self._trigger_level = 0.0
        self._persist_frames: deque = deque(maxlen=12)
        self._last_iq: Optional[np.ndarray] = None
        self._peak_texts: List[pg.TextItem] = []

        root = QHBoxLayout(self)

        # --- Left: time-domain + analysis plot stacked ---
        left = QVBoxLayout()
        self.time_plot = pg.PlotWidget(title="Time Domain (IQ)")
        self.time_plot.setLabel("bottom", "Time", units="s")
        self.time_plot.setLabel("left", "Amplitude")
        self.time_plot.addLegend()
        self.i_curve = self.time_plot.plot(pen=pg.mkPen("#3399ff", width=1),
                                           name="I")
        self.q_curve = self.time_plot.plot(pen=pg.mkPen("#ff5555", width=1),
                                           name="Q")
        self.trig_line = pg.InfiniteLine(angle=0, movable=True,
                                         pen=pg.mkPen("#ffff00"))
        self.time_plot.addItem(self.trig_line)

        # 4 movable vertical measurement markers (hidden until enabled).
        self.markers: List[pg.InfiniteLine] = []
        for c in self.MARKER_COLORS:
            m = pg.InfiniteLine(angle=90, movable=True,
                                pen=pg.mkPen(c, width=1,
                                             style=Qt.PenStyle.DashLine))
            m.setVisible(False)
            m.sigPositionChanged.connect(self._update_marker_readout)
            self.time_plot.addItem(m)
            self.markers.append(m)
        left.addWidget(self.time_plot, stretch=3)

        # Analysis plot (mode-selectable).
        self.analysis_plot = pg.PlotWidget(title="Constellation (I vs Q)")
        self.analysis_plot.setAspectLocked(True)
        self.const_scatter = pg.ScatterPlotItem(size=2, brush=pg.mkBrush(
            51, 255, 204, 160), pen=None)
        self.persist_scatter = pg.ScatterPlotItem(size=2, brush=pg.mkBrush(
            51, 255, 204, 40), pen=None)
        self.analysis_plot.addItem(self.persist_scatter)
        self.analysis_plot.addItem(self.const_scatter)
        self.analysis_curve = self.analysis_plot.plot(
            pen=pg.mkPen("#33ffcc", width=1))
        self.analysis_curve.setVisible(False)
        left.addWidget(self.analysis_plot, stretch=2)
        root.addLayout(left, stretch=3)

        # --- Right: controls + FFT + measurements ---
        right = QVBoxLayout()

        disp = QGroupBox("Analysis view")
        dform = QFormLayout(disp)
        self.view_mode = QComboBox()
        self.view_mode.addItems(["Constellation", "Eye Diagram",
                                 "Phase", "Freq Deviation"])
        self.view_mode.currentTextChanged.connect(self._view_changed)
        dform.addRow("Mode", self.view_mode)
        self.persist_chk = QCheckBox("Constellation persistence")
        self.persist_chk.setChecked(True)
        dform.addRow(self.persist_chk)
        self.markers_chk = QCheckBox("Show 4 markers")
        self.markers_chk.toggled.connect(self._toggle_markers)
        dform.addRow(self.markers_chk)
        self.peaks_chk = QCheckBox("Annotate FFT peaks")
        self.peaks_chk.setChecked(True)
        dform.addRow(self.peaks_chk)
        right.addWidget(disp)

        trg = QGroupBox("Trigger")
        trg_form = QFormLayout(trg)
        self.trig_mode = QComboBox()
        self.trig_mode.addItems(["auto", "normal", "single"])
        self.trig_mode.currentTextChanged.connect(
            lambda m: setattr(self, "_trigger_mode", m))
        trg_form.addRow("Mode", self.trig_mode)
        self.trig_edge = QComboBox()
        self.trig_edge.addItems(["rising", "falling"])
        trg_form.addRow("Edge", self.trig_edge)
        self.trig_level = QDoubleSpinBox()
        self.trig_level.setRange(-2.0, 2.0)
        self.trig_level.setSingleStep(0.05)
        self.trig_level.valueChanged.connect(self._set_trig_level)
        trg_form.addRow("Level", self.trig_level)
        self.timediv = QComboBox()
        self.timediv.addItems(["1 us", "5 us", "10 us", "50 us", "100 us",
                               "500 us", "1 ms"])
        trg_form.addRow("Time/div", self.timediv)
        right.addWidget(trg)

        self.fft_plot = pg.PlotWidget(title="Window FFT")
        self.fft_plot.setLabel("bottom", "Freq", units="Hz")
        self.fft_plot.setLabel("left", "dB")
        self.fft_curve = self.fft_plot.plot(pen=pg.mkPen("#55ff88", width=1))
        right.addWidget(self.fft_plot, stretch=2)

        meas = QGroupBox("Measurements")
        grid = QGridLayout(meas)
        self._meas_labels = {}
        keys = ("Frequency", "Period", "Amplitude", "RMS", "Markers")
        for i, key in enumerate(keys):
            grid.addWidget(QLabel(key + ":"), i, 0)
            lbl = QLabel("—")
            lbl.setProperty("readout", "true")
            self._meas_labels[key] = lbl
            grid.addWidget(lbl, i, 1)
        right.addWidget(meas)
        right.addStretch(1)
        root.addLayout(right, stretch=2)

    # ------------------------------------------------------------------
    def _set_trig_level(self, v: float) -> None:
        self._trigger_level = v
        self.trig_line.setValue(v)

    def _toggle_markers(self, on: bool) -> None:
        n = self._last_iq.size if self._last_iq is not None else 4096
        for idx, m in enumerate(self.markers):
            m.setVisible(on)
            if on:
                m.setValue((idx + 1) / (len(self.markers) + 1) * n / self._fs)
        self._update_marker_readout()

    def _update_marker_readout(self, *args) -> None:
        if not self.markers_chk.isChecked():
            self._meas_labels["Markers"].setText("—")
            return
        xs = sorted(m.value() for m in self.markers)
        dt = xs[-1] - xs[0]
        df = (1.0 / dt) if dt else 0.0
        self._meas_labels["Markers"].setText(
            f"Δt={dt*1e6:.2f} us  Δf={df/1e3:.2f} kHz")

    def _view_changed(self, mode: str) -> None:
        is_const = mode == "Constellation"
        self.const_scatter.setVisible(is_const)
        self.persist_scatter.setVisible(is_const and self.persist_chk.isChecked())
        self.analysis_curve.setVisible(not is_const)
        titles = {
            "Constellation": "Constellation (I vs Q)",
            "Eye Diagram": "Eye Diagram (I)",
            "Phase": "Instantaneous Phase",
            "Freq Deviation": "Frequency Deviation (FM)",
        }
        self.analysis_plot.setTitle(titles.get(mode, mode))
        self.analysis_plot.setAspectLocked(is_const)
        if self._last_iq is not None:
            self._update_analysis(self._last_iq)

    # ------------------------------------------------------------------
    def update_iq(self, iq: np.ndarray, fs: Optional[float] = None) -> None:
        """Render a block of complex IQ samples."""
        try:
            iq = np.asarray(iq)
            if iq.size == 0:
                return
            if fs:
                self._fs = fs
        except Exception:  # noqa: BLE001
            return
        n = min(iq.size, 4096)
        iq = iq[:n]
        self._last_iq = iq
        t = np.arange(n) / self._fs
        i = np.real(iq)
        q = np.imag(iq)
        self.i_curve.setData(t, i)
        self.q_curve.setData(t, q)

        self._update_analysis(iq)

        # FFT of window + peak annotation.
        win = np.hanning(n)
        spec = np.fft.fftshift(np.fft.fft(iq * win))
        psd = 20.0 * np.log10(np.abs(spec) + 1e-9)
        freqs = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / self._fs))
        self.fft_curve.setData(freqs, psd)
        self._annotate_peaks(freqs, psd)

        self._update_measurements(iq, i, q)

    def _update_analysis(self, iq: np.ndarray) -> None:
        mode = self.view_mode.currentText()
        n = iq.size
        i = np.real(iq)
        q = np.imag(iq)
        if mode == "Constellation":
            step = max(1, n // 1024)
            if self.persist_chk.isChecked():
                self.persist_scatter.setVisible(True)
                pts = np.concatenate(list(self._persist_frames)) \
                    if self._persist_frames else np.empty((0, 2))
                if pts.size:
                    self.persist_scatter.setData(pts[:, 0], pts[:, 1])
                frame = np.column_stack((i[::step], q[::step]))
                self._persist_frames.append(frame)
            else:
                self.persist_scatter.setVisible(False)
            self.const_scatter.setData(i[::step], q[::step])
        elif mode == "Eye Diagram":
            # Overlay short segments of I aligned to a symbol-ish span.
            span = max(8, n // 64)
            segs = n // span
            xs, ys = [], []
            tt = np.arange(span) / self._fs
            for s in range(min(segs, 200)):
                seg = i[s * span:(s + 1) * span]
                if seg.size == span:
                    xs.append(tt)
                    ys.append(seg)
            if xs:
                x = np.concatenate([np.append(a, np.nan) for a in xs])
                y = np.concatenate([np.append(b, np.nan) for b in ys])
                self.analysis_curve.setData(x, y, connect="finite")
        elif mode == "Phase":
            phase = np.unwrap(np.angle(iq))
            self.analysis_curve.setData(np.arange(n) / self._fs, phase)
        elif mode == "Freq Deviation":
            if n > 1:
                dphase = np.diff(np.unwrap(np.angle(iq)))
                inst_f = dphase * self._fs / (2 * np.pi)
                self.analysis_curve.setData(np.arange(inst_f.size) / self._fs,
                                            inst_f)

    def _annotate_peaks(self, freqs: np.ndarray, psd: np.ndarray,
                        max_peaks: int = 3) -> None:
        for txt in self._peak_texts:
            self.fft_plot.removeItem(txt)
        self._peak_texts.clear()
        if not self.peaks_chk.isChecked() or psd.size == 0:
            return
        order = np.argsort(psd)[::-1]
        chosen: List[int] = []
        min_sep = max(1, psd.size // 40)
        for idx in order:
            if all(abs(idx - c) > min_sep for c in chosen):
                chosen.append(int(idx))
            if len(chosen) >= max_peaks:
                break
        for idx in chosen:
            txt = pg.TextItem(f"{freqs[idx]/1e3:.1f} kHz\n{psd[idx]:.0f} dB",
                              color="#ffcc00", anchor=(0.5, 1.0))
            txt.setPos(float(freqs[idx]), float(psd[idx]))
            self.fft_plot.addItem(txt)
            self._peak_texts.append(txt)

    def _update_measurements(self, iq, i, q) -> None:
        amp = float(np.max(np.abs(iq)))
        rms = float(np.sqrt(np.mean(np.abs(iq) ** 2)))
        n = iq.size
        spec = np.fft.fftshift(np.fft.fft(iq))
        freqs = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / self._fs))
        peak_f = float(freqs[int(np.argmax(np.abs(spec)))])
        period = (1.0 / abs(peak_f)) if peak_f else 0.0
        self._meas_labels["Frequency"].setText(f"{peak_f/1e3:.3f} kHz")
        self._meas_labels["Period"].setText(
            f"{period*1e6:.3f} us" if period else "—")
        self._meas_labels["Amplitude"].setText(f"{amp:.4f}")
        self._meas_labels["RMS"].setText(f"{rms:.4f}")
