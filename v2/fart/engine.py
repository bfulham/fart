"""Tracking, aiming, and safety logic. No sockets, no UI. Everything here
is a plain function operating on plain data, so it can be unit tested
without a GUI, a network, or a running plugin -- and so the plugins on
either side of it can be swapped freely.

Ported from fart.py v1 (calculate_aim, write_fixture_to_frame, the
calibration solver) with behaviour preserved, plus run_cycle() -- a new,
explicit, testable replacement for what used to live inline in App.loop().
"""
from __future__ import annotations

import math

from .config import FixtureConfig


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def wrap180(v):
    return (v + 180.0) % 360.0 - 180.0


def dmx16(frac):
    n = round(clamp(frac, 0.0, 1.0) * 65535)
    return (n >> 8) & 255, n & 255


def fixture_channels(fixture: FixtureConfig):
    return {
        "pan coarse": fixture.pan_coarse,
        "pan fine": fixture.pan_fine,
        "tilt coarse": fixture.tilt_coarse,
        "tilt fine": fixture.tilt_fine,
        "dimmer": fixture.dimmer,
        "dimmer fine": fixture.dimmer_fine,
        "shutter": fixture.shutter,
        "zoom": fixture.zoom,
        "zoom fine": fixture.zoom_fine,
        "iris": fixture.iris,
        "focus": fixture.focus,
        "focus fine": fixture.focus_fine,
        "console mode": fixture.console_mode_channel,
        "console marker": fixture.console_marker_channel,
    }


def enabled_output_universes(settings):
    universes = sorted({int(f.output_universe) for f in settings.fixtures if f.enabled})
    return universes or [0]


def blank_frames_for_settings(settings):
    return {int(u): bytearray(512) for u in enabled_output_universes(settings)}


def universe_uses_console_relay(settings, universe):
    universe = int(universe)
    return any(
        f.enabled and int(f.output_universe) == universe and int(f.console_mode_channel) > 0
        for f in settings.fixtures
    )


def dmx_in_is_needed(settings):
    """Whether anything actually consumes DMX-in data this run. The control-
    input plugin binds a port (6454 for Art-Net, a multicast group for
    sACN), so it should only run when something needs it -- not
    unconditionally just because a protocol is configured as 'active'.
    """
    if settings.fader.source == "dmx_in":
        return True
    return any(f.enabled and int(f.console_mode_channel) > 0 for f in settings.fixtures)


def dmx_in_universes_needed(settings):
    """Every universe that actually needs to be readable from the active
    DMX-in source: each enabled fixture's own output_universe (console
    relay always reads within a fixture's own universe), plus the DMX-in
    universe the master fader reads from when fader.source == 'dmx_in'.

    Art-Net-in ignores this (it accepts every incoming universe for free,
    with no per-universe join needed), but sACN-in needs it to know which
    multicast groups to join -- otherwise it can only ever receive the one
    universe named on the DMX In tab, silently losing console relay for
    any fixture on a different universe even though a real console is
    genuinely sending it.
    """
    universes = {int(f.output_universe) for f in settings.fixtures
                 if f.enabled and int(f.console_mode_channel) > 0}
    if settings.fader.source == "dmx_in":
        active = settings.dmx_in.active
        universes.add(int(getattr(settings.dmx_in, active).universe))
    return universes


def resolve_console_mode(fixture: FixtureConfig, universe_frame):
    """'auto' or 'manual'. console_mode_channel == 0, or missing/short frame
    data, always falls back to 'auto' -- a stale/absent console signal
    cannot be trusted to mean "manual" either.
    """
    channel = int(fixture.console_mode_channel)
    if channel <= 0 or universe_frame is None or channel > len(universe_frame):
        return "auto"
    return "manual" if universe_frame[channel - 1] < 128 else "auto"


def resolve_live_marker_id(fixture: FixtureConfig, universe_frame):
    """console_marker_channel == 0, a missing/short frame, or a DMX value of
    0 all mean "use the fixture's configured default marker_id". A nonzero
    value is used directly as the marker ID (not an index into a discovered-
    tracker list, which would silently change meaning if a tracker dropped
    out mid-show).
    """
    channel = int(fixture.console_marker_channel)
    if channel <= 0 or universe_frame is None or channel > len(universe_frame):
        return int(fixture.marker_id)
    value = universe_frame[channel - 1]
    return int(value) if value > 0 else int(fixture.marker_id)


def calculate_aim(fixture: FixtureConfig, x, y, z, previous_pan=None):
    """Line of sight from a fixture's optical centre to a marker.

    World axes: +X house right, +Y away/upstage, +Z up. Bearing 0 points +Y
    and increases toward +X. Elevation 0 is horizontal and positive is up.
    """
    dx, dy, dz = x - fixture.x, y - fixture.y, z - fixture.z
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    if distance < 1e-6:
        raise ValueError("Marker is at the fixture optical centre")

    bearing = math.degrees(math.atan2(dx, dy))
    elevation = math.degrees(math.atan2(dz, math.hypot(dx, dy)))

    base_pan = wrap180(bearing - fixture.pan_zero_bearing) * fixture.pan_direction + fixture.pan_offset
    tilt = (elevation - fixture.tilt_zero_elevation) * fixture.tilt_direction + fixture.tilt_offset

    candidates = [base_pan + 360 * k for k in range(-3, 4)]
    valid = [p for p in candidates if fixture.pan_min <= p <= fixture.pan_max]
    reference = previous_pan if previous_pan is not None else (fixture.pan_min + fixture.pan_max) / 2.0
    pool = valid or candidates
    pan = min(pool, key=lambda p: abs(p - reference))

    return bearing, elevation, pan, tilt, distance


def fixture_has_zoom_model(fixture: FixtureConfig):
    return fixture.zoom_angle_at_0 > 0.0 and fixture.zoom_angle_at_100 > 0.0 and abs(fixture.zoom_angle_at_100 - fixture.zoom_angle_at_0) > 0.001


def auto_zoom_for_distance(fixture: FixtureConfig, distance, target_diameter_m, fallback_zoom=0.5):
    if not fixture_has_zoom_model(fixture) or distance <= 0:
        return clamp(fallback_zoom, 0.0, 1.0), None, False
    diameter = max(0.01, float(target_diameter_m))
    required = math.degrees(2.0 * math.atan((diameter / 2.0) / max(0.001, distance)))
    a0, a100 = fixture.zoom_angle_at_0, fixture.zoom_angle_at_100
    value = (required - a0) / (a100 - a0)
    return clamp(value, 0.0, 1.0), required, True


def fixture_has_iris_model(fixture: FixtureConfig):
    return fixture.iris > 0 and abs(fixture.iris_physical_at_100 - fixture.iris_physical_at_0) > 0.001


def _iris_control_for_physical(fixture: FixtureConfig, desired_physical, fallback_iris=1.0):
    if not fixture_has_iris_model(fixture):
        return clamp(fallback_iris, 0.0, 1.0), False
    p0, p100 = fixture.iris_physical_at_0, fixture.iris_physical_at_100
    if fixture.iris_reverse:
        p0, p100 = p100, p0
    value = (float(desired_physical) - p0) / (p100 - p0)
    return clamp(value, 0.0, 1.0), True


def auto_beam_for_distance(fixture: FixtureConfig, distance, target_diameter_m, fallback_zoom=0.5, fallback_iris=1.0):
    zoom, required, available = auto_zoom_for_distance(fixture, distance, target_diameter_m, fallback_zoom)
    if not available or required is None:
        return zoom, clamp(fallback_iris, 0.0, 1.0), required, False, False

    a0, a100 = fixture.zoom_angle_at_0, fixture.zoom_angle_at_100
    achieved_angle = a0 + (a100 - a0) * zoom

    desired_physical = 1.0
    iris_used = False
    if achieved_angle > required + 0.0001 and achieved_angle > 0:
        desired_physical = clamp(required / achieved_angle, 0.0, 1.0)
        iris_used = True

    iris, iris_available = _iris_control_for_physical(fixture, desired_physical, fallback_iris)
    return zoom, iris, required, True, bool(iris_used and iris_available)


def write_fixture_to_frame(frame, fixture: FixtureConfig, pan, tilt, fader, blackout,
                            zoom=0.5, iris=1.0, focus=0.5,
                            intensity_passthrough=False, beam_passthrough=False):
    """Write one fixture's channels into a 512-byte DMX frame.

    intensity_passthrough/beam_passthrough are for console-relayed fixtures:
    when set, dimmer/shutter or zoom/iris/focus are left exactly as they
    already are in `frame` (presumably the console's own live values from
    the active DMX-in source) instead of being written here, *except* when
    a safety condition (blackout, or a limit-blackout zoom/iris-to-100%
    override) needs that specific channel regardless.
    """
    plim = clamp(pan, fixture.pan_min, fixture.pan_max)
    tlim = clamp(tilt, fixture.tilt_min, fixture.tilt_max)
    pan_limit = not math.isclose(pan, plim, abs_tol=1e-9)
    tilt_limit = not math.isclose(tilt, tlim, abs_tol=1e-9)
    limit_blackout = bool((pan_limit or tilt_limit) and fixture.blackout_on_limit)
    force_zoom_100 = limit_blackout and fixture.limit_blackout_zoom_100
    force_iris_100 = limit_blackout and fixture.limit_blackout_iris_100
    if force_zoom_100:
        zoom = 1.0
    if force_iris_100:
        iris = 1.0
    pan_fraction = (plim - fixture.pan_min) / (fixture.pan_max - fixture.pan_min)
    tilt_fraction = (tlim - fixture.tilt_min) / (fixture.tilt_max - fixture.tilt_min)
    pc, pf = dmx16(pan_fraction)
    tc, tf = dmx16(tilt_fraction)

    values = [
        (fixture.pan_coarse, pc),
        (fixture.pan_fine, pf),
        (fixture.tilt_coarse, tc),
        (fixture.tilt_fine, tf),
    ]

    if not (intensity_passthrough and not blackout and not limit_blackout):
        intensity = 0.0 if (blackout or limit_blackout) else clamp(fader * fixture.intensity_scale, 0.0, 1.0)
        dc, df = dmx16(intensity)
        values.append((fixture.dimmer, dc))
        if fixture.dimmer_fine:
            values.append((fixture.dimmer_fine, df))
        if fixture.shutter:
            values.append((fixture.shutter, 0 if blackout else fixture.shutter_open))

    def add_parameter(coarse_channel, fine_channel, value, reverse=False):
        if not coarse_channel:
            return
        fraction = clamp(float(value), 0.0, 1.0)
        if reverse:
            fraction = 1.0 - fraction
        coarse, fine = dmx16(fraction)
        values.append((coarse_channel, coarse))
        if fine_channel:
            values.append((fine_channel, fine))

    if not beam_passthrough or force_zoom_100:
        add_parameter(fixture.zoom, fixture.zoom_fine, zoom, fixture.zoom_reverse)

    if fixture.iris and (not beam_passthrough or force_iris_100):
        iris_fraction = clamp(float(iris), 0.0, 1.0)
        if fixture.iris_reverse:
            iris_fraction = 1.0 - iris_fraction
        iris_cap = int(clamp(fixture.iris_100_dmx, 0, 255))
        values.append((fixture.iris, round(iris_fraction * iris_cap)))

    if not beam_passthrough:
        add_parameter(fixture.focus, fixture.focus_fine, focus, fixture.focus_reverse)

    for channel, value in values:
        if 1 <= channel <= 512:
            frame[channel - 1] = int(clamp(value, 0, 255))

    return {
        "pan": pan,
        "tilt": tilt,
        "pan_dmx_angle": plim,
        "tilt_dmx_angle": tlim,
        "pan_limit": pan_limit,
        "tilt_limit": tilt_limit,
        "limit_blackout": limit_blackout,
    }


class CycleState:
    """Cross-cycle memory for run_cycle(), explicit instead of hidden in
    closures/instance attributes, so a fresh CycleState() gives fully
    reproducible behaviour in tests."""

    def __init__(self):
        self.smoothed = {}
        self.previous_pan = {}
        self.was_stale = {}
        self.previous_marker = {}
        self.previous_mode = {}
        self.marker_change_until = {}


def run_cycle(settings, trackers, bus, fader, zoom_value, iris_value, focus_value, armed, cycle_start, state: CycleState):
    """One tracking/output cycle. Returns (frames: dict[int, bytearray], light_statuses: list[dict]).

    This is the direct, explicitly-parameterised replacement for the body of
    App.loop() in fart.py v1 -- same behaviour, but with every input passed
    in instead of read off `self`, so it can be called from a plain test
    with synthetic trackers/bus and no engine, sockets, or GUI involved.
    """
    smoothing = clamp(settings.psn_in.smoothing, 0.0, 0.95)
    alpha = 1.0 - smoothing
    timeout_s = settings.psn_in.timeout_s
    lead_lag_s = settings.psn_in.lead_lag_ms / 1000.0

    frames = {}
    console_frame_for_universe = {}
    console_stale_for_universe = {}
    for universe in enabled_output_universes(settings):
        if universe_uses_console_relay(settings, universe):
            raw, ts = bus.get(universe)
            console_frame_for_universe[universe] = raw
            console_stale_for_universe[universe] = raw is None or (cycle_start - ts) > timeout_s
            frames[universe] = bytearray(raw) if raw is not None else bytearray(512)
        else:
            frames[universe] = bytearray(512)

    light_statuses = []

    for index, fixture in enumerate(settings.fixtures):
        if not fixture.enabled:
            continue
        universe = int(fixture.output_universe)
        console_relay = int(fixture.console_mode_channel) > 0
        universe_frame = console_frame_for_universe.get(universe)
        console_stale = console_stale_for_universe.get(universe, False)
        mode = resolve_console_mode(fixture, universe_frame) if console_relay else "auto"

        # For the Operator tab's "DMX In Mode"/"DMX In Marker" columns: None
        # means "this fixture has no console relay configured" (or no
        # marker-select channel), distinct from an actual resolved value,
        # so the UI can show "--" rather than a misleading "Auto"/marker id
        # for a fixture that never reads DMX-in at all.
        dmx_mode = mode if console_relay else None
        dmx_marker = (resolve_live_marker_id(fixture, universe_frame)
                      if console_relay and int(fixture.console_marker_channel) > 0 else None)

        if console_relay and mode == "manual" and not console_stale:
            marker_xyz = trackers.get(int(fixture.marker_id))[:3]
            light_statuses.append({
                "index": index, "name": fixture.name, "marker_id": fixture.marker_id,
                "output_universe": fixture.output_universe,
                "marker_xyz": marker_xyz, "fixture_xyz": (fixture.x, fixture.y, fixture.z),
                "error": "MANUAL (console)", "stale": False, "blackout": False,
                "pan_limit": False, "tilt_limit": False,
                "dmx_mode": dmx_mode, "dmx_marker": dmx_marker,
            })
            state.previous_mode[index] = "manual"
            continue

        effective_marker_id = resolve_live_marker_id(fixture, universe_frame) if console_relay else int(fixture.marker_id)
        marker_switched = state.previous_marker.get(index) is not None and state.previous_marker[index] != effective_marker_id
        resumed_from_manual = state.previous_mode.get(index) == "manual"
        if marker_switched or resumed_from_manual:
            state.was_stale.pop(effective_marker_id, None)
            state.smoothed.pop(effective_marker_id, None)
            state.marker_change_until[index] = cycle_start + float(fixture.marker_change_blackout_s)
        state.previous_marker[index] = effective_marker_id
        state.previous_mode[index] = "auto"

        x, y, z, tracker_time = trackers.predict(effective_marker_id, lead_lag_s) if abs(lead_lag_s) > 0.0001 else trackers.get(effective_marker_id)
        stale = tracker_time == 0 or cycle_start - tracker_time > timeout_s
        reacquired = state.was_stale.get(effective_marker_id, False) and not stale
        state.was_stale[effective_marker_id] = stale
        previous_xyz = state.smoothed.get(effective_marker_id)
        if previous_xyz is None or reacquired:
            sx, sy, sz = x, y, z
        else:
            sx = previous_xyz[0] + (x - previous_xyz[0]) * alpha
            sy = previous_xyz[1] + (y - previous_xyz[1]) * alpha
            sz = previous_xyz[2] + (z - previous_xyz[2]) * alpha
        state.smoothed[effective_marker_id] = (sx, sy, sz)

        tracking_lost_blackout = stale and fixture.on_tracking_loss != "Keep current intensity"
        console_lost_blackout = console_relay and console_stale and fixture.on_console_loss != "Keep tracking, hold last dimmer"
        in_marker_change_window = cycle_start < state.marker_change_until.get(index, 0.0)
        blackout = tracking_lost_blackout or console_lost_blackout or in_marker_change_window or not armed
        intensity_passthrough = console_relay and not console_stale
        beam_passthrough = console_relay and not (settings.zoom_mode == "Auto beam size" and fixture_has_zoom_model(fixture))

        try:
            bearing, elevation, pan, tilt, distance = calculate_aim(fixture, sx, sy, sz, state.previous_pan.get(index))
            state.previous_pan[index] = pan
            zoom_out, iris_out = zoom_value, iris_value
            zoom_angle = None
            zoom_auto = iris_auto = False
            if settings.zoom_mode == "Auto beam size":
                zoom_out, iris_out, zoom_angle, zoom_auto, iris_auto = auto_beam_for_distance(
                    fixture, distance, settings.auto_beam_diameter_m, zoom_value, iris_value
                )
            frame = frames.setdefault(universe, bytearray(512))
            status = write_fixture_to_frame(
                frame, fixture, pan, tilt, fader, blackout, zoom_out, iris_out, focus_value,
                intensity_passthrough=intensity_passthrough, beam_passthrough=beam_passthrough,
            )
            status.update({
                "index": index, "name": fixture.name, "marker_id": effective_marker_id, "output_universe": fixture.output_universe,
                "marker_xyz": (sx, sy, sz), "fixture_xyz": (fixture.x, fixture.y, fixture.z),
                "bearing": bearing, "elevation": elevation, "distance": distance,
                "zoom_value": zoom_out, "iris_value": iris_out, "zoom_angle": zoom_angle, "zoom_auto": zoom_auto, "iris_auto": iris_auto,
                "stale": stale, "blackout": blackout,
                "dmx_mode": dmx_mode, "dmx_marker": dmx_marker,
            })
            light_statuses.append(status)
        except ValueError as exc:
            light_statuses.append({
                "index": index, "name": fixture.name, "marker_id": effective_marker_id, "output_universe": fixture.output_universe,
                "marker_xyz": (sx, sy, sz), "fixture_xyz": (fixture.x, fixture.y, fixture.z),
                "error": str(exc), "stale": stale, "blackout": True,
                "pan_limit": False, "tilt_limit": False,
                "dmx_mode": dmx_mode, "dmx_marker": dmx_marker,
            })

    return frames, light_statuses
