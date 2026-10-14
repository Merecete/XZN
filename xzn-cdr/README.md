## Related tools

- **[XZN-CDR](./xzn-cdr)** — a companion CD ripper that converts audio CDs
  to MP3 / OGG / FLAC / WAV. Same look and feel, cross-platform, with a
  prebuilt Windows binary available in the [releases](../../releases).

## Screenshots

![Main window](main.png)

## Installation

```bash
git clone https://github.com/Merecete/Xenon-Zones.git
cd Xenon-Zones/xzn-cdr
python -m venv .venv

# Windows:
.venv\Scripts\activate

# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt

### Linux (Debian/Ubuntu)

\`\`\`bash
sudo apt install python3-venv python3-tk libportaudio2 \
                 pkg-config swig ffmpeg \
                 libcdio-dev libiso9660-dev \
                 libcdio-paranoia-dev libcdio-cdda-dev
\`\`\`

**macOS:** use `xzn_cdr_mac.py` (requires `brew install cdparanoia ffmpeg`)
