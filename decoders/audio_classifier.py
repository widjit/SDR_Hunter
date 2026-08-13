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
    category: str = "unknown"
    matched_signature: Optional[Dict[str, Any]] = None
    features: Optional[AudioFeatures] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"label": self.label, "confidence": round(self.confidence, 3),
                "modulation": self.modulation, "category": self.category,
                "metadata": self.metadata}


# Built-in audio signal signatures. ``kind`` describes the demodulated audio
# character; matching uses bandwidth + modulation family.
DEFAULT_AUDIO_SIGNATURES: List[Dict[str, Any]] = [
    {"label": "Broadcast FM (stereo)", "category": "broadcast_fm",
     "modulation": "WBFM", "min_bw_hz": 100e3, "max_bw_hz": 256e3,
     "requires_pilot_19k": True},
    {"label": "Broadcast FM (mono)", "category": "broadcast_fm",
     "modulation": "WBFM", "min_bw_hz": 80e3, "max_bw_hz": 256e3,
     "requires_pilot_19k": False},
    {"label": "NOAA Weather Radio", "category": "noaa_weather",
     "modulation": "NBFM", "min_bw_hz": 8e3, "max_bw_hz": 20e3,
     "requires_pilot_19k": False, "freq_lo_hz": 162.4e6, "freq_hi_hz": 162.55e6},
    {"label": "Aircraft ATIS/VOLMET", "category": "atis_volmet",
     "modulation": "AM", "min_bw_hz": 4e3, "max_bw_hz": 12e3,
     "requires_pilot_19k": False, "freq_lo_hz": 108e6, "freq_hi_hz": 137e6},
    {"label": "NBFM Voice Comms", "category": "voice_comms",
     "modulation": "NBFM", "min_bw_hz": 8e3, "max_bw_hz": 25e3,
     "requires_pilot_19k": False},
    {"label": "APRS / Packet (AFSK)", "category": "aprs",
     "modulation": "NBFM-AFSK", "min_bw_hz": 8e3, "max_bw_hz": 20e3,
     "requires_pilot_19k": False, "afsk_tones": [1200.0, 2200.0]},
    {"label": "Broadcast AM", "category": "broadcast_am",
     "modulation": "AM", "min_bw_hz": 5e3, "max_bw_hz": 20e3,
     "requires_pilot_19k": False},
    {"label": "Digital Data (modem)", "category": "digital_data",
     "modulation": "FSK", "min_bw_hz": 1e3, "max_bw_hz": 20e3,
     "requires_pilot_19k": False, "high_flatness": True},
    {"label": "DTMF Tones", "category": "dtmf",
     "modulation": "DTMF", "min_bw_hz": 0.0, "max_bw_hz": 4e3,
     "requires_pilot_19k": False, "detect_dtmf": True},
    {"label": "SSB Voice", "category": "voice_comms", "modulation": "SSB",
     "min_bw_hz": 2e3, "max_bw_hz": 4e3, "requires_pilot_19k": False},
    {"label": "CW / Morse", "category": "cw", "modulation": "CW",
     "min_bw_hz": 0.0, "max_bw_hz": 1e3, "requires_pilot_19k": False},
]

# DTMF tone pairs (low-group x high-group), Hz.
DTMF_LOW = [697.0, 770.0, 852.0, 941.0]
DTMF_HIGH = [1209.0, 1336.0, 1477.0, 1633.0]


class AudioClassifier:
    """Feature-based classifier for demodulated audio / baseband IQ."""

    def __init__(self, signatures: Optional[List[Dict[str, Any]]] = None,
                 db_path: Optional[str] = None):
        self.signatures = signatures or list(DEFAULT_AUDIO_SIGNATURES)
        # Known-audio-signal reference database (from audio_signals.json).
        self.reference_signals: List[Dict[str, Any]] = []
        self.ctcss_tones: List[float] = []
        if db_path:
            self.load_db(db_path)

    def add_signature(self, signature: Dict[str, Any]) -> None:
        self.signatures.append(signature)

    def load_db(self, path: str) -> None:
        """Load the audio-signal reference database (audio_signals.json)."""
        import json
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.reference_signals = data.get("signals", [])
            self.ctcss_tones = data.get("ctcss_tones_hz", [])
        except (OSError, ValueError):
            self.reference_signals = []

    def match_reference(self, freq_hz: float, tolerance_hz: float = 50e3
                        ) -> Optional[Dict[str, Any]]:
        """Return the nearest reference signal to ``freq_hz`` if within tol."""
        best, best_d = None, tolerance_hz
        for sig in self.reference_signals:
            f = float(sig.get("freq_hz", 0) or 0)
            if f <= 0:
                continue
            d = abs(f - freq_hz)
            if d <= best_d:
                best_d, best = d, sig
        return best

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
    def classify(self, iq: np.ndarray, sample_rate: float,
                 freq_hz: Optional[float] = None,
                 audio: Optional[np.ndarray] = None,
                 audio_rate: float = 48000.0) -> ClassificationResult:
        """Classify a baseband IQ block into an audio signal type.

        ``freq_hz`` (if given) adds band context (e.g. NOAA weather band,
        airband ATIS). ``audio`` is optional demodulated audio used for
        tone-based detection (DTMF, AFSK).
        """
        feats = self.extract_features(iq, sample_rate)
        dtmf = self.detect_dtmf(audio, audio_rate) if audio is not None else None
        best: Optional[Dict[str, Any]] = None
        best_conf = 0.0
        for sig in self.signatures:
            conf = self._score(feats, sig, freq_hz, dtmf is not None)
            if conf > best_conf:
                best_conf = conf
                best = sig
        metadata: Dict[str, Any] = {}
        ref = self.match_reference(freq_hz) if freq_hz else None
        if ref:
            metadata["reference"] = ref.get("label")
            metadata["content"] = ref.get("content")
            metadata["region"] = ref.get("region")
            best_conf = min(1.0, best_conf + 0.15)
        if dtmf:
            metadata["dtmf_digit"] = dtmf
        if best is None:
            return ClassificationResult("unknown", 0.0, features=feats,
                                        metadata=metadata)
        return ClassificationResult(
            label=best["label"], confidence=best_conf,
            modulation=best.get("modulation", "unknown"),
            category=best.get("category", "unknown"),
            matched_signature=best, features=feats, metadata=metadata,
        )

    def detect_dtmf(self, audio: Optional[np.ndarray], rate: float
                    ) -> Optional[str]:
        """Detect a DTMF digit from a short audio block via Goertzel energy."""
        if audio is None:
            return None
        audio = np.asarray(audio, dtype=np.float64)
        if audio.size < int(rate * 0.02):
            return None
        def goertzel(freq: float) -> float:
            k = int(0.5 + audio.size * freq / rate)
            w = 2.0 * np.pi * k / audio.size
            coeff = 2.0 * np.cos(w)
            s0 = s1 = s2 = 0.0
            for x in audio:
                s0 = x + coeff * s1 - s2
                s2, s1 = s1, s0
            return s1 * s1 + s2 * s2 - coeff * s1 * s2
        low_e = [goertzel(f) for f in DTMF_LOW]
        high_e = [goertzel(f) for f in DTMF_HIGH]
        li, hi = int(np.argmax(low_e)), int(np.argmax(high_e))
        total = sum(low_e) + sum(high_e) + 1e-20
        # Require the two winning tones to dominate.
        if (low_e[li] + high_e[hi]) / total < 0.5:
            return None
        keypad = [["1", "2", "3", "A"], ["4", "5", "6", "B"],
                  ["7", "8", "9", "C"], ["*", "0", "#", "D"]]
        return keypad[li][hi]

    @staticmethod
    def _score(feats: AudioFeatures, sig: Dict[str, Any],
               freq_hz: Optional[float] = None,
               dtmf_present: bool = False) -> float:
        lo = sig.get("min_bw_hz", 0.0)
        hi = sig.get("max_bw_hz", 1e12)
        if not (lo <= feats.bandwidth_hz <= hi):
            return 0.0
        score = 0.55
        if sig.get("requires_pilot_19k"):
            score += 0.3 if feats.has_pilot_19k else -0.4
        # AM has high envelope variance; FM/SSB relatively constant envelope.
        if sig.get("modulation") == "AM" and feats.envelope_variance > 0.2:
            score += 0.2
        if sig.get("modulation", "").endswith("FM") and feats.envelope_variance < 0.2:
            score += 0.1
        if sig.get("high_flatness") and feats.spectral_flatness > 0.5:
            score += 0.2
        if sig.get("detect_dtmf"):
            score += 0.4 if dtmf_present else -0.5
        # Band context bonus.
        flo, fhi = sig.get("freq_lo_hz"), sig.get("freq_hi_hz")
        if freq_hz is not None and flo is not None and fhi is not None:
            score += 0.25 if (flo <= freq_hz <= fhi) else -0.15
        return max(0.0, min(1.0, score))
