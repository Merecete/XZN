#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XZN-CDR v1.0.5
==============
Companion tool for Xenon-Zones that rips audio CDs to MP3 / OGG / FLAC / WAV.

Features
--------
- Rips selected tracks (or the whole disc) from an audio CD
- Encodes to MP3, OGG, FLAC, or writes WAV directly
- Bitrate selection for lossy formats (MP3, OGG)
- STOP button: finished tracks stay on disk, current track is discarded
- Looks the disc up on MusicBrainz (artist, album, track titles) and
  names files "NN - Title" in an "Artist - Album" folder, fully tagged
- Writes ReplayGain tags so Xenon-Zones can level the volume
- Themed GUI matching Xenon-Zones (accent color, alpha, Info/Theme/License)
- Auto-saves settings (~/.xzn_cdr_settings.json)
- Writes a log file (xzn-cdr.log) to the Xenon-Zones app data directory

Platform backends
-----------------
The CD-access layer is selected at runtime:

- Windows : ``ffmpeg -f libcdio``   (requires a full ffmpeg build)
- Linux   : ``pycdio``              (libcdio + pycdio packages)
- macOS   : ``cd-paranoia``         (brew install libcdio-paranoia)

Requirements
------------
- ffmpeg on all platforms (used for encoding)
    - Windows: MUST be a "full" build with libcdio support
- Linux:   ``sudo apt install libcdio-dev libcdio-cdda-dev python3-pycdio``
- macOS:   ``brew install libcdio-paranoia ffmpeg``
- Pillow (PIL) — for the Info window logo

Bundling (PyInstaller)
----------------------
Example layout before bundling::

    xzn-cdr.py
    xzn.ico
    xzn-logo.png
    ffmpeg/
        ffmpeg.exe
        ffprobe.exe

On Windows::

    pyinstaller --onefile --windowed --icon="xzn.ico" ^
                --name="XZN-CDR" ^
                --add-data "xzn-logo.png;." ^
                --add-binary "ffmpeg;ffmpeg" ^
                xzn-cdr.py

On Linux / macOS, the ``--add-data`` / ``--add-binary`` separators are ``:``
instead of ``;``.

Copyright (c) 2026 Merecete
Released under the MIT License. See LICENSE file for details.
"""
import os
import sys
import json
import shutil
import queue
import wave
import threading
import time
import subprocess
import traceback
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import List, Optional, Tuple
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk

APP_NAME = "XZN-CDR"
APP_LONG_NAME = "Xenon-Zones CD Ripper"
VERSION = "1.0.5"
SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".xzn_cdr_settings.json")

ICON_FILENAME = "xzn.ico"
LOGO_FILENAME = "xzn-logo.png"
TASKBAR_APP_ID = "com.katie.xzn.cdripper.1"

IS_WINDOWS = (sys.platform == "win32")
IS_MAC = (sys.platform == "darwin")


# --- Log file ------------------------------------------------------------
# Same approach as Xenon-Zones: everything printed (status lines,
# ffmpeg errors, tracebacks) is mirrored into xzn-cdr.log, so problems
# can be diagnosed in the windowed release builds without a console.
LOG_MAX_BYTES = 1_000_000
LOG_PATH = None
_LOG_LOCK = threading.Lock()


def _get_app_data_dir():
    """Per-user data directory shared with Xenon-Zones."""
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif IS_MAC:
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    app_dir = os.path.join(base, "Xenon-Zones")
    try:
        os.makedirs(app_dir, exist_ok=True)
    except Exception as e:
        print(f"[XZN-CDR] Could not create app data dir '{app_dir}': {e}")
        return None
    return app_dir


class _LogTee:
    """Writes to the original stream (if any) and to the log file,
    prefixing each new line with a timestamp."""

    def __init__(self, stream, fh):
        self._stream = stream
        self._fh = fh
        self._line_start = True
        self.encoding = "utf-8"

    def write(self, s):
        if not s:
            return 0
        if self._stream is not None:
            try:
                self._stream.write(s)
            except Exception:
                pass
        with _LOG_LOCK:
            try:
                out = []
                for part in s.splitlines(keepends=True):
                    if self._line_start:
                        out.append(time.strftime("%Y-%m-%d %H:%M:%S  "))
                    out.append(part)
                    self._line_start = part.endswith("\n")
                self._fh.write("".join(out))
                self._fh.flush()
            except Exception:
                pass
        return len(s)

    def flush(self):
        for f in (self._stream, self._fh):
            try:
                if f is not None:
                    f.flush()
            except Exception:
                pass

    def isatty(self):
        return False


def setup_logging(log_dir):
    """Mirror stdout/stderr into <log_dir>/xzn-cdr.log (previous log kept
    as xzn-cdr.log.1 once it grows beyond LOG_MAX_BYTES)."""
    global LOG_PATH
    if not log_dir:
        return None
    path = os.path.join(log_dir, "xzn-cdr.log")
    try:
        if os.path.isfile(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            os.replace(path, path + ".1")
        fh = open(path, "a", encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"[XZN-CDR] Could not open log file '{path}': {e}")
        return None
    fh.write("\n")
    sys.stdout = _LogTee(sys.stdout, fh)
    sys.stderr = _LogTee(sys.stderr, fh)
    LOG_PATH = path
    return path


_SWALLOWED_SEEN = set()


def _log_swallowed():
    """For 'except Exception:' blocks that deliberately ignore an error:
    logs it once per code location."""
    et, ev, tb = sys.exc_info()
    if et is None or tb is None:
        return
    code = tb.tb_frame.f_code
    key = (code.co_filename, tb.tb_lineno, et.__name__)
    if key in _SWALLOWED_SEEN or len(_SWALLOWED_SEEN) > 500:
        return
    _SWALLOWED_SEEN.add(key)
    msg = str(ev).strip().replace("\n", " ")[:200]
    print(f"[XZN-CDR] (ignored) {et.__name__}: {msg} — in {code.co_name}(), line {tb.tb_lineno}")


def open_with_system(path):
    """Open a file or folder with the platform's default application."""
    try:
        if IS_WINDOWS:
            os.startfile(path)  # type: ignore[attr-defined]
        elif IS_MAC:
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        print(f"[XZN-CDR] Could not open '{path}': {e}")
IS_LINUX = not IS_WINDOWS and not IS_MAC

# --- Windows taskbar App-ID ---------------------------------------------
if IS_WINDOWS:
    try:
        import ctypes
    except Exception:
        ctypes = None
else:
    ctypes = None

# --- pycdio import (Linux only, soft-fail everywhere) -------------------
try:
    import cdio
    import pycdio
    _PYCDIO_AVAILABLE = True
except Exception:
    _PYCDIO_AVAILABLE = False
    cdio = None
    pycdio = None

# --- Audio CD constants -------------------------------------------------
AUDIO_SAMPLE_RATE = 44100
AUDIO_CHANNELS = 2
AUDIO_BYTES_PER_SAMPLE = 2
CD_SECTORS_PER_SECOND = 75
CD_FRAME_SIZE = 2352
LAST_TRACK_SAFETY_MARGIN = 0.5

CDPARANOIA_BIN = "cd-paranoia"
CDPARANOIA_PKG = "libcdio-paranoia"

# --- Bitrate → Vorbis quality mapping for OGG ---------------------------
OGG_QUALITY_MAP = {
    "128": "3",
    "192": "5",
    "256": "7",
    "320": "9",
}

# --- Full MIT license text ----------------------------------------------
MIT_LICENSE_TEXT = """MIT License

Copyright (c) 2026 Merecete

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


# ========================================================================
# Palette + helpers
# ========================================================================
DEFAULT_PALETTE = {
    "BG":        "#14121f",
    "PANEL":     "#1e1a30",
    "RAISED":    "#332c4d",
    "SUNKEN":    "#0a0812",
    "ACCENT":    "#b28dff",
    "ACCENT_HI": "#d9c4ff",
    "ACCENT_LO": "#5a4590",
    "CYAN":      "#8fd4ff",
    "GRAY":      "#9a94b0",
    "WHITE":     "#ece9f5",
    "ERR":       "#ff8fa3",
}

PRESET_COLORS = [
    ("Purple",    "#a066ff"),
    ("Blue",      "#7fb4ff"),
    ("Green",     "#7fe0a4"),
    ("Pink",      "#ff8fd0"),
    ("Amber",     "#ffb066"),
    ("Red",       "#ff8a8a"),
    ("Turquoise", "#6fe6d8"),
    ("Black",     "#101018"),
    ("White",     "#dfe3f0"),
]

FONT_UI   = ("Helvetica", 9)
FONT_UI_B = ("Helvetica", 9, "bold")
FONT_LCD  = ("Courier New", 13, "bold")
FONT_LG   = ("Courier New", 16, "bold")
FONT_BTN  = ("Courier New", 10, "bold")
FONT_TAG  = ("Helvetica", 11, "italic")


def resource_path(relative_path):
    if hasattr(sys, "_MEIPASS"):
        base_path = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def get_icon_path():
    p = resource_path(ICON_FILENAME)
    return p if os.path.isfile(p) else None


def find_ffmpeg() -> Optional[str]:
    exe_name = "ffmpeg.exe" if IS_WINDOWS else "ffmpeg"
    bundled = resource_path(os.path.join("ffmpeg", exe_name))
    if os.path.isfile(bundled):
        return bundled
    
    found = shutil.which("ffmpeg")
    if found:
        return found
        
    if IS_MAC:
        macos_paths = [
            "/opt/homebrew/bin/ffmpeg",
            "/usr/local/bin/ffmpeg"
        ]
        for path in macos_paths:
            if os.path.isfile(path):
                return path

    return None


def find_ffprobe() -> Optional[str]:
    exe_name = "ffprobe.exe" if IS_WINDOWS else "ffprobe"
    bundled = resource_path(os.path.join("ffmpeg", exe_name))
    if os.path.isfile(bundled):
        return bundled
    return shutil.which("ffprobe")


def find_cdparanoia() -> Optional[str]:
    found = shutil.which(CDPARANOIA_BIN)
    if found:
        return found
    
    macos_paths = [
        "/opt/homebrew/bin/cd-paranoia",
        "/usr/local/bin/cd-paranoia"
    ]
    for path in macos_paths:
        if os.path.isfile(path):
            return path
            
    return None


def find_drutil() -> Optional[str]:
    return shutil.which("drutil") or "/usr/bin/drutil"


def set_taskbar_app_id():
    if not IS_WINDOWS or ctypes is None:
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(TASKBAR_APP_ID)
    except Exception as e:
        print(f"[XZN-CDR] Taskbar App-ID failed: {e}")


def apply_window_icon(window):
    icon_path = get_icon_path()
    if not icon_path:
        return
    if IS_WINDOWS:
        try:
            window.iconbitmap(icon_path)
            return
        except Exception:
            _log_swallowed()
    try:
        img = tk.PhotoImage(file=icon_path)
        window.tk.call("wm", "iconphoto", window._w, img)
        window._icon_photo_ref = img
    except Exception as e:
        print(f"[XZN-CDR] iconphoto failed: {e}")


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*[max(0, min(255, int(c))) for c in rgb])


def mix(c1, c2, t):
    r1, g1, b1 = hex_to_rgb(c1)
    r2, g2, b2 = hex_to_rgb(c2)
    return rgb_to_hex((r1 + (r2 - r1) * t,
                       g1 + (g2 - g1) * t,
                       b1 + (b2 - b1) * t))


def darken(hex_color, factor=0.7):
    r, g, b = hex_to_rgb(hex_color)
    return rgb_to_hex((r * factor, g * factor, b * factor))


def lighten(hex_color, factor=0.4):
    r, g, b = hex_to_rgb(hex_color)
    return rgb_to_hex((r + (255 - r) * factor,
                       g + (255 - g) * factor,
                       b + (255 - b) * factor))


def luminance(hex_color):
    r, g, b = hex_to_rgb(hex_color)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def build_palette(accent_hex):
    base_bg = DEFAULT_PALETTE["BG"]
    base_panel = DEFAULT_PALETTE["PANEL"]
    lum = luminance(accent_hex)
    is_dark = lum < 0.15
    is_light = lum > 0.85

    if is_dark:
        bg_base      = base_bg
        panel_base   = base_panel
        raised_base  = DEFAULT_PALETTE["RAISED"]
        sunken_base  = DEFAULT_PALETTE["SUNKEN"]
        accent       = "#e8e6f0"
        accent_hi    = "#ffffff"
        accent_lo    = "#5c2b9c"
        tint         = "#5c2b9c"
    elif is_light:
        bg_base      = "#f2f1f6"
        panel_base   = "#e8e6ef"
        raised_base  = "#dcd9e6"
        sunken_base  = "#ffffff"
        accent       = "#3a2a6b"
        accent_hi    = "#1a1240"
        accent_lo    = "#a09bd4"
        tint         = "#a09bd4"
    else:
        tint = accent_hex
        bg_base      = mix(base_bg, tint, 0.06)
        panel_base   = mix(base_panel, tint, 0.10)
        raised_base  = mix(base_panel, tint, 0.26)
        sunken_base  = darken(mix(base_bg, tint, 0.03), 0.70)
        accent       = accent_hex
        accent_hi    = lighten(accent, 0.45)
        accent_lo    = darken(accent, 0.62)

    return {
        "BG":        bg_base,
        "PANEL":     panel_base,
        "RAISED":    raised_base,
        "SUNKEN":    sunken_base,
        "ACCENT":    accent,
        "ACCENT_HI": accent_hi,
        "ACCENT_LO": accent_lo,
        "CYAN":      lighten(accent, 0.55) if not is_light else "#3a5a8a",
        "GRAY":      "#9a94b0" if not is_light else "#6a6478",
        "WHITE":     "#ece9f5" if not is_light else "#1a1a2a",
        "ERR":       "#ff8fa3",
    }


def make_flat_button(parent, text, cmd, palette, width=None,
                     bg=None, fg=None, font=None, padx=14, pady=4):
    bg_color = bg if bg is not None else palette["RAISED"]
    fg_color = fg if fg is not None else palette["WHITE"]
    font_use = font if font is not None else FONT_UI_B

    if IS_MAC or IS_LINUX:
        lbl = tk.Label(parent, text=text, bg=bg_color, fg=fg_color,
                       font=font_use, padx=padx, pady=pady,
                       relief="raised", bd=1, cursor="hand2")
        lbl.bind("<Button-1>", lambda e: cmd())
        lbl.bind("<Enter>", lambda e: lbl.config(bg=lighten(bg_color, 0.15)))
        lbl.bind("<Leave>", lambda e: lbl.config(bg=bg_color))
        return lbl
    else:
        kw = dict(text=text, command=cmd, bg=bg_color, fg=fg_color,
                  activebackground=lighten(bg_color, 0.15),
                  activeforeground=fg_color,
                  relief="raised", bd=1, font=font_use,
                  padx=padx, pady=pady, takefocus=0)
        if width is not None:
            kw["width"] = width
        return tk.Button(parent, **kw)


def _bind_scale_wheel(scale, callback, lo=-12.0, hi=12.0, step=0.5, round_to=0.5):
    """
    Attach mouse-wheel control to a tk.Scale.

    round_to controls snapping precision:
        - 0.5 for dB sliders (EQ bands, preamp)  → half-dB steps
        - 1.0 for integer sliders (volume, balance, alpha) → whole steps
        - 0   disables snapping entirely

    Important: tk.Scale.set() does NOT fire the widget's `command`
    callback, so we call it manually. Without this, the value would
    change visually but neither the app nor the persistence layer
    would ever hear about it.
    """
    def _round_val(v):
        if round_to <= 0:
            return v
        return round(v / round_to) * round_to

    def _on_wheel(event):
        # Linux uses Button-4/5, Windows/macOS use event.delta
        if getattr(event, "num", None) == 4:
            delta = +1
        elif getattr(event, "num", None) == 5:
            delta = -1
        else:
            delta = +1 if getattr(event, "delta", 0) > 0 else -1

        try:
            current = float(scale.get())
        except Exception:
            return "break"

        new_val = _round_val(current + delta * step)
        new_val = max(lo, min(hi, new_val))

        if new_val == current:
            return "break"

        try:
            scale.set(new_val)
        except Exception:
            return "break"

        try:
            callback(new_val)
        except Exception as e:
            print(f"[XZN-CDR] wheel callback failed: {e}")

        return "break"

    scale.bind("<MouseWheel>", _on_wheel)   # Windows / macOS
    scale.bind("<Button-4>", _on_wheel)     # Linux scroll up
    scale.bind("<Button-5>", _on_wheel)     # Linux scroll down


def _detect_windows_cd_drives() -> List[str]:
    result = []
    if not IS_WINDOWS or ctypes is None:
        return result
    try:
        kernel32 = ctypes.windll.kernel32
        DRIVE_CDROM = 5
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            root = f"{letter}:\\"
            try:
                dtype = kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
            except Exception:
                dtype = 0
            if dtype == DRIVE_CDROM:
                result.append(f"{letter}:")
    except Exception as e:
        print(f"[XZN-CDR] Optical drive detection failed: {e}")
    return result


def _suggest_default_drive() -> Optional[str]:
    if IS_WINDOWS:
        drives = _detect_windows_cd_drives()
        return drives[0] if drives else None

    if IS_MAC:
        for dev in ("/dev/disk2", "/dev/disk4", "/dev/disk1", "/dev/disk3"):
            if os.path.exists(dev):
                return dev
        return "/dev/disk2"

    for dev in ("/dev/cdrom", "/dev/sr0", "/dev/cdrom0", "/dev/dvd"):
        if os.path.exists(dev):
            return dev
    return "/dev/cdrom"


def load_settings():
    defaults = {
        "accent": DEFAULT_PALETTE["ACCENT"],
        "alpha": 100,
        "format": "MP3",
        "bitrate": "320",
        "outdir": os.path.join(os.path.expanduser("~"), "Music", "XZN-Rips"),
        "drive": _suggest_default_drive() or "",
        "eject": True,
    }
    try:
        if os.path.isfile(SETTINGS_PATH):
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    if k in defaults:
                        defaults[k] = v
    except Exception as e:
        print(f"[XZN-CDR] Failed to load settings: {e}")

    drv = defaults.get("drive") or ""
    if IS_WINDOWS and drv.startswith("/"):
        defaults["drive"] = _suggest_default_drive() or ""
    elif not IS_WINDOWS and len(drv) == 2 and drv.endswith(":"):
        defaults["drive"] = _suggest_default_drive() or ""

    return defaults


def save_settings(state):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        print(f"[XZN-CDR] Settings saved -> {SETTINGS_PATH}")
    except Exception as e:
        print(f"[XZN-CDR] Failed to save settings: {e}")


def fmt_time(seconds):
    seconds = int(seconds or 0)
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ========================================================================
# Cancellation helpers
# ========================================================================
class CancelledError(Exception):
    pass


def _kill_process(proc):
    try:
        if proc.stderr is not None:
            proc.stderr.close()
    except Exception:
        _log_swallowed()
    try:
        proc.terminate()
    except Exception:
        _log_swallowed()
    try:
        proc.wait(timeout=2)
    except Exception:
        try:
            proc.kill()
        except Exception:
            _log_swallowed()
        try:
            proc.wait(timeout=1)
        except Exception:
            _log_swallowed()


def _safe_unlink(path):
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except Exception:
        _log_swallowed()


# ========================================================================
# CD info: MusicBrainz lookup, file names, tags, ReplayGain
# ========================================================================
# A disc is identified by its table of contents (TOC): the start sector
# of every track plus the lead-out. From that, the MusicBrainz disc ID is
# computed and looked up in the free MusicBrainz database, which returns
# artist, album and track titles. Only the disc ID / TOC is sent.
#
# TOC format used throughout this file:
#   {"first": 1, "last": 12, "offsets": {1: 0, 2: 18055, ...}, "leadout": 225422}
# All sector numbers are LSNs (track 1 normally starts at 0); the 150
# sector lead-in that MusicBrainz expects is added when building the ID.

MB_LEAD_IN = 150
MB_USER_AGENT = f"XZN-CDR/{VERSION} ( https://github.com/Merecete/XZN )"
MB_URL = "https://musicbrainz.org/ws/2/discid/{disc_id}?toc={toc}&inc=recordings+artist-credits&fmt=json"


def musicbrainz_disc_id(toc: dict) -> str:
    """MusicBrainz disc ID (28 chars) for a TOC, per
    https://musicbrainz.org/doc/Disc_ID_Calculation"""
    import hashlib
    import base64
    first, last = toc["first"], toc["last"]
    s = "%02X%02X%08X" % (first, last, toc["leadout"] + MB_LEAD_IN)
    for n in range(1, 100):
        off = toc["offsets"].get(n)
        s += "%08X" % (off + MB_LEAD_IN if off is not None else 0)
    digest = hashlib.sha1(s.encode("ascii")).digest()
    return (base64.b64encode(digest).decode("ascii")
            .replace("+", ".").replace("/", "_").replace("=", "-"))


def musicbrainz_toc_param(toc: dict) -> str:
    """'first+last+leadout+offset1+…' — lets MusicBrainz find the disc
    by track lengths even when this exact disc ID isn't registered."""
    parts = [toc["first"], toc["last"], toc["leadout"] + MB_LEAD_IN]
    parts += [toc["offsets"][n] + MB_LEAD_IN
              for n in range(toc["first"], toc["last"] + 1) if n in toc["offsets"]]
    return "+".join(str(p) for p in parts)


def _artist_credit(ac) -> str:
    return "".join(f"{c.get('name', '')}{c.get('joinphrase', '')}"
                   for c in (ac or []) if isinstance(c, dict)).strip()


def _ssl_context():
    """Certificates for HTTPS. Frozen builds (especially on macOS) may not
    find the system store, so prefer certifi when it is bundled."""
    import ssl
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def parse_musicbrainz(data: dict, disc_id: str, n_tracks: int) -> Optional[dict]:
    """Turn a MusicBrainz discid response into
    {"artist", "album", "date", "disc", "tracks": {position: {"title", "artist"}}}."""
    if not isinstance(data, dict):
        return None

    # Unregistered discs sometimes exist as a simple "CD stub".
    if data.get("tracks") and data.get("title") and not data.get("releases"):
        album_artist = data.get("artist") or ""
        tracks = {}
        for i, t in enumerate(data["tracks"], start=1):
            tracks[i] = {"title": t.get("title") or "", "artist": t.get("artist") or album_artist}
        return {"artist": album_artist, "album": data.get("title") or "",
                "date": "", "disc": None, "tracks": tracks}

    best = None
    for rel in data.get("releases") or []:
        media = rel.get("media") or []
        for medium in media:
            discs = [d.get("id") for d in medium.get("discs") or []]
            score = 0
            if disc_id in discs:
                score += 2
            if (medium.get("track-count") or len(medium.get("tracks") or [])) == n_tracks:
                score += 1
            if medium.get("tracks"):
                score += 1
            if best is None or score > best[0]:
                best = (score, rel, medium, len(media))
    if best is None:
        return None
    _, rel, medium, n_media = best

    album_artist = _artist_credit(rel.get("artist-credit"))
    tracks = {}
    for t in medium.get("tracks") or []:
        pos = t.get("position")
        if not isinstance(pos, int):
            continue
        rec = t.get("recording") or {}
        tracks[pos] = {
            "title": t.get("title") or rec.get("title") or "",
            "artist": (_artist_credit(t.get("artist-credit"))
                       or _artist_credit(rec.get("artist-credit")) or album_artist),
        }
    disc = None
    if n_media > 1 and isinstance(medium.get("position"), int):
        disc = f"{medium['position']}/{n_media}"
    return {"artist": album_artist, "album": rel.get("title") or "",
            "date": (rel.get("date") or "")[:4], "disc": disc, "tracks": tracks}


def lookup_cd_info(toc: dict, timeout: float = 12.0) -> Optional[dict]:
    """Ask MusicBrainz about the disc. Returns parsed info, or None if the
    disc is unknown or the lookup failed (no internet, timeout, …)."""
    import urllib.request
    import urllib.error
    disc_id = musicbrainz_disc_id(toc)
    url = MB_URL.format(disc_id=disc_id, toc=musicbrainz_toc_param(toc))
    print(f"[XZN-CDR] MusicBrainz lookup: disc ID {disc_id}")
    req = urllib.request.Request(url, headers={"User-Agent": MB_USER_AGENT,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("[XZN-CDR] MusicBrainz: disc not found")
        else:
            print(f"[XZN-CDR] MusicBrainz lookup failed: HTTP {e.code}")
        return None
    except Exception as e:
        print(f"[XZN-CDR] MusicBrainz lookup failed: {e}")
        return None
    n_tracks = len(toc["offsets"])
    info = parse_musicbrainz(data, disc_id, n_tracks)
    if info:
        print(f"[XZN-CDR] MusicBrainz: {info['artist']} – {info['album']} "
              f"({len(info['tracks'])} titles)")
    return info


_BAD_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str, max_len: int = 120) -> str:
    """Make a string usable as a file/folder name on all platforms."""
    name = _BAD_FILENAME_CHARS.sub("_", name or "").strip().rstrip(". ")
    if len(name) > max_len:
        name = name[:max_len].rstrip(". ")
    return name or "_"


def measure_replaygain(ffmpeg: str, path: str) -> Optional[Tuple[float, float]]:
    """(track_gain_dB, track_peak) via FFmpeg's replaygain filter."""
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostats", "-i", path,
             "-af", "replaygain", "-f", "null", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            timeout=600, creationflags=0x08000000 if IS_WINDOWS else 0)
    except Exception as e:
        print(f"[XZN-CDR] ReplayGain measurement failed: {e}")
        return None
    g = re.search(r"track_gain\s*=\s*([-+]?\d+(?:\.\d+)?)\s*dB", proc.stderr or "")
    pk = re.search(r"track_peak\s*=\s*(\d+(?:\.\d+)?)", proc.stderr or "")
    if not g:
        print("[XZN-CDR] ReplayGain: no result from ffmpeg")
        return None
    return float(g.group(1)), (float(pk.group(1)) if pk else 1.0)


def finalize_track(ffmpeg: str, path: str, fmt: str, tags: dict) -> None:
    """After ripping: measure ReplayGain (so Xenon-Zones can level the
    volume) and write all tags in one lossless remux. Never raises;
    on failure the ripped file simply stays untagged."""
    tags = {k: str(v) for k, v in tags.items() if v not in (None, "")}
    if fmt != "WAV":
        rg = measure_replaygain(ffmpeg, path)
        if rg:
            tags["REPLAYGAIN_TRACK_GAIN"] = f"{rg[0]:+.2f} dB"
            tags["REPLAYGAIN_TRACK_PEAK"] = f"{rg[1]:.6f}"
    if not tags:
        return
    base, ext = os.path.splitext(path)
    tmp = f"{base}.tagging{ext}"
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", path,
           "-map", "0", "-c", "copy"]
    for k, v in tags.items():
        cmd += ["-metadata", f"{k}={v}"]
    if fmt == "MP3":
        cmd += ["-id3v2_version", "3"]   # best compatibility (Explorer, car radios)
    cmd.append(tmp)
    try:
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              text=True, timeout=300,
                              creationflags=0x08000000 if IS_WINDOWS else 0)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or "").strip()[-300:])
        os.replace(tmp, path)
    except Exception as e:
        _safe_unlink(tmp)
        print(f"[XZN-CDR] Tagging failed for {os.path.basename(path)}: {e}")


# ========================================================================
# CD backends
# ========================================================================
@dataclass
class RipJob:
    tracks: List[int]
    track_durations: List[float]
    track_starts: List[float]
    outdir: str
    fmt: str
    bitrate: str
    drive: str
    eject: bool
    ffmpeg: Optional[str]
    cancel_event: Optional[threading.Event] = None
    # MusicBrainz result (see lookup_cd_info) or None → track_XX names
    info: Optional[dict] = None
    first_track: int = 1

    def extension(self) -> str:
        return {
            "MP3":  ".mp3",
            "OGG":  ".ogg",
            "FLAC": ".flac",
            "WAV":  ".wav",
        }.get(self.fmt, ".mp3")

    def is_cancelled(self) -> bool:
        return self.cancel_event is not None and self.cancel_event.is_set()


def encode_args_for_ffmpeg(ffmpeg: str, fmt: str, bitrate: str,
                           wav_in: str, out: str) -> List[str]:
    args = [ffmpeg, "-y", "-i", wav_in]
    if fmt == "MP3":
        args += ["-codec:a", "libmp3lame", "-b:a", f"{bitrate}k"]
    elif fmt == "OGG":
        q = OGG_QUALITY_MAP.get(str(bitrate), "6")
        args += ["-codec:a", "libvorbis", "-qscale:a", q]
    elif fmt == "FLAC":
        args += ["-codec:a", "flac", "-compression_level", "5"]
    elif fmt == "WAV":
        args += ["-codec:a", "pcm_s16le"]
    args += [out]
    return args


class CDBackend:
    name = "abstract"
    # Filled by list_tracks(): exact TOC for the MusicBrainz lookup (see
    # "CD info" above) and each track's start time in seconds.
    toc: Optional[dict] = None
    starts: Optional[dict] = None

    def is_available(self) -> bool:
        return False

    def availability_hint(self) -> str:
        return ""

    def list_tracks(self, drive: str) -> List[Tuple[int, float]]:
        raise NotImplementedError

    def rip_track(self, drive: str, track_no: int, out_path: str,
                  duration_sec: float,
                  start_sec: float = 0.0,
                  bitrate: str = "320",
                  progress_callback=None,
                  cancel_event=None,
                  is_last_track: bool = False) -> None:
        raise NotImplementedError

    def eject(self, drive: str, ui_queue: "queue.Queue") -> None:
        pass


class FFmpegBackend(CDBackend):
    name = "ffmpeg-libcdio"

    def __init__(self, ffmpeg: Optional[str], ffprobe: Optional[str]):
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    def is_available(self) -> bool:
        return bool(self.ffmpeg and self.ffprobe)

    def availability_hint(self) -> str:
        if not self.ffmpeg:
            return "ffmpeg not found."
        if not self.ffprobe:
            return "ffprobe not found (ships with ffmpeg full builds)."
        return ""

    def list_tracks(self, drive: str) -> List[Tuple[int, float]]:
        cmd = [
            self.ffprobe,
            "-f", "libcdio",
            "-i", drive,
            "-print_format", "json",
            "-show_chapters",
            "-loglevel", "error",
        ]
        try:
            probe = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("ffprobe timed out while reading the CD TOC.")

        if probe.returncode != 0:
            err = (probe.stderr or "").strip()
            raise RuntimeError(f"ffprobe failed: {err}")

        try:
            data = json.loads(probe.stdout or "{}")
        except Exception as e:
            raise RuntimeError(f"Could not parse ffprobe output as JSON: {e}")

        chapters = data.get("chapters", [])
        if not chapters:
            raise RuntimeError(
                "No chapters found on disc. Make sure an audio CD is inserted "
                "and the drive letter is correct."
            )

        tracks: List[Tuple[int, float]] = []
        offsets, starts = {}, {}
        leadout = 0
        for idx, ch in enumerate(chapters):
            track_no = idx + 1
            try:
                title = (ch.get("tags", {}) or {}).get("title", "")
                m = re.search(r"(\d+)", str(title))
                if m:
                    track_no = int(m.group(1))
            except Exception:
                _log_swallowed()

            try:
                start = float(ch.get("start_time", 0.0))
                end = float(ch.get("end_time", 0.0))
            except Exception:
                continue

            # Exact positions: chapters carry integer start/end in their
            # time base (samples), which converts losslessly to sectors.
            try:
                tb = Fraction(str(ch.get("time_base") or "1/1"))
                start = float(int(ch["start"]) * tb)
                end = float(int(ch["end"]) * tb)
            except Exception:
                _log_swallowed()

            dur = max(0.0, end - start)
            tracks.append((track_no, dur))
            offsets[track_no] = int(round(start * CD_SECTORS_PER_SECOND))
            starts[track_no] = start
            leadout = max(leadout, int(round(end * CD_SECTORS_PER_SECOND)))

        self.starts = starts
        self.toc = ({"first": min(offsets), "last": max(offsets),
                     "offsets": offsets, "leadout": leadout} if offsets else None)
        return tracks

    def rip_track(self, drive: str, track_no: int, out_path: str,
                  duration_sec: float,
                  start_sec: float = 0.0,
                  bitrate: str = "320",
                  progress_callback=None,
                  cancel_event=None,
                  is_last_track: bool = False) -> None:

        effective_duration = duration_sec
        if is_last_track:
            effective_duration = max(0.5, duration_sec - LAST_TRACK_SAFETY_MARGIN)
            if duration_sec > LAST_TRACK_SAFETY_MARGIN:
                print(f"[XZN-CDR] Last track: shortening by "
                      f"{LAST_TRACK_SAFETY_MARGIN:.2f}s to avoid end-of-disc hang")

        ext = os.path.splitext(out_path)[1].lower()
        if ext == ".mp3":
            codec_args = ["-c:a", "libmp3lame", "-b:a", f"{bitrate}k"]
        elif ext == ".ogg":
            q = OGG_QUALITY_MAP.get(str(bitrate), "6")
            codec_args = ["-c:a", "libvorbis", "-qscale:a", q]
        elif ext == ".flac":
            codec_args = ["-c:a", "flac", "-compression_level", "5"]
        elif ext == ".wav":
            codec_args = ["-c:a", "pcm_s16le"]
        else:
            codec_args = ["-c:a", "copy"]

        cmd = [
            self.ffmpeg,
            "-y",
            "-f", "libcdio",
            "-paranoia_mode", "full",
            "-ss", "0",
            "-i", drive,
            "-ss", str(start_sec),
            "-t", str(effective_duration),
            *codec_args,
            out_path,
        ]

        creationflags = 0x08000000 if IS_WINDOWS else 0

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1,
            creationflags=creationflags,
        )

        if proc.stderr is not None:
            for line in proc.stderr:
                if cancel_event is not None and cancel_event.is_set():
                    _kill_process(proc)
                    _safe_unlink(out_path)
                    raise CancelledError()

                line = line.strip()
                if not line:
                    continue
                m = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", line)
                if m and progress_callback and effective_duration > 0:
                    hh, mm, ss = int(m.group(1)), int(m.group(2)), float(m.group(3))
                    elapsed = hh * 3600 + mm * 60 + ss
                    pct = min(100, int((elapsed / effective_duration) * 100))
                    progress_callback(pct)

        proc.wait()

        if cancel_event is not None and cancel_event.is_set():
            _safe_unlink(out_path)
            raise CancelledError()

        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg failed to rip track {track_no} "
                f"(exit code {proc.returncode})"
            )

    def eject(self, drive: str, ui_queue: "queue.Queue") -> None:
        try:
            letter = drive.rstrip(":\\/")
            ps = (
                f"(New-Object -comObject Shell.Application)"
                f".NameSpace(17).ParseName('{letter}:').InvokeVerb('Eject')"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            ui_queue.put(("log", "Disc ejected."))
        except Exception as e:
            ui_queue.put(("log", f"Eject skipped: {e}"))


class PyCdioBackend(CDBackend):
    name = "pycdio"

    def is_available(self) -> bool:
        return _PYCDIO_AVAILABLE

    def availability_hint(self) -> str:
        if not _PYCDIO_AVAILABLE:
            return ("pycdio not installed — install libcdio + pycdio:\n"
                    "  sudo apt install libcdio-dev libcdio-cdda-dev python3-pycdio")
        return ""

    def list_tracks(self, drive: str) -> List[Tuple[int, float]]:
        if not _PYCDIO_AVAILABLE:
            raise RuntimeError("pycdio is not available.")

        tracks: List[Tuple[int, float]] = []
        cd = None
        try:
            cd = cdio.Device(source=drive)
            # pycdio's Device exposes the TOC methods directly; keep the
            # get_toc() path for wrappers that provide one.
            toc = cd.get_toc() if hasattr(cd, "get_toc") else cd
            if not toc:
                raise RuntimeError("No TOC available — disc may not be an audio CD.")

            first_track = toc.get_first_track()
            last_track = toc.get_last_track()
            if hasattr(first_track, "track"):     # pycdio Track objects
                first_track, last_track = first_track.track, last_track.track
            offsets = {}
            leadout = None

            for track_num in range(first_track, last_track + 1):
                track = toc.get_track(track_num)
                fmt = None
                try:
                    fmt = track.get_format()
                except Exception:
                    fmt = None
                if fmt is not None and str(fmt).lower() != "audio":
                    continue

                lsn_start = track.get_lsn()
                lsn_end = track.get_last_lsn()
                num_sectors = lsn_end - lsn_start + 1
                duration_sec = num_sectors / float(CD_SECTORS_PER_SECOND)
                tracks.append((track_num, duration_sec))
                offsets[track_num] = lsn_start
                leadout = lsn_end + 1

            # Enhanced CDs: a data session follows the audio, separated by
            # an 11400-sector gap that MusicBrainz excludes from the lead-out.
            if offsets and max(offsets) < last_track:
                try:
                    data_lsn = toc.get_track(max(offsets) + 1).get_lsn()
                    leadout = data_lsn - 11400
                except Exception:
                    _log_swallowed()
            self.starts = {n: o / float(CD_SECTORS_PER_SECOND) for n, o in offsets.items()}
            self.toc = ({"first": min(offsets), "last": max(offsets),
                         "offsets": offsets, "leadout": leadout} if offsets else None)
            return tracks
        except Exception as e:
            raise RuntimeError(f"Failed to read CD TOC: {e}")
        finally:
            if cd is not None:
                try:
                    cd.close()
                except Exception:
                    _log_swallowed()

    def _read_track_pcm(self, drive: str, track_num: int,
                        progress_callback=None,
                        cancel_event=None) -> bytes:
        cd = None
        try:
            cd = cdio.Device(source=drive)
            toc = cd.get_toc()
            track = toc.get_track(track_num)

            lsn_start = track.get_lsn()
            lsn_end = track.get_last_lsn()
            total_sectors = lsn_end - lsn_start + 1

            chunk_size = CD_SECTORS_PER_SECOND
            chunks: List[bytes] = []
            current_lsn = lsn_start

            while current_lsn <= lsn_end:
                if cancel_event is not None and cancel_event.is_set():
                    raise CancelledError()

                sectors_to_read = min(chunk_size, lsn_end - current_lsn + 1)
                try:
                    blocks, data = cd.read_sectors(
                        current_lsn,
                        pycdio.READ_MODE_AUDIO,
                        sectors_to_read,
                    )
                except Exception as e:
                    raise RuntimeError(
                        f"Failed to read sectors at LSN {current_lsn}: {e}"
                    )

                if blocks <= 0:
                    raise RuntimeError(
                        f"Read returned 0 blocks at LSN {current_lsn}."
                    )

                if isinstance(data, str):
                    raw_bytes = data.encode("utf-8", errors="surrogateescape")
                else:
                    raw_bytes = bytes(data)

                chunks.append(raw_bytes)
                current_lsn += blocks

                if progress_callback:
                    progress_callback(current_lsn - lsn_start, total_sectors)

            return b"".join(chunks)
        finally:
            if cd is not None:
                try:
                    cd.close()
                except Exception:
                    _log_swallowed()

    @staticmethod
    def _write_wav(path: str, pcm_data: bytes) -> None:
        with wave.open(path, "wb") as wf:
            wf.setnchannels(AUDIO_CHANNELS)
            wf.setsampwidth(AUDIO_BYTES_PER_SAMPLE)
            wf.setframerate(AUDIO_SAMPLE_RATE)
            wf.writeframes(pcm_data)

    def rip_track(self, drive: str, track_no: int, out_path: str,
                  duration_sec: float,
                  start_sec: float = 0.0,
                  bitrate: str = "320",
                  progress_callback=None,
                  cancel_event=None,
                  is_last_track: bool = False) -> None:
        if not _PYCDIO_AVAILABLE:
            raise RuntimeError("pycdio is not available.")

        fmt = self._infer_format(out_path)
        tmpdir = os.path.dirname(out_path) or "."
        wav_path = os.path.join(tmpdir, f".xzn_tmp_{track_no:02d}.wav")

        def pcm_progress(current, total):
            if progress_callback:
                pct = int((current / max(1, total)) * 50)
                progress_callback(pct)

        try:
            pcm_data = self._read_track_pcm(
                drive, track_no, pcm_progress, cancel_event
            )
        except CancelledError:
            _safe_unlink(wav_path)
            raise
        except Exception as e:
            raise RuntimeError(f"Failed to read track {track_no}: {e}")

        if cancel_event is not None and cancel_event.is_set():
            _safe_unlink(wav_path)
            raise CancelledError()

        if not pcm_data:
            raise RuntimeError(f"Track {track_no} contained no audio data.")

        self._write_wav(wav_path, pcm_data)
        if progress_callback:
            progress_callback(60)

        if fmt == "WAV":
            shutil.move(wav_path, out_path)
            return

        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            _safe_unlink(wav_path)
            raise RuntimeError("ffmpeg not found — required for MP3/OGG/FLAC.")

        args = encode_args_for_ffmpeg(ffmpeg, fmt, bitrate, wav_path, out_path)
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=0x08000000 if IS_WINDOWS else 0,
        )
        proc.wait()

        if cancel_event is not None and cancel_event.is_set():
            _kill_process(proc)
            _safe_unlink(out_path)
            _safe_unlink(wav_path)
            raise CancelledError()

        if proc.returncode != 0:
            err = ""
            try:
                if proc.stderr is not None:
                    err = "".join(proc.stderr.readlines())[-300:]
            except Exception:
                _log_swallowed()
            _safe_unlink(wav_path)
            raise RuntimeError(
                f"ffmpeg failed for track {track_no}: {err.strip()}"
            )

        _safe_unlink(wav_path)

    @staticmethod
    def _infer_format(out_path: str) -> str:
        ext = os.path.splitext(out_path)[1].lower()
        return {".mp3": "MP3", ".ogg": "OGG", ".flac": "FLAC", ".wav": "WAV"}.get(
            ext, "MP3"
        )

    def eject(self, drive: str, ui_queue: "queue.Queue") -> None:
        try:
            if shutil.which("eject"):
                subprocess.run(
                    ["eject", drive],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
                ui_queue.put(("log", "Disc ejected."))
            else:
                ui_queue.put(("log", "Eject skipped (no eject tool available)."))
        except Exception as e:
            ui_queue.put(("log", f"Eject skipped: {e}"))


class CdParanoiaBackend(CDBackend):
    name = "cd-paranoia"

    def __init__(self, ffmpeg: Optional[str]):
        self.ffmpeg = ffmpeg

    def is_available(self) -> bool:
        return bool(find_cdparanoia())

    def availability_hint(self) -> str:
        if not find_cdparanoia():
            return (f"{CDPARANOIA_BIN} not found — install with: "
                    f"brew install {CDPARANOIA_PKG}")
        return ""

    def list_tracks(self, drive: str) -> List[Tuple[int, float]]:
        cdparanoia = find_cdparanoia()
        if not cdparanoia:
            raise RuntimeError(
                f"{CDPARANOIA_BIN} not found. "
                f"Install it with: brew install {CDPARANOIA_PKG}"
            )

        cmd = [cdparanoia, "-Q", "-d", drive]
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{CDPARANOIA_BIN} -Q timed out.")

        out = result.stdout or ""
        tracks: List[Tuple[int, float]] = []
        offsets = {}
        leadout = 0
        # Standard cd-paranoia -Q layout:
        #   "  1.    18055 [04:00.55]        0 [00:00.00]    no   no  2"
        #   track.  length-in-sectors [mm:ss.ff]  begin-sector [..] …
        sector_pattern = re.compile(r"^\s*(\d+)\.\s+(\d+)\s+\[[\d:.]+\]\s+(\d+)\s+\[")
        pattern = re.compile(r"^\s*(\d+)\.\s+(\d+):(\d+):(\d+)")
        for line in out.splitlines():
            ms = sector_pattern.match(line)
            if ms:
                track_no, length, begin = (int(ms.group(1)), int(ms.group(2)),
                                           int(ms.group(3)))
                if length > 0:
                    tracks.append((track_no, length / float(CD_SECTORS_PER_SECOND)))
                    offsets[track_no] = begin
                    leadout = max(leadout, begin + length)
                continue
            m = pattern.match(line)
            if not m:
                continue
            try:
                track_no = int(m.group(1))
                mm = int(m.group(2))
                ss = int(m.group(3))
                ff = int(m.group(4))
            except Exception:
                continue
            dur = mm * 60 + ss + ff / 75.0
            if dur > 0:
                tracks.append((track_no, dur))

        self.toc = ({"first": min(offsets), "last": max(offsets),
                     "offsets": offsets, "leadout": leadout} if offsets else None)
        self.starts = {n: o / float(CD_SECTORS_PER_SECOND) for n, o in offsets.items()}

        if not tracks:
            if ("No CD" in out or "Unable to open" in out
                    or "not ready" in out or "No medium" in out):
                raise RuntimeError(
                    f"CD drive '{drive}' not ready or no audio CD detected."
                )
            raise RuntimeError(
                "Could not parse cd-paranoia output. Make sure an audio CD is "
                "inserted and the device path is correct.\n\n"
                f"cd-paranoia said:\n{out[-500:]}"
            )

        return tracks

    def rip_track(self, drive: str, track_no: int, out_path: str,
                  duration_sec: float,
                  start_sec: float = 0.0,
                  bitrate: str = "320",
                  progress_callback=None,
                  cancel_event=None,
                  is_last_track: bool = False) -> None:
        cdparanoia = find_cdparanoia()
        if not cdparanoia:
            raise RuntimeError(
                f"{CDPARANOIA_BIN} not found. "
                f"Install it with: brew install {CDPARANOIA_PKG}"
            )

        fmt = PyCdioBackend._infer_format(out_path)
        tmpdir = os.path.dirname(out_path) or "."
        wav_path = os.path.join(tmpdir, f".xzn_tmp_{track_no:02d}.wav")

        cmd = [cdparanoia, "-d", drive, "-w", wav_path, str(track_no)]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1,
        )

        if proc.stderr is not None:
            for line in proc.stderr:
                if cancel_event is not None and cancel_event.is_set():
                    _kill_process(proc)
                    _safe_unlink(wav_path)
                    raise CancelledError()

                line = line.rstrip()
                if not line:
                    continue
                m = re.search(r"\[(\d+):(\d+)\]", line)
                if m and progress_callback and duration_sec > 0:
                    mm = int(m.group(1))
                    ss = int(m.group(2))
                    elapsed = mm * 60 + ss
                    pct = min(50, int((elapsed / duration_sec) * 50))
                    progress_callback(pct)

        proc.wait()

        if cancel_event is not None and cancel_event.is_set():
            _safe_unlink(wav_path)
            raise CancelledError()

        if proc.returncode != 0:
            _safe_unlink(wav_path)
            raise RuntimeError(
                f"cd-paranoia failed for track {track_no} "
                f"(exit code {proc.returncode})."
            )

        if not os.path.isfile(wav_path):
            raise RuntimeError(
                f"cd-paranoia did not create '{wav_path}'. "
                "The CD may be copy-protected or unreadable."
            )

        if progress_callback:
            progress_callback(50)

        if fmt == "WAV":
            shutil.move(wav_path, out_path)
            return

        if not self.ffmpeg:
            _safe_unlink(wav_path)
            raise RuntimeError("ffmpeg not found — required for MP3/OGG/FLAC.")

        args = encode_args_for_ffmpeg(
            self.ffmpeg, fmt, bitrate, wav_path, out_path
        )
        enc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        enc.wait()

        if cancel_event is not None and cancel_event.is_set():
            _kill_process(enc)
            _safe_unlink(out_path)
            _safe_unlink(wav_path)
            raise CancelledError()

        if enc.returncode != 0:
            err = ""
            try:
                if enc.stderr is not None:
                    err = "".join(enc.stderr.readlines())[-300:]
            except Exception:
                _log_swallowed()
            _safe_unlink(wav_path)
            raise RuntimeError(
                f"ffmpeg failed for track {track_no}: {err.strip()}"
            )

        _safe_unlink(wav_path)

    def eject(self, drive: str, ui_queue: "queue.Queue") -> None:
        try:
            drutil = find_drutil()
            subprocess.run(
                [drutil, "tray", "eject"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            ui_queue.put(("log", "Disc ejected."))
        except Exception as e:
            ui_queue.put(("log", f"Eject skipped: {e}"))


def make_backend(ffmpeg: Optional[str], ffprobe: Optional[str]) -> CDBackend:
    if IS_WINDOWS:
        return FFmpegBackend(ffmpeg, ffprobe)
    if IS_MAC:
        return CdParanoiaBackend(ffmpeg)
    return PyCdioBackend()


def rip_worker(job: RipJob, backend: CDBackend, ui_queue: "queue.Queue"):
    info = job.info
    outdir = job.outdir
    if info and (info.get("artist") or info.get("album")):
        # One folder per album: "Artist - Album"
        outdir = os.path.join(outdir, safe_filename(
            f"{info.get('artist') or 'Unknown Artist'} - {info.get('album') or 'Unknown Album'}"))
    os.makedirs(outdir, exist_ok=True)
    n_disc_tracks = len(info["tracks"]) if info and info.get("tracks") else 0

    total = len(job.tracks)
    if total == 0:
        ui_queue.put(("error", "No tracks selected."))
        return

    ripped = 0
    try:
        for idx, (track_no, duration_sec, start_sec) in enumerate(
            zip(job.tracks, job.track_durations, job.track_starts), start=1
        ):
            if job.is_cancelled():
                ui_queue.put(("stopped", f"Stopped after {ripped} track(s)."))
                return

            meta = {}
            if info:
                meta = (info.get("tracks") or {}).get(track_no - job.first_track + 1) or {}
            if meta.get("title"):
                out_name = f"{track_no:02d} - {safe_filename(meta['title'])}{job.extension()}"
            else:
                out_name = f"track_{track_no:02d}{job.extension()}"
            out_path = os.path.join(outdir, out_name)

            label = f"Track {track_no:02d} ({idx}/{total})"
            ui_queue.put(("log", f"Ripping {label} …"))
            ui_queue.put(("progress", 0, label))

            def progress_cb(pct):
                ui_queue.put(("progress", pct, label))

            is_last = (idx == total)

            try:
                backend.rip_track(
                    job.drive,
                    track_no,
                    out_path,
                    duration_sec,
                    start_sec=start_sec,
                    bitrate=job.bitrate,
                    progress_callback=progress_cb,
                    cancel_event=job.cancel_event,
                    is_last_track=is_last,
                )
            except CancelledError:
                ui_queue.put(("stopped", f"Stopped after {ripped} track(s)."))
                return
            except Exception as e:
                raise RuntimeError(f"Failed to rip track {track_no}: {e}")

            # Tags + ReplayGain (lossless remux of the finished file)
            if job.ffmpeg:
                ui_queue.put(("progress", 100, f"{label} – tagging"))
                tags = {"track": f"{track_no}/{n_disc_tracks}" if n_disc_tracks else str(track_no)}
                if info:
                    tags.update({
                        "title": meta.get("title"),
                        "artist": meta.get("artist") or info.get("artist"),
                        "album": info.get("album"),
                        "album_artist": info.get("artist"),
                        "date": info.get("date"),
                        "disc": info.get("disc"),
                    })
                finalize_track(job.ffmpeg, out_path, job.fmt, tags)

            ripped += 1
            ui_queue.put(("progress", 100, label))
            ui_queue.put(("log", f"  -> {out_name}"))

        if job.is_cancelled():
            ui_queue.put(("stopped", f"Stopped after {ripped} track(s)."))
            return

        if job.eject:
            backend.eject(job.drive, ui_queue)

        ui_queue.put(("done", f"Finished ripping {total} track(s)."))

    except Exception as e:
        traceback.print_exc()
        ui_queue.put(("error", str(e)))


class FlatOptionMenu:
    def __init__(self, parent, var, options, palette, width=8, on_change=None):
        self.palette = palette
        self.var = var
        self.options = list(options)
        self.on_change = on_change
        self._popup = None

        bg_color = palette["RAISED"]
        fg_color = palette["WHITE"]

        self.btn = tk.Label(
            parent,
            text=var.get(),
            bg=bg_color, fg=fg_color,
            font=FONT_UI_B, padx=8, pady=3,
            relief="raised", bd=1, cursor="hand2",
            width=width, anchor="w",
        )
        self.btn.bind("<Button-1>", self._open)
        self.btn.bind("<Enter>", lambda e: self.btn.config(bg=lighten(bg_color, 0.15)))
        self.btn.bind("<Leave>", lambda e: self.btn.config(bg=bg_color))

        try:
            self.var.trace_add("write", lambda *a: self._update_text())
        except Exception:
            try:
                self.var.trace("w", lambda *a: self._update_text())
            except Exception:
                _log_swallowed()

    def _update_text(self):
        try:
            self.btn.config(text=self.var.get())
        except Exception:
            _log_swallowed()

    def grid(self, **kwargs):
        self.btn.grid(**kwargs)
        return self

    def pack(self, **kwargs):
        self.btn.pack(**kwargs)
        return self

    def _open(self, event=None):
        if self._popup is not None:
            return
        p = self.palette

        self._popup = tk.Toplevel(self.btn)
        self._popup.overrideredirect(True)
        self._popup.configure(bg=p["ACCENT_LO"])
        try:
            self._popup.attributes("-topmost", True)
        except Exception:
            _log_swallowed()

        inner = tk.Frame(self._popup, bg=p["RAISED"], bd=0, highlightthickness=0)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        for opt in self.options:
            item = tk.Label(
                inner, text=opt, font=FONT_UI,
                bg=p["RAISED"], fg=p["WHITE"],
                anchor="w", padx=14, pady=4, cursor="hand2",
            )
            item.pack(fill="x")
            item.bind("<Enter>", lambda e, w=item: w.config(bg=p["ACCENT"], fg=p["WHITE"]))
            item.bind("<Leave>", lambda e, w=item: w.config(bg=p["RAISED"], fg=p["WHITE"]))
            item.bind("<Button-1>", lambda e, o=opt: self._select(o))

        x = self.btn.winfo_rootx()
        y = self.btn.winfo_rooty() + self.btn.winfo_height()
        self._popup.geometry(f"+{x}+{y}")

        try:
            self._popup.focus_set()
        except Exception:
            _log_swallowed()

        self._popup.bind("<FocusOut>", lambda e: self._close())
        self._popup.bind("<Escape>", lambda e: self._close())
        self.btn.bind_all("<Button-1>", self._on_global_click, add="+")

    def _on_global_click(self, event):
        if self._popup is None:
            return
        try:
            w = self.btn.winfo_containing(event.x_root, event.y_root)
        except Exception:
            w = None
        p = w
        while p is not None:
            if p is self._popup:
                return
            try:
                p = p.master
            except Exception:
                p = None
        self._close()

    def _select(self, value):
        try:
            self.var.set(value)
        except Exception:
            _log_swallowed()
        if self.on_change is not None:
            try:
                self.on_change(value)
            except Exception:
                _log_swallowed()
        self._close()

    def _close(self):
        if self._popup is None:
            return
        try:
            self.btn.unbind_all("<Button-1>")
        except Exception:
            _log_swallowed()
        try:
            self._popup.destroy()
        except Exception:
            _log_swallowed()
        self._popup = None


def show_license_window(parent, palette, app=None):
    # Only one license window: a second click on "License" closes it.
    if app is not None:
        old = getattr(app, "_license_win", None)
        try:
            if old is not None and old.winfo_exists():
                if old in app._extra_alpha_windows:
                    app._extra_alpha_windows.remove(old)
                old.destroy()
                app._license_win = None
                return None
        except Exception:
            _log_swallowed()
    p = palette
    win = tk.Toplevel(parent)
    win.title("License – XZN-CDR")
    win.configure(bg=p["PANEL"])
    win.geometry("620x520")
    win.minsize(400, 300)
    win.transient(parent)
    apply_window_icon(win)

    if app is not None:
        try:
            app._attach_macos_menu(win)
        except Exception:
            _log_swallowed()

    def _close():
        if app is not None:
            try:
                if win in app._extra_alpha_windows:
                    app._extra_alpha_windows.remove(win)
            except Exception:
                _log_swallowed()
        try:
            win.destroy()
        except Exception:
            _log_swallowed()

    win.protocol("WM_DELETE_WINDOW", _close)

    footer = tk.Frame(win, bg=p["PANEL"], padx=20, pady=14)
    footer.pack(side="bottom", fill="x")
    make_flat_button(footer, "Close", _close, palette=p,
                     padx=14, pady=4).pack(side="right")

    header = tk.Frame(win, bg=p["PANEL"], padx=20, pady=14)
    header.pack(side="top", fill="x")
    tk.Label(header, text="MIT LICENSE",
             font=("Helvetica", 12, "bold"),
             bg=p["PANEL"], fg=p["ACCENT"]).pack(anchor="w")

    tk.Frame(win, bg=p["ACCENT_LO"], height=1).pack(
        side="top", fill="x", padx=20
    )

    body = tk.Frame(win, bg=p["PANEL"], padx=20, pady=14)
    body.pack(side="top", fill="both", expand=True)

    text_wrap = tk.Frame(body, bg=p["SUNKEN"], bd=0, highlightthickness=0)
    text_wrap.pack(fill="both", expand=True)

    txt = tk.Text(
        text_wrap,
        wrap="word",
        bg=p["SUNKEN"], fg=p["WHITE"],
        insertbackground=p["WHITE"],
        selectbackground=p["ACCENT"],
        selectforeground=p["WHITE"],
        relief="flat", bd=0, highlightthickness=0,
        font=("Courier New", 9),
        padx=14, pady=12,
    )
    txt.pack(side="left", fill="both", expand=True)

    sb = tk.Scrollbar(text_wrap, command=txt.yview,
                      bg=p["RAISED"], troughcolor=p["SUNKEN"],
                      activebackground=p["ACCENT"],
                      relief="flat", bd=0, highlightthickness=0,
                      width=12)
    sb.pack(side="right", fill="y")
    txt.config(yscrollcommand=sb.set)

    txt.insert("1.0", MIT_LICENSE_TEXT)
    txt.config(state="disabled")

    win.bind("<Escape>", lambda e: _close())

    try:
        win.update_idletasks()
        mx = parent.winfo_rootx() + parent.winfo_width() // 2
        my = parent.winfo_rooty() + parent.winfo_height() // 2
        w = win.winfo_width()
        h = win.winfo_height()
        win.geometry(f"+{mx - w // 2}+{my - h // 2}")
    except Exception:
        _log_swallowed()

    if app is not None:
        try:
            app._extra_alpha_windows.append(win)
        except Exception:
            _log_swallowed()
        try:
            win.after(30, app._apply_alpha_to_windows)
        except Exception:
            _log_swallowed()
        app._license_win = win

    try:
        win.focus_set()
    except Exception:
        _log_swallowed()


class ThemeWindow:
    def __init__(self, master, app):
        self.app = app
        self.master = master
        self.win = tk.Toplevel(master)
        self.win.title("Color Scheme")
        self.win.configure(bg=app.palette["PANEL"])
        self.win.resizable(False, False)
        self.win.transient(master)
        apply_window_icon(self.win)

        try:
            app._attach_macos_menu(self.win)
        except Exception:
            _log_swallowed()

        p = app.palette
        frame = tk.Frame(self.win, bg=p["PANEL"], padx=18, pady=16)
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text="Accent Color",
                 font=("Helvetica", 12, "bold"),
                 bg=p["PANEL"], fg=p["ACCENT"]).pack(anchor="w")

        grid = tk.Frame(frame, bg=p["PANEL"])
        grid.pack(fill="x")

        cols = 3
        for i, (name, color) in enumerate(PRESET_COLORS):
            row = i // cols
            col = i % cols
            cell = tk.Frame(grid, bg=p["PANEL"], padx=4, pady=4)
            cell.grid(row=row, column=col, sticky="nsew")

            border_color = p["ACCENT"] if color.lower() == app.accent.lower() else p["SUNKEN"]
            border = tk.Frame(cell, bg=border_color, padx=2, pady=2)
            border.pack()

            swatch = tk.Canvas(border, width=42, height=42, bg=color,
                               highlightthickness=0, bd=0)
            swatch.pack()
            swatch.bind("<Button-1>", lambda e, c=color: self._pick(c))

        btn_row = tk.Frame(frame, bg=p["PANEL"])
        btn_row.pack(fill="x", pady=(14, 0))

        make_flat_button(btn_row, "Default (Purple)", self._reset,
                         palette=p, padx=10, pady=4).pack(side="left")
        make_flat_button(btn_row, "Close", self._close,
                         palette=p, padx=10, pady=4).pack(side="right")

        self.win.update_idletasks()
        mx = master.winfo_rootx() + master.winfo_width() // 2
        my = master.winfo_rooty() + master.winfo_height() // 2
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        self.win.geometry(f"+{mx - w // 2}+{my - h // 2}")
        self.win.bind("<Escape>", lambda e: self._close())

        try:
            app._extra_alpha_windows.append(self.win)
        except Exception:
            _log_swallowed()

    def _close(self):
        try:
            if self.win in self.app._extra_alpha_windows:
                self.app._extra_alpha_windows.remove(self.win)
        except Exception:
            _log_swallowed()
        try:
            self.win.destroy()
        except Exception:
            _log_swallowed()

    def _pick(self, color):
        self._close()
        self.app.apply_accent(color)

    def _reset(self):
        self._close()
        self.app.apply_accent(DEFAULT_PALETTE["ACCENT"])


class InfoWindow:
    def __init__(self, master, palette, app):
        self.app = app
        self.win = tk.Toplevel(master)
        self.win.title("Info – XZN-CDR")
        self.win.configure(bg=palette["PANEL"])
        self.win.resizable(False, False)
        self.win.transient(master)
        apply_window_icon(self.win)

        try:
            app._attach_macos_menu(self.win)
        except Exception:
            _log_swallowed()

        p = palette
        frame = tk.Frame(self.win, bg=p["PANEL"], padx=26, pady=22)
        frame.pack(fill="both", expand=True)

        title_row = tk.Frame(frame, bg=p["PANEL"])
        title_row.pack(anchor="w", fill="x")

        logo_path = resource_path(LOGO_FILENAME)
        if os.path.isfile(logo_path):
            try:
                logo_img = Image.open(logo_path).convert("RGBA")
                logo_img = logo_img.resize((64, 64), Image.Resampling.LANCZOS)
                logo_photo = ImageTk.PhotoImage(logo_img)
                logo_lbl = tk.Label(title_row, image=logo_photo,
                                    bg=p["PANEL"], bd=0,
                                    highlightthickness=0)
                logo_lbl.image = logo_photo
                logo_lbl.pack(side="left", padx=(0, 14))
            except Exception as e:
                print(f"[XZN-CDR] Info logo load failed: {e}")

        tk.Label(title_row, text=APP_LONG_NAME.upper(),
                 font=("Helvetica", 15, "bold"),
                 bg=p["PANEL"], fg=p["ACCENT"]).pack(side="left", anchor="w")

        tk.Frame(frame, bg=p["ACCENT_LO"], height=1).pack(fill="x", pady=(8, 14))

        tk.Label(frame, text="Companion tool for Xenon-Zones.",
                 font=FONT_TAG,
                 bg=p["PANEL"], fg=p["WHITE"],
                 anchor="w", justify="left",
                 wraplength=420).pack(anchor="w", pady=(0, 6))

        tk.Label(frame, text="Rips audio CDs to MP3, OGG, FLAC or WAV.",
                 font=FONT_TAG,
                 bg=p["PANEL"], fg=p["WHITE"],
                 anchor="w", justify="left",
                 wraplength=420).pack(anchor="w", pady=(0, 16))

        tk.Frame(frame, bg=p["ACCENT_LO"], height=1).pack(fill="x", pady=(0, 14))

        backend_name = getattr(app.backend, "name", "unknown")
        tk.Label(frame, text=f"CD backend: {backend_name}",
                 font=FONT_UI,
                 bg=p["PANEL"], fg=p["WHITE"]).pack(anchor="w")
        tk.Label(frame, text="Encodes with ffmpeg (MP3 / OGG / FLAC).",
                 font=FONT_UI,
                 bg=p["PANEL"], fg=p["WHITE"]).pack(anchor="w", pady=(0, 14))

        tk.Label(frame, text=f"Version {VERSION}",
                 font=FONT_UI, bg=p["PANEL"], fg=p["GRAY"]).pack(anchor="w")
        tk.Label(frame, text="© 2026 Merecete",
                 font=FONT_UI, bg=p["PANEL"], fg=p["GRAY"]).pack(anchor="w", pady=(2, 2))
        tk.Label(frame, text="Released under the MIT License.",
                 font=FONT_UI, bg=p["PANEL"], fg=p["GRAY"]).pack(anchor="w", pady=(0, 16))

        btn_row = tk.Frame(frame, bg=p["PANEL"])
        btn_row.pack(anchor="e", fill="x")

        make_flat_button(
            btn_row, "License",
            lambda: show_license_window(self.win, p, app),
            palette=p, padx=10, pady=4
        ).pack(side="left")

        # Opens xzn-cdr.log, e.g. to attach it to a bug report.
        if LOG_PATH:
            make_flat_button(
                btn_row, "Log",
                lambda: open_with_system(LOG_PATH),
                palette=p, padx=10, pady=4
            ).pack(side="left", padx=(6, 0))

        make_flat_button(
            btn_row, "Close", self._close,
            palette=p, padx=14, pady=4
        ).pack(side="right")

        self.win.protocol("WM_DELETE_WINDOW", self._close)
        self.win.bind("<Escape>", lambda e: self._close())

        self.win.update_idletasks()
        mx = master.winfo_rootx() + master.winfo_width() // 2
        my = master.winfo_rooty() + master.winfo_height() // 2
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        self.win.geometry(f"+{mx - w // 2}+{my - h // 2}")

        try:
            app._extra_alpha_windows.append(self.win)
        except Exception:
            _log_swallowed()

    def _close(self):
        try:
            if self.win in self.app._extra_alpha_windows:
                self.app._extra_alpha_windows.remove(self.win)
        except Exception:
            _log_swallowed()
        try:
            self.win.destroy()
        except Exception:
            _log_swallowed()


class App:
    def __init__(self):
        set_taskbar_app_id()

        self.root = tk.Tk()
        apply_window_icon(self.root)
        self._setup_macos_menu()
        self.root.title(APP_NAME)
        self.root.configure(bg=DEFAULT_PALETTE["BG"])
        # Start small; _fit_window() sets the final size once the
        # widgets exist (the smallest size the layout needs).
        self.root.minsize(520, 560)
        self.root.geometry("560x600")

        settings = load_settings()
        self.accent = settings.get("accent") or DEFAULT_PALETTE["ACCENT"]
        self._initial_alpha = int(settings.get("alpha") or 100)
        self.palette = build_palette(self.accent)

        self.ffmpeg = find_ffmpeg()
        self.ffprobe = find_ffprobe()
        self.backend: CDBackend = make_backend(self.ffmpeg, self.ffprobe)

        self.cd_tracks: List[Tuple[int, float]] = []
        self.total_cd_time: float = 0.0
        # MusicBrainz info for the scanned disc (None = unknown / pending)
        self.cd_info: Optional[dict] = None
        self._scan_token = 0
        self.ripping = False
        self.worker_thread: Optional[threading.Thread] = None
        self.ui_queue: "queue.Queue" = queue.Queue()
        self.current_outdir = settings.get("outdir")
        self.current_drive = settings.get("drive") or _suggest_default_drive() or ""
        self.format_var = tk.StringVar(value=settings.get("format") or "MP3")
        self.bitrate_var = tk.StringVar(value=settings.get("bitrate") or "320")
        self.eject_var = tk.BooleanVar(value=bool(settings.get("eject", True)))
        self.track_checks: List[tk.BooleanVar] = []

        # Visual selection (independent of the checkboxes)
        self._track_selected: set = set()
        self._track_anchor: Optional[int] = None
        self._track_rows: List[Tuple[tk.Frame, List[tk.Widget], tk.BooleanVar]] = []

        self._cancel_event: Optional[threading.Event] = None

        self._main_frame = None
        self._progress_value = 0.0
        self._progress_label = ""
        self._extra_alpha_windows: List[tk.Toplevel] = []
        # Tool windows (one each; a second click closes them)
        self._info_win = None
        self._theme_win = None
        self._license_win = None

        self._build_ui()
        self._fit_window()

        try:
            self.root.attributes("-alpha", self._initial_alpha / 100.0)
        except Exception:
            _log_swallowed()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        # (Esc no longer quits: in every other XZN window it only closes
        # that window, so quitting the whole ripper by accident was easy.)

        self.root.after(100, self._poll_ui_queue)
        self.root.after(200, self._startup_check)

    def _fit_window(self):
        """Open at the smallest size the layout needs: the widest control
        row sets the width, nothing else."""
        try:
            self.root.update_idletasks()
            w = self._main_frame.winfo_reqwidth()
            h = self._main_frame.winfo_reqheight()
            self.root.minsize(w, h)
            self.root.geometry(f"{w}x{h}")
        except Exception:
            _log_swallowed()

    def _setup_macos_menu(self):
        if not IS_MAC:
            return
        try:
            self._mac_menubar = tk.Menu(self.root)
            self.root.config(menu=self._mac_menubar)
            # "About XZN-CDR" in the macOS app menu → our own Info window.
            # Tk calls tkAboutDialog for that entry (older Tk versions use
            # ::tk::mac::ShowAbout); overriding standardAboutPanel alone
            # does not work, so register both names (same fix as in XZN).
            for name in ("tkAboutDialog", "::tk::mac::ShowAbout"):
                try:
                    self.root.createcommand(name, self.show_info)
                except tk.TclError:
                    _log_swallowed()
        except Exception as e:
            self._mac_menubar = None
            print(f"[XZN-CDR] macOS menu setup failed: {e}")

    def _attach_macos_menu(self, win):
        if not IS_MAC:
            return
        mb = getattr(self, "_mac_menubar", None)
        if mb is None:
            return
        try:
            win.config(menu=mb)
        except Exception as e:
            print(f"[XZN-CDR] _attach_macos_menu failed: {e}")

    def _mkbtn(self, parent, text, cmd, w=4):
        p = self.palette
        bg_color = p["RAISED"]
        fg_color = p["WHITE"]
        hover_bg = lighten(bg_color, 0.15)

        lbl = tk.Label(parent, text=text, bg=bg_color, fg=fg_color,
                       font=FONT_BTN, padx=8, pady=3,
                       relief="raised", bd=1, cursor="hand2")
        lbl.bind("<Button-1>", lambda e: cmd())
        lbl.bind("<Enter>", lambda e: lbl.config(bg=hover_bg))
        lbl.bind("<Leave>", lambda e: lbl.config(bg=bg_color))
        return lbl

    def _mk_pl_scale(self, parent):
        p = self.palette
        return tk.Scale(parent, from_=0, to=1, orient="vertical",
                        showvalue=False, resolution=0.001,
                        bg=p["PANEL"], fg=p["ACCENT"],
                        troughcolor=p["SUNKEN"],
                        highlightthickness=0, sliderrelief="flat",
                        width=14,
                        command=self._on_track_scroll)

    def _on_track_scroll(self, v):
        try:
            self.track_canvas.yview_moveto(float(v))
        except Exception:
            _log_swallowed()

    def _sync_track_scrollbar(self, *args):
        try:
            first, last = self.track_canvas.yview()
            if last - first >= 1.0:
                self.track_scale.set(0)
            else:
                self.track_scale.set(first)
        except Exception:
            _log_swallowed()

    def _build_ui(self):
        p = self.palette
        self.root.configure(bg=p["BG"])

        if self._main_frame is not None:
            try:
                self._main_frame.destroy()
            except Exception:
                _log_swallowed()
        self._main_frame = tk.Frame(self.root, bg=p["BG"])
        self._main_frame.pack(fill="both", expand=True)
        parent = self._main_frame

        hdr = tk.Frame(parent, bg=p["PANEL"], height=44)
        hdr.pack(fill="x", padx=8, pady=(8, 4))
        hdr.pack_propagate(False)

        self._mkbtn(hdr, "INFO", self.show_info, w=6).pack(side="left", padx=(10, 0))
        self._mkbtn(hdr, "C", self.show_theme, w=3).pack(side="left", padx=(4, 0))

        alpha_box = tk.Frame(hdr, bg=p["PANEL"])
        alpha_box.pack(side="right", padx=(0, 10))
        tk.Label(alpha_box, text="Alpha", font=FONT_UI,
                 bg=p["PANEL"], fg=p["GRAY"]).pack(side="left")
        self.alpha_scale = tk.Scale(alpha_box, from_=70, to=100, orient="horizontal",
                                    length=110, showvalue=False,
                                    command=self._on_alpha_cmd,
                                    bg=p["PANEL"], fg=p["ACCENT"], troughcolor=p["SUNKEN"],
                                    highlightthickness=0, sliderrelief="flat")
        self.alpha_scale.set(self._initial_alpha)
        self.alpha_scale.pack(side="left", padx=(4, 0))
        # --- Mouse-wheel control for the window Alpha slider (5% steps) ---
        _bind_scale_wheel(self.alpha_scale, self._on_alpha_cmd,
                          lo=70, hi=100, step=5, round_to=1.0)

        hero = tk.Canvas(parent, height=70, bg=p["PANEL"], highlightthickness=0)
        hero.pack(fill="x", padx=8, pady=(0, 6))
        hero.update_idletasks()

        is_light_theme = luminance(p["BG"]) > 0.5
        hero_shadow = "#000000" if not is_light_theme else "#ffffff"
        hero_main   = "#ffffff" if not is_light_theme else "#1a1240"

        title_text = APP_NAME
        title_font = ("Helvetica", 20, "bold")
        tx, ty = 20, 18
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1),
                       (-2, 0), (2, 0), (0, -2), (0, 2)):
            hero.create_text(tx + dx, ty + dy, text=title_text,
                             font=title_font, fill=hero_shadow, anchor="nw")
        hero.create_text(tx, ty, text=title_text,
                         font=title_font, fill=hero_main, anchor="nw")

        hero.create_text(20, 46,
                         text="Rip audio CDs to MP3 / OGG / FLAC / WAV.",
                         font=FONT_TAG, fill=p["WHITE"], anchor="nw")

        opt = tk.Frame(parent, bg=p["PANEL"])
        opt.pack(fill="x", padx=8, pady=(0, 4))

        tk.Label(opt, text="Drive", font=FONT_UI_B,
                 bg=p["PANEL"], fg=p["GRAY"]).grid(row=0, column=0, sticky="w", padx=(10, 4), pady=4)

        if IS_MAC:
            entry_wrap = tk.Frame(opt, bg=p["SUNKEN"], bd=0, highlightthickness=0)
            entry_wrap.grid(row=0, column=1, sticky="w", pady=4)
            self.drive_entry = tk.Entry(
                entry_wrap, width=16,
                bg=p["SUNKEN"], fg=p["WHITE"],
                insertbackground=p["WHITE"],
                relief="flat", bd=0, highlightthickness=0,
                font=FONT_UI,
            )
            self.drive_entry.pack(padx=6, pady=3)
        else:
            self.drive_entry = tk.Entry(opt, width=18, bg=p["SUNKEN"], fg=p["WHITE"],
                                        insertbackground=p["WHITE"], relief="flat",
                                        font=FONT_UI, highlightthickness=0, bd=0)
            self.drive_entry.grid(row=0, column=1, sticky="w", pady=4, ipady=3)

        self.drive_entry.insert(0, self.current_drive)

        self._mkbtn(opt, "SCAN", self.scan_drive, w=6).grid(row=0, column=2, padx=(6, 0), pady=4)

        tk.Label(opt, text="Format", font=FONT_UI_B,
                 bg=p["PANEL"], fg=p["GRAY"]).grid(row=0, column=3, sticky="w", padx=(20, 4), pady=4)

        if IS_MAC:
            fmt_menu = FlatOptionMenu(opt, self.format_var,
                                      ["MP3", "OGG", "FLAC", "WAV"],
                                      palette=p, width=6)
            fmt_menu.grid(row=0, column=4, sticky="w", pady=4)
        else:
            fmt_menu = tk.OptionMenu(opt, self.format_var, "MP3", "OGG", "FLAC", "WAV")
            fmt_menu.config(bg=p["RAISED"], fg=p["WHITE"],
                            activebackground=p["ACCENT"], activeforeground=p["WHITE"],
                            highlightthickness=0, bd=1, relief="raised",
                            font=FONT_UI_B, width=6)
            fmt_menu["menu"].config(bg=p["RAISED"], fg=p["WHITE"],
                                    activebackground=p["ACCENT"],
                                    activeforeground=p["WHITE"], font=FONT_UI)
            fmt_menu.grid(row=0, column=4, sticky="w", pady=4)

        tk.Label(opt, text="Bitrate", font=FONT_UI_B,
                 bg=p["PANEL"], fg=p["GRAY"]).grid(row=0, column=5, sticky="w", padx=(20, 4), pady=4)

        if IS_MAC:
            bit_menu = FlatOptionMenu(opt, self.bitrate_var,
                                      ["128", "192", "256", "320"],
                                      palette=p, width=5)
            bit_menu.grid(row=0, column=6, sticky="w", pady=4)
        else:
            bit_menu = tk.OptionMenu(opt, self.bitrate_var, "128", "192", "256", "320")
            bit_menu.config(bg=p["RAISED"], fg=p["WHITE"],
                            activebackground=p["ACCENT"], activeforeground=p["WHITE"],
                            highlightthickness=0, bd=1, relief="raised",
                            font=FONT_UI_B, width=5)
            bit_menu["menu"].config(bg=p["RAISED"], fg=p["WHITE"],
                                    activebackground=p["ACCENT"],
                                    activeforeground=p["WHITE"], font=FONT_UI)
            bit_menu.grid(row=0, column=6, sticky="w", pady=4)

        tk.Checkbutton(opt, text="Eject when done", variable=self.eject_var,
                       bg=p["PANEL"], fg=p["WHITE"],
                       activebackground=p["PANEL"], activeforeground=p["ACCENT"],
                       selectcolor=p["SUNKEN"], font=FONT_UI,
                       bd=0, highlightthickness=0
                       ).grid(row=1, column=0, columnspan=3, sticky="w", padx=(10, 0), pady=(2, 6))

        od = tk.Frame(parent, bg=p["PANEL"])
        od.pack(fill="x", padx=8, pady=(0, 4))
        tk.Label(od, text="Output", font=FONT_UI_B,
                 bg=p["PANEL"], fg=p["GRAY"]).pack(side="left", padx=(10, 6))
        # width=1: a long path is cut off instead of widening the window
        self.outdir_label = tk.Label(od, text=self.current_outdir or "(not set)",
                                     font=FONT_UI, bg=p["SUNKEN"], fg=p["WHITE"],
                                     anchor="w", padx=6, width=1)
        self.outdir_label.pack(side="left", fill="x", expand=True, ipady=3)
        self._mkbtn(od, "…", self.pick_outdir, w=3).pack(side="right", padx=(4, 0))

        list_frame = tk.Frame(parent, bg=p["PANEL"])
        list_frame.pack(fill="both", expand=True, padx=8, pady=(4, 4))

        top = tk.Frame(list_frame, bg=p["PANEL"])
        top.pack(fill="x")
        tk.Label(top, text="TRACKS", font=FONT_UI_B,
                 bg=p["PANEL"], fg=p["GRAY"]).pack(side="left")
        self.track_count = tk.Label(top, text="", font=FONT_UI,
                                    bg=p["PANEL"], fg=p["ACCENT_LO"])
        self.track_count.pack(side="right")
        # "Artist – Album (Year)" from MusicBrainz, shown after a scan.
        # width=1 + fill: long names are cut instead of widening the window.
        self.album_lbl = tk.Label(top, text="", font=FONT_UI_B, width=1,
                                  bg=p["PANEL"], fg=p["ACCENT"], anchor="w")
        self.album_lbl.pack(side="left", fill="x", expand=True, padx=(10, 10))

        lrow = tk.Frame(list_frame, bg=p["SUNKEN"])
        lrow.pack(fill="both", expand=True, pady=(2, 4))

        self.track_canvas = tk.Canvas(lrow, bg=p["SUNKEN"], highlightthickness=0)
        self.track_scale = self._mk_pl_scale(lrow)
        self.track_canvas.configure(yscrollcommand=self._sync_track_scrollbar)
        self.track_canvas.pack(side="left", fill="both", expand=True)
        self.track_scale.pack(side="right", fill="y", padx=(2, 0))

        self.track_inner = tk.Frame(self.track_canvas, bg=p["SUNKEN"])
        self._track_window_id = self.track_canvas.create_window(
            (0, 0), window=self.track_inner, anchor="nw"
        )
        self.track_inner.bind(
            "<Configure>",
            lambda e: self.track_canvas.configure(scrollregion=self.track_canvas.bbox("all")),
        )
        self.track_canvas.bind(
            "<Configure>",
            lambda e: self.track_canvas.itemconfigure(self._track_window_id, width=e.width),
        )

        self._bind_mousewheel(self.track_canvas)
        self._bind_mousewheel(self.track_inner)

        prog = tk.Frame(parent, bg=p["PANEL"])
        prog.pack(fill="x", padx=8, pady=(0, 4))
        self.progress = tk.Canvas(prog, height=14, bg=p["SUNKEN"], highlightthickness=0)
        self.progress.pack(fill="x")
        self.progress.bind("<Configure>", lambda e: self._draw_progress(self._progress_value))

        brow = tk.Frame(parent, bg=p["PANEL"])
        brow.pack(fill="x", padx=8, pady=(0, 4))
        self.btn_rip = self._mkbtn(brow, "RIP ALL", self.rip_all, w=10)
        self.btn_rip.pack(side="left", padx=(10, 4))
        self.btn_rip_sel = self._mkbtn(brow, "RIP SELECTED", self.rip_selected, w=14)
        self.btn_rip_sel.pack(side="left", padx=(0, 4))
        self.btn_cancel = self._mkbtn(brow, "STOP", self.cancel_stop, w=8)
        self.btn_cancel.pack(side="left", padx=(0, 4))
        self.btn_open_out = self._mkbtn(brow, "OPEN FOLDER", self.open_outdir, w=12)
        self.btn_open_out.pack(side="right", padx=(4, 10))

        self.status = tk.Label(parent, text="Ready.", width=1,
                               font=FONT_UI, bg=p["PANEL"], fg=p["GRAY"], anchor="w")
        self.status.pack(fill="x", side="bottom")

        self._render_tracks()

    def _bind_mousewheel(self, widget):
        widget.bind("<MouseWheel>", self._on_track_mousewheel)
        widget.bind("<Button-4>", self._on_track_mousewheel)
        widget.bind("<Button-5>", self._on_track_mousewheel)

    def _on_track_mousewheel(self, event):
        try:
            if getattr(event, "num", None) == 4:
                delta = -1
            elif getattr(event, "num", None) == 5:
                delta = 1
            else:
                delta = -1 if event.delta > 0 else 1
            self.track_canvas.yview_scroll(delta, "units")
        except Exception:
            _log_swallowed()
        return "break"

    def apply_accent(self, accent_hex):
        self.accent = accent_hex
        self.palette = build_palette(accent_hex)

        try:
            a = float(self.alpha_scale.get())
        except Exception:
            a = float(self._initial_alpha)

        self._build_ui()

        try:
            self.alpha_scale.set(int(a))
            self.root.attributes("-alpha", a / 100.0)
        except Exception:
            _log_swallowed()

        self._persist_settings()
        self._apply_alpha_to_windows()

    def _on_alpha_cmd(self, v):
        self._apply_alpha_to_windows()
        self._persist_settings()

    def _apply_alpha_to_windows(self):
        try:
            a = float(self.alpha_scale.get()) / 100.0
        except Exception:
            a = 1.0
        a = max(0.0, min(1.0, a))

        try:
            self.root.attributes("-alpha", a)
        except Exception:
            _log_swallowed()

        all_toplevels = []

        def _collect(widget):
            try:
                for child in widget.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        all_toplevels.append(child)
                    _collect(child)
            except Exception:
                _log_swallowed()

        _collect(self.root)

        for w in list(getattr(self, "_extra_alpha_windows", [])):
            try:
                if w.winfo_exists() and w not in all_toplevels:
                    all_toplevels.append(w)
            except Exception:
                _log_swallowed()

        for w in all_toplevels:
            try:
                if w.winfo_exists():
                    w.attributes("-alpha", a)
            except Exception:
                _log_swallowed()

    def _toggle_window(self, attr, opener):
        """Open a tool window, or close it when it is already open
        (same behaviour as the VIDEO button). Prevents duplicates."""
        win = getattr(self, attr, None)
        try:
            if win is not None and win.winfo_exists():
                try:
                    if win in self._extra_alpha_windows:
                        self._extra_alpha_windows.remove(win)
                except Exception:
                    _log_swallowed()
                win.destroy()
                setattr(self, attr, None)
                return
        except Exception:
            _log_swallowed()
        setattr(self, attr, opener())
        self._apply_alpha_to_windows()

    def show_info(self):
        self._toggle_window("_info_win", lambda: InfoWindow(self.root, self.palette, self).win)

    def show_theme(self):
        self._toggle_window("_theme_win", lambda: ThemeWindow(self.root, self).win)

    def _persist_settings(self):
        state = {
            "accent": self.accent,
            "alpha": int(self.alpha_scale.get()) if hasattr(self, "alpha_scale") else 100,
            "format": self.format_var.get(),
            "bitrate": self.bitrate_var.get(),
            "outdir": self.current_outdir,
            "drive": self.current_drive,
            "eject": bool(self.eject_var.get()),
        }
        save_settings(state)

    def _startup_check(self):
        p = self.palette
        msgs = []
        if not self.ffmpeg:
            msgs.append("ffmpeg missing (required)")
        if not self.backend.is_available():
            msgs.append(self.backend.availability_hint() or
                        f"{self.backend.name} backend unavailable")
        if msgs:
            self.status.config(text="Warning: " + " | ".join(msgs), fg=p["ERR"])
        else:
            src = "bundled" if hasattr(sys, "_MEIPASS") else "system"
            self.status.config(
                text=f"Ready. Backend: {self.backend.name} ({src}).",
                fg=p["GRAY"],
            )

    def scan_drive(self):
        p = self.palette
        drive = self.drive_entry.get().strip() or self.current_drive
        self.current_drive = drive

        if not drive:
            self.status.config(text="No drive specified.", fg=p["ERR"])
            return

        if not self.ffmpeg:
            self.status.config(text="ffmpeg not found.", fg=p["ERR"])
            return

        if not self.backend.is_available():
            hint = self.backend.availability_hint() or "Backend unavailable."
            self.status.config(text=hint, fg=p["ERR"])
            messagebox.showerror("CD backend unavailable", hint)
            return

        self.status.config(text=f"Scanning {drive} …", fg=p["GRAY"])
        self.root.update_idletasks()

        try:
            tracks = self.backend.list_tracks(drive)
        except Exception as e:
            self.status.config(text=f"Scan failed: {e}", fg=p["ERR"])
            return

        if not tracks:
            self.status.config(text="No audio CD tracks found.", fg=p["ERR"])
            return

        self.cd_tracks = tracks
        self.total_cd_time = sum(d for _, d in tracks)
        self.cd_info = None
        self._scan_token += 1
        found = f"Found {len(tracks)} track(s) — total {fmt_time(self.total_cd_time)}."
        self._render_tracks()
        self._persist_settings()

        # Look the disc up on MusicBrainz in the background.
        toc = getattr(self.backend, "toc", None)
        if toc and toc.get("offsets"):
            self.status.config(text=f"{found} Looking up CD info …", fg=p["GRAY"])
            token = self._scan_token

            def lookup():
                info = lookup_cd_info(toc)
                self.ui_queue.put(("cdinfo", token, info))

            threading.Thread(target=lookup, daemon=True).start()
        else:
            self.status.config(text=found, fg=p["GRAY"])

    def _render_tracks(self):
        p = self.palette
        if not hasattr(self, "track_inner") or self.track_inner is None:
            return

        for w in self.track_inner.winfo_children():
            w.destroy()
        self.track_checks = []
        self._track_selected = set()
        self._track_anchor = None
        self._track_rows = []

        if not self.cd_tracks:
            tk.Label(self.track_inner,
                     text="No CD scanned yet. Insert an audio CD and press SCAN.",
                     font=FONT_UI, bg=p["SUNKEN"], fg=p["GRAY"]).pack(anchor="w", padx=8, pady=8)
            try:
                self.track_count.config(text="")
            except Exception:
                _log_swallowed()
            try:
                self.track_canvas.yview_moveto(0.0)
                self.track_scale.set(0)
            except Exception:
                _log_swallowed()
            return

        for idx, (track_no, dur) in enumerate(self.cd_tracks):
            row = tk.Frame(self.track_inner, bg=p["SUNKEN"])
            row.pack(fill="x", padx=4, pady=1)

            var = tk.BooleanVar(value=True)
            self.track_checks.append(var)

            cb = tk.Checkbutton(row, variable=var, bg=p["SUNKEN"],
                                activebackground=p["SUNKEN"],
                                selectcolor=p["RAISED"], bd=0, highlightthickness=0,
                                command=lambda i=idx: self._on_checkbox_clicked(i))
            cb.pack(side="left", padx=(4, 4))

            num_lbl = tk.Label(row, text=f"{track_no:02d}", font=FONT_LCD,
                               bg=p["SUNKEN"], fg=p["ACCENT"], width=4, anchor="w")
            num_lbl.pack(side="left")

            dur_lbl = tk.Label(row, text=f"{fmt_time(dur)}", font=FONT_UI,
                               bg=p["SUNKEN"], fg=p["WHITE"], width=8, anchor="w")
            dur_lbl.pack(side="left")

            title, artist = self._track_title(track_no)
            if title and artist and self.cd_info and artist != self.cd_info.get("artist"):
                title = f"{title}  ·  {artist}"      # compilations: show the artist
            desc_lbl = tk.Label(row, text=title or "Audio Track", font=FONT_UI,
                                bg=p["SUNKEN"], fg=p["WHITE"] if title else p["GRAY"],
                                anchor="w")
            desc_lbl.pack(side="left", padx=(6, 0), fill="x", expand=True)

            labels = [num_lbl, dur_lbl, desc_lbl, cb]
            self._track_rows.append((row, labels, var))

            def _on_click(event, _idx=idx):
                shift = bool(event.state & 0x0001)
                ctrl  = bool(event.state & 0x0004) or bool(event.state & 0x0008)

                if shift and self._track_anchor is not None:
                    lo = min(self._track_anchor, _idx)
                    hi = max(self._track_anchor, _idx)
                    self._track_selected = set(range(lo, hi + 1))
                    self._track_anchor = _idx
                elif ctrl:
                    if _idx in self._track_selected:
                        self._track_selected.discard(_idx)
                    else:
                        self._track_selected.add(_idx)
                    self._track_anchor = _idx
                else:
                    self._track_selected = {_idx}
                    self._track_anchor = _idx

                self._apply_row_selection_colors()
                try:
                    self.track_canvas.focus_set()
                except Exception:
                    _log_swallowed()
                return "break"

            def _on_right_click(event, _idx=idx):
                if _idx not in self._track_selected:
                    self._track_selected = {_idx}
                    self._track_anchor = _idx
                    self._apply_row_selection_colors()
                self._toggle_checks_for_selection()
                return "break"

            for w in (row, num_lbl, dur_lbl, desc_lbl):
                w.configure(cursor="hand2")
                w.bind("<Button-1>", _on_click)
                w.bind("<Button-3>", _on_right_click)
                w.bind("<Button-2>", _on_right_click)
                w.bind("<Control-Button-1>", _on_right_click)

            for w in (row, cb, num_lbl, dur_lbl, desc_lbl):
                self._bind_mousewheel(w)

        try:
            self.track_canvas.configure(takefocus=1)
            self.track_canvas.bind("<space>", self._on_space_toggle)
        except Exception:
            _log_swallowed()

        try:
            self.track_count.config(text=f"{len(self.cd_tracks)} track(s)")
        except Exception:
            _log_swallowed()
        self._update_album_label()

        self._apply_row_selection_colors()

        try:
            self.track_canvas.update_idletasks()
            self.track_canvas.configure(scrollregion=self.track_canvas.bbox("all"))
            self.track_canvas.yview_moveto(0.0)
            self.track_scale.set(0)
        except Exception:
            _log_swallowed()

    # --- CD info (MusicBrainz) -------------------------------------------
    def _track_title(self, track_no):
        """(title, artist) for a disc track, or ("", "") if unknown."""
        if not self.cd_info:
            return "", ""
        toc = getattr(self.backend, "toc", None) or {}
        pos = track_no - int(toc.get("first", 1)) + 1
        t = (self.cd_info.get("tracks") or {}).get(pos) or {}
        return t.get("title") or "", t.get("artist") or ""

    def _update_album_label(self):
        try:
            info = self.cd_info
            if info and (info.get("artist") or info.get("album")):
                txt = f"{info.get('artist') or '?'} – {info.get('album') or '?'}"
                if info.get("date"):
                    txt += f" ({info['date']})"
                if info.get("disc"):
                    txt += f"  [CD {info['disc']}]"
                self.album_lbl.config(text=txt)
            else:
                self.album_lbl.config(text="")
        except Exception:
            _log_swallowed()

    def _on_cd_info(self, token, info):
        if token != self._scan_token:
            return                      # a newer scan has started
        p = self.palette
        self.cd_info = info
        found = f"{len(self.cd_tracks)} track(s) — total {fmt_time(self.total_cd_time)}."
        if info:
            self.status.config(text=f"{found} CD info from MusicBrainz.", fg=p["GRAY"])
        else:
            self.status.config(
                text=f"{found} No CD info found — files will be named track_XX.",
                fg=p["GRAY"])
        # Keep the user's check marks while swapping in the titles.
        checks = [v.get() for v in self.track_checks]
        self._render_tracks()
        for v, c in zip(self.track_checks, checks):
            v.set(c)

    def _on_space_toggle(self, event=None):
        """Keyboard shortcut: toggle checkboxes of the selected rows."""
        self._toggle_checks_for_selection()
        return "break"

    def _on_checkbox_clicked(self, idx):
        """Synchronisiert den Klick auf eine Checkbox auf alle aktuell markierten Tracks."""
        if idx in self._track_selected and len(self._track_selected) > 1:
            try:
                target_state = self.track_checks[idx].get()
                for i in self._track_selected:
                    if i != idx:
                        try:
                            self.track_checks[i].set(target_state)
                        except Exception:
                            _log_swallowed()
            except Exception:
                _log_swallowed()

    def _apply_row_selection_colors(self):
        """Repaint row backgrounds based on self._track_selected."""
        p = self.palette
        row_bg     = p["SUNKEN"]
        row_bg_sel = mix(p["PANEL"], p["ACCENT"], 0.45)

        for i, (row, labels, _var) in enumerate(self._track_rows):
            bg = row_bg_sel if i in self._track_selected else row_bg
            try:
                row.configure(bg=bg)
                for w in labels:
                    if isinstance(w, tk.Checkbutton):
                        w.configure(bg=bg, activebackground=bg)
                    else:
                        w.configure(bg=bg)
            except Exception:
                _log_swallowed()

    def _toggle_checks_for_selection(self):
        """
        Toggle the checkboxes of every currently-selected row.

        If ALL selected rows are checked, uncheck them all. Otherwise
        (any unchecked, or a mixed state), check them all.
        """
        sel = sorted(self._track_selected)
        if not sel:
            return

        all_checked = True
        for i in sel:
            try:
                if not self.track_checks[i].get():
                    all_checked = False
                    break
            except Exception:
                all_checked = False
                break

        target = not all_checked
        for i in sel:
            try:
                self.track_checks[i].set(target)
            except Exception:
                _log_swallowed()

    def pick_outdir(self):
        d = filedialog.askdirectory(title="Choose output folder",
                                    initialdir=self.current_outdir or os.path.expanduser("~"))
        if d:
            self.current_outdir = d
            self.outdir_label.config(text=d)
            self._persist_settings()

    def open_outdir(self):
        d = self.current_outdir
        if not d or not os.path.isdir(d):
            self.status.config(text="Output folder does not exist.", fg=self.palette["ERR"])
            return
        try:
            if IS_WINDOWS:
                os.startfile(d)  # type: ignore[attr-defined]
            elif IS_MAC:
                subprocess.Popen(["open", d])
            else:
                subprocess.Popen(["xdg-open", d])
        except Exception as e:
            self.status.config(text=f"Open failed: {e}", fg=self.palette["ERR"])

    def _ensure_ready(self, tracks):
        p = self.palette
        if self.ripping:
            self.status.config(text="Already ripping. Please wait…", fg=p["GRAY"])
            return False
        if not self.ffmpeg:
            self.status.config(text="ffmpeg not found.", fg=p["ERR"])
            return False
        if not self.backend.is_available():
            hint = self.backend.availability_hint() or "Backend unavailable."
            self.status.config(text=hint, fg=p["ERR"])
            return False
        if not tracks:
            self.status.config(text="No tracks selected.", fg=p["ERR"])
            return False
        if not self.current_outdir:
            self.status.config(text="Please choose an output folder.", fg=p["ERR"])
            return False
        os.makedirs(self.current_outdir, exist_ok=True)
        return True

    def rip_all(self):
        if not self.cd_tracks:
            self.status.config(text="Scan a CD first.", fg=self.palette["ERR"])
            return
        self._start_rip(list(self.cd_tracks))

    def rip_selected(self):
        if not self.cd_tracks:
            self.status.config(text="Scan a CD first.", fg=self.palette["ERR"])
            return
        selected = [t for t, var in zip(self.cd_tracks, self.track_checks) if var.get()]
        self._start_rip(selected)

    def _start_rip(self, selected_tracks: List[Tuple[int, float]]):
        if not self._ensure_ready(selected_tracks):
            return

        track_ids: List[int] = []
        durations: List[float] = []
        starts: List[float] = []
        # Real start positions from the TOC. (Summing the durations of
        # only the *selected* tracks put e.g. track 3 at 0:00 and ripped
        # the wrong audio on Windows.)
        toc_starts = getattr(self.backend, "starts", None) or {}
        all_starts, acc = {}, 0.0
        for track_no, dur in self.cd_tracks:
            all_starts[track_no] = acc
            acc += dur
        for track_no, dur in selected_tracks:
            track_ids.append(track_no)
            durations.append(dur)
            starts.append(toc_starts.get(track_no, all_starts.get(track_no, 0.0)))

        self._cancel_event = threading.Event()

        job = RipJob(
            tracks=track_ids,
            track_durations=durations,
            track_starts=starts,
            outdir=self.current_outdir,
            fmt=self.format_var.get(),
            bitrate=self.bitrate_var.get(),
            drive=self.current_drive,
            eject=bool(self.eject_var.get()),
            ffmpeg=self.ffmpeg,
            cancel_event=self._cancel_event,
            info=self.cd_info,
            first_track=int((getattr(self.backend, "toc", None) or {}).get("first", 1)),
        )

        self.ripping = True
        self._progress_value = 0.0
        self._progress_label = ""
        self._draw_progress(0.0)
        self.status.config(text="Ripping…", fg=self.palette["GRAY"])
        try:
            self.btn_cancel.config(text="STOP", relief="raised")
        except Exception:
            _log_swallowed()

        backend = self.backend

        def worker():
            rip_worker(job, backend, self.ui_queue)

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def cancel_stop(self):
        if not self.ripping:
            return
        try:
            if self._cancel_event is not None:
                self._cancel_event.set()
            self.status.config(
                text="Stopping…",
                fg=self.palette["GRAY"],
            )
            self.btn_cancel.config(text="STOPPING…", relief="sunken")
        except Exception as e:
            print(f"[XZN-CDR] Stop failed: {e}")

    def _draw_progress(self, value):
        c = self.progress
        p = self.palette
        try:
            w = max(c.winfo_width(), 10)
            h = max(c.winfo_height(), 10)
        except Exception:
            return
        c.delete("all")
        c.create_rectangle(0, 0, w, h, fill=p["SUNKEN"], outline="")
        bar_w = int(w * max(0.0, min(1.0, value / 100.0)))
        if bar_w > 0:
            c.create_rectangle(0, 0, bar_w, h, fill=p["ACCENT"], outline="")
        if self._progress_label:
            c.create_text(w // 2, h // 2, text=self._progress_label,
                          font=("Helvetica", 8), fill=p["WHITE"])

    def _poll_ui_queue(self):
        try:
            while True:
                kind, *rest = self.ui_queue.get_nowait()
                if kind == "log":
                    msg = rest[0]
                    print(f"[XZN-CDR] {msg}")
                elif kind == "progress":
                    percent, label = rest
                    self._progress_value = percent
                    self._progress_label = label
                    self._draw_progress(percent)
                elif kind == "cdinfo":
                    self._on_cd_info(*rest)
                elif kind == "done":
                    msg = rest[0]
                    self.status.config(text=msg, fg=self.palette["GRAY"])
                    self._finish_rip_ok()
                elif kind == "stopped":
                    msg = rest[0]
                    self.status.config(text=msg, fg=self.palette["GRAY"])
                    self._finish_rip_ok()
                elif kind == "error":
                    msg = rest[0]
                    self.status.config(text=f"Error: {msg}", fg=self.palette["ERR"])
                    self._finish_rip_ok()
                    messagebox.showerror("Rip failed", msg)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_ui_queue)

    def _finish_rip_ok(self):
        self.ripping = False
        try:
            self.btn_cancel.config(text="STOP", relief="raised")
        except Exception:
            _log_swallowed()

    def on_close(self):
        if self.ripping:
            if not messagebox.askyesno(
                "Ripping in progress",
                "A rip is currently running. Quit anyway?\n"
                "The current track will be incomplete."
            ):
                return
            try:
                if self._cancel_event is not None:
                    self._cancel_event.set()
            except Exception:
                _log_swallowed()
            try:
                if self.worker_thread is not None:
                    self.worker_thread.join(timeout=3)
            except Exception:
                _log_swallowed()
        try:
            self._persist_settings()
        except Exception as e:
            print(f"[XZN-CDR] Failed to persist settings: {e}")
        try:
            self.root.destroy()
        except Exception:
            _log_swallowed()


def main():
    setup_logging(_get_app_data_dir())
    print(f"[XZN-CDR] Starting {APP_LONG_NAME} {VERSION}")
    if LOG_PATH:
        print(f"[XZN-CDR] Log file: {LOG_PATH}")
    print(f"[XZN-CDR] Python {sys.version}")
    print(f"[XZN-CDR] Platform: {sys.platform}")
    print(f"[XZN-CDR] Frozen: {hasattr(sys, '_MEIPASS')}")
    print(f"[XZN-CDR] pycdio available: {_PYCDIO_AVAILABLE}")
    print(f"[XZN-CDR] cd-paranoia: {find_cdparanoia() or 'NOT FOUND'}")
    print(f"[XZN-CDR] ffmpeg: {find_ffmpeg() or 'NOT FOUND'}")
    print(f"[XZN-CDR] ffprobe: {find_ffprobe() or 'NOT FOUND'}")
    print(f"[XZN-CDR] Settings: {SETTINGS_PATH}")
    icon = get_icon_path()
    print(f"[XZN-CDR] Icon: {icon if icon else 'NOT FOUND (xzn.ico missing)'}")
    app = App()
    app.root.mainloop()


if __name__ == "__main__":
    main()
