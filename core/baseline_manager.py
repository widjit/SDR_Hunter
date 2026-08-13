"""Spectrum baseline capture, persistence and comparison.

A *baseline* is an averaged spectrum (and a list of known signals) captured at a
named location. Live spectra can be compared against a baseline to flag
anomalies: new signals, power changes, or disappeared signals.
"""
from __future__ import annotations

import glob
import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from .signal_detector import SignalEvent


@dataclass
class AnomalyEvent:
    """A difference detected between live spectrum and a baseline."""

    kind: str  # "new" | "changed" | "disappeared"
    freq_hz: float
    bandwidth_hz: float
    live_power_db: float = 0.0
    baseline_power_db: float = 0.0
    delta_db: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AnomalyList:
    """Structured result of comparing a live spectrum against a baseline."""

    new_signals: List[AnomalyEvent] = field(default_factory=list)
    disappeared_signals: List[AnomalyEvent] = field(default_factory=list)
    power_changed_signals: List[AnomalyEvent] = field(default_factory=list)

    @property
    def all(self) -> List[AnomalyEvent]:
        return (self.new_signals + self.disappeared_signals
                + self.power_changed_signals)

    def __len__(self) -> int:
        return len(self.all)

    def __bool__(self) -> bool:
        return bool(self.all)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "new": [a.to_dict() for a in self.new_signals],
            "disappeared": [a.to_dict() for a in self.disappeared_signals],
            "power_changed": [a.to_dict() for a in self.power_changed_signals],
        }


@dataclass
class BaselineProfile:
    """A named spectrum baseline for a location."""

    name: str
    location_name: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    created_at: float = field(default_factory=time.time)
    freq_start_hz: float = 0.0
    freq_end_hz: float = 0.0
    bin_hz: float = 0.0
    psd_db: List[float] = field(default_factory=list)
    signals: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BaselineProfile":
        return cls(**data)

    @property
    def psd_array(self) -> np.ndarray:
        return np.asarray(self.psd_db, dtype=np.float64)

    def freq_axis(self) -> np.ndarray:
        n = len(self.psd_db)
        if n == 0:
            return np.array([])
        return np.linspace(self.freq_start_hz, self.freq_end_hz, n, endpoint=False)


class BaselineAccumulator:
    """Accumulates PSD frames spanning a scan range into one averaged baseline.

    The full scan range is divided into ``num_bins`` frequency bins. As spectrum
    frames arrive (each covering a sub-band), their power is accumulated into the
    matching global bins and averaged.
    """

    def __init__(self, freq_start_hz: float, freq_end_hz: float,
                 num_bins: int = 8192):
        self.freq_start_hz = freq_start_hz
        self.freq_end_hz = freq_end_hz
        self.num_bins = num_bins
        self._acc = np.zeros(num_bins, dtype=np.float64)
        self._count = np.zeros(num_bins, dtype=np.int64)
        self._bin_hz = (freq_end_hz - freq_start_hz) / num_bins

    def add_frame(self, freqs_hz: np.ndarray, psd_db: np.ndarray) -> None:
        """Add a spectrum frame (absolute freqs + dB values)."""
        lin = 10.0 ** (psd_db / 10.0)
        bins = ((freqs_hz - self.freq_start_hz) / self._bin_hz).astype(int)
        valid = (bins >= 0) & (bins < self.num_bins)
        np.add.at(self._acc, bins[valid], lin[valid])
        np.add.at(self._count, bins[valid], 1)

    def finalize(self, name: str, location_name: str = "",
                 lat: Optional[float] = None, lon: Optional[float] = None,
                 signals: Optional[List[Dict[str, Any]]] = None
                 ) -> BaselineProfile:
        counts = np.maximum(self._count, 1)
        avg_lin = self._acc / counts
        avg_db = 10.0 * np.log10(avg_lin + 1e-20)
        # Bins that never received data are set to a low floor.
        avg_db[self._count == 0] = -140.0
        return BaselineProfile(
            name=name, location_name=location_name, lat=lat, lon=lon,
            freq_start_hz=self.freq_start_hz, freq_end_hz=self.freq_end_hz,
            bin_hz=self._bin_hz, psd_db=avg_db.tolist(),
            signals=signals or [],
        )


class BaselineManager:
    """Save/load baselines and compare live spectra against them."""

    def __init__(self, baselines_dir: str):
        self.baselines_dir = baselines_dir
        os.makedirs(baselines_dir, exist_ok=True)
        self.active: Optional[BaselineProfile] = None

    # -- persistence -------------------------------------------------------
    def _path_for(self, name: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        return os.path.join(self.baselines_dir, f"{safe}.json")

    def save(self, profile: BaselineProfile) -> str:
        path = self._path_for(profile.name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(profile.to_dict(), fh, indent=2)
        return path

    def load(self, name: str) -> BaselineProfile:
        with open(self._path_for(name), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        profile = BaselineProfile.from_dict(data)
        self.active = profile
        return profile

    def list_baselines(self) -> List[str]:
        names = []
        for path in glob.glob(os.path.join(self.baselines_dir, "*.json")):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    names.append(json.load(fh).get("name",
                                 os.path.basename(path)[:-5]))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(names)

    def delete(self, name: str) -> bool:
        path = self._path_for(name)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    # -- capture -----------------------------------------------------------
    def capture_baseline(self, engine: Any, duration_s: float = 30.0,
                         freq_start: Optional[float] = None,
                         freq_end: Optional[float] = None,
                         name: str = "baseline",
                         location_name: str = "",
                         lat: Optional[float] = None,
                         lon: Optional[float] = None,
                         num_bins: int = 8192,
                         progress_cb: Optional[Callable[[float], None]] = None,
                         ) -> BaselineProfile:
        """Sweep the engine's scanner across a range and average into a baseline.

        The scanner device is retuned across ``[freq_start, freq_end]`` for
        ``duration_s`` seconds; each captured spectrum frame is accumulated into
        a :class:`BaselineAccumulator`. Works with the real or mock SDR device.

        ``progress_cb`` (optional) receives a 0.0-1.0 fraction as capture
        proceeds. Returns the finalized :class:`BaselineProfile` (also saved).
        """
        from . import dsp_engine  # local import to avoid hard dependency

        dev = getattr(engine, "scanner_dev", None)
        if dev is None:
            # Try to set up devices on demand.
            setup = getattr(engine, "setup_devices", None)
            if callable(setup):
                setup()
                dev = getattr(engine, "scanner_dev", None)
        if dev is None:
            raise RuntimeError("Engine has no scanner device for capture")

        channel = getattr(engine, "_scanner_channel", 0)
        cfg = getattr(engine, "scanner_cfg", None)
        sample_rate = getattr(cfg, "sample_rate", 2.048e6)
        fft_size = getattr(cfg, "fft_size", 4096)

        freq_start = freq_start if freq_start is not None else \
            float(getattr(dev, "freq_min", 88e6) or 88e6)
        freq_end = freq_end if freq_end is not None else \
            float(getattr(dev, "freq_max", 108e6) or 108e6)
        if freq_end <= freq_start:
            freq_end = freq_start + sample_rate

        acc = BaselineAccumulator(freq_start, freq_end, num_bins=num_bins)
        step = sample_rate
        centers = np.arange(freq_start + step / 2, freq_end, step)
        if centers.size == 0:
            centers = np.array([(freq_start + freq_end) / 2.0])

        deadline = time.time() + duration_s
        stop_evt = getattr(engine, "_stop_evt", None)
        i = 0
        while time.time() < deadline:
            if stop_evt is not None and stop_evt.is_set():
                break
            center = float(centers[i % len(centers)])
            i += 1
            try:
                dev.set_frequency(channel, center)
                iq = dev.get_iq_stream(channel, fft_size)
                psd = dsp_engine.compute_psd(iq, fft_size, "hann", sample_rate)
                freqs = center + np.linspace(-sample_rate / 2, sample_rate / 2,
                                             len(psd), endpoint=False)
                acc.add_frame(freqs, psd)
            except Exception as exc:  # noqa: BLE001
                logger.debug("capture_baseline frame error: %s", exc)
            if progress_cb is not None:
                frac = 1.0 - max(0.0, (deadline - time.time()) / max(duration_s, 1e-6))
                try:
                    progress_cb(min(1.0, frac))
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(0.01)

        profile = acc.finalize(name=name, location_name=location_name,
                               lat=lat, lon=lon)
        self.save(profile)
        self.active = profile
        if progress_cb is not None:
            try:
                progress_cb(1.0)
            except Exception:  # noqa: BLE001
                pass
        return profile

    # -- comparison --------------------------------------------------------
    def compare_spectrum(self, freqs_hz: np.ndarray, live_psd_db: np.ndarray,
                         baseline: Optional[BaselineProfile] = None,
                         change_threshold_db: float = 10.0
                         ) -> List[AnomalyEvent]:
        """Compare a live spectrum frame against the baseline PSD.

        Returns anomalies where the live power differs from the baseline by more
        than ``change_threshold_db``.
        """
        baseline = baseline or self.active
        if baseline is None or not baseline.psd_db:
            return []
        base_freqs = baseline.freq_axis()
        base_psd = baseline.psd_array
        # Interpolate baseline onto the live frequency grid.
        interp = np.interp(freqs_hz, base_freqs, base_psd,
                           left=-140.0, right=-140.0)
        delta = live_psd_db - interp
        anomalies: List[AnomalyEvent] = []
        bin_hz = float(freqs_hz[1] - freqs_hz[0]) if freqs_hz.size > 1 else 0.0

        above = delta > change_threshold_db
        below = delta < -change_threshold_db
        for mask, kind in ((above, "new"), (below, "disappeared")):
            idx = 0
            n = mask.size
            while idx < n:
                if not mask[idx]:
                    idx += 1
                    continue
                start = idx
                while idx < n and mask[idx]:
                    idx += 1
                end = idx
                peak = start + int(np.argmax(np.abs(delta[start:end])))
                anomalies.append(AnomalyEvent(
                    kind=kind,
                    freq_hz=float(freqs_hz[peak]),
                    bandwidth_hz=(end - start) * bin_hz,
                    live_power_db=float(live_psd_db[peak]),
                    baseline_power_db=float(interp[peak]),
                    delta_db=float(delta[peak]),
                ))
        return anomalies

    def compare_live_to_baseline(self, live_psd_db: np.ndarray,
                                 baseline: Optional[BaselineProfile] = None,
                                 freqs_hz: Optional[np.ndarray] = None,
                                 new_threshold_db: float = 10.0,
                                 change_threshold_db: float = 6.0
                                 ) -> AnomalyList:
        """Compare a live PSD against a baseline and classify anomalies.

        Returns an :class:`AnomalyList` separating *new*, *disappeared* and
        *power_changed* regions. ``freqs_hz`` defaults to the baseline's own
        frequency axis when omitted (assumes ``live_psd_db`` is aligned to it).
        """
        result = AnomalyList()
        baseline = baseline or self.active
        if baseline is None or not baseline.psd_db:
            return result
        base_psd = baseline.psd_array
        if freqs_hz is None:
            freqs_hz = baseline.freq_axis()
            interp = base_psd
            if len(interp) != len(live_psd_db):
                interp = np.interp(
                    np.linspace(0, 1, len(live_psd_db)),
                    np.linspace(0, 1, len(base_psd)), base_psd)
                freqs_hz = np.linspace(baseline.freq_start_hz,
                                       baseline.freq_end_hz,
                                       len(live_psd_db), endpoint=False)
        else:
            interp = np.interp(freqs_hz, baseline.freq_axis(), base_psd,
                               left=-140.0, right=-140.0)
        live_psd_db = np.asarray(live_psd_db, dtype=np.float64)
        n = min(len(live_psd_db), len(interp))
        live_psd_db = live_psd_db[:n]
        interp = interp[:n]
        freqs_hz = np.asarray(freqs_hz)[:n]
        delta = live_psd_db - interp
        bin_hz = float(freqs_hz[1] - freqs_hz[0]) if freqs_hz.size > 1 else 0.0

        def _regions(mask: np.ndarray):
            idx, out = 0, []
            while idx < mask.size:
                if not mask[idx]:
                    idx += 1
                    continue
                start = idx
                while idx < mask.size and mask[idx]:
                    idx += 1
                out.append((start, idx))
            return out

        new_mask = delta > new_threshold_db
        gone_mask = delta < -new_threshold_db
        changed_mask = (np.abs(delta) > change_threshold_db) & ~new_mask & ~gone_mask

        for mask, target, kind in (
            (new_mask, result.new_signals, "new"),
            (gone_mask, result.disappeared_signals, "disappeared"),
            (changed_mask, result.power_changed_signals, "changed"),
        ):
            for start, end in _regions(mask):
                peak = start + int(np.argmax(np.abs(delta[start:end])))
                target.append(AnomalyEvent(
                    kind=kind,
                    freq_hz=float(freqs_hz[peak]),
                    bandwidth_hz=(end - start) * bin_hz,
                    live_power_db=float(live_psd_db[peak]),
                    baseline_power_db=float(interp[peak]),
                    delta_db=float(delta[peak]),
                ))
        return result

    def compare_signals(self, live_signals: List[SignalEvent],
                        baseline: Optional[BaselineProfile] = None,
                        tolerance_hz: float = 50e3) -> List[AnomalyEvent]:
        """Compare detected signals against the baseline's known signal list."""
        baseline = baseline or self.active
        if baseline is None:
            return []
        base_freqs = [float(s.get("freq_hz", s.get("freq_start_hz", 0)))
                      for s in baseline.signals]
        anomalies: List[AnomalyEvent] = []
        for ev in live_signals:
            near = any(abs(ev.freq_hz - bf) <= tolerance_hz for bf in base_freqs)
            if not near:
                anomalies.append(AnomalyEvent(
                    kind="new", freq_hz=ev.freq_hz,
                    bandwidth_hz=ev.bandwidth_hz,
                    live_power_db=ev.power_db, delta_db=ev.snr_db,
                ))
        return anomalies
