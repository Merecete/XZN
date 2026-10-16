#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XZN-CDR v2.0
============
Companion tool for Xenon-Zones that rips audio CDs to MP3 / OGG / FLAC / WAV.

Unified codebase — one file, three platforms, three CD backends:

- Windows : ffmpeg -f libcdio   (single dependency: ffmpeg full build)
- Linux   : pycdio              (libcdio + pycdio packages)
- macOS   : cd-paranoia         (brew install libcdio-paranoia)

The GUI, theming, alpha handling, cancel logic and license window are
shared across all platforms. Only the CD-access layer differs and is
selected at runtime by make_backend().

Requirements (source):
- ffmpeg (all platforms, required for encoding)
    - Windows: MUST be a "full" build with libcdio support
- Windows: nothing else (ffprobe ships with ffmpeg)
- Linux:   sudo apt install libcdio-dev libcdio-cdda-dev python3-pycdio
- macOS:   brew install libcdio-paranoia ffmpeg
- Pillow (PIL) — for the Info window logo

Bundling (PyInstaller):
    pyinstaller --onefile --windowed --icon="xzn.ico"
                --name="XZN-CDR"
                --add-data "xzn-logo.png;."
                --add-binary "ffmpeg;ffmpeg"
                xzn_cdr.py

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
import subprocess
import traceback
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk

APP_NAME = "XZN-CDR"
APP_LONG_NAME = "Xenon-Zones CD Ripper"
VERSION = "1.0.0"
SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".xzn_cdr_settings.json")

ICON_FILENAME = "xzn.ico"
LOGO_FILENAME = "xzn-logo.png"
TASKBAR_APP_ID = "com.katie.xzn.cdripper.1"

IS_WINDOWS = (sys.platform == "win32")
IS_MAC = (sys.platform == "darwin")
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
    return shutil.which("ffmpeg")


def find_ffprobe() -> Optional[str]:
    exe_name = "ffprobe.exe" if IS_WINDOWS else "ffprobe"
    bundled = resource_path(os.path.join("ffmpeg", exe_name))
    if os.path.isfile(bundled):
        return bundled
    return shutil.which("ffprobe")


def find_cdparanoia() -> Optional[str]:
    return shutil.which(CDPARANOIA_BIN)


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
            pass
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


# ========================================================================
# CD backends
# ========================================================================
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


def encode_args_for_ffmpeg(ffmpeg: str, fmt: str, bitrate: str,
                           wav_in: str, out: str) -> List[str]:
    """
    Build a ffmpeg command line for encoding a WAV file to the target format.
    `bitrate` is a string like "128", "192", "256" or "320" (kbit/s).
    For OGG it is mapped to the corresponding Vorbis quality value.
    """
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
    """Abstract CD backend. Subclasses implement list_tracks + rip_track."""
    name = "abstract"

    def is_available(self) -> bool:
        return False

    def availability_hint(self) -> str:
        return ""

    def list_tracks(self, drive: str) -> List[Tuple[int, float]]:
        raise NotImplementedError

    def rip_track(self, drive: str, track_no: int, out_path: str,
                  duration_sec: float,
                  bitrate: str = "320",
                  progress_callback=None,
                  cancel_event=None,
                  is_last_track: bool = False) -> None:
        """
        Rip a single track to out_path. Backend is responsible for:
        - reading CD audio
        - encoding to the target format (or writing WAV directly)
        - honouring cancel_event (raise CancelledError)
        - honouring is_last_track (avoid end-of-disc hang)
        - honouring the requested bitrate for lossy formats
        """
        raise NotImplementedError

    def eject(self, drive: str, ui_queue: "queue.Queue") -> None:
        """Optional: eject the disc. Default does nothing."""
        pass


# ------------------------------------------------------------------------
# Windows backend: ffmpeg -f libcdio
# ------------------------------------------------------------------------
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
        for idx, ch in enumerate(chapters):
            track_no = idx + 1
            try:
                title = (ch.get("tags", {}) or {}).get("title", "")
                m = re.search(r"(\d+)", str(title))
                if m:
                    track_no = int(m.group(1))
            except Exception:
                pass

            try:
                start = float(ch.get("start_time", 0.0))
                end = float(ch.get("end_time", 0.0))
            except Exception:
                continue

            dur = max(0.0, end - start)
            tracks.append((track_no, dur))

        return tracks

    def rip_track(self, drive: str, track_no: int, out_path: str,
                  duration_sec: float,
                  bitrate: str = "320",
                  progress_callback=None,
                  cancel_event=None,
                  is_last_track: bool = False) -> None:

        # For the Windows ffmpeg backend we need the track's start time
        # on the disc — reconstruct it here by querying the TOC once more.
        all_tracks = self.list_tracks(drive)
        starts = {}
        acc = 0.0
        for i, (tn, dur) in enumerate(all_tracks):
            starts[tn] = acc
            acc += dur
        start_sec = starts.get(track_no, 0.0)

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


# ------------------------------------------------------------------------
# Linux backend: pycdio
# ------------------------------------------------------------------------
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
            toc = cd.get_toc()
            if not toc:
                raise RuntimeError("No TOC available — disc may not be an audio CD.")

            first_track = toc.get_first_track()
            last_track = toc.get_last_track()

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

            return tracks
        except Exception as e:
            raise RuntimeError(f"Failed to read CD TOC: {e}")
        finally:
            if cd is not None:
                try:
                    cd.close()
                except Exception:
                    pass

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
                    pass

    @staticmethod
    def _write_wav(path: str, pcm_data: bytes) -> None:
        with wave.open(path, "wb") as wf:
            wf.setnchannels(AUDIO_CHANNELS)
            wf.setsampwidth(AUDIO_BYTES_PER_SAMPLE)
            wf.setframerate(AUDIO_SAMPLE_RATE)
            wf.writeframes(pcm_data)

    def rip_track(self, drive: str, track_no: int, out_path: str,
                  duration_sec: float,
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
                pass
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


# ------------------------------------------------------------------------
# macOS backend: cd-paranoia
# ------------------------------------------------------------------------
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
                pass
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
    """Pick the right CD backend for the current platform."""
    if IS_WINDOWS:
        return FFmpegBackend(ffmpeg, ffprobe)
    if IS_MAC:
        return CdParanoiaBackend(ffmpeg)
    return PyCdioBackend()


# ========================================================================
# Ripper worker (platform-agnostic)
# ========================================================================
def rip_worker(job: RipJob, backend: CDBackend, ui_queue: "queue.Queue"):
    os.makedirs(job.outdir, exist_ok=True)

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

            out_name = f"track_{track_no:02d}{job.extension()}"
            out_path = os.path.join(job.outdir, out_name)

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
                    bitrate=job.bitrate,
                    progress_callback=progress_cb,
                    cancel_event=job.cancel_event,
                    is_last_track=is_last,
                )
            except CancelledError:
                ui_queue.put(("cancelled", f"Cancelled after {ripped} track(s)."))
                return
            except Exception as e:
                raise RuntimeError(f"Failed to rip track {track_no}: {e}")

            ripped += 1
            ui_queue.put(("progress", 100, label))
            ui_queue.put(("log", f"  -> {out_name}"))

        if job.is_cancelled():
            ui_queue.put(("cancelled", f"Cancelled after {ripped} track(s)."))
            return

        if job.eject:
            backend.eject(job.drive, ui_queue)

        ui_queue.put(("done", f"Finished ripping {total} track(s)."))

    except Exception as e:
        traceback.print_exc()
        ui_queue.put(("error", str(e)))


# ========================================================================
# macOS flat dropdown (only used on macOS, harmless elsewhere)
# ========================================================================
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


# ========================================================================
# License / Info / Theme windows
# ========================================================================
def show_license_window(parent, palette, app=None):
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
            pass

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
        pass

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
            pass

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
            pass

    def _close(self):
        try:
            if self.win in self.app._extra_alpha_windows:
                self.app._extra_alpha_windows.remove(self.win)
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass

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
            pass

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
            pass

    def _close(self):
        try:
            if self.win in self.app._extra_alpha_windows:
                self.app._extra_alpha_windows.remove(self.win)
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass


# ========================================================================
# Main application
# ========================================================================
class App:
    def __init__(self):
        set_taskbar_app_id()

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
        self.ffprobe = find_ffprobe()
        self.backend: CDBackend = make_backend(self.ffmpeg, self.ffprobe)

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
        self._extra_alpha_windows: List[tk.Toplevel] = []

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
            self._mac_menubar = tk.Menu(self.root)
            self.root.config(menu=self._mac_menubar)
            self.root.createcommand(
                "tk::mac::standardAboutPanel", self._show_mac_about
            )
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

    def _show_mac_about(self):
        try:
            self.show_info()
        except Exception as e:
            print(f"[XZN-CDR] macOS About failed: {e}")

    # --- UI helpers ------------------------------------------------------
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

    # --- Build UI --------------------------------------------------------
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

        # Header
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

        # Hero
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

        # Options
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

        # Output directory
        od = tk.Frame(parent, bg=p["PANEL"])
        od.pack(fill="x", padx=8, pady=(0, 4))
        tk.Label(od, text="Output", font=FONT_UI_B,
                 bg=p["PANEL"], fg=p["GRAY"]).pack(side="left", padx=(10, 6))
        self.outdir_label = tk.Label(od, text=self.current_outdir or "(not set)",
                                     font=FONT_UI, bg=p["SUNKEN"], fg=p["WHITE"],
                                     anchor="w", padx=6)
        self.outdir_label.pack(side="left", fill="x", expand=True, ipady=3)
        self._mkbtn(od, "…", self.pick_outdir, w=3).pack(side="right", padx=(4, 0))

        # Track list
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

        # Progress bar
        prog = tk.Frame(parent, bg=p["PANEL"])
        prog.pack(fill="x", padx=8, pady=(0, 4))
        self.progress = tk.Canvas(prog, height=14, bg=p["SUNKEN"], highlightthickness=0)
        self.progress.pack(fill="x")
        self.progress.bind("<Configure>", lambda e: self._draw_progress(self._progress_value))

        # Action buttons
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

        # Status bar
        self.status = tk.Label(parent, text="Ready.",
                               font=FONT_UI, bg=p["PANEL"], fg=p["GRAY"], anchor="w")
        self.status.pack(fill="x", side="bottom")

        self._render_tracks()

    # --- Mousewheel ------------------------------------------------------
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

    # --- Palette / alpha -------------------------------------------------
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
            pass

        all_toplevels = []

        def _collect(widget):
            try:
                for child in widget.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        all_toplevels.append(child)
                    _collect(child)
            except Exception:
                pass

        _collect(self.root)

        for w in list(getattr(self, "_extra_alpha_windows", [])):
            try:
                if w.winfo_exists() and w not in all_toplevels:
                    all_toplevels.append(w)
            except Exception:
                pass

        for w in all_toplevels:
            try:
                if w.winfo_exists():
                    w.attributes("-alpha", a)
            except Exception:
                pass

    # --- Info / Theme ----------------------------------------------------
    def show_info(self):
        InfoWindow(self.root, self.palette, self)
        self._apply_alpha_to_windows()

    def show_theme(self):
        ThemeWindow(self.root, self)
        self._apply_alpha_to_windows()

    # --- Settings --------------------------------------------------------
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

    # --- Startup ---------------------------------------------------------
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

    # --- Drive scanning --------------------------------------------------
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
        self.status.config(
            text=f"Found {len(tracks)} track(s) — total {fmt_time(self.total_cd_time)}.",
            fg=p["GRAY"])
        self._render_tracks()
        self._persist_settings()

    # --- Track rendering -------------------------------------------------
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

    # --- Output ----------------------------------------------------------
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

    # --- Ripping ---------------------------------------------------------
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

        backend = self.backend  # bind for the thread

        def worker():
            rip_worker(job, backend, self.ui_queue)

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

    # --- Progress bar ----------------------------------------------------
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

    # --- UI queue polling ------------------------------------------------
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

    # --- Shutdown --------------------------------------------------------
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
