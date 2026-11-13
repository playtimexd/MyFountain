"""Thumbnail helpers (Pillow / ffmpeg). GLB files without a source preview get no thumbnail (Blender belongs to anim-core)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ... import config

_NOWIN = {"creationflags": 0x08000000} if sys.platform == "win32" else {}


def image_thumb(src: Path, out: Path, size: int = 512) -> Path | None:
    try:
        from PIL import Image
        with Image.open(src) as im:
            im.thumbnail((size, size))
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                out = out.with_suffix(".png")
                im.save(out)
            else:
                out = out.with_suffix(".jpg")
                im.convert("RGB").save(out, quality=85)
        return out
    except Exception:
        return None


def font_preview(font: Path, out: Path, title: str) -> Path | None:
    try:
        from PIL import Image, ImageDraw, ImageFont
        im = Image.new("RGB", (512, 288), (24, 26, 34))
        d = ImageDraw.Draw(im)
        big = ImageFont.truetype(str(font), 96)
        small = ImageFont.truetype(str(font), 30)
        d.text((24, 40), "Aa Gg", font=big, fill=(232, 234, 240))
        d.text((24, 190), title[:28], font=small, fill=(154, 161, 178))
        d.text((24, 232), "0123456789 !?", font=small, fill=(124, 92, 255))
        out = out.with_suffix(".png")
        im.save(out)
        return out
    except Exception:
        return None


def audio_wave(src: Path, out: Path) -> Path | None:
    out = out.with_suffix(".png")
    try:
        r = subprocess.run([config.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-filter_complex",
                            "showwavespic=s=512x200:colors=#38c98a", "-frames:v", "1", str(out)],
                           capture_output=True, timeout=60, **_NOWIN)
        return out if r.returncode == 0 and out.exists() else None
    except Exception:
        return None
