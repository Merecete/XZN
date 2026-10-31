"""Drawn button icons, icon states and tooltips."""
import os, sys, tempfile
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import xzn, T
ok = True
def check(c, m):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + m)
names = ["play", "pause", "stop", "prev", "next", "shuffle", "eq", "volume", "mute", "palette"]
imgs = [xzn.draw_icon(n, 14, "#ece9f5") for n in names]
check(all(i.size == (14, 14) and i.getbbox() for i in imgs), "all 10 icons render (non-empty, 14 px)")
xzn.SETTINGS_PATH = T("s.json"); xzn.ensure_sample_track = lambda: None
with mock.patch.object(xzn.Player, "start"):
    app = xzn.App()
app.toggle_shuffle()
check(app.btn_shuf._xzn_icon == ("shuffle", True), "shuffle on -> accent icon")
app.toggle_shuffle()
check(app.btn_shuf._xzn_icon == ("shuffle", False), "shuffle off -> normal icon")
app.toggle_mute()
check(app.btn_mute._xzn_icon == ("mute", True), "mute -> crossed speaker, highlighted")
app._on_state("playing")
check(app.btn_play._xzn_icon == ("pause", False), "playing -> pause icon")
app._on_state("paused")
check(app.btn_play._xzn_icon == ("play", False), "paused -> play icon")
# tooltip
lbl = mock.MagicMock()
tip = xzn.Tooltip(lbl, "Equalizer  (E)", app.palette)
tip._show()
check(tip._tip is not None, "tooltip appears")
tip._hide()
check(tip._tip is None, "tooltip disappears on leave/click")
# OSD mirrors icons
vw = app._video_win
xzn.tk.Label.side_effect = lambda *a, **k: mock.MagicMock()
app.btn_play = app._mkbtn(mock.MagicMock(), "x", lambda: None, icon="play")
app.btn_shuf = app._mkbtn(mock.MagicMock(), "x", lambda: None, icon="shuffle")
app._on_state("playing")
vw._build_osd()
check(vw._osd_play._xzn_icon == ("pause", False), "fullscreen controls show the same play/pause icon")
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
