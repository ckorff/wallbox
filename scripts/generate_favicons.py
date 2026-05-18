"""Generate raster favicons from the SVG design.

Renders at 8x oversample with Pillow's draw primitives, then
LANCZOS-downsamples to the target size. The vertices and colours
mirror charging/static/img/favicon.svg.
"""
from pathlib import Path

from PIL import Image, ImageDraw

NAVY = "#0a1729"
CYAN = "#5beaff"

# Bolt vertices in the 64-unit reference space used by the SVG.
BOLT = [
    (34.67, 5.33),
    (8.0, 37.33),
    (26.67, 37.33),
    (24.0, 58.67),
    (56.0, 26.67),
    (37.33, 26.67),
    (40.0, 5.33),
]

OUT_DIR = Path(__file__).resolve().parent.parent / "charging" / "static" / "img"
OVERSAMPLE = 8


def _render(size: int) -> Image.Image:
    scale = size * OVERSAMPLE / 64
    big = size * OVERSAMPLE
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, big - 1, big - 1), fill=NAVY)
    draw.polygon([(x * scale, y * scale) for x, y in BOLT], fill=CYAN)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    _render(16).save(OUT_DIR / "favicon-16.png", format="PNG")
    _render(32).save(OUT_DIR / "favicon-32.png", format="PNG")
    _render(180).save(OUT_DIR / "apple-touch-icon.png", format="PNG")

    # Multi-size ICO: render the largest, let Pillow emit each size.
    _render(48).save(
        OUT_DIR / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)],
    )


if __name__ == "__main__":
    main()
