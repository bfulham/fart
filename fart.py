#!/usr/bin/env python3
"""FART - Fixture Aiming and Remote Tracking.

A single-file Windows GUI that receives OpenFollow PosiStageNet (PSN) tracker
positions, calculates the exact line of sight from one or more moving fixtures
to independently selected markers, and outputs DMX using ENTTEC Open DMX USB, Art-Net, or
sACN. Intensity can come from the manual UI, a configurable OSC value, or an
Art-Net input channel.

FART is experimental show-control software. It is not a safety-rated tracking
or motion-control system.
"""
from __future__ import annotations

import json
import math
import os
import queue
import socket
import struct
import sys
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
import tkinter as tk
from dataclasses import dataclass, asdict, field
from pathlib import Path
from tkinter import ttk, messagebox, filedialog, simpledialog

APP_VERSION = "1.1.0"
APP_SHORT_NAME = "FART"
APP_LONG_NAME = "Fixture Aiming and Remote Tracking"
APP_NAME = f"{APP_SHORT_NAME} {APP_VERSION}"
WINDOW_TITLE = f"{APP_NAME} — {APP_LONG_NAME}"
CONFIG_FILE = Path(os.getenv("APPDATA", Path.home())) / "FART.json"
LEGACY_CONFIG_FILE = Path(os.getenv("APPDATA", Path.home())) / "OpenFollowFollowspot.json"

try:
    import serial
    import serial.tools.list_ports
except Exception:
    serial = None

try:
    from pythonosc.dispatcher import Dispatcher
    from pythonosc.osc_server import ThreadingOSCUDPServer
except Exception:
    Dispatcher = None
    ThreadingOSCUDPServer = None

try:
    from sacn import sACNsender
except Exception:
    sACNsender = None


def clamp(v, lo, hi): return max(lo, min(hi, v))
def wrap180(v): return (v + 180.0) % 360.0 - 180.0
def norm360(v): return v % 360.0

def dmx16(frac):
    n = round(clamp(frac, 0.0, 1.0) * 65535)
    return (n >> 8) & 255, n & 255


@dataclass
class FixtureConfig:
    name: str = "Light 1"
    enabled: bool = True
    marker_id: int = 1

    # Optical centre / pan-tilt pivot in OpenFollow world coordinates.
    x: float = 0.0
    y: float = -8.0
    z: float = 5.0

    # World bearing/elevation represented by physical fixture angle zero.
    pan_zero_bearing: float = 0.0
    tilt_zero_elevation: float = 0.0
    pan_direction: int = 1
    tilt_direction: int = -1
    pan_offset: float = 0.0
    tilt_offset: float = 0.0

    pan_min: float = -270.0
    pan_max: float = 270.0
    tilt_min: float = -135.0
    tilt_max: float = 135.0

    # Absolute DMX slots within the selected output universe. Zero disables.
    pan_coarse: int = 1
    pan_fine: int = 2
    tilt_coarse: int = 3
    tilt_fine: int = 4
    dimmer: int = 5
    dimmer_fine: int = 0
    shutter: int = 0
    shutter_open: int = 255
    intensity_scale: float = 1.0

    # Optional beam controls. Coarse channels are 8-bit when their fine
    # channel is zero, otherwise they are emitted as 16-bit pairs.
    zoom: int = 0
    zoom_fine: int = 0
    iris: int = 0
    iris_100_dmx: int = 255
    focus: int = 0
    focus_fine: int = 0
    zoom_reverse: bool = False
    iris_reverse: bool = False
    focus_reverse: bool = False

    # Optional beam-angle model used for automatic spot-size control.
    # Values are the beam/field angle in degrees produced by the zoom UI at
    # 0% and 100%. Leave both as 0 to disable auto-zoom for that fixture.
    zoom_angle_at_0: float = 0.0
    zoom_angle_at_100: float = 0.0


@dataclass
class Settings:
    marker_id: int = 1
    psn_multicast: str = "236.10.10.10"
    psn_port: int = 56565
    psn_interface: str = "0.0.0.0"

    fader_mode: str = "Manual"
    manual_fader: float = 0.0
    osc_fader_port: int = 9000
    osc_fader_address: str = "/openfollow/1/xyzf"
    osc_fader_arg: int = 3
    osc_fader_min: float = 0.0
    osc_fader_max: float = 1.0
    artnet_input_universe: int = 0
    artnet_input_channel: int = 1

    output: str = "Open DMX"
    serial_port: str = "COM3"
    artnet_ip: str = "255.255.255.255"
    universe: int = 0
    refresh_hz: int = 30
    timeout_s: float = 0.5
    smoothing: float = 0.12

    zoom_master: float = 0.5
    iris_master: float = 1.0
    focus_master: float = 0.5
    zoom_mode: str = "Manual"
    auto_beam_diameter_m: float = 1.0

    fixtures: list[FixtureConfig] = field(default_factory=lambda: [FixtureConfig()])


class TrackerBank:
    """Thread-safe store of the latest position for every PSN tracker ID."""
    def __init__(self):
        self.lock = threading.Lock()
        self.xyz = {}

    def update(self, marker_id, x, y, z):
        with self.lock:
            self.xyz[int(marker_id)] = (float(x), float(y), float(z), time.monotonic())

    def get(self, marker_id):
        with self.lock:
            return self.xyz.get(int(marker_id), (0.0, 0.0, 0.0, 0.0))

    def snapshot(self):
        with self.lock:
            return dict(self.xyz)


class FaderState:
    def __init__(self):
        self.lock = threading.Lock()
        self.value = 0.0
        self.updated = 0.0
    def update(self, value):
        with self.lock:
            self.value = clamp(float(value), 0.0, 1.0)
            self.updated = time.monotonic()
    def get(self):
        with self.lock:
            return self.value, self.updated


def _chunks(data, start, length):
    end = min(len(data), start + length)
    pos = start
    while pos + 4 <= end:
        raw = struct.unpack_from('<I', data, pos)[0]
        cid = raw & 0xFFFF
        data_len = (raw >> 16) & 0x7FFF
        sub = bool(raw & 0x80000000)
        body = pos + 4
        body_end = body + data_len
        if body_end > end:
            break
        yield cid, sub, body, data_len
        pos = body_end


class PSNReceiver:
    DATA_PACKET = 0x6755
    DATA_TRACKER_LIST = 0x0001
    DATA_TRACKER_POS = 0x0000

    def __init__(self, group, port, interface, marker, tracker, log, on_discovered=None):
        self.group, self.port, self.interface = group, port, interface
        self.marker, self.tracker, self.log = marker, tracker, log
        self.on_discovered = on_discovered
        self.discovered = set()
        self.sock = None
        self.stop_evt = threading.Event()
        self.packet_count = 0
        self.data_packet_count = 0
        self.selected_tracker_count = 0
        self.position_count = 0
        self.last_packet_time = 0.0
        self.last_position_time = 0.0

    def set_setup_tabs_enabled(self, enabled):
        if not hasattr(self, 'notebook'):
            return
        state = 'normal' if enabled else 'disabled'
        # Operator is tab 0; all other tabs are setup tabs and should not be
        # edited while live output is running.
        for tab_index in range(1, 4):
            try:
                self.notebook.tab(tab_index, state=state)
            except Exception:
                pass

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('', self.port))
        iface = self.interface.strip() or '0.0.0.0'
        membership = socket.inet_aton(self.group) + socket.inet_aton(iface)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        self.sock.settimeout(0.3)
        threading.Thread(target=self._loop, daemon=True).start()
        self.log(f'Listening for PSN tracker {self.marker} on {self.group}:{self.port} via {iface}')

    def _loop(self):
        while not self.stop_evt.is_set():
            try:
                data, _ = self.sock.recvfrom(65535)
                self._decode(data)
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as e:
                self.log('PSN decode error: ' + str(e))

    def _decode(self, data):
        self.packet_count += 1
        self.last_packet_time = time.monotonic()
        if len(data) < 4:
            return
        raw = struct.unpack_from('<I', data, 0)[0]
        root_id = raw & 0xFFFF
        root_len = (raw >> 16) & 0x7FFF
        if root_id != self.DATA_PACKET:
            return
        self.data_packet_count += 1
        for cid, sub, body, length in _chunks(data, 4, root_len):
            if cid != self.DATA_TRACKER_LIST or not sub:
                continue
            for tracker_id, tracker_sub, t_body, t_len in _chunks(data, body, length):
                if not tracker_sub:
                    continue
                if tracker_id not in self.discovered:
                    self.discovered.add(tracker_id)
                    if self.on_discovered:
                        self.on_discovered(tracker_id)
                if tracker_id == self.marker:
                    self.selected_tracker_count += 1
                for field_id, field_sub, f_body, f_len in _chunks(data, t_body, t_len):
                    # OpenFollow uses pypsn. pypsn sets the high flag bit on
                    # leaf chunks as well as container chunks, including the
                    # 12-byte position chunk. Do not reject position merely
                    # because that flag is set.
                    if field_id == self.DATA_TRACKER_POS and f_len >= 12:
                        x, y, z = struct.unpack_from('<fff', data, f_body)
                        self.tracker.update(tracker_id, x, y, z)
                        self.position_count += 1
                        self.last_position_time = time.monotonic()
                        if self.position_count == 1:
                            self.log(f'PSN position received for tracker {tracker_id}: {x:.3f}, {y:.3f}, {z:.3f}')
                        break

    def stats(self):
        return {
            'packets': self.packet_count,
            'data_packets': self.data_packet_count,
            'selected_tracker': self.selected_tracker_count,
            'positions': self.position_count,
            'last_packet_age': (time.monotonic() - self.last_packet_time) if self.last_packet_time else None,
            'last_position_age': (time.monotonic() - self.last_position_time) if self.last_position_time else None,
        }

    def stop(self):
        self.stop_evt.set()
        if self.sock:
            try: self.sock.close()
            except Exception: pass


class OSCFaderReceiver:
    def __init__(self, port, address, arg_index, input_min, input_max, fader, log):
        self.port, self.address, self.arg_index = port, address, arg_index
        self.input_min, self.input_max = input_min, input_max
        self.fader, self.log, self.server = fader, log, None
    def set_setup_tabs_enabled(self, enabled):
        if not hasattr(self, 'notebook'):
            return
        state = 'normal' if enabled else 'disabled'
        # Operator is tab 0; all other tabs are setup tabs and should not be
        # edited while live output is running.
        for tab_index in range(1, 4):
            try:
                self.notebook.tab(tab_index, state=state)
            except Exception:
                pass

    def start(self):
        if Dispatcher is None:
            raise RuntimeError('python-osc is missing')
        if self.input_max == self.input_min:
            raise ValueError('OSC fader maximum must differ from minimum')
        d = Dispatcher()
        d.map(self.address, self._message)
        self.server = ThreadingOSCUDPServer(('0.0.0.0', self.port), d)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.log(f'OSC fader: {self.address}, argument {self.arg_index}, UDP {self.port}')
    def _message(self, address, *args):
        try:
            raw = float(args[self.arg_index])
            value = (raw - self.input_min) / (self.input_max - self.input_min)
            self.fader.update(value)
        except (IndexError, TypeError, ValueError):
            return
    def stop(self):
        if self.server:
            self.server.shutdown(); self.server.server_close()


class ArtNetFaderReceiver:
    def __init__(self, universe, channel, fader, log):
        self.universe, self.channel, self.fader, self.log = universe, channel, fader, log
        self.sock = None
        self.stop_evt = threading.Event()
    def set_setup_tabs_enabled(self, enabled):
        if not hasattr(self, 'notebook'):
            return
        state = 'normal' if enabled else 'disabled'
        # Operator is tab 0; all other tabs are setup tabs and should not be
        # edited while live output is running.
        for tab_index in range(1, 4):
            try:
                self.notebook.tab(tab_index, state=state)
            except Exception:
                pass

    def start(self):
        if not 1 <= self.channel <= 512:
            raise ValueError('Art-Net input channel must be 1–512')
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('', 6454))
        self.sock.settimeout(0.3)
        threading.Thread(target=self._loop, daemon=True).start()
        self.log(f'Art-Net fader: universe {self.universe}, channel {self.channel}')
    def _loop(self):
        while not self.stop_evt.is_set():
            try:
                data, _ = self.sock.recvfrom(2048)
                if len(data) < 18 or data[:8] != b'Art-Net\x00':
                    continue
                if struct.unpack_from('<H', data, 8)[0] != 0x5000:
                    continue
                universe = struct.unpack_from('<H', data, 14)[0]
                length = struct.unpack_from('>H', data, 16)[0]
                if universe == self.universe and self.channel <= length and 18 + length <= len(data):
                    self.fader.update(data[18 + self.channel - 1] / 255.0)
            except socket.timeout:
                continue
            except OSError:
                break
    def stop(self):
        self.stop_evt.set()
        if self.sock:
            try: self.sock.close()
            except Exception: pass


class Output:
    def send(self, frame): pass
    def close(self): pass


class OpenDMX(Output):
    """ENTTEC Open DMX USB via FTDI VCP.

    Open DMX is an unbuffered adapter. The PC must generate BREAK, MAB and all
    513 serial slots continuously. Timing is therefore best-effort under Windows,
    but this is the standard VCP approach and is adequate for a rudimentary tool.
    """
    def __init__(self, port):
        if serial is None: raise RuntimeError("pyserial is missing")
        self.s = serial.Serial(port=port, baudrate=250000, bytesize=8,
                               parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_TWO,
                               timeout=0, write_timeout=0.25)
    def send(self, frame):
        # DMX512-A minimum BREAK 88 us and MAB 8 us. Windows scheduling is not
        # microsecond deterministic, so use conservative values.
        self.s.break_condition = True
        time.sleep(0.00012)
        self.s.break_condition = False
        time.sleep(0.000012)
        self.s.write(b"\x00" + frame)
    def close(self):
        try: self.s.break_condition = True; time.sleep(0.02); self.s.close()
        except Exception: pass


class ArtNet(Output):
    def __init__(self, ip, universe):
        self.ip, self.u = ip, universe
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    def send(self, frame):
        hdr = (b"Art-Net\x00" + struct.pack("<H",0x5000) + struct.pack(">H",14) +
               bytes((0,0)) + struct.pack("<H",self.u) + struct.pack(">H",512))
        self.s.sendto(hdr + frame, (self.ip,6454))
    def close(self): self.s.close()


class SACN(Output):
    """ANSI E1.31/sACN multicast output using the ``sacn`` package.

    The package exposes active universes through ``sender[universe]``.  The
    previous implementation called a non-existent ``get_output`` method and
    also left multicast disabled, which made startup fail whenever sACN was
    selected.
    """
    def __init__(self, universe, fps=30):
        if sACNsender is None:
            raise RuntimeError("sacn is missing")
        if not 1 <= int(universe) <= 63999:
            raise ValueError("sACN universe must be between 1 and 63999")

        self.u = int(universe)
        self.s = sACNsender(source_name=APP_NAME, fps=max(1, min(100, int(fps))))
        try:
            self.s.start()
            self.s.activate_output(self.u)
            self.out = self.s[self.u]
            self.out.multicast = True
            self.out.dmx_data = tuple([0] * 512)
        except Exception:
            try:
                self.s.stop()
            except Exception:
                pass
            raise

    def send(self, frame):
        if len(frame) != 512:
            raise ValueError("sACN output frame must contain exactly 512 channels")
        self.out.dmx_data = tuple(frame)

    def close(self):
        try:
            self.s.stop()
        except Exception:
            pass



def calculate_aim(fixture: FixtureConfig, x, y, z, previous_pan=None):
    """Calculate the exact line from one fixture optical centre to the marker.

    World axes: +X right, +Y away/upstage, +Z up. Bearing 0 points +Y and
    increases toward +X. Elevation 0 is horizontal and positive is up.
    """
    dx, dy, dz = x - fixture.x, y - fixture.y, z - fixture.z
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    if distance < 1e-6:
        raise ValueError("Marker is at the fixture optical centre")

    bearing = math.degrees(math.atan2(dx, dy))
    elevation = math.degrees(math.atan2(dz, math.hypot(dx, dy)))

    base_pan = wrap180(bearing - fixture.pan_zero_bearing) * fixture.pan_direction + fixture.pan_offset
    tilt = (elevation - fixture.tilt_zero_elevation) * fixture.tilt_direction + fixture.tilt_offset

    # A fixture with more than 360 degrees of pan has several mechanically
    # equivalent ways to point at the same bearing. Prefer the one nearest its
    # previous angle so it does not unexpectedly spin through the long path.
    candidates = [base_pan + 360 * k for k in range(-3, 4)]
    valid = [p for p in candidates if fixture.pan_min <= p <= fixture.pan_max]
    if valid:
        reference = previous_pan if previous_pan is not None else (fixture.pan_min + fixture.pan_max) / 2.0
        pan = min(valid, key=lambda p: abs(p - reference))
    else:
        # Keep the nearest equivalent angle; the caller will clamp it and show
        # a limit warning rather than silently selecting an unrelated heading.
        reference = previous_pan if previous_pan is not None else (fixture.pan_min + fixture.pan_max) / 2.0
        pan = min(candidates, key=lambda p: abs(p - reference))

    return bearing, elevation, pan, tilt, distance


def fixture_channels(fixture: FixtureConfig):
    return {
        'pan coarse': fixture.pan_coarse,
        'pan fine': fixture.pan_fine,
        'tilt coarse': fixture.tilt_coarse,
        'tilt fine': fixture.tilt_fine,
        'dimmer': fixture.dimmer,
        'dimmer fine': fixture.dimmer_fine,
        'shutter': fixture.shutter,
        'zoom': fixture.zoom,
        'zoom fine': fixture.zoom_fine,
        'iris': fixture.iris,
        'focus': fixture.focus,
        'focus fine': fixture.focus_fine,
    }



def _strip_ns(tag):
    return tag.split('}', 1)[-1] if '}' in tag else tag


def _xml_attr(node, name, default=''):
    # GDTF files vary in case and namespace handling between tools. Keep this forgiving.
    for key, value in node.attrib.items():
        if key.lower() == name.lower():
            return value
    return default


def _parse_gdtf_offsets(value):
    if not value:
        return []
    text = str(value).replace('{', '').replace('}', '').replace(',', ' ')
    out = []
    for part in text.split():
        try:
            out.append(int(part))
        except Exception:
            pass
    return out


def _read_gdtf_description(path):
    with zipfile.ZipFile(path, 'r') as zf:
        names = zf.namelist()
        desc_name = next((n for n in names if n.lower().endswith('description.xml')), None)
        if not desc_name:
            raise ValueError('No description.xml found in the GDTF file')
        return ET.fromstring(zf.read(desc_name))


def list_gdtf_modes(path):
    root = _read_gdtf_description(path)
    modes = [node for node in root.iter() if _strip_ns(node.tag) == 'DMXMode']
    names = [_xml_attr(m, 'Name', f'Mode {i+1}') for i, m in enumerate(modes)]
    if not names:
        raise ValueError('No DMXMode entries found in the GDTF file')
    return names


def select_gdtf_mode(parent, path):
    modes = list_gdtf_modes(path)
    if len(modes) == 1:
        return modes[0]

    dialog = tk.Toplevel(parent)
    dialog.title(f'{APP_SHORT_NAME} GDTF mode')
    dialog.geometry('520x360')
    dialog.transient(parent)
    dialog.grab_set()
    result = {'mode': None}

    ttk.Label(
        dialog,
        text='Select the GDTF DMX mode to import. This must match the fixture mode patched in MA3.',
        wraplength=480,
        justify='left',
    ).pack(fill='x', padx=10, pady=(10, 6))

    frame = ttk.Frame(dialog)
    frame.pack(fill='both', expand=True, padx=10, pady=6)
    scrollbar = ttk.Scrollbar(frame, orient='vertical')
    listbox = tk.Listbox(frame, height=12, yscrollcommand=scrollbar.set, exportselection=False)
    scrollbar.config(command=listbox.yview)
    listbox.pack(side='left', fill='both', expand=True)
    scrollbar.pack(side='right', fill='y')
    for mode in modes:
        listbox.insert('end', mode)
    listbox.selection_set(0)
    listbox.activate(0)

    buttons = ttk.Frame(dialog)
    buttons.pack(fill='x', padx=10, pady=(4, 10))

    def choose():
        sel = listbox.curselection()
        if sel:
            result['mode'] = modes[int(sel[0])]
            dialog.destroy()

    def cancel():
        dialog.destroy()

    ttk.Button(buttons, text='Import selected mode', command=choose).pack(side='right', padx=4)
    ttk.Button(buttons, text='Cancel', command=cancel).pack(side='right', padx=4)
    listbox.bind('<Double-1>', lambda _event: choose())
    dialog.bind('<Return>', lambda _event: choose())
    dialog.bind('<Escape>', lambda _event: cancel())
    parent.wait_window(dialog)
    return result['mode']


def _parse_gdtf_dmx_value(value, default=None):
    if value is None or value == '':
        return default
    text = str(value).strip().replace('{', '').replace('}', '')
    if not text:
        return default
    # GDTF often stores values like "20/1" or "32768/2". The first
    # number is the actual DMX value; the value after / is the byte count.
    text = text.split()[0].split(',')[0].split('/')[0]
    try:
        return int(float(text))
    except Exception:
        return default


def _parse_gdtf_physical(value, default=None):
    if value is None or value == '':
        return default
    text = str(value).strip().replace('{', '').replace('}', '')
    if not text:
        return default
    # GDTF physical values are plain numbers for angular attributes in degrees.
    # Some exporters include units or multi-value text; keep the first token.
    token = text.replace(',', ' ').split()[0]
    try:
        return float(token)
    except Exception:
        return default


def _derive_zoom_angles(dmx_channel):
    """Return beam/field angle at zoom 0% and 100% if GDTF provides it.

    GDTF stores this most commonly on a Zoom ChannelFunction as PhysicalFrom
    and PhysicalTo. The values are not guaranteed to exist, so this is always a
    best-effort import and remains editable in the UI.
    """
    candidates = []
    for node in dmx_channel.iter():
        tag = _strip_ns(node.tag)
        if tag not in ('ChannelFunction', 'LogicalChannel'):
            continue
        attr_text = ' '.join(filter(None, [_xml_attr(node, 'Attribute', ''), _xml_attr(node, 'Name', '')])).lower()
        if 'zoom' not in attr_text:
            continue
        physical_from = _parse_gdtf_physical(_xml_attr(node, 'PhysicalFrom', ''))
        physical_to = _parse_gdtf_physical(_xml_attr(node, 'PhysicalTo', ''))
        if physical_from is None or physical_to is None:
            continue
        if physical_from <= 0 or physical_to <= 0:
            continue
        # Prefer the widest DMX range if there are multiple zoom functions.
        r = _gdtf_range_from_node(node) or (0, 255)
        candidates.append((abs(r[1] - r[0]), float(physical_from), float(physical_to)))
    if not candidates:
        return None
    _span, a0, a100 = max(candidates, key=lambda item: item[0])
    return a0, a100


def fixture_has_zoom_model(fixture):
    try:
        a0 = float(getattr(fixture, 'zoom_angle_at_0', 0.0))
        a100 = float(getattr(fixture, 'zoom_angle_at_100', 0.0))
    except Exception:
        return False
    return a0 > 0.0 and a100 > 0.0 and abs(a100 - a0) > 0.001


def auto_zoom_for_distance(fixture, distance, target_diameter_m, fallback_zoom=0.5):
    """Map desired spot diameter at the marker to normalized zoom output.

    Returns (normalized_zoom, required_angle_degrees, available). If the fixture
    has no physical zoom model, available is False and fallback_zoom is used.
    """
    if not fixture_has_zoom_model(fixture) or distance <= 0:
        return clamp(fallback_zoom, 0.0, 1.0), None, False
    diameter = max(0.01, float(target_diameter_m))
    required = math.degrees(2.0 * math.atan((diameter / 2.0) / max(0.001, distance)))
    a0 = float(fixture.zoom_angle_at_0)
    a100 = float(fixture.zoom_angle_at_100)
    value = (required - a0) / (a100 - a0)
    return clamp(value, 0.0, 1.0), required, True


def _gdtf_range_from_node(node):
    start = _parse_gdtf_dmx_value(_xml_attr(node, 'DMXFrom', ''))
    end = _parse_gdtf_dmx_value(_xml_attr(node, 'DMXTo', ''))
    if start is None and end is None:
        return None
    if start is None:
        start = end
    if end is None:
        end = start
    if end < start:
        start, end = end, start
    return int(start), int(end)


def _gdtf_ranges_for_channel(dmx_channel):
    ranges = []
    for node in dmx_channel.iter():
        if node is dmx_channel:
            continue
        tag = _strip_ns(node.tag)
        if tag not in ('ChannelFunction', 'ChannelSet'):
            continue
        r = _gdtf_range_from_node(node)
        if not r:
            continue
        name = ' '.join(filter(None, [
            _xml_attr(node, 'Name', ''),
            _xml_attr(node, 'Attribute', ''),
            _xml_attr(node, 'PhysicalFrom', ''),
            _xml_attr(node, 'PhysicalTo', ''),
        ])).lower()
        ranges.append((r[0], r[1], name, tag))
    return sorted(ranges, key=lambda item: (item[0], item[1]))


def _derive_shutter_open_value(dmx_channel):
    bad = ('closed', 'close', 'strobe', 'random', 'pulse', 'effect', 'macro', 'reset', 'lamp', 'strike', 'douse')
    candidates = []
    for start, end, name, _tag in _gdtf_ranges_for_channel(dmx_channel):
        if 'open' in name and not any(word in name for word in bad):
            candidates.append((start, end))
    if candidates:
        start, end = candidates[0]
        return int(round((start + end) / 2))
    # Fall back to common simple shutter behaviour if the GDTF names are poor.
    for start, end, name, _tag in _gdtf_ranges_for_channel(dmx_channel):
        if 'open' in name:
            return int(round((start + end) / 2))
    return None


def _derive_iris_100_value(dmx_channel):
    ranges = _gdtf_ranges_for_channel(dmx_channel)
    if not ranges:
        return None
    effect_words = ('effect', 'macro', 'pulse', 'random', 'strobe', 'shake', 'pattern', 'animation')
    useful = []
    effect_starts = []
    for start, end, name, _tag in ranges:
        is_effect = any(word in name for word in effect_words)
        if is_effect:
            effect_starts.append(start)
        else:
            useful.append((start, end, name))
    if effect_starts:
        first_effect = min(effect_starts)
        before_effect = [(s, e, n) for s, e, n in useful if s < first_effect]
        if before_effect:
            return int(clamp(max(e for _s, e, _n in before_effect), 0, 255))
        return int(clamp(first_effect - 1, 0, 255))
    if useful:
        return int(clamp(max(e for _s, e, _n in useful), 0, 255))
    return None


def import_gdtf_channel_mapping(path, start_address=1, preferred_mode=None):
    """Best-effort GDTF DMX attribute extraction for one selected mode.

    Returns (mapping, mode_names, selected_mode_name). Mapping values are
    absolute one-based DMX slots, except shutter_open and iris_100_dmx which are
    DMX values for those attributes. Complex GDTF files may still need manual
    checking against the fixture manual.
    """
    root = _read_gdtf_description(path)
    modes = [node for node in root.iter() if _strip_ns(node.tag) == 'DMXMode']
    mode_names = [_xml_attr(m, 'Name', f'Mode {i+1}') for i, m in enumerate(modes)]
    if not modes:
        raise ValueError('No DMXMode entries found in the GDTF file')
    mode = modes[0]
    if preferred_mode:
        for candidate in modes:
            if _xml_attr(candidate, 'Name', '').lower() == preferred_mode.lower():
                mode = candidate
                break
    selected_mode = _xml_attr(mode, 'Name', mode_names[0] if mode_names else 'default')

    def classify(attr):
        a = attr.lower().replace('_', '').replace('-', '').replace(' ', '')
        if 'pan' in a and 'tilt' not in a:
            return 'pan'
        if 'tilt' in a:
            return 'tilt'
        if 'dimmer' in a or 'intensity' in a:
            return 'dimmer'
        if 'shutter' in a or 'strobe' in a:
            return 'shutter'
        if 'zoom' in a:
            return 'zoom'
        if 'iris' in a:
            return 'iris'
        if 'focus' in a:
            return 'focus'
        return None

    found = {}
    kind_channels = {}
    for dmx_channel in mode.iter():
        if _strip_ns(dmx_channel.tag) != 'DMXChannel':
            continue
        offsets = _parse_gdtf_offsets(_xml_attr(dmx_channel, 'Offset', ''))
        if not offsets:
            continue
        attr_names = [_xml_attr(dmx_channel, 'Attribute', '')]
        for child in dmx_channel.iter():
            if child is dmx_channel:
                continue
            attr_names.append(_xml_attr(child, 'Attribute', ''))
            attr_names.append(_xml_attr(child, 'Name', ''))
        kind = None
        for name in attr_names:
            kind = classify(name)
            if kind:
                break
        if not kind or kind in kind_channels:
            continue
        kind_channels[kind] = dmx_channel
        abs_offsets = [int(start_address) + off - 1 for off in offsets]
        if kind == 'pan':
            found['pan_coarse'] = abs_offsets[0]
            if len(abs_offsets) > 1:
                found['pan_fine'] = abs_offsets[1]
        elif kind == 'tilt':
            found['tilt_coarse'] = abs_offsets[0]
            if len(abs_offsets) > 1:
                found['tilt_fine'] = abs_offsets[1]
        elif kind == 'dimmer':
            found['dimmer'] = abs_offsets[0]
            if len(abs_offsets) > 1:
                found['dimmer_fine'] = abs_offsets[1]
        elif kind == 'shutter':
            found['shutter'] = abs_offsets[0]
        elif kind == 'zoom':
            found['zoom'] = abs_offsets[0]
            if len(abs_offsets) > 1:
                found['zoom_fine'] = abs_offsets[1]
            zoom_angles = _derive_zoom_angles(dmx_channel)
            if zoom_angles:
                found['zoom_angle_at_0'] = round(float(zoom_angles[0]), 4)
                found['zoom_angle_at_100'] = round(float(zoom_angles[1]), 4)
        elif kind == 'iris':
            found['iris'] = abs_offsets[0]
        elif kind == 'focus':
            found['focus'] = abs_offsets[0]
            if len(abs_offsets) > 1:
                found['focus_fine'] = abs_offsets[1]

    if 'shutter' in kind_channels:
        open_value = _derive_shutter_open_value(kind_channels['shutter'])
        if open_value is not None:
            found['shutter_open'] = int(clamp(open_value, 0, 255))
    if 'iris' in kind_channels:
        iris_cap = _derive_iris_100_value(kind_channels['iris'])
        if iris_cap is not None:
            found['iris_100_dmx'] = int(clamp(iris_cap, 0, 255))

    if not found:
        raise ValueError('No usable pan/tilt/dimmer/beam channels were found in that GDTF mode')
    return found, mode_names, selected_mode

def write_fixture_to_frame(frame, fixture, pan, tilt, fader, blackout, zoom=0.5, iris=1.0, focus=0.5):
    plim = clamp(pan, fixture.pan_min, fixture.pan_max)
    tlim = clamp(tilt, fixture.tilt_min, fixture.tilt_max)
    pan_fraction = (plim - fixture.pan_min) / (fixture.pan_max - fixture.pan_min)
    tilt_fraction = (tlim - fixture.tilt_min) / (fixture.tilt_max - fixture.tilt_min)
    pc, pf = dmx16(pan_fraction)
    tc, tf = dmx16(tilt_fraction)

    intensity = 0.0 if blackout else clamp(fader * fixture.intensity_scale, 0.0, 1.0)
    dc, df = dmx16(intensity)
    values = [
        (fixture.pan_coarse, pc),
        (fixture.pan_fine, pf),
        (fixture.tilt_coarse, tc),
        (fixture.tilt_fine, tf),
        (fixture.dimmer, dc),
    ]
    if fixture.dimmer_fine:
        values.append((fixture.dimmer_fine, df))
    if fixture.shutter:
        values.append((fixture.shutter, 0 if blackout else fixture.shutter_open))

    def add_parameter(coarse_channel, fine_channel, value, reverse=False):
        if not coarse_channel:
            return
        fraction = clamp(float(value), 0.0, 1.0)
        if reverse:
            fraction = 1.0 - fraction
        coarse, fine = dmx16(fraction)
        values.append((coarse_channel, coarse))
        if fine_channel:
            values.append((fine_channel, fine))

    add_parameter(fixture.zoom, fixture.zoom_fine, zoom, fixture.zoom_reverse)

    # Iris is intentionally capped by a per-fixture "100%" DMX point.
    # Many fixtures put iris effects/macros above the useful manual iris range.
    if fixture.iris:
        iris_fraction = clamp(float(iris), 0.0, 1.0)
        if fixture.iris_reverse:
            iris_fraction = 1.0 - iris_fraction
        iris_cap = int(clamp(getattr(fixture, 'iris_100_dmx', 255), 0, 255))
        values.append((fixture.iris, round(iris_fraction * iris_cap)))

    add_parameter(fixture.focus, fixture.focus_fine, focus, fixture.focus_reverse)

    for channel, value in values:
        if 1 <= channel <= 512:
            frame[channel - 1] = int(clamp(value, 0, 255))

    return {
        'pan': pan,
        'tilt': tilt,
        'pan_dmx_angle': plim,
        'tilt_dmx_angle': tlim,
        'pan_limit': not math.isclose(pan, plim, abs_tol=1e-9),
        'tilt_limit': not math.isclose(tilt, tlim, abs_tol=1e-9),
    }



def _solve_3x3(matrix, vector):
    """Small dependency-free 3x3 linear solver for calibration."""
    a = [list(map(float, row)) + [float(vector[i])] for i, row in enumerate(matrix)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda row: abs(a[row][col]))
        if abs(a[pivot][col]) < 1e-9:
            raise ValueError('Calibration geometry is degenerate; use wider-spaced target points')
        if pivot != col:
            a[col], a[pivot] = a[pivot], a[col]
        div = a[col][col]
        for j in range(col, 4):
            a[col][j] /= div
        for row in range(3):
            if row == col:
                continue
            factor = a[row][col]
            for j in range(col, 4):
                a[row][j] -= factor * a[col][j]
    return [a[i][3] for i in range(3)]


def _direction_from_bearing_elevation(bearing, elevation):
    b = math.radians(bearing)
    e = math.radians(elevation)
    ce = math.cos(e)
    return (math.sin(b) * ce, math.cos(b) * ce, math.sin(e))


def _closest_point_to_lines(line_points, line_dirs):
    """Return the point closest to all 3D lines using normal equations."""
    m = [[0.0, 0.0, 0.0] for _ in range(3)]
    v = [0.0, 0.0, 0.0]
    for point, direction in zip(line_points, line_dirs):
        dx, dy, dz = direction
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        if length < 1e-9:
            continue
        dx, dy, dz = dx / length, dy / length, dz / length
        # Projection matrix onto the plane perpendicular to the line.
        proj = [
            [1.0 - dx * dx, -dx * dy, -dx * dz],
            [-dy * dx, 1.0 - dy * dy, -dy * dz],
            [-dz * dx, -dz * dy, 1.0 - dz * dz],
        ]
        px, py, pz = point
        for r in range(3):
            for c in range(3):
                m[r][c] += proj[r][c]
            v[r] += proj[r][0] * px + proj[r][1] * py + proj[r][2] * pz
    return _solve_3x3(m, v)


def solve_fixture_calibration(base_fixture: FixtureConfig, samples):
    """Estimate fixture position and physical pan/tilt mapping from aimed samples.

    This solver converts each captured pan/tilt value into a 3D ray aimed at a
    known point. It then triangulates the fixture position from the reverse rays.
    v1.0.2 also tries pan/tilt direction combinations, rejects high-error
    solutions, and returns the direction combination that best matches the data.
    """
    if len(samples) < 4:
        raise ValueError('At least four calibration points are required')
    points = [(float(x), float(y), float(z), float(pan), float(tilt)) for x, y, z, pan, tilt in samples]
    target_points = [(p[0], p[1], p[2]) for p in points]
    min_x, max_x = min(p[0] for p in points), max(p[0] for p in points)
    min_y, max_y = min(p[1] for p in points), max(p[1] for p in points)
    min_z, max_z = min(p[2] for p in points), max(p[2] for p in points)

    xy_margin = 60.0
    # Keep the height range generous, but not infinite. False solutions usually
    # show up as huge height errors or multi-metre ray fit errors.
    if base_fixture.z > max_z + 1.0 and base_fixture.z < 60.0:
        z_low = max(-2.0, min(max_z + 0.2, base_fixture.z - 20.0))
        z_high = max(max_z + 35.0, base_fixture.z + 20.0)
    else:
        z_low = -2.0
        z_high = max(max_z + 35.0, 30.0)

    def make_fixture(position, pan_zero, tilt_zero, pan_dir, tilt_dir):
        f = FixtureConfig(**asdict(base_fixture))
        f.x, f.y, f.z = position
        f.pan_zero_bearing = wrap180(pan_zero)
        f.tilt_zero_elevation = tilt_zero
        f.pan_direction = 1 if pan_dir >= 0 else -1
        f.tilt_direction = 1 if tilt_dir >= 0 else -1
        f.pan_offset = 0.0
        f.tilt_offset = 0.0
        return f

    def candidate_from_zeros(pan_zero, tilt_zero, pan_dir, tilt_dir):
        dirs = []
        for _tx, _ty, _tz, observed_pan, observed_tilt in points:
            bearing = pan_zero + (observed_pan - base_fixture.pan_offset) / (pan_dir or 1)
            elevation = tilt_zero + (observed_tilt - base_fixture.tilt_offset) / (tilt_dir or 1)
            dirs.append(_direction_from_bearing_elevation(bearing, elevation))
        position = _closest_point_to_lines(target_points, dirs)
        return position, dirs

    def score_candidate(position, dirs, pan_zero, tilt_zero, pan_dir, tilt_dir):
        x, y, z = position
        ray_total = 0.0
        total = 0.0
        for (tx, ty, tz, observed_pan, observed_tilt), direction in zip(points, dirs):
            vx, vy, vz = tx - x, ty - y, tz - z
            dx, dy, dz = direction
            along = vx * dx + vy * dy + vz * dz
            closest = (x + dx * along, y + dy * along, z + dz * along)
            err = math.dist((tx, ty, tz), closest)
            ray_total += err * err
            total += err * err
            if along <= 0:
                total += 5000.0 + abs(along) * 100.0
            f = make_fixture(position, pan_zero, tilt_zero, pan_dir, tilt_dir)
            try:
                _b, _e, pan, tilt, _d = calculate_aim(f, tx, ty, tz, observed_pan)
                total += (wrap180(pan - observed_pan) * 0.03) ** 2 + ((tilt - observed_tilt) * 0.03) ** 2
            except Exception:
                total += 1e6
        if not (min_x - xy_margin <= x <= max_x + xy_margin):
            total += (min(abs(x - (min_x - xy_margin)), abs(x - (max_x + xy_margin))) * 50.0) ** 2
        if not (min_y - xy_margin <= y <= max_y + xy_margin):
            total += (min(abs(y - (min_y - xy_margin)), abs(y - (max_y + xy_margin))) * 50.0) ** 2
        if z < z_low:
            total += ((z_low - z) * 80.0) ** 2
        if z > z_high:
            total += ((z - z_high) * 80.0) ** 2
        # Very light preference for the user's seed height when it is plausible.
        if -2.0 <= base_fixture.z <= 60.0 and base_fixture.z > max_z + 0.5:
            total += ((z - base_fixture.z) / 25.0) ** 2
        ray_rms = math.sqrt(ray_total / max(1, len(points)))
        return total / len(points), ray_rms

    def loss_for_zeros(pan_zero, tilt_zero, pan_dir, tilt_dir):
        try:
            position, dirs = candidate_from_zeros(pan_zero, tilt_zero, pan_dir, tilt_dir)
            loss, ray_rms = score_candidate(position, dirs, pan_zero, tilt_zero, pan_dir, tilt_dir)
            return loss, ray_rms, position
        except Exception:
            return 1e12, float('inf'), None

    base_pan_dir = 1 if base_fixture.pan_direction >= 0 else -1
    base_tilt_dir = 1 if base_fixture.tilt_direction >= 0 else -1
    direction_candidates = []
    for combo in ((base_pan_dir, base_tilt_dir), (-base_pan_dir, base_tilt_dir), (base_pan_dir, -base_tilt_dir), (-base_pan_dir, -base_tilt_dir)):
        if combo not in direction_candidates:
            direction_candidates.append(combo)

    starts = []
    for p0 in (base_fixture.pan_zero_bearing, 0.0, 90.0, -90.0, 180.0, -180.0, 30.0, -30.0):
        for t0 in (base_fixture.tilt_zero_elevation, -90.0, -45.0, 0.0, 45.0, 90.0):
            starts.append((wrap180(p0), t0))

    best = None
    best_loss = float('inf')
    for pan_dir, tilt_dir in direction_candidates:
        for start_pan, start_tilt in starts:
            params = [float(start_pan), float(start_tilt)]
            steps = [45.0, 30.0]
            current, ray_rms, pos = loss_for_zeros(params[0], params[1], pan_dir, tilt_dir)
            for _ in range(160):
                improved = False
                for i in range(2):
                    for sign in (1.0, -1.0):
                        trial = params[:]
                        trial[i] += steps[i] * sign
                        if i == 0:
                            trial[i] = wrap180(trial[i])
                        value, trial_rms, trial_pos = loss_for_zeros(trial[0], trial[1], pan_dir, tilt_dir)
                        if value < current:
                            params, current, ray_rms, pos, improved = trial, value, trial_rms, trial_pos, True
                if not improved:
                    steps = [step * 0.55 for step in steps]
                    if max(steps) < 0.001:
                        break
            if current < best_loss and pos is not None:
                best = (params[:], pos, pan_dir, tilt_dir, ray_rms)
                best_loss = current

    if best is None:
        raise ValueError('Calibration failed; check captured points and pan/tilt directions')
    (pan_zero, tilt_zero), position, pan_dir, tilt_dir, ray_rms = best
    x, y, z = position
    if z > z_high + 0.5 or z < z_low - 0.5:
        raise ValueError(
            f'Calibration result was physically implausible: Z={z:.2f} m. '
            'Check tilt direction and capture points, or seed the fixture with an approximate height first.'
        )
    if ray_rms > 1.0:
        raise ValueError(
            f'Calibration did not fit well enough to apply safely: fit error {ray_rms:.2f} m. '
            'Recapture wider-spaced points, check the selected fixture, and confirm pan/tilt channels are not swapped.'
        )
    solved = make_fixture(position, pan_zero, tilt_zero, pan_dir, tilt_dir)
    solved.x = round(solved.x, 4)
    solved.y = round(solved.y, 4)
    solved.z = round(solved.z, 4)
    solved.pan_zero_bearing = round(wrap180(solved.pan_zero_bearing), 4)
    solved.tilt_zero_elevation = round(solved.tilt_zero_elevation, 4)
    solved.pan_offset = 0.0
    solved.tilt_offset = 0.0
    return solved, ray_rms



class DMXSetupDialog(tk.Toplevel):
    """Collect the minimum DMX mapping required before live calibration."""

    CHANNEL_FIELDS = [
        ('pan_coarse', 'Pan coarse', True),
        ('pan_fine', 'Pan fine', False),
        ('tilt_coarse', 'Tilt coarse', True),
        ('tilt_fine', 'Tilt fine', False),
        ('dimmer', 'Dimmer coarse', True),
        ('dimmer_fine', 'Dimmer fine', False),
        ('shutter', 'Shutter', False),
        ('shutter_open', 'Shutter open value', False),
        ('zoom', 'Zoom coarse', False),
        ('zoom_fine', 'Zoom fine', False),
        ('zoom_angle_at_0', 'Beam angle at zoom 0%', False),
        ('zoom_angle_at_100', 'Beam angle at zoom 100%', False),
        ('iris', 'Iris', False),
        ('iris_100_dmx', 'Iris 100% DMX', False),
        ('focus', 'Focus coarse', False),
        ('focus_fine', 'Focus fine', False),
    ]

    def __init__(self, app, light_index, on_complete=None):
        super().__init__(app)
        self.app = app
        self.light_index = light_index
        self.on_complete = on_complete
        self.fixture = FixtureConfig(**asdict(app.settings.fixtures[light_index]))
        self.title(f'{APP_SHORT_NAME} DMX setup')
        self.geometry('560x650')
        self.transient(app)
        self.grab_set()
        self.vars = {}
        self.build_ui()

    def build_ui(self):
        intro = ttk.LabelFrame(self, text='DMX setup before calibration')
        intro.pack(fill='x', padx=10, pady=8)
        ttk.Label(intro, text=(
            'Calibration faders drive the real fixture, so FART needs the fixture DMX channels first.\n'
            'Set at least pan coarse, tilt coarse, and dimmer. Fine channels and shutter are optional but recommended.'
        ), justify='left').pack(anchor='w', padx=8, pady=8)

        out = ttk.LabelFrame(self, text='Output currently selected')
        out.pack(fill='x', padx=10, pady=5)
        ttk.Label(out, text=f'Output: {self.app.vars["output"].get()}   Universe: {self.app.vars["universe"].get()}').pack(anchor='w', padx=8, pady=6)
        ttk.Label(out, text='Change Art-Net/sACN/Open DMX output settings on the Setup tab before starting calibration output.').pack(anchor='w', padx=8, pady=(0, 6))

        frame = ttk.LabelFrame(self, text=f'Channels for {self.fixture.name}')
        frame.pack(fill='both', expand=True, padx=10, pady=8)
        for row, (field, label, required) in enumerate(self.CHANNEL_FIELDS):
            ttk.Label(frame, text=label + (' *' if required else '')).grid(row=row, column=0, sticky='w', padx=8, pady=4)
            if field in ('zoom_angle_at_0', 'zoom_angle_at_100'):
                var = tk.DoubleVar(value=float(getattr(self.fixture, field)))
            else:
                var = tk.IntVar(value=int(getattr(self.fixture, field)))
            self.vars[field] = var
            ttk.Entry(frame, textvariable=var, width=10).grid(row=row, column=1, sticky='w', padx=8, pady=4)
        ttk.Label(frame, text='* Required for calibration. Use 0 to disable optional channels.').grid(row=len(self.CHANNEL_FIELDS), column=0, columnspan=2, sticky='w', padx=8, pady=8)
        ttk.Button(frame, text='Import channels from GDTF…', command=self.import_gdtf).grid(row=len(self.CHANNEL_FIELDS)+1, column=0, columnspan=2, sticky='ew', padx=8, pady=(0, 8))

        buttons = ttk.Frame(self)
        buttons.pack(fill='x', padx=10, pady=(0, 10))
        ttk.Button(buttons, text='Save DMX and continue', command=self.save_continue).pack(side='right', padx=4)
        ttk.Button(buttons, text='Cancel', command=self.destroy).pack(side='right', padx=4)

    def import_gdtf(self):
        try:
            path = filedialog.askopenfilename(
                title='Select GDTF fixture file',
                filetypes=[('GDTF fixture', '*.gdtf'), ('Zip files', '*.zip'), ('All files', '*.*')],
                parent=self,
            )
            if not path:
                return
            start = simpledialog.askinteger(
                APP_NAME,
                'Fixture start DMX address?\n\nExample: for patch 1.101 enter 101.',
                initialvalue=max(1, min([int(v.get()) for k, v in self.vars.items() if k != 'shutter_open' and int(v.get()) > 0] or [1])),
                minvalue=1,
                maxvalue=512,
                parent=self,
            )
            if not start:
                return
            mode = select_gdtf_mode(self, path)
            if not mode:
                return
            mapping, modes, selected_mode = import_gdtf_channel_mapping(path, start, mode)
            for field, channel in mapping.items():
                if field in self.vars:
                    self.vars[field].set(float(channel) if field in ('zoom_angle_at_0', 'zoom_angle_at_100') else int(channel))
            found = ', '.join(f'{k}={v}' for k, v in mapping.items())
            messagebox.showinfo(APP_NAME, f'Imported GDTF channel mapping. Check it against the manual.\n\nMode used: {modes[0] if modes else "default"}\n{found}', parent=self)
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)

    def save_continue(self):
        try:
            fixture = FixtureConfig(**asdict(self.fixture))
            for field, _label, _required in self.CHANNEL_FIELDS:
                if field in ('zoom_angle_at_0', 'zoom_angle_at_100'):
                    value = float(self.vars[field].get())
                    if value < 0:
                        raise ValueError(f'{_label} cannot be negative')
                    setattr(fixture, field, value)
                else:
                    setattr(fixture, field, int(self.vars[field].get()))
            self.app.require_calibration_dmx(fixture)
            self.app.validate_fixture(fixture)
            # Check conflicts against all other enabled fixtures before applying.
            fixtures = [FixtureConfig(**asdict(f)) for f in self.app.settings.fixtures]
            fixtures[self.light_index] = fixture
            self.app.validate_channel_conflicts(fixtures)
            self.app.settings.fixtures[self.light_index] = fixture
            self.app.rebuild_light_tree(self.light_index)
            self.app.load_light_editor(self.light_index)
            self.app.apply_selected_light(silent=True)
            self.app.log(f'DMX channels set for {fixture.name}; calibration can now start')
            self.destroy()
            if self.on_complete:
                self.app.after(100, self.on_complete)
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)


class CalibrationWizard(tk.Toplevel):
    DEFAULT_TARGETS = [
        ('Centre floor', 0.0, 0.0, 0.0),
        ('Stage right floor', 5.0, 0.0, 0.0),
        ('Stage left floor', -5.0, 0.0, 0.0),
        ('Upstage floor', 0.0, 5.0, 0.0),
        ('Downstage floor', 0.0, -5.0, 0.0),
        ('Centre at head height', 0.0, 0.0, 1.7),
    ]

    def __init__(self, app, light_indices):
        super().__init__(app)
        self.app = app
        if isinstance(light_indices, int):
            light_indices = [light_indices]
        self.light_indices = [int(i) for i in light_indices]
        self.title(f'{APP_SHORT_NAME} calibration wizard')
        self.geometry('1050x760')
        self.transient(app)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.fixtures = {
            idx: FixtureConfig(**asdict(app.settings.fixtures[idx]))
            for idx in self.light_indices
        }
        self.targets = list(self.DEFAULT_TARGETS)
        self.samples = {idx: [] for idx in self.light_indices}
        self.output = None
        self.output_running = False
        self.current_target = tk.IntVar(value=0)
        self.control_vars = {}
        for idx, fixture in self.fixtures.items():
            self.control_vars[idx] = {
                'pan': tk.DoubleVar(value=(fixture.pan_min + fixture.pan_max) / 2.0),
                'tilt': tk.DoubleVar(value=(fixture.tilt_min + fixture.tilt_max) / 2.0),
                'level': tk.DoubleVar(value=0.15),
                'zoom': tk.DoubleVar(value=float(getattr(app, 'zoom_value', 0.5))),
                'iris': tk.DoubleVar(value=float(getattr(app, 'iris_value', 1.0))),
            }
        self.status_var = tk.StringVar(value='Output stopped')
        self.solution_var = tk.StringVar(value='No solution yet')
        self.build_ui()
        self.after(80, self.output_tick)

    def build_ui(self):
        intro = ttk.LabelFrame(self, text='Fixture-position calibration')
        intro.pack(fill='x', padx=10, pady=8)
        ttk.Label(intro, text=(
            'Select a known XYZ point, then aim every selected fixture at that same point. '
            'Use coarse faders plus fine buttons for accurate pan/tilt. Capture the point, repeat for several points, then solve.\n'
            'Zoom and iris are available here so you can calibrate with a small, clean beam. Keep dimmer low while aiming.'
        ), justify='left').pack(anchor='w', padx=8, pady=8)
        body = ttk.Frame(self)
        body.pack(fill='both', expand=True, padx=10, pady=6)
        left = ttk.LabelFrame(body, text='Known points')
        left.pack(side='left', fill='y', padx=(0,8))
        self.target_list = tk.Listbox(left, height=12, exportselection=False, width=34)
        self.target_list.pack(fill='both', expand=True, padx=6, pady=6)
        for name, x, y, z in self.targets:
            self.target_list.insert('end', f'{name}: X {x:g}, Y {y:g}, Z {z:g}')
        self.target_list.selection_set(0)
        self.target_list.bind('<<ListboxSelect>>', self.target_selected)
        custom = ttk.LabelFrame(left, text='Add custom point')
        custom.pack(fill='x', padx=6, pady=6)
        self.custom_x = tk.DoubleVar(value=0.0); self.custom_y = tk.DoubleVar(value=0.0); self.custom_z = tk.DoubleVar(value=0.0)
        for row, (label, var) in enumerate((('X', self.custom_x), ('Y', self.custom_y), ('Z', self.custom_z))):
            ttk.Label(custom, text=label).grid(row=row, column=0, sticky='w', padx=4, pady=2)
            ttk.Entry(custom, textvariable=var, width=10).grid(row=row, column=1, padx=4, pady=2)
        ttk.Button(custom, text='Add point', command=self.add_custom_point).grid(row=3, column=0, columnspan=2, sticky='ew', padx=4, pady=4)
        ttk.Label(left, text='Tip: spread points across the area.\nUse left, right, upstage, downstage,\ncentre and at least one raised point.', justify='left').pack(anchor='w', padx=8, pady=8)

        right = ttk.LabelFrame(body, text='Aim selected fixtures')
        right.pack(side='left', fill='both', expand=True)
        self.target_label = ttk.Label(right, text='', font=('Segoe UI', 12, 'bold'))
        self.target_label.pack(anchor='w', padx=8, pady=8)
        self.update_target_label()

        self.rows_frame = ttk.Frame(right)
        self.rows_frame.pack(fill='both', expand=True, padx=8, pady=4)
        header = ttk.Frame(self.rows_frame)
        header.pack(fill='x')
        for w, text in ((18, 'Fixture'), (36, 'Pan'), (36, 'Tilt'), (20, 'Dimmer'), (20, 'Zoom'), (20, 'Iris')):
            ttk.Label(header, text=text, width=w).pack(side='left', padx=2)
        self.readout_labels = {}
        for idx in self.light_indices:
            self.add_fixture_control_row(idx)

        out_buttons = ttk.Frame(right); out_buttons.pack(fill='x', padx=8, pady=6)
        ttk.Button(out_buttons, text='Start calibration output', command=self.start_output).pack(side='left', padx=3)
        ttk.Button(out_buttons, text='Stop output / blackout', command=self.stop_output).pack(side='left', padx=3)
        ttk.Label(out_buttons, textvariable=self.status_var).pack(side='left', padx=12)
        cap_buttons = ttk.Frame(right); cap_buttons.pack(fill='x', padx=8, pady=8)
        ttk.Button(cap_buttons, text='Capture point for all fixtures', command=self.capture).pack(side='left', padx=3)
        ttk.Button(cap_buttons, text='Solve and apply all', command=self.solve_apply).pack(side='left', padx=3)
        ttk.Button(cap_buttons, text='Close', command=self.close).pack(side='left', padx=3)
        self.sample_tree = ttk.Treeview(right, columns=('fixture','target','pan','tilt'), show='headings', height=9)
        for col, text, width in (('fixture','Fixture',150), ('target','Captured point',300), ('pan','Pan',80), ('tilt','Tilt',80)):
            self.sample_tree.heading(col, text=text); self.sample_tree.column(col, width=width, anchor='e' if col in ('pan','tilt') else 'w')
        self.sample_tree.pack(fill='both', expand=True, padx=8, pady=6)
        ttk.Label(right, textvariable=self.solution_var, justify='left').pack(anchor='w', padx=8, pady=6)
        self.update_readouts()

    def add_fixture_control_row(self, idx):
        fixture = self.fixtures[idx]
        vars_ = self.control_vars[idx]
        row = ttk.LabelFrame(self.rows_frame, text=fixture.name)
        row.pack(fill='x', pady=3)
        ttk.Label(row, text=fixture.name, width=18).pack(side='left', padx=2)
        self._angle_control(row, idx, 'pan', fixture.pan_min, fixture.pan_max, 36).pack(side='left', fill='x', expand=True, padx=2)
        self._angle_control(row, idx, 'tilt', fixture.tilt_min, fixture.tilt_max, 36).pack(side='left', fill='x', expand=True, padx=2)
        self._simple_control(row, idx, 'level', 0.0, 1.0, 20).pack(side='left', padx=2)
        self._simple_control(row, idx, 'zoom', 0.0, 1.0, 20).pack(side='left', padx=2)
        self._simple_control(row, idx, 'iris', 0.0, 1.0, 20).pack(side='left', padx=2)

    def _angle_control(self, parent, idx, key, low, high, width):
        box = ttk.Frame(parent, width=width*6)
        scale = ttk.Scale(box, from_=low, to=high, variable=self.control_vars[idx][key], orient='horizontal')
        scale.pack(fill='x', expand=True)
        buttons = ttk.Frame(box); buttons.pack(fill='x')
        for delta in (-1.0, -0.1, 0.1, 1.0):
            ttk.Button(buttons, text=f'{delta:+g}', width=4, command=lambda d=delta, i=idx, k=key: self.nudge_angle(i, k, d)).pack(side='left', padx=1)

        # Direct numeric entry is intentionally beside the fine controls so
        # users can type values from a console/DMX monitor instead of trying
        # to land a slider exactly. The entry commits on Enter or focus loss
        # and clamps to the fixture's configured mechanical range.
        entry = ttk.Entry(buttons, textvariable=self.control_vars[idx][key], width=9)
        entry.pack(side='right', padx=(2, 0))
        entry.bind('<Return>', lambda _e, i=idx, k=key: self.commit_angle_entry(i, k))
        entry.bind('<FocusOut>', lambda _e, i=idx, k=key: self.commit_angle_entry(i, k))
        ttk.Label(buttons, text='°').pack(side='right')

        lbl = ttk.Label(buttons, width=9)
        lbl.pack(side='right', padx=(4, 0))
        self.readout_labels[(idx, key)] = lbl
        return box

    def _simple_control(self, parent, idx, key, low, high, width):
        box = ttk.Frame(parent, width=width*6)
        ttk.Scale(box, from_=low, to=high, variable=self.control_vars[idx][key], orient='horizontal').pack(fill='x', expand=True)
        lbl = ttk.Label(box, width=8)
        lbl.pack(anchor='e')
        self.readout_labels[(idx, key)] = lbl
        return box

    def angle_limits(self, idx, key):
        fixture = self.fixtures[idx]
        if key == 'pan':
            return fixture.pan_min, fixture.pan_max
        return fixture.tilt_min, fixture.tilt_max

    def commit_angle_entry(self, idx, key):
        var = self.control_vars[idx][key]
        low, high = self.angle_limits(idx, key)
        try:
            value = float(var.get())
        except Exception:
            messagebox.showerror(APP_NAME, f'Invalid {key} value. Enter a number between {low:g} and {high:g}.', parent=self)
            value = clamp(0.0, low, high)
        var.set(round(clamp(value, low, high), 4))

    def nudge_angle(self, idx, key, delta):
        var = self.control_vars[idx][key]
        low, high = self.angle_limits(idx, key)
        try:
            current = float(var.get())
        except Exception:
            current = 0.0
        var.set(round(clamp(current + delta, low, high), 4))

    def update_readouts(self):
        if self.winfo_exists():
            for idx in self.light_indices:
                vars_ = self.control_vars[idx]
                for key in ('pan', 'tilt'):
                    self.readout_labels[(idx, key)].configure(text=f'{vars_[key].get():.2f}°')
                for key in ('level', 'zoom', 'iris'):
                    self.readout_labels[(idx, key)].configure(text=f'{vars_[key].get()*100:.0f}%')
            self.after(80, self.update_readouts)

    def target_selected(self, _event=None):
        sel = self.target_list.curselection()
        if sel:
            self.current_target.set(sel[0]); self.update_target_label()

    def update_target_label(self):
        name, x, y, z = self.targets[self.current_target.get()]
        self.target_label.configure(text=f'Aim all fixtures at: {name}   X {x:g}, Y {y:g}, Z {z:g}')

    def add_custom_point(self):
        try:
            point = ('Custom', float(self.custom_x.get()), float(self.custom_y.get()), float(self.custom_z.get()))
            self.targets.append(point)
            self.target_list.insert('end', f'Custom: X {point[1]:g}, Y {point[2]:g}, Z {point[3]:g}')
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)

    def start_output(self):
        if self.app.running:
            messagebox.showerror(APP_NAME, 'Stop the main FART output before calibration output.', parent=self); return
        try:
            settings = self.app.collect()
            for idx in self.light_indices:
                self.fixtures[idx] = FixtureConfig(**asdict(settings.fixtures[idx]))
            self.output = self.app.make_output(settings)
            self.output_running = True
            self.status_var.set('Calibration output running')
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)

    def stop_output(self):
        self.output_running = False
        if self.output:
            try:
                self.output.send(bytes(512)); self.output.close()
            except Exception:
                pass
            self.output = None
        self.status_var.set('Output stopped / blackout sent')

    def output_tick(self):
        if self.output_running and self.output:
            try:
                frame = bytearray(512)
                for idx in self.light_indices:
                    fixture = self.fixtures[idx]
                    vars_ = self.control_vars[idx]
                    write_fixture_to_frame(frame, fixture, vars_['pan'].get(), vars_['tilt'].get(), vars_['level'].get(), False,
                                           vars_['zoom'].get(), vars_['iris'].get(), self.app.focus_value)
                self.output.send(bytes(frame))
            except Exception as exc:
                self.status_var.set('Output error: ' + str(exc)); self.output_running = False
        if self.winfo_exists():
            self.after(33, self.output_tick)

    def capture(self):
        name, x, y, z = self.targets[self.current_target.get()]
        for idx in self.light_indices:
            vars_ = self.control_vars[idx]
            sample = (x, y, z, float(vars_['pan'].get()), float(vars_['tilt'].get()))
            self.samples[idx].append(sample)
            self.sample_tree.insert('', 'end', values=(self.fixtures[idx].name, f'{name} ({x:g}, {y:g}, {z:g})', f'{sample[3]:.3f}', f'{sample[4]:.3f}'))
        counts = ', '.join(f'{self.fixtures[idx].name}: {len(self.samples[idx])}' for idx in self.light_indices)
        self.solution_var.set(f'Captured point for all selected fixtures. Samples: {counts}. Capture at least 4 per fixture, preferably 5–6.')

    def solve_apply(self):
        try:
            messages = []
            first_index = self.light_indices[0]
            for idx in self.light_indices:
                solved, rms = solve_fixture_calibration(self.fixtures[idx], self.samples[idx])
                self.app.settings.fixtures[idx] = solved
                self.app.insert_or_update_light_row(idx, solved)
                messages.append(f'{solved.name}: XYZ=({solved.x:.3f}, {solved.y:.3f}, {solved.z:.3f}), pan zero={solved.pan_zero_bearing:.3f}, tilt zero={solved.tilt_zero_elevation:.3f}, pan dir={solved.pan_direction:+d}, tilt dir={solved.tilt_direction:+d}, fit={rms:.3f} m')
                self.app.log('Calibration applied to ' + messages[-1])
            self.app.rebuild_light_tree(first_index)
            self.app.load_light_editor(first_index)
            self.solution_var.set('Applied solutions:\n' + '\n'.join(messages))
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)

    def close(self):
        self.stop_output(); self.destroy()


class App(tk.Tk):
    GENERAL_TYPES = {
        'marker_id': int,
        'psn_multicast': str,
        'psn_port': int,
        'psn_interface': str,
        'fader_mode': str,
        'manual_fader': float,
        'osc_fader_port': int,
        'osc_fader_address': str,
        'osc_fader_arg': int,
        'osc_fader_min': float,
        'osc_fader_max': float,
        'artnet_input_universe': int,
        'artnet_input_channel': int,
        'output': str,
        'serial_port': str,
        'artnet_ip': str,
        'universe': int,
        'refresh_hz': int,
        'timeout_s': float,
        'smoothing': float,
        'zoom_master': float,
        'iris_master': float,
        'focus_master': float,
        'zoom_mode': str,
        'auto_beam_diameter_m': float,
    }

    FIXTURE_TYPES = {
        'name': str,
        'enabled': bool,
        'marker_id': int,
        'x': float,
        'y': float,
        'z': float,
        'pan_zero_bearing': float,
        'tilt_zero_elevation': float,
        'pan_direction': int,
        'tilt_direction': int,
        'pan_offset': float,
        'tilt_offset': float,
        'pan_min': float,
        'pan_max': float,
        'tilt_min': float,
        'tilt_max': float,
        'pan_coarse': int,
        'pan_fine': int,
        'tilt_coarse': int,
        'tilt_fine': int,
        'dimmer': int,
        'dimmer_fine': int,
        'shutter': int,
        'shutter_open': int,
        'intensity_scale': float,
        'zoom': int,
        'zoom_fine': int,
        'iris': int,
        'iris_100_dmx': int,
        'focus': int,
        'focus_fine': int,
        'zoom_reverse': bool,
        'iris_reverse': bool,
        'focus_reverse': bool,
        'zoom_angle_at_0': float,
        'zoom_angle_at_100': float,
    }

    def __init__(self):
        super().__init__()
        self.title(WINDOW_TITLE)
        self.geometry('1240x820')
        self.minsize(1120, 760)
        self.configure_theme()

        self.settings = self.load_settings()
        self.trackers = TrackerBank()
        self.fader = FaderState()
        self.running = False
        self.psn = None
        self.psn_scanner = None
        self.fader_input = None
        self.output = None
        self.worker = None
        self.stop_evt = threading.Event()
        self.logs = queue.Queue()
        self.psn_discovered = queue.Queue()
        self.psn_tracker_ids = set()
        self.manual_fader_value = self.settings.manual_fader
        self.zoom_value = self.settings.zoom_master
        self.iris_value = self.settings.iris_master
        self.focus_value = self.settings.focus_master
        self.vars = {}
        self.light_vars = {}
        self.selected_light_index = 0
        self.loading_light_editor = False
        self.live = None

        self.build_ui()
        self.populate()
        self.after(100, self.ui_tick)
        self.protocol('WM_DELETE_WINDOW', self.on_close)


    def configure_theme(self):
        """Apply a calm, higher-contrast ttk theme without external assets."""
        try:
            style = ttk.Style(self)
            if 'vista' in style.theme_names():
                style.theme_use('vista')
            elif 'clam' in style.theme_names():
                style.theme_use('clam')
            default_font = ('Segoe UI', 9)
            heading_font = ('Segoe UI', 9, 'bold')
            self.option_add('*Font', default_font)
            style.configure('TNotebook.Tab', padding=(14, 7))
            style.configure('TLabelframe', padding=(8, 6))
            style.configure('TLabelframe.Label', font=heading_font)
            style.configure('Treeview', rowheight=24)
            style.configure('Treeview.Heading', font=heading_font)
            style.configure('Danger.TButton', padding=(8, 5))
            style.configure('Primary.TButton', padding=(8, 5))
        except Exception:
            pass

    def load_settings(self):
        source_path = CONFIG_FILE if CONFIG_FILE.exists() else LEGACY_CONFIG_FILE
        try:
            data = json.loads(source_path.read_text())
        except Exception:
            return Settings()

        try:
            fixtures_data = data.get('fixtures')
            fixtures = []
            if isinstance(fixtures_data, list):
                for item in fixtures_data:
                    if isinstance(item, dict):
                        filtered = {k: v for k, v in item.items() if k in FixtureConfig.__dataclass_fields__}
                        if 'marker_id' not in filtered:
                            filtered['marker_id'] = int(data.get('marker_id', 1))
                        fixtures.append(FixtureConfig(**filtered))

            # Migrate all pre-0.3 single-light settings automatically.
            if not fixtures:
                old_map = {
                    'fixture_x': 'x', 'fixture_y': 'y', 'fixture_z': 'z',
                    'pan_zero_bearing': 'pan_zero_bearing',
                    'tilt_zero_elevation': 'tilt_zero_elevation',
                    'pan_direction': 'pan_direction', 'tilt_direction': 'tilt_direction',
                    'pan_offset': 'pan_offset', 'tilt_offset': 'tilt_offset',
                    'pan_min': 'pan_min', 'pan_max': 'pan_max',
                    'tilt_min': 'tilt_min', 'tilt_max': 'tilt_max',
                    'pan_coarse': 'pan_coarse', 'pan_fine': 'pan_fine',
                    'tilt_coarse': 'tilt_coarse', 'tilt_fine': 'tilt_fine',
                    'dimmer': 'dimmer', 'shutter': 'shutter',
                    'shutter_open': 'shutter_open',
                }
                migrated = {'name': 'Light 1', 'marker_id': int(data.get('marker_id', 1))}
                for old, new in old_map.items():
                    if old in data:
                        migrated[new] = data[old]
                fixtures = [FixtureConfig(**migrated)]

            general = {
                k: v for k, v in data.items()
                if k in Settings.__dataclass_fields__ and k != 'fixtures'
            }
            settings = Settings(**general, fixtures=fixtures)

            # Preserve existing users' settings when upgrading from the old
            # OpenFollow Followspot name. The legacy file is left untouched.
            if source_path == LEGACY_CONFIG_FILE and not CONFIG_FILE.exists():
                try:
                    CONFIG_FILE.write_text(json.dumps(asdict(settings), indent=2))
                except Exception:
                    pass
            return settings
        except Exception:
            return Settings()

    def log(self, message):
        self.logs.put(f"{time.strftime('%H:%M:%S')}  {message}")

    def var(self, name, typ=str):
        variable = {str: tk.StringVar, int: tk.IntVar, float: tk.DoubleVar, bool: tk.BooleanVar}[typ]()
        self.vars[name] = variable
        return variable

    def light_var(self, name, typ=str):
        variable = {str: tk.StringVar, int: tk.IntVar, float: tk.DoubleVar, bool: tk.BooleanVar}[typ]()
        self.light_vars[name] = variable
        return variable

    def add_entry(self, parent, row, label, name, typ=float, width=12, column=0):
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky='w', padx=4, pady=3)
        ttk.Entry(parent, textvariable=self.var(name, typ), width=width).grid(
            row=row, column=column + 1, sticky='ew', padx=4, pady=3
        )

    def add_light_entry(self, parent, row, label, name, typ=float, width=12, column=0):
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky='w', padx=4, pady=3)
        ttk.Entry(parent, textvariable=self.light_var(name, typ), width=width).grid(
            row=row, column=column + 1, sticky='ew', padx=4, pady=3
        )

    def build_ui(self):
        header = ttk.Frame(self)
        header.pack(fill='x', padx=12, pady=(10, 0))
        ttk.Label(header, text='FART', font=('Segoe UI', 22, 'bold')).pack(side='left')
        ttk.Label(
            header, text='  Fixture Aiming and Remote Tracking — PSN to DMX followspot control',
            font=('Segoe UI', 11)
        ).pack(side='left', anchor='s', pady=(0, 5))
        ttk.Label(header, text=f'v{APP_VERSION}').pack(side='right', anchor='s', pady=(0, 4))

        notebook = ttk.Notebook(self)
        self.notebook = notebook
        notebook.pack(fill='both', expand=True, padx=8, pady=8)
        run_tab = ttk.Frame(notebook)
        lights_tab = ttk.Frame(notebook)
        io_tab = ttk.Frame(notebook)
        calibration_tab = ttk.Frame(notebook)
        notebook.add(run_tab, text='Operator')
        notebook.add(lights_tab, text='Setup: Lights')
        notebook.add(io_tab, text='Setup: I/O')
        notebook.add(calibration_tab, text='Setup: Calibration')

        # Run tab
        connection = ttk.LabelFrame(run_tab, text='Tracking and output')
        connection.pack(side='left', fill='y', padx=8, pady=8)
        ttk.Label(connection, text='Default PSN tracker').grid(row=0, column=0, sticky='w', padx=4, pady=3)
        self.psn_tracker_combo = ttk.Combobox(connection, textvariable=self.var('marker_id', int), width=16)
        self.psn_tracker_combo.grid(row=0, column=1, sticky='ew', padx=4, pady=3)
        self.add_entry(connection, 1, 'PSN multicast', 'psn_multicast', str, 18)
        self.add_entry(connection, 2, 'PSN UDP port', 'psn_port', int)
        self.add_entry(connection, 3, 'PSN interface IP', 'psn_interface', str, 18)
        ttk.Button(connection, text='Auto-detect PSN trackers', command=self.scan_psn).grid(
            row=4, column=0, columnspan=2, sticky='ew', padx=4, pady=4
        )
        self.psn_detect_label = ttk.Label(connection, text='No scan run', justify='left')
        self.psn_detect_label.grid(row=5, column=0, columnspan=2, sticky='w', padx=4, pady=(0, 4))
        ttk.Label(connection, text='Fader source').grid(row=6, column=0, sticky='w', padx=4, pady=3)
        self.fader_mode_combo = ttk.Combobox(
            connection, textvariable=self.var('fader_mode'),
            values=['Manual', 'OSC', 'Art-Net Input'], state='readonly', width=16
        )
        self.fader_mode_combo.grid(row=6, column=1, padx=4)
        self.fader_mode_combo.bind('<<ComboboxSelected>>', lambda _e: self.update_fader_mode())
        ttk.Label(connection, text='Output').grid(row=7, column=0, sticky='w', padx=4, pady=3)
        ttk.Combobox(
            connection, textvariable=self.var('output'),
            values=['Open DMX', 'Art-Net', 'sACN'], state='readonly', width=16
        ).grid(row=7, column=1, padx=4)
        self.add_entry(connection, 8, 'Refresh Hz', 'refresh_hz', int)
        self.add_entry(connection, 9, 'Tracking timeout', 'timeout_s', float)
        self.add_entry(connection, 10, 'Smoothing 0–0.95', 'smoothing', float)
        self.start_btn = ttk.Button(connection, text='START — DIMMER LOCKED', command=self.start)
        self.start_btn.grid(row=12, column=0, columnspan=2, sticky='ew', padx=4, pady=(15, 4))
        self.arm_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(connection, text='Arm all light dimmers', variable=self.arm_var).grid(
            row=13, column=0, columnspan=2, sticky='w', padx=4, pady=4
        )
        ttk.Button(connection, text='STOP / BLACKOUT ALL', command=self.stop).grid(
            row=14, column=0, columnspan=2, sticky='ew', padx=4, pady=4
        )

        run_right = ttk.Frame(run_tab)
        run_right.pack(side='left', fill='both', expand=True, padx=8, pady=8)
        status = ttk.LabelFrame(run_right, text='System status')
        status.pack(fill='x')
        self.status_labels = {}
        status_keys = ['PSN', 'Selected light', 'Fader', 'Lights', 'State']
        for row, key in enumerate(status_keys):
            ttk.Label(status, text=key + ':').grid(row=row, column=0, sticky='e', padx=5, pady=3)
            label = ttk.Label(status, text='—', font=('Segoe UI', 10, 'bold'))
            label.grid(row=row, column=1, sticky='w', padx=5)
            self.status_labels[key] = label

        fader_box = ttk.LabelFrame(run_right, text='Manual fader')
        fader_box.pack(fill='x', pady=(8, 0))
        self.manual_scale_var = tk.DoubleVar(value=self.settings.manual_fader * 100.0)
        self.manual_scale = ttk.Scale(
            fader_box, from_=0, to=100, variable=self.manual_scale_var, command=self.manual_changed
        )
        self.manual_scale.pack(side='left', fill='x', expand=True, padx=8, pady=8)
        self.manual_value_label = ttk.Label(fader_box, text='0.0%', width=8)
        self.manual_value_label.pack(side='left', padx=8)

        beam_box = ttk.LabelFrame(run_right, text='Beam controls — shared normalized output')
        beam_box.pack(fill='x', pady=(8, 0))
        self.beam_value_labels = {}
        for row, (label, attr, initial) in enumerate((
            ('Zoom', 'zoom', self.zoom_value),
            ('Iris', 'iris', self.iris_value),
            ('Focus', 'focus', self.focus_value),
        )):
            ttk.Label(beam_box, text=label, width=7).grid(row=row, column=0, padx=5, pady=2, sticky='w')
            variable = tk.DoubleVar(value=initial * 100.0)
            setattr(self, f'{attr}_scale_var', variable)
            scale = ttk.Scale(beam_box, from_=0, to=100, variable=variable,
                              command=lambda value, name=attr: self.beam_control_changed(name, value))
            scale.grid(row=row, column=1, sticky='ew', padx=5, pady=2)
            value_label = ttk.Label(beam_box, text=f'{initial * 100:.1f}%', width=8)
            value_label.grid(row=row, column=2, padx=5)
            self.beam_value_labels[attr] = value_label
        beam_box.columnconfigure(1, weight=1)
        ttk.Separator(beam_box).grid(row=3, column=0, columnspan=3, sticky='ew', pady=(6, 4))
        ttk.Label(beam_box, text='Zoom mode', width=10).grid(row=4, column=0, padx=5, pady=2, sticky='w')
        self.zoom_mode_combo = ttk.Combobox(
            beam_box, textvariable=self.var('zoom_mode'), values=['Manual', 'Auto beam size'],
            state='readonly', width=18
        )
        self.zoom_mode_combo.grid(row=4, column=1, sticky='w', padx=5, pady=2)
        self.zoom_mode_combo.bind('<<ComboboxSelected>>', lambda _e: self.update_zoom_mode_state())
        ttk.Label(beam_box, text='Spot diameter').grid(row=5, column=0, padx=5, pady=2, sticky='w')
        self.auto_beam_diameter_entry = ttk.Entry(
            beam_box, textvariable=self.var('auto_beam_diameter_m', float), width=10
        )
        self.auto_beam_diameter_entry.grid(row=5, column=1, sticky='w', padx=5, pady=2)
        self.auto_beam_status_label = ttk.Label(beam_box, text='Auto zoom unavailable until a fixture has beam-angle data', wraplength=460)
        self.auto_beam_status_label.grid(row=6, column=0, columnspan=3, sticky='w', padx=5, pady=(2, 4))

        overview_notebook = ttk.Notebook(run_right)
        overview_notebook.pack(fill='both', expand=True, pady=(8, 0))
        lights_overview = ttk.Frame(overview_notebook)
        preview_page = ttk.Frame(overview_notebook)
        log_page = ttk.Frame(overview_notebook)
        overview_notebook.add(lights_overview, text='Light overview')
        overview_notebook.add(preview_page, text='3D preview')
        overview_notebook.add(log_page, text='Log')

        self.overview_tree = ttk.Treeview(
            lights_overview, columns=('marker', 'position', 'angles', 'distance', 'zoom', 'state'),
            show='tree headings', height=13
        )
        self.overview_tree.heading('#0', text='Light')
        self.overview_tree.heading('marker', text='Marker')
        self.overview_tree.heading('position', text='Marker XYZ')
        self.overview_tree.heading('angles', text='Pan / Tilt')
        self.overview_tree.heading('distance', text='Distance')
        self.overview_tree.heading('zoom', text='Zoom')
        self.overview_tree.heading('state', text='State')
        self.overview_tree.column('#0', width=145)
        self.overview_tree.column('marker', width=65, anchor='center')
        self.overview_tree.column('position', width=145)
        self.overview_tree.column('angles', width=130)
        self.overview_tree.column('distance', width=75, anchor='e')
        self.overview_tree.column('zoom', width=90, anchor='center')
        self.overview_tree.column('state', width=120)
        self.overview_tree.pack(fill='both', expand=True, padx=5, pady=5)

        preview_controls = ttk.Frame(preview_page)
        preview_controls.pack(fill='x', padx=5, pady=4)
        ttk.Label(preview_controls, text='Drag to orbit • Mouse wheel to zoom').pack(side='left')
        ttk.Button(preview_controls, text='Reset view', command=self.reset_preview_view).pack(side='right')
        self.preview_canvas = tk.Canvas(preview_page, background='#10151a', highlightthickness=0)
        self.preview_canvas.pack(fill='both', expand=True, padx=5, pady=(0, 5))
        self.preview_yaw = -35.0
        self.preview_pitch = 25.0
        self.preview_zoom = 32.0
        self.preview_drag = None
        self.preview_canvas.bind('<ButtonPress-1>', self.preview_press)
        self.preview_canvas.bind('<B1-Motion>', self.preview_motion)
        self.preview_canvas.bind('<MouseWheel>', self.preview_wheel)

        self.logbox = tk.Text(log_page, height=14, state='disabled')
        self.logbox.pack(fill='both', expand=True)

        # Lights tab
        list_frame = ttk.LabelFrame(lights_tab, text='Lights in output universe')
        list_frame.pack(side='left', fill='y', padx=8, pady=8)
        self.light_tree = ttk.Treeview(
            list_frame, columns=('enabled', 'marker', 'position', 'channels'), show='tree headings',
            selectmode='extended', height=22
        )
        self.light_tree.heading('#0', text='Name')
        self.light_tree.heading('enabled', text='On')
        self.light_tree.heading('marker', text='Marker')
        self.light_tree.heading('position', text='Position X,Y,Z')
        self.light_tree.heading('channels', text='Pan/Tilt')
        self.light_tree.column('#0', width=130)
        self.light_tree.column('enabled', width=40, anchor='center')
        self.light_tree.column('marker', width=60, anchor='center')
        self.light_tree.column('position', width=145)
        self.light_tree.column('channels', width=90)
        self.light_tree.pack(fill='both', expand=True, padx=5, pady=5)
        self.light_tree.bind('<<TreeviewSelect>>', self.light_selected)
        buttons = ttk.Frame(list_frame)
        buttons.pack(fill='x', padx=5, pady=(0, 5))
        ttk.Button(buttons, text='Add', command=self.add_light).pack(side='left', fill='x', expand=True)
        ttk.Button(buttons, text='Duplicate', command=self.duplicate_light).pack(side='left', fill='x', expand=True, padx=3)
        ttk.Button(buttons, text='Remove', command=self.remove_light).pack(side='left', fill='x', expand=True)

        editor = ttk.LabelFrame(lights_tab, text='Selected light')
        editor.pack(side='left', fill='both', expand=True, padx=8, pady=8)
        editor_notebook = ttk.Notebook(editor)
        editor_notebook.pack(fill='both', expand=True, padx=5, pady=5)
        geometry_page = ttk.Frame(editor_notebook)
        dmx_page = ttk.Frame(editor_notebook)
        editor_notebook.add(geometry_page, text='Geometry and calibration')
        editor_notebook.add(dmx_page, text='DMX channels')

        identity = ttk.LabelFrame(geometry_page, text='Light')
        identity.pack(side='left', fill='y', padx=6, pady=6)
        self.add_light_entry(identity, 0, 'Name', 'name', str, 18)
        ttk.Checkbutton(identity, text='Enabled', variable=self.light_var('enabled', bool)).grid(
            row=1, column=0, columnspan=2, sticky='w', padx=4, pady=4
        )
        ttk.Label(identity, text='PSN marker').grid(row=2, column=0, sticky='w', padx=4, pady=3)
        marker_var = self.light_var('marker_id', int)
        self.light_marker_combo = ttk.Combobox(identity, textvariable=marker_var, width=16)
        self.light_marker_combo.grid(row=2, column=1, sticky='ew', padx=4, pady=3)
        self.add_light_entry(identity, 3, 'Optical centre X', 'x')
        self.add_light_entry(identity, 4, 'Optical centre Y', 'y')
        self.add_light_entry(identity, 5, 'Optical centre Z', 'z')
        self.add_light_entry(identity, 6, 'Intensity scale', 'intensity_scale')

        mapping = ttk.LabelFrame(geometry_page, text='Physical angle mapping')
        mapping.pack(side='left', fill='y', padx=6, pady=6)
        self.add_light_entry(mapping, 0, 'Pan-zero bearing', 'pan_zero_bearing')
        self.add_light_entry(mapping, 1, 'Tilt-zero elevation', 'tilt_zero_elevation')
        self.add_light_entry(mapping, 2, 'Pan direction (+1/-1)', 'pan_direction', int)
        self.add_light_entry(mapping, 3, 'Tilt direction (+1/-1)', 'tilt_direction', int)
        self.add_light_entry(mapping, 4, 'Pan trim offset', 'pan_offset')
        self.add_light_entry(mapping, 5, 'Tilt trim offset', 'tilt_offset')

        limits = ttk.LabelFrame(geometry_page, text='Personality angle range')
        limits.pack(side='left', fill='y', padx=6, pady=6)
        self.add_light_entry(limits, 0, 'Pan minimum', 'pan_min')
        self.add_light_entry(limits, 1, 'Pan maximum', 'pan_max')
        self.add_light_entry(limits, 2, 'Tilt minimum', 'tilt_min')
        self.add_light_entry(limits, 3, 'Tilt maximum', 'tilt_max')

        channels = ttk.LabelFrame(dmx_page, text='Absolute channels (1-based; 0 disables)')
        channels.pack(side='left', fill='y', padx=8, pady=8)
        channel_fields = [
            ('Pan coarse', 'pan_coarse'), ('Pan fine', 'pan_fine'),
            ('Tilt coarse', 'tilt_coarse'), ('Tilt fine', 'tilt_fine'),
            ('Dimmer coarse', 'dimmer'), ('Dimmer fine', 'dimmer_fine'),
            ('Shutter', 'shutter'), ('Shutter open value', 'shutter_open'),
            ('Zoom coarse', 'zoom'), ('Zoom fine', 'zoom_fine'),
            ('Iris', 'iris'), ('Iris 100% DMX', 'iris_100_dmx'), ('Focus coarse', 'focus'), ('Focus fine', 'focus_fine'),
        ]
        for row, (label, name) in enumerate(channel_fields):
            self.add_light_entry(channels, row, label, name, int)
        ttk.Button(channels, text='Import channels from GDTF…', command=self.import_gdtf_for_selected_light).grid(
            row=len(channel_fields), column=0, columnspan=2, sticky='ew', padx=4, pady=(10, 4)
        )

        direction_box = ttk.LabelFrame(dmx_page, text='Beam control direction')
        direction_box.pack(side='left', fill='y', padx=8, pady=8)
        ttk.Checkbutton(direction_box, text='Reverse zoom', variable=self.light_var('zoom_reverse', bool)).pack(anchor='w', padx=6, pady=5)
        ttk.Checkbutton(direction_box, text='Reverse iris', variable=self.light_var('iris_reverse', bool)).pack(anchor='w', padx=6, pady=5)
        ttk.Checkbutton(direction_box, text='Reverse focus', variable=self.light_var('focus_reverse', bool)).pack(anchor='w', padx=6, pady=5)
        ttk.Label(direction_box, text="Use reverse when the fixture's\nDMX range runs opposite to the UI.", justify='left').pack(anchor='w', padx=6, pady=(8, 5))

        beam_model = ttk.LabelFrame(dmx_page, text='Auto zoom beam model')
        beam_model.pack(side='left', fill='y', padx=8, pady=8)
        self.add_light_entry(beam_model, 0, 'Beam angle at zoom 0%', 'zoom_angle_at_0', float)
        self.add_light_entry(beam_model, 1, 'Beam angle at zoom 100%', 'zoom_angle_at_100', float)
        ttk.Label(
            beam_model,
            text='Used only by Operator → Auto beam size.\nGDTF import fills these when the file\ncontains Zoom PhysicalFrom/To values.\nLeave both 0 to disable auto zoom.',
            justify='left', wraplength=230
        ).grid(row=2, column=0, columnspan=2, sticky='w', padx=4, pady=8)

        ttk.Label(
            dmx_page,
            text='All lights share the selected 512-channel output universe.\n'
                 'The app rejects overlapping enabled channels so one light cannot overwrite another.',
            justify='left'
        ).pack(side='left', anchor='n', padx=12, pady=12)

        editor_buttons = ttk.Frame(editor)
        editor_buttons.pack(fill='x', padx=5, pady=(0, 5))
        ttk.Button(editor_buttons, text='Apply selected light changes', command=self.apply_selected_light).pack(
            side='left', padx=3
        )
        ttk.Button(editor_buttons, text='Calibrate selected light', command=self.open_calibration_wizard).pack(side='left', padx=3)
        ttk.Button(editor_buttons, text='Save all settings', command=self.save).pack(side='left', padx=3)

        # I/O tab
        fader_settings = ttk.LabelFrame(io_tab, text='Fader input settings')
        fader_settings.pack(side='left', fill='y', padx=8, pady=8)
        self.add_entry(fader_settings, 0, 'OSC UDP port', 'osc_fader_port', int)
        self.add_entry(fader_settings, 1, 'OSC address', 'osc_fader_address', str, 24)
        self.add_entry(fader_settings, 2, 'OSC argument index', 'osc_fader_arg', int)
        self.add_entry(fader_settings, 3, 'OSC input minimum', 'osc_fader_min', float)
        self.add_entry(fader_settings, 4, 'OSC input maximum', 'osc_fader_max', float)
        self.add_entry(fader_settings, 5, 'Art-Net input universe', 'artnet_input_universe', int)
        self.add_entry(fader_settings, 6, 'Art-Net input channel', 'artnet_input_channel', int)
        ttk.Label(
            fader_settings, text='OSC argument index is zero-based.\nExample xyzf fader: index 3.', justify='left'
        ).grid(row=7, column=0, columnspan=2, sticky='w', padx=4, pady=8)

        output_settings = ttk.LabelFrame(io_tab, text='Output connection')
        output_settings.pack(side='left', fill='y', padx=8, pady=8)
        ttk.Label(output_settings, text='Serial port').grid(row=0, column=0, sticky='w', padx=4, pady=3)
        self.port_combo = ttk.Combobox(output_settings, textvariable=self.var('serial_port'), width=18)
        self.port_combo.grid(row=0, column=1, padx=4)
        ttk.Button(output_settings, text='Refresh ports', command=self.refresh_ports).grid(
            row=1, column=0, columnspan=2, sticky='ew', padx=4, pady=3
        )
        self.add_entry(output_settings, 2, 'Art-Net target IP', 'artnet_ip', str, 18)
        self.add_entry(output_settings, 3, 'Universe', 'universe', int)
        ttk.Label(
            output_settings,
            text='Open DMX requires the FTDI Virtual COM Port driver.\n'
                 'Art-Net universe is zero-based; sACN universe is one-based.',
            justify='left'
        ).grid(row=4, column=0, columnspan=2, sticky='w', padx=4, pady=8)

        # Calibration tab
        calibration = ttk.LabelFrame(calibration_tab, text='Selected-light pointing verification')
        calibration.pack(fill='x', padx=8, pady=8)
        self.calibration_light_label = ttk.Label(calibration, text='Selected light: —', font=('Segoe UI', 11, 'bold'))
        self.calibration_light_label.grid(row=0, column=0, columnspan=4, sticky='w', padx=8, pady=8)
        ttk.Label(
            calibration,
            text='Keep the lamp/shutter closed. Put the marker at a known point, start output, and verify each light separately.\n'
                 'Select one or more lights on the Lights tab. The wizard can calibrate all selected lights against the same target points.',
            justify='left'
        ).grid(row=1, column=0, columnspan=4, sticky='w', padx=8, pady=8)
        ttk.Button(calibration, text='Open fixture calibration wizard', command=self.open_calibration_wizard).grid(
            row=2, column=0, padx=5, pady=5, sticky='ew'
        )
        ttk.Button(calibration, text='Reverse pan direction', command=lambda: self.reverse_light('pan_direction')).grid(
            row=2, column=1, padx=5, pady=5
        )
        ttk.Button(calibration, text='Reverse tilt direction', command=lambda: self.reverse_light('tilt_direction')).grid(
            row=2, column=2, padx=5, pady=5
        )
        ttk.Button(calibration, text='Save settings', command=self.save).grid(
            row=3, column=0, padx=5, pady=12, sticky='ew'
        )
        ttk.Button(calibration, text='Export settings', command=self.export).grid(
            row=3, column=1, padx=5, pady=12, sticky='ew'
        )
        ttk.Label(
            calibration_tab,
            text='World convention: +X right, +Y away/upstage, +Z up. Bearing 0° is +Y; +90° is +X.',
            justify='left'
        ).pack(anchor='w', padx=12, pady=12)
        self.refresh_ports()

    def populate(self):
        for name in self.GENERAL_TYPES:
            if name == 'manual_fader':
                continue
            if name in self.vars:
                self.vars[name].set(getattr(self.settings, name))
        self.manual_scale_var.set(self.settings.manual_fader * 100.0)
        self.zoom_value = self.settings.zoom_master
        self.iris_value = self.settings.iris_master
        self.focus_value = self.settings.focus_master
        for name in ('zoom', 'iris', 'focus'):
            variable = getattr(self, f'{name}_scale_var', None)
            if variable is not None:
                variable.set(getattr(self, f'{name}_value') * 100.0)
            if hasattr(self, 'beam_value_labels'):
                self.beam_value_labels[name].configure(text=f"{getattr(self, f'{name}_value') * 100:.1f}%")
        self.manual_changed(self.manual_scale_var.get())
        self.update_fader_mode()
        self.update_zoom_mode_state()
        self.rebuild_light_tree(select_index=0)
        self.update_zoom_mode_state()

    def _psn_discovered(self, tracker_id):
        self.psn_discovered.put(int(tracker_id))

    def scan_psn(self):
        if self.psn_scanner:
            try:
                self.psn_scanner.stop()
            except Exception:
                pass
            self.psn_scanner = None
        try:
            group = str(self.vars['psn_multicast'].get()).strip()
            port = int(self.vars['psn_port'].get())
            interface = str(self.vars['psn_interface'].get()).strip() or '0.0.0.0'
            marker = int(self.vars['marker_id'].get())
            self.psn_tracker_ids.clear()
            self.psn_tracker_combo['values'] = []
            self.psn_detect_label.configure(text='Listening for PSN…')
            self.psn_scanner = PSNReceiver(
                group, port, interface, marker, TrackerBank(), self.log, self._psn_discovered
            )
            self.psn_scanner.start()
            self.after(4000, self.finish_psn_scan)
        except Exception as exc:
            self.psn_detect_label.configure(text='Detection failed')
            messagebox.showerror(APP_NAME, str(exc))

    def finish_psn_scan(self):
        if self.psn_scanner:
            try:
                self.psn_scanner.stop()
            except Exception:
                pass
            self.psn_scanner = None
        if self.psn_tracker_ids:
            ids = sorted(self.psn_tracker_ids)
            self.psn_tracker_combo['values'] = ids
            if hasattr(self, 'light_marker_combo'):
                self.light_marker_combo['values'] = ids
            self.psn_detect_label.configure(text='Found tracker IDs: ' + ', '.join(map(str, ids)))
            try:
                selected = int(self.vars['marker_id'].get())
            except Exception:
                selected = None
            if selected not in self.psn_tracker_ids:
                self.vars['marker_id'].set(ids[0])
        else:
            self.psn_detect_label.configure(text='No PSN trackers found')

    def manual_changed(self, value):
        self.manual_fader_value = clamp(float(value) / 100.0, 0.0, 1.0)
        if hasattr(self, 'manual_value_label'):
            self.manual_value_label.configure(text=f'{self.manual_fader_value * 100:.1f}%')
        if self.settings.fader_mode == 'Manual':
            self.fader.update(self.manual_fader_value)

    def beam_control_changed(self, name, value):
        normalized = clamp(float(value) / 100.0, 0.0, 1.0)
        setattr(self, f'{name}_value', normalized)
        if hasattr(self, 'beam_value_labels') and name in self.beam_value_labels:
            self.beam_value_labels[name].configure(text=f'{normalized * 100:.1f}%')

    def update_fader_mode(self):
        mode = self.vars['fader_mode'].get() if 'fader_mode' in self.vars else 'Manual'
        if hasattr(self, 'manual_scale'):
            self.manual_scale.state(['!disabled'] if mode == 'Manual' else ['disabled'])

    def zoom_auto_available(self):
        return any(fixture.enabled and fixture_has_zoom_model(fixture) for fixture in self.settings.fixtures)

    def update_zoom_mode_state(self):
        available = self.zoom_auto_available()
        if hasattr(self, 'zoom_mode_combo'):
            if available:
                self.zoom_mode_combo.state(['!disabled', 'readonly'])
            else:
                # No imported/manual beam angles: force Manual and grey out the auto option.
                if 'zoom_mode' in self.vars:
                    self.vars['zoom_mode'].set('Manual')
                self.zoom_mode_combo.state(['disabled'])
        if hasattr(self, 'auto_beam_diameter_entry'):
            self.auto_beam_diameter_entry.state(['!disabled'] if available else ['disabled'])
        if hasattr(self, 'auto_beam_status_label'):
            if available:
                self.auto_beam_status_label.configure(
                    text='Auto beam size is available. FART will adjust each light with beam-angle data to maintain the requested spot diameter.'
                )
            else:
                self.auto_beam_status_label.configure(
                    text='Auto beam size is greyed out: no enabled fixture has zoom physical beam-angle data. Import a GDTF with Zoom PhysicalFrom/To or enter beam angles manually.'
                )

    def rebuild_light_tree(self, select_index=None):
        self.loading_light_editor = True
        try:
            for item in self.light_tree.get_children():
                self.light_tree.delete(item)
            for index, fixture in enumerate(self.settings.fixtures):
                self.insert_or_update_light_row(index, fixture)
            if not self.settings.fixtures:
                self.settings.fixtures.append(FixtureConfig())
                return self.rebuild_light_tree(0)
            if select_index is None:
                select_index = min(self.selected_light_index, len(self.settings.fixtures) - 1)
            self.selected_light_index = max(0, min(select_index, len(self.settings.fixtures) - 1))
            iid = str(self.selected_light_index)
            self.light_tree.selection_set(iid)
            self.light_tree.focus(iid)
            self.load_light_editor(self.selected_light_index)
        finally:
            self.loading_light_editor = False

    def insert_or_update_light_row(self, index, fixture):
        iid = str(index)
        position = f'{fixture.x:g}, {fixture.y:g}, {fixture.z:g}'
        pan_tilt = f'{fixture.pan_coarse}/{fixture.tilt_coarse}'
        values = ('Yes' if fixture.enabled else 'No', fixture.marker_id, position, pan_tilt)
        if self.light_tree.exists(iid):
            self.light_tree.item(iid, text=fixture.name, values=values)
        else:
            self.light_tree.insert('', 'end', iid=iid, text=fixture.name, values=values)

    def light_selected(self, _event=None):
        if self.loading_light_editor:
            return
        selection = self.light_tree.selection()
        if not selection:
            return
        try:
            new_index = int(selection[0])
        except ValueError:
            return
        old_index = self.selected_light_index
        if new_index == old_index:
            return
        try:
            edited = self.fixture_from_editor()
            self.settings.fixtures[old_index] = edited
            self.insert_or_update_light_row(old_index, edited)
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))
            self.loading_light_editor = True
            try:
                self.light_tree.selection_set(str(old_index))
                self.light_tree.focus(str(old_index))
            finally:
                self.loading_light_editor = False
            return
        self.selected_light_index = new_index
        self.load_light_editor(new_index)

    def load_light_editor(self, index):
        if not 0 <= index < len(self.settings.fixtures):
            return
        fixture = self.settings.fixtures[index]
        self.loading_light_editor = True
        try:
            for name in self.FIXTURE_TYPES:
                self.light_vars[name].set(getattr(fixture, name))
            if hasattr(self, 'light_marker_combo'):
                self.light_marker_combo['values'] = sorted(self.psn_tracker_ids)
            self.calibration_light_label.configure(text=f'Selected light: {fixture.name}')
            self.update_zoom_mode_state()
        finally:
            self.loading_light_editor = False

    def fixture_from_editor(self):
        values = {}
        for name, typ in self.FIXTURE_TYPES.items():
            raw = self.light_vars[name].get()
            if typ is int:
                raw = int(raw)
            elif typ is float:
                raw = float(raw)
            elif typ is bool:
                raw = bool(raw)
            else:
                raw = str(raw).strip()
            values[name] = raw
        fixture = FixtureConfig(**values)
        self.validate_fixture(fixture)
        return fixture

    def validate_fixture(self, fixture):
        if not fixture.name:
            raise ValueError('Each light needs a name')
        if fixture.pan_max <= fixture.pan_min or fixture.tilt_max <= fixture.tilt_min:
            raise ValueError(f'{fixture.name}: angle maximum must be greater than minimum')
        if fixture.pan_direction not in (-1, 1) or fixture.tilt_direction not in (-1, 1):
            raise ValueError(f'{fixture.name}: pan and tilt directions must be +1 or -1')
        if fixture.marker_id < 0 or fixture.marker_id > 65535:
            raise ValueError(f'{fixture.name}: PSN marker ID must be 0–65535')
        if fixture.intensity_scale < 0:
            raise ValueError(f'{fixture.name}: intensity scale cannot be negative')
        if not 0 <= fixture.shutter_open <= 255:
            raise ValueError(f'{fixture.name}: shutter open value must be 0–255')
        if not 0 <= int(getattr(fixture, 'iris_100_dmx', 255)) <= 255:
            raise ValueError(f'{fixture.name}: iris 100% DMX value must be 0–255')
        if float(getattr(fixture, 'zoom_angle_at_0', 0.0)) < 0 or float(getattr(fixture, 'zoom_angle_at_100', 0.0)) < 0:
            raise ValueError(f'{fixture.name}: zoom beam angles cannot be negative')
        if fixture.enabled and (fixture.pan_coarse == 0 or fixture.tilt_coarse == 0):
            raise ValueError(f'{fixture.name}: enabled lights require pan coarse and tilt coarse channels')
        if fixture.pan_fine and not fixture.pan_coarse:
            raise ValueError(f'{fixture.name}: pan fine requires pan coarse')
        if fixture.tilt_fine and not fixture.tilt_coarse:
            raise ValueError(f'{fixture.name}: tilt fine requires tilt coarse')
        if fixture.dimmer_fine and not fixture.dimmer:
            raise ValueError(f'{fixture.name}: dimmer fine requires dimmer coarse')
        if fixture.zoom_fine and not fixture.zoom:
            raise ValueError(f'{fixture.name}: zoom fine requires zoom coarse')
        if fixture.focus_fine and not fixture.focus:
            raise ValueError(f'{fixture.name}: focus fine requires focus coarse')
        for label, channel in fixture_channels(fixture).items():
            if not 0 <= channel <= 512:
                raise ValueError(f'{fixture.name}: {label} channel must be 0–512')

    def apply_selected_light(self, silent=False):
        try:
            fixture = self.fixture_from_editor()
            self.settings.fixtures[self.selected_light_index] = fixture
            self.insert_or_update_light_row(self.selected_light_index, fixture)
            self.calibration_light_label.configure(text=f'Selected light: {fixture.name}')
            self.update_zoom_mode_state()
            if not silent:
                self.log(f'Applied changes to {fixture.name}')
            return True
        except Exception as exc:
            if not silent:
                messagebox.showerror(APP_NAME, str(exc))
            return False

    def add_light(self):
        if not self.apply_selected_light(silent=True):
            messagebox.showerror(APP_NAME, 'Fix the selected light settings before adding another light')
            return
        number = len(self.settings.fixtures) + 1
        new_fixture = FixtureConfig(name=f'Light {number}', marker_id=int(self.vars['marker_id'].get()))
        used = [ch for fixture in self.settings.fixtures for ch in fixture_channels(fixture).values() if ch > 0]
        start = max(used, default=0) + 1
        if start + 4 <= 512:
            new_fixture.pan_coarse = start
            new_fixture.pan_fine = start + 1
            new_fixture.tilt_coarse = start + 2
            new_fixture.tilt_fine = start + 3
            new_fixture.dimmer = start + 4
        else:
            new_fixture.enabled = False
            new_fixture.pan_coarse = new_fixture.pan_fine = 0
            new_fixture.tilt_coarse = new_fixture.tilt_fine = 0
            new_fixture.dimmer = 0
        self.settings.fixtures.append(new_fixture)
        self.rebuild_light_tree(len(self.settings.fixtures) - 1)
        choice = messagebox.askyesnocancel(
            APP_NAME,
            'How do you want to set up this new fixture?\n\n'
            'Yes = set DMX channels first, then open calibration wizard\n'
            'No = manual XYZ/bearing entry on the Lights tab\n'
            'Cancel = leave the fixture created but do nothing else'
        )
        if choice is True:
            self.open_dmx_setup_then_calibration()

    def duplicate_light(self):
        if not self.apply_selected_light(silent=True):
            return
        source = self.settings.fixtures[self.selected_light_index]
        copied = FixtureConfig(**asdict(source))
        copied.name = source.name + ' copy'

        # Preserve profile channel gaps. If pan is offset 18 in the source,
        # it remains offset 18 relative to the duplicate's new start address.
        source_channels = [ch for ch in fixture_channels(source).values() if ch > 0]
        all_used = [
            ch for fixture in self.settings.fixtures
            for ch in fixture_channels(fixture).values() if ch > 0
        ]
        if source_channels:
            shift = max(all_used, default=0) + 1 - min(source_channels)
            shifted = {}
            for field_name in ('pan_coarse', 'pan_fine', 'tilt_coarse', 'tilt_fine',
                               'dimmer', 'dimmer_fine', 'shutter', 'zoom', 'zoom_fine',
                               'iris', 'focus', 'focus_fine'):
                channel = getattr(copied, field_name)
                shifted[field_name] = channel + shift if channel > 0 else 0
            if max(shifted.values(), default=0) <= 512:
                for field_name, channel in shifted.items():
                    setattr(copied, field_name, channel)
            else:
                copied.enabled = False
        self.settings.fixtures.append(copied)
        self.rebuild_light_tree(len(self.settings.fixtures) - 1)

    def remove_light(self):
        if len(self.settings.fixtures) <= 1:
            messagebox.showerror(APP_NAME, 'At least one light must remain')
            return
        fixture = self.settings.fixtures[self.selected_light_index]
        if not messagebox.askyesno(APP_NAME, f'Remove {fixture.name}?'):
            return
        del self.settings.fixtures[self.selected_light_index]
        self.rebuild_light_tree(min(self.selected_light_index, len(self.settings.fixtures) - 1))

    def collect(self):
        if not self.apply_selected_light(silent=True):
            raise ValueError('Fix the selected light settings before starting or saving')

        values = {}
        for name, typ in self.GENERAL_TYPES.items():
            if name == 'manual_fader':
                values[name] = self.manual_fader_value
                continue
            if name in ('zoom_master', 'iris_master', 'focus_master'):
                values[name] = getattr(self, name.replace('_master', '_value'))
                continue
            raw = self.vars[name].get()
            if typ is int:
                raw = int(raw)
            elif typ is float:
                raw = float(raw)
            else:
                raw = str(raw)
            values[name] = raw
        values['manual_fader'] = self.manual_fader_value
        values['zoom_master'] = self.zoom_value
        values['iris_master'] = self.iris_value
        values['focus_master'] = self.focus_value

        fixtures = [FixtureConfig(**asdict(fixture)) for fixture in self.settings.fixtures]
        settings = Settings(**values, fixtures=fixtures)
        if not 1 <= settings.refresh_hz <= 100:
            raise ValueError('Refresh rate must be 1–100 Hz')
        if settings.timeout_s <= 0:
            raise ValueError('Tracking timeout must be greater than zero')
        if not 0 <= settings.smoothing <= 0.95:
            raise ValueError('Smoothing must be between 0 and 0.95')
        for label, value in (('Zoom', settings.zoom_master), ('Iris', settings.iris_master), ('Focus', settings.focus_master)):
            if not 0 <= value <= 1:
                raise ValueError(f'{label} master must be between 0 and 1')
        if settings.zoom_mode not in ('Manual', 'Auto beam size'):
            raise ValueError('Zoom mode must be Manual or Auto beam size')
        if settings.auto_beam_diameter_m <= 0:
            raise ValueError('Auto beam diameter must be greater than zero')
        if settings.zoom_mode == 'Auto beam size' and not any(fixture.enabled and fixture_has_zoom_model(fixture) for fixture in settings.fixtures):
            raise ValueError('Auto beam size requires at least one enabled fixture with zoom beam-angle data')
        if settings.osc_fader_arg < 0:
            raise ValueError('OSC argument index cannot be negative')
        if not 1 <= settings.artnet_input_channel <= 512:
            raise ValueError('Art-Net input channel must be 1–512')
        if settings.output == 'Art-Net' and not 0 <= settings.universe <= 32767:
            raise ValueError('Art-Net universe must be between 0 and 32767')
        if settings.output == 'sACN' and not 1 <= settings.universe <= 63999:
            raise ValueError('sACN universe must be between 1 and 63999')
        if not any(fixture.enabled for fixture in settings.fixtures):
            raise ValueError('At least one light must be enabled')
        for fixture in settings.fixtures:
            self.validate_fixture(fixture)
        self.validate_channel_conflicts(settings.fixtures)
        return settings

    def validate_channel_conflicts(self, fixtures):
        owners = {}
        for fixture in fixtures:
            if not fixture.enabled:
                continue
            local = {}
            for label, channel in fixture_channels(fixture).items():
                if channel == 0:
                    continue
                if channel in local:
                    raise ValueError(
                        f'{fixture.name}: DMX channel {channel} is assigned to both {local[channel]} and {label}'
                    )
                local[channel] = label
                if channel in owners:
                    other_name, other_label = owners[channel]
                    raise ValueError(
                        f'DMX channel {channel} overlaps: {other_name} {other_label} and {fixture.name} {label}'
                    )
                owners[channel] = (fixture.name, label)

    def save(self):
        try:
            self.settings = self.collect()
            CONFIG_FILE.write_text(json.dumps(asdict(self.settings), indent=2))
            self.log(f'Saved {CONFIG_FILE}')
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def export(self):
        try:
            path = filedialog.asksaveasfilename(initialfile='fart-settings.json', defaultextension='.json', filetypes=[('JSON', '*.json')])
            if path:
                Path(path).write_text(json.dumps(asdict(self.collect()), indent=2))
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def refresh_ports(self):
        ports = [port.device for port in serial.tools.list_ports.comports()] if serial else []
        if hasattr(self, 'port_combo'):
            self.port_combo['values'] = ports

    def reverse_light(self, name):
        try:
            self.light_vars[name].set(-int(self.light_vars[name].get()))
            self.apply_selected_light()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))


    def import_gdtf_for_selected_light(self):
        try:
            if not self.apply_selected_light(silent=True):
                raise ValueError('Fix the selected light settings before importing GDTF channels')
            fixture = self.settings.fixtures[self.selected_light_index]
            path = filedialog.askopenfilename(
                title='Select GDTF fixture file',
                filetypes=[('GDTF fixture', '*.gdtf'), ('Zip files', '*.zip'), ('All files', '*.*')]
            )
            if not path:
                return
            start = simpledialog.askinteger(
                APP_NAME,
                'Fixture start DMX address?\n\nExample: for patch 1.101 enter 101.',
                initialvalue=max(1, min([ch for ch in fixture_channels(fixture).values() if ch > 0] or [1])),
                minvalue=1,
                maxvalue=512,
                parent=self,
            )
            if not start:
                return
            mode = select_gdtf_mode(self, path)
            if not mode:
                return
            mapping, modes, selected_mode = import_gdtf_channel_mapping(path, start, mode)
            for field, channel in mapping.items():
                if field in self.light_vars:
                    self.light_vars[field].set(channel)
                    setattr(fixture, field, channel)
            self.settings.fixtures[self.selected_light_index] = fixture
            self.insert_or_update_light_row(self.selected_light_index, fixture)
            self.update_zoom_mode_state()
            found = ', '.join(f'{k}={v}' for k, v in mapping.items())
            messagebox.showinfo(
                APP_NAME,
                'Imported GDTF channel mapping.\n\n'
                'Check these against the fixture manual before moving a real light.\n\n'
                f'Mode used: {selected_mode}\n{found}',
                parent=self,
            )
            self.log(f'Imported GDTF channels for {fixture.name}: {found}')
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)

    def require_calibration_dmx(self, fixture):
        missing = []
        if int(fixture.pan_coarse) <= 0:
            missing.append('pan coarse')
        if int(fixture.tilt_coarse) <= 0:
            missing.append('tilt coarse')
        if int(fixture.dimmer) <= 0:
            missing.append('dimmer coarse')
        if missing:
            raise ValueError(
                f'{fixture.name}: set DMX channels before calibration. Missing: ' + ', '.join(missing)
            )
        for label, channel in (
            ('pan coarse', fixture.pan_coarse), ('pan fine', fixture.pan_fine),
            ('tilt coarse', fixture.tilt_coarse), ('tilt fine', fixture.tilt_fine),
            ('dimmer coarse', fixture.dimmer), ('dimmer fine', fixture.dimmer_fine),
            ('shutter', fixture.shutter), ('zoom coarse', fixture.zoom), ('zoom fine', fixture.zoom_fine),
            ('iris', fixture.iris), ('focus coarse', fixture.focus), ('focus fine', fixture.focus_fine),
        ):
            if not 0 <= int(channel) <= 512:
                raise ValueError(f'{fixture.name}: {label} channel must be 0–512')
        if not 0 <= int(fixture.shutter_open) <= 255:
            raise ValueError(f'{fixture.name}: shutter open value must be 0–255')
        if not 0 <= int(getattr(fixture, 'iris_100_dmx', 255)) <= 255:
            raise ValueError(f'{fixture.name}: iris 100% DMX value must be 0–255')
        if float(getattr(fixture, 'zoom_angle_at_0', 0.0)) < 0 or float(getattr(fixture, 'zoom_angle_at_100', 0.0)) < 0:
            raise ValueError(f'{fixture.name}: zoom beam angles cannot be negative')
        return True

    def open_dmx_setup_then_calibration(self):
        try:
            if not self.apply_selected_light(silent=True):
                raise ValueError('Fix the selected light settings before calibration')
            DMXSetupDialog(self, self.selected_light_index, on_complete=self.open_calibration_wizard)
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def selected_light_indices(self):
        selection = self.light_tree.selection() if hasattr(self, 'light_tree') else ()
        indices = []
        for item in selection:
            try:
                idx = int(item)
            except ValueError:
                continue
            if 0 <= idx < len(self.settings.fixtures):
                indices.append(idx)
        if not indices:
            indices = [self.selected_light_index]
        return sorted(set(indices))

    def open_calibration_wizard(self):
        try:
            if not self.apply_selected_light(silent=True):
                raise ValueError('Fix the selected light settings before calibration')
            indices = self.selected_light_indices()
            for idx in indices:
                fixture = self.settings.fixtures[idx]
                try:
                    self.require_calibration_dmx(fixture)
                except ValueError:
                    self.selected_light_index = idx
                    self.light_tree.selection_set(str(idx))
                    self.light_tree.focus(str(idx))
                    self.load_light_editor(idx)
                    DMXSetupDialog(self, idx, on_complete=self.open_calibration_wizard)
                    return
            CalibrationWizard(self, indices)
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def current_selected_aim(self):
        if not self.apply_selected_light(silent=True):
            raise ValueError('Selected light settings are invalid')
        fixture = self.settings.fixtures[self.selected_light_index]
        x, y, z, timestamp = self.trackers.get(fixture.marker_id)
        if timestamp == 0:
            raise ValueError('No live PSN position has been received')
        return fixture, calculate_aim(fixture, x, y, z)

    def cal_pan_zero(self):
        try:
            fixture, (bearing, _elevation, _pan, _tilt, _distance) = self.current_selected_aim()
            self.light_vars['pan_zero_bearing'].set(round(bearing, 3))
            self.light_vars['pan_offset'].set(0.0)
            self.apply_selected_light()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def cal_tilt_zero(self):
        try:
            fixture, (_bearing, elevation, _pan, _tilt, _distance) = self.current_selected_aim()
            self.light_vars['tilt_zero_elevation'].set(round(elevation, 3))
            self.light_vars['tilt_offset'].set(0.0)
            self.apply_selected_light()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def make_output(self, settings):
        if settings.output == 'Open DMX':
            return OpenDMX(settings.serial_port)
        if settings.output == 'Art-Net':
            return ArtNet(settings.artnet_ip, settings.universe)
        return SACN(settings.universe, settings.refresh_hz)

    def make_fader_input(self, settings):
        if settings.fader_mode == 'Manual':
            self.fader.update(self.manual_fader_value)
            return None
        if settings.fader_mode == 'OSC':
            return OSCFaderReceiver(
                settings.osc_fader_port, settings.osc_fader_address, settings.osc_fader_arg,
                settings.osc_fader_min, settings.osc_fader_max, self.fader, self.log
            )
        return ArtNetFaderReceiver(
            settings.artnet_input_universe, settings.artnet_input_channel, self.fader, self.log
        )

    def set_setup_tabs_enabled(self, enabled):
        if not hasattr(self, 'notebook'):
            return
        state = 'normal' if enabled else 'disabled'
        # Operator is tab 0; all other tabs are setup tabs and should not be
        # edited while live output is running.
        for tab_index in range(1, 4):
            try:
                self.notebook.tab(tab_index, state=state)
            except Exception:
                pass

    def start(self):
        if self.running:
            return
        try:
            self.settings = self.collect()
            self.output = self.make_output(self.settings)
            self.psn = PSNReceiver(
                self.settings.psn_multicast, self.settings.psn_port, self.settings.psn_interface,
                self.settings.marker_id, self.trackers, self.log, self._psn_discovered
            )
            self.psn.start()
            self.fader_input = self.make_fader_input(self.settings)
            if self.fader_input:
                self.fader_input.start()
            self.stop_evt.clear()
            self.running = True
            self.set_setup_tabs_enabled(False)
            self.worker = threading.Thread(target=self.loop, daemon=True)
            self.worker.start()
            self.start_btn.configure(text='RUNNING')
            count = sum(f.enabled for f in self.settings.fixtures)
            self.log(f'FART started with {count} enabled light(s); dimmers remain locked until armed')
        except Exception as exc:
            if self.fader_input:
                try:
                    self.fader_input.stop()
                except Exception:
                    pass
            if self.psn:
                try:
                    self.psn.stop()
                except Exception:
                    pass
            if self.output:
                try:
                    self.output.close()
                except Exception:
                    pass
            self.psn = self.fader_input = self.output = None
            self.set_setup_tabs_enabled(True)
            messagebox.showerror(APP_NAME, str(exc))

    def stop(self):
        self.arm_var.set(False)
        self.stop_evt.set()
        self.running = False
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(timeout=1)
        if self.psn:
            try:
                self.psn.stop()
            except Exception:
                pass
        if self.fader_input:
            try:
                self.fader_input.stop()
            except Exception:
                pass
        if self.output:
            try:
                self.output.send(bytes(512))
                time.sleep(0.05)
                self.output.close()
            except Exception:
                pass
        self.psn = self.fader_input = self.output = None
        self.set_setup_tabs_enabled(True)
        self.start_btn.configure(text='START — DIMMER LOCKED')
        self.log('Stopped and blacked out all lights')

    def loop(self):
        smoothed = {}
        previous_pan = {}
        try:
            while not self.stop_evt.is_set():
                cycle_start = time.monotonic()
                settings = self.settings
                fader, _fader_time = self.fader.get()
                smoothing = clamp(settings.smoothing, 0.0, 0.95)
                alpha = 1.0 - smoothing
                frame = bytearray(512)
                light_statuses = []

                for index, fixture in enumerate(settings.fixtures):
                    if not fixture.enabled:
                        continue
                    x, y, z, tracker_time = self.trackers.get(fixture.marker_id)
                    previous_xyz = smoothed.get(fixture.marker_id)
                    if previous_xyz is None:
                        sx, sy, sz = x, y, z
                    else:
                        sx = previous_xyz[0] + (x - previous_xyz[0]) * alpha
                        sy = previous_xyz[1] + (y - previous_xyz[1]) * alpha
                        sz = previous_xyz[2] + (z - previous_xyz[2]) * alpha
                    smoothed[fixture.marker_id] = (sx, sy, sz)
                    stale = tracker_time == 0 or cycle_start - tracker_time > settings.timeout_s
                    blackout = stale or not self.arm_var.get()
                    try:
                        bearing, elevation, pan, tilt, distance = calculate_aim(
                            fixture, sx, sy, sz, previous_pan.get(index)
                        )
                        previous_pan[index] = pan
                        zoom_out = self.zoom_value
                        zoom_angle = None
                        zoom_auto = False
                        if settings.zoom_mode == 'Auto beam size':
                            zoom_out, zoom_angle, zoom_auto = auto_zoom_for_distance(
                                fixture, distance, settings.auto_beam_diameter_m, self.zoom_value
                            )
                        status = write_fixture_to_frame(
                            frame, fixture, pan, tilt, fader, blackout,
                            zoom_out, self.iris_value, self.focus_value
                        )
                        status.update({
                            'index': index, 'name': fixture.name, 'marker_id': fixture.marker_id,
                            'marker_xyz': (sx, sy, sz), 'fixture_xyz': (fixture.x, fixture.y, fixture.z),
                            'bearing': bearing, 'elevation': elevation, 'distance': distance,
                            'zoom_value': zoom_out, 'zoom_angle': zoom_angle, 'zoom_auto': zoom_auto,
                            'stale': stale, 'blackout': blackout,
                        })
                        light_statuses.append(status)
                    except ValueError as exc:
                        light_statuses.append({
                            'index': index, 'name': fixture.name, 'marker_id': fixture.marker_id,
                            'marker_xyz': (sx, sy, sz), 'fixture_xyz': (fixture.x, fixture.y, fixture.z),
                            'error': str(exc), 'stale': stale, 'blackout': True,
                            'pan_limit': False, 'tilt_limit': False,
                        })

                self.output.send(bytes(frame))
                self.live = {
                    'fader': fader,
                    'blackout': not self.arm_var.get(),
                    'lights': light_statuses,
                    'trackers': self.trackers.snapshot(),
                }
                sleep_time = 1.0 / max(1, settings.refresh_hz) - (time.monotonic() - cycle_start)
                time.sleep(max(0.0, sleep_time))
        except Exception as exc:
            self.log('OUTPUT ERROR: ' + str(exc))
            self.after(0, self.stop)

    def ui_tick(self):
        while True:
            try:
                message = self.logs.get_nowait()
            except queue.Empty:
                break
            self.logbox.configure(state='normal')
            self.logbox.insert('end', message + '\n')
            self.logbox.see('end')
            self.logbox.configure(state='disabled')

        found_changed = False
        while True:
            try:
                tracker_id = self.psn_discovered.get_nowait()
            except queue.Empty:
                break
            if tracker_id not in self.psn_tracker_ids:
                self.psn_tracker_ids.add(tracker_id)
                found_changed = True
        if found_changed:
            ids = sorted(self.psn_tracker_ids)
            self.psn_tracker_combo['values'] = ids
            if hasattr(self, 'light_marker_combo'):
                self.light_marker_combo['values'] = ids
            self.psn_detect_label.configure(text='Found tracker IDs: ' + ', '.join(map(str, ids)))

        if self.psn:
            stats = self.psn.stats()
            packet_age = 'never' if stats['last_packet_age'] is None else f"{stats['last_packet_age']:.2f}s ago"
            self.status_labels['PSN'].configure(
                text=f"packets {stats['packets']}, data {stats['data_packets']}, "
                     f"selected {stats['selected_tracker']}, positions {stats['positions']} ({packet_age})"
            )
        else:
            self.status_labels['PSN'].configure(text='Stopped')

        if self.live:
            lights = self.live['lights']
            selected = next((item for item in lights if item.get('index') == self.selected_light_index), None)
            if selected is None and lights:
                selected = lights[0]
            limits = sum(bool(item.get('pan_limit') or item.get('tilt_limit')) for item in lights)
            stale_count = sum(bool(item.get('stale')) for item in lights)
            values = {
                'Fader': f"{self.live['fader'] * 100:.1f}%",
                'Lights': f'{len(lights)} enabled, {stale_count} lost, {limits} at a limit',
                'State': 'ALL DIMMERS LOCKED' if self.live['blackout'] else (
                    'TRACKING WARNINGS' if stale_count else 'LIVE'
                ),
            }
            if selected:
                values['Selected light'] = f"{selected['name']} → marker {selected.get('marker_id', '—')}"
            else:
                values['Selected light'] = 'No enabled lights'
            for key, value in values.items():
                self.status_labels[key].configure(text=value)
            self.update_overview_tree(lights)
            self.draw_preview(lights)

        self.after(100, self.ui_tick)

    def update_overview_tree(self, lights):
        current = set()
        for item in lights:
            iid = str(item.get('index'))
            current.add(iid)
            xyz = item.get('marker_xyz', (0.0, 0.0, 0.0))
            position = f'{xyz[0]:.2f}, {xyz[1]:.2f}, {xyz[2]:.2f}'
            if item.get('error'):
                angles, distance, state = '—', '—', item['error']
            else:
                angles = f"{item['pan']:.1f}° / {item['tilt']:.1f}°"
                distance = f"{item['distance']:.2f} m"
                if item.get('stale'):
                    state = 'TRACKING LOST'
                elif item.get('pan_limit') or item.get('tilt_limit'):
                    state = 'AT LIMIT'
                elif item.get('blackout'):
                    state = 'DIMMER LOCKED'
                else:
                    state = 'LIVE'
            values = (item.get('marker_id'), position, angles, distance, state)
            if self.overview_tree.exists(iid):
                self.overview_tree.item(iid, text=item.get('name'), values=values)
            else:
                self.overview_tree.insert('', 'end', iid=iid, text=item.get('name'), values=values)
        for iid in self.overview_tree.get_children():
            if iid not in current:
                self.overview_tree.delete(iid)

    def reset_preview_view(self):
        self.preview_yaw = -35.0
        self.preview_pitch = 25.0
        self.preview_zoom = 32.0
        self.draw_preview(self.live['lights'] if self.live else [])

    def preview_press(self, event):
        self.preview_drag = (event.x, event.y, self.preview_yaw, self.preview_pitch)

    def preview_motion(self, event):
        if not self.preview_drag:
            return
        x, y, yaw, pitch = self.preview_drag
        self.preview_yaw = yaw + (event.x - x) * 0.5
        self.preview_pitch = clamp(pitch - (event.y - y) * 0.5, -85.0, 85.0)
        self.draw_preview(self.live['lights'] if self.live else [])

    def preview_wheel(self, event):
        self.preview_zoom = clamp(self.preview_zoom * (1.12 if event.delta > 0 else 0.89), 5.0, 180.0)
        self.draw_preview(self.live['lights'] if self.live else [])

    def _project_preview(self, point, centre, width, height):
        x, y, z = point
        x -= centre[0]; y -= centre[1]; z -= centre[2]
        yaw = math.radians(self.preview_yaw)
        pitch = math.radians(self.preview_pitch)
        x1 = x * math.cos(yaw) - y * math.sin(yaw)
        y1 = x * math.sin(yaw) + y * math.cos(yaw)
        y2 = y1 * math.cos(pitch) - z * math.sin(pitch)
        z2 = y1 * math.sin(pitch) + z * math.cos(pitch)
        return width / 2 + x1 * self.preview_zoom, height / 2 - z2 * self.preview_zoom, y2

    def draw_preview(self, lights):
        if not hasattr(self, 'preview_canvas'):
            return
        c = self.preview_canvas
        c.delete('all')
        width = max(c.winfo_width(), 100)
        height = max(c.winfo_height(), 100)
        points = []
        for item in lights:
            points.extend([item.get('fixture_xyz', (0, 0, 0)), item.get('marker_xyz', (0, 0, 0))])
        if not points:
            c.create_text(width/2, height/2, text='Start FART to preview lights and PSN markers', fill='#c7d0d9')
            return
        centre = tuple(sum(p[i] for p in points) / len(points) for i in range(3))
        # Ground grid around scene centre.
        extent = max(5, int(max(max(abs(p[0]-centre[0]), abs(p[1]-centre[1])) for p in points) + 2))
        for n in range(-extent, extent + 1):
            for axis in (0, 1):
                a = (centre[0]-extent if axis == 0 else centre[0]+n, centre[1]+n if axis == 0 else centre[1]-extent, 0)
                b = (centre[0]+extent if axis == 0 else centre[0]+n, centre[1]+n if axis == 0 else centre[1]+extent, 0)
                pa = self._project_preview(a, centre, width, height)
                pb = self._project_preview(b, centre, width, height)
                c.create_line(pa[0], pa[1], pb[0], pb[1], fill='#26323b')
        marker_drawn = set()
        for item in sorted(lights, key=lambda x: self._project_preview(x.get('fixture_xyz',(0,0,0)), centre, width, height)[2]):
            fp = item.get('fixture_xyz', (0, 0, 0))
            mp = item.get('marker_xyz', (0, 0, 0))
            f2 = self._project_preview(fp, centre, width, height)
            m2 = self._project_preview(mp, centre, width, height)
            beam_colour = '#b24a4a' if item.get('stale') or item.get('error') else '#e3c85b'
            c.create_line(f2[0], f2[1], m2[0], m2[1], fill=beam_colour, width=2)
            c.create_rectangle(f2[0]-5, f2[1]-5, f2[0]+5, f2[1]+5, fill='#6fa8dc', outline='white')
            c.create_text(f2[0]+7, f2[1]-7, text=item.get('name','Light'), fill='#dce8f2', anchor='sw')
            marker_key = item.get('marker_id')
            if marker_key not in marker_drawn:
                marker_drawn.add(marker_key)
                c.create_oval(m2[0]-6, m2[1]-6, m2[0]+6, m2[1]+6, fill='#63d17a', outline='white')
                c.create_text(m2[0]+8, m2[1]-8, text=f'Marker {marker_key}', fill='#dff5e4', anchor='sw')

    def on_close(self):
        if self.psn_scanner:
            try:
                self.psn_scanner.stop()
            except Exception:
                pass
        self.stop()
        self.save()
        self.destroy()


def main():
    App().mainloop()


if __name__ == '__main__':
    main()
