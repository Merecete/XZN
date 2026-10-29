#!/bin/sh
# ======================================================================
#  Builds the macOS disk image with the classic install window:
#  the app on the left, an "Applications" shortcut on the right —
#  drag one onto the other to install. The .dmg is zipped for the
#  download:  dist/XZN.app -> release/XZN-macOS-arm_64_v<ver>.zip
#  Usage: sh installer/build_dmg.sh <version>
#  (normally called by build_unix.sh)
# ======================================================================
set -e
VER="$1"
[ -n "$VER" ] || { echo "Usage: $0 <version>"; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARCH="$(uname -m)"
[ "$ARCH" = "arm64" ] && ARCH="arm_64"
mkdir -p "$ROOT/release"

# make_dmg <app-path> <volume-name> <output.dmg> [readme-text]
make_dmg() {
    APP="$1"; VOL="$2"; OUT="$3"; README="$4"
    if [ ! -d "$APP" ]; then
        echo "Skipping $VOL: $APP not found (build it first)."
        return 0
    fi
    APPNAME="$(basename "$APP")"
    STAGE="$ROOT/build/dmg/$VOL"
    TMP_DMG="$ROOT/build/dmg/$VOL-rw.dmg"
    rm -rf "$STAGE" "$TMP_DMG" "$OUT"
    mkdir -p "$STAGE"
    cp -R "$APP" "$STAGE/"
    ln -s /Applications "$STAGE/Applications"
    [ -n "$README" ] && printf '%s\n' "$README" > "$STAGE/READ ME.txt"

    # Writable image first, so the window layout can be stored in it.
    hdiutil create -volname "$VOL" -srcfolder "$STAGE" -ov -fs HFS+ \
            -format UDRW "$TMP_DMG" >/dev/null
    MNT="/Volumes/$VOL"
    [ -d "$MNT" ] && hdiutil detach "$MNT" -quiet || true
    hdiutil attach "$TMP_DMG" -readwrite -noverify -noautoopen -mountpoint "$MNT" >/dev/null

    # Arrange the install window (needs a logged-in desktop session;
    # if Finder can't be scripted, the image still works, just unarranged).
    README_POS=""
    [ -n "$README" ] && README_POS="set position of item \"READ ME.txt\" of container window to {280, 330}"
    osascript <<APPLESCRIPT || echo "Note: could not arrange the DMG window (no Finder access) - image is still fine."
tell application "Finder"
    tell disk "$VOL"
        open
        set current view of container window to icon view
        set toolbar visible of container window to false
        set statusbar visible of container window to false
        set the bounds of container window to {200, 120, 760, 540}
        set viewOptions to the icon view options of container window
        set arrangement of viewOptions to not arranged
        set icon size of viewOptions to 128
        set text size of viewOptions to 13
        set position of item "$APPNAME" of container window to {140, 170}
        set position of item "Applications" of container window to {420, 170}
        $README_POS
        close
        open
        update without registering applications
        delay 1
        close
    end tell
end tell
APPLESCRIPT
    sync
    hdiutil detach "$MNT" -quiet || hdiutil detach "$MNT" -force -quiet
    hdiutil convert "$TMP_DMG" -format UDZO -imagekey zlib-level=9 -o "$OUT" >/dev/null
    rm -f "$TMP_DMG"
    echo "Built: $OUT"
}

make_dmg "$ROOT/dist/XZN.app" "XZN $VER" "$ROOT/build/dmg/XZN.dmg"
ZIP="$ROOT/release/XZN-macOS-${ARCH}_v${VER}.zip"
rm -f "$ZIP"
ditto -c -k "$ROOT/build/dmg/XZN.dmg" "$ZIP"
echo "Built: $ZIP"
