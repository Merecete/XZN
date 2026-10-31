"""CD: loudness (EBU R128), TOC parsing for all platforms, disc IDs, virtual WAV, ripping."""
import os, sys, io, json, wave, struct, plistlib, tempfile, subprocess, threading, queue
from unittest import mock
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import xzn, T

ok = True
def check(c, m):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + m)

# ---------------------------------------------------------------- loudness
m48 = xzn.LoudnessMeter._k_weighting(48000)
std_shelf = [1.53512485958697, -2.69169618940638, 1.19839281085285, 1, -1.69065929318241, 0.73248077421585]
std_hp_a = [1, -1.99004745483398, 0.99007225036621]
check(np.allclose(m48[0], std_shelf, atol=1e-6) and np.allclose(m48[1][3:], std_hp_a, atol=1e-6),
      "K-weighting at 48 kHz matches ITU-R BS.1770 coefficients")

def pcm_of(x):
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()

t = np.arange(44100 * 10) / 44100
a = 10 ** (-23 / 20)
sine = np.stack([a * np.sin(2 * np.pi * 997 * t)] * 2, axis=1)
lm = xzn.LoudnessMeter()
for i in range(0, len(sine), 44100):
    lm.add(pcm_of(sine[i:i + 44100]))
g, pk = lm.result()
check(abs((-18 - g) - (-23.0)) < 0.1, f"997 Hz stereo sine at -23 dBFS reads {-18 - g:.2f} LUFS (EBU 3341: -23.0)")

# compare with ffmpeg's EBU R128 meter on a music-like signal
rng = np.random.default_rng(1)
noise = rng.standard_normal((44100 * 20, 2)) * 0.1
noise[:, 1] *= 0.6
env = (np.sin(np.arange(len(noise)) / 44100 * 2 * np.pi * 0.3) * 0.5 + 0.6)[:, None]
sig = noise * env
d = tempfile.mkdtemp(); wav = os.path.join(d, "n.wav")
with wave.open(wav, "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100); w.writeframes(pcm_of(sig))
import shutil
if shutil.which("ffmpeg"):
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", wav, "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    ff_I = float(out.rsplit("I:", 1)[1].split("LUFS")[0])
    lm = xzn.LoudnessMeter()
    raw = pcm_of(sig)
    for i in range(0, len(raw), 2352 * 75):
        lm.add(raw[i:i + 2352 * 75])
    mine = -18 - lm.result()[0]
    check(abs(mine - ff_I) < 0.2, f"integrated loudness {mine:.2f} LUFS vs ffmpeg ebur128 {ff_I:.2f} LUFS")
else:
    print("SKIP comparison with ffmpeg's ebur128 meter (ffmpeg not installed)")


def write_aiff(path, pcm_le):
    """Minimal 16-bit stereo 44.1 kHz AIFF writer (like macOS cddafs files)."""
    data = np.frombuffer(pcm_le, "<i2").astype(">i2").tobytes()
    frames = len(data) // 4
    comm = struct.pack(">hIh", 2, frames, 16) + bytes.fromhex("400EAC44000000000000")
    ssnd = struct.pack(">II", 0, 0) + data
    body = b"AIFF" + b"COMM" + struct.pack(">I", len(comm)) + comm + b"SSND" + struct.pack(">I", len(ssnd)) + ssnd
    with open(path, "wb") as f:
        f.write(b"FORM" + struct.pack(">I", len(body)) + body)

# ---------------------------------------------------------------- TOC / disc ID
entries = [(1, 0, False), (2, 15213, False), (3, 32164, False), (4, 46442, False), (5, 63264, False), (6, 80339, False)]
toc = xzn._toc_from_entries(entries, 95312)
check(xzn.musicbrainz_disc_id(toc) == "49HHV7Eb8UKF3aQiNmu1GR8vKTY-", "disc ID from TOC entries (MusicBrainz docs example)")
enh = xzn._toc_from_entries(entries + [(7, 95312 + 11400, True)], 120000)
check(enh["leadout"] == 95312 and enh["lengths"][6] == 95312 - 80339 and 7 not in enh["offsets"],
      "enhanced CD: data track excluded, lead-out = data start - 11400")

def msf(lba):
    f = lba + 150
    return f // 4500, (f // 75) % 60, f % 75

# Windows CDROM_TOC buffer
buf = bytearray(4 + 100 * 8)
buf[2], buf[3] = 1, 7
for i, (n, lba, data) in enumerate(entries + [(7, 95312 + 11400, True)] + [(0xAA, 130000, False)]):
    m_, s_, f_ = msf(lba)
    buf[4 + i * 8: 12 + i * 8] = bytes([0, (0x4 if data else 0) << 4 | 1, n, 0, 0, m_, s_, f_])
wt = xzn.WinCdDrive.parse_toc(bytes(buf))
check(xzn.musicbrainz_disc_id(wt) == "49HHV7Eb8UKF3aQiNmu1GR8vKTY-", "Windows TOC parsing (with data track) -> same disc ID")
check(struct.calcsize("<qII") == 16, "RAW_READ_INFO is 16 bytes")

# Linux ioctl structs + mocked driver
check(struct.calcsize("=BBBxiB3x") == 12, "cdrom_tocentry is 12 bytes")
check(struct.calcsize("@iBiP") == 24, "cdrom_read_audio is 24 bytes (64-bit)")
disc_pcm = (np.arange(95312 * 588 * 2) % 30000).astype("<i2").tobytes()
def fake_ioctl(fd, req, arg, mut=True):
    if req == 0x5305:
        arg[0], arg[1] = 1, 6
    elif req == 0x5306:
        n = arg[0]
        lba = 95312 if n == 0xAA else dict((e[0], e[1]) for e in entries)[n]
        arg[:] = struct.pack("=BBBxiB3x", n, 0x10, 1, lba, 0)
    elif req == 0x530E:
        lsn, fmt, nfr, addr = struct.unpack("@iBiP", bytes(arg))
        data = disc_pcm[lsn * 2352:(lsn + nfr) * 2352]
        import ctypes
        ctypes.memmove(addr, data, len(data))
    return 0
fcntl_mod = type(sys)("fcntl"); fcntl_mod.ioctl = fake_ioctl
with mock.patch.dict(sys.modules, {"fcntl": fcntl_mod}), mock.patch.object(xzn.os, "open", return_value=99):
    ld = xzn.LinuxCdDrive("/dev/sr0")
    lt = ld.read_toc()
    check(xzn.musicbrainz_disc_id(lt) == "49HHV7Eb8UKF3aQiNmu1GR8vKTY-", "Linux TOC via ioctl -> same disc ID")
    got = b"".join(ld.iter_pcm(2, lt))
    s2 = lt["offsets"][2] * 2352
    check(got == disc_pcm[s2:s2 + lt["lengths"][2] * 2352], "Linux CDROMREADAUDIO reads the exact track PCM")

    # ------------------------------------------------------------ virtual WAV for playback
    xzn._CD_DRIVES["/dev/sr0"] = ld
    ld._toc = lt
    path = ld.play_path(3)
    drive, track = xzn._cdda_lookup(path)
    check(path == "cdda:///dev/sr0/3" and drive is ld and track == 3, f"cdda path round trip {path}")
    f = xzn.CdTrackFile(ld, lt["offsets"][3], lt["lengths"][3])
    with wave.open(f, "rb") as w:
        params = (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes())
        w.setpos(44100 * 30)
        chunk = w.readframes(1000)
    s3 = lt["offsets"][3] * 2352 + 44100 * 30 * 4
    check(params == (2, 2, 44100, lt["lengths"][3] * 588) and chunk == disc_pcm[s3:s3 + 4000],
          "CdTrackFile is a valid, seekable WAV with the right audio")
    f.seek(0, 2); check(f.tell() == 44 + lt["lengths"][3] * 2352, "CdTrackFile size")

    # ------------------------------------------------------------ rip to WAV (+ loudness)
    outdir = tempfile.mkdtemp()
    info = {"artist": "AC/DC", "album": "Live", "date": "1992", "disc": None,
            "tracks": {2: {"title": "Back: In Black", "artist": "AC/DC"}}}
    msgs = []
    with mock.patch.object(xzn, "write_cd_tags") as wt_tags:
        xzn.rip_cd(ld, lt, [2, 3], outdir, "WAV", "320", info, lambda *a: msgs.append(a), threading.Event())
    folder = os.path.join(outdir, "AC_DC - Live")
    files = sorted(os.listdir(folder))
    check(files == ["02 - Back_ In Black.wav", "track_03.wav"], f"rip names {files}")
    with wave.open(os.path.join(folder, files[0])) as w:
        data = w.readframes(w.getnframes())
    check(data == disc_pcm[s2:s2 + lt["lengths"][2] * 2352], "ripped WAV is bit-identical to the disc")
    tags = wt_tags.call_args_list[0].args[2]
    check(tags["title"] == "Back: In Black" and tags["tracknumber"] == "2/6"
          and tags["replaygain_track_gain"].endswith("dB"), f"tags passed: {tags}")
    check(msgs[-1] == ("done", 2), "rip reports done")
    # cancel
    ev = threading.Event(); ev.set(); msgs.clear()
    with mock.patch.object(xzn, "write_cd_tags"):
        xzn.rip_cd(ld, lt, [4], outdir, "WAV", "320", None, lambda *a: msgs.append(a), ev)
    check(msgs[-1] == ("stopped", 0) and not os.path.exists(os.path.join(outdir, "track_04.wav")), "STOP removes the unfinished file")

# ---------------------------------------------------------------- macOS cddafs
vol = os.path.join(tempfile.mkdtemp(), "Audio CD")
os.makedirs(vol)
names = {1: "1 Audiotitel.aiff", 2: "2 Audiotitel.aiff", 3: "3 Audiotitel.aiff"}
lens = {1: 75 * 3, 2: 75 * 2, 3: 75 * 4}
off = 0; raw = bytearray(b"\x00\x00\x01\x01")
src = {}
for n in (1, 2, 3):
    pcm = (np.arange(lens[n] * 1176) * n % 20000).astype("<i2").tobytes()
    src[n] = pcm
    write_aiff(os.path.join(vol, names[n]), pcm)
    m_, s_, f_ = msf(off)
    raw += bytes([1, 0x10, 0, n, 0, 0, 0, 0, m_, s_, f_])
    off += lens[n]
m_, s_, f_ = msf(off)
raw += bytes([1, 0x10, 0, 0xA2, 0, 0, 0, 0, m_, s_, f_])
plist = {"Format 0x02 TOC Data": bytes(raw),
         "Sessions": [{"First Track": 1, "Last Track": 3, "Leadout Block": off + 150, "Session Number": 1,
                       "Track Array": [{"Point": n, "Start Block": o + 150, "Data": False}
                                       for n, o in ((1, 0), (2, lens[1]), (3, lens[1] + lens[2]))]}]}
with open(os.path.join(vol, ".TOC.plist"), "wb") as f:
    plistlib.dump(plist, f)
md = xzn.MacCdDrive(vol)
mt = md.read_toc()
check(mt["offsets"] == {1: 0, 2: 225, 3: 375} and mt["leadout"] == 675, f"macOS raw TOC {mt['offsets']} lead-out {mt['leadout']}")
del plist["Format 0x02 TOC Data"]
with open(os.path.join(vol, ".TOC.plist"), "wb") as f:
    plistlib.dump(plist, f)
check(xzn.MacCdDrive(vol).read_toc() == mt, "macOS fallback TOC (Start Block -150) gives the same result")
check(md.play_path(2) == os.path.join(vol, names[2]), "macOS play path is the track's AIFF")
check(b"".join(md.iter_pcm(2, mt)) == src[2], "macOS AIFF -> exact little-endian PCM")
with mock.patch.object(xzn.os, "listdir", side_effect=lambda p: ["Macintosh HD", "Audio CD"] if p == "/Volumes" else os.listdir(p)), \
     mock.patch.object(xzn.os.path, "isfile", side_effect=lambda p: p == "/Volumes/Audio CD/.TOC.plist"):
    found = xzn._list_mac_drives()
check([x.id for x in found] == ["/Volumes/Audio CD"], "macOS detects the mounted audio CD")

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
