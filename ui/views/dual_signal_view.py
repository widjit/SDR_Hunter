"""Dual-signal analysis mode.

A standalone window that shows RX0 and RX1 side by side, each with its own
spectrum, waterfall and IQ scope, plus synchronisation controls (link ref-level
and span) and comparison tools (spectral difference + cross-correlation).

Launched from the main window's *View → Dual Signal Analysis Mode* action. It is
fed spectrum frames via :meth:`update_frame` (the same dict format used across
the app: ``{channel, center_freq, sample_rate, psd_db}``). When live IQ is not
available, an approximate time-domain view is reconstructed from the PSD so the
scope stays populated.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import (QCheckBox, QDialog, QGroupBox, QHBoxLayout, QLabel,
                             QSplitter, QVBoxLayout, QWidget)
from PyQt6.QtCore import Qt

from ..widgets.scope_widget import ScopeWidget
from ..widgets.spectrum_widget import SpectrumWidget
from ..widgets.waterfall_widget import WaterfallWidget


class _RXColumn(QWidget):
    """One receiver column: spectrum + waterfall + scope stacked."""

    def __init__(self, channel: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.channel = channel
        v = QVBoxLayout(self)
        v.addWidget(QLabel(f"<b>RX{channel}</b>"))
        split = QSplitter(Qt.Orientation.Vertical)
        self.spectrum = SpectrumWidget(title=f"RX{channel} spectrum")
        self.waterfall = WaterfallWidget(title=f"RX{channel} waterfall")
        self.scope = ScopeWidget()
        split.addWidget(self.spectrum)
        split.addWidget(self.waterfall)
        split.addWidget(self.scope)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 2)
        split.setStretchFactor(2, 3)
        v.addWidget(split)

    def update_frame(self, frame: dict) -> None:
        self.spectrum.update_spectrum(frame)
        self.waterfall.update_spectrum(frame)
        psd = np.asarray(frame.get("psd_db", []), dtype=float)
        fs = float(frame.get("sample_rate", 2.048e6))
        if psd.size:
            iq = self._psd_to_iq(psd)
            self.scope.update_iq(iq, fs)

    @staticmethod
    def _psd_to_iq(psd_db: np.ndarray) -> np.ndarray:
        """Reconstruct an approximate complex time series from a PSD frame."""
        amp = 10.0 ** (psd_db / 20.0)
        phase = np.random.uniform(-np.pi, np.pi, size=amp.size)
        spec = amp * np.exp(1j * phase)
        iq = np.fft.ifft(np.fft.ifftshift(spec))
        peak = np.max(np.abs(iq)) or 1.0
        return (iq / peak).astype(np.complex64)


class DualSignalView(QDialog):
    """Side-by-side dual-RX analysis window with sync + comparison tools."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Dual Signal Analysis Mode")
        self.resize(1200, 760)
        self._last: Dict[int, dict] = {}

        root = QVBoxLayout(self)

        # -- sync + comparison control bar --------------------------------
        ctl = QGroupBox("Sync & comparison")
        cl = QHBoxLayout(ctl)
        self.link_ref = QCheckBox("Link ref level")
        self.link_span = QCheckBox("Link span/zoom")
        self.show_diff = QCheckBox("Show spectral difference")
        self.show_diff.setChecked(True)
        for w in (self.link_ref, self.link_span, self.show_diff):
            cl.addWidget(w)
        cl.addStretch(1)
        self.corr_label = QLabel("Cross-correlation: —")
        self.corr_label.setProperty("readout", "true")
        cl.addWidget(self.corr_label)
        root.addWidget(ctl)

        # -- two RX columns ------------------------------------------------
        cols = QSplitter(Qt.Orientation.Horizontal)
        self.rx0 = _RXColumn(0)
        self.rx1 = _RXColumn(1)
        cols.addWidget(self.rx0)
        cols.addWidget(self.rx1)
        cols.setStretchFactor(0, 1)
        cols.setStretchFactor(1, 1)
        root.addWidget(cols, stretch=4)

        # -- comparison difference plot -----------------------------------
        self.diff_plot = pg.PlotWidget(title="Spectral difference (RX0 − RX1)")
        self.diff_plot.setLabel("bottom", "Bin")
        self.diff_plot.setLabel("left", "Δ dB")
        self.diff_curve = self.diff_plot.plot(pen=pg.mkPen("#ffaa33", width=1))
        self.diff_plot.setMaximumHeight(180)
        root.addWidget(self.diff_plot, stretch=1)

        self.link_ref.toggled.connect(self._apply_sync)
        self.link_span.toggled.connect(self._apply_sync)

    # ------------------------------------------------------------------
    def update_frame(self, frame: dict) -> None:
        ch = int(frame.get("channel", 0))
        self._last[ch] = frame
        (self.rx0 if ch == 0 else self.rx1).update_frame(frame)
        self._apply_sync()
        self._update_comparison()

    def _apply_sync(self, *args) -> None:
        try:
            if self.link_ref.isChecked() and 0 in self._last:
                # Mirror RX0 ref level onto RX1 (best-effort).
                psd0 = np.asarray(self._last[0].get("psd_db", []), dtype=float)
                if psd0.size:
                    top = float(np.max(psd0)) + 10.0
                    self.rx0.spectrum.set_ref_level(top)
                    self.rx1.spectrum.set_ref_level(top)
            if self.link_span.isChecked() and 0 in self._last:
                f = self._last[0]
                center = float(f.get("center_freq", 0)) / 1e6
                span = float(f.get("sample_rate", 2.048e6)) / 1e6
                if center:
                    self.rx0.spectrum.set_span(center, span)
                    self.rx1.spectrum.set_span(center, span)
        except Exception:  # noqa: BLE001
            pass

    def _update_comparison(self) -> None:
        if 0 not in self._last or 1 not in self._last:
            return
        p0 = np.asarray(self._last[0].get("psd_db", []), dtype=float)
        p1 = np.asarray(self._last[1].get("psd_db", []), dtype=float)
        if p0.size == 0 or p1.size == 0:
            return
        n = min(p0.size, p1.size)
        p0, p1 = p0[:n], p1[:n]
        if self.show_diff.isChecked():
            self.diff_curve.setData(np.arange(n), p0 - p1)
        # Normalised cross-correlation of the two PSDs.
        a = p0 - p0.mean()
        b = p1 - p1.mean()
        denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1.0
        corr = float(np.dot(a, b) / denom)
        self.corr_label.setText(f"Cross-correlation: {corr:+.3f}")
