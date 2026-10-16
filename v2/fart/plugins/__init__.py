"""Static plugin registry. Explicit imports (not filesystem-scanned) so
PyInstaller's static analysis bundles every built-in plugin correctly."""
from .artnet_in import ArtNetInPlugin
from .artnet_out import ArtNetOutPlugin
from .open_dmx_out import OpenDMXOutPlugin
from .psn_in import PSNInPlugin
from .sacn_in import SACNInPlugin
from .sacn_out import SACNOutPlugin

POSITION_INPUT_PLUGINS = {
    "psn": PSNInPlugin,
}

CONTROL_INPUT_PLUGINS = {
    "artnet": ArtNetInPlugin,
    "sacn": SACNInPlugin,
}

OUTPUT_PLUGINS = {
    "artnet": ArtNetOutPlugin,
    "sacn": SACNOutPlugin,
    "open_dmx": OpenDMXOutPlugin,
}

__all__ = [
    "ArtNetInPlugin", "ArtNetOutPlugin", "OpenDMXOutPlugin",
    "PSNInPlugin", "SACNInPlugin", "SACNOutPlugin",
    "POSITION_INPUT_PLUGINS", "CONTROL_INPUT_PLUGINS", "OUTPUT_PLUGINS",
]
