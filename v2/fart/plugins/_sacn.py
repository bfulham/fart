"""Shared sACN (E1.31) DATA packet wire format for sacn_in.

Byte layout (verified against the E1.31 spec, not just recalled from
memory) for a full 512-channel DATA packet:

  Root layer:    0-15   preamble + ACN packet identifier
                 16-17  flags & length
                 18-21  vector (VECTOR_ROOT_E131_DATA = 0x00000004)
                 22-37  CID (16 bytes)
  Framing layer: 38-39  flags & length
                 40-43  vector (VECTOR_E131_DATA_PACKET = 0x00000002)
                 44-107 source name (64 bytes)
                 108    priority
                 109-110 sync address
                 111    sequence number
                 112    options
                 113-114 universe (big-endian uint16)
  DMP layer:     115-116 flags & length
                 117    vector (VECTOR_DMP_SET_PROPERTY = 0x02)
                 118-122 fixed address/data-type + first property address + address increment
                 123-124 property value count (big-endian uint16, 513 = 1 start code + 512 channels)
                 125    DMX start code (0x00 for normal DMX data)
                 126-637 DMX data (512 bytes)

Total minimum packet size: 638 bytes.
"""
from __future__ import annotations

import struct

SACN_PORT = 5568
VECTOR_ROOT_E131_DATA = 0x00000004
VECTOR_E131_DATA_PACKET = 0x00000002
VECTOR_DMP_SET_PROPERTY = 0x02
MIN_PACKET_LENGTH = 638


def sacn_multicast_group(universe):
    universe = int(universe) & 0xFFFF
    return f"239.255.{(universe >> 8) & 0xFF}.{universe & 0xFF}"


def _flags_and_length(length):
    """2-byte ACN PDU flags-and-length field: top 4 bits fixed at 0x7,
    remaining 12 bits are `length`. Verified against the reference sacn
    library's make_flagsandlength(): (0x7 << 4) + ((length & 0xF00) >> 8),
    length & 0xFF.
    """
    return bytes([0x70 | ((length >> 8) & 0x0F), length & 0xFF])


def build_sacn_dmx(cid, source_name, sequence, universe, dmx_bytes, priority=100):
    """Build one E1.31 sACN DATA packet carrying a full 512-channel frame.

    cid: 16 raw bytes identifying this source (e.g. uuid.uuid4().bytes),
    stable for the lifetime of the sender. sequence: 0-255, incremented by
    the caller each packet (wraps).
    """
    dmx = bytes(dmx_bytes)
    dmx = dmx[:512] + bytes(512 - len(dmx)) if len(dmx) < 512 else dmx[:512]
    name_bytes = source_name.encode("utf-8")[:63]
    name_field = name_bytes + bytes(64 - len(name_bytes))
    total_length = MIN_PACKET_LENGTH  # 638: fixed size for a full-frame packet

    root = (
        struct.pack(">H", 0x0010) + struct.pack(">H", 0x0000)
        + b"ASC-E1.17\x00\x00\x00"
        + _flags_and_length(total_length - 16)
        + struct.pack(">I", VECTOR_ROOT_E131_DATA)
        + bytes(cid)
    )
    framing = (
        _flags_and_length(total_length - 38)
        + struct.pack(">I", VECTOR_E131_DATA_PACKET)
        + name_field
        + bytes([priority & 0xFF])
        + struct.pack(">H", 0)
        + bytes([sequence & 0xFF])
        + bytes([0])
        + struct.pack(">H", int(universe))
    )
    dmp = (
        _flags_and_length(total_length - 115)
        + bytes([VECTOR_DMP_SET_PROPERTY])
        + bytes([0xA1])
        + struct.pack(">H", 0x0000)
        + struct.pack(">H", 0x0001)
        + struct.pack(">H", 513)
        + bytes([0])
        + dmx
    )
    packet = root + framing + dmp
    assert len(packet) == total_length
    return packet


def parse_sacn_dmx(data):
    """Parse an E1.31 sACN DATA packet. Returns (universe, dmx_bytes) or
    None if this is not a well-formed, full-size sACN DMX data packet."""
    if len(data) < MIN_PACKET_LENGTH:
        return None
    root_vector = struct.unpack_from(">I", data, 18)[0]
    if root_vector != VECTOR_ROOT_E131_DATA:
        return None
    framing_vector = struct.unpack_from(">I", data, 40)[0]
    if framing_vector != VECTOR_E131_DATA_PACKET:
        return None
    if data[117] != VECTOR_DMP_SET_PROPERTY:
        return None
    universe = struct.unpack_from(">H", data, 113)[0]
    dmx = bytes(data[126:638])
    return universe, dmx
