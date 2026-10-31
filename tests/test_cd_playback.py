"""Playing CDs: virtual WAV streaming, gapless CD tracks, CD window and playlist."""
import os, sys, time, queue, tempfile, threading
from unittest import mock
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import xzn, FakeStream, SR, T
from cdfakes import FakeDrive

ok = True
def check(c, m):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + m)

drv = FakeDrive(); xzn._CD_DRIVES["X:"] = drv
p1, p2 = drv.play_path(1), drv.play_path(2)
ev = queue.Queue()
pl = xzn.Player(ev); pl.start()
order = [p1, p2]
pl.next_provider = lambda: order[order.index(pl.current_path) + 1] if order.index(pl.current_path) + 1 < 2 else None
pl.name_for = lambda p: {p1: "Band - One", p2: "Band - Two"}[p]
pl.open_file(p1)
log = []
t0 = time.monotonic()
while time.monotonic() - t0 < 8:
    try:
        k, v = ev.get(timeout=0.05); log.append((k, v if k != "meta" else round(v["duration"], 2)))
        if k == "ended":
            break
    except queue.Empty:
        pass
time.sleep(0.3)
st = FakeStream.instances[-1]; st.stop()
out = np.round(np.concatenate(st.out) / 0.85, 3)
vals = [float(x) for x in out]
segs = []
for v in vals:
    if segs and segs[-1][0] == v: segs[-1][1] += 1
    else: segs.append([v, 1])
body = [s for s in segs if s[0] != 0]
check(len(body) == 2 and abs(body[0][0] - 8000 / 32768) < 1e-3 and abs(body[1][0] - 16000 / 32768) < 1e-3
      and all(s[0] != 0 for s in segs[segs.index(body[0]):segs.index(body[1]) + 1]), f"both CD tracks played, no gap: {body}")
check(abs(body[0][1] - SR * 2) <= 2 and abs(body[1][1] - int(SR * 1.5)) <= 1100, "full length of each track")
check(("advanced", p2) in log, "gapless hand-over to the next CD track")
check(pl.title_text == "Band - Two", f"player shows CD title: {pl.title_text}")
check(drv.reads > 0, "audio came from the drive")

# ---------------------------------------------------------------- app integration
xzn.SETTINGS_PATH = T("s.json")
xzn.ensure_sample_track = lambda: None
with mock.patch.object(xzn.Player, "start"):
    app = xzn.App()
tl = xzn.tk.Toplevel.return_value
tl.winfo_width.return_value = 400; tl.winfo_height.return_value = 300
app.root.winfo_rootx.return_value = 0; app.root.winfo_rooty.return_value = 0
app.root.winfo_width.return_value = 640; app.root.winfo_height.return_value = 700
with mock.patch.object(xzn, "list_cd_drives", return_value=[drv]), mock.patch.object(xzn, "lookup_cd_info", return_value=None):
    tl.winfo_exists.return_value = 0
    app.show_cd()
    cw = app._cd_win
    check(isinstance(cw, xzn.CdWindow), "CD button opens the CD window")
    class Var:
        def __init__(self, v=""): self.v = v
        def get(self): return self.v
        def set(self, v): self.v = v
    cw.drive_var = Var()
    cw.scan()
    time.sleep(0.3)
    cw.win.winfo_exists.return_value = True
    cw._poll()
    check(cw.toc is not None and sorted(cw.toc["offsets"]) == [1, 2], "CD window reads the disc")
    cw.info = {"artist": "Band", "album": "LP", "tracks": {1: {"title": "One", "artist": "Band"}, 2: {"title": "Two", "artist": "Band"}}}
    cw.listbox.curselection.return_value = (1,)
    with mock.patch.object(app, "play_index") as pi:
        cw.play()
        check(app.playlist[-2:] == [p1, p2] and app._display_names[p2] == "Band - Two"
              and pi.call_args.args == (app.playlist.index(p2),), "PLAY adds the CD to the playlist and starts at the selected track")
    app.player.state = "playing"; app.player.current_path = p2
    with mock.patch.object(app, "stop_play") as sp:
        app.stop_cd_playback(drv)
        check(sp.called, "ripping stops CD playback on the same drive")
    tl.winfo_exists.return_value = 1; tl.state.return_value = "normal"
    cw.ripping = False
    app.show_cd()
    check(tl.destroy.called, "second click closes the CD window")
    cw.ripping = True; tl.withdraw.reset_mock()
    cw.close()
    check(tl.withdraw.called, "closing while ripping only hides it")
    tl.state.return_value = "withdrawn"; tl.deiconify.reset_mock()
    app.show_cd()
    check(tl.deiconify.called, "CD button brings the hidden rip window back")
app._track_durations[p1] = 2.0
app._persist_settings()
import json
saved = json.load(open(xzn.SETTINGS_PATH))
check(not any(k.startswith("cdda://") for k in saved["track_durations"]), "CD durations are not saved")
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
