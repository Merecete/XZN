"""CD window messages: empty drive, data disc, real errors."""
import os, sys, time, tempfile
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import xzn, T
ok = True
def check(c, m):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + m)
xzn.SETTINGS_PATH = T("s.json"); xzn.ensure_sample_track = lambda: None
with mock.patch.object(xzn.Player, "start"):
    app = xzn.App()
tl = xzn.tk.Toplevel.return_value; tl.winfo_width.return_value = 400; tl.winfo_height.return_value = 300
for a in ("winfo_rootx", "winfo_rooty"): getattr(app.root, a).return_value = 0
app.root.winfo_width.return_value = 640; app.root.winfo_height.return_value = 700
class Var:
    def __init__(self): self.v = ""
    def get(self): return self.v
    def set(self, v): self.v = v
def run(drive):
    with mock.patch.object(xzn, "list_cd_drives", return_value=[drive]):
        tl.winfo_exists.return_value = 0
        app._cd_win = None
        app.show_cd(); cw = app._cd_win; cw.drive_var = Var()
        cw.status.config.reset_mock()
        cw.scan(); time.sleep(0.3); cw.win.winfo_exists.return_value = True; cw._poll()
        k = cw.status.config.call_args.kwargs
        return k["text"], k["fg"] == app.palette["ERR"]
for name, exc in (("Linux", OSError(123, "No medium found")), ("Windows", OSError(21, "not ready")),
                  ("Windows/empty TOC", RuntimeError("No audio CD in the drive."))):
    d = mock.Mock(label="/dev/sr0" if name == "Linux" else "G:", spec=["label", "read_toc", "id"])
    d.read_toc.side_effect = exc
    text, red = run(d)
    check(text.startswith("No CD in drive") and not red, f"{name}: '{text}' (not red)")
d = mock.Mock(label="G:", spec=["label", "read_toc", "id"]); d.read_toc.return_value = None
text, red = run(d)
check("no audio tracks" in text and not red, f"data disc: '{text}'")
d = mock.Mock(label="G:", spec=["label", "read_toc", "id"]); d.read_toc.side_effect = OSError(5, "I/O error")
text, red = run(d)
check(text.startswith("Could not read the CD") and red, f"real error stays red: '{text}'")
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
