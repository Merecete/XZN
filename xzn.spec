# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build specification for Xenon-Zones.

Usage:
    pyinstaller xzn.spec

Debug build (console window visible, useful for crash tracing):
    set XZN_DEBUG=1 && pyinstaller xzn.spec          (Windows CMD)
    $env:XZN_DEBUG=1; pyinstaller xzn.spec           (PowerShell)
    XZN_DEBUG=1 pyinstaller xzn.spec                 (Linux/macOS)

Single-file build (not recommended for a media player — PyAV + FFmpeg
+ PortAudio extract ~50 MB of DLLs on every launch in onefile mode):
    set XZN_ONEFILE=1 && pyinstaller xzn.spec

Output:
    dist/XZN/XZN.exe       (Windows, onedir — the default)
    dist/XZN/XZN           (Linux/macOS, onedir)

The installer (setup.iss) expects the onedir layout.
"""

import os
from PyInstaller.utils.hooks import collect_all


# --- Build switches ---------------------------------------------------
DEBUG   = os.environ.get("XZN_DEBUG",   "0") == "1"
ONEFILE = os.environ.get("XZN_ONEFILE", "0") == "1"


# --- Files that must be reachable via resource_path() at runtime -----
datas = [
    ("xzn.ico",        "."),
    ("xzn-logo.png",   "."),
    ("XZN Intro.mp3",  "."),
]
binaries = []
hiddenimports = []


# --- Packages with native binaries -----------------------------------
# av (PyAV)           → FFmpeg shared libraries (avcodec-*.dll, …)
# sounddevice         → PortAudio DLL
# PIL (Pillow)        → image codec DLLs
# tkinterdnd2         → Tcl/Tk DnD library
# mutagen             → optional, pure Python (harmless if missing)
for pkg in ("av", "sounddevice", "PIL", "tkinterdnd2", "mutagen"):
    try:
        d, b, h = collect_all(pkg)
        datas         += d
        binaries      += b
        hiddenimports += h
        print(f"[spec] collect_all({pkg!r}): "
              f"{len(d)} datas, {len(b)} binaries, {len(h)} imports")
    except Exception as e:
        print(f"[spec] collect_all({pkg!r}) skipped: {e}")


# --- Analysis ---------------------------------------------------------
a = Analysis(
    ["xzn.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Not used by XZN; keeps the bundle lean.
        "matplotlib", "scipy", "pandas", "pytest",
        "setuptools._distutils",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

pyz = PYZ(a.pure)


# --- EXE / COLLECT ---------------------------------------------------
if ONEFILE:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="XZN",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,       # UPX can corrupt FFmpeg / PortAudio DLLs
        console=DEBUG,
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon="xzn.ico",
    )
else:
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name="XZN",
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
        name="XZN",
    )
