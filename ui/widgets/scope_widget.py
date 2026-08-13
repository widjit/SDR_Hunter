"""Oscilloscope-style time-domain IQ display + constellation + FFT sidebar.

Used in the "dual signal view" mode where both RX are parked on specific
signals. Renders I (blue) and Q (red) traces, a constellation scatter (I vs Q),
a small FFT of the captured window, simple trigger controls and live
measurements (period, frequency, amplitude, RMS).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout,
                             QGroupBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget)


class ScopeWidget(QWidget):
    """Time-domain IQ scope with constellation and FFT."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._fs = 2.048e6
        self._trigger_mode = "auto"
        self._trigger_level = 0.0

        root = QHBoxLayout(self)

        # --- Left: time-domain + constellation stacked ---
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
        left.addWidget(self.time_plot, stretch=3)

        self.const_plot = pg.PlotWidget(title="Constellation (I vs Q)")
        self.const_plot.setAspectLocked(True)
        self.const_scatter = pg.ScatterPlotItem(size=2, brush=pg.mkBrush(
            51, 255, 204, 120), pen=None)
        self.const_plot.addItem(self.const_scatter)
        left.addWidget(self.const_plot, stretch=2)
        root.addLayout(left, stretch=3)

        # --- Right: controls + FFT + measurements ---
        right = QVBoxLayout()

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
        for i, key in enumerate(("Frequency", "Period", "Amplitude", "RMS")):
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
        t = np.arange(n) / self._fs
        i = np.real(iq)
        q = np.imag(iq)
        self.i_curve.setData(t, i)
        self.q_curve.setData(t, q)

        # Constellation (subsample for perf).
        step = max(1, n // 1024)
        self.const_scatter.setData(i[::step], q[::step])

        # FFT of window.
        win = np.hanning(n)
        spec = np.fft.fftshift(np.fft.fft(iq * win))
        psd = 20.0 * np.log10(np.abs(spec) + 1e-9)
        freqs = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / self._fs))
        self.fft_curve.setData(freqs, psd)

        self._update_measurements(iq, i, q)

    def _update_measurements(self, iq, i, q) -> None:
        amp = float(np.max(np.abs(iq)))
        rms = float(np.sqrt(np.mean(np.abs(iq) ** 2)))
        # Dominant frequency via FFT peak.
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
