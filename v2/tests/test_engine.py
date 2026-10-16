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
        # Beam passthrough alone never stops FART owning the dimmer.
        self.assertGreater(frame[4], 0)

    def test_dimmer_passthrough_leaves_the_shadow_dimmer_alone(self):
        fixture = resolved(type_overrides={"dimmer": 5, "shutter": 8, "shutter_open": 255})
        frame = bytearray(512)
        frame[4] = 123  # dimmer, as seeded from a shadow copy
        frame[7] = 77   # shutter
        write_fixture_to_frame(frame, fixture, 0.0, 0.0, 1.0, False, dimmer_passthrough=True)
        self.assertEqual(frame[4], 123)
        self.assertEqual(frame[7], 77)

    def test_dimmer_passthrough_is_still_forced_to_zero_by_blackout(self):
        fixture = resolved(type_overrides={"dimmer": 5, "shutter": 8, "shutter_open": 255})
        frame = bytearray(512)
        frame[4] = 123
        frame[7] = 77
        write_fixture_to_frame(frame, fixture, 0.0, 0.0, 1.0, True, dimmer_passthrough=True)
        self.assertEqual(frame[4], 0, "blackout must beat a passed-through console dimmer")
        self.assertEqual(frame[7], 0, "blackout must close the shutter even on a passed-through dimmer")


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

    def test_manual_dimmer_follows_the_fader_and_pan_tilt_are_always_computed(self):
        settings = self._settings(type_overrides={"dimmer": 5, "pan_coarse": 1, "tilt_coarse": 3})
        trackers = TrackerBank()
        trackers.update(1, 5.0, 0.0, 5.0)
        bus = ExternalInputBus()
        frames, _ = run_cycle(settings, trackers, bus, 0.0, 0.5, 1.0, 0.5, time.monotonic(), CycleState())
        self.assertEqual(frames[0][4], 0, "fader at 0 means dimmer 0")
        self.assertGreater(frames[0][0], 0, "pan is computed whatever the fader says")
        frames, _ = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, time.monotonic(), CycleState())
        self.assertEqual(frames[0][4], 255, "fader at 100% means full dimmer, with no separate arm step")

    def test_stale_tracking_forces_blackout(self):
        settings = self._settings(type_overrides={"dimmer": 5})
        trackers = TrackerBank()
        trackers.xyz[1] = (5.0, 0.0, 5.0, 0.0)  # timestamp 0 => always stale
        bus = ExternalInputBus()
        state = CycleState()
        _frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, time.monotonic(), state)
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
        frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, time.monotonic(), state)
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
        frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, time.monotonic(), state)
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
        frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, time.monotonic(), state)
        self.assertNotIn("error", statuses[0])
        self.assertGreater(frames[0][0], 0, "pan should be computed by FART in auto mode")
        self.assertGreater(frames[0][4], 0, "dimmer is FART-computed in auto mode with the default manual dimmer and fader > 0")

    def test_beam_passes_through_from_shadow_in_dmx_beam_mode(self):
        settings = self._settings(
            type_overrides={"dimmer": 5, "pan_coarse": 1, "tilt_coarse": 3, "zoom": 6, "iris": 7, "footprint": 10},
            shadow_universe=2, shadow_start_address=1,
        )
        settings.beam_mode = "dmx"
        trackers = TrackerBank()
        trackers.update(1, 5.0, 0.0, 5.0)
        bus = ExternalInputBus()
        shadow_frame = bytearray(512)
        shadow_frame[5] = 111  # zoom, driven live by the console
        shadow_frame[6] = 222  # iris
        bus.update(2, shadow_frame)
        state = CycleState()
        frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, time.monotonic(), state)
        self.assertEqual(frames[0][5], 111, "zoom should pass through from the shadow feed in DMX beam mode")
        self.assertEqual(frames[0][6], 222, "iris should pass through from the shadow feed in DMX beam mode")

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
        frames, _statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, time.monotonic(), state)
        self.assertEqual(frames[0][1], 0, "a byte beyond this mode's real footprint must not be copied")

    def _shadow_settings(self, **fixture_overrides):
        settings = self._settings(
            type_overrides={"dimmer": 5, "pan_coarse": 1, "tilt_coarse": 3, "zoom": 6, "iris": 7, "focus": 8,
                            "footprint": 10},
            shadow_universe=2, shadow_start_address=1, **fixture_overrides,
        )
        trackers = TrackerBank()
        trackers.update(1, 5.0, 0.0, 5.0)
        bus = ExternalInputBus()
        shadow_frame = bytearray(512)
        shadow_frame[4] = 200  # dimmer
        shadow_frame[5] = 111  # zoom
        shadow_frame[6] = 222  # iris
        shadow_frame[7] = 77   # focus
        bus.update(2, shadow_frame)
        return settings, trackers, bus

    def test_dimmer_dmx_in_mode_passes_the_console_dimmer_and_ignores_the_fader(self):
        settings, trackers, bus = self._shadow_settings()
        settings.dimmer_mode = "dmx_in"
        frames, statuses = run_cycle(settings, trackers, bus, 0.0, 0.5, 1.0, 0.5, time.monotonic(), CycleState())
        self.assertEqual(frames[0][4], 200)
        self.assertEqual(statuses[0]["dimmer_source"], "dmx")
        self.assertGreater(frames[0][0], 0, "FART still drives pan in auto mode")

    def test_dimmer_dmx_in_mode_is_still_blacked_out_by_tracking_loss(self):
        settings, trackers, bus = self._shadow_settings()
        settings.dimmer_mode = "dmx_in"
        trackers.xyz[1] = (5.0, 0.0, 5.0, 0.0)  # always stale
        frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, time.monotonic(), CycleState())
        self.assertEqual(frames[0][4], 0, "a passed-through dimmer must not defeat the tracking-loss blackout")

    def test_dimmer_dmx_in_mode_without_a_shadow_feed_falls_back_to_the_fader(self):
        settings = self._settings(type_overrides={"dimmer": 5})
        settings.dimmer_mode = "dmx_in"
        trackers = TrackerBank()
        trackers.update(1, 5.0, 0.0, 5.0)
        frames, statuses = run_cycle(settings, trackers, ExternalInputBus(), 1.0, 0.5, 1.0, 0.5,
                                     time.monotonic(), CycleState())
        self.assertEqual(frames[0][4], 255)
        self.assertEqual(statuses[0]["dimmer_source"], "manual (no DMX feed)")

    def test_use_dmx_in_off_is_fart_only_control(self):
        settings, trackers, bus = self._shadow_settings(console_universe=1, console_mode_channel=10)
        mode_frame = bytearray(512)
        mode_frame[9] = 0  # would be full passthrough if DMX in were on
        bus.update(1, mode_frame)
        settings.dimmer_mode = "dmx_in"
        settings.beam_mode = "dmx"
        settings.use_dmx_in = False
        frames, statuses = run_cycle(settings, trackers, bus, 0.25, 0.5, 1.0, 0.5, time.monotonic(), CycleState())
        self.assertNotIn("error", statuses[0], "mode channel must be ignored: auto mode, not MANUAL (console)")
        self.assertEqual(frames[0][4], 64, "dimmer comes from the fader")
        self.assertNotEqual(frames[0][5], 111, "zoom comes from the slider, not the shadow feed")
        self.assertEqual(statuses[0]["dimmer_source"], "manual (DMX in off)")
        self.assertEqual(statuses[0]["beam_source"], "manual (DMX in off)")

    def test_beam_modes(self):
        settings, trackers, bus = self._shadow_settings()
        settings.beam_mode = "manual"
        frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.25, time.monotonic(), CycleState())
        self.assertEqual((frames[0][5], frames[0][6], frames[0][7]), (128, 255, 64))
        self.assertEqual(statuses[0]["beam_source"], "manual")
        settings.beam_mode = "dmx"
        frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.25, time.monotonic(), CycleState())
        self.assertEqual((frames[0][5], frames[0][6], frames[0][7]), (111, 222, 77))
        self.assertEqual(statuses[0]["beam_source"], "dmx")
        settings.beam_mode = "auto_size"
        _frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.25, time.monotonic(), CycleState())
        self.assertEqual(statuses[0]["beam_source"], "auto")

    def test_slow_console_feed_is_not_lost_under_the_dmx_in_timeout(self):
        # 2 s between packets (0.5 fps) -- far over PSN's 0.5 s tracking
        # timeout, which is what used to make the dimmer flash.
        settings = self._settings(
            type_overrides={"dimmer": 5, "pan_coarse": 1},
            console_universe=0, console_mode_channel=10,
        )
        trackers = TrackerBank()
        trackers.update(1, 5.0, 0.0, 5.0)
        bus = ExternalInputBus()
        console_frame = bytearray(512)
        console_frame[9] = 255
        bus.update(0, console_frame)
        now = time.monotonic()
        bus.frames[0] = (bus.frames[0][0], now - 2.0)
        _frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, now, CycleState())
        self.assertFalse(statuses[0]["dmx_lost"])
        self.assertFalse(statuses[0]["blackout"])
        bus.frames[0] = (bus.frames[0][0], now - 5.0)
        _frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, now, CycleState())
        self.assertTrue(statuses[0]["dmx_lost"])
        self.assertTrue(statuses[0]["blackout"])

    def test_passthrough_dimmer_blacks_out_when_its_shadow_feed_goes_quiet(self):
        settings, trackers, bus = self._shadow_settings()
        settings.dimmer_mode = "dmx_in"
        now = time.monotonic()
        bus.frames[2] = (bus.frames[2][0], now - 10.0)
        frames, statuses = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, now, CycleState())
        self.assertTrue(statuses[0]["dmx_lost"])
        self.assertEqual(frames[0][4], 0, "a frozen console dimmer must not stay lit")

    def test_shadow_copy_never_includes_the_mode_and_marker_channels(self):
        # Footprint is 10 channels; mode/marker live at 11/12 on the same
        # universe, past the footprint, and must not reach the output.
        settings, trackers, bus = self._shadow_settings(
            console_universe=2, console_mode_channel=11, console_marker_channel=12,
        )
        frame2 = bytearray(bus.get(2)[0])
        frame2[10] = 255  # mode: auto
        frame2[11] = 9    # marker select
        bus.update(2, frame2)
        frames, _ = run_cycle(settings, trackers, bus, 1.0, 0.5, 1.0, 0.5, time.monotonic(), CycleState())
        self.assertEqual(frames[0][10], 0)
        self.assertEqual(frames[0][11], 0)


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

    def test_still_needed_when_use_dmx_in_is_off_so_the_toggle_can_be_live(self):
        settings = Settings()
        settings.use_dmx_in = False
        settings.fixtures = [FixtureConfig(console_mode_channel=5)]
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
