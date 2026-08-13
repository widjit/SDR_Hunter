"""Dedicated spectrum-hunting view.

Large waterfall (top ~60%), spectrum (~30%), and a right control column (~10%)
with a scrolling hit list, quick-tune band presets, scan-range controls, a hit
counter/rate, and a session log. Includes "Hunt mode" (auto-hop across
configured ranges), a signal-persistence heatmap and auto-bookmarking.
"""
from __future__ import annotations

import time
from typing import Optional

import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QDoubleSpinBox, QGroupBox, QHBoxLayout,
                             QLabel, QListWidget, QPushButton, QSplitter,
                             QVBoxLayout, QWidget)

from ..widgets.spectrum_widget import SpectrumWidget
from ..widgets.waterfall_widget import WaterfallWidget

# name -> (start_hz, end_hz)
BAND_PRESETS = {
    "ISM 433 MHz": (433.05e6, 434.79e6),
    "ISM 868 MHz": (863e6, 870e6),
    "ISM 915 MHz": (902e6, 928e6),
    "ISM 2.4 GHz": (2400e6, 2483.5e6),
    "VHF (30-300)": (30e6, 300e6),
    "UHF (300-1000)": (300e6, 1000e6),
    "Airband (118-137)": (118e6, 137e6),
    "FM Broadcast": (88e6, 108e6),
    "Cellular 700/850": (700e6, 900e6),
    "GPS L1": (1574.42e6, 1576.42e6),
}


class SpectrumHuntingView(QWidget):
    """Full-screen hunting workspace."""

    scan_range_requested = pyqtSignal(float, float, float, int)  # start,end,step,dwell
    hunt_toggled = pyqtSignal(bool)
    tune_requested = pyqtSignal(float)
    stop_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._hits = 0
        self._hit_times: list[float] = []
        self._persist: Optional[np.ndarray] = None

        root = QHBoxLayout(self)

        # --- Left: waterfall (60%) + spectrum (30%) stacked ---
        left = QSplitter(Qt.Orientation.Vertical)
        self.waterfall = WaterfallWidget(title="Hunting Waterfall", history=400)
        self.spectrum = SpectrumWidget(title="Hunting Spectrum")
        left.addWidget(self.waterfall)
        left.addWidget(self.spectrum)
        left.setSizes([600, 300])
        root.addWidget(left, stretch=9)

        # --- Right control column (10%) ---
        right = QVBoxLayout()

        hunt_box = QGroupBox("Hunt")
        hb = QVBoxLayout(hunt_box)
        self.hunt_btn = QPushButton("▶ Hunt Mode (auto-hop)")
        self.hunt_btn.setCheckable(True)
        self.hunt_btn.toggled.connect(self.hunt_toggled)
        hb.addWidget(self.hunt_btn)
        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.clicked.connect(self.stop_requested)
        hb.addWidget(self.stop_btn)
        right.addWidget(hunt_box)

        scan_box = QGroupBox("Scan Range")
        sb = QVBoxLayout(scan_box)
        self.start_mhz = self._spin("Start", 100.0, sb)
        self.stop_mhz = self._spin("Stop", 200.0, sb)
        self.step_mhz = self._spin("Step", 2.0, sb)
        self.dwell = self._spin("Dwell (ms)", 200.0, sb, is_ms=True)
        apply_btn = QPushButton("Apply Scan Range")
        apply_btn.clicked.connect(self._emit_range)
        sb.addWidget(apply_btn)
        right.addWidget(scan_box)

        preset_box = QGroupBox("Quick-tune bands")
        pb = QVBoxLayout(preset_box)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(list(BAND_PRESETS.keys()))
        pb.addWidget(self.preset_combo)
        go = QPushButton("Load band")
        go.clicked.connect(self._load_preset)
        pb.addWidget(go)
        right.addWidget(preset_box)

        stat_box = QGroupBox("Stats")
        stb = QVBoxLayout(stat_box)
        self.hit_count = QLabel("Hits: 0")
        self.hit_count.setProperty("readout", "true")
        self.hit_rate = QLabel("Rate: 0.0/min")
        self.hit_rate.setProperty("readout", "true")
        stb.addWidget(self.hit_count)
        stb.addWidget(self.hit_rate)
        right.addWidget(stat_box)

        right.addWidget(QLabel("Hit list:"))
        self.hit_list = QListWidget()
        right.addWidget(self.hit_list, stretch=2)

        right.addWidget(QLabel("Session log:"))
        self.log = QListWidget()
        right.addWidget(self.log, stretch=1)

        root.addLayout(right, stretch=1)

    # ------------------------------------------------------------------
    def _spin(self, label: str, value: float, layout, is_ms: bool = False):
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        spin = QDoubleSpinBox()
        if is_ms:
            spin.setRange(1, 10000)
            spin.setDecimals(0)
        else:
            spin.setRange(0.01, 6000.0)
            spin.setDecimals(3)
            spin.setSuffix(" MHz")
        spin.setValue(value)
        row.addWidget(spin)
        layout.addLayout(row)
        return spin

    def _emit_range(self) -> None:
        self.scan_range_requested.emit(
            self.start_mhz.value() * 1e6, self.stop_mhz.value() * 1e6,
            self.step_mhz.value() * 1e6, int(self.dwell.value()))
        self.add_log(f"Scan range {self.start_mhz.value():.3f}–"
                     f"{self.stop_mhz.value():.3f} MHz")

    def _load_preset(self) -> None:
        name = self.preset_combo.currentText()
        lo, hi = BAND_PRESETS[name]
        self.start_mhz.setValue(lo / 1e6)
        self.stop_mhz.setValue(hi / 1e6)
        self.add_log(f"Loaded band '{name}'")

    # ------------------------------------------------------------------
    def update_spectrum(self, frame: dict) -> None:
        self.spectrum.update_spectrum(frame)
        self.waterfall.update_spectrum(frame)
        self._accumulate_persistence(frame)

    def _accumulate_persistence(self, frame: dict) -> None:
        psd = np.asarray(frame.get("psd_db", []), dtype=float)
        if psd.size == 0:
            return
        if self._persist is None or self._persist.size != psd.size:
            self._persist = np.zeros(psd.size)
        # Count bins above a rough threshold as "activity".
        self._persist += (psd > (np.median(psd) + 10)).astype(float)

    def on_hit(self, sig: dict) -> None:
        """Register a detected-signal hit (auto-bookmark)."""
        self._hits += 1
        self._hit_times.append(time.time())
        self._hit_times = [t for t in self._hit_times if time.time() - t < 60]
        f = sig.get("freq_hz", 0.0) / 1e6
        p = sig.get("power_db", 0.0)
        self.hit_list.insertItem(0, f"{f:.4f} MHz  {p:.1f} dBm")
        if self.hit_list.count() > 300:
            self.hit_list.takeItem(self.hit_list.count() - 1)
        self.hit_count.setText(f"Hits: {self._hits}")
        self.hit_rate.setText(f"Rate: {len(self._hit_times):.1f}/min")

    def add_log(self, msg: str) -> None:
        self.log.insertItem(0, f"{time.strftime('%H:%M:%S')}  {msg}")
        if self.log.count() > 200:
            self.log.takeItem(self.log.count() - 1)
