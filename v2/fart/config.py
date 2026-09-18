"""Settings schema and migration to the current shape.

v1's Settings was one flat dataclass mixing PSN, fader, and both DMX-in and
DMX-out concerns together, with only one of Art-Net/sACN/Open DMX ever
representable at a time (the `output` string field) and no way to keep a
second protocol's settings around while it's inactive. v2 groups settings
by concern and keeps *all* protocols' settings persisted at once, alongside
which one is currently active -- see dmx_in.active / dmx_out.active.

Schema v3 (current) splits what used to be one flat per-fixture dataclass
(~14 raw absolute DMX channel numbers) into two pieces, the way a real
lighting console patches a fixture:

- FixtureType: a reusable *personality* -- channel offsets within the
  fixture's own footprint (not absolute channel numbers) plus physical
  properties (pan/tilt range, beam model, reverse flags). Defined once,
  referenced by any number of fixture instances.
- FixtureConfig: an instance -- position, calibration, and three
  independent DMX patches (universe + start address each): where FART
  sends its own computed output, where a live console feed of this
  fixture's *entire* real channel footprint can be read from (so FART can
  pass through whatever it doesn't understand -- color, gobo, whatever --
  and channels it only sometimes owns, like zoom/iris when auto-beam-size
  is off), and where a small mode/marker control block lives for live
  console relay switching. These can all be on different universes with
  different start addresses -- input and output no longer have to share a
  universe number, which was a real conflict risk when they did.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_VERSION = 3
DEFAULT_FIXTURE_TYPE_ID = "default"


@dataclass
class PSNInConfig:
    multicast: str = "236.10.10.10"
    port: int = 56565
    interface: str = "0.0.0.0"
    default_marker_id: int = 1
    timeout_s: float = 0.5
    smoothing: float = 0.12
    lead_lag_ms: float = 0.0


@dataclass
class ArtNetInConfig:
    universe: int = 0


@dataclass
class SACNInConfig:
    universe: int = 1


@dataclass
class DMXInConfig:
    active: str = "artnet"  # "artnet" | "sacn"
    artnet: ArtNetInConfig = field(default_factory=ArtNetInConfig)
    sacn: SACNInConfig = field(default_factory=SACNInConfig)


@dataclass
class ArtNetOutConfig:
    target_ip: str = "255.255.255.255"


@dataclass
class SACNOutConfig:
    universes: list = field(default_factory=lambda: [1])


@dataclass
class OpenDMXOutConfig:
    adapters: str = ""  # "COM3=0, COM4=1"; blank uses fallback_port for universe 0
    fallback_port: str = "COM3"


@dataclass
class DMXOutConfig:
    active: str = "artnet"  # "artnet" | "sacn" | "open_dmx"
    artnet: ArtNetOutConfig = field(default_factory=ArtNetOutConfig)
    sacn: SACNOutConfig = field(default_factory=SACNOutConfig)
    open_dmx: OpenDMXOutConfig = field(default_factory=OpenDMXOutConfig)


@dataclass
class FaderConfig:
    source: str = "manual"  # "manual" | "dmx_in"
    channel: int = 1  # channel on whichever protocol dmx_in.active names


@dataclass
class FixtureType:
    """A reusable fixture personality: channel offsets *within the
    fixture's own footprint* (1-based, 0 = "this function has no
    channel" -- same convention the old absolute channel numbers used),
    plus the physical properties of the model. `footprint` is the total
    channel count the real fixture uses (e.g. 35), which can be much
    larger than the handful of channels FART actually drives -- it's
    needed so a console shadow-patch copy can bring across every channel,
    not just the ones FART knows about.
    """
    id: str = ""
    name: str = "New Fixture Type"
    footprint: int = 0  # 0 = derive from the highest offset used below

    pan_min: float = -270.0
    pan_max: float = 270.0
    tilt_min: float = -135.0
    tilt_max: float = 135.0

    pan_coarse: int = 1
    pan_fine: int = 2
    tilt_coarse: int = 3
    tilt_fine: int = 4
    dimmer: int = 5
    dimmer_fine: int = 0
    shutter: int = 0
    shutter_open: int = 255
    intensity_scale: float = 1.0

    zoom: int = 0
    zoom_fine: int = 0
    iris: int = 0
    iris_100_dmx: int = 255
    iris_physical_at_0: float = 0.0
    iris_physical_at_100: float = 0.0
    focus: int = 0
    focus_fine: int = 0
    zoom_reverse: bool = False
    iris_reverse: bool = False
    focus_reverse: bool = False

    zoom_angle_at_0: float = 0.0
    zoom_angle_at_100: float = 0.0

    def effective_footprint(self) -> int:
        if self.footprint > 0:
            return self.footprint
        offsets = [self.pan_coarse, self.pan_fine, self.tilt_coarse, self.tilt_fine,
                   self.dimmer, self.dimmer_fine, self.shutter, self.zoom, self.zoom_fine,
                   self.iris, self.focus, self.focus_fine]
        return max(offsets) if any(offsets) else 0


@dataclass
class FixtureConfig:
    name: str = "Light 1"
    enabled: bool = True
    marker_id: int = 1
    fixture_type_id: str = DEFAULT_FIXTURE_TYPE_ID

    x: float = 0.0
    y: float = -8.0
    z: float = 5.0

    pan_zero_bearing: float = 0.0
    tilt_zero_elevation: float = 0.0
    pan_direction: int = 1
    tilt_direction: int = -1
    pan_offset: float = 0.0
    tilt_offset: float = 0.0

    # Where FART sends its own computed output for this fixture.
    output_universe: int = 0
    output_start_address: int = 1

    # Where a live console feed of this fixture's *entire* real channel
    # footprint can be read from, at the same offsets as output_start_address
    # (per fixture_type) but on the console's own patch -- 0 = no shadow feed,
    # this fixture always uses FART's own manual/master beam values instead.
    shadow_universe: int = 0
    shadow_start_address: int = 1

    # A small mode/marker control block for live relay-mode switching --
    # independent of the shadow feed above, and can be anywhere.
    console_universe: int = 0
    console_mode_channel: int = 0
    console_marker_channel: int = 0

    blackout_on_limit: bool = False
    limit_blackout_zoom_100: bool = False
    limit_blackout_iris_100: bool = False
    on_tracking_loss: str = "Blackout"  # "Blackout" | "Keep current intensity"
    on_console_loss: str = "Blackout"   # "Blackout" | "Keep tracking, force dimmer off" | "Keep tracking, hold last dimmer"
    marker_change_blackout_s: float = 0.5


@dataclass
class Settings:
    config_version: int = CONFIG_VERSION
    psn_in: PSNInConfig = field(default_factory=PSNInConfig)
    dmx_in: DMXInConfig = field(default_factory=DMXInConfig)
    dmx_out: DMXOutConfig = field(default_factory=DMXOutConfig)
    fader: FaderConfig = field(default_factory=FaderConfig)
    refresh_hz: int = 30
    zoom_master: float = 0.5
    iris_master: float = 1.0
    focus_master: float = 0.5
    zoom_mode: str = "Manual"  # "Manual" | "Auto beam size"
    auto_beam_diameter_m: float = 1.0
    fixture_types: list = field(default_factory=lambda: [FixtureType(id=DEFAULT_FIXTURE_TYPE_ID, name="Default Type")])
    fixtures: list = field(default_factory=lambda: [FixtureConfig()])


ON_TRACKING_LOSS_OPTIONS = ["Blackout", "Keep current intensity"]
ON_CONSOLE_LOSS_OPTIONS = ["Blackout", "Keep tracking, force dimmer off", "Keep tracking, hold last dimmer"]


def _fixture_type_from_dict(data: dict) -> FixtureType:
    known = {k: v for k, v in data.items() if k in FixtureType.__dataclass_fields__}
    return FixtureType(**known)


def _fixture_from_dict(data: dict) -> FixtureConfig:
    known = {k: v for k, v in data.items() if k in FixtureConfig.__dataclass_fields__}
    return FixtureConfig(**known)


def _split_legacy_fixture(data: dict, index: int) -> tuple[FixtureType, FixtureConfig]:
    """Convert one pre-v3 fixture dict (absolute channel numbers, a single
    output_universe used for both DMX in and out) into a (FixtureType,
    FixtureConfig) pair. Numerically lossless: with output_start_address=1,
    "offset within footprint" and "absolute channel number" are the same
    number, so the old channel fields become the new type's offsets
    unchanged. Console relay (if any) keeps working exactly as before --
    all three new patches default to the old shared output_universe -- the
    user can then split them apart onto different universes as needed.
    """
    type_id = f"legacy-{index}-{uuid.uuid4().hex[:8]}"
    old_universe = int(data.get("output_universe", 0))
    fixture_type = FixtureType(
        id=type_id,
        name=f"{data.get('name', f'Light {index + 1}')} (imported)",
        pan_min=float(data.get("pan_min", -270.0)), pan_max=float(data.get("pan_max", 270.0)),
        tilt_min=float(data.get("tilt_min", -135.0)), tilt_max=float(data.get("tilt_max", 135.0)),
        pan_coarse=int(data.get("pan_coarse", 1)), pan_fine=int(data.get("pan_fine", 2)),
        tilt_coarse=int(data.get("tilt_coarse", 3)), tilt_fine=int(data.get("tilt_fine", 4)),
        dimmer=int(data.get("dimmer", 5)), dimmer_fine=int(data.get("dimmer_fine", 0)),
        shutter=int(data.get("shutter", 0)), shutter_open=int(data.get("shutter_open", 255)),
        intensity_scale=float(data.get("intensity_scale", 1.0)),
        zoom=int(data.get("zoom", 0)), zoom_fine=int(data.get("zoom_fine", 0)),
        iris=int(data.get("iris", 0)), iris_100_dmx=int(data.get("iris_100_dmx", 255)),
        iris_physical_at_0=float(data.get("iris_physical_at_0", 0.0)),
        iris_physical_at_100=float(data.get("iris_physical_at_100", 0.0)),
        focus=int(data.get("focus", 0)), focus_fine=int(data.get("focus_fine", 0)),
        zoom_reverse=bool(data.get("zoom_reverse", False)), iris_reverse=bool(data.get("iris_reverse", False)),
        focus_reverse=bool(data.get("focus_reverse", False)),
        zoom_angle_at_0=float(data.get("zoom_angle_at_0", 0.0)), zoom_angle_at_100=float(data.get("zoom_angle_at_100", 0.0)),
    )
    console_mode_channel = int(data.get("console_mode_channel", 0))
    fixture = FixtureConfig(
        name=data.get("name", f"Light {index + 1}"),
        enabled=bool(data.get("enabled", True)),
        marker_id=int(data.get("marker_id", 1)),
        fixture_type_id=type_id,
        x=float(data.get("x", 0.0)), y=float(data.get("y", -8.0)), z=float(data.get("z", 5.0)),
        pan_zero_bearing=float(data.get("pan_zero_bearing", 0.0)),
        tilt_zero_elevation=float(data.get("tilt_zero_elevation", 0.0)),
        pan_direction=int(data.get("pan_direction", 1)), tilt_direction=int(data.get("tilt_direction", -1)),
        pan_offset=float(data.get("pan_offset", 0.0)), tilt_offset=float(data.get("tilt_offset", 0.0)),
        output_universe=old_universe, output_start_address=1,
        # Old configs assumed input and output shared a universe; carry
        # that forward as the starting point rather than silently
        # disabling relay/shadow on migration -- the user can now split
        # them apart since that's exactly the conflict being fixed.
        shadow_universe=old_universe if console_mode_channel > 0 else 0, shadow_start_address=1,
        console_universe=old_universe if console_mode_channel > 0 else 0,
        console_mode_channel=console_mode_channel,
        console_marker_channel=int(data.get("console_marker_channel", 0)),
        blackout_on_limit=bool(data.get("blackout_on_limit", False)),
        limit_blackout_zoom_100=bool(data.get("limit_blackout_zoom_100", False)),
        limit_blackout_iris_100=bool(data.get("limit_blackout_iris_100", False)),
        on_tracking_loss=data.get("on_tracking_loss", "Blackout"),
        on_console_loss=data.get("on_console_loss", "Blackout"),
        marker_change_blackout_s=float(data.get("marker_change_blackout_s", 0.5)),
    )
    return fixture_type, fixture


def _split_legacy_fixtures(fixtures_data) -> tuple[list, list]:
    fixture_types, fixtures = [], []
    for index, f in enumerate(fixtures_data):
        if not isinstance(f, dict):
            continue
        fixture_type, fixture = _split_legacy_fixture(f, index)
        fixture_types.append(fixture_type)
        fixtures.append(fixture)
    return fixture_types, fixtures


def migrate_v1(data: dict) -> Settings:
    """Map a v1 (flat) FART.json, or a pre-v3 v2 FART2.json (both used
    absolute per-fixture channel numbers with no fixture-type concept),
    onto the current nested Settings shape.

    v1 had a single `output` string ("Open DMX" | "Art-Net" | "sACN") and no
    OSC-in-DMX-in concept (OSC intensity input is dropped entirely in v2 --
    existing OSC users fall back to Manual and need to reconfigure).
    """
    settings = Settings()

    settings.psn_in.multicast = data.get("psn_multicast", settings.psn_in.multicast)
    settings.psn_in.port = int(data.get("psn_port", settings.psn_in.port))
    settings.psn_in.interface = data.get("psn_interface", settings.psn_in.interface)
    settings.psn_in.default_marker_id = int(data.get("marker_id", settings.psn_in.default_marker_id))
    settings.psn_in.timeout_s = float(data.get("timeout_s", settings.psn_in.timeout_s))
    settings.psn_in.smoothing = float(data.get("smoothing", settings.psn_in.smoothing))
    settings.psn_in.lead_lag_ms = float(data.get("lead_lag_ms", settings.psn_in.lead_lag_ms))

    settings.dmx_in.artnet.universe = int(data.get("artnet_input_universe", 0))
    fader_mode = data.get("fader_mode", "Manual")
    if fader_mode == "Art-Net Input":
        settings.fader.source = "dmx_in"
        settings.fader.channel = int(data.get("artnet_input_channel", 1))
        settings.dmx_in.active = "artnet"
    else:
        settings.fader.source = "manual"
        settings.fader.channel = int(data.get("artnet_input_channel", 1))
    settings.fader.channel = max(1, settings.fader.channel)

    output = data.get("output", "Open DMX")
    settings.dmx_out.artnet.target_ip = data.get("artnet_ip", settings.dmx_out.artnet.target_ip)
    universe = int(data.get("universe", 0))
    if output == "Art-Net":
        settings.dmx_out.active = "artnet"
        settings.dmx_out.artnet.target_ip = data.get("artnet_ip", settings.dmx_out.artnet.target_ip)
    elif output == "sACN":
        settings.dmx_out.active = "sacn"
        settings.dmx_out.sacn.universes = sorted({int(f.get("output_universe", universe))
                                                    for f in data.get("fixtures", []) if f.get("enabled", True)}) or [universe or 1]
    else:
        settings.dmx_out.active = "open_dmx"
        settings.dmx_out.open_dmx.adapters = data.get("open_dmx_adapters", "")
        settings.dmx_out.open_dmx.fallback_port = data.get("serial_port", "COM3")

    settings.refresh_hz = int(data.get("refresh_hz", settings.refresh_hz))
    settings.zoom_master = float(data.get("zoom_master", settings.zoom_master))
    settings.iris_master = float(data.get("iris_master", settings.iris_master))
    settings.focus_master = float(data.get("focus_master", settings.focus_master))
    settings.zoom_mode = data.get("zoom_mode", settings.zoom_mode)
    settings.auto_beam_diameter_m = float(data.get("auto_beam_diameter_m", settings.auto_beam_diameter_m))

    fixtures_data = data.get("fixtures")
    if isinstance(fixtures_data, list) and fixtures_data:
        fixture_types, fixtures = _split_legacy_fixtures(fixtures_data)
        if fixtures:
            settings.fixture_types = fixture_types
            settings.fixtures = fixtures

    return settings


def _settings_from_v3_dict(data: dict) -> Settings:
    fixture_types = [_fixture_type_from_dict(t) for t in data.get("fixture_types", []) if isinstance(t, dict)] \
        or [FixtureType(id=DEFAULT_FIXTURE_TYPE_ID, name="Default Type")]
    fixtures = [_fixture_from_dict(f) for f in data.get("fixtures", []) if isinstance(f, dict)] or [FixtureConfig()]
    return Settings(
        psn_in=PSNInConfig(**{k: v for k, v in data.get("psn_in", {}).items() if k in PSNInConfig.__dataclass_fields__}),
        dmx_in=DMXInConfig(
            active=data.get("dmx_in", {}).get("active", "artnet"),
            artnet=ArtNetInConfig(**{k: v for k, v in data.get("dmx_in", {}).get("artnet", {}).items() if k in ArtNetInConfig.__dataclass_fields__}),
            sacn=SACNInConfig(**{k: v for k, v in data.get("dmx_in", {}).get("sacn", {}).items() if k in SACNInConfig.__dataclass_fields__}),
        ),
        dmx_out=DMXOutConfig(
            active=data.get("dmx_out", {}).get("active", "artnet"),
            artnet=ArtNetOutConfig(**{k: v for k, v in data.get("dmx_out", {}).get("artnet", {}).items() if k in ArtNetOutConfig.__dataclass_fields__}),
            sacn=SACNOutConfig(**{k: v for k, v in data.get("dmx_out", {}).get("sacn", {}).items() if k in SACNOutConfig.__dataclass_fields__}),
            open_dmx=OpenDMXOutConfig(**{k: v for k, v in data.get("dmx_out", {}).get("open_dmx", {}).items() if k in OpenDMXOutConfig.__dataclass_fields__}),
        ),
        fader=FaderConfig(**{k: v for k, v in data.get("fader", {}).items() if k in FaderConfig.__dataclass_fields__}),
        refresh_hz=int(data.get("refresh_hz", 30)),
        zoom_master=float(data.get("zoom_master", 0.5)),
        iris_master=float(data.get("iris_master", 1.0)),
        focus_master=float(data.get("focus_master", 0.5)),
        zoom_mode=data.get("zoom_mode", "Manual"),
        auto_beam_diameter_m=float(data.get("auto_beam_diameter_m", 1.0)),
        fixture_types=fixture_types,
        fixtures=fixtures,
    )


def load_settings(path: Path) -> Settings:
    if not path.exists():
        return Settings()
    try:
        data = json.loads(path.read_text())
    except Exception:
        return Settings()

    try:
        if data.get("config_version") == CONFIG_VERSION:
            return _settings_from_v3_dict(data)
        # Anything without a matching config_version is treated as a
        # pre-v3 file (v1's flat FART.json, or v2's flat-fixture FART2.json)
        # and run through the same absolute-channels-to-type-and-patch split.
        return migrate_v1(data)
    except Exception:
        return Settings()


def save_settings(path: Path, settings: Settings) -> None:
    path.write_text(json.dumps(asdict(settings), indent=2))
