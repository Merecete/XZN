# Building XZN

One script per platform builds XZN and packages it. Everything ends up
in `release/`, already named for the GitHub release.

| Platform | Command | Result |
|---|---|---|
| Windows | `installer\build_windows.bat` | `XZN_Setup_v<ver>.exe` |
| Linux | `sh installer/build_unix.sh` | `XZN-Linux-x86_64_v<ver>.deb`, `XZN-Linux-x86_64_v<ver>.tar.xz` |
| macOS | `sh installer/build_unix.sh` | `XZN-macOS-arm_64_v<ver>.zip` (contains the .dmg) |

All build files live in this folder: the PyInstaller spec (`xzn.spec`),
the Inno Setup script (`xzn.iss`) and the scripts below. Run them from
anywhere; they work from the repository root.

The version number is read from `VERSION = "…"` in `xzn.py` — change it
there, nowhere else. The scripts create their own virtual environment
(`.venv-build/`).

Audio CD support needs no extra tools on any platform: XZN reads CDs
through the operating system and encodes with the FFmpeg libraries
that come with PyAV.

## Windows

- Python 3.10+ from python.org
- [Inno Setup 6](https://jrsoftware.org/isinfo.php) for the setup program
  (without it you still get the program in `dist\`)

## Linux

```
sudo apt install python3-venv python3-tk python3-dev libportaudio2 dpkg-dev
```

Install the result with `sudo apt install ./release/XZN-Linux-x86_64_v<ver>.deb`
(apt pulls in PortAudio). The package installs to `/opt/xzn`, adds a
menu entry, "Open with" support for audio/video files and the command
`xzn`. Remove with `sudo apt remove xzn`.

Reading audio CDs needs access to the drive (`/dev/sr0`); desktop
sessions normally grant that, otherwise add the user to the `cdrom` group.

Build on the oldest distribution you want to support — a build made on a
new system may not start on older ones (glibc).

## macOS

- Python 3.10+ from python.org (Homebrew's Python lacks Tk by default)
- Xcode command line tools: `xcode-select --install`

The DMG opens the classic install window: drag the app onto
*Applications*. Run the script from a normal desktop session so Finder
can arrange that window. The build is for the Mac's own architecture
(Apple Silicon → arm_64).

The app is not notarized, so on first start macOS warns about an
unidentified developer: right-click the app → **Open** once.
