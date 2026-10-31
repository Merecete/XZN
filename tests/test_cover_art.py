"""Embedded album art is not treated as a video."""
import os, sys, types, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import xzn, T
def vs(name, frames, w=1280, h=720):
    return types.SimpleNamespace(type="video", width=w, height=h, frames=frames, disposition=0,
                                 codec_context=types.SimpleNamespace(name=name))
a = types.SimpleNamespace(type="audio", codec_context=object())
P = xzn.Player._pick_streams
cover = types.SimpleNamespace(streams=[a, vs("mjpeg", 1)])
movie = types.SimpleNamespace(streams=[a, vs("h264", 5000)])
mjpeg_movie = types.SimpleNamespace(streams=[a, vs("mjpeg", 900)])
me = types.SimpleNamespace(_is_cover_art=xzn.Player._is_cover_art)
r1, r2, r3 = P(me, cover), P(me, movie), P(me, mjpeg_movie)
ok = r1[0] is None and r1[1] is a and r2[0] is not None and r3[0] is not None
print("PASS" if r1[0] is None else "FAIL", "MP3 cover art (mjpeg, 1 frame) is not treated as video")
print("PASS" if r2[0] is not None else "FAIL", "real video still detected")
print("PASS" if r3[0] is not None else "FAIL", "real MJPEG video (many frames) still detected")
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
