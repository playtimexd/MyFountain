"""Export to a game-engine project (Godot / Unity / Unreal / three.js / plain folder).  See app/services/engine_export.py."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..jobs import jobs
from ..services import engine_export as ee

router = APIRouter(prefix="/api/engine", tags=["engine"])


@router.get("/projects")
def projects(refresh: bool = False, root: str | None = None):
    """Engine projects found on this PC (D:\, Documents, Desktop by default; or under `root`)."""
    return ee.find_projects([root] if root else None, refresh=refresh)


@router.get("/detect")
def detect(path: str):
    info = ee.detect_engine(path)
    if not info:
        raise HTTPException(404, "No Godot, Unity, Unreal or three.js project in that folder.")
    return info


class ExportReq(BaseModel):
    asset: dict[str, Any] = Field(..., description="{type: character, rig_id, clip_ids?} | {type: fur, groom_id} | {type: model, ref} | {type: media, media_id}")
    engine: str
    project: str
    name: str | None = None
    overwrite: bool = False


@router.post("/export")
def export(req: ExportReq):
    if req.engine not in ee.ENGINES:
        raise HTTPException(400, f"engine must be one of {', '.join(ee.ENGINES)}")
    if req.engine != "folder":
        info = ee.detect_engine(req.project)
        if not info or info["engine"] != req.engine:
            raise HTTPException(400, f"{req.project} is not a {req.engine} project" + (f" (it looks like {info['engine']})" if info else ""))
    if req.asset.get("type") not in ("character", "fur", "model", "media"):
        raise HTTPException(400, "asset.type must be character, fur, model or media")
    title = f"Export to {req.engine}: {req.name or req.asset.get('type')}"
    return jobs.submit("engine-export", title[:100], lambda ctx: ee.export(ctx, req.asset, req.engine, req.project, req.name, req.overwrite)).public()
