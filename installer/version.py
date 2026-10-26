"""Prints the VERSION of a Python source file (used by the build scripts).

    python installer/version.py xzn.py   ->  1.0.4
"""
import re
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', f.read(), re.M)
if not m:
    sys.exit(f"No VERSION found in {sys.argv[1]}")
print(m.group(1))
