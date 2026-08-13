"""Rich signal-database browser tab.

A full-featured workspace for the known-signals SQLite table:

* searchable, sortable table of all known signals
* filter sidebar (by modulation and category)
* add / edit / delete signals
* import / export as CSV or JSON
* a detail pane for the selected signal
* tune-to-signal and find-in-area actions

Backward-compatible with the previous ``tool_views.SignalDatabaseView``:
exposes ``search_requested``, ``add_requested``, ``delete_requested``,
``export_requested`` and ``refresh_requested`` signals, a ``.search`` line edit
and a ``set_signals(list)`` method. Additional signals (``edit_requested``,
``tune_requested``, ``import_requested``, ``find_in_area_requested``) are opt-in
and safely ignored if the host window does not connect them.
"""
from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QGroupBox, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QListWidget,
                             QMessageBox, QPushButton, QSplitter, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)


class SignalDatabaseView(QWidget):
    """Browse / search / filter / edit / import-export known signals."""

    # Backward-compatible signals.
    search_requested = pyqtSignal(str)
    add_requested = pyqtSignal(dict)
    delete_requested = pyqtSignal(int)
    export_requested = pyqtSignal()
    refresh_requested = pyqtSignal()
    # New optional signals.
    edit_requested = pyqtSignal(dict)
    tune_requested = pyqtSignal(float)
    import_requested = pyqtSignal(str)
    find_in_area_requested = pyqtSignal(float, float)   # freq_hz, span_hz

    COLS = ["ID", "Name", "Start (MHz)", "End (MHz)", "Modulation",
            "Category", "Description"]

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._signals: List[dict] = []
        root = QVBoxLayout(self)

        # -- top search / action bar --------------------------------------
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Full-text search known signals…")
        self.search.returnPressed.connect(
            lambda: self.search_requested.emit(self.search.text()))
        bar.addWidget(self.search, stretch=2)
        sb = QPushButton("Search")
        sb.clicked.connect(lambda: self.search_requested.emit(self.search.text()))
        bar.addWidget(sb)
        rb = QPushButton("Show All")
        rb.clicked.connect(self._show_all)
        bar.addWidget(rb)
        ib = QPushButton("Import…")
        ib.clicked.connect(self._import)
        bar.addWidget(ib)
        eb = QPushButton("Export…")
        eb.clicked.connect(self._export)
        bar.addWidget(eb)
        root.addLayout(bar)

        # -- main splitter: filter sidebar | table | detail ---------------
        split = QSplitter(Qt.Orientation.Horizontal)

        # Filter sidebar.
        side = QWidget()
        sv = QVBoxLayout(side)
        sv.addWidget(QLabel("<b>Filters</b>"))
        sv.addWidget(QLabel("Modulation:"))
        self.mod_filter = QComboBox()
        self.mod_filter.currentTextChanged.connect(self._apply_filters)
        sv.addWidget(self.mod_filter)
        sv.addWidget(QLabel("Category:"))
        self.cat_filter = QComboBox()
        self.cat_filter.currentTextChanged.connect(self._apply_filters)
        sv.addWidget(self.cat_filter)
        sv.addStretch(1)
        self.count = QLabel("0 known signals")
        sv.addWidget(self.count)
        split.addWidget(side)

        # Table.
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive)
        self.table.currentCellChanged.connect(lambda *a: self._show_detail())
        self.table.itemDoubleClicked.connect(lambda *a: self._tune())
        split.addWidget(self.table)

        # Detail pane.
        detail = QWidget()
        dv = QVBoxLayout(detail)
        dv.addWidget(QLabel("<b>Details</b>"))
        self.detail = QListWidget()
        dv.addWidget(self.detail)
        self.tune_btn = QPushButton("Tune RX0 to signal")
        self.tune_btn.clicked.connect(self._tune)
        dv.addWidget(self.tune_btn)
        self.area_btn = QPushButton("Find in area (±1 MHz)")
        self.area_btn.clicked.connect(self._find_in_area)
        dv.addWidget(self.area_btn)
        split.addWidget(detail)

        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 4)
        split.setStretchFactor(2, 2)
        root.addWidget(split, stretch=3)

        # -- add / edit form ----------------------------------------------
        add_box = QGroupBox("Add / edit known signal")
        af = QHBoxLayout(add_box)
        self.a_name = QLineEdit(); self.a_name.setPlaceholderText("Name")
        self.a_start = QLineEdit(); self.a_start.setPlaceholderText("Start MHz")
        self.a_end = QLineEdit(); self.a_end.setPlaceholderText("End MHz")
        self.a_mod = QLineEdit(); self.a_mod.setPlaceholderText("Modulation")
        self.a_cat = QLineEdit(); self.a_cat.setPlaceholderText("Category")
        self.a_desc = QLineEdit(); self.a_desc.setPlaceholderText("Description")
        for w in (self.a_name, self.a_start, self.a_end, self.a_mod,
                  self.a_cat, self.a_desc):
            af.addWidget(w)
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._emit_add)
        af.addWidget(add_btn)
        edit_btn = QPushButton("Save Edit")
        edit_btn.clicked.connect(self._emit_edit)
        af.addWidget(edit_btn)
        del_btn = QPushButton("Delete Selected")
        del_btn.clicked.connect(self._emit_delete)
        af.addWidget(del_btn)
        root.addWidget(add_box)

    # ------------------------------------------------------------------
    # Data population
    # ------------------------------------------------------------------
    def set_signals(self, signals: List[dict]) -> None:
        self._signals = list(signals)
        self._refresh_filter_options()
        self._apply_filters()

    def _refresh_filter_options(self) -> None:
        mods = sorted({str(s.get("modulation", "") or "") for s in self._signals
                       if s.get("modulation")})
        cats = sorted({str(s.get("category", "") or "") for s in self._signals
                       if s.get("category")})
        for combo, values in ((self.mod_filter, mods), (self.cat_filter, cats)):
            cur = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("All")
            combo.addItems(values)
            idx = combo.findText(cur)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

    def _apply_filters(self, *args) -> None:
        mod = self.mod_filter.currentText()
        cat = self.cat_filter.currentText()
        rows = [s for s in self._signals
                if (mod in ("", "All") or str(s.get("modulation", "")) == mod)
                and (cat in ("", "All") or str(s.get("category", "")) == cat)]
        self._populate(rows)

    def _populate(self, signals: List[dict]) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(signals))
        for row, s in enumerate(signals):
            start = float(s.get("freq_start_hz", s.get("freq_hz", 0)) or 0) / 1e6
            end = float(s.get("freq_end_hz", start * 1e6) or start * 1e6) / 1e6
            vals = [s.get("id", ""), s.get("name", ""), f"{start:.4f}",
                    f"{end:.4f}", s.get("modulation", ""),
                    s.get("category", ""), s.get("description", "")]
            for col, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if col == 0 and str(v).isdigit():
                    item.setData(Qt.ItemDataRole.UserRole, int(v))
                self.table.setItem(row, col, item)
        self.table.setSortingEnabled(True)
        self.count.setText(f"{len(signals)} known signals "
                           f"(of {len(self._signals)})")

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------
    def _selected_row_values(self) -> Optional[dict]:
        row = self.table.currentRow()
        if row < 0:
            return None
        def cell(c):
            it = self.table.item(row, c)
            return it.text() if it else ""
        return {
            "id": cell(0), "name": cell(1), "start": cell(2), "end": cell(3),
            "modulation": cell(4), "category": cell(5), "description": cell(6),
        }

    def _show_detail(self) -> None:
        vals = self._selected_row_values()
        self.detail.clear()
        if not vals:
            return
        self.detail.addItem(f"Name: {vals['name']}")
        self.detail.addItem(f"ID: {vals['id']}")
        self.detail.addItem(f"Start: {vals['start']} MHz")
        self.detail.addItem(f"End: {vals['end']} MHz")
        self.detail.addItem(f"Modulation: {vals['modulation']}")
        self.detail.addItem(f"Category: {vals['category']}")
        self.detail.addItem(f"Description: {vals['description']}")
        # Populate edit form with the selection.
        self.a_name.setText(vals["name"])
        self.a_start.setText(vals["start"])
        self.a_end.setText(vals["end"])
        self.a_mod.setText(vals["modulation"])
        self.a_cat.setText(vals["category"])
        self.a_desc.setText(vals["description"])

    def _center_freq_hz(self) -> Optional[float]:
        vals = self._selected_row_values()
        if not vals:
            return None
        try:
            start = float(vals["start"]) * 1e6
            end = float(vals["end"] or vals["start"]) * 1e6
            return (start + end) / 2.0
        except ValueError:
            return None

    def _tune(self) -> None:
        freq = self._center_freq_hz()
        if freq is not None:
            self.tune_requested.emit(freq)

    def _find_in_area(self) -> None:
        freq = self._center_freq_hz()
        if freq is not None:
            self.find_in_area_requested.emit(freq, 2e6)

    # ------------------------------------------------------------------
    # CRUD emit helpers
    # ------------------------------------------------------------------
    def _payload_from_form(self) -> Optional[dict]:
        try:
            start = float(self.a_start.text() or 0) * 1e6
            end = float(self.a_end.text() or self.a_start.text() or 0) * 1e6
        except ValueError:
            return None
        if not self.a_name.text():
            return None
        return {
            "name": self.a_name.text(),
            "freq_start_hz": start,
            "freq_end_hz": end,
            "modulation": self.a_mod.text(),
            "category": self.a_cat.text(),
            "description": self.a_desc.text(),
        }

    def _emit_add(self) -> None:
        payload = self._payload_from_form()
        if payload:
            self.add_requested.emit(payload)

    def _emit_edit(self) -> None:
        payload = self._payload_from_form()
        vals = self._selected_row_values()
        if payload and vals and str(vals["id"]).isdigit():
            payload["id"] = int(vals["id"])
            self.edit_requested.emit(payload)

    def _emit_delete(self) -> None:
        vals = self._selected_row_values()
        if vals and str(vals["id"]).isdigit():
            self.delete_requested.emit(int(vals["id"]))

    def _show_all(self) -> None:
        self.search.clear()
        self.mod_filter.setCurrentIndex(0)
        self.cat_filter.setCurrentIndex(0)
        self.refresh_requested.emit()

    def _export(self) -> None:
        self.export_requested.emit()

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import signals", "",
            "Signal files (*.json *.csv);;All files (*)")
        if path:
            self.import_requested.emit(path)
