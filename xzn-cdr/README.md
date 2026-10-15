## Related tools

- **[XZN-CDR](./xzn-cdr)** — a companion CD ripper that converts audio CDs
  to MP3 / OGG / FLAC / WAV. Same look and feel, cross-platform, with a
  prebuilt Windows binary available in the [releases](../../releases).

## Screenshots

![Main window](main.png)

## Installation

git clone https://github.com/Merecete/Xenon-Zones.git
cd Xenon-Zones/xzn-cdr
python -m venv .venv

# Windows:
.venv\Scripts\activate

# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt

### Linux (Debian/Ubuntu)

`sudo apt install python3-venv python3-tk libportaudio2 \
                 pkg-config swig ffmpeg \
                 libcdio-dev libiso9660-dev \
                 libcdio-paranoia-dev libcdio-cdda-dev`

**Windows/Linux:** use `xzn-cdr.py`
**macOS:** use `xzn-cdr-mac.py` (requires `brew install cdparanoia ffmpeg`)

### macOS requirements

XZN-CDR on macOS does **not** need a `requirements.txt`. It uses only
Python's standard library plus two external command-line tools:

**Install once via Homebrew:**

`brew install libcdio-paranoia ffmpeg`

If you don't have Homebrew:

`/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`
