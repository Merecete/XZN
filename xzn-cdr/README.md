# Xenon-Zones CD-Ripper (XZN-CDR)

Companion CD ripper that converts audio CDs
to MP3 / OGG / FLAC / WAV, with the same look like XZN.

## Screenshots

![Main window](main.png)

## Features

- Converts CD audio tracks into all common formats
- MP3, OGG, FLAC & WAV
- Bitrates 128, 192, 256, 320

## Requirements

- Python 3.10+
- **Windows:** [ffmpeg (full build)](https://www.gyan.dev/ffmpeg/builds/) with `libcdio` support
- **Linux:** `ffmpeg` and `libcdio` development packages (see Installation below)
- **macOS:** `cd-paranoia` and `ffmpeg` via Homebrew (see Installation below)

## Installation

```bash
git clone https://github.com/Merecete/Xenon-Zones.git
cd Xenon-Zones/xzn-cdr

# Windows:
python -m venv .venv

# Linux/macOS:
python3 -m venv .venv

# Windows:
.venv\Scripts\activate

# Linux/macOS:
source .venv/bin/activate

# Windows/Linux:
pip install -r requirements.txt

# macOS:
does not need requirements.txt. It uses only
Python's standard library plus twp external command-line tools:

Install both once via Homebrew:

brew install libcdio-paranoia ffmpeg

If you don't have Homebrew:

/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
