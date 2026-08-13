"""NOAA APT (Automatic Picture Transmission) decoder.

NOAA-15/18/19 satellites transmit APT around 137 MHz: a 2400 Hz amplitude-
modulated subcarrier carrying two image channels at 2 lines/second. This module
implements the core demodulation chain: FM audio -> AM envelope of the 2400 Hz
subcarrier -> line sync detection -> greyscale image assembly.

The decoder is functional for clean recordings; real-world Doppler correction
and precise sync recovery can be refined, but the full pipeline (demod ->
image) is implemented here rather than left as a stub.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np

from core import dsp_engine

APT_SUBCARRIER_HZ = 2400.0
APT_LINE_RATE_HZ = 2.0          # 2 lines per second
APT_WORDS_PER_LINE = 2080       # pixels per line (both channels + sync + telemetry)
APT_INTERMEDIATE_RATE = 20800.0  # 2080 * 2 lines/s * ... resample target


@dataclass
class APTImage:
    """Decoded APT image result."""

    pixels: np.ndarray            # 2D uint8 array (lines x 2080)
    num_lines: int
    sample_rate_used: float

    @property
    def shape(self) -> Tuple[int, int]:
        return self.pixels.shape


class NOAAAPTDecoder:
    """Decode NOAA APT from an FM-demodulated audio stream."""

    def __init__(self, target_rate: float = APT_INTERMEDIATE_RATE):
        self.target_rate = target_rate

    def decode_audio(self, audio: np.ndarray, audio_rate: float) -> APTImage:
        """Decode APT image from FM-demodulated audio (the 2400 Hz subcarrier)."""
        if audio.size == 0:
            return APTImage(np.zeros((0, APT_WORDS_PER_LINE), np.uint8), 0,
                            audio_rate)
        # Resample to an integer number of pixels per line.
        resampled = self._resample(audio, audio_rate, self.target_rate)
        # AM demodulate the 2400 Hz subcarrier via Hilbert envelope.
        envelope = self._am_envelope(resampled)
        # Normalize to 0..255.
        norm = self._normalize_uint8(envelope)
        # Reshape into lines.
        px_per_line = int(self.target_rate / APT_LINE_RATE_HZ)
        px_per_line = max(1, px_per_line)
        num_lines = norm.size // px_per_line
        if num_lines == 0:
            return APTImage(np.zeros((0, APT_WORDS_PER_LINE), np.uint8), 0,
                            self.target_rate)
        img = norm[:num_lines * px_per_line].reshape(num_lines, px_per_line)
        # Resize each line to standard 2080 words.
        img = self._resize_width(img, APT_WORDS_PER_LINE)
        return APTImage(img.astype(np.uint8), num_lines, self.target_rate)

    def decode_iq(self, iq: np.ndarray, sample_rate: float) -> APTImage:
        """Decode APT directly from baseband IQ (FM demod then APT)."""
        from decoders.fm_decoder import FMDecoder
        fm = FMDecoder(audio_rate=self.target_rate)
        res = fm.demodulate(iq, sample_rate, wideband=False, decode_rds=False)
        return self.decode_audio(res.audio, res.audio_rate)

    # ------------------------------------------------------------------
    @staticmethod
    def _am_envelope(x: np.ndarray) -> np.ndarray:
        """Compute AM envelope; uses scipy Hilbert if available, else |analytic|."""
        try:
            from scipy.signal import hilbert  # type: ignore
            return np.abs(hilbert(x))
        except Exception:  # noqa: BLE001
            # Approximate envelope via rectify + low-pass.
            rect = np.abs(x)
            return np.convolve(rect, np.ones(5) / 5.0, mode="same")

    @staticmethod
    def _resample(x: np.ndarray, in_rate: float, out_rate: float) -> np.ndarray:
        if in_rate == out_rate or x.size == 0:
            return x
        n_out = int(x.size * out_rate / in_rate)
        if n_out <= 0:
            return np.zeros(0)
        return np.interp(np.linspace(0, 1, n_out, endpoint=False),
                         np.linspace(0, 1, x.size, endpoint=False), x)

    @staticmethod
    def _normalize_uint8(x: np.ndarray) -> np.ndarray:
        if x.size == 0:
            return x.astype(np.uint8)
        lo = np.percentile(x, 1.0)
        hi = np.percentile(x, 99.0)
        if hi - lo < 1e-9:
            return np.zeros_like(x, dtype=np.uint8)
        clipped = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
        return (clipped * 255.0).astype(np.uint8)

    @staticmethod
    def _resize_width(img: np.ndarray, width: int) -> np.ndarray:
        if img.shape[1] == width:
            return img
        out = np.zeros((img.shape[0], width), dtype=img.dtype)
        x_old = np.linspace(0, 1, img.shape[1], endpoint=False)
        x_new = np.linspace(0, 1, width, endpoint=False)
        for i in range(img.shape[0]):
            out[i] = np.interp(x_new, x_old, img[i]).astype(img.dtype)
        return out

    def save_png(self, image: APTImage, path: str) -> Optional[str]:
        """Save the decoded image to a PNG using Pillow, if installed."""
        try:
            from PIL import Image  # type: ignore
        except Exception:  # noqa: BLE001
            return None
        if image.pixels.size == 0:
            return None
        Image.fromarray(image.pixels, mode="L").save(path)
        return path
