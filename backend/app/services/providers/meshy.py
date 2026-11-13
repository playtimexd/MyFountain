"""Meshy (https://docs.meshy.ai) text/image -> 3D, refine (texture) and auto-rig. Official docs read Nov 2026.

Verified from the official API pages (api/text-to-3d, api/image-to-3d, api/rigging, api/animation, api/pricing, api/balance):
  * Base https://api.meshy.ai ; auth `Authorization: Bearer <key>`
  * Text to 3D   POST /openapi/v2/text-to-3d  {mode:'preview'|'refine', ...} -> {"result": id}; GET /openapi/v2/text-to-3d/{id}
  * Image to 3D  POST /openapi/v1/image-to-3d {image_url (public URL or base64 data URI), ...}; GET /openapi/v1/image-to-3d/{id}
  * Rigging      POST /openapi/v1/rigging {input_task_id | model_url, height_meters}; GET /openapi/v1/rigging/{id}
                 -> result.rigged_character_glb_url / _fbx_url, result.basic_animations.{walking,running}_glb_url
  * Balance      GET /openapi/v1/balance -> {balance, monthly_credits, permanent_credits}
  * Status PENDING | IN_PROGRESS | SUCCEEDED | FAILED | CANCELED ; consumed_credits ; failed tasks are refunded.
  * RIG TYPES: humanoid (biped) ONLY.  "Not supported: untextured meshes, non-humanoid assets"; quadrupeds fall under
    non-humanoid.  Text-to-motion explicitly rejects quadruped rigs.  Input must be a textured GLB, <= 380k triangles,
    face towards +Z.  -> Meshy cannot rig a bear.
  * Credits (pricing page): text preview 20 (meshy-6/latest) or 5 (meshy-6-lite); refine 10 (2K/4K); image-to-3d with
    texture 30 (latest/meshy-6) or 15 (meshy-6-lite); rigging 5; walking+running clips come with the rig.
NOT verified: rate-limit values, asset retention period (`expires_at` is returned per task), live behaviour.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ...jobs import JobCancelled
from . import common as C
from .common import ProviderError

BASE = "https://api.meshy.ai"
MODELS = {"draft": "meshy-6-lite", "standard": "latest"}
CREDITS = {  # from docs.meshy.ai/en/api/pricing
    "text_preview": {"draft": 5, "standard": 20}, "text_refine": 10,
    "image_textured": {"draft": 15, "standard": 30}, "rig": 5,
}
RIG_TYPES = ["biped"]


def key() -> str:
    return (C.settings().get("meshy_api_key") or "").strip()


def listing() -> dict:
    k = key()
    return {
        "id": "meshy", "name": "Meshy - cloud", "available": bool(k), "needs_key": True, "key_set": bool(k), "cloud": True, "verified": True,
        "cost_note": ("Credits on your Meshy account (paid plan required for the API). Draft text-to-3D ~15 credits (5 mesh + 10 texture), "
                      "standard ~30; image-to-3D 15 / 30; auto-rig +5. Failed tasks are refunded."),
        "capabilities": {
            "text_to_3d": True, "image_to_3d": True, "refine": True, "rig": RIG_TYPES, "animals_rig": False,
            "animation": ["walk", "run"], "formats": ["glb"], "quality": ["draft", "standard"], "outputs_per_request": 1,
            "rig_note": "Auto-rig works for textured HUMANOID (biped) models only. Quadrupeds such as a bear are not supported - use Tripo for animals.",
        },
        "note": "", "howto": "" if k else "Paste your Meshy API key in Settings (gear icon) -> 3D generation -> Meshy API key.",
    }


def _plan(req: dict) -> dict:
    q = req.get("quality") or "draft"
    if q not in MODELS:
        raise ProviderError("quality must be 'draft' or 'standard'.")
    rig = req.get("rig") or None
    rtype = None
    if rig:
        rtype = (rig.get("type") or "biped").lower()
        if rtype == "humanoid":
            rtype = "biped"
        if rtype not in RIG_TYPES:
            raise ProviderError(f"Meshy auto-rig supports humanoid (biped) characters only, not '{rtype}'. Use Tripo for quadrupeds/animals.")
    anim = req.get("animation")
    if anim and not rig:
        raise ProviderError("Basic animations come with the rig: enable auto-rig to get them.")
    if anim and anim not in ("walk", "run"):
        raise ProviderError("Meshy basic animations are 'walk' and 'run'.")
    if not req.get("prompt") and not req.get("image_mediaId"):
        raise ProviderError("Give a prompt or a start image.")
    if req.get("prompt") and len(req["prompt"]) > 800:
        raise ProviderError("Meshy prompts are limited to 800 characters.")
    return {"quality": q, "model": MODELS[q], "rig": rtype, "height": float((rig or {}).get("height_meters") or 1.7), "animation": anim}


def estimate(req: dict) -> dict:
    p = _plan(req)
    use_img = bool(req.get("image_mediaId"))
    credits = CREDITS["image_textured"][p["quality"]] if use_img else CREDITS["text_preview"][p["quality"]] + CREDITS["text_refine"]
    if p["rig"]:
        credits += CREDITS["rig"]
    return {"provider": "meshy", "currency": "credits", "amount": credits, "unit": "Meshy credits",
            "note": ("From the Meshy pricing page: " + ("image-to-3D (textured)" if use_img else "text preview + refine")
                     + (" + auto-rig 5" if p["rig"] else "") + ". Charged per task; failed tasks are refunded. Ultra geometry / 8K textures are not used.")}


def _preview_body(req: dict, p: dict) -> dict:
    return {"mode": "preview", "prompt": req["prompt"], "ai_model": p["model"], "model_type": "standard",
            "pose_mode": "t-pose" if p["rig"] else "", "target_formats": ["glb"]}


def _refine_body(p: dict, preview_id: str) -> dict:
    b: dict[str, Any] = {"mode": "refine", "preview_task_id": preview_id, "enable_pbr": True, "target_formats": ["glb"]}
    if p["model"] == "meshy-6-lite":
        b["remove_lighting"] = False     # docs: remove_lighting=true returns 400 on meshy-6-lite
    return b


def _image_body(p: dict, uri: str) -> dict:
    return {"image_url": uri, "ai_model": p["model"], "should_texture": True, "enable_pbr": True,
            "pose_mode": "t-pose" if p["rig"] else "", "target_formats": ["glb"]}


def dry_run(req: dict) -> dict:
    p = _plan(req)
    h = {"Authorization": f"Bearer {key() or '<not set>'}", "Content-Type": "application/json"}
    reqs = []
    if req.get("image_mediaId"):
        reqs.append(C.describe_request("POST", f"{BASE}/openapi/v1/image-to-3d", h, _image_body(p, "data:image/jpeg;base64,AAAA")))
        last = "<image-to-3d task id>"
    else:
        reqs.append(C.describe_request("POST", f"{BASE}/openapi/v2/text-to-3d", h, _preview_body(req, p)))
        reqs.append(C.describe_request("POST", f"{BASE}/openapi/v2/text-to-3d", h, _refine_body(p, "<preview task id>")))
        last = "<refine task id>"
    if p["rig"]:
        reqs.append(C.describe_request("POST", f"{BASE}/openapi/v1/rigging", h, {"input_task_id": last, "height_meters": p["height"]}))
    reqs.append(C.describe_request("GET", "<model_urls.glb / result.rigged_character_glb_url (pre-signed)>", {}, None, "downloaded without credentials"))
    return {"provider": "meshy", "requests": reqs, "estimate": estimate(req)}


def balance() -> dict:
    if not key():
        raise ProviderError("Meshy API key is not set.")
    with C.make_client(headers=C.bearer(key()), timeout=20.0) as cl:
        r = C.check(cl.get(f"{BASE}/openapi/v1/balance"), "Meshy balance")
        j = r.json()
        return {"balance": j.get("balance"), "monthly": j.get("monthly_credits"), "permanent": j.get("permanent_credits"), "unit": "credits"}


def _wait(ctx, cl, path: str, label: str, lo: float, hi: float, timeout: float = 1500.0) -> dict:
    t0 = time.monotonic()
    while True:
        C.nap(ctx, 5.0)
        try:
            r = cl.get(f"{BASE}{path}")
        except Exception:
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            continue
        C.check(r, f"Meshy {label} status")
        j = r.json()
        st = j.get("status")
        pr = (j.get("progress") or 0) / 100.0
        ctx.progress(lo + (hi - lo) * pr, f"Meshy {label}: {st.lower() if st else '?'} {int(pr * 100)}%")
        if st == "SUCCEEDED":
            return j
        if st in ("FAILED", "CANCELED"):
            err = (j.get("task_error") or {}).get("message") or ""
            raise ProviderError(C.scrub(f"Meshy {label} {st.lower()}: {err}".strip(": ")))
        if time.monotonic() - t0 > timeout:
            raise ProviderError(f"Meshy {label} timed out after {int(timeout / 60)} minutes (task {path.rsplit('/', 1)[-1]} may still finish on Meshy).")


def run(ctx, req: dict, dest_dir: Path, base_name: str) -> dict:
    k = key()
    if not k:
        raise ProviderError("Meshy API key is not set. Open Settings (gear icon) and paste your key.")
    p = _plan(req)
    headers = {**C.bearer(k), "Content-Type": "application/json"}
    credits = 0
    created: list[tuple[str, str]] = []     # (path, id) of tasks so they can be cancelled
    files: dict[str, str] = {}
    with C.make_client(headers=headers, timeout=60.0) as cl:
        try:
            use_rig = bool(p["rig"])
            if req.get("image_mediaId"):
                ctx.progress(0.01, "Preparing image")
                uri = C.data_uri(C.media_image_path(req["image_mediaId"]), max_side=2048, max_bytes=15_000_000)
                r = C.check(cl.post(f"{BASE}/openapi/v1/image-to-3d", json=_image_body(p, uri)), "Meshy image-to-3D")
                tid = r.json()["result"]
                created.append(("/openapi/v1/image-to-3d", tid))
                hi = 0.7 if use_rig else 0.88
                t = _wait(ctx, cl, f"/openapi/v1/image-to-3d/{tid}", "image-to-3D", 0.03, hi)
                credits += t.get("consumed_credits") or 0
                model_task, model_json = tid, t
            else:
                r = C.check(cl.post(f"{BASE}/openapi/v2/text-to-3d", json=_preview_body(req, p)), "Meshy text-to-3D preview")
                pid = r.json()["result"]
                created.append(("/openapi/v2/text-to-3d", pid))
                t1 = _wait(ctx, cl, f"/openapi/v2/text-to-3d/{pid}", "preview mesh", 0.03, 0.3)
                credits += t1.get("consumed_credits") or 0
                r = C.check(cl.post(f"{BASE}/openapi/v2/text-to-3d", json=_refine_body(p, pid)), "Meshy text-to-3D refine")
                rid = r.json()["result"]
                created.append(("/openapi/v2/text-to-3d", rid))
                t2 = _wait(ctx, cl, f"/openapi/v2/text-to-3d/{rid}", "texturing", 0.3, 0.7 if use_rig else 0.88)
                credits += t2.get("consumed_credits") or 0
                model_task, model_json = rid, t2
            glb = (model_json.get("model_urls") or {}).get("glb")
            if not glb:
                raise ProviderError("Meshy finished but returned no GLB.")
            ctx.progress(0.89 if not use_rig else 0.72, "Downloading model")
            files["model"] = str(C.download(glb, dest_dir / f"{base_name}.glb", ctx))
            preview = None
            if model_json.get("thumbnail_url"):
                try:
                    preview = str(C.download(model_json["thumbnail_url"], dest_dir / f"{base_name}_preview.png", ctx))
                except Exception:
                    preview = None
            anims: dict[str, str] = {}
            if use_rig:
                r = C.check(cl.post(f"{BASE}/openapi/v1/rigging", json={"input_task_id": model_task, "height_meters": p["height"]}), "Meshy rigging")
                gid = r.json()["result"]
                created.append(("/openapi/v1/rigging", gid))
                t3 = _wait(ctx, cl, f"/openapi/v1/rigging/{gid}", "auto-rig", 0.74, 0.94)
                credits += t3.get("consumed_credits") or 0
                res = t3.get("result") or {}
                if not res.get("rigged_character_glb_url"):
                    raise ProviderError("Meshy rigging finished but returned no rigged GLB.")
                ctx.progress(0.95, "Downloading rigged model")
                files["rigged"] = str(C.download(res["rigged_character_glb_url"], dest_dir / f"{base_name}_rigged.glb", ctx))
                ba = res.get("basic_animations") or {}
                for nm, kk in (("walk", "walking_glb_url"), ("run", "running_glb_url")):
                    if p["animation"] == nm and ba.get(kk):
                        anims[nm] = str(C.download(ba[kk], dest_dir / f"{base_name}_{nm}.glb", ctx))
            return {"files": files, "animations": anims, "preview_image": preview, "credits": credits, "credit_unit": "Meshy credits",
                    "rig_type": p["rig"], "meta": {"model_task": model_task, "ai_model": p["model"]}}
        except JobCancelled:
            for path, tid in reversed(created):  # DELETE refunds PENDING tasks; running ones cannot be deleted (409)
                try:
                    cl.delete(f"{BASE}{path}/{tid}")
                except Exception:
                    pass
            ctx.job.message = "Cancelled. Meshy tasks that had already started are still charged."
            raise
