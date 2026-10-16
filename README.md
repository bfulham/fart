# FART

**Fixture Aiming and Remote Tracking**

[![Build Windows and macOS](https://github.com/bfulham/fart/actions/workflows/build.yml/badge.svg)](https://github.com/bfulham/fart/actions/workflows/build.yml)
[![Latest release](https://img.shields.io/github/v/release/bfulham/fart?include_prereleases)](https://github.com/bfulham/fart/releases/latest)
[![MIT License](https://img.shields.io/github/license/bfulham/fart)](LICENSE)
[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/G5F8279CAL)

FART is a GUI application (Windows and macOS) that receives live marker positions from OpenFollow over PosiStageNet (PSN), calculates the exact line of sight from one or more moving fixtures to independently selected PSN markers, and outputs 16-bit pan/tilt DMX to aim them. It sits between your lighting console and the lights: the console still owns the show, and FART owns the aiming maths for whichever fixtures are following a tracked performer. It can take any part of a fixture back to the console, or hand the whole fixture over, while it runs.

FART is developed primarily on Windows; the macOS build is newer and less battle-tested. See [CHANGELOG.md](CHANGELOG.md) for what's new in each release.

> **Safety warning:** FART is experimental software, not a safety-rated tracking or motion-control system. Test with shutters closed or lamps disabled, use conservative movement limits, and keep an operator able to remove DMX or power immediately. Never use it where unexpected movement or light output could injure people.

## Contents

- [What FART does](#what-fart-does)
- [Quick start](#quick-start)
- [Recommended workflow](#recommended-workflow)
- [The Operator tab: dimmer and beam control](#the-operator-tab-dimmer-and-beam-control)
- [PSN input (OpenFollow)](#psn-input-openfollow)
- [Coordinate convention](#coordinate-convention)
- [Fixture types and fixtures](#fixture-types-and-fixtures)
- [Calibration](#calibration)
- [DMX in and DMX out](#dmx-in-and-dmx-out)
- [Console control: shadow patch and mode/marker](#console-control-shadow-patch-and-modemarker)
- [Safety behaviour reference](#safety-behaviour-reference)
- [Configuration files](#configuration-files)
- [Coming from v1](#coming-from-v1)
- [Testing against grandMA3 onPC](#testing-against-grandma3-onpc)
- [Development](#development)
- [Contributing and support](#contributing-and-support)
- [Licence](#licence)

## What FART does

For each enabled light, FART:

1. Reads that light's assigned PSN marker's XYZ position (with optional smoothing and lead/lag prediction).
2. Calculates the bearing and elevation from that fixture's configured optical centre to the marker.
3. Converts that into the fixture's own pan/tilt angles, using its calibrated zero points, direction, trim, and mechanical limits.
4. Outputs 16-bit pan/tilt DMX, plus dimmer, shutter, zoom, iris, and focus if the fixture has them.

Several lights can follow the same marker, or different lights can follow different markers, all independently. Dimmer and beam (zoom/iris/focus) come either from FART's own faders or straight from your console, depending on the [modes you pick](#the-operator-tab-dimmer-and-beam-control).

## Quick start

### Use a standalone build

Download the archive for your platform from the [latest release](https://github.com/bfulham/fart/releases/latest):

- **Windows**: `FART-Windows-x64.zip` → extract `FART.exe` and run it. Windows SmartScreen may warn because community builds are not code-signed.
- **macOS (Apple Silicon)**: `FART-macOS-arm64.zip` → extract `FART.app` and open it. Gatekeeper will refuse to open an unsigned app from an unidentified developer the first time — right-click (or Control-click) `FART.app`, choose **Open**, then confirm in the dialog; this is only needed once. Intel Macs are not currently built or tested.

### Run from source

Install Python 3.10 or newer, then:

**Windows** — double-click `run_source.bat`, or run:

```powershell
py -3 -m pip install -r requirements.txt
py -3 -m fart
```

**macOS** — double-click `run_source_macos.command`, or run:

```bash
python3 -m pip install -r requirements.txt
python3 -m fart
```

### Build a standalone app yourself

**Windows** — double-click `build_windows_exe.bat`. The resulting executable is `dist\FART.exe`.

**macOS** — run `./build_macos_app.sh`. The resulting app is `dist/FART.app`.

## Recommended workflow

1. **DMX Out** tab: choose Art-Net, sACN, or Open DMX and set where FART's output goes.
2. **Fixtures** tab: create a fixture type (import it from a GDTF file, or browse GDTF Share), then add a fixture of that type and patch its output universe and start address.
3. **PSN In** tab: start FART and watch the tracker table fill with the markers OpenFollow is sending; give each light its PSN marker ID.
4. **Calibration** tab: run the calibration wizard for each fixture (see [Calibration](#calibration)).
5. Optionally set up [console control](#console-control-shadow-patch-and-modemarker) for lights the console should be able to take back mid-show, and pick your dimmer and beam modes on the Operator tab.
6. **Operator** tab: click **START**, confirm tracking and aim look right with the dimmer fader still at 0, then bring the dimmer up.

## The Operator tab: dimmer and beam control

There is no separate arm step: **START** is the only gate, and the manual dimmer fader always starts at 0, so nothing lights until you raise it.

- **Use DMX in** — unticked, FART behaves exactly as if every fixture's DMX-in addresses were 0: FART-only control (auto mode, the configured marker, the faders). It is a live toggle and is saved with your settings.
- **Dimmer mode** — **Manual** drives each light's dimmer from the dimmer fader; **DMX in** passes each light's own dimmer channel through from the console.
- **Beam mode** — **Manual** uses the zoom, iris and focus faders; **Auto size** has FART calculate zoom (and iris, once zoom is as tight as it goes) to hit the requested spot diameter at the marker; **DMX passthrough** passes zoom, iris and focus through from the console.

Any fader that currently has no effect is greyed out, so what you can touch is always what is in control. A light with no shadow feed patched falls back to the faders for any DMX-in mode, and the overview table shows that in its Dimmer and Beam columns. Passing a dimmer through never overrides the safety blackouts below.

The Operator tab also shows PSN packet counters (they should keep increasing while OpenFollow is sending) and DMX-in status (each universe's packet rate and age, the quickest way to see a slow or dead console feed), a live overview row per light, and the log.

## PSN input (OpenFollow)

Typical OpenFollow PSN settings, on the **PSN In** tab:

| Setting | Value |
|---|---:|
| Multicast group | `236.10.10.10` |
| UDP port | `56565` |
| Interface | `0.0.0.0`, or the PC's IPv4 address on the OpenFollow network |

Once FART is started, the tab shows a live table of **every** tracker being received, whether or not any light uses it: position, speed (as sent, and as derived from successive positions), orientation, acceleration, target, status, update rate, age, and which fixtures follow it. A tracker's age turns red past the tracking timeout. **Clear table** forgets every tracker; live ones reappear on their next packet.

FART uses PSN for XYZ position only.

## Coordinate convention

FART assumes:

- `+X`: house right — the audience's/FOH right, facing the stage. This is the opposite side from traditional theatrical "stage right," which is defined from the performer's perspective.
- `+Y`: away from the audience / upstage
- `+Z`: upward
- Bearing `0°`: `+Y`
- Bearing `+90°`: `+X`
- Elevation `0°`: horizontal

The aim vector is always calculated as `marker position - fixture optical-centre position`. Fixture calibration then maps that world-space bearing and elevation into the fixture's own physical pan and tilt angles.

## Fixture types and fixtures

Like a lighting console, FART separates a **fixture type** (a reusable personality) from a **fixture** (one patched instance of it).

- A **fixture type** holds the channel offsets within the fixture's own footprint (pan, tilt, dimmer, shutter, zoom, iris, focus and their fine channels), the footprint size, its physical pan/tilt range, reverse flags, and an optional zoom/iris beam model for Auto size. Define it once and patch as many fixtures of it as you need.
- A **fixture** holds its name, PSN marker, optical-centre position, calibration (pan-zero bearing, tilt-zero elevation, direction, trim), the DMX patches described below, and its safety options.

Channels are **offsets within the fixture's own footprint** (1-based, `0` disables), not absolute DMX slots; FART adds the fixture's start address.

### GDTF import and GDTF Share

**Import from GDTF…** creates a type from a `.gdtf` file, and **Browse GDTF Share…** searches and imports from [gdtf-share.com](https://gdtf-share.com) (an account is required; your password is kept in your OS keychain). A GDTF-backed type keeps the whole file, and **which DMX mode a fixture runs is chosen per fixture**, so two fixtures of one type can run different modes. A GDTF type's channel fields are read-only; use **Convert to custom copy…** if you need to edit them by hand.

The importer derives pan/tilt/dimmer/shutter/zoom/iris/focus offsets, the real footprint of the mode, a shutter-open value, an iris 100% cap (so the iris control stays out of pulse and effect ranges), zoom beam angles from `PhysicalFrom`/`PhysicalTo`, and iris physical apertures where present.

GDTF import is a setup helper, not a source of truth: always verify the result against the fixture's manual before moving a real fixture.

### The patches

Every fixture has up to three independent DMX patches, each with its own universe and start address, so DMX in and DMX out never have to share a universe number:

| Patch | Purpose | Always on? |
|---|---|---|
| **DMX Out patch** | Where FART sends its computed output to the real light | Yes |
| **Console shadow patch** | A live copy of the fixture's full real channel footprint from the console, for passthrough | Tickbox |
| **Console mode/marker control** | A small block of channels from the console that switches the fixture between manual and auto, and picks its marker | Tickbox |

See [Console control](#console-control-shadow-patch-and-modemarker) for how the last two work.

## Calibration

Calibration estimates a fixture's real-world optical-centre position and its pan-zero/tilt-zero angles by aiming it at several known points and solving for the geometry that fits. See **[docs/CALIBRATION.md](docs/CALIBRATION.md)** for the full step-by-step workflow.

In short: on the **Calibration** tab, select one or more fixtures and open the wizard. Aim each fixture at each known point (using the sliders, the dials, or the fine-adjust controls), capture it, then solve. The wizard has arrow-key nudging (Left/Right pan, Up/Down tilt; Shift = a tenth of the step, Ctrl = ten times), a step selector (1°, 0.1°, 0.01°), exact numeric pan/tilt entry, and Shift-drag fine control on the pan dial and tilt arc.

## DMX in and DMX out

### DMX in

On the **DMX In** tab choose **Art-Net** or **sACN** (one at a time; both protocols' settings are remembered). The tab includes a live 512-channel monitor for any universe — hover a cell to see its channel number and value — which doubles as a console-feed debugging tool.

- **Input timeout** — how long a universe can go without a packet before the console feed counts as lost (default 3 s). Set it comfortably above your console's send interval; a console sending slowly (even under 1 frame a second) is fine as long as the timeout is longer than its interval.
- **sACN** — FART joins the multicast group for every universe a fixture needs, and when several sources send to one universe it locks onto the highest-priority one (first seen wins among equals, failing over after 2.5 s of silence), so two sources never fight over a universe.

### DMX out

On the **DMX Out** tab choose one output (the same live monitor shows exactly what was last sent):

- **Art-Net** — universe numbering starts at `0`. Unicast to the receiving node where possible. FART sends one ArtDMX stream per enabled fixture universe.
- **sACN** — multicast, universe numbering starts at `1` (`1–63999`). FART transmits every enabled fixture universe.
- **Open DMX (ENTTEC USB)** — choose the FTDI virtual serial port (`COMx` on Windows, `/dev/cu.usbserial-*` on macOS). For several adapters, map them to software universes, e.g. `COM3=0, COM4=1`. Open DMX is unbuffered, so the OS must generate the break and slots continuously; this has only been verified on Windows. Prefer Art-Net, sACN or a buffered interface for anything critical.

## Console control: shadow patch and mode/marker

This is the intended way to use FART alongside a console that doesn't do 3D tracking natively: the console owns the show and can reclaim a tracked fixture whenever it needs to.

> Unit-tested and field-tested on one rig, but not verified across consoles, networks and fixtures. Test with shutters closed before relying on it in a show.

**Shadow patch.** Tick **Enable console shadow patch** on a fixture and give the universe and start address where the console's *own* patch of that fixture lives. Each cycle FART copies that fixture's whole footprint from there into its output, then overwrites only what it owns. Anything it doesn't understand (colour, gobo, prism, effects) therefore always passes through. Universe 0 is a real Art-Net universe, so the tickbox, not the universe number, turns this on. With nothing patched, there is nothing to pass.

**Mode and marker control.** Tick **Enable console mode/marker control** and give the console universe plus a mode channel and/or a marker-select channel (`0` means that channel isn't used). These channels are not part of the fixture's footprint, so they are read by FART and never sent to the light.

- **Mode** — below `128` is **full passthrough**: FART writes nothing for that fixture, and its whole footprint — dimmer, pan, tilt, beam, everything — comes from the console. `128` and above is **auto**: FART drives pan and tilt, and the dimmer and beam follow your [Dimmer and Beam modes](#the-operator-tab-dimmer-and-beam-control).
- **Marker select** — `0` uses the fixture's configured marker; a non-zero value is used directly as the PSN marker ID to follow.

Reassigning a fixture's marker live, or returning it from full passthrough to auto, forces a fresh snap to the new target and a brief configurable blackout (**Marker-change blackout**, default 0.5 s) instead of sweeping across the space while lit.

If a fixture is in full passthrough but outputs nothing, the overview table's State column says why: the shadow patch isn't enabled, the footprint is 0, or FART is still waiting for the feed.

## Safety behaviour reference

FART's dimmer output is governed by several independent safety conditions, evaluated per fixture every cycle. Any one of them forces the dimmer to `0` (and the shutter closed, if configured) regardless of the others — including when the dimmer is being passed through from the console:

| Condition | Configured | Default behaviour |
|---|---|---|
| PSN tracking lost (no fresh position within the tracking timeout) | Per-fixture **On tracking loss** | Blackout — set to *Keep current intensity* to leave the dimmer alone while pan/tilt freezes at the last known position |
| Calculated pan/tilt exceeds a fixture's mechanical limits | Per-fixture **Blackout on limit**, with optional zoom/iris-to-100% | Off by default (opt in per fixture) |
| Console signal lost: the mode channel goes quiet, or a passed-through dimmer's shadow feed does | Per-fixture **On console signal loss**, and the **Input timeout** on the DMX In tab | Blackout — *Keep tracking, force dimmer off* also keeps aiming from live PSN so the fixture is already right when the console returns; *Keep tracking, hold last dimmer* is an explicit opt-in that can leave a light lit indefinitely if the console never returns |
| Live marker reassignment or mode change | Per-fixture **Marker-change blackout** (seconds) | Forces a brief blackout while the fixture swings to its new target |
| Start | — | The manual dimmer fader always starts at 0 |

Stopping FART, or its output loop erroring, always sends one blank (all-zero) frame per universe before closing the output.

## Configuration files

FART saves its settings to `FART2.fart` in your home folder (the `2` is historical and kept so existing settings are never orphaned). A `.fart` file is a zip archive containing the settings as JSON plus one copy of each GDTF file your fixture types use, so a saved file is fully self-contained. Settings are saved when you close the app.

Your GDTF Share login lives in your OS keychain, and a cache of the catalogue listing is kept in `FART2_gdtf_share_cache.json` in your home folder.

## Coming from v1

v1 was a different (Tkinter) application that this version replaces; it remains available in the git history and under the `v1.x` tags. To bring v1 settings across, copy your v1 `FART.json` to `FART2.json` in your home folder before first launching FART: it is imported once and then saved in the new format. What carries over: PSN settings, DMX output, and each fixture's position, calibration and channel map (each v1 fixture becomes its own type plus a fixture). What doesn't: OSC and Art-Net intensity-fader input (the dimmer is now the fader, or per-fixture passthrough), and the old arming step (removed; see [the Operator tab](#the-operator-tab-dimmer-and-beam-control)). A v1 fixture that used live console control keeps working, with its shadow patch and mode/marker control switched on.

## Testing against grandMA3 onPC

Use an otherwise unused Art-Net universe for FART and map it to a dedicated MA local universe, with the real fixture personality patched at the exact matching start address. Put the PSN-tracked marker fixture on a different MA universe, so incoming Art-Net zeroes from FART don't force it to `0,0,0`.

See **[docs/GRANDMA3_TESTING.md](docs/GRANDMA3_TESTING.md)** for a complete example layout and troubleshooting steps.

## Development

Run the tests (`py -3` on Windows, `python3` on macOS/Linux):

```bash
python3 -m pip install -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -v
```

(`pytest` also works.) The tests include real-socket tests for every protocol plugin and an end-to-end test that wires real PSN, real Art-Net/sACN in and out through the actual runner.

The [GitHub Actions workflow](https://github.com/bfulham/fart/actions/workflows/build.yml) tests the application and builds `FART.exe` (Windows) and `FART.app` (macOS, Apple Silicon) as workflow artifacts on every push and pull request. Pushing a tag beginning with `v` also creates or updates a GitHub pre-release with both builds attached (see [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)).

For how the code is organised — the engine, the plugins, the runner and the UI — see **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

### Testing without OpenFollow or a fixture

[tools/fart_test_tool.py](tools/fart_test_tool.py) is a standalone script that sends synthetic OpenFollow-style PSN positions and decodes FART's own Art-Net output, so tracking (and console control) can be exercised end to end without OpenFollow, a real console, or a physical fixture. See [tools/README.md](tools/README.md).

## Contributing and support

Contributions are welcome — see **[CONTRIBUTING.md](CONTRIBUTING.md)** for development setup and pull request expectations. Bugs and feature requests go through [GitHub Issues](https://github.com/bfulham/fart/issues); if you believe you've found a safety-relevant issue (unexpected movement or intensity), see **[SECURITY.md](SECURITY.md)** first.

If FART's been useful to you, you can support development here: [![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/G5F8279CAL)

## Licence

FART is released under the [MIT License](LICENSE).
