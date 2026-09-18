"""GDTF (.gdtf) fixture file import. Ported from fart.py v1 with behaviour
unchanged, minus the Tk mode-selection dialog (select_gdtf_mode in v1) --
that's a UI concern for whatever calls list_gdtf_modes()/
import_gdtf_channel_mapping() to handle, not part of the import logic
itself.

GDTF import is a best-effort setup helper: always verify imported channels
and values against the fixture's manual before moving a real fixture.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile

from .engine import clamp


def _strip_ns(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _xml_attr(node, name, default=""):
    # GDTF files vary in case and namespace handling between tools. Keep this forgiving.
    for key, value in node.attrib.items():
        if key.lower() == name.lower():
            return value
    return default


def _parse_gdtf_offsets(value):
    if not value:
        return []
    text = str(value).replace("{", "").replace("}", "").replace(",", " ")
    out = []
    for part in text.split():
        try:
            out.append(int(part))
        except Exception:
            pass
    return out


def _read_gdtf_description(path):
    with zipfile.ZipFile(path, "r") as zf:
        names = zf.namelist()
        desc_name = next((n for n in names if n.lower().endswith("description.xml")), None)
        if not desc_name:
            raise ValueError("No description.xml found in the GDTF file")
        return ET.fromstring(zf.read(desc_name))


def list_gdtf_modes(path):
    root = _read_gdtf_description(path)
    modes = [node for node in root.iter() if _strip_ns(node.tag) == "DMXMode"]
    names = [_xml_attr(m, "Name", f"Mode {i + 1}") for i, m in enumerate(modes)]
    if not names:
        raise ValueError("No DMXMode entries found in the GDTF file")
    return names


def _parse_gdtf_dmx_value(value, default=None):
    if value is None or value == "":
        return default
    text = str(value).strip().replace("{", "").replace("}", "")
    if not text:
        return default
    # GDTF often stores values like "20/1" or "32768/2". The first
    # number is the actual DMX value; the value after / is the byte count.
    text = text.split()[0].split(",")[0].split("/")[0]
    try:
        return int(float(text))
    except Exception:
        return default


def _parse_gdtf_physical(value, default=None):
    if value is None or value == "":
        return default
    text = str(value).strip().replace("{", "").replace("}", "")
    if not text:
        return default
    # GDTF physical values are plain numbers for angular attributes in degrees.
    # Some exporters include units or multi-value text; keep the first token.
    token = text.replace(",", " ").split()[0]
    try:
        return float(token)
    except Exception:
        return default


def _gdtf_range_from_node(node):
    start = _parse_gdtf_dmx_value(_xml_attr(node, "DMXFrom", ""))
    end = _parse_gdtf_dmx_value(_xml_attr(node, "DMXTo", ""))
    if start is None and end is None:
        return None
    if start is None:
        start = end
    if end is None:
        end = start
    if end < start:
        start, end = end, start
    return int(start), int(end)


def _derive_zoom_angles(dmx_channel):
    """Return beam/field angle at zoom 0% and 100% if GDTF provides it.

    GDTF stores this most commonly on a Zoom ChannelFunction as PhysicalFrom
    and PhysicalTo. Not guaranteed to exist -- always a best-effort import,
    remains editable afterward.
    """
    candidates = []
    for node in dmx_channel.iter():
        tag = _strip_ns(node.tag)
        if tag not in ("ChannelFunction", "LogicalChannel"):
            continue
        attr_text = " ".join(filter(None, [_xml_attr(node, "Attribute", ""), _xml_attr(node, "Name", "")])).lower()
        if "zoom" not in attr_text:
            continue
        physical_from = _parse_gdtf_physical(_xml_attr(node, "PhysicalFrom", ""))
        physical_to = _parse_gdtf_physical(_xml_attr(node, "PhysicalTo", ""))
        if physical_from is None or physical_to is None:
            continue
        if physical_from <= 0 or physical_to <= 0:
            continue
        r = _gdtf_range_from_node(node) or (0, 255)
        candidates.append((abs(r[1] - r[0]), float(physical_from), float(physical_to)))
    if not candidates:
        return None
    _span, a0, a100 = max(candidates, key=lambda item: item[0])
    return a0, a100


def _derive_pan_tilt_limits(dmx_channel, kind):
    """Best-effort physical pan/tilt limit import from GDTF PhysicalFrom/To.

    GDTF files commonly store Pan/Tilt physical values as either centred
    ranges (-270..270) or positive spans (0..540). FART expects fixture DMX
    angle ranges around the mechanical centre, so positive-only ranges are
    converted to +/- span/2.
    """
    candidates = []
    key = kind.lower()
    for node in dmx_channel.iter():
        tag = _strip_ns(node.tag)
        if tag not in ("ChannelFunction", "LogicalChannel"):
            continue
        attr_text = " ".join(filter(None, [_xml_attr(node, "Attribute", ""), _xml_attr(node, "Name", "")])).lower()
        if key not in attr_text:
            continue
        if key == "pan" and "tilt" in attr_text:
            continue
        physical_from = _parse_gdtf_physical(_xml_attr(node, "PhysicalFrom", ""))
        physical_to = _parse_gdtf_physical(_xml_attr(node, "PhysicalTo", ""))
        if physical_from is None or physical_to is None:
            continue
        span = abs(float(physical_to) - float(physical_from))
        if span < 1.0:
            continue
        r = _gdtf_range_from_node(node) or (0, 255)
        candidates.append((abs(r[1] - r[0]), float(physical_from), float(physical_to), span))
    if not candidates:
        return None
    _dmx_span, a, b, span = max(candidates, key=lambda item: item[0])
    lo, hi = min(a, b), max(a, b)
    if lo >= 0 and hi > 180:
        return -span / 2.0, span / 2.0
    return lo, hi


def _gdtf_ranges_for_channel(dmx_channel):
    ranges = []
    for node in dmx_channel.iter():
        if node is dmx_channel:
            continue
        tag = _strip_ns(node.tag)
        if tag not in ("ChannelFunction", "ChannelSet"):
            continue
        r = _gdtf_range_from_node(node)
        if not r:
            continue
        name = " ".join(filter(None, [
            _xml_attr(node, "Name", ""),
            _xml_attr(node, "Attribute", ""),
            _xml_attr(node, "PhysicalFrom", ""),
            _xml_attr(node, "PhysicalTo", ""),
        ])).lower()
        ranges.append((r[0], r[1], name, tag))
    return sorted(ranges, key=lambda item: (item[0], item[1]))


def _derive_shutter_open_value(dmx_channel):
    bad = ("closed", "close", "strobe", "random", "pulse", "effect", "macro", "reset", "lamp", "strike", "douse")
    candidates = []
    for start, end, name, _tag in _gdtf_ranges_for_channel(dmx_channel):
        if "open" in name and not any(word in name for word in bad):
            candidates.append((start, end))
    if candidates:
        start, end = candidates[0]
        return int(round((start + end) / 2))
    for start, end, name, _tag in _gdtf_ranges_for_channel(dmx_channel):
        if "open" in name:
            return int(round((start + end) / 2))
    return None


def _derive_iris_100_value(dmx_channel):
    ranges = _gdtf_ranges_for_channel(dmx_channel)
    if not ranges:
        return None
    effect_words = ("effect", "macro", "pulse", "random", "strobe", "shake", "pattern", "animation")
    useful = []
    effect_starts = []
    for start, end, name, _tag in ranges:
        is_effect = any(word in name for word in effect_words)
        if is_effect:
            effect_starts.append(start)
        else:
            useful.append((start, end, name))
    if effect_starts:
        first_effect = min(effect_starts)
        before_effect = [(s, e, n) for s, e, n in useful if s < first_effect]
        if before_effect:
            return int(clamp(max(e for _s, e, _n in before_effect), 0, 255))
        return int(clamp(first_effect - 1, 0, 255))
    if useful:
        return int(clamp(max(e for _s, e, _n in useful), 0, 255))
    return None


def _derive_iris_physical_values(dmx_channel):
    """Return physical iris values at control 0% and 100% when available.

    GDTF iris functions often use PhysicalFrom=1 and PhysicalTo=0 for full
    open to full closed. Prefer the widest non-effect iris function.
    """
    candidates = []
    effect_words = ("effect", "macro", "pulse", "random", "strobe", "shake", "pattern", "animation")
    for node in dmx_channel.iter():
        tag = _strip_ns(node.tag)
        if tag not in ("ChannelFunction", "LogicalChannel"):
            continue
        attr_text = " ".join(filter(None, [_xml_attr(node, "Attribute", ""), _xml_attr(node, "Name", "")])).lower()
        if "iris" not in attr_text:
            continue
        if any(word in attr_text for word in effect_words):
            continue
        physical_from = _parse_gdtf_physical(_xml_attr(node, "PhysicalFrom", ""))
        physical_to = _parse_gdtf_physical(_xml_attr(node, "PhysicalTo", ""))
        if physical_from is None or physical_to is None:
            continue
        r = _gdtf_range_from_node(node) or (0, 255)
        candidates.append((abs(r[1] - r[0]), float(physical_from), float(physical_to)))
    if not candidates:
        return None
    _span, p0, p100 = max(candidates, key=lambda item: item[0])
    return p0, p100


def import_gdtf_channel_mapping(path, start_address=1, preferred_mode=None):
    """Best-effort GDTF DMX attribute extraction for one selected mode.

    Returns (mapping, mode_names, selected_mode_name). Mapping values are
    absolute one-based DMX slots, except shutter_open and iris_100_dmx
    which are DMX values for those attributes. Complex GDTF files may
    still need manual checking against the fixture manual.
    """
    root = _read_gdtf_description(path)
    modes = [node for node in root.iter() if _strip_ns(node.tag) == "DMXMode"]
    mode_names = [_xml_attr(m, "Name", f"Mode {i + 1}") for i, m in enumerate(modes)]
    if not modes:
        raise ValueError("No DMXMode entries found in the GDTF file")
    mode = modes[0]
    if preferred_mode:
        for candidate in modes:
            if _xml_attr(candidate, "Name", "").lower() == preferred_mode.lower():
                mode = candidate
                break
    selected_mode = _xml_attr(mode, "Name", mode_names[0] if mode_names else "default")

    def classify(attr):
        a = attr.lower().replace("_", "").replace("-", "").replace(" ", "")
        if "pan" in a and "tilt" not in a:
            return "pan"
        if "tilt" in a:
            return "tilt"
        if "dimmer" in a or "intensity" in a:
            return "dimmer"
        if "shutter" in a or "strobe" in a:
            return "shutter"
        if "zoom" in a:
            return "zoom"
        if "iris" in a:
            return "iris"
        if "focus" in a:
            return "focus"
        return None

    found = {}
    kind_channels = {}
    for dmx_channel in mode.iter():
        if _strip_ns(dmx_channel.tag) != "DMXChannel":
            continue
        offsets = _parse_gdtf_offsets(_xml_attr(dmx_channel, "Offset", ""))
        if not offsets:
            continue
        attr_names = [_xml_attr(dmx_channel, "Attribute", "")]
        for child in dmx_channel.iter():
            if child is dmx_channel:
                continue
            attr_names.append(_xml_attr(child, "Attribute", ""))
            attr_names.append(_xml_attr(child, "Name", ""))
        kind = None
        for name in attr_names:
            kind = classify(name)
            if kind:
                break
        if not kind or kind in kind_channels:
            continue
        kind_channels[kind] = dmx_channel
        abs_offsets = [int(start_address) + off - 1 for off in offsets]
        if kind == "pan":
            found["pan_coarse"] = abs_offsets[0]
            if len(abs_offsets) > 1:
                found["pan_fine"] = abs_offsets[1]
            pan_limits = _derive_pan_tilt_limits(dmx_channel, "pan")
            if pan_limits:
                found["pan_min"] = round(float(pan_limits[0]), 4)
                found["pan_max"] = round(float(pan_limits[1]), 4)
        elif kind == "tilt":
            found["tilt_coarse"] = abs_offsets[0]
            if len(abs_offsets) > 1:
                found["tilt_fine"] = abs_offsets[1]
            tilt_limits = _derive_pan_tilt_limits(dmx_channel, "tilt")
            if tilt_limits:
                found["tilt_min"] = round(float(tilt_limits[0]), 4)
                found["tilt_max"] = round(float(tilt_limits[1]), 4)
        elif kind == "dimmer":
            found["dimmer"] = abs_offsets[0]
            if len(abs_offsets) > 1:
                found["dimmer_fine"] = abs_offsets[1]
        elif kind == "shutter":
            found["shutter"] = abs_offsets[0]
        elif kind == "zoom":
            found["zoom"] = abs_offsets[0]
            if len(abs_offsets) > 1:
                found["zoom_fine"] = abs_offsets[1]
            zoom_angles = _derive_zoom_angles(dmx_channel)
            if zoom_angles:
                found["zoom_angle_at_0"] = round(float(zoom_angles[0]), 4)
                found["zoom_angle_at_100"] = round(float(zoom_angles[1]), 4)
        elif kind == "iris":
            found["iris"] = abs_offsets[0]
            iris_physical = _derive_iris_physical_values(dmx_channel)
            if iris_physical:
                found["iris_physical_at_0"] = round(float(iris_physical[0]), 4)
                found["iris_physical_at_100"] = round(float(iris_physical[1]), 4)
        elif kind == "focus":
            found["focus"] = abs_offsets[0]
            if len(abs_offsets) > 1:
                found["focus_fine"] = abs_offsets[1]

    if "shutter" in kind_channels:
        open_value = _derive_shutter_open_value(kind_channels["shutter"])
        if open_value is not None:
            found["shutter_open"] = int(clamp(open_value, 0, 255))
    if "iris" in kind_channels:
        iris_cap = _derive_iris_100_value(kind_channels["iris"])
        if iris_cap is not None:
            found["iris_100_dmx"] = int(clamp(iris_cap, 0, 255))

    if not found:
        raise ValueError("No usable pan/tilt/dimmer/beam channels were found in that GDTF mode")
    return found, mode_names, selected_mode
