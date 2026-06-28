# Changelog

All notable changes to FART are documented here.

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
