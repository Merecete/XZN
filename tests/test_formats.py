"""Media formats: playlist import (M3U/PLS/XSPF), CD rip formats and tag
reading/writing for every tag family. Tag tests need mutagen; the wide
format check also needs ffmpeg on PATH (skipped otherwise)."""
import os, sys, wave, shutil, subprocess, tempfile
from unittest import mock
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import xzn

ok = True
def check(c, m):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + m)

d = tempfile.mkdtemp()

# ---------------------------------------------------------------- extensions
for ext in (".aif", ".wv", ".mpc", ".dsf", ".mka", ".vob", ".mts", ".rmvb"):
    check(xzn.is_media_file("x" + ext), f"{ext} is playable")
check(xzn.is_taggable_audio("x.wma") and xzn.is_taggable_audio("x.wv") and not xzn.is_taggable_audio("x.mka"),
      "tag editor offered for WMA / WavPack, not for Matroska audio")
check(xzn.is_playlist_file("x.pls") and xzn.is_playlist_file("x.xspf"), "PLS and XSPF are playlists")

# ---------------------------------------------------------------- playlists
music = os.path.join(d, "Mu sik"); os.makedirs(music)
a, b = os.path.join(music, "a b.mp3"), os.path.join(music, "ü.flac")
for p in (a, b):
    open(p, "wb").close()
with open(os.path.join(d, "t.pls"), "w", encoding="utf-8") as f:
    f.write(f"[playlist]\nFile1=Mu sik/a b.mp3\nTitle1=A\nFILE2={b}\nFile3=http://radio.example/x\nNumberOfEntries=3\n")
check(xzn.read_m3u(os.path.join(d, "t.pls")) == ([a, b], 1), "PLS: relative + absolute paths, web stream skipped")
uri = "file://" + a.replace(" ", "%20") if os.name != "nt" else "file:///" + a.replace("\\", "/").replace(" ", "%20")
with open(os.path.join(d, "t.xspf"), "w", encoding="utf-8") as f:
    f.write('<?xml version="1.0" encoding="UTF-8"?><playlist version="1" xmlns="http://xspf.org/ns/0/">'
            f'<trackList><track><location>{uri}</location></track>'
            '<track><location>Mu%20sik/%C3%BC.flac</location></track>'
            '<track><location>https://x.example/a.mp3</location></track></trackList></playlist>')
check(xzn.read_m3u(os.path.join(d, "t.xspf"))[0] == [a, b], "XSPF: file URIs and relative locations")
with open(os.path.join(d, "bad.xspf"), "w") as f:
    f.write("<playlist><trackList>")
check(xzn.read_m3u(os.path.join(d, "bad.xspf")) == ([], 0), "broken XSPF is ignored")

# ---------------------------------------------------------------- CD formats
with mock.patch.object(xzn.av, "codecs_available",
                       {"libmp3lame", "libvorbis", "libopus", "aac", "flac", "alac", "pcm_s16be", "wavpack"},
                       create=True):
    check(xzn.cd_encoders() == set(xzn.CD_FORMATS), "all nine rip formats offered when FFmpeg has the encoders")
with mock.patch.object(xzn.av, "codecs_available", {"flac"}, create=True):
    check(xzn.cd_encoders() >= {"WAV", "FLAC"} and "OPUS" not in xzn.cd_encoders(), "missing encoders are hidden")
check({f: e for f, (_c, e) in xzn.CD_FORMAT_INFO.items()} ==
      {"MP3": ".mp3", "OGG": ".ogg", "OPUS": ".opus", "AAC": ".m4a", "FLAC": ".flac",
       "ALAC": ".m4a", "WAV": ".wav", "AIFF": ".aiff", "WV": ".wv"}, "file extensions per rip format")

class FakeOut:
    def __init__(self):
        self.streams = []
    def add_stream(self, codec, rate):
        st = mock.MagicMock(); st.codec_context = mock.MagicMock()
        self.streams.append((codec, rate, st)); return st
with mock.patch.object(xzn.av, "open", lambda *a, **k: out, create=True), \
     mock.patch.object(xzn.av, "codecs_available", {"libopus"}, create=True):
    out = FakeOut()
    xzn.TrackEncoder(os.path.join(d, "x.opus"), "OPUS", "192")
    codec, rate, st = out.streams[0]
    check(codec == "libopus" and rate == 48000 and st.codec_context.bit_rate == 192000,
          "Opus is encoded at 48 kHz with the chosen bitrate")
    out = FakeOut()
    xzn.TrackEncoder(os.path.join(d, "x.m4a"), "ALAC", "192")
    check(out.streams[0][:2] == ("alac", 44100), "ALAC keeps 44.1 kHz")

# ---------------------------------------------------------------- list + mouse wheel
class Var:
    def __init__(self, v): self.v = v
    def get(self): return self.v
    def set(self, v): self.v = v
pal = xzn.build_palette("#b48cff")
seen = []
fv = Var("MP3")
cb = xzn.FlatOptionMenu(xzn.tk.Frame(), fv, ["MP3", "OPUS", "FLAC"], pal, on_change=seen.append, wheel=True)
cb.step(1); check(fv.get() == "OPUS", "wheel down: next format")
cb.step(1); cb.step(1); check(fv.get() == "MP3", "wraps around at the end")
cb.step(-1); check(fv.get() == "FLAC" and seen[-1] == "FLAC", "wheel up: previous, change reported")
fv.set("OGG"); cb.step(1); check(fv.get() == "MP3", "unknown saved value: starts at the first entry")
cb._popup = object(); cb.step(1)
check(fv.get() == "MP3", "wheel does nothing while the list is open")
cb._popup = None
cb.set_enabled(False)
check(cb.btn.config.call_args.kwargs["fg"] == pal["GRAY"], "bitrate grays out for lossless formats")
check(xzn.CD_LOSSY == {"MP3", "OGG", "OPUS", "AAC"} and set(xzn.CD_FORMAT_TIPS) == set(xzn.CD_FORMATS),
      "every format has a tooltip; bitrate only for lossy ones")

# ---------------------------------------------------------------- tags
if not xzn._MUTAGEN_AVAILABLE:
    print("SKIP tag tests (mutagen not installed)")
else:
    want = {"title": "Tïtel", "artist": "A1", "album": "B1", "genre": "Rock", "date": "2020", "tracknumber": "3"}
    cd_tags = {"title": "Back: In Black", "artist": "AC/DC", "album": "Live", "albumartist": "AC/DC",
               "date": "1992", "tracknumber": "2/6", "replaygain_track_gain": "-7.25 dB",
               "replaygain_track_peak": "0.988000"}

    def make_wav(path):
        with wave.open(path, "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100)
            w.writeframes((np.sin(np.arange(44100) / 10) * 8000).astype("<i2").repeat(2).tobytes())

    wav = os.path.join(d, "t.wav"); make_wav(wav)
    r = xzn.write_tags(wav, want)
    check(r[0] and xzn.read_tags(wav) == want, "WAV: tag editor really saves (was silently ignored before)")
    xzn.write_tags(wav, dict(want, genre=""))
    check(xzn.read_tags(wav)["genre"] == "" and xzn.read_tags(wav)["title"] == "Tïtel", "empty field removes the tag")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("SKIP wide format check (ffmpeg not on PATH)")
    else:
        files = {"mp3": "libmp3lame", "ogg": "libvorbis", "opus": "libopus", "flac": "flac",
                 "m4a": "aac", "aiff": "pcm_s16be", "aif": "pcm_s16be", "wv": "wavpack",
                 "tta": "tta", "wma": "wmav2", "aac": "aac", "mp2": "mp2"}
        for ext, codec in files.items():
            p = os.path.join(d, "f." + ext)
            fmt = ["-f", "aiff"] if ext == "aif" else []
            if subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-i", wav, "-map_metadata", "-1",
                               "-c:a", codec] + fmt + [p]).returncode:
                print(f"SKIP .{ext} (no {codec} encoder in this ffmpeg)")
                continue
            okw = xzn.write_tags(p, want)[0]
            check(okw and xzn.read_tags(p) == want, f".{ext}: tags written and read back")
        rips = {"MP3": "mp3", "OGG": "ogg", "OPUS": "opus", "AAC": "m4a", "FLAC": "flac",
                "WAV": "wav", "AIFF": "aiff", "WV": "wv"}
        for fmt, ext in rips.items():
            p = os.path.join(d, "f." + ext)
            if not os.path.exists(p):
                continue
            xzn.write_cd_tags(p, fmt, cd_tags)
            rg = xzn.read_replaygain(p)
            check(xzn.read_tags(p)["title"] == "Back: In Black" and rg and abs(rg[0] + 7.25) < 0.01,
                  f"rip {fmt}: tags + ReplayGain {rg}")
print("ALL PASS" if ok else "SOME FAILED")
