"""Media library: import local files/folders, upload, list, serve (with Range), thumbs, waveform."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import db
from ..config import MEDIA_DIR
from ..ffutil import kind_of, make_thumb, waveform_peaks
from ..ingest import ingest_file
from ..jobs import jobs

router = APIRouter(prefix="/api/media", tags=["media"])


class ImportReq(BaseModel):
    paths: list[str]                 # files or folders on this machine (linked in place, not copied)
    recursive: bool = True
    copy: bool = False               # copy into data/media instead of linking


@router.post("/import")
def import_paths(req: ImportReq):
    def work(ctx):
        files: list[Path] = []
        for p in map(Path, req.paths):
            if p.is_dir():
                it = p.rglob("*") if req.recursive else p.glob("*")
                files += [f for f in it if f.is_file() and kind_of(f)]
            elif p.is_file() and kind_of(p):
                files.append(p)
        out = []
        for i, f in enumerate(files):
            ctx.progress(i / max(len(files), 1), f"{f.name}")
            try:
                if req.copy:
                    dst = MEDIA_DIR / f"{uuid.uuid4().hex[:8]}_{f.name}"
                    shutil.copy2(f, dst); f = dst
                out.append(ingest_file(f, origin="import"))
            except Exception as e:  # skip unreadable files, keep going
                out.append({"error": f"{f.name}: {e}"})
        return {"items": [o for o in out if "error" not in o], "errors": [o["error"] for o in out if "error" in o]}

    return jobs.submit("import", f"Import {len(req.paths)} paths", work).public()


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    dst = MEDIA_DIR / f"{uuid.uuid4().hex[:8]}_{Path(file.filename or 'upload').name}"
    with dst.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    if not kind_of(dst):
        dst.unlink(missing_ok=True)
        raise HTTPException(400, "unsupported file type")
    return ingest_file(dst, origin="upload", name=Path(file.filename or dst.name).stem)


@router.get("")
def list_media(kind: str | None = None, q: str | None = None, origin: str | None = None,
               tag: str | None = None, limit: int = 500, offset: int = 0):
    return db.list_media(kind, q, origin, tag, limit, offset)


@router.get("/{mid}")
def get_media(mid: str):
    m = db.get_media(mid)
    if not m:
        raise HTTPException(404)
    return m


class PatchReq(BaseModel):
    name: str | None = None
    tags: list[str] | None = None


@router.patch("/{mid}")
def patch_media(mid: str, p: PatchReq):
    m = db.update_media(mid, **{k: v for k, v in p.model_dump().items() if v is not None})
    if not m:
        raise HTTPException(404)
    return m


@router.delete("/{mid}")
def delete_media(mid: str, delete_file: bool = False):
    m = db.get_media(mid)
    if not m:
        raise HTTPException(404)
    db.delete_media(mid)
    if delete_file and Path(m["path"]).is_relative_to(MEDIA_DIR):   # never delete user's linked originals
        Path(m["path"]).unlink(missing_ok=True)
    return {"ok": True}


@router.get("/{mid}/file")
def media_file(mid: str):
    m = db.get_media(mid)
    if not m or not Path(m["path"]).exists():
        raise HTTPException(404)
    return FileResponse(m["path"])     # Starlette handles Range requests -> seekable <video>


@router.get("/{mid}/thumb")
def media_thumb(mid: str):
    m = db.get_media(mid)
    if not m:
        raise HTTPException(404)
    t = make_thumb(m["path"], m["kind"], mid, m["duration"])
    if not t.exists():
        raise HTTPException(404)
    return FileResponse(t, media_type="image/jpeg")


@router.get("/{mid}/waveform")
def media_waveform(mid: str, buckets: int = 800):
    m = db.get_media(mid)
    if not m:
        raise HTTPException(404)
    return {"peaks": waveform_peaks(m["path"], mid, buckets)}
