#!/bin/sh
# ======================================================================
#  Builds XZN on Linux or macOS and packages it.
#  Run from anywhere:   sh installer/build_unix.sh
#
#  Linux  -> release/XZN-Linux-x86_64_v<ver>.deb    (Debian/Ubuntu)
#            release/XZN-Linux-x86_64_v<ver>.tar.xz (other distributions)
#  macOS  -> release/XZN-macOS-arm_64_v<ver>.zip    (contains the .dmg)
#
#  Needs (Linux, Debian/Ubuntu):
#    sudo apt install python3-venv python3-tk python3-dev libportaudio2 dpkg-dev
#  Needs (macOS): Python 3.10+ from python.org (includes Tk), Xcode
#    command line tools (xcode-select --install)
# ======================================================================
set -e
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
OS="$(uname)"

PY=python3
if ! $PY -c "import tkinter" 2>/dev/null; then
    echo "ERROR: this Python has no Tk (tkinter)."
    [ "$OS" = "Darwin" ] && echo "  Use the Python installer from python.org." \
                         || echo "  sudo apt install python3-tk"
    exit 1
fi

[ -d .venv-build ] || $PY -m venv .venv-build
. .venv-build/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller

VER="$(python installer/version.py xzn.py)"
echo
echo "=== Building XZN $VER ($OS) ==="
pyinstaller --noconfirm --clean installer/xzn.spec
mkdir -p release

if [ "$OS" = "Darwin" ]; then
    sh installer/build_dmg.sh "$VER"
else
    sh installer/build_deb.sh "$VER"
    ARCH="$(uname -m)"
    tar -C dist -cJf "release/XZN-Linux-${ARCH}_v${VER}.tar.xz" XZN
    echo "Built: release/XZN-Linux-${ARCH}_v${VER}.tar.xz"
fi
echo
echo "Done. Packages are in: $ROOT/release/"
