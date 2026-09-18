"""PSN (PosiStageNet) position input, as sent by OpenFollow. Ported from
PSNReceiver in fart.py v1 with behaviour unchanged."""
from __future__ import annotations

import socket
import struct
import threading
import time

DATA_PACKET = 0x6755
DATA_TRACKER_LIST = 0x0001
DATA_TRACKER_POS = 0x0000


def _chunks(data, start, length):
    end = min(len(data), start + length)
    pos = start
    while pos + 4 <= end:
        raw = struct.unpack_from("<I", data, pos)[0]
        cid = raw & 0xFFFF
        data_len = (raw >> 16) & 0x7FFF
        sub = bool(raw & 0x80000000)
        body = pos + 4
        body_end = body + data_len
        if body_end > end:
            break
        yield cid, sub, body, data_len
        pos = body_end


class PSNInPlugin:
    def __init__(self, log=None, on_discovered=None):
        self.log = log or (lambda _msg: None)
        self.on_discovered = on_discovered
        self.marker = None
        self.discovered = set()
        self.sock = None
        self.stop_evt = threading.Event()
        self.packet_count = 0
        self.data_packet_count = 0
        self.position_count = 0
        self.last_packet_time = 0.0
        self.last_position_time = 0.0
        self._positions = None
        self._thread = None

    def start(self, config, positions):
        """config: a PSNInConfig. positions: a bus.TrackerBank."""
        self._positions = positions
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("", config.port))
        iface = (config.interface or "").strip() or "0.0.0.0"
        membership = socket.inet_aton(config.multicast) + socket.inet_aton(iface)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        self.sock.settimeout(0.3)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.log(f"PSN in: listening on {config.multicast}:{config.port} via {iface}")

    def _loop(self):
        while not self.stop_evt.is_set():
            try:
                data, _ = self.sock.recvfrom(65535)
                self._decode(data)
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as exc:
                self.log("PSN decode error: " + str(exc))

    def _decode(self, data):
        self.packet_count += 1
        self.last_packet_time = time.monotonic()
        if len(data) < 4:
            return
        raw = struct.unpack_from("<I", data, 0)[0]
        root_id = raw & 0xFFFF
        root_len = (raw >> 16) & 0x7FFF
        if root_id != DATA_PACKET:
            return
        self.data_packet_count += 1
        for cid, sub, body, length in _chunks(data, 4, root_len):
            if cid != DATA_TRACKER_LIST or not sub:
                continue
            for tracker_id, tracker_sub, t_body, t_len in _chunks(data, body, length):
                if not tracker_sub:
                    continue
                if tracker_id not in self.discovered:
                    self.discovered.add(tracker_id)
                    if self.on_discovered:
                        self.on_discovered(tracker_id)
                for field_id, _field_sub, f_body, f_len in _chunks(data, t_body, t_len):
                    # OpenFollow (via pypsn) sets the high sub-chunk flag bit
                    # on leaf chunks too, including the 12-byte position
                    # chunk -- don't reject position merely because that
                    # flag is set.
                    if field_id == DATA_TRACKER_POS and f_len >= 12:
                        x, y, z = struct.unpack_from("<fff", data, f_body)
                        self._positions.update(tracker_id, x, y, z)
                        self.position_count += 1
                        self.last_position_time = time.monotonic()
                        break

    def stats(self):
        return {
            "packets": self.packet_count,
            "data_packets": self.data_packet_count,
            "positions": self.position_count,
            "last_packet_age": (time.monotonic() - self.last_packet_time) if self.last_packet_time else None,
            "last_position_age": (time.monotonic() - self.last_position_time) if self.last_position_time else None,
        }

    def stop(self):
        self.stop_evt.set()
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)
