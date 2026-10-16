"""Thread-safe shared state between plugins (producers) and the engine
(consumer). No plugin ever talks to another plugin directly — everything
flows through these three stores. This is what makes "exactly one control-
input plugin owns the socket, multiple internal consumers read from it"
actually work: the plugin writes here once, the engine reads it as many
times as it needs to (once for the fader, once per fixture's console-relay
channels) without touching the network again.
"""
from __future__ import annotations

import threading
import time
from collections import deque


class TrackerBank:
    """Latest position and velocity for every PSN tracker ID, plus whatever
    else the sender includes (speed, orientation, acceleration, target
    position, status, timestamp, name) and per-tracker receive rate. The
    extras are display-only -- nothing in the engine reads them -- so the PSN
    In tab can show everything being received even for markers no fixture
    uses."""

    RATE_WINDOW_S = 10.0

    def __init__(self):
        self.lock = threading.Lock()
        self.xyz = {}
        self.vel = {}
        self.extras = {}    # marker_id -> {"speed": (x, y, z), "orientation": ..., ...}
        self.names = {}     # marker_id -> str, from PSN info packets
        self.arrivals = {}  # marker_id -> deque of recent data-packet times
        self.system_name = ""

    def update(self, marker_id, x, y, z):
        marker_id = int(marker_id)
        now = time.monotonic()
        x, y, z = float(x), float(y), float(z)
        with self.lock:
            old = self.xyz.get(marker_id)
            if old and now > old[3]:
                dt = max(0.001, now - old[3])
                self.vel[marker_id] = ((x - old[0]) / dt, (y - old[1]) / dt, (z - old[2]) / dt)
            self.xyz[marker_id] = (x, y, z, now)

    def get(self, marker_id):
        with self.lock:
            return self.xyz.get(int(marker_id), (0.0, 0.0, 0.0, 0.0))

    def predict(self, marker_id, seconds):
        marker_id = int(marker_id)
        with self.lock:
            x, y, z, t = self.xyz.get(marker_id, (0.0, 0.0, 0.0, 0.0))
            vx, vy, vz = self.vel.get(marker_id, (0.0, 0.0, 0.0))
        return (x + vx * float(seconds), y + vy * float(seconds), z + vz * float(seconds), t)

    def snapshot(self):
        with self.lock:
            return dict(self.xyz)

    def update_extras(self, marker_id, **fields):
        """Record one data packet's worth of display-only fields for this
        tracker (any subset of speed, orientation, accel, target, status,
        psn_time) and count the packet toward its receive rate."""
        marker_id = int(marker_id)
        now = time.monotonic()
        with self.lock:
            self.extras.setdefault(marker_id, {}).update(fields)
            self.arrivals.setdefault(marker_id, deque(maxlen=64)).append(now)

    def set_name(self, marker_id, name):
        with self.lock:
            self.names[int(marker_id)] = str(name)

    def clear(self):
        """Forget every tracker. Live ones reappear on their next packet."""
        with self.lock:
            self.xyz.clear()
            self.vel.clear()
            self.extras.clear()
            self.names.clear()
            self.arrivals.clear()

    def snapshot_detail(self):
        """{marker_id: dict} of everything known about each tracker seen --
        position, derived speed (from consecutive positions), anything the
        sender included itself, name, receive rate (packets/s over the last
        RATE_WINDOW_S) and age of the last packet. Safe to read without the
        lock afterwards."""
        now = time.monotonic()
        detail = {}
        with self.lock:
            ids = set(self.xyz) | set(self.extras) | set(self.names) | set(self.arrivals)
            for marker_id in ids:
                x, y, z, t = self.xyz.get(marker_id, (0.0, 0.0, 0.0, 0.0))
                times = self.arrivals.get(marker_id)
                recent = [a for a in times if now - a <= self.RATE_WINDOW_S] if times else []
                rate = ((len(recent) - 1) / (recent[-1] - recent[0])
                        if len(recent) >= 2 and recent[-1] > recent[0] else 0.0)
                last = max(t, times[-1] if times else 0.0)
                entry = {
                    "position": (x, y, z) if t else None,
                    "derived_velocity": self.vel.get(marker_id),
                    "name": self.names.get(marker_id, ""),
                    "rate": rate,
                    "age": (now - last) if last else None,
                }
                entry.update(self.extras.get(marker_id, {}))
                detail[marker_id] = entry
        return detail


class FaderState:
    """Latest normalized (0-1) intensity fader value."""

    def __init__(self):
        self.lock = threading.Lock()
        self.value = 0.0
        self.updated = 0.0

    def update(self, value):
        with self.lock:
            self.value = max(0.0, min(1.0, float(value)))
            self.updated = time.monotonic()

    def get(self):
        with self.lock:
            return self.value, self.updated


class ExternalInputBus:
    """Latest full 512-channel DMX frame received per universe, from
    whichever ControlInputPlugin (Art-Net-in or sACN-in) is currently
    active. Exactly one such plugin ever runs at a time (dmx_in.active),
    so there is never contention over which plugin owns the listening
    socket — but there are still multiple *readers*: every fixture's
    console-relay mode/marker-select channels and shadow feed all read the
    same stored frames independently.
    """

    RATE_WINDOW_S = 10.0

    def __init__(self):
        self.lock = threading.Lock()
        self.frames = {}
        self.arrivals = {}  # universe -> deque of recent packet times, for rate()

    def update(self, universe, dmx_bytes):
        universe = int(universe)
        frame = bytearray(512)
        n = min(512, len(dmx_bytes))
        frame[:n] = dmx_bytes[:n]
        now = time.monotonic()
        with self.lock:
            self.frames[universe] = (bytes(frame), now)
            self.arrivals.setdefault(universe, deque(maxlen=64)).append(now)

    def rates(self):
        """{universe: (packets_per_second, age_seconds)} for every universe
        seen. Rate is measured over the packets that arrived within the last
        RATE_WINDOW_S, so a feed running well under 1 fps still reads
        sensibly instead of rounding to zero; 0.0 once fewer than two
        packets fall in the window."""
        now = time.monotonic()
        result = {}
        with self.lock:
            for universe, times in self.arrivals.items():
                recent = [t for t in times if now - t <= self.RATE_WINDOW_S]
                if len(recent) >= 2 and recent[-1] > recent[0]:
                    rate = (len(recent) - 1) / (recent[-1] - recent[0])
                else:
                    rate = 0.0
                result[universe] = (rate, max(0.0, now - times[-1]))
        return result

    def get(self, universe):
        """Returns (frame_bytes_or_None, last_update_monotonic_or_0.0)."""
        with self.lock:
            return self.frames.get(int(universe), (None, 0.0))

    def snapshot(self):
        """Every universe currently held, for live-monitor UI display (e.g.
        the DMX In tab's channel grid) -- a plain copy, safe to read
        without the lock afterwards."""
        with self.lock:
            return dict(self.frames)

    def get_channel(self, universe, channel):
        """Returns (value 0-255 or None, age_in_seconds or None)."""
        frame, ts = self.get(universe)
        if frame is None or channel <= 0 or channel > len(frame):
            return None, None
        return frame[channel - 1], max(0.0, time.monotonic() - ts)
