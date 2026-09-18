"""Uniform plugin interfaces. A plugin never talks to another plugin
directly -- input plugins only write into a bus object (fart.bus), output
plugins only read a computed frame dict handed to them by the engine layer.
"""
from __future__ import annotations

from typing import Protocol


class PositionInputPlugin(Protocol):
    """Produces marker positions (currently just psn-in)."""
    def start(self, config, positions) -> None: ...
    def stop(self) -> None: ...


class ControlInputPlugin(Protocol):
    """Produces raw DMX frames from an external console (artnet-in, sacn-in).
    Exactly one of these runs at a time -- see Settings.dmx_in.active."""
    def start(self, config, bus) -> None: ...
    def stop(self) -> None: ...


class OutputPlugin(Protocol):
    """Transmits computed DMX frames (artnet-out, sacn-out, open-dmx-out).
    Exactly one of these runs at a time -- see Settings.dmx_out.active."""
    def start(self, config) -> None: ...
    def send(self, frames: dict) -> None: ...
    def close(self) -> None: ...
