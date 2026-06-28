# Contributing

Thanks for helping improve FART.

## Before opening an issue

- Confirm you are using the newest version.
- For tracking problems, include the PSN status counters shown in the Run tab.
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

Keep changes focused and describe how they were tested. New protocol or geometry behaviour should include a unit test where practical. Do not remove blackout, dimmer-arm, channel-validation, or tracking-timeout safeguards without a clear safety justification.

By contributing, you agree that your contribution may be distributed under the MIT License.
