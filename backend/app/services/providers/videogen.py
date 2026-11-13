"""Reference-video generation orchestration (provider choice, cost gate, job body). Used by routers/videogen.py."""
from __future__ import annotations

from pathlib import Path

from ... import config
from ...ingest import ingest_file
from . import comfyui, seedance
from . import common as C
from .common import ProviderError

CLOUD = {"seedance"}
IDS = ["seedance", "local"]

CAPTURE_BLOCK = (
    "Static locked-off camera, no camera movement, no zoom, no cuts. The subject's entire body is visible from head to feet for the whole "
    "clip and fills about two thirds of the frame height, shown in clean side view (or three-quarter view), moving steadily in a straight "
    "line on flat ground. Plain uncluttered neutral background, even soft daylight, no other people or animals, no text or watermark. "
    "Natural, evenly paced motion with clearly visible arms and legs."
)


def build_prompt(req: dict) -> str:
    p = (req.get("prompt") or "").strip()
    if req.get("capture_friendly"):
        p = f"{p.rstrip('. ')}. {CAPTURE_BLOCK}" if p else CAPTURE_BLOCK
    return p


def providers() -> list[dict]:
    return [seedance.listing(), comfyui.listing()]


def resolve_provider(req: dict) -> str:
    pid = (req.get("provider") or "").strip().lower()
    if pid in ("", "auto"):
        pid = (C.settings().get("videogen_provider") or "auto").strip().lower()
    if pid in ("", "auto"):
        p = comfyui.probe(timeout=1.5)
        if any(w["ready"] for w in p["workflows"]):
            return "local"
        return "seedance" if seedance.cfg()["key"] else "local"
    if pid not in IDS:
        raise ProviderError(f"Unknown video provider '{pid}'. Use auto, seedance or local.")
    return pid


def estimate(pid: str, req: dict) -> dict:
    return seedance.estimate(req) if pid == "seedance" else comfyui.estimate(req)


def dry_run(pid: str, req: dict) -> dict:
    return seedance.dry_run(req) if pid == "seedance" else comfyui.dry_run(req)


def validate(pid: str, req: dict) -> None:
    if not (req.get("prompt") or "").strip():
        raise ProviderError("Write a prompt first.")
    if req.get("image_mediaId"):
        C.media_image_path(req["image_mediaId"])      # existence / kind check, no network
    estimate(pid, req)                                # raises on cap violations


def run_job(ctx, pid: str, req: dict) -> dict:
    adapter = seedance if pid == "seedance" else comfyui
    out = adapter.run(ctx, req, config.MEDIA_DIR)
    ctx.progress(0.97, "Adding to library")
    title = (req.get("_orig_prompt") or req["prompt"]).strip().replace("\n", " ")[:40]
    row = ingest_file(Path(out["file"]), origin="gen", name=f"Gen video: {title}", tags=["videogen", pid],
                      meta={"prompt": req.get("_orig_prompt") or req["prompt"], "capture_friendly": bool(req.get("capture_friendly")),
                            "provider": pid, **{k: v for k, v in out["meta"].items() if v is not None}})
    return {"media": row, "provider": pid, "info": out["meta"]}
