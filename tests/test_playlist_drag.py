"""Reordering the playlist by dragging rows with the mouse."""
import os, sys
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import xzn

ok = True
def check(c, m):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + m)


class FakeListbox:
    """Just enough of tk.Listbox: rows are 20 px high, no scrolling."""
    ROW = 20
    def __init__(self): self.items, self.sel, self.cursor = [], set(), ""
    def insert(self, i, text):
        i = len(self.items) if i == "end" else i
        self.items.insert(i, text)
    def delete(self, a, b=None):
        if a == 0 and b == "end": self.items.clear(); return
        del self.items[a]
    def nearest(self, y): return max(0, min(len(self.items) - 1, y // self.ROW))
    def curselection(self): return tuple(sorted(self.sel))
    def selection_clear(self, a, b=None): self.sel.clear()
    def selection_set(self, a, b=None): self.sel.add(a)
    def see(self, i): pass
    def activate(self, i): pass
    def config(self, **kw): self.cursor = kw.get("cursor", self.cursor)
    def winfo_height(self): return self.ROW * len(self.items)
    def yview_scroll(self, *a): pass

class Ev:
    def __init__(self, y, state=0): self.y, self.state = y, state

xzn.ensure_sample_track = lambda: None
with mock.patch.object(xzn.Player, "start"):
    app = xzn.App()
names = ["a", "b", "c", "d", "e"]
def reset(sel=()):
    app.playlist[:] = [f"/m/{n}.mp3" for n in names]
    app.listbox = lb = FakeListbox()
    for p in app.playlist: lb.insert("end", app._format_pl_line(p))
    lb.sel = set(sel)
    return lb
order = lambda: [os.path.basename(p)[0] for p in app.playlist]
row_y = lambda r: r * 20 + 5

with mock.patch.object(app, "_persist_settings") as persist:
    lb = reset({1}); app.current_index = 1
    app._pl_drag_press(Ev(row_y(1)))
    r = app._pl_drag_motion(Ev(row_y(3)))
    check(order() == list("acdbe") and r == "break", f"dragging b down two rows: {order()}")
    check(lb.items == [app._format_pl_line(p) for p in app.playlist], "list rows match the playlist")
    check(app.current_index == 3, "playing track keeps playing at its new place")
    check(not persist.called, "settings are not saved on every step")
    app._pl_drag_release(Ev(row_y(3)))
    check(persist.call_count == 1 and lb.cursor == "", "saved once when the mouse is released")

    lb = reset({0}); app.current_index = -1
    app._pl_drag_press(Ev(row_y(0))); app._pl_drag_motion(Ev(row_y(4) + 200)); app._pl_drag_release(Ev(0))
    check(order() == list("bcdea"), f"drag beyond the end stops at the last row: {order()}")

    lb = reset({1, 2})
    r = app._pl_drag_press(Ev(row_y(2)))
    check(r == "break", "pressing on a selected row keeps the multi-selection")
    app._pl_drag_motion(Ev(row_y(0)))
    check(order() == list("bcade") and lb.curselection() == (0, 1), f"two selected rows move together: {order()}")
    app._pl_drag_release(Ev(row_y(0)))

    lb = reset({1, 2})
    app._pl_drag_press(Ev(row_y(2))); app._pl_drag_release(Ev(row_y(2)))
    check(lb.curselection() == (2,) and order() == names, "click without dragging selects just that row")

    lb = reset({1})
    app._pl_drag_press(Ev(row_y(1))); app._pl_drag_motion(Ev(row_y(1) + 2)); app._pl_drag_release(Ev(0))
    check(order() == names, "a tiny mouse wobble does not move anything")

    lb = reset({1})
    check(app._pl_drag_press(Ev(row_y(1), state=0x0001)) is None and app._pl_drag is None,
          "Shift-click still selects ranges, no drag")
print("ALL PASS" if ok else "SOME FAILED")
