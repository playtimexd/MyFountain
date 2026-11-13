"""Intake: bring files from elsewhere (OneDrive / other cloud drives via an AI agent, direct links, local sync folders) into CutLab
and file them in the right library, then auto-categorise.

Routing by file type:
  video / audio / image  -> media library (+ classify.apply: category, subjects, style)
  .glb .gltf .fbx .obj .blend -> 3D model library (data/anim/incoming)
  .bvh .npz              -> animation library (motion import job)
  .ttf .otf .ttc         -> user fonts (data/fonts)
Anything else is skipped and reported.

Remote downloads never send credentials: the caller passes a URL that is already authorised (e.g. OneDrive/Graph
`@microsoft.graph.downloadUrl`, a share link with download=1). Only http(s) to public hosts is allowed.
"""
from __future__ import annotations

import base64
import ipaddress
import os
import re
import shutil
import socket
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from .. import config
from ..ffutil import kind_of
from ..ingest import ingest_file
from ..jobs import JobContext
from . import classify

INTAKE_DIR = config.MEDIA_DIR / "intake"
CHUNK_DIR = config.CACHE_DIR / "intake_chunks"
for _d in (INTAKE_DIR, CHUNK_DIR):
    _d.mkdir(parents=True, exist_ok=True)

MODEL_EXT = {".glb", ".gltf", ".fbx", ".obj", ".blend"}
MOTION_EXT = {".bvh", ".npz"}
FONT_EXT = {".ttf", ".otf", ".ttc"}
MAX_DOWNLOAD = 4 * 1024 ** 3                 # 4 GB per file
MAX_UPLOAD = 1024 ** 3                       # 1 GB per chunked upload
MAX_CHUNK = 32 * 1024 ** 2                   # 32 MB of raw data per chunk


def route_of(name: str) -> str | None:
    e = Path(name).suffix.lower()
    if kind_of(name):
        return "media"
    if e in MODEL_EXT:
        return "model"
    if e in MOTION_EXT:
        return "motion"
    if e in FONT_EXT:
        return "font"
    return None


def safe_filename(name: str, default: str = "file") -> str:
    name = unquote(Path(name.replace("\\", "/")).name)
    stem, ext = os.path.splitext(name)
    stem = re.sub(r"[^\w\-. ]+", "_", stem, flags=re.UNICODE).strip(" ._")[:80] or default
    ext = re.sub(r"[^A-Za-z0-9.]", "", ext)[:10]
    return stem + ext


def _unique(dirp: Path, name: str) -> Path:
    p = dirp / name
    if not p.exists():
        return p
    stem, ext = os.path.splitext(name)
    return dirp / f"{stem}_{uuid.uuid4().hex[:6]}{ext}"


def file_in(path: Path, *, origin: str, source_url: str | None = None, categorize: bool = True, tags: list[str] | None = None,
            category: str | None = None, ctx: JobContext | None = None) -> dict:
    """File one local file into the right library. Returns {route, ...}."""
    route = route_of(path.name)
    if route == "media":
        row = ingest_file(path, origin=origin, source_url=source_url, tags=tags or [])
        if category:                                                   # caller's category wins (e.g. the agent already knows)
            meta = dict(row.get("meta") or {})
            meta["classify"] = {"kind": row["kind"], "category": category, "subjects": [], "style": None, "confidence": 1.0, "method": "given", "at": time.time()}
            from .. import db
            row = db.update_media(row["id"], meta=meta, tags=[f"cat:{category}"] + [t for t in row.get("tags") or [] if not t.startswith("cat:")])
        elif categorize:
            try:
                row = classify.apply(row["id"]) or row
            except Exception as e:  # noqa: BLE001  (classification must never lose the import)
                row = {**row, "classify_error": str(e)}
        c = (row.get("meta") or {}).get("classify") or {}
        return {"route": "media", "id": row["id"], "name": row["name"], "kind": row["kind"], "category": c.get("category"),
                "subjects": c.get("subjects"), "style": c.get("style"), "confidence": c.get("confidence")}
    if route == "model":
        from .providers.gen3d import INCOMING
        dst = _unique(INCOMING, f"{Path(path.name).stem}_{time.strftime('%Y%m%d_%H%M%S')}_upload{path.suffix.lower()}")
        shutil.copy2(path, dst)
        return {"route": "model", "file": dst.name, "name": path.stem, "category": "3d-model",
                "next": "3D Models library; use Send to Animation to rig/animate it."}
    if route == "motion":
        from .anim import library
        if ctx is None:
            raise RuntimeError("motion import needs a job context")
        res = library.import_clip(ctx, str(path), path.stem, {})
        clip = (res or {}).get("clip") or (res if (res or {}).get("id") else {})
        return {"route": "motion", "clip_id": clip.get("id"), "name": clip.get("name") or path.stem, "category": "motion"}
    if route == "font":
        dst = _unique(config.FONTS_DIR, safe_filename(path.name))
        shutil.copy2(path, dst)
        return {"route": "font", "file": dst.name, "name": path.stem, "category": "font"}
    return {"route": None, "name": path.name, "skipped": "unsupported file type"}


# ---------------------------------------------------------------------------------------------- remote URLs
def _public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True


def check_url(url: str) -> str:
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("Only http(s) links can be imported.")
    if not _public_host(u.hostname):
        raise ValueError(f"Refusing to download from a local/private address ({u.hostname}).")
    return url.strip()


def _name_from(resp: httpx.Response, url: str, fallback: str | None) -> str:
    if fallback:
        return safe_filename(fallback)
    cd = resp.headers.get("content-disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd) or re.search(r'filename="?([^";]+)"?', cd)
    if m:
        return safe_filename(m.group(1))
    return safe_filename(urlparse(url).path or "download", "download")


def download(ctx: JobContext, url: str, name: str | None, lo: float, hi: float) -> Path:
    url = check_url(url)
    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60.0, read=300.0), headers={"User-Agent": "CutLab-intake/1.0"}) as cl:
        with cl.stream("GET", url) as r:
            r.raise_for_status()
            if r.url.host and not _public_host(r.url.host):                 # redirects must stay public too
                raise ValueError("The link redirected to a local/private address.")
            fname = _name_from(r, str(r.url), name)
            if not Path(fname).suffix:
                ext = {"video/mp4": ".mp4", "image/jpeg": ".jpg", "image/png": ".png", "audio/mpeg": ".mp3", "model/gltf-binary": ".glb",
                       "image/webp": ".webp", "audio/wav": ".wav", "video/quicktime": ".mov"}.get(r.headers.get("content-type", "").split(";")[0])
                fname += ext or ""
            total = int(r.headers.get("content-length") or 0)
            if total > MAX_DOWNLOAD:
                raise ValueError(f"File is too large ({total / 1e9:.1f} GB > 4 GB).")
            dst = _unique(INTAKE_DIR, fname)
            got = 0
            with dst.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    got += len(chunk)
                    if got > MAX_DOWNLOAD:
                        f.close(); dst.unlink(missing_ok=True)
                        raise ValueError("File is larger than 4 GB.")
                    f.write(chunk)
                    if total:
                        ctx.progress(lo + (hi - lo) * got / total, f"{fname}: {got / 1e6:.0f}/{total / 1e6:.0f} MB")
                    if ctx.cancelled:
                        f.close(); dst.unlink(missing_ok=True)
                        ctx.check()
    return dst


def import_urls(ctx: JobContext, items: list[dict], categorize: bool = True, source: str = "link") -> dict:
    out, errors = [], []
    n = max(len(items), 1)
    for i, it in enumerate(items):
        lo, hi = i / n, (i + 1) / n
        url = it.get("url") or ""
        try:
            name = it.get("name")
            if name and route_of(name) is None and Path(name).suffix:
                raise ValueError(f"unsupported file type: {name}")
            p = download(ctx, url, name, lo, lo + (hi - lo) * 0.8)
            if route_of(p.name) is None:
                p.unlink(missing_ok=True)
                raise ValueError(f"unsupported file type: {p.name}")
            ctx.progress(lo + (hi - lo) * 0.85, f"Filing {p.name}")
            res = file_in(p, origin="intake", source_url=_redact(url), categorize=categorize, tags=[source, *(it.get("tags") or [])],
                          category=it.get("category"), ctx=ctx)
            if res.get("route") != "media":                       # copied elsewhere: drop the intake copy
                p.unlink(missing_ok=True)
            out.append(res)
        except Exception as e:  # noqa: BLE001
            errors.append({"url": _redact(url), "name": it.get("name"), "error": str(e)})
    return {"items": out, "errors": errors, "summary": summarize(out)}


def _redact(url: str) -> str:
    """Pre-authorised links carry tokens in the query string: never store them."""
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}{u.path}" if u.query else url


def summarize(items: list[dict]) -> dict:
    s: dict[str, dict[str, int]] = {}
    for it in items:
        r = it.get("route") or "skipped"
        c = it.get("category") or "uncategorized"
        s.setdefault(r, {}).setdefault(c, 0)
        s[r][c] += 1
    return s


# ---------------------------------------------------------------------------------------------- chunked upload (agent sends bytes)
def upload_chunk(upload_id: str | None, name: str, data_b64: str, offset: int | None = None) -> dict:
    if route_of(name) is None:
        raise ValueError(f"Unsupported file type: {name}")
    raw = base64.b64decode(data_b64, validate=True)
    if len(raw) > MAX_CHUNK:
        raise ValueError("Chunk too large (max 32 MB of raw data per call).")
    uid = upload_id or uuid.uuid4().hex[:16]
    if not re.fullmatch(r"[0-9a-f]{16}", uid):
        raise ValueError("bad upload_id")
    part = CHUNK_DIR / f"{uid}.part"
    size = part.stat().st_size if part.exists() else 0
    if offset is not None and offset != size:
        raise ValueError(f"offset mismatch: server has {size} bytes")
    if size + len(raw) > MAX_UPLOAD:
        part.unlink(missing_ok=True)
        raise ValueError("Upload larger than 1 GB; use a download link or the OneDrive sync folder instead.")
    with part.open("ab") as f:
        f.write(raw)
    (CHUNK_DIR / f"{uid}.name").write_text(safe_filename(name), "utf-8")
    return {"upload_id": uid, "received": size + len(raw)}


def finish_upload(ctx: JobContext, upload_id: str, categorize: bool = True, category: str | None = None, tags: list[str] | None = None) -> dict:
    if not re.fullmatch(r"[0-9a-f]{16}", upload_id or ""):
        raise ValueError("bad upload_id")
    part, namef = CHUNK_DIR / f"{upload_id}.part", CHUNK_DIR / f"{upload_id}.name"
    if not part.exists() or not namef.exists():
        raise ValueError("unknown upload_id")
    dst = _unique(INTAKE_DIR, namef.read_text("utf-8"))
    shutil.move(str(part), dst)
    namef.unlink(missing_ok=True)
    res = file_in(dst, origin="intake", categorize=categorize, category=category, tags=["upload", *(tags or [])], ctx=ctx)
    if res.get("route") != "media":
        dst.unlink(missing_ok=True)
    return res


# ---------------------------------------------------------------------------------------------- local sync folders
def cloud_folders() -> list[dict]:
    """OneDrive (personal / business) and other synced cloud folders on this PC. Files there import without any network access."""
    seen, out = set(), []
    home = Path.home()
    cands = [("OneDrive", os.environ.get("OneDrive")), ("OneDrive (personal)", os.environ.get("OneDriveConsumer")),
             ("OneDrive (work/school)", os.environ.get("OneDriveCommercial"))]
    cands += [(p.name, str(p)) for p in home.glob("OneDrive*") if p.is_dir()]
    cands += [(n, str(home / n)) for n in ("Dropbox", "Google Drive", "iCloudDrive") if (home / n).is_dir()]
    for label, p in cands:
        if not p:
            continue
        rp = str(Path(p).resolve())
        if rp in seen or not Path(rp).is_dir():
            continue
        seen.add(rp)
        out.append({"label": label, "path": rp})
    return out


def import_local(ctx: JobContext, paths: list[str], recursive: bool = True, categorize: bool = True, limit: int = 5000) -> dict:
    files: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            files += [f for f in it if f.is_file() and route_of(f.name)]
        elif p.is_file() and route_of(p.name):
            files.append(p)
        if len(files) > limit:
            raise ValueError(f"More than {limit} files; import a smaller folder.")
    out, errors = [], []
    for i, f in enumerate(files):
        ctx.progress(i / max(len(files), 1), f.name)
        ctx.check()
        try:
            # OneDrive "online-only" placeholders download on first read; linking in place keeps one copy on disk
            out.append(file_in(f, origin="import", categorize=categorize, tags=["onedrive"] if "onedrive" in str(f).lower() else [], ctx=ctx))
        except Exception as e:  # noqa: BLE001
            errors.append({"path": str(f), "error": str(e)})
    return {"items": out, "errors": errors, "summary": summarize(out)}


def categorize(ctx: JobContext, ids: list[str] | None, only_missing: bool = True) -> dict:
    from .. import db
    rows = [db.get_media(i) for i in ids] if ids else db.list_media(limit=1_000_000)
    rows = [r for r in rows if r and (not only_missing or not (r.get("meta") or {}).get("classify"))]
    done = []
    for i, r in enumerate(rows):
        ctx.progress(i / max(len(rows), 1), r["name"])
        ctx.check()
        try:
            row = classify.apply(r["id"], force=not only_missing) or r
            c = (row.get("meta") or {}).get("classify") or {}
            done.append({"id": r["id"], "name": r["name"], "kind": r["kind"], "category": c.get("category"), "subjects": c.get("subjects"),
                         "confidence": c.get("confidence")})
        except Exception as e:  # noqa: BLE001
            done.append({"id": r["id"], "name": r["name"], "error": str(e)})
    return {"items": done, "summary": summarize([{**d, "route": "media"} for d in done if "error" not in d])}
