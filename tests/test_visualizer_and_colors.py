"""Visualizer styles (click cycles, choice is saved) and the custom color
picker in the color scheme window."""
import os, sys, json, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import xzn, T

ok = True
def check(c, m):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + m)

xzn.ensure_sample_track = lambda: None
app = xzn.App()


class FakeCanvas:
    """Records drawing calls (and can render them, see bottom)."""
    def __init__(self, w=600): self.w = w; self.items = []
    def delete(self, *_): self.items = []
    def winfo_width(self): return self.w
    def create_rectangle(self, *a, **k): self.items.append(("rect", a, k))
    def create_line(self, *a, **k): self.items.append(("line", a, k))
    def create_text(self, *a, **k): self.items.append(("text", a, k))


app.anal = FakeCanvas()
t = np.arange(16384) / 44100
music = (0.4 * np.sin(2 * np.pi * 110 * t) + 0.2 * np.sin(2 * np.pi * 880 * t)
         + 0.1 * np.sin(2 * np.pi * 3500 * t)).astype(np.float32)
app.player.spec_buf[:] = music
app.player.state = "playing"
drawn = {}
for i, style in enumerate(xzn.ANALYZER_STYLES):
    app._viz_style = i
    app._update_analyzer()
    drawn[style] = list(app.anal.items)
check(len(drawn["Bars"]) == 32 and len(drawn["Mirror"]) == 32, "Bars / Mirror: one bar per band")
check(len(drawn["LED"]) > 32, "LED: stacked segments")
check(len(drawn["Peaks"]) == 64, "Peaks: bar + falling cap per band")
check(drawn["Wave"][0][0] == "line" and len(drawn["Wave"][0][1]) > 100, "Wave: oscilloscope line")

app._viz_style = 0
app._next_viz()
check(app._viz_style == 1 and json.load(open(xzn.SETTINGS_PATH))["viz_style"] == 1, "click: next style, saved")
app._update_analyzer()
check(any(it[0] == "text" and it[2].get("text") == "LED" for it in app.anal.items), "style name is shown briefly")
for _ in range(len(xzn.ANALYZER_STYLES) - 1):
    app._next_viz()
check(app._viz_style == 0, "cycles back to the first style")

# custom color window
tw = xzn.ThemeWindow.__new__(xzn.ThemeWindow)
import colorsys
tw._colorsys = colorsys
tw._hue, tw._light = 0.74, 0.7
check(tw._custom_color().lower().startswith("#a"), f"hue/shade -> {tw._custom_color()}")
tw._strip_w = 162
ev = type("E", (), {"x": 81})()
check(abs(tw._frac(ev) - 0.5031) < 0.01, "strip position -> fraction")
tw.app = app
picked = []
app.apply_accent = lambda c, persist, rebuild: picked.append(c)
tw.win = app.root
tw._pick(tw._custom_color())
check(picked and picked[0] == tw._custom_color(), "Apply sets the custom color as accent")

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
