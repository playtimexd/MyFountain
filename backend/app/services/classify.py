"""Automatic categorisation of library media: a fixed taxonomy (category) + subject and style tags.

Images and videos: zero-shot CLIP (the cached laion ViT-B-32 used by local search; never downloads). Videos are judged on three
frames. Audio: file-name keywords + duration (no model). Falls back to keywords only when CLIP is not available.
Result is stored in media.meta["classify"] and as tags: "cat:<category>", plus subject/style words, so the Library can filter.
"""
from __future__ import annotations

import re
import time
from typing import Any

import numpy as np

from .. import db

TAXONOMY: dict[str, list[tuple[str, str]]] = {
    # kind -> [(category id, CLIP prompt)]
    "image": [
        ("photo", "a photograph of a real scene"),
        ("portrait", "a photo portrait of a person"),
        ("illustration", "a digital illustration or painting"),
        ("character-art", "concept art of a game or cartoon character"),
        ("texture", "a seamless texture of a material surface"),
        ("sprite-icon", "a game sprite, icon or user interface element on a plain background"),
        ("logo", "a logo or emblem"),
        ("screenshot", "a screenshot of software or a website"),
        ("background", "a wide landscape background or environment art"),
        ("render-3d", "a 3D render of an object or character"),
    ],
    "video": [
        ("footage", "a frame of real camera footage"),
        ("talking-head", "a person talking to the camera"),
        ("animation", "a frame from an animated cartoon or anime"),
        ("game-capture", "a frame of video game gameplay"),
        ("screen-recording", "a screen recording of software"),
        ("render-3d", "a frame of a 3D animation render"),
        ("motion-graphics", "motion graphics with text and shapes"),
    ],
}
SUBJECTS = ["person", "people group", "bear", "dog", "cat", "bird", "horse", "wild animal", "fish", "insect", "forest", "mountain",
            "ocean", "beach", "river", "desert", "snow", "city", "street", "building interior", "room", "food", "car", "aircraft",
            "boat", "sky", "night", "sunset", "flowers", "plants", "weapon", "robot", "monster", "castle", "space", "sports", "music instrument",
            "text", "furniture", "rock"]
STYLES = [("realistic", "a realistic photo"), ("cartoon", "a cartoon drawing"), ("anime", "an anime style picture"),
          ("pixel-art", "pixel art"), ("low-poly", "a low poly 3D render"), ("3d-render", "a smooth 3D render"),
          ("watercolor", "a watercolor painting"), ("sketch", "a pencil sketch")]

AUDIO_KEYS = [("voice", r"voice|vo\b|narrat|speech|dialog|podcast|interview|配音|旁白|人声"),
              ("ambience", r"ambien|atmos|room ?tone|rain|wind|forest|city|crowd|环境|氛围"),
              ("sound-effect", r"sfx|fx|whoosh|hit|impact|click|swoosh|explosion|footstep|ui[_ ]|音效"),
              ("music", r"music|song|bgm|track|theme|loop|beat|ost|score|音乐|配乐")]

CATEGORY_LABELS = {c: c.replace("-", " ").title().replace("3D", "3D") for k in TAXONOMY.values() for c, _ in k}
CATEGORY_LABELS.update({"music": "Music", "sound-effect": "Sound effect", "voice": "Voice", "ambience": "Ambience",
                        "render-3d": "3D render", "sprite-icon": "Sprite / icon", "character-art": "Character art",
                        "talking-head": "Talking head", "game-capture": "Game capture", "screen-recording": "Screen recording",
                        "motion-graphics": "Motion graphics"})

_TEXT_CACHE: dict[str, np.ndarray] = {}


def _text_vecs(prompts: list[str]) -> np.ndarray | None:
    from . import rag
    out = []
    for p in prompts:
        if p not in _TEXT_CACHE:
            v = rag.clip_embed_text(p)
            if v is None:
                return None
            _TEXT_CACHE[p] = v
        out.append(_TEXT_CACHE[p])
    return np.stack(out)


def _softmax(x: np.ndarray, t: float = 100.0) -> np.ndarray:
    z = (x - x.max()) * t
    e = np.exp(z)
    return e / e.sum()


def _frames(row: dict) -> list[bytes]:
    from . import rag
    if row["kind"] == "image":                     # PIL first: ffmpeg's mjpeg pipe returns nothing for some JPEGs
        try:
            import io
            from PIL import Image
            im = Image.open(row["path"])
            im.thumbnail((512, 512))
            if im.mode in ("RGBA", "LA", "P"):
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[-1])
                im = bg
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "JPEG", quality=90)
            return [buf.getvalue()]
        except Exception:  # noqa: BLE001
            b = rag.grab_frame(row["path"], 0)
            return [b] if b else []
    d = float(row.get("duration") or 0)
    ts = [d * f for f in (0.15, 0.5, 0.85)] if d > 1 else [0.0]
    return [b for b in (rag.grab_frame(row["path"], t) for t in ts) if b]


def _keyword_category(name: str, kind: str) -> str | None:
    n = name.lower()
    if kind == "image":
        for cat, rx in (("texture", r"texture|albedo|normal|rough|diffuse|_basecolor|材质|贴图"), ("logo", r"logo|标志"),
                        ("screenshot", r"screenshot|screen ?shot|截图|截屏"), ("sprite-icon", r"icon|sprite|ui[_ -]|图标")):
            if re.search(rx, n):
                return cat
    if kind == "video" and re.search(r"screen ?record|录屏", n):
        return "screen-recording"
    return None


def classify_row(row: dict) -> dict[str, Any]:
    kind, name = row["kind"], row.get("name") or ""
    res: dict[str, Any] = {"kind": kind, "category": None, "subjects": [], "style": None, "confidence": 0.0, "method": "keywords", "at": time.time()}
    if kind == "audio":
        dur = float(row.get("duration") or 0)
        cat = next((c for c, rx in AUDIO_KEYS if re.search(rx, name.lower())), None)
        if cat:
            res.update(category=cat, confidence=0.7)
        else:
            res.update(category="sound-effect" if dur and dur < 6 else "music" if dur >= 30 else "sound-effect", confidence=0.35,
                       method="duration")
        return res
    kw = _keyword_category(name, kind)
    imgs = _frames(row)
    from . import rag
    E = rag.clip_embed_images(imgs) if imgs else None
    if E is None or not len(E):
        res.update(category=kw or ("photo" if kind == "image" else "footage"), confidence=0.3 if kw else 0.1)
        return res
    v = E.mean(0)
    v /= np.linalg.norm(v) + 1e-9
    tax = TAXONOMY[kind]
    T = _text_vecs([p for _, p in tax])
    S = _text_vecs([f"a picture of {s}" for s in SUBJECTS])
    Y = _text_vecs([p for _, p in STYLES])
    if T is None or S is None or Y is None:
        res.update(category=kw or tax[0][0], confidence=0.2)
        return res
    pc = _softmax(T @ v)
    i = int(pc.argmax())
    cat, conf = tax[i][0], float(pc[i])
    if kw and conf < 0.6:
        cat, conf = kw, max(conf, 0.5)
    # subjects: only clear winners (CLIP spreads probability over generic words like "person" on abstract images)
    ps = _softmax(S @ v)
    order = np.argsort(-ps)
    subjects = [SUBJECTS[j] for j in order[:3] if ps[j] > 0.25 and (j == order[0] or ps[j] > 0.5 * ps[order[0]])]
    if cat in ("texture", "sprite-icon", "logo", "screenshot"):
        subjects = [x for x in subjects if ps[SUBJECTS.index(x)] > 0.5]
    py = _softmax(Y @ v)
    style = STYLES[int(py.argmax())][0] if py.max() > 0.5 else None
    res.update(category=cat, confidence=round(conf, 3), subjects=subjects, style=style, method="clip",
               scores={tax[j][0]: round(float(pc[j]), 3) for j in np.argsort(-pc)[:3]})
    return res


def apply(mid: str, force: bool = False) -> dict | None:
    row = db.get_media(mid)
    if not row:
        return None
    meta = dict(row.get("meta") or {})
    if meta.get("classify") and not force:
        return row
    c = classify_row(row)
    meta["classify"] = c
    tags = [t for t in (row.get("tags") or []) if not t.startswith("cat:")]
    new = [f"cat:{c['category']}"] if c["category"] else []
    new += [s for s in c["subjects"] if s not in tags]
    if c.get("style") and c["style"] not in tags:
        new.append(c["style"])
    return db.update_media(mid, meta=meta, tags=new + tags)


def categories() -> list[dict]:
    """Category facet with counts, for the Library filter."""
    counts: dict[str, int] = {}
    for r in db.list_media(limit=1_000_000):
        for t in r.get("tags") or []:
            if t.startswith("cat:"):
                counts[t[4:]] = counts.get(t[4:], 0) + 1
    return [{"id": k, "label": CATEGORY_LABELS.get(k, k), "count": n} for k, n in sorted(counts.items(), key=lambda kv: -kv[1])]
