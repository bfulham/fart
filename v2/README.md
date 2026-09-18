# FART v2 (in progress)

A ground-up redesign, developed here alongside the shipped v1 (`fart.py` at
the repo root, untouched) until it's ready to replace it. Nothing here is
wired into the v1 app, its tests, or its CI.

The engine, plugins, and PySide6 UI are all in place and runnable
(`python3 -m fart` from this directory). What's left before this can
replace v1 is packaging and real-hardware verification -- see "What's *not*
here yet" below.

## Why

v1 grew into one 3400-line file mixing UI, protocol I/O, and tracking logic
together. That made a real bug hard to see coming: two separate sockets
(the Art-Net fader input and the live-console-control input) both tried to
exclusively bind UDP port 6454, which fails outright on macOS (`[Errno 48]
Address already in use`) even though it happened to work on Windows. v2
fixes this at the architecture level rather than patching it: `Settings.
dmx_in.active` is a single choice (`"artnet"` or `"sacn"`, never both), so
two control-input plugins competing for the same socket is not just
avoided by convention -- it's not a state the system can be in.

## What's here

- `fart/bus.py` -- the three thread-safe stores (`TrackerBank`, `FaderState`,
  `ExternalInputBus`) that connect input plugins to the engine.
- `fart/config.py` -- the nested `Settings` schema (grouped by concern:
  `psn_in`, `dmx_in`, `dmx_out`, `fader`, `fixture_types`, `fixtures`), plus
  `migrate_v1()` that maps a pre-v3 config (v1's flat `FART.json`, or v2's
  earlier flat-fixture `FART2.json`) onto the current shape.

  **Fixtures are split into a `FixtureType` and a `FixtureConfig` instance**,
  the way a real console patches a fixture, rather than one flat dataclass
  with ~14 raw absolute channel numbers per fixture:
  - `FixtureType` -- a reusable personality: channel *offsets* within the
    fixture's own footprint (not absolute channel numbers), its total
    footprint size, and physical properties (pan/tilt range, beam model,
    reverse flags, intensity scale). Define one type, patch as many
    instances of it as needed -- no more retyping the same ~14 channel
    numbers for every identical fixture in a rig.
  - `FixtureConfig` -- an instance: position, calibration, and **three
    independent DMX patches** (universe + start address each): where FART
    sends its own computed output, where a live console feed of this
    fixture's *entire* real channel footprint can optionally be read from
    (the "shadow patch" -- see below), and where a small mode/marker
    control block for live relay switching lives. These can all be on
    completely different universes -- DMX In and DMX Out no longer have to
    share a universe number, which was a real conflict risk when a
    console's own DMX output and FART's computed output collided on the
    same universe.
- `fart/engine.py` -- all tracking/aiming/safety math, ported from v1 with
  behaviour preserved (`calculate_aim`, `write_fixture_to_frame`,
  console-relay mode/marker resolution), plus `run_cycle()`: an explicit,
  directly-testable replacement for what used to live inline in v1's
  `App.loop()`. `resolve_fixture(fixture, fixture_type)` merges a patched
  instance with its type into a `ResolvedFixture` (concrete absolute
  channel numbers) -- the shape `calculate_aim`/`write_fixture_to_frame`
  actually consume, built fresh each cycle.

  **Console shadow patch**: for a fixture with `shadow_universe` set,
  `copy_shadow_into_output()` copies that fixture's *entire* footprint
  (e.g. all 35 channels of a real fixture, not just the handful FART
  understands) from the console's shadow feed into its slice of the output
  frame every cycle, before FART overwrites the channels it actually owns
  (pan/tilt always; dimmer always in auto mode; zoom/iris only when
  auto-beam-size is on). Everything else -- color, gobo, prism, or zoom/
  iris/focus when FART isn't actively driving them -- passes through
  untouched. Manual mode (via the console mode channel) skips FART's
  writes entirely, so the shadow copy alone *is* the full passthrough.
- `fart/runner.py` -- headless orchestration: wires whichever plugins
  `Settings` names to the engine and runs the cycle loop. No UI dependency;
  a future Qt UI (or a test, or a script) drives this same class.
- `fart/plugins/` -- one module per protocol:
  - `psn_in.py` -- ported from v1's `PSNReceiver`.
  - `artnet_in.py` -- new: a single unified Art-Net listener (fixes the
    port-6454 bug directly; see "Why" above).
  - `sacn_in.py` -- **new in v2** (v1 had no sACN input at all). Hand-rolled
    E1.31 parsing, byte-for-byte verified against the reference `sacn`
    library's implementation before being written, not just recalled from
    memory.
  - `artnet_out.py` -- ported from v1's `ArtNet`.
  - `sacn_out.py` -- hand-rolled E1.31 sending, replacing v1's dependency on
    the third-party `sacn` PyPI package. Same wire-format module (`_sacn.py`)
    as `sacn_in.py`, so the two directions can't drift apart from each other.
  - `open_dmx_out.py` -- ported from v1's `OpenDMX`/`OpenDMXPort`.
- `fart/calibration.py` -- the multi-fixture calibration solver, ported
  from v1's `solve_fixture_calibration` with behaviour unchanged.
- `fart/gdtf.py` -- GDTF (.gdtf) fixture file import, ported from v1's
  `import_gdtf_channel_mapping` and its helpers, minus the Tk mode-selection
  dialog (`select_gdtf_mode` in v1) -- picking a mode when a GDTF has more
  than one is a UI concern for whatever calls this, not part of the import
  logic itself. Called with `start_address=1` (the default), its output
  maps directly onto a `FixtureType`'s own offset fields, plus a derived
  `footprint` (the highest channel offset seen anywhere in the mode, not
  just the channels FART classifies -- needed so a shadow patch can bring
  across the whole fixture).
- `fart/ui/` -- the PySide6 UI, replacing v1's Tkinter front end:
  - `main_window.py` -- `MainWindow`: owns `Settings`, the `Runner`, and a
    100ms timer draining a thread-safe log queue and refreshing live status.
    Setup tabs stay usable while running (no lockout) -- edits apply on the
    next output cycle, though switching DMX in/out protocol still needs a
    stop/start to actually reconnect the plugin sockets.
  - `operator_tab.py` -- Start/Stop, arm, manual fader, zoom/iris/focus beam
    sliders, live per-light overview table, log view.
  - `psn_in_tab.py`, `dmx_in_tab.py`, `dmx_out_tab.py`, `fixtures_tab.py`,
    `calibration_tab.py` + `calibration_wizard.py` -- one tab per concern,
    matching the layout described in "Why" below.
  - `fixtures_tab.py` -- a Fixture Types library (add/duplicate/remove/
    import-from-GDTF) alongside the fixture instance list; picking a
    fixture shows its patch (type, position, calibration, the three DMX
    patches), picking a type shows its channel offsets and physical
    properties. Removing a type in use reassigns affected fixtures to
    another type rather than leaving a dangling reference.
  - `binding.py` -- small two-way widget <-> `Settings` field helpers shared
    by every tab.
  - `dmx_in_tab.py` / `dmx_out_tab.py` show only the active protocol's
    settings (a `QStackedWidget` switched by radio buttons in a
    `QButtonGroup`) while still saving the inactive protocol's settings in
    the background, so switching back doesn't lose anything. Both also
    have a live "Artnetominator-style" 512-channel status grid for a
    chosen universe -- DMX In reads straight off the shared
    `ExternalInputBus` (whatever the active control-input plugin actually
    received), DMX Out reads the literal last frame the Runner sent
    (`dmx_channel_grid.py` is the shared grid widget).
- `fart/__main__.py` -- entry point (`python3 -m fart`).

OSC support is dropped entirely (was only ever an intensity-fader input
option in v1; not needed going forward).

## What's *not* here yet

- Real hardware testing for `open_dmx_out.py` (no Open DMX USB adapter
  available to test against here).
- Packaging (PyInstaller spec, CI build workflow) -- v1's exists at the repo
  root; v2 needs its own once the app is otherwise feature-complete.

## How this has been verified

Every plugin has a **real UDP socket test**, not just a parse/build round
trip through a pure function -- `tests/test_plugins.py` actually sends and
receives real packets over loopback for PSN, Art-Net, and sACN. The sACN
tests matter most here: nothing in this project has ever exercised sACN
input before, and the E1.31 byte layout used was independently checked
against the reference `sacn` library's source before writing a single byte
of it, both for parsing (`_sacn.py:parse_sacn_dmx`) and building
(`_sacn.py:build_sacn_dmx`).

`tests/test_runner_integration.py` goes one level up: it starts a real
`Runner` with real plugins, sends real PSN packets, and checks the
resulting Art-Net (and separately sACN) output against an independently
computed expected pan/tilt -- proving the full pipeline end to end, not
just each piece in isolation.

`tests/test_calibration.py` and `tests/test_gdtf.py` are carried over
directly from v1's test suite (same fixture geometry, same synthetic GDTF
file), confirming the ported solver and importer still produce the same
results.

`tests/test_ui.py` drives the real PySide6 widgets with `QTest` (real
synthesized clicks and key events against a real, shown `MainWindow`, run
offscreen via `QT_QPA_PLATFORM=offscreen`) -- not just calling handler
methods directly. It covers adding/editing/removing fixtures, switching the
DMX In protocol radio buttons and confirming both protocols' settings
survive the switch, starting/stopping the runner and confirming setup tabs
lock, and one full live end-to-end pass: real PSN packets sent over
loopback while the window is running, polling the operator tab's overview
table until it shows the tracked fixture as "LIVE".

Run the tests:

```bash
cd v2
pip install -r requirements.txt
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -v
```

67/67 passing as of this writing.
