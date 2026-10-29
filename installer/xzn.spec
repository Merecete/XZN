# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build specification for Xenon-Zones.

Usage:
    pyinstaller installer/xzn.spec

Debug build (console window visible, useful for crash tracing):
    set XZN_DEBUG=1 && pyinstaller installer/xzn.spec          (Windows CMD)
    $env:XZN_DEBUG=1; pyinstaller installer/xzn.spec           (PowerShell)
    XZN_DEBUG=1 pyinstaller installer/xzn.spec                 (Linux/macOS)

Single-file build (not recommended for a media player — PyAV + FFmpeg
+ PortAudio extract ~50 MB of DLLs on every launch in onefile mode):
    set XZN_ONEFILE=1 && pyinstaller installer/xzn.spec

Output:
    dist/XZN/XZN.exe       (Windows, onedir — the default)
    dist/XZN/XZN           (Linux, onedir)
    dist/XZN.app           (macOS app bundle)

The installer scripts in installer/ expect this onedir layout.
"""

import os
import re
import sys
from PyInstaller.utils.hooks import collect_all

# Version straight from the source, so the app bundle never lags behind.
# This file lives in installer/; all project files are one level up.
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))


def R(*parts):
    return os.path.join(ROOT, *parts)


with open(R("xzn.py"), encoding="utf-8") as _f:
    VERSION = re.search(r'^VERSION\s*=\s*"([^"]+)"', _f.read(), re.M).group(1)


# --- Build switches ---------------------------------------------------
DEBUG   = os.environ.get("XZN_DEBUG",   "0") == "1"
ONEFILE = os.environ.get("XZN_ONEFILE", "0") == "1"


# --- Files that must be reachable via resource_path() at runtime -----
datas = [
    (R("xzn.ico"),        "."),
    (R("xzn.png"),        "."),    # window icon on Linux / macOS
    (R("xzn-logo.png"),   "."),
    (R("XZN Intro.mp3"), "."),
]
binaries = []
# certifi: CA certificates for the HTTPS MusicBrainz lookup (audio CDs)
hiddenimports = ["certifi"]


# --- Packages with native binaries -----------------------------------
# av (PyAV)           → FFmpeg shared libraries (avcodec-*.dll, …)
# sounddevice         → PortAudio DLL
# PIL (Pillow)        → image codec DLLs
# tkinterdnd2         → Tcl/Tk DnD library
# mutagen             → optional, pure Python (harmless if missing)
# jeepney             → Linux media keys (MPRIS), pure Python
for pkg in ("av", "sounddevice", "PIL", "tkinterdnd2", "mutagen", "jeepney"):
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
    [R("xzn.py")],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Not used by XZN; keeps the bundle lean.
        # (scipy must NOT be excluded: the equalizer uses scipy.signal.)
        "matplotlib", "pandas", "pytest",
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
        icon=R("xzn.ico"),
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
        icon=R("xzn.ico"),
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="XZN",
    )

# --- macOS app bundle -------------------------------------------------
if sys.platform == "darwin" and not ONEFILE:
    _audio = ["mp3", "ogg", "oga", "opus", "flac", "wav", "m4a", "m4b",
              "aac", "aiff", "aif", "ape", "wma"]
    _video = ["mp4", "mkv", "avi", "mov", "webm", "flv", "m4v"]
    app = BUNDLE(
        coll,
        name="XZN.app",
        icon=R("xzn.icns"),
        bundle_identifier="com.merecete.xzn",
        version=VERSION,
        info_plist={
            "CFBundleName": "XZN",
            "CFBundleDisplayName": "XZN",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "© 2026 Merecete. MIT License.",
            # "Open with XZN" in Finder (handled via ::tk::mac::OpenDocument)
            "CFBundleDocumentTypes": [
                {"CFBundleTypeName": "Audio", "CFBundleTypeRole": "Viewer",
                 "LSHandlerRank": "Alternate", "CFBundleTypeExtensions": _audio},
                {"CFBundleTypeName": "Video", "CFBundleTypeRole": "Viewer",
                 "LSHandlerRank": "Alternate", "CFBundleTypeExtensions": _video},
            ],
        },
    )
