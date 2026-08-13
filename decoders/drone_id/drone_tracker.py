"""Track drone positions over time and maintain an active-drone list.

Aggregates Remote ID frames, decoded OpenDroneID location messages, suspected
drone signals, and manual map operator identifications into a unified list of
tracked drones with position history. Supports manual pinning of a drone on the
map when automatic ID fails but the operator can see it.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .remote_id import RemoteIDFrame
from .drone_signal_detector import DroneSuspicion


@dataclass
class TrackPoint:
    """A single position sample in a drone track."""

    timestamp: float
    lat: Optional[float] = None
    lon: Optional[float] = None
    alt_m: Optional[float] = None
    speed_mps: Optional[float] = None
    source: str = "remote_id"  # remote_id | manual | rf_estimate


@dataclass
class TrackedDrone:
    """A tracked drone with identity and position history."""

    uid: str
    uas_id: str = ""
    callsign: str = ""
    manufacturer: str = ""
    source: str = "remote_id"  # remote_id | rf_suspected | manual
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    history: List[TrackPoint] = field(default_factory=list)
    operator_lat: Optional[float] = None
    operator_lon: Optional[float] = None
    freq_hz: Optional[float] = None
    confidence: float = 1.0
    id_failed: bool = False  # detected but protocol decode failed

    @property
    def last_position(self) -> Optional[TrackPoint]:
        return self.history[-1] if self.history else None

    def to_dict(self) -> Dict[str, Any]:
        lp = self.last_position
        return {
            "uid": self.uid, "uas_id": self.uas_id, "callsign": self.callsign,
            "manufacturer": self.manufacturer, "source": self.source,
            "first_seen": self.first_seen, "last_seen": self.last_seen,
            "operator_lat": self.operator_lat, "operator_lon": self.operator_lon,
            "freq_hz": self.freq_hz, "confidence": self.confidence,
            "id_failed": self.id_failed,
            "lat": lp.lat if lp else None,
            "lon": lp.lon if lp else None,
            "alt_m": lp.alt_m if lp else None,
            "num_points": len(self.history),
        }


class DroneTracker:
    """Maintain a set of tracked drones keyed by UAS ID or synthetic UID."""

    def __init__(self, stale_after_s: float = 120.0):
        self.stale_after_s = stale_after_s
        self._drones: Dict[str, TrackedDrone] = {}

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------
    def update_from_remote_id(self, frame: RemoteIDFrame) -> TrackedDrone:
        """Update tracker state from a decoded Remote ID frame."""
        uas_id = frame.uas_id or frame.mac_address or f"rid-{uuid.uuid4().hex[:8]}"
        drone = self._drones.get(uas_id)
        if drone is None:
            drone = TrackedDrone(uid=uas_id, uas_id=frame.uas_id,
                                 source="remote_id")
            self._drones[uas_id] = drone
        drone.last_seen = time.time()
        loc = frame.location()
        if loc is not None:
            drone.history.append(TrackPoint(
                timestamp=time.time(), lat=loc.latitude, lon=loc.longitude,
                alt_m=loc.altitude_geo_m, speed_mps=loc.speed_mps,
                source="remote_id"))
        for m in frame.messages:
            if m.system is not None:
                drone.operator_lat = m.system.operator_latitude
                drone.operator_lon = m.system.operator_longitude
            if m.operator_id:
                drone.callsign = m.operator_id
        return drone

    def update_from_suspicion(self, suspicion: DroneSuspicion) -> TrackedDrone:
        """Create/update an RF-suspected drone (no decoded position)."""
        key = f"rf-{int(suspicion.freq_hz)}"
        drone = self._drones.get(key)
        if drone is None:
            drone = TrackedDrone(
                uid=key, source="rf_suspected",
                manufacturer=suspicion.manufacturer,
                freq_hz=suspicion.freq_hz, confidence=suspicion.confidence,
                id_failed=True)
            self._drones[key] = drone
        drone.last_seen = time.time()
        drone.confidence = max(drone.confidence, suspicion.confidence)
        return drone

    def add_manual(self, lat: float, lon: float, callsign: str = "",
                   alt_m: Optional[float] = None,
                   freq_hz: Optional[float] = None) -> TrackedDrone:
        """Manually pin a drone on the map (visual ID when decode fails)."""
        uid = f"manual-{uuid.uuid4().hex[:8]}"
        drone = TrackedDrone(uid=uid, callsign=callsign or "Manual Contact",
                             source="manual", freq_hz=freq_hz,
                             id_failed=True, confidence=1.0)
        drone.history.append(TrackPoint(timestamp=time.time(), lat=lat, lon=lon,
                                        alt_m=alt_m, source="manual"))
        self._drones[uid] = drone
        return drone

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------
    def active_drones(self, include_stale: bool = False) -> List[TrackedDrone]:
        now = time.time()
        out = []
        for d in self._drones.values():
            if include_stale or (now - d.last_seen) <= self.stale_after_s:
                out.append(d)
        return sorted(out, key=lambda d: d.last_seen, reverse=True)

    def get(self, uid: str) -> Optional[TrackedDrone]:
        return self._drones.get(uid)

    def prune(self) -> int:
        """Remove drones not seen within ``stale_after_s``. Returns count removed."""
        now = time.time()
        stale = [k for k, d in self._drones.items()
                 if (now - d.last_seen) > self.stale_after_s]
        for k in stale:
            del self._drones[k]
        return len(stale)

    def clear(self) -> None:
        self._drones.clear()

    def to_geojson(self) -> Dict[str, Any]:
        """Export active drone positions as a GeoJSON FeatureCollection."""
        features = []
        for d in self.active_drones():
            lp = d.last_position
            if not lp or lp.lat is None or lp.lon is None:
                continue
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lp.lon, lp.lat]},
                "properties": d.to_dict(),
            })
        return {"type": "FeatureCollection", "features": features}
