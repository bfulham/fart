import math
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

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
        self.assertAlmostEqual(tilt, -elevation, places=6)
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


    def test_zoom_iris_focus_mapping(self):
        fixture = fart.FixtureConfig(
            dimmer=0, shutter=0,
            zoom=10, zoom_fine=11, iris=12, focus=13, focus_fine=14,
        )
        frame = bytearray(512)
        fart.write_fixture_to_frame(
            frame, fixture, 0.0, 0.0, 0.0, False,
            zoom=0.5, iris=1.0, focus=0.25,
        )
        self.assertEqual(frame[9:14], bytes([128, 0, 255, 64, 0]))

    def test_beam_control_reverse(self):
        fixture = fart.FixtureConfig(
            dimmer=0, shutter=0, zoom=10, iris=11, focus=12,
            zoom_reverse=True, iris_reverse=True, focus_reverse=True,
        )
        frame = bytearray(512)
        fart.write_fixture_to_frame(
            frame, fixture, 0.0, 0.0, 0.0, False,
            zoom=0.0, iris=0.25, focus=1.0,
        )
        self.assertEqual(frame[9], 255)
        self.assertEqual(frame[10], 191)
        self.assertEqual(frame[11], 0)

    def test_calibration_solver_recovers_fixture_position(self):
        true_fixture = fart.FixtureConfig(
            x=0.0, y=-8.0, z=5.0,
            pan_zero_bearing=5.0, tilt_zero_elevation=-2.0,
            pan_min=-270.0, pan_max=270.0, tilt_min=-135.0, tilt_max=135.0,
        )
        targets = [(0, 0, 0), (5, 0, 0), (-5, 0, 0), (0, 5, 0), (0, -5, 0), (0, 0, 1.7)]
        samples = []
        for target in targets:
            _bearing, _elevation, pan, tilt, _distance = fart.calculate_aim(true_fixture, *target)
            samples.append((*target, pan, tilt))
        start_fixture = fart.FixtureConfig(
            x=0.0, y=-8.0, z=5.0,
            pan_zero_bearing=0.0, tilt_zero_elevation=0.0,
            pan_min=-270.0, pan_max=270.0, tilt_min=-135.0, tilt_max=135.0,
        )
        solved, rms = fart.solve_fixture_calibration(start_fixture, samples)
        self.assertLess(rms, 0.1)
        self.assertAlmostEqual(solved.x, true_fixture.x, delta=0.05)
        self.assertAlmostEqual(solved.y, true_fixture.y, delta=0.05)
        self.assertAlmostEqual(solved.z, true_fixture.z, delta=0.1)
        self.assertAlmostEqual(solved.pan_zero_bearing, true_fixture.pan_zero_bearing, delta=0.1)
        self.assertAlmostEqual(solved.tilt_zero_elevation, true_fixture.tilt_zero_elevation, delta=0.3)

    def test_auto_zoom_for_distance(self):
        fixture = fart.FixtureConfig(zoom_angle_at_0=2.0, zoom_angle_at_100=45.0)
        value, angle, available = fart.auto_zoom_for_distance(fixture, distance=20.0, target_diameter_m=1.0, fallback_zoom=0.5)
        self.assertTrue(available)
        self.assertAlmostEqual(angle, math.degrees(2.0 * math.atan(0.5 / 20.0)), places=6)
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 1.0)

        no_model = fart.FixtureConfig()
        value, angle, available = fart.auto_zoom_for_distance(no_model, distance=20.0, target_diameter_m=1.0, fallback_zoom=0.42)
        self.assertFalse(available)
        self.assertIsNone(angle)
        self.assertAlmostEqual(value, 0.42)

    def test_auto_beam_uses_iris_when_zoom_not_tight_enough(self):
        fixture = fart.FixtureConfig(
            zoom=10,
            iris=11,
            zoom_angle_at_0=2.0,
            zoom_angle_at_100=45.0,
            iris_physical_at_0=1.0,
            iris_physical_at_100=0.0,
        )
        zoom, iris, angle, zoom_available, iris_used = fart.auto_beam_for_distance(
            fixture, distance=100.0, target_diameter_m=1.0, fallback_zoom=0.5, fallback_iris=1.0
        )
        self.assertTrue(zoom_available)
        self.assertTrue(iris_used)
        self.assertAlmostEqual(zoom, 0.0)
        self.assertGreater(iris, 0.0)
        self.assertLess(iris, 1.0)
        self.assertAlmostEqual(angle, math.degrees(2.0 * math.atan(0.5 / 100.0)), places=6)


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
            iris=194,
            zoom=195,
            zoom_fine=196,
            focus=197,
            focus_fine=198,
        )
        self.assertEqual(fart.fixture_channels(fixture), {
            'pan coarse': 118,
            'pan fine': 119,
            'tilt coarse': 120,
            'tilt fine': 121,
            'dimmer': 102,
            'dimmer fine': 103,
            'shutter': 101,
            'zoom': 195,
            'zoom fine': 196,
            'iris': 194,
            'focus': 197,
            'focus fine': 198,
            'console mode': 0,
            'console marker': 0,
        })


class GDTFImportTests(unittest.TestCase):
    def make_gdtf(self):
        xml = '<?xml version="1.0" encoding="UTF-8"?>\n<FixtureType>\n  <DMXModes>\n    <DMXMode Name="Basic">\n      <DMXChannels>\n        <DMXChannel Offset="1"><LogicalChannel Attribute="Dimmer"><ChannelFunction Name="Dimmer" Attribute="Dimmer" DMXFrom="0/1" DMXTo="255/1" /></LogicalChannel></DMXChannel>\n      </DMXChannels>\n    </DMXMode>\n    <DMXMode Name="Extended">\n      <DMXChannels>\n        <DMXChannel Offset="1"><LogicalChannel Attribute="Shutter1"><ChannelFunction Name="Shutter closed" Attribute="Shutter1" DMXFrom="0/1" DMXTo="19/1"/><ChannelFunction Name="Shutter open" Attribute="Shutter1" DMXFrom="20/1" DMXTo="49/1"/><ChannelFunction Name="Strobe" Attribute="Shutter1" DMXFrom="50/1" DMXTo="200/1"/></LogicalChannel></DMXChannel>\n        <DMXChannel Offset="2 3"><LogicalChannel Attribute="Dimmer"><ChannelFunction Name="Dimmer" Attribute="Dimmer" DMXFrom="0/2" DMXTo="65535/2"/></LogicalChannel></DMXChannel>\n        <DMXChannel Offset="14 15"><LogicalChannel Attribute="Zoom"><ChannelFunction Name="Zoom" Attribute="Zoom" DMXFrom="0/2" DMXTo="65535/2" PhysicalFrom="2" PhysicalTo="45"/></LogicalChannel></DMXChannel>\n        <DMXChannel Offset="16 17"><LogicalChannel Attribute="Focus"><ChannelFunction Name="Focus" Attribute="Focus" DMXFrom="0/2" DMXTo="65535/2"/></LogicalChannel></DMXChannel>\n        <DMXChannel Offset="18 19"><LogicalChannel Attribute="Pan"><ChannelFunction Name="Pan" Attribute="Pan" DMXFrom="0/2" DMXTo="65535/2"/></LogicalChannel></DMXChannel>\n        <DMXChannel Offset="20 21"><LogicalChannel Attribute="Tilt"><ChannelFunction Name="Tilt" Attribute="Tilt" DMXFrom="0/2" DMXTo="65535/2"/></LogicalChannel></DMXChannel>\n        <DMXChannel Offset="13"><LogicalChannel Attribute="Iris"><ChannelFunction Name="Iris" Attribute="Iris" DMXFrom="0/1" DMXTo="191/1" PhysicalFrom="1" PhysicalTo="0"/><ChannelFunction Name="Iris pulse effect" Attribute="Iris" DMXFrom="192/1" DMXTo="255/1"/></LogicalChannel></DMXChannel>\n      </DMXChannels>\n    </DMXMode>\n  </DMXModes>\n</FixtureType>\n'
        tmp = tempfile.NamedTemporaryFile(suffix='.gdtf', delete=False)
        tmp.close()
        with zipfile.ZipFile(tmp.name, 'w') as zf:
            zf.writestr('description.xml', xml)
        return Path(tmp.name)

    def test_gdtf_mode_selection_and_practical_values(self):
        path = self.make_gdtf()
        try:
            modes = fart.list_gdtf_modes(path)
            self.assertEqual(modes, ['Basic', 'Extended'])
            mapping, _modes, selected = fart.import_gdtf_channel_mapping(path, start_address=101, preferred_mode='Extended')
            self.assertEqual(selected, 'Extended')
            self.assertEqual(mapping['shutter'], 101)
            self.assertEqual(mapping['shutter_open'], 34)
            self.assertEqual(mapping['dimmer'], 102)
            self.assertEqual(mapping['dimmer_fine'], 103)
            self.assertEqual(mapping['iris'], 113)
            self.assertEqual(mapping['iris_100_dmx'], 191)
            self.assertEqual(mapping['iris_physical_at_0'], 1.0)
            self.assertEqual(mapping['iris_physical_at_100'], 0.0)
            self.assertEqual(mapping['zoom'], 114)
            self.assertEqual(mapping['zoom_fine'], 115)
            self.assertEqual(mapping['zoom_angle_at_0'], 2.0)
            self.assertEqual(mapping['zoom_angle_at_100'], 45.0)
            self.assertEqual(mapping['focus'], 116)
            self.assertEqual(mapping['focus_fine'], 117)
            self.assertEqual(mapping['pan_coarse'], 118)
            self.assertEqual(mapping['pan_fine'], 119)
            self.assertEqual(mapping['tilt_coarse'], 120)
            self.assertEqual(mapping['tilt_fine'], 121)
        finally:
            path.unlink(missing_ok=True)


class MultiUniverseTests(unittest.TestCase):
    def test_channel_conflicts_are_per_universe(self):
        a = fart.FixtureConfig(name='A', output_universe=1, pan_coarse=1, pan_fine=2, tilt_coarse=3, tilt_fine=4, dimmer=5)
        b = fart.FixtureConfig(name='B', output_universe=2, pan_coarse=1, pan_fine=2, tilt_coarse=3, tilt_fine=4, dimmer=5)
        # Duplicate slots on different universes are valid.
        app_validate = getattr(fart.App, 'validate_channel_conflicts')
        class Dummy: pass
        app_validate(Dummy(), [a, b])

    def test_open_dmx_adapter_parser(self):
        mapping = fart.parse_open_dmx_adapter_map('COM3=0, COM4=1', 'COM9', 0)
        self.assertEqual(mapping, {0: 'COM3', 1: 'COM4'})
        fallback = fart.parse_open_dmx_adapter_map('', 'COM9', 7)
        self.assertEqual(fallback, {7: 'COM9'})


def artnet_packet(universe, dmx_bytes):
    header = (
        b'Art-Net\x00' + struct.pack('<H', 0x5000) + struct.pack('>H', 14)
        + bytes((0, 0)) + struct.pack('<H', universe) + struct.pack('>H', len(dmx_bytes))
    )
    return header + dmx_bytes


class ConsoleRelayTests(unittest.TestCase):
    def test_parse_artnet_dmx_roundtrip(self):
        packet = artnet_packet(3, bytes([10, 20, 30]))
        result = fart.parse_artnet_dmx(packet)
        self.assertEqual(result, (3, bytes([10, 20, 30])))

    def test_parse_artnet_dmx_rejects_malformed_packets(self):
        self.assertIsNone(fart.parse_artnet_dmx(b'not art-net'))
        self.assertIsNone(fart.parse_artnet_dmx(b'Art-Net\x00' + b'\x00' * 9))
        truncated = artnet_packet(1, bytes([1, 2, 3]))[:-1]
        self.assertIsNone(fart.parse_artnet_dmx(truncated))

    def test_console_input_bank_pads_and_reports_staleness(self):
        bank = fart.ConsoleInputBank()
        missing, ts = bank.get(5)
        self.assertIsNone(missing)
        self.assertEqual(ts, 0.0)
        bank.update(5, bytes([1, 2, 3]))
        frame, ts = bank.get(5)
        self.assertEqual(len(frame), 512)
        self.assertEqual(frame[:3], bytes([1, 2, 3]))
        self.assertEqual(frame[3], 0)
        self.assertGreater(ts, 0.0)

    def test_resolve_console_mode(self):
        fixture = fart.FixtureConfig(console_mode_channel=5)
        auto_frame = bytearray(512)
        auto_frame[4] = 200
        manual_frame = bytearray(512)
        manual_frame[4] = 50
        self.assertEqual(fart.resolve_console_mode(fixture, auto_frame), 'auto')
        self.assertEqual(fart.resolve_console_mode(fixture, manual_frame), 'manual')
        # No frame data, or the feature disabled: always falls back to auto.
        self.assertEqual(fart.resolve_console_mode(fixture, None), 'auto')
        self.assertEqual(fart.resolve_console_mode(fart.FixtureConfig(console_mode_channel=0), manual_frame), 'auto')
        short_frame = bytearray(2)
        self.assertEqual(fart.resolve_console_mode(fixture, short_frame), 'auto')

    def test_resolve_live_marker_id(self):
        fixture = fart.FixtureConfig(marker_id=7, console_marker_channel=10)
        frame = bytearray(512)
        frame[9] = 42
        self.assertEqual(fart.resolve_live_marker_id(fixture, frame), 42)
        frame[9] = 0
        self.assertEqual(fart.resolve_live_marker_id(fixture, frame), 7)
        self.assertEqual(fart.resolve_live_marker_id(fixture, None), 7)
        self.assertEqual(fart.resolve_live_marker_id(fart.FixtureConfig(marker_id=7, console_marker_channel=0), frame), 7)

    def test_universe_uses_console_relay(self):
        relay = fart.FixtureConfig(name='A', output_universe=1, enabled=True, console_mode_channel=3)
        plain = fart.FixtureConfig(name='B', output_universe=1, enabled=True, console_mode_channel=0)
        disabled_relay = fart.FixtureConfig(name='C', output_universe=2, enabled=False, console_mode_channel=3)
        settings = fart.Settings(fixtures=[relay, plain, disabled_relay])
        self.assertTrue(fart.universe_uses_console_relay(settings, 1))
        self.assertFalse(fart.universe_uses_console_relay(settings, 2))

    def test_intensity_passthrough_preserves_console_dimmer_unless_blacked_out(self):
        fixture = fart.FixtureConfig(dimmer=5, shutter=6, shutter_open=30)
        frame = bytearray(512)
        frame[4] = 200  # console's own dimmer value already in the frame
        frame[5] = 30
        fart.write_fixture_to_frame(frame, fixture, 0.0, 0.0, 1.0, False, intensity_passthrough=True)
        self.assertEqual(frame[4], 200, "dimmer should pass through untouched when not blacked out")
        self.assertEqual(frame[5], 30, "shutter should pass through untouched when not blacked out")

        fart.write_fixture_to_frame(frame, fixture, 0.0, 0.0, 1.0, True, intensity_passthrough=True)
        self.assertEqual(frame[4], 0, "blackout must still force dimmer to 0 even with passthrough enabled")
        self.assertEqual(frame[5], 0, "blackout must still force shutter closed even with passthrough enabled")

    def test_beam_passthrough_preserves_zoom_iris_focus_unless_at_limit(self):
        fixture = fart.FixtureConfig(
            dimmer=0, shutter=0, zoom=10, zoom_fine=11, iris=12, focus=13,
            blackout_on_limit=True, limit_blackout_zoom_100=True,
            pan_min=-10.0, pan_max=10.0, tilt_min=-10.0, tilt_max=10.0,
        )
        frame = bytearray(512)
        frame[9] = 111   # console-driven zoom coarse
        frame[10] = 222  # console-driven zoom fine
        frame[11] = 133  # console-driven iris
        frame[12] = 144  # console-driven focus
        fart.write_fixture_to_frame(frame, fixture, 0.0, 0.0, 1.0, False, zoom=0.9, iris=0.9, focus=0.9, beam_passthrough=True)
        self.assertEqual((frame[9], frame[10], frame[11], frame[12]), (111, 222, 133, 144),
                         "zoom/iris/focus should pass through untouched when beam_passthrough is set")

        # Pan pushed past its limit with blackout_on_limit + zoom-to-100% on
        # limit: zoom must still be forced open even though beam_passthrough
        # is set, since that is a safety override, not a normal auto-beam value.
        fart.write_fixture_to_frame(frame, fixture, 50.0, 0.0, 1.0, False, zoom=0.9, iris=0.9, focus=0.9, beam_passthrough=True)
        self.assertEqual((frame[9], frame[10]), (255, 255), "zoom must be forced to 100% on limit blackout regardless of passthrough")
        self.assertEqual(frame[11], 133, "iris has no limit-blackout override configured, so it stays passed through")
        self.assertEqual(frame[12], 144, "focus has no limit-blackout concept at all, so it stays passed through")


if __name__ == '__main__':
    unittest.main()
