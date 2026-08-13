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
from dataclasses import dataclass
from typing import Callable, List, Optional

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

        # Focus state.
        self._focus_freq: Optional[float] = None
        self._focus_until: Optional[float] = None
        self._focus_recording = False

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
                  duration_s: Optional[float] = None) -> None:
        """Lock RX1 onto a specific frequency (optionally for a duration)."""
        with self._lock:
            self._focus_freq = freq
            self._focus_until = (time.time() + duration_s) if duration_s else None
            if bandwidth:
                self.focus_cfg.bandwidth = bandwidth
            if self.focus_dev is not None and not self._shared_device:
                self.focus_dev.set_frequency(self._focus_channel, freq)
                if bandwidth:
                    self.focus_dev.set_bandwidth(self._focus_channel, bandwidth)

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
                if (self.recorder is not None and not self.recorder.is_recording):
                    self.auto_record_unknown(ev)
        if self.scheduler.should_hop():
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
        with self._lock:
            self._focus_freq = None
            self._focus_until = None
            if self._focus_recording and self.recorder is not None:
                meta = self.recorder.stop()
                if meta:
                    logger.info("Finished auto-recording: %s (%.1fs)",
                                meta.path, meta.duration_s)
            self._focus_recording = False
