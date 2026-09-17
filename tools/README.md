# FART test tool

`fart_test_tool.py` is a standalone script that simulates OpenFollow's PSN
traffic and decodes FART's actual DMX output, so FART's tracking math and
[live console control](../README.md#live-console-control-optional) can be
exercised without OpenFollow, a real lighting console, or a physical
fixture. It is dependency-free (standard library only) and does not import
`fart.py`, so it can run on a different, lighter machine than FART itself.

Run `python3 fart_test_tool.py --help` or `python3 fart_test_tool.py <command> --help`
for the full flag list. The sections below cover the common cases.

## Setup

In FART: add or use one light, set **Output** to **Art-Net** targeting this
machine's IP (`127.0.0.1` if running the tool on the same machine as FART),
and note its universe (default `0`). Click **Start**. For the most accurate
`verify` results, also set **Smoothing** to `0` and **Lead/lag** to `0` —
otherwise error is dominated by the smoothing filter's lag rather than any
real problem.

You do not need to click **Arm all light dimmers** — pan/tilt is written to
the DMX frame regardless of arming; only intensity depends on it.

## Watch FART track a moving marker

```bash
python3 fart_test_tool.py send-psn --pattern circle --radius 2 --z 1.7 --speed 0.2
```

Sends a marker slowly circling 2m out at head height. In FART, click
**Auto-detect PSN trackers** (it should find marker `1`), assign it to your
light, and watch the Light overview / 3D preview tabs update live.
`--pattern static` sends one fixed point; `--pattern line` sweeps back and
forth. Multiple markers: run the command again in another terminal with a
different `--marker-id`.

## Watch what FART actually sends

```bash
python3 fart_test_tool.py monitor-artnet --universe 0 --channels 1-8
```

Prints the requested channel values whenever the frame changes (or every
`--interval` seconds if it hasn't). Add `--decode-fixture` (with
`--pan-coarse`/`--pan-fine`/`--tilt-coarse`/`--tilt-fine`/`--pan-min`/
`--pan-max`/`--tilt-min`/`--tilt-max` matching your light's actual channels
and ranges) to also print the decoded pan/tilt angle.

## Automatically verify tracking is correct

```bash
python3 fart_test_tool.py verify --pattern circle --radius 2 --z 1.7 --speed 0.2
```

Sends the same kind of scripted marker trajectory as `send-psn`, but also
listens for FART's Art-Net output on `--universe` (default `0`), independently
computes the pan/tilt a light with the given calibration (defaults match a
freshly-added, uncalibrated FART light) *should* produce for the marker's
known position, and compares it against what FART's DMX frame actually
decodes to. Prints `OK`/`FAIL` per sample and a final summary; exits non-zero
if anything exceeded `--tolerance` degrees (default 2.0). If your light isn't
at FART's default calibration, pass the matching `--fixture-x/y/z`,
`--pan-zero-bearing`, `--pan-direction`, `--pan-coarse`, etc.

Example output from an actual run against the real macOS build:

```text
[OK  ] t=   7.9s  expected pan=  -0.02 tilt= +18.26  actual pan=  +0.08 tilt= +18.26  error= 0.10deg

==============================
156/156 samples within 1 degrees, max error 0.10 degrees.
```

Ctrl+C stops it and prints the summary early.

## Exercise live console control without a real console

```bash
python3 fart_test_tool.py send-console --mode-channel 6 --marker-channel 7 --mode auto --marker-id 2 --repeat
```

Sends a synthetic Art-Net frame acting as the console side of
[live console control](../README.md#live-console-control-optional): set
`--mode-channel`/`--marker-channel` to whatever you configured in FART's
fixture (Lights tab -> DMX channels -> Live console control), and
`--mode manual`/`--mode auto` plus `--marker-id` to exercise passthrough vs.
auto-follow and live marker reassignment. `--repeat` keeps sending (a single
frame will eventually look stale to FART's console-signal-loss timeout).
