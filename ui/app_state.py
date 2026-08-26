"""Shared application state.

A single :class:`AppState` object wires together the settings, database,
device manager, dual-RX engine, detectors, trackers and decoders so that both
the (future) Qt desktop UI and the web server operate on the same live state.

The state object is deliberately UI-framework agnostic: it exposes plain
callbacks and data structures. A Qt adapter or the web ``ws_manager`` subscribe
to these callbacks to receive spectrum frames and events.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional

import numpy as np

from config import settings as settings_mod
from config.settings import Settings, load_json_db
from core.baseline_manager import BaselineManager
from core.bookmark_manager import BookmarkManager
from core.dual_rx_engine import DualRXEngine
from core.recording_engine import IQRecorder
from core.sdr_manager import DeviceManager
from core.signal_detector import (KnownSignalMatcher, SignalDetector,
                                     SignalEvent)
from core.spectrum_recorder import SpectrumRecorder, default_spectrum_path
from database.signal_db import SignalDB
from decoders.audio_classifier import AudioClassifier
from decoders.drone_id.drone_signal_detector import DroneSignalDetector
from decoders.drone_id.drone_tracker import DroneTracker
from decoders.drone_id.remote_id import RemoteIDDecoder


class AppState:
    """Central application state and service wiring."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or settings_mod.get_settings()
        self.settings.ensure_dirs()

        # Persistence.
        self.db = SignalDB(self.settings.db_path)
        # Seed known signals from bundled JSON (idempotent replace of builtins).
        builtin = load_json_db(settings_mod.DEFAULT_SIGNALS_JSON)
        if builtin:
            self.db.seed_known_signals(builtin)

        # Matcher / detector. Detection thresholds come from settings so they
        # can be tuned without code changes (see SDRSettings.detect_*).
        self.matcher = KnownSignalMatcher(builtin)
        self.detector = SignalDetector(
            self.matcher,
            threshold_db=self.settings.sdr.detect_threshold_db,
            min_bin_width=self.settings.sdr.detect_min_bin_width,
            min_snr_db=self.settings.sdr.detect_min_snr_db,
            max_events=self.settings.sdr.detect_max_events,
            guard_cells=self.settings.sdr.detect_guard_cells,
            train_cells=self.settings.sdr.detect_train_cells,
        )

        # Devices + engine.
        self.device_manager = DeviceManager(allow_mock=True)
        self.recorder = IQRecorder(self.settings.recordings_dir)
        # Lightweight PSD-frame recorder for the RX0 waterfall/scope replay.
        self.spectrum_recorder = SpectrumRecorder()
        self.baseline_manager = BaselineManager(self.settings.baselines_dir)
        self.engine = DualRXEngine(
            device_manager=self.device_manager,
            detector=self.detector,
            recorder=self.recorder,
            baseline_manager=self.baseline_manager,
            auto_record_seconds=self.settings.sdr.auto_record_seconds,
        )
        self.engine.scanner_cfg.sample_rate = self.settings.sdr.default_sample_rate
        self.engine.scanner_cfg.fft_size = self.settings.sdr.fft_size
        self.engine.focus_cfg.sample_rate = self.settings.sdr.default_sample_rate

        # Decoders / trackers.
        drone_db = load_json_db(settings_mod.DRONE_FREQS_JSON)
        # load_json_db returns [] for dict-shaped files; load raw instead.
        self.drone_detector = DroneSignalDetector(
            drone_freqs_path=settings_mod.DRONE_FREQS_JSON)
        self.drone_tracker = DroneTracker()
        self.remote_id = RemoteIDDecoder()
        self.audio_classifier = AudioClassifier(
            db_path=settings_mod.AUDIO_SIGNALS_JSON)

        # Frequency bookmarks.
        self.bookmark_manager = BookmarkManager(
            settings_mod.DEFAULT_BOOKMARKS_JSON)

        # Live state buffers.
        self._lock = threading.Lock()
        self.latest_spectrum: Dict[int, Dict[str, Any]] = {}
        self.recent_signals: Deque[SignalEvent] = deque(maxlen=200)
        self.session_id: Optional[int] = None
        self.scanning = False

        # External subscribers (e.g. web ws_manager). Each is called with
        # (kind, payload) where kind in {"spectrum", "signal", "unknown",
        # "drone"}.
        self._subscribers: List[Callable[[str, Any], None]] = []

        # Wire engine callbacks.
        self.engine.on_spectrum_data = self._on_spectrum
        self.engine.on_signal_detected = self._on_signal
        self.engine.on_unknown_signal = self._on_unknown

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------
    def subscribe(self, cb: Callable[[str, Any], None]) -> None:
        with self._lock:
            self._subscribers.append(cb)

    def unsubscribe(self, cb: Callable[[str, Any], None]) -> None:
        with self._lock:
            if cb in self._subscribers:
                self._subscribers.remove(cb)

    def _broadcast(self, kind: str, payload: Any) -> None:
        for cb in list(self._subscribers):
            try:
                cb(kind, payload)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # Engine callbacks
    # ------------------------------------------------------------------
    def _on_spectrum(self, channel: int, center_freq: float, fs: float,
                     psd_db: np.ndarray) -> None:
        # Tap the RX0 (scanner) PSD stream for optional spectrum recording,
        # using the raw float array before it is rounded/serialised below.
        if channel == 0 and self.spectrum_recorder.is_recording:
            try:
                self.spectrum_recorder.add_frame(
                    center_freq, fs, psd_db, channel=channel)
            except Exception:  # noqa: BLE001
                pass
        frame = {
            "channel": channel,
            "center_freq": center_freq,
            "sample_rate": fs,
            "psd_db": psd_db.astype(float).round(2).tolist(),
            "timestamp": time.time(),
        }
        with self._lock:
            self.latest_spectrum[channel] = frame
        self._broadcast("spectrum", frame)

    def _on_signal(self, event: SignalEvent) -> None:
        with self._lock:
            self.recent_signals.append(event)
        self.db.add_detection(
            freq_hz=event.freq_hz, bandwidth_hz=event.bandwidth_hz,
            power_db=event.power_db, snr_db=event.snr_db,
            modulation_hint=event.modulation_hint, is_known=event.is_known,
            session_id=self.session_id)
        self._broadcast("signal", event.to_dict())
        # Drone heuristic.
        suspicion = self.drone_detector.evaluate(event)
        if suspicion is not None and suspicion.confidence >= 0.5:
            drone = self.drone_tracker.update_from_suspicion(suspicion)
            self.db.add_drone_event(
                uid=drone.uid, manufacturer=drone.manufacturer,
                source=drone.source, freq_hz=drone.freq_hz,
                confidence=drone.confidence, id_failed=True)
            self._broadcast("drone", {**suspicion.to_dict(),
                                      "uid": drone.uid})

    def _on_unknown(self, event: SignalEvent) -> None:
        self._broadcast("unknown", event.to_dict())

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------
    def list_devices(self) -> List[Dict[str, str]]:
        return self.device_manager.enumerate_devices()

    def apply_detection_settings(self) -> None:
        """Push the current ``settings.sdr.detect_*`` values into the live
        :class:`SignalDetector` so changes made in the Settings dialog take
        effect without restarting. The detection-list widget's merge/expiry
        attributes are updated by the UI layer (it owns that widget)."""
        s = self.settings.sdr
        d = self.detector
        d.threshold_db = s.detect_threshold_db
        d.min_bin_width = s.detect_min_bin_width
        d.min_snr_db = s.detect_min_snr_db
        d.max_events = s.detect_max_events
        d.guard_cells = s.detect_guard_cells
        d.train_cells = s.detect_train_cells

    def start_spectrum_recording(self, path: Optional[str] = None) -> str:
        """Start recording the RX0 PSD frame stream. Returns the file path."""
        if path is None:
            path = default_spectrum_path(self.settings.recordings_dir)
        return self.spectrum_recorder.start(path, channel=0)

    def stop_spectrum_recording(self) -> Optional[str]:
        """Stop spectrum recording and write the file. Returns the path."""
        return self.spectrum_recorder.stop()

    def start_scan(self, freq_start: float, freq_end: float,
                   step: Optional[float] = None,
                   dwell_ms: Optional[int] = None) -> None:
        if self.scanning:
            return
        self.session_id = self.db.start_session(
            name=f"scan_{int(time.time())}", freq_start_hz=freq_start,
            freq_end_hz=freq_end, device_label="")
        self.engine.start_scan(freq_start, freq_end, step,
                               dwell_ms or self.settings.sdr.scan_dwell_ms)
        self.scanning = True

    def stop_scan(self) -> None:
        if not self.scanning:
            return
        self.engine.stop()
        if self.session_id is not None:
            self.db.end_session(self.session_id)
        self.scanning = False

    def tune_focus(self, freq: float, bandwidth: Optional[float] = None,
                   duration_s: Optional[float] = None,
                   rx: int = 1) -> int:
        """Tune a receiver to ``freq``.

        ``rx`` selects the receiver using the engine's 0-indexed channels
        (``0`` -> RX1/scanner, ``1`` -> RX2/focus). Defaults to ``1`` (focus)
        to preserve the historical single-argument behaviour of this method.
        Returns the receiver actually tuned.
        """
        return self.engine.tune(rx, freq, bandwidth, duration_s)

    def get_latest_spectrum(self, channel: int = 0) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self.latest_spectrum.get(channel)

    def get_recent_signals(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [s.to_dict() for s in self.recent_signals]

    def get_active_drones(self) -> List[Dict[str, Any]]:
        return [d.to_dict() for d in self.drone_tracker.active_drones()]

    def add_manual_drone(self, lat: float, lon: float, callsign: str = "",
                         freq_hz: Optional[float] = None) -> Dict[str, Any]:
        drone = self.drone_tracker.add_manual(lat, lon, callsign,
                                              freq_hz=freq_hz)
        self.db.add_drone_event(uid=drone.uid, callsign=drone.callsign,
                                source="manual", lat=lat, lon=lon,
                                freq_hz=freq_hz, id_failed=True)
        return drone.to_dict()

    def shutdown(self) -> None:
        try:
            self.stop_scan()
        finally:
            self.db.close()
