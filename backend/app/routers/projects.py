"""Projects API: saved work per editor area (video / image / anim / model / segment) + a persistent job history per area.

  GET    /api/projects?kind=video&q=&sort=opened|updated|created|name&limit=   list (newest opened first)
  POST   /api/projects                {kind, name, doc?, meta?, tags?}        create
  GET    /api/projects/{id}                                                   metadata
  GET    /api/projects/{id}/doc                                               the document (raw JSON, as saved)
  PUT    /api/projects/{id}/doc?name=&meta=<json>&rev=   body = raw JSON      save (autosave); rev = optimistic lock
  PATCH  /api/projects/{id}           {name?, tags?, meta?}                   rename / tag / annotate
  POST   /api/projects/{id}/open                                              mark as opened (sorts it first)
  POST   /api/projects/{id}/duplicate {name?}                                 copy (parent_id = original)
  PUT    /api/projects/{id}/thumb     body = image/jpeg|png|webp              preview image
  GET    /api/projects/{id}/thumb
  DELETE /api/projects/{id}?purge=false                                       move to data/projects/_trash (purge: erase)
  GET    /api/projects/activity?area=download,ai&limit=&status=               finished background jobs (persistent)
  DELETE /api/projects/activity/{job_id}  |  DELETE /api/projects/activity?area=ai
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..jobs import jobs
from ..services import projects as store

router = APIRouter(prefix="/api/projects", tags=["projects"])

# persist every finished job (download / AI / animation ...) so tool areas have a history across restarts
_listeners = getattr(jobs, "listeners", None)
if isinstance(_listeners, list) and store.record_job not in _listeners:
    _listeners.append(store.record_job)


def _nf(pid: str):
    return HTTPException(404, f"project {pid} not found")


# ------------------------------------------------------------------ activity (declared before /{pid} routes)
@router.get("/activity")
def activity(area: str | None = None, limit: int = 50, status: str | None = None):
    return store.list_activity(area, limit, status)


@router.delete("/activity/{job_id}")
def activity_delete(job_id: str):
    return {"deleted": store.delete_activity(aid=job_id)}


@router.delete("/activity")
def activity_clear(area: str):
    return {"deleted": store.delete_activity(area=area)}


# ------------------------------------------------------------------ projects
@router.get("")
def list_projects(kind: str | None = None, q: str | None = None, sort: str = "opened", limit: int = 200, offset: int = 0):
    try:
        return store.list_projects(kind, q, limit, offset, sort)
    except ValueError as e:
        raise HTTPException(400, str(e))


class CreateReq(BaseModel):
    kind: str
    name: str = "Untitled project"
    doc: Any = None
    meta: dict = {}
    tags: list[str] = []


@router.post("")
def create(req: CreateReq):
    try:
        return store.create(req.kind, req.name, req.doc, req.meta, req.tags)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{pid}")
def get(pid: str):
    try:
        return store.get(pid)
    except store.NotFound:
        raise _nf(pid)


@router.get("/{pid}/doc")
def get_doc(pid: str):
    try:
        f = store.doc_path(pid)
    except store.NotFound:
        raise _nf(pid)
    if not f:
        raise HTTPException(404, "this project has no saved document yet")
    return FileResponse(f, media_type="application/json", headers={"Cache-Control": "no-store"})


@router.put("/{pid}/doc")
async def put_doc(pid: str, request: Request, name: str | None = None, meta: str | None = None, rev: int | None = None):
    try:
        m = json.loads(meta) if meta else None
        if m is not None and not isinstance(m, dict):
            raise ValueError("meta must be a JSON object")
    except ValueError as e:
        raise HTTPException(400, f"bad meta: {e}")
    body = await request.body()
    try:
        return store.save_doc(pid, body, name, m, rev)
    except store.NotFound:
        raise _nf(pid)
    except ValueError as e:
        raise HTTPException(409 if str(e).startswith("conflict") else 400, str(e))


class PatchReq(BaseModel):
    name: str | None = None
    tags: list[str] | None = None
    meta: dict | None = None


@router.patch("/{pid}")
def patch(pid: str, req: PatchReq):
    try:
        return store.patch(pid, req.name, req.tags, req.meta)
    except store.NotFound:
        raise _nf(pid)


@router.post("/{pid}/open")
def open_(pid: str):
    try:
        return store.touch(pid)
    except store.NotFound:
        raise _nf(pid)


class DupReq(BaseModel):
    name: str | None = None


@router.post("/{pid}/duplicate")
def duplicate(pid: str, req: DupReq | None = None):
    try:
        return store.duplicate(pid, req.name if req else None)
    except store.NotFound:
        raise _nf(pid)


@router.put("/{pid}/thumb")
async def put_thumb(pid: str, request: Request):
    try:
        return store.save_thumb(pid, await request.body(), request.headers.get("content-type", ""))
    except store.NotFound:
        raise _nf(pid)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{pid}/thumb")
def get_thumb(pid: str):
    try:
        f = store.thumb_path(pid)
    except store.NotFound:
        raise _nf(pid)
    if not f:
        raise HTTPException(404, "no thumbnail")
    return FileResponse(f, headers={"Cache-Control": "no-cache"})


@router.delete("/{pid}")
def delete(pid: str, purge: bool = False):
    try:
        return store.delete(pid, purge)
    except store.NotFound:
        raise _nf(pid)
