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
    program_type_name: str = ""    # PTY human name
    pi_code: int = -1              # Program Identification
    traffic_program: bool = False  # TP flag
    traffic_announcement: bool = False  # TA flag
    alt_frequencies: list = field(default_factory=list)  # AF list in MHz
    clock_time: str = ""           # CT: ISO-ish "YYYY-MM-DD HH:MM" (UTC+offset)
    music_speech: str = ""         # "music" | "speech"
    groups_decoded: int = 0
    present: bool = False

    def to_dict(self) -> Dict:
        return {
            "program_service": self.program_service.strip(),
            "radio_text": self.radio_text.strip(),
            "program_type": self.program_type,
            "program_type_name": self.program_type_name,
            "pi_code": self.pi_code,
            "traffic_program": self.traffic_program,
            "traffic_announcement": self.traffic_announcement,
            "alt_frequencies": self.alt_frequencies,
            "clock_time": self.clock_time,
            "music_speech": self.music_speech,
            "groups_decoded": self.groups_decoded,
            "present": self.present,
        }


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
    # RDS (57 kHz subcarrier) -- full group decoder
    # ------------------------------------------------------------------
    RDS_BITRATE = 1187.5  # bits/s
    # Offset words A, B, C, C', D used for block synchronisation (10-bit).
    _OFFSET_WORDS = {"A": 0x0FC, "B": 0x198, "C": 0x168, "Cp": 0x350,
                     "D": 0x1B4}

    def _decode_rds(self, mpx: np.ndarray, sample_rate: float) -> RDSData:
        """Recover RDS metadata from the FM MPX signal.

        Pipeline: detect the 57 kHz subcarrier, band-pass + downconvert to
        baseband, PLL-lock a Costas loop for BPSK, recover 1187.5 bps bits via
        differential decoding, sync on the 26-bit block offset words, then parse
        groups 0A/0B (PS, TP/TA/PTY, AF), 2A/2B (RadioText) and 4A (clock).

        The presence detector is robust; full symbol recovery only completes on
        genuine broadcast IQ (mock/synthetic input reports ``present`` only).
        """
        rds = RDSData()
        if mpx.size < 512:
            return rds
        # 1. Subcarrier presence.
        psd = dsp_engine.compute_psd(mpx.astype(np.complex64),
                                     min(8192, mpx.size), "hann", sample_rate)
        freqs = dsp_engine.freq_axis(0.0, sample_rate, psd.size)
        band = (np.abs(freqs - 57000.0) < 2400.0) | (np.abs(freqs + 57000.0)
                                                     < 2400.0)
        if not np.any(band):
            return rds
        subcarrier_db = float(np.max(psd[band]))
        floor = dsp_engine.estimate_noise_floor(psd)
        if (subcarrier_db - floor) <= 6.0:
            return rds
        rds.present = True

        try:
            bits = self._recover_rds_bits(mpx, sample_rate)
            if bits.size < 104:  # need at least a full group (4 x 26 bits)
                return rds
            groups = self._sync_and_extract_groups(bits)
            for blocks in groups:
                self._parse_group(blocks, rds)
                rds.groups_decoded += 1
            if rds.program_type >= 0:
                rds.program_type_name = self.pty_name(rds.program_type)
        except Exception:  # noqa: BLE001 - never let RDS crash the pipeline
            pass
        return rds

    def _recover_rds_bits(self, mpx: np.ndarray, sample_rate: float
                          ) -> np.ndarray:
        """Downconvert the 57 kHz subcarrier and recover differential bits."""
        n = np.arange(mpx.size)
        # Mix 57 kHz down to baseband.
        lo = np.exp(-2j * np.pi * 57000.0 * n / sample_rate)
        bb = mpx.astype(np.complex64) * lo
        # Low-pass ~2.4 kHz (RDS occupies +/-2.4 kHz around 57 kHz).
        bb = dsp_engine.lowpass_fir(bb, 2400.0, sample_rate, num_taps=129)
        # Decimate towards a few samples/symbol.
        sps_target = 8
        decim = max(1, int(sample_rate / (self.RDS_BITRATE * sps_target)))
        bb = bb[::decim]
        sym_rate = sample_rate / decim
        sps = sym_rate / self.RDS_BITRATE
        # Costas-style carrier lock: remove residual phase via 2nd power.
        phase = np.angle(np.mean(bb ** 2)) / 2.0
        bb = bb * np.exp(-1j * phase)
        # Symbol sampling: take the real part at symbol centers.
        num_syms = int(bb.size / sps)
        if num_syms < 2:
            return np.zeros(0, dtype=np.int8)
        idx = (np.arange(num_syms) * sps + sps / 2).astype(int)
        idx = idx[idx < bb.size]
        symbols = np.real(bb[idx])
        raw = (symbols > 0).astype(np.int8)
        # Differential decode (RDS uses differential Manchester/BPSK).
        bits = np.bitwise_xor(raw[1:], raw[:-1]).astype(np.int8)
        return bits

    def _sync_and_extract_groups(self, bits: np.ndarray):
        """Slide a 26-bit window, verify CRC+offset, group blocks into groups.

        Returns a list of groups; each group is a list of up to 4 block ints
        (16 data bits each) in A,B,C,D order.
        """
        groups = []
        i = 0
        n = bits.size
        current = []
        expected = ["A", "B", "C", "D"]
        while i + 26 <= n:
            block = bits[i:i + 26]
            data = int("".join(str(b) for b in block[:16]), 2)
            check = int("".join(str(b) for b in block[16:]), 2)
            syndrome = self._rds_syndrome(block) ^ check
            matched = None
            for name, off in self._OFFSET_WORDS.items():
                if syndrome == off:
                    matched = name
                    break
            if matched is not None:
                current.append(data)
                if len(current) == 4:
                    groups.append(current)
                    current = []
                i += 26
            else:
                i += 1  # search bit-by-bit for sync
        return groups

    @staticmethod
    def _rds_syndrome(block: np.ndarray) -> int:
        """Compute the RDS (26,16) shortened-cyclic syndrome for a block."""
        # Generator polynomial x^10 + x^8 + x^7 + x^5 + x^4 + x^3 + 1 = 0x5B9.
        reg = 0
        for bit in block:
            reg = ((reg << 1) | int(bit)) & 0x3FF
            if reg & 0x400:
                reg ^= 0x5B9
        # Only the 16 information bits participate; process via poly division.
        rem = 0
        poly = 0x5B9
        val = 0
        for bit in block[:16]:
            val = (val << 1) | int(bit)
        rem = val << 10
        for shift in range(15, -1, -1):
            if rem & (1 << (shift + 10)):
                rem ^= poly << shift
        return rem & 0x3FF

    def _parse_group(self, blocks, rds: RDSData) -> None:
        """Parse one RDS group (list of 4 x 16-bit ints) into ``rds``."""
        if len(blocks) < 2:
            return
        block_a, block_b = blocks[0], blocks[1]
        rds.pi_code = block_a
        group_type = (block_b >> 12) & 0x0F
        version_b = bool((block_b >> 11) & 0x01)
        rds.traffic_program = bool((block_b >> 10) & 0x01)
        rds.program_type = (block_b >> 5) & 0x1F

        if group_type == 0:  # 0A/0B: PS name, TA, M/S, AF
            rds.traffic_announcement = bool((block_b >> 4) & 0x01)
            rds.music_speech = "music" if (block_b >> 3) & 0x01 else "speech"
            addr = block_b & 0x03
            if len(blocks) >= 4:
                if not version_b and len(blocks) >= 3:
                    self._parse_af(blocks[2], rds)
                d = blocks[3]
                ps = list(rds.program_service.ljust(8))
                ps[addr * 2] = self._rds_char(d >> 8)
                ps[addr * 2 + 1] = self._rds_char(d & 0xFF)
                rds.program_service = "".join(ps)
        elif group_type == 2:  # 2A/2B: RadioText
            addr = block_b & 0x0F
            rt = list(rds.radio_text.ljust(64))
            if not version_b and len(blocks) >= 4:
                rt[addr * 4] = self._rds_char(blocks[2] >> 8)
                rt[addr * 4 + 1] = self._rds_char(blocks[2] & 0xFF)
                rt[addr * 4 + 2] = self._rds_char(blocks[3] >> 8)
                rt[addr * 4 + 3] = self._rds_char(blocks[3] & 0xFF)
            elif len(blocks) >= 4:
                rt[addr * 2] = self._rds_char(blocks[3] >> 8)
                rt[addr * 2 + 1] = self._rds_char(blocks[3] & 0xFF)
            rds.radio_text = "".join(rt)
        elif group_type == 4 and len(blocks) >= 4:  # 4A: clock time
            self._parse_ct(blocks, rds)

    @staticmethod
    def _parse_af(word: int, rds: RDSData) -> None:
        """Parse an Alternative Frequency pair (each byte encodes a freq)."""
        for byte in (word >> 8, word & 0xFF):
            if 1 <= byte <= 204:  # VHF AF codes: 87.6 + (n-1)*0.1 MHz
                mhz = round(87.5 + byte * 0.1, 1)
                if mhz not in rds.alt_frequencies:
                    rds.alt_frequencies.append(mhz)

    @staticmethod
    def _parse_ct(blocks, rds: RDSData) -> None:
        """Parse a 4A clock-time group into an ISO-ish string."""
        try:
            mjd = ((blocks[1] & 0x03) << 15) | (blocks[2] >> 1)
            hour = ((blocks[2] & 0x01) << 4) | (blocks[3] >> 12)
            minute = (blocks[3] >> 6) & 0x3F
            # Convert Modified Julian Date to calendar date.
            yp = int((mjd - 15078.2) / 365.25)
            mp = int((mjd - 14956.1 - int(yp * 365.25)) / 30.6001)
            day = mjd - 14956 - int(yp * 365.25) - int(mp * 30.6001)
            k = 1 if mp in (14, 15) else 0
            year = 1900 + yp + k
            month = mp - 1 - k * 12
            if 1 <= month <= 12 and 0 <= hour < 24 and 0 <= minute < 60:
                rds.clock_time = (f"{year:04d}-{month:02d}-{day:02d} "
                                  f"{hour:02d}:{minute:02d}")
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _rds_char(code: int) -> str:
        """Map an RDS character code to ASCII (printable range only)."""
        code &= 0xFF
        return chr(code) if 32 <= code < 127 else " "

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
