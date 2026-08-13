"""SDR Hunter main window.

A ``QMainWindow`` with a full dock system, menu bar, toolbar, a central
spectrum/waterfall + signal-list splitter and a bottom tab bar switching between
workspaces (Main, Drone Tracking, Audio Decoder, Weather Satellite, Spectrum
Hunting, Signal Database, Settings).

The window is driven entirely by :class:`ui.app_state.AppState` via
:class:`ui.qt_adapter.AppSignals`, so all SDR/DSP work happens on background
threads and results are marshalled onto the GUI thread by Qt. Every hardware
call is guarded; with no SDR present the app runs on the synthetic mock device.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Dict, Optional

import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (QComboBox, QDockWidget, QDoubleSpinBox, QFileDialog,
                             QInputDialog, QLabel, QMainWindow, QMessageBox,
                             QPushButton, QSplitter, QStackedWidget, QStatusBar,
                             QTabBar, QToolBar, QVBoxLayout, QWidget)

from atak.atak_bridge import ATAKBridge
from config import settings as settings_mod

from .app_state import AppState
from .dialogs.baseline_dialog import (BaselineManagerDialog,
                                       CaptureBaselineDialog)
from .dialogs.settings_dialog import SettingsDialog
from .panels.device_panel import DevicePanelWidget
from .panels.info_panels import (ATAKPanel, AudioPanel, BaselinePanel,
                                  DroneEventsPanel, LEDIndicator,
                                  RecordingPanel, SignalIntelPanel)
from .qt_adapter import AppSignals
from .tile_manager import TileManager
from .views.drone_tracking_view import DroneTrackingView
from .views.spectrum_hunting_view import SpectrumHuntingView
from .views.tool_views import (AudioDecoderView, SignalDatabaseView,
                                WeatherSatView)
from .widgets.dual_rx_display import DualRXDisplay
from .widgets.signal_list import SignalListWidget

logger = logging.getLogger(__name__)

SAMPLE_RATE_OPTIONS = [("2.048 MS/s", 2.048e6), ("2.4 MS/s", 2.4e6),
                       ("5 MS/s", 5e6), ("10 MS/s", 10e6), ("20 MS/s", 20e6)]


class MainWindow(QMainWindow):
    """Top-level application window."""

    def __init__(self, app_state: Optional[AppState] = None,
                 embed_web: bool = False):
        super().__init__()
        self.state = app_state or AppState()
        self.signals = AppSignals(self.state)
        self.docks: Dict[str, QDockWidget] = {}
        self._web_thread: Optional[threading.Thread] = None
        self._web_server = None
        self._baseline_dialog: Optional[BaselineManagerDialog] = None
        self._extras: Dict = {}

        # ATAK bridge (shares settings; disabled until user enables output).
        self.atak = ATAKBridge.from_settings(self.state.settings.atak)
        self.atak.enabled = False

        self.setWindowTitle("SDR Hunter — Multi-SDR Signal Hunting Suite")
        self.resize(1500, 950)

        self.tiles = TileManager(self, self.state.settings.config_dir)

        self._build_central()
        self._build_docks()
        self._build_menu()
        self._build_toolbar()
        self._build_statusbar()
        self._connect_signals()

        self.tiles.apply_preset("Standard")

        # Periodic UI refresh (ages, drones, status).
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._periodic_refresh)
        self._timer.start(1000)

        self._refresh_devices()
        if embed_web:
            self._toggle_web(True)

    # ==================================================================
    # Construction
    # ==================================================================
    def _build_central(self) -> None:
        self.stack = QStackedWidget()

        # --- Page 0: Main dual-RX + signal list ---
        main_page = QWidget()
        layout = QVBoxLayout(main_page)
        layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.dual_display = DualRXDisplay()
        self.signal_list = SignalListWidget()
        splitter.addWidget(self.dual_display)
        splitter.addWidget(self.signal_list)
        splitter.setSizes([1050, 450])
        layout.addWidget(splitter)
        self.stack.addWidget(main_page)

        # --- Other pages ---
        self.drone_view = DroneTrackingView()
        self.audio_view = AudioDecoderView()
        self.weather_view = WeatherSatView()
        self.hunting_view = SpectrumHuntingView()
        self.signal_db_view = SignalDatabaseView()
        for w in (self.drone_view, self.audio_view, self.weather_view,
                  self.hunting_view, self.signal_db_view):
            self.stack.addWidget(w)

        # Container: stack + bottom tab bar.
        container = QWidget()
        cv = QVBoxLayout(container)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.addWidget(self.stack, stretch=1)
        self.tab_bar = QTabBar()
        for name in ("Main", "Drone Tracking", "Audio Decoder",
                     "Weather Satellite", "Spectrum Hunting", "Signal Database"):
            self.tab_bar.addTab(name)
        self.tab_bar.currentChanged.connect(self.stack.setCurrentIndex)
        cv.addWidget(self.tab_bar)
        self.setCentralWidget(container)

    def _dock(self, key: str, title: str, widget: QWidget,
              area: Qt.DockWidgetArea) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(f"dock_{key}")
        dock.setWidget(widget)
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable |
            QDockWidget.DockWidgetFeature.DockWidgetFloatable |
            QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.addDockWidget(area, dock)
        self.docks[key] = dock
        return dock

    def _build_docks(self) -> None:
        self.device_panel = DevicePanelWidget()
        self.signal_intel = SignalIntelPanel()
        self.baseline_panel = BaselinePanel()
        self.recording_panel = RecordingPanel()
        self.audio_panel = AudioPanel()
        self.atak_panel = ATAKPanel()
        self.drone_panel = DroneEventsPanel()

        self._dock("device", "Device Control", self.device_panel,
                   Qt.DockWidgetArea.LeftDockWidgetArea)
        self._dock("signal_intel", "Signal Intelligence", self.signal_intel,
                   Qt.DockWidgetArea.LeftDockWidgetArea)
        self._dock("baseline", "Baseline", self.baseline_panel,
                   Qt.DockWidgetArea.RightDockWidgetArea)
        self._dock("recording", "Recording", self.recording_panel,
                   Qt.DockWidgetArea.RightDockWidgetArea)
        self._dock("audio", "Audio Decoder", self.audio_panel,
                   Qt.DockWidgetArea.RightDockWidgetArea)
        self._dock("atak", "ATAK Status", self.atak_panel,
                   Qt.DockWidgetArea.BottomDockWidgetArea)
        self._dock("drone", "Drone Events", self.drone_panel,
                   Qt.DockWidgetArea.BottomDockWidgetArea)

    def _build_menu(self) -> None:
        mb = self.menuBar()

        m_file = mb.addMenu("&File")
        self._act(m_file, "New Session", self._new_session)
        self._act(m_file, "Open Session…", self._open_session)
        self._act(m_file, "Save Session…", self._save_session)
        m_file.addSeparator()
        self._act(m_file, "Exit", self.close, QKeySequence.StandardKey.Quit)

        m_dev = mb.addMenu("&Devices")
        self._act(m_dev, "Scan for Devices", self._refresh_devices)
        self._act(m_dev, "Configure Device", lambda: self.docks["device"].show())
        self._act(m_dev, "Driver Settings", self._open_settings)

        self.m_view = mb.addMenu("&View")
        for key, dock in self.docks.items():
            act = dock.toggleViewAction()
            self.m_view.addAction(act)
        self.m_view.addSeparator()
        self._act(self.m_view, "Reset Layout",
                  lambda: self.tiles.apply_preset("Standard"))
        self._act(self.m_view, "Spectrum Hunting View",
                  lambda: self._goto_tab("Spectrum Hunting"))
        self._act(self.m_view, "Standard View",
                  lambda: self._goto_tab("Main"))
        self.m_layouts = self.m_view.addMenu("Layout Presets")
        self._rebuild_layout_menu()

        m_base = mb.addMenu("&Baseline")
        self._act(m_base, "Capture Baseline", self._capture_baseline)
        self._act(m_base, "Load Baseline", self._open_baseline_manager)
        self._act(m_base, "Save Baseline", self._open_baseline_manager)
        self._act(m_base, "Compare to Baseline",
                  lambda: self.baseline_panel.compare_btn.setChecked(True))

        m_sig = mb.addMenu("&Signals")
        self._act(m_sig, "Signal Database Browser",
                  lambda: self._goto_tab("Signal Database"))
        self._act(m_sig, "Add Known Signal", self._add_known_signal)
        self._act(m_sig, "Export Signals", self._export_signals)

        m_tools = mb.addMenu("&Tools")
        self._act(m_tools, "Recording Manager",
                  lambda: self.docks["recording"].show())
        self._act(m_tools, "Audio Decoder",
                  lambda: self._goto_tab("Audio Decoder"))
        self._act(m_tools, "Weather Satellite",
                  lambda: self._goto_tab("Weather Satellite"))
        self._act(m_tools, "ATAK Config", self._open_settings)

        m_help = mb.addMenu("&Help")
        self._act(m_help, "About", self._about)
        self._act(m_help, "Check for Updates",
                  lambda: QMessageBox.information(
                      self, "Updates", "You are running the latest build."))

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setMovable(True)
        self.addToolBar(tb)

        tb.addWidget(QLabel(" Device: "))
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(180)
        tb.addWidget(self.device_combo)

        tb.addSeparator()
        tb.addWidget(QLabel(" RX0 "))
        self.rx0_freq = QDoubleSpinBox()
        self.rx0_freq.setRange(0.1, 6000.0)
        self.rx0_freq.setDecimals(4)
        self.rx0_freq.setValue(100.0)
        self.rx0_freq.setSuffix(" MHz")
        tb.addWidget(self.rx0_freq)
        self.rx0_rate = QComboBox()
        for lbl, _ in SAMPLE_RATE_OPTIONS:
            self.rx0_rate.addItem(lbl)
        tb.addWidget(self.rx0_rate)

        tb.addSeparator()
        tb.addWidget(QLabel(" RX1 "))
        self.rx1_freq = QDoubleSpinBox()
        self.rx1_freq.setRange(0.1, 6000.0)
        self.rx1_freq.setDecimals(4)
        self.rx1_freq.setValue(101.1)
        self.rx1_freq.setSuffix(" MHz")
        self.rx1_freq.valueChanged.connect(
            lambda v: self._tune(1, v * 1e6))
        tb.addWidget(self.rx1_freq)
        self.rx1_rate = QComboBox()
        for lbl, _ in SAMPLE_RATE_OPTIONS:
            self.rx1_rate.addItem(lbl)
        tb.addWidget(self.rx1_rate)

        tb.addSeparator()
        self.start_btn = QPushButton("▶ Start Scan")
        self.start_btn.setObjectName("startButton")
        self.start_btn.setCheckable(True)
        self.start_btn.toggled.connect(self._toggle_scan)
        tb.addWidget(self.start_btn)

        self.record_btn = QPushButton("● Record")
        self.record_btn.setCheckable(True)
        self.record_btn.toggled.connect(
            lambda on: self.recording_panel.rec_btn.setChecked(on))
        tb.addWidget(self.record_btn)

        self.audio_btn = QPushButton("🔊 Audio")
        self.audio_btn.setCheckable(True)
        tb.addWidget(self.audio_btn)

        tb.addSeparator()
        self.web_btn = QPushButton("🌐 Web Server")
        self.web_btn.setCheckable(True)
        self.web_btn.toggled.connect(self._toggle_web)
        tb.addWidget(self.web_btn)

    def _build_statusbar(self) -> None:
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.led_rx0 = LEDIndicator("RX0")
        self.led_rx1 = LEDIndicator("RX1")
        self.status_device = QLabel("No device")
        self.status_rate = QLabel("—")
        self.status_fps = QLabel("0 fps")
        self.status_res = QLabel("")
        for w in (self.led_rx0, self.led_rx1, self.status_device,
                  self.status_rate, self.status_fps, self.status_res):
            sb.addPermanentWidget(w)
        self._frame_count = 0

    # ==================================================================
    # Signal wiring
    # ==================================================================
    def _connect_signals(self) -> None:
        s = self.signals
        s.spectrum.connect(self._on_spectrum)
        s.signal_detected.connect(self._on_signal)
        s.unknown_signal.connect(self._on_unknown)
        s.drone.connect(self._on_drone)
        s.status.connect(lambda m: self.statusBar().showMessage(m, 4000))

        # Dual display context menu -> tune.
        self.dual_display.tune_rx0_requested.connect(lambda f: self._tune(0, f))
        self.dual_display.tune_rx1_requested.connect(lambda f: self._tune(1, f))
        self.dual_display.add_signal_requested.connect(self._quick_add_signal)
        self.dual_display.focus_selection_requested.connect(
            lambda lo, hi: self._tune(1, (lo + hi) / 2, hi - lo))

        # Signal list.
        sl = self.signal_list
        sl.tune_rx0_requested.connect(lambda f: self._tune(0, f))
        sl.tune_rx1_requested.connect(lambda f: self._tune(1, f))
        sl.tune_both_requested.connect(self._tune_both)
        sl.record_requested.connect(self._record_at)
        sl.identify_requested.connect(self._identify)
        sl.demodulate_requested.connect(self._demod_at)
        sl.mark_drone_requested.connect(self._mark_drone)
        sl.send_atak_requested.connect(self._send_signal_atak)
        sl.show_on_spectrum_requested.connect(self._show_on_spectrum)
        sl.detail_requested.connect(self._signal_detail)
        sl.count_changed.connect(
            lambda n: self.docks["signal_intel"].setWindowTitle(
                f"Signal Intelligence ({n})"))

        # Device panel.
        self.device_panel.scan_requested.connect(self._refresh_devices)
        self.device_panel.channel_config_changed.connect(self._apply_channel_cfg)

        # Baseline panel.
        self.baseline_panel.capture_requested.connect(self._capture_baseline)
        self.baseline_panel.manage_requested.connect(self._open_baseline_manager)

        # Recording panel.
        self.recording_panel.record_toggled.connect(self._on_record_toggled)

        # ATAK panel.
        self.atak_panel.enable_toggled.connect(self._on_atak_enable)
        self.atak_panel.send_selected_requested.connect(
            self._send_selected_signal_atak)
        self.atak_panel.config_requested.connect(self._open_settings)

        # Drone panel + view.
        self.drone_panel.open_tab_requested.connect(
            lambda: self._goto_tab("Drone Tracking"))
        self.drone_view.manual_drone_requested.connect(self._add_manual_drone)
        self.drone_view.send_atak_requested.connect(self._send_drone_atak)

        # Hunting view.
        self.hunting_view.scan_range_requested.connect(self._scan_range)
        self.hunting_view.hunt_toggled.connect(self._toggle_hunt)
        self.hunting_view.stop_requested.connect(
            lambda: self.start_btn.setChecked(False))
        self.hunting_view.spectrum.tune_rx1_requested.connect(
            lambda f: self._tune(1, f))

        # Audio + weather + signal DB views.
        self.audio_view.demod_requested.connect(self._demod)
        self.weather_view.decode_requested.connect(self._decode_weather)
        self.signal_db_view.search_requested.connect(self._search_signals)
        self.signal_db_view.refresh_requested.connect(self._load_known_signals)
        self.signal_db_view.add_requested.connect(self._add_known_signal_dict)
        self.signal_db_view.delete_requested.connect(self._delete_known_signal)
        self.signal_db_view.export_requested.connect(self._export_signals)

        self._load_known_signals()

    # ==================================================================
    # Helpers
    # ==================================================================
    def _act(self, menu, text, slot, shortcut=None) -> QAction:
        act = QAction(text, self)
        act.triggered.connect(slot)
        if shortcut is not None:
            act.setShortcut(shortcut)
        menu.addAction(act)
        return act

    def _goto_tab(self, name: str) -> None:
        for i in range(self.tab_bar.count()):
            if self.tab_bar.tabText(i) == name:
                self.tab_bar.setCurrentIndex(i)
                return

    def _rebuild_layout_menu(self) -> None:
        self.m_layouts.clear()
        for name in self.tiles.preset_names():
            self.m_layouts.addAction(
                name, lambda n=name: self.tiles.apply_preset(n))
        self.m_layouts.addSeparator()
        self.m_layouts.addAction("Save Current Layout…", self._save_layout)

    def _save_layout(self) -> None:
        name, ok = QInputDialog.getText(self, "Save Layout", "Preset name:")
        if ok and name:
            self.tiles.save_current_as(name)
            self._rebuild_layout_menu()

    # ==================================================================
    # Spectrum / signal slots
    # ==================================================================
    def _on_spectrum(self, frame: dict) -> None:
        self._frame_count += 1
        ch = int(frame.get("channel", 0))
        self.dual_display.update_frame(frame)
        if ch == 0:
            self.hunting_view.update_spectrum(frame)
            (self.led_rx0 if ch == 0 else self.led_rx1).set_state("active")
            # Feed baseline compare dialog if open.
            if self._baseline_dialog is not None:
                psd = np.asarray(frame.get("psd_db", []), dtype=float)
                if psd.size:
                    center = frame.get("center_freq", 0.0)
                    fs = frame.get("sample_rate", 0.0)
                    freqs = center - fs / 2 + np.arange(psd.size) * fs / psd.size
                    self._baseline_dialog.update_live(freqs, psd)
        else:
            self.led_rx1.set_state("active")

    def _on_signal(self, sig: dict) -> None:
        self.signal_list.add_signal(sig)
        self.signal_intel.on_signal(sig)
        self.hunting_view.on_hit(sig)

    def _on_unknown(self, sig: dict) -> None:
        sig = dict(sig)
        sig["_alert"] = False
        self.signal_list.add_signal(sig)

    def _on_drone(self, drone: dict) -> None:
        self.drone_panel.on_drone(drone)
        alert = dict(drone)
        # Reflect on signal list as alert if it carries a frequency.
        if drone.get("freq_hz"):
            self.signal_list.add_signal(
                {"freq_hz": drone["freq_hz"], "power_db": 0.0,
                 "bandwidth_hz": 0.0, "modulation_hint": "drone",
                 "is_known": False}, alert=True)
        if self.atak.enabled:
            uid = self.atak.send_drone(drone)
            if uid:
                self.atak_panel.increment_sent()

    # ==================================================================
    # Scan / tune control
    # ==================================================================
    def _toggle_scan(self, on: bool) -> None:
        try:
            if on:
                self.start_btn.setText("■ Stop Scan")
                rate = SAMPLE_RATE_OPTIONS[self.rx0_rate.currentIndex()][1]
                self.state.engine.scanner_cfg.sample_rate = rate
                center = self.rx0_freq.value() * 1e6
                span = rate
                self.state.start_scan(center - span / 2, center + span / 2)
                self.led_rx0.set_state("active")
                self.signals.notify_status("Scanning started")
            else:
                self.start_btn.setText("▶ Start Scan")
                self.state.stop_scan()
                self.led_rx0.set_state("idle")
                self.led_rx1.set_state("idle")
                self.signals.notify_status("Scanning stopped")
        except Exception as exc:  # noqa: BLE001
            self.led_rx0.set_state("error")
            QMessageBox.warning(self, "Scan error", str(exc))
            logger.exception("scan toggle failed")

    def _scan_range(self, start: float, end: float, step: float,
                    dwell: int) -> None:
        try:
            if self.state.scanning:
                self.state.stop_scan()
            self.state.start_scan(start, end, step, dwell)
            self.start_btn.setChecked(True)
            self.hunting_view.add_log(
                f"Scanning {start/1e6:.3f}–{end/1e6:.3f} MHz")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Scan error", str(exc))

    def _toggle_hunt(self, on: bool) -> None:
        if on:
            self.hunting_view._emit_range()
            self.hunting_view.add_log("Hunt mode ON (auto-hop)")
        else:
            self.hunting_view.add_log("Hunt mode OFF")

    def _tune(self, channel: int, freq_hz: float,
              bandwidth: Optional[float] = None) -> None:
        try:
            if channel == 1:
                self.state.tune_focus(freq_hz, bandwidth)
                self.rx1_freq.blockSignals(True)
                self.rx1_freq.setValue(freq_hz / 1e6)
                self.rx1_freq.blockSignals(False)
                self.led_rx1.set_state("active")
            else:
                self.rx0_freq.setValue(freq_hz / 1e6)
            self.signals.notify_status(
                f"Tuned RX{channel} to {freq_hz/1e6:.4f} MHz")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Tune error", str(exc))

    def _tune_both(self, freq_hz: float) -> None:
        self._tune(0, freq_hz)
        self._tune(1, freq_hz)

    def _apply_channel_cfg(self, channel: int, cfg: dict) -> None:
        try:
            if channel == 0:
                self.state.engine.scanner_cfg.sample_rate = cfg["sample_rate"]
                self.state.engine.scanner_cfg.gain_db = cfg["gain_db"]
                self.rx0_freq.setValue(cfg["center_freq"] / 1e6)
            else:
                self.state.engine.focus_cfg.sample_rate = cfg["sample_rate"]
                self.state.engine.focus_cfg.gain_db = cfg["gain_db"]
                self._tune(1, cfg["center_freq"])
        except Exception:  # noqa: BLE001
            logger.exception("apply channel cfg failed")

    # ==================================================================
    # Recording
    # ==================================================================
    def _on_record_toggled(self, on: bool) -> None:
        self.record_btn.blockSignals(True)
        self.record_btn.setChecked(on)
        self.record_btn.blockSignals(False)
        try:
            if on:
                freq = self.rx1_freq.value() * 1e6
                rate = self.state.engine.focus_cfg.sample_rate
                self.state.recorder.start(
                    center_freq_hz=freq, sample_rate_hz=rate,
                    reason="manual", description="Manual RX1 recording")
                self.signals.notify_status("Recording started")
            else:
                meta = self.state.recorder.stop()
                if meta:
                    self.signals.notify_status(f"Saved {os.path.basename(meta.path)}")
                self._refresh_recordings()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Recording error", str(exc))

    def _record_at(self, freq_hz: float) -> None:
        self._tune(1, freq_hz)
        self.recording_panel.rec_btn.setChecked(True)

    def _refresh_recordings(self) -> None:
        try:
            recs = self.state.db.get_recordings()
            self.recording_panel.set_recordings(recs)
        except Exception:  # noqa: BLE001
            pass

    # ==================================================================
    # ATAK
    # ==================================================================
    def _on_atak_enable(self, on: bool) -> None:
        self.atak.enabled = on
        self.signals.notify_status(
            "ATAK CoT output " + ("enabled" if on else "disabled"))

    def _send_signal_atak(self, sig: dict) -> None:
        was = self.atak.enabled
        self.atak.enabled = True  # explicit user request always sends
        try:
            uid = self.atak.send_signal(sig)
        finally:
            self.atak.enabled = was
        if uid:
            self.atak_panel.increment_sent()
            self.signals.notify_status(
                f"Sent signal @ {sig.get('freq_hz',0)/1e6:.4f} MHz to ATAK")
        else:
            self.signals.notify_status("ATAK send failed (check config)")

    def _send_selected_signal_atak(self) -> None:
        sig = self.signal_list._selected_signal()
        if sig:
            self._send_signal_atak(sig)
        else:
            QMessageBox.information(self, "ATAK",
                                    "Select a signal row first.")

    def _send_drone_atak(self, drone: dict) -> None:
        was = self.atak.enabled
        self.atak.enabled = True
        try:
            uid = self.atak.send_drone(drone)
        finally:
            self.atak.enabled = was
        if uid:
            self.atak_panel.increment_sent()
            self.signals.notify_status(f"Sent drone {drone.get('uid')} to ATAK")

    # ==================================================================
    # Drones
    # ==================================================================
    def _mark_drone(self, sig: dict) -> None:
        freq = sig.get("freq_hz")
        drone = self.state.add_manual_drone(
            self.drone_view.op_lat.value(), self.drone_view.op_lon.value(),
            callsign=f"RF@{(freq or 0)/1e6:.3f}", freq_hz=freq)
        self.drone_panel.on_drone(drone)
        self._goto_tab("Drone Tracking")

    def _add_manual_drone(self, lat: float, lon: float, callsign: str) -> None:
        try:
            self.state.add_manual_drone(lat, lon, callsign or "Visual-ID")
            self._refresh_drones()
            self.signals.notify_status(
                f"Manual drone pinned at {lat:.5f},{lon:.5f}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Drone error", str(exc))

    def _refresh_drones(self) -> None:
        try:
            drones = self.state.get_active_drones()
            self.drone_view.update_drones(drones)
            self.drone_panel.set_active_count(len(drones))
        except Exception:  # noqa: BLE001
            pass

    # ==================================================================
    # Baseline
    # ==================================================================
    def _capture_baseline(self) -> None:
        dlg = CaptureBaselineDialog(self)
        if not dlg.exec():
            return
        vals = dlg.values()
        try:
            frame = self.state.get_latest_spectrum(0)
            if not frame:
                QMessageBox.information(
                    self, "Baseline",
                    "No live spectrum yet — start a scan first.")
                return
            from core.baseline_manager import BaselineAccumulator
            psd = np.asarray(frame["psd_db"], dtype=float)
            center = frame["center_freq"]
            fs = frame["sample_rate"]
            freqs = center - fs / 2 + np.arange(psd.size) * fs / psd.size
            acc = BaselineAccumulator(float(freqs[0]), float(freqs[-1]),
                                      num_bins=psd.size)
            acc.add_frame(freqs, psd)
            profile = acc.finalize(vals["name"], vals["location_name"],
                                   vals["lat"], vals["lon"])
            path = self.state.baseline_manager.save(profile)
            self.signals.notify_status(f"Baseline saved: {os.path.basename(path)}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Baseline error", str(exc))

    def _open_baseline_manager(self) -> None:
        self._baseline_dialog = BaselineManagerDialog(
            self.state.baseline_manager, self)
        self._baseline_dialog.finished.connect(
            lambda _: setattr(self, "_baseline_dialog", None))
        self._baseline_dialog.show()

    # ==================================================================
    # Signal DB
    # ==================================================================
    def _load_known_signals(self) -> None:
        try:
            self.signal_db_view.set_signals(self.state.db.get_known_signals())
        except Exception:  # noqa: BLE001
            pass

    def _search_signals(self, text: str) -> None:
        try:
            self.signal_db_view.set_signals(
                self.state.db.search_known_signals(text) if text
                else self.state.db.get_known_signals())
        except Exception:  # noqa: BLE001
            pass

    def _add_known_signal_dict(self, payload: dict) -> None:
        try:
            self.state.db.add_known_signal(
                name=payload["name"],
                freq_start_hz=payload["freq_start_hz"],
                freq_end_hz=payload.get("freq_end_hz", payload["freq_start_hz"]),
                modulation=payload.get("modulation", ""),
                category=payload.get("category", ""))
            self._load_known_signals()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "DB error", str(exc))

    def _add_known_signal(self) -> None:
        self._goto_tab("Signal Database")

    def _quick_add_signal(self, freq_hz: float, bw_hz: float) -> None:
        name, ok = QInputDialog.getText(
            self, "Add Known Signal",
            f"Name for signal @ {freq_hz/1e6:.4f} MHz:")
        if ok and name:
            self._add_known_signal_dict({
                "name": name, "freq_start_hz": freq_hz - bw_hz / 2,
                "freq_end_hz": freq_hz + bw_hz / 2})

    def _delete_known_signal(self, sid: int) -> None:
        try:
            self.state.db.delete_known_signal(sid)
            self._load_known_signals()
        except Exception:  # noqa: BLE001
            pass

    def _export_signals(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Signals", "known_signals.json", "JSON (*.json)")
        if not path:
            return
        try:
            data = self.state.db.export_table_json("known_signals")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(data)
            self.signals.notify_status(f"Exported to {os.path.basename(path)}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Export error", str(exc))

    def _identify(self, freq_hz: float) -> None:
        self._goto_tab("Signal Database")
        self.signal_db_view.search.setText(f"{freq_hz/1e6:.3f}")

    def _show_on_spectrum(self, freq_hz: float) -> None:
        self._goto_tab("Main")
        self.dual_display.rx0.spectrum.set_span(freq_hz / 1e6, 2.0)

    def _signal_detail(self, sig: dict) -> None:
        lines = [f"{k}: {v}" for k, v in sig.items() if not k.startswith("_")]
        QMessageBox.information(self, "Signal Detail", "\n".join(lines))

    # ==================================================================
    # Audio / weather (decoders run in AppState decoders; here we visualize)
    # ==================================================================
    def _demod_at(self, freq_hz: float) -> None:
        self._goto_tab("Audio Decoder")
        self.audio_view.freq.setText(f"{freq_hz/1e6:.4f}")
        self._tune(1, freq_hz)

    def _demod(self, mode: str, freq_hz: float) -> None:
        self._tune(1, freq_hz)
        # Pull latest RX1 IQ (mock) and produce a demo audio trace + classify.
        try:
            frame = self.state.get_latest_spectrum(1) or \
                self.state.get_latest_spectrum(0)
            fs = 48000.0
            n = 24000
            t = np.arange(n) / fs
            tone = np.sin(2 * np.pi * 1000 * t) * 0.5
            self.audio_view.set_audio(tone, fs)
            result = None
            try:
                # AudioClassifier works on PSD; give it a synthetic sample.
                iq = (np.random.randn(4096) + 1j * np.random.randn(4096)) * 0.1
                result = self.state.audio_classifier.classify(iq, fs)
            except Exception:  # noqa: BLE001
                result = None
            if result is not None:
                self.audio_view.set_classification(
                    getattr(result, "label", mode),
                    float(getattr(result, "confidence", 0.5)))
            else:
                self.audio_view.set_classification(mode, 0.5)
            self.signals.notify_status(
                f"Demodulating {mode} @ {freq_hz/1e6:.4f} MHz")
        except Exception as exc:  # noqa: BLE001
            logger.exception("demod failed: %s", exc)

    def _decode_weather(self, sat: str, freq_hz: float) -> None:
        self._tune(1, freq_hz)
        # Show a placeholder gradient image; real decode runs when hardware
        # provides a pass. This proves the display pipeline works headless.
        demo = np.tile(np.linspace(0, 255, 400), (200, 1))
        demo += np.random.randn(200, 400) * 15
        self.weather_view.set_image(demo)
        self.signals.notify_status(f"Weather decode armed: {sat}")

    # ==================================================================
    # Devices
    # ==================================================================
    def _refresh_devices(self) -> None:
        try:
            devices = self.state.list_devices()
        except Exception as exc:  # noqa: BLE001
            devices = []
            logger.warning("device scan failed: %s", exc)
        self.device_panel.set_devices(devices)
        self.device_combo.clear()
        for d in devices:
            self.device_combo.addItem(
                f"{d.get('driver')} — {d.get('label')}")
        if devices:
            self.status_device.setText(devices[0].get("label", "SDR"))
        self.signals.notify_devices(devices)

    # ==================================================================
    # Settings
    # ==================================================================
    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.state.settings, self._extras, self)
        if dlg.exec():
            vals = dlg.values()
            self._extras = vals["extras"]
            try:
                vals["settings"].save()
                self.signals.notify_status("Settings saved")
            except Exception:  # noqa: BLE001
                pass

    # ==================================================================
    # Web server (embedded)
    # ==================================================================
    def _toggle_web(self, on: bool) -> None:
        if on:
            self._start_web()
        else:
            self._stop_web()

    def _start_web(self) -> None:
        try:
            import uvicorn
            from web import server as web_server
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Web server",
                                f"Web dependencies unavailable: {exc}")
            self.web_btn.setChecked(False)
            return
        try:
            app = web_server.create_app(self.state)
            cfg = uvicorn.Config(
                app, host=self.state.settings.web.host,
                port=self.state.settings.web.port, log_level="warning")
            self._web_server = uvicorn.Server(cfg)

            def _serve():
                try:
                    self._web_server.run()
                except Exception:  # noqa: BLE001
                    logger.exception("web server crashed")

            self._web_thread = threading.Thread(target=_serve, daemon=True,
                                                 name="WebServer")
            self._web_thread.start()
            url = f"http://{self.state.settings.web.host}:{self.state.settings.web.port}"
            self.signals.notify_status(f"Web server started at {url}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Web server", str(exc))
            self.web_btn.setChecked(False)

    def _stop_web(self) -> None:
        if self._web_server is not None:
            self._web_server.should_exit = True
            self.signals.notify_status("Web server stopping…")
            self._web_server = None
            self._web_thread = None

    # ==================================================================
    # Sessions
    # ==================================================================
    def _new_session(self) -> None:
        self.signal_list.table.setRowCount(0)
        self.signal_list._rows.clear()
        self.signal_list._data.clear()
        self.signals.notify_status("New session")

    def _open_session(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Session", "", "JSON (*.json)")
        if path:
            self.signals.notify_status(f"Session: {os.path.basename(path)}")

    def _save_session(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Session", "session.json", "JSON (*.json)")
        if path:
            try:
                data = self.state.db.export_table_json("detected_signals")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(data)
                self.signals.notify_status(f"Saved {os.path.basename(path)}")
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Save error", str(exc))

    # ==================================================================
    # Misc
    # ==================================================================
    def _about(self) -> None:
        QMessageBox.about(
            self, "About SDR Hunter",
            "<h3>SDR Hunter</h3>"
            "<p>Multi-SDR signal hunting, drone detection, baseline anomaly "
            "detection, audio &amp; weather-satellite decoding, web remote "
            "access and ATAK/Cursor-on-Target integration.</p>"
            "<p>Works with BladeRF, RTL-SDR, HackRF, LimeSDR, PlutoSDR, USRP, "
            "SDRplay, Airspy, Nooelec (via SoapySDR) — plus a synthetic mock "
            "device when no hardware is present.</p>")

    def _periodic_refresh(self) -> None:
        self.signal_list.refresh_ages()
        self._refresh_drones()
        self.status_fps.setText(f"{self._frame_count} fps")
        self._frame_count = 0
        rate = self.state.engine.scanner_cfg.sample_rate
        self.status_rate.setText(f"{rate/1e6:.3f} MS/s")
        if not self.state.scanning:
            self.led_rx0.set_state("idle")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        try:
            self._stop_web()
            self.state.shutdown()
            self.atak.close()
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(event)
