"""3D model library for the "3D Models" workspace: one list over characters already in the animation rig library, models generated
by Tripo/Meshy (data/anim/incoming) and models the user uploads, plus fur grooms.  Animation keeps motion (clips, mocap,
retarget); a model reaches Animation through "Send to Animation" (= the existing rig import)."""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..jobs import jobs
from ..services.anim import db as anim_db
from ..services.anim import fur
from ..services.providers.gen3d import INCOMING

router = APIRouter(prefix="/api/models", tags=["models"])

MODEL_EXT = {".glb", ".gltf", ".fbx", ".obj", ".blend"}
VIEWABLE = {".glb", ".gltf"}
_SAFE = re.compile(r"^[A-Za-z0-9_\-. ]+$")


def _rig_entries() -> list[dict]:
    out = []
    for r in anim_db.list_rigs():
        src = next((anim_db.rig_dir(r["id"]) / f"source{e}" for e in (".glb", ".gltf", ".fbx") if (anim_db.rig_dir(r["id"]) / f"source{e}").exists()),
                   anim_db.rig_dir(r["id"]) / "source.glb")
        out.append({"id": f"rig:{r['id']}", "kind": "character", "name": r["name"], "created": r.get("created") or 0,
                    "thumb_url": r.get("thumb_url"), "view_url": f"/api/models/rig/{r['id']}/source{src.suffix}" if src.exists() else None, "format": src.suffix.lstrip(".") or "glb",
                    "in_animation": True, "rig_id": r["id"], "rig_kind": r.get("kind"), "bones": r.get("bone_count"),
                    "height_m": r.get("height"), "source": "rig library"})
    return out


def _generated_entries(linked: set[str]) -> list[dict]:
    out = []
    for p in sorted(INCOMING.glob("*")):
        if p.suffix.lower() not in MODEL_EXT or not p.is_file():
            continue
        stem = p.stem.lower()
        gen = any(t in stem for t in ("_meshy", "_tripo"))
        out.append({"id": f"file:{p.name}", "kind": "model", "name": p.stem, "created": p.stat().st_mtime, "size": p.stat().st_size,
                    "thumb_url": None, "view_url": f"/api/models/file/{p.name}" if p.suffix.lower() in VIEWABLE | {".fbx"} else None, "format": p.suffix.lstrip(".").lower(),
                    "download_url": f"/api/models/file/{p.name}", "file": p.name, "in_animation": str(p) in linked,
                    "source": "generated" if gen else "uploaded"})
    return out


@router.get("")
def list_models():
    rigs = _rig_entries()
    linked = {str(Path(r.get("source_path") or "")) for r in (anim_db.get_rig(x["rig_id"], True) for x in rigs) if r}
    grooms = [{"id": f"fur:{g['id']}", "kind": "fur", "name": g["name"], "created": g.get("created") or 0,
               "thumb_url": g["urls"].get("render"), "view_url": g["urls"].get("glb"), "groom_id": g["id"],
               "hair_count": (g.get("groom") or {}).get("hair_count"), "source": "fur groom"} for g in fur.list_grooms()]
    hub = []
    try:
        from ..services.hub import db as hubdb
        for h in hubdb.search({"kind": "model"}, 1, 2000).get("items", []):
            hub.append({"id": f"hub:{h['id']}", "kind": "model", "name": h["name"], "created": h.get("created") or 0, "thumb_url": h.get("thumb_url"),
                        "view_url": f"/api/hub/file/{h['id']}/main", "download_url": f"/api/hub/file/{h['id']}/main", "hub_id": h["id"],
                        "format": Path(hubdb.file_path(h["id"], "main") or "x.glb").suffix.lstrip(".").lower(),
                        "source": f"Asset Hub · {h.get('source')}", "license": h.get("license"), "author": h.get("author"),
                        "rigged": h.get("rigged"), "animated": h.get("animated"), "in_animation": False})
    except Exception:  # noqa: BLE001  (hub optional)
        pass
    items = rigs + _generated_entries(linked) + grooms + hub
    return sorted(items, key=lambda x: -float(x.get("created") or 0))


@router.get("/rig/{rid}/source.{ext}")
def rig_source(rid: str, ext: str):
    if not anim_db.get_rig(rid):
        raise HTTPException(404, "character not found")
    if ext not in ("glb", "gltf", "fbx"):
        raise HTTPException(400, "bad extension")
    p = anim_db.rig_dir(rid) / f"source.{ext}"
    if not p.exists():
        raise HTTPException(404, "this character has no GLB source")
    return FileResponse(p, media_type="model/gltf-binary" if ext == "glb" else "application/octet-stream")


@router.get("/file/{name}")
def model_file(name: str):
    if not _SAFE.match(name) or name.startswith("."):
        raise HTTPException(400, "bad file name")
    p = INCOMING / name
    if not p.is_file() or p.parent != INCOMING or p.suffix.lower() not in MODEL_EXT:
        raise HTTPException(404, "model not found")
    mt = "model/gltf-binary" if p.suffix.lower() == ".glb" else "application/octet-stream"
    return FileResponse(p, media_type=mt, filename=None if p.suffix.lower() in VIEWABLE else p.name)


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    name = Path(file.filename or "model.glb").name
    ext = Path(name).suffix.lower()
    if ext not in MODEL_EXT:
        raise HTTPException(400, f"Unsupported model type {ext or '?'}; use {', '.join(sorted(MODEL_EXT))}.")
    stem = re.sub(r"[^A-Za-z0-9_\-]+", "_", Path(name).stem).strip("_")[:40] or "model"
    dst = INCOMING / f"{stem}_{time.strftime('%Y%m%d_%H%M%S')}_upload{ext}"
    with dst.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return next(m for m in _generated_entries(set()) if m["file"] == dst.name)


@router.delete("/file/{name}")
def delete_file(name: str):
    if not _SAFE.match(name) or name.startswith("."):
        raise HTTPException(400, "bad file name")
    p = INCOMING / name
    if not p.is_file() or p.parent != INCOMING:
        raise HTTPException(404, "model not found")
    p.unlink()
    return {"ok": True}


@router.post("/hub/{hid}/to-animation")
def hub_to_animation(hid: str):
    from ..services.hub import db as hubdb
    p = hubdb.file_path(hid, "main")
    if not p or not Path(p).is_file():
        raise HTTPException(404, "hub model file not found")
    a = hubdb.get(hid) or {}
    from ..services.anim import library
    return jobs.submit("anim-rig", f"Send to Animation: {a.get('name', hid)}"[:100],
                       lambda ctx: library.import_rig(ctx, p, None, a.get("name"), None)).public()


@router.post("/file/{name}/to-animation")
def to_animation(name: str):
    """Import a generated/uploaded model into the animation rig library (Blender inspection + normalisation)."""
    if not _SAFE.match(name):
        raise HTTPException(400, "bad file name")
    p = INCOMING / name
    if not p.is_file() or p.parent != INCOMING:
        raise HTTPException(404, "model not found")
    from ..services.anim import library
    return jobs.submit("anim-rig", f"Send to Animation: {p.stem}"[:100],
                       lambda ctx: library.import_rig(ctx, str(p), None, p.stem.split("_2")[0][:40] or p.stem, None)).public()


# ---------------------------------------------------------------------------------------------- local 3D / animation index
from pydantic import BaseModel  # noqa: E402

from ..services import asset_index  # noqa: E402


class IndexReq(BaseModel):
    paths: list[str] = []


@router.post("/local/index")
def index_local(req: IndexReq):
    paths = req.paths or ["D:\\", str(Path.home() / "Downloads"), str(Path.home() / "Documents"), str(Path.home() / "Desktop"), str(Path.home() / "OneDrive")]
    paths = [p for p in paths if Path(p).exists()]
    if not paths:
        raise HTTPException(400, "None of the folders exist.")
    return jobs.submit("models-index", f"Index 3D & animation files ({len(paths)} folders)", lambda ctx: asset_index.scan(ctx, paths)).public()


@router.get("/local")
def local_assets(kind: str | None = None, q: str | None = None, project: str | None = None, tag: str | None = None, ext: str | None = None,
                 limit: int = 200, offset: int = 0):
    return asset_index.query(kind, q, project, tag, ext, limit, offset)


@router.get("/local/{aid}")
def local_asset(aid: str):
    a = asset_index.get(aid)
    if not a:
        raise HTTPException(404, "asset not found")
    return a


@router.get("/local/{aid}/file")
def local_file(aid: str):
    a = asset_index.get(aid)
    if not a or not Path(a["path"]).is_file():
        raise HTTPException(404, "file not found (moved or deleted? re-index)")
    mt = {".glb": "model/gltf-binary", ".gltf": "model/gltf+json"}.get(a["ext"], "application/octet-stream")
    return FileResponse(a["path"], media_type=mt)


@router.post("/local/{aid}/to-animation")
def local_to_animation(aid: str):
    """animation / animated-*: import the clip(s) into the animation library; rigged / model: import as a character (rig)."""
    a = asset_index.get(aid)
    if not a or not Path(a["path"]).is_file():
        raise HTTPException(404, "file not found")
    from ..services.anim import library
    if a["kind"] in ("animation", "animated-character", "animated-model"):
        return jobs.submit("anim-clip", f"Import motion: {a['name']}"[:100], lambda ctx: library.import_clip(ctx, a["path"], a["name"], {})).public()
    if a["kind"] in ("rigged", "model", "scene"):
        return jobs.submit("anim-rig", f"Import character: {a['name']}"[:100], lambda ctx: library.import_rig(ctx, a["path"], None, a["name"], None)).public()
    raise HTTPException(400, f"Cannot import a file of kind {a['kind']}.")
