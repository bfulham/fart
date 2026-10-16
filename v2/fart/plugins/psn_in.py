"""PSN (PosiStageNet) position input, as sent by OpenFollow. Ported from
PSNReceiver in fart.py v1 with behaviour unchanged."""
from __future__ import annotations

import socket
import struct
import threading
import time

DATA_PACKET = 0x6755
INFO_PACKET = 0x6756
DATA_TRACKER_LIST = 0x0001
DATA_TRACKER_POS = 0x0000
DATA_TRACKER_SPEED = 0x0001
DATA_TRACKER_ORI = 0x0002
DATA_TRACKER_STATUS = 0x0003
DATA_TRACKER_ACCEL = 0x0004
DATA_TRACKER_TRGTPOS = 0x0005
DATA_TRACKER_TIMESTAMP = 0x0006
INFO_SYSTEM_NAME = 0x0001
INFO_TRACKER_LIST = 0x0002
INFO_TRACKER_NAME = 0x0000

# Vector fields a tracker can carry besides position, by PSN field id.
_VECTOR_FIELDS = {
    DATA_TRACKER_SPEED: "speed",
    DATA_TRACKER_ORI: "orientation",
    DATA_TRACKER_ACCEL: "accel",
    DATA_TRACKER_TRGTPOS: "target",
}


def _text(data, start, length):
    return bytes(data[start:start + length]).split(b"\x00", 1)[0].decode("utf-8", errors="replace")


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
        if root_id == INFO_PACKET:
            self._decode_info(data, root_len)
            return
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
                extras = {}
                for field_id, _field_sub, f_body, f_len in _chunks(data, t_body, t_len):
                    # OpenFollow (via pypsn) sets the high sub-chunk flag bit
                    # on leaf chunks too, including the 12-byte position
                    # chunk -- don't reject a field merely because that
                    # flag is set.
                    if field_id == DATA_TRACKER_POS and f_len >= 12:
                        x, y, z = struct.unpack_from("<fff", data, f_body)
                        self._positions.update(tracker_id, x, y, z)
                        self.position_count += 1
                        self.last_position_time = time.monotonic()
                    elif field_id in _VECTOR_FIELDS and f_len >= 12:
                        extras[_VECTOR_FIELDS[field_id]] = struct.unpack_from("<fff", data, f_body)
                    elif field_id == DATA_TRACKER_STATUS and f_len >= 4:
                        extras["status"] = struct.unpack_from("<f", data, f_body)[0]
                    elif field_id == DATA_TRACKER_TIMESTAMP and f_len >= 8:
                        extras["psn_time"] = struct.unpack_from("<Q", data, f_body)[0]
                # Always recorded, even with no extra fields, so the
                # tracker's receive rate counts every packet it appears in.
                self._positions.update_extras(tracker_id, **extras)

    def _decode_info(self, data, root_len):
        """PSN info packet: system name and per-tracker names. Display-only."""
        for cid, sub, body, length in _chunks(data, 4, root_len):
            if cid == INFO_SYSTEM_NAME:
                self._positions.system_name = _text(data, body, length)
            elif cid == INFO_TRACKER_LIST and sub:
                for tracker_id, tracker_sub, t_body, t_len in _chunks(data, body, length):
                    if not tracker_sub:
                        continue
                    for field_id, _field_sub, f_body, f_len in _chunks(data, t_body, t_len):
                        if field_id == INFO_TRACKER_NAME:
                            self._positions.set_name(tracker_id, _text(data, f_body, f_len))

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
