"""CutLab MCP server (stdio, JSON-RPC 2.0, no extra dependencies).

Lets Claude Code / Codex / any MCP client drive CutLab with their own model (no API key needed inside CutLab):
the client's model plans, CutLab executes, and tools can return frames as images so the client can SEE results.

  Claude Code:  claude mcp add cutlab -s user -e PYTHONPATH=D:\\CutLab\\backend -- D:\\CutLab\\backend\\.venv\\Scripts\\python.exe -m app.mcp_server
  Codex:        ~/.codex/config.toml ->  [mcp_servers.cutlab] command = "D:\\CutLab\\backend\\.venv\\Scripts\\python.exe"
                                         args = ["-m", "app.mcp_server"]   cwd = "D:\\CutLab\\backend"
Run from D:\\CutLab\\backend (or set PYTHONPATH). Engine URL: env CUTLAB_URL (default http://127.0.0.1:8787);
the engine is started automatically if it is not running. Never writes anything but protocol JSON to stdout.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

import httpx

URL = os.environ.get("CUTLAB_URL", "http://127.0.0.1:8787").rstrip("/")
BACKEND = Path(__file__).resolve().parents[1]
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
http = httpx.Client(base_url=URL, timeout=120)


def log(*a):
    print("[cutlab-mcp]", *a, file=sys.stderr, flush=True)


# ------------------------------------------------------------------------------------------- engine helpers
def ensure_engine() -> None:
    try:
        http.get("/api/health", timeout=2).raise_for_status()
        return
    except Exception:
        pass
    log("engine not running, starting it…")
    py = BACKEND / ".venv" / "Scripts" / "python.exe"
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0      # DETACHED_PROCESS | NEW_PROCESS_GROUP
    from .config import DATA as data         # same data folder the engine uses (CUTLAB_DATA / desktop.json / ./data)
    data.mkdir(exist_ok=True)
    subprocess.Popen([str(py if py.exists() else sys.executable), "-m", "app"], cwd=str(BACKEND),
                     stdout=open(data / "server.log", "ab"), stderr=open(data / "server.err", "ab"),
                     stdin=subprocess.DEVNULL, creationflags=flags)
    for _ in range(60):
        time.sleep(0.5)
        try:
            http.get("/api/health", timeout=2).raise_for_status()
            return
        except Exception:
            continue
    raise RuntimeError(f"CutLab engine did not start at {URL} (see data/server.err)")


def call(method: str, path: str, body: Any = None, params: dict | None = None) -> Any:
    ensure_engine()
    r = http.request(method, path, json=body, params=params)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {detail if isinstance(detail, str) else json.dumps(detail)[:800]}")
    ct = r.headers.get("content-type", "")
    return r.content if ct.startswith("image/") else r.json()


def wait_job(job: dict, timeout: float = 3600) -> dict:
    """Poll a CutLab job until it finishes; raise on error."""
    if not isinstance(job, dict) or "status" not in job or "id" not in job:
        return job
    t0 = time.time()
    while job["status"] in ("queued", "running"):
        if time.time() - t0 > timeout:
            raise RuntimeError(f"job {job['id']} still running after {timeout:.0f}s (last: {job.get('message')})")
        time.sleep(1.0)
        job = call("GET", f"/api/jobs/{job['id']}")
    if job["status"] != "done":
        raise RuntimeError(f"job {job['title']} {job['status']}: {job.get('error') or job.get('message')}")
    return job


def slim_media(m: dict | None) -> dict | None:
    if not m:
        return m
    out = {k: m.get(k) for k in ("id", "kind", "name", "duration", "width", "height", "fps", "has_audio", "origin", "path", "tags")}
    c = (m.get("meta") or {}).get("classify") if isinstance(m.get("meta"), dict) else None
    if c:
        out["category"] = c.get("category")
    return out


def slim(obj: Any) -> Any:
    """Shrink results for the model: media rows -> key fields, drop huge arrays."""
    if isinstance(obj, dict):
        if "kind" in obj and "url" in obj and "thumb" in obj:
            return slim_media(obj)
        return {k: slim(v) for k, v in obj.items() if k not in ("peaks",)}
    if isinstance(obj, list):
        return [slim(x) for x in obj[:200]]
    return obj


def text(obj: Any) -> list[dict]:
    return [{"type": "text", "text": obj if isinstance(obj, str) else json.dumps(slim(obj), ensure_ascii=False, indent=1)}]


def image(jpeg: bytes, caption: str = "") -> list[dict]:
    out = [{"type": "image", "data": base64.b64encode(jpeg).decode(), "mimeType": "image/jpeg"}]
    return out + ([{"type": "text", "text": caption}] if caption else [])


def bridge_project() -> dict:
    snap = call("GET", "/api/bridge/project")
    if not snap.get("project"):
        raise RuntimeError("No timeline available. Open the CutLab Video Editor at http://localhost:5173 (it syncs its project to the engine).")
    return snap


def summarize_project(p: dict) -> dict:
    tracks = []
    for t in p.get("tracks", []):
        clips = []
        for c in t.get("clips", []):
            dur = (c.get("out", 0) - c.get("in", 0)) / (c.get("speed") or 1)
            d = {"id": c["id"], "type": c.get("type"), "start": round(c.get("start", 0), 3), "duration": round(dur, 3)}
            if c.get("assetId"): d["assetId"] = c["assetId"]
            if c.get("type") == "text": d["text"] = (c.get("text") or {}).get("content", "")[:80]
            if c.get("speed", 1) != 1: d["speed"] = c["speed"]
            if c.get("transitionIn"): d["transitionIn"] = c["transitionIn"]
            if c.get("filter"): d["filter"] = c["filter"]
            if c.get("audioDetached"): d["audioDetached"] = True
            clips.append(d)
        tracks.append({"id": t["id"], "kind": t.get("kind"), "name": t.get("name"), "muted": t.get("muted"), "hidden": t.get("hidden"), "clips": clips})
    end = max([c["start"] + c["duration"] for t in tracks for c in t["clips"]] or [0])
    return {"name": p.get("name"), "size": f"{p.get('width')}x{p.get('height')}", "fps": p.get("fps"), "duration": round(end, 3), "tracks": tracks}


# ------------------------------------------------------------------------------------------- tools
TOOLS: dict[str, tuple[str, dict, Callable[[dict], list[dict]]]] = {}


def tool(name: str, description: str, props: dict | None = None, required: list[str] | None = None):
    schema = {"type": "object", "properties": props or {}, "required": required or []}

    def deco(fn):
        TOOLS[name] = (description, schema, fn)
        return fn
    return deco


S = lambda d="": {"type": "string", "description": d}            # noqa: E731
N = lambda d="": {"type": "number", "description": d}            # noqa: E731


@tool("status", "CutLab engine status: available local engines/models (GPU, ffmpeg/NVENC, Whisper, Real-ESRGAN, ACE-Step, Ollama, Blender…) and whether a Video Editor is connected for live timeline editing.")
def t_status(a):
    caps = call("GET", "/api/capabilities")
    caps.pop("settings", None)
    try:
        b = call("GET", "/api/bridge/project")
        caps["editor_connected"] = b.get("connected")
    except Exception:
        caps["editor_connected"] = False
    return text(caps)


@tool("list_media", "List assets in the CutLab media library (videos, audio, images). Filter by auto category with `category` (see list_categories).",
      {"kind": {"type": "string", "enum": ["video", "audio", "image"]}, "query": S("name/tag filter"), "origin": S("import|download|music|tts|export|ai|segment|anim|gen|intake"),
       "category": S("e.g. photo, texture, footage, music, sound-effect"), "limit": N()})
def t_list(a):
    p = {k: v for k, v in {"kind": a.get("kind"), "q": a.get("query"), "origin": a.get("origin"), "tag": f"cat:{a['category']}" if a.get("category") else None,
                           "limit": int(a.get("limit") or 50)}.items() if v}
    return text([slim_media(m) for m in call("GET", "/api/media", params=p)])


@tool("search_local_files", "Semantic + keyword search over indexed local folders (filenames, transcripts, visual embeddings). Hits are added to the library.",
      {"query": S(), "kinds": {"type": "array", "items": {"type": "string"}}, "limit": N()}, ["query"])
def t_search(a):
    return text(call("POST", "/api/search/query", {"q": a["query"], "kinds": a.get("kinds"), "limit": int(a.get("limit") or 20)}))


@tool("index_local_folders", "Index local folders for search_local_files (incremental).", {"paths": {"type": "array", "items": {"type": "string"}}}, ["paths"])
def t_index(a):
    return text(wait_job(call("POST", "/api/search/index", {"paths": a["paths"], "recursive": True}))["result"])


@tool("import_media", "Import local files or folders into the library (linked in place, not copied).", {"paths": {"type": "array", "items": {"type": "string"}}}, ["paths"])
def t_import(a):
    return text(wait_job(call("POST", "/api/media/import", {"paths": a["paths"]}))["result"])


# ------------------------------------------------------------------------------------------ intake (OneDrive & other drives)
@tool("import_from_links", "Import files from cloud drives or the web into CutLab and auto-categorise them. For OneDrive pass each file's pre-authorised "
      "download URL (Microsoft Graph `@microsoft.graph.downloadUrl`, or a share link ending in download=1) together with its file name. No credentials are sent. "
      "Video/audio/images go to the media library (auto category + subject/style tags); .glb/.fbx/.obj/.blend to the 3D model library; .bvh/.npz to the "
      "animation library; .ttf/.otf to fonts. Returns where each file went and its category.",
      {"items": {"type": "array", "items": {"type": "object", "properties": {"url": S(), "name": S("file name with extension"), "category": S("optional: force a category"),
                                                                             "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["url"]}},
       "source": S("tag for all items, e.g. onedrive"), "categorize": {"type": "boolean"}}, ["items"])
def t_import_links(a):
    body = {"items": a["items"], "categorize": a.get("categorize", True), "source": a.get("source") or "link"}
    return text(wait_job(call("POST", "/api/intake/urls", body), timeout=3600)["result"])


@tool("upload_file", "Send a file's bytes to CutLab (base64), for connectors that return file content instead of a link. Call repeatedly with the same "
      "upload_id for chunks (max 32 MB each, 1 GB total), then once with finish=true. Prefer import_from_links or import_cloud_folder for big videos.",
      {"name": S("file name with extension"), "data_base64": S(), "upload_id": S("from the previous chunk"), "finish": {"type": "boolean"},
       "category": S(), "tags": {"type": "array", "items": {"type": "string"}}}, ["name"])
def t_upload(a):
    uid = a.get("upload_id")
    if a.get("data_base64"):
        uid = call("POST", "/api/intake/upload/chunk", {"name": a["name"], "data_base64": a["data_base64"], "upload_id": uid})["upload_id"]
    if not a.get("finish"):
        return text({"upload_id": uid, "next": "send the next chunk with this upload_id, or finish=true"})
    if not uid:
        raise RuntimeError("nothing uploaded")
    return text(wait_job(call("POST", "/api/intake/upload/finish", {"upload_id": uid, "category": a.get("category"), "tags": a.get("tags") or []}))["result"])


@tool("list_cloud_folders", "Find OneDrive (personal/work), Dropbox, Google Drive or iCloud folders synced on this PC. Importing from them needs no network.")
def t_cloud_folders(a):
    return text(call("GET", "/api/intake/cloud-folders"))


@tool("import_cloud_folder", "Import files from local or synced cloud folders (e.g. a OneDrive folder from list_cloud_folders) into the right CutLab library and "
      "auto-categorise them. Files are linked in place (not copied), except 3D models, motions and fonts.",
      {"paths": {"type": "array", "items": {"type": "string"}}, "recursive": {"type": "boolean"}, "categorize": {"type": "boolean"}}, ["paths"])
def t_import_folder(a):
    body = {"paths": a["paths"], "recursive": a.get("recursive", True), "categorize": a.get("categorize", True)}
    return text(wait_job(call("POST", "/api/intake/local", body), timeout=7200)["result"])


@tool("categorize_media", "Auto-categorise library media (CLIP for images/videos, name + duration for audio). Without ids: every item that has no category yet.",
      {"media_ids": {"type": "array", "items": {"type": "string"}}, "recompute": {"type": "boolean"}})
def t_categorize(a):
    return text(wait_job(call("POST", "/api/intake/categorize", {"ids": a.get("media_ids"), "only_missing": not a.get("recompute")}), timeout=3600)["result"])


@tool("list_categories", "The category taxonomy and how many library items are in each category.")
def t_categories(a):
    return text(call("GET", "/api/intake/categories"))


# ------------------------------------------------------------------------------------------ make animation (4-step flow)
def _rig_id(ref: str | None) -> str:
    rigs = call("GET", "/api/anim/rigs")
    if not ref:
        hum = [r for r in rigs if r.get("kind") == "humanoid"]
        if not hum:
            raise RuntimeError("No humanoid character in the animation library.")
        return hum[0]["id"]
    for r in rigs:
        if ref in (r["id"], r["name"]) or ref.lower() == r["name"].lower():
            return r["id"]
    raise RuntimeError(f"Character {ref!r} not found; characters: {', '.join(r['name'] for r in rigs)}")


@tool("make_animation", "Make a game-ready animation on a 3D character in one step (Animation -> Make animation). Either describe the motion "
      "(`prompt`, text-to-motion with NVIDIA Kimodo, humanoid characters only) or give a reference video (`media_id`, optional start/end seconds; "
      "humans or four-legged animals). Returns the retargeted clip, a quality score, a GLB url and a preview video. Edit it afterwards with "
      "edit_animation, export with export_to_engine (asset {type:'character', rig_id, clip_ids:[clip_id]}).",
      {"prompt": S("e.g. 'A person swings a sword overhead and strikes down'"), "media_id": S(), "start": N(), "end": N(),
       "character": S("rig name or id, e.g. 'UE5 Manny' (default: first humanoid)"), "duration": N("seconds, text mode, 1-10"), "seed": N(),
       "loop": {"type": "boolean", "description": "force a seamless loop on/off; default: text prompts for repeating motions (walk, run, idle) "
                "loop automatically when a whole cycle is found, reference videos loop"}})
def t_make_animation(a):
    rid = _rig_id(a.get("character"))
    opts = {"loop": bool(a["loop"])} if a.get("loop") is not None else {}
    if a.get("prompt"):
        j = call("POST", "/api/anim/quick/generate-text", {"prompt": a["prompt"], "rigId": rid, "duration": float(a.get("duration") or 4), "seed": a.get("seed"),
                                                           "options": opts})
    elif a.get("media_id"):
        j = call("POST", "/api/anim/quick/generate", {"mediaId": a["media_id"], "rigId": rid, "start": a.get("start"), "end": a.get("end"), "options": opts})
    else:
        raise RuntimeError("Give either prompt or media_id.")
    r = wait_job(j, timeout=3600)["result"]
    return text({**_anim_result(r), "source_clip_id": r["source_clip"]["id"], "rig_id": rid, "metrics": r.get("metrics"), "history": r.get("history")})


@tool("make_transition", "Join two humanoid animations with a generated in-between motion, or turn a one-shot animation into a seamless loop, "
      "with NVIDIA MotionBricks (runs locally, NVIDIA Open Model Agreement: commercial use allowed). Works on text-to-motion clips "
      "(make_animation with a prompt). Pass clip_a and clip_b for A -> transition -> B, or clip_a with loop=true. The result is put on the "
      "character (default: clip_a's character) like make_animation.",
      {"clip_a": S("clip id (retargeted or source)"), "clip_b": S("clip id to transition into"),
       "loop": {"type": "boolean", "description": "close clip_a into a seamless loop instead"},
       "character": S("rig name or id (default: clip_a's character)"), "distance": N("metres between A's end and B's start (default: from their speeds)"),
       "turn_deg": N("B's heading relative to A's final heading, -180..180")}, ["clip_a"])
def t_make_transition(a):
    rid = _rig_id(a["character"]) if a.get("character") else None
    j = call("POST", "/api/anim/quick/transition", {"clipA": a["clip_a"], "clipB": a.get("clip_b"), "rigId": rid, "loop": bool(a.get("loop")),
                                                     "distance": a.get("distance"), "turnDeg": float(a.get("turn_deg") or 0)})
    r = wait_job(j, timeout=3600)["result"]
    return text({**_anim_result(r), "source_clip_id": r["source_clip"]["id"], "motionbricks": r.get("motionbricks"), "metrics": r.get("metrics")})


def _anim_result(r: dict) -> dict:
    c = r["clip"]
    return {"clip_id": c["id"], "clip": c.get("name"), "score": c.get("score"), "loop": c.get("loop"), "duration_s": c["duration"], "glb_url": r["glb_url"],
            "options": r["options"], "issues": [i.get("message") for i in ((r.get("metrics") or {}).get("issues") or [])],
            "notes": [n for n in (r.get("notes") or []) if n.startswith("loop")], "preview_media_id": (r.get("media") or {}).get("id")}


@tool("edit_animation", "Change an animation in plain words ('20% slower', 'less foot sliding', 'make it a seamless loop', "
      "'only the first 2 seconds'). Pass the clip_id of the animation to change (from make_animation, a previous edit or the Animation library); "
      "its motion, character and settings are looked up. Each edit makes a new clip.",
      {"clip_id": S(), "instruction": S(), "options": {"type": "object", "description": "optional explicit settings that override the clip's"}},
      ["clip_id", "instruction"])
def t_edit_animation(a):
    body = {"clipId": a.get("clip_id"), "sourceClipId": a.get("source_clip_id"), "rigId": a.get("rig_id"), "instruction": a["instruction"],
            "options": a.get("options") or {}}
    r = wait_job(call("POST", "/api/anim/quick/edit", body), timeout=3600)["result"]
    return text({**_anim_result(r), "edit": r.get("edit")})


@tool("auto_tune_animation", "Automatically try settings (foot lock, ground fix, smoothing, slightly smaller strides) to raise an animation's "
      "quality score to a target. Keeps the loop / in-place choice and never shrinks strides below 80%. Pass the clip_id. Takes 1-3 minutes.",
      {"clip_id": S(), "target": N("score 50-100, default 90"), "max_tries": N("1-20, default 8")}, ["clip_id"])
def t_auto_tune(a):
    r = wait_job(call("POST", "/api/anim/quick/autotune", {"clipId": a["clip_id"], "target": float(a.get("target") or 90),
                                                           "max_tries": int(a.get("max_tries") or 8)}), timeout=3600)["result"]
    at = r.get("autotune") or {}
    return text({**_anim_result(r), "reached": at.get("reached"), "tries": [{k: t.get(k) for k in ("change", "score")} for t in at.get("tries", [])],
                 "remaining_penalties": at.get("remaining")})


@tool("generate_3d_model", "Make a textured 3D model (GLB) for free on this PC with TRELLIS.2 (MIT) in CutLab's ComfyUI: from an image "
      "in the library (`media_id`, best: one object, whole body visible) or from text (`prompt`, a reference image is made first with "
      "Z-Image Turbo). Takes 2-5 minutes on the GPU. The result is UNRIGGED and lands in 3D Models > Model library; export it with "
      "export_to_engine. Paid cloud generators with auto-rig (Meshy/Tripo) are only reachable through cutlab_api with the user's cost consent.",
      {"prompt": S(), "media_id": S(), "quality": S("draft | standard (default) | high"), "name": S("file name stem")})
def t_generate_3d(a):
    if not (a.get("prompt") or a.get("media_id")):
        raise RuntimeError("Give either prompt or media_id.")
    body = {"provider": "local", "prompt": None if a.get("media_id") else a.get("prompt"), "image_mediaId": a.get("media_id"),
            "quality": a.get("quality") or "standard", "name": a.get("name")}
    r = wait_job(call("POST", "/api/gen3d", body), timeout=1800)["result"]
    return text({"file": r["file"], "glb_url": r["url"], "rigged": r["rigged"], "info": r.get("info"),
                 "reference_image_media_id": (r.get("preview_media") or {}).get("id")})


# ------------------------------------------------------------------------------------------ game engines
@tool("list_engine_projects", "Game-engine projects found on this PC (Godot, Unity, Unreal, three.js) with their folders and versions.",
      {"root": S("optional folder to search instead of D:\, Documents and Desktop"), "refresh": {"type": "boolean"}})
def t_engine_projects(a):
    p = {k: v for k, v in {"root": a.get("root"), "refresh": "true" if a.get("refresh") else None}.items() if v}
    return text(call("GET", "/api/engine/projects", params=p))


@tool("export_to_engine", "Export a CutLab asset into a game-engine project in a format it imports by itself: Godot -> GLB in assets/cutlab, "
      "Unity -> FBX in Assets/CutLab, three.js -> GLB in public/assets/cutlab (+ shellFur.ts for fur), Unreal -> FBX in SourceArt/CutLab plus "
      "import_to_unreal.py (run it with an Unreal MCP's Python tool or Tools > Execute Python Script to get /Game/CutLab/<name>), folder -> GLB + FBX. "
      "asset: {type:'character', rig_id, clip_ids?} (rigged character with its retargeted clips) | {type:'fur', groom_id} | "
      "{type:'model', ref:'hub:<id>'|'file:<name>'|'local:<id>'|'rig:<id>'} | {type:'media', media_id}. Writes only inside the project, never overwrites unless asked.",
      {"asset": {"type": "object"}, "engine": {"type": "string", "enum": ["godot", "unity", "unreal", "threejs", "folder"]}, "project": S("project folder"),
       "name": S(), "overwrite": {"type": "boolean"}}, ["asset", "engine", "project"])
def t_export_engine(a):
    body = {"asset": a["asset"], "engine": a["engine"], "project": a["project"], "name": a.get("name"), "overwrite": bool(a.get("overwrite"))}
    return text(wait_job(call("POST", "/api/engine/export", body), timeout=3600)["result"])


@tool("download_video", "Download a video from a URL or a pasted share text (YouTube, Bilibili, X, Facebook, Instagram, TikTok, Douyin, Xiaohongshu…). Only for content the user has the right to use.",
      {"url": S(), "quality": {"type": "string", "enum": ["best", "2160", "1440", "1080", "720", "480", "audio"]}}, ["url"])
def t_download(a):
    return text(wait_job(call("POST", "/api/download", {"url": a["url"], "quality": a.get("quality") or "best"}))["result"])


@tool("segment_media", "Split a media item into segments: scene cuts, speech/silence, fixed length or ASR sentences. Returns [{start,end,label,text?}] in source seconds.",
      {"media_id": S(), "method": {"type": "string", "enum": ["scene", "silence", "fixed", "asr"]}, "threshold": N(), "every": N("seconds for fixed"), "min_len": N(), "language": S()},
      ["media_id", "method"])
def t_segment(a):
    body = {"mediaId": a["media_id"], "method": a["method"], **{k: a[k] for k in ("threshold", "every", "min_len", "language") if a.get(k) is not None}}
    return text(wait_job(call("POST", "/api/segment", body))["result"])


@tool("transcribe", "Speech to text (local Whisper) with timestamps; also writes an SRT.", {"media_id": S(), "language": S("e.g. en, zh, auto")}, ["media_id"])
def t_transcribe(a):
    r = wait_job(call("POST", "/api/ai/subtitles", {"mediaId": a["media_id"], "language": a.get("language") or "auto"}))["result"]
    r.pop("srt", None)
    return text(r)


@tool("generate_music", "Generate background music locally (ACE-Step if running, else the procedural generator). Adds it to the library.",
      {"prompt": S(), "duration": N("seconds"), "provider": {"type": "string", "enum": ["auto", "acestep", "procedural"]}}, ["prompt"])
def t_music(a):
    return text(wait_job(call("POST", "/api/music/generate", {"prompt": a["prompt"], "duration": int(a.get("duration") or 30), "provider": a.get("provider") or "auto", "instrumental": True}))["result"])


@tool("text_to_speech", "Generate a voice-over locally and add it to the library.", {"text": S(), "voice": S(), "speed": N()}, ["text"])
def t_tts(a):
    return text(wait_job(call("POST", "/api/tts/generate", {"text": a["text"], "voice": a.get("voice"), "speed": a.get("speed") or 1}))["result"])


@tool("upscale_media", "Upscale a video or image (Real-ESRGAN when installed, else Lanczos). Creates a new library item.", {"media_id": S(), "scale": {"type": "integer", "enum": [2, 4]}}, ["media_id"])
def t_upscale(a):
    return text(wait_job(call("POST", "/api/ai/upscale", {"mediaId": a["media_id"], "scale": a.get("scale") or 2, "model": "auto"}))["result"])


@tool("remove_watermark", "Remove a static watermark. Without regions it auto-detects them first. Regions are pixel boxes in source coordinates.",
      {"media_id": S(), "regions": {"type": "array", "items": {"type": "object", "properties": {"x": N(), "y": N(), "w": N(), "h": N()}}},
       "method": {"type": "string", "enum": ["auto", "delogo", "inpaint"]}}, ["media_id"])
def t_watermark(a):
    regions = a.get("regions")
    if not regions:
        det = call("POST", "/api/ai/watermark/detect", {"mediaId": a["media_id"]})
        regions = [{k: r[k] for k in ("x", "y", "w", "h")} for r in (det.get("regions") or det if isinstance(det, list) else det.get("regions", []))]
        if not regions:
            return text({"detail": "No static watermark detected; pass regions explicitly.", "detect": det})
    return text(wait_job(call("POST", "/api/ai/watermark", {"mediaId": a["media_id"], "regions": regions, "method": a.get("method") or "auto"}))["result"])


@tool("auto_cut", "Suggest which parts of a media item to keep: tight (remove silences), highlights (energy/motion peaks), talking-head.",
      {"media_id": S(), "style": {"type": "string", "enum": ["tight", "highlights", "talking-head"]}, "target_duration": N()}, ["media_id"])
def t_autocut(a):
    return text(wait_job(call("POST", "/api/ai/auto-cut", {"mediaId": a["media_id"], "style": a.get("style") or "tight", "target_duration": a.get("target_duration")}))["result"])


@tool("view_media_frame", "Look at a frame of a library video (or an image) at time t. Returns the picture.", {"media_id": S(), "t": N("seconds")}, ["media_id"])
def t_view_media(a):
    return image(call("GET", "/api/ai/frame", params={"mediaId": a["media_id"], "t": a.get("t") or 0}), f"media {a['media_id']} @ {a.get('t') or 0}s")


@tool("get_timeline", "Read the timeline currently open in the Video Editor: tracks, clips (ids, start, duration, asset ids, text), playhead and selection. Set full=true for the complete project JSON.",
      {"full": {"type": "boolean"}})
def t_get_timeline(a):
    snap = bridge_project()
    p = snap["project"]
    out = {"connected": snap["connected"], "playhead": snap["playhead"], "selection": snap["selection"],
           "project": p if a.get("full") else summarize_project(p)}
    return text(out)


OPS_DOC = ("Ops (applied in order as ONE undo step): add_clip{assetId,track?:'auto'|trackId,start?,in?,out?}, split{clipId,at}, delete_clip{clipId}, "
           "trim{clipId,in?,out?}, move{clipId,start,track?}, set{clipId,patch}, set_speed{clipId,speed}, add_transition{clipId,type:fade|slideleft|slideright|slideup|slidedown|zoomin|wipeleft|wiperight,duration}, "
           "add_text{text,start,duration,style?}, add_music{assetId,start?,volume?}, add_subtitles{segments:[{start,end,text}],style?}, remove_ranges{ranges:[{start,end}]} (ripple delete, timeline seconds), "
           "detach_audio{clipId} (split a video clip's sound onto its own audio-track clip; the video clip goes silent).")


@tool("edit_timeline", "Edit the live Video Editor timeline. The user sees the change immediately and can undo it with Ctrl+Z. " + OPS_DOC,
      {"ops": {"type": "array", "items": {"type": "object"}}}, ["ops"])
def t_edit(a):
    r = call("POST", "/api/bridge/command", {"type": "ops", "payload": {"ops": a["ops"]}, "wait": 20})
    if r.get("status") == "done":
        r["timeline"] = summarize_project(bridge_project()["project"])
    return text(r)


@tool("new_timeline", "Replace the editor's project with a new empty one (the previous one stays in undo history).",
      {"name": S(), "width": N(), "height": N(), "fps": N()})
def t_new(a):
    proj = {"version": 1, "name": a.get("name") or "Untitled project", "width": int(a.get("width") or 1920), "height": int(a.get("height") or 1080),
            "fps": a.get("fps") or 30, "background": "#000000", "tracks": []}
    return text(call("POST", "/api/bridge/command", {"type": "set_project", "payload": {"project": proj}, "wait": 20}))


@tool("seek", "Move the editor playhead.", {"t": N("seconds")}, ["t"])
def t_seek(a):
    return text(call("POST", "/api/bridge/command", {"type": "seek", "payload": {"t": a["t"]}, "wait": 10}))


@tool("view_timeline_frame", "Render the CURRENT timeline at time t exactly as it will export (all tracks, text, effects) and return the picture.",
      {"t": N("seconds"), "width": N("default 960")}, ["t"])
def t_view_tl(a):
    p = bridge_project()["project"]
    return image(call("POST", "/api/export/frame", {"project": p, "t": a["t"], "width": int(a.get("width") or 960)}), f"timeline @ {a['t']}s")


@tool("export_timeline", "Export the current timeline to a file (mp4/webm/gif/mp3). Returns the output path; the result is also added to the library.",
      {"format": {"type": "string", "enum": ["mp4", "webm", "gif", "mp3"]}, "quality": {"type": "string", "enum": ["draft", "normal", "high"]}, "width": N(), "height": N()})
def t_export(a):
    p = bridge_project()["project"]
    preset = {"format": a.get("format") or "mp4", "quality": a.get("quality") or "normal", **{k: int(a[k]) for k in ("width", "height") if a.get(k)}}
    return text(wait_job(call("POST", "/api/export", {"project": p, "preset": preset}))["result"])


@tool("cutlab_api", "Escape hatch: call any CutLab HTTP endpoint under /api (e.g. /api/anim/*, /api/mocap, /api/gen3d, /api/videogen, /api/assets/catalog). "
      "If the response is a job it is awaited. Cloud generators (gen3d, videogen) spend the USER'S credits: only call them with confirm_cost:true after the user explicitly agreed to the cost.",
      {"method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]}, "path": S("must start with /api/"), "body": {"type": "object"}, "query": {"type": "object"},
       "wait_job": {"type": "boolean"}}, ["method", "path"])
def t_api(a):
    path = a["path"]
    if not path.startswith("/api/") or ".." in path:
        raise ValueError("path must start with /api/")
    if path.startswith("/api/settings") and a["method"] != "GET":
        raise ValueError("settings (API keys) can only be changed by the user in the CutLab Settings dialog")
    r = call(a["method"], path, a.get("body"), a.get("query"))
    if isinstance(r, (bytes, bytearray)):
        return image(bytes(r))
    if a.get("wait_job", True) and isinstance(r, dict) and {"id", "status", "kind"} <= r.keys():
        r = wait_job(r)
    return text(r)


# ------------------------------------------------------------------------------------------- protocol
INSTRUCTIONS = ("CutLab is a local video editor + image editor + AI asset factory. Typical flow: status -> list_media / download_video / import_media -> "
                "segment_media / transcribe -> get_timeline -> edit_timeline (live, undoable) -> view_timeline_frame to check -> export_timeline. "
                "Files from OneDrive/other drives: list_cloud_folders + import_cloud_folder (synced folders), or import_from_links with download URLs; they are auto-categorised. "
                "Animations: make_animation (text prompt or reference video) + edit_animation; make_transition joins two text-to-motion clips or loops one (MotionBricks). Game engines: list_engine_projects + export_to_engine (Godot/Unity/three.js auto-import; Unreal returns a Python import script to run via an Unreal MCP). "
                "Look at frames (view_media_frame / view_timeline_frame) before and after edits. Timeline edits need the Video Editor open at http://localhost:5173.")


def handle(msg: dict) -> dict | None:
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:                                   # notification
        return None
    try:
        if method == "initialize":
            v = params.get("protocolVersion")
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": v if v in PROTOCOLS else PROTOCOLS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "cutlab", "version": "0.1.0"}, "instructions": INSTRUCTIONS}}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": mid, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": n, "description": d, "inputSchema": s} for n, (d, s, _) in TOOLS.items()]}}
        if method == "tools/call":
            name = params.get("name")
            if name not in TOOLS:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {name}"}}
            try:
                content = TOOLS[name][2](params.get("arguments") or {})
                return {"jsonrpc": "2.0", "id": mid, "result": {"content": content, "isError": False}}
            except Exception as e:  # tool errors are results, so the model can react
                return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}}
        if method in ("resources/list", "prompts/list"):
            return {"jsonrpc": "2.0", "id": mid, "result": {method.split("/")[0]: []}}
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    except Exception as e:  # noqa: BLE001
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}


def main() -> None:
    out = sys.stdout.buffer
    for raw in sys.stdin.buffer:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            out.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}).encode() + b"\n")
            out.flush()
            continue
        for m in (msg if isinstance(msg, list) else [msg]):
            resp = handle(m)
            if resp is not None:
                out.write(json.dumps(resp, ensure_ascii=False).encode("utf-8") + b"\n")
                out.flush()


if __name__ == "__main__":
    main()
