"""Bridge between SDR Hunter events and ATAK/CoT.

Converts tracked drones and signal detections into CoT events and dispatches
them through a :class:`CoTSender`. Can be attached to an :class:`AppState` as a
subscriber so that live drone/signal events are automatically forwarded to
ATAK.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .cot_protocol import (CoTSender, DroneCoTEvent, SignalCoTEvent, new_uid,
                           DEFAULT_MULTICAST_GROUP, DEFAULT_MULTICAST_PORT)

logger = logging.getLogger(__name__)


class ATAKBridge:
    """Forward drone tracks and signal events to ATAK as CoT."""

    def __init__(self, sender: Optional[CoTSender] = None,
                 sensor_lat: float = 0.0, sensor_lon: float = 0.0,
                 enabled: bool = True):
        self.sender = sender or CoTSender()
        self.sensor_lat = sensor_lat
        self.sensor_lon = sensor_lon
        self.enabled = enabled

    @classmethod
    def from_settings(cls, atak_settings: Any, sensor_lat: float = 0.0,
                      sensor_lon: float = 0.0) -> "ATAKBridge":
        sender = CoTSender(
            multicast_group=getattr(atak_settings, "multicast_group",
                                    DEFAULT_MULTICAST_GROUP),
            multicast_port=getattr(atak_settings, "multicast_port",
                                   DEFAULT_MULTICAST_PORT),
            unicast_host=getattr(atak_settings, "unicast_host", ""),
            unicast_port=getattr(atak_settings, "unicast_port", 4242),
            use_multicast=getattr(atak_settings, "use_multicast", True),
        )
        return cls(sender=sender, sensor_lat=sensor_lat, sensor_lon=sensor_lon,
                   enabled=getattr(atak_settings, "enabled", False))

    # ------------------------------------------------------------------
    def send_drone(self, drone: Dict[str, Any]) -> Optional[str]:
        """Send a drone track (dict from :meth:`TrackedDrone.to_dict`)."""
        if not self.enabled:
            return None
        lat = drone.get("lat")
        lon = drone.get("lon")
        # Fall back to operator location, else sensor location.
        if lat is None or lon is None:
            lat = drone.get("operator_lat") or self.sensor_lat
            lon = drone.get("operator_lon") or self.sensor_lon
        if lat is None or lon is None:
            return None
        ev = DroneCoTEvent(
            uid=drone.get("uid") or new_uid("DRONE"),
            lat=float(lat), lon=float(lon),
            alt=float(drone.get("alt_m") or 0.0),
            callsign=drone.get("callsign") or drone.get("uas_id") or "DRONE",
            drone_id=drone.get("uas_id", ""),
        )
        xml = ev.to_xml()
        try:
            self.sender.send(xml)
            return ev.uid
        except OSError as exc:
            logger.warning("Failed to send drone CoT: %s", exc)
            return None

    def send_signal(self, signal: Dict[str, Any]) -> Optional[str]:
        """Send an RF signal detection at the sensor location."""
        if not self.enabled:
            return None
        ev = SignalCoTEvent(
            uid=new_uid("SIG"),
            lat=self.sensor_lat, lon=self.sensor_lon,
            freq_hz=float(signal.get("freq_hz", 0.0)),
            power_db=float(signal.get("power_db", 0.0)),
            modulation=signal.get("modulation_hint", ""),
            label=(signal.get("signal_db_match") or {}).get("name", "RF Signal")
            if signal.get("signal_db_match") else "RF Signal",
        )
        xml = ev.to_xml()
        try:
            self.sender.send(xml)
            return ev.uid
        except OSError as exc:
            logger.warning("Failed to send signal CoT: %s", exc)
            return None

    # ------------------------------------------------------------------
    def appstate_subscriber(self):
        """Return a callback for :meth:`AppState.subscribe`.

        Drones are forwarded as CoT; unknown/suspected signals optionally too.
        """
        def _cb(kind: str, payload: Any) -> None:
            if not self.enabled:
                return
            if kind == "drone":
                self.send_drone(payload)
        return _cb

    def close(self) -> None:
        self.sender.close()
