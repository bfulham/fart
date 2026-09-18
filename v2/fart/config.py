"""Settings schema and v1 (flat FART.json) -> v2 (nested) migration.

v1's Settings was one flat dataclass mixing PSN, fader, and both DMX-in and
DMX-out concerns together, with only one of Art-Net/sACN/Open DMX ever
representable at a time (the `output` string field) and no way to keep a
second protocol's settings around while it's inactive. v2 groups settings
by concern and keeps *all* protocols' settings persisted at once, alongside
which one is currently active -- see dmx_in.active / dmx_out.active.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_VERSION = 2


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
class FixtureConfig:
    name: str = "Light 1"
    enabled: bool = True
    marker_id: int = 1
    output_universe: int = 0

    x: float = 0.0
    y: float = -8.0
    z: float = 5.0

    pan_zero_bearing: float = 0.0
    tilt_zero_elevation: float = 0.0
    pan_direction: int = 1
    tilt_direction: int = -1
    pan_offset: float = 0.0
    tilt_offset: float = 0.0

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

    blackout_on_limit: bool = False
    limit_blackout_zoom_100: bool = False
    limit_blackout_iris_100: bool = False

    # Live console control: channel numbers on whichever protocol dmx_in.active
    # currently names, within this fixture's own output_universe.
    console_mode_channel: int = 0
    console_marker_channel: int = 0
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
    fixtures: list = field(default_factory=lambda: [FixtureConfig()])


ON_TRACKING_LOSS_OPTIONS = ["Blackout", "Keep current intensity"]
ON_CONSOLE_LOSS_OPTIONS = ["Blackout", "Keep tracking, force dimmer off", "Keep tracking, hold last dimmer"]


def _fixture_from_dict(data: dict) -> FixtureConfig:
    known = {k: v for k, v in data.items() if k in FixtureConfig.__dataclass_fields__}
    return FixtureConfig(**known)


def migrate_v1(data: dict) -> Settings:
    """Map a v1 (flat) FART.json onto the v2 nested Settings shape.

    v1 had a single `output` string ("Open DMX" | "Art-Net" | "sACN") and no
    OSC-in-DMX-in concept (OSC intensity input is dropped entirely in v2 --
    existing OSC users fall back to Manual and need to reconfigure). Fixture
    fields are unchanged and pass through as-is.
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
        settings.fixtures = [_fixture_from_dict(f) for f in fixtures_data if isinstance(f, dict)]
    if not settings.fixtures:
        settings.fixtures = [FixtureConfig()]

    return settings


def load_settings(path: Path) -> Settings:
    if not path.exists():
        return Settings()
    try:
        data = json.loads(path.read_text())
    except Exception:
        return Settings()

    try:
        if data.get("config_version") == CONFIG_VERSION:
            fixtures = [_fixture_from_dict(f) for f in data.get("fixtures", []) if isinstance(f, dict)] or [FixtureConfig()]
            settings = Settings(
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
                fixtures=fixtures,
            )
            return settings
        # Anything without a matching config_version is treated as a v1 file.
        return migrate_v1(data)
    except Exception:
        return Settings()


def save_settings(path: Path, settings: Settings) -> None:
    path.write_text(json.dumps(asdict(settings), indent=2))
