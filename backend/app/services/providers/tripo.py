"""Tripo (https://developers.tripo3d.com, API v3) text/image -> 3D, rig-check, auto-rig and preset animation.

Verified from the official docs (llms.txt index + raw pages: quick-start, authentication, generation-*-to-model,
task-query, task-lifecycle, files, account, animations-rig, animations-rig-check, animations-retarget, error-handling):
  * Base https://openapi.tripo3d.com/v3 ; auth `Authorization: Bearer <key>`
  * Upload        POST /files (multipart field `file`; images JPEG/PNG <= 20 MB) -> data.file_token
  * Text->3D      POST /generation/text-to-model {prompt (<=1024), model, ...}           -> data.task_id
  * Image->3D     POST /generation/image-to-model {input: file_token|url|task_id, model, ...}
  * Models        H series v3.1-20260211 (recommended) / v3.0-20250812 / v2.5-20250123; P series P1-20260311, P2-20260801
  * Poll          GET /tasks/{id} -> data.{status: queued|running|success|failed|cancelled, progress, output.model_url,
                  output.rendered_image_url, credits_consumed, error_code, error_message}
  * Rig-check     POST /animations/rig-check {input: task_id} -> output.{riggable, rig_type}   (credits_consumed 0.00 in the doc example)
  * Auto-rig      POST /animations/rig {input: task_id, model, rig_type, spec, out_format}
        model v1.0-20240301 = biped only; v2.5-20260210 = non-humanoid (quadruped, hexapod, octopod, serpentine, aquatic, avian);
        v3.0-20260909 = recommended for humanoids, also `others`.  rig_type: biped|quadruped|hexapod|octopod|avian|serpentine|aquatic|others.
        spec (tripo|mixamo|actorcore|unity|unreal|vrm) only applies to biped.  Input GLB/FBX/OBJ/STL <= 150 MB.
        The docs state no pose / size / face-count requirement.
  * Retarget      POST /animations/retarget {input: rig task id, animation: 'preset:walk' ...}; rig v2.5/v3.0 presets:
        biped idle/walk/run/dive/climb/jump/slash/shoot/hurt/fall/turn; quadruped ONLY `preset:quadruped:walk`.
  * Balance       GET /account/balance -> data.{balance, frozen}; credits are frozen at creation, failed/cancelled are not charged.
NOT verified: a price table (the docs publish none - only example values), a task-cancel endpoint (none documented),
output URL lifetime, live behaviour.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ...jobs import JobCancelled
from . import common as C
from .common import ProviderError

BASE = "https://openapi.tripo3d.com/v3"
GEN_MODEL = "v3.1-20260211"
RIG_TYPES = ["biped", "quadruped", "hexapod", "octopod", "avian", "serpentine", "aquatic", "others"]
RIG_MODEL = {"biped": "v3.0-20260909", "others": "v3.0-20260909"}      # all other types -> v2.5-20260210
RIG_MODEL_NONHUMANOID = "v2.5-20260210"
SPECS = ["tripo", "mixamo", "actorcore", "unity", "unreal", "vrm"]
ANIM_PRESETS = {"biped": {"walk": "preset:walk", "run": "preset:run"}, "quadruped": {"walk": "preset:quadruped:walk"},
                "hexapod": {"walk": "preset:hexapod:walk"}, "octopod": {"walk": "preset:octopod:walk"},
                "serpentine": {"walk": "preset:serpentine:march"}, "aquatic": {"walk": "preset:aquatic:march"}}


def key() -> str:
    return (C.settings().get("tripo_api_key") or "").strip()


def listing() -> dict:
    k = key()
    return {
        "id": "tripo", "name": "Tripo - cloud", "available": bool(k), "needs_key": True, "key_set": bool(k), "cloud": True, "verified": True,
        "cost_note": ("Credits on your Tripo account. Tripo's API docs publish no price table, so CutLab cannot estimate the cost - "
                      "check 'Balance' before and after; failed tasks are not charged."),
        "capabilities": {
            "text_to_3d": True, "image_to_3d": True, "refine": False, "rig": ["auto"] + RIG_TYPES, "animals_rig": True,
            "animation": ["walk", "run"], "formats": ["glb"], "outputs_per_request": 1,
            "rig_note": ("Auto-rig supports biped, quadruped, hexapod, octopod, avian, serpentine, aquatic and others (non-humanoid needs rig model v2.5). "
                         "Choose 'auto' to let Tripo's free rig-check pick the type. Preset animations: biped walk/run; quadruped walk only."),
        },
        "note": "", "howto": "" if k else "Paste your Tripo API key in Settings (gear icon) -> 3D generation -> Tripo API key.",
    }


def _plan(req: dict) -> dict:
    if not req.get("prompt") and not req.get("image_mediaId"):
        raise ProviderError("Give a prompt or a start image.")
    if req.get("prompt") and len(req["prompt"]) > 1024:
        raise ProviderError("Tripo prompts are limited to 1024 characters.")
    rig = req.get("rig") or None
    rtype = None
    spec = None
    if rig:
        rtype = (rig.get("type") or "auto").lower()
        if rtype == "humanoid":
            rtype = "biped"
        if rtype != "auto" and rtype not in RIG_TYPES:
            raise ProviderError(f"Unknown rig type '{rtype}'. Tripo supports: auto, {', '.join(RIG_TYPES)}.")
        spec = rig.get("spec")
        if spec and spec not in SPECS:
            raise ProviderError(f"Unknown biped skeleton spec '{spec}'.")
    anim = req.get("animation")
    if anim and not rig:
        raise ProviderError("Preset animations need an auto-rig: enable rigging first.")
    if anim and anim not in ("walk", "run"):
        raise ProviderError("Tripo preset animations supported here: 'walk', 'run' (run is biped only).")
    if anim == "run" and rtype not in (None, "auto", "biped"):
        raise ProviderError("Only biped rigs have a 'run' preset; quadrupeds have 'walk' only.")
    return {"rig": rtype, "spec": spec, "animation": anim, "model": req.get("tripo_model") or GEN_MODEL}


def _hint(rtype: str | None) -> str:
    if rtype == "biped":
        return ", full body, T-pose, plain background"
    if rtype in ("quadruped", "hexapod", "octopod", "others", "auto"):
        return ", full body, neutral standing pose with legs apart, side view, plain background"
    return ", full body, plain background"


def estimate(req: dict) -> dict:
    p = _plan(req)
    steps = ["image-to-model" if req.get("image_mediaId") else "text-to-model"]
    if p["rig"]:
        steps += (["rig-check"] if p["rig"] == "auto" else []) + ["auto-rig"]
    if p["animation"]:
        steps.append("preset animation")
    return {"provider": "tripo", "currency": "credits", "amount": None, "unit": "Tripo credits",
            "note": "Tripo publishes no price table in its API docs, so the cost is unknown to CutLab (steps billed: " + ", ".join(steps)
                    + "). Credits are frozen on submission and refunded if a task fails. Check your balance with the Balance button."}


def _gen_body(req: dict, p: dict, file_token: str | None) -> tuple[str, dict]:
    if file_token is not None:
        return "/generation/image-to-model", {"input": file_token, "model": p["model"]}
    prompt = req["prompt"].strip()
    if p["rig"]:
        h = _hint(p["rig"])
        if len(prompt) + len(h) <= 1024:
            prompt += h
    return "/generation/text-to-model", {"prompt": prompt, "model": p["model"]}


def _rig_body(p: dict, task_id: str, rtype: str) -> dict:
    body: dict[str, Any] = {"input": task_id, "model": RIG_MODEL.get(rtype, RIG_MODEL_NONHUMANOID), "rig_type": rtype, "out_format": "glb"}
    if rtype == "biped" and p["spec"]:
        body["spec"] = p["spec"]
    return body


def dry_run(req: dict) -> dict:
    p = _plan(req)
    h = {"Authorization": f"Bearer {key() or '<not set>'}", "Content-Type": "application/json"}
    reqs = []
    tok = None
    if req.get("image_mediaId"):
        reqs.append(C.describe_request("POST", f"{BASE}/files", {"Authorization": h["Authorization"]}, {"file": "<image, multipart>"}))
        tok = "<file_token>"
    path, body = _gen_body(req, p, tok)
    reqs.append(C.describe_request("POST", BASE + path, h, body))
    reqs.append(C.describe_request("GET", f"{BASE}/tasks/<task id>", h, None, "polled every ~3 s until status = success"))
    if p["rig"]:
        if p["rig"] == "auto":
            reqs.append(C.describe_request("POST", f"{BASE}/animations/rig-check", h, {"input": "<task id>"}, "returns the recommended rig_type"))
        if p["rig"] == "auto":
            rb = {"input": "<task id>", "model": f"<{RIG_MODEL['biped']} for biped/others, {RIG_MODEL_NONHUMANOID} otherwise>",
                  "rig_type": "<recommended rig_type>", "out_format": "glb"}
        else:
            rb = _rig_body(p, "<task id>", p["rig"])
        reqs.append(C.describe_request("POST", f"{BASE}/animations/rig", h, rb))
    if p["animation"]:
        reqs.append(C.describe_request("POST", f"{BASE}/animations/retarget", h, {"input": "<rig task id>", "animation": "<preset for the rig type>", "out_format": "glb"}))
    reqs.append(C.describe_request("GET", "<output.model_url (CDN)>", {}, None, "downloaded without credentials"))
    return {"provider": "tripo", "requests": reqs, "estimate": estimate(req)}


def balance() -> dict:
    if not key():
        raise ProviderError("Tripo API key is not set.")
    with C.make_client(headers=C.bearer(key()), timeout=20.0) as cl:
        r = C.check(cl.get(f"{BASE}/account/balance"), "Tripo balance")
        d = (r.json() or {}).get("data") or {}
        return {"balance": d.get("balance"), "frozen": d.get("frozen"), "unit": "credits"}


def _data(r, what: str) -> dict:
    C.check(r, what)
    j = r.json()
    if isinstance(j, dict) and j.get("code") not in (0, None):
        raise ProviderError(C.scrub(f"{what} failed: {j.get('code')}: {j.get('message', '')} {j.get('suggestion', '')}".strip()))
    return j.get("data") or {}


def _wait(ctx, cl, task_id: str, label: str, lo: float, hi: float, timeout: float = 1500.0) -> dict:
    t0 = time.monotonic()
    while True:
        C.nap(ctx, 3.0)
        try:
            r = cl.get(f"{BASE}/tasks/{task_id}")
        except Exception:
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            continue
        d = _data(r, f"Tripo {label} status")
        st = d.get("status")
        pr = (d.get("progress") or 0) / 100.0
        ctx.progress(lo + (hi - lo) * pr, f"Tripo {label}: {st} {int(pr * 100)}%")
        if st == "success":
            return d
        if st in ("failed", "cancelled", "banned", "expired"):
            raise ProviderError(C.scrub(f"Tripo {label} {st}: {d.get('error_code', '')} {d.get('error_message', '')}".strip()))
        if time.monotonic() - t0 > timeout:
            raise ProviderError(f"Tripo {label} timed out after {int(timeout / 60)} minutes (task {task_id} may still finish on Tripo).")


def run(ctx, req: dict, dest_dir: Path, base_name: str) -> dict:
    k = key()
    if not k:
        raise ProviderError("Tripo API key is not set. Open Settings (gear icon) and paste your key.")
    p = _plan(req)
    credits = 0.0
    files: dict[str, str] = {}
    anims: dict[str, str] = {}
    headers = C.bearer(k)
    with C.make_client(headers=headers, timeout=90.0) as cl:
        try:
            tok = None
            if req.get("image_mediaId"):
                ctx.progress(0.01, "Uploading image to Tripo")
                data, mime = C.image_bytes_for_upload(C.media_image_path(req["image_mediaId"]), max_side=2048, max_bytes=15_000_000,
                                                      allowed=("image/jpeg", "image/png"))
                ext = ".png" if mime == "image/png" else ".jpg"
                up = _data(cl.post(f"{BASE}/files", files={"file": (f"{base_name}{ext}", data, mime)}), "Tripo image upload")
                tok = up.get("file_token")
                if not tok:
                    raise ProviderError("Tripo did not return a file token.")
            path, body = _gen_body(req, p, tok)
            d = _data(cl.post(BASE + path, json=body), "Tripo generation")
            gen_id = d["task_id"]
            use_rig = bool(p["rig"])
            t = _wait(ctx, cl, gen_id, "model", 0.03, 0.55 if use_rig else 0.88)
            credits += float(t.get("credits_consumed") or 0)
            out = t.get("output") or {}
            if not out.get("model_url"):
                raise ProviderError("Tripo finished but returned no model URL.")
            ctx.progress(0.56 if use_rig else 0.9, "Downloading model")
            files["model"] = str(C.download(out["model_url"], dest_dir / f"{base_name}.glb", ctx))
            preview = None
            if out.get("rendered_image_url"):
                try:
                    preview = str(C.download(out["rendered_image_url"], dest_dir / f"{base_name}_preview.png", ctx))
                except Exception:
                    preview = None
            rtype = p["rig"]
            if use_rig:
                if rtype == "auto":
                    d = _data(cl.post(f"{BASE}/animations/rig-check", json={"input": gen_id}), "Tripo rig check")
                    chk = _wait(ctx, cl, d["task_id"], "rig check", 0.58, 0.62)
                    credits += float(chk.get("credits_consumed") or 0)
                    o = chk.get("output") or {}
                    if o.get("riggable") is False:
                        raise ProviderError("Tripo's rig check says this model is not riggable (the generated model is saved without a rig). "
                                            "Try a different prompt/image, e.g. a clearer full-body pose.")
                    rtype = o.get("rig_type") or "quadruped"
                    ctx.progress(0.62, f"Rig check recommends: {rtype}")
                d = _data(cl.post(f"{BASE}/animations/rig", json=_rig_body(p, gen_id, rtype)), "Tripo auto-rig")
                rig_id = d["task_id"]
                t = _wait(ctx, cl, rig_id, "auto-rig", 0.62, 0.85 if p["animation"] else 0.95)
                credits += float(t.get("credits_consumed") or 0)
                ro = (t.get("output") or {}).get("model_url")
                if not ro:
                    raise ProviderError("Tripo rigging finished but returned no model URL.")
                ctx.progress(0.9, "Downloading rigged model")
                files["rigged"] = str(C.download(ro, dest_dir / f"{base_name}_rigged.glb", ctx))
                if p["animation"]:
                    preset = (ANIM_PRESETS.get(rtype) or {}).get(p["animation"])
                    if not preset:
                        ctx.progress(0.95, f"No '{p['animation']}' preset exists for rig type {rtype}; skipped")
                    else:
                        d = _data(cl.post(f"{BASE}/animations/retarget", json={"input": rig_id, "animation": preset, "out_format": "glb"}), "Tripo animation")
                        t = _wait(ctx, cl, d["task_id"], "animation", 0.86, 0.97)
                        credits += float(t.get("credits_consumed") or 0)
                        au = (t.get("output") or {}).get("model_url")
                        if au:
                            anims[p["animation"]] = str(C.download(au, dest_dir / f"{base_name}_{p['animation']}.glb", ctx))
            return {"files": files, "animations": anims, "preview_image": preview, "credits": round(credits, 2), "credit_unit": "Tripo credits",
                    "rig_type": rtype if use_rig else None, "meta": {"model_task": gen_id, "model": p["model"]}}
        except JobCancelled:
            ctx.job.message = "Cancelled locally. Tripo has no documented cancel endpoint, so a task already submitted may still complete and be charged."
            raise
