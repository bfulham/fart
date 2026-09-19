import math
import sys
import tempfile
import time
import unittest
import unittest.mock
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fart.bus import ExternalInputBus, TrackerBank
from fart.config import FixtureConfig, FixtureType, Settings
from fart.engine import (
    CycleState, calculate_aim, dmx_in_is_needed, effective_fixture_type, resolve_console_mode,
    resolve_fixture, resolve_live_marker_id, run_cycle, write_fixture_to_frame,
)

# Two modes with genuinely different channel layouts, so switching modes
# is observably different: "DimmerOnly" is a 1-channel footprint with just
# a dimmer; "PanTiltDimmer" adds pan/tilt ahead of a dimmer at offset 5.
TWO_MODE_GDTF_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<FixtureType>\n  <DMXModes>\n'
    '    <DMXMode Name="DimmerOnly"><DMXChannels>\n'
    '        <DMXChannel Offset="1"><LogicalChannel Attribute="Dimmer">'
    '<ChannelFunction Name="Dimmer" Attribute="Dimmer" DMXFrom="0/1" DMXTo="255/1" />'
    '</LogicalChannel></DMXChannel>\n    </DMXChannels></DMXMode>\n'
    '    <DMXMode Name="PanTiltDimmer"><DMXChannels>\n'
    '        <DMXChannel Offset="1 2"><LogicalChannel Attribute="Pan">'
    '<ChannelFunction Name="Pan" Attribute="Pan" DMXFrom="0/2" DMXTo="65535/2"/>'
    '</LogicalChannel></DMXChannel>\n'
    '        <DMXChannel Offset="3 4"><LogicalChannel Attribute="Tilt">'
    '<ChannelFunction Name="Tilt" Attribute="Tilt" DMXFrom="0/2" DMXTo="65535/2"/>'
    '</LogicalChannel></DMXChannel>\n'
    '        <DMXChannel Offset="5"><LogicalChannel Attribute="Dimmer">'
    '<ChannelFunction Name="Dimmer" Attribute="Dimmer" DMXFrom="0/1" DMXTo="255/1" />'
    '</LogicalChannel></DMXChannel>\n    </DMXChannels></DMXMode>\n'
    '  </DMXModes>\n</FixtureType>\n'
)


def make_two_mode_gdtf_bytes():
    tmp = tempfile.NamedTemporaryFile(suffix=".gdtf", delete=False)
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w") as zf:
        zf.writestr("description.xml", TWO_MODE_GDTF_XML)
    data = Path(tmp.name).read_bytes()
    Path(tmp.name).unlink()
    return data


def resolved(instance_overrides=None, type_overrides=None):
    """Builds a ResolvedFixture the way run_cycle does: an instance merged
    with a type. Most tests only care about one side, so both dicts of
    overrides default to empty (plain defaults on the other)."""
    instance = FixtureConfig(**(instance_overrides or {}))
    fixture_type = FixtureType(**(type_overrides or {}))
    return resolve_fixture(instance, fixture_type)


class GeometryTests(unittest.TestCase):
    def test_marker_directly_ahead_and_lower(self):
        fixture = resolved({"x": 0.0, "y": -5.0, "z": 4.0})
        bearing, elevation, pan, tilt, distance = calculate_aim(fixture, 0.0, 0.0, 1.5)
        self.assertAlmostEqual(bearing, 0.0, places=6)
        self.assertAlmostEqual(elevation, -26.565051, places=5)
        self.assertAlmostEqual(pan, 0.0, places=6)
        self.assertAlmostEqual(tilt, -elevation, places=6)
        self.assertAlmostEqual(distance, math.sqrt(31.25), places=6)

    def test_marker_house_right(self):
        fixture = resolved({"x": 0.0, "y": -5.0, "z": 4.0})
        bearing, elevation, pan, tilt, _ = calculate_aim(fixture, 5.0, 0.0, 4.0)
        self.assertAlmostEqual(bearing, 45.0, places=6)
        self.assertAlmostEqual(pan, 45.0, places=6)

    def test_marker_at_optical_centre_raises(self):
        fixture = resolved({"x": 1.0, "y": 1.0, "z": 1.0})
        with self.assertRaises(ValueError):
            calculate_aim(fixture, 1.0, 1.0, 1.0)


class WriteFixtureToFrameTests(unittest.TestCase):
    def test_midpoint_maps_to_16_bit_midpoint(self):
        fixture = resolved(type_overrides={"pan_coarse": 1, "pan_fine": 2, "tilt_coarse": 3, "tilt_fine": 4,
                                            "dimmer": 5, "shutter": 0})
        frame = bytearray(512)
        write_fixture_to_frame(frame, fixture, 0.0, 0.0, 0.5, False)
        self.assertEqual(frame[0:5], bytes([128, 0, 128, 0, 128]))

    def test_blackout_closes_intensity(self):
        fixture = resolved(type_overrides={"dimmer": 5, "shutter": 6, "shutter_open": 30})
        frame = bytearray(512)
        write_fixture_to_frame(frame, fixture, 0.0, 0.0, 1.0, True)
        self.assertEqual(frame[4], 0)
        self.assertEqual(frame[5], 0)

    def test_beam_passthrough_preserves_shadow_zoom_iris_focus_unless_limit_blackout(self):
        fixture = resolved(type_overrides={"dimmer": 5, "zoom": 6, "iris": 7, "focus": 8})
        frame = bytearray(512)
        frame[5] = 111  # zoom, as if already seeded from a shadow copy
        frame[6] = 222  # iris
        frame[7] = 77   # focus
        write_fixture_to_frame(frame, fixture, 0.0, 0.0, 1.0, False, beam_passthrough=True)
        self.assertEqual(frame[5], 111, "beam passthrough must not touch the shadow-seeded zoom byte")
        self.assertEqual(frame[6], 222, "beam passthrough must not touch the shadow-seeded iris byte")
        self.assertEqual(frame[7], 77, "beam passthrough must not touch the shadow-seeded focus byte")
        # Dimmer is never passed through -- FART always owns it once this
        # function is called at all (full passthrough skips calling it).
        self.assertGreater(frame[4], 0)


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
    def _settings(self, type_overrides=None, **fixture_overrides):
        settings = Settings()
        settings.psn_in.timeout_s = 0.5
        settings.psn_in.smoothing = 0.0
        fixture_type = FixtureType(id="t", **(type_overrides or {}))
        fixture = FixtureConfig(x=0.0, y=-8.0, z=5.0, marker_id=1, output_universe=0,
                                 fixture_type_id="t", **fixture_overrides)
        settings.fixture_types = [fixture_type]
        settings.fixtures = [fixture]
        return settings

    def test_unarmed_blacks_out_dimmer_but_still_computes_pan_tilt(self):
        settings = self._settings(type_overrides={"dimmer": 5, "pan_coarse": 1, "tilt_coarse": 3})
        trackers = TrackerBank()
        trackers.update(1, 5.0, 0.0, 5.0)
        bus = ExternalInputBus()
        state = CycleState()
        frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, False, time.monotonic(), state)
        self.assertEqual(frames[0][4], 0, "unarmed must force dimmer to 0")
        self.assertGreater(frames[0][0], 0, "pan should still be computed and written even when unarmed")

    def test_stale_tracking_forces_blackout(self):
        settings = self._settings(type_overrides={"dimmer": 5})
        trackers = TrackerBank()
        trackers.xyz[1] = (5.0, 0.0, 5.0, 0.0)  # timestamp 0 => always stale
        bus = ExternalInputBus()
        state = CycleState()
        _frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, True, time.monotonic(), state)
        self.assertTrue(statuses[0]["stale"])
        self.assertTrue(statuses[0]["blackout"])

    def test_console_relay_manual_mode_is_full_passthrough(self):
        settings = self._settings(
            type_overrides={"dimmer": 5, "pan_coarse": 1},
            console_universe=0, console_mode_channel=10, console_marker_channel=11,
            shadow_universe=0,
        )
        trackers = TrackerBank()
        trackers.update(1, 5.0, 0.0, 5.0)
        bus = ExternalInputBus()
        console_frame = bytearray(512)
        console_frame[9] = 0    # mode channel < 128 => manual
        bus.update(0, console_frame)
        state = CycleState()
        frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, True, time.monotonic(), state)
        self.assertEqual(statuses[0]["error"], "MANUAL (console)")

    def test_console_relay_manual_mode_with_shadow_patch_passes_dimmer_through(self):
        settings = self._settings(
            type_overrides={"dimmer": 5, "pan_coarse": 1, "footprint": 10},
            console_universe=1, console_mode_channel=10, console_marker_channel=11,
            shadow_universe=2, shadow_start_address=1,
        )
        trackers = TrackerBank()
        trackers.update(1, 5.0, 0.0, 5.0)
        bus = ExternalInputBus()
        mode_frame = bytearray(512)
        mode_frame[9] = 0  # mode channel < 128 => manual
        bus.update(1, mode_frame)
        shadow_frame = bytearray(512)
        shadow_frame[4] = 200  # console's own dimmer value on the shadow feed
        bus.update(2, shadow_frame)
        state = CycleState()
        frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, True, time.monotonic(), state)
        self.assertEqual(statuses[0]["error"], "MANUAL (console)")
        self.assertEqual(frames[0][4], 200, "manual mode with a shadow patch must relay the console's dimmer byte")

    def test_console_relay_auto_mode_computes_pan_tilt_and_dimmer(self):
        settings = self._settings(
            type_overrides={"dimmer": 5, "pan_coarse": 1, "tilt_coarse": 3},
            console_universe=0, console_mode_channel=10, console_marker_channel=11,
        )
        trackers = TrackerBank()
        trackers.update(1, 5.0, 0.0, 5.0)
        bus = ExternalInputBus()
        console_frame = bytearray(512)
        console_frame[9] = 255  # mode channel >= 128 => auto
        bus.update(0, console_frame)
        state = CycleState()
        frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, True, time.monotonic(), state)
        self.assertNotIn("error", statuses[0])
        self.assertGreater(frames[0][0], 0, "pan should be computed by FART in auto mode")
        self.assertGreater(frames[0][4], 0, "dimmer is always computed by FART in auto mode, armed and fader > 0")

    def test_beam_passes_through_from_shadow_when_auto_beam_size_is_off(self):
        settings = self._settings(
            type_overrides={"dimmer": 5, "pan_coarse": 1, "tilt_coarse": 3, "zoom": 6, "iris": 7, "footprint": 10},
            shadow_universe=2, shadow_start_address=1,
        )
        trackers = TrackerBank()
        trackers.update(1, 5.0, 0.0, 5.0)
        bus = ExternalInputBus()
        shadow_frame = bytearray(512)
        shadow_frame[5] = 111  # zoom, driven live by the console
        shadow_frame[6] = 222  # iris
        bus.update(2, shadow_frame)
        state = CycleState()
        frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, True, time.monotonic(), state)
        self.assertEqual(frames[0][5], 111, "zoom should pass through from the shadow feed with auto-beam-size off")
        self.assertEqual(frames[0][6], 222, "iris should pass through from the shadow feed with auto-beam-size off")

    def test_shadow_patch_copy_uses_the_selected_modes_footprint(self):
        # DimmerOnly's real footprint is 1 channel. Using the type's own
        # (meaningless, for a GDTF-backed type) stored field defaults
        # instead of the resolved mode's footprint would compute 5 here by
        # coincidence (FixtureType()'s own pan/tilt/dimmer field defaults
        # happen to span channels 1-5) and wrongly copy a byte one past
        # what this mode actually has.
        settings = self._settings(
            type_overrides={"gdtf_data": make_two_mode_gdtf_bytes()},
            gdtf_mode="DimmerOnly", shadow_universe=2, shadow_start_address=1,
        )
        trackers = TrackerBank()
        trackers.update(1, 5.0, 0.0, 5.0)
        bus = ExternalInputBus()
        shadow_frame = bytearray(512)
        shadow_frame[1] = 250  # channel 2 -- beyond DimmerOnly's 1-channel footprint
        bus.update(2, shadow_frame)
        state = CycleState()
        frames, _statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, True, time.monotonic(), state)
        self.assertEqual(frames[0][1], 0, "a byte beyond this mode's real footprint must not be copied")


class GDTFBackedResolutionTests(unittest.TestCase):
    def test_custom_type_is_returned_unchanged(self):
        fixture_type = FixtureType(dimmer=7)
        fixture = FixtureConfig(gdtf_mode="whatever")
        self.assertIs(effective_fixture_type(fixture, fixture_type), fixture_type)

    def test_selected_mode_drives_the_resolved_channels_not_the_types_own_fields(self):
        # The type's own stored fields (dimmer=99 here) must be completely
        # ignored for a GDTF-backed type -- only the instance's selected
        # mode matters.
        fixture_type = FixtureType(id="t", gdtf_data=make_two_mode_gdtf_bytes(), dimmer=99)
        fixture = FixtureConfig(fixture_type_id="t", gdtf_mode="DimmerOnly", output_start_address=100)
        resolved = resolve_fixture(fixture, fixture_type)
        self.assertEqual(resolved.dimmer, 100)
        self.assertEqual(resolved.pan_coarse, 0, "DimmerOnly has no pan channel")
        self.assertEqual(resolved.footprint, 1)

    def test_two_fixtures_sharing_one_type_can_run_different_modes(self):
        fixture_type = FixtureType(id="t", gdtf_data=make_two_mode_gdtf_bytes())
        dimmer_only = FixtureConfig(fixture_type_id="t", gdtf_mode="DimmerOnly", output_start_address=1)
        pan_tilt = FixtureConfig(fixture_type_id="t", gdtf_mode="PanTiltDimmer", output_start_address=1)

        a = resolve_fixture(dimmer_only, fixture_type)
        b = resolve_fixture(pan_tilt, fixture_type)
        self.assertEqual(a.footprint, 1)
        self.assertEqual(a.pan_coarse, 0)
        self.assertEqual(b.footprint, 5)
        self.assertEqual(b.pan_coarse, 1)
        self.assertEqual(b.dimmer, 5)

    def test_unset_or_unknown_mode_resolves_to_an_all_disabled_baseline_not_a_crash(self):
        fixture_type = FixtureType(id="t", gdtf_data=make_two_mode_gdtf_bytes())
        fixture = FixtureConfig(fixture_type_id="t", gdtf_mode="")
        resolved = resolve_fixture(fixture, fixture_type)
        self.assertEqual(resolved.footprint, 0)
        self.assertEqual(resolved.dimmer, 0)
        self.assertEqual(resolved.pan_coarse, 0)

        fixture.gdtf_mode = "NoSuchMode"
        resolved = resolve_fixture(fixture, fixture_type)
        self.assertEqual(resolved.dimmer, 0)

    def test_gdtf_parsing_is_cached_on_the_type_not_redone_every_resolve(self):
        fixture_type = FixtureType(id="t", gdtf_data=make_two_mode_gdtf_bytes())
        fixture = FixtureConfig(fixture_type_id="t", gdtf_mode="DimmerOnly")
        resolve_fixture(fixture, fixture_type)
        with unittest.mock.patch("fart.gdtf.derive_all_modes") as derive:
            resolve_fixture(fixture, fixture_type)
            resolve_fixture(fixture, fixture_type)
        derive.assert_not_called()

    def test_replacing_gdtf_data_invalidates_the_cache(self):
        fixture_type = FixtureType(id="t", gdtf_data=make_two_mode_gdtf_bytes())
        fixture = FixtureConfig(fixture_type_id="t", gdtf_mode="DimmerOnly")
        resolve_fixture(fixture, fixture_type)
        fixture_type.gdtf_data = make_two_mode_gdtf_bytes()  # a new bytes object, same content
        resolved = resolve_fixture(fixture, fixture_type)
        self.assertEqual(resolved.dimmer, 1, "must still resolve correctly against the replaced file")


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

    def test_needed_when_a_fixture_has_a_shadow_patch_with_no_relay(self):
        settings = Settings()
        settings.fixtures = [FixtureConfig(shadow_universe=3, console_mode_channel=0)]
        self.assertTrue(dmx_in_is_needed(settings))

    def test_not_needed_when_console_relay_fixture_is_disabled(self):
        settings = Settings()
        settings.fixtures = [FixtureConfig(console_mode_channel=5, enabled=False)]
        self.assertFalse(dmx_in_is_needed(settings))


if __name__ == "__main__":
    unittest.main()
