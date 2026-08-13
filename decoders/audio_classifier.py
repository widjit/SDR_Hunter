"""Audio / broadcast signal classifier.

Classifies a demodulated audio (or baseband IQ) signal against a database of
known audio-signal signatures, and distinguishes AM vs FM broadcast using
simple spectral features. Extensible: signatures are plain dicts so more can be
added to the database at runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from core import dsp_engine


@dataclass
class AudioFeatures:
    """Extracted features from an audio or baseband signal."""

    bandwidth_hz: float
    spectral_flatness: float
    spectral_centroid_hz: float
    has_pilot_19k: bool = False
    has_rds_57k: bool = False
    envelope_variance: float = 0.0


@dataclass
class ClassificationResult:
    """Result of classifying a signal."""

    label: str
    confidence: float
    modulation: str = "unknown"
    matched_signature: Optional[Dict[str, Any]] = None
    features: Optional[AudioFeatures] = None


# Built-in audio signal signatures. ``kind`` describes the demodulated audio
# character; matching uses bandwidth + modulation family.
DEFAULT_AUDIO_SIGNATURES: List[Dict[str, Any]] = [
    {"label": "WBFM Broadcast (stereo)", "modulation": "WBFM",
     "min_bw_hz": 100e3, "max_bw_hz": 256e3, "requires_pilot_19k": True},
    {"label": "WBFM Broadcast (mono)", "modulation": "WBFM",
     "min_bw_hz": 80e3, "max_bw_hz": 256e3, "requires_pilot_19k": False},
    {"label": "NBFM Voice", "modulation": "NBFM",
     "min_bw_hz": 8e3, "max_bw_hz": 25e3, "requires_pilot_19k": False},
    {"label": "AM Broadcast", "modulation": "AM",
     "min_bw_hz": 5e3, "max_bw_hz": 20e3, "requires_pilot_19k": False},
    {"label": "SSB Voice", "modulation": "SSB",
     "min_bw_hz": 2e3, "max_bw_hz": 4e3, "requires_pilot_19k": False},
    {"label": "CW / Morse", "modulation": "CW",
     "min_bw_hz": 0.0, "max_bw_hz": 1e3, "requires_pilot_19k": False},
]


class AudioClassifier:
    """Feature-based classifier for demodulated audio / baseband IQ."""

    def __init__(self, signatures: Optional[List[Dict[str, Any]]] = None):
        self.signatures = signatures or list(DEFAULT_AUDIO_SIGNATURES)

    def add_signature(self, signature: Dict[str, Any]) -> None:
        self.signatures.append(signature)

    # ------------------------------------------------------------------
    # Feature extraction
    # ------------------------------------------------------------------
    def extract_features(self, iq: np.ndarray, sample_rate: float) -> AudioFeatures:
        """Extract spectral features from a baseband IQ block."""
        n = min(8192, iq.size) if iq.size else 0
        if n < 16:
            return AudioFeatures(0.0, 0.0, 0.0)
        psd_db = dsp_engine.compute_psd(iq, n, "hann", sample_rate)
        lin = 10.0 ** (psd_db / 10.0)
        freqs = dsp_engine.freq_axis(0.0, sample_rate, psd_db.size)

        # Occupied bandwidth: span holding 99% of power.
        bandwidth = self._occupied_bw(lin, sample_rate)
        # Spectral flatness.
        gmean = np.exp(np.mean(np.log(lin + 1e-20)))
        amean = np.mean(lin) + 1e-20
        flatness = float(gmean / amean)
        # Spectral centroid (magnitude of frequency offset).
        centroid = float(np.sum(np.abs(freqs) * lin) / (np.sum(lin) + 1e-20))
        # Pilot / RDS detection (for wideband FM MPX).
        pilot = self._tone_present(psd_db, freqs, 19000.0)
        rds = self._tone_present(psd_db, freqs, 57000.0)
        # Envelope variance (AM has high amplitude variation).
        env = np.abs(iq)
        env_var = float(np.var(env) / (np.mean(env) ** 2 + 1e-20))
        return AudioFeatures(bandwidth, flatness, centroid, pilot, rds, env_var)

    @staticmethod
    def _occupied_bw(psd_lin: np.ndarray, sample_rate: float,
                     frac: float = 0.99) -> float:
        total = np.sum(psd_lin)
        if total <= 0:
            return 0.0
        csum = np.cumsum(psd_lin) / total
        lo = int(np.searchsorted(csum, (1 - frac) / 2))
        hi = int(np.searchsorted(csum, 1 - (1 - frac) / 2))
        bin_hz = sample_rate / psd_lin.size
        return max(0.0, (hi - lo) * bin_hz)

    @staticmethod
    def _tone_present(psd_db: np.ndarray, freqs: np.ndarray, tone_hz: float,
                      tol_hz: float = 500.0, threshold_db: float = 6.0) -> bool:
        band = np.abs(np.abs(freqs) - tone_hz) < tol_hz
        if not np.any(band):
            return False
        floor = dsp_engine.estimate_noise_floor(psd_db)
        return float(np.max(psd_db[band])) - floor > threshold_db

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------
    def classify(self, iq: np.ndarray, sample_rate: float) -> ClassificationResult:
        """Classify a baseband IQ block into an audio signal type."""
        feats = self.extract_features(iq, sample_rate)
        best: Optional[Dict[str, Any]] = None
        best_conf = 0.0
        for sig in self.signatures:
            conf = self._score(feats, sig)
            if conf > best_conf:
                best_conf = conf
                best = sig
        if best is None:
            return ClassificationResult("unknown", 0.0, features=feats)
        return ClassificationResult(
            label=best["label"], confidence=best_conf,
            modulation=best.get("modulation", "unknown"),
            matched_signature=best, features=feats,
        )

    @staticmethod
    def _score(feats: AudioFeatures, sig: Dict[str, Any]) -> float:
        lo = sig.get("min_bw_hz", 0.0)
        hi = sig.get("max_bw_hz", 1e12)
        if not (lo <= feats.bandwidth_hz <= hi):
            return 0.0
        score = 0.6
        if sig.get("requires_pilot_19k"):
            score += 0.3 if feats.has_pilot_19k else -0.4
        # AM has high envelope variance; FM/SSB relatively constant envelope.
        if sig.get("modulation") == "AM" and feats.envelope_variance > 0.2:
            score += 0.2
        if sig.get("modulation", "").endswith("FM") and feats.envelope_variance < 0.2:
            score += 0.1
        return max(0.0, min(1.0, score))
