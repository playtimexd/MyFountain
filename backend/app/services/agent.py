"""Editing agent: Ollama tool-calling loop -> {reply, ops, toolTrace}. Ops are validated/repaired against the project."""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from .. import config, db
from ..ffutil import _NOWIN
from ..jobs import jobs
from . import asr, autocut, rag
from . import music_providers

MAX_ITER = 6
TRANSITIONS = {"fade", "slideleft", "slideright", "slideup", "slidedown", "zoomin", "wipeleft", "wiperight"}
TRANS_ALIAS = {"dissolve": "fade", "crossfade": "fade", "淡入淡出": "fade", "淡入": "fade", "left": "slideleft", "right": "slideright",
               "up": "slideup", "down": "slidedown", "zoom": "zoomin", "wipe": "wipeleft", "slide": "slideleft", "推": "slideleft", "擦除": "wipeleft", "缩放": "zoomin"}
CLIP_FIELDS = {"start", "in", "out", "speed", "volume", "fadeIn", "fadeOut", "fit", "transform", "adjust", "filter", "text", "transitionIn", "name", "keyframes"}

TOOLS = [
    {"type": "function", "function": {"name": "search_library", "description": "Semantic search over the local media library and indexed folders for videos, audio and images (file names, transcripts, visual captions).",
                                        "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "search text"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "list_media", "description": "List media in the library (id, name, duration).",
                                        "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["video", "audio", "image"], "description": "optional filter by kind"}}}}},
    {"type": "function", "function": {"name": "media_info", "description": "Get details of one media item (duration, resolution, frame rate, has audio).",
                                        "parameters": {"type": "object", "properties": {"mediaId": {"type": "string"}}, "required": ["mediaId"]}}},
    {"type": "function", "function": {"name": "detect_silences", "description": "Detect silent parts of a media item. Returns media times and, if the media is on the timeline, the matching timeline times.",
                                        "parameters": {"type": "object", "properties": {"mediaId": {"type": "string"}}, "required": ["mediaId"]}}},
    {"type": "function", "function": {"name": "find_scenes", "description": "Detect shot changes (scene cuts) in a video and return each scene's start/end in seconds.",
                                        "parameters": {"type": "object", "properties": {"mediaId": {"type": "string"}}, "required": ["mediaId"]}}},
    {"type": "function", "function": {"name": "transcribe", "description": "Transcribe speech in a media item into timestamped text (needs a local Whisper model).",
                                        "parameters": {"type": "object", "properties": {"mediaId": {"type": "string"}}, "required": ["mediaId"]}}},
    {"type": "function", "function": {"name": "generate_music", "description": "Generate background music and add it to the library; returns the new media id. Then use an add_music op to put it on the timeline.",
                                        "parameters": {"type": "object", "properties": {"prompt": {"type": "string", "description": "music style / mood description"},
                                                                                       "duration": {"type": "integer", "description": "seconds (10-120)"},
                                                                                       "engine": {"type": "string", "enum": ["fast", "ai"], "description": "fast (default, seconds) or ai (best local model, can take minutes)"}}, "required": ["prompt"]}}},
    {"type": "function", "function": {"name": "project_summary", "description": "Get an overview of the current project's tracks and clips (clip ids, media, start/duration).",
                                        "parameters": {"type": "object", "properties": {}}}},
]

SYSTEM = """You are the AI editing assistant built into the CutLab video editor. Be concise.
Reply in English by default; if the user writes in another language, answer in that language.
You can call tools to look up media / project information, then propose "ops" that modify the timeline. The editor applies the ops only after the user confirms.
Rules:
1. Media ids and clip ids must come from tool results or the project overview below. Never invent ids.
2. Times are in seconds. The start/end of remove_ranges are TIMELINE times, not media times. A media time t maps to the timeline as clip.start + (t - clip.in) / clip.speed. detect_silences already returns timelineRanges that you can use directly.
3. Your final answer must be a single JSON object (no markdown code fence): {"reply": "short explanation for the user", "ops": [...]}. Use "ops": [] when the timeline does not need to change.
4. Available ops:
 {"op":"add_clip","assetId":MEDIA_ID,"track":"auto","start":SEC,"in":SEC,"out":SEC}   (omit in/out for the whole media)
 {"op":"split","clipId":..,"at":TIMELINE_SEC}   {"op":"delete_clip","clipId":..}   {"op":"trim","clipId":..,"in":SEC,"out":SEC}
 {"op":"move","clipId":..,"start":SEC}   {"op":"set","clipId":..,"patch":{"volume":0.5,"transform":{"scale":1.2},"adjust":{"brightness":0.1}}}
 {"op":"set_speed","clipId":..,"speed":0.1-8}   {"op":"add_transition","clipId":..,"type":"fade|slideleft|slideright|slideup|slidedown|zoomin|wipeleft|wiperight","duration":SEC}
 {"op":"add_text","text":"TEXT","start":SEC,"duration":SEC}   {"op":"add_music","assetId":AUDIO_MEDIA_ID,"start":0,"volume":0.3}
 {"op":"add_subtitles","segments":[{"start":SEC,"end":SEC,"text":".."}]}   {"op":"remove_ranges","ranges":[{"start":SEC,"end":SEC}]}
5. Only propose the changes the user asked for. If unsure, call a tool to check first, or ask in "reply" and leave "ops" empty."""


# ------------------------------------------------------------------------------- project helpers
def _clip_index(project: dict | None) -> dict[str, dict]:
    out = {}
    for t in (project or {}).get("tracks", []) or []:
        for c in t.get("clips", []) or []:
            if isinstance(c, dict) and c.get("id"):
                out[str(c["id"])] = {**c, "_track": t.get("id")}
    return out


def _cdur(c: dict) -> float:
    try:
        return max(0.01, (float(c.get("out", 0)) - float(c.get("in", 0))) / (float(c.get("speed") or 1) or 1))
    except Exception:
        return 0.0


def project_summary(project: dict | None) -> dict:
    if not project or not project.get("tracks"):
        return {"empty": True, "message": "There is no project open (or it is empty)"}
    tracks = []
    n = 0
    for t in project["tracks"]:
        clips = []
        for c in t.get("clips", []):
            n += 1
            if n > 80:
                break
            m = db.get_media(c.get("assetId")) if c.get("assetId") else None
            clips.append({"id": c.get("id"), "type": c.get("type"), "assetId": c.get("assetId"), "name": (m or {}).get("name") or c.get("name") or (c.get("text") or {}).get("content"),
                          "start": round(float(c.get("start", 0)), 2), "duration": round(_cdur(c), 2), "in": c.get("in"), "out": c.get("out"), "speed": c.get("speed", 1)})
        tracks.append({"id": t.get("id"), "kind": t.get("kind"), "name": t.get("name"), "clips": clips})
    end = max((c["start"] + c["duration"] for t in tracks for c in t["clips"]), default=0)
    return {"name": project.get("name"), "size": f"{project.get('width')}x{project.get('height')}@{project.get('fps')}", "duration": round(end, 2), "tracks": tracks}


def _to_timeline(project: dict | None, media_id: str, ranges: list[tuple[float, float]]) -> list[dict]:
    """Map source-time ranges of `media_id` onto the timeline through the first clip using it."""
    for c in _clip_index(project).values():
        if c.get("assetId") == media_id:
            sp = float(c.get("speed") or 1) or 1
            cin, cout, cs = float(c.get("in", 0)), float(c.get("out", 0)), float(c.get("start", 0))
            out = []
            for s, e in ranges:
                s2, e2 = max(s, cin), min(e, cout)
                if e2 - s2 > 0.05:
                    out.append({"start": round(cs + (s2 - cin) / sp, 3), "end": round(cs + (e2 - cin) / sp, 3), "clipId": c["id"]})
            return out
    return []


# ------------------------------------------------------------------------------- tool implementations
def _need_media(mid: str) -> dict:
    m = db.get_media(str(mid))
    if not m:
        raise ValueError(f"Media {mid} not found; use list_media / search_library to get a valid id")
    return m


def t_find_scenes(m: dict) -> dict:
    if m["kind"] != "video":
        return {"scenes": [], "note": "Scene detection only works on videos"}
    p = subprocess.run([config.FFMPEG, "-hide_banner", "-nostats", "-i", m["path"], "-an", "-vf", "select='gt(scene,0.32)',showinfo", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", **_NOWIN)
    cuts = [float(x) for x in re.findall(r"pts_time:([\d.]+)", p.stderr)]
    bounds = [0.0] + [c for c in cuts if 0.3 < c < m["duration"] - 0.3] + [m["duration"]]
    scenes = [{"start": round(a, 2), "end": round(b, 2)} for a, b in zip(bounds, bounds[1:]) if b - a > 0.2]
    return {"count": len(scenes), "scenes": scenes[:60]}


def t_generate_music(prompt: str, duration: int = 30, engine: str = "fast") -> dict:
    # "fast" = procedural generator (seconds); "ai" = best available model (ACE-Step / MusicGen, can take minutes)
    req = {"prompt": prompt, "duration": int(max(10, min(120, duration or 30))), "instrumental": True,
           "provider": "auto" if engine == "ai" else "procedural"}
    job = jobs.submit("music", f"Agent music: {prompt[:16]}", lambda ctx: music_providers.generate(ctx, req))
    t0 = time.time()
    while job.status in ("queued", "running") and time.time() - t0 < 240:
        time.sleep(0.5)
    if job.status != "done":
        raise RuntimeError(job.error or "Music generation timed out")
    m = job.result["media"]
    return {"mediaId": m["id"], "name": m["name"], "duration": round(m["duration"], 1), "provider": job.result.get("provider")}


def run_tool(name: str, args: dict, project: dict | None) -> Any:
    if name == "search_library":
        return {"results": rag.search_library(str(args.get("query", "")), 8)}
    if name == "list_media":
        rows = db.list_media(args.get("kind") or None, limit=40)
        return {"count": len(rows), "items": [{"mediaId": r["id"], "name": r["name"], "kind": r["kind"], "duration": round(r["duration"], 1),
                                               "size": f"{r['width']}x{r['height']}" if r["width"] else ""} for r in rows]}
    if name == "media_info":
        m = _need_media(args.get("mediaId"))
        return {k: m[k] for k in ("id", "name", "kind", "duration", "width", "height", "fps", "has_audio", "tags", "origin")}
    if name == "detect_silences":
        m = _need_media(args.get("mediaId"))
        if not m["has_audio"]:
            return {"silences": [], "note": "The media has no audio track"}
        sil, thr = autocut.detect_silences(m["path"])
        rng = [(s, (m["duration"] if e < 0 else e)) for s, e in sil]
        return {"thresholdDb": round(thr), "count": len(rng), "silences": [{"start": round(s, 2), "end": round(e, 2)} for s, e in rng[:50]],
                "timelineRanges": _to_timeline(project, m["id"], rng)[:50]}
    if name == "find_scenes":
        return t_find_scenes(_need_media(args.get("mediaId")))
    if name == "transcribe":
        m = _need_media(args.get("mediaId"))
        st = asr.status()
        if not st["ready"]:
            return {"error": st["message"]}
        res = asr.transcribe(m["path"], ctx=None)
        return {"language": res["language"], "segments": res["segments"][:80]}
    if name == "generate_music":
        return t_generate_music(str(args.get("prompt", "background music")), int(args.get("duration") or 30), str(args.get("engine") or "fast"))
    if name == "project_summary":
        return project_summary(project)
    return {"error": f"Unknown tool {name}"}


# ------------------------------------------------------------------------------- op validation
def _num(v, lo=None, hi=None, default=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if x != x or x in (float("inf"), float("-inf")):
        return default
    if lo is not None and x < lo:
        x = lo
    if hi is not None and x > hi:
        x = hi
    return x


def validate_ops(ops: Any, project: dict | None) -> tuple[list[dict], list[dict]]:
    """Return (valid ops, dropped [{op, reason}]). Repairs obvious problems (string numbers, synonyms, clamping)."""
    good: list[dict] = []
    bad: list[dict] = []
    clips = _clip_index(project)
    track_ids = {t.get("id") for t in (project or {}).get("tracks", [])}
    if not isinstance(ops, list):
        return [], [{"op": ops, "reason": "ops is not an array"}]
    for raw in ops:
        def drop(why, raw=raw):
            bad.append({"op": raw, "reason": why})
        if not isinstance(raw, dict) or not isinstance(raw.get("op"), str):
            drop("missing op field"); continue
        k = raw["op"]
        o: dict = {"op": k}
        if k == "add_clip":
            m = db.get_media(str(raw.get("assetId")))
            if not m: drop("assetId does not exist"); continue
            o["assetId"] = m["id"]
            t = raw.get("track", "auto")
            o["track"] = t if (t == "auto" or t in track_ids) else "auto"
            for f in ("start", "in", "out"):
                v = _num(raw.get(f), 0)
                if v is not None: o[f] = v
            if "in" in o and "out" in o and o["out"] <= o["in"]: o.pop("out")
            if m["duration"]:
                if "out" in o: o["out"] = min(o["out"], m["duration"])
                if "in" in o and o["in"] >= m["duration"]: o["in"] = 0
        elif k in ("split", "delete_clip", "trim", "move", "set", "set_speed", "add_transition"):
            cid = str(raw.get("clipId"))
            if cid not in clips: drop(f"clipId {cid} is not in the project"); continue
            c = clips[cid]
            o["clipId"] = cid
            cs, ce = float(c.get("start", 0)), float(c.get("start", 0)) + _cdur(c)
            if k == "split":
                at = _num(raw.get("at"))
                if at is None or not (cs + 0.05 < at < ce - 0.05): drop(f"split position {raw.get('at')} is outside the clip ({cs:.2f}-{ce:.2f})"); continue
                o["at"] = at
            elif k == "trim":
                i, ou = _num(raw.get("in"), 0), _num(raw.get("out"), 0)
                if i is None and ou is None: drop("trim needs in/out"); continue
                if i is not None and ou is not None and ou <= i: drop("trim: out must be greater than in"); continue
                if i is not None: o["in"] = i
                if ou is not None: o["out"] = ou
            elif k == "move":
                s = _num(raw.get("start"), 0)
                if s is None: drop("move needs start"); continue
                o["start"] = s
                if raw.get("track") in track_ids: o["track"] = raw["track"]
            elif k == "set":
                patch = raw.get("patch")
                if not isinstance(patch, dict): drop("set needs a patch"); continue
                p2 = {a: b for a, b in patch.items() if a in CLIP_FIELDS}
                if "speed" in p2: p2["speed"] = _num(p2["speed"], 0.1, 8, 1)
                if "volume" in p2: p2["volume"] = _num(p2["volume"], 0, 2, 1)
                if not p2: drop("patch has no editable fields"); continue
                o["patch"] = p2
            elif k == "set_speed":
                sp = _num(raw.get("speed"), 0.1, 8)
                if sp is None: drop("set_speed needs speed"); continue
                o["speed"] = sp
            elif k == "add_transition":
                ty = str(raw.get("type", "fade")).lower()
                ty = TRANS_ALIAS.get(ty, ty)
                if ty not in TRANSITIONS: ty = "fade"
                o["type"] = ty
                o["duration"] = _num(raw.get("duration"), 0.1, 5, 0.5)
        elif k == "add_text":
            txt = raw.get("text")
            if not isinstance(txt, str) or not txt.strip(): drop("add_text needs text"); continue
            o["text"] = txt
            o["start"] = _num(raw.get("start"), 0, default=0.0)
            o["duration"] = _num(raw.get("duration"), 0.2, 3600, 3.0)
            if isinstance(raw.get("style"), dict): o["style"] = raw["style"]
        elif k == "add_music":
            m = db.get_media(str(raw.get("assetId")))
            if not m or m["kind"] != "audio": drop("add_music needs an existing audio assetId"); continue
            o["assetId"] = m["id"]
            if raw.get("start") is not None: o["start"] = _num(raw.get("start"), 0, default=0.0)
            if raw.get("volume") is not None: o["volume"] = _num(raw.get("volume"), 0, 2, 0.4)
        elif k == "add_subtitles":
            segs = []
            for s in raw.get("segments") or []:
                if not isinstance(s, dict): continue
                a, b = _num(s.get("start"), 0), _num(s.get("end"), 0)
                if a is None or b is None or b <= a or not str(s.get("text", "")).strip(): continue
                segs.append({"start": a, "end": b, "text": str(s["text"]).strip()})
            if not segs: drop("add_subtitles has no valid segments"); continue
            o["segments"] = segs
            if isinstance(raw.get("style"), dict): o["style"] = raw["style"]
        elif k == "remove_ranges":
            rs = []
            for r in raw.get("ranges") or []:
                if not isinstance(r, dict): continue
                a, b = _num(r.get("start"), 0), _num(r.get("end"), 0)
                if a is None or b is None or b - a < 0.02: continue
                rs.append({"start": a, "end": b})
            if not rs: drop("remove_ranges has no valid ranges"); continue
            o["ranges"] = rs
        else:
            drop(f"unknown op {k}"); continue
        good.append(o)
    return good, bad


# ------------------------------------------------------------------------------- the loop
def _chat(model: str, url: str, messages: list[dict], tools: list | None, fmt: Any = None, num_predict: int = 1500) -> dict:
    body: dict = {"model": model, "messages": messages, "stream": False, "think": False,
                  "options": {"temperature": 0.3, "num_ctx": 8192, "num_predict": num_predict}}
    if tools:
        body["tools"] = tools
    if fmt:
        body["format"] = fmt
    r = httpx.post(url.rstrip("/") + "/api/chat", json=body, timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"Ollama {r.status_code}: {r.text[:300]}")
    return r.json()["message"]


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    t = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    t = re.sub(r"^```(?:json)?|```$", "", t.strip(), flags=re.M).strip()
    try:
        j = json.loads(t)
        return j if isinstance(j, dict) else None
    except Exception:
        pass
    a = t.find("{")
    while a != -1:
        depth, in_s, esc = 0, False, False
        for i in range(a, len(t)):
            ch = t[i]
            if in_s:
                if esc: esc = False
                elif ch == "\\": esc = True
                elif ch == '"': in_s = False
            elif ch == '"': in_s = True
            elif ch == "{": depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        j = json.loads(t[a:i + 1])
                        if isinstance(j, dict) and ("reply" in j or "ops" in j):
                            return j
                    except Exception:
                        pass
                    break
        a = t.find("{", a + 1)
    return None


_FINAL_SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}, "ops": {"type": "array", "items": {"type": "object"}}}, "required": ["reply", "ops"]}


def chat(messages: list[dict], project: dict | None = None, selection: dict | None = None) -> dict:
    s = config.load_settings()
    model, url = s["llm_model"], s["ollama_url"]
    sysmsg = SYSTEM
    if project and project.get("tracks"):
        sysmsg += "\n\nCurrent project overview:\n" + json.dumps(project_summary(project), ensure_ascii=False)
    else:
        sysmsg += "\n\nNo project is open: ops may only reference library media (add_clip / add_music ...) or you can just give text advice."
    if selection and (selection.get("clipIds") or selection.get("mediaIds")):
        sysmsg += "\nCurrently selected by the user: " + json.dumps(selection, ensure_ascii=False)
    tools = [t for t in TOOLS if t["function"]["name"] != "transcribe" or asr.status()["ready"]]
    msgs: list[dict] = [{"role": "system", "content": sysmsg}]
    for m in messages[-16:]:
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
            msgs.append({"role": m["role"], "content": m["content"]})
    trace: list[dict] = []
    final_text = ""
    for it in range(MAX_ITER + 1):
        last = it == MAX_ITER
        msg = _chat(model, url, msgs, None if last else tools)
        calls = msg.get("tool_calls") or []
        if not calls or last:
            final_text = msg.get("content") or ""
            break
        msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for call in calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try: args = json.loads(args)
                except Exception: args = {}
            t0 = time.time()
            try:
                result = run_tool(name, args, project)
            except Exception as e:  # noqa: BLE001
                result = {"error": f"{type(e).__name__}: {e}"}
            ms = int((time.time() - t0) * 1000)
            txt = json.dumps(result, ensure_ascii=False)
            trace.append({"tool": name, "args": args, "result": result if len(txt) < 1500 else txt[:1500] + "…", "ms": ms})
            msgs.append({"role": "tool", "tool_name": name, "content": txt[:6000]})
    parsed = _extract_json(final_text)
    if parsed is None:
        # structured-output repair pass
        fix = msgs + [{"role": "assistant", "content": final_text},
                      {"role": "user", "content": 'Rewrite your final answer as JSON: {"reply": "explanation for the user", "ops": [array of ops, or an empty array]}'}]
        try:
            msg = _chat(model, url, fix, None, fmt=_FINAL_SCHEMA)
            parsed = _extract_json(msg.get("content") or "")
        except Exception:
            parsed = None
    if parsed is None:
        parsed = {"reply": re.sub(r"<think>.*?</think>", "", final_text, flags=re.S).strip() or "(the model returned nothing)", "ops": []}
    ops, dropped = validate_ops(parsed.get("ops") or [], project)
    return {"reply": str(parsed.get("reply") or ""), "ops": ops, "toolTrace": trace, "droppedOps": dropped, "model": model}
