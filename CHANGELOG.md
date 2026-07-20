# Changelog

## 0.7.2

- Fixed calibration dialogs calling the old light-editor method name.
- Added zoom, iris, focus, and iris 100% DMX point fields to the DMX setup wizard.
- Changed new-fixture default tilt direction to -1 to match common moving-head tilt behaviour.
- Added basic GDTF channel import for pan, tilt, dimmer, shutter, zoom, iris, and focus setup.
- Capped iris output at a configurable DMX value so 100% can avoid effect ranges above the usable iris band.

## 0.7.1 - 2026-07-20

- Calibration now requires DMX setup first.
- Added a DMX setup dialog before calibration so pan, tilt, dimmer, and optional shutter channels are confirmed before any calibration output can start.
- Starting calibration on an incomplete fixture now opens DMX setup instead of the pan/tilt aiming wizard.

## 0.7.0 - 2026-07-20

- Replaced confusing set-zero calibration buttons with a fixture calibration wizard.
- New fixtures now prompt for manual setup or calibration.
- Calibration mode provides pan/tilt faders, aiming output, fixed/custom target points, capture workflow, and solver.
- Solver estimates fixture optical-centre XYZ plus pan-zero bearing and tilt-zero elevation from captured aim samples.
- Added automated calibration-solver test.

All notable changes to FART are documented here.

## 0.6.0 - 2026-07-05

- Added live shared zoom, iris, and focus controls to the Overview tab.
- Added per-light zoom, iris, and focus DMX channel mapping.
- Added optional 16-bit fine channels for zoom and focus.
- Added per-light reverse controls for fixture personalities with inverted attribute ranges.
- Extended channel conflict validation, duplication, configuration migration, tests, and the MAC Quantum example.

## 0.5.0 - 2026-07-04

- Added independent PSN marker selection for every light.
- PSN receiver now retains live positions for all discovered trackers simultaneously.
- Rebuilt the Overview tab for multi-light operation.
- Added a live per-light status table with marker, XYZ, pan/tilt, distance, limits, and tracking state.
- Added a lightweight interactive 3D preview showing fixtures, markers, and beam lines.
- Added per-light tracking-loss blackout so one lost marker does not black out lights following other healthy markers.
- Added migration of existing configurations so each light inherits the previous global marker ID.
- Added multi-tracker PSN decoder test coverage.

## 0.4.0 - 2026-06-28

- Renamed the application to **FART — Fixture Aiming and Remote Tracking**.
- Renamed the executable to `FART.exe`.
- Moved the normal settings file to `%APPDATA%\FART.json`.
- Added automatic migration from `%APPDATA%\OpenFollowFollowspot.json`.
- Added a complete GitHub-ready repository structure, tests, documentation, examples, issue templates, and Windows build automation.

## 0.3.1 - 2026-06-28

- Fixed sACN startup with the current `sacn` package.
- Enabled multicast output and validated sACN universe ranges.

## 0.3.0 - 2026-06-28

- Added support for multiple independently calibrated fixtures aimed at one PSN marker.
- Added per-light channel conflict detection and optional 16-bit dimmer output.

## 0.2.1 - 2026-06-28

- Fixed OpenFollow/pypsn position-chunk decoding.
- Added detailed PSN diagnostics.
