"""Core DSP primitives for SDR Hunter.

Implements FFT/PSD computation, spectrogram/waterfall generation, decimation,
and basic filtering using numpy/scipy. All functions operate on complex64 IQ
blocks and are pure (no device dependency) so they can be unit tested easily.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

try:
    from scipy import signal as _sp_signal  # type: ignore
    HAVE_SCIPY = True
except Exception:  # noqa: BLE001
    _sp_signal = None  # type: ignore
    HAVE_SCIPY = False


def make_window(n: int, kind: str = "hann") -> np.ndarray:
    """Return a window of length ``n``."""
    kind = kind.lower()
    if kind == "hann":
        return np.hanning(n).astype(np.float64)
    if kind == "hamming":
        return np.hamming(n).astype(np.float64)
    if kind == "blackman":
        return np.blackman(n).astype(np.float64)
    if kind in ("rect", "boxcar", "none"):
        return np.ones(n, dtype=np.float64)
    return np.hanning(n).astype(np.float64)


def compute_psd(iq: np.ndarray, fft_size: int = 4096, window: str = "hann",
                fs: float = 1.0) -> np.ndarray:
    """Compute a single-shot power spectral density in dB.

    Returns an ``fft_size``-length array of dB values, FFT-shifted so index 0
    is the lowest frequency (center - fs/2) and the last is (center + fs/2).
    """
    if iq.size < fft_size:
        iq = np.concatenate([iq, np.zeros(fft_size - iq.size, dtype=np.complex64)])
    else:
        iq = iq[:fft_size]
    win = make_window(fft_size, window)
    win_gain = np.sum(win ** 2)
    spec = np.fft.fftshift(np.fft.fft(iq * win, fft_size))
    psd = (np.abs(spec) ** 2) / (win_gain * fs)
    psd_db = 10.0 * np.log10(psd + 1e-20)
    return psd_db.astype(np.float64)


def welch_psd(iq: np.ndarray, fft_size: int = 4096, window: str = "hann",
              fs: float = 1.0, overlap: float = 0.5) -> np.ndarray:
    """Averaged (Welch) PSD in dB for a smoother spectrum estimate."""
    step = max(1, int(fft_size * (1.0 - overlap)))
    if iq.size < fft_size:
        return compute_psd(iq, fft_size, window, fs)
    win = make_window(fft_size, window)
    win_gain = np.sum(win ** 2)
    acc = np.zeros(fft_size, dtype=np.float64)
    count = 0
    for start in range(0, iq.size - fft_size + 1, step):
        seg = iq[start:start + fft_size]
        spec = np.fft.fftshift(np.fft.fft(seg * win, fft_size))
        acc += (np.abs(spec) ** 2) / (win_gain * fs)
        count += 1
    if count == 0:
        return compute_psd(iq, fft_size, window, fs)
    return (10.0 * np.log10(acc / count + 1e-20)).astype(np.float64)


def freq_axis(center_freq: float, fs: float, fft_size: int) -> np.ndarray:
    """Return the absolute frequency for each PSD bin (Hz)."""
    return center_freq + np.linspace(-fs / 2.0, fs / 2.0, fft_size, endpoint=False)


def decimate(iq: np.ndarray, factor: int) -> np.ndarray:
    """Decimate IQ by an integer factor with anti-alias filtering."""
    if factor <= 1:
        return iq
    if HAVE_SCIPY:
        return _sp_signal.decimate(iq, factor, ftype="fir").astype(np.complex64)
    # Fallback: simple FIR low-pass then downsample.
    taps = np.ones(factor, dtype=np.float64) / factor
    filt = np.convolve(iq, taps, mode="same")
    return filt[::factor].astype(np.complex64)


def lowpass_fir(iq: np.ndarray, cutoff_hz: float, fs: float,
                num_taps: int = 65) -> np.ndarray:
    """Apply a low-pass FIR filter to complex IQ."""
    if HAVE_SCIPY:
        taps = _sp_signal.firwin(num_taps, cutoff_hz, fs=fs)
        return _sp_signal.lfilter(taps, 1.0, iq).astype(np.complex64)
    # Naive windowed-sinc fallback.
    fc = cutoff_hz / (fs / 2.0)
    n = np.arange(num_taps) - (num_taps - 1) / 2.0
    taps = np.sinc(fc * n) * np.hamming(num_taps)
    taps /= np.sum(taps)
    return np.convolve(iq, taps, mode="same").astype(np.complex64)


def frequency_shift(iq: np.ndarray, shift_hz: float, fs: float) -> np.ndarray:
    """Shift the IQ spectrum by ``shift_hz`` (mix with a complex exponential)."""
    n = np.arange(iq.size)
    lo = np.exp(-2j * np.pi * shift_hz * n / fs).astype(np.complex64)
    return (iq * lo).astype(np.complex64)


@dataclass
class Spectrogram:
    """A rolling spectrogram / waterfall buffer of PSD rows."""

    fft_size: int
    max_rows: int = 512
    _rows: Optional[np.ndarray] = None
    _count: int = 0

    def __post_init__(self) -> None:
        self._rows = np.full((self.max_rows, self.fft_size), -120.0,
                             dtype=np.float32)

    def push(self, psd_db: np.ndarray) -> None:
        """Append a new PSD row (scrolls the buffer up)."""
        assert self._rows is not None
        if psd_db.size != self.fft_size:
            psd_db = np.interp(
                np.linspace(0, psd_db.size - 1, self.fft_size),
                np.arange(psd_db.size), psd_db,
            )
        self._rows = np.roll(self._rows, -1, axis=0)
        self._rows[-1] = psd_db.astype(np.float32)
        self._count += 1

    @property
    def data(self) -> np.ndarray:
        assert self._rows is not None
        return self._rows

    def normalized(self, vmin: float = -100.0, vmax: float = -20.0) -> np.ndarray:
        """Return the buffer scaled to 0..1 for color mapping."""
        assert self._rows is not None
        clipped = np.clip(self._rows, vmin, vmax)
        return (clipped - vmin) / max(1e-9, (vmax - vmin))


class SpectrumAccumulator:
    """Maintains peak-hold, min-hold and running-average traces."""

    def __init__(self, fft_size: int, avg_alpha: float = 0.3):
        self.fft_size = fft_size
        self.avg_alpha = avg_alpha
        self.peak_hold: Optional[np.ndarray] = None
        self.min_hold: Optional[np.ndarray] = None
        self.average: Optional[np.ndarray] = None

    def update(self, psd_db: np.ndarray) -> None:
        if self.peak_hold is None:
            self.peak_hold = psd_db.copy()
            self.min_hold = psd_db.copy()
            self.average = psd_db.copy()
            return
        self.peak_hold = np.maximum(self.peak_hold, psd_db)
        self.min_hold = np.minimum(self.min_hold, psd_db)
        self.average = (self.avg_alpha * psd_db
                        + (1.0 - self.avg_alpha) * self.average)

    def reset(self) -> None:
        self.peak_hold = None
        self.min_hold = None
        self.average = None


def estimate_noise_floor(psd_db: np.ndarray, percentile: float = 30.0) -> float:
    """Estimate the noise floor as a low percentile of the PSD."""
    return float(np.percentile(psd_db, percentile))
