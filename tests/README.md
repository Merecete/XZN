# XZN tests

```
pip install numpy scipy pillow
python tests/run_all.py
```

The tests run without audio device, display, CD drive or network.
`harness.py` loads `xzn.py` with stand-ins for PyAV, sounddevice and Tk,
so the player thread, the app logic and the CD code run headless in about
a minute. Settings and log files go to a temporary folder, never to
your real XZN settings.

| File | What it checks |
|---|---|
| `test_playback.py` | Gapless playback, ReplayGain, pause/resume without skipping, start position |
| `test_video_audio.py` | Sound keeps playing with the video window hidden; picture ready instantly |
| `test_app.py` | Resume, next-track logic, playlist search, mini mode, media keys, settings, F1 window, fullscreen controls |
| `test_icons.py` | Button icons, their states and tooltips |
| `test_tool_windows.py` | Info / color / EQ / license windows open once, second click closes |
| `test_cover_art.py` | Album art inside audio files is not treated as video |
| `test_replaygain_and_log.py` | ReplayGain tag parsing, log file |
| `test_cd_core.py` | Loudness measurement (EBU R128), CD tables of contents on Windows / Linux / macOS, MusicBrainz disc IDs, ripping |
| `test_cd_playback.py` | Playing CD tracks (gapless), CD window, playlist entries |
| `test_cd_rip_selection.py` | RIP ALL and RIP SELECTED |
| `test_cd_drive_errors.py` | Reconnecting after a lost drive connection, scratched sectors |
| `test_cd_messages.py` | Messages for an empty drive, data discs and real errors |
| `test_visualizer_and_colors.py` | Visualizer styles (click to cycle, saved) and the custom color picker |
| `test_build_spec.py` | `installer/xzn.spec` bundles all files, scipy and the right version |

What these tests can't cover: real audio output, the look of the
windows, real CD drives and encoding through PyAV / mutagen. Those still
need a quick manual check before a release.
