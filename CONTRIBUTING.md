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

```powershell
py -3 -m venv .venv
.venv\Scripts\activate
py -3 -m pip install -r requirements-dev.txt
py -3 -m unittest discover -s tests -v
py -3 fart.py
```

## Pull requests

Keep changes focused and describe how they were tested. New protocol or geometry behaviour should include a unit test where practical, and any pure logic (packet parsing, mode/marker resolution, staleness policies, and similar) should be written as a function that can be tested without a running GUI.

Do not remove or weaken any of the following without a clear, explicitly stated safety justification: dimmer arming, tracking-loss blackout, limit blackout, channel-overlap validation, the tracking timeout, or (for live console control) the console-signal-loss policies and marker-change blackout window. If a change touches real fixture movement or intensity and hasn't been verified against real hardware, say so plainly in the pull request rather than presenting it as fully tested.

By contributing, you agree that your contribution may be distributed under the MIT License.
