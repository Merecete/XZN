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

XZN-CDR on macOS does **not** need a `requirements.txt`. It uses only
Python's standard library plus two external command-line tools:

- **`cd-paranoia`** — for reading audio CDs (via `libcdio-paranoia`)
- **`ffmpeg`** — for encoding to MP3 / OGG / FLAC

Install both once via Homebrew:

`brew install libcdio-paranoia ffmpeg`

If you don't have Homebrew:

`/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`

## Installation

```bash
git clone https://github.com/Merecete/Xenon-Zones.git
cd Xenon-Zones/xzn-cdr
python -m venv .venv

# Windows:
.venv\Scripts\activate

# Linux/macOS:
source .venv/bin/activate

Windows and Linux only:
pip install -r requirements.txt

