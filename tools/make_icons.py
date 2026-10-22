"""Draw the app icons into docs/icons/. Run once: python tools/make_icons.py"""
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(__file__), "..", "docs", "icons")
BLUE, WHITE, RED = (29, 78, 216), (255, 255, 255), (240, 68, 56)


def draw(size, maskable=False):
    s = 4 * size  # draw large, then downsample for smooth edges
    img = Image.new("RGBA", (s, s), BLUE if maskable else (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if not maskable:
        d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=BLUE)
    scale = 0.72 if maskable else 1.0  # keep artwork inside the maskable safe zone
    c = s / 2

    def p(x, y):
        return (c + (x - 0.5) * s * scale, c + (y - 0.5) * s * scale)

    # Red TFR ring
    r = 0.34 * s * scale
    d.ellipse([c - r, c - r, c + r, c + r], outline=RED, width=int(0.055 * s * scale))
    # White airplane, nose up
    plane = [(0.5, 0.20), (0.535, 0.27), (0.535, 0.43), (0.76, 0.56), (0.76, 0.61), (0.535, 0.545),
             (0.535, 0.70), (0.60, 0.75), (0.60, 0.79), (0.5, 0.765), (0.40, 0.79), (0.40, 0.75),
             (0.465, 0.70), (0.465, 0.545), (0.24, 0.61), (0.24, 0.56), (0.465, 0.43), (0.465, 0.27)]
    d.polygon([p(x, y) for x, y in plane], fill=WHITE)
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    draw(192).save(os.path.join(OUT, "icon-192.png"))
    draw(512).save(os.path.join(OUT, "icon-512.png"))
    draw(512, maskable=True).save(os.path.join(OUT, "icon-maskable-512.png"))
    draw(180, maskable=True).convert("RGB").save(os.path.join(OUT, "apple-touch-icon.png"))
    print("icons written to", os.path.abspath(OUT))
