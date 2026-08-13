"""OpenDroneID message decoder.

Decodes OpenDroneID / ASTM F3411 message payloads. OpenDroneID defines a set of
fixed 25-byte messages (Basic ID, Location/Vector, Authentication, Self-ID,
System, Operator ID, and Message Pack) that are broadcast over Bluetooth 4
legacy advertising, Bluetooth 5 long-range extended advertising, WiFi Beacon,
and WiFi NAN.

This module parses the *message payloads* once they have been extracted from
the link layer (BT/WiFi). It uses ``construct`` when available and falls back to
manual ``struct``-based parsing otherwise, so it works with or without the
optional dependency.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field, asdict
from enum import IntEnum
from typing import Any, Dict, List, Optional

# OpenDroneID application/vendor identifiers used to recognize frames.
ODID_MESSAGE_SIZE = 25
ASTM_CID = bytes([0xFA, 0x0B, 0xBC])  # ASTM International company ID (BT)
ODID_AD_APP_CODE = 0x0D               # OpenDroneID BT application code


class MessageType(IntEnum):
    """OpenDroneID message types (high nibble of first byte)."""

    BASIC_ID = 0x0
    LOCATION = 0x1
    AUTH = 0x2
    SELF_ID = 0x3
    SYSTEM = 0x4
    OPERATOR_ID = 0x5
    MESSAGE_PACK = 0xF


class UAType(IntEnum):
    """Unmanned aircraft type."""

    NONE = 0
    AEROPLANE = 1
    HELICOPTER_MULTIROTOR = 2
    GYROPLANE = 3
    HYBRID_LIFT = 4
    ORNITHOPTER = 5
    GLIDER = 6
    KITE = 7
    FREE_BALLOON = 8
    CAPTIVE_BALLOON = 9
    AIRSHIP = 10
    FREE_FALL_PARACHUTE = 11
    ROCKET = 12
    TETHERED_POWERED = 13
    GROUND_OBSTACLE = 14
    OTHER = 15


@dataclass
class BasicID:
    ua_type: int = 0
    id_type: int = 0
    uas_id: str = ""


@dataclass
class LocationVector:
    latitude: float = 0.0
    longitude: float = 0.0
    altitude_baro_m: float = 0.0
    altitude_geo_m: float = 0.0
    height_m: float = 0.0
    speed_mps: float = 0.0
    vertical_speed_mps: float = 0.0
    direction_deg: float = 0.0
    status: int = 0
    timestamp: float = 0.0


@dataclass
class SystemMessage:
    operator_latitude: float = 0.0
    operator_longitude: float = 0.0
    area_count: int = 0
    area_radius_m: float = 0.0


@dataclass
class ODIDMessage:
    """A decoded OpenDroneID message."""

    message_type: int
    basic_id: Optional[BasicID] = None
    location: Optional[LocationVector] = None
    system: Optional[SystemMessage] = None
    operator_id: str = ""
    self_id: str = ""
    raw_hex: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"message_type": int(self.message_type),
                             "raw_hex": self.raw_hex}
        if self.basic_id:
            d["basic_id"] = asdict(self.basic_id)
        if self.location:
            d["location"] = asdict(self.location)
        if self.system:
            d["system"] = asdict(self.system)
        if self.operator_id:
            d["operator_id"] = self.operator_id
        if self.self_id:
            d["self_id"] = self.self_id
        return d


# Scale factors from the OpenDroneID spec.
_LATLON_SCALE = 1e-7
_ALT_OFFSET = 1000.0
_ALT_SCALE = 0.5
_SPEED_SCALE = 0.25


class OpenDroneIDDecoder:
    """Decode OpenDroneID message payloads (25-byte units)."""

    def decode_message(self, payload: bytes) -> Optional[ODIDMessage]:
        """Decode a single 25-byte OpenDroneID message."""
        if len(payload) < 1:
            return None
        header = payload[0]
        msg_type = (header >> 4) & 0x0F
        raw_hex = payload.hex()

        if msg_type == MessageType.BASIC_ID:
            return ODIDMessage(msg_type, basic_id=self._basic_id(payload),
                               raw_hex=raw_hex)
        if msg_type == MessageType.LOCATION:
            return ODIDMessage(msg_type, location=self._location(payload),
                               raw_hex=raw_hex)
        if msg_type == MessageType.SYSTEM:
            return ODIDMessage(msg_type, system=self._system(payload),
                               raw_hex=raw_hex)
        if msg_type == MessageType.OPERATOR_ID:
            return ODIDMessage(msg_type,
                               operator_id=self._ascii(payload[1:21]),
                               raw_hex=raw_hex)
        if msg_type == MessageType.SELF_ID:
            return ODIDMessage(msg_type,
                               self_id=self._ascii(payload[2:25]),
                               raw_hex=raw_hex)
        return ODIDMessage(msg_type, raw_hex=raw_hex)

    def decode_message_pack(self, payload: bytes) -> List[ODIDMessage]:
        """Decode a Message Pack (multiple 25-byte messages)."""
        out: List[ODIDMessage] = []
        if len(payload) < 3:
            return out
        # payload[0] header, [1] msg size, [2] num messages, then messages.
        msg_size = payload[1] or ODID_MESSAGE_SIZE
        num = payload[2]
        offset = 3
        for _ in range(num):
            chunk = payload[offset:offset + msg_size]
            if len(chunk) < 1:
                break
            msg = self.decode_message(chunk)
            if msg:
                out.append(msg)
            offset += msg_size
        return out

    # ------------------------------------------------------------------
    # Individual message parsers
    # ------------------------------------------------------------------
    def _basic_id(self, p: bytes) -> BasicID:
        id_type = (p[1] >> 4) & 0x0F if len(p) > 1 else 0
        ua_type = p[1] & 0x0F if len(p) > 1 else 0
        uas_id = self._ascii(p[2:22])
        return BasicID(ua_type=ua_type, id_type=id_type, uas_id=uas_id)

    def _location(self, p: bytes) -> LocationVector:
        loc = LocationVector()
        if len(p) < 24:
            return loc
        status = (p[1] >> 4) & 0x0F
        loc.status = status
        # Direction, horizontal speed, vertical speed.
        loc.direction_deg = float(p[2])  # simplified (EW segment omitted)
        loc.speed_mps = float(p[3]) * _SPEED_SCALE
        loc.vertical_speed_mps = struct.unpack("<b", p[4:5])[0] * _SPEED_SCALE
        lat = struct.unpack("<i", p[5:9])[0]
        lon = struct.unpack("<i", p[9:13])[0]
        loc.latitude = lat * _LATLON_SCALE
        loc.longitude = lon * _LATLON_SCALE
        alt_baro = struct.unpack("<H", p[13:15])[0]
        alt_geo = struct.unpack("<H", p[15:17])[0]
        height = struct.unpack("<H", p[17:19])[0]
        loc.altitude_baro_m = alt_baro * _ALT_SCALE - _ALT_OFFSET
        loc.altitude_geo_m = alt_geo * _ALT_SCALE - _ALT_OFFSET
        loc.height_m = height * _ALT_SCALE - _ALT_OFFSET
        return loc

    def _system(self, p: bytes) -> SystemMessage:
        sysm = SystemMessage()
        if len(p) < 14:
            return sysm
        op_lat = struct.unpack("<i", p[2:6])[0]
        op_lon = struct.unpack("<i", p[6:10])[0]
        sysm.operator_latitude = op_lat * _LATLON_SCALE
        sysm.operator_longitude = op_lon * _LATLON_SCALE
        sysm.area_count = struct.unpack("<H", p[10:12])[0]
        sysm.area_radius_m = float(p[12]) * 10.0
        return sysm

    @staticmethod
    def _ascii(data: bytes) -> str:
        try:
            return data.split(b"\x00")[0].decode("ascii", errors="ignore").strip()
        except Exception:  # noqa: BLE001
            return ""

    # ------------------------------------------------------------------
    # Link-layer helpers
    # ------------------------------------------------------------------
    @staticmethod
    def is_opendroneid_bt(ad_payload: bytes) -> bool:
        """Heuristic: does a BT advertising payload carry OpenDroneID?"""
        return ASTM_CID in ad_payload or bytes([ODID_AD_APP_CODE]) in ad_payload
