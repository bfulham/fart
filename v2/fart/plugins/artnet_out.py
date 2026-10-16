"""Art-Net DMX output. Ported from ArtNet in fart.py v1."""
from __future__ import annotations

import socket

from ._artnet import ARTNET_PORT, build_artnet_dmx


class ArtNetOutPlugin:
    def __init__(self):
        self.sock = None
        self.target_ip = None

    def start(self, config):
        """config: an ArtNetOutConfig."""
        self.target_ip = config.target_ip
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    def send(self, frames: dict):
        for universe, frame in frames.items():
            if not 0 <= int(universe) <= 32767:
                continue
            self.sock.sendto(build_artnet_dmx(universe, frame), (self.target_ip, ARTNET_PORT))

    def close(self):
        if self.sock:
            self.sock.close()
