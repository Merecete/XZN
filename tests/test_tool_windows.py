"""Info, color scheme, equalizer and license windows open once; a second
click closes them (like the video window)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import xzn

ok = True
def check(c, m):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + m)

xzn.ensure_sample_track = lambda: None
app = xzn.App()
tl = xzn.tk.Toplevel.return_value
tl.winfo_width.return_value = 400; tl.winfo_height.return_value = 300
app.root.winfo_rootx.return_value = 0; app.root.winfo_rooty.return_value = 0
app.root.winfo_width.return_value = 640; app.root.winfo_height.return_value = 700

for name, fn in (("Info", app.show_info), ("Color scheme", app.show_theme), ("Equalizer", app.show_equalizer)):
    n0 = xzn.tk.Toplevel.call_count; tl.destroy.reset_mock()
    tl.winfo_exists.return_value = 0
    fn()
    opened = xzn.tk.Toplevel.call_count > n0
    tl.winfo_exists.return_value = 1
    n1 = xzn.tk.Toplevel.call_count
    fn()
    check(opened and xzn.tk.Toplevel.call_count == n1 and tl.destroy.called,
          f"{name}: opens once, second click closes")

tl.winfo_exists.return_value = 0
xzn.show_license_window(app.root, app.palette, app)
check(app._license_win is not None, "license window remembered")
tl.winfo_exists.return_value = 1; n = xzn.tk.Toplevel.call_count; tl.destroy.reset_mock()
xzn.show_license_window(app.root, app.palette, app)
check(xzn.tk.Toplevel.call_count == n and tl.destroy.called, "License: second click closes")

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
