"""Tabbed application settings dialog.

Reads from / writes to a :class:`config.settings.Settings` object. Only a subset
of the fields map directly onto the Phase-1 Settings dataclass; the remaining
UI-only preferences (FFT size, window, detection thresholds, notifications,
appearance) are collected into an ``extras`` dict returned by :meth:`values`
so the caller can persist them alongside.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QDoubleSpinBox, QFormLayout, QLineEdit, QSpinBox,
                             QTabWidget, QVBoxLayout, QWidget)

from config.settings import Settings


class SettingsDialog(QDialog):
    """Multi-page preferences dialog."""

    def __init__(self, settings: Settings, extras: Optional[Dict[str, Any]] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Settings — SDR Hunter")
        self.resize(560, 520)
        self._settings = settings
        extras = extras or {}

        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        self._build_general(extras)
        self._build_sdr(extras)
        self._build_detection(extras)
        self._build_recording(extras)
        self._build_audio(extras)
        self._build_web()
        self._build_atak()
        self._build_notifications(extras)
        self._build_appearance(extras)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ------------------------------------------------------------------
    def _page(self, title: str) -> QFormLayout:
        page = QWidget()
        form = QFormLayout(page)
        self.tabs.addTab(page, title)
        return form

    def _build_general(self, extras) -> None:
        f = self._page("General")
        self.theme = QComboBox()
        self.theme.addItems(["dark", "light", "custom"])
        self.theme.setCurrentText(self._settings.theme)
        f.addRow("Theme", self.theme)
        self.font_size = QSpinBox()
        self.font_size.setRange(8, 20)
        self.font_size.setValue(extras.get("font_size", 12))
        f.addRow("Font size", self.font_size)
        self.units = QComboBox()
        self.units.addItems(["MHz", "kHz", "Hz"])
        self.units.setCurrentText(extras.get("units", "MHz"))
        f.addRow("Frequency units", self.units)

    def _build_sdr(self, extras) -> None:
        f = self._page("SDR Defaults")
        self.def_rate = QDoubleSpinBox()
        self.def_rate.setRange(0.1, 61.44)
        self.def_rate.setValue(self._settings.sdr.default_sample_rate / 1e6)
        self.def_rate.setSuffix(" MS/s")
        f.addRow("Default sample rate", self.def_rate)
        self.fft_size = QComboBox()
        self.fft_size.addItems(["512", "1024", "2048", "4096", "8192",
                                "16384", "32768", "65536"])
        self.fft_size.setCurrentText(str(self._settings.sdr.fft_size))
        f.addRow("FFT size", self.fft_size)
        self.window = QComboBox()
        self.window.addItems(["Hann", "Blackman", "Hamming", "Flat-top"])
        self.window.setCurrentText(extras.get("window", "Hann"))
        f.addRow("Window function", self.window)
        self.overlap = QSpinBox()
        self.overlap.setRange(0, 90)
        self.overlap.setValue(extras.get("overlap", 50))
        self.overlap.setSuffix(" %")
        f.addRow("Overlap", self.overlap)

    def _build_detection(self, extras) -> None:
        f = self._page("Detection")
        sdr = self._settings.sdr

        # --- Live detector tunables (persisted in SDRSettings.detect_*) -----
        f.addRow(self._section_label("Signal Detection (CFAR)"))
        self.detect_threshold = QDoubleSpinBox()
        self.detect_threshold.setRange(0.5, 60.0)
        self.detect_threshold.setDecimals(1)
        self.detect_threshold.setValue(sdr.detect_threshold_db)
        self.detect_threshold.setSuffix(" dB")
        self.detect_threshold.setToolTip("CFAR margin above the local noise "
                                         "average for a bin to count as signal")
        f.addRow("Detection threshold", self.detect_threshold)

        self.detect_min_snr = QDoubleSpinBox()
        self.detect_min_snr.setRange(0.0, 60.0)
        self.detect_min_snr.setDecimals(1)
        self.detect_min_snr.setValue(sdr.detect_min_snr_db)
        self.detect_min_snr.setSuffix(" dB")
        self.detect_min_snr.setToolTip("Final gate: peak power minus noise floor")
        f.addRow("Min SNR", self.detect_min_snr)

        self.detect_min_bw = QSpinBox()
        self.detect_min_bw.setRange(1, 512)
        self.detect_min_bw.setValue(sdr.detect_min_bin_width)
        self.detect_min_bw.setSuffix(" bins")
        self.detect_min_bw.setToolTip("Minimum contiguous FFT bins for a detection")
        f.addRow("Min bin width", self.detect_min_bw)

        self.detect_max_events = QSpinBox()
        self.detect_max_events.setRange(1, 1000)
        self.detect_max_events.setValue(sdr.detect_max_events)
        self.detect_max_events.setToolTip("Cap on detections per PSD frame")
        f.addRow("Max events/frame", self.detect_max_events)

        self.detect_guard = QSpinBox()
        self.detect_guard.setRange(0, 128)
        self.detect_guard.setValue(sdr.detect_guard_cells)
        self.detect_guard.setToolTip("CFAR guard cells each side of a cell")
        f.addRow("Guard cells", self.detect_guard)

        self.detect_train = QSpinBox()
        self.detect_train.setRange(1, 256)
        self.detect_train.setValue(sdr.detect_train_cells)
        self.detect_train.setToolTip("CFAR training cells each side of a cell")
        f.addRow("Training cells", self.detect_train)

        # --- Detection list (merge / expiry) -------------------------------
        f.addRow(self._section_label("Detection List"))
        self.detect_merge_khz = QDoubleSpinBox()
        self.detect_merge_khz.setRange(0.0, 5000.0)
        self.detect_merge_khz.setDecimals(1)
        self.detect_merge_khz.setValue(sdr.detect_merge_tolerance_hz / 1e3)
        self.detect_merge_khz.setSuffix(" kHz")
        self.detect_merge_khz.setToolTip("Merge detections within this distance "
                                        "into one list entry (0 disables merging)")
        f.addRow("Merge tolerance", self.detect_merge_khz)

        self.detect_max_age = QDoubleSpinBox()
        self.detect_max_age.setRange(0.0, 86400.0)
        self.detect_max_age.setDecimals(0)
        self.detect_max_age.setValue(sdr.detect_max_age_seconds)
        self.detect_max_age.setSuffix(" s")
        self.detect_max_age.setToolTip("Drop list entries not re-seen for this "
                                      "long (0 or less disables expiry)")
        f.addRow("Max age (0 = disabled)", self.detect_max_age)

        # --- UI-only extras (kept for backward compatibility) --------------
        f.addRow(self._section_label("Bandwidth / display"))
        self.min_dur = QDoubleSpinBox()
        self.min_dur.setRange(0.0, 10.0)
        self.min_dur.setValue(extras.get("min_signal_duration_s", 0.0))
        self.min_dur.setSuffix(" s")
        f.addRow("Min signal duration", self.min_dur)
        self.bw_method = QComboBox()
        self.bw_method.addItems(["contiguous-bins", "-3dB", "-6dB", "occupied-99%"])
        self.bw_method.setCurrentText(extras.get("bw_method", "contiguous-bins"))
        f.addRow("Bandwidth estimation", self.bw_method)

    @staticmethod
    def _section_label(text: str):
        from PyQt6.QtWidgets import QLabel
        lbl = QLabel(f"<b>{text}</b>")
        return lbl

    def _build_recording(self, extras) -> None:
        f = self._page("Recording")
        self.rec_path = QLineEdit(self._settings.recordings_dir)
        f.addRow("Save path", self.rec_path)
        self.rec_format = QComboBox()
        self.rec_format.addItems(["SigMF", "raw IQ (cf32)", "WAV"])
        self.rec_format.setCurrentText(extras.get("rec_format", "SigMF"))
        f.addRow("Format", self.rec_format)
        self.auto_dur = QSpinBox()
        self.auto_dur.setRange(5, 3600)
        self.auto_dur.setValue(self._settings.sdr.auto_record_seconds)
        self.auto_dur.setSuffix(" s")
        f.addRow("Auto-record duration", self.auto_dur)

    def _build_audio(self, extras) -> None:
        f = self._page("Audio")
        self.audio_out = QComboBox()
        self.audio_out.addItems(["Default", "System", "None (mute)"])
        f.addRow("Output device", self.audio_out)
        self.volume = QSpinBox()
        self.volume.setRange(0, 100)
        self.volume.setValue(extras.get("volume", 70))
        f.addRow("Volume", self.volume)
        self.squelch = QDoubleSpinBox()
        self.squelch.setRange(-120.0, 0.0)
        self.squelch.setValue(extras.get("squelch_db", -80.0))
        self.squelch.setSuffix(" dB")
        f.addRow("Squelch level", self.squelch)

    def _build_web(self) -> None:
        f = self._page("Web Server")
        self.web_enabled = QCheckBox("Enable web dashboard")
        self.web_enabled.setChecked(bool(self._settings.web.enabled))
        f.addRow(self.web_enabled)
        self.web_port = QSpinBox()
        self.web_port.setRange(1, 65535)
        self.web_port.setValue(self._settings.web.port)
        f.addRow("Port", self.web_port)
        self.web_host = QLineEdit(self._settings.web.host)
        f.addRow("Bind address", self.web_host)
        self.web_token = QLineEdit()
        self.web_token.setPlaceholderText("optional auth token")
        f.addRow("Auth token", self.web_token)

    def _build_atak(self) -> None:
        f = self._page("ATAK")
        self.atak_mcast = QLineEdit(self._settings.atak.multicast_group)
        f.addRow("Multicast group", self.atak_mcast)
        self.atak_mport = QSpinBox()
        self.atak_mport.setRange(1, 65535)
        self.atak_mport.setValue(self._settings.atak.multicast_port)
        f.addRow("Multicast port", self.atak_mport)
        self.atak_host = QLineEdit(self._settings.atak.unicast_host)
        f.addRow("TAK server IP", self.atak_host)
        self.atak_stale = QSpinBox()
        self.atak_stale.setRange(1, 3600)
        self.atak_stale.setValue(120)
        self.atak_stale.setSuffix(" s")
        f.addRow("CoT stale time", self.atak_stale)

    def _build_notifications(self, extras) -> None:
        f = self._page("Notifications")
        self.alert_sound = QCheckBox("Play alert sounds")
        self.alert_sound.setChecked(extras.get("alert_sound", True))
        f.addRow(self.alert_sound)
        self.notify_unknown = QCheckBox("Desktop notify on new unknowns")
        self.notify_unknown.setChecked(extras.get("notify_unknown", True))
        f.addRow(self.notify_unknown)
        self.notify_drone = QCheckBox("Desktop notify on drone detection")
        self.notify_drone.setChecked(extras.get("notify_drone", True))
        f.addRow(self.notify_drone)

    def _build_appearance(self, extras) -> None:
        f = self._page("Appearance")
        self.wf_cmap = QComboBox()
        self.wf_cmap.addItems(["viridis", "plasma", "inferno", "magma", "hot"])
        self.wf_cmap.setCurrentText(extras.get("wf_cmap", "viridis"))
        f.addRow("Waterfall colormap", self.wf_cmap)
        self.line_width = QSpinBox()
        self.line_width.setRange(1, 5)
        self.line_width.setValue(extras.get("line_width", 1))
        f.addRow("Spectrum line width", self.line_width)

    # ------------------------------------------------------------------
    def values(self) -> Dict[str, Any]:
        """Return (mutated) settings + extras dict."""
        s = self._settings
        s.theme = self.theme.currentText()
        s.sdr.default_sample_rate = self.def_rate.value() * 1e6
        s.sdr.fft_size = int(self.fft_size.currentText())
        s.sdr.auto_record_seconds = self.auto_dur.value()
        # Live detector tunables + detection-list merge/expiry.
        s.sdr.detect_threshold_db = self.detect_threshold.value()
        s.sdr.detect_min_snr_db = self.detect_min_snr.value()
        s.sdr.detect_min_bin_width = int(self.detect_min_bw.value())
        s.sdr.detect_max_events = int(self.detect_max_events.value())
        s.sdr.detect_guard_cells = int(self.detect_guard.value())
        s.sdr.detect_train_cells = int(self.detect_train.value())
        s.sdr.detect_merge_tolerance_hz = self.detect_merge_khz.value() * 1e3
        s.sdr.detect_max_age_seconds = self.detect_max_age.value()
        s.recordings_dir = self.rec_path.text()
        s.web.enabled = self.web_enabled.isChecked()
        s.web.port = self.web_port.value()
        s.web.host = self.web_host.text()
        s.atak.multicast_group = self.atak_mcast.text()
        s.atak.multicast_port = self.atak_mport.value()
        s.atak.unicast_host = self.atak_host.text()
        extras = {
            "font_size": self.font_size.value(),
            "units": self.units.currentText(),
            "window": self.window.currentText(),
            "overlap": self.overlap.value(),
            "min_signal_duration_s": self.min_dur.value(),
            "bw_method": self.bw_method.currentText(),
            "rec_format": self.rec_format.currentText(),
            "volume": self.volume.value(),
            "squelch_db": self.squelch.value(),
            "web_token": self.web_token.text(),
            "cot_stale_s": self.atak_stale.value(),
            "alert_sound": self.alert_sound.isChecked(),
            "notify_unknown": self.notify_unknown.isChecked(),
            "notify_drone": self.notify_drone.isChecked(),
            "wf_cmap": self.wf_cmap.currentText(),
            "line_width": self.line_width.value(),
        }
        return {"settings": s, "extras": extras}
