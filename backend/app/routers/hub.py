"""Asset Hub API (/api/hub): license-aware database of free game assets from official APIs + manual pack imports."""
from __future__ import annotations

import functools
import shutil
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..jobs import jobs
from ..services.hub import ai, db, engine, harvest, imports, licenses, paths, pipeline
from ..services.hub.connectors import REGISTRY, ConnectorError, NeedsKey
from ..services.hub.credits import credits_md
from ..services.hub.net import HubHTTPError

router = APIRouter(prefix="/api/hub", tags=["hub"])


def _err(e: Exception) -> HTTPException:
    if isinstance(e, NeedsKey):
        return HTTPException(400, str(e))
    if isinstance(e, (ConnectorError, HubHTTPError)):
        return HTTPException(502, paths.redact(str(e)))
    return HTTPException(500, paths.redact(f"{type(e).__name__}: {e}"))


# ------------------------------------------------------------------ sources / keys / stats
@router.get("/sources")
def sources():
    counts = db.counts_by_source()
    return [{**c.describe(), "items": counts.get(c.id, 0)} for c in REGISTRY.values()]


@router.get("/keys")
def get_keys():
    return {"keys": paths.key_status()}


class KeysIn(BaseModel):
    freesound: str | None = None
    polypizza: str | None = None
    sketchfab: str | None = None
    google_fonts: str | None = None


@router.put("/keys")
def put_keys(body: KeysIn):
    # only fields present in the request change; "" clears a key. Values are never echoed back.
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    return {"keys": paths.set_keys(patch)}


@router.get("/stats")
def stats():
    return db.stats()


@router.get("/licenses")
def license_list():
    return [licenses.info(k) for k in licenses.LICENSES if k != "custom:unknown"]


@router.get("/ai/status")
def ai_status():
    return ai.status()


# ------------------------------------------------------------------ local database
@functools.lru_cache(maxsize=64)
def _qvec(q: str):
    return ai.query_vectors(q)


@router.get("/assets")
def list_assets(q: str = "", kind: str = "", source: str = "", license: str = "", rigged: bool = False, animated: bool = False,
                format: str = "", page: int = 1, page_size: int = 48, sort: str = "", vector: bool = True):
    f = {"q": q, "kind": kind, "source": source, "license": license, "rigged": rigged, "animated": animated, "format": format, "sort": sort}
    tv = cv = None
    if q.strip() and vector:
        st = db.stats()
        if st["with_text_embedding"] or st["with_clip"]:
            tv, cv = _qvec(q.strip().lower())
    return db.search(f, page=page, page_size=page_size, text_vec=tv, clip_vec=cv)


@router.get("/assets/{aid}")
def get_asset(aid: str):
    a = db.get(aid)
    if not a:
        raise HTTPException(404, "Asset not found")
    a["license_info"] = licenses.info(a["license"])
    return a


class PatchIn(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None


@router.patch("/assets/{aid}")
def patch_asset(aid: str, body: PatchIn):
    if not db.get(aid):
        raise HTTPException(404, "Asset not found")
    db.update_fields(aid, **body.model_dump(exclude_none=True))
    return db.get(aid)


@router.delete("/assets/{aid}")
def delete_asset(aid: str, files: bool = True):
    rec = pipeline.delete_asset(aid, delete_files=files)
    if not rec:
        raise HTTPException(404, "Asset not found")
    return {"ok": True, "id": aid}


@router.get("/file/{aid}/{role}")
def get_file(aid: str, role: str):
    p = db.file_path(aid, role)
    if not p or not Path(p).exists():
        raise HTTPException(404, "File not found")
    rp = Path(p).resolve()
    if paths.HUB_DIR.resolve() not in rp.parents:
        raise HTTPException(403, "Outside the hub folder")
    return FileResponse(rp, filename=rp.name if role != "thumb" else None)


# ------------------------------------------------------------------ online search + add
@router.get("/online")
def online(source: str, q: str = "", kind: str = "", page: int = 1, policy: str = "commercial"):
    con = REGISTRY.get(source)
    if not con:
        raise HTTPException(404, f"Unknown source {source}")
    try:
        kw = {"policy": policy} if source in ("freesound", "sketchfab") else {}
        res = con.search(q, kind or None, page, **kw)
    except Exception as e:  # noqa: BLE001
        raise _err(e) from None
    for it in res["items"]:
        ex = db.get_by_source(it["source"], it["source_id"])
        it["in_hub"] = ex["id"] if ex else None
        it["allowed"] = licenses.allowed(it["license"], policy)
    res["source"] = source
    return res


class AddIn(BaseModel):
    items: list[dict]
    variants: dict = {}
    policy: str | None = None


@router.post("/add")
def add(body: AddIn):
    items = body.items[:500]
    if not items:
        raise HTTPException(400, "Nothing to add")

    def work(ctx):
        out = {"added": [], "exists": [], "duplicates": [], "skipped": [], "errors": []}
        for i, it in enumerate(items):
            ctx.progress(i / len(items), f"{i + 1}/{len(items)} {it.get('name') or it.get('source_id')}")
            try:
                con = REGISTRY[it["source"]]
                cand = con.get(str(it["source_id"]))   # always re-read server-side; never trust client-supplied URLs
                r = pipeline.add_candidate(cand, variant=body.variants, policy=body.policy, check=ctx.check)
                key = {"added": "added", "exists": "exists", "duplicate": "duplicates"}[r["status"]]
                out[key].append({"id": r["asset"]["id"], "name": r["asset"]["name"]})
            except pipeline.Skipped as e:
                out["skipped"].append({"name": it.get("name"), "reason": str(e)})
            except Exception as e:  # noqa: BLE001
                from ..jobs import JobCancelled
                if isinstance(e, JobCancelled):
                    raise
                out["errors"].append({"name": it.get("name"), "error": paths.redact(f"{e}")[:300]})
        ctx.progress(1, f"Added {len(out['added'])}")
        return out

    job = jobs.submit("hub.add", f"Add {len(items)} asset(s) to the hub", work)
    return job.public()


# ------------------------------------------------------------------ harvest
class HarvestIn(BaseModel):
    sources: list[str] = []
    queries: list[str] = []
    kinds: list[str] = []
    max_items: int = 50
    max_gb: float = 2.0
    license_policy: str = "commercial"
    variants: dict = {"texture_res": "1k"}


def _start_harvest(st: dict) -> dict:
    job = jobs.submit("hub.harvest", f"Harvest {', '.join(REGISTRY[s].name for s in st['params']['sources'])}",
                      lambda ctx: harvest.run(st, ctx))
    st["job_id"] = job.id
    harvest.save(st)
    return {**job.public(), "harvest_id": st["id"]}


@router.post("/harvest")
def start_harvest(body: HarvestIn):
    return _start_harvest(harvest.new_state(body.model_dump()))


@router.get("/harvest")
def harvests():
    return harvest.list_harvests()


@router.get("/harvest/{hid}")
def harvest_state(hid: str):
    st = harvest.load(hid)
    if not st:
        raise HTTPException(404, "Harvest not found")
    return st


@router.post("/harvest/{hid}/resume")
def resume_harvest(hid: str):
    try:
        st = harvest.prepare_resume(hid)
    except KeyError:
        raise HTTPException(404, "Harvest not found") from None
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from None
    return _start_harvest(st)


# ------------------------------------------------------------------ manual imports
class ImportIn(BaseModel):
    path: str
    source: str = "local"
    license: str = "CC0-1.0"
    pack_name: str | None = None
    author: str | None = None
    author_url: str | None = None
    source_url: str | None = None
    kind_hint: str | None = None
    tags: list[str] = []
    to_anim: bool = False


def _submit_import(body: ImportIn, cleanup: Path | None = None) -> dict:
    def work(ctx):
        try:
            return imports.import_pack(body.path, source=body.source, license=body.license, pack_name=body.pack_name,
                                       author=body.author, author_url=body.author_url, source_url=body.source_url,
                                       kind_hint=body.kind_hint, tags=body.tags, to_anim=body.to_anim,
                                       progress=lambda p, m: ctx.progress(p, m))
        finally:
            if cleanup:
                shutil.rmtree(cleanup, ignore_errors=True)
    job = jobs.submit("hub.import", f"Import {Path(body.path).name} into the hub", work)
    return job.public()


@router.post("/import")
def import_path(body: ImportIn):
    if not Path(body.path).expanduser().exists():
        raise HTTPException(400, f"Path not found: {body.path}")
    if body.source == "mixamo":
        body.to_anim = True if body.to_anim is False else body.to_anim
    return _submit_import(body)


@router.post("/import/upload")
async def import_upload(file: UploadFile = File(...), source: str = Form("local"), license: str = Form("CC0-1.0"),
                        pack_name: str = Form(""), author: str = Form(""), source_url: str = Form(""), kind_hint: str = Form("")):
    import uuid
    tmp = paths.TMP_DIR / uuid.uuid4().hex[:10]
    tmp.mkdir(parents=True, exist_ok=True)
    dest = tmp / paths.safe_name(file.filename or "upload.zip", 120)
    with open(dest, "wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)
    body = ImportIn(path=str(dest), source=source, license=license, pack_name=pack_name or Path(file.filename or "pack").stem,
                    author=author or None, source_url=source_url or None, kind_hint=kind_hint or None, to_anim=source == "mixamo")
    return _submit_import(body, cleanup=tmp)


# ------------------------------------------------------------------ enrichment / credits / engine
class IdsIn(BaseModel):
    asset_ids: list[str] = []


@router.post("/enrich")
def enrich(body: IdsIn):
    ids = body.asset_ids or list(dict.fromkeys(db.ids_without("embedding", 5000) + db.ids_without("clip", 5000)))

    def work(ctx):
        done = 0
        for i, aid in enumerate(ids):
            ctx.progress(i / max(1, len(ids)), f"{i + 1}/{len(ids)}")
            if pipeline.enrich(aid):
                done += 1
        return {"processed": len(ids), "updated": done}
    return jobs.submit("hub.enrich", f"Auto-tag / embed {len(ids)} asset(s)", work).public()


@router.post("/credits")
def credits(body: IdsIn):
    return {"markdown": credits_md(body.asset_ids or None), "count": len(body.asset_ids) if body.asset_ids else db.stats()["total"]}


@router.get("/engine/status")
def engine_status():
    st = engine.engine_status()
    st.setdefault("message", "my-engine asset server is running" if st.get("running") else engine.NOT_RUNNING)
    return st


@router.post("/send/my-engine")
def send_my_engine(body: IdsIn):
    if not body.asset_ids:
        raise HTTPException(400, "No assets selected")
    return engine.send(body.asset_ids[:200])
