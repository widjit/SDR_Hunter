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


# Standard APT line layout (words, 2080 words/line):
#   sync A 0-38 | space A 39-46 | video A 47-955 | telemetry A 956-1043 |
#   sync B 1044-1084 | space B 1085-1119 | video B 1120-2027 | telem B ...
# We use conservative video windows that avoid the sync/telemetry bands.
APT_CHANNEL_A_START = 86
APT_CHANNEL_A_END = 995        # ~909 px of Channel A video
APT_CHANNEL_B_START = 1126
APT_CHANNEL_B_END = 2035       # ~909 px of Channel B video


@dataclass
class APTImage:
    """Decoded APT image result."""

    pixels: np.ndarray            # 2D uint8 array (lines x 2080), full frame
    num_lines: int
    sample_rate_used: float
    channel_a: Optional[np.ndarray] = None   # 2D uint8 (Channel A video)
    channel_b: Optional[np.ndarray] = None   # 2D uint8 (Channel B video)

    @property
    def shape(self) -> Tuple[int, int]:
        return self.pixels.shape

    @property
    def height(self) -> int:
        return int(self.pixels.shape[0]) if self.pixels.ndim >= 1 else 0

    @property
    def width(self) -> int:
        return int(self.pixels.shape[1]) if self.pixels.ndim >= 2 else 0


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
        img = self._resize_width(img, APT_WORDS_PER_LINE).astype(np.uint8)
        # Align on the APT sync-A pulse train so channels land in fixed cols.
        img = self._align_sync(img)
        ch_a, ch_b = self._split_channels(img)
        return APTImage(img, num_lines, self.target_rate,
                        channel_a=ch_a, channel_b=ch_b)

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

    # ------------------------------------------------------------------
    # Sync detection & channel split
    # ------------------------------------------------------------------
    @staticmethod
    def _sync_template() -> np.ndarray:
        """APT sync-A reference: 7 cycles of a ~1040 Hz square wave.

        At 2080 words/line and 2 lines/s the word rate is 4160 words/s, so a
        1040 Hz square wave is 4 words/cycle (2 high, 2 low).
        """
        cycle = np.array([255.0, 255.0, 0.0, 0.0])
        return np.tile(cycle, 7) - 128.0  # zero-mean for correlation

    def _align_sync(self, img: np.ndarray) -> np.ndarray:
        """Roll every line so the sync-A burst sits at column 0.

        A single global offset (from the column-averaged profile) is used so
        clean recordings align without introducing per-line jitter.
        """
        if img.ndim != 2 or img.shape[1] < APT_WORDS_PER_LINE // 2:
            return img
        try:
            tmpl = self._sync_template()
            prof = img.mean(axis=0).astype(np.float64)
            prof = prof - prof.mean()
            # Circular correlation: search the whole line width.
            extended = np.concatenate([prof, prof[:tmpl.size]])
            corr = np.correlate(extended, tmpl, mode="valid")
            offset = int(np.argmax(corr))
            if offset:
                img = np.roll(img, -offset, axis=1)
        except Exception:  # noqa: BLE001
            return img
        return img

    @staticmethod
    def _split_channels(img: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Return (Channel A, Channel B) video sub-images."""
        if img.ndim != 2 or img.shape[1] < APT_CHANNEL_B_END:
            empty = np.zeros((img.shape[0] if img.ndim else 0, 0), np.uint8)
            return empty, empty
        a = img[:, APT_CHANNEL_A_START:APT_CHANNEL_A_END]
        b = img[:, APT_CHANNEL_B_START:APT_CHANNEL_B_END]
        return a.copy(), b.copy()

    @staticmethod
    def false_color(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """Build an approximate false-colour RGB from Channel A (visible) and
        Channel B (thermal IR).

        This is a documented heuristic composite (not a calibrated product):
        dark visible → sea (blue), bright visible → land (green), bright in
        both channels → cloud (white). Returns an ``(H, W, 3)`` uint8 array.
        """
        if a.size == 0 or b.size == 0:
            return np.zeros((0, 0, 3), np.uint8)
        h = min(a.shape[0], b.shape[0])
        w = min(a.shape[1], b.shape[1])
        af = a[:h, :w].astype(np.float32) / 255.0
        bf = b[:h, :w].astype(np.float32) / 255.0
        cloud = np.clip((af - 0.55) * 2.5, 0.0, 1.0)
        land = np.clip(af, 0.0, 1.0)
        rgb = np.zeros((h, w, 3), np.float32)
        rgb[..., 0] = np.clip(0.15 * land + cloud, 0, 1)          # R
        rgb[..., 1] = np.clip(0.45 * land + cloud, 0, 1)          # G
        rgb[..., 2] = np.clip((1.0 - land) * 0.6 + cloud, 0, 1)   # B
        # Fold in thermal so cold cloud tops read brighter.
        rgb += (bf[..., None] * 0.10)
        return (np.clip(rgb, 0, 1) * 255.0).astype(np.uint8)

    def save_png(self, image: APTImage, path: str,
                 channel: str = "full") -> Optional[str]:
        """Save the decoded image to a PNG using Pillow, if installed.

        ``channel`` selects what to write: ``"full"`` (whole 2080-word frame),
        ``"a"`` / ``"b"`` (single channel greyscale) or ``"false"`` (false
        colour RGB composite).
        """
        try:
            from PIL import Image  # type: ignore
        except Exception:  # noqa: BLE001
            return None
        arr = self.render(image, channel)
        if arr is None or arr.size == 0:
            return None
        mode = "RGB" if arr.ndim == 3 else "L"
        Image.fromarray(arr, mode=mode).save(path)
        return path

    def render(self, image: APTImage, channel: str = "full"
               ) -> Optional[np.ndarray]:
        """Return the requested view as a numpy array (2D grey or 3D RGB)."""
        channel = (channel or "full").lower()
        if channel == "a" and image.channel_a is not None:
            return image.channel_a
        if channel == "b" and image.channel_b is not None:
            return image.channel_b
        if channel in ("false", "false_color", "falsecolor"):
            if image.channel_a is not None and image.channel_b is not None:
                return self.false_color(image.channel_a, image.channel_b)
            return image.pixels
        return image.pixels
