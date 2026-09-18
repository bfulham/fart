"""sACN (E1.31) DMX output. Hand-rolled against the verified wire format in
_sacn.py, replacing v1's dependency on the third-party `sacn` package --
one less external dependency, and the same tested format module as sacn_in
so the two directions can't drift apart from each other."""
from __future__ import annotations

import socket
import uuid

from .._version import APP_NAME
from ._sacn import SACN_PORT, build_sacn_dmx, sacn_multicast_group


class SACNOutPlugin:
    def __init__(self):
        self.sock = None
        self.universes = []
        self.cid = uuid.uuid4().bytes
        self.sequence = 0

    def start(self, config):
        """config: a SACNOutConfig."""
        self.universes = sorted({int(u) for u in config.universes})
        if not self.universes:
            raise ValueError("At least one sACN universe is required")
        for universe in self.universes:
            if not 1 <= universe <= 63999:
                raise ValueError("sACN universe must be between 1 and 63999")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 8)

    def send(self, frames: dict):
        self.sequence = (self.sequence + 1) & 0xFF
        blank = bytes(512)
        for universe in self.universes:
            frame = frames.get(universe, blank)
            packet = build_sacn_dmx(self.cid, APP_NAME, self.sequence, universe, frame)
            self.sock.sendto(packet, (sacn_multicast_group(universe), SACN_PORT))

    def close(self):
        if self.sock:
            self.sock.close()
