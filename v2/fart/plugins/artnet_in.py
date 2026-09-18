"""Art-Net control input. Exactly one instance of this ever runs (enforced
by Settings.dmx_in.active being a single choice, not a set), so unlike v1
there is no other socket that can ever compete with it for port 6454 --
that bug class is structurally gone, not just avoided by convention.

Stores every universe it sees into the shared ExternalInputBus; it has no
opinion about what any of that data *means* (fader value vs. a fixture's
console mode/marker-select channel) -- that interpretation happens where
the data is consumed, not here.
"""
from __future__ import annotations

import socket
import threading

from ._artnet import ARTNET_PORT, parse_artnet_dmx


class ArtNetInPlugin:
    def __init__(self, log=None):
        self.log = log or (lambda _msg: None)
        self.sock = None
        self.stop_evt = threading.Event()
        self._bus = None
        self._thread = None

    def start(self, config, bus):
        """config: an ArtNetInConfig (currently unused -- Art-Net carries
        its universe in-band per packet, so nothing to bind per-universe).
        bus: a bus.ExternalInputBus."""
        self._bus = bus
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("", ARTNET_PORT))
        self.sock.settimeout(0.3)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.log(f"DMX in (Art-Net): listening on UDP {ARTNET_PORT}")

    def _loop(self):
        while not self.stop_evt.is_set():
            try:
                data, _addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            parsed = parse_artnet_dmx(data)
            if parsed:
                universe, dmx = parsed
                self._bus.update(universe, dmx)

    def stop(self):
        self.stop_evt.set()
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        # Wait for the receive loop to actually exit before returning, so a
        # quick stop-then-start doesn't race the OS for this port: closing
        # the socket while the loop thread is mid-recvfrom doesn't always
        # free the port instantly on every platform.
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)
