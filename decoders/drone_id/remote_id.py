"""ASTM F3411 Remote ID decoder (link-layer framing).

ASTM F3411 / FAA Remote ID broadcasts OpenDroneID message payloads over four
transports:
    * Bluetooth 4 legacy advertising
    * Bluetooth 5 Long Range (extended advertising, coded PHY)
    * WiFi Beacon (vendor-specific IE in beacon frames)
    * WiFi Neighbor Awareness Networking (NAN) service discovery frames

This module extracts the OpenDroneID payload from each transport's framing and
delegates message parsing to :class:`OpenDroneIDDecoder`. It provides a single
:meth:`decode` entry point that auto-detects the transport.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from .opendrone_id import (ODID_MESSAGE_SIZE, ODIDMessage, OpenDroneIDDecoder,
                           MessageType)


class Transport(Enum):
    BT4_LEGACY = "bt4_legacy"
    BT5_LONG_RANGE = "bt5_long_range"
    WIFI_BEACON = "wifi_beacon"
    WIFI_NAN = "wifi_nan"
    UNKNOWN = "unknown"


# OpenDroneID WiFi vendor-specific IE header: DRI (Drone Remote ID) uses
# OUI 0xFA-0x0B-0xBC with vendor type 0x0D.
WIFI_ODID_OUI = bytes([0xFA, 0x0B, 0xBC])
WIFI_ODID_VENDOR_TYPE = 0x0D


@dataclass
class RemoteIDFrame:
    """A decoded Remote ID frame with its transport and messages."""

    transport: Transport
    messages: List[ODIDMessage] = field(default_factory=list)
    mac_address: str = ""
    rssi_dbm: Optional[float] = None

    @property
    def uas_id(self) -> str:
        for m in self.messages:
            if m.basic_id and m.basic_id.uas_id:
                return m.basic_id.uas_id
        return ""

    def location(self):
        for m in self.messages:
            if m.location:
                return m.location
        return None


class RemoteIDDecoder:
    """Extract and decode ASTM F3411 Remote ID frames from link-layer bytes."""

    def __init__(self):
        self.odid = OpenDroneIDDecoder()

    def decode(self, raw: bytes, transport: Transport = Transport.UNKNOWN,
               mac_address: str = "",
               rssi_dbm: Optional[float] = None) -> Optional[RemoteIDFrame]:
        """Decode a link-layer frame into a :class:`RemoteIDFrame`."""
        if transport == Transport.UNKNOWN:
            transport = self._detect_transport(raw)
        payload = self._extract_payload(raw, transport)
        if payload is None:
            return None
        messages = self._decode_payload(payload)
        if not messages:
            return None
        return RemoteIDFrame(transport=transport, messages=messages,
                             mac_address=mac_address, rssi_dbm=rssi_dbm)

    # ------------------------------------------------------------------
    def _decode_payload(self, payload: bytes) -> List[ODIDMessage]:
        """Decode a payload that is either a single message or a pack."""
        if not payload:
            return []
        msg_type = (payload[0] >> 4) & 0x0F
        if msg_type == MessageType.MESSAGE_PACK:
            return self.odid.decode_message_pack(payload)
        # Possibly several concatenated single messages.
        out: List[ODIDMessage] = []
        for off in range(0, len(payload), ODID_MESSAGE_SIZE):
            chunk = payload[off:off + ODID_MESSAGE_SIZE]
            msg = self.odid.decode_message(chunk)
            if msg:
                out.append(msg)
        return out

    def _detect_transport(self, raw: bytes) -> Transport:
        if WIFI_ODID_OUI in raw:
            # Beacon frames start with frame control 0x80; NAN uses action frames.
            if raw[:1] == b"\x80":
                return Transport.WIFI_BEACON
            return Transport.WIFI_NAN
        if OpenDroneIDDecoder.is_opendroneid_bt(raw):
            return Transport.BT4_LEGACY
        return Transport.UNKNOWN

    def _extract_payload(self, raw: bytes,
                         transport: Transport) -> Optional[bytes]:
        """Strip transport framing and return the OpenDroneID payload."""
        if transport in (Transport.BT4_LEGACY, Transport.BT5_LONG_RANGE):
            return self._extract_bt(raw)
        if transport in (Transport.WIFI_BEACON, Transport.WIFI_NAN):
            return self._extract_wifi(raw)
        # Unknown: assume the buffer already holds ODID messages.
        return raw or None

    @staticmethod
    def _extract_bt(raw: bytes) -> Optional[bytes]:
        """Locate the ASTM AD structure and return the ODID payload.

        BT advertising service data AD structure:
        [len][AD type 0x16][UUID 0xFFFA][app code 0x0D][counter][messages...]
        """
        idx = raw.find(bytes([0xFA, 0x0B, 0xBC]))
        if idx >= 0:
            # After the 3-byte company ID + 1-byte app code + 1-byte counter.
            start = idx + 3
            if start < len(raw) and raw[start] == 0x0D:
                start += 2  # skip app code + counter
            return raw[start:]
        # Fallback: 0x16 service-data AD with 0xFFFA UUID.
        idx = raw.find(b"\xfa\xff")
        if idx >= 0:
            start = idx + 2
            if start < len(raw) and raw[start] == 0x0D:
                start += 2
            return raw[start:]
        return None

    @staticmethod
    def _extract_wifi(raw: bytes) -> Optional[bytes]:
        """Locate the OpenDroneID vendor-specific IE and return the payload."""
        idx = raw.find(WIFI_ODID_OUI)
        if idx < 0:
            return None
        # OUI(3) + vendor type(1) + counter(1) then messages.
        start = idx + 3
        if start < len(raw) and raw[start] == WIFI_ODID_VENDOR_TYPE:
            start += 2  # vendor type + counter
        return raw[start:]
