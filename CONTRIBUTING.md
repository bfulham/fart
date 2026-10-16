# Contributing

Thanks for helping improve FART.

## Before opening an issue

Use the [bug report](https://github.com/bfulham/fart/issues/new?template=bug_report.yml) or [feature request](https://github.com/bfulham/fart/issues/new?template=feature_request.yml) form.

- Confirm you are using the newest version.
- For tracking problems, include the PSN status counters shown on the Operator tab.
- For fixture problems, include the exact fixture model, mode, patch address, and relevant DMX chart.
- For output problems, state whether ArtNetominator or another packet monitor sees the expected values.
- Remove private network information that is not needed to reproduce the problem.

## Development setup

Windows:

```powershell
py -3 -m venv .venv
.venv\Scripts\activate
py -3 -m pip install -r requirements-dev.txt
py -3 -m unittest discover -s tests -v
py -3 -m fart
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -v
python3 -m fart
```

`QT_QPA_PLATFORM=offscreen` lets the UI tests run without a display. On a bare Linux machine the Qt libraries may also need system packages (for example `libegl1`). See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the code is organised.

## Pull requests

Keep changes focused and describe how they were tested. New protocol or geometry behaviour should include a unit test where practical, and any pure logic (packet parsing, mode/marker resolution, staleness policies, and similar) should be written as a function that can be tested without a running GUI.

Do not remove or weaken any of the following without a clear, explicitly stated safety justification: the dimmer fader starting at 0, tracking-loss blackout, limit blackout, the tracking timeout, the DMX-in timeout, or (for console control) the console-signal-loss policies and marker-change blackout window. A dimmer passed through from the console must never defeat any of these blackouts. If a change touches real fixture movement or intensity and hasn't been verified against real hardware, say so plainly in the pull request rather than presenting it as fully tested.

By contributing, you agree that your contribution may be distributed under the MIT License.
