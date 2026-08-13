"""METEOR-M2 LRPT decoder skeleton.

METEOR-M N2 satellites transmit LRPT (Low Rate Picture Transmission) around
137.1 MHz using QPSK at 72 kSym/s, with convolutional coding (r=1/2, k=7),
CCSDS framing, and JPEG-like (DCT) image compression.

This module implements the front-end of the chain (QPSK carrier/symbol recovery
scaffolding and soft-symbol extraction) and defines the data structures for the
downstream Viterbi + CCSDS + image stages, which are staged for a later phase.
It is importable and runs end-to-end on IQ input, producing soft symbols.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from core import dsp_engine

LRPT_SYMBOL_RATE = 72000.0
LRPT_CARRIER_DEFAULT = 137.1e6


@dataclass
class LRPTSymbols:
    """Recovered soft QPSK symbols (complex, normalized)."""

    symbols: np.ndarray
    symbol_rate: float
    num_symbols: int


@dataclass
class LRPTResult:
    """Result of an LRPT decode attempt."""

    symbols: Optional[LRPTSymbols] = None
    frames_decoded: int = 0
    image_available: bool = False
    note: str = ""


class MeteorM2Decoder:
    """METEOR-M2 LRPT QPSK front-end decoder."""

    def __init__(self, symbol_rate: float = LRPT_SYMBOL_RATE):
        self.symbol_rate = symbol_rate

    def decode_iq(self, iq: np.ndarray, sample_rate: float) -> LRPTResult:
        """Run the LRPT front-end on baseband IQ and recover soft symbols."""
        if iq.size < 64:
            return LRPTResult(note="insufficient samples")
        # AGC normalize.
        iq = self._agc(iq)
        # Coarse carrier recovery via 4th-power FFT (QPSK).
        iq = self._coarse_carrier_recovery(iq, sample_rate)
        # Matched filter (root-raised-cosine approximated by low-pass).
        iq = dsp_engine.lowpass_fir(iq, self.symbol_rate * 0.75, sample_rate)
        # Symbol timing: decimate to ~2 samples/symbol then pick symbols.
        symbols = self._symbol_sync(iq, sample_rate)
        result = LRPTResult(
            symbols=LRPTSymbols(symbols, self.symbol_rate, symbols.size),
            note=("Soft QPSK symbols recovered. Viterbi (r=1/2,k=7) + CCSDS "
                  "deframing + DCT image reconstruction staged for next phase."),
        )
        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _agc(iq: np.ndarray) -> np.ndarray:
        rms = np.sqrt(np.mean(np.abs(iq) ** 2)) + 1e-12
        return (iq / rms).astype(np.complex64)

    def _coarse_carrier_recovery(self, iq: np.ndarray,
                                 sample_rate: float) -> np.ndarray:
        """Estimate and remove the residual carrier via the 4th-power method."""
        p4 = iq ** 4
        n = min(65536, p4.size)
        spec = np.fft.fftshift(np.fft.fft(p4[:n]))
        freqs = dsp_engine.freq_axis(0.0, sample_rate, spec.size)
        peak = freqs[int(np.argmax(np.abs(spec)))]
        f_off = peak / 4.0
        return dsp_engine.frequency_shift(iq, f_off, sample_rate)

    def _symbol_sync(self, iq: np.ndarray, sample_rate: float) -> np.ndarray:
        """Very simple symbol synchronizer: resample to 1 sample/symbol.

        Uses fixed-rate resampling (Gardner TED refinement staged for later);
        adequate to expose the constellation for the next stage.
        """
        sps_target = 1
        n_out = int(iq.size * (self.symbol_rate * sps_target) / sample_rate)
        if n_out <= 0:
            return np.zeros(0, dtype=np.complex64)
        idx = np.linspace(0, iq.size - 1, n_out)
        re = np.interp(idx, np.arange(iq.size), iq.real)
        im = np.interp(idx, np.arange(iq.size), iq.imag)
        return (re + 1j * im).astype(np.complex64)

    @staticmethod
    def constellation_metrics(symbols: np.ndarray) -> dict:
        """Return simple QPSK constellation quality metrics."""
        if symbols.size == 0:
            return {"evm": None, "num": 0}
        # Nearest ideal QPSK point (+-1 +-1)/sqrt2.
        ideal = np.sign(symbols.real) + 1j * np.sign(symbols.imag)
        ideal /= np.sqrt(2)
        err = symbols / (np.abs(symbols) + 1e-12) - ideal
        evm = float(np.sqrt(np.mean(np.abs(err) ** 2)))
        return {"evm": evm, "num": int(symbols.size)}
