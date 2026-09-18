import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fart.gdtf import import_gdtf_channel_mapping, list_gdtf_modes

GDTF_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<FixtureType>\n  <DMXModes>\n'
    '    <DMXMode Name="Basic">\n      <DMXChannels>\n'
    '        <DMXChannel Offset="1"><LogicalChannel Attribute="Dimmer">'
    '<ChannelFunction Name="Dimmer" Attribute="Dimmer" DMXFrom="0/1" DMXTo="255/1" />'
    '</LogicalChannel></DMXChannel>\n      </DMXChannels>\n    </DMXMode>\n'
    '    <DMXMode Name="Extended">\n      <DMXChannels>\n'
    '        <DMXChannel Offset="1"><LogicalChannel Attribute="Shutter1">'
    '<ChannelFunction Name="Shutter closed" Attribute="Shutter1" DMXFrom="0/1" DMXTo="19/1"/>'
    '<ChannelFunction Name="Shutter open" Attribute="Shutter1" DMXFrom="20/1" DMXTo="49/1"/>'
    '<ChannelFunction Name="Strobe" Attribute="Shutter1" DMXFrom="50/1" DMXTo="200/1"/>'
    '</LogicalChannel></DMXChannel>\n'
    '        <DMXChannel Offset="2 3"><LogicalChannel Attribute="Dimmer">'
    '<ChannelFunction Name="Dimmer" Attribute="Dimmer" DMXFrom="0/2" DMXTo="65535/2"/>'
    '</LogicalChannel></DMXChannel>\n'
    '        <DMXChannel Offset="14 15"><LogicalChannel Attribute="Zoom">'
    '<ChannelFunction Name="Zoom" Attribute="Zoom" DMXFrom="0/2" DMXTo="65535/2" PhysicalFrom="2" PhysicalTo="45"/>'
    '</LogicalChannel></DMXChannel>\n'
    '        <DMXChannel Offset="16 17"><LogicalChannel Attribute="Focus">'
    '<ChannelFunction Name="Focus" Attribute="Focus" DMXFrom="0/2" DMXTo="65535/2"/>'
    '</LogicalChannel></DMXChannel>\n'
    '        <DMXChannel Offset="18 19"><LogicalChannel Attribute="Pan">'
    '<ChannelFunction Name="Pan" Attribute="Pan" DMXFrom="0/2" DMXTo="65535/2"/>'
    '</LogicalChannel></DMXChannel>\n'
    '        <DMXChannel Offset="20 21"><LogicalChannel Attribute="Tilt">'
    '<ChannelFunction Name="Tilt" Attribute="Tilt" DMXFrom="0/2" DMXTo="65535/2"/>'
    '</LogicalChannel></DMXChannel>\n'
    '        <DMXChannel Offset="13"><LogicalChannel Attribute="Iris">'
    '<ChannelFunction Name="Iris" Attribute="Iris" DMXFrom="0/1" DMXTo="191/1" PhysicalFrom="1" PhysicalTo="0"/>'
    '<ChannelFunction Name="Iris pulse effect" Attribute="Iris" DMXFrom="192/1" DMXTo="255/1"/>'
    '</LogicalChannel></DMXChannel>\n      </DMXChannels>\n    </DMXMode>\n  </DMXModes>\n</FixtureType>\n'
)


def make_gdtf():
    tmp = tempfile.NamedTemporaryFile(suffix=".gdtf", delete=False)
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w") as zf:
        zf.writestr("description.xml", GDTF_XML)
    return Path(tmp.name)


class GDTFImportTests(unittest.TestCase):
    def test_mode_selection_and_practical_values(self):
        path = make_gdtf()
        try:
            modes = list_gdtf_modes(path)
            self.assertEqual(modes, ["Basic", "Extended"])
            mapping, _modes, selected = import_gdtf_channel_mapping(path, start_address=101, preferred_mode="Extended")
            self.assertEqual(selected, "Extended")
            self.assertEqual(mapping["shutter"], 101)
            self.assertEqual(mapping["shutter_open"], 34)
            self.assertEqual(mapping["dimmer"], 102)
            self.assertEqual(mapping["dimmer_fine"], 103)
            self.assertEqual(mapping["iris"], 113)
            self.assertEqual(mapping["iris_100_dmx"], 191)
            self.assertEqual(mapping["iris_physical_at_0"], 1.0)
            self.assertEqual(mapping["iris_physical_at_100"], 0.0)
            self.assertEqual(mapping["zoom"], 114)
            self.assertEqual(mapping["zoom_fine"], 115)
            self.assertEqual(mapping["zoom_angle_at_0"], 2.0)
            self.assertEqual(mapping["zoom_angle_at_100"], 45.0)
            self.assertEqual(mapping["focus"], 116)
            self.assertEqual(mapping["focus_fine"], 117)
            self.assertEqual(mapping["pan_coarse"], 118)
            self.assertEqual(mapping["pan_fine"], 119)
            self.assertEqual(mapping["tilt_coarse"], 120)
            self.assertEqual(mapping["tilt_fine"], 121)
        finally:
            path.unlink(missing_ok=True)

    def test_default_mode_is_first_when_no_preference_given(self):
        path = make_gdtf()
        try:
            mapping, _modes, selected = import_gdtf_channel_mapping(path, start_address=1)
            self.assertEqual(selected, "Basic")
            self.assertEqual(mapping["dimmer"], 1)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
