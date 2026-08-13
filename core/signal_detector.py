"""Signal detection engine.

Detects signals in a PSD array using CFAR (Cell-Averaging Constant False Alarm
Rate) and simple thresholding, estimates their bandwidth, provides a coarse
modulation hint, and matches detections against a known-signal database.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

import numpy as np

from .dsp_engine import estimate_noise_floor


@dataclass
class SignalEvent:
    """A single detected signal."""

    freq_hz: float
    bandwidth_hz: float
    power_db: float
    modulation_hint: str = "unknown"
    timestamp: float = field(default_factory=time.time)
    duration_s: float = 0.0
    snr_db: float = 0.0
    is_known: bool = False
    signal_db_match: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class KnownSignalMatcher:
    """Matches detected frequencies against a known-signal database."""

    def __init__(self, signals: Optional[List[Dict[str, Any]]] = None):
        self.signals: List[Dict[str, Any]] = signals or []

    def load(self, signals: List[Dict[str, Any]]) -> None:
        self.signals = signals

    def match(self, freq_hz: float, bandwidth_hz: float = 0.0,
              tolerance_hz: float = 50e3) -> Optional[Dict[str, Any]]:
        """Return the best matching known signal, or ``None``.

        A signal matches if ``freq_hz`` falls within [start-tol, end+tol]. Point
        signals (start==end) match within ``tolerance_hz``.
        """
        best: Optional[Dict[str, Any]] = None
        best_score = float("inf")
        for sig in self.signals:
            start = float(sig.get("freq_start_hz", sig.get("freq_hz", 0)))
            end = float(sig.get("freq_end_hz", start))
            lo, hi = start - tolerance_hz, end + tolerance_hz
            if lo <= freq_hz <= hi:
                center = (start + end) / 2.0
                score = abs(freq_hz - center)
                if score < best_score:
                    best_score = score
                    best = sig
        return best


class SignalDetector:
    """CFAR / threshold based signal detector operating on PSD arrays."""

    def __init__(self, matcher: Optional[KnownSignalMatcher] = None,
                 guard_cells: int = 4, train_cells: int = 16,
                 threshold_db: float = 8.0, min_bin_width: int = 2):
        self.matcher = matcher or KnownSignalMatcher()
        self.guard_cells = guard_cells
        self.train_cells = train_cells
        self.threshold_db = threshold_db
        self.min_bin_width = min_bin_width

    # ------------------------------------------------------------------
    # CFAR
    # ------------------------------------------------------------------
    def cfar_mask(self, psd_db: np.ndarray) -> np.ndarray:
        """Return a boolean mask of bins exceeding the CFAR threshold."""
        n = psd_db.size
        g, t = self.guard_cells, self.train_cells
        psd_lin = 10.0 ** (psd_db / 10.0)
        mask = np.zeros(n, dtype=bool)
        win = g + t
        # Use a sliding cumulative sum for the training average.
        csum = np.concatenate([[0.0], np.cumsum(psd_lin)])
        for i in range(n):
            lo_start = max(0, i - win)
            lo_end = max(0, i - g)
            hi_start = min(n, i + g + 1)
            hi_end = min(n, i + win + 1)
            train_sum = (csum[lo_end] - csum[lo_start]
                         + csum[hi_end] - csum[hi_start])
            train_n = (lo_end - lo_start) + (hi_end - hi_start)
            if train_n <= 0:
                continue
            noise = train_sum / train_n
            noise_db = 10.0 * np.log10(noise + 1e-20)
            if psd_db[i] > noise_db + self.threshold_db:
                mask[i] = True
        return mask

    def threshold_mask(self, psd_db: np.ndarray,
                       margin_db: Optional[float] = None) -> np.ndarray:
        """Simple noise-floor + margin threshold (fast alternative to CFAR)."""
        floor = estimate_noise_floor(psd_db)
        margin = self.threshold_db if margin_db is None else margin_db
        return psd_db > (floor + margin)

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------
    def detect(self, psd_db: np.ndarray, center_freq: float, fs: float,
               use_cfar: bool = True,
               match_tolerance_hz: float = 50e3) -> List[SignalEvent]:
        """Detect signals and return a list of :class:`SignalEvent`."""
        n = psd_db.size
        bin_hz = fs / n
        mask = self.cfar_mask(psd_db) if use_cfar else self.threshold_mask(psd_db)
        floor = estimate_noise_floor(psd_db)
        events: List[SignalEvent] = []

        # Group contiguous True runs into detections.
        idx = 0
        while idx < n:
            if not mask[idx]:
                idx += 1
                continue
            start = idx
            while idx < n and mask[idx]:
                idx += 1
            end = idx  # exclusive
            width = end - start
            if width < self.min_bin_width:
                continue
            seg = psd_db[start:end]
            peak_bin = start + int(np.argmax(seg))
            peak_power = float(psd_db[peak_bin])
            bandwidth_hz = width * bin_hz
            freq_hz = center_freq - fs / 2.0 + (peak_bin + 0.5) * bin_hz
            snr = peak_power - floor
            mod = self.modulation_hint(psd_db, start, end, bin_hz)
            match = self.matcher.match(freq_hz, bandwidth_hz, match_tolerance_hz)
            events.append(SignalEvent(
                freq_hz=freq_hz,
                bandwidth_hz=bandwidth_hz,
                power_db=peak_power,
                modulation_hint=mod,
                snr_db=snr,
                is_known=match is not None,
                signal_db_match=match,
            ))
        return events

    # ------------------------------------------------------------------
    # Modulation hint (very coarse heuristic)
    # ------------------------------------------------------------------
    @staticmethod
    def modulation_hint(psd_db: np.ndarray, start: int, end: int,
                        bin_hz: float) -> str:
        """Guess a modulation family from spectral shape."""
        width_hz = (end - start) * bin_hz
        seg = psd_db[start:end]
        if seg.size < 2:
            return "unknown"
        # Flatness: ratio of geometric mean to arithmetic mean (in linear).
        lin = 10.0 ** (seg / 10.0)
        gmean = np.exp(np.mean(np.log(lin + 1e-20)))
        amean = np.mean(lin)
        flatness = float(gmean / (amean + 1e-20))
        # Heuristics by bandwidth + flatness.
        if width_hz < 6e3:
            return "CW/narrowband"
        if width_hz < 20e3:
            return "AM/SSB/NBFM"
        if width_hz < 200e3:
            return "NBFM/digital-voice" if flatness < 0.5 else "FM"
        if width_hz < 300e3:
            return "WBFM"
        if flatness > 0.7:
            return "spread-spectrum/OFDM"
        if width_hz > 1e6:
            return "wideband-digital"
        return "unknown"
