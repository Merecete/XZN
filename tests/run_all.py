"""Runs every test in this folder and prints a summary.

    python tests/run_all.py

No audio device, display, CD drive or network is needed: audio, Tk and
PyAV are replaced by fakes (see harness.py). Needs numpy, scipy, Pillow.
"""
import glob
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
tests = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
failed = []
t0 = time.time()
for path in tests:
    name = os.path.basename(path)
    start = time.time()
    proc = subprocess.run([sys.executable, path], capture_output=True, text=True, cwd=HERE)
    status = "ok" if proc.returncode == 0 else "FAILED"
    print(f"{status:7} {name:32} {time.time() - start:5.1f}s")
    if proc.returncode != 0:
        failed.append(name)
        lines = [l for l in (proc.stdout + proc.stderr).splitlines()
                 if l.startswith(("FAIL", "Traceback", "  File", "    ")) or "Error" in l]
        for line in lines[-25:]:
            print("        " + line)
print(f"\n{len(tests) - len(failed)} of {len(tests)} test files passed in {time.time() - t0:.0f}s")
sys.exit(1 if failed else 0)
