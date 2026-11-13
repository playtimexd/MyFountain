"""Paste-a-link downloader. yt-dlp handles YouTube / Bilibili / X / Facebook / Instagram / TikTok …;
Douyin & Xiaohongshu share links (where yt-dlp is often broken or needs cookies) get a page-parsing fallback."""
from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config
from ..config import FFMPEG, MEDIA_DIR
from ..ingest import ingest_file
from ..jobs import jobs

router = APIRouter(prefix="/api/download", tags=["download"])

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


def extract_url(text: str) -> str:
    """Share text from apps often looks like '3.5 复制打开抖音… https://v.douyin.com/xxxx/ 复制此链接…' — pull the URL out."""
    m = re.search(r"https?://[^\s一-鿿<>\"']+", text)
    if not m:
        raise HTTPException(400, "No valid link found")
    return m.group(0).rstrip(".,;)")


def site_of(url: str) -> str:
    h = (urlparse(url).hostname or "").lower()
    for key, name in (("douyin", "douyin"), ("iesdouyin", "douyin"), ("xiaohongshu", "xiaohongshu"), ("xhslink", "xiaohongshu"),
                      ("bilibili", "bilibili"), ("b23.tv", "bilibili"), ("youtube", "youtube"), ("youtu.be", "youtube"),
                      ("facebook", "facebook"), ("fb.watch", "facebook"), ("twitter", "x"), ("x.com", "x"), ("t.co", "x"),
                      ("instagram", "instagram"), ("tiktok", "tiktok"), ("weibo", "weibo"), ("kuaishou", "kuaishou")):
        if key in h:
            return name
    return h or "web"


def _ydl_opts(quality: str, cookies_browser: str | None, hook=None, outtmpl: str | None = None, info_only=False) -> dict:
    s = config.load_settings()
    ff = shutil.which(FFMPEG) or FFMPEG
    o: dict = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "retries": 3, "socket_timeout": 25,
        "ffmpeg_location": str(Path(ff).parent) if Path(ff).exists() else None,
        "merge_output_format": "mp4", "windowsfilenames": True, "concurrent_fragment_downloads": 4,
        "js_runtimes": {"node": {}} if shutil.which("node") else {"deno": {}},   # YouTube needs a JS runtime
    }
    if s.get("proxy"):
        o["proxy"] = s["proxy"]
    cb = cookies_browser or s.get("cookies_from_browser")
    if cb:
        o["cookiesfrombrowser"] = (cb,)
    if not info_only:
        o["outtmpl"] = outtmpl or str(MEDIA_DIR / "dl_%(id).60B_%(title).50B.%(ext)s")
        if hook:
            o["progress_hooks"] = [hook]
        if quality == "audio":
            o["format"] = "ba/b"
            o["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
        else:
            h = int(quality) if quality.isdigit() else None
            sel = f"bv*[height<={h}]+ba/b[height<={h}]/b" if h else "bv*+ba/b"
            o["format"] = sel
            o["format_sort"] = ["vcodec:h264", "res", "acodec:aac"]   # prefer broadly playable H.264/AAC
    return o


class InfoReq(BaseModel):
    url: str
    cookies_from_browser: str | None = None


@router.post("/info")
def info(req: InfoReq):
    import yt_dlp
    url = extract_url(req.url)
    site = site_of(url)
    try:
        with yt_dlp.YoutubeDL(_ydl_opts("best", req.cookies_from_browser, info_only=True) | {"skip_download": True}) as ydl:
            i = ydl.extract_info(url, download=False)
        heights = sorted({f.get("height") for f in i.get("formats", []) if f.get("height")}, reverse=True)
        return {"url": url, "site": site, "title": i.get("title"), "thumbnail": i.get("thumbnail"),
                "duration": i.get("duration"), "uploader": i.get("uploader") or i.get("channel"),
                "heights": heights, "extractor": i.get("extractor_key")}
    except Exception as e:  # noqa: BLE001
        if site in ("douyin", "xiaohongshu"):
            try:
                meta = _fallback_resolve(url, site)
                return {"url": url, "site": site, "title": meta["title"], "thumbnail": meta.get("cover"), "duration": meta.get("duration"),
                        "uploader": meta.get("author"), "heights": [], "extractor": f"fallback:{site}"}
            except Exception as e2:  # noqa: BLE001
                raise HTTPException(400, f"Parse failed: {_clean(e)}; fallback parser also failed: {e2}")
        raise HTTPException(400, f"Parse failed: {_clean(e)}")


def _clean(e: Exception) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "")[:400]


# ------------------------------------------------------------------------------------ fallback parsers
def _fallback_resolve(url: str, site: str) -> dict:
    with httpx.Client(headers={"User-Agent": MOBILE_UA, "Referer": url}, follow_redirects=True, timeout=20) as c:
        r = c.get(url)
        html, final = r.text, str(r.url)
    if site == "douyin":
        m = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>", html, re.S)
        if not m:
            raise RuntimeError("No video data on the page (login cookies may be required)")
        data = json.loads(m.group(1))
        item = None
        for v in (data.get("loaderData") or {}).values():
            lst = ((v or {}).get("videoInfoRes") or {}).get("item_list") or []
            if lst:
                item = lst[0]; break
        if not item:
            raise RuntimeError("Video entry not found")
        play = item["video"]["play_addr"]["url_list"][0].replace("playwm", "play")
        return {"title": item.get("desc") or "douyin", "video_url": play, "cover": (item["video"].get("cover") or {}).get("url_list", [None])[0],
                "duration": (item["video"].get("duration") or 0) / 1000, "author": (item.get("author") or {}).get("nickname"), "referer": final}
    # xiaohongshu
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    if not m:
        raise RuntimeError("No note data on the page (login cookies may be required)")
    state = json.loads(m.group(1).replace("undefined", "null"))
    notes = (state.get("note") or {}).get("noteDetailMap") or {}
    note = next((v.get("note") for v in notes.values() if v.get("note")), None)
    if not note:
        raise RuntimeError("Note not found")
    vid = None
    stream = ((note.get("video") or {}).get("media") or {}).get("stream") or {}
    for codec in ("h264", "h265", "av1"):
        if stream.get(codec):
            vid = stream[codec][0].get("masterUrl"); break
    if vid:
        return {"title": note.get("title") or note.get("desc", "")[:40] or "xiaohongshu", "video_url": vid,
                "cover": ((note.get("imageList") or [{}])[0]).get("urlDefault"), "duration": 0,
                "author": (note.get("user") or {}).get("nickname"), "referer": final}
    raise RuntimeError("This note is not a video (use image download for photo notes)")


class DlReq(BaseModel):
    url: str
    quality: str = "best"            # best | 2160 | 1440 | 1080 | 720 | 480 | audio
    cookies_from_browser: str | None = None


@router.post("")
def download(req: DlReq):
    url = extract_url(req.url)
    site = site_of(url)

    def work(ctx):
        import yt_dlp

        def hook(d):
            if d.get("status") == "downloading":
                tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                if tot:
                    ctx.progress(0.9 * d["downloaded_bytes"] / tot,
                                 f"Downloading {d.get('_speed_str', '').strip()} ETA {d.get('_eta_str', '').strip()}")
            elif d.get("status") == "finished":
                ctx.progress(0.92, "Merging/transcoding…")
            ctx.check()

        ctx.progress(0.02, "Parsing link…")
        try:
            with yt_dlp.YoutubeDL(_ydl_opts(req.quality, req.cookies_from_browser, hook)) as ydl:
                i = ydl.extract_info(url, download=True)
                rd = (i.get("requested_downloads") or [{}])[0]
                path = rd.get("filepath") or ydl.prepare_filename(i)
                if req.quality == "audio":
                    path = str(Path(path).with_suffix(".mp3"))
                title, uploader = i.get("title"), i.get("uploader")
        except Exception as e:  # noqa: BLE001
            if site not in ("douyin", "xiaohongshu"):
                raise RuntimeError(_clean(e))
            ctx.progress(0.05, "yt-dlp failed, trying fallback parser…")
            meta = _fallback_resolve(url, site)
            dst = MEDIA_DIR / f"dl_{uuid.uuid4().hex[:8]}.mp4"
            with httpx.stream("GET", meta["video_url"], headers={"User-Agent": MOBILE_UA, "Referer": meta["referer"]},
                              follow_redirects=True, timeout=60) as r:
                r.raise_for_status()
                tot, got = int(r.headers.get("content-length") or 0), 0
                with dst.open("wb") as f:
                    for ch in r.iter_bytes(1 << 16):
                        f.write(ch); got += len(ch)
                        if tot:
                            ctx.progress(0.1 + 0.8 * got / tot, "Downloading")
                        ctx.check()
            path, title, uploader = str(dst), meta["title"], meta.get("author")
        ctx.progress(0.95, "Adding to library…")
        row = ingest_file(path, origin="download", source_url=url, name=(title or Path(path).stem)[:80],
                          tags=[site], meta={"uploader": uploader, "site": site})
        return {"media": row}

    return jobs.submit("download", f"Download {site}", work).public()
