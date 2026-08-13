"""Baseline manager dialog.

Lists saved baselines, previews their spectrum, captures new baselines (with a
name + location + optional lat/lon), loads one for live comparison, and shows a
compare view overlaying live spectrum on the baseline with an anomaly list.
Backed by :class:`core.baseline_manager.BaselineManager`.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import (QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                             QInputDialog, QLabel, QLineEdit, QListWidget,
                             QMessageBox, QPushButton, QTabWidget, QVBoxLayout,
                             QWidget)

from core.baseline_manager import BaselineManager, BaselineProfile


class CaptureBaselineDialog(QDialog):
    """Collect a name + location for a new baseline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Capture New Baseline")
        form = QFormLayout(self)
        self.name = QLineEdit(f"baseline_{int(__import__('time').time())}")
        form.addRow("Name", self.name)
        self.location = QLineEdit()
        form.addRow("Location name", self.location)
        self.lat = QDoubleSpinBox()
        self.lat.setRange(-90.0, 90.0)
        self.lat.setDecimals(6)
        form.addRow("Latitude", self.lat)
        self.lon = QDoubleSpinBox()
        self.lon.setRange(-180.0, 180.0)
        self.lon.setDecimals(6)
        form.addRow("Longitude", self.lon)
        row = QHBoxLayout()
        ok = QPushButton("Capture")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        form.addRow(row)

    def values(self) -> dict:
        return {"name": self.name.text(), "location_name": self.location.text(),
                "lat": self.lat.value() or None, "lon": self.lon.value() or None}


class BaselineManagerDialog(QDialog):
    """Browse / preview / load / compare / import-export baselines."""

    def __init__(self, manager: BaselineManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Baseline Manager")
        self.resize(820, 560)
        self.manager = manager
        self._loaded_for_compare: Optional[BaselineProfile] = None

        root = QHBoxLayout(self)

        # Left: list + buttons.
        left = QVBoxLayout()
        left.addWidget(QLabel("Saved baselines:"))
        self.list = QListWidget()
        self.list.currentTextChanged.connect(self._preview)
        left.addWidget(self.list)
        self.capture_btn = QPushButton("Capture New Baseline…")
        self.load_btn = QPushButton("Load (enable compare)")
        self.delete_btn = QPushButton("Delete")
        self.import_btn = QPushButton("Import JSON…")
        self.export_btn = QPushButton("Export JSON…")
        for b in (self.capture_btn, self.load_btn, self.delete_btn,
                  self.import_btn, self.export_btn):
            left.addWidget(b)
        self.load_btn.clicked.connect(self._load_selected)
        self.delete_btn.clicked.connect(self._delete_selected)
        root.addLayout(left, stretch=1)

        # Right: tabs preview / compare.
        self.tabs = QTabWidget()
        preview = QWidget()
        pv = QVBoxLayout(preview)
        self.preview_plot = pg.PlotWidget(title="Baseline spectrum")
        self.preview_plot.setLabel("bottom", "Frequency", units="MHz")
        self.preview_plot.setLabel("left", "Power", units="dBm")
        self.preview_curve = self.preview_plot.plot(pen=pg.mkPen("#33ffcc"))
        pv.addWidget(self.preview_plot)
        self.info = QLabel("—")
        pv.addWidget(self.info)
        self.tabs.addTab(preview, "Preview")

        compare = QWidget()
        cv = QVBoxLayout(compare)
        self.compare_plot = pg.PlotWidget(title="Live (cyan) vs Baseline (grey)")
        self.compare_plot.setLabel("bottom", "Frequency", units="MHz")
        self.compare_plot.setLabel("left", "Power", units="dBm")
        self.base_curve = self.compare_plot.plot(pen=pg.mkPen("#888"))
        self.live_curve = self.compare_plot.plot(pen=pg.mkPen("#33ffff"))
        cv.addWidget(self.compare_plot)
        cv.addWidget(QLabel("Anomalies (in live, not baseline):"))
        self.anomaly_list = QListWidget()
        cv.addWidget(self.anomaly_list)
        self.tabs.addTab(compare, "Compare")
        root.addWidget(self.tabs, stretch=3)

        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        self.list.clear()
        try:
            for name in self.manager.list_baselines():
                self.list.addItem(name)
        except Exception:  # noqa: BLE001
            pass

    def _preview(self, name: str) -> None:
        if not name:
            return
        try:
            prof = self.manager.load(name)
        except Exception as exc:  # noqa: BLE001
            self.info.setText(f"Load error: {exc}")
            return
        psd = prof.psd_array
        if psd.size:
            freqs = prof.freq_axis() / 1e6
            self.preview_curve.setData(freqs, psd)
        self.info.setText(
            f"{prof.name}  |  {prof.location_name or 'no location'}  |  "
            f"{prof.freq_start_hz/1e6:.3f}–{prof.freq_end_hz/1e6:.3f} MHz  |  "
            f"{len(prof.psd_db)} bins")

    def _load_selected(self) -> None:
        name = self.list.currentText()
        if not name:
            return
        try:
            prof = self.manager.load(name)
            self.manager.active = prof
            self._loaded_for_compare = prof
            if prof.psd_array.size:
                self.base_curve.setData(prof.freq_axis() / 1e6, prof.psd_array)
            self.tabs.setCurrentIndex(1)
            QMessageBox.information(self, "Baseline loaded",
                                    f"'{name}' is now active for comparison.")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Error", str(exc))

    def _delete_selected(self) -> None:
        name = self.list.currentText()
        if name and self.manager.delete(name):
            self.refresh()

    def update_live(self, freqs_hz: np.ndarray, live_psd: np.ndarray) -> None:
        """Feed a live spectrum frame into the compare tab."""
        if self._loaded_for_compare is None:
            return
        self.live_curve.setData(freqs_hz / 1e6, live_psd)
        try:
            anomalies = self.manager.compare_spectrum(
                freqs_hz, live_psd, self._loaded_for_compare)
        except Exception:  # noqa: BLE001
            anomalies = []
        self.anomaly_list.clear()
        for a in anomalies[:200]:
            self.anomaly_list.addItem(
                f"[{a.kind}] {a.freq_hz/1e6:.4f} MHz  Δ{a.delta_db:+.1f} dB")
