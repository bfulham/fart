# FART

**Fixture Aiming and Remote Tracking**

Version 1.0.2 is a cleaned-up production baseline with the same tested functionality as the 0.x prototypes, a tidier UI layout, safer startup checks, improved documentation, and a clean GitHub repository structure.

[![Build Windows EXE](https://github.com/bfulham/fart/actions/workflows/build-windows.yml/badge.svg)](https://github.com/bfulham/fart/actions/workflows/build-windows.yml)
[![Latest release](https://img.shields.io/github/v/release/bfulham/fart?include_prereleases)](https://github.com/bfulham/fart/releases/latest)
[![MIT License](https://img.shields.io/github/license/bfulham/fart)](LICENSE)

FART is a Windows GUI application that receives live marker positions from OpenFollow over PosiStageNet, calculates the exact line of sight from one or more moving fixtures to independently selected PSN markers, and outputs 16-bit pan/tilt DMX.

It supports:

- OpenFollow PSN position input with automatic tracker discovery
- Per-light PSN marker selection, allowing different lights to follow different markers
- Independent fixture position, calibration, limits, channel mapping, and intensity scaling
- Manual, OSC, or Art-Net intensity input
- ENTTEC Open DMX USB, Art-Net, and sACN output
- 8-bit or 16-bit dimmer mapping
- Live shared zoom, iris, and focus controls with optional 16-bit zoom/focus output
- Calibration wizard zoom and iris controls for small-beam aiming
- Coarse and fine pan/tilt calibration controls
- Multi-fixture calibration against the same set of known points
- Tracking-loss blackout and explicit dimmer arming
- Multi-light overview table and lightweight interactive 3D preview
- Configuration import/export through JSON

> **Safety warning:** FART is experimental software, not a safety-rated tracking or motion-control system. Test with shutters closed or lamps disabled, use conservative movement limits, and keep an operator able to remove DMX or power immediately. Never use it where unexpected movement or light output could injure people.


### Fixture calibration wizard

Calibration now starts with DMX setup. FART asks for pan, tilt, dimmer, and optional shutter channels before the aiming faders are shown, because the calibration faders directly drive the selected fixture.


When adding a fixture, FART now asks whether to set it up manually or calibrate it. Manual setup is the existing workflow: type the fixture XYZ position, zero bearings, directions, trims, limits, and DMX channels yourself.

Calibration mode is intended for users who do not know the fixture's exact installed position. It opens a wizard with pan, tilt, dimmer, zoom and iris controls. Use the zoom and iris controls to make a small beam, then use the pan/tilt faders plus the fine nudge buttons to aim accurately at several known stage points, such as `(5, 0, 0)`, `(-5, 0, 0)`, `(0, 0, 0)`, upstage/downstage points, and a raised point. Capture the pan/tilt reading for each point, then solve. FART estimates the fixture optical-centre XYZ plus pan-zero bearing and tilt-zero elevation.

Multiple fixtures can be calibrated together: select several lights in the Lights tab, open calibration, choose one known point, aim every selected fixture at that same point, capture, then repeat. Solve and apply will calculate each selected light independently from the shared point list.

The old "set current bearing/elevation as zero" buttons have been removed because they were easy to misunderstand.

## Recommended workflow

1. Configure output first: Art-Net, sACN, or Open DMX.
2. Add or import a fixture profile and confirm DMX channels.
3. Calibrate manually or with the multi-fixture calibration wizard.
4. Auto-detect PSN trackers.
5. Assign markers per light.
6. Test with dimmers locked, then arm output only when movement is correct.

## Quick start

### Use the standalone Windows build

Download `FART-Windows-x64.zip` from the [latest release](https://github.com/bfulham/fart/releases/latest), extract `FART.exe`, then run it. Windows SmartScreen may warn because community builds are not code-signed.

### Run from source

Install Python 3.10 or newer, then either double-click `run_source.bat` or run:

```powershell
py -3 -m pip install -r requirements.txt
py -3 fart.py
```

### Build a single-file EXE

Double-click:

```text
build_windows_exe.bat
```

The resulting executable is:

```text
dist\FART.exe
```

## OpenFollow / PSN

Typical OpenFollow PSN settings are:

| Setting | Value |
|---|---:|
| Multicast group | `236.10.10.10` |
| UDP port | `56565` |
| Interface | `0.0.0.0`, or the PC's IPv4 address on the OpenFollow network |

Click **Auto-detect PSN trackers** to populate the default tracker selector and each light's marker selector. The default is used for newly added lights; every light can then be assigned independently. During operation, the PSN status counters should continuously increase.

FART uses PSN for XYZ position only. Intensity is selected independently.

## Fader modes

### Manual

The on-screen 0–100% fader controls all enabled fixtures, with each fixture's intensity scale applied afterward.

### OSC

Configure a UDP port, OSC address, zero-based argument index, and input range. For OpenFollow's common message:

```text
/openfollow/1/xyzf x y z markerfader
```

use argument index `3`, minimum `0`, and maximum `1`. Wildcard OSC addresses such as `/openfollow/*/xyzf` are supported by `python-osc`.

### Art-Net input

Choose an Art-Net universe and one 8-bit DMX channel. Values `0–255` map to `0–100%`. Avoid using the same broadcast universe for fader input and fixture output.

## Output modes

### ENTTEC Open DMX USB

Select **Open DMX** and choose the FTDI virtual COM port. The Open DMX is unbuffered, so Windows must generate the DMX break and all slots continuously. Art-Net, sACN, or a buffered interface is preferable for critical use.

### Art-Net

Art-Net universe numbering starts at `0`. Unicast to the receiving node or visualiser where possible.

### sACN

sACN uses multicast and universe numbering starts at `1`. Valid universes are `1–63999`.

## Adding lights

Every enabled fixture chooses its own PSN marker and calculates its own aim from its configured optical centre. Several lights can share one marker, or different groups can follow different markers. For each light configure:

- PSN marker ID
- Optical-centre/pan-tilt-pivot X, Y, and Z in OpenFollow coordinates
- World bearing represented by physical pan zero
- World elevation represented by physical tilt zero
- Pan/tilt direction and trim
- Mechanical/personality angle limits
- Absolute DMX channels within the output universe
- Shutter-open value and optional 16-bit dimmer fine channel
- Optional zoom, iris, and focus channels, including fine channels and per-light direction reversal

Channel fields are **absolute DMX slots**, not fixture offsets. For a fixture starting at channel 101, an attribute at fixture offset 18 is absolute channel `118`.

FART blocks startup if enabled fixtures overlap on any configured DMX channel.


## Zoom, iris, and focus

The **Overview** tab includes live 0–100% controls for zoom, iris, and focus. These normalized values are sent to every enabled light that has the corresponding channel configured. Each light can independently enable or omit the attribute and reverse its direction when the fixture personality runs opposite to the UI.

- Zoom supports an optional fine channel for 16-bit output.
- Iris is 8-bit.
- Focus supports an optional fine channel for 16-bit output.
- A channel value of `0` disables that attribute for the light.

For the MAC Quantum Profile Extended mode at address `1.182`, for example, use iris `194`, zoom `195/196`, and focus `197/198`.

## Multi-light overview and 3D preview

The **Overview** tab includes a live row for every enabled light, showing its assigned marker, marker XYZ, calculated pan/tilt, distance, and tracking state.

The **3D preview** is intentionally lightweight and uses only Tkinter. It displays fixture positions, assigned PSN markers, and the calculated beam line between them. Drag with the left mouse button to orbit, use the mouse wheel to zoom, and use **Reset view** to return to the default camera. It is a diagnostic reference, not a photometric or fixture-body simulation.

## Coordinate convention

FART assumes:

- `+X`: stage right
- `+Y`: away from the audience / upstage
- `+Z`: upward
- Bearing `0°`: `+Y`
- Bearing `+90°`: `+X`
- Elevation `0°`: horizontal

The aim vector is always calculated as:

```text
marker position - fixture optical-centre position
```

Fixture calibration then maps that world-space bearing and elevation into its physical pan and tilt angles.

See [docs/CALIBRATION.md](docs/CALIBRATION.md) before connecting a real fixture.

## grandMA3 visualiser testing

Use an otherwise unused Art-Net universe for FART and map it to a dedicated MA local universe. Patch the real fixture personality at the exact matching start address. Put the PSN-controlled MArker fixture on a different MA universe so incoming Art-Net zeroes do not force it to `0,0,0`.

See [docs/GRANDMA3_TESTING.md](docs/GRANDMA3_TESTING.md) for a complete test layout.

## Configuration files

FART stores its local settings at:

```text
%APPDATA%\FART.json
```

When first launched after upgrading, it automatically imports `%APPDATA%\OpenFollowFollowspot.json` if present and leaves the old file untouched.

An example four-fixture MAC Quantum Profile configuration is included at [examples/four_mac_quantum_profiles.json](examples/four_mac_quantum_profiles.json). Replace the illustrative XYZ positions and calibrate every fixture before use.

## Development

Run the tests:

```powershell
py -3 -m unittest discover -s tests -v
```

The included [GitHub Actions workflow](https://github.com/bfulham/fart/actions/workflows/build-windows.yml) tests the application, builds `FART.exe` on Windows, and uploads a ZIP artifact. Pushing a tag beginning with `v` creates or updates a GitHub release automatically.

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). Bugs and feature requests can be submitted through [GitHub Issues](https://github.com/bfulham/fart/issues).

## Licence

FART is released under the [MIT License](LICENSE).


### GDTF channel import

FART can import a DMX channel map from a `.gdtf` fixture file from either the main light editor or the DMX setup step before calibration. When a GDTF contains multiple DMX modes, FART asks which mode to import so it can match the mode patched in MA3 or on the real fixture.

The importer fills common attributes where they are present in the selected mode: pan, tilt, dimmer, shutter, zoom, iris, focus, and supported fine channels. It also attempts to derive practical defaults from the GDTF channel ranges, including a shutter-open value and an iris 100% cap so the iris slider does not run into pulse, pattern, or effect ranges.

Use GDTF import as a setup helper only: always verify the imported channels and values against the fixture manual before moving real fixtures. Complex GDTF personalities may still need manual correction.
