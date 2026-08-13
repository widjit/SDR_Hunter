"""Dock layout / tile manager.

Wraps Qt's native dock system with named preset layouts and JSON persistence of
custom layouts. Qt already provides drag-and-drop docking and
``saveState``/``restoreState``; this class stores those blobs (base64-encoded)
under named presets in a JSON file in the config dir.
"""
from __future__ import annotations

import base64
import json
import os
from typing import TYPE_CHECKING, Dict, List, Optional

from PyQt6.QtCore import Qt

if TYPE_CHECKING:  # pragma: no cover
    from .main_window import MainWindow

# Built-in preset names. Their concrete arrangement is applied procedurally in
# :meth:`TileManager.apply_preset` because docks may not all exist yet at first
# save. Custom presets are saved as raw Qt state blobs.
BUILTIN_PRESETS = ["Standard", "Spectrum Hunting", "Drone Ops",
                   "Dual Signal Analysis", "Compact"]


class TileManager:
    """Manage dock widget layouts and presets."""

    def __init__(self, window: "MainWindow", config_dir: str):
        self.window = window
        self.config_dir = config_dir
        os.makedirs(config_dir, exist_ok=True)
        self.path = os.path.join(config_dir, "layouts.json")
        self._custom: Dict[str, str] = {}
        self._load()

    # ------------------------------------------------------------------
    def preset_names(self) -> List[str]:
        return BUILTIN_PRESETS + sorted(self._custom.keys())

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    self._custom = json.load(fh)
            except Exception:  # noqa: BLE001
                self._custom = {}

    def _save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self._custom, fh, indent=2)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    def save_current_as(self, name: str) -> None:
        state = bytes(self.window.saveState())
        self._custom[name] = base64.b64encode(state).decode("ascii")
        self._save()

    def apply_preset(self, name: str) -> bool:
        """Apply a named preset. Returns True if applied."""
        if name in self._custom:
            try:
                blob = base64.b64decode(self._custom[name])
                from PyQt6.QtCore import QByteArray
                return bool(self.window.restoreState(QByteArray(blob)))
            except Exception:  # noqa: BLE001
                return False
        return self._apply_builtin(name)

    # ------------------------------------------------------------------
    def _apply_builtin(self, name: str) -> bool:
        w = self.window
        docks = w.docks  # dict of name -> QDockWidget
        # Start from all visible & docked.
        for d in docks.values():
            d.setFloating(False)
            d.show()

        def only(*keep):
            for key, d in docks.items():
                d.setVisible(key in keep)

        if name == "Standard":
            for key, area in (
                ("device", Qt.DockWidgetArea.LeftDockWidgetArea),
                ("signal_intel", Qt.DockWidgetArea.LeftDockWidgetArea),
                ("baseline", Qt.DockWidgetArea.RightDockWidgetArea),
                ("recording", Qt.DockWidgetArea.RightDockWidgetArea),
                ("audio", Qt.DockWidgetArea.RightDockWidgetArea),
                ("atak", Qt.DockWidgetArea.BottomDockWidgetArea),
                ("drone", Qt.DockWidgetArea.BottomDockWidgetArea)):
                if key in docks:
                    w.addDockWidget(area, docks[key])
                    docks[key].show()
        elif name == "Spectrum Hunting":
            only("device", "signal_intel")
        elif name == "Drone Ops":
            only("drone", "atak", "signal_intel")
        elif name == "Dual Signal Analysis":
            only("device", "recording", "audio")
        elif name == "Compact":
            only()
        else:
            return False
        return True
