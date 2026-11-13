"""Font index: Windows system fonts + user fonts in data/fonts. Used by the text-clip rasteriser and the UI."""
from __future__ import annotations

import functools
import os
from pathlib import Path

from PIL import ImageFont

from ..config import FONTS_DIR

_DIRS = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
         Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts", FONTS_DIR]
_EXT = {".ttf", ".otf", ".ttc"}


@functools.lru_cache(maxsize=1)
def index() -> dict[str, dict[str, tuple[str, int]]]:
    """family -> {'regular'|'bold'|'italic'|'bolditalic': (file, face_index)}"""
    fams: dict[str, dict[str, tuple[str, int]]] = {}
    for d in _DIRS:
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.suffix.lower() not in _EXT:
                continue
            for i in range(0, 16 if f.suffix.lower() == ".ttc" else 1):
                try:
                    font = ImageFont.truetype(str(f), 10, index=i)
                    fam, style = font.getname()
                except Exception:
                    break
                s = (style or "").lower()
                key = "bolditalic" if ("bold" in s and ("italic" in s or "oblique" in s)) else \
                      "bold" if "bold" in s else "italic" if ("italic" in s or "oblique" in s) else "regular"
                fams.setdefault(fam, {}).setdefault(key, (str(f), i))
    return fams


def resolve(family: str, bold: bool = False, italic: bool = False) -> tuple[str, int] | None:
    idx = index()
    entry = idx.get(family)
    if entry is None:       # case-insensitive / prefix fallback, then CJK-friendly default
        low = family.lower()
        entry = next((v for k, v in idx.items() if k.lower() == low), None) \
            or next((v for k, v in idx.items() if k.lower().startswith(low)), None) \
            or idx.get("Microsoft YaHei") or idx.get("Arial") or next(iter(idx.values()), None)
    if entry is None:
        return None
    want = "bolditalic" if bold and italic else "bold" if bold else "italic" if italic else "regular"
    return entry.get(want) or entry.get("bold" if bold else "regular") or next(iter(entry.values()))


def load(family: str, size_px: float, bold: bool = False, italic: bool = False) -> ImageFont.FreeTypeFont:
    r = resolve(family, bold, italic)
    if r is None:
        return ImageFont.load_default(size=int(size_px))  # type: ignore[return-value]
    return ImageFont.truetype(r[0], max(4, int(round(size_px))), index=r[1])


def list_families() -> list[dict]:
    user = {f.stem for f in FONTS_DIR.glob("*") if f.suffix.lower() in {".ttf", ".otf"}}
    out = []
    for fam, styles in sorted(index().items(), key=lambda kv: kv[0].lower()):
        reg = styles.get("regular") or next(iter(styles.values()))
        is_user = Path(reg[0]).parent == FONTS_DIR
        out.append({"family": fam, "file": reg[0], "user": is_user,
                    "url": f"/api/assets/fonts/file/{Path(reg[0]).name}" if is_user else None})
    del user
    return out
