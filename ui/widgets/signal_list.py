"""Detected-signal table with color coding, filtering and a rich context menu.

Rows are keyed by rounded frequency so repeated detections of the same signal
update in place (with an "Age" column) instead of piling up. Right-click a row
for tuning, recording, identification, baseline, demod, drone-marking and
"Send to ATAK" (signal of interest) actions.
"""
from __future__ import annotations

import time
from typing import Dict, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QMenu, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

COLUMNS = ["Freq (MHz)", "Power (dBm)", "BW (kHz)", "Modulation",
           "Name/Match", "Status", "Age (s)"]

COLOR_KNOWN = QColor("#17c964")
COLOR_UNKNOWN = QColor("#f5d76e")
COLOR_ALERT = QColor("#ff6b6b")


class SignalListWidget(QWidget):
    """Live table of detected signals."""

    tune_rx0_requested = pyqtSignal(float)
    tune_rx1_requested = pyqtSignal(float)
    tune_both_requested = pyqtSignal(float)
    record_requested = pyqtSignal(float)
    identify_requested = pyqtSignal(float)
    add_baseline_requested = pyqtSignal(float)
    show_on_spectrum_requested = pyqtSignal(float)
    demodulate_requested = pyqtSignal(float)
    mark_drone_requested = pyqtSignal(dict)
    send_atak_requested = pyqtSignal(dict)   # signal of interest -> ATAK
    detail_requested = pyqtSignal(dict)
    count_changed = pyqtSignal(int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._rows: Dict[int, int] = {}       # freq_key -> table row
        self._data: Dict[int, dict] = {}      # freq_key -> signal dict
        self._filter_text = ""
        self._only_unknown = False
        self._only_alerts = False

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)

        # Filter bar.
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search freq / name / modulation…")
        self.search.textChanged.connect(self._on_filter_text)
        bar.addWidget(self.search, stretch=2)

        self.cat = QComboBox()
        self.cat.addItems(["All", "Known", "Unknown", "Alerts"])
        self.cat.currentTextChanged.connect(self._on_category)
        bar.addWidget(self.cat)

        self.count_label = QLabel("0 signals")
        bar.addWidget(self.count_label)
        root.addLayout(bar)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_menu)
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table)

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------
    def add_signal(self, sig: dict, alert: bool = False) -> None:
        """Insert or update a signal row from a SignalEvent.to_dict()."""
        try:
            freq = float(sig.get("freq_hz", 0.0))
        except Exception:  # noqa: BLE001
            return
        key = int(round(freq / 1e3))  # kHz resolution key
        sig = dict(sig)
        sig["_alert"] = alert or bool(sig.get("_alert"))
        sig["_seen"] = time.time()
        self._data[key] = sig

        self.table.setSortingEnabled(False)
        if key in self._rows:
            row = self._rows[key]
        else:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._rows[key] = row
        self._populate_row(row, sig)
        self.table.setSortingEnabled(True)
        self._apply_filter_row(row, sig)
        self.count_label.setText(f"{len(self._data)} signals")
        self.count_changed.emit(len(self._data))

    def _populate_row(self, row: int, sig: dict) -> None:
        freq = float(sig.get("freq_hz", 0.0))
        match = sig.get("signal_db_match") or {}
        name = match.get("name", "") if match else ""
        is_known = bool(sig.get("is_known"))
        alert = bool(sig.get("_alert"))
        status = "ALERT" if alert else ("Known" if is_known else "Unknown")
        vals = [
            f"{freq/1e6:.4f}",
            f"{float(sig.get('power_db', 0.0)):.1f}",
            f"{float(sig.get('bandwidth_hz', 0.0))/1e3:.1f}",
            str(sig.get("modulation_hint", "")),
            name,
            status,
            "0",
        ]
        color = COLOR_ALERT if alert else (
            COLOR_KNOWN if is_known else COLOR_UNKNOWN)
        for col, text in enumerate(vals):
            item = QTableWidgetItem(text)
            if col in (0, 1, 2):
                item.setData(Qt.ItemDataRole.EditRole, float(text) if text else 0.0)
            item.setForeground(QBrush(color))
            self.table.setItem(row, col, item)

    def refresh_ages(self) -> None:
        """Update the Age column for all rows (call on a timer)."""
        now = time.time()
        for key, row in self._rows.items():
            sig = self._data.get(key)
            if not sig:
                continue
            age = int(now - sig.get("_seen", now))
            item = self.table.item(row, 6)
            if item:
                item.setText(str(age))

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------
    def _on_filter_text(self, text: str) -> None:
        self._filter_text = text.lower().strip()
        self._reapply_filters()

    def _on_category(self, cat: str) -> None:
        self._only_unknown = cat == "Unknown"
        self._only_alerts = cat == "Alerts"
        self._only_known = cat == "Known"
        self._reapply_filters()

    def _reapply_filters(self) -> None:
        for key, row in self._rows.items():
            self._apply_filter_row(row, self._data.get(key, {}))

    def _apply_filter_row(self, row: int, sig: dict) -> None:
        visible = True
        cat = self.cat.currentText()
        if cat == "Known" and not sig.get("is_known"):
            visible = False
        elif cat == "Unknown" and sig.get("is_known"):
            visible = False
        elif cat == "Alerts" and not sig.get("_alert"):
            visible = False
        if visible and self._filter_text:
            hay = " ".join(str(self.table.item(row, c).text())
                           for c in range(self.table.columnCount())
                           if self.table.item(row, c)).lower()
            visible = self._filter_text in hay
        self.table.setRowHidden(row, not visible)

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------
    def _row_signal(self, row: int) -> Optional[dict]:
        for key, r in self._rows.items():
            if r == row:
                return self._data.get(key)
        return None

    def _selected_signal(self) -> Optional[dict]:
        row = self.table.currentRow()
        return self._row_signal(row) if row >= 0 else None

    def _on_double_click(self) -> None:
        sig = self._selected_signal()
        if sig:
            self.detail_requested.emit(sig)

    def _show_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        self.table.selectRow(row)
        sig = self._row_signal(row)
        if not sig:
            return
        freq = float(sig.get("freq_hz", 0.0))
        menu = QMenu(self)
        menu.addAction(f"{freq/1e6:.4f} MHz").setEnabled(False)
        menu.addSeparator()
        menu.addAction("Tune RX0 to this",
                       lambda: self.tune_rx0_requested.emit(freq))
        menu.addAction("Tune RX1 to this",
                       lambda: self.tune_rx1_requested.emit(freq))
        menu.addAction("Tune both RX to this signal",
                       lambda: self.tune_both_requested.emit(freq))
        menu.addSeparator()
        menu.addAction("Start recording",
                       lambda: self.record_requested.emit(freq))
        menu.addAction("Identify signal",
                       lambda: self.identify_requested.emit(freq))
        menu.addAction("Add to baseline as known",
                       lambda: self.add_baseline_requested.emit(freq))
        menu.addAction("Show on spectrum",
                       lambda: self.show_on_spectrum_requested.emit(freq))
        menu.addAction("Demodulate audio",
                       lambda: self.demodulate_requested.emit(freq))
        menu.addSeparator()
        menu.addAction("Mark as drone",
                       lambda: self.mark_drone_requested.emit(sig))
        menu.addAction("Send to ATAK (Signal of Interest)",
                       lambda: self.send_atak_requested.emit(sig))
        menu.exec(self.table.viewport().mapToGlobal(pos))
