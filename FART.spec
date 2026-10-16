# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

app_version = Path("VERSION").read_text().strip()

a = Analysis(
    ["run_fart.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    # Onefile mode in combination with a macOS .app bundle is deprecated by
    # PyInstaller ("don't make sense... will become an error in v7.0"), so
    # macOS builds a onedir EXE and wraps it in BUNDLE() below. Windows (the
    # else branch) keeps a single-file EXE. Named "FART2" (not "FART") so a
    # user can keep the v1 and v2 builds side by side without one
    # overwriting the other.
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="FART2",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        name="FART2",
    )
    app = BUNDLE(
        coll,
        name="FART2.app",
        icon=None,
        bundle_identifier="com.bfulham.fart2",
        info_plist={
            "CFBundleName": "FART2",
            "CFBundleDisplayName": "FART 2",
            "CFBundleShortVersionString": app_version,
            "CFBundleVersion": app_version,
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "MIT License",
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="FART2",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
