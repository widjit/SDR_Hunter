"""Scrolling time-frequency waterfall widget built on pyqtgraph ImageItem.

New spectrum rows scroll downward (newest at top). Supports selectable color
maps, adjustable color range, a right-click context menu and click-drag range
selection for focusing RX1 on a sub-band.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QMenu, QVBoxLayout,
                             QWidget)

COLORMAPS = ["viridis", "plasma", "inferno", "magma", "hot", "CET-L17"]


class WaterfallWidget(QWidget):
    """Rolling 2D spectrogram."""

    tune_rx1_requested = pyqtSignal(float)
    add_signal_requested = pyqtSignal(float, float)
    focus_selection_requested = pyqtSignal(float, float)  # f_lo_hz, f_hi_hz

    def __init__(self, title: str = "Waterfall", history: int = 300,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._history = history
        self._nbins = 0
        self._img_data: Optional[np.ndarray] = None
        self._center = 100e6
        self._fs = 2.048e6
        self._cmin = -110.0
        self._cmax = -20.0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Colormap:"))
        self.cmap_combo = QComboBox()
        self.cmap_combo.addItems(COLORMAPS)
        self.cmap_combo.currentTextChanged.connect(self.set_colormap)
        bar.addWidget(self.cmap_combo)
        bar.addStretch(1)
        root.addLayout(bar)

        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Frequency", units="MHz")
        self.plot.setLabel("left", "Time", units="s")
        self.plot.setMenuEnabled(False)
        self.plot.invertY(True)
        self.img = pg.ImageItem()
        self.plot.addItem(self.img)
        root.addWidget(self.plot)

        # Selection region (hidden until dragged).
        self.region = pg.LinearRegionItem(brush=pg.mkBrush(0, 209, 178, 40))
        self.region.setZValue(10)
        self.region.setVisible(False)
        self.region.sigRegionChangeFinished.connect(self._on_region_changed)
        self.plot.addItem(self.region)

        self.set_colormap("viridis")
        self.plot.scene().sigMouseClicked.connect(self._on_mouse_clicked)

    # ------------------------------------------------------------------
    def set_colormap(self, name: str) -> None:
        try:
            cmap = pg.colormap.get(name)
        except Exception:  # noqa: BLE001
            cmap = pg.colormap.get("viridis")
        self.img.setLookupTable(cmap.getLookupTable(0.0, 1.0, 256))

    def set_color_range(self, cmin: float, cmax: float) -> None:
        self._cmin, self._cmax = float(cmin), float(cmax)
        self.img.setLevels([self._cmin, self._cmax])

    def set_history(self, rows: int) -> None:
        self._history = max(20, int(rows))
        self._img_data = None  # will reallocate

    # ------------------------------------------------------------------
    def update_spectrum(self, frame: dict) -> None:
        """Append one spectrum row (only channel 0 by default is shown)."""
        try:
            psd = np.asarray(frame.get("psd_db", []), dtype=float)
            if psd.size == 0:
                return
            center = float(frame.get("center_freq", self._center))
            fs = float(frame.get("sample_rate", self._fs))
        except Exception:  # noqa: BLE001
            return

        if self._img_data is None or psd.size != self._nbins:
            self._nbins = psd.size
            self._img_data = np.full((self._history, self._nbins), self._cmin,
                                     dtype=np.float32)
        self._center, self._fs = center, fs
        # Scroll: shift down, insert new row at top.
        self._img_data[1:] = self._img_data[:-1]
        self._img_data[0] = psd
        self.img.setImage(self._img_data.T, autoLevels=False,
                          levels=[self._cmin, self._cmax])
        # Map image X to MHz, Y to seconds.
        f_lo = (center - fs / 2.0) / 1e6
        self.img.setRect(pg.QtCore.QRectF(f_lo, 0.0, fs / 1e6,
                                          float(self._history)))

    # ------------------------------------------------------------------
    def _scene_to_freq_hz(self, scene_pos) -> Optional[float]:
        vb = self.plot.getPlotItem().vb
        if not self.plot.sceneBoundingRect().contains(scene_pos):
            return None
        return float(vb.mapSceneToView(scene_pos).x() * 1e6)

    def _on_mouse_clicked(self, ev) -> None:
        freq_hz = self._scene_to_freq_hz(ev.scenePos())
        if freq_hz is None:
            return
        if ev.button() == Qt.MouseButton.RightButton:
            menu = QMenu(self)
            menu.addAction(f"@ {freq_hz/1e6:.4f} MHz").setEnabled(False)
            menu.addSeparator()
            menu.addAction("Tune RX1 here",
                           lambda: self.tune_rx1_requested.emit(freq_hz))
            menu.addAction("Add to Known Signals",
                           lambda: self.add_signal_requested.emit(freq_hz, 25e3))
            menu.addAction("Show selection region", self._toggle_region)
            menu.exec(ev.screenPos().toPoint())

    def _toggle_region(self) -> None:
        if not self.region.isVisible():
            c = self._center / 1e6
            span = self._fs / 1e6
            self.region.setRegion([c - span / 8, c + span / 8])
        self.region.setVisible(not self.region.isVisible())

    def _on_region_changed(self) -> None:
        lo, hi = self.region.getRegion()
        self.focus_selection_requested.emit(lo * 1e6, hi * 1e6)
