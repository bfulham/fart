# FART

**Fixture Aiming and Remote Tracking**

Version 1.5.0 adds optional live console control: a fixture can be handed to a lighting console (Art-Net) for manual passthrough or live marker reassignment mid-show, with independent, configurable dimmer behaviour for PSN loss vs. console-signal loss. It also fixes a worker-thread Tk-safety issue, a smoothing bug that could sweep a lit fixture across the space on tracking reacquisition, and a calibration-solve UI freeze.

[![Build Windows EXE](https://github.com/bfulham/fart/actions/workflows/build-windows.yml/badge.svg)](https://github.com/bfulham/fart/actions/workflows/build-windows.yml)
[![Latest release](https://img.shields.io/github/v/release/bfulham/fart?include_prereleases)](https://github.com/bfulham/fart/releases/latest)
[![MIT License](https://img.shields.io/github/license/bfulham/fart)](LICENSE)

FART is a Windows GUI application that receives live marker positions from OpenFollow over PosiStageNet, calculates the exact line of sight from one or more moving fixtures to independently selected PSN markers, and outputs 16-bit pan/tilt DMX.

It supports:

- OpenFollow PSN position input with automatic tracker discovery
- Per-light PSN marker selection, allowing different lights to follow different markers
- Independent fixture position, calibration, limits, channel mapping, and intensity scaling
- Manual, OSC, or Art-Net intensity input
- ENTTEC Open DMX USB, Art-Net, and sACN output, including multiple output universes
- 8-bit or 16-bit dimmer mapping
- Live shared zoom, iris, and focus controls with optional 16-bit zoom/focus output
- Calibration wizard zoom and iris controls for small-beam aiming
- Coarse and fine pan/tilt calibration controls
- Multi-fixture calibration against the same set of known points
- Tracking-loss blackout and explicit dimmer arming, with independent policies for PSN loss vs. console-signal loss
- Optional live console control per fixture (Art-Net), for handing a fixture to a lighting console for manual passthrough or reassigning its followed marker mid-show
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

Select **Open DMX** and choose the FTDI virtual COM port. For multiple adapters, enter a mapping such as `COM3=0, COM4=1` in **Open DMX adapters**. Open DMX adapters do not know about universes themselves, so FART maps each USB adapter to a software universe and sends that universe's 512-channel DMX frame to that adapter. Leave the mapping blank to use the single selected serial port for the default universe. The Open DMX is unbuffered, so Windows must generate the DMX break and all slots continuously. Art-Net, sACN, or a buffered interface is preferable for critical use.

### Art-Net

Art-Net universe numbering starts at `0`. Unicast to the receiving node or visualiser where possible. FART sends one ArtDMX stream for each enabled fixture universe.

### sACN

sACN uses multicast and universe numbering starts at `1`. Valid universes are `1–63999`. FART activates and transmits every enabled fixture universe.

## Adding lights

Every enabled fixture chooses its own PSN marker and calculates its own aim from its configured optical centre. Several lights can share one marker, or different groups can follow different markers. For each light configure:

- PSN marker ID
- Output universe
- Optical-centre/pan-tilt-pivot X, Y, and Z in OpenFollow coordinates
- World bearing represented by physical pan zero
- World elevation represented by physical tilt zero
- Pan/tilt direction and trim
- Mechanical/personality angle limits
- Absolute DMX channels within that light's output universe
- Shutter-open value and optional 16-bit dimmer fine channel
- Optional zoom, iris, and focus channels, including fine channels and per-light direction reversal

Channel fields are **absolute DMX slots**, not fixture offsets. For a fixture starting at channel 101, an attribute at fixture offset 18 is absolute channel `118`.

FART blocks startup if enabled fixtures overlap on any configured DMX channel within the same universe. The same channel numbers can be reused on different universes.

## Live console control (optional)

FART is meant to sit in the signal path where a lighting console that doesn't do 3D tracking natively (for example Onyx) still owns the show. A fixture can be handed to a console for live control during a run, instead of always being fully computed by FART:

1. Dedicate one output universe to FART and route the console's own output for it to FART instead of straight to the node — FART both receives (Art-Net input, listening on UDP 6454) and re-sends that universe.
2. Patch the real fixture's own personality on that universe as usual.
3. Patch a small companion "FART control" fixture immediately after it, exposing just two channels: **Mode** (below 128 = manual passthrough, 128 and above = auto-follow) and **Marker select** (`0` = use the fixture's configured default marker; a nonzero value is used directly as the PSN marker ID to follow).
4. Set the fixture's **Mode channel** and **Marker-select channel** fields (Lights tab → DMX channels → Live console control) to that control fixture's two addresses. Leave the mode channel at `0` to disable this entirely — the fixture then behaves exactly as it does without this feature.

In **manual** mode FART does not touch that fixture's DMX at all: every channel — pan, tilt, dimmer, color, gobo, effects — passes straight through from the console's own frame. In **auto-follow** mode FART takes over pan/tilt (and zoom/iris, unless a per-fixture Auto beam size model applies) while dimmer keeps passing through from the console — the control fixture needs no intensity channel of its own, since intensity always comes from the real fixture's own dimmer.

Two independent policies decide what happens to dimmer when a fixture's own data source goes stale, since PSN loss and console-signal loss are different failures and one being fine doesn't mean the other is:

- **On tracking loss** — `Blackout` (default) forces dimmer to 0; `Keep current intensity` leaves dimmer alone while pan/tilt freezes at the last known position, same as always.
- **On console signal loss** (only relevant with a mode channel configured) — `Blackout` and `Keep tracking, force dimmer off` both force dimmer to 0, but the latter keeps computing pan/tilt from live PSN so the fixture is already aimed correctly once the console signal returns, instead of swinging into place while lit. `Keep tracking, hold last dimmer` leaves dimmer and every other console-driven channel exactly as they were in the last frame received — an explicit, opt-in risk (a light could stay lit indefinitely if the console signal never returns), not the default.

Reassigning a fixture's marker live, or returning it from manual to auto-follow, always forces a fresh snap to the new target and a brief configurable blackout (**Marker-change blackout**, default 0.5&nbsp;s) instead of sweeping across the space while lit.

This has been built and unit-tested but not verified against a real console or physical fixtures — test thoroughly with shutters closed before relying on it in a show, per the safety warning above.


## Zoom, iris, and focus

The **Operator** tab includes live controls for zoom, iris, and focus. Zoom can run in **Manual** mode, where the shared 0–100% zoom slider is sent to every enabled light with a zoom channel, or **Auto beam size** mode, where FART calculates a zoom value per fixture so the beam is roughly the requested diameter at the marker. If the zoom reaches its tightest beam and the GDTF/manual iris model provides physical aperture values, FART then closes the iris just enough to approximate the remaining size reduction. Auto mode is greyed out until at least one enabled fixture has zoom beam-angle data.

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

- `+X`: house right — the audience's/FOH right, facing the stage. This is the
  opposite side from traditional theatrical "stage right," which is defined
  from the performer's perspective.
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

The importer fills common attributes where they are present in the selected mode: pan, tilt, dimmer, shutter, zoom, iris, focus, and supported fine channels. It also attempts to derive practical defaults from the GDTF channel ranges, including a shutter-open value, an iris 100% cap so the iris slider does not run into pulse/pattern/effect ranges, zoom physical beam angles from GDTF `PhysicalFrom`/`PhysicalTo` data, and iris physical aperture values such as `1` open to `0` closed when present.

Use GDTF import as a setup helper only: always verify the imported channels and values against the fixture manual before moving real fixtures. Complex GDTF personalities may still need manual correction.
