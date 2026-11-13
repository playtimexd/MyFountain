"""Intake: import files from cloud drives (OneDrive via an AI agent / MCP), links and sync folders, file them into the right
library and auto-categorise.  See app/services/intake.py and app/services/classify.py."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..jobs import jobs
from ..services import classify, intake

router = APIRouter(prefix="/api/intake", tags=["intake"])


class UrlItem(BaseModel):
    url: str
    name: str | None = None              # file name incl. extension (recommended: links often hide it)
    category: str | None = None          # skip auto-categorisation and use this category
    tags: list[str] = Field(default_factory=list)


class UrlsReq(BaseModel):
    items: list[UrlItem]
    categorize: bool = True
    source: str = "link"                 # tag added to every item, e.g. "onedrive"


@router.post("/urls")
def import_urls(req: UrlsReq):
    if not req.items or len(req.items) > 200:
        raise HTTPException(400, "Send 1..200 items.")
    for it in req.items:
        try:
            intake.check_url(it.url)
        except ValueError as e:
            raise HTTPException(400, f"{it.name or it.url[:60]}: {e}")
    src = intake.safe_filename(req.source or "link", "link")[:24]
    items = [i.model_dump() for i in req.items]
    return jobs.submit("intake", f"Import {len(items)} file(s) from {src}", lambda ctx: intake.import_urls(ctx, items, req.categorize, src)).public()


class ChunkReq(BaseModel):
    name: str
    data_base64: str
    upload_id: str | None = None
    offset: int | None = None


@router.post("/upload/chunk")
def upload_chunk(req: ChunkReq):
    try:
        return intake.upload_chunk(req.upload_id, req.name, req.data_base64, req.offset)
    except ValueError as e:
        raise HTTPException(400, str(e))


class FinishReq(BaseModel):
    upload_id: str
    categorize: bool = True
    category: str | None = None
    tags: list[str] = Field(default_factory=list)


@router.post("/upload/finish")
def upload_finish(req: FinishReq):
    return jobs.submit("intake", "Import uploaded file", lambda ctx: intake.finish_upload(ctx, req.upload_id, req.categorize, req.category, req.tags)).public()


@router.get("/cloud-folders")
def cloud_folders():
    return intake.cloud_folders()


class LocalReq(BaseModel):
    paths: list[str]
    recursive: bool = True
    categorize: bool = True


@router.post("/local")
def import_local(req: LocalReq):
    if not req.paths:
        raise HTTPException(400, "paths is required")
    return jobs.submit("intake", f"Import {len(req.paths)} path(s)", lambda ctx: intake.import_local(ctx, req.paths, req.recursive, req.categorize)).public()


class CategorizeReq(BaseModel):
    ids: list[str] | None = None
    only_missing: bool = True


@router.post("/categorize")
def categorize(req: CategorizeReq):
    return jobs.submit("classify", "Auto-categorise media", lambda ctx: intake.categorize(ctx, req.ids, req.only_missing)).public()


@router.get("/categories")
def categories():
    return {"categories": classify.categories(), "taxonomy": {k: [c for c, _ in v] for k, v in classify.TAXONOMY.items()},
            "audio": [c for c, _ in classify.AUDIO_KEYS], "other": ["3d-model", "motion", "font"]}
