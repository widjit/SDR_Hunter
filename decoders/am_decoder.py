"""AM demodulation and carrier detection.

Provides envelope-detection AM demodulation with DC removal and audio
resampling, plus a simple carrier-presence detector.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from core import dsp_engine


@dataclass
class AMResult:
    """Output of an AM demodulation pass."""

    audio: np.ndarray
    audio_rate: float
    carrier_present: bool
    carrier_power_db: float


class AMDecoder:
    """Envelope-detector AM demodulator."""

    def __init__(self, audio_rate: float = 48000.0):
        self.audio_rate = audio_rate

    def demodulate(self, iq: np.ndarray, sample_rate: float,
                   audio_cutoff_hz: float = 5000.0) -> AMResult:
        """Demodulate an AM signal centered at DC.

        The input ``iq`` should be baseband (signal of interest at 0 Hz).
        """
        if iq.size == 0:
            return AMResult(np.zeros(0), self.audio_rate, False, -140.0)
        # Envelope = magnitude.
        env = np.abs(iq).astype(np.float64)
        carrier_power_db = 10.0 * np.log10(np.mean(env ** 2) + 1e-20)
        # Remove DC (carrier) component.
        audio = env - np.mean(env)
        # Low-pass to audio bandwidth.
        audio = np.real(dsp_engine.lowpass_fir(
            audio.astype(np.complex64), audio_cutoff_hz, sample_rate))
        audio = self._resample(audio, sample_rate, self.audio_rate)
        audio = self._normalize(audio)
        carrier_present = self.detect_carrier(iq, sample_rate)
        return AMResult(audio, self.audio_rate, carrier_present,
                        carrier_power_db)

    @staticmethod
    def detect_carrier(iq: np.ndarray, sample_rate: float,
                       threshold_db: float = 10.0) -> bool:
        """Detect an AM carrier via DC-bin dominance in the spectrum."""
        if iq.size < 64:
            return False
        psd = dsp_engine.compute_psd(iq, min(4096, iq.size), "hann",
                                     sample_rate)
        center = psd.size // 2
        peak = float(np.max(psd[center - 2:center + 3]))
        floor = dsp_engine.estimate_noise_floor(psd)
        return (peak - floor) > threshold_db

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
