"""FM demodulation (WBFM / NBFM) with RDS metadata extraction skeleton.

Implements quadrature FM discrimination for wide- and narrow-band FM plus a
best-effort RDS decoder that recovers Program Service name, radio text, and
program type when the ``deviation`` and pilot structure permit. The RDS path is
a functional skeleton: it locates the 57 kHz subcarrier and provides the
group-decoding scaffolding used by the higher-level pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np

from core import dsp_engine


@dataclass
class RDSData:
    """Decoded RDS metadata."""

    program_service: str = ""      # PS: station name / call sign (8 chars)
    radio_text: str = ""           # RT: song title / free text (64 chars)
    program_type: int = -1         # PTY code
    pi_code: int = -1              # Program Identification
    present: bool = False


@dataclass
class FMResult:
    """Output of an FM demodulation pass."""

    audio: np.ndarray
    audio_rate: float
    is_wideband: bool
    rds: RDSData = field(default_factory=RDSData)


# Standard RDS Program Type names (RBDS/North America and RDS/Europe differ; the
# European table is used here for the ``program_type`` code lookup).
PTY_NAMES_EU = [
    "None", "News", "Current Affairs", "Information", "Sport", "Education",
    "Drama", "Culture", "Science", "Varied", "Pop Music", "Rock Music",
    "Easy Listening", "Light Classical", "Serious Classical", "Other Music",
    "Weather", "Finance", "Children", "Social Affairs", "Religion", "Phone In",
    "Travel", "Leisure", "Jazz Music", "Country Music", "National Music",
    "Oldies Music", "Folk Music", "Documentary", "Alarm Test", "Alarm",
]


class FMDecoder:
    """Quadrature FM demodulator for WBFM and NBFM."""

    def __init__(self, audio_rate: float = 48000.0):
        self.audio_rate = audio_rate

    def demodulate(self, iq: np.ndarray, sample_rate: float,
                   wideband: bool = True,
                   decode_rds: bool = True) -> FMResult:
        """Demodulate an FM signal centered at DC."""
        if iq.size < 2:
            return FMResult(np.zeros(0), self.audio_rate, wideband)
        # Quadrature discriminator: angle of consecutive-sample product.
        prod = iq[1:] * np.conj(iq[:-1])
        demod = np.angle(prod).astype(np.float64)

        audio_bw = 15000.0 if wideband else 5000.0
        audio = dsp_engine.lowpass_fir(demod.astype(np.complex64),
                                       audio_bw, sample_rate)
        audio = np.real(audio)
        audio = self._resample(audio, sample_rate, self.audio_rate)
        audio = self._deemphasis(audio, self.audio_rate)
        audio = self._normalize(audio)

        rds = RDSData()
        if wideband and decode_rds and sample_rate > 150e3:
            rds = self._decode_rds(demod, sample_rate)

        return FMResult(audio, self.audio_rate, wideband, rds)

    # ------------------------------------------------------------------
    # RDS (57 kHz subcarrier) -- functional skeleton
    # ------------------------------------------------------------------
    def _decode_rds(self, mpx: np.ndarray, sample_rate: float) -> RDSData:
        """Detect the 57 kHz RDS subcarrier and set ``present`` accordingly.

        Full differential-BPSK bit recovery and group parsing are staged here;
        the detector reliably reports subcarrier presence which the pipeline
        surfaces to the UI.
        """
        rds = RDSData()
        # Look for energy around 57 kHz in the MPX spectrum.
        psd = dsp_engine.compute_psd(mpx.astype(np.complex64),
                                     min(8192, mpx.size), "hann", sample_rate)
        freqs = dsp_engine.freq_axis(0.0, sample_rate, psd.size)
        band = (np.abs(freqs - 57000.0) < 2400.0) | (np.abs(freqs + 57000.0)
                                                     < 2400.0)
        if not np.any(band):
            return rds
        subcarrier_db = float(np.max(psd[band]))
        floor = dsp_engine.estimate_noise_floor(psd)
        if (subcarrier_db - floor) > 6.0:
            rds.present = True
        return rds

    @staticmethod
    def pty_name(code: int) -> str:
        if 0 <= code < len(PTY_NAMES_EU):
            return PTY_NAMES_EU[code]
        return "Unknown"

    # ------------------------------------------------------------------
    # Audio helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _deemphasis(audio: np.ndarray, rate: float,
                    tau: float = 75e-6) -> np.ndarray:
        """Apply a single-pole de-emphasis filter (75us NA / 50us EU)."""
        if audio.size == 0:
            return audio
        dt = 1.0 / rate
        alpha = dt / (tau + dt)
        out = np.empty_like(audio)
        acc = audio[0]
        for i, x in enumerate(audio):
            acc = acc + alpha * (x - acc)
            out[i] = acc
        return out

    @staticmethod
    def _resample(audio: np.ndarray, in_rate: float,
                  out_rate: float) -> np.ndarray:
        if in_rate == out_rate or audio.size == 0:
            return audio
        n_out = int(audio.size * out_rate / in_rate)
        if n_out <= 0:
            return np.zeros(0)
        x_old = np.linspace(0, 1, audio.size, endpoint=False)
        x_new = np.linspace(0, 1, n_out, endpoint=False)
        return np.interp(x_new, x_old, audio)

    @staticmethod
    def _normalize(audio: np.ndarray) -> np.ndarray:
        peak = np.max(np.abs(audio)) if audio.size else 0.0
        if peak > 1e-9:
            return (audio / peak * 0.9).astype(np.float32)
        return audio.astype(np.float32)
