"""Real loopback network tests for every plugin -- actual UDP sockets, not
just the pure parse/build helpers. This is the load-bearing test file for
the hand-rolled sACN implementation in particular, since nothing else in
this project has ever exercised it before.
"""
import errno
import struct
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fart.bus import ExternalInputBus, TrackerBank
from fart.config import ArtNetInConfig, ArtNetOutConfig, PSNInConfig, SACNInConfig, SACNOutConfig
from fart.plugins._artnet import build_artnet_dmx, parse_artnet_dmx
from fart.plugins._sacn import SourceSelector, build_sacn_dmx, parse_sacn_dmx, parse_sacn_packet, sacn_multicast_group
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


def multicast_sendto(sock, data, addr):
    """Some sandboxed CI network namespaces (seen on GitHub's hosted macOS
    runner) have no route to any multicast destination at all -- not even
    via loopback -- and every sendto() there fails immediately with
    ENETUNREACH/EHOSTUNREACH, unlike a real machine, where it's delivered
    over loopback normally. Skip rather than fail when that's the case."""
    try:
        sock.sendto(data, addr)
    except OSError as exc:
        if exc.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH):
            raise unittest.SkipTest(f"real multicast send unavailable in this environment: {exc}") from exc
        raise


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


def _psn_chunk(chunk_id, payload, sub=False):
    raw = (chunk_id & 0xFFFF) | ((len(payload) & 0x7FFF) << 16) | (0x80000000 if sub else 0)
    return struct.pack("<I", raw) + payload


def _psn_data_packet(trackers):
    """trackers: {id: [(field_id, payload_bytes), ...]} -> a PSN data packet."""
    tracker_chunks = b"".join(
        _psn_chunk(tid, b"".join(_psn_chunk(fid, payload, sub=True) for fid, payload in fields), sub=True)
        for tid, fields in trackers.items()
    )
    return _psn_chunk(0x6755, _psn_chunk(0x0001, tracker_chunks, sub=True), sub=True)


def _psn_info_packet(system_name, names):
    tracker_chunks = b"".join(
        _psn_chunk(tid, _psn_chunk(0x0000, name.encode() + b"\x00", sub=True), sub=True)
        for tid, name in names.items()
    )
    return _psn_chunk(0x6756, _psn_chunk(0x0001, system_name.encode(), sub=True)
                      + _psn_chunk(0x0002, tracker_chunks, sub=True), sub=True)


class PSNExtraFieldsTests(unittest.TestCase):
    """Everything a PSN sender includes is recorded for the PSN In tab, not
    just the position FART tracks with."""

    def setUp(self):
        self.bank = TrackerBank()
        self.plugin = PSNInPlugin()
        self.plugin._positions = self.bank

    def test_speed_orientation_accel_target_status_and_timestamp_are_all_decoded(self):
        f3 = lambda *v: struct.pack("<fff", *v)  # noqa: E731
        self.plugin._decode(_psn_data_packet({7: [
            (0x0000, f3(1.0, 2.0, 3.0)),
            (0x0001, f3(0.5, 0.0, 0.0)),
            (0x0002, f3(10.0, 20.0, 30.0)),
            (0x0003, struct.pack("<f", 0.75)),
            (0x0004, f3(0.0, 0.1, 0.0)),
            (0x0005, f3(9.0, 8.0, 7.0)),
            (0x0006, struct.pack("<Q", 123456789)),
        ]}))
        detail = self.bank.snapshot_detail()[7]
        self.assertEqual(detail["position"], (1.0, 2.0, 3.0))
        self.assertEqual(detail["speed"], (0.5, 0.0, 0.0))
        self.assertEqual(detail["orientation"], (10.0, 20.0, 30.0))
        self.assertAlmostEqual(detail["status"], 0.75)
        self.assertEqual(detail["accel"][1], struct.unpack("<f", struct.pack("<f", 0.1))[0])
        self.assertEqual(detail["target"], (9.0, 8.0, 7.0))
        self.assertEqual(detail["psn_time"], 123456789)

    def test_position_is_still_decoded_when_it_is_not_the_last_field(self):
        # The old decoder stopped at the position chunk and dropped the rest;
        # fields *after* it, or position *after* other fields, must both work.
        self.plugin._decode(_psn_data_packet({3: [
            (0x0001, struct.pack("<fff", 1.0, 1.0, 1.0)),
            (0x0000, struct.pack("<fff", 4.0, 5.0, 6.0)),
        ]}))
        self.assertEqual(self.bank.get(3)[:3], (4.0, 5.0, 6.0))
        self.assertEqual(self.bank.snapshot_detail()[3]["speed"], (1.0, 1.0, 1.0))

    def test_a_marker_with_no_position_chunk_still_shows_up(self):
        self.plugin._decode(_psn_data_packet({9: [(0x0003, struct.pack("<f", 1.0))]}))
        detail = self.bank.snapshot_detail()
        self.assertIn(9, detail)
        self.assertIsNone(detail[9]["position"])

    def test_short_or_unknown_fields_are_skipped_not_fatal(self):
        self.plugin._decode(_psn_data_packet({5: [
            (0x0001, b"\x00\x00"),                    # speed chunk too short
            (0x0042, struct.pack("<fff", 1, 2, 3)),    # unknown field id
            (0x0000, struct.pack("<fff", 1, 2, 3)),
        ]}))
        detail = self.bank.snapshot_detail()[5]
        self.assertNotIn("speed", detail)
        self.assertEqual(detail["position"], (1.0, 2.0, 3.0))

    def test_info_packet_supplies_system_and_tracker_names(self):
        self.plugin._decode(_psn_info_packet("Stage Tracker", {7: "Lead", 8: "Backup"}))
        self.assertEqual(self.bank.system_name, "Stage Tracker")
        detail = self.bank.snapshot_detail()
        self.assertEqual(detail[7]["name"], "Lead")
        self.assertEqual(detail[8]["name"], "Backup")

    def test_every_data_packet_counts_toward_the_trackers_receive_rate(self):
        for _ in range(3):
            self.plugin._decode(_psn_data_packet({1: [(0x0000, struct.pack("<fff", 0, 0, 0))]}))
        self.assertEqual(len(self.bank.arrivals[1]), 3)


class SACNPacketParsingTests(unittest.TestCase):
    def test_parse_packet_exposes_source_priority_and_sequence(self):
        cid = bytes(range(16))
        packet = parse_sacn_packet(build_sacn_dmx(cid, "src", 77, 5, bytes([9] * 512), priority=150))
        self.assertEqual(packet["cid"], cid)
        self.assertEqual(packet["priority"], 150)
        self.assertEqual(packet["sequence"], 77)
        self.assertEqual(packet["universe"], 5)
        self.assertFalse(packet["terminated"])

    def test_non_zero_start_code_is_not_dmx_levels(self):
        raw = bytearray(build_sacn_dmx(bytes(16), "src", 0, 1, bytes(512)))
        raw[125] = 0xDD  # per-address-priority packet
        self.assertIsNone(parse_sacn_packet(bytes(raw)))
        self.assertIsNone(parse_sacn_dmx(bytes(raw)))

    def test_terminated_option_is_reported(self):
        raw = bytearray(build_sacn_dmx(bytes(16), "src", 0, 1, bytes(512)))
        raw[112] = 0x40
        self.assertTrue(parse_sacn_packet(bytes(raw))["terminated"])


class SACNSourceSelectorTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.sel = SourceSelector(clock=lambda: self.now)

    def test_single_source_is_accepted(self):
        self.assertTrue(self.sel.accept(1, b"A", 100, 1))
        self.assertTrue(self.sel.accept(1, b"A", 100, 2))

    def test_two_equal_priority_sources_do_not_alternate(self):
        self.assertTrue(self.sel.accept(1, b"A", 100, 1))
        self.assertFalse(self.sel.accept(1, b"B", 100, 1), "second source must not steal the universe")
        self.assertTrue(self.sel.accept(1, b"A", 100, 2))
        self.assertFalse(self.sel.accept(1, b"B", 100, 2))

    def test_higher_priority_source_takes_over(self):
        self.assertTrue(self.sel.accept(1, b"A", 100, 1))
        self.assertTrue(self.sel.accept(1, b"B", 150, 1))
        self.assertFalse(self.sel.accept(1, b"A", 100, 2))

    def test_failover_after_the_locked_source_goes_silent(self):
        self.assertTrue(self.sel.accept(1, b"A", 100, 1))
        self.assertFalse(self.sel.accept(1, b"B", 100, 1))
        self.now += 3.0  # past the 2.5 s network-data-loss timeout
        self.assertTrue(self.sel.accept(1, b"B", 100, 2))

    def test_stream_terminated_releases_the_universe_immediately(self):
        self.assertTrue(self.sel.accept(1, b"A", 100, 1))
        self.assertFalse(self.sel.accept(1, b"A", 100, 2, terminated=True))
        self.assertTrue(self.sel.accept(1, b"B", 100, 1))

    def test_duplicate_and_late_packets_are_dropped_but_wraparound_is_fine(self):
        self.assertTrue(self.sel.accept(1, b"A", 100, 10))
        self.assertFalse(self.sel.accept(1, b"A", 100, 10), "duplicate")
        self.assertFalse(self.sel.accept(1, b"A", 100, 5), "late (5 behind)")
        self.assertTrue(self.sel.accept(1, b"A", 100, 11))
        self.assertTrue(self.sel.accept(1, b"A", 100, 200))
        self.assertTrue(self.sel.accept(1, b"A", 100, 255))
        self.assertTrue(self.sel.accept(1, b"A", 100, 0), "sequence wraps 255 -> 0")
        self.assertFalse(self.sel.accept(1, b"A", 100, 250), "17 behind after the wrap is still late")

    def test_universes_are_independent(self):
        self.assertTrue(self.sel.accept(1, b"A", 100, 1))
        self.assertTrue(self.sel.accept(2, b"B", 100, 1))


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
                multicast_sendto(sock, packet, (config.multicast, config.port))
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
                try:
                    sender.send({63: frame})
                except OSError as exc:
                    if exc.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH):
                        raise unittest.SkipTest(
                            f"real multicast send unavailable in this environment: {exc}") from exc
                    raise
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
