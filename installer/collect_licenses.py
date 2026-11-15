"""Collects the license texts of everything that goes into an XZN package.

    python installer/collect_licenses.py [output folder]

Called by installer/xzn.spec during every build, so the packages always
contain the licenses of exactly the library versions they ship with:

    licenses/THIRD_PARTY_NOTICES.txt   overview (from the repository)
    licenses/VERSIONS.txt              component, version, license
    licenses/LICENSE-XZN.txt           XZN's own license
    licenses/GPL-2.0.txt ...           full GPL / LGPL texts (repository)
    licenses/<package>-<version>/      license files from each package
    licenses/Python, licenses/Tcl-Tk   if found in the build Python
"""
import os
import shutil
import sys
from importlib import metadata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PACKAGES = ["av", "numpy", "scipy", "pillow", "sounddevice", "soundfile", "cffi",
            "mutagen", "tkinterdnd2", "jeepney", "certifi", "lameenc", "pyinstaller"]
NAMES = ("license", "licence", "copying", "notice", "authors")


def _package_licenses(name, out):
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return None
    lic = (dist.metadata.get("License-Expression") or "").strip()
    raw = (dist.metadata.get("License") or "").strip()
    if not lic and raw and "\n" not in raw and len(raw) < 40:
        lic = raw                                  # a name, not the full text
    if not lic:
        classifiers = [c.rsplit("::", 1)[-1].strip() for c in dist.metadata.get_all("Classifier") or []
                       if c.startswith("License ::")]
        lic = ", ".join(classifiers) or "see license files"
    target = os.path.join(out, f"{dist.metadata['Name']}-{dist.version}")
    copied = 0
    for f in dist.files or []:
        base = os.path.basename(str(f)).lower()
        if not base.startswith(NAMES) and "license" not in str(f).lower():
            continue
        src = f.locate()
        if not os.path.isfile(src) or os.path.getsize(src) > 2_000_000:
            continue
        rel = str(f).split(".dist-info/", 1)[-1].replace("licenses/", "", 1)
        dst = os.path.join(target, rel.replace("/", "_") if "/" in rel else rel)
        os.makedirs(target, exist_ok=True)
        shutil.copyfile(src, dst)
        copied += 1
    return dist.metadata["Name"], dist.version, lic, copied


def _copy_first(candidates, dst):
    for c in candidates:
        if c and os.path.isfile(c):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(c, dst)
            return True
    return False


def collect(out):
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    shutil.copyfile(os.path.join(ROOT, "THIRD_PARTY_NOTICES.txt"), os.path.join(out, "THIRD_PARTY_NOTICES.txt"))
    shutil.copyfile(os.path.join(ROOT, "LICENSE.txt"), os.path.join(out, "LICENSE-XZN.txt"))
    texts = os.path.join(ROOT, "licenses")
    for name in sorted(os.listdir(texts)):
        shutil.copyfile(os.path.join(texts, name), os.path.join(out, name))

    rows = [("Python", sys.version.split()[0], "PSF-2.0")]
    base = sys.base_prefix
    _copy_first([os.path.join(base, "LICENSE.txt"), os.path.join(base, "LICENSE"),
                 os.path.join(os.path.dirname(os.__file__), "LICENSE.txt")],
                os.path.join(out, "Python", "LICENSE.txt"))
    try:
        import tkinter
        tcl = tkinter.Tcl()
        tcl_lib = tcl.eval("info library")
        rows.append(("Tcl/Tk", tcl.eval("info patchlevel"), "TCL"))
        _copy_first([os.path.join(tcl_lib, "license.terms")], os.path.join(out, "Tcl-Tk", "license.terms"))
    except Exception:
        pass
    for name in PACKAGES:
        r = _package_licenses(name, out)
        if r:
            rows.append(r[:3])
    with open(os.path.join(out, "VERSIONS.txt"), "w", encoding="utf-8") as f:
        f.write("Components in this XZN package (build-time versions)\n\n")
        for n, v, lic in rows:
            f.write(f"{n:<14}{v:<14}{lic}\n")
        f.write("\nFFmpeg and its codecs are part of PyAV; see THIRD_PARTY_NOTICES.txt.\n")
    print(f"[licenses] {len(rows)} components -> {out}")
    return out


if __name__ == "__main__":
    collect(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build", "licenses"))
