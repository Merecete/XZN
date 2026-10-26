# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build specification for XZN-CDR.

Usage:
    pyinstaller xzn-cdr.spec

Output:
    dist/XZN-CDR/XZN-CDR.exe   (Windows)
    dist/XZN-CDR/XZN-CDR       (Linux)
    dist/XZN-CDR.app           (macOS)
"""

import os
import re
import sys
from PyInstaller.utils.hooks import collect_all

with open(os.path.join(SPECPATH, "xzn-cdr.py"), encoding="utf-8") as _f:
    VERSION = re.search(r'^VERSION\s*=\s*"([^"]+)"', _f.read(), re.M).group(1)

# --- Build switches ---------------------------------------------------
DEBUG = os.environ.get("XZN_DEBUG", "0") == "1"

# --- Files and Folders to bundle --------------------------------------
datas = [
    ("xzn.ico", "."),
    ("xzn.png", "."),
    ("xzn-logo.png", "."),
]

# WICHTIG: Den ffmpeg-Ordner mit den .exe-Dateien bündeln
if os.path.isdir("ffmpeg"):
    datas.append(("ffmpeg", "ffmpeg"))
else:
    print("[spec] WARNUNG: 'ffmpeg'-Ordner nicht gefunden. Der Build wird auf System-ffmpeg angewiesen sein.")

binaries = []
# certifi: CA certificates for the HTTPS MusicBrainz lookup
hiddenimports = ["certifi"]

# --- Packages with native binaries -----------------------------------
# PIL (Pillow) → für das Logo im Info-Fenster
for pkg in ("PIL",):
    try:
        d, b, h = collect_all(pkg)
        datas         += d
        binaries      += b
        hiddenimports += h
    except Exception as e:
        print(f"[spec] collect_all({pkg!r}) skipped: {e}")

# pycdio (nur Linux) – soft fail, falls nicht vorhanden
try:
    import cdio
    import pycdio
    hiddenimports += ["cdio", "pycdio"]
except ImportError:
    pass

# --- Analysis ---------------------------------------------------------
a = Analysis(
    ["xzn-cdr.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib", "scipy", "pandas", "pytest",
        "setuptools._distutils",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

pyz = PYZ(a.pure)

# --- EXE / COLLECT ---------------------------------------------------
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="XZN-CDR",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=DEBUG,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="xzn.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="XZN-CDR",
)

# --- macOS app bundle -------------------------------------------------
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="XZN-CDR.app",
        icon="xzn.icns",
        bundle_identifier="com.merecete.xzn-cdr",
        version=VERSION,
        info_plist={
            "CFBundleName": "XZN-CDR",
            "CFBundleDisplayName": "Xenon-Zones CD Ripper",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "© 2026 Merecete. MIT License.",
        },
    )
