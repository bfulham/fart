import json
import sys
import tempfile
import unittest
import zipfile
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fart.config import (
    CONFIG_ENTRY_NAME, CONFIG_VERSION, FixtureConfig, FixtureType, Settings,
    load_settings, migrate_v1, save_settings,
)


class MigrateV1Tests(unittest.TestCase):
    def test_manual_fader_open_dmx(self):
        v1 = {
            "psn_multicast": "236.10.10.10", "psn_port": 56565, "psn_interface": "0.0.0.0",
            "marker_id": 2, "timeout_s": 0.7, "smoothing": 0.2, "lead_lag_ms": 10.0,
            "fader_mode": "Manual", "output": "Open DMX", "serial_port": "COM5",
            "open_dmx_adapters": "COM5=0", "universe": 0,
            "fixtures": [{"name": "A", "output_universe": 0, "marker_id": 2}],
        }
        settings = migrate_v1(v1)
        self.assertEqual(settings.psn_in.default_marker_id, 2)
        self.assertEqual(settings.psn_in.timeout_s, 0.7)
        self.assertEqual(settings.fader.source, "manual")
        self.assertEqual(settings.dmx_out.active, "open_dmx")
        self.assertEqual(settings.dmx_out.open_dmx.adapters, "COM5=0")
        self.assertEqual(settings.fixtures[0].name, "A")

    def test_artnet_input_fader_becomes_dmx_in(self):
        v1 = {
            "fader_mode": "Art-Net Input", "artnet_input_universe": 3, "artnet_input_channel": 7,
            "output": "Art-Net", "artnet_ip": "10.0.0.5",
            "fixtures": [{"name": "A"}],
        }
        settings = migrate_v1(v1)
        self.assertEqual(settings.fader.source, "dmx_in")
        self.assertEqual(settings.fader.channel, 7)
        self.assertEqual(settings.dmx_in.active, "artnet")
        self.assertEqual(settings.dmx_in.artnet.universe, 3)
        self.assertEqual(settings.dmx_out.active, "artnet")
        self.assertEqual(settings.dmx_out.artnet.target_ip, "10.0.0.5")

    def test_sacn_output_collects_enabled_fixture_universes(self):
        v1 = {
            "output": "sACN",
            "fixtures": [
                {"name": "A", "output_universe": 1, "enabled": True},
                {"name": "B", "output_universe": 2, "enabled": True},
                {"name": "C", "output_universe": 3, "enabled": False},
            ],
        }
        settings = migrate_v1(v1)
        self.assertEqual(settings.dmx_out.active, "sacn")
        self.assertEqual(settings.dmx_out.sacn.universes, [1, 2])

    def test_missing_fixtures_gets_one_default(self):
        settings = migrate_v1({})
        self.assertEqual(len(settings.fixtures), 1)
        self.assertIsInstance(settings.fixtures[0], FixtureConfig)


class LoadSaveRoundTripTests(unittest.TestCase):
    def test_save_then_load_preserves_both_dmx_in_protocols(self):
        settings = Settings()
        settings.dmx_in.active = "sacn"
        settings.dmx_in.artnet.universe = 5
        settings.dmx_in.sacn.universe = 9
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "FART.fart"
            save_settings(path, settings)
            self.assertTrue(zipfile.is_zipfile(path), "a saved settings file is a zip container")
            with zipfile.ZipFile(path) as zf:
                data = json.loads(zf.read(CONFIG_ENTRY_NAME))
            self.assertEqual(data["config_version"], CONFIG_VERSION)
            loaded = load_settings(path)
            self.assertEqual(loaded.dmx_in.active, "sacn")
            # Both protocols' settings survive even though only one is active.
            self.assertEqual(loaded.dmx_in.artnet.universe, 5)
            self.assertEqual(loaded.dmx_in.sacn.universe, 9)

    def test_load_missing_file_returns_defaults(self):
        settings = load_settings(Path("/nonexistent/path/FART.fart"))
        self.assertEqual(settings.dmx_in.active, "artnet")

    def test_load_v1_file_is_migrated(self):
        # A pre-.fart plain-JSON file -- must still load even though it is
        # not a zip at all, not just an older JSON schema inside one.
        v1 = {"output": "sACN", "fixtures": [{"name": "X", "output_universe": 4}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "FART.json"
            path.write_text(json.dumps(v1))
            settings = load_settings(path)
            self.assertEqual(settings.dmx_out.active, "sacn")
            self.assertEqual(settings.fixtures[0].name, "X")

    def test_gdtf_data_round_trips_through_its_own_zip_entry_not_the_json(self):
        settings = Settings()
        settings.fixture_types = [FixtureType(id="t1", name="Robe MegaPointe", gdtf_data=b"PK\x03\x04fake-gdtf-bytes")]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "FART.fart"
            save_settings(path, settings)
            with zipfile.ZipFile(path) as zf:
                data = json.loads(zf.read(CONFIG_ENTRY_NAME))
                self.assertNotIn("gdtf_data", data["fixture_types"][0],
                                  "binary GDTF data must not be embedded in config.json")
                self.assertEqual(zf.read("gdtf/t1.gdtf"), b"PK\x03\x04fake-gdtf-bytes")
            loaded = load_settings(path)
            self.assertEqual(loaded.fixture_types[0].gdtf_data, b"PK\x03\x04fake-gdtf-bytes")

    def test_custom_type_with_no_gdtf_data_writes_no_zip_entry(self):
        settings = Settings()
        settings.fixture_types = [FixtureType(id="t1", name="Custom")]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "FART.fart"
            save_settings(path, settings)
            with zipfile.ZipFile(path) as zf:
                self.assertNotIn("gdtf/t1.gdtf", zf.namelist())
            loaded = load_settings(path)
            self.assertEqual(loaded.fixture_types[0].gdtf_data, b"")


if __name__ == "__main__":
    unittest.main()
