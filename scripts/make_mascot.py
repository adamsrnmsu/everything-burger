"""Make src/burger/assets/mascot.ans (the banner art) from an image. Not part of the package.

Cut the background out first (e.g. `uvx --from 'rembg[cpu,cli]' rembg i photo.png cutout.png`),
then:

    uvx --with pillow python scripts/make_mascot.py cutout.png > src/burger/assets/mascot.ans

Use an image you own the rights to: the generated art ships in a public repo.
"""

import sys

from PIL import Image

RAMP = "+*#%@"
COLS, ASPECT = 52, 0.5  # 52 keeps the tall sceptre near 40 rows; cells are about 2:1
LIFT = 0.55  # gamma < 1 brightens the dark patty and wood on a black terminal

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
