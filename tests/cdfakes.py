"""Shared fakes for CD tests: a fake CD drive and an av.open() that
decodes the virtual WAV files XZN builds for CD tracks."""
import os, sys, wave
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import xzn, SR

# fake av.open that decodes WAV file objects (like PyAV would)
real_open = harness.av.open
class WavContainer(harness.Container):
    def __init__(self, f):
        with wave.open(f, "rb") as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2)
        self.pcm = pcm.T.astype(np.float32) / 32768.0
        self.n = pcm.shape[0]; self.value = None; self.metadata = {}
        self.streams = [harness.AStream(self.n, {})]
        self.duration = int(self.n / SR * 1e6); self.format = harness.Fmt(); self.pos = 0
    def demux(self):
        s = self.streams[0]
        while self.pos < self.n:
            k = min(1152, self.n - self.pos)
            d = self.pcm[:, self.pos:self.pos + k]; p = self.pos; self.pos += k
            yield harness.Packet(s, p, d)
def fake_open(target, format=None, **kw):
    return WavContainer(target) if hasattr(target, "read") else real_open(target)
harness.av.open = fake_open
xzn.av.open = fake_open

class FakeDrive(xzn._RawCdDrive):
    kind = "fake"
    def __init__(self):
        super().__init__(); self.id = "X:"; self.label = "X:"
        self.reads = 0
        self.pcm = {1: np.full(SR * 2 * 2, 8000, "<i2"), 2: np.full(int(SR * 1.5) * 2, 16000, "<i2")}
        self.disc = self.pcm[1].tobytes() + self.pcm[2].tobytes()
    def _open(self): pass
    def read_toc(self):
        l1 = len(self.pcm[1]) * 2 // 2352; l2 = len(self.pcm[2]) * 2 // 2352
        return xzn._toc_from_entries([(1, 0, False), (2, l1, False)], l1 + l2)
    def _read_raw(self, lsn, n):
        self.reads += 1
        return self.disc[lsn * 2352:(lsn + n) * 2352].ljust(n * 2352, b"\0")

