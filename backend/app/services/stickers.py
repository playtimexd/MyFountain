"""Procedurally drawn sticker / shape / background PNGs (Pillow, 4x supersampled) so the material
library works fully offline. Files are cached under data/cache/stickers."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ..config import CACHE_DIR
from . import fonts

DIR = CACHE_DIR / "stickers"
DIR.mkdir(parents=True, exist_ok=True)
S = 512          # output size
SS = 4           # supersample


def _star(cx, cy, r_out, r_in, n=5, rot=-math.pi / 2):
    pts = []
    for i in range(n * 2):
        r = r_out if i % 2 == 0 else r_in
        a = rot + i * math.pi / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _heart(cx, cy, s):
    pts = []
    for i in range(0, 360, 3):
        t = math.radians(i)
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((cx + x * s, cy - y * s))
    return pts


def _canvas():
    im = Image.new("RGBA", (S * SS, S * SS), (0, 0, 0, 0))
    return im, ImageDraw.Draw(im)


def _done(im: Image.Image, path: Path):
    im.resize((S, S), Image.LANCZOS).save(path)


def _shadow(im: Image.Image, off=10 * SS // 4) -> Image.Image:
    from PIL import ImageFilter
    a = im.split()[3]
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    sh.putalpha(a.point(lambda v: int(v * 0.45)))
    sh = sh.filter(ImageFilter.GaussianBlur(10 * SS // 2))
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    out.alpha_composite(sh, (0, off))
    out.alpha_composite(im)
    return out


def _draw(name: str, path: Path):
    im, d = _canvas()
    c = S * SS // 2
    u = SS
    if name == "heart":
        d.polygon(_heart(c, c + 10 * u, 12.5 * u), fill="#ff4d6d")
    elif name == "star":
        d.polygon(_star(c, c + 10 * u, 230 * u, 100 * u), fill="#ffd23f", outline="#ff9f1c", width=6 * u)
    elif name == "circle":
        d.ellipse([40 * u, 40 * u, 472 * u, 472 * u], fill="#5b8cff")
    elif name == "ring":
        d.ellipse([40 * u, 40 * u, 472 * u, 472 * u], outline="#ffffff", width=30 * u)
    elif name == "rounded":
        d.rounded_rectangle([30 * u, 120 * u, 482 * u, 392 * u], radius=60 * u, fill="#7c5cff")
    elif name == "frame":
        d.rounded_rectangle([20 * u, 20 * u, 492 * u, 492 * u], radius=24 * u, outline="#ffffff", width=14 * u)
    elif name == "arrow":
        d.polygon([(40 * u, 190 * u), (300 * u, 190 * u), (300 * u, 90 * u), (480 * u, 256 * u), (300 * u, 422 * u),
                   (300 * u, 322 * u), (40 * u, 322 * u)], fill="#38c98a")
    elif name == "bubble":
        d.rounded_rectangle([30 * u, 60 * u, 482 * u, 360 * u], radius=70 * u, fill="#ffffff")
        d.polygon([(120 * u, 340 * u), (110 * u, 460 * u), (230 * u, 350 * u)], fill="#ffffff")
    elif name == "burst":
        d.polygon(_star(c, c, 250 * u, 160 * u, n=14), fill="#ff5c6c")
    elif name == "sparkle":
        d.polygon(_star(c, c, 250 * u, 40 * u, n=4), fill="#fff3a8")
    elif name == "check":
        d.ellipse([30 * u, 30 * u, 482 * u, 482 * u], fill="#38c98a")
        d.line([(130 * u, 270 * u), (220 * u, 360 * u), (390 * u, 170 * u)], fill="#fff", width=44 * u, joint="curve")
    elif name == "cross":
        d.ellipse([30 * u, 30 * u, 482 * u, 482 * u], fill="#ff5c6c")
        d.line([(160 * u, 160 * u), (352 * u, 352 * u)], fill="#fff", width=44 * u)
        d.line([(352 * u, 160 * u), (160 * u, 352 * u)], fill="#fff", width=44 * u)
    elif name == "triangle":
        d.polygon([(256 * u, 50 * u), (480 * u, 450 * u), (32 * u, 450 * u)], fill="#f5b83d")
    elif name == "diamond":
        d.polygon([(256 * u, 20 * u), (480 * u, 256 * u), (256 * u, 492 * u), (32 * u, 256 * u)], fill="#44d7e8")
    elif name == "play":
        d.ellipse([30 * u, 30 * u, 482 * u, 482 * u], fill=(0, 0, 0, 170))
        d.polygon([(200 * u, 150 * u), (200 * u, 362 * u), (380 * u, 256 * u)], fill="#ffffff")
    elif name in ("badge_new", "badge_hot", "badge_live", "badge_sale"):
        label, col = {"badge_new": ("NEW", "#7c5cff"), "badge_hot": ("HOT", "#ff5c6c"),
                      "badge_live": ("LIVE", "#e63946"), "badge_sale": ("SALE", "#f5b83d")}[name]
        d.rounded_rectangle([20 * u, 150 * u, 492 * u, 362 * u], radius=50 * u, fill=col)
        f = fonts.load("Arial", 130 * u, bold=True)
        d.text((c, c), label, font=f, fill="#ffffff", anchor="mm")
    else:
        raise KeyError(name)
    if name not in ("ring", "frame"):
        im = _shadow(im)
    _done(im, path)


STICKERS = [
    ("heart", "Heart"), ("star", "Star"), ("sparkle", "Sparkle"), ("burst", "Burst"), ("circle", "Circle"), ("ring", "Ring"),
    ("rounded", "Rounded box"), ("frame", "Frame"), ("arrow", "Arrow"), ("bubble", "Speech bubble"), ("check", "Check"),
    ("cross", "Cross"), ("triangle", "Triangle"), ("diamond", "Diamond"), ("play", "Play"),
    ("badge_new", "NEW"), ("badge_hot", "HOT"), ("badge_live", "LIVE"), ("badge_sale", "SALE"),
]


def sticker_path(sid: str) -> Path:
    p = DIR / f"{sid}.png"
    if not p.exists():
        _draw(sid, p)
    return p


GRADIENTS = [
    ("sunset", "Sunset", ["#ff9a9e", "#fad0c4", "#fbc2eb"]), ("ocean", "Ocean", ["#2193b0", "#6dd5ed"]),
    ("violet", "Violet", ["#7c5cff", "#5b8cff"]), ("mint", "Mint", ["#11998e", "#38ef7d"]),
    ("night", "Night sky", ["#0f2027", "#203a43", "#2c5364"]), ("peach", "Peach", ["#ffecd2", "#fcb69f"]),
    ("fire", "Blaze", ["#f12711", "#f5af19"]), ("mono", "Mono", ["#000000", "#434343"]),
    ("candy", "Candy", ["#ff6a88", "#ff99ac", "#fcb69f"]), ("aurora", "Aurora", ["#00c6ff", "#0072ff", "#7c5cff"]),
]


def background_png(bid: str, w: int = 1920, h: int = 1080) -> bytes:
    import io
    cols = next(c for i, _, c in GRADIENTS if i == bid)
    stops = [tuple(int(col[i:i + 2], 16) for i in (1, 3, 5)) for col in cols]
    yy, xx = np.mgrid[0:h, 0:w]
    t = ((xx / w) * 0.35 + (yy / h) * 0.65).astype(np.float32)
    seg = t * (len(stops) - 1)
    i0 = np.clip(seg.astype(int), 0, len(stops) - 2)
    f = (seg - i0)[..., None]
    a = np.array(stops, dtype=np.float32)
    img = (a[i0] * (1 - f) + a[i0 + 1] * f).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(img, "RGB").save(buf, "PNG")
    return buf.getvalue()
