"""Material library: transitions, filters, stickers, text presets, backgrounds, fonts (+ optional stock search)."""
from __future__ import annotations

import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from .. import config
from ..config import FONTS_DIR, MEDIA_DIR
from ..ingest import ingest_file
from ..jobs import jobs
from ..services import fonts, stickers

router = APIRouter(prefix="/api/assets", tags=["assets"])

TRANSITIONS = [
    {"id": "fade", "name": "Fade"}, {"id": "slideleft", "name": "Slide left"}, {"id": "slideright", "name": "Slide right"},
    {"id": "slideup", "name": "Slide up"}, {"id": "slidedown", "name": "Slide down"}, {"id": "zoomin", "name": "Zoom in"},
    {"id": "wipeleft", "name": "Wipe left"}, {"id": "wiperight", "name": "Wipe right"},
]

FILTERS = [
    {"id": "none", "name": "Original", "adjust": {}},
    {"id": "vivid", "name": "Vivid", "adjust": {"saturation": 1.4, "contrast": 1.1}},
    {"id": "fresh", "name": "Fresh", "adjust": {"brightness": 0.05, "saturation": 1.2, "contrast": 0.95, "temperature": -0.1}},
    {"id": "cinematic", "name": "Cinematic", "adjust": {"contrast": 1.2, "saturation": 0.9, "temperature": 0.12, "vignette": 0.35, "brightness": -0.03}},
    {"id": "retro", "name": "Retro", "adjust": {"sepia": 0.55, "contrast": 1.05, "saturation": 0.85, "vignette": 0.3}},
    {"id": "bw", "name": "B&W", "adjust": {"grayscale": 1, "contrast": 1.12}},
    {"id": "cool", "name": "Cool", "adjust": {"temperature": -0.5, "saturation": 1.05}},
    {"id": "warm", "name": "Warm", "adjust": {"temperature": 0.5, "saturation": 1.05}},
    {"id": "contrast", "name": "High contrast", "adjust": {"contrast": 1.4, "saturation": 1.1}},
    {"id": "faded", "name": "Faded", "adjust": {"contrast": 0.85, "saturation": 0.8, "brightness": 0.06}},
    {"id": "dream", "name": "Dreamy", "adjust": {"blur": 2.5, "brightness": 0.08, "saturation": 1.15}},
    {"id": "sharp", "name": "Sharpen", "adjust": {"sharpen": 0.8, "contrast": 1.05}},
]

TEXT_PRESETS = [
    {"id": "title", "name": "Title", "text": {"content": "Title text", "font": "Microsoft YaHei", "size": 0.1, "bold": True, "color": "#ffffff", "stroke": "#000000", "strokeWidth": 0.06, "shadow": True, "bg": ""}},
    {"id": "subtitle", "name": "Subtitle", "text": {"content": "Subtitle text", "font": "Microsoft YaHei", "size": 0.05, "bold": True, "color": "#ffffff", "stroke": "#000000", "strokeWidth": 0.08, "shadow": False, "bg": ""}},
    {"id": "subtitle_box", "name": "Subtitle (box)", "text": {"content": "Subtitle text", "font": "Microsoft YaHei", "size": 0.045, "bold": False, "color": "#ffffff", "stroke": "#000000", "strokeWidth": 0, "shadow": False, "bg": "#000000b3", "bgPadding": 0.35}},
    {"id": "neon", "name": "Neon", "text": {"content": "NEON", "font": "Arial", "size": 0.11, "bold": True, "color": "#7cffea", "stroke": "#00b8a3", "strokeWidth": 0.1, "shadow": True, "bg": ""}},
    {"id": "yellow", "name": "Variety yellow", "text": {"content": "LOL", "font": "Microsoft YaHei", "size": 0.09, "bold": True, "color": "#ffe14d", "stroke": "#d6002a", "strokeWidth": 0.12, "shadow": True, "bg": ""}},
    {"id": "serif", "name": "Serif", "text": {"content": "Soft & airy", "font": "SimSun", "size": 0.06, "bold": False, "color": "#f4efe6", "stroke": "#000000", "strokeWidth": 0.0, "shadow": True, "bg": ""}},
    {"id": "tag", "name": "Tag", "text": {"content": "TAG", "font": "Arial", "size": 0.05, "bold": True, "color": "#ffffff", "stroke": "#000000", "strokeWidth": 0, "shadow": False, "bg": "#7c5cffee", "bgPadding": 0.5}},
    {"id": "lower_third", "name": "Lower third", "text": {"content": "Name · Title", "font": "Microsoft YaHei", "size": 0.04, "bold": True, "color": "#ffffff", "stroke": "#000000", "strokeWidth": 0, "shadow": False, "bg": "#1b1e26e6", "bgPadding": 0.6, "align": "left"}},
]


@router.get("/catalog")
def catalog():
    return {
        "transitions": TRANSITIONS,
        "filters": FILTERS,
        "stickers": [{"id": i, "name": n, "url": f"/api/assets/sticker/{i}.png"} for i, n in stickers.STICKERS],
        "textPresets": TEXT_PRESETS,
        "backgrounds": [{"id": i, "name": n, "css": f"linear-gradient(135deg, {', '.join(c)})", "url": f"/api/assets/background/{i}.png"}
                        for i, n, c in stickers.GRADIENTS],
        "fonts": [{"family": f["family"], "url": f["url"]} for f in fonts.list_families()],
    }


@router.get("/fonts")
def list_fonts():
    return [{"family": f["family"], "url": f["url"], "user": f["user"]} for f in fonts.list_families()]


@router.post("/fonts/upload")
async def upload_font(file: UploadFile = File(...)):
    name = Path(file.filename or "font.ttf").name
    if Path(name).suffix.lower() not in {".ttf", ".otf"}:
        raise HTTPException(400, "Only .ttf / .otf are supported")
    (FONTS_DIR / name).write_bytes(await file.read())
    fonts.index.cache_clear()
    return {"ok": True, "fonts": [f for f in fonts.list_families() if f["user"]]}


@router.get("/fonts/file/{name}")
def font_file(name: str):
    p = FONTS_DIR / Path(name).name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@router.get("/sticker/{sid}.png")
def sticker(sid: str):
    try:
        return FileResponse(stickers.sticker_path(sid), media_type="image/png")
    except KeyError:
        raise HTTPException(404)


@router.get("/background/{bid}.png")
def background(bid: str, w: int = 1920, h: int = 1080):
    try:
        return Response(stickers.background_png(bid, w, h), media_type="image/png")
    except StopIteration:
        raise HTTPException(404)


# ---------------------------------------------------------------- optional stock search (needs a free API key)
@router.get("/stock")
def stock(q: str, kind: str = "photo", page: int = 1):
    s = config.load_settings()
    out: list[dict] = []
    if s.get("pexels_api_key"):
        h = {"Authorization": s["pexels_api_key"]}
        if kind == "video":
            r = httpx.get("https://api.pexels.com/videos/search", params={"query": q, "per_page": 24, "page": page}, headers=h, timeout=15).json()
            for v in r.get("videos", []):
                f = sorted(v["video_files"], key=lambda x: abs((x.get("height") or 0) - 1080))[0]
                out.append({"id": f"pexels-v{v['id']}", "thumb": v["image"], "url": f["link"], "kind": "video", "duration": v["duration"], "source": "Pexels"})
        else:
            r = httpx.get("https://api.pexels.com/v1/search", params={"query": q, "per_page": 24, "page": page}, headers=h, timeout=15).json()
            for p in r.get("photos", []):
                out.append({"id": f"pexels-p{p['id']}", "thumb": p["src"]["medium"], "url": p["src"]["large2x"], "kind": "image", "source": "Pexels"})
    if s.get("pixabay_api_key"):
        if kind == "video":
            r = httpx.get("https://pixabay.com/api/videos/", params={"key": s["pixabay_api_key"], "q": q, "per_page": 24, "page": page}, timeout=15).json()
            for v in r.get("hits", []):
                out.append({"id": f"pixabay-v{v['id']}", "thumb": f"https://i.vimeocdn.com/video/{v['picture_id']}_295x166.jpg", "url": v["videos"]["large"]["url"] or v["videos"]["medium"]["url"],
                            "kind": "video", "duration": v["duration"], "source": "Pixabay"})
        else:
            r = httpx.get("https://pixabay.com/api/", params={"key": s["pixabay_api_key"], "q": q, "per_page": 24, "page": page, "image_type": "photo"}, timeout=15).json()
            for p in r.get("hits", []):
                out.append({"id": f"pixabay-p{p['id']}", "thumb": p["previewURL"], "url": p["largeImageURL"], "kind": "image", "source": "Pixabay"})
    if not out and not (s.get("pexels_api_key") or s.get("pixabay_api_key")):
        raise HTTPException(400, "No stock-site API key configured (set pexels_api_key or pixabay_api_key in Settings; both are free to obtain)")
    return out


class StockImport(BaseModel):
    url: str
    name: str = "stock"
    kind: str = "image"


@router.post("/stock/import")
def stock_import(req: StockImport):
    def work(ctx):
        ext = ".mp4" if req.kind == "video" else ".jpg"
        dst = MEDIA_DIR / f"stock_{uuid.uuid4().hex[:8]}{ext}"
        with httpx.stream("GET", req.url, follow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            got = 0
            with dst.open("wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    f.write(chunk); got += len(chunk)
                    if total:
                        ctx.progress(got / total)
        return {"media": ingest_file(dst, origin="download", name=req.name, tags=["stock"])}

    return jobs.submit("stock", "Download media", work).public()
