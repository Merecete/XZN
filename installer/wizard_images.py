"""Creates the side and corner images for the Windows setup (Inno Setup).

    python installer/wizard_images.py [output folder]

Writes large_<dpi>.bmp (left panel on the first and last page) and
small_<dpi>.bmp (top right on the other pages) from xzn-logo.png, one per
display scaling, so the images stay sharp on high-DPI screens. Called by
build_windows.bat; the output folder defaults to build/wizard.
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGO = os.path.join(ROOT, "xzn-logo.png")

# Sizes Inno Setup 6 uses for WizardStyle=modern at 100 % ... 250 % scaling
DPI = (100, 125, 150, 175, 200, 225, 250)
LARGE = ((164, 314), (192, 386), (246, 459), (273, 556), (328, 604), (355, 700), (410, 797))
SMALL = ((55, 55), (64, 68), (83, 80), (92, 97), (110, 106), (119, 123), (138, 140))

TOP, BOTTOM = (0x1e, 0x1a, 0x30), (0x0a, 0x08, 0x12)   # XZN panel -> sunken
GLOW = (0x92, 0x00, 0xb9)                               # logo purple


def large(size, logo):
    w, h = size
    img = Image.new("RGB", size)
    d = ImageDraw.Draw(img)
    for y in range(h):                                  # dark vertical gradient
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))
    tile = int(w * 0.62)
    x, y = (w - tile) // 2, int(h * 0.40) - tile // 2
    glow = Image.new("L", size, 0)                      # soft accent glow behind the logo
    pad = max(2, tile // 14)
    ImageDraw.Draw(glow).rectangle([x - pad, y - pad, x + tile + pad, y + tile + pad], fill=110)
    glow = glow.filter(ImageFilter.GaussianBlur(tile // 6))
    img = Image.composite(Image.new("RGB", size, GLOW), img, glow)
    img.paste(logo.resize((tile, tile), Image.Resampling.LANCZOS), (x, y))
    return img


def small(size, logo):
    w, h = size
    side = min(w, h)
    img = Image.new("RGB", size, (255, 255, 255))      # wizard header is white
    img.paste(logo.resize((side, side), Image.Resampling.LANCZOS), ((w - side) // 2, (h - side) // 2))
    return img


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build", "wizard")
    os.makedirs(out, exist_ok=True)
    logo = Image.open(LOGO).convert("RGB")
    for dpi, ls, ss in zip(DPI, LARGE, SMALL):
        large(ls, logo).save(os.path.join(out, f"large_{dpi}.bmp"))
        small(ss, logo).save(os.path.join(out, f"small_{dpi}.bmp"))
    print(f"Setup images written to {out}")


if __name__ == "__main__":
    main()
