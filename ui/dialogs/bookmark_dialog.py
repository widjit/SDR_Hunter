"""Frequency bookmark manager dialog.

Presents the :class:`core.bookmark_manager.BookmarkManager` contents as a
folder tree on the left and a table of bookmarks on the right, with full CRUD,
JSON/CSV import-export and a *Tune* action that emits :pyattr:`tune_requested`
so the main window can retune RX0 to the selected bookmark.

The dialog is defensive: every persistence / IO operation is wrapped so a bad
file never crashes the GUI.
"""
from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                             QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMessageBox,
                             QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from core.bookmark_manager import Bookmark, BookmarkManager


class _BookmarkEditDialog(QDialog):
    """Small add/edit form for a single bookmark."""

    def __init__(self, folders: List[str], bm: Optional[Bookmark] = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Bookmark" if bm else "Add Bookmark")
        self.resize(360, 300)
        form = QFormLayout(self)

        self.freq = QLineEdit(self)
        self.name = QLineEdit(self)
        self.modulation = QLineEdit(self)
        self.bandwidth = QLineEdit(self)
        self.category = QLineEdit(self)
        self.folder = QComboBox(self)
        self.folder.setEditable(True)
        self.folder.addItems(sorted(set(folders) | {"Default"}))
        self.color = QLineEdit(self)
        self.notes = QLineEdit(self)

        if bm is not None:
            self.freq.setText(f"{bm.freq_hz/1e6:.6f}")
            self.name.setText(bm.name)
            self.modulation.setText(bm.modulation)
            self.bandwidth.setText(f"{bm.bandwidth_hz/1e3:.3f}")
            self.category.setText(bm.category)
            self.folder.setCurrentText(bm.folder)
            self.color.setText(bm.color)
            self.notes.setText(bm.notes)
        else:
            self.color.setText("#33ffcc")
            self.folder.setCurrentText("Default")
            self.category.setText("General")

        form.addRow("Frequency (MHz):", self.freq)
        form.addRow("Name:", self.name)
        form.addRow("Modulation:", self.modulation)
        form.addRow("Bandwidth (kHz):", self.bandwidth)
        form.addRow("Category:", self.category)
        form.addRow("Folder:", self.folder)
        form.addRow("Color:", self.color)
        form.addRow("Notes:", self.notes)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> Optional[dict]:
        try:
            freq_hz = float(self.freq.text()) * 1e6
        except (TypeError, ValueError):
            return None
        if freq_hz <= 0:
            return None
        try:
            bw_hz = float(self.bandwidth.text() or 0) * 1e3
        except (TypeError, ValueError):
            bw_hz = 0.0
        return {
            "freq_hz": freq_hz,
            "name": self.name.text().strip(),
            "modulation": self.modulation.text().strip(),
            "bandwidth_hz": bw_hz,
            "category": self.category.text().strip() or "General",
            "folder": self.folder.currentText().strip() or "Default",
            "color": self.color.text().strip() or "#33ffcc",
            "notes": self.notes.text().strip(),
        }


class BookmarkDialog(QDialog):
    """Browse, organise, import/export and tune frequency bookmarks."""

    tune_requested = pyqtSignal(float)

    _ALL_FOLDERS = "\u2014 All folders \u2014"

    def __init__(self, manager: BookmarkManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Frequency Bookmarks")
        self.resize(820, 520)
        self.manager = manager
        self._rows: List[Bookmark] = []

        root = QHBoxLayout(self)

        # -- Left: folder list -----------------------------------------
        left = QVBoxLayout()
        left.addWidget(QLabel("Folders"))
        self.folder_list = QListWidget(self)
        self.folder_list.itemSelectionChanged.connect(self._refresh_table)
        left.addWidget(self.folder_list, 1)
        root.addLayout(left, 0)

        # -- Right: table + controls -----------------------------------
        right = QVBoxLayout()
        self.table = QTableWidget(0, 6, self)
        self.table.setHorizontalHeaderLabels(
            ["Frequency", "Name", "Mod", "BW (kHz)", "Category", "Folder"])
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self._tune_selected)
        self.table.horizontalHeader().setStretchLastSection(True)
        right.addWidget(self.table, 1)

        btn_row = QHBoxLayout()
        for text, slot in (
            ("Add", self._add), ("Edit", self._edit),
            ("Delete", self._delete), ("Tune RX0", self._tune_selected),
        ):
            b = QPushButton(text, self)
            b.clicked.connect(slot)
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        right.addLayout(btn_row)

        io_row = QHBoxLayout()
        for text, slot in (
            ("Import JSON", self._import_json),
            ("Export JSON", self._export_json),
            ("Import CSV", self._import_csv),
            ("Export CSV", self._export_csv),
        ):
            b = QPushButton(text, self)
            b.clicked.connect(slot)
            io_row.addWidget(b)
        io_row.addStretch(1)
        close = QPushButton("Close", self)
        close.clicked.connect(self.accept)
        io_row.addWidget(close)
        right.addLayout(io_row)

        root.addLayout(right, 1)

        self._refresh_folders()

    # ------------------------------------------------------------------
    # Refresh helpers
    # ------------------------------------------------------------------
    def _refresh_folders(self) -> None:
        current = None
        item = self.folder_list.currentItem()
        if item is not None:
            current = item.text()
        self.folder_list.blockSignals(True)
        self.folder_list.clear()
        self.folder_list.addItem(self._ALL_FOLDERS)
        for folder in self.manager.folders():
            self.folder_list.addItem(folder)
        # restore selection
        target = 0
        if current:
            for i in range(self.folder_list.count()):
                if self.folder_list.item(i).text() == current:
                    target = i
                    break
        self.folder_list.setCurrentRow(target)
        self.folder_list.blockSignals(False)
        self._refresh_table()

    def _selected_folder(self) -> Optional[str]:
        item = self.folder_list.currentItem()
        if item is None or item.text() == self._ALL_FOLDERS:
            return None
        return item.text()

    def _refresh_table(self) -> None:
        folder = self._selected_folder()
        rows = (self.manager.in_folder(folder) if folder
                else self.manager.all())
        self._rows = rows
        self.table.setRowCount(len(rows))
        for r, bm in enumerate(rows):
            values = [
                f"{bm.freq_hz/1e6:.4f} MHz", bm.name, bm.modulation,
                f"{bm.bandwidth_hz/1e3:.1f}" if bm.bandwidth_hz else "",
                bm.category, bm.folder,
            ]
            for c, val in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(str(val)))

    def _current_bookmark(self) -> Optional[Bookmark]:
        row = self.table.currentRow()
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    # ------------------------------------------------------------------
    # CRUD actions
    # ------------------------------------------------------------------
    def _add(self) -> None:
        dlg = _BookmarkEditDialog(self.manager.folders(), None, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        vals = dlg.values()
        if not vals:
            QMessageBox.warning(self, "Bookmark", "Invalid frequency.")
            return
        try:
            self.manager.add(**vals)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Bookmark", f"Add failed: {exc}")
            return
        self._refresh_folders()

    def _edit(self) -> None:
        bm = self._current_bookmark()
        if bm is None:
            return
        dlg = _BookmarkEditDialog(self.manager.folders(), bm, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        vals = dlg.values()
        if not vals:
            QMessageBox.warning(self, "Bookmark", "Invalid frequency.")
            return
        try:
            self.manager.update(bm.uid, **vals)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Bookmark", f"Update failed: {exc}")
            return
        self._refresh_folders()

    def _delete(self) -> None:
        bm = self._current_bookmark()
        if bm is None:
            return
        if QMessageBox.question(
                self, "Delete Bookmark",
                f"Delete '{bm.name or bm.freq_hz}'?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.manager.delete(bm.uid)
        self._refresh_folders()

    def _tune_selected(self) -> None:
        bm = self._current_bookmark()
        if bm is not None:
            self.tune_requested.emit(float(bm.freq_hz))

    # ------------------------------------------------------------------
    # Import / export
    # ------------------------------------------------------------------
    def _import_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import bookmarks (JSON)", "", "JSON files (*.json)")
        if not path:
            return
        try:
            n = self.manager.import_json(path, merge=True)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Import", f"Import failed: {exc}")
            return
        QMessageBox.information(self, "Import", f"Imported {n} bookmarks.")
        self._refresh_folders()

    def _export_json(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export bookmarks (JSON)", "bookmarks.json",
            "JSON files (*.json)")
        if not path:
            return
        try:
            self.manager.export_json(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Export", f"Export failed: {exc}")
            return
        QMessageBox.information(self, "Export", f"Saved to {path}.")

    def _import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import bookmarks (CSV)", "", "CSV files (*.csv)")
        if not path:
            return
        try:
            n = self.manager.import_csv(path, merge=True)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Import", f"Import failed: {exc}")
            return
        QMessageBox.information(self, "Import", f"Imported {n} bookmarks.")
        self._refresh_folders()

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export bookmarks (CSV)", "bookmarks.csv",
            "CSV files (*.csv)")
        if not path:
            return
        try:
            self.manager.export_csv(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Export", f"Export failed: {exc}")
            return
        QMessageBox.information(self, "Export", f"Saved to {path}.")
