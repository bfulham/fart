# FART

**Fixture Aiming and Remote Tracking**

[![Build Windows EXE](https://github.com/bfulham/fart/actions/workflows/build-windows.yml/badge.svg)](https://github.com/bfulham/fart/actions/workflows/build-windows.yml)
[![Latest release](https://img.shields.io/github/v/release/bfulham/fart?include_prereleases)](https://github.com/bfulham/fart/releases/latest)
[![MIT License](https://img.shields.io/github/license/bfulham/fart)](LICENSE)

FART is a Windows GUI application that receives live marker positions from OpenFollow over PosiStageNet, calculates the exact line of sight from one or more moving fixtures to the selected marker, and outputs 16-bit pan/tilt DMX.

It supports:

- OpenFollow PSN position input with automatic tracker discovery
- Multiple moving lights aimed at one marker
- Independent fixture position, calibration, limits, channel mapping, and intensity scaling
- Manual, OSC, or Art-Net intensity input
- ENTTEC Open DMX USB, Art-Net, and sACN output
- 8-bit or 16-bit dimmer mapping
- Tracking-loss blackout and explicit dimmer arming
- Configuration import/export through JSON

> **Safety warning:** FART is experimental software, not a safety-rated tracking or motion-control system. Test with shutters closed or lamps disabled, use conservative movement limits, and keep an operator able to remove DMX or power immediately. Never use it where unexpected movement or light output could injure people.

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

Click **Auto-detect PSN trackers** to populate the tracker selector. During operation, the PSN status counters should continuously increase, especially `selected` and `positions`.

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

Every enabled fixture uses the same selected PSN marker but calculates its own aim from its configured optical centre. For each light configure:

- Optical-centre/pan-tilt-pivot X, Y, and Z in OpenFollow coordinates
- World bearing represented by physical pan zero
- World elevation represented by physical tilt zero
- Pan/tilt direction and trim
- Mechanical/personality angle limits
- Absolute DMX channels within the output universe
- Shutter-open value and optional 16-bit dimmer fine channel

Channel fields are **absolute DMX slots**, not fixture offsets. For a fixture starting at channel 101, an attribute at fixture offset 18 is absolute channel `118`.

FART blocks startup if enabled fixtures overlap on any configured DMX channel.

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
