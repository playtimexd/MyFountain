"""Starter packs: free characters and base meshes (official sources, clear licences) installed with one click.  See services/hub/starter.py."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..jobs import jobs
from ..services.hub import starter

router = APIRouter(prefix="/api/hub/starter", tags=["hub"])


@router.get("")
def catalog():
    return starter.catalog()


@router.post("/thumbs")
def thumbs():
    """Render missing thumbnails for hub 3D models."""
    return jobs.submit("hub.thumbs", "Render 3D model thumbnails", lambda ctx: starter.render_model_thumbs(ctx)).public()


@router.post("/{pid}")
def install(pid: str):
    p = starter.PACKS.get(pid)
    if not p:
        raise HTTPException(404, "unknown pack")
    return jobs.submit("hub.starter", f"Install {p['name']}"[:100], lambda ctx: starter.install(ctx, pid)).public()
