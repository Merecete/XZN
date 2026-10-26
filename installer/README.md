# Building XZN and XZN-CDR

One script per platform builds **both** programs and packages them.
Everything ends up in `release/`.

| Platform | Command | Result |
|---|---|---|
| Windows | `installer\build_windows.bat` | `XZN_Setup_v<ver>.exe`, `XZN-CDR_Setup_v<ver>.exe` |
| Linux (Debian/Ubuntu/Mint) | `sh installer/build_unix.sh` | `xzn_<ver>_amd64.deb`, `xzn-cdr_<ver>_amd64.deb` |
| macOS | `sh installer/build_unix.sh` | `XZN-<ver>-macOS-arm64.dmg`, `XZN-CDR-<ver>-macOS-arm64.dmg` |

The version number is read from `VERSION = "…"` in `xzn.py` and
`xzn-cdr/xzn-cdr.py` — change it there, nowhere else.
The scripts create their own virtual environment (`.venv-build/`).

## Windows

- Python 3.10+ from python.org
- [Inno Setup 6](https://jrsoftware.org/isinfo.php) for the setup programs
  (without it you still get the programs in `dist\`)
- XZN-CDR: put a *full* ffmpeg build (with libcdio) into `xzn-cdr\ffmpeg\`
  (`ffmpeg.exe`, `ffprobe.exe`), e.g. from gyan.dev — it gets bundled.

## Linux

```
sudo apt install python3-venv python3-tk python3-dev libportaudio2 \
                 swig pkg-config libcdio-dev libiso9660-dev dpkg-dev
```

Install the result with `sudo apt install ./release/xzn_<ver>_amd64.deb`
(apt pulls in the dependencies: PortAudio for XZN, ffmpeg for XZN-CDR).
The packages install to `/opt/xzn` and `/opt/xzn-cdr`, add menu entries,
"Open with" support for audio/video files, and the commands `xzn` /
`xzn-cdr`. Remove with `sudo apt remove xzn xzn-cdr`.

Build on the oldest distribution you want to support — a build made on a
new system may not start on older ones (glibc).

## macOS

- Python 3.10+ from python.org (Homebrew's Python lacks Tk by default)
- Xcode command line tools: `xcode-select --install`

The DMG opens the classic install window: drag the app onto
*Applications*. Run the script from a normal desktop session so Finder
can arrange that window. The DMG is built for the Mac's own architecture
(Apple Silicon → arm64).

The apps are not notarized, so on first start macOS warns about an
unidentified developer: right-click the app → **Open** once.
XZN-CDR additionally needs `brew install ffmpeg libcdio-paranoia`
(also explained in the DMG's "READ ME").
