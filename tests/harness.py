"""Test harness: loads ../xzn.py with fake av / sounddevice / tkinter, so
the player and app logic can be tested without audio device, display,
CD drive or network. Settings and logs go to a temporary folder."""
import os, sys, types, threading, time, queue, tempfile

# Isolate from the real user: settings, logs and the sample track go here.
TMP = tempfile.mkdtemp(prefix="xzn-tests-")
os.environ["HOME"] = TMP
os.environ["XDG_DATA_HOME"] = TMP
os.environ["APPDATA"] = TMP


def T(name):
    """Path of a scratch file inside the test folder."""
    return os.path.join(TMP, name)
from fractions import Fraction
from unittest import mock
import numpy as np

SR = 44100

# ---------------- fake tkinter --------------------------------------------
for name in ("tkinter", "tkinter.font", "tkinter.filedialog", "tkinter.messagebox", "PIL.ImageTk"):
    sys.modules[name] = mock.MagicMock()
sys.modules["tkinter"].font = sys.modules["tkinter.font"]

# ---------------- fake sounddevice ---------------------------------------
class FakeStream:
    instances = []
    def __init__(self, samplerate, channels, dtype, callback, blocksize, latency):
        self.cb = callback; self.bs = blocksize; self.latency = 0.1
        self.out = []; self.running = False
        FakeStream.instances.append(self)
    def start(self):
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()
    def _loop(self):
        t = time.monotonic(); dt = self.bs / SR
        while self.running:
            buf = np.zeros((self.bs, 2), dtype=np.float32)
            self.cb(buf, self.bs, None, None)
            self.out.append(buf[:, 0].copy())
            t += dt
            d = t - time.monotonic()
            if d > 0: time.sleep(d)
    def stop(self): self.running = False
    def close(self): pass
sd = types.ModuleType("sounddevice"); sd.OutputStream = FakeStream
sys.modules["sounddevice"] = sd

# ---------------- fake av --------------------------------------------------
TRACKS = {}   # path -> dict(seconds, value, metadata, video)

class CC:
    def __init__(self): self.name = "pcm"; self.sample_rate = SR; self.channels = 2
class AStream:
    type = "audio"
    def __init__(self, n, md):
        self.index = 0; self.time_base = Fraction(1, SR); self.duration = n
        self.rate = SR; self.channels = 2; self.codec_context = CC(); self.metadata = md
class Frame:
    def __init__(self, pts, data): self.pts = pts; self.data = data; self.samples = data.shape[1]
    def to_ndarray(self): return self.data
class Packet:
    def __init__(self, stream, pts, data): self.stream = stream; self.pts = pts; self._d = data
    def decode(self): return [Frame(self.pts, self._d)]
class Fmt: name = "fake"
class Container:
    def __init__(self, path):
        if path not in TRACKS: raise FileNotFoundError(path)
        t = TRACKS[path]
        self.n = int(t["seconds"] * SR); self.value = t["value"]
        self.metadata = dict(t.get("metadata", {}))
        self.streams = [AStream(self.n, {})]
        self.duration = int(t["seconds"] * 1_000_000); self.format = Fmt(); self.pos = 0
    def demux(self):
        s = self.streams[0]
        while self.pos < self.n:
            k = min(1152, self.n - self.pos)
            # sample values encode track value + position, for continuity checks
            d = np.full((2, k), self.value, dtype=np.float32)
            p = self.pos; self.pos += k
            yield Packet(s, p, d)
    def seek(self, ts, any_frame=False, backward=True):
        self.pos = max(0, min(self.n, int(ts / 1_000_000 * SR)))
    def close(self): pass
class Resampler:
    def __init__(self, **kw): pass
    def resample(self, f): return [] if f is None else [f]
av = types.ModuleType("av"); av.open = Container; av.AudioResampler = Resampler
av.time_base = 1_000_000; av.__version__ = "fake"
sys.modules["av"] = av

import importlib.util
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("xzn", os.path.join(REPO, "xzn.py"))
xzn = importlib.util.module_from_spec(spec); spec.loader.exec_module(xzn)
xzn.SETTINGS_PATH = T("xzn_settings.json")
