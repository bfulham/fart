# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

sacn_datas, sacn_binaries, sacn_hiddenimports = collect_all("sacn")
app_version = Path("VERSION").read_text().strip()

a = Analysis(
    ["fart.py"],
    pathex=[],
    binaries=sacn_binaries,
    datas=sacn_datas,
    hiddenimports=sacn_hiddenimports,
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
    # else branch) keeps the existing single-file EXE unchanged.
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="FART",
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
        name="FART",
    )
    app = BUNDLE(
        coll,
        name="FART.app",
        icon=None,
        bundle_identifier="com.bfulham.fart",
        info_plist={
            "CFBundleName": "FART",
            "CFBundleDisplayName": "FART",
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
        name="FART",
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
