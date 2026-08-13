"""Lightweight dock panels: signal intelligence, baseline, recording, audio,
ATAK status and drone-events summary.

Each is a self-contained ``QWidget`` designed to live inside a ``QDockWidget``.
They emit request signals that :class:`ui.main_window.MainWindow` wires to
``AppState``. All of them render safely with no hardware / no data.
"""
from __future__ import annotations

import time
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
                             QLabel, QLineEdit, QListWidget, QProgressBar,
                             QPushButton, QVBoxLayout, QWidget)


class LEDIndicator(QLabel):
    """Small colored LED-style status dot."""

    COLORS = {"idle": "#555a70", "active": "#17c964", "error": "#ff6b6b",
              "warn": "#f5d76e"}

    def __init__(self, label: str = ""):
        super().__init__()
        self._label = label
        self.set_state("idle")

    def set_state(self, state: str) -> None:
        color = self.COLORS.get(state, "#555a70")
        self.setText(f"● {self._label}")
        self.setStyleSheet(f"color: {color}; font-weight: bold;")


class SignalIntelPanel(QWidget):
    """Summary intelligence: counts by category and last unknown."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        form = QFormLayout(self)
        self.total = QLabel("0"); self.total.setProperty("readout", "true")
        self.known = QLabel("0"); self.known.setProperty("readout", "true")
        self.unknown = QLabel("0"); self.unknown.setProperty("readout", "true")
        self.alerts = QLabel("0"); self.alerts.setProperty("readout", "true")
        self.last_unknown = QLabel("—")
        form.addRow("Total detections", self.total)
        form.addRow("Known", self.known)
        form.addRow("Unknown", self.unknown)
        form.addRow("Alerts", self.alerts)
        form.addRow("Last unknown", self.last_unknown)
        self._t = self._k = self._u = self._a = 0

    def on_signal(self, sig: dict) -> None:
        self._t += 1
        if sig.get("is_known"):
            self._k += 1
        else:
            self._u += 1
            self.last_unknown.setText(f"{sig.get('freq_hz',0)/1e6:.4f} MHz")
        if sig.get("_alert"):
            self._a += 1
        self.total.setText(str(self._t))
        self.known.setText(str(self._k))
        self.unknown.setText(str(self._u))
        self.alerts.setText(str(self._a))


class BaselinePanel(QWidget):
    """Quick baseline capture/compare controls + anomaly list."""

    capture_requested = pyqtSignal()
    compare_toggled = pyqtSignal(bool)
    manage_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        row = QHBoxLayout()
        self.capture_btn = QPushButton("Capture Baseline")
        self.capture_btn.clicked.connect(self.capture_requested)
        self.compare_btn = QPushButton("Compare Live")
        self.compare_btn.setCheckable(True)
        self.compare_btn.toggled.connect(self.compare_toggled)
        row.addWidget(self.capture_btn)
        row.addWidget(self.compare_btn)
        root.addLayout(row)
        self.manage_btn = QPushButton("Baseline Manager…")
        self.manage_btn.clicked.connect(self.manage_requested)
        root.addWidget(self.manage_btn)
        root.addWidget(QLabel("Anomalies (live vs baseline):"))
        self.anomalies = QListWidget()
        root.addWidget(self.anomalies)

    def add_anomaly(self, anomaly: dict) -> None:
        kind = anomaly.get("kind", "?")
        f = anomaly.get("freq_hz", 0.0) / 1e6
        d = anomaly.get("delta_db", 0.0)
        self.anomalies.insertItem(0, f"[{kind}] {f:.4f} MHz  Δ{d:+.1f} dB")
        if self.anomalies.count() > 200:
            self.anomalies.takeItem(self.anomalies.count() - 1)


class RecordingPanel(QWidget):
    """Recording controls + list of recorded files."""

    record_toggled = pyqtSignal(bool)
    open_manager_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        self.status = QLabel("Idle")
        self.status.setProperty("readout", "true")
        root.addWidget(self.status)
        self.rec_btn = QPushButton("● Record RX1")
        self.rec_btn.setCheckable(True)
        self.rec_btn.toggled.connect(self._on_toggle)
        root.addWidget(self.rec_btn)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate when recording
        self.progress.setVisible(False)
        root.addWidget(self.progress)
        root.addWidget(QLabel("Recordings:"))
        self.files = QListWidget()
        root.addWidget(self.files)

    def _on_toggle(self, on: bool) -> None:
        self.status.setText("● RECORDING" if on else "Idle")
        self.progress.setVisible(on)
        self.record_toggled.emit(on)

    def set_recordings(self, recs: list) -> None:
        self.files.clear()
        for r in recs:
            self.files.addItem(str(r.get("data_path", r)))


class AudioPanel(QWidget):
    """Audio demod controls + live classification result."""

    demod_changed = pyqtSignal(str, float)   # mode, freq_hz
    mute_toggled = pyqtSignal(bool)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        form = QFormLayout(self)
        self.mode = QComboBox()
        self.mode.addItems(["WBFM", "NBFM", "AM", "USB", "LSB", "CW"])
        form.addRow("Demod mode", self.mode)
        self.freq = QLineEdit("100.100")
        self.freq.setProperty("readout", "true")
        form.addRow("Freq (MHz)", self.freq)
        self.classify = QLabel("—")
        self.classify.setProperty("readout", "true")
        form.addRow("Classified as", self.classify)
        self.meta = QLabel("—")
        form.addRow("Metadata", self.meta)
        self.mute = QPushButton("Mute")
        self.mute.setCheckable(True)
        self.mute.toggled.connect(self.mute_toggled)
        form.addRow(self.mute)

    def set_classification(self, name: str, confidence: float,
                           meta: str = "") -> None:
        self.classify.setText(f"{name} ({confidence*100:.0f}%)")
        if meta:
            self.meta.setText(meta)


class ATAKPanel(QWidget):
    """ATAK/CoT status and manual send-signal-of-interest button."""

    enable_toggled = pyqtSignal(bool)
    send_selected_requested = pyqtSignal()
    config_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        self.led = LEDIndicator("ATAK bridge")
        root.addWidget(self.led)
        self.enable = QPushButton("Enable CoT Output")
        self.enable.setCheckable(True)
        self.enable.toggled.connect(self._on_enable)
        root.addWidget(self.enable)
        self.send_btn = QPushButton("Send Selected Signal → ATAK")
        self.send_btn.clicked.connect(self.send_selected_requested)
        root.addWidget(self.send_btn)
        self.cfg_btn = QPushButton("ATAK Config…")
        self.cfg_btn.clicked.connect(self.config_requested)
        root.addWidget(self.cfg_btn)
        self.sent = QLabel("CoT sent: 0")
        self.sent.setProperty("readout", "true")
        root.addWidget(self.sent)
        root.addStretch(1)
        self._count = 0

    def _on_enable(self, on: bool) -> None:
        self.led.set_state("active" if on else "idle")
        self.enable_toggled.emit(on)

    def increment_sent(self, n: int = 1) -> None:
        self._count += n
        self.sent.setText(f"CoT sent: {self._count}")


class DroneEventsPanel(QWidget):
    """Compact drone summary; links to the full Drone Tracking tab."""

    open_tab_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        self.count = QLabel("0 active drones")
        self.count.setProperty("readout", "true")
        root.addWidget(self.count)
        self.events = QListWidget()
        root.addWidget(self.events)
        self.open_btn = QPushButton("Open Drone Tracking →")
        self.open_btn.clicked.connect(self.open_tab_requested)
        root.addWidget(self.open_btn)

    def on_drone(self, drone: dict) -> None:
        uid = drone.get("uid", "?")
        f = drone.get("freq_hz")
        conf = drone.get("confidence", 0.0)
        txt = f"{time.strftime('%H:%M:%S')}  {uid}"
        if f:
            txt += f"  {f/1e6:.3f} MHz"
        txt += f"  conf={conf:.2f}"
        self.events.insertItem(0, txt)
        if self.events.count() > 200:
            self.events.takeItem(self.events.count() - 1)

    def set_active_count(self, n: int) -> None:
        self.count.setText(f"{n} active drones")
