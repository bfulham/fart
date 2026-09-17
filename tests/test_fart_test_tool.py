import math
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import fart_test_tool as tool  # noqa: E402


class ArgsStub:
    """Minimal stand-in for argparse.Namespace with FixtureConfig defaults."""
    fixture_x = 0.0
    fixture_y = -8.0
    fixture_z = 5.0
    pan_zero_bearing = 0.0
    tilt_zero_elevation = 0.0
    pan_direction = 1
    tilt_direction = -1
    pan_offset = 0.0
    tilt_offset = 0.0
    pan_min = -270.0
    pan_max = 270.0
    tilt_min = -135.0
    tilt_max = 135.0
    pan_coarse = 1
    pan_fine = 2
    tilt_coarse = 3
    tilt_fine = 4


class PSNPacketTests(unittest.TestCase):
    def test_build_psn_packet_structure(self):
        packet = tool.build_psn_packet([(7, 1.25, 2.5, 3.75)])
        raw = struct.unpack_from("<I", packet, 0)[0]
        self.assertEqual(raw & 0xFFFF, tool.PSN_DATA_PACKET)
        self.assertTrue(raw & 0x80000000)

    def test_build_psn_packet_multiple_markers_are_independent(self):
        packet = tool.build_psn_packet([(1, 1.0, 2.0, 3.0), (2, 4.0, 5.0, 6.0)])
        # Both marker IDs' float payloads should appear somewhere in the packet.
        self.assertIn(struct.pack("<fff", 1.0, 2.0, 3.0), packet)
        self.assertIn(struct.pack("<fff", 4.0, 5.0, 6.0), packet)


class PSNRoundTripAgainstFartTests(unittest.TestCase):
    """Cross-checks the tool's PSN encoder against fart.py's actual decoder,
    so this file drifting out of sync with the real wire format is caught."""

    def test_decodes_correctly_with_real_psn_receiver(self):
        try:
            import fart
        except ImportError as exc:
            self.skipTest(f"fart.py could not be imported here (likely no tkinter): {exc}")

        tracker_state = fart.TrackerBank()
        receiver = fart.PSNReceiver(
            "236.10.10.10", 56565, "0.0.0.0", 7, tracker_state, lambda _msg: None
        )
        packet = tool.build_psn_packet([(7, 1.25, -2.5, 3.75)])
        receiver._decode(packet)
        x, y, z, timestamp = tracker_state.get(7)
        self.assertAlmostEqual(x, 1.25, places=5)
        self.assertAlmostEqual(y, -2.5, places=5)
        self.assertAlmostEqual(z, 3.75, places=5)
        self.assertGreater(timestamp, 0.0)


class ArtNetTests(unittest.TestCase):
    def test_build_and_parse_round_trip(self):
        dmx = bytes([10, 20, 30] + [0] * 509)
        packet = tool.build_artnet_dmx(3, dmx)
        universe, parsed = tool.parse_artnet_dmx(packet)
        self.assertEqual(universe, 3)
        self.assertEqual(parsed[:3], bytes([10, 20, 30]))
        self.assertEqual(len(parsed), 512)

    def test_parse_rejects_malformed_packets(self):
        self.assertIsNone(tool.parse_artnet_dmx(b"not art-net"))
        self.assertIsNone(tool.parse_artnet_dmx(b"Art-Net\x00" + b"\x00" * 9))

    def test_dmx16_fraction(self):
        self.assertAlmostEqual(tool.dmx16_fraction(0, 0), 0.0)
        self.assertAlmostEqual(tool.dmx16_fraction(255, 255), 1.0)
        self.assertAlmostEqual(tool.dmx16_fraction(128, 0), 0.50002, places=4)


class GeometryTests(unittest.TestCase):
    def test_expected_pan_tilt_matches_calculate_aim_when_available(self):
        args = ArgsStub()
        marker = (5.0, 0.0, 4.0)
        pan, tilt = tool.expected_pan_tilt(marker, args)
        try:
            import fart
        except ImportError as exc:
            self.skipTest(f"fart.py could not be imported here (likely no tkinter): {exc}")
        fixture = fart.FixtureConfig()
        _b, _e, fart_pan, fart_tilt, _d = fart.calculate_aim(fixture, *marker)
        self.assertAlmostEqual(pan, fart_pan, places=6)
        self.assertAlmostEqual(tilt, fart_tilt, places=6)

    def test_decoded_pan_tilt_inverts_expected_encoding(self):
        args = ArgsStub()
        pan, tilt = 45.0, -10.0
        pan_fraction = (pan - args.pan_min) / (args.pan_max - args.pan_min)
        tilt_fraction = (tilt - args.tilt_min) / (args.tilt_max - args.tilt_min)
        n_pan = round(pan_fraction * 65535)
        n_tilt = round(tilt_fraction * 65535)
        dmx = bytearray(10)
        dmx[0] = (n_pan >> 8) & 255
        dmx[1] = n_pan & 255
        dmx[2] = (n_tilt >> 8) & 255
        dmx[3] = n_tilt & 255
        decoded_pan, decoded_tilt = tool.decoded_pan_tilt(bytes(dmx), args)
        self.assertAlmostEqual(decoded_pan, pan, places=2)
        self.assertAlmostEqual(decoded_tilt, tilt, places=2)


class MarkerPatternTests(unittest.TestCase):
    def test_static_pattern_never_moves(self):
        self.assertEqual(tool.marker_position("static", 100.0, 1, 2, 3, 5, 0.5), (1, 2, 3))

    def test_circle_pattern_starts_at_radius_offset(self):
        x, y, z = tool.marker_position("circle", 0.0, 0.0, 0.0, 1.7, 2.0, 0.2)
        self.assertAlmostEqual(x, 2.0)
        self.assertAlmostEqual(y, 0.0)
        self.assertAlmostEqual(z, 1.7)

    def test_circle_pattern_traces_a_circle(self):
        for t in (0.5, 1.3, 4.0):
            x, y, _z = tool.marker_position("circle", t, 0.0, 0.0, 0.0, 3.0, 0.2)
            self.assertAlmostEqual(math.hypot(x, y), 3.0, places=6)

    def test_unknown_pattern_raises(self):
        with self.assertRaises(ValueError):
            tool.marker_position("spiral", 0.0, 0, 0, 0, 1, 1)


class ChannelRangeParsingTests(unittest.TestCase):
    def test_parses_mixed_ranges_and_singles(self):
        self.assertEqual(tool.parse_channel_ranges("1-3,7,10-11"), [1, 2, 3, 7, 10, 11])

    def test_empty_spec(self):
        self.assertEqual(tool.parse_channel_ranges(""), [])


if __name__ == "__main__":
    unittest.main()
