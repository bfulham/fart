# Release checklist

Before publishing a FART release:

1. Run the tests on the exact commit you intend to tag (after merging, not just on a feature branch — a branch's tests passing doesn't guarantee the merged result still does):
   `QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -v`
2. Start the GUI from source with `py -3 -m fart` and confirm it opens without errors.
3. Start FART and confirm the PSN In tab's tracker table shows your OpenFollow trackers, and the Operator tab's PSN counters keep increasing.
4. Test Art-Net and/or sACN output in ArtNetominator or grandMA3 onPC on a spare universe (see [GRANDMA3_TESTING.md](GRANDMA3_TESTING.md)).
5. Confirm the dimmer fader starts at 0 on Start, tracking-loss blackout, and (if changed this release) any other [safety behaviour](../README.md#safety-behaviour-reference) still works as documented.
6. If the release includes anything not exercised by the automated tests or the checks above — new hardware protocols, new console-control/network-input behaviour, anything touching real fixture movement — call that out explicitly in the release notes rather than presenting it as fully verified.
7. Update `VERSION` and `APP_VERSION` in `fart/_version.py` (they must match), and add a `CHANGELOG.md` entry.
8. Build with `build_windows_exe.bat` (or `./build_macos_app.sh`) and sanity-check that the resulting `dist\FART.exe` / `dist/FART.app` launches.
9. Tag with `vX.Y.Z` — pushing it triggers the GitHub Actions workflow, which runs the tests, builds `FART.exe` and `FART.app`, and creates or updates a GitHub **pre-release** with both ZIPs attached.
   - A release is created as a pre-release because CI cannot verify anything against real hardware. Once it has been verified (step 6), promote it from the GitHub releases page by unticking *pre-release*.
   - To write the notes yourself first, create the release with `gh release create vX.Y.Z --prerelease --title "..." --notes "..."` before or as you push the tag: the workflow only creates the release if one doesn't already exist, and still attaches the built ZIPs afterwards.
