#!/bin/sh
# ======================================================================
#  Builds an AppImage from the PyInstaller output:
#    dist/XZN/  ->  release/XZN-Linux-x86_64_v<ver>.AppImage
#  Usage: sh installer/build_appimage.sh <version>
#  (normally called by build_unix.sh, right after the .deb)
#
#  An AppImage is one file that runs on most Linux distributions:
#    chmod +x XZN-Linux-x86_64_v<ver>.AppImage && ./XZN-Linux-...AppImage
#  appimagetool is downloaded once into build/ if it isn't installed.
# ======================================================================
set -e
VER="$1"
[ -n "$VER" ] || { echo "Usage: $0 <version>"; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARCH="$(uname -m)"
case "$ARCH" in aarch64|arm64) FARCH=arm_64 ;; *) FARCH="$ARCH" ;; esac

if [ ! -x "$ROOT/dist/XZN/XZN" ]; then
    echo "Skipping AppImage: dist/XZN/XZN not found (build it first)."
    exit 0
fi

# --- appimagetool ---------------------------------------------------------
TOOL="$(command -v appimagetool || true)"
if [ -z "$TOOL" ]; then
    TOOL="$ROOT/build/appimagetool-$ARCH.AppImage"
    if [ ! -x "$TOOL" ]; then
        echo "Downloading appimagetool ..."
        mkdir -p "$ROOT/build"
        curl -fL -o "$TOOL" \
            "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
        chmod +x "$TOOL"
    fi
fi

# --- AppDir ---------------------------------------------------------------
APPDIR="$ROOT/build/appimage/XZN.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib/xzn"
cp -r "$ROOT/dist/XZN/." "$APPDIR/usr/lib/xzn/"

cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/xzn/XZN" "$@"
APPRUN
chmod 0755 "$APPDIR/AppRun"

cat > "$APPDIR/xzn.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=XZN
Comment=Streamlined media player with a cyberpunk look
Exec=XZN %F
Icon=xzn
Terminal=false
Categories=AudioVideo;Audio;Video;Player;
StartupWMClass=XZN
X-AppImage-Version=$VER
DESKTOP

# Icon: menus expect a standard size, so scale xzn.png to 256 x 256
if python3 -c "import PIL" 2>/dev/null; then
    python3 - "$ROOT/xzn.png" "$APPDIR/xzn.png" <<'PY'
import sys
from PIL import Image
Image.open(sys.argv[1]).convert("RGBA").resize((256, 256), Image.Resampling.LANCZOS).save(sys.argv[2])
PY
else
    cp "$ROOT/xzn.png" "$APPDIR/xzn.png"
fi
ln -sf xzn.png "$APPDIR/.DirIcon"

# --- Build ----------------------------------------------------------------
mkdir -p "$ROOT/release"
OUT="$ROOT/release/XZN-Linux-${FARCH}_v${VER}.AppImage"
rm -f "$OUT"
# APPIMAGE_EXTRACT_AND_RUN: run appimagetool without FUSE (works in
# containers and on systems without libfuse2)
APPIMAGE_EXTRACT_AND_RUN=1 ARCH="$ARCH" "$TOOL" --no-appstream "$APPDIR" "$OUT"
chmod +x "$OUT"
echo "Built: $OUT"
