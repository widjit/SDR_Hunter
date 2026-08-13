"""IQ recording engine using the SigMF format.

Records complex64 IQ blocks to disk as a SigMF dataset (``.sigmf-data`` +
``.sigmf-meta``). Falls back to writing a plain interleaved-float binary plus a
JSON sidecar if the ``sigmf`` package is not installed. Also supports playback
and simple clip management.
"""
from __future__ import annotations

import json
import os
import time
import threading
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

import numpy as np

try:
    import sigmf  # type: ignore
    from sigmf import SigMFFile  # type: ignore
    HAVE_SIGMF = True
except Exception:  # noqa: BLE001
    sigmf = None  # type: ignore
    SigMFFile = None  # type: ignore
    HAVE_SIGMF = False


@dataclass
class RecordingMeta:
    """Metadata describing an IQ recording."""

    path: str
    center_freq_hz: float
    sample_rate_hz: float
    start_time: float
    num_samples: int = 0
    duration_s: float = 0.0
    description: str = ""
    reason: str = "manual"  # "manual" | "unknown_signal" | "focus"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class IQRecorder:
    """Streaming IQ recorder. Thread-safe append of complex64 blocks."""

    def __init__(self, out_dir: str):
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._fh = None
        self._meta: Optional[RecordingMeta] = None
        self._samples_written = 0
        self._data_path = ""

    @property
    def is_recording(self) -> bool:
        return self._fh is not None

    def start(self, center_freq_hz: float, sample_rate_hz: float,
              name: Optional[str] = None, reason: str = "manual",
              description: str = "") -> RecordingMeta:
        """Begin a new recording. Returns the :class:`RecordingMeta`."""
        with self._lock:
            if self._fh is not None:
                raise RuntimeError("A recording is already in progress")
            ts = time.time()
            base = name or f"rec_{int(ts)}_{int(center_freq_hz)}"
            self._data_path = os.path.join(self.out_dir, base + ".sigmf-data")
            self._fh = open(self._data_path, "wb")
            self._samples_written = 0
            self._meta = RecordingMeta(
                path=self._data_path,
                center_freq_hz=center_freq_hz,
                sample_rate_hz=sample_rate_hz,
                start_time=ts,
                reason=reason,
                description=description,
            )
            return self._meta

    def write(self, iq: np.ndarray) -> None:
        """Append a complex64 IQ block to the current recording."""
        with self._lock:
            if self._fh is None:
                return
            iq = np.asarray(iq, dtype=np.complex64)
            self._fh.write(iq.tobytes())
            self._samples_written += iq.size

    def stop(self) -> Optional[RecordingMeta]:
        """Finish the recording and write the SigMF metadata sidecar."""
        with self._lock:
            if self._fh is None or self._meta is None:
                return None
            self._fh.close()
            self._fh = None
            self._meta.num_samples = self._samples_written
            self._meta.duration_s = (self._samples_written
                                     / max(1.0, self._meta.sample_rate_hz))
            self._write_meta(self._meta)
            meta = self._meta
            self._meta = None
            return meta

    def _write_meta(self, meta: RecordingMeta) -> None:
        meta_path = meta.path.replace(".sigmf-data", ".sigmf-meta")
        if HAVE_SIGMF:  # pragma: no cover - depends on optional dep
            try:
                smf = SigMFFile(
                    data_file=meta.path,
                    global_info={
                        SigMFFile.DATATYPE_KEY: "cf32_le",
                        SigMFFile.SAMPLE_RATE_KEY: meta.sample_rate_hz,
                        SigMFFile.DESCRIPTION_KEY: meta.description,
                        SigMFFile.RECORDER_KEY: "SDR Hunter",
                    },
                )
                smf.add_capture(0, metadata={
                    SigMFFile.FREQUENCY_KEY: meta.center_freq_hz,
                    SigMFFile.DATETIME_KEY: time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(meta.start_time)),
                })
                smf.tofile(meta_path)
                return
            except Exception:  # noqa: BLE001
                pass
        # Fallback JSON sidecar (SigMF-compatible layout).
        doc = {
            "global": {
                "core:datatype": "cf32_le",
                "core:sample_rate": meta.sample_rate_hz,
                "core:description": meta.description,
                "core:recorder": "SDR Hunter",
                "sdrhunter:reason": meta.reason,
                "sdrhunter:num_samples": meta.num_samples,
                "sdrhunter:duration_s": meta.duration_s,
            },
            "captures": [{
                "core:sample_start": 0,
                "core:frequency": meta.center_freq_hz,
                "core:datetime": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(meta.start_time)),
            }],
            "annotations": [],
        }
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)


def load_recording(data_path: str) -> np.ndarray:
    """Load a complex64 IQ recording from a ``.sigmf-data`` file."""
    return np.fromfile(data_path, dtype=np.complex64)


def list_recordings(out_dir: str) -> List[Dict[str, Any]]:
    """List recordings in a directory using their SigMF metadata sidecars."""
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(out_dir):
        return out
    for fname in sorted(os.listdir(out_dir)):
        if not fname.endswith(".sigmf-meta"):
            continue
        try:
            with open(os.path.join(out_dir, fname), "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            g = doc.get("global", {})
            caps = doc.get("captures", [{}])
            out.append({
                "name": fname.replace(".sigmf-meta", ""),
                "data_path": os.path.join(out_dir,
                                          fname.replace(".sigmf-meta",
                                                        ".sigmf-data")),
                "sample_rate": g.get("core:sample_rate"),
                "frequency": caps[0].get("core:frequency"),
                "datetime": caps[0].get("core:datetime"),
                "reason": g.get("sdrhunter:reason", "manual"),
                "duration_s": g.get("sdrhunter:duration_s"),
            })
        except (OSError, json.JSONDecodeError):
            continue
    return out
