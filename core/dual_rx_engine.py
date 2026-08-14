"""Dual-RX orchestration engine.

Coordinates two logical receivers:
    * **RX0 (scanner)** -- hops across a frequency range, computing spectra and
      running the signal detector.
    * **RX1 (focus)**   -- locks onto a specific signal for close inspection or
      recording (including auto-recording of unknown signals).

Allocation strategy:
    * If a device natively exposes >= 2 RX channels, use channels 0 and 1.
    * Else if two devices are available, use one each.
    * Else time-multiplex a single channel between scan and focus duties.

The engine runs a background worker thread and emits results through callbacks
(so it is independent of Qt); a thin Qt adapter can forward these to signals.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, List, Optional

import numpy as np

from . import dsp_engine
from .baseline_manager import BaselineManager
from .recording_engine import IQRecorder
from .scan_scheduler import ScanScheduler
from .sdr_manager import DeviceManager, SDRDevice
from .signal_detector import SignalDetector, SignalEvent

logger = logging.getLogger(__name__)

# Callback type aliases.
SpectrumCB = Callable[[int, float, float, np.ndarray], None]  # ch, cf, fs, psd
SignalCB = Callable[[SignalEvent], None]


@dataclass
class RXConfig:
    """Runtime configuration for a receiver channel."""

    sample_rate: float = 2.048e6
    gain_db: float = 30.0
    bandwidth: float = 2.0e6
    fft_size: int = 4096


@dataclass
class PendingIdentification:
    """An unknown signal queued for identification (auto-focus / recording)."""

    freq_hz: float
    bandwidth_hz: float
    power_db: float = 0.0
    snr_db: float = 0.0
    first_seen: float = field(default_factory=time.time)
    recording_path: str = ""
    status: str = "queued"   # queued | focusing | recording | done | skipped

    def to_dict(self) -> dict:
        return {
            "freq_hz": self.freq_hz, "bandwidth_hz": self.bandwidth_hz,
            "power_db": self.power_db, "snr_db": self.snr_db,
            "first_seen": self.first_seen, "recording_path": self.recording_path,
            "status": self.status,
        }


class DualRXEngine:
    """Manage scanner (RX0) and focus (RX1) receivers."""

    def __init__(self, device_manager: Optional[DeviceManager] = None,
                 detector: Optional[SignalDetector] = None,
                 recorder: Optional[IQRecorder] = None,
                 baseline_manager: Optional[BaselineManager] = None,
                 auto_record_seconds: int = 180):
        self.device_manager = device_manager or DeviceManager()
        self.detector = detector or SignalDetector()
        self.recorder = recorder
        self.baseline_manager = baseline_manager
        self.auto_record_seconds = auto_record_seconds

        self.scanner_dev: Optional[SDRDevice] = None
        self.focus_dev: Optional[SDRDevice] = None
        self._scanner_channel = 0
        self._focus_channel = 1
        self._shared_device = False  # single channel time-multiplex mode

        self.scanner_cfg = RXConfig()
        self.focus_cfg = RXConfig()
        self.scheduler = ScanScheduler()

        # Callbacks.
        self.on_spectrum_data: Optional[SpectrumCB] = None
        self.on_signal_detected: Optional[SignalCB] = None
        self.on_unknown_signal: Optional[SignalCB] = None
        self.on_pending_updated: Optional[Callable[[List[dict]], None]] = None

        # Focus state.
        self._focus_freq: Optional[float] = None
        self._focus_until: Optional[float] = None
        self._focus_recording = False
        self._manual_focus = False   # True when user manually tuned RX1

        # Pending-identification queue for unknown signals.
        self.pending_queue: Deque[PendingIdentification] = deque(maxlen=200)
        self._pending_seen: set = set()   # freq keys already queued
        self.dual_signal_mode = False     # both RX parked for comparison
        self._park_center: Optional[float] = None  # RX0 park freq in dual mode
        # RX0 (scanner) manual park frequency set via a remote/manual tune.
        # When not None the scanner stops hopping and stays on this frequency.
        self._scanner_park_freq: Optional[float] = None

        self._worker: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Device allocation
    # ------------------------------------------------------------------
    def setup_devices(self, scanner_args: Optional[dict] = None,
                      focus_args: Optional[dict] = None) -> None:
        """Open and allocate receivers based on available hardware.

        ``scanner_args`` / ``focus_args`` are optional dicts with ``driver`` and
        ``serial`` keys. When omitted, devices are auto-selected.
        """
        devices = self.device_manager.enumerate_devices()
        if not devices:
            raise RuntimeError("No SDR devices available")

        scanner_args = scanner_args or {"driver": devices[0]["driver"],
                                        "serial": devices[0]["serial"]}
        self.scanner_dev = self.device_manager.open_device(**scanner_args)

        if self.scanner_dev.profile.num_channels >= 2:
            # Native dual-channel: same device, channels 0 and 1.
            self.focus_dev = self.scanner_dev
            self._scanner_channel, self._focus_channel = 0, 1
            self._shared_device = False
            logger.info("Dual-RX via single dual-channel device")
        elif focus_args or len(devices) >= 2:
            fa = focus_args or {"driver": devices[1]["driver"],
                                "serial": devices[1]["serial"]}
            self.focus_dev = self.device_manager.open_device(**fa)
            self._scanner_channel = self._focus_channel = 0
            self._shared_device = False
            logger.info("Dual-RX via two separate devices")
        else:
            # Single channel: time-multiplex.
            self.focus_dev = self.scanner_dev
            self._scanner_channel = self._focus_channel = 0
            self._shared_device = True
            logger.info("Single-channel device: time-multiplex scan/focus")

        self._configure_channel(self.scanner_dev, self._scanner_channel,
                                self.scanner_cfg)
        if not self._shared_device and self.focus_dev is not self.scanner_dev:
            self._configure_channel(self.focus_dev, self._focus_channel,
                                    self.focus_cfg)

    @staticmethod
    def _configure_channel(dev: SDRDevice, channel: int, cfg: RXConfig) -> None:
        dev.set_sample_rate(channel, cfg.sample_rate)
        dev.set_gain(channel, cfg.gain_db)
        dev.set_bandwidth(channel, cfg.bandwidth)

    # ------------------------------------------------------------------
    # Scan control
    # ------------------------------------------------------------------
    def start_scan(self, freq_start: float, freq_end: float,
                   step: Optional[float] = None, dwell_ms: int = 200) -> None:
        """Start the RX0 scanner sweeping across a frequency range."""
        if self.scanner_dev is None:
            self.setup_devices()
        step = step or self.scanner_cfg.sample_rate
        self.scheduler.build_plan(freq_start, freq_end, step, dwell_ms)
        self.scheduler.start()
        self._start_worker()

    def stop(self) -> None:
        """Stop the engine and close devices."""
        self._stop_evt.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=3.0)
        self.scheduler.stop()
        if self.recorder and self.recorder.is_recording:
            self.recorder.stop()
        for dev in {id(self.scanner_dev): self.scanner_dev,
                    id(self.focus_dev): self.focus_dev}.values():
            if dev is not None:
                try:
                    dev.close()
                except Exception:  # noqa: BLE001
                    pass
        self.scanner_dev = None
        self.focus_dev = None

    # ------------------------------------------------------------------
    # Focus control
    # ------------------------------------------------------------------
    def focus_rx1(self, freq: float, bandwidth: Optional[float] = None,
                  duration_s: Optional[float] = None,
                  manual: bool = False) -> None:
        """Lock RX1 onto a specific frequency (optionally for a duration).

        When ``manual`` is True this is a user-initiated tune: any in-progress
        auto-focus/auto-record is interrupted first, and the auto-focus loop is
        prevented from overriding the manual selection until it is released.
        """
        if manual:
            self.interrupt_auto_focus()
        with self._lock:
            self._manual_focus = manual or self._manual_focus
            self._focus_freq = freq
            self._focus_until = (time.time() + duration_s) if duration_s else None
            if bandwidth:
                self.focus_cfg.bandwidth = bandwidth
            if self.focus_dev is not None and not self._shared_device:
                self.focus_dev.set_frequency(self._focus_channel, freq)
                if bandwidth:
                    self.focus_dev.set_bandwidth(self._focus_channel, bandwidth)

    def interrupt_auto_focus(self) -> None:
        """Stop any auto-focus recording currently in progress on RX1."""
        with self._lock:
            was_recording = self._focus_recording
        if was_recording and self.recorder is not None:
            try:
                meta = self.recorder.stop()
                if meta:
                    self._mark_pending_done(meta.center_freq_hz, meta.path)
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            self._focus_recording = False
            self._focus_until = None

    def release_manual_focus(self) -> None:
        """Release the manual-focus lock so auto-focus can resume."""
        with self._lock:
            self._manual_focus = False
            self._focus_freq = None
            self._focus_until = None

    def tune_scanner(self, freq: float, bandwidth: Optional[float] = None) -> None:
        """Park the RX0 scanner on ``freq`` (stops sweeping until released).

        This is the RX0/RX1 (UI "RX1") counterpart to :meth:`focus_rx1`. The
        scanner keeps producing spectra/detections on the parked frequency
        instead of hopping across the plan. Safe when no device is open yet:
        the frequency is stored and applied on the next scan step.
        """
        with self._lock:
            self._scanner_park_freq = freq
            if bandwidth:
                self.scanner_cfg.bandwidth = bandwidth
            if self.scanner_dev is not None:
                try:
                    self.scanner_dev.set_frequency(self._scanner_channel, freq)
                    if bandwidth:
                        self.scanner_dev.set_bandwidth(self._scanner_channel,
                                                       bandwidth)
                except Exception:  # noqa: BLE001
                    logger.debug("tune_scanner: device retune failed",
                                 exc_info=True)

    def release_scanner_park(self) -> None:
        """Release the RX0 scanner park so the sweep resumes."""
        with self._lock:
            self._scanner_park_freq = None

    def tune(self, rx: int, freq: float, bandwidth: Optional[float] = None,
             duration_s: Optional[float] = None) -> int:
        """Route a tune command to the selected receiver.

        ``rx`` uses the engine's 0-indexed channels (matching the web UI:
        ``0`` -> RX1/scanner, ``1`` -> RX2/focus). Any out-of-range value
        falls back to ``0`` (RX1). Returns the receiver actually tuned.
        """
        try:
            rx = int(rx)
        except (TypeError, ValueError):
            rx = 0
        if rx not in (0, 1):
            rx = 0
        if rx == 1:
            self.focus_rx1(freq, bandwidth=bandwidth, duration_s=duration_s,
                           manual=True)
        else:
            self.tune_scanner(freq, bandwidth=bandwidth)
        return rx

    # ------------------------------------------------------------------
    # Pending identification queue
    # ------------------------------------------------------------------
    def _enqueue_pending(self, ev: SignalEvent) -> None:
        key = int(round(ev.freq_hz / 1e3))
        if key in self._pending_seen:
            return
        self._pending_seen.add(key)
        self.pending_queue.append(PendingIdentification(
            freq_hz=ev.freq_hz, bandwidth_hz=ev.bandwidth_hz,
            power_db=getattr(ev, "power_db", 0.0),
            snr_db=getattr(ev, "snr_db", 0.0)))
        self._notify_pending()

    def _mark_pending_done(self, freq_hz: float, path: str = "") -> None:
        key = int(round(freq_hz / 1e3))
        for p in self.pending_queue:
            if int(round(p.freq_hz / 1e3)) == key and p.status != "done":
                p.status = "done"
                p.recording_path = path or p.recording_path
                break
        self._notify_pending()

    def _notify_pending(self) -> None:
        if self.on_pending_updated:
            try:
                self.on_pending_updated([p.to_dict() for p in self.pending_queue])
            except Exception:  # noqa: BLE001
                pass

    def get_pending(self) -> List[dict]:
        """Return the current pending-identification queue as dicts."""
        return [p.to_dict() for p in self.pending_queue]

    def clear_pending(self) -> None:
        self.pending_queue.clear()
        self._pending_seen.clear()
        self._notify_pending()

    def set_dual_signal_mode(self, enabled: bool, rx0_freq: Optional[float] = None,
                             rx1_freq: Optional[float] = None) -> None:
        """Park both receivers on specific signals for side-by-side comparison.

        In dual-signal mode RX0 stops sweeping (parks on ``rx0_freq``) and RX1
        parks on ``rx1_freq``; auto-focus of unknown signals is suspended.
        """
        with self._lock:
            self.dual_signal_mode = enabled
            self._park_center = rx0_freq if enabled else None
        if enabled:
            self.scheduler.stop()
            if rx0_freq is not None and self.scanner_dev is not None:
                try:
                    self.scanner_dev.set_frequency(self._scanner_channel,
                                                   rx0_freq)
                except Exception:  # noqa: BLE001
                    pass
            if rx1_freq is not None:
                self.focus_rx1(rx1_freq, manual=True)
        else:
            self.release_manual_focus()
            self.scheduler.start()

    def auto_record_unknown(self, signal_event: SignalEvent) -> None:
        """Auto-record an unknown signal on RX1 for ``auto_record_seconds``."""
        if self.recorder is None:
            logger.debug("No recorder configured; skipping auto-record")
            return
        if self.recorder.is_recording:
            return
        self.focus_rx1(signal_event.freq_hz,
                       bandwidth=max(signal_event.bandwidth_hz * 4, 200e3),
                       duration_s=self.auto_record_seconds)
        self.recorder.start(
            center_freq_hz=signal_event.freq_hz,
            sample_rate_hz=self.focus_cfg.sample_rate,
            reason="unknown_signal",
            description=(f"Auto-record unknown signal @ "
                         f"{signal_event.freq_hz/1e6:.4f} MHz"),
        )
        self._focus_recording = True
        # Update pending-queue status for this signal.
        key = int(round(signal_event.freq_hz / 1e3))
        for p in self.pending_queue:
            if int(round(p.freq_hz / 1e3)) == key:
                p.status = "recording"
                break
        self._notify_pending()
        logger.info("Auto-recording unknown signal at %.4f MHz",
                    signal_event.freq_hz / 1e6)

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------
    def _start_worker(self) -> None:
        self._stop_evt.clear()
        self._worker = threading.Thread(target=self._run, daemon=True,
                                        name="DualRXEngine")
        self._worker.start()

    def _run(self) -> None:
        cfg = self.scanner_cfg
        while not self._stop_evt.is_set():
            try:
                self._scan_step(cfg)
                if self._focus_freq is not None:
                    self._focus_step()
            except Exception as exc:  # noqa: BLE001
                logger.exception("DualRX worker error: %s", exc)
                time.sleep(0.1)

    def _scan_step(self, cfg: RXConfig) -> None:
        if self.dual_signal_mode and self._park_center is not None:
            center = self._park_center
        elif self._scanner_park_freq is not None:
            center = self._scanner_park_freq
        else:
            center = self.scheduler.current_center
        if center is None or self.scanner_dev is None:
            time.sleep(0.05)
            return
        self.scanner_dev.set_frequency(self._scanner_channel, center)
        iq = self.scanner_dev.get_iq_stream(self._scanner_channel, cfg.fft_size)
        psd = dsp_engine.compute_psd(iq, cfg.fft_size, "hann", cfg.sample_rate)
        if self.on_spectrum_data:
            self.on_spectrum_data(self._scanner_channel, center,
                                  cfg.sample_rate, psd)
        events = self.detector.detect(psd, center, cfg.sample_rate)
        for ev in events:
            if self.on_signal_detected:
                self.on_signal_detected(ev)
            if not ev.is_known:
                if self.on_unknown_signal:
                    self.on_unknown_signal(ev)
                # Queue for identification; auto-focus only when not busy and
                # not overridden by a manual tune / dual-signal comparison.
                self._enqueue_pending(ev)
                if (self.recorder is not None
                        and not self.recorder.is_recording
                        and not self._manual_focus
                        and not self.dual_signal_mode):
                    self.auto_record_unknown(ev)
        if (not self.dual_signal_mode and self._scanner_park_freq is None
                and self.scheduler.should_hop()):
            self.scheduler.next_center()

    def _focus_step(self) -> None:
        # Time-multiplex mode: temporarily retune the shared device.
        if self._focus_until and time.time() > self._focus_until:
            self._end_focus()
            return
        dev = self.focus_dev
        if dev is None or self._focus_freq is None:
            return
        cfg = self.focus_cfg
        if self._shared_device:
            dev.set_frequency(self._focus_channel, self._focus_freq)
        iq = dev.get_iq_stream(self._focus_channel, cfg.fft_size)
        if self._focus_recording and self.recorder is not None:
            self.recorder.write(iq)
        psd = dsp_engine.compute_psd(iq, cfg.fft_size, "hann", cfg.sample_rate)
        if self.on_spectrum_data:
            self.on_spectrum_data(self._focus_channel, self._focus_freq,
                                  cfg.sample_rate, psd)

    def _end_focus(self) -> None:
        done_meta = None
        with self._lock:
            # A manual focus without a timed duration stays put.
            if self._manual_focus and self._focus_until is None:
                return
            self._focus_freq = None
            self._focus_until = None
            if self._focus_recording and self.recorder is not None:
                done_meta = self.recorder.stop()
                if done_meta:
                    logger.info("Finished auto-recording: %s (%.1fs)",
                                done_meta.path, done_meta.duration_s)
            self._focus_recording = False
            self._manual_focus = False
        if done_meta is not None:
            self._mark_pending_done(done_meta.center_freq_hz, done_meta.path)
