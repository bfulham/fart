"""Shared Art-Net (ArtDMX) wire format, used by both artnet_in and
artnet_out so the two directions can't drift apart."""
from __future__ import annotations

import struct

ARTNET_PORT = 6454


def parse_artnet_dmx(data):
    """Parse an Art-Net ArtDMX UDP packet. Returns (universe, dmx_bytes) or
    None if this is not a well-formed ArtDMX packet."""
    if len(data) < 18 or data[:8] != b"Art-Net\x00":
        return None
    if struct.unpack_from("<H", data, 8)[0] != 0x5000:
        return None
    universe = struct.unpack_from("<H", data, 14)[0]
    length = struct.unpack_from(">H", data, 16)[0]
    if 18 + length > len(data):
        return None
    return universe, bytes(data[18:18 + length])


def build_artnet_dmx(universe, dmx_bytes):
    frame = bytes(dmx_bytes) + bytes(512 - len(dmx_bytes))
    header = (
        b"Art-Net\x00" + struct.pack("<H", 0x5000) + struct.pack(">H", 14)
        + bytes((0, 0)) + struct.pack("<H", int(universe)) + struct.pack(">H", 512)
    )
    return header + frame
