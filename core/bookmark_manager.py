"""Frequency bookmark system.

A *bookmark* pins a frequency (with a name, notes, category and color) so the
operator can jump back to it later. Bookmarks are organised into folders and
persisted as JSON. Import/export uses the same JSON layout, and a CSV export is
also provided for interchange with other SDR tools (SDR#, GQRX-style).
"""
from __future__ import annotations

import csv
import io
import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class Bookmark:
    """A single frequency bookmark."""

    freq_hz: float
    name: str = ""
    notes: str = ""
    category: str = "General"
    color: str = "#33ffcc"
    folder: str = "Default"
    modulation: str = ""
    bandwidth_hz: float = 0.0
    created_at: float = field(default_factory=time.time)
    uid: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Bookmark":
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in allowed})


class BookmarkManager:
    """CRUD + JSON persistence for frequency bookmarks."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._bookmarks: Dict[str, Bookmark] = {}
        self.load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def load(self) -> None:
        self._bookmarks.clear()
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            items = data.get("bookmarks", data) if isinstance(data, dict) else data
            for item in items:
                bm = Bookmark.from_dict(item)
                self._bookmarks[bm.uid] = bm
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    def save(self) -> str:
        doc = {"version": 1, "bookmarks": [b.to_dict() for b in
                                           self._sorted()]}
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
        return self.path

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def add(self, freq_hz: float, name: str = "", notes: str = "",
            category: str = "General", color: str = "#33ffcc",
            folder: str = "Default", modulation: str = "",
            bandwidth_hz: float = 0.0) -> Bookmark:
        bm = Bookmark(freq_hz=freq_hz, name=name or f"{freq_hz/1e6:.4f} MHz",
                      notes=notes, category=category, color=color,
                      folder=folder, modulation=modulation,
                      bandwidth_hz=bandwidth_hz)
        self._bookmarks[bm.uid] = bm
        self.save()
        return bm

    def add_bookmark(self, bm: Bookmark) -> Bookmark:
        self._bookmarks[bm.uid] = bm
        self.save()
        return bm

    def update(self, uid: str, **fields: Any) -> Optional[Bookmark]:
        bm = self._bookmarks.get(uid)
        if bm is None:
            return None
        for k, v in fields.items():
            if hasattr(bm, k):
                setattr(bm, k, v)
        self.save()
        return bm

    def delete(self, uid: str) -> bool:
        if uid in self._bookmarks:
            del self._bookmarks[uid]
            self.save()
            return True
        return False

    def clear(self) -> None:
        self._bookmarks.clear()
        self.save()

    def get(self, uid: str) -> Optional[Bookmark]:
        return self._bookmarks.get(uid)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def _sorted(self) -> List[Bookmark]:
        return sorted(self._bookmarks.values(),
                      key=lambda b: (b.folder.lower(), b.freq_hz))

    def all(self) -> List[Bookmark]:
        return self._sorted()

    def folders(self) -> List[str]:
        return sorted({b.folder for b in self._bookmarks.values()} or {"Default"})

    def in_folder(self, folder: str) -> List[Bookmark]:
        return [b for b in self._sorted() if b.folder == folder]

    def in_range(self, freq_start_hz: float, freq_end_hz: float
                 ) -> List[Bookmark]:
        lo, hi = min(freq_start_hz, freq_end_hz), max(freq_start_hz, freq_end_hz)
        return [b for b in self._sorted() if lo <= b.freq_hz <= hi]

    def as_dicts(self) -> List[Dict[str, Any]]:
        return [b.to_dict() for b in self._sorted()]

    # ------------------------------------------------------------------
    # Import / export
    # ------------------------------------------------------------------
    def export_json(self, path: str) -> str:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "bookmarks": self.as_dicts()}, fh, indent=2)
        return path

    def import_json(self, path: str, merge: bool = True) -> int:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        items = data.get("bookmarks", data) if isinstance(data, dict) else data
        if not merge:
            self._bookmarks.clear()
        count = 0
        for item in items:
            try:
                bm = Bookmark.from_dict(item)
                self._bookmarks[bm.uid] = bm
                count += 1
            except (TypeError, ValueError):
                continue
        self.save()
        return count

    def export_csv(self, path: str) -> str:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["freq_hz", "name", "modulation", "bandwidth_hz",
                        "category", "folder", "notes"])
            for b in self._sorted():
                w.writerow([b.freq_hz, b.name, b.modulation, b.bandwidth_hz,
                            b.category, b.folder, b.notes])
        return path

    def import_csv(self, path: str, merge: bool = True) -> int:
        if not merge:
            self._bookmarks.clear()
        count = 0
        with open(path, "r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                try:
                    freq = float(row.get("freq_hz") or row.get("Frequency") or 0)
                except (TypeError, ValueError):
                    continue
                if freq <= 0:
                    continue
                bm = Bookmark(
                    freq_hz=freq, name=row.get("name", ""),
                    modulation=row.get("modulation", ""),
                    bandwidth_hz=float(row.get("bandwidth_hz") or 0),
                    category=row.get("category", "General"),
                    folder=row.get("folder", "Default"),
                    notes=row.get("notes", ""))
                self._bookmarks[bm.uid] = bm
                count += 1
        self.save()
        return count
