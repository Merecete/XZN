#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Xenon Zones (XZN) v4.6
=======================
- Transparency (Alpha) applies to main, info, theme and EQ windows
- Video window always stays fully opaque (even in fullscreen)
- Drag & Drop for the playlist (via tkinterdnd2, optional)
- Playlist scrollbar styled like VOL/BAL (as tk.Scale)
- Full 10-band equalizer (Winamp style) as its own window
- Gradient background & glow effects in the original look
- Robust video window control & playlist persistence
- Fix: tracks are no longer truncated at the end (ring drain)
- UI fully localized in English
- Custom borderless BG popup menu (no native white frame)
- Extended Info window with tagline, credits and copyright
- Auto-save playlist on every change (crash-safe)
- Export playlist as .m3u / import .m3u and .m3u8
- v4.6: macOS-compatible buttons (Label-based on darwin)
- v4.6: Playlist reordering via ▲ / ▼ buttons (rebuilds listbox)
- v4.6: All windows (EQ, Info, Theme) now use macOS-safe buttons

Copyright (c) 2026 Merecete

Released under the MIT License. See LICENSE file for details.
"""
import os
import sys
import json
import time
import queue
import random
import threading
import traceback
from collections import deque
import numpy as np
import av
import sounddevice as sd
from PIL import Image, ImageTk, ImageDraw, ImageFilter
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


APP_NAME = "Xenon Zones"
VERSION = "Alpha 1.0"
SAMPLE_RATE = 44100
VIDEO_W, VIDEO_H = 624, 351
ANALYZER_BARS = 32

SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".xzn_settings.json")

# --- Icon constants -----------------------------------------------------
ICON_FILENAME = "xzn.ico"
TASKBAR_APP_ID = "com.merecete.xzn.mediaplayer.1"

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
    p = resource_path(ICON_FILENAME)
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
    icon_path = get_icon_path()
    if not icon_path:
        return
    try:
        window.iconbitmap(icon_path)
    except Exception as e:
        print(f"[XZN] iconbitmap failed: {e}")
        try:
            img = tk.PhotoImage(file=icon_path)
            window.tk.call("wm", "iconphoto", window._w, img)
            window._icon_photo_ref = img
        except Exception as e2:
            print(f"[XZN] iconphoto fallback failed: {e2}")


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


def load_background(w, h, palette, custom_path=None):
    candidates = []
    if custom_path:
        candidates.append(custom_path)
    for name in ("bg.png", "bg.jpg", "bg.jpeg", "background.png", "background.jpg"):
        candidates.append(os.path.join(SCRIPT_DIR, name))

    for p in candidates:
        if p and os.path.isfile(p):
            try:
                img = Image.open(p).convert("RGB")
                iw, ih = img.size
                scale = max(w / iw, h / ih)
                nw, nh = int(iw * scale), int(ih * scale)
                img = img.resize((nw, nh), Image.Resampling.LANCZOS)
                left = (nw - w) // 2
                top_ = (nh - h) // 2
                img = img.crop((left, top_, left + w, top_ + h))
                return img
            except Exception as e:
                print(f"[XZN] Failed to load BG ({p}): {e}")

    return _make_gradient_bg(w, h, palette)


def make_flat_button(parent, text, cmd, palette, width=None,
                     bg=None, fg=None, font=None, padx=14, pady=4):
    """
    Cross-platform button that respects bg/fg on macOS too.

    On macOS, tk.Button ignores bg/fg (Aqua theme), so we use a
    tk.Label with click bindings instead. On Windows/Linux this
    returns a normal tk.Button.
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
    def __init__(self, master):
        self.master = master
        self.win = tk.Toplevel(master)
        self.win.title("Video – XZN")
        self.win.configure(bg="#0a0812")
        self.win.geometry("960x540")
        self.win.minsize(240, 160)
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        self.win.withdraw()

        apply_window_icon(self.win)

        self.canvas = tk.Canvas(self.win, bg="#0a0812", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self._photo = None
        self._last_gen = -1
        self._fullscreen = False
        self._normal_geometry = "960x540"
        self._last_canvas_size = (0, 0)
        self._scaled_cache = None

        self.canvas.bind("<Double-Button-1>", self.toggle_fullscreen)
        self.win.bind("<Escape>", lambda e: self.exit_fullscreen())

    def is_alive(self):
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False

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
            self.win.deiconify()
            self.win.lift()
        except Exception as e:
            print(f"[XZN] VideoWindow.show failed: {e}")

    def hide(self):
        try:
            if self.is_alive():
                self.win.withdraw()
        except Exception:
            pass
        self._photo = None
        self._last_gen = -1
        self._scaled_cache = None

    def fit_to_video(self, native_w, native_h):
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

    def show_placeholder(self):
        try:
            if not self.is_alive():
                return
            self.canvas.delete("all")
            w = max(self.canvas.winfo_width(), 10)
            h = max(self.canvas.winfo_height(), 10)
            self.canvas.create_text(w // 2, h // 2, text="XZN",
                                    font=("Courier New", 40, "bold"), fill="#5a4590")
        except Exception:
            pass
        self._photo = None
        self._last_gen = -1
        self._scaled_cache = None

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

        try:
            self.canvas.delete("all")
            self.canvas.create_image(cw // 2, ch // 2, image=self._photo, anchor="center")
        except Exception as e:
            print(f"[XZN] Video canvas draw failed: {e}")


# ------------------------------------------------------------------------
class InfoWindow:
    def __init__(self, master, palette):
        self.master = master
        self.win = tk.Toplevel(master)
        self.win.title("Info – XZN")
        self.win.configure(bg=palette["PANEL"])
        self.win.resizable(False, False)
        self.win.transient(master)
        apply_window_icon(self.win)

        p = palette
        frame = tk.Frame(self.win, bg=p["PANEL"], padx=26, pady=22)
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text="XENON ZONES (XZN)",
                 font=("Helvetica", 15, "bold"),
                 bg=p["PANEL"], fg=p["ACCENT"]).pack(anchor="w")

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

        btn = make_flat_button(frame, "Close", self.win.destroy, palette=p)
        btn.pack(anchor="e")

        self.win.update_idletasks()
        mx = master.winfo_rootx() + master.winfo_width() // 2
        my = master.winfo_rooty() + master.winfo_height() // 2
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        self.win.geometry(f"+{mx - w // 2}+{my - h // 2}")
        self.win.bind("<Escape>", lambda e: self.win.destroy())


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
        self.pre_scale = tk.Scale(pre_box, from_=-12, to=12, orient="vertical",
                                  length=200, resolution=0.5, showvalue=True,
                                  bg=p["PANEL"], fg=p["ACCENT"],
                                  troughcolor=p["SUNKEN"],
                                  highlightthickness=0, sliderrelief="flat",
                                  font=("Courier New", 7),
                                  command=self._on_preamp)
        self.pre_scale.set(getattr(app, "_eq_preamp", 0.0))
        self.pre_scale.pack()

        self.band_scales = []
        for i, (freq, label) in enumerate(zip(EQ_BANDS, EQ_BAND_LABELS)):
            col = tk.Frame(body, bg=p["PANEL"])
            col.pack(side="left", padx=1)
            tk.Label(col, text=label, font=("Courier New", 8, "bold"),
                     bg=p["PANEL"], fg=p["CYAN"]).pack()
            sc = tk.Scale(col, from_=-12, to=12, orient="vertical",
                          length=180, resolution=0.5, showvalue=False,
                          bg=p["PANEL"], fg=p["ACCENT"],
                          troughcolor=p["SUNKEN"],
                          highlightthickness=0, sliderrelief="flat",
                          width=14,
                          command=lambda v, idx=i: self._on_band(idx, v))
            sc.set(getattr(app, "_eq_gains", [0.0]*len(EQ_BANDS))[i])
            sc.pack()
            self.band_scales.append(sc)

        preset_row = tk.Frame(self.win, bg=p["PANEL"])
        preset_row.pack(fill="x", padx=10, pady=(0, 6))
        tk.Label(preset_row, text="Preset:",
                 font=FONT_UI, bg=p["PANEL"], fg=p["GRAY"]).pack(side="left")

        self.preset_var = tk.StringVar(value="Flat")
        preset_menu = tk.OptionMenu(preset_row, self.preset_var, *EQ_PRESETS.keys(),
                                    command=self._apply_preset)
        preset_menu.config(bg=p["RAISED"], fg=p["WHITE"],
                           activebackground=p["ACCENT"],
                           activeforeground=p["WHITE"],
                           highlightthickness=0, bd=1, relief="raised",
                           font=FONT_UI_B, width=10)
        preset_menu["menu"].config(bg=p["RAISED"], fg=p["WHITE"],
                                   activebackground=p["ACCENT"],
                                   activeforeground=p["WHITE"],
                                   font=FONT_UI)
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

        self.item1 = tk.Label(inner, text="Choose image…",
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

        self.win.bind("<FocusOut>", lambda e: self.close())
        self.win.bind("<Escape>", lambda e: self.close())
        self._bind_id = app.root.bind_all("<Button-1>",
                                          self._on_global_click, add="+")
        try:
            self.win.focus_set()
        except Exception:
            pass

    def _on_global_click(self, event):
        if self._destroyed:
            return
        try:
            w = self.app.root.winfo_containing(event.x_root, event.y_root)
        except Exception:
            w = None
        p = w
        while p is not None:
            if p is self.win:
                return
            try:
                p = p.master
            except Exception:
                p = None
        self.close()

    def _choose(self):
        self.close()
        self.app.choose_bg()

    def _remove(self):
        self.close()
        self.app.remove_bg()

    def close(self):
        if self._destroyed:
            return
        self._destroyed = True
        bid = getattr(self, "_bind_id", None)
        if bid is not None:
            try:
                self.app.root.unbind_all("<Button-1>")
            except Exception:
                pass
            self._bind_id = None
        try:
            self.win.destroy()
        except Exception:
            pass
        if getattr(self.app, "_bg_popup", None) is self:
            self.app._bg_popup = None


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
        self.palette = build_palette(self.accent)
        print(f"[XZN] Accent color: {self.accent}")

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
        self._auto_gain = 1.0
        self._bg_photo = None
        self._hero_size = (0, 0)
        self._hero_resize_job = None
        self._video_win = None
        self._eq_win = None
        self._main_frame = None
        self._frame_counter = 0
        self._dnd_widgets = []
        self._bg_popup = None

        self._build_ui()
        self._video_win = VideoWindow(self.root)
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
                    self.listbox.insert("end", os.path.basename(p))
            self._update_pl_count()
            self.status_left.config(
                text=f"{len(self.playlist)} tracks loaded from last session.",
                fg=self.palette["GRAY"])

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

        for w in list(self.root.winfo_children()):
            if not isinstance(w, tk.Toplevel):
                continue
            if video_win is not None and w is video_win:
                continue
            try:
                if w.winfo_exists():
                    w.attributes("-alpha", a)
            except Exception:
                pass

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
            self.listbox.insert("end", os.path.basename(p))
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

        vrow = tk.Frame(self._main_frame, bg=p["PANEL"])
        vrow.pack(fill="x", padx=8, pady=(0, 4))
        tk.Label(vrow, text="VOL", font=FONT_UI_B, bg=p["PANEL"], fg=p["GRAY"]).pack(side="left")
        self.vol_scale = tk.Scale(vrow, from_=0, to=100, orient="horizontal", length=130,
                                  showvalue=False, command=self._on_vol_cmd, bg=p["PANEL"],
                                  fg=p["ACCENT"], troughcolor=p["SUNKEN"], highlightthickness=0,
                                  sliderrelief="flat")
        self.vol_scale.set(85)
        self.vol_scale.pack(side="left", padx=(4, 10))
        tk.Label(vrow, text="BAL", font=FONT_UI_B, bg=p["PANEL"], fg=p["GRAY"]).pack(side="left")
        self.bal_scale = tk.Scale(vrow, from_=0, to=100, orient="horizontal", length=130,
                                  showvalue=False, command=self._on_bal_cmd, bg=p["PANEL"],
                                  fg=p["ACCENT"], troughcolor=p["SUNKEN"], highlightthickness=0,
                                  sliderrelief="flat")
        self.bal_scale.set(50)
        self.bal_scale.pack(side="left", padx=(4, 10))
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
        self.listbox = tk.Listbox(lrow, height=8, selectmode="single", bd=0,
                                  highlightthickness=0, exportselection=False,
                                  bg=list_bg, fg=p["WHITE"], font=("Courier New", 9),
                                  selectbackground=select_bg, selectforeground=p["WHITE"],
                                  activestyle="none")
        self.pl_scale = self._mk_pl_scale(lrow)
        self.listbox.configure(yscrollcommand=self._sync_pl_scrollbar)
        self.listbox.pack(side="left", fill="both", expand=True)
        self.pl_scale.pack(side="right", fill="y", padx=(2, 0))
        self.listbox.bind("<Double-Button-1>", lambda e: self._play_selected())
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

    # --- Playlist reorder -----------------------------------------------
    def move_up(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        i = sel[0]
        if i <= 0:
            return
        self._swap_items(i, i - 1)

    def move_down(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        i = sel[0]
        if i >= len(self.playlist) - 1:
            return
        self._swap_items(i, i + 1)

    def _swap_items(self, i, j):
        if not (0 <= i < len(self.playlist) and 0 <= j < len(self.playlist)):
            return
        if i == j:
            return

        self.playlist[i], self.playlist[j] = self.playlist[j], self.playlist[i]

        self.listbox.delete(0, "end")
        for p in self.playlist:
            self.listbox.insert("end", os.path.basename(p))

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
        self._hero_resize_job = None
        try:
            w = max(self.hero.winfo_width(), 40)
            h = max(self.hero.winfo_height(), 40)
            img = load_background(w, h, self.palette, self._bg_path)
            self._bg_photo = ImageTk.PhotoImage(img)
            self.hero.delete("bg")
            self.hero.create_image(0, 0, image=self._bg_photo, anchor="nw", tags="bg")
            self.hero.tag_lower("bg")
            self._draw_hero_overlay()
        except Exception as e:
            print(f"[XZN] _render_bg failed: {e}")

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
        try:
            if is_light_theme:
                c.create_rectangle(0, h - 60, w, h, fill="#ffffff", outline="", stipple="gray50", tags="fg")
            else:
                c.create_rectangle(0, h - 60, w, h, fill=p["SUNKEN"], outline="", stipple="gray50", tags="fg")
        except Exception:
            pass

        text = "XZN"
        font = ("Helvetica", 22, "bold")
        shadow = "#000000" if not is_light_theme else "#ffffff"
        main = "#ffffff" if not is_light_theme else "#1a1240"

        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-2, 0), (2, 0), (0, -2), (0, 2)):
            c.create_text(20 + dx, 22 + dy, text=text, font=font, fill=shadow, anchor="nw", tags="fg")
        c.create_text(20, 22, text=text, font=font, fill=main, anchor="nw", tags="fg")

    # --- BG popup --------------------------------------------------------
    def _bg_menu(self):
        self._close_bg_popup()
        try:
            x = self.btn_bg.winfo_rootx()
            y = self.btn_bg.winfo_rooty() + self.btn_bg.winfo_height()
        except Exception:
            x = self.root.winfo_rootx() + 100
            y = self.root.winfo_rooty() + 100
        self._bg_popup = BgPopup(self, x, y)

    def _close_bg_popup(self):
        popup = getattr(self, "_bg_popup", None)
        if popup is not None:
            try:
                popup.close()
            except Exception:
                pass
            self._bg_popup = None

    def remove_bg(self):
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
            title="Choose background image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.gif"), ("All files", "*.*")])
        if p:
            self._bg_path = p
            self._render_bg()
            self._persist_settings()
            self.status_left.config(text=f"Background: {os.path.basename(p)}", fg=self.palette["GRAY"])

    def _on_alpha_cmd(self, v):
        self._apply_alpha_to_windows()
        self._persist_settings()

    def show_info(self):
        InfoWindow(self.root, self.palette)
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
                self.listbox.insert("end", os.path.basename(p))
                added += 1
        self._update_pl_count()
        if added:
            self._persist_settings()
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
        for i in sorted(sel, reverse=True):
            del self.playlist[i]
            self.listbox.delete(i)
            if i < self.current_index:
                self.current_index -= 1
            elif i == self.current_index:
                self.current_index = -1
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
        v = int(self.vol_scale.get()) + dv
        self.vol_scale.set(max(0, min(100, v)))
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
                self._video_win = VideoWindow(self.root)
                self._video_win.apply_palette(self.palette)
            if self._video_win.win.state() == "normal":
                self._video_win.hide()
            else:
                self._video_win.show()
            self._apply_alpha_to_windows()
        except Exception as e:
            print(f"[XZN] _toggle_video_window failed: {e}")

    def _toggle_video_fullscreen(self):
        try:
            if self._video_win is None or not self._video_win.is_alive():
                self._video_win = VideoWindow(self.root)
                self._video_win.apply_palette(self.palette)
            self._video_win.show()
            self._video_win.toggle_fullscreen()
            self._apply_alpha_to_windows()
        except Exception as e:
            print(f"[XZN] _toggle_video_fullscreen failed: {e}")

    def _on_seek_cmd(self, v):
        if self._seek_dragging:
            dur = self.player.duration or 0.0
            self.cur_lbl.config(text=fmt_time(dur * float(v) / 1000.0))

    def _on_seek_release(self, e=None):
        self._seek_dragging = False
        p = self.player
        if p.state in ("playing", "paused") and p.duration > 0:
            p.seek(p.duration * float(self.seek.get()) / 1000.0)

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
        if title and artist:
            self.player.title_text = f"{artist} — {title}"
            if 0 <= self.current_index < len(self.playlist):
                try:
                    self.listbox.delete(self.current_index)
                    self.listbox.insert(self.current_index, f"{artist} - {title}")
                    self.listbox.selection_set(self.current_index)
                except Exception:
                    pass
        elif title:
            self.player.title_text = title
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
                self._video_win = VideoWindow(self.root)
                self._video_win.apply_palette(self.palette)
            try:
                self._video_win.fit_to_video(v[1], v[2])
            except Exception as e:
                print(f"[XZN] fit_to_video failed: {e}")
            self._video_win.show()
            self._video_win.show_placeholder()
            self._apply_alpha_to_windows()

    def _on_state(self, s):
        glyph = {"playing": "▶", "paused": "II"}.get(s, "■")
        self.state_lbl.config(text=glyph)
        self.btn_play.config(text="||" if s == "playing" else "▶")
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
            if not self._seek_dragging:
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
    print(f"[XZN] Icon: {icon if icon else 'NOT FOUND (xzn.ico missing)'}")
    app = App()
    argv_files = [a for a in sys.argv[1:] if os.path.isfile(a)]
    if argv_files:
        def _add_cli():
            app.add_paths(argv_files)
        app.root.after(260, _add_cli)
    app.run()


if __name__ == "__main__":
    main()
