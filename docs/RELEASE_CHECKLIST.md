# Release checklist

Before publishing a FART release:

1. Run `python -m unittest discover -s tests -v` on the exact commit you intend to tag (after merging, not just on a feature branch — a feature branch's tests passing doesn't guarantee the merged result still does).
2. Start the GUI from source with `py -3 fart.py` and confirm it opens without errors.
3. Confirm PSN auto-detect sees OpenFollow trackers.
4. Test Art-Net and/or sACN output in ArtNetominator or grandMA3 onPC on a spare universe (see [GRANDMA3_TESTING.md](GRANDMA3_TESTING.md)).
5. Confirm dimmer lock, tracking-loss blackout, and (if changed this release) any other [safety behaviour](../README.md#safety-behaviour-reference) still works as documented.
6. If the release includes anything not exercised by the automated tests or the checks above — new hardware protocols, new console-relay/network-input behaviour, anything touching real fixture movement — call that out explicitly in the release notes rather than presenting it as fully verified.
7. Update `VERSION`, `APP_VERSION` in `fart.py`, and add a `CHANGELOG.md` entry.
8. Build with `build_windows_exe.bat` and sanity-check the resulting `dist\FART.exe` launches.
9. Tag with `vX.Y.Z` — pushing it triggers the GitHub Actions workflow, which runs the tests, builds `FART.exe`, and creates or updates a GitHub release with the built ZIP attached.
   - If the release should be marked as a **pre-release** (anything not yet verified against real hardware, per step 6), create it yourself first with `gh release create vX.Y.Z --prerelease --title "..." --notes "..."` before or as you push the tag — the workflow only creates the release if one doesn't already exist, so a release you create first keeps whatever prerelease flag you set; it will still attach the built ZIP to it afterward.
   - Otherwise, just push the tag and let the workflow create the release.
