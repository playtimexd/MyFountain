"""Fur for animal characters: Blender strand groom (offline renders) + fur fields baked into a GLB for real-time shell fur.

Layout:  data/anim/fur/<id>/fur.json (schema cutlab.fur/1), <stem>_fur.glb, <stem>_fur_render.png, <stem>_groom.blend
The GLB carries per-vertex _FUR_LEN / _FUR_DIR / _FUR_DENS attributes; fur.json carries the shell-shader settings the game
engine should use (frontend/src/modules/anim/fur/shellFur.ts is the reference implementation).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ... import config, ingest
from ...jobs import JobContext
from . import blender, db

FUR_DIR = db.ANIM_DIR / "fur"
FUR_DIR.mkdir(parents=True, exist_ok=True)

GROOM_KEYS = ("length_m", "density_per_m2", "comb", "lift", "gravity", "frizz", "curl", "melanin", "redness", "seed")
SHELL_DEFAULTS: dict[str, Any] = {
    "shells": 24, "length": 1.0, "strands": 230, "thickness": 0.28, "comb": 1.1, "gravity": 0.3, "wind": 0.12, "inertia": 0.6,
    "rootTint": [0.32, 0.28, 0.26], "tipTint": [0.95, 0.86, 0.76], "occlusion": 0.5, "sheen": 0.5, "lodDistance": 4, "minShells": 6,
}
SHELL_LIMITS = {"shells": (1, 64), "length": (0.05, 5), "strands": (20, 2000), "thickness": (0.02, 0.3), "comb": (-2, 2), "gravity": (-2, 2),
                "wind": (0, 2), "inertia": (0, 4), "occlusion": (0, 1), "sheen": (0, 1), "lodDistance": (0, 100), "minShells": (1, 64)}


def sources() -> list[dict]:
    """Skinned GLBs that can be groomed: GLB exports that contain a mesh, and rigs imported from a GLB."""
    out = []
    for e in db.list_exports():
        if e["format"] != "glb" or not Path(e["file"]).exists():
            continue
        v = (e["info"] or {}).get("verify") or {}
        if v and not (v.get("meshes") or v.get("skinned_meshes")):
            continue                                                   # animation-only export
        rig = db.get_rig(e["rig_id"]) if e["rig_id"] else None
        clips = [c.get("name") for c in (e["info"] or {}).get("clips", [])]
        out.append({"id": f"export:{e['id']}", "name": f"{rig['name'] if rig else 'Export'} - {', '.join(clips) or 'rest pose'}",
                    "file": e["file"], "rig_id": e["rig_id"], "animated": bool(clips), "created": e["created"]})
    for r in db.list_rigs():
        src = Path(r.get("source_path") or "")
        cand = db.rig_dir(r["id"]) / "source.glb"
        f = cand if cand.exists() else (src if src.suffix.lower() == ".glb" and src.exists() else None)
        if f:
            out.append({"id": f"rig:{r['id']}", "name": f"{r['name']} - rig (no animation)", "file": str(f), "rig_id": r["id"],
                        "animated": False, "created": r.get("created") or 0})
    return out


def _source(sid: str) -> dict:
    s = next((x for x in sources() if x["id"] == sid), None)
    if not s:
        raise ValueError(f"Unknown or unsupported fur source {sid!r} (needs a GLB export with a mesh, or a rig imported from GLB).")
    return s


def _record(d: Path) -> dict | None:
    p = d / "fur.json"
    if not p.exists():
        return None
    try:
        rec = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    fid = d.name
    rec["id"] = fid
    rec["urls"] = {k: f"/api/anim/fur/{fid}/file/{Path(v).name}" for k, v in (rec.get("files") or {}).items() if v}
    rec["urls"]["json"] = f"/api/anim/fur/{fid}/file/fur.json"
    return rec


def list_grooms() -> list[dict]:
    recs = [r for r in (_record(d) for d in FUR_DIR.iterdir() if d.is_dir()) if r]
    return sorted(recs, key=lambda r: -float(r.get("created") or 0))


def get(fid: str) -> dict | None:
    d = FUR_DIR / fid
    return _record(d) if d.is_dir() and d.parent == FUR_DIR else None


def file_path(fid: str, name: str) -> Path | None:
    d = FUR_DIR / fid
    if not d.is_dir() or "/" in name or "\\" in name or name.startswith("."):
        return None
    p = d / name
    return p if p.exists() and p.parent == d else None


def clean_shell(p: dict) -> dict:
    out = dict(SHELL_DEFAULTS)
    for k, v in (p or {}).items():
        if k not in SHELL_DEFAULTS or v is None:
            continue
        if k in ("rootTint", "tipTint"):
            if isinstance(v, (list, tuple)) and len(v) == 3:
                out[k] = [max(0.0, min(3.0, float(x))) for x in v]
            continue
        lo, hi = SHELL_LIMITS[k]
        out[k] = max(lo, min(hi, float(v)))
        if k in ("shells", "minShells", "strands"):
            out[k] = int(round(out[k]))
    out["minShells"] = min(out["minShells"], out["shells"])
    return out


def delete(fid: str) -> bool:
    import shutil
    d = FUR_DIR / fid
    if not (d.is_dir() and d.parent == FUR_DIR and (d / "fur.json").exists()):
        return False
    shutil.rmtree(d, ignore_errors=True)
    return True


def save_shell(fid: str, shell: dict) -> dict:
    d = FUR_DIR / fid
    p = d / "fur.json"
    if not p.exists():
        raise FileNotFoundError(fid)
    rec = json.loads(p.read_text("utf-8"))
    rec["shell"] = clean_shell(shell)
    rec["updated"] = time.time()
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=1), "utf-8")
    return get(fid)  # type: ignore[return-value]


def groom(ctx: JobContext, source_id: str, params: dict | None = None, render: dict | None = None, shell: dict | None = None,
          name: str | None = None) -> dict:
    src = _source(source_id)
    fid = db.new_id()
    d = FUR_DIR / fid
    d.mkdir(parents=True, exist_ok=True)
    stem = Path(src["file"]).stem
    gp = {k: v for k, v in (params or {}).items() if k in GROOM_KEYS and v is not None}
    ropt = {"enabled": True, "size": [960, 600], "samples": 48, "camera": "three_quarter", **(render or {})}
    ctx.progress(0.02, "Starting Blender")
    res = blender.run_script("fur_groom.py", {"input": src["file"], "out_dir": str(d), "stem": stem, "params": gp, "render": ropt},
                             ctx, (0.04, 0.97), timeout=1800)
    rig = db.get_rig(src["rig_id"]) if src.get("rig_id") else None
    rec = {
        "schema": "cutlab.fur/1",
        "name": name or f"{(rig or {}).get('name') or stem} fur",
        "created": time.time(),
        "source": {"id": source_id, "file": src["file"], "name": src["name"], "animated": src["animated"]},
        "files": {"glb": res.get("glb"), "render": res.get("render"), "blend": res.get("blend")},
        "attributes": {
            "_FUR_LEN": "float, strand length in mesh units (three.js: _fur_len)",
            "_FUR_DIR": "vec3, comb direction in mesh space, glTF Y-up (three.js: _fur_dir)",
            "_FUR_DENS": "float 0..1, strand density (three.js: _fur_dens)",
        },
        "shell": clean_shell(shell or {}),
        "groom": {"params": res.get("params"), "hair_count": res.get("hair_count"), "base_length_m": res.get("base_length_m"),
                  "height_m": res.get("height_m"), "meshes": res.get("meshes"), "render_device": res.get("render_device")},
        "engine": "Shell fur: draw the mesh N extra times, offset along the skinned normal; see shellFur.ts (three.js reference).",
    }
    (d / "fur.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), "utf-8")
    ctx.progress(1.0, "Fur ready")
    return get(fid)  # type: ignore[return-value]


def render_video(ctx: JobContext, fid: str, opts: dict | None = None) -> dict:
    """Render the groom's animation with strand fur in Cycles and add it to the media library (origin='anim').
    opts: camera (three_quarter|side|front), size [w,h], samples, transparent (alpha WebM with a shadow catcher), max_frames."""
    import shutil
    from .library import _ffmpeg_png_seq, safe_name
    opts = opts or {}
    rec = get(fid)
    if not rec:
        raise RuntimeError("fur groom not found")
    blend = (rec.get("files") or {}).get("blend")
    if not blend or not Path(blend).exists():
        raise RuntimeError("This groom has no .blend file; groom the character again.")
    if not (rec.get("source") or {}).get("animated"):
        raise RuntimeError("This groom has no animation. Groom a GLB export that contains a clip.")
    camera = opts.get("camera", "three_quarter")
    transparent = bool(opts.get("transparent", False))
    size = opts.get("size") or [960, 540]
    d = FUR_DIR / fid
    seq = d / f"seq_{int(time.time())}"
    res = blender.run_script("fur_render.py", {"blend": blend, "out_dir": str(seq), "camera": camera, "size": size,
                                               "samples": int(opts.get("samples") or 32), "transparent": transparent,
                                               "max_frames": int(opts.get("max_frames") or 240)}, ctx, (0.02, 0.9), timeout=7200)
    fps, n = float(res["fps"]), int(res["frames"])
    w, h = res["size"]
    base = safe_name(rec["name"])
    ext = "webm" if transparent else "mp4"
    out = d / f"{base}_{camera}{'_alpha' if transparent else ''}.{ext}"
    ctx.progress(0.91, f"Encoding {ext}")
    _ffmpeg_png_seq(seq / "f_%04d.png", fps, out, ext, (w, h), "#20232b", ctx, n / fps)
    pngs = sorted(seq.glob("f_*.png"))
    poster = d / f"{out.stem}.jpg"
    if pngs:
        from PIL import Image
        im = Image.open(pngs[len(pngs) // 2]).convert("RGBA")
        bg = Image.new("RGBA", im.size, (32, 35, 43, 255))
        bg.alpha_composite(im)
        bg.convert("RGB").save(poster, quality=90)
    shutil.rmtree(seq, ignore_errors=True)
    ctx.progress(0.97, "Adding to the media library")
    row = ingest.ingest_file(out, origin="anim", name=f"{rec['name']} ({camera}{', alpha' if transparent else ''})",
                             tags=["anim", "fur", "cycles"],
                             meta={"fur": {"groomId": fid, "camera": camera, "fps": fps, "frames": n, "device": res.get("device")},
                                   "alpha": transparent})
    if poster.exists():
        try:
            shutil.copyfile(poster, config.THUMB_DIR / f"{row['id']}.jpg")
        except OSError:
            pass
    p = d / "fur.json"
    full = json.loads(p.read_text("utf-8"))
    vids = [v for v in full.get("videos", []) if v.get("file") != str(out)]
    vids.insert(0, {"file": str(out), "media_id": row["id"], "camera": camera, "transparent": transparent, "frames": n, "fps": fps,
                    "seconds": res.get("seconds"), "device": res.get("device"), "created": time.time()})
    full["videos"] = vids
    p.write_text(json.dumps(full, ensure_ascii=False, indent=1), "utf-8")
    return {"media": row, "file": str(out), "url": f"/api/anim/fur/{fid}/file/{out.name}", "frames": n, "fps": fps,
            "render_seconds": res.get("seconds"), "device": res.get("device"), "transparent": transparent}
