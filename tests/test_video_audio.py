"""Audio keeps playing while the video window is hidden; picture is ready instantly."""
"""Audio must keep playing when nobody consumes video frames (window hidden)."""
import os, sys, time, queue
from fractions import Fraction
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import FakeStream, SR, T

SECONDS = 6.0
FPS = 25


class VCC:
    name = "h264"
    thread_type = None
    thread_count = None


class VStream:
    type = "video"
    def __init__(self):
        self.index = 1; self.time_base = Fraction(1, FPS); self.width = 320; self.height = 180
        self.codec_context = VCC(); self.average_rate = Fraction(FPS); self.base_rate = None
        self.duration = int(SECONDS * FPS); self.metadata = {}


class VFrame:
    def __init__(self, pts): self.pts = pts; self.width = 320; self.height = 180
    def reformat(self, format, width, height):
        return type("R", (), {"to_ndarray": lambda s: np.zeros((height, width, 3), np.uint8)})()


class VPacket:
    def __init__(self, st, pts): self.stream = st; self.pts = pts
    def decode(self): return [VFrame(self.pts)]


class VContainer(harness.Container):
    def __init__(self, path):
        harness.TRACKS[path] = {"seconds": SECONDS, "value": 0.25}
        super().__init__(path)
        self.streams = [harness.AStream(self.n, {}), VStream()]

    def demux(self):
        a, v = self.streams
        apos, vno = self.pos, int(self.pos / SR * FPS)
        while apos < self.n:
            k = min(1152, self.n - apos)
            yield harness.Packet(a, apos, np.full((2, k), 0.25, np.float32))
            apos += k
            while vno < apos / SR * FPS:
                yield VPacket(v, vno); vno += 1
        self.pos = self.n


harness.av.open = VContainer
xzn = harness.xzn


def heard(visible, consume):
    FakeStream.instances.clear()
    pl = xzn.Player(queue.Queue()); pl.start()
    pl.video_visible = visible
    pl.open_file(T("xzn_video"))
    t0 = time.monotonic()
    while time.monotonic() - t0 < SECONDS + 1.5:
        if consume:
            pl.vbuf.pop_due(pl.position())
        time.sleep(0.016)
    st = FakeStream.instances[-1]; st.stop()
    return sum(int(np.count_nonzero(b)) for b in st.out) / SR, pl.vbuf.size()


bug, _ = heard(visible=True, consume=False)
print(f"old behaviour (window hidden, buffer not emptied): {bug:.2f} s of {SECONDS} s audio")
fixed, size = heard(visible=False, consume=False)
print(f"fixed (player knows window is hidden):            {fixed:.2f} s of {SECONDS} s audio, {size} frames buffered")
shown, _ = heard(visible=True, consume=True)
print(f"window visible (frames consumed):                  {shown:.2f} s of {SECONDS} s audio")
# window hidden but app drains the buffer: is a frame ready the moment it opens?
FakeStream.instances.clear()
pl = xzn.Player(queue.Queue()); pl.start(); pl.open_file(T("xzn_video"))
t0 = time.monotonic()
while time.monotonic() - t0 < 3.0:
    pl.vbuf.pop_due(pl.position()); time.sleep(0.016)
now = pl.position(); nxt = pl.vbuf.latest()
with pl.vbuf.lock:
    first = pl.vbuf.dq[0][0] if pl.vbuf.dq else None
FakeStream.instances[-1].stop(); pl.stop()
ready = first is not None and 0 <= first - now < 0.1
print(f"window opens at {now:.2f}s: next buffered frame at {first if first is None else round(first, 2)}s -> {'instant' if ready else 'delayed'}")
ok = ready and bug < SECONDS - 1 and abs(fixed - SECONDS) < 0.05 and size == 0 and abs(shown - SECONDS) < 0.05
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
