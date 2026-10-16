"""ENTTEC Open DMX USB output. Ported from OpenDMXPort/OpenDMX in fart.py
v1, behaviour unchanged."""
from __future__ import annotations

import time

try:
    import serial
except Exception:
    serial = None


def parse_open_dmx_adapter_map(text, fallback_port, default_universe):
    """Parse 'COM3=0, COM4=1' into {universe: port}. Blank text falls back
    to a single adapter on default_universe."""
    text = (text or "").strip()
    if not text:
        return {int(default_universe): str(fallback_port).strip()}
    mapping = {}
    normalized = text.replace("\n", ",").replace(";", ",")
    for part in normalized.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError("Open DMX adapters must use COMx=universe, e.g. COM3=0, COM4=1")
        port, universe = part.split("=", 1)
        port = port.strip()
        if not port:
            raise ValueError("Open DMX adapter mapping has a blank serial port")
        universe = int(universe.strip())
        if universe in mapping:
            raise ValueError(f"Open DMX universe {universe} is assigned to more than one adapter")
        if port.lower() in [p.lower() for p in mapping.values()]:
            raise ValueError(f"Open DMX adapter {port} is assigned more than once")
        mapping[universe] = port
    if not mapping:
        raise ValueError("No Open DMX adapters were configured")
    return mapping


class _OpenDMXPort:
    def __init__(self, port):
        if serial is None:
            raise RuntimeError("pyserial is missing")
        self.port = str(port).strip()
        self.s = serial.Serial(port=self.port, baudrate=250000, bytesize=8,
                                parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_TWO,
                                timeout=0, write_timeout=0.25)

    def send_frame(self, frame):
        self.s.break_condition = True
        time.sleep(0.00012)
        self.s.break_condition = False
        time.sleep(0.000012)
        self.s.write(b"\x00" + bytes(frame))

    def close(self):
        try:
            self.s.break_condition = True
            time.sleep(0.02)
            self.s.close()
        except Exception:
            pass


class OpenDMXOutPlugin:
    def __init__(self):
        self.adapters = []
        self.default_universe = 0

    def start(self, config):
        """config: an OpenDMXOutConfig."""
        self.default_universe = 0
        mapping = parse_open_dmx_adapter_map(config.adapters, config.fallback_port, self.default_universe)
        for universe, port in sorted(mapping.items(), key=lambda item: int(item[0])):
            port = str(port).strip()
            if not port:
                continue
            self.adapters.append((int(universe), _OpenDMXPort(port)))
        if not self.adapters:
            raise RuntimeError("No Open DMX adapters are configured")

    def send(self, frames: dict):
        blank = bytes(512)
        for universe, adapter in self.adapters:
            adapter.send_frame(frames.get(int(universe), blank))

    def close(self):
        for _universe, adapter in self.adapters:
            adapter.close()
