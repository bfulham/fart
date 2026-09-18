"""sACN (E1.31) control input. New in v2 -- v1 had no sACN input path at
all. Hand-rolled (not using the `sacn` PyPI package's receiver) to match
how psn_in/artnet_in are implemented and keep the wire format directly
under test, the same way artnet_in is.

Exactly one control-input plugin (this or artnet_in) ever runs at a time,
so -- same as artnet_in -- there is no possibility of two input plugins
fighting over a socket.
"""
from __future__ import annotations

import socket
import threading

from ._sacn import SACN_PORT, parse_sacn_dmx, sacn_multicast_group


class SACNInPlugin:
    def __init__(self, log=None):
        self.log = log or (lambda _msg: None)
        self.sock = None
        self.stop_evt = threading.Event()
        self._bus = None
        self._thread = None

    def start(self, config, bus, universes=None):
        """config: a SACNInConfig (config.universe is the fallback/fader
        universe). universes: every universe actually needed (see
        engine.dmx_in_universes_needed) -- sACN requires joining a
        multicast group per universe, unlike Art-Net, so without this a
        fixture whose console relay lives on any universe but config.universe
        would silently never receive data. bus: a bus.ExternalInputBus."""
        self._bus = bus
        universes = sorted({int(u) for u in universes} if universes else {int(config.universe)})
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("", SACN_PORT))
        for universe in universes:
            membership = socket.inet_aton(sacn_multicast_group(universe)) + socket.inet_aton("0.0.0.0")
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        self.sock.settimeout(0.3)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.log(f"DMX in (sACN): listening on universes {universes} (port {SACN_PORT})")

    def _loop(self):
        while not self.stop_evt.is_set():
            try:
                data, _addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            parsed = parse_sacn_dmx(data)
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
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)
