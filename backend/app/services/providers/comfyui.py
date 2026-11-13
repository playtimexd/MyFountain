"""Local video generation through a running ComfyUI (https://github.com/comfyanonymous/ComfyUI).

Talks to the stock ComfyUI HTTP API only:
  GET /system_stats, GET /object_info/{Class}, POST /upload/image, POST /prompt, GET /history/{id}, GET /queue,
  GET /view, POST /interrupt, POST /queue {delete:[..]}, POST /free.
Workflow graphs live in comfy_workflows/*.json (API format with {{placeholders}}); node classes and input names were
checked against ComfyUI master (comfy_extras/nodes_wan.py, nodes_lt.py, nodes_video.py) and the official
Comfy-Org/workflow_templates. CutLab can run its own ComfyUI (services/providers/comfy_local.py, data/comfyui) and starts it on
demand; a ComfyUI the user runs on the same port is reused. The adapter retries alternative spellings of the SaveVideo
DynamicCombo inputs (`save_variants`) when ComfyUI rejects that node.
"""
from __future__ import annotations

import fnmatch
import json
import math
import random
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from ...jobs import JobCancelled
from . import common as C
from .common import ProviderError

WF_DIR = Path(__file__).with_name("comfy_workflows")
PREFERENCE = ["wan22_ti2v_5b", "wan22_ti2v_5b_gguf", "ltxv_2b_distilled", "ltxv_2b"]
ASPECTS = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]
VIDEO_EXT = (".mp4", ".webm", ".mkv", ".mov", ".webp", ".gif")

INSTALL_STEPS = [
    "1. Install ComfyUI (the Windows portable NVIDIA build from https://github.com/comfyanonymous/ComfyUI/releases) - CutLab does not install it.",
    "2. Start it (run_nvidia_gpu.bat). For a GPU shared with other apps add --lowvram to the launch arguments. It listens on http://127.0.0.1:8188.",
    "3. Download the model files listed for one workflow below into the given ComfyUI folders (several GB; CutLab never downloads them).",
    "4. If your ComfyUI runs elsewhere, set 'ComfyUI URL' in Settings (gear icon). Then reload this panel.",
]


@lru_cache(maxsize=1)
def workflows() -> dict[str, dict]:
    out = {}
    for p in sorted(WF_DIR.glob("*.json")):
        w = json.loads(p.read_text("utf-8"))
        out[w["id"]] = w
    return out


def base_url() -> str:
    return (C.settings().get("comfyui_url") or "http://127.0.0.1:8188").strip().rstrip("/")


def _cl(timeout: float = 10.0):
    return C.make_client(local=True, timeout=timeout)


# --- object_info helpers --------------------------------------------------------------------------------------
def _options(info: dict | None, inp: str) -> list[str] | None:
    """Combo options of `inp` in a /object_info/{class} entry (handles both legacy and COMBO-typed spellings)."""
    if not info:
        return None
    for sect in ("required", "optional"):
        spec = ((info.get("input") or {}).get(sect) or {}).get(inp)
        if spec:
            first = spec[0]
            if isinstance(first, list):
                return [str(x) for x in first]
            if first == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
                return [str(x) for x in spec[1].get("options", [])]
            return []
    return None


def _pick(options: list[str], m: dict) -> str | None:
    low = {o.lower(): o for o in options}
    for cand in m.get("candidates", []):
        # ComfyUI lists models with sub-folder prefixes; match on the file name too
        if cand.lower() in low:
            return low[cand.lower()]
        for o in options:
            if o.replace("\\", "/").split("/")[-1].lower() == cand.lower():
                return o
    for pat in m.get("patterns", []):
        for o in options:
            if fnmatch.fnmatch(o.replace("\\", "/").split("/")[-1].lower(), pat.lower()):
                return o
    return None


def fetch_infos(cl, url: str, classes: set[str]) -> dict[str, dict | None]:
    out: dict[str, dict | None] = {}
    for c in sorted(classes):
        try:
            r = cl.get(f"{url}/object_info/{c}")
            j = r.json() if r.status_code == 200 else {}
            out[c] = j.get(c) if isinstance(j, dict) and j.get(c) else None
        except Exception:
            out[c] = None
    return out


def check_workflow(wf: dict, infos: dict[str, dict | None]) -> dict:
    missing_nodes = [n for n in wf["required_nodes"] if infos.get(n) is None]
    picks, missing_models = {}, []
    for m in wf["models"]:
        opts = _options(infos.get(m["node"]), m["input"])
        pick = _pick(opts, m) if opts is not None else None
        if pick:
            picks[m["id"]] = pick
        else:
            missing_models.append({"file": m["candidates"][0], "folder": m["folder"], "size_gb": m["size_gb"], "url": m["url"],
                                   "node_missing": opts is None})
    custom = [c for c in wf.get("custom_nodes", []) if infos.get(c["provides"]) is None]
    ready = not missing_nodes and not missing_models
    howto = []
    if missing_nodes:
        extra = "; ".join(f"install custom node {c['name']} ({c['repo']})" for c in custom)
        howto.append("Missing ComfyUI nodes: " + ", ".join(missing_nodes) + ("  ->  " + extra if extra else "  ->  update ComfyUI to the latest version."))
    for mm in missing_models:
        howto.append(f"Download {mm['file']} ({mm['size_gb']} GB) from {mm['url']} into ComfyUI/{mm['folder']}/")
    return {"id": wf["id"], "name": wf["name"], "description": wf["description"], "ready": ready, "picks": picks,
            "missing_nodes": missing_nodes, "missing_models": missing_models, "howto": howto,
            "download_gb": round(sum(x["size_gb"] for x in missing_models), 2), "approx_vram": wf["approx_vram"],
            "max_seconds": wf["max_seconds"], "est_seconds": wf["est_seconds"]}


def probe(timeout: float = 2.0) -> dict:
    """Is ComfyUI up, and which workflows are ready? Never raises."""
    url = base_url()
    res: dict[str, Any] = {"running": False, "url": url, "workflows": []}
    try:
        with _cl(timeout) as cl:
            r = cl.get(f"{url}/system_stats")
            if r.status_code != 200:
                res["error"] = f"ComfyUI answered HTTP {r.status_code}"
                return res
            res["running"] = True
            try:
                j = r.json()
                res["version"] = (j.get("system") or {}).get("comfyui_version")
                dev = (j.get("devices") or [{}])[0]
                if dev.get("vram_free"):
                    res["vram_free_gb"] = round(dev["vram_free"] / 1e9, 1)
                    res["vram_total_gb"] = round(dev.get("vram_total", 0) / 1e9, 1)
            except Exception:
                pass
            wfs = workflows()
            classes = {n for w in wfs.values() for n in w["required_nodes"]} | {m["node"] for w in wfs.values() for m in w["models"]}
            infos = fetch_infos(cl, url, classes)
            res["workflows"] = [check_workflow(wfs[i], infos) for i in PREFERENCE if i in wfs]
    except Exception as e:
        res["error"] = f"ComfyUI not reachable at {url} ({type(e).__name__})"
    return res


def _is_local_default() -> bool:
    return base_url() in ("http://127.0.0.1:8188", "http://localhost:8188")


def listing() -> dict:
    from . import comfy_local
    p = probe()
    if not p["running"] and _is_local_default() and comfy_local.installed():
        comfy_local.start()                                     # CutLab's own ComfyUI: start it in the background
        p["starting"] = True
        p["error"] = "Starting CutLab's local ComfyUI (about 30-60 s); reload this panel in a moment."
    ready = [w for w in p["workflows"] if w["ready"]]
    howto = ""
    if not p["running"]:
        howto = "ComfyUI is not running. " + " ".join(INSTALL_STEPS)
    elif not ready:
        best = min(p["workflows"], key=lambda w: (w["download_gb"], len(w["missing_nodes"])), default=None)
        howto = "ComfyUI is running but no video workflow is ready. Easiest: " + (f"{best['name']}: " + " | ".join(best["howto"]) if best else "n/a")
    return {
        "id": "local", "name": "Local - ComfyUI (Wan 2.2 5B / LTX-Video)", "available": bool(ready), "needs_key": False, "key_set": False,
        "cloud": False, "verified": True,
        "cost_note": "Free (uses your GPU). Typically several minutes per 5 s clip on a shared GPU; unloads models afterwards.",
        "capabilities": {"modes": ["text-to-video", "image-to-video"], "max_duration": 6, "aspects": ASPECTS, "resolutions": ["~480p"],
                         "outputs_per_request": 1, "ready_workflows": [w["id"] for w in ready]},
        "note": "Workflow graphs follow the official ComfyUI templates but were not executed here (ComfyUI is not installed on the dev machine).",
        "howto": howto, "running": p["running"], "url": p["url"], "vram_free_gb": p.get("vram_free_gb"),
        "workflows": p["workflows"], "error": p.get("error"), "install_steps": INSTALL_STEPS,
    }


# --- planning -------------------------------------------------------------------------------------------------
def plan(req: dict, wf: dict) -> dict:
    dur = float(req.get("duration") or 5)
    if dur > wf["max_seconds"]:
        raise ProviderError(f"Duration {dur:g}s exceeds the local cap of {wf['max_seconds']}s for {wf['name']}.")
    if dur <= 0:
        raise ProviderError("Duration must be positive.")
    aspect = req.get("aspect") or "16:9"
    if aspect not in wf["sizes"]:
        raise ProviderError(f"Unsupported aspect ratio '{aspect}' for local generation.")
    w, h = wf["sizes"][aspect]
    mult = wf["frame_multiple"]
    frames = mult * max(1, round(dur * wf["fps"] / mult)) + 1
    return {"width": w, "height": h, "frames": frames, "fps": wf["fps"], "seconds": round((frames - 1) / wf["fps"], 2)}


def estimate(req: dict) -> dict:
    wf = workflows()[req.get("variant") or PREFERENCE[0]]
    p = plan(req, wf)
    return {"provider": "local", "currency": None, "amount": 0, "unit": "free (local GPU)", "duration": p["seconds"],
            "note": f"Runs {wf['name']} at {p['width']}x{p['height']}, {p['frames']} frames @ {p['fps']} fps. About {wf['est_seconds'] // 60} min on a free GPU (estimate)."}


def instantiate(wf: dict, params: dict, picks: dict, image_name: str | None, variant: int = 0) -> dict:
    g = json.loads(json.dumps(wf["graph_i2v"] if image_name else wf["graph"]))
    subs = {"prompt": params["prompt"], "negative": wf["negative"], "width": params["width"], "height": params["height"],
            "frames": params["frames"], "fps": params["fps"], "seed": params["seed"], "prefix": params["prefix"], "image": image_name}
    for k, v in picks.items():
        subs[f"model.{k}"] = v
    for node in g.values():
        for k, v in list(node["inputs"].items()):
            if isinstance(v, str) and v.startswith("{{") and v.endswith("}}"):
                node["inputs"][k] = subs[v[2:-2]]
    save = g["58"]["inputs"]
    for k in [k for k in save if k not in ("video", "filename_prefix")]:
        del save[k]
    save.update(wf["save_variants"][variant])
    return g


def dry_run(req: dict) -> dict:
    p = probe()
    wf_id = req.get("variant") or next((w["id"] for w in p["workflows"] if w["ready"]), PREFERENCE[0])
    wf = workflows()[wf_id]
    pl = plan(req, wf)
    picks = next((w["picks"] for w in p["workflows"] if w["id"] == wf_id), {})
    for m in wf["models"]:
        picks.setdefault(m["id"], m["candidates"][0])
    g = instantiate(wf, {**pl, "prompt": req["prompt"], "seed": 0, "prefix": "video/cutlab"}, picks,
                    "<uploaded start image>" if req.get("image_mediaId") else None)
    return {"provider": "local", "workflow": wf_id, "requests": [
        C.describe_request("POST", f"{base_url()}/prompt", {}, {"prompt": g, "client_id": "<uuid>"}),
        C.describe_request("GET", f"{base_url()}/history/<prompt_id>", {}, None, "polled every ~3 s"),
        C.describe_request("GET", f"{base_url()}/view?filename=<output>&type=output", {}, None)],
        "running": p["running"], "ready": any(w["ready"] for w in p["workflows"]), "estimate": estimate(req)}


# --- run ------------------------------------------------------------------------------------------------------
def _node_errors(j: dict) -> str:
    parts = []
    for nid, ne in (j.get("node_errors") or {}).items():
        for e in ne.get("errors", []):
            parts.append(f"node {nid} ({ne.get('class_type', '?')}): {e.get('message', '')} {e.get('details', '')}".strip())
    err = j.get("error")
    if isinstance(err, dict):
        parts.insert(0, f"{err.get('type', '')}: {err.get('message', '')}".strip(": "))
    return "; ".join(parts)[:900] or "ComfyUI rejected the workflow"


def _interrupt(cl, url: str, pid: str) -> None:
    try:
        q = cl.get(f"{url}/queue").json()
        running = [x[1] for x in q.get("queue_running", []) if len(x) > 1]
        if pid in running:
            cl.post(f"{url}/interrupt")          # ours is the active job: safe to interrupt
        cl.post(f"{url}/queue", json={"delete": [pid]})
    except Exception:
        pass


def _outputs(entry: dict) -> list[dict]:
    files = []
    for node_out in (entry.get("outputs") or {}).values():
        for key in ("images", "gifs", "videos"):
            for f in node_out.get(key, []) or []:
                if str(f.get("filename", "")).lower().endswith(VIDEO_EXT) and f.get("type", "output") == "output":
                    files.append(f)
    return files


def run(ctx, req: dict, dest_dir: Path) -> dict:
    url = base_url()
    ctx.progress(0.01, "Checking ComfyUI")
    p = probe(timeout=4.0)
    if not p["running"] and _is_local_default():
        from . import comfy_local
        if comfy_local.installed() and comfy_local.ensure_running(progress=lambda m: ctx.progress(0.01, m)):
            p = probe(timeout=10.0)
    if not p["running"]:
        raise ProviderError("ComfyUI is not running. " + " ".join(INSTALL_STEPS))
    ready = {w["id"]: w for w in p["workflows"] if w["ready"]}
    wid = req.get("variant")
    if wid:
        if wid not in ready:
            raise ProviderError(f"Workflow '{wid}' is not ready: " + " | ".join(next((w["howto"] for w in p["workflows"] if w["id"] == wid), ["unknown workflow"])))
    else:
        wid = next((i for i in PREFERENCE if i in ready), None)
        if not wid:
            best = min(p["workflows"], key=lambda w: w["download_gb"], default=None)
            raise ProviderError("No ComfyUI video workflow is ready. " + (" | ".join(best["howto"]) if best else ""))
    wf, chk = workflows()[wid], ready[wid]
    pl = plan(req, wf)
    seed = random.randint(0, 2**32 - 1)
    prefix = f"video/cutlab_{C.stamp()}"
    pid = None
    with _cl(60.0) as cl:
        try:
            image_name = None
            if req.get("image_mediaId"):
                ctx.progress(0.03, "Uploading start image to ComfyUI")
                ip = C.media_image_path(req["image_mediaId"])
                data, mime = C.image_bytes_for_upload(ip, max_side=2048)
                fname = f"cutlab_{uuid.uuid4().hex[:8]}{'.png' if mime == 'image/png' else '.jpg'}"
                r = C.check(cl.post(f"{url}/upload/image", files={"image": (fname, data, mime)}, data={"overwrite": "true", "type": "input"}), "ComfyUI image upload")
                j = r.json()
                image_name = (j.get("subfolder") + "/" if j.get("subfolder") else "") + j["name"]
            params = {**pl, "prompt": req["prompt"], "seed": seed, "prefix": prefix}
            cid = uuid.uuid4().hex
            last = ""
            for vi in range(len(wf["save_variants"])):
                graph = instantiate(wf, params, chk["picks"], image_name, vi)
                r = cl.post(f"{url}/prompt", json={"prompt": graph, "client_id": cid})
                if r.status_code == 200:
                    pid = r.json().get("prompt_id")
                    break
                try:
                    last = _node_errors(r.json())
                except Exception:
                    last = f"HTTP {r.status_code}"
                if not ("58" in last or "SaveVideo" in last):   # only the SaveVideo spelling is retried
                    break
            if not pid:
                raise ProviderError(f"ComfyUI rejected the workflow: {last}")
            t0 = time.monotonic()
            ctx.progress(0.05, f"Queued in ComfyUI ({wf['name']})")
            while True:
                C.nap(ctx, 3.0)
                try:
                    h = cl.get(f"{url}/history/{pid}").json()
                except Exception:
                    continue
                entry = h.get(pid) if isinstance(h, dict) else None
                el = time.monotonic() - t0
                if entry:
                    st = entry.get("status") or {}
                    if st.get("status_str") == "error":
                        msg = ""
                        for kind, payload in st.get("messages", []):
                            if kind == "execution_error":
                                msg = f"{payload.get('node_type', '')}: {payload.get('exception_message', '')}".strip()
                        low = msg.lower()
                        hint = " Try the GGUF workflow, a shorter clip, or close other GPU apps." if "out of memory" in low else ""
                        raise ProviderError(C.scrub(f"ComfyUI failed: {msg or 'execution error'}.{hint}"))
                    files = _outputs(entry)
                    if files:
                        f = files[0]
                        ctx.progress(0.93, "Fetching video from ComfyUI")
                        out = dest_dir / f"videogen_local_{C.stamp()}{Path(f['filename']).suffix or '.mp4'}"
                        r = cl.get(f"{url}/view", params={"filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": f.get("type", "output")})
                        C.check(r, "ComfyUI download")
                        out.write_bytes(r.content)
                        try:  # shared GPU: give the VRAM back
                            cl.post(f"{url}/free", json={"unload_models": True, "free_memory": True})
                        except Exception:
                            pass
                        return {"file": str(out), "meta": {"workflow": wid, "seed": seed, **{k: pl[k] for k in ("width", "height", "frames", "fps")}}}
                    if st.get("completed"):
                        raise ProviderError("ComfyUI finished but produced no video file (check the SaveVideo node).")
                msg = "generating"
                try:
                    q = cl.get(f"{url}/queue").json()
                    pend = [x[1] for x in q.get("queue_pending", []) if len(x) > 1]
                    if pid in pend:
                        msg = f"waiting in ComfyUI queue (position {pend.index(pid) + 1})"
                except Exception:
                    pass
                frac = 0.05 + 0.85 * (1 - math.exp(-el / max(wf["est_seconds"], 30)))
                ctx.progress(min(0.9, frac), f"ComfyUI {msg} ({int(el)}s)")
        except JobCancelled:
            if pid:
                _interrupt(cl, url, pid)
            raise
