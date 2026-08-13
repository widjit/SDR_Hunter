"""Drone tracking view.

Combines a click-to-pin map (lat/lon scatter built on pyqtgraph — no network
tiles required so it works offline), a table of tracked drones (Remote ID /
OpenDroneID decoded, RF-suspected, or manual), and a details pane. When Remote
ID decode fails but the operator can see the drone, they click the map to drop
a manual contact (``AppState.add_manual_drone``). Selected drones can be pushed
to ATAK.
"""
from __future__ import annotations

from typing import Optional

import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QDoubleSpinBox, QGroupBox, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QSplitter,
                             QTableWidget, QTableWidgetItem, QTextEdit,
                             QVBoxLayout, QWidget)


class DroneTrackingView(QWidget):
    """Map + tracked-drone table + details."""

    manual_drone_requested = pyqtSignal(float, float, str)  # lat, lon, callsign
    send_atak_requested = pyqtSignal(dict)
    center_map_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._click_to_add = False

        root = QVBoxLayout(self)

        bar = QHBoxLayout()
        self.click_add = QCheckBox("Click map to add manual contact")
        self.click_add.toggled.connect(
            lambda v: setattr(self, "_click_to_add", v))
        bar.addWidget(self.click_add)
        bar.addWidget(QLabel("Callsign:"))
        self.callsign = QLineEdit()
        self.callsign.setPlaceholderText("Visual-ID drone")
        bar.addWidget(self.callsign)
        self.op_lat = QDoubleSpinBox(); self.op_lat.setRange(-90, 90)
        self.op_lat.setDecimals(6); self.op_lat.setPrefix("lat ")
        self.op_lon = QDoubleSpinBox(); self.op_lon.setRange(-180, 180)
        self.op_lon.setDecimals(6); self.op_lon.setPrefix("lon ")
        bar.addWidget(self.op_lat)
        bar.addWidget(self.op_lon)
        self.send_btn = QPushButton("Send Selected → ATAK")
        self.send_btn.clicked.connect(self._send_selected)
        bar.addWidget(self.send_btn)
        bar.addStretch(1)
        root.addLayout(bar)

        split = QSplitter(Qt.Orientation.Horizontal)

        # Map (pyqtgraph, lon=x, lat=y).
        map_box = QGroupBox("Map (click to pin manual contact)")
        mv = QVBoxLayout(map_box)
        self.map = pg.PlotWidget()
        self.map.setLabel("bottom", "Longitude")
        self.map.setLabel("left", "Latitude")
        self.map.showGrid(x=True, y=True, alpha=0.3)
        self.map.setMenuEnabled(False)
        self.scatter = pg.ScatterPlotItem(size=14, pen=pg.mkPen("#fff"))
        self.map.addItem(self.scatter)
        self.op_marker = pg.ScatterPlotItem(
            size=16, symbol="t", brush=pg.mkBrush("#33ffcc"))
        self.map.addItem(self.op_marker)
        self.map.scene().sigMouseClicked.connect(self._on_map_click)
        mv.addWidget(self.map)
        split.addWidget(map_box)

        # Table + details.
        right = QWidget()
        rv = QVBoxLayout(right)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["UID", "Callsign", "Source", "Freq (MHz)", "Conf", "ID?"])
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self._on_select)
        rv.addWidget(self.table, stretch=2)
        rv.addWidget(QLabel("Details:"))
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        rv.addWidget(self.details, stretch=1)
        split.addWidget(right)
        split.setSizes([500, 500])
        root.addWidget(split)

        self._drones: list[dict] = []

    # ------------------------------------------------------------------
    def _on_map_click(self, ev) -> None:
        if not self._click_to_add:
            return
        vb = self.map.getPlotItem().vb
        if not self.map.sceneBoundingRect().contains(ev.scenePos()):
            return
        pt = vb.mapSceneToView(ev.scenePos())
        self.manual_drone_requested.emit(
            float(pt.y()), float(pt.x()), self.callsign.text())

    def set_operator(self, lat: float, lon: float) -> None:
        self.op_marker.setData([lon], [lat])

    def update_drones(self, drones: list) -> None:
        """Refresh the table + map from a list of drone dicts."""
        self._drones = drones
        self.table.setRowCount(len(drones))
        spots = []
        for row, d in enumerate(drones):
            vals = [d.get("uid", ""), d.get("callsign", ""),
                    d.get("source", ""),
                    f"{(d.get('freq_hz') or 0)/1e6:.3f}" if d.get("freq_hz") else "",
                    f"{d.get('confidence', 0):.2f}",
                    "FAIL" if d.get("id_failed") else "OK"]
            for col, v in enumerate(vals):
                self.table.setItem(row, col, QTableWidgetItem(str(v)))
            lat, lon = d.get("lat"), d.get("lon")
            if lat is not None and lon is not None:
                color = "#ff6b6b" if d.get("source") == "manual" else "#f5d76e"
                spots.append({"pos": (lon, lat),
                              "brush": pg.mkBrush(color)})
        self.scatter.setData(spots)

    def _on_select(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self._drones):
            d = self._drones[row]
            lines = [f"{k}: {v}" for k, v in d.items()]
            self.details.setPlainText("\n".join(lines))

    def _send_selected(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self._drones):
            self.send_atak_requested.emit(self._drones[row])
