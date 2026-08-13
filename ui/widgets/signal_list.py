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
                             QLabel, QLineEdit, QMenu, QPushButton, QTabWidget,
                             QTableWidget, QTableWidgetItem, QTreeWidget,
                             QTreeWidgetItem, QListWidget, QVBoxLayout, QWidget)

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
    favorite_toggled = pyqtSignal(float, bool)   # freq_hz, is_favorite
    bookmark_requested = pyqtSignal(dict)        # signal -> new bookmark

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._rows: Dict[int, int] = {}       # freq_key -> table row
        self._data: Dict[int, dict] = {}      # freq_key -> signal dict
        self._filter_text = ""
        self._only_unknown = False
        self._only_alerts = False
        self._only_known = False
        self._favorites: set = set()          # freq_key set
        self._history: list = []              # recent detections (dicts)

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)

        # Filter bar.
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search freq / name / modulation…")
        self.search.textChanged.connect(self._on_filter_text)
        bar.addWidget(self.search, stretch=2)

        self.cat = QComboBox()
        self.cat.addItems(["All", "Known", "Unknown", "Alerts", "Favorites"])
        self.cat.currentTextChanged.connect(self._on_category)
        bar.addWidget(self.cat)

        self.count_label = QLabel("0 signals")
        bar.addWidget(self.count_label)
        root.addLayout(bar)

        # Tabbed views: flat detections table, category tree, history.
        self.tabs = QTabWidget()

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
        self.tabs.addTab(self.table, "Detections")

        # Category tree grouping.
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Signal", "Power", "Status"])
        self.tree.itemDoubleClicked.connect(self._on_tree_double_click)
        self.tabs.addTab(self.tree, "By category")

        # History panel.
        self.history_list = QListWidget()
        self.tabs.addTab(self.history_list, "History")
        root.addWidget(self.tabs)

        # Quick-tune panel.
        qt = QHBoxLayout()
        qt.addWidget(QLabel("Quick tune (MHz):"))
        self.quick_freq = QLineEdit()
        self.quick_freq.setPlaceholderText("e.g. 100.100")
        qt.addWidget(self.quick_freq, stretch=1)
        b0 = QPushButton("→ RX0")
        b0.clicked.connect(lambda: self._quick_tune(0))
        qt.addWidget(b0)
        b1 = QPushButton("→ RX1")
        b1.clicked.connect(lambda: self._quick_tune(1))
        qt.addWidget(b1)
        bb = QPushButton("Bookmark")
        bb.clicked.connect(self._quick_bookmark)
        qt.addWidget(bb)
        root.addLayout(qt)

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
        self._add_history(sig)
        self._rebuild_tree()

    def _add_history(self, sig: dict) -> None:
        freq = float(sig.get("freq_hz", 0.0))
        stamp = time.strftime("%H:%M:%S", time.localtime(sig.get("_seen")))
        status = ("ALERT" if sig.get("_alert") else
                  ("Known" if sig.get("is_known") else "Unknown"))
        self._history.append(sig)
        self.history_list.insertItem(
            0, f"{stamp}  {freq/1e6:.4f} MHz  [{status}]  "
               f"{float(sig.get('power_db', 0.0)):.1f} dBm")
        while self.history_list.count() > 300:
            self.history_list.takeItem(self.history_list.count() - 1)

    def _rebuild_tree(self) -> None:
        self.tree.clear()
        groups: Dict[str, QTreeWidgetItem] = {}
        for key, sig in sorted(self._data.items()):
            match = sig.get("signal_db_match") or {}
            cat = (match.get("category") or sig.get("category")
                   or ("Known" if sig.get("is_known") else "Unknown"))
            if cat not in groups:
                groups[cat] = QTreeWidgetItem(self.tree, [cat, "", ""])
            freq = float(sig.get("freq_hz", 0.0))
            star = "★ " if key in self._favorites else ""
            name = match.get("name", "") if match else ""
            label = f"{star}{freq/1e6:.4f} MHz{(' — ' + name) if name else ''}"
            status = ("ALERT" if sig.get("_alert") else
                      ("Known" if sig.get("is_known") else "Unknown"))
            child = QTreeWidgetItem(
                groups[cat],
                [label, f"{float(sig.get('power_db', 0.0)):.1f}", status])
            child.setData(0, Qt.ItemDataRole.UserRole, freq)
        self.tree.expandAll()

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
        key = int(round(float(sig.get("freq_hz", 0.0)) / 1e3)) if sig else 0
        if cat == "Known" and not sig.get("is_known"):
            visible = False
        elif cat == "Unknown" and sig.get("is_known"):
            visible = False
        elif cat == "Alerts" and not sig.get("_alert"):
            visible = False
        elif cat == "Favorites" and key not in self._favorites:
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
        key = int(round(freq / 1e3))
        is_fav = key in self._favorites
        menu.addAction("★ Remove favorite" if is_fav else "☆ Add favorite",
                       lambda: self._toggle_favorite(freq))
        menu.addAction("Add frequency bookmark…",
                       lambda: self.bookmark_requested.emit(
                           self._signal_to_bookmark(sig)))
        menu.addSeparator()
        menu.addAction("Mark as drone",
                       lambda: self.mark_drone_requested.emit(sig))
        menu.addAction("Send to ATAK (Signal of Interest)",
                       lambda: self.send_atak_requested.emit(sig))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    # Favorites / bookmarks / quick-tune / tree
    # ------------------------------------------------------------------
    def _toggle_favorite(self, freq_hz: float) -> None:
        key = int(round(freq_hz / 1e3))
        if key in self._favorites:
            self._favorites.discard(key)
            fav = False
        else:
            self._favorites.add(key)
            fav = True
        self.favorite_toggled.emit(freq_hz, fav)
        self._reapply_filters()
        self._rebuild_tree()

    @staticmethod
    def _signal_to_bookmark(sig: dict) -> dict:
        freq = float(sig.get("freq_hz", 0.0))
        match = sig.get("signal_db_match") or {}
        return {
            "freq_hz": freq,
            "name": match.get("name", "") or f"{freq/1e6:.4f} MHz",
            "modulation": sig.get("modulation_hint", ""),
            "bandwidth_hz": float(sig.get("bandwidth_hz", 0.0)),
            "category": match.get("category", "detection"),
        }

    def _quick_tune(self, channel: int) -> None:
        try:
            freq = float(self.quick_freq.text()) * 1e6
        except ValueError:
            return
        if channel == 1:
            self.tune_rx1_requested.emit(freq)
        else:
            self.tune_rx0_requested.emit(freq)

    def _quick_bookmark(self) -> None:
        try:
            freq = float(self.quick_freq.text()) * 1e6
        except ValueError:
            return
        self.bookmark_requested.emit({
            "freq_hz": freq, "name": f"{freq/1e6:.4f} MHz",
            "category": "manual"})

    def _on_tree_double_click(self, item, _col) -> None:
        freq = item.data(0, Qt.ItemDataRole.UserRole)
        if freq:
            self.tune_rx0_requested.emit(float(freq))
