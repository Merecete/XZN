"""Player core: gapless playback, ReplayGain, pause/resume, start position."""
import os, sys, time, queue
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from harness import xzn, TRACKS, FakeStream, SR, T

for p in ("A", "B", "C", "S"):
    open(T(f"xzn_{p}"), "w").close()   # _prepare_gapless checks isfile
TRACKS.update({
    T("xzn_A"): {"seconds": 2.0, "value": 0.10},
    T("xzn_B"): {"seconds": 1.0, "value": 0.20,
                   "metadata": {"REPLAYGAIN_TRACK_GAIN": "-6.02 dB"}},
    T("xzn_S"): {"seconds": 0.2, "value": 0.30},     # shorter than the ring buffer
    T("xzn_C"): {"seconds": 1.0, "value": 0.40},
    T("xzn_L"): {"seconds": 4.0, "value": 0.50},
})
VOL = 0.85


def run(order, actions=(), timeout=12, replaygain=True):
    FakeStream.instances.clear()
    ev = queue.Queue()
    pl = xzn.Player(ev); pl.start(); pl.set_replaygain(replaygain)
    def provider():   # like the app: "what follows the current track?"
        i = order.index(pl.current_path)
        return order[i + 1] if i + 1 < len(order) else None
    pl.next_provider = provider
    pl.open_file(order[0])
    log = []
    t0 = time.monotonic(); acts = list(actions)
    while time.monotonic() - t0 < timeout:
        while acts and time.monotonic() - t0 >= acts[0][0]:
            getattr(pl, acts.pop(0)[1])()
        try:
            k, v = ev.get(timeout=0.02)
            log.append((round(time.monotonic() - t0, 2), k, v if k != "meta" else v["duration"]))
            if k == "ended":
                break
        except queue.Empty:
            pass
    time.sleep(0.3)
    st = FakeStream.instances[-1]; st.stop()
    out = np.concatenate(st.out)
    pl.cmds.put(("quit",))
    return out, log


def segments(out):
    """Run-length encode the output as (value, nframes)."""
    vals = [round(float(x), 3) for x in out / VOL]
    segs = []
    for v in vals:
        if segs and segs[-1][0] == v:
            segs[-1][1] += 1
        else:
            segs.append([v, 1])
    return [(v, n) for v, n in segs]


def show(name, out, log):
    print(f"\n== {name}")
    for e in log:
        print("   event", e)
    segs = segments(out)
    print("   audio:", [(float(v), n) for v, n in segs])
    return segs


ok = True
def check(cond, msg):
    global ok
    print(("   PASS " if cond else "   FAIL ") + msg)
    ok &= bool(cond)

# 1. Gapless A -> B -> short S -> C, ReplayGain on B
out, log = run([T("xzn_A"), T("xzn_B"), T("xzn_S"), T("xzn_C")])
segs = show("gapless chain", out, log)
body = [s for s in segs if s[0] != 0]
first = next(i for i, s in enumerate(segs) if s[0] != 0)
last = max(i for i, s in enumerate(segs) if s[0] != 0)
check(all(s[0] != 0 for s in segs[first:last + 1]), "no silence between tracks")
check([s[0] for s in body] == [0.1, 0.3, 0.4], f"track order/values {[s[0] for s in body]}")
want = {0.1: int(2.0 * SR), 0.3: int(0.2 * SR), 0.4: int(1.0 * SR)}
got = {}
for v, n in body:
    got[float(v)] = got.get(float(v), 0) + n
check(got.get(0.1) == want[0.1] + int(1.0 * SR), f"A+B(RG -6dB → 0.1) sample count {got.get(0.1)} == {want[0.1] + SR}")
check(got.get(0.3) == want[0.3] and got.get(0.4) == want[0.4], "short track and last track complete")
adv = [e for e in log if e[1] == "advanced"]
check([os.path.basename(e[2]) for e in adv] == ["xzn_B", "xzn_S", "xzn_C"], "advanced events in order")
check(sum(1 for e in log if e[1] == "ended") == 1, "exactly one 'ended' at the very end")
check(sum(1 for e in log if e[1] == "state" and e[2] == "idle") == 1, "no idle flicker between tracks")

# 2. ReplayGain off
out, log = run([T("xzn_B")], replaygain=False, timeout=5)
segs = show("replaygain off", out, log)
check([float(s[0]) for s in segs if s[0] != 0] == [0.2], "B plays at full level without ReplayGain")

# 3. Pause/resume must not skip audio
out, log = run([T("xzn_A")], actions=[(0.8, "pause"), (1.5, "resume")], timeout=8)
segs = show("pause/resume", out, log)
got = sum(n for v, n in segs if v != 0)
check(abs(got - 2 * SR) <= 1024, f"A fully heard across pause: {got} vs {2*SR}")
check(any(e[1] == "state" and e[2] == "paused" for e in log), "pause in the last 1.5 s of the last track works")

# 4. Pause during the gapless hand-over, then resume
out, log = run([T("xzn_A"), T("xzn_C")], actions=[(1.75, "pause"), (2.3, "resume")], timeout=10)
segs = show("pause during hand-over", out, log)
a = sum(n for v, n in segs if v == 0.1); c = sum(n for v, n in segs if v == 0.4)
check(abs(a - 2 * SR) <= 1024, f"A complete ({a})")
check(c == SR, f"C complete ({c})")
check([os.path.basename(e[2]) for e in log if e[1] == "advanced"] == ["xzn_C"], "C still follows gaplessly")

# 5. Resume position at open
out, log = run([T("xzn_A")], timeout=5)
FakeStream.instances.clear()
ev = queue.Queue(); pl = xzn.Player(ev); pl.start()
pl.open_file(T("xzn_L"), 2.5)
time.sleep(2.5); st = FakeStream.instances[-1]; st.stop()
heard = sum(int(np.count_nonzero(b)) for b in st.out)
check(abs(heard - int(1.5 * SR)) <= 1024, f"start_pos=2.5 plays last 1.5 s only ({heard})")

print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
