import math
import struct
import unittest

import fart


def chunk(chunk_id, payload, sub=False):
    raw = chunk_id | (len(payload) << 16)
    if sub:
        raw |= 0x80000000
    return struct.pack('<I', raw) + payload


class GeometryTests(unittest.TestCase):
    def test_marker_directly_ahead_and_lower(self):
        fixture = fart.FixtureConfig(x=0.0, y=-5.0, z=4.0)
        bearing, elevation, pan, tilt, distance = fart.calculate_aim(
            fixture, 0.0, 0.0, 1.5
        )
        self.assertAlmostEqual(bearing, 0.0, places=6)
        self.assertAlmostEqual(elevation, -26.565051, places=5)
        self.assertAlmostEqual(pan, 0.0, places=6)
        self.assertAlmostEqual(tilt, elevation, places=6)
        self.assertAlmostEqual(distance, math.sqrt(31.25), places=6)

    def test_marker_stage_right(self):
        fixture = fart.FixtureConfig(x=0.0, y=-5.0, z=4.0)
        bearing, elevation, pan, tilt, _ = fart.calculate_aim(
            fixture, 5.0, 0.0, 4.0
        )
        self.assertAlmostEqual(bearing, 45.0, places=6)
        self.assertAlmostEqual(elevation, 0.0, places=6)
        self.assertAlmostEqual(pan, 45.0, places=6)
        self.assertAlmostEqual(tilt, 0.0, places=6)

    def test_midpoint_maps_to_16_bit_midpoint(self):
        fixture = fart.FixtureConfig(
            pan_min=-270.0,
            pan_max=270.0,
            tilt_min=-135.0,
            tilt_max=135.0,
            pan_coarse=1,
            pan_fine=2,
            tilt_coarse=3,
            tilt_fine=4,
            dimmer=5,
            shutter=0,
        )
        frame = bytearray(512)
        fart.write_fixture_to_frame(frame, fixture, 0.0, 0.0, 0.5, False)
        self.assertEqual(frame[0:5], bytes([128, 0, 128, 0, 128]))

    def test_blackout_closes_intensity(self):
        fixture = fart.FixtureConfig(dimmer=5, shutter=6, shutter_open=30)
        frame = bytearray(512)
        fart.write_fixture_to_frame(frame, fixture, 0.0, 0.0, 1.0, True)
        self.assertEqual(frame[4], 0)
        self.assertEqual(frame[5], 0)


class PSNTests(unittest.TestCase):
    def test_openfollow_flagged_position_leaf_is_decoded(self):
        tracker_state = fart.TrackerBank()
        discovered = []
        receiver = fart.PSNReceiver(
            '236.10.10.10', 56565, '0.0.0.0', 7,
            tracker_state, lambda _message: None, discovered.append
        )
        position = chunk(0x0000, struct.pack('<fff', 1.25, 2.5, 3.75), sub=True)
        tracker = chunk(7, position, sub=True)
        tracker_list = chunk(0x0001, tracker, sub=True)
        packet = chunk(0x6755, tracker_list, sub=True)

        receiver._decode(packet)
        x, y, z, timestamp = tracker_state.get(7)
        self.assertEqual(discovered, [7])
        self.assertAlmostEqual(x, 1.25, places=6)
        self.assertAlmostEqual(y, 2.5, places=6)
        self.assertAlmostEqual(z, 3.75, places=6)
        self.assertGreater(timestamp, 0.0)
        self.assertEqual(receiver.position_count, 1)

    def test_multiple_tracker_positions_are_retained(self):
        tracker_state = fart.TrackerBank()
        receiver = fart.PSNReceiver(
            '236.10.10.10', 56565, '0.0.0.0', 1,
            tracker_state, lambda _message: None
        )
        tracker1 = chunk(1, chunk(0x0000, struct.pack('<fff', 1.0, 2.0, 3.0), sub=True), sub=True)
        tracker2 = chunk(2, chunk(0x0000, struct.pack('<fff', 4.0, 5.0, 6.0), sub=True), sub=True)
        packet = chunk(0x6755, chunk(0x0001, tracker1 + tracker2, sub=True), sub=True)
        receiver._decode(packet)
        self.assertEqual(tracker_state.get(1)[:3], (1.0, 2.0, 3.0))
        self.assertEqual(tracker_state.get(2)[:3], (4.0, 5.0, 6.0))



class ChannelTests(unittest.TestCase):
    def test_fixture_channel_map(self):
        fixture = fart.FixtureConfig(
            pan_coarse=118,
            pan_fine=119,
            tilt_coarse=120,
            tilt_fine=121,
            dimmer=102,
            dimmer_fine=103,
            shutter=101,
        )
        self.assertEqual(fart.fixture_channels(fixture), {
            'pan coarse': 118,
            'pan fine': 119,
            'tilt coarse': 120,
            'tilt fine': 121,
            'dimmer': 102,
            'dimmer fine': 103,
            'shutter': 101,
        })


if __name__ == '__main__':
    unittest.main()
