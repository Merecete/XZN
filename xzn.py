#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Xenon-Zones (XZN) v1.1.0
========================
A lightweight media player in classic Winamp style.

Features
--------
- Plays audio and video (via PyAV)
- Playlist with drag & drop, reordering, and .m3u / .m3u8 import/export
- 10-band Winamp-style equalizer with presets
- Audio analyzer / spectrum display
- Video window with idle card (logo shown when no video frame)
- Tag editor for audio files (via mutagen)
- Accent color theming and alpha transparency
- Animated GIF / WebP backgrounds in the hero canvas
  (playback is tied to the player: the animation runs only while
  a track is actually playing and freezes on pause/stop)
- Auto-saves playlist and settings (crash-safe)
- Gapless playback, ReplayGain volume leveling, resume on start
- Type-to-search in the playlist, mini mode (click the XZN wordmark)
- Media keys (Windows: global hotkeys, Linux: MPRIS via jeepney)
- Writes a log file (xzn.log) to the app data directory
- Plays and rips audio CDs (CD button): MP3 / OGG / FLAC / WAV, titles
  from MusicBrainz, tags + ReplayGain - no external tools needed
- Ships with a built-in sample intro track (installed on first run)

Platform notes
--------------
- Fully cross-platform (Windows, Linux, macOS)
- macOS: shared menubar across all dialogs; label-based buttons
- Optional: drag & drop needs `tkinterdnd2`; tag editing needs `mutagen`

Copyright (c) 2026 Merecete
Released under the MIT License. See LICENSE file for details.
"""
import os
import sys
import json
import time
import queue
import random
import re
import shutil
import struct
import threading
import traceback
from collections import deque
import numpy as np
import av
import sounddevice as sd
from PIL import Image, ImageTk, ImageDraw, ImageFilter, ImageSequence
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox


# --- Log file ------------------------------------------------------------
# Everything XZN prints (status lines, warnings, tracebacks) is mirrored
# into xzn.log in the app data directory, so problems can be diagnosed
# even in the windowed release builds, where there is no console at all.
LOG_MAX_BYTES = 1_000_000
LOG_PATH = None
_LOG_LOCK = threading.Lock()


class _LogTee:
    """File-like object: writes to the original stream (if any) and to
    the log file, prefixing each new line with a timestamp."""

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
    """Mirror stdout/stderr into <log_dir>/xzn.log. The previous log is
    kept as xzn.log.1 once the file grows beyond LOG_MAX_BYTES."""
    global LOG_PATH
    if not log_dir:
        return None
    path = os.path.join(log_dir, "xzn.log")
    try:
        if os.path.isfile(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            os.replace(path, path + ".1")
        fh = open(path, "a", encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"[XZN] Could not open log file '{path}': {e}")
        return None
    fh.write("\n")
    sys.stdout = _LogTee(sys.stdout, fh)
    sys.stderr = _LogTee(sys.stderr, fh)
    LOG_PATH = path
    return path


_SWALLOWED_SEEN = set()


def _log_swallowed():
    """Called from 'except Exception:' blocks that deliberately ignore an
    error. Logs it once per code location, so the UI keeps running but
    the log still shows what went wrong."""
    et, ev, tb = sys.exc_info()
    if et is None or tb is None:
        return
    code = tb.tb_frame.f_code
    key = (code.co_filename, tb.tb_lineno, et.__name__)
    if key in _SWALLOWED_SEEN or len(_SWALLOWED_SEEN) > 500:
        return
    _SWALLOWED_SEEN.add(key)
    msg = str(ev).strip().replace("\n", " ")[:200]
    print(f"[XZN] (ignored) {et.__name__}: {msg} — in {code.co_name}(), line {tb.tb_lineno}")


def open_with_system(path):
    """Open a file or folder with the platform's default application."""
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        print(f"[XZN] Could not open '{path}': {e}")


# --- Windows: taskbar icon ----------------------------------------------
if sys.platform == "win32":
    try:
        import ctypes
    except Exception:
        ctypes = None
else:
    ctypes = None

# --- Platform detection -------------------------------------------------
IS_MAC = (sys.platform == "darwin")
IS_WINDOWS = (sys.platform == "win32")

# --- Optional: Drag & Drop ----------------------------------------------
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES  # type: ignore
    _DND_AVAILABLE = True
except Exception:
    TkinterDnD = None  # type: ignore
    DND_FILES = None   # type: ignore
    _DND_AVAILABLE = False

# --- Optional: Tag editing ----------------------------------------------
try:
    from mutagen import File as MutagenFile  # type: ignore
    _MUTAGEN_AVAILABLE = True
except Exception:
    MutagenFile = None  # type: ignore
    _MUTAGEN_AVAILABLE = False

# --- Optional: fast EQ filtering ----------------------------------------
# scipy's sosfilt runs the biquad cascade in C. Without scipy the
# equalizer falls back to the (much slower) pure-Python loop.
try:
    from scipy.signal import sosfilt as _sosfilt  # type: ignore
    _SCIPY_AVAILABLE = True
except Exception:
    _sosfilt = None  # type: ignore
    _SCIPY_AVAILABLE = False


APP_NAME = "Xenon-Zones"
VERSION = "1.1.0"
SAMPLE_RATE = 44100
VIDEO_W, VIDEO_H = 624, 351
ANALYZER_BARS = 32

SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".xzn_settings.json")

# --- Icon / resource constants ------------------------------------------
ICON_FILENAME = "xzn.ico"                 # Windows icon
LINUX_ICON_FILENAME = "xzn.png"           # Linux / macOS icon
LOGO_FILENAME = "xzn-logo.png"
INTRO_FILENAME = "XZN Intro.mp3"
TASKBAR_APP_ID = "com.merecete.xzn.mediaplayer.1"

# --- Full MIT license text (for the Info window button) ----------------
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

# --- Default palette (dark bluish purple) -------------------------------
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
    ("Purple",  "#a066ff"),
    ("Blue",    "#7fb4ff"),
    ("Green",   "#7fe0a4"),
    ("Pink",    "#ff8fd0"),
    ("Amber",   "#ffb066"),
    ("Red",     "#ff8a8a"),
    ("Turquoise", "#6fe6d8"),
    ("Black",   "#101018"),
    ("White",   "#dfe3f0"),
]

FONT_UI   = ("Helvetica", 9)
FONT_UI_B = ("Helvetica", 9, "bold")
FONT_LCD  = ("Courier New", 13, "bold")
FONT_LG   = ("Courier New", 16, "bold")
FONT_BTN  = ("Courier New", 10, "bold")
FONT_TAGLINE = ("Helvetica", 11, "italic")

MEDIA_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".ogv",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".m4v", ".asf", ".3gp",
    ".mp3", ".ogg", ".oga", ".opus", ".flac", ".wav", ".wma", ".aac",
    ".m4a", ".m4b", ".aiff", ".ape", ".alac",
}

# Formats that mutagen can reliably write tags to
TAGGABLE_AUDIO_EXTS = {
    ".mp3", ".ogg", ".oga", ".opus", ".flac", ".wav", ".aac",
    ".m4a", ".m4b", ".aiff", ".ape",
}

PLAYLIST_EXTS = {".m3u", ".m3u8"}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Equalizer ----------------------------------------------------------
EQ_BANDS = [60, 170, 310, 600, 1000, 3000, 6000, 12000, 14000, 16000]
EQ_BAND_LABELS = ["60", "170", "310", "600", "1k", "3k", "6k", "12k", "14k", "16k"]
EQ_PRESETS = {
    "Flat":      [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    "Rock":      [5, 4, 2, -1, -2, 1, 3, 5, 5, 5],
    "Pop":       [-1, 2, 4, 5, 4, 1, -1, -1, 1, 2],
    "Jazz":      [4, 3, 1, 2, -1, -1, 0, 2, 3, 4],
    "Classical": [4, 3, 2, 0, 0, 0, -2, -3, -3, -4],
    "Bass":      [8, 7, 5, 2, 0, 0, 0, 0, 0, 0],
    "Treble":    [0, 0, 0, 0, 0, 2, 4, 6, 7, 8],
    "Vocal":     [-2, -1, 2, 4, 5, 4, 2, 0, -1, -2],
}


# ------------------------------------------------------------------------
def resource_path(relative_name):
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = SCRIPT_DIR
    return os.path.join(base, relative_name)


def get_icon_path():
    """
    Return the platform-appropriate window-icon path, or None if the
    file is missing.

    - Windows uses the classic .ico (xzn.ico).
    - Linux and macOS use a PNG (xzn.png), because Tk on those
      platforms cannot load .ico files via iconbitmap().
    """
    name = ICON_FILENAME if IS_WINDOWS else LINUX_ICON_FILENAME
    p = resource_path(name)
    return p if os.path.isfile(p) else None


def set_taskbar_app_id():
    if sys.platform != "win32" or ctypes is None:
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            TASKBAR_APP_ID
        )
    except Exception as e:
        print(f"[XZN] Taskbar App-ID set failed: {e}")


def apply_window_icon(window):
    """
    Apply the window icon in a cross-platform way.

    - Windows: iconbitmap() with the .ico (native, crisp at all sizes).
    - Linux/macOS: iconphoto() with a PNG loaded via PhotoImage, because
      Tk on those platforms does not support iconbitmap() with .ico.

    The PhotoImage reference is stored on the window object so Tk does
    not garbage-collect it (otherwise the icon silently disappears).
    """
    if window is None:
        return

    icon_path = get_icon_path()
    if not icon_path:
        return

    # --- Windows: native .ico ------------------------------------------
    if IS_WINDOWS:
        try:
            window.iconbitmap(icon_path)
            return
        except Exception as e:
            print(f"[XZN] iconbitmap failed: {e}")

    # --- Linux / macOS (and Windows fallback): PNG via iconphoto -------
    try:
        img = tk.PhotoImage(file=icon_path)
        window.tk.call("wm", "iconphoto", window._w, img)
        # Keep a reference so Tk does not GC the image.
        window._icon_photo_ref = img
    except Exception as e:
        print(f"[XZN] iconphoto failed: {e}")


# ------------------------------------------------------------------------
def _get_app_data_dir():
    """
    Return the per-user application data directory for XZN, creating
    it if necessary. This is the *durable* location where the sample
    track is materialised on first run, so it survives across launches
    (unlike the ephemeral PyInstaller _MEIPASS directory).
    """
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
        print(f"[XZN] Could not create app data dir '{app_dir}': {e}")
        return None
    return app_dir


def ensure_sample_track():
    """
    Materialise the bundled intro MP3 into a durable per-user location.

    The bundled file lives in resource_path(INTRO_FILENAME). On first
    run we copy it to the app data directory so the playlist entry
    keeps working across restarts (PyInstaller's _MEIPASS temp dir is
    wiped between launches).

    Returns the absolute path to the durable copy, or None if the
    source file is missing or the copy failed.
    """
    src = resource_path(INTRO_FILENAME)
    if not os.path.isfile(src):
        # Not fatal: the user may have a build without the intro.
        print(f"[XZN] No bundled intro track found at '{src}'")
        return None

    app_dir = _get_app_data_dir()
    if app_dir is None:
        # Fall back to using the bundled file directly (works while
        # the process is alive, but the path may vanish on restart).
        return src

    dst = os.path.join(app_dir, INTRO_FILENAME)

    # If the destination already exists and is non-empty, keep it.
    # (We don't overwrite so the user's file, if they edited tags
    # or replaced it, stays intact.)
    try:
        if os.path.isfile(dst) and os.path.getsize(dst) > 0:
            return dst
    except Exception:
        _log_swallowed()

    try:
        shutil.copy2(src, dst)
        print(f"[XZN] Sample intro installed → {dst}")
        return dst
    except Exception as e:
        print(f"[XZN] Failed to copy sample intro: {e}")
        return src


# ------------------------------------------------------------------------
def fmt_time(t):
    if t is None or t < 0:
        t = 0.0
    t = int(t)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def is_media_file(path):
    return os.path.splitext(path)[1].lower() in MEDIA_EXTS


def is_taggable_audio(path):
    return os.path.splitext(path)[1].lower() in TAGGABLE_AUDIO_EXTS


def is_playlist_file(path):
    return os.path.splitext(path)[1].lower() in PLAYLIST_EXTS


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


def load_settings():
    defaults = {
        "accent": DEFAULT_PALETTE["ACCENT"],
        "alpha": 100,
        "background": None,
        "playlist": [],
        "eq_gains": [0.0] * len(EQ_BANDS),
        "eq_enabled": True,
        "eq_preamp": 0.0,
        "sample_offered": False,
        "track_durations": {},
        "replaygain": True,
        "resume": None,
        "cd": {},
    }
    try:
        if os.path.isfile(SETTINGS_PATH):
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return defaults
            accent = data.get("accent")
            if isinstance(accent, str) and accent.startswith("#"):
                defaults["accent"] = accent
            alpha = data.get("alpha")
            if isinstance(alpha, (int, float)) and 70 <= alpha <= 100:
                defaults["alpha"] = int(alpha)
            bg = data.get("background")
            if isinstance(bg, str) and bg:
                defaults["background"] = bg
            pl = data.get("playlist")
            if isinstance(pl, list):
                defaults["playlist"] = [p for p in pl if isinstance(p, str) and os.path.isfile(p)]
            eq = data.get("eq_gains")
            if isinstance(eq, list) and len(eq) == len(EQ_BANDS):
                defaults["eq_gains"] = [float(max(-12.0, min(12.0, x))) for x in eq]
            eq_en = data.get("eq_enabled")
            if isinstance(eq_en, bool):
                defaults["eq_enabled"] = eq_en
            pre = data.get("eq_preamp")
            if isinstance(pre, (int, float)):
                defaults["eq_preamp"] = float(max(-12.0, min(12.0, pre)))
            so = data.get("sample_offered")
            if isinstance(so, bool):
                defaults["sample_offered"] = so
            td = data.get("track_durations")
            if isinstance(td, dict):
                cleaned = {}
                for k, v in td.items():
                    if isinstance(k, str) and isinstance(v, (int, float)) and v > 0:
                        cleaned[k] = float(v)
                defaults["track_durations"] = cleaned
            rg = data.get("replaygain")
            if isinstance(rg, bool):
                defaults["replaygain"] = rg
            cd = data.get("cd")
            if isinstance(cd, dict):
                defaults["cd"] = cd
            res = data.get("resume")
            if isinstance(res, dict) and isinstance(res.get("path"), str) \
                    and isinstance(res.get("pos"), (int, float)):
                defaults["resume"] = {"path": res["path"], "pos": max(0.0, float(res["pos"]))}
    except Exception as e:
        print(f"[XZN] Failed to load settings: {e}")
    return defaults


def save_settings(state):
    try:
        payload = {
            "accent": state.get("accent") or DEFAULT_PALETTE["ACCENT"],
            "alpha": int(state.get("alpha") or 100),
            "background": state.get("background"),
            "playlist": list(state.get("playlist") or []),
            "eq_gains": list(state.get("eq_gains") or [0.0] * len(EQ_BANDS)),
            "eq_enabled": bool(state.get("eq_enabled", True)),
            "eq_preamp": float(state.get("eq_preamp") or 0.0),
            "sample_offered": bool(state.get("sample_offered", False)),
            "track_durations": dict(state.get("track_durations") or {}),
            "replaygain": bool(state.get("replaygain", True)),
            "resume": state.get("resume"),
            "cd": dict(state.get("cd") or {}),
        }
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        print(f"[XZN] Failed to save settings: {e}")


def clear_settings():
    try:
        if os.path.isfile(SETTINGS_PATH):
            os.remove(SETTINGS_PATH)
            print(f"[XZN] Settings deleted: {SETTINGS_PATH}")
    except Exception as e:
        print(f"[XZN] Failed to delete settings: {e}")


def _make_gradient_bg(w, h, palette):
    top = np.array(hex_to_rgb(mix(palette["BG"], palette["ACCENT"], 0.15)), dtype=np.float32)
    bot = np.array(hex_to_rgb(darken(palette["BG"], 0.6)), dtype=np.float32)
    y = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    grad = (top[None, None, :] * (1 - y) + bot[None, None, :] * y)
    arr = np.repeat(grad, w, axis=1).astype(np.uint8)
    img = Image.fromarray(arr, mode="RGB")

    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(glow)
    cx, cy, r = int(w * 0.78), int(h * 0.18), int(min(w, h) * 0.55)
    acc = hex_to_rgb(palette["ACCENT"])
    d.ellipse((cx - r, cy - r, cx + r, cy + r),
              fill=(acc[0] // 2, acc[1] // 2, acc[2] // 2, 100))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=max(4, min(w, h) // 6)))

    glow2 = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d2 = ImageDraw.Draw(glow2)
    cx2, cy2, r2 = int(w * 0.18), int(h * 0.85), int(min(w, h) * 0.5)
    d2.ellipse((cx2 - r2, cy2 - r2, cx2 + r2, cy2 + r2),
               fill=(acc[0] // 3, acc[1] // 3, acc[2] // 3, 80))
    glow2 = glow2.filter(ImageFilter.GaussianBlur(radius=max(4, min(w, h) // 6)))

    img = img.convert("RGBA")
    img = Image.alpha_composite(img, glow)
    img = Image.alpha_composite(img, glow2)
    return img.convert("RGB")


def _prepare_rgb_frame(pil_frame, palette):
    """
    Convert any PIL image (static or animation frame) to flat RGB.

    GIF / PNG transparency is composited over the PANEL color, so
    transparent regions blend seamlessly into the hero canvas.
    """
    if pil_frame.mode == "RGB":
        return pil_frame
    if pil_frame.mode in ("RGBA", "LA", "P"):
        rgba = pil_frame.convert("RGBA")
        bg = Image.new("RGB", rgba.size, hex_to_rgb(palette["PANEL"]))
        bg.paste(rgba, (0, 0), rgba)
        return bg
    return pil_frame.convert("RGB")


def _process_bg_frame(img_rgb, w, h, palette):
    """
    Resize (cover), center-crop, and apply the hero fade to a single
    background frame. Returns a PIL.Image sized exactly (w, h).
    """
    iw, ih = img_rgb.size
    if iw <= 0 or ih <= 0:
        return Image.new("RGB", (w, h), hex_to_rgb(palette["PANEL"]))
    scale = max(w / iw, h / ih)
    nw = max(2, int(iw * scale))
    nh = max(2, int(ih * scale))
    img = img_rgb.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - w) // 2
    top_ = (nh - h) // 2
    img = img.crop((left, top_, left + w, top_ + h))
    return _apply_hero_fade(img, palette)


def load_background_frames(w, h, palette, custom_path=None):
    """
    Load a background image (static PNG/JPG/BMP or animated GIF/WebP)
    and return (frames, durations, is_animated).

    - frames      : list of PIL.Image (RGB, sized to w×h, fade applied)
    - durations   : list of per-frame durations in ms (only for animated)
    - is_animated : True if this file has more than one frame
    """
    candidates = []

    if custom_path:
        candidates.append(custom_path)

    for name in ("bg.png", "bg.jpg", "bg.jpeg", "bg.gif", "bg.webp",
                 "background.png", "background.jpg", "background.gif",
                 "background.webp"):
        # Bundled / source location (via sys._MEIPASS when frozen)
        candidates.append(resource_path(name))
        # Working directory, so users can drop a bg next to the exe
        candidates.append(os.path.join(os.getcwd(), name))
        # Original script dir (works when running from source)
        candidates.append(os.path.join(SCRIPT_DIR, name))

    seen = set()
    for p in candidates:
        if not p or p in seen:
            continue
        seen.add(p)
        if os.path.isfile(p):
            try:
                return _load_bg_file(p, w, h, palette)
            except Exception as e:
                print(f"[XZN] Failed to load BG ({p}): {e}")

    return ([_make_gradient_bg(w, h, palette)], [0], False)


def _load_bg_file(path, w, h, palette):
    """Load one background file; returns (frames, durations, animated)."""
    ext = os.path.splitext(path)[1].lower()

    # Animated formats: extract all frames via ImageSequence so that
    # GIF disposal methods are handled correctly by PIL.
    if ext in (".gif", ".webp"):
        try:
            im = Image.open(path)
            n_frames = getattr(im, "n_frames", 1)
            if n_frames > 1:
                frames = []
                durations = []
                for frame in ImageSequence.Iterator(im):
                    # Read the per-frame duration BEFORE moving on.
                    try:
                        dur = frame.info.get("duration", 100)
                        if not isinstance(dur, (int, float)) or dur <= 0:
                            dur = 100
                    except Exception:
                        dur = 100

                    rgb = _prepare_rgb_frame(frame, palette)
                    frames.append(_process_bg_frame(rgb, w, h, palette))
                    durations.append(int(dur))

                print(f"[XZN] Loaded animated BG: {path} "
                      f"({n_frames} frame(s))")
                return (frames, durations, True)
        except Exception as e:
            print(f"[XZN] Animated BG load failed ({path}): {e}")
            # Fall through to static handling

    # Static
    img = Image.open(path)
    rgb = _prepare_rgb_frame(img, palette)
    processed = _process_bg_frame(rgb, w, h, palette)
    print(f"[XZN] Loaded background: {path}")
    return ([processed], [0], False)


def load_background(w, h, palette, custom_path=None):
    """Backward-compatible helper: returns the first frame only."""
    frames, _durations, _animated = load_background_frames(
        w, h, palette, custom_path
    )
    return frames[0]


def _apply_hero_fade(img, palette):
    """
    Softly darken the bottom of the hero image.
    Replaces the Tk stipple rectangle, which does not work on macOS.
    """
    w, h = img.size
    fade_height = min(90, max(40, h // 3))

    is_light_theme = luminance(palette["BG"]) > 0.5
    if is_light_theme:
        fade_rgb = (255, 255, 255)
    else:
        fade_rgb = hex_to_rgb(palette["SUNKEN"])

    fade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(fade)
    for y in range(fade_height):
        t = y / max(1, fade_height - 1)
        alpha = int(180 * (t ** 1.6))
        y_actual = h - fade_height + y
        draw.line([(0, y_actual), (w, y_actual)],
                  fill=fade_rgb + (alpha,))

    img_rgba = img.convert("RGBA")
    return Image.alpha_composite(img_rgba, fade).convert("RGB")


def make_flat_button(parent, text, cmd, palette, width=None,
                     bg=None, fg=None, font=None, padx=14, pady=4):
    """
    Cross-platform button that respects bg/fg on macOS too.
    """
    bg_color = bg if bg is not None else palette["RAISED"]
    fg_color = fg if fg is not None else palette["WHITE"]
    font_use = font if font is not None else FONT_UI_B

    if IS_MAC or not IS_WINDOWS:
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


# ------------------------------------------------------------------------
# M3U parsing / writing helpers
# ------------------------------------------------------------------------
def write_m3u(path, playlist):
    with open(path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for p in playlist:
            f.write(p + "\n")


def read_m3u(path):
    base_dir = os.path.dirname(os.path.abspath(path))
    out = []
    missing = 0
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    continue
                p = line.replace("\\", os.sep).replace("/", os.sep)
                if not os.path.isabs(p):
                    p = os.path.join(base_dir, p)
                p = os.path.normpath(p)
                if os.path.isfile(p):
                    out.append(p)
                else:
                    missing += 1
    except Exception as e:
        print(f"[XZN] Failed to read M3U '{path}': {e}")
        return [], 0
    if missing:
        print(f"[XZN] M3U import: {missing} path(s) not found and skipped")
    return out, missing


# ------------------------------------------------------------------------
# Tag reading / writing (mutagen)
# ------------------------------------------------------------------------
def read_tags(path):
    """
    Read the most common audio tags from a file.
    Returns a dict with keys: title, artist, album, genre, date, tracknumber.
    Missing values are empty strings.
    """
    result = {
        "title": "", "artist": "", "album": "",
        "genre": "", "date": "", "tracknumber": "",
    }
    if not _MUTAGEN_AVAILABLE or MutagenFile is None:
        return result
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return result
        for key in result.keys():
            val = audio.get(key)
            if val and isinstance(val, (list, tuple)) and len(val) > 0:
                result[key] = str(val[0]).strip()
            elif isinstance(val, str):
                result[key] = val.strip()
    except Exception as e:
        print(f"[XZN] Failed to read tags from '{path}': {e}")
    return result


def write_tags(path, tags):
    """
    Write the given tags to a file.
    Empty values remove the corresponding tag.
    Returns (success: bool, error_message: str).
    """
    if not _MUTAGEN_AVAILABLE or MutagenFile is None:
        return False, "mutagen is not installed."
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return False, "Could not open file for tag editing."
        for key, value in tags.items():
            try:
                if value:
                    audio[key] = [value]
                else:
                    if key in audio:
                        del audio[key]
            except Exception as e:
                print(f"[XZN] Failed to set tag '{key}': {e}")
        audio.save()
        return True, ""
    except PermissionError:
        return False, "Permission denied — file may be read-only."
    except Exception as e:
        return False, f"Failed to save tags: {e}"


# ------------------------------------------------------------------------
# ReplayGain (volume leveling)
# ------------------------------------------------------------------------
_RG_NUM = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def _rg_float(value):
    m = _RG_NUM.search(str(value or ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def _tag_first(v):
    """Reduce a tag value from any mutagen format (ID3 frame, Vorbis list,
    MP4 freeform bytes, APE value) to a plain string."""
    if hasattr(v, "text"):
        v = v.text
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    if isinstance(v, bytes):
        v = v.decode("utf-8", "ignore")
    return str(v)


def read_replaygain(path, *metadata_dicts):
    """
    Return (gain_db, peak) for a file, or None if it has no ReplayGain
    information. Looks at the FFmpeg metadata first (FLAC, Ogg, many
    MP3s), then at the tags via mutagen (ID3 TXXX, MP4 freeform, APE).
    Track gain is preferred, album gain is the fallback. Opus R128 gains
    are converted to the ReplayGain reference level.
    """
    tags = {}
    for md in metadata_dicts:
        try:
            for k, v in (md or {}).items():
                tags.setdefault(str(k).lower(), str(v))
        except Exception:
            _log_swallowed()

    if _MUTAGEN_AVAILABLE and "replaygain_track_gain" not in tags:
        try:
            mf = MutagenFile(path)
            if mf is not None and mf.tags is not None:
                for k, v in mf.tags.items():
                    # "TXXX:REPLAYGAIN_TRACK_GAIN", "----:com.apple.iTunes:
                    # replaygain_track_gain" → "replaygain_track_gain"
                    key = str(k).lower().rsplit(":", 1)[-1]
                    tags.setdefault(key, _tag_first(v))
        except Exception:
            _log_swallowed()

    for scope in ("track", "album"):
        gain = _rg_float(tags.get(f"replaygain_{scope}_gain"))
        if gain is not None:
            peak = _rg_float(tags.get(f"replaygain_{scope}_peak"))
            return gain, (peak if peak and peak > 0 else None)
        r128 = _rg_float(tags.get(f"r128_{scope}_gain"))
        if r128 is not None:
            # Q7.8 fixed point relative to -23 LUFS; ReplayGain uses -18.
            return r128 / 256.0 + 5.0, None
    return None


def replaygain_factor(rg, preamp_db=0.0):
    """Linear gain for a read_replaygain() result, limited so the track
    peak never exceeds full scale."""
    if not rg:
        return 1.0
    gain_db, peak = rg
    f = 10.0 ** ((gain_db + preamp_db) / 20.0)
    if peak:
        f = min(f, 1.0 / peak)
    return max(0.05, min(f, 8.0))


# ------------------------------------------------------------------------
class RingBuffer:
    def __init__(self, capacity_frames):
        self.buf = np.zeros((capacity_frames, 2), dtype=np.float32)
        self.r = 0
        self.w = 0
        self.lock = threading.Lock()

    def write(self, chunk):
        with self.lock:
            cap = self.buf.shape[0]
            m = len(chunk)
            if m >= cap:
                chunk = chunk[-cap:]
                m = cap
            end = self.w + m
            if end <= cap:
                self.buf[self.w:end] = chunk
            else:
                first = cap - self.w
                self.buf[self.w:] = chunk[:first]
                self.buf[:end - cap] = chunk[first:]
            self.w = end % cap

    def read(self, n):
        out = np.zeros((n, 2), dtype=np.float32)
        with self.lock:
            cap = self.buf.shape[0]
            avail = (self.w - self.r) % cap
            k = min(n, avail)
            if k:
                end = self.r + k
                if end <= cap:
                    out[:k] = self.buf[self.r:end]
                else:
                    first = cap - self.r
                    out[:first] = self.buf[self.r:]
                    out[first:k] = self.buf[:end - cap]
                self.r = end % cap
        return out

    def available(self):
        with self.lock:
            return (self.w - self.r) % self.buf.shape[0]

    def clear(self):
        with self.lock:
            self.r = self.w = 0


# ------------------------------------------------------------------------
class VideoFrameBuffer:
    def __init__(self, maxlen=64):
        self.dq = deque(maxlen=maxlen)
        self.lock = threading.Lock()
        self._last_shown = None

    def push(self, t, img):
        with self.lock:
            self.dq.append((t, img))

    def pop_due(self, t):
        chosen = None
        with self.lock:
            while self.dq and self.dq[0][0] <= t + 0.005:
                chosen = self.dq.popleft()
            while len(self.dq) > self.dq.maxlen - 4:
                self.dq.popleft()
        if chosen is not None:
            self._last_shown = chosen
        return chosen if chosen is not None else self._last_shown

    def size(self):
        with self.lock:
            return len(self.dq)

    def latest(self):
        with self.lock:
            if self.dq:
                return self.dq[-1]
        return None

    def clear(self):
        with self.lock:
            self.dq.clear()
            self._last_shown = None


# ------------------------------------------------------------------------
class Equalizer:
    def __init__(self, sample_rate, bands):
        self.fs = float(sample_rate)
        self.bands = list(bands)
        self.n = len(bands)
        self.gains = np.zeros(self.n, dtype=np.float64)
        self.enabled = True
        self.preamp = 0.0
        self.lock = threading.Lock()

        self.coeffs = np.tile(np.array([1.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float64),
                              (self.n, 1))
        self.z = np.zeros((2, self.n, 4), dtype=np.float64)
        # sosfilt layout: one row per section [b0, b1, b2, 1, a1, a2];
        # state shape (sections, 2, channels) for a (frames, channels) block.
        self.sos = np.zeros((self.n, 6), dtype=np.float64)
        self.zi = np.zeros((self.n, 2, 2), dtype=np.float64)
        self._recompute()

    def set_gains(self, gains_db):
        with self.lock:
            g = np.asarray(gains_db, dtype=np.float64)
            if g.shape[0] != self.n:
                return
            self.gains = np.clip(g, -12.0, 12.0)
            self._recompute()

    def set_enabled(self, en):
        with self.lock:
            self.enabled = bool(en)

    def set_preamp(self, db):
        with self.lock:
            self.preamp = float(max(-12.0, min(12.0, db)))

    def reset_state(self):
        with self.lock:
            self.z[:] = 0.0
            self.zi[:] = 0.0

    def _recompute(self):
        fs = self.fs
        for i, f0 in enumerate(self.bands):
            A = 10.0 ** (self.gains[i] / 40.0)
            w0 = 2.0 * np.pi * f0 / fs
            cw = np.cos(w0)
            sw = np.sin(w0)
            Q = 1.1
            alpha = sw / (2.0 * Q)
            b0 = 1.0 + alpha * A
            b1 = -2.0 * cw
            b2 = 1.0 - alpha * A
            a0 = 1.0 + alpha / A
            a1 = -2.0 * cw
            a2 = 1.0 - alpha / A
            self.coeffs[i, 0] = b0 / a0
            self.coeffs[i, 1] = b1 / a0
            self.coeffs[i, 2] = b2 / a0
            self.coeffs[i, 3] = a1 / a0
            self.coeffs[i, 4] = a2 / a0
        c = self.coeffs
        self.sos[:, 0:3] = c[:, 0:3]
        self.sos[:, 3] = 1.0
        self.sos[:, 4:6] = c[:, 3:5]

    def process(self, block):
        with self.lock:
            if not self.enabled:
                return
            pre = 10.0 ** (self.preamp / 20.0)
            if pre != 1.0:
                block *= pre
            if _SCIPY_AVAILABLE:
                y, self.zi = _sosfilt(self.sos, block.astype(np.float64),
                                      axis=0, zi=self.zi)
                block[:] = y
                np.clip(block, -1.0, 1.0, out=block)
                return
            coef = self.coeffs
            z = self.z
            n = self.n
            for ch in range(2):
                x = block[:, ch].astype(np.float64, copy=False)
                for i in range(n):
                    b0, b1, b2, a1, a2 = coef[i]
                    x1 = z[ch, i, 0]
                    x2 = z[ch, i, 1]
                    y1 = z[ch, i, 2]
                    y2 = z[ch, i, 3]
                    out = np.empty_like(x)
                    for k in range(len(x)):
                        xin = x[k]
                        y = b0 * xin + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
                        x2 = x1
                        x1 = xin
                        y2 = y1
                        y1 = y
                        out[k] = y
                    z[ch, i, 0] = x1
                    z[ch, i, 1] = x2
                    z[ch, i, 2] = y1
                    z[ch, i, 3] = y2
                    x = out
                block[:, ch] = x.astype(np.float32, copy=False)
            np.clip(block, -1.0, 1.0, out=block)


# ------------------------------------------------------------------------
class Player(threading.Thread):
    def __init__(self, events):
        super().__init__(daemon=True)
        self.events = events
        self.cmds = queue.Queue()
        self.ring = RingBuffer(SAMPLE_RATE * 8)
        self.vbuf = VideoFrameBuffer(maxlen=64)
        self.spec_buf = np.zeros(16384, dtype=np.float32)
        self.spec_pos = 0
        self.spec_lock = threading.Lock()
        self.state = "idle"
        self.duration = 0.0
        self.current_path = ""
        self.title_text = ""
        self.has_video = False
        self.video_size_native = (0, 0)
        # Set to False to skip video frames entirely (decode only). The app
        # keeps it True and drains the frame buffer itself, even while the
        # video window is hidden, so the picture is ready instantly.
        self.video_visible = True
        self._paused_pos = 0.0
        self._head_t = 0.0
        self._video_pts = 0.0
        self._t0_mono = time.monotonic()
        self._t0_media = 0.0
        self._clock_running = False
        self._gen = 0
        self._want_pause = False
        self._want_resume = False
        self._want_stop = False
        self._pending_seek = None
        self._skip_until = 0.0
        self._vol_t = 0.85
        self._bal_t = 0.0
        self._mute = False
        self._vol = 0.85
        self._bal = 0.0

        self._played_frames = 0
        self._audio_start_frame = None
        self._audio_start_media = 0.0
        self._clock_lock = threading.Lock()
        self._out_latency = 0.15

        self.eq = Equalizer(SAMPLE_RATE, EQ_BANDS)

        # Gapless playback: the app installs a callable that returns the
        # path of the track that should follow the current one (or None).
        # It is called from this thread shortly before a track ends.
        self.next_provider = None
        # Display name for a path (set by the app: playlist names, CD titles)
        self.name_for = os.path.basename
        self.gapless = True
        # ReplayGain: linear gain applied to the samples being decoded.
        self.replaygain_enabled = True
        self._dec_rg = None

        self.stream = None
        try:
            self.stream = sd.OutputStream(
                samplerate=SAMPLE_RATE,
                channels=2,
                dtype="float32",
                callback=self._audio_cb,
                blocksize=1024,
                latency="high"
            )
            self.stream.start()
            try:
                self._out_latency = float(self.stream.latency)
                if self._out_latency <= 0 or self._out_latency > 0.5:
                    self._out_latency = 0.15
            except Exception:
                self._out_latency = 0.15
            print(f"[XZN] Audio output started OK (latency={self._out_latency*1000:.0f}ms)")
        except Exception as e:
            print(f"[XZN] WARNING: no audio output → {e}")

    def open_file(self, path, start_pos=0.0):
        self.current_path = path
        self.title_text = self.name_for(path)
        self._gen += 1
        self._want_pause = False
        self._want_resume = False
        self._want_stop = False
        self._pending_seek = None
        self.cmds.put(("open", path, self._gen, float(start_pos or 0.0)))

    def set_replaygain(self, enabled):
        self.replaygain_enabled = bool(enabled)

    def pause(self):
        self._want_pause = True

    def resume(self):
        self._want_resume = True

    def stop(self):
        self._want_stop = True
        self.ring.clear()
        self.vbuf.clear()

    def seek(self, t):
        if not self.current_path or self.state == "idle":
            return
        self._pending_seek = max(0.0, min(t, max(0.0, self.duration - 0.25)))

    def set_volume(self, v):
        self._vol_t = max(0.0, min(1.0, v))

    def set_balance(self, b):
        self._bal_t = max(-1.0, min(1.0, b))

    def set_mute(self, m):
        self._mute = bool(m)

    def set_eq_gains(self, gains_db):
        self.eq.set_gains(gains_db)

    def set_eq_enabled(self, en):
        self.eq.set_enabled(en)
        if en:
            self.eq.reset_state()

    def set_eq_preamp(self, db):
        self.eq.set_preamp(db)

    def position(self):
        if self.state == "playing":
            with self._clock_lock:
                if self._audio_start_frame is not None:
                    played = self._played_frames
                    start_frame = self._audio_start_frame
                    start_media = self._audio_start_media
                    p = start_media + (played - start_frame) / SAMPLE_RATE
                    return min(p, self.duration) if self.duration > 0 else p
            if not self._clock_running:
                return self._t0_media
            p = self._t0_media + (time.monotonic() - self._t0_mono)
            return min(p, self.duration) if self.duration > 0 else p
        if self.state == "paused":
            return self._paused_pos
        return 0.0

    def run(self):
        print("[XZN] Player thread started")
        while True:
            try:
                cmd = self.cmds.get(timeout=0.25)
            except queue.Empty:
                continue
            if cmd[0] == "open":
                try:
                    self._play_file(cmd[1], cmd[2], cmd[3])
                except Exception as e:
                    print(f"[XZN] Player thread crashed: {e}")
                    traceback.print_exc()
                    self.state = "idle"
                    self.events.put(("state", "idle"))
                    self.events.put(("error", str(e)))
            elif cmd[0] == "quit":
                break

    @staticmethod
    def _is_cover_art(s):
        """Album art embedded in audio files (MP3/M4A/FLAC) shows up as a
        one-frame 'video' stream; it must not open the video window."""
        try:
            disp = getattr(av.stream, "Disposition", None)
            if disp is not None and (s.disposition & disp.attached_pic):
                return True
        except Exception:
            _log_swallowed()
        try:
            name = (s.codec_context.name or "").lower()
            if name in ("mjpeg", "png", "bmp", "gif", "webp", "tiff") and (s.frames or 0) <= 1:
                return True
        except Exception:
            _log_swallowed()
        return False

    def _pick_streams(self, cont):
        vstreams, astreams = [], []
        for s in cont.streams:
            try:
                if s.type == "video" and s.codec_context is not None:
                    if (s.width or 0) * (s.height or 0) > 64 * 64 and not self._is_cover_art(s):
                        vstreams.append(s)
                elif s.type == "audio":
                    astreams.append(s)
            except Exception:
                _log_swallowed()
        v = max(vstreams, key=lambda s: (s.width or 0) * (s.height or 0)) if vstreams else None
        a = astreams[0] if astreams else None
        return v, a

    def _enable_hw_threads(self, cont):
        for s in cont.streams:
            try:
                cc = s.codec_context
                if cc is None:
                    continue
                if s.type == "video":
                    cc.thread_type = "AUTO"
                    cc.thread_count = 0
            except Exception as e:
                print(f"[XZN] thread setup failed for stream {s.index}: {e}")

    def _collect_meta(self, cont, vs, as_):
        md = cont.metadata or {}
        title = (md.get("title") or "").strip()
        artist = (md.get("artist") or md.get("album_artist") or "").strip()
        duration = 0.0
        for s in (vs, as_):
            if s is not None and getattr(s, "duration", None) and s.time_base:
                try:
                    d = float(s.duration * s.time_base)
                    if d > 0:
                        duration = max(duration, d)
                except Exception:
                    _log_swallowed()
        if not duration:
            try:
                duration = float(cont.duration) / 1_000_000
            except Exception:
                duration = 0.0
        vinfo = None
        if vs is not None:
            fps = None
            try:
                r = vs.average_rate or vs.base_rate
                if r and float(r) > 1:
                    fps = round(float(r), 2)
            except Exception:
                _log_swallowed()
            vinfo = (vs.codec_context.name, vs.width, vs.height, fps)
        ainfo = None
        if as_ is not None:
            rate = getattr(as_, "rate", None) or (as_.codec_context.sample_rate if as_.codec_context else 44100)
            ch = getattr(as_, "channels", None) or (as_.codec_context.channels if as_.codec_context else 2)
            ainfo = (as_.codec_context.name, int(rate), int(ch))
        return {
            "title": title, "artist": artist, "duration": duration,
            "video": vinfo, "audio": ainfo,
            "format": getattr(cont.format, "name", "") or ""
        }

    def _detect_pts_offset(self, cont, vstream, astream):
        v_start = None
        a_start = None
        try:
            pk_iter = cont.demux()
            for pk in pk_iter:
                try:
                    if pk.pts is None:
                        continue
                    st = pk.stream
                    tb = getattr(st, "time_base", None)
                    if tb is None:
                        continue
                    sec = float(pk.pts * tb)
                except Exception:
                    continue

                if vstream is not None and st.index == vstream.index and v_start is None:
                    v_start = sec
                if astream is not None and st.index == astream.index and a_start is None:
                    a_start = sec
                if (vstream is None or v_start is not None) and \
                   (astream is None or a_start is not None):
                    break
            del pk_iter
        except Exception as e:
            print(f"[XZN] PTS-Peek failed: {e}")

        try:
            cont.seek(0)
        except Exception as e:
            print(f"[XZN] cont.seek(0) failed: {e}")

        if a_start is not None:
            offset = a_start
        elif v_start is not None:
            offset = v_start
        else:
            offset = 0.0
        return offset

    def _drain_ring(self, timeout=8.0, poll=0.02):
        t0 = time.monotonic()
        while not self._want_stop:
            if self.ring.available() == 0:
                break
            if time.monotonic() - t0 > timeout:
                print(f"[XZN] drain timeout after {timeout:.1f}s "
                      f"({self.ring.available()} frames left)")
                return
            time.sleep(poll)
        if not self._want_stop:
            wait = max(0.0, self._out_latency) + 0.05
            slept = 0.0
            while slept < wait and not self._want_stop:
                chunk = min(0.02, wait - slept)
                time.sleep(chunk)
                slept += chunk

    # --- Track contexts ------------------------------------------------
    # A "track context" bundles everything needed to decode one file:
    # the container, its streams, the resampler and the demux iterator.
    # Normally one context is both played and decoded. For gapless
    # playback the next track is opened and decoded into the ring buffer
    # while the tail of the current one is still being heard; the switch
    # (title, duration, clock) happens when the first sample of the new
    # track actually reaches the speakers.

    def _new_resampler(self):
        try:
            return av.AudioResampler(format="fltp", layout="stereo",
                                     rate=SAMPLE_RATE)
        except Exception as e:
            print(f"[XZN] Resampler create failed: {e}")
            return None

    def _open_track(self, path):
        """Open a file and build its track context. Raises if the file
        can't be opened; returns None if it has no playable streams."""
        cont, media_file = open_media(path)
        try:
            self._enable_hw_threads(cont)
            vstream, astream = self._pick_streams(cont)
            if vstream is None and astream is None:
                cont.close()
                return None
            meta = self._collect_meta(cont, vstream, astream)
            time_offset = self._detect_pts_offset(cont, vstream, astream)
            rg = None
            if astream is not None and not is_cdda(path):
                try:
                    rg = read_replaygain(path, cont.metadata,
                                         getattr(astream, "metadata", None))
                except Exception:
                    _log_swallowed()
            if rg:
                print(f"[XZN] ReplayGain: {rg[0]:+.2f} dB"
                      + (f", peak {rg[1]:.3f}" if rg[1] else ""))
            return {
                "path": path, "cont": cont,
                "vstream": vstream, "astream": astream,
                "meta": meta, "time_offset": time_offset, "rg": rg,
                "resampler": self._new_resampler() if astream is not None else None,
                "demux": cont.demux(), "flushed": False, "file": media_file,
            }
        except Exception:
            try:
                cont.close()
                if media_file is not None:
                    media_file.close()
            except Exception:
                _log_swallowed()
            raise

    def _close_track(self, ctx):
        if ctx is None:
            return
        try:
            ctx["cont"].close()
            if ctx.get("file") is not None:
                ctx["file"].close()
        except Exception:
            _log_swallowed()

    def _flush_track(self, ctx):
        """Push the resampler's last samples into the ring buffer."""
        if ctx is None or ctx["flushed"] or ctx["resampler"] is None:
            return
        ctx["flushed"] = True
        try:
            for rf in ctx["resampler"].resample(None):
                self._write_audio_frame(rf)
        except Exception as e:
            print(f"[XZN] resampler flush failed: {e}")

    def _prepare_gapless(self, cur):
        """
        Called once the current track is fully decoded. Opens the next
        track and returns a pending switch, or None if playback should
        simply end (no next track, video involved, or an error).
        """
        if not self.gapless or cur["vstream"] is not None or self.next_provider is None:
            return None
        try:
            nxt_path = self.next_provider()
        except Exception as e:
            print(f"[XZN] next_provider failed: {e}")
            return None
        if not nxt_path or not (is_cdda(nxt_path) or os.path.isfile(nxt_path)):
            return None
        try:
            ctx = self._open_track(nxt_path)
        except Exception as e:
            print(f"[XZN] Gapless: could not open next track: {e}")
            return None
        if ctx is None:
            return None
        if ctx["vstream"] is not None or ctx["astream"] is None:
            # Videos get the normal start-up (window, sync, prebuffer).
            self._close_track(ctx)
            return None

        # The new track's first sample is heard once everything already
        # in the ring buffer and the output device has been played.
        with self._clock_lock:
            switch_frame = (self._played_frames + self.ring.available()
                            + int(self._out_latency * SAMPLE_RATE))
        self._head_t = 0.0
        self._skip_until = 0.0
        self._dec_rg = ctx["rg"]
        print(f"[XZN] Gapless: queued {os.path.basename(nxt_path)}")
        return {"ctx": ctx, "switch_frame": switch_frame}

    def _commit_gapless(self, pending):
        """The queued track has become audible: make it the current one."""
        ctx = pending["ctx"]
        meta = ctx["meta"]
        self.current_path = ctx["path"]
        self.title_text = self.name_for(ctx["path"])
        self.duration = meta["duration"]
        self.has_video = False
        self.video_size_native = (0, 0)
        self._paused_pos = 0.0
        with self._clock_lock:
            self._audio_start_frame = pending["switch_frame"]
            self._audio_start_media = 0.0
        self._t0_media = 0.0
        self._t0_mono = time.monotonic()
        self.events.put(("advanced", ctx["path"]))
        self.events.put(("meta", meta))
        print(f"[XZN] Gapless: now playing {self.title_text}")

    def _play_file(self, path, gen, start_pos=0.0):
        print(f"[XZN] Opening: {path}")
        try:
            cur = self._open_track(path)
        except Exception as e:
            print(f"[XZN] OPEN FAILED: {e}")
            traceback.print_exc()
            self.events.put(("error", f"Could not open file:\n{e}"))
            return
        if cur is None:
            self.events.put(("error", "No playable audio or video streams found."))
            return

        dec = cur          # the context whose packets are being decoded
        pending = None     # gapless: next track decoded, not yet audible
        dec_eof = False    # dec has no more packets
        draining = False   # last track decoded, ring buffer playing out

        def abandon_pending():
            # Pause/seek during a gapless hand-over: drop the prepared
            # next track and continue with the one still being heard.
            nonlocal pending, dec, dec_eof, draining
            if pending is not None:
                self._close_track(pending["ctx"])
                pending = None
                print("[XZN] Gapless: hand-over cancelled")
            dec = cur
            dec_eof = False
            draining = False
            self._dec_rg = cur["rg"]

        try:
            self.eq.reset_state()
            meta = cur["meta"]
            self.duration = meta["duration"]
            self.state = "playing"
            self._paused_pos = 0.0
            self._head_t = 0.0
            self._video_pts = 0.0
            self._skip_until = 0.0
            self._t0_media = 0.0
            self._t0_mono = time.monotonic()
            self._clock_running = False
            self._dec_rg = cur["rg"]
            self.ring.clear()
            self.vbuf.clear()

            with self._clock_lock:
                self._audio_start_frame = None
                self._audio_start_media = 0.0

            vstream = cur["vstream"]
            self.has_video = (vstream is not None)
            self.video_size_native = (vstream.width, vstream.height) if vstream is not None else (0, 0)
            self.events.put(("meta", meta))
            self.events.put(("state", "playing"))

            # Resume where the user left off last session.
            if start_pos and start_pos > 1.0 and (self.duration <= 0 or start_pos < self.duration - 1.0):
                self._pending_seek = start_pos

            packet_count = 0
            has_video = vstream is not None
            has_audio = cur["astream"] is not None
            prebuffer_done = False

            PREBUFFER  = int(SAMPLE_RATE * 0.5)
            HIGH_WATER = int(SAMPLE_RATE * 1.5)

            while not self._want_stop and self._gen == gen:

                # Gapless: has the queued track reached the speakers?
                if pending is not None:
                    with self._clock_lock:
                        reached = self._played_frames >= pending["switch_frame"]
                    if reached:
                        self._close_track(cur)
                        cur = pending["ctx"]
                        self._commit_gapless(pending)
                        pending = None
                        has_video = False
                        has_audio = True
                        continue

                if self._pending_seek is not None:
                    abandon_pending()
                    target = self._pending_seek
                    self._pending_seek = None
                    try:
                        cur["cont"].seek(int((target + cur["time_offset"]) * av.time_base),
                                         any_frame=False, backward=True)
                        if cur["astream"] is not None:
                            cur["resampler"] = self._new_resampler()
                            cur["flushed"] = False
                        self.ring.clear()
                        self.vbuf.clear()
                        self.eq.reset_state()
                        self._head_t = target
                        self._video_pts = target
                        self._t0_media = target
                        self._t0_mono = time.monotonic()
                        self._skip_until = target
                        self._clock_running = False
                        with self._clock_lock:
                            self._audio_start_frame = None
                            self._audio_start_media = target
                        prebuffer_done = False
                        cur["demux"] = cur["cont"].demux()
                        print(f"[XZN] Seek → {target:.2f}s")
                    except Exception as e:
                        print(f"[XZN] Seek failed: {e}")
                        traceback.print_exc()
                    continue

                if self._want_pause and self.state == "playing":
                    abandon_pending()
                    self._paused_pos = min(self.position(), self.duration) if self.duration > 0 else self.position()
                    self.ring.clear()
                    self.state = "paused"
                    self._want_pause = False
                    self._clock_running = False
                    self.events.put(("state", "paused"))
                    continue

                if self._want_resume and self.state == "paused":
                    self._t0_media = self._paused_pos
                    self._t0_mono = time.monotonic()
                    with self._clock_lock:
                        self._audio_start_frame = None
                        self._audio_start_media = self._paused_pos
                    self.state = "playing"
                    self._want_resume = False
                    self._clock_running = False
                    prebuffer_done = False
                    # Pausing discarded the decoded-but-unplayed audio
                    # (up to 1.5 s). Seek back to the exact pause point
                    # so nothing is skipped on resume.
                    self._pending_seek = self._paused_pos
                    self.events.put(("state", "playing"))
                    continue

                if self.state == "paused":
                    time.sleep(0.02)
                    continue

                if dec_eof:
                    if pending is not None:
                        # The queued track is already fully decoded (a very
                        # short file); wait until it becomes audible.
                        time.sleep(0.005)
                        continue
                    if not draining:
                        pending = self._prepare_gapless(cur)
                        if pending is not None:
                            dec = pending["ctx"]
                            dec_eof = False
                            continue
                        draining = True
                    # Nothing follows: let the buffered audio play out here
                    # (not in a blocking drain) so pause and seek still work
                    # during the last second of the track.
                    if self.ring.available() == 0:
                        break
                    time.sleep(0.01)
                    continue

                # Throttle on the video buffer only while frames are being
                # shown. With the window hidden nothing empties the buffer,
                # so waiting here would starve the audio until it cut out.
                if has_video and self.video_visible and self.vbuf.size() > 60:
                    time.sleep(0.003)
                    continue

                if has_audio:
                    avail = self.ring.available()
                    if avail > HIGH_WATER:
                        time.sleep(0.003)
                        continue

                if not prebuffer_done:
                    if has_audio:
                        if self.ring.available() >= PREBUFFER:
                            prebuffer_done = True
                            with self._clock_lock:
                                self._audio_start_frame = self._played_frames
                                buffered = self.ring.available() / SAMPLE_RATE
                                self._audio_start_media = max(
                                    0.0,
                                    self._head_t - buffered - self._out_latency
                                )
                                start_media = self._audio_start_media
                            self._t0_media = start_media
                            self._t0_mono = time.monotonic()
                            self._clock_running = True
                    else:
                        prebuffer_done = True
                        self._clock_running = True

                try:
                    packet = next(dec["demux"])
                except StopIteration:
                    self._flush_track(dec)
                    dec_eof = True
                    if not prebuffer_done:
                        # Track shorter than the prebuffer: start the clock.
                        prebuffer_done = True
                        with self._clock_lock:
                            self._audio_start_frame = self._played_frames
                            self._audio_start_media = 0.0
                        self._clock_running = True
                    continue
                except Exception as e:
                    print(f"[XZN] demux error: {e}")
                    if pending is not None:
                        abandon_pending()
                    break

                packet_count += 1

                try:
                    vs, as_ = dec["vstream"], dec["astream"]
                    if vs is not None and packet.stream.index == vs.index:
                        for vf in packet.decode():
                            self._on_video(vf, vs, dec["time_offset"])
                    elif as_ is not None and packet.stream.index == as_.index:
                        for af in packet.decode():
                            self._on_audio(af, as_, dec["resampler"], dec["time_offset"])
                except Exception as e:
                    print(f"[XZN] packet decode error: {e}")

            print(f"[XZN] Playback loop ended after {packet_count} packets")

        except Exception as e:
            print(f"[XZN] FATAL: {e}")
            traceback.print_exc()
            self.events.put(("error", str(e)))
        finally:
            if pending is not None:
                self._close_track(pending["ctx"])
                pending = None

            self._flush_track(cur)

            if not self._want_stop and self._gen == gen:
                try:
                    self._drain_ring(timeout=8.0)
                except Exception as e:
                    print(f"[XZN] drain failed: {e}")

            self._close_track(cur)

            self.ring.clear()
            self.vbuf.clear()
            user_stopped = self._want_stop
            self.state = "idle"
            self._clock_running = False
            with self._clock_lock:
                self._audio_start_frame = None
            self.events.put(("state", "idle"))
            if not user_stopped and self._gen == gen:
                self.events.put(("ended", cur["path"]))

    def _on_video(self, vf, vstream, time_offset):
        if self.state != "playing":
            return
        fps = 25.0
        try:
            r = vstream.average_rate or vstream.base_rate
            if r and float(r) > 1:
                fps = float(r)
        except Exception:
            _log_swallowed()

        tb = vstream.time_base
        if vf.pts is not None and tb is not None:
            t = float(vf.pts * tb) - time_offset
        else:
            t = self._video_pts + 1.0 / fps
        if t < -0.5 or t < self._skip_until - 0.4:
            return
        if not self.video_visible:
            self._video_pts = t
            return

        try:
            vw, vh = vf.width, vf.height
            max_w = 1280
            if vw > max_w:
                scale = max_w / vw
                vw = max_w
                vh = max(2, int(round(vf.height * scale)) // 2 * 2)
            vw = max(2, vw // 2 * 2)
            vh = max(2, vh // 2 * 2)
            rgb = vf.reformat(format="rgb24", width=vw, height=vh)
            arr = rgb.to_ndarray()
            img = Image.fromarray(arr)
        except Exception as e:
            print(f"[XZN] video reformat failed: {e}")
            try:
                img = vf.to_image()
            except Exception as e2:
                print(f"[XZN] video fallback failed: {e2}")
                return

        self.vbuf.push(t, img)
        self._video_pts = t

    def _write_audio_frame(self, rf):
        try:
            arr = rf.to_ndarray()
        except Exception:
            return

        if arr.ndim == 1:
            arr = np.stack([arr, arr], axis=0)
        elif arr.shape[0] == 1:
            arr = np.repeat(arr, 2, axis=0)
        elif arr.shape[0] > 2:
            arr = arr[:2]

        f = arr.T.astype(np.float32)

        if self.replaygain_enabled and self._dec_rg:
            f *= replaygain_factor(self._dec_rg)

        peak = np.max(np.abs(f)) if len(f) else 0.0
        if peak > 0.98:
            f *= 0.98 / peak

        if len(f):
            self.ring.write(f)
            mono = f.mean(axis=1)
            with self.spec_lock:
                buf, p = self.spec_buf, self.spec_pos
                m = len(mono)
                end = p + m
                if end <= buf.shape[0]:
                    buf[p:end] = mono
                else:
                    first = buf.shape[0] - p
                    buf[p:] = mono[:first]
                    buf[:end - buf.shape[0]] = mono[first:]
                self.spec_pos = end % buf.shape[0]

    def _on_audio(self, af, astream, resampler, time_offset):
        if self.state != "playing" or resampler is None or self._want_stop:
            return
        tb = astream.time_base
        if af.pts is not None and tb is not None:
            t = float(af.pts * tb) - time_offset
        else:
            t = self._head_t

        try:
            frames = resampler.resample(af)
        except Exception as e:
            print(f"[XZN] resample failed: {e}")
            return

        for rf in frames:
            if self._want_stop:
                return
            n = int(getattr(rf, "samples", 0) or rf.samples)
            dur = n / SAMPLE_RATE
            if t < self._skip_until:
                k = min(n, max(0, int(round((self._skip_until - t) * SAMPLE_RATE))))
                if k >= n:
                    t += dur
                    continue
                t += k / SAMPLE_RATE
            self._write_audio_frame(rf)
            t += dur

        if t > self._head_t:
            self._head_t = t

    def _audio_cb(self, outdata, frames, t, status):
        with self._clock_lock:
            self._played_frames += frames
        data = self.ring.read(frames)
        if data.shape[0] > 0:
            self.eq.process(data)
        target = 0.0 if self._mute else self._vol_t
        self._vol += (target - self._vol) * 0.12
        self._bal += (self._bal_t - self._bal) * 0.12
        gl = max(0.0, self._vol * (1.0 - self._bal))
        gr = max(0.0, self._vol * (1.0 + self._bal))
        outdata[:, 0] = data[:, 0] * gl
        outdata[:, 1] = data[:, 1] * gr


# ------------------------------------------------------------------------
class VideoWindow:
    def __init__(self, master, app=None):
        self.master = master
        self.app = app
        self.win = tk.Toplevel(master)
        self.win.title("Video – XZN")
        self.win.configure(bg="#0a0812")
        self.win.geometry("960x540")
        self.win.minsize(240, 160)
        self.win.protocol("WM_DELETE_WINDOW", self._user_close)
        self.win.withdraw()

        apply_window_icon(self.win)

        # macOS: share the app's menubar with this Toplevel so the
        # default Tk menu (Python/File/Edit/Window/Help) does not appear.
        if app is not None:
            try:
                app._attach_macos_menu(self.win)
            except Exception:
                _log_swallowed()

        self.canvas = tk.Canvas(self.win, bg="#0a0812", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self._photo = None
        self._idle_logo_photo = None
        self._last_gen = -1
        self._fullscreen = False
        self._normal_geometry = "960x540"
        self._last_canvas_size = (0, 0)
        self._scaled_cache = None

        # Idle-card state (used so the resize handler can redraw it).
        self._showing_idle_card = False
        self._idle_primary = ""
        self._idle_secondary = ""

        # --- Positioning state ------------------------------------------
        # These track whether the window has ever been placed next to
        # the main window, and what its last known (x, y) was. The
        # position is preserved across hide/show cycles and across
        # fit_to_video() resizes, so switching between videos no
        # longer resets the window's location.
        self._has_been_positioned = False
        self._last_xy = None

        self.canvas.bind("<Double-Button-1>", self.toggle_fullscreen)
        self.win.bind("<Escape>", lambda e: self.exit_fullscreen())

        # --- Fullscreen controls (OSD) ----------------------------------
        # In fullscreen, moving the mouse to the bottom edge shows a mini
        # player (bottom left) inside the video window; it hides again after 5 s
        # without mouse movement. Built lazily, rebuilt on theme change.
        self._osd = None
        self._osd_visible = False
        self._osd_hide_job = None
        self._osd_seek_dragging = False
        self.win.bind("<Motion>", self._on_osd_motion, add="+")

        # Player keys also work while the video window has the focus
        # (in fullscreen the main window is out of reach).
        if app is not None:
            keys = {
                "<space>": app.toggle_play,
                "<Left>": lambda: app.seek_by(-5), "<Right>": lambda: app.seek_by(5),
                "<Up>": lambda: app.nudge_volume(+5), "<Down>": lambda: app.nudge_volume(-5),
                "m": app.toggle_mute, "M": app.toggle_mute,
                "n": app.next_track, "N": app.next_track,
                "p": app.prev_track, "P": app.prev_track,
                "f": self.toggle_fullscreen, "F": self.toggle_fullscreen,
            }
            for seq, fn in keys.items():
                self.win.bind(seq, lambda e, fn=fn: (fn(), "break")[1])

        # Redraw the idle card when the window is resized and no video
        # frame is currently shown. Otherwise the card stays tiny.
        self.canvas.bind("<Configure>", self._on_canvas_configure)

    # --- Position helpers ----------------------------------------------
    def _get_xy(self):
        """
        Return the window's current top-left (x, y), but only if the
        window is actually mapped ("normal"). Withdrawn windows report
        odd/offscreen coordinates on some platforms, so we ignore those.
        """
        try:
            if self.win.state() != "normal":
                return None
            x = self.win.winfo_x()
            y = self.win.winfo_y()
            if x < -10000 or y < -10000:
                return None
            return (x, y)
        except Exception:
            return None

    def _position_next_to_main(self):
        """
        Place the window next to the main window on first show.

        Preferred order:
            1. Right of the main window, top edge aligned.
            2. Left of the main window.
            3. Below the main window.
            4. Centered on the primary screen (fallback).

        Only runs once — after that, the user's manual position wins.
        """
        if self._has_been_positioned:
            return

        try:
            mx = self.master.winfo_rootx()
            my = self.master.winfo_rooty()
            mw = self.master.winfo_width()
            mh = self.master.winfo_height()
        except Exception:
            mx = my = mw = mh = 0

        try:
            sw = self.win.winfo_screenwidth()
            sh = self.win.winfo_screenheight()
        except Exception:
            sw, sh = 1920, 1080

        # Use current size if available, otherwise sensible defaults.
        try:
            vw = max(self.win.winfo_width(), 320)
            vh = max(self.win.winfo_height(), 200)
        except Exception:
            vw, vh = 960, 540

        gap = 16

        # 1) Right of the main window, top aligned.
        x = mx + mw + gap
        y = my

        # 2) Not enough room on the right? Try left.
        if x + vw > sw:
            x = mx - vw - gap
            if x < 0:
                # 3) Try below the main window.
                x = max(0, mx)
                y = my + mh + gap
                if y + vh > sh:
                    # 4) Fallback: center on the primary screen.
                    x = max(0, (sw - vw) // 2)
                    y = max(0, (sh - vh) // 2)

        # Keep at least the title bar reachable on screen.
        x = max(0, min(x, sw - 200))
        y = max(0, min(y, sh - 100))

        self._last_xy = (x, y)
        self._has_been_positioned = True
        print(f"[XZN] Video window positioned next to main: +{x}+{y}")

    def _restore_xy(self, xy):
        """Re-apply a saved (x, y), clamped to the current screen size."""
        if xy is None:
            return
        try:
            if not self.is_alive():
                return
            sw = self.win.winfo_screenwidth()
            sh = self.win.winfo_screenheight()
            x = max(0, min(xy[0], sw - 200))
            y = max(0, min(xy[1], sh - 100))
            self.win.geometry(f"+{x}+{y}")
        except Exception:
            _log_swallowed()

    def is_alive(self):
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False

    def _on_canvas_configure(self, event=None):
        """
        If the canvas is currently showing the idle card, redraw it at
        the new size. If a video frame is showing, do nothing.
        """
        try:
            if self._scaled_cache is not None:
                # Video is playing: let update_frame handle redraws.
                return
            if getattr(self, "_showing_idle_card", False):
                primary = getattr(self, "_idle_primary", "")
                secondary = getattr(self, "_idle_secondary", "")
                self._draw_idle_card(primary, secondary)
        except Exception:
            _log_swallowed()

    def apply_palette(self, palette):
        try:
            if not self.is_alive():
                return
            self.win.configure(bg=palette["SUNKEN"])
            self.canvas.configure(bg=palette["SUNKEN"])
        except Exception:
            _log_swallowed()
        # The fullscreen controls are rebuilt in the new colors on demand.
        self._destroy_osd()

    # --- Fullscreen controls (OSD) --------------------------------------
    OSD_EDGE_PX = 110      # distance from the bottom edge that shows it
    OSD_HIDE_MS = 5000     # hide after this long without mouse movement

    def _build_osd(self):
        """Mini-player style control bar, placed bottom-left over the
        video. Mirrors the main window's state via refresh_osd()."""
        app = self.app
        p = app.palette
        osd = tk.Frame(self.win, bg=p["PANEL"], bd=0,
                       highlightthickness=1, highlightbackground=p["ACCENT_LO"])

        mrow = tk.Frame(osd, bg=p["PANEL"])
        mrow.pack(fill="x", padx=8, pady=(8, 3))
        logo_font = tkfont.Font(family="Helvetica", size=16, weight="bold")
        logo = tk.Canvas(mrow, bg=p["PANEL"], highlightthickness=0, bd=0,
                         width=logo_font.measure("XZN") + 8,
                         height=logo_font.metrics("linespace") + 4, cursor="hand2")
        app._draw_wordmark(logo, 4, 2, logo_font, "logo")
        # XZN = leave fullscreen (same as the double-click)
        logo.bind("<Button-1>", lambda e: self.exit_fullscreen())
        logo.pack(side="left", padx=(0, 8))
        self._osd_state = tk.Label(mrow, text="■", font=("Courier New", 12, "bold"),
                                   bg=p["SUNKEN"], fg=p["ACCENT"], width=3)
        self._osd_state.pack(side="left", padx=(0, 6), ipady=2)
        self._osd_marquee = tk.Label(mrow, text="", font=FONT_LCD, bg=p["SUNKEN"],
                                     fg=p["CYAN"], anchor="w", width=1)
        self._osd_marquee.pack(side="left", fill="x", expand=True, ipady=3)

        trow = tk.Frame(osd, bg=p["PANEL"])
        trow.pack(fill="x", padx=8, pady=(0, 2))
        self._osd_cur = tk.Label(trow, text="00:00", font=FONT_LG, bg=p["SUNKEN"], fg=p["ACCENT"])
        self._osd_cur.pack(side="left")
        tk.Label(trow, text="/", font=FONT_LG, bg=p["PANEL"], fg=p["GRAY"]).pack(side="left")
        self._osd_dur = tk.Label(trow, text="--:--", font=FONT_LG, bg=p["SUNKEN"], fg=p["ACCENT"])
        self._osd_dur.pack(side="left")
        vbox = tk.Frame(trow, bg=p["PANEL"])
        vbox.pack(side="right", padx=(0, 2))
        tk.Label(vbox, text="VOL", font=FONT_UI_B, bg=p["PANEL"], fg=p["GRAY"]).pack(side="left")
        self._osd_vol = tk.Scale(vbox, from_=0, to=100, orient="horizontal", length=130,
                                 showvalue=False, command=app._on_vol_cmd,
                                 bg=p["PANEL"], fg=p["ACCENT"], troughcolor=p["SUNKEN"],
                                 highlightthickness=0, sliderrelief="flat")
        self._osd_vol.set(app.vol_scale.get())
        self._osd_vol.pack(side="left", padx=(4, 0))
        _bind_scale_wheel(self._osd_vol, app._on_vol_cmd, lo=0, hi=100, step=5, round_to=1.0)

        brow = tk.Frame(osd, bg=p["PANEL"])
        brow.pack(fill="x", padx=8, pady=(0, 2))
        app._mkbtn(brow, "|◀", app.prev_track, icon="prev").pack(side="left")
        self._osd_play = app._mkbtn(brow, "▶", app.toggle_play, w=5, icon="play")
        self._osd_play.pack(side="left", padx=(2, 0))
        app._mkbtn(brow, "■", self._osd_stop, icon="stop").pack(side="left", padx=(2, 0))
        app._mkbtn(brow, "▶|", app.next_track, icon="next").pack(side="left", padx=(2, 0))
        self._osd_shuf = app._mkbtn(brow, "SHUF", app.toggle_shuffle, w=6, icon="shuffle")
        self._osd_shuf.pack(side="right")
        self._osd_rep = app._mkbtn(brow, "REP:OFF", app.cycle_repeat, w=9)
        self._osd_rep.pack(side="right", padx=(0, 2))

        self._osd_seek = tk.Scale(osd, from_=0, to=1000, orient="horizontal", length=120,
                                  showvalue=False, bg=p["PANEL"], fg=p["ACCENT"],
                                  troughcolor=p["SUNKEN"], highlightthickness=0,
                                  sliderrelief="flat")
        self._osd_seek.pack(fill="x", padx=8, pady=(2, 8))
        self._osd_seek.bind("<Button-1>", lambda e: setattr(self, "_osd_seek_dragging", True))
        self._osd_seek.bind("<ButtonRelease-1>", self._on_osd_seek_release)

        self._osd = osd
        self.refresh_osd()

    def _destroy_osd(self):
        self._cancel_osd_timer()
        if self._osd is not None:
            try:
                self._osd.destroy()
            except Exception:
                _log_swallowed()
        self._osd = None
        self._osd_visible = False

    def _osd_stop(self):
        # Stopping hides the video window; leave fullscreen first so the
        # window comes back in its normal size next time.
        self.exit_fullscreen()
        self.app.stop_play()

    def _on_osd_seek_release(self, event=None):
        self._osd_seek_dragging = False
        p = self.app.player
        if p.duration > 0:
            p.seek(self._osd_seek.get() / 1000.0 * p.duration)

    def refresh_osd(self):
        """Copy the main window's state into the fullscreen controls."""
        if self._osd is None:
            return
        app = self.app
        try:
            self._osd_state.config(text=app.state_lbl.cget("text"))
            self._osd_marquee.config(text=app.marquee.cget("text"))
            self._osd_cur.config(text=app.cur_lbl.cget("text"))
            self._osd_dur.config(text=app.dur_lbl.cget("text"))
            self._osd_rep.config(text=app.btn_rep.cget("text"), fg=app.btn_rep.cget("fg"),
                                 relief=app.btn_rep.cget("relief"))
            for src, dst in ((app.btn_play, self._osd_play), (app.btn_shuf, self._osd_shuf)):
                state = getattr(src, "_xzn_icon", None)
                if state and getattr(dst, "_xzn_icon", None) != state:
                    app._set_icon(dst, *state)
            if not self._osd_seek_dragging:
                self._osd_seek.set(app.seek.get())
        except Exception:
            _log_swallowed()

    def _show_osd(self):
        if self._osd is None:
            self._build_osd()
        if not self._osd_visible:
            self.refresh_osd()
            self._osd.place(relx=0.0, rely=1.0, anchor="sw", x=28, y=-28)
            self._osd.lift()
            self._osd_visible = True

    def _hide_osd(self):
        self._cancel_osd_timer()
        if self._osd is not None and self._osd_visible:
            self._osd.place_forget()
        self._osd_visible = False

    def _cancel_osd_timer(self):
        if self._osd_hide_job is not None:
            try:
                self.win.after_cancel(self._osd_hide_job)
            except Exception:
                _log_swallowed()
            self._osd_hide_job = None

    def _on_osd_motion(self, event=None):
        if not self._fullscreen or self.app is None:
            return
        try:
            y = self.win.winfo_pointery() - self.win.winfo_rooty()
            h = self.win.winfo_height()
        except Exception:
            return
        if not self._osd_visible:
            if y < h - self.OSD_EDGE_PX:
                return
            self._show_osd()
        # Every mouse movement while it is visible restarts the timer.
        self._cancel_osd_timer()
        self._osd_hide_job = self.win.after(self.OSD_HIDE_MS, self._hide_osd)

    def show(self):
        try:
            if not self.is_alive():
                return
            try:
                self.win.attributes("-alpha", 1.0)
            except Exception:
                _log_swallowed()

            # First show ever? Place next to the main window.
            self._position_next_to_main()

            self.win.deiconify()
            self.win.lift()

            # Restore the remembered position after deiconify, in case
            # the window manager reset it. Use after_idle so the WM has
            # already finished its own placement pass.
            if self._last_xy is not None:
                try:
                    self.win.after_idle(
                        lambda xy=self._last_xy: self._restore_xy(xy)
                    )
                except Exception:
                    self._restore_xy(self._last_xy)

            # If the canvas is empty (no frame shown yet), draw the idle
            # card so the user never sees a plain black rectangle.
            try:
                if not self.canvas.find_all():
                    title = ""
                    if self.app is not None:
                        try:
                            title = self.app.player.title_text or ""
                        except Exception:
                            title = ""
                    self._draw_idle_card("", title)
            except Exception:
                _log_swallowed()
        except Exception as e:
            print(f"[XZN] VideoWindow.show failed: {e}")

    def _user_close(self):
        """Closed via the window's X: stay hidden for the next videos too,
        until the user opens it again (VIDEO button or F)."""
        if self.app is not None:
            self.app._video_user_hidden = True
        self.hide()

    def hide(self):
        # Remember the position before withdrawing, so we can restore
        # it on the next show().
        if self._has_been_positioned:
            xy = self._get_xy()
            if xy is not None:
                self._last_xy = xy
        try:
            if self.is_alive():
                self.win.withdraw()
        except Exception:
            _log_swallowed()
        self._photo = None
        self._last_gen = -1
        self._scaled_cache = None

    def fit_to_video(self, native_w, native_h):
        """
        Resize the window for a video with the given native resolution.

        Crucially, the current (or remembered) window position is
        preserved, so switching between videos with different aspect
        ratios no longer causes the window to jump.
        """
        if native_w <= 0 or native_h <= 0:
            return
        try:
            sw = self.win.winfo_screenwidth()
            sh = self.win.winfo_screenheight()
        except Exception:
            return
        max_w = int(sw * 0.80)
        max_h = int(sh * 0.80)
        scale = min(max_w / native_w, max_h / native_h, 1.0)
        w = max(320, int(native_w * scale))
        h = max(200, int(native_h * scale))

        # Preserve position. Prefer the live position (window mapped),
        # fall back to the remembered one (window hidden).
        xy = self._get_xy()
        if xy is None:
            xy = self._last_xy

        if xy is not None:
            geom = f"{w}x{h}+{xy[0]}+{xy[1]}"
        else:
            # First ever call, before show() positions the window.
            geom = f"{w}x{h}"

        try:
            self.win.geometry(geom)
        except Exception:
            _log_swallowed()
        self._normal_geometry = geom

    def toggle_fullscreen(self, event=None):
        if self._fullscreen:
            self.exit_fullscreen()
        else:
            self.enter_fullscreen()

    def enter_fullscreen(self):
        if self._fullscreen:
            return
        try:
            self._normal_geometry = self.win.geometry()
            try:
                self.win.attributes("-alpha", 1.0)
            except Exception:
                _log_swallowed()
            self.win.attributes("-fullscreen", True)
            self._fullscreen = True
            # Keyboard focus to the video, so Space, arrows etc. work.
            self.win.focus_force()
        except Exception:
            _log_swallowed()

    def exit_fullscreen(self):
        if not self._fullscreen:
            return
        self._hide_osd()
        try:
            self.win.attributes("-fullscreen", False)
            self.win.geometry(self._normal_geometry)
            try:
                self.win.attributes("-alpha", 1.0)
            except Exception:
                _log_swallowed()
        except Exception:
            _log_swallowed()
        self._fullscreen = False

    def show_placeholder(self, primary_text="", secondary_text=""):
        """
        Show the XZN idle card. Called when no video frame is available
        (start of playback, audio-only files, after stop).
        """
        self._photo = None
        self._last_gen = -1
        self._scaled_cache = None
        self._showing_idle_card = True
        self._idle_primary = primary_text
        self._idle_secondary = secondary_text
        self._draw_idle_card(primary_text, secondary_text)

    def _draw_idle_card(self, primary_text="", secondary_text=""):
        """
        Draw the placeholder card shown when no video frame is available
        (audio-only files, paused-at-start, or before the first frame).

        Layout:
            - Large centered XZN logo (xzn-logo.png). The logo already
              contains the "XZN" wordmark, so we do NOT print it again.
            - Fallback: if the logo file is missing, draw a simple "XZN"
              wordmark instead, so the window never looks broken.
            - Optional secondary line (track title / status) underneath,
              using the same font as the playlist (Courier New 9).
        """
        try:
            if not self.is_alive():
                return
            self.canvas.delete("all")
            w = max(self.canvas.winfo_width(), 10)
            h = max(self.canvas.winfo_height(), 10)
            cx, cy = w // 2, h // 2
        except Exception:
            return

        # Remember we're on the idle card, so the resize handler can
        # redraw it if the user resizes the window.
        self._showing_idle_card = True
        self._idle_primary = primary_text
        self._idle_secondary = secondary_text

        # --- Big centred logo (if the file exists) -----------------------
        logo_drawn = False
        logo_size = 0
        logo_path = resource_path(LOGO_FILENAME)
        if os.path.isfile(logo_path):
            try:
                logo_size = max(120, min(320, int(min(w, h) * 0.35)))
                logo_img = Image.open(logo_path).convert("RGBA")
                logo_img = logo_img.resize((logo_size, logo_size),
                                           Image.Resampling.LANCZOS)
                # PhotoImage must be kept alive on self, otherwise Tk
                # garbage-collects it and the canvas shows nothing.
                self._idle_logo_photo = ImageTk.PhotoImage(logo_img)
                self.canvas.create_image(
                    cx, cy - 25,
                    image=self._idle_logo_photo,
                    anchor="center",
                )
                logo_drawn = True
            except Exception as e:
                print(f"[XZN] VideoWindow idle logo failed: {e}")
                logo_drawn = False

        # --- Fallback wordmark, only if the logo is missing --------------
        # (The real logo already contains the "XZN" text, so we must not
        # draw the wordmark when the logo is present.)
        if not logo_drawn:
            wordmark_y = cy
            font_word = ("Helvetica", 32, "bold")
            shadow = "#000000"
            main = "#d9c4ff"
            for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2),
                           (-1, -1), (1, -1), (-1, 1), (1, 1)):
                self.canvas.create_text(
                    cx + dx, wordmark_y + dy,
                    text="XZN", font=font_word,
                    fill=shadow, anchor="center",
                )
            self.canvas.create_text(
                cx, wordmark_y,
                text="XZN", font=font_word,
                fill=main, anchor="center",
            )

        # --- Optional secondary line (track title / status) --------------
        # Uses the same font as the playlist (Courier New 9), placed
        # a bit further below the logo so it has room to breathe.
        if secondary_text:
            try:
                font_sec = ("Courier New", 9)
                max_chars = max(20, int(w / 8))
                text = secondary_text
                if len(text) > max_chars:
                    text = text[:max_chars - 1] + "…"

                if logo_drawn and logo_size > 0:
                    # Logo centre is cy - 15; add a generous gap below it
                    # so the text does not sit right against the artwork.
                    offset_y = cy - 15 + (logo_size // 2) + 60
                else:
                    offset_y = cy + 70

                self.canvas.create_text(
                    cx, offset_y,
                    text=text, font=font_sec,
                    fill="#9a94b0", anchor="center",
                )
            except Exception:
                _log_swallowed()

    def update_frame(self, img, gen):
        if img is None or not self.is_alive():
            return
        try:
            cw = max(self.canvas.winfo_width(), 10)
            ch = max(self.canvas.winfo_height(), 10)
        except Exception:
            return

        if self._scaled_cache is not None:
            cgen, csize, _cphoto = self._scaled_cache
            if cgen == gen and csize == (cw, ch):
                return

        iw, ih = img.size
        scale = min(cw / iw, ch / ih)
        nw = max(2, int(iw * scale))
        nh = max(2, int(ih * scale))

        try:
            photo = ImageTk.PhotoImage(img.resize((nw, nh), Image.Resampling.LANCZOS))
        except Exception as e:
            print(f"[XZN] Video frame PhotoImage failed: {e}")
            return

        self._photo = photo
        self._scaled_cache = (gen, (cw, ch), photo)
        self._last_gen = gen
        self._last_canvas_size = (cw, ch)
        # A real frame is now on the canvas — leave idle-card mode.
        self._showing_idle_card = False

        try:
            self.canvas.delete("all")
            self.canvas.create_image(cw // 2, ch // 2, image=self._photo, anchor="center")
        except Exception as e:
            print(f"[XZN] Video canvas draw failed: {e}")


# ------------------------------------------------------------------------
# License window (full MIT text)
# ------------------------------------------------------------------------
def show_license_window(parent, palette, app=None):
    """
    Open a small window showing the full MIT license text.
    Read-only, scrollable, matching the app's look.
    Registers itself with the app so the alpha slider affects it too.
    """
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
    win.title("License – XZN")
    win.configure(bg=p["PANEL"])
    win.geometry("620x520")
    win.minsize(400, 300)
    win.transient(parent)
    apply_window_icon(win)

    # macOS: share the app's menubar with this Toplevel
    if app is not None:
        try:
            app._attach_macos_menu(win)
        except Exception:
            _log_swallowed()

    # --- Close handler that also unregisters the window -----------------
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

    # Footer FIRST, packed at the bottom, so it is always visible
    footer = tk.Frame(win, bg=p["PANEL"], padx=20, pady=14)
    footer.pack(side="bottom", fill="x")

    make_flat_button(footer, "Close", _close, palette=p,
                     padx=14, pady=4).pack(side="right")

    # Header (top)
    header = tk.Frame(win, bg=p["PANEL"], padx=20, pady=14)
    header.pack(side="top", fill="x")

    tk.Label(header, text="MIT LICENSE",
             font=("Helvetica", 12, "bold"),
             bg=p["PANEL"], fg=p["ACCENT"]).pack(anchor="w")

    tk.Frame(win, bg=p["ACCENT_LO"], height=1).pack(
        side="top", fill="x", padx=20
    )

    # Body (fills the rest)
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

    # Center on parent
    try:
        win.update_idletasks()
        mx = parent.winfo_rootx() + parent.winfo_width() // 2
        my = parent.winfo_rooty() + parent.winfo_height() // 2
        w = win.winfo_width()
        h = win.winfo_height()
        win.geometry(f"+{mx - w // 2}+{my - h // 2}")
    except Exception:
        _log_swallowed()

    # Register with the app so it participates in the alpha system,
    # then apply the current alpha immediately.
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


# ------------------------------------------------------------------------
class InfoWindow:
    def __init__(self, master, palette, app):
        self.master = master
        self.app = app
        self.win = tk.Toplevel(master)
        self.win.title("Info – XZN")
        self.win.configure(bg=palette["PANEL"])
        self.win.resizable(False, False)
        self.win.transient(master)
        apply_window_icon(self.win)

        # macOS: share the app's menubar with this Toplevel
        try:
            app._attach_macos_menu(self.win)
        except Exception:
            _log_swallowed()

        p = palette
        frame = tk.Frame(self.win, bg=p["PANEL"], padx=26, pady=22)
        frame.pack(fill="both", expand=True)

        # --- Logo + Title row ---
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
                logo_lbl.image = logo_photo  # keep reference
                logo_lbl.pack(side="left", padx=(0, 14))
            except Exception as e:
                print(f"[XZN] Info logo load failed: {e}")

        tk.Label(title_row, text="XENON-ZONES (XZN)",
                 font=("Helvetica", 15, "bold"),
                 bg=p["PANEL"], fg=p["ACCENT"]).pack(side="left", anchor="w")

        tk.Frame(frame, bg=p["ACCENT_LO"], height=1).pack(fill="x", pady=(8, 14))

        tk.Label(frame, text="Classic media player vibes and cyberpunk aesthetics.",
                 font=FONT_TAGLINE,
                 bg=p["PANEL"], fg=p["WHITE"],
                 justify="left", anchor="w",
                 wraplength=440).pack(anchor="w", pady=(0, 6))

        tk.Label(frame, text="No unnecessary clutter. Just music and videos.",
                 font=FONT_TAGLINE,
                 bg=p["PANEL"], fg=p["WHITE"],
                 justify="left", anchor="w",
                 wraplength=440).pack(anchor="w", pady=(0, 16))

        tk.Frame(frame, bg=p["ACCENT_LO"], height=1).pack(fill="x", pady=(0, 14))

        tk.Label(frame, text="Built with Python and a little help from AI.",
                 font=FONT_UI,
                 bg=p["PANEL"], fg=p["WHITE"],
                 justify="left", anchor="w",
                 wraplength=440).pack(anchor="w", pady=(0, 14))

        tk.Label(frame, text=f"Version {VERSION}",
                 font=FONT_UI,
                 bg=p["PANEL"], fg=p["GRAY"]).pack(anchor="w")

        tk.Label(frame, text="© 2026 Merecete",
                 font=FONT_UI,
                 bg=p["PANEL"], fg=p["GRAY"]).pack(anchor="w", pady=(2, 2))

        tk.Label(frame, text="Released under the MIT License.",
                 font=FONT_UI,
                 bg=p["PANEL"], fg=p["GRAY"]).pack(anchor="w", pady=(0, 16))

        # --- Button row: License + Close ---
        btn_row = tk.Frame(frame, bg=p["PANEL"])
        btn_row.pack(anchor="e", fill="x")

        make_flat_button(
            btn_row, "License",
            lambda: show_license_window(self.win, p, self.app),
            palette=p, padx=10, pady=4
        ).pack(side="left")

        # Opens xzn.log, e.g. to attach it to a bug report.
        if LOG_PATH:
            make_flat_button(
                btn_row, "Log",
                lambda: open_with_system(LOG_PATH),
                palette=p, padx=10, pady=4
            ).pack(side="left", padx=(6, 0))

        # --- Close handler that also unregisters ------------------------
        def _close_info():
            try:
                if self.win in self.app._extra_alpha_windows:
                    self.app._extra_alpha_windows.remove(self.win)
            except Exception:
                _log_swallowed()
            try:
                self.win.destroy()
            except Exception:
                _log_swallowed()

        make_flat_button(
            btn_row, "Close",
            _close_info,
            palette=p, padx=14, pady=4
        ).pack(side="right")

        self.win.protocol("WM_DELETE_WINDOW", _close_info)
        self.win.bind("<Escape>", lambda e: _close_info())

        self.win.update_idletasks()
        mx = master.winfo_rootx() + master.winfo_width() // 2
        my = master.winfo_rooty() + master.winfo_height() // 2
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        self.win.geometry(f"+{mx - w // 2}+{my - h // 2}")

        # Register with the app for alpha participation
        try:
            self.app._extra_alpha_windows.append(self.win)
        except Exception:
            _log_swallowed()


# ------------------------------------------------------------------------
# Keyboard & mouse reference (F1)
# ------------------------------------------------------------------------
SHORTCUTS = [
    ("PLAYBACK", [
        ("Space",       "Play / Pause"),
        ("← / →",       "Seek 5 s back / forward"),
        ("↑ / ↓",       "Volume up / down"),
        ("M",           "Mute"),
        ("N",           "Next track"),
        ("P",           "Previous track (or restart the current one)"),
        ("Media keys",  "Play/Pause, Next, Previous, Stop"),
    ]),
    ("WINDOWS", [
        ("E",           "Equalizer"),
        ("C",           "Color scheme"),
        ("F",           "Video fullscreen on / off"),
        ("Ctrl+W",      "Mini player on / off"),
        ("Esc",         "Close window / leave fullscreen"),
        ("F1",          "This overview"),
    ]),
    ("PLAYLIST", [
        ("Type",        "Search (mouse over the playlist)"),
        ("↑ / ↓",       "Previous / next search hit"),
        ("Enter",       "Play search hit"),
        ("Double-click", "Play track"),
        ("Right-click", "Edit tags (also Ctrl+click)" if IS_MAC else "Edit tags"),
        ("Shift+click", "Select a range"),
        ("Drag & drop", "Add files and folders"),
    ]),
    ("MOUSE", [
        ("Click XZN",   "Mini player on / off"),
        ("Wheel",       "Fine steps on sliders, 5 s on the timeline"),
        ("Double-click video", "Fullscreen"),
        ("Bottom edge", "Controls in video fullscreen"),
        ("Right-click BG", "Remove background image"),
    ]),
]


class ShortcutsWindow:
    """Reference of all keys and mouse actions. Opened and closed with
    F1 (or the Close button / Esc). Follows the color scheme and the
    window alpha like every other XZN window."""

    def __init__(self, master, app):
        self.app = app
        p = app.palette
        self.win = tk.Toplevel(master)
        self.win.title("Shortcuts – XZN")
        self.win.configure(bg=p["PANEL"])
        self.win.resizable(False, False)
        self.win.transient(master)
        apply_window_icon(self.win)
        try:
            app._attach_macos_menu(self.win)
        except Exception:
            _log_swallowed()

        frame = tk.Frame(self.win, bg=p["PANEL"], padx=22, pady=18)
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text="KEYS & MOUSE", font=("Helvetica", 15, "bold"),
                 bg=p["PANEL"], fg=p["ACCENT"]).pack(anchor="w")
        tk.Frame(frame, bg=p["ACCENT_LO"], height=1).pack(fill="x", pady=(8, 10))

        # Two columns of sections so the window stays compact.
        cols = tk.Frame(frame, bg=p["PANEL"])
        cols.pack(fill="both", expand=True)
        half = (len(SHORTCUTS) + 1) // 2
        for ci, sections in enumerate((SHORTCUTS[:half], SHORTCUTS[half:])):
            col = tk.Frame(cols, bg=p["PANEL"])
            col.grid(row=0, column=ci, sticky="nw", padx=(0, 22) if ci == 0 else 0)
            r = 0
            for title, rows in sections:
                tk.Label(col, text=title, font=("Courier New", 8, "bold"),
                         bg=p["PANEL"], fg=p["CYAN"]).grid(
                    row=r, column=0, columnspan=2, sticky="w", pady=(0 if r == 0 else 10, 3))
                r += 1
                for key, desc in rows:
                    tk.Label(col, text=key, font=("Courier New", 9, "bold"),
                             bg=p["SUNKEN"], fg=p["ACCENT"], padx=6, pady=1).grid(
                        row=r, column=0, sticky="w", pady=1)
                    tk.Label(col, text=desc, font=FONT_UI, bg=p["PANEL"],
                             fg=p["WHITE"], anchor="w").grid(
                        row=r, column=1, sticky="w", padx=(8, 0), pady=1)
                    r += 1

        tk.Frame(frame, bg=p["ACCENT_LO"], height=1).pack(fill="x", pady=(12, 10))
        btn_row = tk.Frame(frame, bg=p["PANEL"])
        btn_row.pack(fill="x")
        tk.Label(btn_row, text="F1 or Esc closes this window.", font=FONT_UI,
                 bg=p["PANEL"], fg=p["GRAY"]).pack(side="left")
        make_flat_button(btn_row, "Close", self.close, palette=p,
                         padx=14, pady=4).pack(side="right")

        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.bind("<Escape>", lambda e: self.close())

        self.win.update_idletasks()
        mx = master.winfo_rootx() + master.winfo_width() // 2
        my = master.winfo_rooty() + master.winfo_height() // 2
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        self.win.geometry(f"+{max(0, mx - w // 2)}+{max(0, my - h // 2)}")
        try:
            app._extra_alpha_windows.append(self.win)
        except Exception:
            _log_swallowed()
        # Take the keyboard focus right away, so Esc works without first
        # clicking into the window (F1 works from anywhere anyway).
        try:
            self.win.lift()
            self.win.focus_force()
        except Exception:
            _log_swallowed()

    def is_alive(self):
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False

    def close(self):
        try:
            if self.win in self.app._extra_alpha_windows:
                self.app._extra_alpha_windows.remove(self.win)
        except Exception:
            _log_swallowed()
        try:
            self.win.destroy()
        except Exception:
            _log_swallowed()


# ------------------------------------------------------------------------
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

        # macOS: share the app's menubar with this Toplevel
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

        self.swatches = []
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
            self.swatches.append((color, border))

        btn_row = tk.Frame(frame, bg=p["PANEL"])
        btn_row.pack(fill="x", pady=(14, 0))

        make_flat_button(btn_row, "Default (Purple)", self._reset, palette=p,
                         padx=10, pady=4).pack(side="left")
        make_flat_button(btn_row, "Close", self.win.destroy, palette=p,
                         padx=10, pady=4).pack(side="right")

        self.win.update_idletasks()
        mx = master.winfo_rootx() + master.winfo_width() // 2
        my = master.winfo_rooty() + master.winfo_height() // 2
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        self.win.geometry(f"+{mx - w // 2}+{my - h // 2}")
        self.win.bind("<Escape>", lambda e: self.win.destroy())

    def _pick(self, color):
        try:
            self.win.destroy()
        except Exception:
            _log_swallowed()
        self.app.apply_accent(color, persist=True, rebuild=True)

    def _reset(self):
        try:
            self.win.destroy()
        except Exception:
            _log_swallowed()
        self.app.apply_accent(DEFAULT_PALETTE["ACCENT"], persist=True, rebuild=True)


# ------------------------------------------------------------------------
# ------------------------------------------------------------------------
# Button icons
# ------------------------------------------------------------------------
# Drawn with PIL instead of Unicode symbols: symbol glyphs look different
# (or are missing) depending on platform and font, drawn icons are crisp
# everywhere and take the theme colors. Drawn 4x larger, then scaled down
# for smooth edges. Coordinates below are on a 0..100 grid.

def draw_icon(name, size, color):
    """Return a size x size RGBA PIL image of the named icon."""
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rgb = hex_to_rgb(color) + (255,)

    def P(*pts):
        return [(x * s / 100.0, y * s / 100.0) for x, y in pts]

    def box(x0, y0, x1, y1):
        return [x0 * s / 100.0, y0 * s / 100.0, x1 * s / 100.0, y1 * s / 100.0]

    w = max(1, int(s * 0.09))          # stroke width

    def arrow_line(pts, head=True):
        d.line(P(*pts), fill=rgb, width=w, joint="curve")
        if head:
            (x0, y0), (x1, y1) = pts[-2], pts[-1]
            ang = np.arctan2(y1 - y0, x1 - x0)
            L, spread = 16, 0.55
            a = (x1 - L * np.cos(ang - spread), y1 - L * np.sin(ang - spread))
            b = (x1 - L * np.cos(ang + spread), y1 - L * np.sin(ang + spread))
            d.polygon(P((x1 + 4 * np.cos(ang), y1 + 4 * np.sin(ang)), a, b), fill=rgb)

    if name == "play":
        d.polygon(P((24, 14), (24, 86), (84, 50)), fill=rgb)
    elif name == "pause":
        d.rectangle(box(22, 14, 42, 86), fill=rgb)
        d.rectangle(box(58, 14, 78, 86), fill=rgb)
    elif name == "stop":
        d.rectangle(box(20, 20, 80, 80), fill=rgb)
    elif name == "prev":
        d.rectangle(box(16, 18, 28, 82), fill=rgb)
        d.polygon(P((84, 18), (84, 82), (32, 50)), fill=rgb)
    elif name == "next":
        d.rectangle(box(72, 18, 84, 82), fill=rgb)
        d.polygon(P((16, 18), (16, 82), (68, 50)), fill=rgb)
    elif name == "shuffle":
        arrow_line([(8, 28), (30, 28), (62, 72), (88, 72)])
        arrow_line([(8, 72), (30, 72), (62, 28), (88, 28)])
    elif name == "eq":
        for x, knob in ((22, 62), (50, 30), (78, 52)):
            d.line(P((x, 12), (x, 88)), fill=rgb, width=max(1, w // 2))
            d.rectangle(box(x - 11, knob - 7, x + 11, knob + 7), fill=rgb)
    elif name in ("volume", "mute"):
        d.polygon(P((10, 36), (28, 36), (50, 16), (50, 84), (28, 64), (10, 64)), fill=rgb)
        if name == "volume":
            d.arc(box(40, 30, 66, 70), start=-55, end=55, fill=rgb, width=w)
            d.arc(box(42, 14, 88, 86), start=-55, end=55, fill=rgb, width=w)
        else:
            d.line(P((62, 34), (90, 66)), fill=rgb, width=w)
            d.line(P((62, 66), (90, 34)), fill=rgb, width=w)
    elif name == "palette":
        # painter's palette: solid shape with paint wells and a thumb notch
        clear = (0, 0, 0, 0)
        d.ellipse(box(6, 10, 94, 90), fill=rgb)
        for cx, cy in ((30, 36), (50, 25), (71, 34), (27, 60)):
            d.ellipse(box(cx - 8, cy - 8, cx + 8, cy + 8), fill=clear)
        d.ellipse(box(58, 60, 100, 102), fill=clear)
        d.ellipse(box(50, 52, 66, 68), fill=clear)
    return img.resize((size, size), Image.Resampling.LANCZOS)


class Tooltip:
    """Small explanation shown after hovering over a widget for a moment."""

    DELAY_MS = 600

    def __init__(self, widget, text, palette):
        self.widget, self.text, self.palette = widget, text, palette
        self._job = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, event=None):
        self._cancel()
        try:
            self._job = self.widget.after(self.DELAY_MS, self._show)
        except Exception:
            _log_swallowed()

    def _cancel(self):
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                _log_swallowed()
            self._job = None

    def _show(self):
        self._job = None
        if self._tip is not None:
            return
        text = self.text() if callable(self.text) else self.text
        if not text:
            return
        p = self.palette
        try:
            tip = tk.Toplevel(self.widget)
            tip.wm_overrideredirect(True)
            try:
                tip.attributes("-topmost", True)
            except Exception:
                _log_swallowed()
            tk.Label(tip, text=text, font=FONT_UI, bg=p["SUNKEN"], fg=p["WHITE"],
                     padx=7, pady=3, bd=0, highlightthickness=1,
                     highlightbackground=p["ACCENT_LO"]).pack()
            x = self.widget.winfo_rootx()
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
            tip.geometry(f"+{x}+{y}")
            self._tip = tip
        except Exception:
            _log_swallowed()

    def _hide(self, event=None):
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                _log_swallowed()
            self._tip = None


# ------------------------------------------------------------------------
class FlatOptionMenu:
    """
    Cross-platform replacement for tk.OptionMenu.
    """
    def __init__(self, parent, var, options, palette, width=10, on_change=None):
        self.palette = palette
        self.var = var
        self.options = list(options)
        self.on_change = on_change
        self._popup = None

        bg_color = palette["RAISED"]
        fg_color = palette["WHITE"]

        self.btn = tk.Label(
            parent, text=var.get(),
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


# ------------------------------------------------------------------------
def _bind_scale_wheel(scale, callback, lo=-12.0, hi=12.0, step=0.5, round_to=0.5):
    """
    Attach mouse-wheel control to a vertical tk.Scale.

    The wheel-up direction always means "increase the value" (i.e. more
    dB / louder), regardless of the scale's from_/to order. This matches
    the expected behaviour after the scale was set to from_=12, to=-12
    (so that the value at the top is +12 dB).

    round_to controls snapping precision:
        - 0.5 for dB sliders (EQ bands, preamp)  → half-dB steps
        - 1.0 for integer sliders (volume, balance, alpha) → whole steps
        - 0   disables snapping entirely

    Important: tk.Scale.set() does NOT fire the widget's `command`
    callback, so we call it manually. Without this, the value would
    change visually but neither the player nor the persistence layer
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
            print(f"[XZN] wheel callback failed: {e}")

        return "break"

    scale.bind("<MouseWheel>", _on_wheel)   # Windows / macOS
    scale.bind("<Button-4>", _on_wheel)     # Linux scroll up
    scale.bind("<Button-5>", _on_wheel)     # Linux scroll down


# ------------------------------------------------------------------------
class EqualizerWindow:
    def __init__(self, master, app):
        self.app = app
        self.master = master
        self.palette = app.palette
        self.win = tk.Toplevel(master)
        self.win.title("Equalizer – XZN")
        self.win.configure(bg=self.palette["PANEL"])
        self.win.resizable(False, False)
        self.win.transient(master)
        apply_window_icon(self.win)

        # macOS: share the app's menubar with this Toplevel
        try:
            app._attach_macos_menu(self.win)
        except Exception:
            _log_swallowed()

        self._syncing = False

        p = self.palette

        header = tk.Frame(self.win, bg=p["PANEL"])
        header.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(header, text="10-BAND EQUALIZER",
                 font=("Courier New", 11, "bold"),
                 bg=p["PANEL"], fg=p["ACCENT"]).pack(side="left")

        self.btn_on = make_flat_button(header, "ON", self._toggle_on,
                                       palette=p, width=4,
                                       padx=6, pady=3)
        self.btn_on.pack(side="right")

        tk.Label(header, text="Preamp",
                 font=FONT_UI, bg=p["PANEL"], fg=p["GRAY"]).pack(side="right", padx=(0, 6))

        body = tk.Frame(self.win, bg=p["PANEL"])
        body.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        pre_box = tk.Frame(body, bg=p["PANEL"])
        pre_box.pack(side="left", padx=(0, 8))
        tk.Label(pre_box, text="PRE", font=("Courier New", 8, "bold"),
                 bg=p["PANEL"], fg=p["CYAN"]).pack()

        # NOTE: from_=12, to=-12 so that +12 dB is at the top and
        # -12 dB is at the bottom. Vertical tk.Scale puts from_ at
        # the top and to at the bottom; with from_ < to it would be
        # inverted (bug: pulling the slider down made it LOUDER).
        self.pre_scale = tk.Scale(pre_box, from_=12, to=-12, orient="vertical",
                                  length=200, resolution=0.5, showvalue=True,
                                  bg=p["PANEL"], fg=p["ACCENT"],
                                  troughcolor=p["SUNKEN"],
                                  highlightthickness=0, sliderrelief="flat",
                                  font=("Courier New", 7),
                                  takefocus=0,
                                  command=self._on_preamp)
        self.pre_scale.set(getattr(app, "_eq_preamp", 0.0))
        self.pre_scale.pack()

        # Mouse-wheel control for the preamp
        _bind_scale_wheel(self.pre_scale, self._on_preamp, -12.0, 12.0, 0.5)

        self.band_scales = []
        for i, (freq, label) in enumerate(zip(EQ_BANDS, EQ_BAND_LABELS)):
            col = tk.Frame(body, bg=p["PANEL"])
            col.pack(side="left", padx=1)
            tk.Label(col, text=label, font=("Courier New", 8, "bold"),
                     bg=p["PANEL"], fg=p["CYAN"]).pack()

            # Same from_/to flip as the preamp: +12 at top, -12 at bottom.
            sc = tk.Scale(col, from_=12, to=-12, orient="vertical",
                          length=180, resolution=0.5, showvalue=False,
                          bg=p["PANEL"], fg=p["ACCENT"],
                          troughcolor=p["SUNKEN"],
                          highlightthickness=0, sliderrelief="flat",
                          width=14,
                          takefocus=0,
                          command=lambda v, idx=i: self._on_band(idx, v))
            sc.set(getattr(app, "_eq_gains", [0.0]*len(EQ_BANDS))[i])
            sc.pack()
            self.band_scales.append(sc)

            # Mouse-wheel control for each band
            _bind_scale_wheel(
                sc,
                (lambda v, idx=i: self._on_band(idx, v)),
                -12.0, 12.0, 0.5,
            )

        preset_row = tk.Frame(self.win, bg=p["PANEL"])
        preset_row.pack(fill="x", padx=10, pady=(0, 6))
        tk.Label(preset_row, text="Preset:",
                 font=FONT_UI, bg=p["PANEL"], fg=p["GRAY"]).pack(side="left")

        self.preset_var = tk.StringVar(value="Flat")
        preset_menu = FlatOptionMenu(preset_row, self.preset_var,
                                     list(EQ_PRESETS.keys()),
                                     palette=p, width=12,
                                     on_change=self._apply_preset)
        preset_menu.pack(side="left", padx=(6, 0))

        footer = tk.Frame(self.win, bg=p["PANEL"])
        footer.pack(fill="x", padx=10, pady=(0, 10))
        make_flat_button(footer, "Reset", self._reset, palette=p,
                         padx=10, pady=3).pack(side="left")
        # Volume leveling via ReplayGain tags (see read_replaygain).
        self.btn_level = make_flat_button(footer, "", self._toggle_level,
                                          palette=p, padx=10, pady=3)
        self.btn_level.pack(side="left", padx=(6, 0))
        self._sync_level_button()
        make_flat_button(footer, "Close", self.win.destroy, palette=p,
                         padx=10, pady=3).pack(side="right")

        self._sync_on_button()

        self.win.update_idletasks()
        mx = master.winfo_rootx() + master.winfo_width() // 2
        my = master.winfo_rooty() + master.winfo_height() // 2
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        self.win.geometry(f"+{mx - w // 2}+{my - h // 2}")
        self.win.bind("<Escape>", lambda e: self.win.destroy())

    def _sync_on_button(self):
        en = bool(getattr(self.app, "_eq_enabled", True))
        self.btn_on.config(text="ON" if en else "OFF",
                           fg=self.palette["ACCENT"] if en else self.palette["GRAY"],
                           relief="sunken" if en else "raised")

    def _toggle_on(self):
        en = not bool(getattr(self.app, "_eq_enabled", True))
        self.app.set_eq_enabled(en)
        self._sync_on_button()

    def _sync_level_button(self):
        en = bool(getattr(self.app, "_replaygain", True))
        self.btn_level.config(text="Level: ON" if en else "Level: OFF",
                              fg=self.palette["ACCENT"] if en else self.palette["GRAY"])

    def _toggle_level(self):
        self.app.set_replaygain(not bool(getattr(self.app, "_replaygain", True)))
        self._sync_level_button()

    def _on_preamp(self, v):
        if self._syncing:
            return
        try:
            db = float(v)
        except Exception:
            return
        self.app.set_eq_preamp(db)

    def _on_band(self, idx, v):
        if self._syncing:
            return
        try:
            db = float(v)
        except Exception:
            return
        self.app._eq_gains[idx] = db
        self.app.player.set_eq_gains(self.app._eq_gains)
        self.app._persist_settings()

    def _apply_preset(self, name):
        gains = EQ_PRESETS.get(name)
        if not gains:
            return
        self._syncing = True
        try:
            for sc, g in zip(self.band_scales, gains):
                sc.set(g)
            self.app._eq_gains = list(gains)
            self.app.player.set_eq_gains(self.app._eq_gains)
            self.app._persist_settings()
        finally:
            self._syncing = False

    def _reset(self):
        self._apply_preset("Flat")
        self._syncing = True
        try:
            self.pre_scale.set(0)
            self.app.set_eq_preamp(0.0)
        finally:
            self._syncing = False
        self.preset_var.set("Flat")


# ------------------------------------------------------------------------
class TagEditorWindow:
    """
    Small tag editor for a single audio file.
    Reads/writes common tags via mutagen's easy interface.
    """
    FIELDS = [
        ("title",       "Title"),
        ("artist",      "Artist"),
        ("album",       "Album"),
        ("genre",       "Genre"),
        ("date",        "Year"),
        ("tracknumber", "Track"),
    ]

    def __init__(self, master, app, path):
        self.app = app
        self.path = path
        self.palette = app.palette
        self._entries = {}

        p = self.palette
        self.win = tk.Toplevel(master)
        self.win.title(f"Edit Tags – {os.path.basename(path)}")
        self.win.configure(bg=p["PANEL"])
        self.win.resizable(False, False)
        self.win.transient(master)
        apply_window_icon(self.win)

        # macOS: share the app's menubar with this Toplevel
        try:
            app._attach_macos_menu(self.win)
        except Exception:
            _log_swallowed()

        outer = tk.Frame(self.win, bg=p["PANEL"], padx=20, pady=18)
        outer.pack(fill="both", expand=True)

        tk.Label(outer, text="EDIT TAGS",
                 font=("Helvetica", 12, "bold"),
                 bg=p["PANEL"], fg=p["ACCENT"]).pack(anchor="w")

        tk.Label(outer, text=os.path.basename(path),
                 font=FONT_UI, bg=p["PANEL"], fg=p["GRAY"],
                 anchor="w", justify="left",
                 wraplength=420).pack(anchor="w", pady=(2, 12))

        tk.Frame(outer, bg=p["ACCENT_LO"], height=1).pack(fill="x", pady=(0, 14))

        # Read existing tags once
        current = read_tags(path)

        form = tk.Frame(outer, bg=p["PANEL"])
        form.pack(fill="x")

        for i, (key, label) in enumerate(self.FIELDS):
            tk.Label(form, text=label + ":",
                     font=FONT_UI_B, bg=p["PANEL"], fg=p["GRAY"],
                     width=8, anchor="w"
                     ).grid(row=i, column=0, sticky="w", pady=3, padx=(0, 8))

            wrap = tk.Frame(form, bg=p["SUNKEN"], bd=0, highlightthickness=0)
            wrap.grid(row=i, column=1, sticky="ew", pady=3)

            entry = tk.Entry(
                wrap,
                width=40,
                bg=p["SUNKEN"], fg=p["WHITE"],
                insertbackground=p["WHITE"],
                relief="flat", bd=0, highlightthickness=0,
                font=FONT_UI,
            )
            entry.pack(padx=6, pady=3, fill="x", expand=True)
            entry.insert(0, current.get(key, ""))
            self._entries[key] = entry

        form.columnconfigure(1, weight=1)

        tk.Frame(outer, bg=p["ACCENT_LO"], height=1).pack(fill="x", pady=(14, 12))

        btn_row = tk.Frame(outer, bg=p["PANEL"])
        btn_row.pack(fill="x")

        make_flat_button(btn_row, "Save",   self._save,          palette=p,
                         padx=12, pady=4).pack(side="left")
        make_flat_button(btn_row, "Cancel", self.win.destroy,     palette=p,
                         padx=12, pady=4).pack(side="right")

        self.win.bind("<Escape>", lambda e: self.win.destroy())
        self.win.bind("<Return>", lambda e: self._save())

        # Center on parent
        self.win.update_idletasks()
        mx = master.winfo_rootx() + master.winfo_width() // 2
        my = master.winfo_rooty() + master.winfo_height() // 2
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        self.win.geometry(f"+{mx - w // 2}+{my - h // 2}")

        # Focus the title field
        try:
            self._entries["title"].focus_set()
            self._entries["title"].icursor("end")
        except Exception:
            _log_swallowed()

    def _save(self):
        new_tags = {}
        for key, entry in self._entries.items():
            try:
                new_tags[key] = entry.get().strip()
            except Exception:
                new_tags[key] = ""

        ok, err = write_tags(self.path, new_tags)
        if not ok:
            messagebox.showerror("Tag save failed", err, parent=self.win)
            return

        # Update playlist entry if this file is in the playlist
        try:
            self.app.refresh_playlist_entry_for(self.path)
        except Exception as e:
            print(f"[XZN] playlist refresh after tag save failed: {e}")

        self.win.destroy()


# ------------------------------------------------------------------------
# Global registry for borderless popups that close on outside click.
#
# Instead of each popup installing its own global binding (which leaks
# Tk bindings over time), we install ONE binding per Tk root, and keep
# a set of currently open popups. The handler walks that set and closes
# anything the click missed.
# ------------------------------------------------------------------------
_POPUP_REGISTRY = {}


def register_popup(root_widget, popup_win):
    """Track a borderless popup so the global click handler can close it."""
    reg = _POPUP_REGISTRY.setdefault(root_widget, set())
    reg.add(popup_win)


def unregister_popup(root_widget, popup_win):
    reg = _POPUP_REGISTRY.get(root_widget)
    if reg is not None:
        reg.discard(popup_win)
        if not reg:
            _POPUP_REGISTRY.pop(root_widget, None)


def _install_popup_click_handler(root_widget):
    """
    Install the single global click handler for a Tk root.
    Safe to call multiple times: Tk would add duplicate bindings, so we
    tag the root with a flag once we've installed it.
    """
    if getattr(root_widget, "_xzn_popup_handler_installed", False):
        return
    try:
        root_widget.bind_all(
            "<Button-1>",
            lambda e, r=root_widget: _on_any_click(r, e),
            add="+",
        )
        root_widget._xzn_popup_handler_installed = True
    except Exception as e:
        print(f"[XZN] popup handler install failed: {e}")


def _on_any_click(root_widget, event):
    reg = _POPUP_REGISTRY.get(root_widget)
    if not reg:
        return
    try:
        clicked = root_widget.winfo_containing(event.x_root, event.y_root)
    except Exception:
        clicked = None

    for popup in list(reg):
        # Always remove from the registry, even if it is already gone.
        try:
            if not popup.winfo_exists():
                reg.discard(popup)
                continue
        except Exception:
            reg.discard(popup)
            continue

        # Walk up the widget tree from the click target, looking for
        # the popup window itself.
        try:
            p = clicked
            inside = False
            while p is not None:
                if p is popup:
                    inside = True
                    break
                try:
                    p = p.master
                except Exception:
                    p = None
            if not inside:
                try:
                    popup.destroy()
                except Exception:
                    _log_swallowed()
                reg.discard(popup)
        except Exception:
            reg.discard(popup)


# ------------------------------------------------------------------------
class BgPopup:
    def __init__(self, app, x, y):
        self.app = app
        self._destroyed = False
        p = app.palette

        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.configure(bg=p["ACCENT_LO"])
        try:
            self.win.attributes("-topmost", True)
        except Exception:
            _log_swallowed()

        inner = tk.Frame(self.win, bg=p["RAISED"], bd=0, highlightthickness=0)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        has_bg = bool(app._bg_path and os.path.isfile(app._bg_path))

        self.item1 = tk.Label(inner, text="Choose image / GIF…",
                              font=FONT_UI, bg=p["RAISED"], fg=p["WHITE"],
                              anchor="w", padx=14, pady=6, cursor="hand2")
        self.item1.pack(fill="x")
        self.item1.bind("<Enter>", lambda e: self.item1.config(bg=p["ACCENT"], fg=p["WHITE"]))
        self.item1.bind("<Leave>", lambda e: self.item1.config(bg=p["RAISED"], fg=p["WHITE"]))
        self.item1.bind("<Button-1>", lambda e: self._choose())

        sep = tk.Frame(inner, bg=p["ACCENT_LO"], height=1)
        sep.pack(fill="x", padx=6, pady=2)

        if has_bg:
            self.item2 = tk.Label(inner, text="Remove background",
                                  font=FONT_UI, bg=p["RAISED"], fg=p["WHITE"],
                                  anchor="w", padx=14, pady=6, cursor="hand2")
            self.item2.pack(fill="x")
            self.item2.bind("<Enter>", lambda e: self.item2.config(bg=p["ACCENT"], fg=p["WHITE"]))
            self.item2.bind("<Leave>", lambda e: self.item2.config(bg=p["RAISED"], fg=p["WHITE"]))
            self.item2.bind("<Button-1>", lambda e: self._remove())
        else:
            self.item2 = tk.Label(inner, text="Remove background",
                                  font=FONT_UI, bg=p["RAISED"], fg=p["GRAY"],
                                  anchor="w", padx=14, pady=6)
            self.item2.pack(fill="x")

        self.win.geometry(f"+{x}+{y}")

        # Do NOT call focus_set() on the popup. If we take focus on
        # Windows, Tk may not deliver subsequent clicks to the BG
        # button after this popup is destroyed. Instead, we listen for
        # clicks on the root window and close ourselves when the click
        # lands outside of us.
        try:
            self._outside_bind_id = app.root.bind_all(
                "<Button-1>", self._on_root_click, add="+"
            )
        except Exception:
            self._outside_bind_id = None

        self.win.bind("<Escape>", lambda e: self.close())

    def _on_root_click(self, event):
        """
        Close the popup when the user clicks outside of it.
        Runs on every root click; if the click was inside the popup we
        ignore it, otherwise we close.
        """
        if self._destroyed:
            return
        try:
            clicked = self.app.root.winfo_containing(event.x_root, event.y_root)
        except Exception:
            clicked = None
        p = clicked
        while p is not None:
            if p is self.win:
                return
            try:
                p = p.master
            except Exception:
                p = None
        self.close()

    def _choose(self):
        # Clear the app's pointer *before* opening the file dialog so a
        # subsequent BG click can open a fresh popup immediately, even
        # while the dialog is still running.
        if getattr(self.app, "_bg_popup", None) is self:
            self.app._bg_popup = None
        self.close()
        self.app.choose_bg()

    def _remove(self):
        if getattr(self.app, "_bg_popup", None) is self:
            self.app._bg_popup = None
        self.close()
        self.app.remove_bg()

    def close(self):
        if self._destroyed:
            return
        self._destroyed = True
        # Remove our per-popup root binding
        bid = getattr(self, "_outside_bind_id", None)
        if bid is not None:
            try:
                self.app.root.unbind_all("<Button-1>")
            except Exception:
                _log_swallowed()
            self._outside_bind_id = None
        # Unregister from the global registry (safety net)
        try:
            unregister_popup(self.app.root, self.win)
        except Exception:
            _log_swallowed()
        try:
            self.win.destroy()
        except Exception:
            _log_swallowed()
        if getattr(self.app, "_bg_popup", None) is self:
            self.app._bg_popup = None


# ========================================================================
# Audio CD: play and rip (no external tools needed)
# ========================================================================
# CD access uses only what the operating system provides:
#   macOS   – audio CDs are mounted by the system (cddafs); the tracks
#             appear as AIFF files and the TOC is in .TOC.plist
#   Windows – DeviceIoControl (IOCTL_CDROM_READ_TOC / _RAW_READ) via ctypes
#   Linux   – ioctl (CDROMREADTOCENTRY / CDROMREADAUDIO) on /dev/sr*
# Encoding uses the FFmpeg libraries bundled with PyAV (lameenc as MP3
# fallback), tags are written with mutagen, ReplayGain is measured here.
#
# TOC format used throughout:
#   {"first": 1, "last": 12, "offsets": {1: 0, 2: 18055, ...},
#    "lengths": {1: 18055, ...}, "leadout": 225422}
# offsets/lengths in sectors (LSN, track 1 normally starts at 0),
# audio tracks only. 1 sector = 1/75 s = 2352 bytes of 16-bit stereo PCM.

CD_SECTOR_BYTES = 2352
CD_SECTORS_PER_SECOND = 75
CD_SESSION_GAP = 11400          # enhanced CDs: audio session → data session
CD_FORMATS = ["MP3", "OGG", "FLAC", "WAV"]
CD_BITRATES = ["128", "192", "256", "320"]
CDDA_PREFIX = "cdda://"

MB_LEAD_IN = 150
MB_USER_AGENT = f"XZN/{VERSION} ( https://github.com/Merecete/XZN )"
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


def parse_musicbrainz(data: dict, disc_id: str, n_tracks: int):
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


def lookup_cd_info(toc: dict, timeout: float = 12.0):
    """Ask MusicBrainz about the disc. Returns parsed info, or None if the
    disc is unknown or the lookup failed (no internet, timeout, …)."""
    import urllib.request
    import urllib.error
    disc_id = musicbrainz_disc_id(toc)
    url = MB_URL.format(disc_id=disc_id, toc=musicbrainz_toc_param(toc))
    print(f"[XZN] MusicBrainz lookup: disc ID {disc_id}")
    req = urllib.request.Request(url, headers={"User-Agent": MB_USER_AGENT,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("[XZN] MusicBrainz: disc not found")
        else:
            print(f"[XZN] MusicBrainz lookup failed: HTTP {e.code}")
        return None
    except Exception as e:
        print(f"[XZN] MusicBrainz lookup failed: {e}")
        return None
    n_tracks = len(toc["offsets"])
    info = parse_musicbrainz(data, disc_id, n_tracks)
    if info:
        print(f"[XZN] MusicBrainz: {info['artist']} – {info['album']} "
              f"({len(info['tracks'])} titles)")
    return info


_BAD_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str, max_len: int = 120) -> str:
    """Make a string usable as a file/folder name on all platforms."""
    name = _BAD_FILENAME_CHARS.sub("_", name or "").strip().rstrip(". ")
    if len(name) > max_len:
        name = name[:max_len].rstrip(". ")
    return name or "_"



def _toc_from_entries(entries, leadout):
    """entries: [(track_no, lsn, is_data)], leadout: LSN of the lead-out.
    Returns the audio-only TOC (enhanced CDs handled like MusicBrainz)."""
    entries = sorted(entries, key=lambda e: e[1])
    audio = [(n, lsn) for n, lsn, data in entries if not data]
    if not audio:
        return None
    last_audio_lsn = audio[-1][1]
    data_after = [lsn for n, lsn, data in entries if data and lsn > last_audio_lsn]
    if data_after:
        leadout = data_after[0] - CD_SESSION_GAP
    offsets = {n: lsn for n, lsn in audio}
    lengths = {}
    for i, (n, lsn) in enumerate(audio):
        end = audio[i + 1][1] if i + 1 < len(audio) else leadout
        lengths[n] = max(0, end - lsn)
    return {"first": audio[0][0], "last": audio[-1][0], "offsets": offsets,
            "lengths": lengths, "leadout": leadout}


def _parse_full_toc(raw):
    """READ TOC format 0x02 ("full TOC") as stored by macOS. Returns
    (entries, leadout) of the first session, or None."""
    if not raw:
        return None
    body = raw[4:] if (len(raw) - 4) % 11 == 0 else (raw if len(raw) % 11 == 0 else None)
    if body is None:
        return None
    entries, leadout = [], None
    for i in range(0, len(body), 11):
        session, adr_ctrl, _tno, point, _m, _s, _f, _z, pm, ps, pf = body[i:i + 11]
        if session != 1:
            continue
        lba = (pm * 60 + ps) * 75 + pf - 150
        if 1 <= point <= 99:
            entries.append((point, lba, bool(adr_ctrl & 0x04)))
        elif point == 0xA2:
            leadout = lba
    # data tracks from later sessions matter for the enhanced-CD lead-out
    for i in range(0, len(body), 11):
        session, adr_ctrl, _tno, point, _m, _s, _f, _z, pm, ps, pf = body[i:i + 11]
        if session > 1 and 1 <= point <= 99:
            entries.append((point, (pm * 60 + ps) * 75 + pf - 150, True))
    if not entries or leadout is None:
        return None
    return entries, leadout


# --- macOS ---------------------------------------------------------------
class MacCdDrive:
    """An audio CD mounted by macOS (cddafs) under /Volumes."""
    kind = "mac"

    def __init__(self, volume):
        self.id = volume
        self.label = os.path.basename(volume) or volume
        self.lock = threading.Lock()

    def _track_files(self):
        files = {}
        for name in os.listdir(self.id):
            m = re.match(r"\D*(\d+)", name)
            if m and name.lower().endswith((".aiff", ".aif", ".cdda")):
                files.setdefault(int(m.group(1)), os.path.join(self.id, name))
        return files

    def read_toc(self):
        import plistlib
        with open(os.path.join(self.id, ".TOC.plist"), "rb") as f:
            pl = plistlib.load(f)
        parsed = _parse_full_toc(pl.get("Format 0x02 TOC Data"))
        if parsed:
            return _toc_from_entries(*parsed)
        # Fallback: the readable per-track keys
        sessions = pl.get("Sessions") or []
        entries, leadout = [], None
        for si, sess in enumerate(sessions):
            for t in sess.get("Track Array") or []:
                entries.append((int(t.get("Point", 0)), int(t.get("Start Block", 0)),
                                bool(t.get("Data", False)) or si > 0))
            if si == 0:
                leadout = int(sess.get("Leadout Block", 0))
        if not entries:
            raise RuntimeError("No tracks in the CD's table of contents.")
        # "Start Block" may include the 2-second lead-in (150 sectors)
        if min(e[1] for e in entries) >= 150:
            entries = [(n, lsn - 150, d) for n, lsn, d in entries]
            leadout -= 150
        return _toc_from_entries(entries, leadout)

    def play_path(self, track):
        return self._track_files().get(track)

    def iter_pcm(self, track, toc, cancel=None):
        """Yield 16-bit little-endian stereo PCM of one track."""
        path = self._track_files().get(track)
        if not path:
            raise RuntimeError(f"Track {track} not found on the CD.")
        with open(path, "rb") as f:
            if f.read(4) != b"FORM":
                raise RuntimeError("Unexpected CD track format.")
            f.read(8)
            while True:
                hdr = f.read(8)
                if len(hdr) < 8:
                    raise RuntimeError("No audio data in CD track.")
                cid, size = hdr[:4], int.from_bytes(hdr[4:], "big")
                if cid == b"SSND":
                    offset = int.from_bytes(f.read(4), "big")
                    f.read(4 + offset)
                    remaining = size - 8 - offset
                    break
                f.seek(size + (size & 1), 1)
            chunk = CD_SECTOR_BYTES * CD_SECTORS_PER_SECOND
            while remaining > 0:
                if cancel is not None and cancel.is_set():
                    return
                data = f.read(min(chunk, remaining))
                if not data:
                    break
                remaining -= len(data)
                data = data[:len(data) - (len(data) % 4)]
                yield np.frombuffer(data, ">i2").astype("<i2").tobytes()

    def eject(self):
        import subprocess
        subprocess.run(["diskutil", "eject", self.id], capture_output=True, timeout=30)


def _list_mac_drives():
    out = []
    try:
        for name in sorted(os.listdir("/Volumes")):
            vol = os.path.join("/Volumes", name)
            if os.path.isfile(os.path.join(vol, ".TOC.plist")):
                out.append(MacCdDrive(vol))
    except Exception:
        _log_swallowed()
    return out


# --- Windows / Linux: raw sector access ----------------------------------
class _RawCdDrive:
    """Base for drives read sector by sector (Windows, Linux)."""
    READ_CHUNK = 24          # sectors per request (drives cap ~64 KB)

    def __init__(self):
        self.lock = threading.Lock()
        self._toc = None

    def toc(self):
        if self._toc is None:
            self._toc = self.read_toc()
        return self._toc

    def play_path(self, track):
        return f"{CDDA_PREFIX}{self.id}/{track}"

    # Errors meaning "the connection to the drive is no longer valid"
    # (Windows re-mounted the drive, it spun down, another program
    # accessed it ...). The cure is to reopen the drive, not to give up.
    LOST_ERRORS = set()

    def _is_lost(self, err):
        return getattr(err, "errno", None) in self.LOST_ERRORS

    def read_sectors(self, lsn, count):
        """Read count audio sectors starting at lsn (thread-safe).

        - Lost connection: the drive is reopened and the read retried
          for a few seconds (spin-up); if it never comes back, an error
          is raised instead of producing endless silence.
        - Real read error on a block (scratch): retried twice, then
          replaced by silence so playback/ripping continues."""
        out = bytearray()
        pos, end = lsn, lsn + count
        with self.lock:
            while pos < end:
                n = min(self.READ_CHUNK, end - pos)
                data, err, lost_tries, bad_tries = None, None, 0, 0
                while data is None:
                    try:
                        self._open()
                        data = self._read_raw(pos, n)
                    except OSError as e:
                        err = e
                        if self._is_lost(e):
                            self._close()
                            lost_tries += 1
                            if lost_tries > 6:
                                raise RuntimeError(f"CD drive {self.id} is not responding ({e})")
                            if lost_tries == 1:
                                print(f"[XZN] CD drive {self.id}: connection lost ({e}), reopening")
                            time.sleep(0.25 * lost_tries)
                        else:
                            bad_tries += 1
                            if bad_tries >= 3:
                                print(f"[XZN] CD read error at sector {pos}: {err}")
                                data = bytes(n * CD_SECTOR_BYTES)
                out += data
                pos += n
        return bytes(out)

    def iter_pcm(self, track, toc, cancel=None):
        start, length = toc["offsets"][track], toc["lengths"][track]
        pos, end = start, start + length
        step = CD_SECTORS_PER_SECOND
        while pos < end:
            if cancel is not None and cancel.is_set():
                return
            n = min(step, end - pos)
            yield self.read_sectors(pos, n)
            pos += n


class WinCdDrive(_RawCdDrive):
    kind = "windows"
    # ERROR_INVALID_HANDLE, NOT_READY, DEV_NOT_EXIST, FILE_INVALID,
    # DEVICE_NOT_CONNECTED, MEDIA_CHANGED
    LOST_ERRORS = {6, 21, 55, 1006, 1167, 1110}
    IOCTL_CDROM_READ_TOC = 0x00024000
    IOCTL_CDROM_RAW_READ = 0x0002403E
    IOCTL_STORAGE_EJECT_MEDIA = 0x002D4808

    def __init__(self, letter):
        super().__init__()
        self.id = letter.rstrip(":\\/").upper() + ":"
        self.label = self.id
        self._h = None

    @staticmethod
    def _k32():
        """kernel32 with explicit signatures (64-bit handles, unsigned flags)."""
        k32 = ctypes.windll.kernel32
        if not getattr(k32, "_xzn_typed", False):
            from ctypes import wintypes as wt
            k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                        wt.DWORD, wt.DWORD, wt.HANDLE]
            k32.CreateFileW.restype = wt.HANDLE
            k32.DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p, wt.DWORD,
                                            ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD),
                                            ctypes.c_void_p]
            k32.DeviceIoControl.restype = wt.BOOL
            k32.CloseHandle.argtypes = [wt.HANDLE]
            k32._xzn_typed = True
        return k32

    def _open(self):
        if self._h is not None:
            return
        k32 = self._k32()
        h = k32.CreateFileW(f"\\\\.\\{self.id}", 0x80000000, 0x1 | 0x2, None, 3, 0, None)
        if not h or h == ctypes.c_void_p(-1).value:
            raise OSError(k32.GetLastError() or 2, f"Cannot open drive {self.id}")
        self._h = h

    def _ioctl(self, code, inbuf, outsize):
        from ctypes import wintypes as wt
        k32 = self._k32()
        out = ctypes.create_string_buffer(max(outsize, 1))
        got = wt.DWORD(0)
        inb = ctypes.create_string_buffer(bytes(inbuf), len(inbuf)) if inbuf else None
        ok = k32.DeviceIoControl(self._h, code, inb, len(inbuf) if inbuf else 0,
                                 out, outsize, ctypes.byref(got), None)
        if not ok:
            raise OSError(k32.GetLastError(), "DeviceIoControl failed")
        return out.raw[:got.value]

    def _close(self):
        if self._h is not None:
            try:
                self._k32().CloseHandle(self._h)
            except Exception:
                _log_swallowed()
            self._h = None

    def read_toc(self):
        # Always with a fresh handle: after a disc change or a re-mount
        # the old one is invalid (error 1006 / 55).
        with self.lock:
            for attempt in range(4):
                self._close()
                try:
                    self._open()
                    raw = self._ioctl(self.IOCTL_CDROM_READ_TOC, None, 4 + 100 * 8)
                    break
                except OSError as e:
                    if not self._is_lost(e) or attempt == 3:
                        raise
                    time.sleep(0.5)
        return self.parse_toc(raw)

    @staticmethod
    def parse_toc(raw):
        """CDROM_TOC (MSF addresses) → TOC."""
        first, last = raw[2], raw[3]
        entries, leadout = [], None
        for i in range(last - first + 2):
            d = raw[4 + i * 8: 12 + i * 8]
            if len(d) < 8:
                break
            ctrl, number = d[1] >> 4, d[2]
            lba = (d[5] * 60 + d[6]) * 75 + d[7] - 150
            if number == 0xAA:
                leadout = lba
            else:
                entries.append((number, lba, bool(ctrl & 0x04)))
        if leadout is None or not entries:
            raise RuntimeError("No audio CD in the drive.")
        return _toc_from_entries(entries, leadout)

    def _read_raw(self, lsn, n):
        info = struct.pack("<qII", lsn * 2048, n, 2)   # TrackMode CDDA = 2
        data = self._ioctl(self.IOCTL_CDROM_RAW_READ, info, n * CD_SECTOR_BYTES)
        if len(data) < n * CD_SECTOR_BYTES:
            raise OSError(0, "short read")
        return data

    def eject(self):
        with self.lock:
            self._open()
            try:
                self._ioctl(self.IOCTL_STORAGE_EJECT_MEDIA, None, 0)
            finally:
                self._close()
        self._toc = None


def _list_windows_drives():
    out = []
    try:
        k32 = ctypes.windll.kernel32
        mask = k32.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                letter = chr(65 + i) + ":"
                if k32.GetDriveTypeW(letter + "\\") == 5:      # DRIVE_CDROM
                    out.append(WinCdDrive(letter))
    except Exception:
        _log_swallowed()
    return out


class LinuxCdDrive(_RawCdDrive):
    kind = "linux"
    # EBADF, ENODEV, ENXIO, ENOMEDIUM
    LOST_ERRORS = {9, 19, 6, 123}
    CDROMREADTOCHDR = 0x5305
    CDROMREADTOCENTRY = 0x5306
    CDROMREADAUDIO = 0x530E
    CDROMEJECT = 0x5309
    CDROM_LBA = 0x01
    CDROM_LEADOUT = 0xAA

    def __init__(self, dev):
        super().__init__()
        self.id = dev
        self.label = dev
        self._fd = None
        self.READ_CHUNK = 24

    def _open(self):
        if self._fd is None:
            self._fd = os.open(self.id, os.O_RDONLY | os.O_NONBLOCK)

    def _close(self):
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                _log_swallowed()
            self._fd = None

    def read_toc(self):
        import fcntl
        with self.lock:
            self._close()          # fresh handle after a disc change
            self._open()
            hdr = bytearray(2)
            fcntl.ioctl(self._fd, self.CDROMREADTOCHDR, hdr, True)
            first, last = hdr[0], hdr[1]
            entries, leadout = [], None
            for n in list(range(first, last + 1)) + [self.CDROM_LEADOUT]:
                # struct cdrom_tocentry: track, adr:4|ctrl:4, format, pad,
                # union addr (int lba), datamode, pad[3]  -> 12 bytes
                buf = bytearray(struct.pack("=BBBxiB3x", n, 0, self.CDROM_LBA, 0, 0))
                fcntl.ioctl(self._fd, self.CDROMREADTOCENTRY, buf, True)
                _t, adr_ctrl, _fmt, lba, _dm = struct.unpack("=BBBxiB3x", bytes(buf))
                if n == self.CDROM_LEADOUT:
                    leadout = lba
                else:
                    entries.append((n, lba, bool((adr_ctrl >> 4) & 0x04)))
        return _toc_from_entries(entries, leadout)

    def _read_raw(self, lsn, n):
        import fcntl
        import ctypes as _ct      # (module-level ctypes is Windows-only)
        buf = _ct.create_string_buffer(n * CD_SECTOR_BYTES)
        # struct cdrom_read_audio: union addr (int), u8 format, int nframes,
        # u8 *buf  (native alignment)
        req = bytearray(struct.pack("@iBiP", lsn, self.CDROM_LBA, n, _ct.addressof(buf)))
        fcntl.ioctl(self._fd, self.CDROMREADAUDIO, req, True)
        return buf.raw

    def eject(self):
        import fcntl
        with self.lock:
            self._open()
            try:
                fcntl.ioctl(self._fd, self.CDROMEJECT, 0)
            finally:
                self._close()
        self._toc = None


def _list_linux_drives():
    out, seen = [], set()
    for dev in [f"/dev/sr{i}" for i in range(4)] + ["/dev/cdrom"]:
        try:
            real = os.path.realpath(dev)
            if os.path.exists(dev) and real not in seen:
                seen.add(real)
                out.append(LinuxCdDrive(dev))
        except Exception:
            _log_swallowed()
    return out


_CD_DRIVES = {}


def list_cd_drives():
    """All CD drives (macOS: mounted audio CDs). Drive objects are reused
    so playback and ripping share one lock and TOC cache per drive."""
    if IS_MAC:
        found = _list_mac_drives()
    elif IS_WINDOWS:
        found = _list_windows_drives()
    elif sys.platform.startswith("linux"):
        found = _list_linux_drives()
    else:
        found = []
    result = []
    for d in found:
        result.append(_CD_DRIVES.setdefault(d.id, d))
    return result


def is_cdda(path):
    return isinstance(path, str) and path.startswith(CDDA_PREFIX)


def _cdda_lookup(path):
    drive_id, track = path[len(CDDA_PREFIX):].rsplit("/", 1)
    drive = _CD_DRIVES.get(drive_id)
    if drive is None:
        drive = WinCdDrive(drive_id) if IS_WINDOWS else LinuxCdDrive(drive_id)
        _CD_DRIVES[drive_id] = drive
    return drive, int(track)


class CdTrackFile:
    """A CD track presented as a seekable WAV file, read from the disc on
    demand, so the player (PyAV) can play and seek it like any file."""

    HEADER = 44
    CACHE_SECTORS = CD_SECTORS_PER_SECOND        # read ahead 1 s

    def __init__(self, drive, start, sectors):
        self.drive, self.start, self.sectors = drive, start, sectors
        data_len = sectors * CD_SECTOR_BYTES
        self.size = self.HEADER + data_len
        self.header = (b"RIFF" + struct.pack("<I", 36 + data_len) + b"WAVEfmt "
                       + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 44100 * 4, 4, 16)
                       + b"data" + struct.pack("<I", data_len))
        self.pos = 0
        self._cache_start, self._cache = -1, b""

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        n = max(0, min(n, self.size - self.pos))
        out = bytearray()
        if self.pos < self.HEADER and n:
            part = self.header[self.pos:self.pos + n]
            out += part
            self.pos += len(part)
            n -= len(part)
        while n > 0:
            off = self.pos - self.HEADER
            sec = off // CD_SECTOR_BYTES
            if not (self._cache_start <= sec < self._cache_start + len(self._cache) // CD_SECTOR_BYTES):
                cnt = min(self.CACHE_SECTORS, self.sectors - sec)
                self._cache = self.drive.read_sectors(self.start + sec, cnt)
                self._cache_start = sec
            i = off - self._cache_start * CD_SECTOR_BYTES
            part = self._cache[i:i + n]
            if not part:
                break
            out += part
            self.pos += len(part)
            n -= len(part)
        return bytes(out)

    def seek(self, offset, whence=0):
        base = {0: 0, 1: self.pos, 2: self.size}[whence]
        self.pos = max(0, min(self.size, base + offset))
        return self.pos

    def tell(self):
        return self.pos

    def seekable(self):
        return True

    def readable(self):
        return True

    def close(self):
        self._cache = b""


def open_media(path):
    """av.open() that also understands cdda:// CD tracks.
    Returns (container, file_object_to_close_or_None)."""
    if is_cdda(path):
        drive, track = _cdda_lookup(path)
        toc = drive.toc()
        f = CdTrackFile(drive, toc["offsets"][track], toc["lengths"][track])
        return av.open(f, format="wav"), f
    return av.open(path), None


# --- Encoding --------------------------------------------------------------
def cd_encoders():
    """Formats that can be written on this system."""
    ok = {"WAV"}
    try:
        names = set(av.codecs_available)
    except Exception:
        names = set()
    if "flac" in names:
        ok.add("FLAC")
    if "libvorbis" in names:
        ok.add("OGG")
    if "libmp3lame" in names:
        ok.add("MP3")
    else:
        try:
            import lameenc  # noqa: F401
            ok.add("MP3")
        except Exception:
            print("[XZN] MP3 encoding unavailable (no libmp3lame in PyAV, no lameenc)")
    return ok


class TrackEncoder:
    """Writes 16-bit stereo 44.1 kHz PCM to MP3 / OGG / FLAC / WAV."""

    def __init__(self, path, fmt, bitrate="320"):
        self.path, self.fmt = path, fmt
        self._wav = self._lame = self._out = self._file = None
        self._samples = 0
        if fmt == "WAV":
            import wave
            self._wav = wave.open(path, "wb")
            self._wav.setnchannels(2)
            self._wav.setsampwidth(2)
            self._wav.setframerate(44100)
            return
        if fmt == "MP3" and "libmp3lame" not in set(av.codecs_available):
            import lameenc
            self._lame = lameenc.Encoder()
            self._lame.set_bit_rate(int(bitrate))
            self._lame.set_in_sample_rate(44100)
            self._lame.set_channels(2)
            self._lame.set_quality(2)
            self._file = open(path, "wb")
            return
        codec = {"MP3": "libmp3lame", "OGG": "libvorbis", "FLAC": "flac"}[fmt]
        self._out = av.open(path, "w")
        st = self._out.add_stream(codec, rate=44100)
        cc = getattr(st, "codec_context", st)
        for target in (cc, st):
            try:
                target.layout = "stereo"
                break
            except Exception:
                continue
        if fmt in ("MP3", "OGG"):
            try:
                cc.bit_rate = int(bitrate) * 1000
            except Exception:
                st.bit_rate = int(bitrate) * 1000
        self._stream = st
        from fractions import Fraction
        self._tb = Fraction(1, 44100)

    def write(self, pcm):
        if not pcm:
            return
        if self._wav is not None:
            self._wav.writeframes(pcm)
            return
        if self._lame is not None:
            self._file.write(self._lame.encode(pcm))
            return
        arr = np.frombuffer(pcm, "<i2").reshape(1, -1)
        frame = av.AudioFrame.from_ndarray(arr, format="s16", layout="stereo")
        frame.sample_rate = 44100
        frame.time_base = self._tb
        frame.pts = self._samples
        self._samples += arr.shape[1] // 2
        for pkt in self._stream.encode(frame):
            self._out.mux(pkt)

    def close(self):
        if self._wav is not None:
            self._wav.close()
        elif self._lame is not None:
            self._file.write(self._lame.flush())
            self._file.close()
        elif self._out is not None:
            for pkt in self._stream.encode(None):
                self._out.mux(pkt)
            self._out.close()


class LoudnessMeter:
    """ReplayGain 2.0 (EBU R128 integrated loudness, -18 LUFS reference)
    measured on the PCM stream while ripping."""

    def __init__(self, fs=44100):
        self.fs = fs
        self.sos = self._k_weighting(fs)
        self.zi = np.zeros((self.sos.shape[0], 2, 2))
        self.sub = int(fs * 0.1)          # 100 ms sub-blocks
        self.rest = np.zeros((0, 2))
        self.energy = []                  # per sub-block: mean square per channel
        self.peak = 0

    @staticmethod
    def _k_weighting(fs):
        """ITU-R BS.1770 K-weighting for any sample rate (same derivation
        as libebur128 / ffmpeg's ebur128 filter)."""
        f0, g, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
        k = np.tan(np.pi * f0 / fs)
        vh = 10 ** (g / 20)
        vb = vh ** 0.4996667741545416
        a0 = 1 + k / q + k * k
        shelf = [(vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0,
                 (vh - vb * k / q + k * k) / a0, 1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]
        f0, q = 38.13547087602444, 0.5003270373238773
        k = np.tan(np.pi * f0 / fs)
        a0 = 1 + k / q + k * k
        highpass = [1.0, -2.0, 1.0, 1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]
        return np.array([shelf, highpass])

    def add(self, pcm):
        if not _SCIPY_AVAILABLE or not pcm:
            return
        from scipy.signal import sosfilt
        x = np.frombuffer(pcm, "<i2").reshape(-1, 2)
        self.peak = max(self.peak, int(np.abs(x.astype(np.int32)).max()))
        y, self.zi = sosfilt(self.sos, x / 32768.0, axis=0, zi=self.zi)
        y = np.concatenate([self.rest, y])
        n = len(y) // self.sub
        if n:
            blocks = y[:n * self.sub].reshape(n, self.sub, 2)
            self.energy.extend((blocks ** 2).mean(axis=1))
        self.rest = y[n * self.sub:]

    def result(self):
        """(gain_db, peak) or None."""
        if len(self.energy) < 4:
            return None
        e = np.array(self.energy)
        z = np.array([e[i:i + 4].mean(axis=0) for i in range(len(e) - 3)])   # 400 ms, 75 % overlap
        power = z.sum(axis=1)
        with np.errstate(divide="ignore"):
            loud = -0.691 + 10 * np.log10(power)
        gated = power[loud > -70.0]
        if not len(gated):
            return None
        rel = -0.691 + 10 * np.log10(gated.mean()) - 10.0
        final = power[(loud > -70.0) & (loud > rel)]
        lufs = -0.691 + 10 * np.log10(final.mean())
        return -18.0 - lufs, self.peak / 32768.0


def write_cd_tags(path, fmt, tags):
    """Write tags + ReplayGain with mutagen (ID3 for MP3/WAV, Vorbis
    comments for OGG/FLAC). Never raises."""
    if not _MUTAGEN_AVAILABLE:
        return
    tags = {k: str(v) for k, v in tags.items() if v not in (None, "")}
    try:
        if fmt in ("MP3", "WAV"):
            from mutagen.id3 import ID3, TIT2, TPE1, TALB, TPE2, TDRC, TRCK, TPOS, TXXX
            frames = {"title": TIT2, "artist": TPE1, "album": TALB, "albumartist": TPE2,
                      "date": TDRC, "tracknumber": TRCK, "discnumber": TPOS}
            if fmt == "WAV":
                from mutagen.wave import WAVE
                audio = WAVE(path)
                if audio.tags is None:
                    audio.add_tags()
                id3 = audio.tags
            else:
                try:
                    id3 = ID3(path)
                except Exception:
                    id3 = ID3()
            for k, v in tags.items():
                if k in frames:
                    id3.add(frames[k](encoding=3, text=[v]))
                elif k.startswith("replaygain_"):
                    id3.add(TXXX(encoding=3, desc=k.upper(), text=[v]))
            if fmt == "WAV":
                audio.save()
            else:
                id3.save(path, v2_version=3)
        else:
            audio = MutagenFile(path)
            for k, v in tags.items():
                audio[k.upper()] = [v]
            audio.save()
    except Exception as e:
        print(f"[XZN] Tagging failed for {os.path.basename(path)}: {e}")


def rip_cd(drive, toc, tracks, outdir, fmt, bitrate, info, report, cancel):
    """Rip tracks (list of track numbers). report(kind, *args) is called
    from this worker thread: ("progress", pct, label), ("file", path),
    ("done", n), ("stopped", n), ("error", msg)."""
    if info and (info.get("artist") or info.get("album")):
        outdir = os.path.join(outdir, safe_filename(
            f"{info.get('artist') or 'Unknown Artist'} - {info.get('album') or 'Unknown Album'}"))
    os.makedirs(outdir, exist_ok=True)
    ext = {"MP3": ".mp3", "OGG": ".ogg", "FLAC": ".flac", "WAV": ".wav"}[fmt]
    n_disc = len(toc["offsets"])
    done = 0
    for idx, track in enumerate(tracks, start=1):
        meta = ((info or {}).get("tracks") or {}).get(track - toc["first"] + 1) or {}
        name = (f"{track:02d} - {safe_filename(meta['title'])}" if meta.get("title")
                else f"track_{track:02d}")
        path = os.path.join(outdir, name + ext)
        label = f"Track {track:02d} ({idx}/{len(tracks)})"
        total = max(1, toc["lengths"][track] * CD_SECTOR_BYTES)
        written = 0
        enc = meter = None
        try:
            enc = TrackEncoder(path, fmt, bitrate)
            meter = LoudnessMeter()
            for pcm in drive.iter_pcm(track, toc, cancel):
                enc.write(pcm)
                meter.add(pcm)
                written += len(pcm)
                report("progress", min(99, int(written * 100 / total)), label)
            enc.close()
            enc = None
            if cancel.is_set():
                try:
                    os.remove(path)
                except OSError:
                    _log_swallowed()
                report("stopped", done)
                return
            tags = {"tracknumber": f"{track}/{n_disc}"}
            if info:
                tags.update({"title": meta.get("title"),
                             "artist": meta.get("artist") or info.get("artist"),
                             "album": info.get("album"), "albumartist": info.get("artist"),
                             "date": info.get("date"), "discnumber": info.get("disc")})
            rg = meter.result()
            if rg:
                tags["replaygain_track_gain"] = f"{rg[0]:+.2f} dB"
                tags["replaygain_track_peak"] = f"{rg[1]:.6f}"
            write_cd_tags(path, fmt, tags)
            done += 1
            report("file", path)
        except Exception as e:
            traceback.print_exc()
            if enc is not None:
                try:
                    enc.close()
                except Exception:
                    _log_swallowed()
            report("error", f"Track {track}: {e}")
            return
    report("done", done)


# ------------------------------------------------------------------------
class CdWindow:
    """Audio CD window: play the CD in XZN or rip it (MP3/OGG/FLAC/WAV,
    titles from MusicBrainz, tags + ReplayGain). Opened with the CD
    button; a second click closes it like the other tool windows."""

    def __init__(self, master, app):
        self.app = app
        p = app.palette
        self.palette = p
        self.win = tk.Toplevel(master)
        self.win.title("CD – XZN")
        self.win.configure(bg=p["PANEL"])
        self.win.resizable(True, True)
        self.win.transient(master)
        apply_window_icon(self.win)
        try:
            app._attach_macos_menu(self.win)
        except Exception:
            _log_swallowed()

        self.drives = []
        self.drive = None
        self.toc = None
        self.info = None
        self.ripping = False
        self._cancel = None
        self._token = 0
        self._queue = queue.Queue()
        self._encoders = cd_encoders()
        cfg = app._cd_settings
        self.format_var = tk.StringVar(value=cfg.get("format") if cfg.get("format") in self._encoders
                                       else ("MP3" if "MP3" in self._encoders else "FLAC" if "FLAC" in self._encoders else "WAV"))
        self.bitrate_var = tk.StringVar(value=cfg.get("bitrate") or "320")
        self.eject_var = tk.BooleanVar(value=bool(cfg.get("eject", True)))
        self.outdir = cfg.get("outdir") or os.path.join(os.path.expanduser("~"), "Music", "XZN-Rips")
        self.drive_var = tk.StringVar(value="")

        frame = tk.Frame(self.win, bg=p["PANEL"], padx=12, pady=10)
        frame.pack(fill="both", expand=True)

        # --- Drive row --------------------------------------------------
        top = tk.Frame(frame, bg=p["PANEL"])
        top.pack(fill="x")
        tk.Label(top, text="DRIVE", font=FONT_UI_B, bg=p["PANEL"], fg=p["GRAY"]).pack(side="left")
        self.drive_menu_box = tk.Frame(top, bg=p["PANEL"])
        self.drive_menu_box.pack(side="left", padx=(6, 6))
        app._mkbtn(top, "SCAN", self.scan, w=6).pack(side="left")
        self.btn_eject = app._mkbtn(top, "EJECT", self.eject, w=6)
        self.btn_eject.pack(side="left", padx=(4, 0))

        self.album_lbl = tk.Label(frame, text="", font=FONT_UI_B, width=1,
                                  bg=p["PANEL"], fg=p["ACCENT"], anchor="w")
        self.album_lbl.pack(fill="x", pady=(8, 2))

        # --- Track list -------------------------------------------------
        lrow = tk.Frame(frame, bg=p["SUNKEN"])
        lrow.pack(fill="both", expand=True)
        list_bg = darken(p["BG"], 0.65) if luminance(p["BG"]) < 0.5 else "#ffffff"
        self.listbox = tk.Listbox(lrow, height=12, width=56, selectmode="extended", bd=0,
                                  highlightthickness=0, exportselection=False,
                                  bg=list_bg, fg=p["WHITE"], font=("Courier New", 9),
                                  selectbackground=mix(p["PANEL"], p["ACCENT"], 0.35),
                                  selectforeground=p["WHITE"], activestyle="none")
        self.listbox.pack(fill="both", expand=True, padx=1, pady=1)
        self.listbox.bind("<Double-Button-1>", lambda e: self.play())
        for seq in ("<Control-a>", "<Control-A>", "<Command-a>"):
            try:
                self.listbox.bind(seq, lambda e: (self.listbox.selection_set(0, "end"), "break")[1])
            except tk.TclError:
                _log_swallowed()

        # --- Options ----------------------------------------------------
        opt = tk.Frame(frame, bg=p["PANEL"])
        opt.pack(fill="x", pady=(8, 0))
        tk.Label(opt, text="FORMAT", font=FONT_UI_B, bg=p["PANEL"], fg=p["GRAY"]).pack(side="left")
        formats = [f for f in CD_FORMATS if f in self._encoders]
        FlatOptionMenu(opt, self.format_var, formats, palette=p, width=5,
                       on_change=lambda v: self._save()).pack(side="left", padx=(6, 12))
        tk.Label(opt, text="BITRATE", font=FONT_UI_B, bg=p["PANEL"], fg=p["GRAY"]).pack(side="left")
        FlatOptionMenu(opt, self.bitrate_var, CD_BITRATES, palette=p, width=4,
                       on_change=lambda v: self._save()).pack(side="left", padx=(6, 12))
        tk.Checkbutton(opt, text="Eject when done", variable=self.eject_var,
                       command=self._save, bg=p["PANEL"], fg=p["WHITE"],
                       activebackground=p["PANEL"], activeforeground=p["ACCENT"],
                       selectcolor=p["SUNKEN"], font=FONT_UI, bd=0,
                       highlightthickness=0).pack(side="left")

        od = tk.Frame(frame, bg=p["PANEL"])
        od.pack(fill="x", pady=(6, 0))
        tk.Label(od, text="OUTPUT", font=FONT_UI_B, bg=p["PANEL"], fg=p["GRAY"]).pack(side="left")
        self.outdir_lbl = tk.Label(od, text=self.outdir, font=FONT_UI, width=1, anchor="w",
                                   bg=p["SUNKEN"], fg=p["WHITE"], padx=6)
        self.outdir_lbl.pack(side="left", fill="x", expand=True, padx=(6, 4), ipady=2)
        app._mkbtn(od, "…", self.pick_outdir, w=3).pack(side="left")

        self.progress = tk.Canvas(frame, height=12, bg=p["SUNKEN"], highlightthickness=0)
        self.progress.pack(fill="x", pady=(8, 4))

        brow = tk.Frame(frame, bg=p["PANEL"])
        brow.pack(fill="x")
        app._mkbtn(brow, "▶ PLAY", self.play, w=8).pack(side="left")
        app._mkbtn(brow, "RIP ALL", lambda: self.rip(selected_only=False), w=8).pack(side="left", padx=(4, 0))
        app._mkbtn(brow, "RIP SELECTED", lambda: self.rip(selected_only=True), w=13).pack(side="left", padx=(4, 0))
        app._mkbtn(brow, "STOP", self.stop_rip, w=6).pack(side="left", padx=(4, 0))
        app._mkbtn(brow, "OPEN FOLDER", self.open_folder, w=12).pack(side="right")

        self.status = tk.Label(frame, text="", font=FONT_UI, width=1, anchor="w",
                               bg=p["PANEL"], fg=p["GRAY"])
        self.status.pack(fill="x", pady=(6, 0))

        # Close button like in the other XZN windows
        tk.Frame(frame, bg=p["ACCENT_LO"], height=1).pack(fill="x", pady=(8, 8))
        footer = tk.Frame(frame, bg=p["PANEL"])
        footer.pack(fill="x")
        make_flat_button(footer, "Close", self.close, palette=p,
                         padx=14, pady=4).pack(side="right")

        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.bind("<Escape>", lambda e: self.close())

        self.win.update_idletasks()
        mx = master.winfo_rootx() + master.winfo_width() // 2
        my = master.winfo_rooty() + master.winfo_height() // 2
        w, h = self.win.winfo_width(), self.win.winfo_height()
        self.win.geometry(f"+{max(0, mx - w // 2)}+{max(0, my - h // 2)}")
        try:
            app._extra_alpha_windows.append(self.win)
        except Exception:
            _log_swallowed()
        self._poll()
        self.win.after(50, self.scan)

    # --- helpers ---------------------------------------------------------
    def _set_status(self, text, error=False):
        try:
            self.status.config(text=text, fg=self.palette["ERR"] if error else self.palette["GRAY"])
        except Exception:
            _log_swallowed()

    def _save(self):
        self.app._cd_settings = {"format": self.format_var.get(), "bitrate": self.bitrate_var.get(),
                                 "eject": bool(self.eject_var.get()), "outdir": self.outdir}
        self.app._persist_settings()

    def _draw_progress(self, pct, label=""):
        c = self.progress
        try:
            c.delete("all")
            w = max(c.winfo_width(), 10)
            c.create_rectangle(0, 0, int(w * pct / 100), 12, fill=self.palette["ACCENT"], width=0)
            if label:
                c.create_text(6, 6, text=f"{label}  {pct}%", anchor="w",
                              fill=self.palette["WHITE"], font=("Helvetica", 8))
        except Exception:
            _log_swallowed()

    def _track_label(self, n):
        sec = self.toc["lengths"][n] / CD_SECTORS_PER_SECOND
        title, artist = self.track_title(n)
        name = title or "Audio Track"
        if title and artist and self.info and artist != self.info.get("artist"):
            name += f"  ·  {artist}"
        return f"{n:02d}  {fmt_time(sec):>6}  {name}"

    def track_title(self, n):
        if not self.info or not self.toc:
            return "", ""
        t = (self.info.get("tracks") or {}).get(n - self.toc["first"] + 1) or {}
        return t.get("title") or "", t.get("artist") or ""

    def _render(self):
        self.listbox.delete(0, "end")
        if not self.toc:
            return
        for n in sorted(self.toc["offsets"]):
            self.listbox.insert("end", self._track_label(n))
        info = self.info
        if info and (info.get("artist") or info.get("album")):
            txt = f"{info.get('artist') or '?'} – {info.get('album') or '?'}"
            if info.get("date"):
                txt += f" ({info['date']})"
            if info.get("disc"):
                txt += f"  [CD {info['disc']}]"
            self.album_lbl.config(text=txt)
        else:
            self.album_lbl.config(text="")

    def _selected_tracks(self):
        tracks = sorted(self.toc["offsets"]) if self.toc else []
        return [tracks[i] for i in self.listbox.curselection() if i < len(tracks)]

    # --- scan / lookup ---------------------------------------------------
    def scan(self):
        self.drives = list_cd_drives()
        for w in self.drive_menu_box.winfo_children():
            w.destroy()
        if not self.drives:
            self.drive = self.toc = self.info = None
            self._render()
            self._set_status("No audio CD found." if IS_MAC else "No CD drive found.")
            return
        labels = [d.label for d in self.drives]
        if self.drive_var.get() not in labels:
            self.drive_var.set(labels[0])
        FlatOptionMenu(self.drive_menu_box, self.drive_var, labels, palette=self.palette,
                       width=14, on_change=lambda v: self._read_disc()).pack()
        self._read_disc()

    def _read_disc(self):
        self.drive = next((d for d in self.drives if d.label == self.drive_var.get()), None)
        self.toc = self.info = None
        self._render()
        if self.drive is None:
            return
        self._set_status("Reading CD …")
        self._token += 1
        token, drive = self._token, self.drive

        def work():
            try:
                if hasattr(drive, "_toc"):
                    drive._toc = None
                toc = drive.read_toc() if not hasattr(drive, "toc") else drive.toc()
            except Exception as e:
                self._queue.put(("toc_error", token, str(e)))
                return
            self._queue.put(("toc", token, toc))
            self._queue.put(("info", token, lookup_cd_info(toc)))

        threading.Thread(target=work, daemon=True).start()

    # --- actions ---------------------------------------------------------
    def play(self):
        if not self.toc or self.drive is None:
            return
        tracks = sorted(self.toc["offsets"])
        sel = self.listbox.curselection()
        start = tracks[sel[0]] if sel else tracks[0]
        items = []
        for n in tracks:
            path = self.drive.play_path(n)
            if not path:
                continue
            title, artist = self.track_title(n)
            if title and artist:
                name = f"{artist} - {title}"
            else:
                name = title or f"CD Track {n:02d}"
            items.append((path, name, self.toc["lengths"][n] / CD_SECTORS_PER_SECOND, n == start))
        self.app.play_cd_tracks(items)
        self._set_status(f"Playing the CD in XZN ({len(items)} tracks).")

    def rip(self, selected_only=False):
        if self.ripping or not self.toc or self.drive is None:
            return
        tracks = self._selected_tracks() if selected_only else sorted(self.toc["offsets"])
        if not tracks:
            mod = "Cmd" if IS_MAC else "Ctrl"
            self._set_status(f"Select the tracks to rip first ({mod}/Shift + click, {mod}+A for all).",
                             error=True)
            return
        fmt, br = self.format_var.get(), self.bitrate_var.get()
        if fmt not in self._encoders:
            self._set_status(f"{fmt} encoding is not available on this system.", error=True)
            return
        # Reading the disc for playback and ripping at the same time
        # makes both stutter: stop CD playback first.
        self.app.stop_cd_playback(self.drive)
        self.ripping = True
        self._cancel = threading.Event()
        self._save()
        drive, toc, info, outdir, cancel = self.drive, self.toc, self.info, self.outdir, self._cancel

        def report(kind, *args):
            self._queue.put(("rip_" + kind,) + args)

        threading.Thread(target=rip_cd, args=(drive, toc, tracks, outdir, fmt, br, info,
                                              report, cancel), daemon=True).start()
        self._set_status(f"Ripping {len(tracks)} track(s) …")

    def stop_rip(self):
        if self.ripping and self._cancel is not None:
            self._cancel.set()
            self._set_status("Stopping after the current read …")

    def eject(self):
        if self.ripping or self.drive is None:
            return
        drive = self.drive
        self.app.stop_cd_playback(drive)
        self.toc = self.info = None
        self._render()

        def work():
            try:
                drive.eject()
                self._queue.put(("ejected",))
            except Exception as e:
                self._queue.put(("eject_error", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def pick_outdir(self):
        d = filedialog.askdirectory(title="Rip CDs to …", initialdir=self.outdir, parent=self.win)
        if d:
            self.outdir = d
            self.outdir_lbl.config(text=d)
            self._save()

    def open_folder(self):
        try:
            os.makedirs(self.outdir, exist_ok=True)
        except Exception:
            _log_swallowed()
        open_with_system(self.outdir)

    # --- worker messages -------------------------------------------------
    def _poll(self):
        try:
            while True:
                msg = self._queue.get_nowait()
                kind = msg[0]
                if kind in ("toc", "info", "toc_error") and msg[1] != self._token:
                    continue
                if kind == "toc":
                    self.toc = msg[2]
                    self._render()
                    total = sum(self.toc["lengths"].values()) / CD_SECTORS_PER_SECOND
                    self._set_status(f"{len(self.toc['offsets'])} tracks — {fmt_time(total)}. "
                                     "Looking up CD info …")
                elif kind == "toc_error":
                    self._set_status(f"Could not read the CD: {msg[2]}", error=True)
                elif kind == "info":
                    self.info = msg[2]
                    sel = self.listbox.curselection()
                    self._render()
                    if sel:
                        self.listbox.selection_clear(0, "end")
                        for i in sel:
                            self.listbox.selection_set(i)
                    if self.toc:
                        n = len(self.toc["offsets"])
                        self._set_status((f"{n} tracks — CD info from MusicBrainz." if self.info
                                          else f"{n} tracks — no CD info found (files: track_XX).")
                                         + (" Cmd" if IS_MAC else " Ctrl") + "/Shift + click selects tracks.")
                elif kind == "rip_progress":
                    self._draw_progress(msg[1], msg[2])
                elif kind == "rip_file":
                    print(f"[XZN] Ripped: {msg[1]}")
                elif kind in ("rip_done", "rip_stopped", "rip_error"):
                    self.ripping = False
                    self._draw_progress(0)
                    if kind == "rip_done":
                        self._set_status(f"Done — {msg[1]} track(s) ripped.")
                        if self.eject_var.get():
                            self.eject()
                    elif kind == "rip_stopped":
                        self._set_status(f"Stopped after {msg[1]} track(s).")
                    else:
                        self._set_status(f"Rip failed: {msg[1]}", error=True)
                elif kind == "ejected":
                    self._set_status("CD ejected.")
                    if IS_MAC:
                        self.win.after(1500, self.scan)
                elif kind == "eject_error":
                    self._set_status(f"Eject failed: {msg[1]}", error=True)
        except queue.Empty:
            pass
        except Exception:
            _log_swallowed()
        try:
            if self.win.winfo_exists():
                self.win.after(100, self._poll)
        except Exception:
            _log_swallowed()

    def close(self):
        """Close the window. While ripping it is only hidden, so the rip
        continues; the CD button brings it back."""
        if self.ripping:
            try:
                self.win.withdraw()
            except Exception:
                _log_swallowed()
            return
        try:
            if self.win in self.app._extra_alpha_windows:
                self.app._extra_alpha_windows.remove(self.win)
        except Exception:
            _log_swallowed()
        try:
            self.win.destroy()
        except Exception:
            _log_swallowed()


# ------------------------------------------------------------------------
# Media keys
# ------------------------------------------------------------------------
# Both backends run in their own daemon thread and only ever put
# ("mediakey", action) tuples into the app's event queue, which the Tk
# thread polls. Actions: playpause, play, pause, stop, next, prev.

class WindowsMediaKeys:
    """Global hotkeys for the keyboard media keys (Windows, ctypes only).
    If another program already owns a key, that key is skipped."""

    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    MOD_NOREPEAT = 0x4000
    KEYS = {1: (0xB3, "playpause"), 2: (0xB0, "next"),
            3: (0xB1, "prev"), 4: (0xB2, "stop")}

    def __init__(self, events):
        self.events = events
        self._tid = None
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="xzn-mediakeys")

    def start(self):
        self._thread.start()

    def update(self, **status):
        pass  # nothing to publish on Windows

    def _run(self):
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        registered = []
        for hk_id, (vk, name) in self.KEYS.items():
            if user32.RegisterHotKey(None, hk_id, self.MOD_NOREPEAT, vk):
                registered.append(hk_id)
            else:
                print(f"[XZN] Media key '{name}' is in use by another program")
        if not registered:
            return
        print(f"[XZN] Media keys active ({len(registered)} keys)")
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == self.WM_HOTKEY and msg.wParam in self.KEYS:
                    self.events.put(("mediakey", self.KEYS[msg.wParam][1]))
        finally:
            for hk_id in registered:
                user32.UnregisterHotKey(None, hk_id)

    def stop(self):
        if self._tid:
            try:
                ctypes.windll.user32.PostThreadMessageW(self._tid, self.WM_QUIT, 0, 0)
            except Exception:
                _log_swallowed()


class MprisMediaKeys:
    """
    Linux: registers XZN as an MPRIS media player on the session D-Bus
    (org.mpris.MediaPlayer2.xzn). Desktop environments route the media
    keys to MPRIS players and show title/status in their media widgets.
    Uses 'jeepney' (pure Python); without it this backend is skipped.
    """

    BUS_NAME = "org.mpris.MediaPlayer2.xzn"
    PATH = "/org/mpris/MediaPlayer2"
    ROOT_IF = "org.mpris.MediaPlayer2"
    PLAYER_IF = "org.mpris.MediaPlayer2.Player"
    PROPS_IF = "org.freedesktop.DBus.Properties"

    INTROSPECT = """<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
 <interface name="org.freedesktop.DBus.Introspectable">
  <method name="Introspect"><arg type="s" direction="out"/></method>
 </interface>
 <interface name="org.freedesktop.DBus.Properties">
  <method name="Get"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
  <method name="GetAll"><arg type="s" direction="in"/><arg type="a{sv}" direction="out"/></method>
  <method name="Set"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/></method>
  <signal name="PropertiesChanged"><arg type="s"/><arg type="a{sv}"/><arg type="as"/></signal>
 </interface>
 <interface name="org.mpris.MediaPlayer2">
  <method name="Raise"/><method name="Quit"/>
  <property name="CanQuit" type="b" access="read"/>
  <property name="CanRaise" type="b" access="read"/>
  <property name="HasTrackList" type="b" access="read"/>
  <property name="Identity" type="s" access="read"/>
  <property name="SupportedUriSchemes" type="as" access="read"/>
  <property name="SupportedMimeTypes" type="as" access="read"/>
 </interface>
 <interface name="org.mpris.MediaPlayer2.Player">
  <method name="Next"/><method name="Previous"/><method name="Pause"/>
  <method name="PlayPause"/><method name="Stop"/><method name="Play"/>
  <method name="Seek"><arg type="x" direction="in"/></method>
  <method name="SetPosition"><arg type="o" direction="in"/><arg type="x" direction="in"/></method>
  <method name="OpenUri"><arg type="s" direction="in"/></method>
  <signal name="Seeked"><arg type="x"/></signal>
  <property name="PlaybackStatus" type="s" access="read"/>
  <property name="Rate" type="d" access="read"/>
  <property name="Metadata" type="a{sv}" access="read"/>
  <property name="Volume" type="d" access="read"/>
  <property name="Position" type="x" access="read"/>
  <property name="MinimumRate" type="d" access="read"/>
  <property name="MaximumRate" type="d" access="read"/>
  <property name="CanGoNext" type="b" access="read"/>
  <property name="CanGoPrevious" type="b" access="read"/>
  <property name="CanPlay" type="b" access="read"/>
  <property name="CanPause" type="b" access="read"/>
  <property name="CanSeek" type="b" access="read"/>
  <property name="CanControl" type="b" access="read"/>
 </interface>
</node>"""

    ACTIONS = {"PlayPause": "playpause", "Play": "play", "Pause": "pause",
               "Stop": "stop", "Next": "next", "Previous": "prev"}

    def __init__(self, events, position_fn=None):
        self.events = events
        self.position_fn = position_fn
        self._lock = threading.Lock()
        self._status = "Stopped"
        self._title = ""
        self._artist = ""
        self._length = 0.0
        self._track_no = 0
        self._outbox = queue.Queue()
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="xzn-mpris")

    @staticmethod
    def available():
        try:
            import jeepney  # noqa: F401
            return bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS"))
        except Exception:
            return False

    def start(self):
        self._thread.start()

    def stop(self):
        self._running = False

    # --- state published to the desktop ---------------------------------
    def update(self, status=None, title=None, artist=None, length=None, new_track=False):
        changed = {}
        with self._lock:
            if status is not None and status != self._status:
                self._status = status
                changed["PlaybackStatus"] = ("s", status)
            meta_changed = new_track
            for attr, val in (("_title", title), ("_artist", artist), ("_length", length)):
                if val is not None and val != getattr(self, attr):
                    setattr(self, attr, val)
                    meta_changed = True
            if new_track:
                self._track_no += 1
            if meta_changed:
                changed["Metadata"] = ("a{sv}", self._metadata())
        if changed:
            self._outbox.put(changed)

    def _metadata(self):
        md = {"mpris:trackid": ("o", f"/org/xzn/track/{self._track_no}")}
        if self._title:
            md["xesam:title"] = ("s", self._title)
        if self._artist:
            md["xesam:artist"] = ("as", [self._artist])
        if self._length > 0:
            md["mpris:length"] = ("x", int(self._length * 1_000_000))
        return md

    def _props(self, iface):
        if iface == self.ROOT_IF:
            return {
                "CanQuit": ("b", False), "CanRaise": ("b", False),
                "HasTrackList": ("b", False), "Identity": ("s", "XZN"),
                "SupportedUriSchemes": ("as", []), "SupportedMimeTypes": ("as", []),
            }
        if iface == self.PLAYER_IF:
            pos = 0
            try:
                if self.position_fn is not None:
                    pos = int(float(self.position_fn()) * 1_000_000)
            except Exception:
                _log_swallowed()
            with self._lock:
                return {
                    "PlaybackStatus": ("s", self._status),
                    "Rate": ("d", 1.0), "MinimumRate": ("d", 1.0), "MaximumRate": ("d", 1.0),
                    "Metadata": ("a{sv}", self._metadata()),
                    "Volume": ("d", 1.0), "Position": ("x", pos),
                    "CanGoNext": ("b", True), "CanGoPrevious": ("b", True),
                    "CanPlay": ("b", True), "CanPause": ("b", True),
                    "CanSeek": ("b", False), "CanControl": ("b", True),
                }
        return None

    # --- D-Bus loop -----------------------------------------------------
    def _run(self):
        try:
            from jeepney import MessageType, HeaderFields, DBusAddress, \
                new_method_return, new_error, new_signal
            from jeepney.bus_messages import message_bus
            from jeepney.io.blocking import open_dbus_connection
        except Exception as e:
            print(f"[XZN] MPRIS disabled (jeepney missing): {e}")
            return
        try:
            conn = open_dbus_connection(bus="SESSION")
            reply = conn.send_and_get_reply(message_bus.RequestName(self.BUS_NAME), timeout=5)
            if reply.body[0] not in (1, 4):   # primary owner / already owner
                print(f"[XZN] MPRIS name {self.BUS_NAME} is taken (another XZN running?)")
                conn.close()
                return
        except Exception as e:
            print(f"[XZN] MPRIS disabled (no session bus): {e}")
            return
        print("[XZN] Media keys active (MPRIS)")
        emitter = DBusAddress(self.PATH, interface=self.PROPS_IF)

        while self._running:
            try:
                while True:
                    changed = self._outbox.get_nowait()
                    conn.send(new_signal(emitter, "PropertiesChanged", "sa{sv}as",
                                         (self.PLAYER_IF, changed, [])))
            except queue.Empty:
                pass
            except Exception as e:
                print(f"[XZN] MPRIS signal failed: {e}")
            try:
                msg = conn.receive(timeout=0.25)
            except TimeoutError:
                continue
            except Exception as e:
                print(f"[XZN] MPRIS connection lost: {e}")
                break
            hdr = msg.header
            if hdr.message_type != MessageType.method_call:
                continue
            f = hdr.fields
            iface = f.get(HeaderFields.interface, "")
            member = f.get(HeaderFields.member, "")
            try:
                if f.get(HeaderFields.path) != self.PATH:
                    resp = new_error(msg, "org.freedesktop.DBus.Error.UnknownObject")
                elif member == "Introspect":
                    resp = new_method_return(msg, "s", (self.INTROSPECT,))
                elif iface == self.PROPS_IF and member == "GetAll":
                    props = self._props(msg.body[0])
                    resp = (new_method_return(msg, "a{sv}", (props,)) if props is not None
                            else new_error(msg, "org.freedesktop.DBus.Error.UnknownInterface"))
                elif iface == self.PROPS_IF and member == "Get":
                    props = self._props(msg.body[0]) or {}
                    if msg.body[1] in props:
                        resp = new_method_return(msg, "v", (props[msg.body[1]],))
                    else:
                        resp = new_error(msg, "org.freedesktop.DBus.Error.UnknownProperty")
                elif iface == self.PROPS_IF and member == "Set":
                    resp = new_error(msg, "org.freedesktop.DBus.Error.PropertyReadOnly")
                elif member in self.ACTIONS:
                    self.events.put(("mediakey", self.ACTIONS[member]))
                    resp = new_method_return(msg)
                elif member in ("Raise", "Quit", "Seek", "SetPosition", "OpenUri"):
                    resp = new_method_return(msg)   # accepted, not supported
                else:
                    resp = new_error(msg, "org.freedesktop.DBus.Error.UnknownMethod")
                conn.send(resp)
            except Exception as e:
                print(f"[XZN] MPRIS request {iface}.{member} failed: {e}")
        try:
            conn.close()
        except Exception:
            _log_swallowed()


# ------------------------------------------------------------------------
class App:
    def __init__(self):
        set_taskbar_app_id()

        if _DND_AVAILABLE and TkinterDnD is not None:
            try:
                self.root = TkinterDnD.Tk()
                print("[XZN] Drag & Drop enabled (tkinterdnd2)")
            except Exception as e:
                print(f"[XZN] TkinterDnD.Tk() failed, fallback: {e}")
                self.root = tk.Tk()
        else:
            self.root = tk.Tk()
            if not _DND_AVAILABLE:
                print("[XZN] Drag & Drop disabled (pip install tkinterdnd2)")

        apply_window_icon(self.root)
        self._setup_macos_menu()

        self.root.title("XZN")
        self.root.configure(bg=DEFAULT_PALETTE["BG"])
        # Start at the smallest size; _fit_full_window() refines it once
        # the widgets exist.
        self._full_minsize = (640, 700)
        self.root.minsize(*self._full_minsize)
        self.root.geometry("640x700")

        # Font used by the playlist for measuring dynamic column widths.
        # Created once so we don't recreate it on every resize.
        self._pl_font = tkfont.Font(family="Courier New", size=9, weight="normal")
        self._pl_resize_job = None

        settings = load_settings()
        self.accent = settings["accent"]
        self._initial_alpha = settings["alpha"]
        self._bg_path = settings["background"]
        self._saved_playlist = settings["playlist"]
        self._eq_gains = list(settings.get("eq_gains") or [0.0] * len(EQ_BANDS))
        self._eq_enabled = bool(settings.get("eq_enabled", True))
        self._eq_preamp = float(settings.get("eq_preamp") or 0.0)
        self._sample_offered = bool(settings.get("sample_offered", False))
        self._replaygain = bool(settings.get("replaygain", True))
        self._saved_resume = settings.get("resume")
        self._cd_settings = dict(settings.get("cd") or {})
        self._cd_win = None
        self.palette = build_palette(self.accent)

        # Runtime cache of track durations (path → seconds). Persisted
        # in the settings file so each file is only probed once.
        self._track_durations = dict(settings.get("track_durations") or {})
        # Custom display names for playlist rows (e.g. "♪ XZN Intro").
        self._display_names = {}

        print(f"[XZN] Accent color: {self.accent}")
        if _MUTAGEN_AVAILABLE:
            print("[XZN] Tag editing enabled (mutagen)")
        else:
            print("[XZN] Tag editing disabled (pip install mutagen)")

        self.events = queue.Queue()
        self.player = Player(self.events)
        self.player.start()

        self.player.set_eq_gains(self._eq_gains)
        self.player.set_eq_preamp(self._eq_preamp)
        self.player.set_eq_enabled(self._eq_enabled)
        self.player.set_replaygain(self._replaygain)
        self.player.next_provider = self._provide_next
        self.player.name_for = lambda p: (self._display_names.get(p) or os.path.basename(p))

        self.playlist = []
        self.current_index = -1
        self.shuffle = False
        self.repeat_mode = 0
        self._marquee_offset = 0
        self._seek_dragging = False
        # While a wheel-seek burst is in progress, _tick_mid must not
        # overwrite the seek slider with the real playback position.
        self._seek_wheel_active = False
        self._seek_wheel_timer = None
        self._auto_gain = 1.0
        self._bg_photo = None
        self._hero_size = (0, 0)
        self._hero_resize_job = None
        self._video_win = None
        # True after the user closed/hid the video window: following
        # videos then play audio-only until VIDEO (or F) shows it again.
        self._video_user_hidden = False
        self._eq_win = None
        self._info_win = None
        self._theme_win = None
        self._license_win = None
        self._tag_win = None
        self._main_frame = None
        self._frame_counter = 0
        self._dnd_widgets = []
        self._bg_popup = None
        # Registry for toplevels that should follow the main window's alpha
        # (used for nested windows like the License window)
        self._extra_alpha_windows = []

        # --- Animated background state ---------------------------------
        # These are populated by _render_bg() when the background file
        # is an animated GIF or WebP.
        self._bg_anim_job = None
        self._bg_frames_photo = None       # list of ImageTk.PhotoImage
        self._bg_frame_durations = None    # list of ints (ms per frame)
        self._bg_frame_index = 0
        # When True, the animation timer is not scheduled and the GIF
        # stays frozen on its current frame. Toggled by the player's
        # state events so the animation only runs while a track is
        # actually playing.
        self._bg_anim_paused = True

        # Gapless: index of the track handed to the player as "next".
        self._queued_next = None
        # Resume: position to continue from when ▶ is pressed after start.
        self._resume_pos = 0.0
        # Playlist type-to-search overlay (created on demand).
        self._search_entry = None
        self._search_var = None
        self._search_timer = None
        # Mini mode
        self._mini = False
        self._full_geometry = None
        self._ui_pack_order = []
        # Media keys (set up after the UI exists)
        self._media_keys = None
        # Key reference window (F1)
        self._help_win = None

        self._build_ui()
        self._fit_full_window()
        self._video_win = VideoWindow(self.root, self)
        self._video_win.apply_palette(self.palette)
        self._setup_dnd()

        try:
            self.root.attributes("-alpha", self._initial_alpha / 100.0)
        except Exception:
            _log_swallowed()
        self.root.after(120, self._apply_alpha_to_windows)

        if self._saved_playlist:
            for p in self._saved_playlist:
                if os.path.isfile(p) and p not in self.playlist:
                    self.playlist.append(p)
                    self.listbox.insert("end", self._format_pl_line(p))
            self._update_pl_count()
            self.status_left.config(
                text=f"{len(self.playlist)} tracks loaded from last session.",
                fg=self.palette["GRAY"])
            # Fill in any missing durations in the background (e.g.
            # after the settings cache has been pruned or cleared).
            self._probe_durations_async(self.playlist)

        # Re-apply the friendly display name for the bundled intro,
        # if it's still in the playlist. This restores the "♪ XZN
        # Intro (Sample)" label without needing to persist custom names.
        app_dir = _get_app_data_dir()
        if app_dir:
            intro_path = os.path.join(app_dir, INTRO_FILENAME)
            if intro_path in self.playlist:
                self._display_names[intro_path] = "♪ XZN Intro (Sample)"
                self._refresh_line_for(intro_path)

        # --- Sample intro track -----------------------------------------
        # Offer the bundled sample intro once, on first run. The MP3 is
        # copied to a durable per-user location so the playlist entry
        # survives restarts (PyInstaller's _MEIPASS is wiped between
        # launches). If the user deletes the entry, it stays gone.
        if not self._sample_offered:
            try:
                intro_path = ensure_sample_track()
                if intro_path and intro_path not in self.playlist:
                    self._display_names[intro_path] = "♪ XZN Intro (Sample)"
                    self.playlist.insert(0, intro_path)
                    self.listbox.insert(0, self._format_pl_line(intro_path))
                    self._update_pl_count()
                    self._refresh_selection()
                    self._probe_durations_async([intro_path])
                    print(f"[XZN] Sample intro offered: {intro_path}")
            except Exception as e:
                print(f"[XZN] Sample intro setup failed: {e}")
            self._sample_offered = True
            self._persist_settings()

        self._restore_resume_point()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<space>", lambda e: self.toggle_play())
        self.root.bind("<Left>",  lambda e: self.seek_by(-5))
        self.root.bind("<Right>", lambda e: self.seek_by(5))
        self.root.bind("<Up>",    lambda e: self.nudge_volume(+5))
        self.root.bind("<Down>",  lambda e: self.nudge_volume(-5))
        # Letter shortcuts. While the mouse is over the playlist, typing
        # starts the playlist search instead (see _shortcut).
        for k in ("m", "M"):
            self.root.bind(k, self._shortcut(self.toggle_mute))
        for k in ("n", "N"):
            self.root.bind(k, self._shortcut(self.next_track))
        for k in ("p", "P"):
            self.root.bind(k, self._shortcut(self.prev_track))
        for k in ("f", "F"):
            self.root.bind(k, self._shortcut(self._toggle_video_fullscreen))
        for k in ("c", "C"):
            self.root.bind(k, self._shortcut(self.show_theme))
        for k in ("e", "E"):
            self.root.bind(k, self._shortcut(self.show_equalizer))
        self.root.bind("<Key>", self._on_root_key)
        # Mini player, Winamp-style "windowshade" key. With Ctrl it also
        # works while the mouse is over the playlist (no search there).
        for k in ("<Control-w>", "<Control-W>"):
            self.root.bind(k, lambda e: (self.toggle_mini(), "break")[1])
        # F1 works from every XZN window (main, EQ, the help itself …).
        self.root.bind_all("<F1>", self.toggle_shortcuts)
        # Esc in the main window closes an open key overview too, so both
        # keys named in its footer work no matter which window has focus.
        self.root.bind("<Escape>", self._close_shortcuts)

        self.root.after(80, self._initial_bg)
        self.root.after(500, self._start_media_keys)

    # --- Playlist row formatting ----------------------------------------
    def _get_max_pl_chars(self):
        """
        Determine how many characters of the playlist font fit into
        the current listbox width. Used to right-align the duration
        column against the actual right edge of the widget.
        """
        try:
            w = self.listbox.winfo_width()
            if w <= 1:
                return 60
            cw = self._pl_font.measure("0")
            if cw <= 0:
                return 60
            # Subtract a couple of characters for scrollbar overlap/safety.
            return max(30, (w // cw) - 2)
        except Exception:
            return 60

    def _format_pl_line(self, path):
        """
        Format a playlist entry as "name<pad>  dur" with the duration
        right-aligned against the actual right edge of the listbox.
        Uses the custom display name when one is registered (e.g. the
        intro track), otherwise the file's basename.
        """
        name = self._display_names.get(path) or os.path.basename(path)
        dur = self._track_durations.get(path)
        dur_str = fmt_time(dur) if dur and dur > 0 else "--:--"
        # Reserve exactly 7 characters for the duration (e.g. "1:23:45"
        # or "  05:07") so all durations line up vertically.
        dur_str = f"{dur_str:>7}"
        
        total_chars = self._get_max_pl_chars()
        max_name_len = total_chars - 9  # 7 for dur_str + 2 spaces
        
        if max_name_len < 10:
            max_name_len = 10
            
        if len(name) > max_name_len:
            name = name[:max_name_len - 1] + "…"
            
        pad = " " * max(0, total_chars - len(name) - len(dur_str))
        return f"{name}{pad}{dur_str}"

    def _on_listbox_resize(self, event=None):
        """
        Debounced handler for listbox resizes. Rebuilds the playlist
        strings so the duration padding matches the new width.
        """
        if self._pl_resize_job is not None:
            try:
                self.root.after_cancel(self._pl_resize_job)
            except Exception:
                _log_swallowed()
        self._pl_resize_job = self.root.after(150, self._refresh_all_pl_lines)

    def _refresh_all_pl_lines(self):
        """
        Redraw every playlist entry (e.g. after a window resize) while
        preserving the current selection.
        """
        self._pl_resize_job = None
        try:
            sel = self.listbox.curselection()
            self.listbox.delete(0, "end")
            for p in self.playlist:
                self.listbox.insert("end", self._format_pl_line(p))
            for i in sel:
                self.listbox.selection_set(i)
        except Exception as e:
            print(f"[XZN] Playlist refresh failed: {e}")

    def _refresh_line_for(self, path):
        """Update a single listbox line (e.g. after a duration probe)."""
        try:
            idx = self.playlist.index(path)
        except ValueError:
            return
        try:
            was_selected = idx in self.listbox.curselection()
            self.listbox.delete(idx)
            self.listbox.insert(idx, self._format_pl_line(path))
            if was_selected:
                self.listbox.selection_set(idx)
        except Exception:
            _log_swallowed()

    def _probe_durations_async(self, paths):
        """
        Probe durations for any new paths in the background and fill
        in the durations in the playlist as they become available.

        Runs in a worker thread. UI updates go through `after(0, …)`
        because tkinter is not thread-safe.
        """
        pending = [p for p in paths if p not in self._track_durations and not is_cdda(p)]
        if not pending:
            return

        def worker():
            new_any = False
            for p in pending:
                try:
                    with av.open(p) as cont:
                        dur = float(cont.duration) / 1_000_000 if cont.duration else 0.0
                        if dur > 0:
                            self._track_durations[p] = dur
                            new_any = True
                            try:
                                self.root.after(0, self._refresh_line_for, p)
                            except Exception:
                                _log_swallowed()
                except Exception as e:
                    print(f"[XZN] Duration probe failed for {p}: {e}")
            if new_any:
                # Persist the new durations so subsequent launches
                # don't have to re-probe. Runs on the main thread.
                try:
                    self.root.after(0, self._persist_settings)
                except Exception:
                    _log_swallowed()

        threading.Thread(target=worker, daemon=True).start()

    # --- macOS menu ------------------------------------------------------
    def _setup_macos_menu(self):
        """
        On macOS, hide Tk's default menu entries (Python / File / Edit /
        Window / Help) by giving the app an empty menubar. We also keep
        a reference to it so every Toplevel can share the same menubar
        — otherwise, focusing a dialog would make macOS switch back to
        the default Tk menu set.
        """
        if not IS_MAC:
            return
        try:
            self._mac_menubar = tk.Menu(self.root)
            self.root.config(menu=self._mac_menubar)
            # "About Xenon-Zones" in the macOS app menu → our own Info
            # window. Tk calls tkAboutDialog for that entry (older Tk
            # versions use ::tk::mac::ShowAbout); overriding the built-in
            # standardAboutPanel is not enough, so register both names.
            for name in ("tkAboutDialog", "::tk::mac::ShowAbout"):
                try:
                    self.root.createcommand(name, self._show_mac_about)
                except tk.TclError:
                    _log_swallowed()
            # Files opened from Finder ("Open with XZN", drag onto the Dock
            # icon) arrive as an Apple Event, not as command-line arguments.
            try:
                self.root.createcommand("::tk::mac::OpenDocument", self._mac_open_documents)
            except tk.TclError:
                _log_swallowed()
            print("[XZN] macOS menu customized.")
        except Exception as e:
            self._mac_menubar = None
            print(f"[XZN] macOS menu setup failed: {e}")

    def _attach_macos_menu(self, win):
        """
        macOS only: share the app's menubar with a Toplevel, so that
        focusing it does not switch the system menu to the default
        Tk 'Python / File / Edit / Window / Help' entries.
        """
        if not IS_MAC:
            return
        mb = getattr(self, "_mac_menubar", None)
        if mb is None:
            return
        try:
            win.config(menu=mb)
        except Exception as e:
            print(f"[XZN] _attach_macos_menu failed: {e}")

    def _mac_open_documents(self, *paths):
        files = [p for p in paths if os.path.isfile(p)]
        if files:
            self.root.after(100, lambda: self.add_paths(files))

    def _show_mac_about(self):
        try:
            self.show_info()
        except Exception as e:
            print(f"[XZN] macOS About failed: {e}")

    def _persist_settings(self):
        try:
            alpha = int(self.alpha_scale.get())
        except Exception:
            alpha = 100
        save_settings({
            "accent": self.accent,
            "alpha": alpha,
            "background": self._bg_path,
            "playlist": list(self.playlist),
            "eq_gains": list(self._eq_gains),
            "eq_enabled": bool(self._eq_enabled),
            "eq_preamp": float(self._eq_preamp),
            "sample_offered": bool(getattr(self, "_sample_offered", False)),
            "track_durations": {k: v for k, v in (getattr(self, "_track_durations", {}) or {}).items()
                                if not is_cdda(k)},
            "replaygain": bool(getattr(self, "_replaygain", True)),
            "resume": self._resume_state(),
            "cd": dict(getattr(self, "_cd_settings", {}) or {}),
        })

    def set_replaygain(self, en):
        self._replaygain = bool(en)
        self.player.set_replaygain(self._replaygain)
        self._persist_settings()

    def set_eq_enabled(self, en):
        self._eq_enabled = bool(en)
        self.player.set_eq_enabled(self._eq_enabled)
        self._persist_settings()

    def set_eq_preamp(self, db):
        self._eq_preamp = float(db)
        self.player.set_eq_preamp(self._eq_preamp)
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

        # Video window must always stay fully opaque
        video_win = None
        try:
            if self._video_win is not None and self._video_win.is_alive():
                video_win = self._video_win.win
        except Exception:
            video_win = None

        bg_popup_win = None
        try:
            if self._bg_popup is not None:
                bg_popup_win = self._bg_popup.win
        except Exception:
            bg_popup_win = None

        # --- Collect ALL Toplevels recursively --------------------------
        # This is the key fix: nested windows (e.g. License inside Info)
        # are no longer missed.
        all_toplevels = []

        def _collect(widget):
            try:
                for child in widget.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        all_toplevels.append(child)
                    # Recurse regardless, so nested toplevels are found
                    _collect(child)
            except Exception:
                _log_swallowed()

        _collect(self.root)

        # Also include explicitly registered extras (belt & suspenders)
        for w in list(getattr(self, "_extra_alpha_windows", [])):
            try:
                if w.winfo_exists() and w not in all_toplevels:
                    all_toplevels.append(w)
            except Exception:
                _log_swallowed()

        for w in all_toplevels:
            if video_win is not None and w is video_win:
                continue
            if bg_popup_win is not None and w is bg_popup_win:
                continue
            try:
                if w.winfo_exists():
                    w.attributes("-alpha", a)
            except Exception:
                _log_swallowed()

        # Video window always fully opaque
        if video_win is not None:
            try:
                video_win.attributes("-alpha", 1.0)
            except Exception:
                _log_swallowed()

        if bg_popup_win is not None:
            try:
                bg_popup_win.attributes("-alpha", 1.0)
            except Exception:
                _log_swallowed()

    def _setup_dnd(self):
        if not _DND_AVAILABLE or DND_FILES is None:
            return
        try:
            targets = [self.listbox]
            for w in (self.listbox.master, self.listbox.master.master):
                if isinstance(w, tk.Widget):
                    targets.append(w)
            for w in targets:
                try:
                    w.drop_target_register(DND_FILES)
                    w.dnd_bind("<<Drop>>", self._on_drop)
                    self._dnd_widgets.append(w)
                except Exception as e:
                    print(f"[XZN] DnD registration failed: {e}")
            print("[XZN] Playlist drop active.")
        except Exception as e:
            print(f"[XZN] _setup_dnd failed: {e}")

    def _on_drop(self, event):
        try:
            raw = event.data
            paths = self._parse_dnd_paths(raw)
        except Exception as e:
            print(f"[XZN] Drop parsing failed: {e}")
            return

        media = []
        dirs = []
        playlists = []
        for p in paths:
            if is_media_file(p):
                media.append(p)
            elif is_playlist_file(p):
                playlists.append(p)
            elif os.path.isdir(p):
                dirs.append(p)

        for d in dirs:
            for root, _dirs, files in os.walk(d):
                for f in sorted(files):
                    p = os.path.join(root, f)
                    if is_media_file(p):
                        media.append(p)

        for pl_path in playlists:
            entries, _missing = read_m3u(pl_path)
            media.extend(entries)

        if not media:
            self.status_left.config(text="Drop: no media files found.",
                                    fg=self.palette["GRAY"])
            return
        self.add_paths(media)
        self.status_left.config(text=f"{len(media)} file(s) added via drop.",
                                fg=self.palette["GRAY"])

    @staticmethod
    def _parse_dnd_paths(data):
        paths = []
        if not data:
            return paths
        s = str(data).strip()
        i = 0
        n = len(s)
        while i < n:
            ch = s[i]
            if ch == "{":
                j = s.find("}", i + 1)
                if j == -1:
                    paths.append(s[i + 1:].strip())
                    break
                paths.append(s[i + 1:j].strip())
                i = j + 1
            elif ch.isspace():
                i += 1
            else:
                j = i
                while j < n and not s[j].isspace():
                    j += 1
                paths.append(s[i:j].strip())
                i = j
        return [p for p in paths if p]

    def apply_accent(self, accent_hex, persist=False, rebuild=True):
        self.accent = accent_hex
        self.palette = build_palette(accent_hex)
        if persist:
            self._persist_settings()
        if rebuild:
            self._rebuild_ui()

    def _rebuild_ui(self):
        state_keep = {
            "playlist": list(self.playlist),
            "current_index": self.current_index,
            "shuffle": self.shuffle,
            "repeat_mode": self.repeat_mode,
            "vol": int(self.vol_scale.get()) if hasattr(self, "vol_scale") else 85,
            "bal": int(self.bal_scale.get()) if hasattr(self, "bal_scale") else 50,
            "bg_path": self._bg_path,
            "status_text": self.status_left.cget("text") if hasattr(self, "status_left") else "",
            "alpha": int(self.alpha_scale.get()) if hasattr(self, "alpha_scale") else 100,
        }

        if self._main_frame is not None:
            try:
                self._main_frame.destroy()
            except Exception:
                _log_swallowed()
            self._main_frame = None
            self._dnd_widgets = []

        self._build_ui()
        self._setup_dnd()

        self.playlist = state_keep["playlist"]
        self.current_index = state_keep["current_index"]
        self.shuffle = state_keep["shuffle"]
        self.repeat_mode = state_keep["repeat_mode"]
        self._bg_path = state_keep["bg_path"]

        for p in self.playlist:
            self.listbox.insert("end", self._format_pl_line(p))
        self._update_pl_count()
        self._refresh_selection()

        try:
            self.vol_scale.set(state_keep["vol"])
            self.bal_scale.set(state_keep["bal"])
            self.alpha_scale.set(state_keep["alpha"])
        except Exception:
            _log_swallowed()

        if self.shuffle:
            self._set_icon(self.btn_shuf, "shuffle", True)
        if self.player._mute:
            self._set_icon(self.btn_mute, "mute", True)
        if self.player.state == "playing":
            self._set_icon(self.btn_play, "pause")
        if self.repeat_mode != 0:
            label = {1: "REP:ONE", 2: "REP:ALL"}[self.repeat_mode]
            self.btn_rep.config(text=label, fg=self.palette["ACCENT"], relief="sunken")

        if state_keep["status_text"]:
            self.status_left.config(text=state_keep["status_text"], fg=self.palette["GRAY"])

        if self._video_win is not None and self._video_win.is_alive():
            self._video_win.apply_palette(self.palette)

        if self._mini:
            self._set_mini(True, resize=False)

        # An open key reference follows the new color scheme.
        if self._help_win is not None and self._help_win.is_alive():
            self._help_win.close()
            self._help_win = ShortcutsWindow(self.root, self)

        self._hero_size = (0, 0)
        self.root.after(40, self._render_bg)
        self.root.after(80, self._apply_alpha_to_windows)

    def _icon(self, name, active=False):
        """Theme-colored PhotoImage for a button icon (cached)."""
        if not hasattr(self, "_icon_cache"):
            self._icon_cache = {}
            try:
                line = int(tkfont.Font(font=FONT_BTN).metrics("linespace"))
            except Exception:
                line = 16
            # Inner height of a text button (line + 2 x pady 3); icons fill
            # most of it, leaving a small margin.
            self._btn_inner_h = line + 6
            # (Windows keeps the size it already looked right with)
            self._icon_size = max(12, min(24, line - 2)) if IS_WINDOWS else max(14, min(26, line))
        color = self.palette["ACCENT"] if active else self.palette["WHITE"]
        key = (name, color, self._icon_size)
        if key not in self._icon_cache:
            self._icon_cache[key] = ImageTk.PhotoImage(draw_icon(name, self._icon_size, color))
        return self._icon_cache[key]

    def _set_icon(self, btn, name, active=False):
        """Switch a button's icon; active = highlighted (accent color)."""
        try:
            img = self._icon(name, active)
            btn.config(image=img, relief="sunken" if active else "raised")
            btn._xzn_icon = (name, active)
            btn._xzn_img = img
        except Exception:
            _log_swallowed()

    def _mkbtn(self, parent, text, cmd, w=4, bg=None, icon=None, tip=None):
        """Flat button. icon: name of a drawn icon (see draw_icon), shown
        instead of the text; tip: hover explanation (str or callable)."""
        p = self.palette
        bg_color = bg if bg else p["RAISED"]
        fg_color = p["WHITE"]
        hover_bg = lighten(bg_color, 0.15)
        # An icon button keeps a single space as text with compound="center"
        # so Tk sizes it exactly like the text buttons next to it.
        extra = {}
        if icon:
            img = self._icon(icon)
            if IS_MAC or not IS_WINDOWS:
                # Label: image only (no text). Tk ignores padx/pady for image
                # labels, so the size is set in pixels: same height as the
                # text buttons, some room left and right of the icon.
                extra = {"image": img, "width": self._icon_size + 20,
                         "height": self._btn_inner_h}
                text = ""
            else:
                # tk.Button: a space as text with compound="center" makes
                # Tk size it exactly like the text buttons.
                extra = {"image": img, "compound": "center"}
                text = " "

        if IS_MAC or not IS_WINDOWS:
            btn = tk.Label(parent, text=text, bg=bg_color, fg=fg_color,
                           font=FONT_BTN, padx=0 if icon else 8, pady=0 if icon else 3,
                           relief="raised", bd=1, cursor="hand2", **extra)
            btn.bind("<Button-1>", lambda e: cmd())
            btn.bind("<Enter>", lambda e: btn.config(bg=hover_bg))
            btn.bind("<Leave>", lambda e: btn.config(bg=bg_color))
        else:
            if icon:
                extra.update(padx=10)
            else:
                extra.update(width=w)
            btn = tk.Button(parent, text=text, command=cmd,
                            bg=bg_color, fg=fg_color,
                            activebackground=hover_bg,
                            activeforeground=fg_color,
                            relief="raised", bd=1, font=FONT_BTN, takefocus=0, **extra)
        if icon:
            btn._xzn_icon = (icon, False)
            btn._xzn_img = extra["image"]        # keep the image alive
            # macOS: make sure the image is drawn once the button is mapped
            btn.bind("<Map>", lambda e, b=btn: b.after_idle(
                lambda: b.winfo_exists() and b.config(image=b._xzn_img)), add="+")
        if tip:
            Tooltip(btn, tip, p)
        return btn

    def _mk_pl_scale(self, parent):
        p = self.palette
        return tk.Scale(parent, from_=0, to=1, orient="vertical",
                        showvalue=False, resolution=0.001,
                        bg=p["PANEL"], fg=p["ACCENT"],
                        troughcolor=p["SUNKEN"],
                        highlightthickness=0, sliderrelief="flat",
                        width=14,
                        command=self._on_pl_scroll)

    def _on_pl_scroll(self, v):
        try:
            self.listbox.yview_moveto(float(v))
        except Exception:
            _log_swallowed()

    def _sync_pl_scrollbar(self, *args):
        try:
            first, last = self.listbox.yview()
            if last - first >= 1.0:
                self.pl_scale.set(0)
            else:
                self.pl_scale.set(first)
        except Exception:
            _log_swallowed()

    def _build_ui(self):
        p = self.palette
        self.root.configure(bg=p["BG"])

        self._main_frame = tk.Frame(self.root, bg=p["BG"])
        self._main_frame.pack(fill="both", expand=True)

        hdr = tk.Frame(self._main_frame, bg=p["PANEL"], height=44)
        hdr.pack(fill="x", padx=8, pady=(8, 4))
        hdr.pack_propagate(False)
        self._ui_hdr = hdr

        self._mkbtn(hdr, "INFO", self.show_info, w=6,
                    tip="About XZN").pack(side="left", padx=(10, 0))
        self._mkbtn(hdr, "C", self.show_theme, w=3, icon="palette",
                    tip="Color scheme  (C)").pack(side="left", padx=(4, 0))

        self.btn_bg = self._mkbtn(hdr, "BG", self._bg_menu, w=4,
                                  tip="Background image  (right-click: remove)")
        self.btn_bg.pack(side="right", padx=(0, 6))
        self.btn_bg.bind("<Button-3>", lambda e: self.remove_bg())
        self.btn_bg.bind("<Button-2>", lambda e: self.remove_bg())

        alpha_box = tk.Frame(hdr, bg=p["PANEL"])
        alpha_box.pack(side="right", padx=(0, 10))
        tk.Label(alpha_box, text="Alpha", font=FONT_UI, bg=p["PANEL"], fg=p["GRAY"]).pack(side="left")
        self.alpha_scale = tk.Scale(alpha_box, from_=70, to=100, orient="horizontal",
                                    length=110, showvalue=False,
                                    command=self._on_alpha_cmd,
                                    bg=p["PANEL"], fg=p["ACCENT"], troughcolor=p["SUNKEN"],
                                    highlightthickness=0, sliderrelief="flat")
        self.alpha_scale.set(getattr(self, "_initial_alpha", 100))
        self.alpha_scale.pack(side="left", padx=(4, 0))
        # --- Mouse-wheel control for the window Alpha slider (5% steps) ---
        _bind_scale_wheel(self.alpha_scale, self._on_alpha_cmd,
                          lo=70, hi=100, step=5, round_to=1.0)

        self.hero = tk.Canvas(self._main_frame, height=200, bg=p["PANEL"], highlightthickness=0)
        self.hero.pack(fill="both", expand=False, padx=8, pady=(0, 6))
        self.hero.bind("<Configure>", self._on_hero_configure)
        # Clicking the XZN wordmark switches to the mini player.
        self.hero.bind("<Button-1>", self._on_hero_click)
        self.hero.bind("<Motion>", self._on_hero_motion)

        mrow = tk.Frame(self._main_frame, bg=p["PANEL"])
        mrow.pack(fill="x", padx=8, pady=(0, 3))
        # Wordmark shown only in mini mode; clicking it restores the
        # full player (the hero canvas with the big wordmark is hidden).
        # Drawn exactly like the big wordmark (white with dark outline,
        # dark with light outline on light themes), just smaller.
        mini_font = tkfont.Font(family="Helvetica", size=16, weight="bold")
        self.mini_logo = tk.Canvas(mrow, bg=p["PANEL"], highlightthickness=0, bd=0,
                                   width=mini_font.measure("XZN") + 8,
                                   height=mini_font.metrics("linespace") + 4,
                                   cursor="hand2")
        self._draw_wordmark(self.mini_logo, 4, 2, mini_font, "logo")
        self.mini_logo.bind("<Button-1>", lambda e: self.toggle_mini())
        self.state_lbl = tk.Label(mrow, text="■", font=("Courier New", 12, "bold"),
                                  bg=p["SUNKEN"], fg=p["ACCENT"], width=3)
        self.state_lbl.pack(side="left", padx=(0, 6), ipady=2)
        # width=1: the scrolling text must never widen the window; the
        # label simply fills whatever space the row has.
        self.marquee = tk.Label(mrow, text="", font=FONT_LCD, bg=p["SUNKEN"],
                                fg=p["CYAN"], anchor="w", width=1)
        self.marquee.pack(side="left", fill="x", expand=True, ipady=3)

        trow = tk.Frame(self._main_frame, bg=p["PANEL"])
        trow.pack(fill="x", padx=8, pady=(0, 2))
        self._ui_mrow = mrow
        self._ui_trow = trow
        self.cur_lbl = tk.Label(trow, text="00:00", font=FONT_LG, bg=p["SUNKEN"], fg=p["ACCENT"])
        self.cur_lbl.pack(side="left")
        tk.Label(trow, text="/", font=FONT_LG, bg=p["PANEL"], fg=p["GRAY"]).pack(side="left")
        self.dur_lbl = tk.Label(trow, text="--:--", font=FONT_LG, bg=p["SUNKEN"], fg=p["ACCENT"])
        self.dur_lbl.pack(side="left")

        # Volume slider for mini mode (the VOL row is hidden there).
        # Same look as the big one; both stay in sync via _on_vol_cmd.
        self._mini_vol_box = tk.Frame(trow, bg=p["PANEL"])
        tk.Label(self._mini_vol_box, text="VOL", font=FONT_UI_B,
                 bg=p["PANEL"], fg=p["GRAY"]).pack(side="left")
        self.mini_vol_scale = tk.Scale(self._mini_vol_box, from_=0, to=100, orient="horizontal",
                                       length=130, showvalue=False, command=self._on_vol_cmd,
                                       bg=p["PANEL"], fg=p["ACCENT"], troughcolor=p["SUNKEN"],
                                       highlightthickness=0, sliderrelief="flat")
        self.mini_vol_scale.set(85)
        self.mini_vol_scale.pack(side="left", padx=(4, 0))
        _bind_scale_wheel(self.mini_vol_scale, self._on_vol_cmd,
                          lo=0, hi=100, step=5, round_to=1.0)

        brow = tk.Frame(self._main_frame, bg=p["PANEL"])
        brow.pack(fill="x", padx=8, pady=(0, 2))
        self._ui_brow = brow
        self.btn_prev = self._mkbtn(brow, "|◀", self.prev_track, icon="prev",
                                    tip="Previous track  (P)")
        self.btn_prev.pack(side="left")
        self.btn_play = self._mkbtn(brow, "▶", self.toggle_play, w=5, icon="play",
                                    tip="Play / Pause  (Space)")
        self.btn_play.pack(side="left", padx=(2, 0))
        self.btn_stop = self._mkbtn(brow, "■", self.stop_play, icon="stop", tip="Stop")
        self.btn_stop.pack(side="left", padx=(2, 0))
        self.btn_next = self._mkbtn(brow, "▶|", self.next_track, icon="next",
                                    tip="Next track  (N)")
        self.btn_next.pack(side="left", padx=(2, 0))
        self.btn_shuf = self._mkbtn(brow, "SHUF", self.toggle_shuffle, w=6, icon="shuffle",
                                    tip="Shuffle (random order)")
        self.btn_shuf.pack(side="right")
        self.btn_rep = self._mkbtn(brow, "REP:OFF", self.cycle_repeat, w=9,
                                   tip="Repeat: off / one track / whole playlist")
        self.btn_rep.pack(side="right", padx=(0, 2))
        self._mkbtn(brow, "VIDEO", self._toggle_video_window, w=6,
                    tip="Video window  (F: fullscreen)").pack(side="right", padx=(0, 6))
        self._mkbtn(brow, "CD", self.show_cd, w=3,
                    tip="Play or rip an audio CD").pack(side="right", padx=(0, 2))

        self.seek = tk.Scale(self._main_frame, from_=0, to=1000, orient="horizontal",
                             length=120, command=self._on_seek_cmd, bg=p["PANEL"],
                             fg=p["ACCENT"], troughcolor=p["SUNKEN"], highlightthickness=0,
                             sliderrelief="flat", showvalue=False)
        # Small requested length: the bar stretches with the window anyway
        # (fill="x"), and a large value would keep the mini player wide.
        self.seek.pack(fill="x", padx=8, pady=(2, 2))
        self.seek.bind("<Button-1>", lambda e: setattr(self, "_seek_dragging", True))
        self.seek.bind("<ButtonRelease-1>", self._on_seek_release)
        # Mouse-wheel scrolling on the timeline (5 s per notch)
        self.seek.bind("<MouseWheel>", self._on_seek_wheel)
        self.seek.bind("<Button-4>", self._on_seek_wheel)
        self.seek.bind("<Button-5>", self._on_seek_wheel)

        vrow = tk.Frame(self._main_frame, bg=p["PANEL"])
        vrow.pack(fill="x", padx=8, pady=(0, 4))
        self._ui_vrow = vrow
        tk.Label(vrow, text="VOL", font=FONT_UI_B, bg=p["PANEL"], fg=p["GRAY"]).pack(side="left")
        self.vol_scale = tk.Scale(vrow, from_=0, to=100, orient="horizontal", length=130,
                                  showvalue=False, command=self._on_vol_cmd, bg=p["PANEL"],
                                  fg=p["ACCENT"], troughcolor=p["SUNKEN"], highlightthickness=0,
                                  sliderrelief="flat")
        self.vol_scale.set(85)
        self.vol_scale.pack(side="left", padx=(4, 10))
        # Mouse-wheel control for Volume (5% steps)
        _bind_scale_wheel(self.vol_scale, self._on_vol_cmd,
                          lo=0, hi=100, step=5, round_to=1.0)

        tk.Label(vrow, text="BAL", font=FONT_UI_B, bg=p["PANEL"], fg=p["GRAY"]).pack(side="left")
        self.bal_scale = tk.Scale(vrow, from_=0, to=100, orient="horizontal", length=130,
                                  showvalue=False, command=self._on_bal_cmd, bg=p["PANEL"],
                                  fg=p["ACCENT"], troughcolor=p["SUNKEN"], highlightthickness=0,
                                  sliderrelief="flat")
        self.bal_scale.set(50)
        self.bal_scale.pack(side="left", padx=(4, 10))
        # Mouse-wheel control for Balance (5-unit steps)
        _bind_scale_wheel(self.bal_scale, self._on_bal_cmd,
                          lo=0, hi=100, step=5, round_to=1.0)

        self.btn_eq = self._mkbtn(vrow, "EQ", self.show_equalizer, w=4, icon="eq",
                                  tip="Equalizer  (E)")
        self.btn_eq.pack(side="right", padx=(0, 4))
        self.btn_mute = self._mkbtn(vrow, "MUTE", self.toggle_mute, w=6, icon="volume",
                                    tip="Mute  (M)")
        self.btn_mute.pack(side="right")

        self.anal = tk.Canvas(self._main_frame, height=44, bg=p["SUNKEN"], highlightthickness=0)
        self.anal.pack(fill="x", padx=8, pady=(0, 4))

        plf = tk.Frame(self._main_frame, bg=p["PANEL"])
        plf.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self._ui_plf = plf
        top = tk.Frame(plf, bg=p["PANEL"])
        top.pack(fill="x")
        tk.Label(top, text="PLAYLIST", font=FONT_UI_B, bg=p["PANEL"], fg=p["GRAY"]).pack(side="left")
        self.pl_count = tk.Label(top, text="", font=FONT_UI, bg=p["PANEL"], fg=p["ACCENT_LO"])
        self.pl_count.pack(side="right")

        lrow = tk.Frame(plf, bg=p["SUNKEN"])
        lrow.pack(fill="both", expand=True, pady=(2, 4))
        self._ui_lrow = lrow
        self._search_entry = None   # old one died with the old UI
        list_bg = darken(p["BG"], 0.65) if luminance(p["BG"]) < 0.5 else "#ffffff"
        select_bg = mix(p["PANEL"], p["ACCENT"], 0.35)

        # selectmode="extended" enables Shift-click range selection and
        # Ctrl/Cmd-click toggle for multi-selection in the playlist.
        self.listbox = tk.Listbox(lrow, height=8, selectmode="extended", bd=0,
                                  highlightthickness=0, exportselection=False,
                                  bg=list_bg, fg=p["WHITE"], font=("Courier New", 9),
                                  selectbackground=select_bg, selectforeground=p["WHITE"],
                                  activestyle="none")
        self.pl_scale = self._mk_pl_scale(lrow)
        self.listbox.configure(yscrollcommand=self._sync_pl_scrollbar)
        self.listbox.pack(side="left", fill="both", expand=True)
        self.pl_scale.pack(side="right", fill="y", padx=(2, 0))
        self.listbox.bind("<Double-Button-1>", lambda e: self._play_selected())
        self.listbox.bind("<Button-3>", self._on_playlist_right_click)
        self.listbox.bind("<Button-2>", self._on_playlist_right_click)
        self.listbox.bind("<Control-Button-1>", self._on_playlist_right_click)
        self.listbox.bind("<MouseWheel>", self._on_list_mousewheel)
        self.listbox.bind("<Button-4>", self._on_list_mousewheel)
        self.listbox.bind("<Button-5>", self._on_list_mousewheel)
        
        # Rebuild strings dynamically to keep durations right-aligned.
        self.listbox.bind("<Configure>", self._on_listbox_resize)

        pbtns = tk.Frame(plf, bg=p["PANEL"])
        pbtns.pack(fill="x")
        self._mkbtn(pbtns, "ADD FILES",  self.add_files_dialog,  w=10).pack(side="left")
        self._mkbtn(pbtns, "ADD FOLDER", self.add_folder_dialog, w=12).pack(side="left", padx=(4, 0))
        self._mkbtn(pbtns, "IMPORT",     self.import_playlist,   w=8).pack(side="left", padx=(4, 0))
        self._mkbtn(pbtns, "EXPORT",     self.export_playlist_m3u, w=8).pack(side="left", padx=(4, 0))
        self._mkbtn(pbtns, "REMOVE",     self.remove_selected,   w=8).pack(side="right")
        self._mkbtn(pbtns, "CLEAR",      self.clear_playlist,    w=6).pack(side="right", padx=(0, 4))

        self._mkbtn(pbtns, "▲", self.move_up,   w=3).pack(side="left", padx=(6, 0))
        self._mkbtn(pbtns, "▼", self.move_down, w=3).pack(side="left", padx=(2, 0))

        srow = tk.Frame(self._main_frame, bg=p["PANEL"])
        srow.pack(fill="x", side="bottom")
        self.status_left = tk.Label(srow, text="Ready — add files to begin.",
                                    font=FONT_UI, bg=p["PANEL"], fg=p["GRAY"], anchor="w")
        self.status_left.pack(side="left", fill="x", expand=True, padx=(8, 0), pady=4)
        self._ui_srow = srow

        # Remember the full layout so mini mode can restore it exactly.
        self._ui_pack_order = []
        for w in self._main_frame.pack_slaves():
            info = dict(w.pack_info())
            info.pop("in", None)
            self._ui_pack_order.append((w, info))

    # --- Mini mode -------------------------------------------------------
    def _wordmark_hit(self, event):
        try:
            bb = self.hero.bbox("fg")
        except Exception:
            return False
        return bool(bb) and bb[0] - 4 <= event.x <= bb[2] + 4 and bb[1] - 4 <= event.y <= bb[3] + 4

    def _on_hero_click(self, event):
        if self._wordmark_hit(event):
            self.toggle_mini()

    def _on_hero_motion(self, event):
        try:
            self.hero.config(cursor="hand2" if self._wordmark_hit(event) else "")
        except Exception:
            _log_swallowed()

    def _fit_full_window(self):
        """Open the full player at the smallest size its layout needs
        (never below the classic 640x700 minimum)."""
        try:
            self.root.update_idletasks()
            w = max(640, self._main_frame.winfo_reqwidth())
            h = max(700, self._main_frame.winfo_reqheight())
            self._full_minsize = (w, h)
            self.root.minsize(w, h)
            self.root.geometry(f"{w}x{h}")
        except Exception:
            _log_swallowed()

    def toggle_mini(self):
        self._set_mini(not self._mini)

    def _set_mini(self, on, resize=True):
        """
        Mini mode keeps only title, time, transport buttons and the seek
        bar. Clicking the small XZN wordmark brings the full player back.
        """
        keep = {self._ui_mrow, self._ui_trow, self._ui_brow, self.seek}
        if on:
            if resize and not self._mini:
                self._full_geometry = self.root.geometry()
            for w, _ in self._ui_pack_order:
                if w not in keep:
                    w.pack_forget()
            self.mini_logo.pack(side="left", padx=(0, 8), before=self.state_lbl)
            self.mini_vol_scale.set(self.vol_scale.get())
            self._mini_vol_box.pack(side="right", padx=(0, 2))
            self._mini = True
            # Smallest size that still shows every control in full.
            self.root.update_idletasks()
            w = self._main_frame.winfo_reqwidth()
            h = self._main_frame.winfo_reqheight()
            self.root.minsize(w, h)
            if resize:
                self.root.geometry(f"{w}x{h}")
            self.root.resizable(True, False)
        else:
            self.mini_logo.pack_forget()
            self._mini_vol_box.pack_forget()
            for w, _ in self._ui_pack_order:
                w.pack_forget()
            for w, info in self._ui_pack_order:
                w.pack(**info)
            self._mini = False
            self.root.resizable(True, True)
            self.root.minsize(*self._full_minsize)
            if resize and self._full_geometry:
                self.root.geometry(self._full_geometry)
            self._hero_size = (0, 0)
            self.root.after(40, self._render_bg)

    # --- Playlist type-to-search -----------------------------------------
    def _pointer_over_playlist(self):
        try:
            x, y = self.root.winfo_pointerxy()
            w = self.root.winfo_containing(x, y)
        except Exception:
            return False
        return w is not None and w in (self.listbox, getattr(self, "pl_scale", None))

    def _shortcut(self, fn):
        """Wrap a letter shortcut: over the playlist the key starts a
        search instead of triggering the shortcut."""
        def handler(event):
            if not self._mini and self._pointer_over_playlist() and event.char:
                self._open_search(event.char)
                return "break"
            fn()
        return handler

    def _on_root_key(self, event):
        ch = event.char
        if not ch or len(ch) != 1 or not ch.isprintable() or ch == " ":
            return None
        if event.state & 0x4:      # Control held: leave it alone
            return None
        if self._mini or not self._pointer_over_playlist():
            return None
        self._open_search(ch)
        return "break"

    def _open_search(self, first_char=""):
        if not self.playlist:
            return
        p = self.palette
        if self._search_entry is None or not self._search_entry.winfo_exists():
            self._search_var = tk.StringVar()
            e = tk.Entry(self._ui_lrow, textvariable=self._search_var,
                         font=("Courier New", 10), bd=0, relief="flat",
                         bg=p["RAISED"], fg=p["WHITE"], insertbackground=p["ACCENT"],
                         highlightthickness=1, highlightbackground=p["ACCENT"],
                         highlightcolor=p["ACCENT"])
            # Without the toplevel tag, typed letters don't fire the
            # root shortcuts (m = mute, n = next …).
            e.bindtags((str(e), "Entry", "all"))
            e.bind("<KeyRelease>", self._on_search_key)
            e.bind("<Return>", self._search_play)
            e.bind("<KP_Enter>", self._search_play)
            e.bind("<Escape>", lambda ev: self._close_search())
            e.bind("<Down>", lambda ev: self._search_step(+1))
            e.bind("<Up>", lambda ev: self._search_step(-1))
            e.bind("<FocusOut>", lambda ev: self._close_search())
            self._search_entry = e
        e = self._search_entry
        e.place(relx=1.0, rely=1.0, anchor="se", x=-6, y=-6, width=240)
        e.lift()
        self._search_var.set(first_char)
        e.icursor("end")
        e.focus_set()
        self._search_update(start=0)

    def _search_matches(self):
        q = (self._search_var.get() if self._search_var else "").lower().split()
        if not q:
            return []
        out = []
        for i, path in enumerate(self.playlist):
            name = (self._display_names.get(path) or os.path.basename(path)).lower()
            if all(t in name for t in q):
                out.append(i)
        return out

    def _search_update(self, start=0, step=1):
        matches = self._search_matches()
        e = self._search_entry
        if not matches:
            e.config(fg=self.palette["ERR"])
        else:
            e.config(fg=self.palette["WHITE"])
            n = len(self.playlist)
            # First match at/after 'start' in the chosen direction.
            order = [(start + k * step) % n for k in range(n)]
            mset = set(matches)
            hit = next(i for i in order if i in mset)
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(hit)
            self.listbox.see(hit)
        self._restart_search_timer()

    def _on_search_key(self, event):
        if event.keysym in ("Return", "KP_Enter", "Escape", "Up", "Down"):
            return
        self._search_update(start=0)

    def _search_step(self, step):
        sel = self.listbox.curselection()
        cur = sel[0] if sel else 0
        self._search_update(start=cur + step, step=step)
        return "break"

    def _search_play(self, event=None):
        sel = self.listbox.curselection()
        self._close_search()
        if sel:
            self.play_index(sel[0])
        return "break"

    def _restart_search_timer(self):
        if self._search_timer is not None:
            try:
                self.root.after_cancel(self._search_timer)
            except Exception:
                _log_swallowed()
        self._search_timer = self.root.after(8000, self._close_search)

    def _close_search(self):
        if self._search_timer is not None:
            try:
                self.root.after_cancel(self._search_timer)
            except Exception:
                _log_swallowed()
            self._search_timer = None
        e = self._search_entry
        if e is not None and e.winfo_exists():
            had_focus = (self.root.focus_get() is e)
            e.place_forget()
            if had_focus:
                self.root.focus_set()

    def _on_list_mousewheel(self, event):
        try:
            delta = 0
            if getattr(event, "num", None) == 4:
                delta = -1
            elif getattr(event, "num", None) == 5:
                delta = 1
            else:
                delta = -1 if event.delta > 0 else 1
            self.listbox.yview_scroll(delta, "units")
        except Exception:
            _log_swallowed()
        return "break"

    # --- Playlist right-click (tag editor) ------------------------------
    def _on_playlist_right_click(self, event):
        """Show a small popup with 'Edit tags…' for the clicked entry."""
        try:
            index = self.listbox.nearest(event.y)
        except Exception:
            return

        if not (0 <= index < len(self.playlist)):
            return

        # Preserve an existing multi-selection if the user right-clicked
        # on an already-selected row (file-manager behaviour). Only
        # collapse the selection when clicking on an unselected row.
        try:
            current_sel = set(self.listbox.curselection())
        except Exception:
            current_sel = set()

        if index not in current_sel:
            try:
                self.listbox.selection_clear(0, "end")
                self.listbox.selection_set(index)
            except Exception:
                _log_swallowed()

        path = self.playlist[index]
        can_edit = is_taggable_audio(path) and _MUTAGEN_AVAILABLE

        # Build a small borderless popup (matches the app style)
        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        p = self.palette
        popup.configure(bg=p["ACCENT_LO"])
        try:
            popup.attributes("-topmost", True)
        except Exception:
            _log_swallowed()

        inner = tk.Frame(popup, bg=p["RAISED"], bd=0, highlightthickness=0)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        item = tk.Label(
            inner,
            text="Edit tags…",
            font=FONT_UI,
            bg=p["RAISED"],
            fg=p["WHITE"] if can_edit else p["GRAY"],
            anchor="w", padx=14, pady=6,
            cursor="hand2" if can_edit else "arrow",
        )
        item.pack(fill="x")

        if can_edit:
            item.bind("<Enter>", lambda e: item.config(bg=p["ACCENT"], fg=p["WHITE"]))
            item.bind("<Leave>", lambda e: item.config(bg=p["RAISED"], fg=p["WHITE"]))

            def _open_editor(_e=None, _p=path, _popup=popup):
                unregister_popup(self.root, _popup)
                try:
                    _popup.destroy()
                except Exception:
                    _log_swallowed()
                self.open_tag_editor(_p)

            item.bind("<Button-1>", _open_editor)
        else:
            # Explain why it's greyed out
            if not _MUTAGEN_AVAILABLE:
                item.config(text="Edit tags… (mutagen missing)")
            elif not is_taggable_audio(path):
                item.config(text="Edit tags… (format not supported)")

        x = event.x_root
        y = event.y_root
        popup.geometry(f"+{x}+{y}")

        def _close_popup(_e=None):
            unregister_popup(self.root, popup)
            try:
                popup.destroy()
            except Exception:
                _log_swallowed()

        popup.bind("<FocusOut>", _close_popup)
        popup.bind("<Escape>", _close_popup)
        register_popup(self.root, popup)
        _install_popup_click_handler(self.root)

        try:
            popup.focus_set()
        except Exception:
            _log_swallowed()

    def open_tag_editor(self, path):
        """Open the tag editor for the given file path."""
        try:
            if self._tag_win is not None and self._tag_win.win.winfo_exists():
                # Bring existing one to front and close it first
                try:
                    self._tag_win.win.destroy()
                except Exception:
                    _log_swallowed()
        except Exception:
            _log_swallowed()
        try:
            self._tag_win = TagEditorWindow(self.root, self, path)
        except Exception as e:
            print(f"[XZN] Tag editor failed: {e}")
            messagebox.showerror("Tag editor",
                                 f"Could not open tag editor:\n{e}")

    def refresh_playlist_entry_for(self, path):
        """
        After tags have been changed, re-read the file's meta and update
        the playlist line (title/artist) for the given path.
        """
        try:
            idx = self.playlist.index(path)
        except ValueError:
            return

        new_name = os.path.basename(path)
        try:
            tags = read_tags(path)
            t = tags.get("title", "")
            a = tags.get("artist", "")
            if t and a:
                new_name = f"{a} - {t}"
            elif t:
                new_name = t
        except Exception:
            _log_swallowed()

        self._display_names[path] = new_name
        try:
            self.listbox.delete(idx)
            self.listbox.insert(idx, self._format_pl_line(path))
            self.listbox.selection_set(idx)
        except Exception:
            _log_swallowed()

        # If this is the currently playing file, update the title text too
        if self.current_index == idx:
            try:
                if self.player.current_path == path:
                    self.player.title_text = new_name
            except Exception:
                _log_swallowed()

    # --- Playlist reorder -----------------------------------------------
    def move_up(self):
        self._move_block(-1)

    def move_down(self):
        self._move_block(+1)

    def _move_block(self, direction):
        """
        Move every selected playlist row as a contiguous block by one
        position. `direction` is -1 for up and +1 for down.

        Multi-row selection is supported (shift-range and ctrl/cmd-click
        toggle both work, thanks to selectmode="extended"). The block
        can only move if it isn't already touching the corresponding
        edge of the playlist.
        """
        try:
            sel = sorted(self.listbox.curselection())
        except Exception:
            return
        if not sel:
            return

        n = len(self.playlist)
        if n <= 1:
            return

        if direction < 0:
            # Moving up: iterate top→bottom so we don't clobber ourselves.
            if sel[0] == 0:
                return
            for i in sel:
                j = i - 1
                self.playlist[i], self.playlist[j] = \
                    self.playlist[j], self.playlist[i]
        else:
            # Moving down: iterate bottom→top.
            if sel[-1] == n - 1:
                return
            for i in reversed(sel):
                j = i + 1
                self.playlist[i], self.playlist[j] = \
                    self.playlist[j], self.playlist[i]

        # Keep current_index pointing at the same file
        if self.current_index in sel:
            self.current_index += direction

        # Rebuild the listbox, restoring the (shifted) selection.
        self.listbox.delete(0, "end")
        for p in self.playlist:
            self.listbox.insert("end", self._format_pl_line(p))

        new_sel = [i + direction for i in sel]
        self.listbox.selection_clear(0, "end")
        for i in new_sel:
            self.listbox.selection_set(i)
        # Keep the leading/trailing edge of the block in view.
        try:
            self.listbox.see(new_sel[0] if direction < 0 else new_sel[-1])
        except Exception:
            _log_swallowed()

        self._persist_settings()

    def _swap_items(self, i, j):
        """(Kept for potential external callers; move_up/down use _move_block.)"""
        if not (0 <= i < len(self.playlist) and 0 <= j < len(self.playlist)):
            return
        if i == j:
            return

        self.playlist[i], self.playlist[j] = self.playlist[j], self.playlist[i]

        self.listbox.delete(0, "end")
        for p in self.playlist:
            self.listbox.insert("end", self._format_pl_line(p))

        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(j)
        self.listbox.see(j)

        if self.current_index == i:
            self.current_index = j
        elif self.current_index == j:
            self.current_index = i

        self._persist_settings()

    # --- Background ------------------------------------------------------
    def _initial_bg(self):
        self._render_bg()
        self._draw_hero_overlay()

    def _on_hero_configure(self, event=None):
        w = event.width if event is not None else self.hero.winfo_width()
        h = event.height if event is not None else self.hero.winfo_height()
        if (w, h) == self._hero_size:
            return
        self._hero_size = (w, h)
        if self._hero_resize_job is not None:
            try:
                self.root.after_cancel(self._hero_resize_job)
            except Exception:
                _log_swallowed()
        self._hero_resize_job = self.root.after(60, self._render_bg)

    def _render_bg(self):
        """
        Render the hero background.

        For static images: display once, no animation.
        For animated GIF/WebP: pre-convert every frame to a PhotoImage
        and cycle through them with root.after(). The animation only
        runs while a track is actually playing; when the render happens
        while paused/stopped, the frames are loaded and the first frame
        is shown, but no timer is scheduled.
        """
        self._hero_resize_job = None

        # Always stop the previous animation before starting fresh.
        self._stop_bg_animation()

        try:
            w = max(self.hero.winfo_width(), 40)
            h = max(self.hero.winfo_height(), 40)
            frames, durations, is_animated = load_background_frames(
                w, h, self.palette, self._bg_path
            )

            if is_animated and len(frames) > 1:
                # --- Animated path ------------------------------------
                self._bg_frames_photo = [
                    ImageTk.PhotoImage(f) for f in frames
                ]
                self._bg_frame_durations = durations
                self._bg_frame_index = 0
                self._bg_photo = self._bg_frames_photo[0]

                self.hero.delete("bg")
                self.hero.create_image(
                    0, 0, image=self._bg_photo, anchor="nw", tags="bg"
                )
                self.hero.tag_lower("bg")
                self._draw_hero_overlay()

                # Sync the paused flag with the player's current state
                # so that resizing or changing the background while a
                # track is playing keeps the animation running, and
                # while paused/stopped keeps it frozen.
                try:
                    is_playing = (self.player.state == "playing")
                except Exception:
                    is_playing = False
                self._bg_anim_paused = not is_playing

                if not self._bg_anim_paused:
                    self._schedule_next_bg_frame()
            else:
                # --- Static path --------------------------------------
                img = frames[0]
                self._bg_photo = ImageTk.PhotoImage(img)
                self.hero.delete("bg")
                self.hero.create_image(
                    0, 0, image=self._bg_photo, anchor="nw", tags="bg"
                )
                self.hero.tag_lower("bg")
                self._draw_hero_overlay()
        except Exception as e:
            print(f"[XZN] _render_bg failed: {e}")

    def _schedule_next_bg_frame(self):
        """Arm the timer for the next background animation frame."""
        if self._bg_anim_paused:
            return
        if not self._bg_frames_photo or not self._bg_frame_durations:
            return
        try:
            if not self.root.winfo_exists():
                return
        except Exception:
            return
        try:
            delay = self._bg_frame_durations[self._bg_frame_index]
            if delay <= 0:
                delay = 100
            # Clamp to a sane range so a broken GIF can't freeze/hog.
            delay = max(20, min(1000, int(delay)))
        except Exception:
            delay = 100
        try:
            self._bg_anim_job = self.root.after(delay, self._bg_anim_tick)
        except Exception:
            self._bg_anim_job = None

    def _bg_anim_tick(self):
        """Show the next frame of the animated background."""
        self._bg_anim_job = None
        if self._bg_anim_paused:
            return
        if not self._bg_frames_photo:
            return
        try:
            self._bg_frame_index = (
                self._bg_frame_index + 1
            ) % len(self._bg_frames_photo)
            self._bg_photo = self._bg_frames_photo[self._bg_frame_index]
            try:
                self.hero.itemconfigure("bg", image=self._bg_photo)
            except Exception:
                # Fallback: recreate the image item entirely.
                self.hero.delete("bg")
                self.hero.create_image(
                    0, 0, image=self._bg_photo, anchor="nw", tags="bg"
                )
                self.hero.tag_lower("bg")
        except Exception as e:
            print(f"[XZN] BG anim tick failed: {e}")
        finally:
            self._schedule_next_bg_frame()

    def _stop_bg_animation(self):
        """Cancel the animation timer and drop PhotoImage references.

        NOTE: this does NOT touch self._bg_anim_paused. That flag is
        driven by the player's state and represents the *intent*
        ("should the animation run right now?"), while this method only
        clears the *mechanics* (pending timer, cached frames).
        """
        if self._bg_anim_job is not None:
            try:
                self.root.after_cancel(self._bg_anim_job)
            except Exception:
                _log_swallowed()
            self._bg_anim_job = None
        self._bg_frames_photo = None
        self._bg_frame_durations = None
        self._bg_frame_index = 0

    def _pause_bg_animation(self):
        """Freeze the animation on its current frame."""
        self._bg_anim_paused = True
        if self._bg_anim_job is not None:
            try:
                self.root.after_cancel(self._bg_anim_job)
            except Exception:
                _log_swallowed()
            self._bg_anim_job = None

    def _resume_bg_animation(self):
        """Start (or continue) the animation if frames are loaded."""
        if not self._bg_frames_photo:
            # Not animated, or frames not loaded yet. The next call to
            # _render_bg() will sync with the player state anyway.
            self._bg_anim_paused = False
            return
        self._bg_anim_paused = False
        if self._bg_anim_job is None:
            self._schedule_next_bg_frame()

    def _draw_hero_overlay(self):
        c = self.hero
        try:
            if not c.winfo_exists():
                return
        except Exception:
            return
        c.delete("fg")
        self._draw_wordmark(c, 20, 22, ("Helvetica", 22, "bold"), "fg")

    def _draw_wordmark(self, c, x, y, font, tags):
        """The XZN wordmark: white with a dark outline, or dark with a
        light outline on light themes. Used by the hero and mini mode."""
        is_light_theme = luminance(self.palette["BG"]) > 0.5
        shadow = "#000000" if not is_light_theme else "#ffffff"
        main = "#ffffff" if not is_light_theme else "#1a1240"
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-2, 0), (2, 0), (0, -2), (0, 2)):
            c.create_text(x + dx, y + dy, text="XZN", font=font, fill=shadow, anchor="nw", tags=tags)
        c.create_text(x, y, text="XZN", font=font, fill=main, anchor="nw", tags=tags)

    # --- BG popup --------------------------------------------------------
    def _bg_menu(self):
        """
        Toggle the BG popup. If one is already open, close it.
        Otherwise open a fresh one.
        """
        if self._bg_popup is not None:
            try:
                self._bg_popup.close()
            except Exception:
                _log_swallowed()
            self._bg_popup = None
            return
        try:
            x = self.btn_bg.winfo_rootx()
            y = self.btn_bg.winfo_rooty() + self.btn_bg.winfo_height()
        except Exception:
            x = self.root.winfo_rootx() + 100
            y = self.root.winfo_rooty() + 100
        self._bg_popup = BgPopup(self, x, y)

    def _close_bg_popup(self):
        """
        Force-close the current BG popup if any.
        Clear the app-side reference *first* so nothing can leave a
        stale pointer behind, even if popup.close() raises.
        """
        popup = getattr(self, "_bg_popup", None)
        self._bg_popup = None
        if popup is not None:
            try:
                popup.close()
            except Exception:
                _log_swallowed()

    def remove_bg(self):
        self._stop_bg_animation()
        had_bg = bool(self._bg_path)
        self._bg_path = None
        self._bg_photo = None
        try:
            self.hero.delete("all")
        except Exception:
            _log_swallowed()
        self._render_bg()
        self._persist_settings()
        msg = "Background removed." if had_bg else "No background set."
        self.status_left.config(text=msg, fg=self.palette["GRAY"])

    def choose_bg(self):
        p = filedialog.askopenfilename(
            title="Choose background image (static or animated GIF)",
            filetypes=[
                ("Images / animations",
                 "*.png *.jpg *.jpeg *.bmp *.gif *.webp"),
                ("All files", "*.*"),
            ])
        if p:
            self._bg_path = p
            self._render_bg()
            self._persist_settings()
            self.status_left.config(
                text=f"Background: {os.path.basename(p)}",
                fg=self.palette["GRAY"])

    def _on_alpha_cmd(self, v):
        self._apply_alpha_to_windows()
        self._persist_settings()

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

    def _close_shortcuts(self, event=None):
        if self._help_win is not None and self._help_win.is_alive():
            self._help_win.close()
            self._help_win = None
            return "break"
        return None

    def toggle_shortcuts(self, event=None):
        """F1: open the key reference, or close it if it is open."""
        if self._help_win is not None and self._help_win.is_alive():
            self._help_win.close()
            self._help_win = None
        else:
            self._help_win = ShortcutsWindow(self.root, self)
            self._apply_alpha_to_windows()
        return "break"

    def show_theme(self):
        self._toggle_window("_theme_win", lambda: ThemeWindow(self.root, self).win)

    def show_cd(self):
        """CD button: open the CD window, or close it if it is open."""
        w = self._cd_win
        try:
            if w is not None and w.win.winfo_exists():
                if w.win.state() == "withdrawn":       # hidden while ripping
                    w.win.deiconify()
                    w.win.lift()
                else:
                    w.close()
                return
        except Exception:
            _log_swallowed()
        self._cd_win = CdWindow(self.root, self)
        self._apply_alpha_to_windows()

    def play_cd_tracks(self, items):
        """items: [(path, display_name, seconds, is_start)] from the CD
        window. Adds the CD tracks to the playlist and starts playing."""
        start_path = None
        for path, name, secs, is_start in items:
            self._display_names[path] = name
            if secs > 0:
                self._track_durations[path] = secs
            if path not in self.playlist:
                self.playlist.append(path)
                self.listbox.insert("end", self._format_pl_line(path))
            else:
                self._refresh_line_for(path)
            if is_start:
                start_path = path
        self._update_pl_count()
        if start_path is not None:
            self.play_index(self.playlist.index(start_path))

    def stop_cd_playback(self, drive):
        """Stop playback if it is reading from the given CD drive."""
        cur = self.player.current_path or ""
        if self.player.state != "idle" and (cur.startswith(f"{CDDA_PREFIX}{drive.id}/")
                                             or cur.startswith(drive.id + os.sep)):
            self.stop_play()

    def show_equalizer(self):
        """EQ button / E key: open the equalizer, or close it if open."""
        try:
            if self._eq_win is not None and self._eq_win.win.winfo_exists():
                self._eq_win.win.destroy()
                self._eq_win = None
                return
        except Exception:
            _log_swallowed()
        self._eq_win = EqualizerWindow(self.root, self)
        self._apply_alpha_to_windows()

    # --- Playlist --------------------------------------------------------
    def _update_pl_count(self):
        n = len(self.playlist)
        self.pl_count.config(text=f"{n} item{'s' if n != 1 else ''}")

    def _refresh_selection(self):
        self.listbox.selection_clear(0, "end")
        if 0 <= self.current_index < len(self.playlist):
            self.listbox.selection_set(self.current_index)
            self.listbox.see(self.current_index)

    def add_files_dialog(self):
        exts = " ".join(f"*{e}" for e in sorted(MEDIA_EXTS))
        pl_exts = " ".join(f"*{e}" for e in sorted(PLAYLIST_EXTS))
        paths = filedialog.askopenfilenames(
            title="Add media files or playlists",
            filetypes=[
                ("Media files", exts),
                ("Playlists (M3U)", pl_exts),
                ("All files", "*.*"),
            ])
        if not paths:
            return

        media = []
        playlists = []
        for p in paths:
            if is_playlist_file(p):
                playlists.append(p)
            elif is_media_file(p):
                media.append(p)

        if media:
            self.add_paths(media)

        total_from_pl = 0
        missing_total = 0
        for pl in playlists:
            entries, missing = read_m3u(pl)
            if entries:
                self.add_paths(entries)
                total_from_pl += len(entries)
            missing_total += missing

        if playlists and total_from_pl == 0 and missing_total == 0:
            self.status_left.config(
                text="Playlist(s) contained no usable entries.",
                fg=self.palette["GRAY"])
        elif playlists:
            msg = f"Imported {total_from_pl} track(s) from {len(playlists)} playlist(s)."
            if missing_total:
                msg += f" {missing_total} path(s) missing."
            self.status_left.config(text=msg, fg=self.palette["GRAY"])

    def add_folder_dialog(self):
        d = filedialog.askdirectory(title="Add folder (scans for media)")
        if not d:
            return
        found = []
        for root, _dirs, files in os.walk(d):
            for f in sorted(files):
                p = os.path.join(root, f)
                if is_media_file(p):
                    found.append(p)
        self.add_paths(found)

    def add_paths(self, paths):
        expanded = []
        for p in paths:
            ap = os.path.abspath(p)
            if is_playlist_file(ap):
                entries, _missing = read_m3u(ap)
                expanded.extend(entries)
            else:
                expanded.append(ap)

        added = 0
        for p in expanded:
            if not is_media_file(p):
                continue
            if not os.path.isfile(p):
                continue
            if p not in self.playlist:
                self.playlist.append(p)
                self.listbox.insert("end", self._format_pl_line(p))
                added += 1
        self._update_pl_count()
        if added:
            self._persist_settings()
            # Kick off a background probe so the durations appear
            # shortly after the rows do.
            self._probe_durations_async(self.playlist)
        if added and self.current_index < 0:
            self.play_index(0)

    def play_index(self, i, start_pos=0.0):
        if not (0 <= i < len(self.playlist)):
            return
        self.current_index = i
        self._resume_pos = 0.0
        path = self.playlist[i]
        self._refresh_selection()
        self.player.open_file(path, start_pos)
        self.status_left.config(text=f"Opening {os.path.basename(path)} …", fg=self.palette["GRAY"])

    def _play_selected(self):
        sel = self.listbox.curselection()
        if sel:
            self.play_index(sel[0])

    def remove_selected(self):
        sel = self.listbox.curselection()
        if not sel:
            return

        # Did we remove the file that's currently playing?
        playing_removed = self.current_index in sel

        for i in sorted(sel, reverse=True):
            del self.playlist[i]
            self.listbox.delete(i)
            if i < self.current_index:
                self.current_index -= 1
            elif i == self.current_index:
                self.current_index = -1

        # If the playing track was among the removed rows, stop playback
        # so the player isn't left in a weird "playing but not in the
        # playlist" state.
        if playing_removed:
            try:
                self.player.stop()
            except Exception:
                _log_swallowed()

        self._update_pl_count()
        self._persist_settings()

    def clear_playlist(self):
        self.playlist.clear()
        self.listbox.delete(0, "end")
        self.current_index = -1
        self.player.stop()
        self._update_pl_count()
        self._persist_settings()

    # --- Playlist import / export ---------------------------------------
    def export_playlist_m3u(self):
        if not self.playlist:
            self.status_left.config(text="Nothing to export — playlist is empty.",
                                    fg=self.palette["GRAY"])
            return
        p = filedialog.asksaveasfilename(
            title="Export playlist as M3U",
            defaultextension=".m3u",
            filetypes=[("M3U playlist", "*.m3u"), ("All files", "*.*")])
        if not p:
            return
        try:
            write_m3u(p, self.playlist)
            self.status_left.config(
                text=f"Exported {len(self.playlist)} track(s) → {os.path.basename(p)}",
                fg=self.palette["GRAY"])
        except Exception as e:
            print(f"[XZN] M3U export failed: {e}")
            self.status_left.config(text=f"Export failed: {e}", fg=self.palette["ERR"])

    def import_playlist(self):
        pl_exts = " ".join(f"*{e}" for e in sorted(PLAYLIST_EXTS))
        paths = filedialog.askopenfilenames(
            title="Import playlist(s)",
            filetypes=[
                ("Playlists (M3U / M3U8)", pl_exts),
                ("All files", "*.*"),
            ])
        if not paths:
            return

        total_added = 0
        total_missing = 0
        for pl in paths:
            entries, missing = read_m3u(pl)
            total_missing += missing
            if entries:
                before = len(self.playlist)
                self.add_paths(entries)
                total_added += len(self.playlist) - before

        if total_added == 0 and total_missing == 0:
            self.status_left.config(
                text="Import: no usable tracks found in selected playlist(s).",
                fg=self.palette["GRAY"])
        else:
            msg = f"Imported {total_added} track(s) from {len(paths)} playlist(s)."
            if total_missing:
                msg += f" {total_missing} path(s) missing."
            self.status_left.config(text=msg, fg=self.palette["GRAY"])

    # --- Playback control ------------------------------------------------
    def toggle_play(self):
        p = self.player
        if p.state == "playing":
            p.pause()
        elif p.state == "paused":
            p.resume()
        elif 0 <= self.current_index < len(self.playlist):
            self.play_index(self.current_index, self._resume_pos)

    def stop_play(self):
        self.player.stop()
        self.state_lbl.config(text="■")
        self._set_icon(self.btn_play, "play")
        self.cur_lbl.config(text="00:00")
        self.seek.set(0)
        try:
            if self._video_win is not None and self._video_win.is_alive():
                self._video_win.hide()
        except Exception:
            _log_swallowed()

    # --- Gapless / resume --------------------------------------------------
    def _provide_next(self):
        """
        Called from the player thread shortly before the current track
        ends. Returns the path of the track to append without a gap, or
        None to end playback normally. Only reads app state; the actual
        switch is reported back through the "advanced" event.
        """
        try:
            if not self.playlist:
                return None
            if self.repeat_mode == 1 and 0 <= self.current_index < len(self.playlist):
                i = self.current_index
            else:
                i = self._next_index()
            if i is None or not (0 <= i < len(self.playlist)):
                self._queued_next = None
                return None
            self._queued_next = i
            return self.playlist[i]
        except Exception as e:
            print(f"[XZN] _provide_next failed: {e}")
            return None

    def _on_advanced(self, path):
        """The player moved on to the next track without a gap."""
        i = self._queued_next
        if not (i is not None and 0 <= i < len(self.playlist) and self.playlist[i] == path):
            i = self.playlist.index(path) if path in self.playlist else -1
        self._queued_next = None
        self.current_index = i
        self._refresh_selection()

    def _resume_state(self):
        """What to save so the next start can continue here."""
        p = self.player
        if p.state in ("playing", "paused") and p.current_path:
            return {"path": p.current_path, "pos": round(float(p.position()), 1)}
        if 0 <= self.current_index < len(self.playlist):
            return {"path": self.playlist[self.current_index],
                    "pos": round(float(self._resume_pos), 1)}
        return None

    def _restore_resume_point(self):
        """Select last session's track; ▶ continues at the saved position."""
        res = self._saved_resume
        if not res or res.get("path") not in self.playlist:
            return
        i = self.playlist.index(res["path"])
        pos = float(res.get("pos") or 0.0)
        dur = self._track_durations.get(res["path"]) or 0.0
        if pos < 3.0 or (dur and pos > dur - 3.0):
            pos = 0.0
        self.current_index = i
        self._resume_pos = pos
        self._refresh_selection()
        name = self._display_names.get(res["path"]) or os.path.basename(res["path"])
        self.player.title_text = name
        if dur:
            self.dur_lbl.config(text=fmt_time(dur))
        if pos > 0:
            self.status_left.config(
                text=f"Press ▶ to continue \"{name}\" at {fmt_time(pos)}.",
                fg=self.palette["GRAY"])

    def _next_index(self):
        n = len(self.playlist)
        if n == 0:
            return None
        if self.current_index < 0:
            return 0
        if self.shuffle and n > 1:
            while True:
                j = random.randrange(n)
                if j != self.current_index:
                    return j
        j = self.current_index + 1
        if j >= n:
            return 0 if self.repeat_mode == 2 else None
        return j

    def _prev_index(self):
        n = len(self.playlist)
        if n == 0 or self.current_index < 0:
            return None
        if self.shuffle and n > 1:
            while True:
                j = random.randrange(n)
                if j != self.current_index:
                    return j
        j = self.current_index - 1
        if j < 0:
            return (n - 1) if self.repeat_mode == 2 else None
        return j

    def next_track(self):
        i = self._next_index()
        if i is not None:
            self.play_index(i)

    def prev_track(self):
        p = self.player
        if p.position() > 4 and p.current_path:
            p.seek(0)
            return
        i = self._prev_index()
        if i is not None:
            self.play_index(i)

    def seek_by(self, dt):
        p = self.player
        if p.state in ("playing", "paused") and p.duration > 0:
            p.seek(p.position() + dt)

    def nudge_volume(self, dv):
        # Clamp before calling set_volume so the player never receives
        # an out-of-range float (matches the value the slider shows).
        v = max(0, min(100, int(self.vol_scale.get()) + dv))
        self.vol_scale.set(v)
        self.player.set_volume(v / 100.0)

    def toggle_mute(self):
        m = not self.player._mute
        self.player.set_mute(m)
        self._set_icon(self.btn_mute, "mute" if m else "volume", m)

    def toggle_shuffle(self):
        self.shuffle = not self.shuffle
        self._set_icon(self.btn_shuf, "shuffle", self.shuffle)

    def cycle_repeat(self):
        self.repeat_mode = (self.repeat_mode + 1) % 3
        label = {0: "REP:OFF", 1: "REP:ONE", 2: "REP:ALL"}[self.repeat_mode]
        on = self.repeat_mode != 0
        self.btn_rep.config(text=label, fg=self.palette["ACCENT"] if on else self.palette["WHITE"],
                            relief="sunken" if on else "raised")

    def _toggle_video_window(self):
        try:
            if self._video_win is None or not self._video_win.is_alive():
                self._video_win = VideoWindow(self.root, self)
                self._video_win.apply_palette(self.palette)
            if self._video_win.win.state() == "normal":
                self._video_user_hidden = True
                self._video_win.hide()
            else:
                self._video_user_hidden = False
                self._video_win.show()
                # Make sure we show the idle card when there's no video
                # to draw yet (e.g. audio-only track).
                try:
                    if not self.player.has_video:
                        self._video_win.show_placeholder(
                            "", self.player.title_text or ""
                        )
                except Exception:
                    _log_swallowed()
            self._apply_alpha_to_windows()
        except Exception as e:
            print(f"[XZN] _toggle_video_window failed: {e}")

    def _toggle_video_fullscreen(self):
        try:
            if self._video_win is None or not self._video_win.is_alive():
                self._video_win = VideoWindow(self.root, self)
                self._video_win.apply_palette(self.palette)
            self._video_user_hidden = False
            self._video_win.show()
            self._video_win.toggle_fullscreen()
            self._apply_alpha_to_windows()
        except Exception as e:
            print(f"[XZN] _toggle_video_fullscreen failed: {e}")

    def _on_seek_cmd(self, v):
        # Also show the scrubbed time while a mouse-wheel seek burst is
        # in progress (not just during a drag).
        if self._seek_dragging or self._seek_wheel_active:
            dur = self.player.duration or 0.0
            self.cur_lbl.config(text=fmt_time(dur * float(v) / 1000.0))

    def _on_seek_release(self, e=None):
        self._seek_dragging = False
        p = self.player
        if p.state in ("playing", "paused") and p.duration > 0:
            p.seek(p.duration * float(self.seek.get()) / 1000.0)

    # --- Mouse-wheel seek on the timeline --------------------------------
    def _on_seek_wheel(self, event):
        """
        Mouse-wheel scrolling on the seek slider jumps 5 seconds per
        notch. A short timer (`_seek_wheel_timer`) keeps
        `_seek_wheel_active` true during rapid bursts, so `_tick_mid`
        doesn't immediately overwrite the slider with the real playback
        position between wheel events.
        """
        p = self.player
        if p.state not in ("playing", "paused") or p.duration <= 0:
            return "break"

        # Linux uses Button-4/5, Windows/macOS use event.delta
        if getattr(event, "num", None) == 4:
            delta = +1
        elif getattr(event, "num", None) == 5:
            delta = -1
        else:
            delta = +1 if getattr(event, "delta", 0) > 0 else -1

        # The seek Scale runs 0..1000 (permille of duration).
        # Convert our 5-second step into permille for the current track.
        step_permille = 5000.0 / p.duration

        try:
            current = float(self.seek.get())
        except Exception:
            return "break"

        new_val = max(0.0, min(1000.0, current + delta * step_permille))
        if new_val == current:
            return "break"

        self._seek_wheel_active = True
        try:
            # Scale.set() fires _on_seek_cmd (updates the time label).
            self.seek.set(new_val)
        except Exception:
            self._seek_wheel_active = False
            return "break"

        p.seek(p.duration * new_val / 1000.0)

        # Debounce: keep _seek_wheel_active true for a short moment so
        # rapid wheel events accumulate without _tick_mid fighting back.
        if self._seek_wheel_timer is not None:
            try:
                self.root.after_cancel(self._seek_wheel_timer)
            except Exception:
                _log_swallowed()
        self._seek_wheel_timer = self.root.after(250, self._reset_seek_wheel_state)

        return "break"

    def _reset_seek_wheel_state(self):
        self._seek_wheel_active = False
        self._seek_wheel_timer = None

    def _on_vol_cmd(self, v):
        self.player.set_volume(float(v) / 100.0)
        # Keep the big and the mini volume slider showing the same value.
        try:
            iv = int(round(float(v)))
            osd_vol = None
            vw = getattr(self, "_video_win", None)
            if vw is not None and getattr(vw, "_osd", None) is not None:
                osd_vol = vw._osd_vol
            for sc in (getattr(self, "vol_scale", None), getattr(self, "mini_vol_scale", None),
                       osd_vol):
                if sc is not None and int(round(float(sc.get()))) != iv:
                    sc.set(iv)
        except Exception:
            _log_swallowed()

    def _on_bal_cmd(self, v):
        self.player.set_balance((float(v) - 50.0) / 50.0)

    # --- Event loop ------------------------------------------------------
    def _poll_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "meta":
                    self._on_meta(payload)
                elif kind == "state":
                    self._on_state(payload)
                elif kind == "ended":
                    self._on_ended()
                elif kind == "advanced":
                    self._on_advanced(payload)
                elif kind == "mediakey":
                    self._on_media_key(payload)
                elif kind == "error":
                    self.status_left.config(text=f"Error: {payload}", fg=self.palette["ERR"])
        except queue.Empty:
            pass

    def _marquee_text(self):
        base = self.player.title_text or "XZN — ready"
        if 0 <= self.current_index < len(self.playlist) and len(self.playlist) > 1:
            base = f"[{self.current_index + 1}/{len(self.playlist)}] {base}"
        return base

    def _on_meta(self, m):
        p = self.palette
        dur = m.get("duration") or 0.0
        cur_path = self.player.current_path
        self._media_update(
            title=m.get("title") or (self._display_names.get(cur_path)
                                     or os.path.basename(cur_path or "")),
            artist=m.get("artist") or "", length=dur, new_track=True)
        self.dur_lbl.config(text=fmt_time(dur))
        title = m.get("title")
        artist = m.get("artist")

        # Update the playlist row: display name from tags, plus the
        # real duration from the container metadata (which is more
        # precise than our background probe).
        if 0 <= self.current_index < len(self.playlist):
            path = self.playlist[self.current_index]
            updated = False
            if title and artist:
                self.player.title_text = f"{artist} — {title}"
                self._display_names[path] = f"{artist} - {title}"
                updated = True
            elif title:
                self.player.title_text = title
                self._display_names[path] = title
                updated = True
            if dur and dur > 0:
                self._track_durations[path] = dur
                updated = True
            if updated:
                try:
                    self.listbox.delete(self.current_index)
                    self.listbox.insert(self.current_index, self._format_pl_line(path))
                    self.listbox.selection_set(self.current_index)
                except Exception:
                    _log_swallowed()

        parts = [m.get("format") or "media"]
        v, a = m.get("video"), m.get("audio")
        if v:
            fps = f" @ {v[3]:g}fps" if v[3] else ""
            parts.append(f"{v[0]} {v[1]}x{v[2]}{fps}")
        if a:
            ch = "stereo" if a[2] == 2 else f"{a[2]}ch"
            parts.append(f"{a[0]} {a[1]/1000:.1f}kHz {ch}")
        self.status_left.config(text=" · ".join(parts), fg=p["GRAY"])

        if v is not None and self._video_win is not None:
            if not self._video_win.is_alive():
                self._video_win = VideoWindow(self.root, self)
                self._video_win.apply_palette(self.palette)
            try:
                minimized = self._video_win.win.state() == "iconic"
            except Exception:
                minimized = False
            if self._video_user_hidden or minimized:
                # The user put the video away: keep listening without it.
                return
            try:
                self._video_win.fit_to_video(v[1], v[2])
            except Exception as e:
                print(f"[XZN] fit_to_video failed: {e}")
            self._video_win.show()
            # Show the idle card with the track title while we wait for
            # the first real video frame (or forever, for audio-only).
            try:
                self._video_win.show_placeholder(
                    "", self.player.title_text or ""
                )
            except Exception:
                _log_swallowed()
            self._apply_alpha_to_windows()

    def _on_state(self, s):
        self._media_update(status={"playing": "Playing", "paused": "Paused"}.get(s, "Stopped"))
        glyph = {"playing": "▶", "paused": "II"}.get(s, "■")
        self.state_lbl.config(text=glyph)
        self._set_icon(self.btn_play, "pause" if s == "playing" else "play")

        # Tie the animated background to the playback state:
        #   playing   → animate
        #   paused    → freeze on current frame
        #   idle/stop → freeze on current frame
        try:
            if s == "playing":
                self._resume_bg_animation()
            else:
                self._pause_bg_animation()
        except Exception as e:
            print(f"[XZN] BG state sync failed: {e}")

        if s == "idle":
            self.cur_lbl.config(text="00:00")
            self.seek.set(0)
            try:
                # A minimized video window stays minimized (instead of being
                # withdrawn and popping up again with the next video).
                if self._video_win is not None and self._video_win.is_alive() \
                        and self._video_win.win.state() != "iconic":
                    self._video_win.hide()
            except Exception:
                _log_swallowed()

    def _on_ended(self):
        if self.repeat_mode == 1 and self.current_index >= 0:
            self.play_index(self.current_index)
            return
        i = self._next_index()
        if i is not None:
            self.play_index(i)
        else:
            self.status_left.config(text="End of playlist.", fg=self.palette["GRAY"])

    # --- Media keys ------------------------------------------------------
    def _start_media_keys(self):
        try:
            if IS_WINDOWS and ctypes is not None:
                self._media_keys = WindowsMediaKeys(self.events)
            elif sys.platform.startswith("linux") and MprisMediaKeys.available():
                self._media_keys = MprisMediaKeys(self.events, self.player.position)
            else:
                print("[XZN] Media keys not available on this system")
                return
            self._media_keys.start()
        except Exception as e:
            print(f"[XZN] Media keys setup failed: {e}")
            self._media_keys = None

    def _media_update(self, **kw):
        if self._media_keys is not None:
            try:
                self._media_keys.update(**kw)
            except Exception:
                _log_swallowed()

    def _on_media_key(self, action):
        state = self.player.state
        if action == "playpause":
            self.toggle_play()
        elif action == "play":
            if state != "playing":
                self.toggle_play()
        elif action == "pause":
            if state == "playing":
                self.player.pause()
        elif action == "stop":
            self.stop_play()
        elif action == "next":
            self.next_track()
        elif action == "prev":
            self.prev_track()

    def _tick_fast(self):
        try:
            self._update_video()
            self._update_analyzer()
        except Exception as e:
            print(f"[XZN] tick_fast error: {e}")
            traceback.print_exc()
        self.root.after(16, self._tick_fast)

    def _tick_mid(self):
        try:
            s = self._marquee_text() + "      "
            n = max(len(s), 1)
            self._marquee_offset = (self._marquee_offset + 1) % n
            out = "".join(s[(self._marquee_offset + i) % n] for i in range(46))
            self.marquee.config(text=out.rstrip())
            p = self.player
            pos = p.position()
            dur = p.duration or 0.0
            if p.state == "idle" and self._resume_pos > 0 \
                    and 0 <= self.current_index < len(self.playlist):
                # Not started yet: show where ▶ will continue.
                pos = self._resume_pos
                dur = self._track_durations.get(self.playlist[self.current_index]) or 0.0
            # Don't fight the slider while the user is dragging it or
            # while a mouse-wheel seek burst is still active.
            if not self._seek_dragging and not self._seek_wheel_active:
                self.cur_lbl.config(text=fmt_time(pos))
                self.seek.set(int(1000 * (pos / dur)) if dur > 0 else 0)
            self._poll_events()
            vw = self._video_win
            if vw is not None and vw._osd_visible:
                vw.refresh_osd()
        except Exception as e:
            print(f"[XZN] tick_mid error: {e}")
            traceback.print_exc()
        self.root.after(100, self._tick_mid)

    def _update_video(self):
        p = self.player
        if p.state not in ("playing", "paused") or not p.has_video:
            return
        visible = False
        try:
            visible = (self._video_win is not None and self._video_win.is_alive()
                       and self._video_win.win.state() == "normal")
        except Exception:
            visible = False

        now = p.position()
        if not visible:
            # Window closed, minimized or still opening: keep the frame
            # buffer flowing (drop what is due) instead of discarding
            # everything. The upcoming frames stay ready, so the picture
            # appears the moment the window is (re)opened, and the
            # decoder never stalls the audio.
            p.vbuf.pop_due(now)
            return

        buf_size = p.vbuf.size()
        item = p.vbuf.pop_due(now + 0.050) if buf_size >= 3 else p.vbuf.pop_due(now)

        if item is None:
            return
        ft, img = item
        try:
            self._frame_counter += 1
            self._video_win.update_frame(img, self._frame_counter)
        except Exception as e:
            print(f"[XZN] _update_video error: {e}")

    def _update_analyzer(self):
        p = self.player
        with p.spec_lock:
            buf = p.spec_buf.copy()
            pos = p.spec_pos
        n = len(buf)
        m = min(4096, n)
        start = (pos - m) % n
        if start + m <= n:
            seg = buf[start:start + m]
        else:
            seg = np.concatenate([buf[start:], buf[:(start + m) % n]])
        c = self.anal
        pal = self.palette
        h = np.zeros(ANALYZER_BARS, dtype=np.float32)
        if p.state == "playing" and len(seg):
            try:
                spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
                freqs = np.fft.rfftfreq(len(seg), 1 / SAMPLE_RATE)
                overall = float(spec.max()) + 1e-9
                self._auto_gain = max(overall, self._auto_gain * 0.95)
                edges = np.geomspace(40, 16000, ANALYZER_BARS + 1)
                for i in range(ANALYZER_BARS):
                    band = spec[(freqs >= edges[i]) & (freqs < edges[i + 1])]
                    v = float(band.max()) if len(band) else 0.0
                    h[i] = min(1.0, (v / self._auto_gain) ** 0.8)
            except Exception:
                _log_swallowed()
        c.delete("all")
        try:
            w = max(c.winfo_width(), 10)
        except Exception:
            w = VIDEO_W
        hh = 44
        bw = max(1, w // ANALYZER_BARS)
        for i in range(ANALYZER_BARS):
            hpx = max(1, int(h[i] * (hh - 4)))
            x0 = i * bw + 1
            c.create_rectangle(x0, hh - 2 - hpx, x0 + bw - 3, hh - 2,
                               fill=pal["ACCENT"] if h[i] > 0.5 else pal["ACCENT_LO"], width=0)

    # --- Shutdown --------------------------------------------------------
    def on_close(self):
        try:
            if self._cd_win is not None and self._cd_win.ripping:
                if not messagebox.askyesno("Ripping in progress",
                                           "A CD is being ripped. Quit anyway?\n"
                                           "The current track will be incomplete."):
                    return
                self._cd_win._cancel.set()
        except Exception:
            _log_swallowed()
        if self._media_keys is not None:
            try:
                self._media_keys.stop()
            except Exception:
                _log_swallowed()
        # Stop the background animation first — its after() callbacks
        # must not fire against a destroyed root.
        try:
            self._stop_bg_animation()
        except Exception:
            _log_swallowed()
        try:
            self._close_bg_popup()
        except Exception:
            _log_swallowed()
        try:
            self._persist_settings()
        except Exception as e:
            print(f"[XZN] on_close persist failed: {e}")
        try:
            self.player.cmds.put(("quit",))
            if self.player.stream is not None:
                self.player.stream.stop()
                self.player.stream.close()
        except Exception:
            _log_swallowed()
        try:
            if self._video_win is not None and self._video_win.is_alive():
                self._video_win.win.destroy()
        except Exception:
            _log_swallowed()
        try:
            if self._eq_win is not None and self._eq_win.win.winfo_exists():
                self._eq_win.win.destroy()
        except Exception:
            _log_swallowed()
        try:
            if self._tag_win is not None and self._tag_win.win.winfo_exists():
                self._tag_win.win.destroy()
        except Exception:
            _log_swallowed()
        self.root.destroy()

    def run(self):
        self._tick_fast()
        self._tick_mid()
        self.root.mainloop()


def main():
    setup_logging(_get_app_data_dir())
    print(f"[XZN] Starting {APP_NAME} {VERSION}")
    if LOG_PATH:
        print(f"[XZN] Log file: {LOG_PATH}")
    print(f"[XZN] Python {sys.version}")
    print(f"[XZN] av={av.__version__}  numpy={np.__version__}")
    print(f"[XZN] Settings: {SETTINGS_PATH}")
    icon = get_icon_path()
    if icon:
        print(f"[XZN] Icon: {icon}")
    else:
        print(f"[XZN] Icon: NOT FOUND "
              f"({ICON_FILENAME if IS_WINDOWS else LINUX_ICON_FILENAME} missing)")
    app = App()
    argv_files = [a for a in sys.argv[1:] if os.path.isfile(a)]
    if argv_files:
        def _add_cli():
            app.add_paths(argv_files)
        app.root.after(260, _add_cli)
    app.run()


if __name__ == "__main__":
    main()

