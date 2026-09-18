# FART v2 (in progress)

A ground-up redesign, developed here alongside the shipped v1 (`fart.py` at
the repo root, untouched) until it's ready to replace it. Nothing here is
wired into the v1 app, its tests, or its CI.

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
- `fart/config.py` -- the new nested `Settings` schema (grouped by concern:
  `psn_in`, `dmx_in`, `dmx_out`, `fader`, per-fixture), plus a `migrate_v1()`
  that maps an existing v1 `FART.json` onto it.
- `fart/engine.py` -- all tracking/aiming/safety math, ported from v1 with
  behaviour preserved (`calculate_aim`, `write_fixture_to_frame`,
  console-relay mode/marker resolution), plus `run_cycle()`: an explicit,
  directly-testable replacement for what used to live inline in v1's
  `App.loop()`.
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

OSC support is dropped entirely (was only ever an intensity-fader input
option in v1; not needed going forward).

## What's *not* here yet

- Any UI at all (planned: PySide6, replacing v1's Tkinter). This branch is
  engine/plugins first, verified working headlessly, before UI is built on
  top of it.
- The calibration solver and GDTF import (still only in v1's `fart.py`).
  Not needed to prove the architecture; will be ported before v2 replaces
  v1.
- Real hardware testing for `open_dmx_out.py` (no Open DMX USB adapter
  available to test against here).

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

Run the tests:

```bash
cd v2
python3 -m unittest discover -s tests -v
```

40/40 passing as of this writing.
