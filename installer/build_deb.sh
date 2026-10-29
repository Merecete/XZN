#!/bin/sh
# ======================================================================
#  Builds the .deb package from the PyInstaller output:
#    dist/XZN/  ->  release/XZN-Linux-x86_64_v<ver>.deb
#  Usage: sh installer/build_deb.sh <version> [arch]
#  (normally called by build_unix.sh)
#
#  Installs to /opt/xzn, with a menu entry, an icon and the command
#  'xzn'. Remove with: sudo apt remove xzn
# ======================================================================
set -e
VER="$1"
ARCH="${2:-$(dpkg --print-architecture 2>/dev/null || echo amd64)}"
[ -n "$VER" ] || { echo "Usage: $0 <version> [arch]"; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/release"

# make_deb <package> <version> <source-dir> <exe> <desktop-name> <comment>
#          <categories> <depends> <mimetypes> <description>
make_deb() {
    PKGNAME="$1"; PVER="$2"; SRC="$3"; EXE="$4"; NAME="$5"; COMMENT="$6"
    CATS="$7"; DEPS="$8"; MIME="$9"; shift 9; DESC="$1"
    if [ ! -x "$SRC/$EXE" ]; then
        echo "Skipping $PKGNAME: $SRC/$EXE not found (build it first)."
        return 0
    fi
    PKG="$ROOT/build/deb/${PKGNAME}_${PVER}_${ARCH}"
    rm -rf "$PKG"
    mkdir -p "$PKG/DEBIAN" "$PKG/opt/$PKGNAME" "$PKG/usr/bin" \
             "$PKG/usr/share/applications" "$PKG/usr/share/pixmaps" \
             "$PKG/usr/share/doc/$PKGNAME"
    cp -r "$SRC/." "$PKG/opt/$PKGNAME/"
    ln -s "/opt/$PKGNAME/$EXE" "$PKG/usr/bin/$PKGNAME"
    cp "$ROOT/xzn.png" "$PKG/usr/share/pixmaps/$PKGNAME.png"
    cp "$ROOT/LICENSE.txt" "$PKG/usr/share/doc/$PKGNAME/copyright"

    {
        echo "[Desktop Entry]"
        echo "Type=Application"
        echo "Name=$NAME"
        echo "Comment=$COMMENT"
        echo "Exec=/opt/$PKGNAME/$EXE %F"
        echo "Icon=$PKGNAME"
        echo "Terminal=false"
        echo "Categories=$CATS"
        [ -n "$MIME" ] && echo "MimeType=$MIME"
        echo "StartupWMClass=$EXE"
    } > "$PKG/usr/share/applications/$PKGNAME.desktop"

    # Refresh the menu / "Open with" database after (un)installing.
    for script in postinst postrm; do
        cat > "$PKG/DEBIAN/$script" <<'SCRIPT'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
SCRIPT
        chmod 0755 "$PKG/DEBIAN/$script"
    done

    SIZE="$(du -sk "$PKG/opt" | cut -f1)"
    {
        echo "Package: $PKGNAME"
        echo "Version: $PVER"
        echo "Section: sound"
        echo "Priority: optional"
        echo "Architecture: $ARCH"
        [ -n "$DEPS" ] && echo "Depends: $DEPS"
        echo "Installed-Size: $SIZE"
        echo "Maintainer: Merecete <https://github.com/Merecete>"
        echo "Homepage: https://github.com/Merecete/XZN"
        echo "Description: $DESC"
    } > "$PKG/DEBIAN/control"

    # Directories 0755, files keep their mode (PyInstaller sets +x where needed).
    find "$PKG" -type d -exec chmod 0755 {} +
    case "$ARCH" in amd64) FARCH=x86_64 ;; arm64) FARCH=arm_64 ;; *) FARCH="$ARCH" ;; esac
    OUT="$ROOT/release/XZN-Linux-${FARCH}_v${PVER}.deb"
    dpkg-deb --build --root-owner-group "$PKG" "$OUT"
    echo "Built: $OUT"
}

AUDIO_MIME="audio/mpeg;audio/ogg;audio/vorbis;audio/opus;audio/flac;audio/x-flac;audio/wav;audio/x-wav;audio/mp4;audio/x-m4a;audio/x-m4b;audio/aac;audio/x-aiff;audio/x-ape;audio/x-ms-wma;"
VIDEO_MIME="video/mp4;video/x-matroska;video/x-msvideo;video/quicktime;video/webm;video/x-flv;video/x-m4v;"
LIST_MIME="audio/x-mpegurl;application/vnd.apple.mpegurl;"

make_deb xzn "$VER" "$ROOT/dist/XZN" XZN \
    "XZN" "Streamlined media player with a cyberpunk look" \
    "AudioVideo;Audio;Video;Player;" "libportaudio2" \
    "$AUDIO_MIME$VIDEO_MIME$LIST_MIME" \
    "Streamlined media player with a cyberpunk look
 Audio and video playback, audio CD playback and ripping (MP3, OGG,
 FLAC, WAV with MusicBrainz titles), 10-band equalizer, gapless
 playback, ReplayGain volume leveling, mini player and media keys."
