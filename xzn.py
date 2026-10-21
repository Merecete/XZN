#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Xenon-Zones (XZN) v1.0.2
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
import shutil
import threading
import traceback
from collections import deque
import numpy as np
import av
import sounddevice as sd
from PIL import Image, ImageTk, ImageDraw, ImageFilter, ImageSequence
import tkinter as tk
from tkinter import filedialog, messagebox

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


APP_NAME = "Xenon-Zones"
VERSION = "1.0.2"
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
        pass

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
        }
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"[XZN] Settings saved → {SETTINGS_PATH}")
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

    def process(self, block):
        with self.lock:
            if not self.enabled:
                return
            pre = 10.0 ** (self.preamp / 20.0)
            if pre != 1.0:
                block *= pre
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

    def open_file(self, path):
        self.current_path = path
        self.title_text = os.path.basename(path)
        self._gen += 1
        self._want_pause = False
        self._want_resume = False
        self._want_stop = False
        self._pending_seek = None
        self.cmds.put(("open", path, self._gen))

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
                    self._play_file(cmd[1], cmd[2])
                except Exception as e:
                    print(f"[XZN] Player thread crashed: {e}")
                    traceback.print_exc()
                    self.state = "idle"
                    self.events.put(("state", "idle"))
                    self.events.put(("error", str(e)))
            elif cmd[0] == "quit":
                break

    def _pick_streams(self, cont):
        vstreams, astreams = [], []
        for s in cont.streams:
            try:
                if s.type == "video" and s.codec_context is not None:
                    if (s.width or 0) * (s.height or 0) > 64 * 64:
                        vstreams.append(s)
                elif s.type == "audio":
                    astreams.append(s)
            except Exception:
                pass
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
                    pass
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
                pass
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

    def _play_file(self, path, gen):
        print(f"[XZN] Opening: {path}")
        cont = None
        resampler = None
        try:
            cont = av.open(path)
        except Exception as e:
            print(f"[XZN] OPEN FAILED: {e}")
            traceback.print_exc()
            self.events.put(("error", f"Could not open file:\n{e}"))
            return

        try:
            self.eq.reset_state()
            self._enable_hw_threads(cont)
            vstream, astream = self._pick_streams(cont)
            if vstream is None and astream is None:
                cont.close()
                self.events.put(("error", "No playable audio or video streams found."))
                return

            meta = self._collect_meta(cont, vstream, astream)
            self.duration = meta["duration"]
            self.state = "playing"
            self._paused_pos = 0.0
            self._head_t = 0.0
            self._video_pts = 0.0
            self._skip_until = 0.0
            self._t0_media = 0.0
            self._t0_mono = time.monotonic()
            self._clock_running = False
            self.ring.clear()
            self.vbuf.clear()

            with self._clock_lock:
                self._audio_start_frame = None
                self._audio_start_media = 0.0

            self.has_video = (vstream is not None)
            self.video_size_native = (vstream.width, vstream.height) if vstream is not None else (0, 0)
            self.events.put(("meta", meta))
            self.events.put(("state", "playing"))

            time_offset = self._detect_pts_offset(cont, vstream, astream)

            if astream is not None:
                try:
                    resampler = av.AudioResampler(
                        format="fltp",
                        layout="stereo",
                        rate=SAMPLE_RATE
                    )
                except Exception as e:
                    print(f"[XZN] Resampler create failed: {e}")
                    resampler = None

            demux_iter = cont.demux()
            packet_count = 0
            has_video = vstream is not None
            has_audio = astream is not None
            prebuffer_done = False

            PREBUFFER  = int(SAMPLE_RATE * 0.5)
            HIGH_WATER = int(SAMPLE_RATE * 1.5)

            while not self._want_stop and self._gen == gen:

                if self._pending_seek is not None:
                    target = self._pending_seek
                    self._pending_seek = None
                    try:
                        cont.seek(int((target + time_offset) * av.time_base),
                                  any_frame=False, backward=True)
                        if astream is not None:
                            try:
                                resampler = av.AudioResampler(
                                    format="fltp", layout="stereo",
                                    rate=SAMPLE_RATE)
                            except Exception:
                                resampler = None
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
                        demux_iter = cont.demux()
                        print(f"[XZN] Seek → {target:.2f}s")
                    except Exception as e:
                        print(f"[XZN] Seek failed: {e}")
                        traceback.print_exc()
                    continue

                if self._want_pause and self.state == "playing":
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
                    self.events.put(("state", "playing"))
                    continue

                if self.state == "paused":
                    time.sleep(0.02)
                    continue

                if has_video and self.vbuf.size() > 60:
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
                    packet = next(demux_iter)
                except StopIteration:
                    break
                except Exception as e:
                    print(f"[XZN] demux error: {e}")
                    break

                packet_count += 1

                try:
                    if vstream is not None and packet.stream.index == vstream.index:
                        for vf in packet.decode():
                            self._on_video(vf, vstream, time_offset)
                    elif astream is not None and packet.stream.index == astream.index:
                        for af in packet.decode():
                            self._on_audio(af, astream, resampler, time_offset)
                except Exception as e:
                    print(f"[XZN] packet decode error: {e}")

            print(f"[XZN] Playback loop ended after {packet_count} packets")

        except Exception as e:
            print(f"[XZN] FATAL: {e}")
            traceback.print_exc()
            self.events.put(("error", str(e)))
        finally:
            if resampler is not None:
                try:
                    for rf in resampler.resample(None):
                        self._write_audio_frame(rf)
                except Exception as e:
                    print(f"[XZN] resampler flush failed: {e}")

            if not self._want_stop:
                try:
                    self._drain_ring(timeout=8.0)
                except Exception as e:
                    print(f"[XZN] drain failed: {e}")

            if cont is not None:
                try:
                    cont.close()
                except Exception:
                    pass

            self.ring.clear()
            self.vbuf.clear()
            user_stopped = self._want_stop
            self.state = "idle"
            self._clock_running = False
            with self._clock_lock:
                self._audio_start_frame = None
            self.events.put(("state", "idle"))
            if not user_stopped and self._gen == gen:
                self.events.put(("ended", path))

    def _on_video(self, vf, vstream, time_offset):
        if self.state != "playing":
            return
        fps = 25.0
        try:
            r = vstream.average_rate or vstream.base_rate
            if r and float(r) > 1:
                fps = float(r)
        except Exception:
            pass

        tb = vstream.time_base
        if vf.pts is not None and tb is not None:
            t = float(vf.pts * tb) - time_offset
        else:
            t = self._video_pts + 1.0 / fps
        if t < -0.5 or t < self._skip_until - 0.4:
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
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        self.win.withdraw()

        apply_window_icon(self.win)

        # macOS: share the app's menubar with this Toplevel so the
        # default Tk menu (Python/File/Edit/Window/Help) does not appear.
        if app is not None:
            try:
                app._attach_macos_menu(self.win)
            except Exception:
                pass

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
            pass

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
            pass

    def apply_palette(self, palette):
        try:
            if not self.is_alive():
                return
            self.win.configure(bg=palette["SUNKEN"])
            self.canvas.configure(bg=palette["SUNKEN"])
        except Exception:
            pass

    def show(self):
        try:
            if not self.is_alive():
                return
            try:
                self.win.attributes("-alpha", 1.0)
            except Exception:
                pass

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
                pass
        except Exception as e:
            print(f"[XZN] VideoWindow.show failed: {e}")

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
            pass
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
            pass
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
                pass
            self.win.attributes("-fullscreen", True)
            self._fullscreen = True
        except Exception:
            pass

    def exit_fullscreen(self):
        if not self._fullscreen:
            return
        try:
            self.win.attributes("-fullscreen", False)
            self.win.geometry(self._normal_geometry)
            try:
                self.win.attributes("-alpha", 1.0)
            except Exception:
                pass
        except Exception:
            pass
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
                pass

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
            pass

    # --- Close handler that also unregisters the window -----------------
    def _close():
        if app is not None:
            try:
                if win in app._extra_alpha_windows:
                    app._extra_alpha_windows.remove(win)
            except Exception:
                pass
        try:
            win.destroy()
        except Exception:
            pass

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
        pass

    # Register with the app so it participates in the alpha system,
    # then apply the current alpha immediately.
    if app is not None:
        try:
            app._extra_alpha_windows.append(win)
        except Exception:
            pass
        try:
            win.after(30, app._apply_alpha_to_windows)
        except Exception:
            pass

    try:
        win.focus_set()
    except Exception:
        pass


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
            pass

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

        # --- Close handler that also unregisters ------------------------
        def _close_info():
            try:
                if self.win in self.app._extra_alpha_windows:
                    self.app._extra_alpha_windows.remove(self.win)
            except Exception:
                pass
            try:
                self.win.destroy()
            except Exception:
                pass

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
            pass


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
            pass

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
            pass
        self.app.apply_accent(color, persist=True, rebuild=True)

    def _reset(self):
        try:
            self.win.destroy()
        except Exception:
            pass
        self.app.apply_accent(DEFAULT_PALETTE["ACCENT"], persist=True, rebuild=True)


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
                pass

    def _update_text(self):
        try:
            self.btn.config(text=self.var.get())
        except Exception:
            pass

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
            pass

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
            pass

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
            pass
        if self.on_change is not None:
            try:
                self.on_change(value)
            except Exception:
                pass
        self._close()

    def _close(self):
        if self._popup is None:
            return
        try:
            self.btn.unbind_all("<Button-1>")
        except Exception:
            pass
        try:
            self._popup.destroy()
        except Exception:
            pass
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
            pass

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
            pass

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
            pass

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
                    pass
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
            pass

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
                pass
            self._outside_bind_id = None
        # Unregister from the global registry (safety net)
        try:
            unregister_popup(self.app.root, self.win)
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass
        if getattr(self.app, "_bg_popup", None) is self:
            self.app._bg_popup = None


# ------------------------------------------------------------------------
class App:
    # Width of the title column in the playlist rows. Durations are
    # right-aligned to a fixed column so they line up vertically.
    PL_TITLE_COLS = 46

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
        self.root.minsize(640, 700)
        self.root.geometry("760x780")

        settings = load_settings()
        self.accent = settings["accent"]
        self._initial_alpha = settings["alpha"]
        self._bg_path = settings["background"]
        self._saved_playlist = settings["playlist"]
        self._eq_gains = list(settings.get("eq_gains") or [0.0] * len(EQ_BANDS))
        self._eq_enabled = bool(settings.get("eq_enabled", True))
        self._eq_preamp = float(settings.get("eq_preamp") or 0.0)
        self._sample_offered = bool(settings.get("sample_offered", False))
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
        self._eq_win = None
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

        self._build_ui()
        self._video_win = VideoWindow(self.root, self)
        self._video_win.apply_palette(self.palette)
        self._setup_dnd()

        try:
            self.root.attributes("-alpha", self._initial_alpha / 100.0)
        except Exception:
            pass
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

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<space>", lambda e: self.toggle_play())
        self.root.bind("<Left>",  lambda e: self.seek_by(-5))
        self.root.bind("<Right>", lambda e: self.seek_by(5))
        self.root.bind("<Up>",    lambda e: self.nudge_volume(+5))
        self.root.bind("<Down>",  lambda e: self.nudge_volume(-5))
        for k in ("m", "M"):
            self.root.bind(k, lambda e: self.toggle_mute())
        for k in ("n", "N"):
            self.root.bind(k, lambda e: self.next_track())
        for k in ("p", "P"):
            self.root.bind(k, lambda e: self.prev_track())
        for k in ("f", "F"):
            self.root.bind(k, lambda e: self._toggle_video_fullscreen())
        for k in ("c", "C"):
            self.root.bind(k, lambda e: self.show_theme())
        for k in ("e", "E"):
            self.root.bind(k, lambda e: self.show_equalizer())

        self.root.after(80, self._initial_bg)

    # --- Playlist row formatting ----------------------------------------
    def _format_pl_line(self, path):
        """
        Format a playlist entry as "name<pad>  dur" with the duration
        right-aligned in a fixed column. Uses the custom display name
        when one is registered (e.g. the intro track), otherwise the
        file's basename.
        """
        name = self._display_names.get(path) or os.path.basename(path)
        dur = self._track_durations.get(path)
        dur_str = fmt_time(dur) if dur and dur > 0 else "--:--"
        # Right-align so tracks longer than an hour still line up
        # (fmt_time yields 5 or 7 chars).
        dur_str = f"{dur_str:>7}"
        if len(name) > self.PL_TITLE_COLS:
            name = name[:self.PL_TITLE_COLS - 1] + "…"
        pad = " " * max(0, self.PL_TITLE_COLS - len(name))
        return f"{name}{pad}  {dur_str}"

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
            pass

    def _probe_durations_async(self, paths):
        """
        Probe durations for any new paths in the background and fill
        in the durations in the playlist as they become available.

        Runs in a worker thread. UI updates go through `after(0, …)`
        because tkinter is not thread-safe.
        """
        pending = [p for p in paths if p not in self._track_durations]
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
                                pass
                except Exception as e:
                    print(f"[XZN] Duration probe failed for {p}: {e}")
            if new_any:
                # Persist the new durations so subsequent launches
                # don't have to re-probe. Runs on the main thread.
                try:
                    self.root.after(0, self._persist_settings)
                except Exception:
                    pass

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
            self.root.createcommand(
                "tk::mac::standardAboutPanel", self._show_mac_about
            )
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
            "track_durations": dict(getattr(self, "_track_durations", {}) or {}),
        })

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
            pass

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
                pass

        _collect(self.root)

        # Also include explicitly registered extras (belt & suspenders)
        for w in list(getattr(self, "_extra_alpha_windows", [])):
            try:
                if w.winfo_exists() and w not in all_toplevels:
                    all_toplevels.append(w)
            except Exception:
                pass

        for w in all_toplevels:
            if video_win is not None and w is video_win:
                continue
            if bg_popup_win is not None and w is bg_popup_win:
                continue
            try:
                if w.winfo_exists():
                    w.attributes("-alpha", a)
            except Exception:
                pass

        # Video window always fully opaque
        if video_win is not None:
            try:
                video_win.attributes("-alpha", 1.0)
            except Exception:
                pass

        if bg_popup_win is not None:
            try:
                bg_popup_win.attributes("-alpha", 1.0)
            except Exception:
                pass

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
                pass
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
            pass

        if self.shuffle:
            self.btn_shuf.config(fg=self.palette["ACCENT"], relief="sunken")
        if self.repeat_mode != 0:
            label = {1: "REP:ONE", 2: "REP:ALL"}[self.repeat_mode]
            self.btn_rep.config(text=label, fg=self.palette["ACCENT"], relief="sunken")

        if state_keep["status_text"]:
            self.status_left.config(text=state_keep["status_text"], fg=self.palette["GRAY"])

        if self._video_win is not None and self._video_win.is_alive():
            self._video_win.apply_palette(self.palette)

        self._hero_size = (0, 0)
        self.root.after(40, self._render_bg)
        self.root.after(80, self._apply_alpha_to_windows)

    def _mkbtn(self, parent, text, cmd, w=4, bg=None):
        p = self.palette
        bg_color = bg if bg else p["RAISED"]
        fg_color = p["WHITE"]
        hover_bg = lighten(bg_color, 0.15)

        if IS_MAC or not IS_WINDOWS:
            lbl = tk.Label(parent, text=text, bg=bg_color, fg=fg_color,
                           font=FONT_BTN, padx=8, pady=3,
                           relief="raised", bd=1, cursor="hand2")
            lbl.bind("<Button-1>", lambda e: cmd())
            lbl.bind("<Enter>", lambda e: lbl.config(bg=hover_bg))
            lbl.bind("<Leave>", lambda e: lbl.config(bg=bg_color))
            return lbl
        else:
            return tk.Button(parent, text=text, width=w, command=cmd,
                             bg=bg_color, fg=fg_color,
                             activebackground=hover_bg,
                             activeforeground=fg_color,
                             relief="raised", bd=1, font=FONT_BTN, takefocus=0)

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
            pass

    def _sync_pl_scrollbar(self, *args):
        try:
            first, last = self.listbox.yview()
            if last - first >= 1.0:
                self.pl_scale.set(0)
            else:
                self.pl_scale.set(first)
        except Exception:
            pass

    def _build_ui(self):
        p = self.palette
        self.root.configure(bg=p["BG"])

        self._main_frame = tk.Frame(self.root, bg=p["BG"])
        self._main_frame.pack(fill="both", expand=True)

        hdr = tk.Frame(self._main_frame, bg=p["PANEL"], height=44)
        hdr.pack(fill="x", padx=8, pady=(8, 4))
        hdr.pack_propagate(False)

        self._mkbtn(hdr, "INFO", self.show_info, w=6).pack(side="left", padx=(10, 0))
        self._mkbtn(hdr, "C", self.show_theme, w=3).pack(side="left", padx=(4, 0))

        self.btn_bg = self._mkbtn(hdr, "BG", self._bg_menu, w=4)
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

        mrow = tk.Frame(self._main_frame, bg=p["PANEL"])
        mrow.pack(fill="x", padx=8, pady=(0, 3))
        self.state_lbl = tk.Label(mrow, text="■", font=("Courier New", 12, "bold"),
                                  bg=p["SUNKEN"], fg=p["ACCENT"], width=3)
        self.state_lbl.pack(side="left", padx=(0, 6), ipady=2)
        self.marquee = tk.Label(mrow, text="", font=FONT_LCD, bg=p["SUNKEN"],
                                fg=p["CYAN"], anchor="w")
        self.marquee.pack(side="left", fill="x", expand=True, ipady=3)

        trow = tk.Frame(self._main_frame, bg=p["PANEL"])
        trow.pack(fill="x", padx=8, pady=(0, 2))
        self.cur_lbl = tk.Label(trow, text="00:00", font=FONT_LG, bg=p["SUNKEN"], fg=p["ACCENT"])
        self.cur_lbl.pack(side="left")
        tk.Label(trow, text="/", font=FONT_LG, bg=p["PANEL"], fg=p["GRAY"]).pack(side="left")
        self.dur_lbl = tk.Label(trow, text="--:--", font=FONT_LG, bg=p["SUNKEN"], fg=p["ACCENT"])
        self.dur_lbl.pack(side="left")

        brow = tk.Frame(self._main_frame, bg=p["PANEL"])
        brow.pack(fill="x", padx=8, pady=(0, 2))
        self.btn_prev = self._mkbtn(brow, "|◀", self.prev_track)
        self.btn_prev.pack(side="left")
        self.btn_play = self._mkbtn(brow, "▶", self.toggle_play, w=5)
        self.btn_play.pack(side="left", padx=(2, 0))
        self.btn_stop = self._mkbtn(brow, "■", self.stop_play)
        self.btn_stop.pack(side="left", padx=(2, 0))
        self.btn_next = self._mkbtn(brow, "▶|", self.next_track)
        self.btn_next.pack(side="left", padx=(2, 0))
        self.btn_shuf = self._mkbtn(brow, "SHUF", self.toggle_shuffle, w=6)
        self.btn_shuf.pack(side="right")
        self.btn_rep = self._mkbtn(brow, "REP:OFF", self.cycle_repeat, w=9)
        self.btn_rep.pack(side="right", padx=(0, 2))
        self._mkbtn(brow, "VIDEO", self._toggle_video_window, w=6).pack(side="right", padx=(0, 6))

        self.seek = tk.Scale(self._main_frame, from_=0, to=1000, orient="horizontal",
                             length=VIDEO_W, command=self._on_seek_cmd, bg=p["PANEL"],
                             fg=p["ACCENT"], troughcolor=p["SUNKEN"], highlightthickness=0,
                             sliderrelief="flat", showvalue=False)
        self.seek.pack(fill="x", padx=8, pady=(2, 2))
        self.seek.bind("<Button-1>", lambda e: setattr(self, "_seek_dragging", True))
        self.seek.bind("<ButtonRelease-1>", self._on_seek_release)
        # Mouse-wheel scrolling on the timeline (5 s per notch)
        self.seek.bind("<MouseWheel>", self._on_seek_wheel)
        self.seek.bind("<Button-4>", self._on_seek_wheel)
        self.seek.bind("<Button-5>", self._on_seek_wheel)

        vrow = tk.Frame(self._main_frame, bg=p["PANEL"])
        vrow.pack(fill="x", padx=8, pady=(0, 4))
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

        self.btn_eq = self._mkbtn(vrow, "EQ", self.show_equalizer, w=4)
        self.btn_eq.pack(side="right", padx=(0, 4))
        self.btn_mute = self._mkbtn(vrow, "MUTE", self.toggle_mute, w=6)
        self.btn_mute.pack(side="right")

        self.anal = tk.Canvas(self._main_frame, height=44, bg=p["SUNKEN"], highlightthickness=0)
        self.anal.pack(fill="x", padx=8, pady=(0, 4))

        plf = tk.Frame(self._main_frame, bg=p["PANEL"])
        plf.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        top = tk.Frame(plf, bg=p["PANEL"])
        top.pack(fill="x")
        tk.Label(top, text="PLAYLIST", font=FONT_UI_B, bg=p["PANEL"], fg=p["GRAY"]).pack(side="left")
        self.pl_count = tk.Label(top, text="", font=FONT_UI, bg=p["PANEL"], fg=p["ACCENT_LO"])
        self.pl_count.pack(side="right")

        lrow = tk.Frame(plf, bg=p["SUNKEN"])
        lrow.pack(fill="both", expand=True, pady=(2, 4))
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
            pass
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
                pass

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
            pass

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
                    pass
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
                pass

        popup.bind("<FocusOut>", _close_popup)
        popup.bind("<Escape>", _close_popup)
        register_popup(self.root, popup)
        _install_popup_click_handler(self.root)

        try:
            popup.focus_set()
        except Exception:
            pass

    def open_tag_editor(self, path):
        """Open the tag editor for the given file path."""
        try:
            if self._tag_win is not None and self._tag_win.win.winfo_exists():
                # Bring existing one to front and close it first
                try:
                    self._tag_win.win.destroy()
                except Exception:
                    pass
        except Exception:
            pass
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
            pass

        self._display_names[path] = new_name
        try:
            self.listbox.delete(idx)
            self.listbox.insert(idx, self._format_pl_line(path))
            self.listbox.selection_set(idx)
        except Exception:
            pass

        # If this is the currently playing file, update the title text too
        if self.current_index == idx:
            try:
                if self.player.current_path == path:
                    self.player.title_text = new_name
            except Exception:
                pass

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
            pass

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
                pass
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
                pass
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
                pass
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
        p = self.palette
        try:
            w = max(c.winfo_width(), 10)
            h = max(c.winfo_height(), 10)
        except Exception:
            return
        c.delete("fg")

        is_light_theme = luminance(p["BG"]) > 0.5
        text = "XZN"
        font = ("Helvetica", 22, "bold")
        shadow = "#000000" if not is_light_theme else "#ffffff"
        main = "#ffffff" if not is_light_theme else "#1a1240"

        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-2, 0), (2, 0), (0, -2), (0, 2)):
            c.create_text(20 + dx, 22 + dy, text=text, font=font, fill=shadow, anchor="nw", tags="fg")
        c.create_text(20, 22, text=text, font=font, fill=main, anchor="nw", tags="fg")

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
                pass
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
                pass

    def remove_bg(self):
        self._stop_bg_animation()
        had_bg = bool(self._bg_path)
        self._bg_path = None
        self._bg_photo = None
        try:
            self.hero.delete("all")
        except Exception:
            pass
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

    def show_info(self):
        InfoWindow(self.root, self.palette, self)
        self._apply_alpha_to_windows()

    def show_theme(self):
        ThemeWindow(self.root, self)
        self._apply_alpha_to_windows()

    def show_equalizer(self):
        try:
            if self._eq_win is not None and self._eq_win.win.winfo_exists():
                self._eq_win.win.deiconify()
                self._eq_win.win.lift()
                self._apply_alpha_to_windows()
                return
        except Exception:
            pass
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

    def play_index(self, i):
        if not (0 <= i < len(self.playlist)):
            return
        self.current_index = i
        path = self.playlist[i]
        self._refresh_selection()
        self.player.open_file(path)
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
                pass

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
            self.play_index(self.current_index)

    def stop_play(self):
        self.player.stop()
        self.state_lbl.config(text="■")
        self.btn_play.config(text="▶")
        self.cur_lbl.config(text="00:00")
        self.seek.set(0)
        try:
            if self._video_win is not None and self._video_win.is_alive():
                self._video_win.hide()
        except Exception:
            pass

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
        self.btn_mute.config(fg=self.palette["ACCENT"] if m else self.palette["WHITE"],
                             relief="sunken" if m else "raised")

    def toggle_shuffle(self):
        self.shuffle = not self.shuffle
        self.btn_shuf.config(fg=self.palette["ACCENT"] if self.shuffle else self.palette["WHITE"],
                             relief="sunken" if self.shuffle else "raised")

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
                self._video_win.hide()
            else:
                self._video_win.show()
                # Make sure we show the idle card when there's no video
                # to draw yet (e.g. audio-only track).
                try:
                    if not self.player.has_video:
                        self._video_win.show_placeholder(
                            "", self.player.title_text or ""
                        )
                except Exception:
                    pass
            self._apply_alpha_to_windows()
        except Exception as e:
            print(f"[XZN] _toggle_video_window failed: {e}")

    def _toggle_video_fullscreen(self):
        try:
            if self._video_win is None or not self._video_win.is_alive():
                self._video_win = VideoWindow(self.root, self)
                self._video_win.apply_palette(self.palette)
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
                pass
        self._seek_wheel_timer = self.root.after(250, self._reset_seek_wheel_state)

        return "break"

    def _reset_seek_wheel_state(self):
        self._seek_wheel_active = False
        self._seek_wheel_timer = None

    def _on_vol_cmd(self, v):
        self.player.set_volume(float(v) / 100.0)

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
                    pass

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
                pass
            self._apply_alpha_to_windows()

    def _on_state(self, s):
        glyph = {"playing": "▶", "paused": "II"}.get(s, "■")
        self.state_lbl.config(text=glyph)
        self.btn_play.config(text="||" if s == "playing" else "▶")

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
                if self._video_win is not None and self._video_win.is_alive():
                    self._video_win.hide()
            except Exception:
                pass

    def _on_ended(self):
        if self.repeat_mode == 1 and self.current_index >= 0:
            self.play_index(self.current_index)
            return
        i = self._next_index()
        if i is not None:
            self.play_index(i)
        else:
            self.status_left.config(text="End of playlist.", fg=self.palette["GRAY"])

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
            # Don't fight the slider while the user is dragging it or
            # while a mouse-wheel seek burst is still active.
            if not self._seek_dragging and not self._seek_wheel_active:
                self.cur_lbl.config(text=fmt_time(pos))
                self.seek.set(int(1000 * (pos / dur)) if dur > 0 else 0)
            self._poll_events()
        except Exception as e:
            print(f"[XZN] tick_mid error: {e}")
            traceback.print_exc()
        self.root.after(100, self._tick_mid)

    def _update_video(self):
        p = self.player
        if self._video_win is None or not self._video_win.is_alive():
            return
        try:
            if self._video_win.win.state() != "normal":
                return
        except Exception:
            return
        if p.state not in ("playing", "paused") or not p.has_video:
            return

        now = p.position()
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
                pass
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
        # Stop the background animation first — its after() callbacks
        # must not fire against a destroyed root.
        try:
            self._stop_bg_animation()
        except Exception:
            pass
        try:
            self._close_bg_popup()
        except Exception:
            pass
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
            pass
        try:
            if self._video_win is not None and self._video_win.is_alive():
                self._video_win.win.destroy()
        except Exception:
            pass
        try:
            if self._eq_win is not None and self._eq_win.win.winfo_exists():
                self._eq_win.win.destroy()
        except Exception:
            pass
        try:
            if self._tag_win is not None and self._tag_win.win.winfo_exists():
                self._tag_win.win.destroy()
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        self._tick_fast()
        self._tick_mid()
        self.root.mainloop()


def main():
    print(f"[XZN] Starting {APP_NAME} {VERSION}")
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
