"""ByteDance Seedance via the OFFICIAL BytePlus ModelArk API (read from docs.byteplus.com, Nov 2026).

Verified against the official docs (see docs links in each constant):
  * Base URL  https://ark.ap-southeast.bytepluses.com/api/v3        ("Base URL and authentication")
  * Auth      Authorization: Bearer <ARK API key>                    (same page; API key = simplest option)
  * Create    POST {base}/contents/generations/tasks   -> {"id": "<task id>"}
  * Retrieve  GET  {base}/contents/generations/tasks/{id}
              status: queued | running | succeeded | failed | expired | cancelled ; content.video_url (valid 24 h);
              usage.completion_tokens ; error {code,message}
  * Cancel    DELETE {base}/contents/generations/tasks/{id}   (only QUEUED tasks can be cancelled; running ones keep
              running and are billed on success)
  * Billing   per-token (price per 1M tokens), tokens = (duration x width x height x fps / 1024); failed tasks are not
              billed.  Usage comes back in usage.completion_tokens.
NOT verified: output fps (assumed 24 for the estimate), minimum-token floors of the 2.x models, the Volcengine (China)
host, any base URL other than the BytePlus one above.  Nothing here has been run against the live service.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ... import config
from ...jobs import JobCancelled
from . import common as C
from .common import ProviderError

OFFICIAL_BASE = "https://ark.ap-southeast.bytepluses.com/api/v3"
DEFAULT_MODEL = "dreamina-seedance-2-0-fast-260128"
HARD_CAP_SECONDS = 10          # CutLab cap per request (cost safety), below the model maximum
HARD_CAP_RESOLUTION = "720p"   # CutLab cap per request
ASSUMED_FPS = 24               # unverified; used for the token estimate only

# model id -> (family, min_s, max_s, supports_audio_flag, camera_fixed_param, usd_per_mtok by resolution)
MODELS: dict[str, dict] = {
    "dreamina-seedance-2-5-260628": dict(name="Seedance 2.5", dur=(4, 30), audio=True, camfix=False,
                                         price={"480p": 10.70, "720p": 10.70, "1080p": 11.70}),
    "dreamina-seedance-2-0-260128": dict(name="Seedance 2.0", dur=(4, 15), audio=True, camfix=False,
                                         price={"480p": 7.00, "720p": 7.00, "1080p": 7.70}),
    "dreamina-seedance-2-0-fast-260128": dict(name="Seedance 2.0 Fast", dur=(4, 15), audio=True, camfix=False,
                                              price={"480p": 5.60, "720p": 5.60}),
    "dreamina-seedance-2-0-mini-260615": dict(name="Seedance 2.0 Mini", dur=(4, 15), audio=True, camfix=False,
                                              price={"480p": 3.50, "720p": 3.50}),
    "seedance-1-5-pro-251215": dict(name="Seedance 1.5 Pro (retiring 2026-11-11)", dur=(4, 12), audio=True, camfix=True,
                                    price={"480p": 1.20, "720p": 1.20, "1080p": 1.20}),
    "seedance-1-0-pro-250528": dict(name="Seedance 1.0 Pro", dur=(2, 12), audio=False, camfix=True,
                                    price={"480p": 2.50, "720p": 2.50, "1080p": 2.50}),
    "seedance-1-0-pro-fast-251015": dict(name="Seedance 1.0 Pro Fast", dur=(2, 12), audio=False, camfix=True,
                                         price={"480p": 1.00, "720p": 1.00, "1080p": 1.00}),
}
# output pixel sizes from the Seedance 2.5 doc ("Resolution to pixel dimensions")
PIXELS = {
    "480p": {"16:9": (854, 480), "4:3": (752, 560), "1:1": (640, 640), "3:4": (560, 752), "9:16": (480, 854), "21:9": (992, 432)},
    "720p": {"16:9": (1280, 720), "4:3": (1112, 834), "1:1": (960, 960), "3:4": (834, 1112), "9:16": (720, 1280), "21:9": (1470, 630)},
    "1080p": {"16:9": (1920, 1080), "4:3": (1664, 1248), "1:1": (1440, 1440), "3:4": (1248, 1664), "9:16": (1080, 1920), "21:9": (2206, 946)},
}
ASPECTS = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]


def cfg() -> dict:
    s = C.settings()
    base = (s.get("seedance_base_url") or "").strip().rstrip("/") or OFFICIAL_BASE
    model = (s.get("seedance_model") or "").strip() or DEFAULT_MODEL
    key = (s.get("seedance_api_key") or "").strip()
    return {"base": base, "model": model, "key": key}


def model_info(model: str) -> dict | None:
    return MODELS.get(model)


def listing() -> dict:
    c = cfg()
    mi = model_info(c["model"])
    verified = c["base"] == OFFICIAL_BASE and mi is not None
    lo, hi = (mi["dur"] if mi else (4, 15))
    return {
        "id": "seedance", "name": f"Seedance ({mi['name'] if mi else c['model']}) - cloud",
        "available": bool(c["key"]), "needs_key": True, "key_set": bool(c["key"]), "cloud": True,
        "verified": verified,
        "cost_note": ("Pay-per-use on your BytePlus ModelArk account, billed per output token "
                      f"(USD {_price(mi, '480p') if mi else '?'} per 1M tokens at 480p, roughly $0.25-0.60 for a 5 s 480p clip). "
                      "Seedance 2.x needs an account balance above USD 30 or a savings plan. Failed tasks are not billed."),
        "capabilities": {
            "modes": ["text-to-video", "image-to-video"], "durations": [lo, min(hi, HARD_CAP_SECONDS)],
            "max_duration": min(hi, HARD_CAP_SECONDS), "aspects": ASPECTS, "resolutions": ["480p", "720p"],
            "outputs_per_request": 1, "model": c["model"], "base_url": c["base"],
        },
        "note": "" if verified else "Custom base URL or model id: the request shape follows the BytePlus ModelArk docs but this host/model was not verified.",
        "howto": "" if c["key"] else "Paste your BytePlus ModelArk API key in Settings (gear icon) -> Video generation -> Seedance API key.",
    }


def _price(mi: dict | None, res: str) -> float | None:
    if not mi:
        return None
    return mi["price"].get(res) or mi["price"].get("720p")


def resolve(req: dict) -> dict:
    """Validate + normalise a request. Returns plan dict (model, duration, aspect, resolution, image flag...)."""
    c = cfg()
    mi = model_info(c["model"])
    lo, hi = mi["dur"] if mi else (4, 15)
    cap = min(hi, HARD_CAP_SECONDS)
    dur = float(req.get("duration") or 5)
    if dur > cap:
        raise ProviderError(f"Duration {dur:g}s exceeds the per-request cap of {cap}s for Seedance in CutLab.")
    d = int(round(dur))
    notes = []
    if d < lo:
        notes.append(f"Duration raised to the model minimum of {lo}s.")
        d = lo
    aspect = req.get("aspect") or "16:9"
    if aspect not in ASPECTS:
        raise ProviderError(f"Unsupported aspect ratio '{aspect}'. Use one of {', '.join(ASPECTS)}.")
    res = req.get("resolution") or "480p"
    if res not in ("480p", "720p"):
        raise ProviderError("Resolution is capped at 720p (use 480p to keep reference clips cheap).")
    if mi and res not in mi["price"]:
        res = "720p" if "720p" in mi["price"] else "480p"
    w, h = PIXELS[res][aspect]
    tokens = int(d * w * h * ASSUMED_FPS / 1024)
    price = _price(mi, res)
    usd = round(tokens * price / 1_000_000, 3) if price else None
    return {"model": c["model"], "duration": d, "aspect": aspect, "resolution": res, "size": [w, h], "tokens": tokens,
            "price_per_mtok": price, "usd": usd, "notes": notes, "base": c["base"], "model_known": mi is not None}


def estimate(req: dict) -> dict:
    p = resolve(req)
    e: dict[str, Any] = {
        "provider": "seedance", "currency": "USD", "amount": p["usd"], "unit": "approx. USD for this clip",
        "tokens": p["tokens"], "model": p["model"], "duration": p["duration"], "resolution": p["resolution"],
        "note": ("Estimate = duration x width x height x 24 fps / 1024 tokens x price per 1M tokens "
                 "(BytePlus pricing page). Output fps and the 2.x minimum-token floor are not documented, so the real "
                 "charge (usage.completion_tokens) may differ. Only successful tasks are billed."),
    }
    if p["usd"] is None:
        e["note"] = "Unknown model id: price cannot be estimated. " + e["note"]
    if p["notes"]:
        e["adjustments"] = p["notes"]
    return e


def build_body(req: dict, plan: dict, image_uri: str | None) -> dict:
    prompt = req["prompt"]
    content: list[dict] = [{"type": "text", "text": prompt}]
    if image_uri:
        content.append({"type": "image_url", "image_url": {"url": image_uri}, "role": "first_frame"})
    mi = model_info(plan["model"])
    body: dict[str, Any] = {
        "model": plan["model"], "content": content, "resolution": plan["resolution"],
        # first-frame tasks only accept "adaptive" (Seedance 2.x docs)
        "ratio": "adaptive" if image_uri else plan["aspect"],
        "duration": plan["duration"], "watermark": False, "execution_expires_after": 3600,
    }
    if mi is None or mi["audio"]:
        body["generate_audio"] = False          # silent reference clip; also avoids audio surcharges (1.5 pro)
    if mi and mi["camfix"] and req.get("capture_friendly"):
        body["camera_fixed"] = True             # only documented for Seedance 1.5 pro / 1.0 series
    return body


def dry_run(req: dict, image_path: Path | None = None) -> dict:
    c = cfg()
    plan = resolve(req)
    uri = f"data:image/jpeg;base64,{'A' * 8}" if image_path or req.get("image_mediaId") else None
    body = build_body(req, plan, uri)
    hdr = {"Authorization": f"Bearer {c['key'] or '<not set>'}", "Content-Type": "application/json"}
    return {"provider": "seedance", "requests": [
        C.describe_request("POST", f"{c['base']}/contents/generations/tasks", hdr, body),
        C.describe_request("GET", f"{c['base']}/contents/generations/tasks/<task id>", hdr, None,
                           "polled every ~6 s until status = succeeded"),
        C.describe_request("GET", "<content.video_url (pre-signed, valid 24 h)>", {}, None, "downloaded without credentials"),
    ], "estimate": estimate(req)}


def run(ctx, req: dict, dest_dir: Path) -> dict:
    """Execute the paid generation. Returns {file, meta}."""
    c = cfg()
    if not c["key"]:
        raise ProviderError("Seedance API key is not set. Open Settings (gear icon) and paste your BytePlus ModelArk key.")
    plan = resolve(req)
    uri = None
    if req.get("image_mediaId"):
        ctx.progress(0.01, "Preparing start image")
        from PIL import Image
        ip = C.media_image_path(req["image_mediaId"])
        with Image.open(ip) as im:
            ar = im.width / max(im.height, 1)
            if im.width < 300 or im.height < 300 or not (0.4 <= ar <= 2.5):
                raise ProviderError("Start image must be 300-6000 px per side with an aspect ratio between 0.4 and 2.5.")
        uri = C.data_uri(ip, max_side=2048, max_bytes=8_000_000)
    body = build_body(req, plan, uri)
    headers = {**C.bearer(c["key"]), "Content-Type": "application/json"}
    task_id = None
    with C.make_client(headers=headers, timeout=60.0) as cl:
        try:
            ctx.progress(0.03, "Submitting to Seedance")
            r = C.check(cl.post(f"{c['base']}/contents/generations/tasks", json=body), "Seedance task creation")
            task_id = (r.json() or {}).get("id")
            if not task_id:
                raise ProviderError("Seedance did not return a task id.")
            t0 = time.monotonic()
            while True:
                C.nap(ctx, 6.0)
                try:
                    rr = cl.get(f"{c['base']}/contents/generations/tasks/{task_id}")
                except Exception as e:  # transient network error while polling: keep waiting, the task is already paid for
                    ctx.progress(ctx.job.progress, f"Seedance task {task_id[:12]}: connection problem, retrying ({type(e).__name__})")
                    continue
                if rr.status_code in (429, 500, 502, 503, 504):
                    continue
                C.check(rr, "Seedance status query")
                j = rr.json()
                st = j.get("status")
                el = time.monotonic() - t0
                if st == "succeeded":
                    url = (j.get("content") or {}).get("video_url")
                    if not url:
                        raise ProviderError("Seedance finished but returned no video URL.")
                    ctx.progress(0.92, "Downloading video")
                    out = dest_dir / f"videogen_seedance_{C.stamp()}.mp4"
                    C.download(url, out, ctx)
                    usage = (j.get("usage") or {}).get("completion_tokens")
                    price = plan["price_per_mtok"]
                    return {"file": str(out), "meta": {
                        "task_id": task_id, "model": plan["model"], "duration": j.get("duration", plan["duration"]),
                        "resolution": j.get("resolution", plan["resolution"]), "ratio": j.get("ratio"),
                        "fps": j.get("framespersecond"), "tokens": usage,
                        "cost_usd": round(usage * price / 1e6, 4) if (usage and price) else None}}
                if st in ("failed", "expired", "cancelled"):
                    err = j.get("error") or {}
                    raise ProviderError(C.scrub(f"Seedance task {st}: {err.get('code', '')} {err.get('message', '')}".strip()))
                frac = 0.05 if st == "queued" else 0.12 + 0.78 * (1 - 2.718 ** (-el / 120.0))
                ctx.progress(min(0.9, frac), f"Seedance {st or 'pending'} - task {task_id[:12]} ({int(el)}s)")
        except JobCancelled:
            if task_id:
                try:  # only queued tasks can be cancelled by the API; running ones finish (and are billed) regardless
                    cl.delete(f"{c['base']}/contents/generations/tasks/{task_id}")
                except Exception:
                    pass
                ctx.job.message = (f"Cancelled. Seedance task {task_id} is cancelled only if it was still queued; "
                                   "a task that had already started may still be billed.")
            raise
