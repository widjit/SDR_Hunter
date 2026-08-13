"""Spectrum baseline capture, persistence and comparison.

A *baseline* is an averaged spectrum (and a list of known signals) captured at a
named location. Live spectra can be compared against a baseline to flag
anomalies: new signals, power changes, or disappeared signals.
"""
from __future__ import annotations

import glob
import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

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
