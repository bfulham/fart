# FART

**Fixture Aiming and Remote Tracking**

[![Build Windows and macOS](https://github.com/bfulham/fart/actions/workflows/build-windows.yml/badge.svg)](https://github.com/bfulham/fart/actions/workflows/build-windows.yml)
[![Latest release](https://img.shields.io/github/v/release/bfulham/fart?include_prereleases)](https://github.com/bfulham/fart/releases/latest)
[![MIT License](https://img.shields.io/github/license/bfulham/fart)](LICENSE)

FART is a GUI application (Windows and macOS) that receives live marker positions from OpenFollow over PosiStageNet (PSN), calculates the exact line of sight from one or more moving fixtures to independently selected PSN markers, and outputs 16-bit pan/tilt DMX to aim them. It's meant to sit in the signal path for a lighting console that doesn't do 3D position tracking natively — the console still owns the show; FART owns the aiming math for whichever fixtures are following a tracked performer. FART is developed primarily on Windows; the macOS build is newer and less battle-tested.

See [CHANGELOG.md](CHANGELOG.md) for what's new in each release.

> **Safety warning:** FART is experimental software, not a safety-rated tracking or motion-control system. Test with shutters closed or lamps disabled, use conservative movement limits, and keep an operator able to remove DMX or power immediately. Never use it where unexpected movement or light output could injure people.

## Contents

- [What FART does](#what-fart-does)
- [Quick start](#quick-start)
- [Recommended workflow](#recommended-workflow)
- [PSN input (OpenFollow)](#psn-input-openfollow)
- [Coordinate convention](#coordinate-convention)
- [Adding and patching lights](#adding-and-patching-lights)
- [Calibration](#calibration)
- [Intensity input (fader modes)](#intensity-input-fader-modes)
- [DMX output modes](#dmx-output-modes)
- [Zoom, iris, and focus](#zoom-iris-and-focus)
- [Live console control (optional)](#live-console-control-optional)
- [Safety behaviour reference](#safety-behaviour-reference)
- [Multi-light overview and 3D preview](#multi-light-overview-and-3d-preview)
- [Configuration files](#configuration-files)
- [Testing against grandMA3 onPC](#testing-against-grandma3-onpc)
- [Development](#development)
- [Contributing and support](#contributing-and-support)
- [Licence](#licence)

## What FART does

For each enabled light, FART:

1. Reads that light's assigned PSN marker's XYZ position (with optional smoothing and lead/lag prediction).
2. Calculates the bearing and elevation from that fixture's configured optical centre to the marker.
3. Converts that into the fixture's own pan/tilt angles, using its calibrated zero points, direction, trim, and mechanical limits.
4. Outputs 16-bit pan/tilt DMX, plus dimmer, shutter, zoom, iris, and focus if configured.

Several lights can follow the same marker, or different lights can follow different markers, all independently. Intensity comes from a manual fader, OSC, or an Art-Net channel — separately from the PSN position data.

## Quick start

### Use a standalone build

Download the archive for your platform from the [latest release](https://github.com/bfulham/fart/releases/latest):

- **Windows**: `FART-Windows-x64.zip` → extract `FART.exe` and run it. Windows SmartScreen may warn because community builds are not code-signed.
- **macOS (Apple Silicon)**: `FART-macOS-arm64.zip` → extract `FART.app` and open it. Gatekeeper will refuse to open an unsigned app from an unidentified developer the first time — right-click (or Control-click) `FART.app`, choose **Open**, then confirm in the dialog that appears; this is only needed once. Intel Macs are not currently built or tested.

### Run from source

Install Python 3.10 or newer, then:

**Windows** — double-click `run_source.bat`, or run:

```powershell
py -3 -m pip install -r requirements.txt
py -3 fart.py
```

**macOS** — double-click `run_source_macos.command`, or run:

```bash
python3 -m pip install -r requirements.txt
python3 fart.py
```

Use a Python that includes Tk — the official [python.org macOS installer](https://www.python.org/downloads/macos/) does; some other distributions (for example a plain `pyenv` build) do not, and will fail with `No module named '_tkinter'`.

### Build a standalone app yourself

**Windows** — double-click `build_windows_exe.bat`. The resulting executable is `dist\FART.exe`.

**macOS** — run `./build_macos_app.sh` (same Tk requirement as above). The resulting app is `dist/FART.app`.

## Recommended workflow

1. Configure output first: Art-Net, sACN, or Open DMX (Setup: I/O tab).
2. Add a fixture and either enter its DMX setup manually or use the calibration wizard.
3. Calibrate manually or with the multi-fixture calibration wizard (see [Calibration](#calibration)).
4. Auto-detect PSN trackers and assign a marker to each light.
5. Optionally set up [live console control](#live-console-control-optional) for lights the console should be able to reclaim mid-show.
6. Start FART with dimmers locked, confirm tracking and aim look correct, then arm output.

## PSN input (OpenFollow)

Typical OpenFollow PSN settings, set on the Operator tab:

| Setting | Value |
|---|---:|
| Multicast group | `236.10.10.10` |
| UDP port | `56565` |
| Interface | `0.0.0.0`, or the PC's IPv4 address on the OpenFollow network |

Click **Auto-detect PSN trackers** to populate the default tracker selector and each light's marker selector. The default marker is used for newly added lights; every light can then be assigned independently. During operation, the PSN status counters on the Operator tab should continuously increase.

FART uses PSN for XYZ position only — intensity always comes from a separate [fader source](#intensity-input-fader-modes).

## Coordinate convention

FART assumes:

- `+X`: house right — the audience's/FOH right, facing the stage. This is the opposite side from traditional theatrical "stage right," which is defined from the performer's perspective.
- `+Y`: away from the audience / upstage
- `+Z`: upward
- Bearing `0°`: `+Y`
- Bearing `+90°`: `+X`
- Elevation `0°`: horizontal

The aim vector is always calculated as `marker position - fixture optical-centre position`. Fixture calibration then maps that world-space bearing and elevation into the fixture's own physical pan and tilt angles.

## Adding and patching lights

Add a light on the **Setup: Lights** tab. Each light configures, across its **Geometry and calibration** and **DMX channels** sub-tabs:

- PSN marker ID and output universe
- Optical-centre/pan-tilt-pivot X, Y, and Z, in the [coordinate convention](#coordinate-convention) above
- World bearing represented by physical pan zero, and world elevation represented by physical tilt zero
- Pan/tilt direction and trim
- Mechanical/personality angle limits
- Absolute DMX channels for pan, tilt, dimmer, shutter, zoom, iris, and focus (including fine channels), and per-channel direction reversal
- Intensity scale, and the [limit-blackout](#safety-behaviour-reference) and [live console control](#live-console-control-optional) options described below

Channel fields are **absolute DMX slots**, not fixture offsets: for a fixture starting at channel 101, an attribute at fixture offset 18 is absolute channel `118`. FART blocks startup if enabled fixtures overlap on any configured DMX channel within the same universe; the same channel numbers can be reused safely on different universes.

### GDTF import

Click **Import channels from GDTF…** (in the DMX channels sub-tab, or during calibration DMX setup) to load a channel map from a `.gdtf` fixture file. When a GDTF contains multiple DMX modes, FART asks which mode to import so it can match the mode patched in the console or visualiser.

The importer fills common attributes present in the selected mode — pan, tilt, dimmer, shutter, zoom, iris, focus, and their fine channels — and derives practical defaults from the GDTF channel ranges: a shutter-open value, an iris 100% cap (so the iris control doesn't run into pulse/pattern/effect ranges), zoom beam angles from `PhysicalFrom`/`PhysicalTo`, and iris physical aperture values such as `1` open to `0` closed when present.

GDTF import is a setup helper, not a source of truth: always verify the imported channels and values against the fixture's manual before moving a real fixture. Complex GDTF personalities may still need manual correction.

## Calibration

Calibration estimates a fixture's real-world optical-centre position and its pan-zero/tilt-zero angles by aiming it at several known points and solving for the geometry that fits. See **[docs/CALIBRATION.md](docs/CALIBRATION.md)** for the full step-by-step workflow, including the required DMX setup, capture workflow, and how to read the solver's fit-quality warnings.

In short: from the Lights tab, select one or more fixtures and click **Calibrate selected light**, or **Open fixture calibration wizard** from the Setup: Calibration tab. FART asks for pan/tilt/dimmer (and optional shutter) DMX channels first, since the wizard's faders drive the real fixture directly. Aim at least four known points (five or six is better, spread across house left/right, upstage/downstage, centre, and one raised point), capturing each with **Capture point for all fixtures**, then click **Solve and apply all**. Multiple selected fixtures are solved independently against the same shared point list, and the multi-fixture solve runs in the background so the wizard (including its own blackout button) stays responsive.

## Intensity input (fader modes)

Set the fader source on the Operator tab.

### Manual

The on-screen 0–100% fader controls all enabled fixtures, with each fixture's own intensity scale applied afterward.

### OSC

Configure a UDP port, OSC address, zero-based argument index, and input range. For OpenFollow's common message:

```text
/openfollow/1/xyzf x y z markerfader
```

use argument index `3`, minimum `0`, and maximum `1`. Wildcard OSC addresses such as `/openfollow/*/xyzf` are supported by `python-osc`.

### Art-Net input

Choose an Art-Net universe and one 8-bit DMX channel. Values `0–255` map to `0–100%`. Avoid using the same broadcast universe for this fader input and for fixture output.

## DMX output modes

Set the output mode on the Setup: I/O tab.

### ENTTEC Open DMX USB

Select **Open DMX** and choose the FTDI virtual serial port — on Windows a `COMx` port, on macOS a `/dev/cu.usbserial-*`-style device. For multiple adapters, enter a mapping such as `COM3=0, COM4=1` (or the macOS equivalent device paths) in **Open DMX adapters**. Open DMX adapters have no universe concept of their own, so FART maps each USB adapter to a software universe and sends that universe's 512-channel frame to that adapter; leave the mapping blank to use the single selected serial port for the default universe. Open DMX is unbuffered, so the OS must generate the DMX break and all slots continuously — this has only been verified on Windows so far. Prefer Art-Net, sACN, or a buffered interface for anything critical, especially on macOS until Open DMX timing is confirmed there.

### Art-Net

Art-Net universe numbering starts at `0`. Unicast to the receiving node or visualiser where possible. FART sends one ArtDMX stream for every enabled fixture universe.

### sACN

sACN uses multicast, and universe numbering starts at `1` (valid range `1–63999`). FART activates and transmits every enabled fixture universe.

## Zoom, iris, and focus

The Operator tab includes live controls for zoom, iris, and focus, each with a channel value of `0` disabling that attribute for a given light. Zoom and focus support an optional fine channel for 16-bit output; iris is 8-bit only.

Zoom runs in one of two modes:

- **Manual** — the shared 0–100% zoom slider is sent to every enabled light with a zoom channel.
- **Auto beam size** — FART calculates a per-fixture zoom value so the beam is roughly the requested diameter at the marker. If zoom reaches its tightest beam and the fixture's GDTF/manual iris model provides physical aperture values, FART then closes the iris just enough to approximate the remaining size reduction. This mode is greyed out until at least one enabled fixture has zoom beam-angle data.

For example, for a MAC Quantum Profile in Extended mode patched at address `1.182`, use iris `194`, zoom `195/196`, and focus `197/198`.

## Live console control (optional)

FART can hand a fixture to a lighting console for direct control during a show, or let the console reassign which PSN marker a fixture follows — instead of that fixture always being fully computed by FART. This is the intended way to use FART alongside a console that doesn't do 3D tracking natively (for example Onyx): the console still owns the show, and can reclaim a tracked fixture whenever it needs to.

> This feature is unit-tested but has not been verified against a real console, real Art-Net network, or physical fixtures. Test thoroughly with shutters closed before relying on it in a show.

### Patch scheme

1. Dedicate one output universe to FART, and route the console's own output for that universe to FART instead of straight to the node — FART both receives it (Art-Net input, listening on UDP 6454) and re-sends it.
2. Patch the real fixture's own personality on that universe as usual.
3. Patch a small companion "FART control" fixture immediately after it, exposing just two channels:
   - **Mode** — below `128` is manual passthrough; `128` and above is auto-follow.
   - **Marker select** — `0` uses the fixture's configured default marker; a nonzero value is used directly as the PSN marker ID to follow.
4. On the fixture's DMX channels sub-tab, under **Live console control**, set the **Mode channel** and **Marker-select channel** fields to that control fixture's two addresses. Leaving the mode channel at `0` disables this entirely — the fixture then behaves exactly as it does without this feature.

### Behaviour by mode

- **Manual** (`< 128`): full passthrough. FART does not touch that fixture's DMX at all — pan, tilt, dimmer, colour, gobo, and any effects pass straight through from the console's own frame.
- **Auto-follow** (`>= 128`): FART computes pan/tilt as usual (and zoom/iris, unless [Auto beam size](#zoom-iris-and-focus) applies to that fixture), while dimmer and every other channel keep passing through from the console. The companion control fixture needs no intensity channel of its own — intensity always comes from the real fixture's own dimmer channel.

Reassigning a fixture's marker live, or returning it from manual to auto-follow, always forces a fresh snap to the new target and a brief configurable blackout (**Marker-change blackout**, default 0.5&nbsp;s) instead of sweeping across the space while lit.

## Safety behaviour reference

FART's dimmer output is governed by several independent safety conditions, evaluated per fixture every cycle. Any one of them forces dimmer to `0` (and shutter closed, if configured) regardless of the others:

| Condition | Configured | Default behaviour |
|---|---|---|
| Output not armed | **Arm all light dimmers** checkbox, Operator tab | Blackout |
| PSN tracking lost (no fresh position within the tracking timeout) | Per-fixture **On tracking loss** | Blackout — set to *Keep current intensity* to leave dimmer alone while pan/tilt freezes at the last known position |
| Calculated pan/tilt exceeds a fixture's mechanical limits | Per-fixture **Blackout on limit**, with optional zoom/iris-to-100% | Off by default (opt in per fixture) |
| Console signal lost, for fixtures using [live console control](#live-console-control-optional) | Per-fixture **On console signal loss** | Blackout — *Keep tracking, force dimmer off* also keeps aiming from live PSN so the fixture is already correct once the console returns; *Keep tracking, hold last dimmer* is an explicit opt-in that can leave a light lit indefinitely if the console signal never returns |
| Live marker reassignment or mode change | Per-fixture **Marker-change blackout** (seconds) | Forces a brief blackout while the fixture swings to its new target |

Stopping FART, or its output loop erroring, always sends one blank (all-zero) frame per universe before closing the output connection.

## Multi-light overview and 3D preview

The Operator tab's **Light overview** sub-tab shows a live row for every enabled light: its assigned marker, marker XYZ, calculated pan/tilt, distance, and tracking state.

The **3D preview** sub-tab is intentionally lightweight and uses only Tkinter. It displays fixture positions, assigned PSN markers, and the calculated beam line between them. Drag with the left mouse button to orbit, use the mouse wheel to zoom, and use **Reset view** to return to the default camera. It is a diagnostic reference, not a photometric or fixture-body simulation.

## Configuration files

FART stores its local settings at `%APPDATA%\FART.json`. When first launched after upgrading from the old "OpenFollow Followspot" name, it automatically imports `%APPDATA%\OpenFollowFollowspot.json` if present and leaves the old file untouched.

An example four-fixture MAC Quantum Profile configuration is included at [examples/four_mac_quantum_profiles.json](examples/four_mac_quantum_profiles.json). Replace the illustrative XYZ positions and calibrate every fixture before use.

## Testing against grandMA3 onPC

Use an otherwise unused Art-Net universe for FART and map it to a dedicated MA local universe, with the real fixture personality patched at the exact matching start address. Put the PSN-tracked marker fixture on a different MA universe, so incoming Art-Net zeroes from FART don't force it to `0,0,0`.

See **[docs/GRANDMA3_TESTING.md](docs/GRANDMA3_TESTING.md)** for a complete example layout and troubleshooting steps.

## Development

Run the tests (`py -3` on Windows, `python3` on macOS):

```powershell
py -3 -m unittest discover -s tests -v
```

The included [GitHub Actions workflow](https://github.com/bfulham/fart/actions/workflows/build-windows.yml) tests the application and builds both `FART.exe` (Windows) and `FART.app` (macOS, Apple Silicon) as workflow artifacts on every push and pull request. Pushing a tag beginning with `v` also creates or updates a GitHub release with both builds attached (see [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md) for the full release process).

### Testing without OpenFollow or a fixture

[tools/fart_test_tool.py](tools/fart_test_tool.py) is a standalone script that sends synthetic OpenFollow-style PSN positions and decodes FART's own Art-Net output, so tracking (and [live console control](#live-console-control-optional)) can be exercised end to end without OpenFollow, a real console, or a physical fixture. See [tools/README.md](tools/README.md).

## Contributing and support

Contributions are welcome — see **[CONTRIBUTING.md](CONTRIBUTING.md)** for development setup and pull request expectations. Bugs and feature requests go through [GitHub Issues](https://github.com/bfulham/fart/issues); if you believe you've found a safety-relevant issue (unexpected movement or intensity), see **[SECURITY.md](SECURITY.md)** first.

## Licence

FART is released under the [MIT License](LICENSE).
