"""installer/xzn.spec: every bundled file exists, scipy is not excluded,
the version comes from xzn.py, macOS gets an app bundle."""
import os, sys, re, types, contextlib, io
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(REPO, "installer", "xzn.spec")

ok = True
def check(c, m):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + m)

# Minimal stand-ins for PyInstaller, which isn't needed to read the spec
hooks = types.ModuleType("PyInstaller.utils.hooks")
hooks.collect_all = lambda pkg: ([], [], [])
for n in ("PyInstaller", "PyInstaller.utils"):
    sys.modules[n] = types.ModuleType(n)
sys.modules["PyInstaller.utils.hooks"] = hooks

def run_spec(platform):
    calls = {}
    def rec(name):
        def f(*a, **k):
            calls[name] = (a, k)
            return types.SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[])
        return f
    ns = {k: rec(k) for k in ("Analysis", "PYZ", "EXE", "COLLECT", "BUNDLE")}
    ns["SPECPATH"] = os.path.dirname(SPEC)
    cwd = os.getcwd()
    os.chdir(os.path.dirname(REPO) or "/")          # must not depend on the cwd
    try:
        with mock.patch.object(sys, "platform", platform), contextlib.redirect_stdout(io.StringIO()):
            exec(compile(open(SPEC, encoding="utf-8").read(), SPEC, "exec"), ns)
    finally:
        os.chdir(cwd)
    return calls, ns

with open(os.path.join(REPO, "xzn.py"), encoding="utf-8") as f:
    version = re.search(r'^VERSION\s*=\s*"([^"]+)"', f.read(), re.M).group(1)

calls, ns = run_spec("linux")
a, k = calls["Analysis"]
paths = list(a[0]) + [d[0] for d in k["datas"]] + [calls["EXE"][1]["icon"]]
missing = [p for p in paths if not os.path.exists(p)]
check(not missing, f"all bundled files exist {missing or ''}")
check("scipy" not in k["excludes"], "scipy is bundled (equalizer, loudness)")
check("certifi" in k["hiddenimports"], "certifi is bundled (MusicBrainz over HTTPS)")
lic = [d for d in k["datas"] if d[1] == "licenses"]
check(lic and os.path.isfile(os.path.join(lic[0][0], "VERSIONS.txt"))
      and all(os.path.isfile(os.path.join(lic[0][0], t)) for t in
              ("THIRD_PARTY_NOTICES.txt", "LICENSE-XZN.txt", "GPL-2.0.txt", "GPL-3.0.txt", "LGPL-2.1.txt", "LGPL-3.0.txt")),
      "license texts and notices are collected into the package")
notices = open(os.path.join(REPO, "THIRD_PARTY_NOTICES.txt"), encoding="utf-8").read().lower()
reqs = [re.split(r"[<>=;\s]", l.strip())[0].lower() for l in open(os.path.join(REPO, "requirements.txt"))
        if l.strip() and not l.startswith("#")]
names = {"av": "pyav", "pillow": "pillow"}
unlisted = [r for r in reqs if names.get(r, r) not in notices]
check(not unlisted, f"every library from requirements.txt is in THIRD_PARTY_NOTICES.txt {unlisted or ''}")
check("bsd 3-clause" in open(os.path.join(REPO, "LICENSE.txt")).read().lower(), "XZN itself is BSD 3-Clause")
check(ns["VERSION"] == version, f"spec version {ns['VERSION']} = xzn.py {version}")
check("BUNDLE" not in calls, "no app bundle on Linux")

calls, ns = run_spec("darwin")
b = calls.get("BUNDLE")
check(b is not None and b[1]["name"] == "XZN.app" and b[1]["info_plist"]["CFBundleShortVersionString"] == version,
      "macOS: XZN.app with the right version")

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
