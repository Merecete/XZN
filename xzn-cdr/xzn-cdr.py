#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XZN-CDR v1.0
============
Companion tool for Xenon Zones that rips audio CDs to MP3 / OGG / FLAC / WAV.

- Windows: reads audio CD via `ffmpeg -f libcdio` (single dependency)
- Linux/macOS: reads audio CD via pycdio (libcdio Python bindings)
- Encodes with ffmpeg (MP3, OGG, FLAC) or writes WAV directly
- Same look & feel as XZN: accent colors, alpha slider, borderless popup
- Track list with a scrollbar styled like XZN's playlist (tk.Scale)
- Background rip thread, live progress per track
- Cancel button to abort a rip cleanly (kills ffmpeg, removes partial file)
- Fix: last-track hang avoided via -paranoia_mode full + safety margin
- PyInstaller-ready: bundled ffmpeg is found via sys._MEIPASS
- Auto-saves settings (~/.xzn_cdr_settings.json)

Requirements (when running from source):
- ffmpeg (all platforms, required)
    - On Windows: MUST be a "full" build with libcdio support
      (e.g. https://www.gyan.dev/ffmpeg/builds/ -> ffmpeg-release-full)
    - Test: `ffmpeg -formats | findstr libcdio`
- pycdio (Linux/macOS only, for native CD access)
    - Linux:   sudo apt install libcdio-dev libcdio-cdda-dev python3-pycdio
    - macOS:   brew install libcdio && pip install pycdio

Requirements (when running as a frozen binary):
- Nothing external. ffmpeg is bundled inside the executable.

Bundling:
    Place `ffmpeg/ffmpeg.exe` and `ffmpeg/ffprobe.exe` next to xzn_cdr.py
    before running PyInstaller. Example layout:
        xzn_cdr.py
        xzn.ico
        ffmpeg/
            ffmpeg.exe
            ffprobe.exe

    PyInstaller command:
        pyinstaller --onefile --windowed --icon="xzn.ico"
                    --name="XZN-CDR"
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

APP_NAME = "XZN-CDR"
APP_LONG_NAME = "Xenon Zones CD Ripper"
VERSION = "Alpha 1.0"
SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".xzn_cdr_settings.json")

# --- Windows: taskbar icon ----------------------------------------------
if sys.platform == "win32":
    try:
        import ctypes
    except Exception:
        ctypes = None
else:
    ctypes = None

ICON_FILENAME = "xzn.ico"
TASKBAR_APP_ID = "com.katie.xzn.cdripper.1"

# --- pycdio import with graceful fallback (only used on Linux/macOS) ---
try:
    import cdio
    import pycdio
    _PYCDIO_AVAILABLE = True
except Exception:
    _PYCDIO_AVAILABLE = False
    cdio = None
    pycdio = None

# ------------------------------------------------------------------------
# Audio CD constants
# ------------------------------------------------------------------------
AUDIO_SAMPLE_RATE = 44100
AUDIO_CHANNELS = 2
AUDIO_BYTES_PER_SAMPLE = 2      # 16-bit PCM
CD_SECTORS_PER_SECOND = 75      # Red Book standard
CD_FRAME_SIZE = 2352            # Raw audio sector size (bytes)

# Safety margin for the last track. Subtracted from the track duration
# so ffmpeg does not try to read past the end of the disc, which can
# trigger a busy-loop in libcdparanoia (via libcdio).
LAST_TRACK_SAFETY_MARGIN = 0.5  # seconds


# ------------------------------------------------------------------------
# Palette + helpers (mirrors XZN's look)
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


# ------------------------------------------------------------------------
# Resource path — works both when running from source and when frozen
# by PyInstaller (--onefile extracts everything to sys._MEIPASS).
# ------------------------------------------------------------------------
def resource_path(relative_path):
    """
    Get absolute path to a resource, works for dev and for PyInstaller.
    When frozen, PyInstaller unpacks bundled data to sys._MEIPASS and we
    resolve relative names against that folder.
    """
    if hasattr(sys, "_MEIPASS"):
        base_path = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def get_icon_path():
    p = resource_path(ICON_FILENAME)
    return p if os.path.isfile(p) else None


def find_ffmpeg() -> Optional[str]:
    """
    Locate ffmpeg, preferring the bundled version if it exists.
    Bundled layout expected: <base>/ffmpeg/ffmpeg.exe (Windows) or
    <base>/ffmpeg/ffmpeg (Linux/macOS) when frozen.
    Falls back to whatever is on PATH (for running from source).
    """
    exe_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    bundled = resource_path(os.path.join("ffmpeg", exe_name))
    if os.path.isfile(bundled):
        return bundled
    return shutil.which("ffmpeg")


def find_ffprobe() -> Optional[str]:
    """
    Locate ffprobe, preferring the bundled version if it exists.
    Same logic as find_ffmpeg().
    """
    exe_name = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
    bundled = resource_path(os.path.join("ffmpeg", exe_name))
    if os.path.isfile(bundled):
        return bundled
    return shutil.which("ffprobe")


def set_taskbar_app_id():
    if sys.platform != "win32" or ctypes is None:
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(TASKBAR_APP_ID)
    except Exception as e:
        print(f"[XZN-CDR] Taskbar App-ID failed: {e}")


def apply_window_icon(window):
    icon_path = get_icon_path()
    if not icon_path:
        return
    try:
        window.iconbitmap(icon_path)
    except Exception:
        try:
            img = tk.PhotoImage(file=icon_path)
            window.tk.call("wm", "iconphoto", window._w, img)
            window._icon_photo_ref = img
        except Exception:
            pass


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


def _detect_windows_cd_drives() -> List[str]:
    result = []
    if sys.platform != "win32" or ctypes is None:
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
    if sys.platform == "win32":
        drives = _detect_windows_cd_drives()
        return drives[0] if drives else None

    if sys.platform == "darwin":
        for dev in ("/dev/disk2", "/dev/disk1", "/dev/disk3"):
            if os.path.exists(dev):
                return dev
        return None

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
    if sys.platform == "win32" and drv.startswith("/"):
        defaults["drive"] = _suggest_default_drive() or ""
    elif sys.platform != "win32" and len(drv) == 2 and drv.endswith(":"):
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


# ------------------------------------------------------------------------
# Cancellation helpers
# ------------------------------------------------------------------------
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
# ffmpeg libcdio helpers (used on Windows)
# ------------------------------------------------------------------------
def ffmpeg_list_tracks_windows(ffmpeg_path: str, drive: str) -> List[Tuple[int, float]]:
    ffprobe = find_ffprobe()
    if not ffprobe:
        raise RuntimeError(
            "ffprobe not found. It ships with ffmpeg — make sure both "
            "ffmpeg and ffprobe are available (either bundled or on PATH)."
        )

    cmd = [
        ffprobe,
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


def ffmpeg_read_track_windows(ffmpeg_path: str, drive: str,
                              track_number: int,
                              start_sec: float, duration_sec: float,
                              out_path: str,
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
        codec_args = ["-c:a", "libmp3lame"]
    elif ext == ".ogg":
        codec_args = ["-c:a", "libvorbis"]
    elif ext == ".flac":
        codec_args = ["-c:a", "flac"]
    elif ext == ".wav":
        codec_args = ["-c:a", "pcm_s16le"]
    else:
        codec_args = ["-c:a", "copy"]

    cmd = [
        ffmpeg_path,
        "-y",
        "-f", "libcdio",
        "-paranoia_mode", "full",
        "-i", drive,
        "-ss", str(start_sec),
        "-t", str(effective_duration),
        *codec_args,
        out_path,
    ]

    creationflags = 0
    if sys.platform == "win32":
        creationflags = 0x08000000  # CREATE_NO_WINDOW

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
            f"ffmpeg failed to rip track {track_number} "
            f"(exit code {proc.returncode})"
        )


# ------------------------------------------------------------------------
# pycdio helpers (used on Linux/macOS)
# ------------------------------------------------------------------------
def pycdio_list_tracks(device: str) -> List[Tuple[int, float]]:
    if not _PYCDIO_AVAILABLE:
        raise RuntimeError("pycdio is not available.")

    tracks: List[Tuple[int, float]] = []
    cd = None
    try:
        cd = cdio.Device(source=device)
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


def pycdio_read_track(device: str, track_num: int,
                      progress_callback=None,
                      cancel_event=None) -> bytes:
    if not _PYCDIO_AVAILABLE:
        raise RuntimeError("pycdio is not available.")

    cd = None
    try:
        cd = cdio.Device(source=device)
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


def pycdio_write_wav(path: str, pcm_data: bytes) -> None:
    with wave.open(path, "wb") as wf:
        wf.setnchannels(AUDIO_CHANNELS)
        wf.setsampwidth(AUDIO_BYTES_PER_SAMPLE)
        wf.setframerate(AUDIO_SAMPLE_RATE)
        wf.writeframes(pcm_data)


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
        if sys.platform == "win32":
            letter = device.rstrip(":\\/")
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
        else:
            if shutil.which("eject"):
                subprocess.run(
                    ["eject", device],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
                ui_queue.put(("log", "Disc ejected."))
            elif sys.platform == "darwin" and shutil.which("drutil"):
                subprocess.run(
                    ["drutil", "tray", "eject"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
                ui_queue.put(("log", "Disc ejected."))
            else:
                ui_queue.put(("log", "Eject skipped (no eject tool available)."))
    except Exception as e:
        ui_queue.put(("log", f"Eject skipped: {e}"))


def rip_worker_ffmpeg_windows(job: RipJob, ui_queue: "queue.Queue"):
    if not job.ffmpeg:
        ui_queue.put(("error", "ffmpeg not found."))
        return

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

            start_sec = sum(job.track_durations[:idx - 1])
            is_last = (idx == total)

            def progress_cb(pct):
                ui_queue.put(("progress", pct, label))

            try:
                ffmpeg_read_track_windows(
                    job.ffmpeg,
                    job.drive,
                    track_no,
                    start_sec,
                    duration_sec,
                    out_path,
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
            _eject_drive(job.drive, ui_queue)

        ui_queue.put(("done", f"Finished ripping {total} track(s)."))

    except Exception as e:
        traceback.print_exc()
        ui_queue.put(("error", str(e)))


def rip_worker_pycdio(job: RipJob, ui_queue: "queue.Queue"):
    if not _PYCDIO_AVAILABLE:
        ui_queue.put(("error", "pycdio is not available."))
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
        for idx, track_no in enumerate(job.tracks, start=1):
            if job.is_cancelled():
                ui_queue.put(("cancelled", f"Cancelled after {ripped} track(s)."))
                return

            wav_path = os.path.join(tmpdir, f"track_{track_no:02d}.wav")
            out_name = f"track_{track_no:02d}{job.extension()}"
            out_path = os.path.join(job.outdir, out_name)

            label = f"Track {track_no:02d} ({idx}/{total})"
            ui_queue.put(("log", f"Reading {label} …"))
            ui_queue.put(("progress", 0, label))

            def progress_cb(current, total_sectors):
                pct = int((current / max(1, total_sectors)) * 50)
                ui_queue.put(("progress", pct, label))

            try:
                pcm_data = pycdio_read_track(
                    job.drive, track_no, progress_cb,
                    cancel_event=job.cancel_event,
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

            if not pcm_data:
                raise RuntimeError(f"Track {track_no} contained no audio data.")

            ui_queue.put(("log", f"  Writing {os.path.basename(wav_path)} …"))
            pycdio_write_wav(wav_path, pcm_data)
            ui_queue.put(("progress", 60, label))

            if job.fmt != "WAV":
                if not job.ffmpeg:
                    raise RuntimeError(
                        "ffmpeg not found — required for MP3/OGG/FLAC."
                    )

                ff_args = build_ffmpeg_args(job, wav_path, out_path)
                ui_queue.put(("log", f"  Encoding -> {out_name}"))

                creationflags = 0
                if sys.platform == "win32":
                    creationflags = 0x08000000

                proc = subprocess.Popen(
                    ff_args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=creationflags,
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

        backend = "pycdio (native)" if sys.platform != "win32" else "ffmpeg -f libcdio"
        tk.Label(frame, text=f"CD backend: {backend}",
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
        set_taskbar_app_id()

        self.root = tk.Tk()
        apply_window_icon(self.root)
        self.root.title(APP_NAME)
        self.root.configure(bg=DEFAULT_PALETTE["BG"])
        self.root.minsize(680, 640)
        self.root.geometry("720x680")

        settings = load_settings()
        self.accent = settings.get("accent") or DEFAULT_PALETTE["ACCENT"]
        self._initial_alpha = int(settings.get("alpha") or 100)
        self.palette = build_palette(self.accent)

        # External tools
        self.ffmpeg = find_ffmpeg()
        self.ffprobe = find_ffprobe()
        self.is_windows = (sys.platform == "win32")

        # State
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

    # -- UI ---------------------------------------------------------------
    def _mkbtn(self, parent, text, cmd, w=4):
        p = self.palette
        bg_color = p["RAISED"]
        fg_color = p["WHITE"]
        hover_bg = lighten(bg_color, 0.15)

        if sys.platform == "darwin" or sys.platform.startswith("linux"):
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
        """Create a vertical tk.Scale styled exactly like XZN's playlist
        scrollbar (same width, same colors, same look)."""
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

        # Header ---------------------------------------------------------
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

        # Hero -----------------------------------------------------------
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

        # Options --------------------------------------------------------
        opt = tk.Frame(parent, bg=p["PANEL"])
        opt.pack(fill="x", padx=8, pady=(0, 4))

        tk.Label(opt, text="Drive", font=FONT_UI_B,
                 bg=p["PANEL"], fg=p["GRAY"]).grid(row=0, column=0, sticky="w", padx=(10, 4), pady=4)
        self.drive_entry = tk.Entry(opt, width=18, bg=p["SUNKEN"], fg=p["WHITE"],
                                    insertbackground=p["WHITE"], relief="flat", font=FONT_UI)
        self.drive_entry.insert(0, self.current_drive)
        self.drive_entry.grid(row=0, column=1, sticky="w", pady=4)

        self._mkbtn(opt, "SCAN", self.scan_drive, w=6).grid(row=0, column=2, padx=(6, 0), pady=4)

        tk.Label(opt, text="Format", font=FONT_UI_B,
                 bg=p["PANEL"], fg=p["GRAY"]).grid(row=0, column=3, sticky="w", padx=(20, 4), pady=4)
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

        # Output directory -----------------------------------------------
        od = tk.Frame(parent, bg=p["PANEL"])
        od.pack(fill="x", padx=8, pady=(0, 4))
        tk.Label(od, text="Output", font=FONT_UI_B,
                 bg=p["PANEL"], fg=p["GRAY"]).pack(side="left", padx=(10, 6))
        self.outdir_label = tk.Label(od, text=self.current_outdir or "(not set)",
                                     font=FONT_UI, bg=p["SUNKEN"], fg=p["WHITE"],
                                     anchor="w", padx=6)
        self.outdir_label.pack(side="left", fill="x", expand=True, ipady=3)
        self._mkbtn(od, "…", self.pick_outdir, w=3).pack(side="right", padx=(4, 0))

        # Track list -----------------------------------------------------
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

        # Progress bar ---------------------------------------------------
        prog = tk.Frame(parent, bg=p["PANEL"])
        prog.pack(fill="x", padx=8, pady=(0, 4))
        self.progress = tk.Canvas(prog, height=14, bg=p["SUNKEN"], highlightthickness=0)
        self.progress.pack(fill="x")
        self.progress.bind("<Configure>", lambda e: self._draw_progress(self._progress_value))

        # Action buttons ------------------------------------------------
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

        # Status bar ----------------------------------------------------
        self.status = tk.Label(parent, text="Ready.",
                               font=FONT_UI, bg=p["PANEL"], fg=p["GRAY"], anchor="w")
        self.status.pack(fill="x", side="bottom")

        self._render_tracks()

    # -- Mousewheel bindings for the track list ---------------------------
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

    # -- Palette ----------------------------------------------------------
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

    # -- Info / Theme -----------------------------------------------------
    def show_info(self):
        InfoWindow(self.root, self.palette, self)

    def show_theme(self):
        ThemeWindow(self.root, self)

    # -- Settings ---------------------------------------------------------
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

    # -- Startup ----------------------------------------------------------
    def _startup_check(self):
        p = self.palette
        msgs = []
        if not self.ffmpeg:
            msgs.append("ffmpeg missing (required)")
        if self.is_windows and not self.ffprobe:
            msgs.append("ffprobe missing (required on Windows)")
        if not self.is_windows and not _PYCDIO_AVAILABLE:
            msgs.append("pycdio missing (install libcdio + pycdio)")
        if msgs:
            self.status.config(text="Warning: " + " | ".join(msgs), fg=p["ERR"])
        else:
            backend = "ffmpeg-libcdio" if self.is_windows else "pycdio"
            src = "bundled" if hasattr(sys, "_MEIPASS") else "system"
            self.status.config(text=f"Ready. Backend: {backend} ({src}).", fg=p["GRAY"])

    # -- Drive scanning ---------------------------------------------------
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

        if not self.is_windows and not _PYCDIO_AVAILABLE:
            self.status.config(
                text="pycdio not installed — install libcdio + pycdio.",
                fg=p["ERR"])
            messagebox.showerror(
                "pycdio not available",
                "This tool requires the 'pycdio' module on Linux/macOS.\n\n"
                "Install:\n"
                "  Linux:   sudo apt install libcdio-dev libcdio-cdda-dev python3-pycdio\n"
                "  macOS:   brew install libcdio && pip install pycdio"
            )
            return

        self.status.config(text=f"Scanning {drive} …", fg=p["GRAY"])
        self.root.update_idletasks()

        try:
            if self.is_windows:
                tracks = ffmpeg_list_tracks_windows(self.ffmpeg, drive)
            else:
                tracks = pycdio_list_tracks(drive)
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

    # -- Track rendering --------------------------------------------------
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

    # -- Output ------------------------------------------------------------
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
            if sys.platform == "win32":
                os.startfile(d)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", d])
            else:
                subprocess.Popen(["xdg-open", d])
        except Exception as e:
            self.status.config(text=f"Open failed: {e}", fg=self.palette["ERR"])

    # -- Ripping -----------------------------------------------------------
    def _ensure_ready(self, tracks):
        p = self.palette
        if self.ripping:
            self.status.config(text="Already ripping. Please wait…", fg=p["GRAY"])
            return False
        if not self.ffmpeg:
            self.status.config(text="ffmpeg not found.", fg=p["ERR"])
            return False
        if not tracks:
            self.status.config(text="No tracks selected.", fg=p["ERR"])
            return False
        if not self.current_outdir:
            self.status.config(text="Please choose an output folder.", fg=p["ERR"])
            return False
        if not self.is_windows and not _PYCDIO_AVAILABLE:
            self.status.config(text="pycdio not available.", fg=p["ERR"])
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
            if self.is_windows:
                rip_worker_ffmpeg_windows(job, self.ui_queue)
            else:
                rip_worker_pycdio(job, self.ui_queue)

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def cancel_rip(self):
        """Signal the worker to stop. Kills ffmpeg, removes partial file."""
        if not self.ripping:
            return
        try:
            if self._cancel_event is not None:
                self._cancel_event.set()
            self.status.config(text="Cancelling…", fg=self.palette["ERR"])
        except Exception as e:
            print(f"[XZN-CDR] Cancel failed: {e}")

    # -- Progress bar -----------------------------------------------------
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

    # -- UI queue polling --------------------------------------------------
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

    # -- Shutdown ---------------------------------------------------------
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
    print(f"[XZN-CDR] ffmpeg: {find_ffmpeg() or 'NOT FOUND'}")
    print(f"[XZN-CDR] ffprobe: {find_ffprobe() or 'NOT FOUND'}")
    print(f"[XZN-CDR] Settings: {SETTINGS_PATH}")
    icon = get_icon_path()
    print(f"[XZN-CDR] Icon: {icon if icon else 'NOT FOUND (xzn.ico missing)'}")
    app = App()
    app.root.mainloop()


if __name__ == "__main__":
    main()
