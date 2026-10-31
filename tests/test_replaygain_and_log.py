"""ReplayGain tag parsing and the log file."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import xzn, T

ok = True
def check(c, m):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + m)

rg = xzn.read_replaygain
cases = [
    ({"REPLAYGAIN_TRACK_GAIN": "-7.89 dB", "REPLAYGAIN_TRACK_PEAK": "0.988"}, (-7.89, 0.988)),
    ({"replaygain_album_gain": "+2.10 dB"}, (2.1, None)),
    ({"R128_TRACK_GAIN": "-512"}, (3.0, None)),
    ({"replaygain_track_gain": "-3,5 dB"}, (-3.5, None)),
    ({"title": "x"}, None),
]
for md, want in cases:
    got = rg(T("nonexistent.mp3"), md)
    check(got == want, f"ReplayGain {md} -> {got}")
f = xzn.replaygain_factor
check(abs(f((6.0, 0.9)) - 1 / 0.9) < 1e-9, "peak limits +6 dB")
check(abs(f((-6.0206, None)) - 0.5) < 1e-4, "-6 dB = half amplitude")

# log file: output is mirrored, swallowed errors are logged once
log_dir = T("logs"); os.makedirs(log_dir, exist_ok=True)
real_out, real_err = sys.stdout, sys.stderr
path = xzn.setup_logging(log_dir)
print("[XZN] hello log")
try:
    {}["missing"]
except Exception:
    xzn._log_swallowed()
    xzn._log_swallowed()
sys.stdout.flush()
sys.stdout, sys.stderr = real_out, real_err
txt = open(path, encoding="utf-8").read()
check("hello log" in txt, "log file receives output")
check(txt.count("(ignored) KeyError") == 1, "swallowed error logged exactly once")

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
