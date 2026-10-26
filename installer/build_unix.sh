#!/bin/sh
# ======================================================================
#  Builds XZN and XZN-CDR on Linux or macOS and packages them.
#  Run from anywhere:   sh installer/build_unix.sh
#
#  Linux  -> release/xzn_<ver>_<arch>.deb, release/xzn-cdr_<ver>_<arch>.deb
#  macOS  -> release/XZN-<ver>-macOS-<arch>.dmg, release/XZN-CDR-...dmg
#
#  Needs (Linux, Debian/Ubuntu):
#    sudo apt install python3-venv python3-tk python3-dev libportaudio2 \
#                     swig pkg-config libcdio-dev libiso9660-dev dpkg-dev
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
# pycdio (Linux CD access) compiles against libcdio; a failure here only
# affects reading CDs in XZN-CDR, so carry on with a clear warning.
if ! pip install -r xzn-cdr/requirements.txt; then
    echo "WARNING: XZN-CDR requirements failed (pycdio needs: swig pkg-config libcdio-dev libiso9660-dev)."
fi

VER="$(python installer/version.py xzn.py)"
CDR_VER="$(python installer/version.py xzn-cdr/xzn-cdr.py)"
echo
echo "=== Building XZN $VER / XZN-CDR $CDR_VER ($OS) ==="

pyinstaller --noconfirm --clean installer/xzn.spec
pyinstaller --noconfirm --clean installer/xzn-cdr.spec

if [ "$OS" = "Darwin" ]; then
    sh installer/build_dmg.sh "$VER" "$CDR_VER"
else
    sh installer/build_deb.sh "$VER" "$CDR_VER"
fi
echo
echo "Done. Packages are in: $ROOT/release/"
