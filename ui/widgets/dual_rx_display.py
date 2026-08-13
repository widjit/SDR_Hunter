"""Dual-RX display: stacked spectrum+waterfall for RX0 (scanner) and RX1 (focus).

Top half = RX0, bottom half = RX1. Each half has a header showing role /
frequency / sample-rate and a mini toolbar (colormap, peak-hold, avg count,
dB range). A link button couples X-axis pan/zoom across both spectra; a swap
button flips the RX0/RX1 roles visually.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel,
                             QPushButton, QSpinBox, QSplitter, QVBoxLayout,
                             QWidget)

from .spectrum_widget import SpectrumWidget
from .waterfall_widget import COLORMAPS, WaterfallWidget


class _RXHalf(QWidget):
    """One receiver's stacked spectrum + waterfall with a mini toolbar."""

    def __init__(self, channel: int, role: str, parent=None):
        super().__init__(parent)
        self.channel = channel
        self.role = role

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)
        root.setSpacing(2)

        header = QHBoxLayout()
        self.header_label = QLabel(f"RX{channel} — {role} — idle")
        self.header_label.setProperty("readout", "true")
        header.addWidget(self.header_label)
        header.addStretch(1)

        header.addWidget(QLabel("Cmap:"))
        self.cmap = QComboBox()
        self.cmap.addItems(COLORMAPS)
        header.addWidget(self.cmap)

        self.peak_cb = QCheckBox("Peak")
        header.addWidget(self.peak_cb)
        self.min_cb = QCheckBox("Min")
        header.addWidget(self.min_cb)

        header.addWidget(QLabel("Avg:"))
        self.avg_spin = QSpinBox()
        self.avg_spin.setRange(1, 100)
        header.addWidget(self.avg_spin)

        header.addWidget(QLabel("Ref:"))
        self.ref_spin = QSpinBox()
        self.ref_spin.setRange(-60, 20)
        self.ref_spin.setValue(-10)
        self.ref_spin.setSuffix(" dBm")
        header.addWidget(self.ref_spin)
        root.addLayout(header)

        split = QSplitter(Qt.Orientation.Vertical)
        self.spectrum = SpectrumWidget(title=f"RX{channel}")
        self.waterfall = WaterfallWidget(title=f"RX{channel}")
        split.addWidget(self.spectrum)
        split.addWidget(self.waterfall)
        split.setSizes([250, 350])
        root.addWidget(split)

        # Wire mini toolbar.
        self.cmap.currentTextChanged.connect(self.waterfall.set_colormap)
        self.peak_cb.toggled.connect(self.spectrum.set_peak_hold)
        self.min_cb.toggled.connect(self.spectrum.set_min_hold)
        self.avg_spin.valueChanged.connect(self.spectrum.set_avg_count)
        self.ref_spin.valueChanged.connect(
            lambda v: self.spectrum.set_ref_level(float(v)))

    def update_frame(self, frame: dict) -> None:
        self.spectrum.update_spectrum(frame)
        self.waterfall.update_spectrum(frame)
        center = frame.get("center_freq", 0.0) / 1e6
        fs = frame.get("sample_rate", 0.0) / 1e6
        self.header_label.setText(
            f"RX{self.channel} — {self.role} — {center:.4f} MHz @ {fs:.3f} MS/s")


class DualRXDisplay(QWidget):
    """Vertical split of two :class:`_RXHalf` receivers."""

    tune_rx0_requested = pyqtSignal(float)
    tune_rx1_requested = pyqtSignal(float)
    add_signal_requested = pyqtSignal(float, float)
    focus_selection_requested = pyqtSignal(float, float)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._linked = False

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)

        bar = QHBoxLayout()
        self.link_btn = QPushButton("Link Axes")
        self.link_btn.setCheckable(True)
        self.link_btn.toggled.connect(self._set_linked)
        bar.addWidget(self.link_btn)
        self.swap_btn = QPushButton("Swap RX Roles")
        self.swap_btn.clicked.connect(self._swap_roles)
        bar.addWidget(self.swap_btn)
        bar.addStretch(1)
        root.addLayout(bar)

        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.rx0 = _RXHalf(0, "Scanner")
        self.rx1 = _RXHalf(1, "Focus")
        self.splitter.addWidget(self.rx0)
        self.splitter.addWidget(self.rx1)
        self.splitter.setSizes([500, 500])
        root.addWidget(self.splitter)

        # Forward context-menu signals from each half's spectrum/waterfall.
        for half in (self.rx0, self.rx1):
            half.spectrum.tune_rx0_requested.connect(self.tune_rx0_requested)
            half.spectrum.tune_rx1_requested.connect(self.tune_rx1_requested)
            half.spectrum.add_signal_requested.connect(self.add_signal_requested)
            half.waterfall.tune_rx1_requested.connect(self.tune_rx1_requested)
            half.waterfall.add_signal_requested.connect(self.add_signal_requested)
            half.waterfall.focus_selection_requested.connect(
                self.focus_selection_requested)

    # ------------------------------------------------------------------
    def update_frame(self, frame: dict) -> None:
        """Route a spectrum frame to the matching RX half."""
        ch = int(frame.get("channel", 0))
        (self.rx0 if ch == 0 else self.rx1).update_frame(frame)

    def _set_linked(self, on: bool) -> None:
        self._linked = on
        vb1 = self.rx1.spectrum.plot.getPlotItem().vb
        if on:
            vb1.setXLink(self.rx0.spectrum.plot.getPlotItem().vb)
        else:
            vb1.setXLink(None)

    def _swap_roles(self) -> None:
        self.rx0.role, self.rx1.role = self.rx1.role, self.rx0.role
        self.rx0.header_label.setText(f"RX0 — {self.rx0.role} — idle")
        self.rx1.header_label.setText(f"RX1 — {self.rx1.role} — idle")
