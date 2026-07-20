# Release checklist

Before publishing a FART release:

1. Run `python -m unittest discover -s tests -v`.
2. Start the GUI from source with `py -3 fart.py`.
3. Confirm PSN auto-detect sees OpenFollow trackers.
4. Test Art-Net output in ArtNetominator or grandMA3 onPC on a spare universe.
5. Confirm dimmer lock and tracking-loss blackout.
6. Build with `build_windows_exe.bat`.
7. Tag with `vX.Y.Z` so GitHub Actions attaches the Windows ZIP to the release.
