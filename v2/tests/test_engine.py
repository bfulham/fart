import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fart.bus import ExternalInputBus, TrackerBank
from fart.config import FixtureConfig, Settings
from fart.engine import (
    CycleState, calculate_aim, dmx_in_is_needed, resolve_console_mode, resolve_live_marker_id,
    run_cycle, write_fixture_to_frame,
)


class GeometryTests(unittest.TestCase):
    def test_marker_directly_ahead_and_lower(self):
        fixture = FixtureConfig(x=0.0, y=-5.0, z=4.0)
        bearing, elevation, pan, tilt, distance = calculate_aim(fixture, 0.0, 0.0, 1.5)
        self.assertAlmostEqual(bearing, 0.0, places=6)
        self.assertAlmostEqual(elevation, -26.565051, places=5)
        self.assertAlmostEqual(pan, 0.0, places=6)
        self.assertAlmostEqual(tilt, -elevation, places=6)
        self.assertAlmostEqual(distance, math.sqrt(31.25), places=6)

    def test_marker_house_right(self):
        fixture = FixtureConfig(x=0.0, y=-5.0, z=4.0)
        bearing, elevation, pan, tilt, _ = calculate_aim(fixture, 5.0, 0.0, 4.0)
        self.assertAlmostEqual(bearing, 45.0, places=6)
        self.assertAlmostEqual(pan, 45.0, places=6)

    def test_marker_at_optical_centre_raises(self):
        fixture = FixtureConfig(x=1.0, y=1.0, z=1.0)
        with self.assertRaises(ValueError):
            calculate_aim(fixture, 1.0, 1.0, 1.0)


class WriteFixtureToFrameTests(unittest.TestCase):
    def test_midpoint_maps_to_16_bit_midpoint(self):
        fixture = FixtureConfig(pan_coarse=1, pan_fine=2, tilt_coarse=3, tilt_fine=4, dimmer=5, shutter=0)
        frame = bytearray(512)
        write_fixture_to_frame(frame, fixture, 0.0, 0.0, 0.5, False)
        self.assertEqual(frame[0:5], bytes([128, 0, 128, 0, 128]))

    def test_blackout_closes_intensity(self):
        fixture = FixtureConfig(dimmer=5, shutter=6, shutter_open=30)
        frame = bytearray(512)
        write_fixture_to_frame(frame, fixture, 0.0, 0.0, 1.0, True)
        self.assertEqual(frame[4], 0)
        self.assertEqual(frame[5], 0)

    def test_intensity_passthrough_preserves_console_dimmer_unless_blacked_out(self):
        fixture = FixtureConfig(dimmer=5, shutter=6, shutter_open=30)
        frame = bytearray(512)
        frame[4] = 200
        frame[5] = 30
        write_fixture_to_frame(frame, fixture, 0.0, 0.0, 1.0, False, intensity_passthrough=True)
        self.assertEqual(frame[4], 200)
        self.assertEqual(frame[5], 30)
        write_fixture_to_frame(frame, fixture, 0.0, 0.0, 1.0, True, intensity_passthrough=True)
        self.assertEqual(frame[4], 0)
        self.assertEqual(frame[5], 0)


class ConsoleRelayResolutionTests(unittest.TestCase):
    def test_resolve_console_mode(self):
        fixture = FixtureConfig(console_mode_channel=5)
        auto_frame = bytearray(512)
        auto_frame[4] = 200
        manual_frame = bytearray(512)
        manual_frame[4] = 50
        self.assertEqual(resolve_console_mode(fixture, auto_frame), "auto")
        self.assertEqual(resolve_console_mode(fixture, manual_frame), "manual")
        self.assertEqual(resolve_console_mode(fixture, None), "auto")
        self.assertEqual(resolve_console_mode(FixtureConfig(console_mode_channel=0), manual_frame), "auto")

    def test_resolve_live_marker_id(self):
        fixture = FixtureConfig(marker_id=7, console_marker_channel=10)
        frame = bytearray(512)
        frame[9] = 42
        self.assertEqual(resolve_live_marker_id(fixture, frame), 42)
        frame[9] = 0
        self.assertEqual(resolve_live_marker_id(fixture, frame), 7)


class RunCycleTests(unittest.TestCase):
    def _settings(self, **fixture_overrides):
        settings = Settings()
        settings.psn_in.timeout_s = 0.5
        settings.psn_in.smoothing = 0.0
        fixture = FixtureConfig(x=0.0, y=-8.0, z=5.0, marker_id=1, output_universe=0, **fixture_overrides)
        settings.fixtures = [fixture]
        return settings

    def test_unarmed_blacks_out_dimmer_but_still_computes_pan_tilt(self):
        settings = self._settings(dimmer=5, pan_coarse=1, tilt_coarse=3)
        trackers = TrackerBank()
        trackers.update(1, 5.0, 0.0, 5.0)
        bus = ExternalInputBus()
        state = CycleState()
        frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, False, 100.0, state)
        self.assertEqual(frames[0][4], 0, "unarmed must force dimmer to 0")
        self.assertGreater(frames[0][0], 0, "pan should still be computed and written even when unarmed")

    def test_stale_tracking_forces_blackout(self):
        settings = self._settings(dimmer=5)
        trackers = TrackerBank()
        trackers.xyz[1] = (5.0, 0.0, 5.0, 0.0)  # timestamp 0 => always stale
        bus = ExternalInputBus()
        state = CycleState()
        _frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, True, 100.0, state)
        self.assertTrue(statuses[0]["stale"])
        self.assertTrue(statuses[0]["blackout"])

    def test_console_relay_manual_mode_is_full_passthrough(self):
        settings = self._settings(console_mode_channel=10, console_marker_channel=11, dimmer=5, pan_coarse=1)
        trackers = TrackerBank()
        trackers.update(1, 5.0, 0.0, 5.0)
        bus = ExternalInputBus()
        console_frame = bytearray(512)
        console_frame[9] = 0    # mode channel < 128 => manual
        console_frame[4] = 200  # console's own dimmer value, must pass through untouched
        bus.update(0, console_frame)
        state = CycleState()
        frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, True, 100.0, state)
        self.assertEqual(statuses[0]["error"], "MANUAL (console)")
        self.assertEqual(frames[0][4], 200, "manual mode must not touch the console's own dimmer byte")

    def test_console_relay_auto_mode_computes_pan_tilt_and_passes_dimmer_through(self):
        settings = self._settings(console_mode_channel=10, console_marker_channel=11, dimmer=5, pan_coarse=1, tilt_coarse=3)
        trackers = TrackerBank()
        trackers.update(1, 5.0, 0.0, 5.0)
        bus = ExternalInputBus()
        console_frame = bytearray(512)
        console_frame[9] = 255  # mode channel >= 128 => auto
        console_frame[4] = 200  # console-driven dimmer should pass through since not blacked out
        bus.update(0, console_frame)
        state = CycleState()
        frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, True, 100.0, state)
        self.assertNotIn("error", statuses[0])
        self.assertGreater(frames[0][0], 0, "pan should be computed by FART in auto mode")
        self.assertEqual(frames[0][4], 200, "dimmer should still pass through from the console in auto mode")


class DMXInNeededTests(unittest.TestCase):
    def test_not_needed_when_nothing_uses_it(self):
        settings = Settings()
        self.assertFalse(dmx_in_is_needed(settings))

    def test_needed_when_fader_sourced_from_dmx_in(self):
        settings = Settings()
        settings.fader.source = "dmx_in"
        self.assertTrue(dmx_in_is_needed(settings))

    def test_needed_when_a_fixture_has_console_relay(self):
        settings = Settings()
        settings.fixtures = [FixtureConfig(console_mode_channel=5)]
        self.assertTrue(dmx_in_is_needed(settings))

    def test_not_needed_when_console_relay_fixture_is_disabled(self):
        settings = Settings()
        settings.fixtures = [FixtureConfig(console_mode_channel=5, enabled=False)]
        self.assertFalse(dmx_in_is_needed(settings))


if __name__ == "__main__":
    unittest.main()
