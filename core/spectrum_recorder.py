"""Lightweight PSD-frame recorder and replay loader.

This records the *spectrum frame* stream (the power-spectral-density arrays that
feed the RX0 scanner waterfall/scope) rather than raw IQ. It is deliberately
independent of :class:`core.recording_engine.IQRecorder` — the goal is a small,
self-describing capture of what the waterfall/scope displayed so it can be
replayed offline through the same widgets.

The on-disk format is a compressed NumPy ``.npz`` containing:

* ``psd``          -- 2D float32 array, shape ``(n_frames, n_bins)`` (rows are
                      padded with NaN when frame widths differ).
* ``timestamps``   -- 1D float64 wall-clock time of each frame (seconds).
* ``center_freqs`` -- 1D float64 center frequency of each frame (Hz).
* ``sample_rates`` -- 1D float64 sample rate / span of each frame (Hz).
* ``widths``       -- 1D int32 valid bin count of each frame (before padding).
* ``channel``      -- 0-d int, the receiver channel that was recorded.

Everything here is Qt-free and importable/unit-testable headless.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

SPECTRUM_FILE_SUFFIX = ".npz"


class SpectrumRecorder:
    """Accumulate PSD frames in memory and save them as a compressed ``.npz``.

    Usage::

        rec = SpectrumRecorder()
        rec.start("/path/spectrum_20240101_120000.npz")
        rec.add_frame(center_freq, sample_rate, psd_db, channel=0)
        ...
        path = rec.stop()   # writes the file, returns its path
    """

    def __init__(self) -> None:
        self._recording = False
        self._path: Optional[str] = None
        self._channel = 0
        self._psd: List[np.ndarray] = []
        self._ts: List[float] = []
        self._centers: List[float] = []
        self._rates: List[float] = []
        self._start_time = 0.0

    # ------------------------------------------------------------------
    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def frame_count(self) -> int:
        return len(self._psd)

    @property
    def path(self) -> Optional[str]:
        return self._path

    # ------------------------------------------------------------------
    def start(self, path: str, channel: int = 0) -> str:
        """Begin recording to ``path`` (frames are only written on stop)."""
        self._recording = True
        self._path = path
        self._channel = int(channel)
        self._psd = []
        self._ts = []
        self._centers = []
        self._rates = []
        self._start_time = time.time()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        return path

    def add_frame(self, center_freq: float, sample_rate: float,
                  psd_db: Any, timestamp: Optional[float] = None,
                  channel: Optional[int] = None) -> None:
        """Append one PSD frame. No-op if not recording or channel mismatches."""
        if not self._recording:
            return
        if channel is not None and int(channel) != self._channel:
            return
        arr = np.asarray(psd_db, dtype=np.float32).ravel()
        if arr.size == 0:
            return
        self._psd.append(arr)
        self._ts.append(float(timestamp if timestamp is not None else time.time()))
        self._centers.append(float(center_freq))
        self._rates.append(float(sample_rate))

    def stop(self) -> Optional[str]:
        """Write the recording to disk and reset. Returns the path (or None)."""
        if not self._recording:
            return None
        self._recording = False
        path = self._path
        if not path or not self._psd:
            self._path = None
            return None
        widths = np.array([a.size for a in self._psd], dtype=np.int32)
        max_w = int(widths.max())
        n = len(self._psd)
        psd = np.full((n, max_w), np.nan, dtype=np.float32)
        for i, a in enumerate(self._psd):
            psd[i, : a.size] = a
        np.savez_compressed(
            path,
            psd=psd,
            timestamps=np.asarray(self._ts, dtype=np.float64),
            center_freqs=np.asarray(self._centers, dtype=np.float64),
            sample_rates=np.asarray(self._rates, dtype=np.float64),
            widths=widths,
            channel=np.int32(self._channel),
        )
        self._path = None
        return path


@dataclass
class SpectrumRecording:
    """A loaded spectrum recording with convenient accessors."""

    psd: np.ndarray                       # (n_frames, n_bins), NaN-padded
    timestamps: np.ndarray                # (n_frames,)
    center_freqs: np.ndarray              # (n_frames,)
    sample_rates: np.ndarray              # (n_frames,)
    widths: np.ndarray                    # (n_frames,)
    channel: int = 0
    path: str = ""

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: str) -> "SpectrumRecording":
        """Load a recording saved by :class:`SpectrumRecorder`."""
        with np.load(path, allow_pickle=False) as z:
            psd = np.asarray(z["psd"], dtype=np.float32)
            timestamps = np.asarray(z["timestamps"], dtype=np.float64)
            center_freqs = np.asarray(z["center_freqs"], dtype=np.float64)
            sample_rates = np.asarray(z["sample_rates"], dtype=np.float64)
            if "widths" in z:
                widths = np.asarray(z["widths"], dtype=np.int32)
            else:
                widths = np.full(psd.shape[0], psd.shape[1], dtype=np.int32)
            channel = int(z["channel"]) if "channel" in z else 0
        return cls(psd=psd, timestamps=timestamps, center_freqs=center_freqs,
                   sample_rates=sample_rates, widths=widths, channel=channel,
                   path=path)

    # ------------------------------------------------------------------
    @property
    def frame_count(self) -> int:
        return int(self.psd.shape[0])

    @property
    def duration(self) -> float:
        """Wall-clock span of the recording in seconds."""
        if self.timestamps.size < 2:
            return 0.0
        return float(self.timestamps[-1] - self.timestamps[0])

    @property
    def freq_range(self) -> tuple:
        """Approximate (min, max) frequency covered, in Hz."""
        if self.center_freqs.size == 0:
            return (0.0, 0.0)
        lo = float(np.min(self.center_freqs - self.sample_rates / 2.0))
        hi = float(np.max(self.center_freqs + self.sample_rates / 2.0))
        return (lo, hi)

    def frame(self, i: int) -> Dict[str, Any]:
        """Return frame ``i`` as a display-compatible dict."""
        w = int(self.widths[i]) if i < self.widths.size else self.psd.shape[1]
        row = self.psd[i, :w]
        return {
            "channel": self.channel,
            "center_freq": float(self.center_freqs[i]),
            "sample_rate": float(self.sample_rates[i]),
            "psd_db": row.astype(float).tolist(),
            "timestamp": float(self.timestamps[i]),
        }

    def info(self) -> Dict[str, Any]:
        lo, hi = self.freq_range
        return {
            "frame_count": self.frame_count,
            "duration": self.duration,
            "freq_min_hz": lo,
            "freq_max_hz": hi,
            "channel": self.channel,
            "path": self.path,
        }


def default_spectrum_path(recordings_dir: str,
                          when: Optional[float] = None) -> str:
    """Return a timestamped path for a new spectrum recording."""
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(when or time.time()))
    return os.path.join(recordings_dir, f"spectrum_{stamp}{SPECTRUM_FILE_SUFFIX}")
