"""Heuristic detection of drone control/video links by frequency + modulation.

When Remote ID decoding is not available (analog links, non-broadcasting drones,
or protocol decode failure), this detector flags *suspected* drone activity by
matching detected signals against the known drone-frequency database and by
recognizing characteristic spectral patterns (frequency-hopping bursts in
2.4/5.8 GHz, wideband analog FPV video, LoRa control chirps).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.signal_detector import SignalEvent


@dataclass
class DroneSuspicion:
    """A suspected drone signal with a confidence score and rationale."""

    freq_hz: float
    bandwidth_hz: float
    power_db: float
    confidence: float
    role: str            # "control" | "video" | "remote_id" | "unknown"
    match_name: str = ""
    manufacturer: str = ""
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "freq_hz": self.freq_hz, "bandwidth_hz": self.bandwidth_hz,
            "power_db": self.power_db, "confidence": self.confidence,
            "role": self.role, "match_name": self.match_name,
            "manufacturer": self.manufacturer, "rationale": self.rationale,
        }


class DroneSignalDetector:
    """Flag suspected drone signals using the drone frequency database."""

    def __init__(self, drone_freqs: Optional[Dict[str, Any]] = None,
                 drone_freqs_path: Optional[str] = None):
        if drone_freqs is None and drone_freqs_path:
            drone_freqs = self._load(drone_freqs_path)
        self.db = drone_freqs or {"control_links": [], "video_links": [],
                                  "remote_id": []}
        # Flatten into a single searchable list with role tags.
        self._entries: List[Dict[str, Any]] = []
        for role, key in (("control", "control_links"),
                          ("video", "video_links"),
                          ("remote_id", "remote_id")):
            for e in self.db.get(key, []):
                item = dict(e)
                item["_role"] = role
                self._entries.append(item)

    @staticmethod
    def _load(path: str) -> Dict[str, Any]:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return {}

    # ------------------------------------------------------------------
    def evaluate(self, event: SignalEvent) -> Optional[DroneSuspicion]:
        """Evaluate a detected signal for drone characteristics."""
        best: Optional[Dict[str, Any]] = None
        for entry in self._entries:
            lo = float(entry.get("freq_start_hz", 0))
            hi = float(entry.get("freq_end_hz", lo))
            if lo <= event.freq_hz <= hi:
                if best is None or self._bw_close(event, entry) > self._bw_close(
                        event, best):
                    best = entry
        if best is None:
            return self._spectral_only(event)

        confidence = 0.5
        rationale = [f"freq in {best.get('name', 'drone band')}"]
        # Bandwidth agreement boosts confidence.
        typical_bw = float(best.get("typical_bw_hz", 0))
        if typical_bw and event.bandwidth_hz > 0:
            ratio = event.bandwidth_hz / typical_bw
            if 0.4 <= ratio <= 2.5:
                confidence += 0.3
                rationale.append("bandwidth matches typical link")
        if event.snr_db > 15:
            confidence += 0.1
            rationale.append("strong signal")
        return DroneSuspicion(
            freq_hz=event.freq_hz, bandwidth_hz=event.bandwidth_hz,
            power_db=event.power_db, confidence=min(1.0, confidence),
            role=best["_role"], match_name=best.get("name", ""),
            manufacturer=best.get("manufacturer", ""),
            rationale="; ".join(rationale),
        )

    @staticmethod
    def _bw_close(event: SignalEvent, entry: Dict[str, Any]) -> float:
        typical = float(entry.get("typical_bw_hz", 0))
        if not typical or event.bandwidth_hz <= 0:
            return 0.0
        return 1.0 / (1.0 + abs(event.bandwidth_hz - typical) / typical)

    @staticmethod
    def _spectral_only(event: SignalEvent) -> Optional[DroneSuspicion]:
        """Flag suspicious wideband bursts in ISM bands without a DB match."""
        f = event.freq_hz
        in_ism = (2.40e9 <= f <= 2.4835e9) or (5.65e9 <= f <= 5.95e9) \
            or (902e6 <= f <= 928e6)
        if in_ism and event.bandwidth_hz >= 5e6:
            return DroneSuspicion(
                freq_hz=f, bandwidth_hz=event.bandwidth_hz,
                power_db=event.power_db, confidence=0.35, role="unknown",
                rationale="wideband burst in ISM band (possible FPV/OcuSync)",
            )
        return None

    def scan_events(self, events: List[SignalEvent]) -> List[DroneSuspicion]:
        """Evaluate a batch of signal events, returning all suspicions."""
        out: List[DroneSuspicion] = []
        for ev in events:
            s = self.evaluate(ev)
            if s is not None:
                out.append(s)
        return out
