"""Audio playback + WAV export helpers.

``sounddevice`` is optional (not always installed / no audio device in
headless environments), so playback degrades gracefully: if the backend is
unavailable, :class:`AudioPlayer` becomes a no-op but WAV export still works.
"""
from __future__ import annotations

import logging
import wave
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

try:  # optional real-time audio output
    import sounddevice as _sd  # type: ignore
    HAVE_SOUNDDEVICE = True
except Exception:  # noqa: BLE001
    _sd = None  # type: ignore
    HAVE_SOUNDDEVICE = False


def _to_int16(audio: np.ndarray) -> np.ndarray:
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0:
        return audio.astype(np.int16)
    peak = float(np.max(np.abs(audio)))
    if peak > 1e-9:
        audio = audio / peak * 0.95
    return (audio * 32767.0).astype(np.int16)


def write_wav(path: str, audio: np.ndarray, sample_rate: float) -> str:
    """Write mono float audio (-1..1) to a 16-bit PCM WAV file."""
    pcm = _to_int16(audio)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(pcm.tobytes())
    return path


class AudioPlayer:
    """Streaming audio sink around ``sounddevice`` with a safe no-op fallback."""

    def __init__(self, sample_rate: float = 48000.0):
        self.sample_rate = sample_rate
        self._stream = None
        self.available = HAVE_SOUNDDEVICE

    def start(self, sample_rate: Optional[float] = None) -> bool:
        if sample_rate:
            self.sample_rate = sample_rate
        if not HAVE_SOUNDDEVICE:
            logger.info("sounddevice unavailable; audio playback disabled")
            return False
        try:
            self._stream = _sd.OutputStream(
                samplerate=int(self.sample_rate), channels=1, dtype="float32")
            self._stream.start()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Audio output unavailable: %s", exc)
            self._stream = None
            return False

    def play_block(self, audio: np.ndarray) -> None:
        if self._stream is None:
            return
        try:
            self._stream.write(np.asarray(audio, dtype=np.float32))
        except Exception:  # noqa: BLE001
            pass

    def play_once(self, audio: np.ndarray,
                  sample_rate: Optional[float] = None) -> None:
        """Blocking one-shot playback (used for short clips)."""
        if not HAVE_SOUNDDEVICE:
            return
        try:
            _sd.play(np.asarray(audio, dtype=np.float32),
                     int(sample_rate or self.sample_rate))
        except Exception:  # noqa: BLE001
            pass

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._stream = None
