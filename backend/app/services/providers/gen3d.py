"""3D character generation orchestration (provider choice, output naming, library ingest, rig-library hand-off)."""
from __future__ import annotations

import time
from pathlib import Path

import httpx

from ... import config
from ...ingest import ingest_file
from . import common as C
from . import meshy, trellis_local, tripo
from .common import ProviderError

INCOMING = config.DATA / "anim" / "incoming"
INCOMING.mkdir(parents=True, exist_ok=True)
ADAPTERS = {"meshy": meshy, "tripo": tripo, "local": trellis_local}
FREE = {"local"}                     # no cost confirmation needed
IDS = list(ADAPTERS)


def providers() -> list[dict]:
    return [trellis_local.listing(), meshy.listing(), tripo.listing()]


def resolve_provider(req: dict) -> str:
    pid = (req.get("provider") or "").strip().lower()
    if pid in ("", "auto"):
        pid = (C.settings().get("gen3d_provider") or "auto").strip().lower()
    if pid in ("", "auto"):
        rt = ((req.get("rig") or {}).get("type") or "").lower()
        needs_animal = bool(req.get("rig")) and rt not in ("", "biped", "humanoid")
        mk, tk = bool(meshy.key()), bool(tripo.key())
        if not mk and not tk and trellis_local.listing()["available"]:
            return "local"                # no cloud keys: the free local generator
        if needs_animal or (req.get("rig") and not mk):
            return "tripo"
        return "meshy" if mk or not tk else "tripo"
    if pid not in IDS:
        raise ProviderError(f"Unknown 3D provider '{pid}'. Use auto, local, meshy or tripo.")
    return pid


def estimate(pid: str, req: dict) -> dict:
    return ADAPTERS[pid].estimate(req)


def dry_run(pid: str, req: dict) -> dict:
    return ADAPTERS[pid].dry_run(req)


def validate(pid: str, req: dict) -> None:
    if req.get("image_mediaId"):
        C.media_image_path(req["image_mediaId"])
    ADAPTERS[pid].estimate(req)          # runs the adapter's plan validation (rig type, prompt length, ...)


def file_url(name: str) -> str:
    return f"/api/gen3d/file/{name}"


def safe_file(name: str) -> Path:
    if not name or "/" in name or "\\" in name or ".." in name or not name.lower().endswith(".glb") and not name.lower().endswith(".png"):
        raise ProviderError("Bad file name.", 400)
    p = (INCOMING / name).resolve()
    if p.parent != INCOMING.resolve() or not p.is_file():
        raise ProviderError("File not found.", 404)
    return p


def import_rig(ctx, path: str, name: str | None = None, *, timeout: float = 240.0) -> dict:
    """Hand a GLB to anim-core's rig library if its endpoint exists. Never raises; returns a status dict."""
    url = f"http://127.0.0.1:{config.PORT}"
    try:
        with C.make_client(local=True, timeout=30.0) as cl:
            r = cl.post(f"{url}/api/anim/rigs/import", json={"path": path})
            if r.status_code in (404, 405):
                return {"imported": False, "reason": "The rig library endpoint (/api/anim/rigs/import) is not available in this engine yet."}
            if r.status_code >= 400:
                return {"imported": False, "reason": f"Rig import refused: {C.error_message(r)}"}
            job = r.json()
            t0 = time.monotonic()
            while job.get("status") in ("queued", "running"):
                if time.monotonic() - t0 > timeout:
                    return {"imported": False, "reason": "Rig import is still running; check the rig library later.", "job": job.get("id")}
                C.nap(ctx, 1.0)
                job = cl.get(f"{url}/api/jobs/{job['id']}").json()
            if job.get("status") != "done":
                return {"imported": False, "reason": job.get("error") or f"Rig import {job.get('status')}"}
            res = job.get("result") or {}
            row = res.get("rig") or res.get("row") or (res if res.get("id") else None)
            if not row:
                rigs = res.get("rigs") or res.get("items")
                row = rigs[0] if rigs else None
            if not row:
                return {"imported": True, "rig": None, "result": res}
            return {"imported": True, "rig": {**row, "id": row.get("id"), "name": row.get("name") or name or Path(path).stem}}
    except httpx.HTTPError as e:
        return {"imported": False, "reason": f"Rig library not reachable ({type(e).__name__})."}


def run_job(ctx, pid: str, req: dict) -> dict:
    seed_name = C.safe_name((req.get("name") or req.get("prompt") or "character")[:30], "character", 24)
    base = f"{seed_name}_{C.stamp()}_{pid}"
    res = ADAPTERS[pid].run(ctx, req, INCOMING, base)
    files = res["files"]
    main = files.get("rigged") or files["model"]
    out: dict = {"provider": pid, "file": main, "name": Path(main).name, "url": file_url(Path(main).name),
                 "model": {"file": files["model"], "url": file_url(Path(files["model"]).name)},
                 "rigged": bool(files.get("rigged")), "rig_type": res.get("rig_type"),
                 "credits": res.get("credits"), "credit_unit": res.get("credit_unit"), "info": res.get("meta"),
                 "animations": {k: {"file": v, "url": file_url(Path(v).name)} for k, v in (res.get("animations") or {}).items()}}
    if files.get("rigged"):
        out["rigged_file"] = {"file": files["rigged"], "url": file_url(Path(files["rigged"]).name)}
    if res.get("preview_image"):
        try:
            out["preview_media"] = ingest_file(res["preview_image"], origin="gen", name=f"3D preview: {seed_name}", tags=["gen3d", pid],
                                               meta={"prompt": req.get("prompt"), "model_file": main})
        except Exception:
            pass
    if files.get("rigged"):
        ctx.progress(0.98, "Adding to rig library")
        imp = import_rig(ctx, main, seed_name)
        out["rig_import"] = imp
        if imp.get("rig"):
            out["rig"] = imp["rig"]
    return out
