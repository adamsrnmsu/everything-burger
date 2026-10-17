"""Regenerate src/burger/assets/mascot.ans from a photo. Not part of the package.

Cut the background out first (e.g. `uvx --from 'rembg[cpu,cli]' rembg i photo.png cutout.png`),
then:

    uvx --with pillow python scripts/make_mascot.py cutout.png > src/burger/assets/mascot.ans

The photo stays out of the repo; only the generated art is committed.
"""

import sys

from PIL import Image

RAMP = "+*#%@"
COLS, ASPECT = 72, 0.5  # terminal cells are about twice as tall as wide
LIFT = 0.55  # gamma < 1 brightens dark brown hair on a black terminal

im = Image.open(sys.argv[1]).convert("RGBA")
im = im.crop(im.getchannel("A").point(lambda a: 255 if a > 40 else 0).getbbox())
rows = round(im.height / im.width * COLS * ASPECT)
im = im.resize((COLS, rows), Image.LANCZOS)
for y in range(rows):
    line = []
    for x in range(COLS):
        r, g, b, a = im.getpixel((x, y))
        if a < 100:
            line.append(" ")
            continue
        lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
        ch = RAMP[min(len(RAMP) - 1, int(lum * len(RAMP)))]
        r, g, b = (round(255 * (c / 255) ** LIFT) for c in (r, g, b))
        line.append(f"\x1b[38;2;{r};{g};{b}m{ch}")
    print("".join(line).rstrip() + "\x1b[0m")
