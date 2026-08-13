"""Cursor-on-Target (CoT) protocol support for ATAK integration.

Builds CoT XML event messages for drone positions and RF signal detections and
sends them over UDP multicast (the ATAK SA multicast group 239.2.3.1:6969) or
TCP unicast to a TAK server. This provides the foundation for a future ATAK
plugin: drone tracks and signal events become CoT events on the tactical map.
"""
from __future__ import annotations

import socket
import time
import uuid
from dataclasses import dataclass
from typing import Optional
from xml.sax.saxutils import escape

# Standard ATAK SA multicast group / port.
DEFAULT_MULTICAST_GROUP = "239.2.3.1"
DEFAULT_MULTICAST_PORT = 6969


def _iso(ts: Optional[float] = None) -> str:
    ts = ts if ts is not None else time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))


def _cot_envelope(uid: str, cot_type: str, lat: float, lon: float,
                  hae: float = 0.0, how: str = "m-g",
                  stale_seconds: int = 120, detail_xml: str = "") -> str:
    """Build a complete CoT ``<event>`` XML string."""
    now = time.time()
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<event version="2.0" uid="{escape(uid)}" type="{escape(cot_type)}" '
        f'how="{escape(how)}" time="{_iso(now)}" start="{_iso(now)}" '
        f'stale="{_iso(now + stale_seconds)}">'
        f'<point lat="{lat:.7f}" lon="{lon:.7f}" hae="{hae:.1f}" '
        'ce="9999999.0" le="9999999.0"/>'
        f'<detail>{detail_xml}</detail>'
        '</event>'
    )


@dataclass
class DroneCoTEvent:
    """A CoT event representing a drone position."""

    uid: str
    lat: float
    lon: float
    alt: float = 0.0
    callsign: str = "DRONE"
    drone_id: str = ""
    speed_mps: float = 0.0
    course_deg: float = 0.0
    # a-f-A-M-F-Q = friendly airborne UAV; a-h-... would be hostile. Use neutral
    # "a-u-A" (unknown airborne) by default for detected/suspected drones.
    cot_type: str = "a-u-A-M-H-Q"

    def to_xml(self, stale_seconds: int = 120) -> str:
        contact = f'<contact callsign="{escape(self.callsign)}"/>'
        track = (f'<track speed="{self.speed_mps:.1f}" '
                 f'course="{self.course_deg:.1f}"/>')
        remarks = ("<remarks>SDR Hunter drone track"
                   + (f" | ID: {escape(self.drone_id)}" if self.drone_id else "")
                   + "</remarks>")
        detail = contact + track + remarks + '<__group name="Magenta"/>'
        return _cot_envelope(self.uid, self.cot_type, self.lat, self.lon,
                             self.alt, how="m-g", stale_seconds=stale_seconds,
                             detail_xml=detail)


@dataclass
class SignalCoTEvent:
    """A CoT event representing an RF signal detection at a sensor location."""

    uid: str
    lat: float
    lon: float
    freq_hz: float
    power_db: float = 0.0
    modulation: str = ""
    label: str = "RF Signal"
    cot_type: str = "b-m-p-s-m"  # sensor point of interest / spot marker

    def to_xml(self, stale_seconds: int = 300) -> str:
        callsign = f"{self.label} {self.freq_hz/1e6:.3f}MHz"
        contact = f'<contact callsign="{escape(callsign)}"/>'
        remarks = (f"<remarks>Freq {self.freq_hz/1e6:.4f} MHz | "
                   f"{self.power_db:.1f} dB | {escape(self.modulation)}"
                   "</remarks>")
        detail = contact + remarks
        return _cot_envelope(self.uid, self.cot_type, self.lat, self.lon,
                             0.0, how="m-r", stale_seconds=stale_seconds,
                             detail_xml=detail)


class CoTSender:
    """Send CoT XML over UDP multicast or TCP unicast."""

    def __init__(self, multicast_group: str = DEFAULT_MULTICAST_GROUP,
                 multicast_port: int = DEFAULT_MULTICAST_PORT,
                 unicast_host: str = "", unicast_port: int = 4242,
                 use_multicast: bool = True, ttl: int = 1):
        self.multicast_group = multicast_group
        self.multicast_port = multicast_port
        self.unicast_host = unicast_host
        self.unicast_port = unicast_port
        self.use_multicast = use_multicast
        self.ttl = ttl
        self._udp: Optional[socket.socket] = None
        self._tcp: Optional[socket.socket] = None

    # -- UDP multicast -----------------------------------------------------
    def _ensure_udp(self) -> socket.socket:
        if self._udp is None:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                              socket.IPPROTO_UDP)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, self.ttl)
            self._udp = s
        return self._udp

    def send_multicast(self, xml: str) -> None:
        s = self._ensure_udp()
        s.sendto(xml.encode("utf-8"),
                 (self.multicast_group, self.multicast_port))

    # -- TCP unicast -------------------------------------------------------
    def _ensure_tcp(self) -> socket.socket:
        if self._tcp is None:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5.0)
            s.connect((self.unicast_host, self.unicast_port))
            self._tcp = s
        return self._tcp

    def send_unicast(self, xml: str) -> None:
        s = self._ensure_tcp()
        s.sendall(xml.encode("utf-8"))

    def send(self, xml: str) -> None:
        """Send using the configured transport (multicast or unicast)."""
        if self.use_multicast:
            self.send_multicast(xml)
        elif self.unicast_host:
            self.send_unicast(xml)

    def close(self) -> None:
        for sock in (self._udp, self._tcp):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self._udp = None
        self._tcp = None


def new_uid(prefix: str = "SDRHUNTER") -> str:
    """Generate a unique CoT UID."""
    return f"{prefix}-{uuid.uuid4()}"
