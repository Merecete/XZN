"""App logic with a fake Tk: resume, gapless provider, search, mini mode,
media keys, settings, F1 window, video window, fullscreen controls."""
import os, sys, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import xzn, TRACKS, T
HOME = T("home"); os.makedirs(HOME, exist_ok=True)
from unittest import mock
xzn.SETTINGS_PATH = os.path.join(HOME, ".xzn_settings.json")
for n, sec in (("one", 200.0), ("two", 100.0), ("three", 50.0)):
    p = os.path.join(HOME, f"{n}.mp3"); open(p, "w").close(); TRACKS[p] = {"seconds": sec, "value": 0.1}
json.dump({"playlist": [os.path.join(HOME, f"{n}.mp3") for n in ("one", "two", "three")],
           "sample_offered": True, "resume": {"path": os.path.join(HOME, "two.mp3"), "pos": 42.0},
           "track_durations": {os.path.join(HOME, "two.mp3"): 100.0}},
          open(xzn.SETTINGS_PATH, "w"))
xzn.ensure_sample_track = lambda: None
app = xzn.App()
ok = True
def check(c, m):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + m)

check(app.current_index == 1 and app._resume_pos == 42.0, "resume point restored (track 2 @ 42 s)")
st = app.status_left.config.call_args_list[-1]
check("00:42" in str(st), f"status hint: {st.kwargs.get('text')}")
# ▶ continues at 42 s
with mock.patch.object(app.player, "open_file") as of:
    app.toggle_play()
    check(of.call_args.args == (os.path.join(HOME, "two.mp3"), 42.0), f"toggle_play → open_file{of.call_args.args}")
check(app._resume_pos == 0.0, "resume consumed")

# gapless provider / advanced
app.current_index = 0
check(app._provide_next() == os.path.join(HOME, "two.mp3"), "next = track 2")
app._on_advanced(os.path.join(HOME, "two.mp3"))
check(app.current_index == 1, "advanced → index 1")
app.current_index = 2; app.repeat_mode = 0
check(app._provide_next() is None, "end of playlist → no next")
app.repeat_mode = 2
check(app._provide_next() == os.path.join(HOME, "one.mp3"), "repeat all wraps")
app.repeat_mode = 1
check(app._provide_next() == os.path.join(HOME, "three.mp3"), "repeat one repeats")
app.repeat_mode = 0

# search
app._search_var = None
app.root.winfo_pointerxy.return_value = (10, 10)
app.root.winfo_width.return_value = 760
app._main_frame.winfo_reqheight.return_value = 160
app._main_frame.winfo_reqwidth.return_value = 640
app.root.winfo_containing.return_value = app.listbox
ev = mock.Mock(char="t", state=0)
check(app._on_root_key(ev) == "break", "typing over playlist opens search")
app._search_var = mock.Mock(); app._search_var.get.return_value = "thr"
app._search_entry = mock.Mock()
app.listbox.selection_set.reset_mock()
app._search_update(start=0)
check(app.listbox.selection_set.call_args.args == (2,), f"'thr' selects 'three' {app.listbox.selection_set.call_args}")
app._search_var.get.return_value = "e"   # one, three
app.listbox.curselection.return_value = (0,)
app._search_step(+1)
check(app.listbox.selection_set.call_args.args == (2,), "Down jumps to next match")
app.listbox.curselection.return_value = (2,)
with mock.patch.object(app, "play_index") as pi:
    app._search_play()
    check(pi.call_args.args == (2,), "Enter plays the match")
called = []
h = app._shortcut(lambda: called.append(1))
check(h(mock.Mock(char="m")) == "break" and not called, "'m' over playlist → search, not mute")
app.root.winfo_containing.return_value = None
h(mock.Mock(char="m"))
check(called == [1], "'m' elsewhere → shortcut still works")
check(app._on_root_key(mock.Mock(char="x", state=0)) is None, "typing elsewhere does nothing")

# mini mode
app.root.geometry.return_value = "760x780+10+10"
app.toggle_mini(); check(app._mini, "mini on")
check(app.root.geometry.call_args.args == ("640x160",), f"mini size = layout request {app.root.geometry.call_args}")
app.toggle_mini(); check(not app._mini, "mini off")
check(app.root.geometry.call_args.args == ("760x780+10+10",), f"full size restored {app.root.geometry.call_args}")

# media keys dispatch
app.player.state = "playing"
with mock.patch.object(app, "toggle_play") as tp, mock.patch.object(app, "next_track") as nt:
    app._on_media_key("playpause"); app._on_media_key("play"); app._on_media_key("next")
    check(tp.call_count == 1 and nt.call_count == 1, "media keys mapped")

# Level toggle persisted
app.set_replaygain(False)
check(json.load(open(xzn.SETTINGS_PATH))["replaygain"] is False and not app.player.replaygain_enabled, "Level off saved")
app.player.state = "idle"; app.current_index = 0; app._resume_pos = 0
app._persist_settings()
check(json.load(open(xzn.SETTINGS_PATH))["resume"] == {"path": os.path.join(HOME, "one.mp3"), "pos": 0.0}, "resume saved")
# F1 help toggles
app.root.winfo_rootx.return_value = 0; app.root.winfo_rooty.return_value = 0
app.root.winfo_height.return_value = 700
tl = xzn.tk.Toplevel.return_value
tl.winfo_width.return_value = 500; tl.winfo_height.return_value = 400
class W:  # fake Toplevel lifetime
    pass
app.toggle_shortcuts()
check(isinstance(app._help_win, xzn.ShortcutsWindow), "F1 opens shortcut window")
app._help_win.win.winfo_exists.return_value = True
app.toggle_shortcuts()
check(app._help_win is None, "F1 again closes it")
n = sum(len(r) for _, r in xzn.SHORTCUTS)
check(n >= 20, f"{n} entries listed")
# video window hidden → frames keep flowing (drained), no stall
vw = app._video_win
vw.is_alive = lambda: True
app.player.state = "playing"; app.player.has_video = True
vw.win.state.return_value = "iconic"
with mock.patch.object(app.player.vbuf, "pop_due") as pd, mock.patch.object(vw, "update_frame") as uf:
    app._update_video()
    check(pd.called and not uf.called and app.player.video_visible, "minimized: buffer drained, nothing drawn")
vw.win.state.return_value = "normal"
with mock.patch.object(vw, "update_frame") as uf, \
     mock.patch.object(app.player.vbuf, "pop_due", return_value=(1.0, "img")):
    app._update_video()
    check(uf.called, "visible: frame drawn immediately")
app.player.state = "idle"; app.player.has_video = False
# user hides via VIDEO button → next video stays hidden
app._toggle_video_window()
check(app._video_user_hidden is True, "VIDEO button hides and remembers")
vw.show = mock.Mock()
app._on_meta({"duration": 10, "video": ("h264", 640, 360, 25), "audio": None, "format": "mp4"})
check(not vw.show.called, "next video does not pop the window up again")
vw.win.state.return_value = "withdrawn"
app._toggle_video_window()
check(app._video_user_hidden is False and vw.show.called, "VIDEO button shows it again")
# fullscreen OSD
vw = app._video_win
w = vw.win
w.winfo_rooty.return_value = 0; w.winfo_height.return_value = 1080
w.after.return_value = "job1"
vw._fullscreen = False
w.winfo_pointery.return_value = 1050
vw._on_osd_motion()
check(vw._osd is None, "no controls outside fullscreen")
vw._fullscreen = True
w.winfo_pointery.return_value = 500
vw._on_osd_motion()
check(not vw._osd_visible, "mouse in the middle: controls stay hidden")
w.winfo_pointery.return_value = 1040
vw._on_osd_motion()
check(vw._osd is not None and vw._osd_visible, "mouse at bottom edge: controls appear")
check(w.after.call_args.args[0] == 5000, "auto-hide after 5 s")
w.winfo_pointery.return_value = 300
w.after.reset_mock(); vw._on_osd_motion()
check(vw._osd_visible and w.after.call_args.args[0] == 5000, "moving while visible restarts the 5 s timer")
vw._hide_osd()
check(not vw._osd_visible, "timer hides controls")
vw._on_osd_motion(); w.winfo_pointery.return_value = 1040; vw._on_osd_motion()
vw.exit_fullscreen()
check(not vw._fullscreen and not vw._osd_visible, "leaving fullscreen hides controls")
app.btn_play.cget.return_value = "||"
vw._show_osd() if False else None
vw.refresh_osd()
check(vw._osd_play.config.call_args.kwargs.get("text") == "||", "controls mirror play state")
vw._osd_seek.get.return_value = 500
app.player.duration = 200.0
with mock.patch.object(app.player, "seek") as sk:
    vw._on_osd_seek_release()
    check(sk.call_args.args == (100.0,), "seek bar seeks")
vw.apply_palette(app.palette)
check(vw._osd is None, "theme change rebuilds controls")
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
