#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XZN-CDR (macOS) v1.3
====================
Companion tool for Xenon Zones that rips audio CDs to MP3 / OGG / FLAC / WAV.

macOS-specific backend:
- CD access via `cd-paranoia` (Homebrew: brew install libcdio-paranoia)
- Encoding with ffmpeg (Homebrew: brew install ffmpeg)
- Eject via `drutil`

No pycdio, no libcdio headers, no C compilation.

Requirements:
    brew install libcdio-paranoia ffmpeg

v1.2:
- macOS menu cleaned up (no File/Edit/Window/Help)
- About entry opens the custom Info window
v1.3:
- Drive entry no longer has a light focus ring
- Format / Bitrate dropdowns now match the dark theme

Copyright (c) 2026 Merecete
Released under the MIT License. See LICENSE file for details.
"""
import os
import sys
import json
import shutil
import queue
import threading
import subprocess
import traceback
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple
import tkinter as tk
from tkinter import filedialog, messagebox

APP_NAME = "XZN-CDR"
APP_LONG_NAME = "Xenon Zones CD Ripper"
VERSION = "Alpha 1.0"
SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".xzn_cdr_settings.json")

ICON_FILENAME = "xzn.ico"

# --- Platform detection -------------------------------------------------
IS_MAC = (sys.platform == "darwin")
IS_WINDOWS = (sys.platform == "win32")

AUDIO_SAMPLE_RATE = 44100
AUDIO_CHANNELS = 2
AUDIO_BYTES_PER_SAMPLE = 2
CD_SECTORS_PER_SECOND = 75
CD_FRAME_SIZE = 2352

CDPARANOIA_BIN = "cd-paranoia"
CDPARANOIA_PKG = "libcdio-paranoia"


# ------------------------------------------------------------------------
# Palette + helpers
# ------------------------------------------------------------------------
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
    bundled = resource_path(os.path.join("ffmpeg", "ffmpeg"))
    if os.path.isfile(bundled):
        return bundled
    return shutil.which("ffmpeg")


def find_cdparanoia() -> Optional[str]:
    return shutil.which(CDPARANOIA_BIN)


def find_drutil() -> Optional[str]:
    return shutil.which("drutil") or "/usr/bin/drutil"


def apply_window_icon(window):
    icon_path = get_icon_path()
    if not icon_path:
        return
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
                       (g + (255 - g) * factor),
                       (b + (255 - b) * factor)))


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


def _suggest_default_drive() -> Optional[str]:
    for dev in ("/dev/disk2", "/dev/disk4", "/dev/disk1", "/dev/disk3"):
        if os.path.exists(dev):
            return dev
    return "/dev/disk2"


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


class CancelledError(Exception):
    pass


def _kill_process(proc):
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=2)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _safe_unlink(path):
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


# ------------------------------------------------------------------------
# Custom flat dropdown for macOS
# ------------------------------------------------------------------------
class FlatOptionMenu:
    """
    macOS-friendly replacement for tk.OptionMenu.

    The native OptionMenu ignores bg/fg on macOS Aqua theme.
    This widget uses a Label + borderless Toplevel popup instead,
    matching the app's color scheme.
    """

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

        # Keep the label text in sync if the variable changes elsewhere
        try:
            self.var.trace_add("write", lambda *a: self._update_text())
        except Exception:
            # Older Tk versions
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
# cd-paranoia helpers (macOS backend)
# ------------------------------------------------------------------------
def cdparanoia_list_tracks(device: str) -> List[Tuple[int, float]]:
    cdparanoia = find_cdparanoia()
    if not cdparanoia:
        raise RuntimeError(
            f"{CDPARANOIA_BIN} not found. "
            f"Install it with: brew install {CDPARANOIA_PKG}"
        )

    cmd = [cdparanoia, "-Q", "-d", device]
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
    pattern = re.compile(r"^\s*(\d+)\.\s+(\d+):(\d+):(\d+)")
    for line in out.splitlines():
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

    if not tracks:
        if ("No CD" in out or "Unable to open" in out
                or "not ready" in out or "No medium" in out):
            raise RuntimeError(
                f"CD drive '{device}' not ready or no audio CD detected."
            )
        raise RuntimeError(
            "Could not parse cd-paranoia output. Make sure an audio CD is "
            "inserted and the device path is correct.\n\n"
            f"cd-paranoia said:\n{out[-500:]}"
        )

    return tracks


def cdparanoia_rip_track(device: str, track_no: int, out_wav: str,
                         progress_callback=None,
                         cancel_event=None,
                         duration_sec: float = 0.0) -> None:
    cdparanoia = find_cdparanoia()
    if not cdparanoia:
        raise RuntimeError(
            f"{CDPARANOIA_BIN} not found. "
            f"Install it with: brew install {CDPARANOIA_PKG}"
        )

    cmd = [cdparanoia, "-d", device, "-w", out_wav, str(track_no)]

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
                _safe_unlink(out_wav)
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
        _safe_unlink(out_wav)
        raise CancelledError()

    if proc.returncode != 0:
        _safe_unlink(out_wav)
        raise RuntimeError(
            f"cd-paranoia failed for track {track_no} "
            f"(exit code {proc.returncode})."
        )

    if not os.path.isfile(out_wav):
        raise RuntimeError(
            f"cd-paranoia did not create '{out_wav}'. "
            "The CD may be copy-protected or unreadable."
        )


# ------------------------------------------------------------------------
# Ripper worker
# ------------------------------------------------------------------------
@dataclass
class RipJob:
    tracks: List[int]
    track_durations: List[float]
    outdir: str
    fmt: str
    bitrate: str
    drive: str
    eject: bool
    ffmpeg: Optional[str]
    cancel_event: Optional[threading.Event] = None

    def extension(self) -> str:
        return {
            "MP3":  ".mp3",
            "OGG":  ".ogg",
            "FLAC": ".flac",
            "WAV":  ".wav",
        }.get(self.fmt, ".mp3")

    def is_cancelled(self) -> bool:
        return self.cancel_event is not None and self.cancel_event.is_set()


def build_ffmpeg_args(job: RipJob, wav_path: str, out_path: str) -> List[str]:
    args = [job.ffmpeg or "ffmpeg", "-y", "-i", wav_path]
    if job.fmt == "MP3":
        args += ["-codec:a", "libmp3lame", "-b:a", f"{job.bitrate}k"]
    elif job.fmt == "OGG":
        q = {"128": "3", "192": "5", "256": "7", "320": "9"}.get(job.bitrate, "6")
        args += ["-codec:a", "libvorbis", "-qscale:a", q]
    elif job.fmt == "FLAC":
        args += ["-codec:a", "flac", "-compression_level", "5"]
    elif job.fmt == "WAV":
        args += ["-codec:a", "pcm_s16le"]
    args += [out_path]
    return args


def _eject_drive(device: str, ui_queue: "queue.Queue"):
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


def rip_worker_mac(job: RipJob, ui_queue: "queue.Queue"):
    if not find_cdparanoia():
        ui_queue.put(("error",
                      f"{CDPARANOIA_BIN} not found. "
                      f"Install with: brew install {CDPARANOIA_PKG}"))
        return

    tmpdir = os.path.join(job.outdir, ".xzn_tmp")
    os.makedirs(job.outdir, exist_ok=True)
    os.makedirs(tmpdir, exist_ok=True)

    total = len(job.tracks)
    if total == 0:
        ui_queue.put(("error", "No tracks selected."))
        return

    ripped = 0
    try:
        for idx, (track_no, duration_sec) in enumerate(
            zip(job.tracks, job.track_durations), start=1
        ):
            if job.is_cancelled():
                ui_queue.put(("cancelled", f"Cancelled after {ripped} track(s)."))
                return

            wav_path = os.path.join(tmpdir, f"track_{track_no:02d}.wav")
            out_name = f"track_{track_no:02d}{job.extension()}"
            out_path = os.path.join(job.outdir, out_name)

            label = f"Track {track_no:02d} ({idx}/{total})"
            ui_queue.put(("log", f"Reading {label} …"))
            ui_queue.put(("progress", 0, label))

            def progress_cb(pct):
                ui_queue.put(("progress", pct, label))

            try:
                cdparanoia_rip_track(
                    job.drive, track_no, wav_path,
                    progress_callback=progress_cb,
                    cancel_event=job.cancel_event,
                    duration_sec=duration_sec,
                )
            except CancelledError:
                _safe_unlink(wav_path)
                ui_queue.put(("cancelled", f"Cancelled after {ripped} track(s)."))
                return
            except Exception as e:
                raise RuntimeError(f"Failed to read track {track_no}: {e}")

            if job.is_cancelled():
                _safe_unlink(wav_path)
                ui_queue.put(("cancelled", f"Cancelled after {ripped} track(s)."))
                return

            ui_queue.put(("progress", 50, label))

            if job.fmt != "WAV":
                if not job.ffmpeg:
                    raise RuntimeError(
                        "ffmpeg not found — required for MP3/OGG/FLAC."
                    )

                ff_args = build_ffmpeg_args(job, wav_path, out_path)
                ui_queue.put(("log", f"  Encoding -> {out_name}"))

                proc = subprocess.Popen(
                    ff_args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                proc.wait()

                if job.is_cancelled():
                    _kill_process(proc)
                    _safe_unlink(out_path)
                    _safe_unlink(wav_path)
                    ui_queue.put(("cancelled", f"Cancelled after {ripped} track(s)."))
                    return

                if proc.returncode != 0:
                    err = ""
                    try:
                        if proc.stderr is not None:
                            err = "".join(proc.stderr.readlines())[-300:]
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"ffmpeg failed for track {track_no}: {err.strip()}"
                    )

                try:
                    os.remove(wav_path)
                except Exception:
                    pass
            else:
                shutil.move(wav_path, out_path)

            ripped += 1
            ui_queue.put(("progress", 100, label))
            ui_queue.put(("log", f"  -> {out_name}"))

        if job.is_cancelled():
            ui_queue.put(("cancelled", f"Cancelled after {ripped} track(s)."))
            return

        if job.eject:
            _eject_drive(job.drive, ui_queue)

        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass

        ui_queue.put(("done", f"Finished ripping {total} track(s)."))

    except Exception as e:
        traceback.print_exc()
        ui_queue.put(("error", str(e)))
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


# ------------------------------------------------------------------------
# Theme + Info windows
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

        app._mkbtn(btn_row, "Default (Purple)", self._reset, w=14).pack(side="left")
        app._mkbtn(btn_row, "Close", self.win.destroy, w=8).pack(side="right")

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
        self.app.apply_accent(color)

    def _reset(self):
        try:
            self.win.destroy()
        except Exception:
            pass
        self.app.apply_accent(DEFAULT_PALETTE["ACCENT"])


class InfoWindow:
    def __init__(self, master, palette, app):
        self.win = tk.Toplevel(master)
        self.win.title("Info – XZN-CDR")
        self.win.configure(bg=palette["PANEL"])
        self.win.resizable(False, False)
        self.win.transient(master)
        apply_window_icon(self.win)

        p = palette
        frame = tk.Frame(self.win, bg=p["PANEL"], padx=26, pady=22)
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text=APP_LONG_NAME.upper(),
                 font=("Helvetica", 15, "bold"),
                 bg=p["PANEL"], fg=p["ACCENT"]).pack(anchor="w")
        tk.Frame(frame, bg=p["ACCENT_LO"], height=1).pack(fill="x", pady=(8, 14))

        tk.Label(frame, text="Companion tool for Xenon Zones.",
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

        tk.Label(frame, text="CD backend: cd-paranoia (libcdio)",
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

        close_btn = app._mkbtn(frame, "Close", self.win.destroy, w=8)
        close_btn.pack(anchor="e")

        self.win.update_idletasks()
        mx = master.winfo_rootx() + master.winfo_width() // 2
        my = master.winfo_rooty() + master.winfo_height() // 2
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        self.win.geometry(f"+{mx - w // 2}+{my - h // 2}")
        self.win.bind("<Escape>", lambda e: self.win.destroy())


# ------------------------------------------------------------------------
# Main application
# ------------------------------------------------------------------------
class App:
    def __init__(self):
        self.root = tk.Tk()
        apply_window_icon(self.root)
        self._setup_macos_menu()
        self.root.title(APP_NAME)
        self.root.configure(bg=DEFAULT_PALETTE["BG"])
        self.root.minsize(680, 640)
        self.root.geometry("720x680")

        settings = load_settings()
        self.accent = settings.get("accent") or DEFAULT_PALETTE["ACCENT"]
        self._initial_alpha = int(settings.get("alpha") or 100)
        self.palette = build_palette(self.accent)

        self.ffmpeg = find_ffmpeg()
        self.cdparanoia = find_cdparanoia()

        self.cd_tracks: List[Tuple[int, float]] = []
        self.total_cd_time: float = 0.0
        self.ripping = False
        self.worker_thread: Optional[threading.Thread] = None
        self.ui_queue: "queue.Queue" = queue.Queue()
        self.current_outdir = settings.get("outdir")
        self.current_drive = settings.get("drive") or _suggest_default_drive() or ""
        self.format_var = tk.StringVar(value=settings.get("format") or "MP3")
        self.bitrate_var = tk.StringVar(value=settings.get("bitrate") or "320")
        self.eject_var = tk.BooleanVar(value=bool(settings.get("eject", True)))
        self.track_checks: List[tk.BooleanVar] = []
        self._cancel_event: Optional[threading.Event] = None

        self._main_frame = None
        self._progress_value = 0.0
        self._progress_label = ""

        self._build_ui()

        try:
            self.root.attributes("-alpha", self._initial_alpha / 100.0)
        except Exception:
            pass

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Escape>", lambda e: self.on_close())

        self.root.after(100, self._poll_ui_queue)
        self.root.after(200, self._startup_check)

    # --- macOS menu ------------------------------------------------------
    def _setup_macos_menu(self):
        if not IS_MAC:
            return
        try:
            menubar = tk.Menu(self.root)
            self.root.config(menu=menubar)
            self.root.createcommand(
                "tk::mac::standardAboutPanel", self._show_mac_about
            )
            print("[XZN-CDR] macOS menu customized.")
        except Exception as e:
            print(f"[XZN-CDR] macOS menu setup failed: {e}")

    def _show_mac_about(self):
        try:
            InfoWindow(self.root, self.palette, self)
        except Exception as e:
            print(f"[XZN-CDR] macOS About failed: {e}")

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
            pass

    def _sync_track_scrollbar(self, *args):
        try:
            first, last = self.track_canvas.yview()
            if last - first >= 1.0:
                self.track_scale.set(0)
            else:
                self.track_scale.set(first)
        except Exception:
            pass

    def _build_ui(self):
        p = self.palette
        self.root.configure(bg=p["BG"])

        if self._main_frame is not None:
            try:
                self._main_frame.destroy()
            except Exception:
                pass
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

        # --- Drive entry inside a dark frame (no macOS focus ring) ------
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
        self.drive_entry.insert(0, self.current_drive)

        self._mkbtn(opt, "SCAN", self.scan_drive, w=6).grid(row=0, column=2, padx=(6, 0), pady=4)

        tk.Label(opt, text="Format", font=FONT_UI_B,
                 bg=p["PANEL"], fg=p["GRAY"]).grid(row=0, column=3, sticky="w", padx=(20, 4), pady=4)
        fmt_menu = FlatOptionMenu(opt, self.format_var,
                                  ["MP3", "OGG", "FLAC", "WAV"],
                                  palette=p, width=6)
        fmt_menu.grid(row=0, column=4, sticky="w", pady=4)

        tk.Label(opt, text="Bitrate", font=FONT_UI_B,
                 bg=p["PANEL"], fg=p["GRAY"]).grid(row=0, column=5, sticky="w", padx=(20, 4), pady=4)
        bit_menu = FlatOptionMenu(opt, self.bitrate_var,
                                  ["128", "192", "256", "320"],
                                  palette=p, width=5)
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
        self.outdir_label = tk.Label(od, text=self.current_outdir or "(not set)",
                                     font=FONT_UI, bg=p["SUNKEN"], fg=p["WHITE"],
                                     anchor="w", padx=6)
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
        self.btn_cancel = self._mkbtn(brow, "CANCEL", self.cancel_rip, w=8)
        self.btn_cancel.pack(side="left", padx=(0, 4))
        self.btn_open_out = self._mkbtn(brow, "OPEN FOLDER", self.open_outdir, w=12)
        self.btn_open_out.pack(side="right", padx=(4, 10))

        self.status = tk.Label(parent, text="Ready.",
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
            pass
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
            pass

        self._persist_settings()

    def _on_alpha_cmd(self, v):
        try:
            self.root.attributes("-alpha", float(v) / 100.0)
        except Exception:
            pass
        self._persist_settings()

    def show_info(self):
        InfoWindow(self.root, self.palette, self)

    def show_theme(self):
        ThemeWindow(self.root, self)

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
            msgs.append("ffmpeg missing (brew install ffmpeg)")
        if not self.cdparanoia:
            msgs.append(f"{CDPARANOIA_BIN} missing (brew install {CDPARANOIA_PKG})")
        if msgs:
            self.status.config(text="Warning: " + " | ".join(msgs), fg=p["ERR"])
        else:
            src = "bundled" if hasattr(sys, "_MEIPASS") else "system"
            self.status.config(
                text=f"Ready. Backend: {CDPARANOIA_BIN} ({src}).", fg=p["GRAY"]
            )

    def scan_drive(self):
        p = self.palette
        drive = self.drive_entry.get().strip() or self.current_drive
        self.current_drive = drive

        if not drive:
            self.status.config(text="No drive specified.", fg=p["ERR"])
            return

        if not self.cdparanoia:
            self.status.config(
                text=f"{CDPARANOIA_BIN} not found — install with: brew install {CDPARANOIA_PKG}",
                fg=p["ERR"],
            )
            messagebox.showerror(
                f"{CDPARANOIA_BIN} not available",
                f"This tool requires '{CDPARANOIA_BIN}' on macOS.\n\n"
                f"Install it with:\n    brew install {CDPARANOIA_PKG}"
            )
            return

        self.status.config(text=f"Scanning {drive} …", fg=p["GRAY"])
        self.root.update_idletasks()

        try:
            tracks = cdparanoia_list_tracks(drive)
        except Exception as e:
            self.status.config(text=f"Scan failed: {e}", fg=p["ERR"])
            return

        if not tracks:
            self.status.config(text="No audio CD tracks found.", fg=p["ERR"])
            return

        self.cd_tracks = tracks
        self.total_cd_time = sum(d for _, d in tracks)
        self.status.config(
            text=f"Found {len(tracks)} track(s) — total {fmt_time(self.total_cd_time)}.",
            fg=p["GRAY"])
        self._render_tracks()
        self._persist_settings()

    def _render_tracks(self):
        p = self.palette
        if not hasattr(self, "track_inner") or self.track_inner is None:
            return

        for w in self.track_inner.winfo_children():
            w.destroy()
        self.track_checks = []

        if not self.cd_tracks:
            tk.Label(self.track_inner,
                     text="No CD scanned yet. Insert an audio CD and press SCAN.",
                     font=FONT_UI, bg=p["SUNKEN"], fg=p["GRAY"]).pack(anchor="w", padx=8, pady=8)
            try:
                self.track_count.config(text="")
            except Exception:
                pass
            try:
                self.track_canvas.yview_moveto(0.0)
                self.track_scale.set(0)
            except Exception:
                pass
            return

        for track_no, dur in self.cd_tracks:
            row = tk.Frame(self.track_inner, bg=p["SUNKEN"])
            row.pack(fill="x", padx=4, pady=1)
            var = tk.BooleanVar(value=True)
            self.track_checks.append(var)
            cb = tk.Checkbutton(row, variable=var, bg=p["SUNKEN"],
                                activebackground=p["SUNKEN"],
                                selectcolor=p["RAISED"], bd=0, highlightthickness=0)
            cb.pack(side="left", padx=(4, 4))
            tk.Label(row, text=f"{track_no:02d}", font=FONT_LCD,
                     bg=p["SUNKEN"], fg=p["ACCENT"], width=4, anchor="w").pack(side="left")
            tk.Label(row, text=f"{fmt_time(dur)}", font=FONT_UI,
                     bg=p["SUNKEN"], fg=p["WHITE"], width=8, anchor="w").pack(side="left")
            tk.Label(row, text="Audio Track", font=FONT_UI,
                     bg=p["SUNKEN"], fg=p["GRAY"]).pack(side="left", padx=(6, 0))

            for child in (row, cb):
                self._bind_mousewheel(child)

        try:
            self.track_count.config(text=f"{len(self.cd_tracks)} track(s)")
        except Exception:
            pass

        try:
            self.track_canvas.update_idletasks()
            self.track_canvas.configure(scrollregion=self.track_canvas.bbox("all"))
            self.track_canvas.yview_moveto(0.0)
            self.track_scale.set(0)
        except Exception:
            pass

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
            subprocess.Popen(["open", d])
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
        if not self.cdparanoia:
            self.status.config(
                text=f"{CDPARANOIA_BIN} not found — install with: brew install {CDPARANOIA_PKG}",
                fg=p["ERR"],
            )
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
            self.status.config(text="Scan a CD first.", fg=p["ERR"])
            return
        self._start_rip(list(self.cd_tracks))

    def rip_selected(self):
        if not self.cd_tracks:
            self.status.config(text="Scan a CD first.", fg=p["ERR"])
            return
        selected = [t for t, var in zip(self.cd_tracks, self.track_checks) if var.get()]
        self._start_rip(selected)

    def _start_rip(self, selected_tracks: List[Tuple[int, float]]):
        if not self._ensure_ready(selected_tracks):
            return

        track_ids = [t[0] for t in selected_tracks]
        durations = [t[1] for t in selected_tracks]

        self._cancel_event = threading.Event()

        job = RipJob(
            tracks=track_ids,
            track_durations=durations,
            outdir=self.current_outdir,
            fmt=self.format_var.get(),
            bitrate=self.bitrate_var.get(),
            drive=self.current_drive,
            eject=bool(self.eject_var.get()),
            ffmpeg=self.ffmpeg,
            cancel_event=self._cancel_event,
        )

        self.ripping = True
        self._progress_value = 0.0
        self._progress_label = ""
        self._draw_progress(0.0)
        self.status.config(text="Ripping…", fg=self.palette["GRAY"])

        def worker():
            rip_worker_mac(job, self.ui_queue)

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def cancel_rip(self):
        if not self.ripping:
            return
        try:
            if self._cancel_event is not None:
                self._cancel_event.set()
            self.status.config(text="Cancelling…", fg=self.palette["ERR"])
        except Exception as e:
            print(f"[XZN-CDR] Cancel failed: {e}")

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
                elif kind == "done":
                    msg = rest[0]
                    self.status.config(text=msg, fg=self.palette["GRAY"])
                    self._finish_rip_ok()
                elif kind == "cancelled":
                    msg = rest[0]
                    self.status.config(text=msg, fg=self.palette["ERR"])
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
                pass
            try:
                if self.worker_thread is not None:
                    self.worker_thread.join(timeout=3)
            except Exception:
                pass
        try:
            self._persist_settings()
        except Exception as e:
            print(f"[XZN-CDR] Failed to persist settings: {e}")
        try:
            self.root.destroy()
        except Exception:
            pass


def main():
    print(f"[XZN-CDR] Starting {APP_LONG_NAME} {VERSION}")
    print(f"[XZN-CDR] Python {sys.version}")
    print(f"[XZN-CDR] Platform: {sys.platform}")
    print(f"[XZN-CDR] Frozen: {hasattr(sys, '_MEIPASS')}")
    print(f"[XZN-CDR] {CDPARANOIA_BIN}: {find_cdparanoia() or 'NOT FOUND'}")
    print(f"[XZN-CDR] ffmpeg: {find_ffmpeg() or 'NOT FOUND'}")
    print(f"[XZN-CDR] Settings: {SETTINGS_PATH}")
    icon = get_icon_path()
    print(f"[XZN-CDR] Icon: {icon if icon else 'NOT FOUND (xzn.ico missing)'}")
    app = App()
    app.root.mainloop()


if __name__ == "__main__":
    main()
