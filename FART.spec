# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

sacn_datas, sacn_binaries, sacn_hiddenimports = collect_all("sacn")

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
