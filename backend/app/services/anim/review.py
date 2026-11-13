"""Annotation (tags / caption) and QA review of retargeted clips.

review modes
  host   : NO model call. Returns frames (base64 JPEG), the QA rubric and measured metrics so an external model (e.g. Claude Code over
           MCP, which can look at the images itself) can judge the result.
  local  : same inputs, sent to the local Ollama model (needs a vision model; falls back to the rule-based findings on failure)
  cloud  : same inputs, sent to the user's Anthropic key (frames leave the machine!)
The numeric metrics are always computed here and always returned.
"""
from __future__ import annotations

import base64
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from ... import config
from ...jobs import JobContext
from .. import llm
from . import blender, db, metrics, search

_NOWIN = {"creationflags": 0x08000000} if sys.platform == "win32" else {}


# ------------------------------------------------------------------------------------------------ annotate
_TEXT_GAIT = [("run", re.compile(r"\b(run|runs|running|sprint\w*|jog\w*|gallop\w*)\b|跑", re.I)),
              ("walk", re.compile(r"\b(walk\w*|sneak\w*|stroll\w*|march\w*|limp\w*|tiptoe\w*|trot\w*)\b|走", re.I)),
              ("idle", re.compile(r"\b(idle|standing still|breath\w*)\b|待机|站立", re.I))]


def _gait(c: dict, st: dict, hps: float, quad: bool) -> list[str]:
    """Gait tag. The clip's own words win (names like 'UE5 Walk Fwd', Kimodo prompts): many motions are recorded in place, so root
    speed alone calls them idle. Measured otherwise - and only called walk / run when the feet actually lift (a sword swing with a
    shuffle travels a little but is not a walk)."""
    words = f"{c.get('name') or ''} {c.get('caption') or ''}"
    for g, rx in _TEXT_GAIT:
        if rx.search(words):
            return (["trot", "run"] if quad else ["run"]) if g == "run" else [g]
    cf = st.get("contact_fraction")
    lifts = cf is not None and cf < 0.95
    cyclic = (st.get("cycle_hz") or 0) >= 0.5
    if hps < 0.08:
        return ["walk"] if lifts and cyclic else ["idle"] if cf is None or cf >= 0.95 else []
    if cf is not None and not lifts:
        return []
    if quad:
        return ["walk"] if hps < 0.9 else ["trot", "run"]
    return ["walk"] if hps < 1.0 else ["run"]


def _gait_tags(c: dict, rig: dict | None) -> list[str]:
    st = c.get("stats", {})
    hps = st.get("root_speed_heights_per_s", 0.0) or 0.0
    quad = (rig or {}).get("kind") == "quadruped" or "paw" in json.dumps(c.get("source", {})).lower() or c.get("skeleton") == "ap10k17"
    tags = ["quadruped" if quad else "biped" if (rig or c.get("skeleton") in ("smpl24", "coco17")) else "skeleton"]
    tags += _gait(c, st, hps, quad)
    if c.get("loop") or (st.get("loop_seam_vs_step", 9) or 9) < 2.0:
        tags.append("loop")
    tags.append("root-motion" if c.get("root_motion") else "in-place")
    meta = c.get("source", {})
    if meta.get("format") == "synthetic" or "synthetic" in json.dumps(meta.get("meta", {})):
        tags.append("synthetic")
    if rig:
        tags.append(rig["name"].lower().replace(" ", "-"))
    return tags


def heuristic_annotate(cid: str) -> dict:
    c = db.get_clip(cid)
    if not c:
        raise RuntimeError("clip not found")
    rig = db.get_rig(c["rig_id"]) if c["rig_id"] else None
    st = c.get("stats", {})
    base = _gait_tags(c, rig)
    # measured tags replace inherited ones: a retargeted clip copies its source motion's tags, and the source can be measured as
    # "idle" (static root joint) or "in-place" while the retargeted clip walks with root motion or is not a loop any more
    measured = {"idle", "walk", "trot", "run", "loop", "in-place", "root-motion"}
    tags = list(dict.fromkeys([*(t for t in c["tags"] if t not in measured), *base]))
    gait = next((t for t in base if t in ("idle", "walk", "trot", "run")), "motion")
    who = f"on {rig['name']}" if rig else f"({c['skeleton']} skeleton)"
    sp = st.get("root_speed_mps")
    cap = f"{gait.capitalize()} cycle {who}: {c['duration']:.1f}s at {c['fps']:.0f} fps"
    if sp:
        cap += f", about {sp:.2f} m/s"
    cap += ", loops cleanly" if "loop" in base else ""
    cap += ", in place." if "in-place" in base else ", with root motion."
    db.update_clip(cid, tags=tags, caption=c["caption"] or cap)
    return db.get_clip(cid)  # type: ignore[return-value]


def _frames_from_video(path: str, n: int = 3, width: int = 384) -> list[bytes]:
    out = []
    for i in range(n):
        pos = (i + 0.5) / n
        try:
            from ...ffutil import probe
            dur = probe(path)["duration"] or 1.0
            r = subprocess.run([config.FFMPEG, "-v", "error", "-ss", f"{dur * pos:.2f}", "-i", path, "-frames:v", "1", "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                               capture_output=True, **_NOWIN)
            if r.stdout:
                out.append(r.stdout)
        except Exception:  # noqa: BLE001
            pass
    return out


def _json_from(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("model reply had no JSON")
    return json.loads(m.group(0))


def annotate(ctx: JobContext, clip_id: str, use_cloud: bool = False) -> dict:
    c = heuristic_annotate(clip_id)
    ctx.progress(0.2, "asking the language model")
    rig = db.get_rig(c["rig_id"]) if c["rig_id"] else None
    meta = {"name": c["name"], "skeleton": c["skeleton"], "rig": rig["name"] if rig else None, "rig_kind": rig["kind"] if rig else None, "duration_s": round(c["duration"], 2),
            "fps": c["fps"], "root_motion": c["root_motion"], "loop": c["loop"],
            "stats": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in c["stats"].items() if k in ("root_speed_mps", "root_speed_heights_per_s", "cycle_hz", "foot_slide_pct_height_per_s", "score")},
            "existing_tags": c["tags"]}
    prompt = ("Describe this animation clip for a searchable game-animation library.\nMetadata: " + json.dumps(meta) +
              "\nReturn ONE JSON object: {\"tags\": [5-10 short lowercase tags: gait, species/body type, mood, usage...], \"caption\": \"one sentence\"}.")
    content: list[dict] | str = prompt
    provider = "anthropic" if use_cloud else "ollama"
    note = ""
    if use_cloud:
        files = c["_files"]
        vid = files.get("mp4") or files.get("webm")
        frames = _frames_from_video(vid, 3) if vid else []
        if frames:
            content = [{"type": "text", "text": prompt}] + [{"type": "image", "media_type": "image/jpeg", "data": base64.b64encode(f).decode()} for f in frames]
            note = "3 preview frames were sent to the cloud model."
    used = {"used": False, "provider": provider}
    try:
        r = llm.chat([{"role": "user", "content": content}], system="You are a precise animation librarian. Output JSON only.", provider_override=provider, max_tokens=600, temperature=0.2)
        j = _json_from(r["text"])
        tags = [str(t).lower().strip()[:32] for t in j.get("tags", []) if str(t).strip()][:12]
        cap = str(j.get("caption", "")).strip()[:300]
        merged = list(dict.fromkeys([*c["tags"], *tags]))
        db.update_clip(clip_id, tags=merged, caption=cap or c["caption"])
        used = {"used": True, "provider": r.get("provider"), "model": r.get("model"), "note": note}
    except Exception as e:  # noqa: BLE001
        used["error"] = str(e)[:240]
    search.ensure_embedding(clip_id)
    out = db.get_clip(clip_id)
    return {"clip": {k: v for k, v in out.items() if not k.startswith("_")}, "llm": used}  # type: ignore[union-attr]


# ------------------------------------------------------------------------------------------------ review
def _sheet(images: list[Image.Image], cols: int = 4) -> Image.Image:
    w, h = images[0].size
    rows = (len(images) + cols - 1) // cols
    s = Image.new("RGB", (w * cols, h * rows), (32, 35, 43))
    for i, im in enumerate(images):
        s.paste(im, ((i % cols) * w, (i // cols) * h))
    return s


def _jpeg(im: Image.Image, q: int = 82) -> bytes:
    b = io.BytesIO()
    im.convert("RGB").save(b, "JPEG", quality=q)
    return b.getvalue()


def review(ctx: JobContext, clip_id: str, mode: str = "host", n_frames: int = 8, use_cloud: bool = False) -> dict:
    if use_cloud and mode == "host":
        mode = "cloud"
    c = db.get_clip(clip_id)
    if not c:
        raise RuntimeError("clip not found")
    if not c["rig_id"]:
        raise RuntimeError("Review needs a retargeted clip (source motions have no character to look at).")
    rig = db.get_rig(c["rig_id"], True)
    ctx.progress(0.05, "measuring")
    m = metrics.compute_from_npz(c["_files"]["bake"])
    db.update_clip(clip_id, stats={**c["stats"], **m})
    T = c["frames"]
    n_frames = max(2, min(int(n_frames), 12))
    idx = [int(round(i * (T - 1) / n_frames)) for i in range(n_frames)]
    frames, images = [], []
    odir = db.PREVIEWS_DIR / clip_id / "review"
    shutil.rmtree(odir, ignore_errors=True)
    for k, (cam, sel, span) in enumerate((("side", idx, (0.1, 0.5)), ("three_quarter", idx[:: max(1, n_frames // 4)], (0.5, 0.9)))):
        ctx.progress(span[0], f"rendering {cam} frames")
        res = blender.run_script("render_preview.py", {"rig_blend": rig["file"], "bake": c["_files"]["bake"], "out_dir": str(odir / cam), "prefix": "s", "camera": cam, "size": [320, 240],
                                                        "transparent": False, "bg": "#303540", "frames_list": sel, "in_place": not c["root_motion"],
                                                        "armature": rig.get("info", {}).get("armature")}, ctx, span)
        ims = [Image.open(f).convert("RGB") for f in res["files"]]
        for i, im in zip(sel, ims):
            frames.append({"camera": cam, "frame": i, "time_s": round(i / c["fps"], 3), "mime": "image/jpeg", "data_base64": base64.b64encode(_jpeg(im)).decode()})
        images.append(_sheet(ims, 4))
    out = {"mode": mode, "clipId": clip_id, "clip": c["name"], "rig": rig["name"], "metrics": {k: v for k, v in m.items() if k not in ("issues",)},
           "score": m.get("score"), "rubric": metrics.RUBRIC, "frames": frames, "sheet": {"mime": "image/jpeg", "data_base64": base64.b64encode(_jpeg(images[0])).decode()},
           "issues": m.get("issues", []), "suggestions": [i["suggestion"] for i in m.get("issues", [])], "llm": {"used": False}}
    if mode == "host":
        out["instructions"] = ("No model was called. Judge the frames yourself against `rubric` (side view first, then three-quarter), use `metrics` as ground truth for "
                               "foot sliding / ground penetration / jitter / loop seam, then report issues and suggested option changes for retarget.")
        return out
    ctx.progress(0.92, f"asking the {mode} model")
    provider = "anthropic" if mode == "cloud" else "ollama"
    prompt = ("You are reviewing a retargeted game animation. The images are consecutive frames of one clip (side view, then three-quarter view). "
              "Measured metrics: " + json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in out["metrics"].items() if not isinstance(v, (list, dict))}) +
              "\nRubric:\n- " + "\n- ".join(metrics.RUBRIC) +
              "\nReturn ONE JSON object: {\"verdict\": \"good|usable|bad\", \"score\": 0-10, \"issues\": [{\"severity\": \"low|medium|high\", \"message\": \"...\", \"frames\": [..], \"fix\": \"which retarget option or bone-map change would help\"}], \"suggestions\": [\"...\"]}.")
    content = [{"type": "text", "text": prompt}] + [{"type": "image", "media_type": "image/jpeg", "data": f["data_base64"]} for f in frames[:10]]
    try:
        r = llm.chat([{"role": "user", "content": content}], system="You are a strict technical animator. Output JSON only.", provider_override=provider, max_tokens=1200, temperature=0.1)
        j = _json_from(r["text"])
        out["llm"] = {"used": True, "provider": r.get("provider"), "model": r.get("model"), "verdict": j.get("verdict"), "score": j.get("score")}
        out["issues"] = [*out["issues"], *[{"severity": i.get("severity", "medium"), "code": "llm", "message": i.get("message", ""), "suggestion": i.get("fix", ""), "frames": i.get("frames")} for i in j.get("issues", []) if isinstance(i, dict)]]
        out["suggestions"] = list(dict.fromkeys([*out["suggestions"], *[str(s) for s in j.get("suggestions", [])]]))
    except Exception as e:  # noqa: BLE001
        out["llm"] = {"used": False, "provider": provider, "error": str(e)[:300],
                      "hint": "Local review needs a vision-capable Ollama model; use mode='host' to let an external model judge the frames." if mode == "local" else ""}
    return out
