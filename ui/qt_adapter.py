"""Qt adapter bridging the framework-agnostic :class:`AppState` to Qt signals.

``AppState`` emits events from background (DSP/SDR) threads via plain Python
callbacks. Touching Qt widgets from a non-GUI thread is unsafe, so this adapter
subscribes to ``AppState`` and re-emits every event as a Qt signal. Because the
adapter's signals are connected across threads, Qt automatically marshals the
payload onto the GUI thread (``QueuedConnection``), keeping the UI safe.

Widgets should connect to :class:`AppSignals` — never to ``AppState`` directly.
"""
from __future__ import annotations

from typing import Any, Dict

from PyQt6.QtCore import QObject, pyqtSignal

from .app_state import AppState


class AppSignals(QObject):
    """Qt signal hub re-emitting :class:`AppState` events on the GUI thread."""

    #: Emitted for every spectrum frame: the raw frame dict from AppState.
    spectrum = pyqtSignal(dict)
    #: Emitted for each detected signal (SignalEvent.to_dict()).
    signal_detected = pyqtSignal(dict)
    #: Emitted for each *unknown* detected signal.
    unknown_signal = pyqtSignal(dict)
    #: Emitted for each drone suspicion / detection.
    drone = pyqtSignal(dict)
    #: Emitted when scanning starts/stops (bool = scanning).
    scan_state = pyqtSignal(bool)
    #: Emitted when the device list changes.
    devices_changed = pyqtSignal(list)
    #: Generic status line message for the status bar.
    status = pyqtSignal(str)

    def __init__(self, app_state: AppState):
        super().__init__()
        self.app_state = app_state
        # Subscribe to AppState broadcasts. This callback runs on worker
        # threads; emitting a Qt signal here is thread-safe and Qt will queue
        # delivery to slots living on the GUI thread.
        app_state.subscribe(self._on_app_event)

    # ------------------------------------------------------------------
    def _on_app_event(self, kind: str, payload: Any) -> None:
        try:
            if kind == "spectrum":
                self.spectrum.emit(payload)
            elif kind == "signal":
                self.signal_detected.emit(payload)
            elif kind == "unknown":
                self.unknown_signal.emit(payload)
            elif kind == "drone":
                self.drone.emit(payload)
        except Exception:  # noqa: BLE001 - never let UI bridge crash a worker
            pass

    # ------------------------------------------------------------------
    def notify_scan_state(self, scanning: bool) -> None:
        self.scan_state.emit(scanning)

    def notify_status(self, message: str) -> None:
        self.status.emit(message)

    def notify_devices(self, devices: list) -> None:
        self.devices_changed.emit(devices)
