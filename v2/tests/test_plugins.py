"""Real loopback network tests for every plugin -- actual UDP sockets, not
just the pure parse/build helpers. This is the load-bearing test file for
the hand-rolled sACN implementation in particular, since nothing else in
this project has ever exercised it before.
"""
import struct
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fart.bus import ExternalInputBus, TrackerBank
from fart.config import ArtNetInConfig, ArtNetOutConfig, PSNInConfig, SACNInConfig, SACNOutConfig
from fart.plugins._artnet import build_artnet_dmx, parse_artnet_dmx
from fart.plugins._sacn import build_sacn_dmx, parse_sacn_dmx, sacn_multicast_group
from fart.plugins.artnet_in import ArtNetInPlugin
from fart.plugins.artnet_out import ArtNetOutPlugin
from fart.plugins.psn_in import PSNInPlugin, _chunks
from fart.plugins.sacn_in import SACNInPlugin
from fart.plugins.sacn_out import SACNOutPlugin


def wait_until(predicate, timeout=2.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def psn_chunk(chunk_id, payload, sub=False):
    raw = (chunk_id & 0xFFFF) | ((len(payload) & 0x7FFF) << 16)
    if sub:
        raw |= 0x80000000
    return struct.pack("<I", raw) + payload


def build_psn_packet(marker_id, x, y, z):
    pos = psn_chunk(0x0000, struct.pack("<fff", x, y, z), sub=True)
    tracker = psn_chunk(marker_id, pos, sub=True)
    tracker_list = psn_chunk(0x0001, tracker, sub=True)
    return psn_chunk(0x6755, tracker_list, sub=True)


class ArtNetWireTests(unittest.TestCase):
    def test_build_parse_round_trip(self):
        packet = build_artnet_dmx(3, bytes([1, 2, 3]))
        universe, dmx = parse_artnet_dmx(packet)
        self.assertEqual(universe, 3)
        self.assertEqual(len(dmx), 512)
        self.assertEqual(dmx[:3], bytes([1, 2, 3]))


class SACNWireTests(unittest.TestCase):
    def test_build_parse_round_trip(self):
        cid = bytes(range(16))
        packet = build_sacn_dmx(cid, "test source", 1, 7, bytes([9, 8, 7]))
        self.assertEqual(len(packet), 638)
        parsed = parse_sacn_dmx(packet)
        self.assertIsNotNone(parsed)
        universe, dmx = parsed
        self.assertEqual(universe, 7)
        self.assertEqual(len(dmx), 512)
        self.assertEqual(dmx[:3], bytes([9, 8, 7]))

    def test_multicast_group_formula(self):
        self.assertEqual(sacn_multicast_group(1), "239.255.0.1")
        self.assertEqual(sacn_multicast_group(63999), "239.255.249.255")

    def test_rejects_non_data_packets(self):
        junk = bytes(700)
        self.assertIsNone(parse_sacn_dmx(junk))


class PSNPluginLoopbackTests(unittest.TestCase):
    def test_real_udp_multicast_round_trip(self):
        positions = TrackerBank()
        plugin = PSNInPlugin()
        config = PSNInConfig(multicast="236.10.10.11", port=56599, interface="0.0.0.0")
        plugin.start(config, positions)
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            packet = build_psn_packet(7, 1.25, -2.5, 3.75)
            deadline = time.monotonic() + 2.0
            x = y = z = None
            while time.monotonic() < deadline:
                sock.sendto(packet, (config.multicast, config.port))
                _x, _y, _z, t = positions.get(7)
                if t > 0:
                    x, y, z = _x, _y, _z
                    break
                time.sleep(0.05)
            sock.close()
            self.assertIsNotNone(x, "no PSN position was received over real multicast loopback")
            self.assertAlmostEqual(x, 1.25, places=5)
            self.assertAlmostEqual(y, -2.5, places=5)
            self.assertAlmostEqual(z, 3.75, places=5)
        finally:
            plugin.stop()

    def test_chunks_bounds_checking_rejects_truncated_data(self):
        # A chunk claiming a body longer than the buffer must not be yielded.
        truncated = struct.pack("<I", 0x0000 | (100 << 16))
        self.assertEqual(list(_chunks(truncated, 0, len(truncated))), [])


class ArtNetPluginLoopbackTests(unittest.TestCase):
    def test_out_to_in_real_udp_round_trip(self):
        bus = ExternalInputBus()
        receiver = ArtNetInPlugin()
        receiver.start(ArtNetInConfig(), bus)
        sender = ArtNetOutPlugin()
        sender.start(ArtNetOutConfig(target_ip="127.0.0.1"))
        try:
            frame = bytearray(512)
            frame[0] = 111
            frame[1] = 222
            found = wait_until(lambda: (sender.send({4: frame}), bus.get(4)[0] is not None)[1])
            self.assertTrue(found, "no Art-Net frame was received over real UDP loopback")
            received, _ts = bus.get(4)
            self.assertEqual(received[0], 111)
            self.assertEqual(received[1], 222)
        finally:
            receiver.stop()
            sender.close()


class SACNPluginLoopbackTests(unittest.TestCase):
    def test_out_to_in_real_multicast_round_trip(self):
        bus = ExternalInputBus()
        receiver = SACNInPlugin()
        receiver.start(SACNInConfig(universe=63), bus)
        sender = SACNOutPlugin()
        sender.start(SACNOutConfig(universes=[63]))
        try:
            frame = bytearray(512)
            frame[0] = 44
            frame[1] = 55

            def attempt():
                sender.send({63: frame})
                return bus.get(63)[0] is not None

            found = wait_until(attempt, timeout=3.0)
            self.assertTrue(found, "no sACN frame was received over real UDP multicast loopback "
                                    "(hand-rolled E1.31 implementation)")
            received, _ts = bus.get(63)
            self.assertEqual(received[0], 44)
            self.assertEqual(received[1], 55)
        finally:
            receiver.stop()
            sender.close()


if __name__ == "__main__":
    unittest.main()
