"""CD window: RIP ALL rips everything, RIP SELECTED only the selection."""
import os, sys, time
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import xzn, T
from cdfakes import FakeDrive
drv = FakeDrive(); xzn._CD_DRIVES["X:"] = drv
xzn.SETTINGS_PATH = T("s.json"); xzn.ensure_sample_track = lambda: None
with mock.patch.object(xzn.Player, "start"):
    app = xzn.App()
tl = xzn.tk.Toplevel.return_value; tl.winfo_width.return_value = 400; tl.winfo_height.return_value = 300
for a in ("winfo_rootx", "winfo_rooty"): getattr(app.root, a).return_value = 0
app.root.winfo_width.return_value = 640; app.root.winfo_height.return_value = 700
ok = True
def check(c, m):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + m)
with mock.patch.object(xzn, "list_cd_drives", return_value=[drv]), mock.patch.object(xzn, "lookup_cd_info", return_value=None):
    tl.winfo_exists.return_value = 0
    app.show_cd(); cw = app._cd_win
    cw.toc = drv.read_toc(); cw.drive = drv
    cw.format_var = mock.Mock(get=lambda: "WAV"); cw.bitrate_var = mock.Mock(get=lambda: "320")
    started = []
    with mock.patch.object(xzn.threading, "Thread") as T:
        cw.listbox.curselection.return_value = ()
        cw.rip(selected_only=True)
        check(not T.called and "Select the tracks" in cw.status.config.call_args.kwargs["text"], "RIP SELECTED without selection asks to select")
        cw.listbox.curselection.return_value = (1,)
        cw.rip(selected_only=True)
        check(T.call_args.kwargs["args"][2] == [2], f"RIP SELECTED rips only the selected track: {T.call_args.kwargs['args'][2]}")
        cw.ripping = False
        cw.rip(selected_only=False)
        check(T.call_args.kwargs["args"][2] == [1, 2], "RIP ALL rips every track")
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
