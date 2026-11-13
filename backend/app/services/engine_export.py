"""Export CutLab assets straight into a game-engine project, in the format and folder each engine imports by itself.

engine   files written                                        where                                       how it gets imported
godot    .glb (+ .anim.json / fur.json)                       <project>/assets/cutlab/<name>/             Godot imports glTF on focus
unity    .fbx (+ .anim.json / fur.json), media as-is          <project>/Assets/CutLab/<name>/             Unity imports on focus
unreal   .fbx + import_to_unreal.py                           <project>/SourceArt/CutLab/<name>/          run the script in Unreal (Tools > Execute
                                                                                                          Python Script, or an Unreal MCP) -> /Game/CutLab/<name>
threejs  .glb (+ .anim.json / fur.json [+ shellFur.ts])       <project>/public/assets/cutlab/<name>/      load with GLTFLoader
folder   .glb + .fbx                                          <folder>/<name>/

Never overwrites: an existing <name> folder gets a numbered sibling unless overwrite=True. Nothing is written outside the project.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

from .. import config, db as media_db
from ..jobs import JobContext

ENGINES = ("godot", "unity", "unreal", "threejs", "folder")
SHELL_FUR_TS = Path(__file__).resolve().parents[3] / "frontend" / "src" / "modules" / "anim" / "fur" / "shellFur.ts"
_SKIP_DIRS = re.compile(r"^(node_modules|\.git|dist.*|build|Binaries|Intermediate|Saved|DerivedDataCache|Library|Temp|Logs|obj|\.godot|\.import|"
                        r"worktrees|\.venv|venv|__pycache__|\$RECYCLE\.BIN|System Volume Information)$", re.I)


# ---------------------------------------------------------------------------------------------- project detection
def detect_engine(path: str | Path) -> dict | None:
    p = Path(path)
    if not p.is_dir():
        return None
    if (p / "project.godot").is_file():
        name, ver = p.name, ""
        try:
            t = (p / "project.godot").read_text("utf-8", errors="replace")
            m = re.search(r'config/name="([^"]+)"', t); name = m.group(1) if m else name
            m = re.search(r'config/features=PackedStringArray\("([\d.]+)"', t); ver = m.group(1) if m else ""
        except OSError:
            pass
        return {"engine": "godot", "path": str(p), "name": name, "version": ver}
    up = next(iter(p.glob("*.uproject")), None)
    if up:
        ver = ""
        try:
            ver = json.loads(up.read_text("utf-8", errors="replace")).get("EngineAssociation", "")
        except (OSError, ValueError):
            pass
        return {"engine": "unreal", "path": str(p), "name": up.stem, "version": ver}
    pv = p / "ProjectSettings" / "ProjectVersion.txt"
    if pv.is_file() and (p / "Assets").is_dir():
        m = re.search(r"m_EditorVersion:\s*(\S+)", pv.read_text("utf-8", errors="replace"))
        return {"engine": "unity", "path": str(p), "name": p.name, "version": m.group(1) if m else ""}
    pk = p / "package.json"
    if pk.is_file():
        try:
            j = json.loads(pk.read_text("utf-8", errors="replace"))
            deps = {**(j.get("dependencies") or {}), **(j.get("devDependencies") or {})}
            if "three" in deps:
                return {"engine": "threejs", "path": str(p), "name": j.get("name") or p.name, "version": deps["three"]}
        except (OSError, ValueError):
            pass
    return None


_cache: dict = {"t": 0.0, "items": []}


def find_projects(roots: list[str] | None = None, max_depth: int = 4, refresh: bool = False) -> list[dict]:
    if not refresh and roots is None and time.time() - _cache["t"] < 600:
        return _cache["items"]
    roots = roots or [r for r in ("D:\\", str(Path.home() / "Documents"), str(Path.home() / "Desktop")) if Path(r).exists()]
    out, seen = [], set()

    data_dir = str(config.DATA).lower()

    def walk(d: Path, depth: int, skip_data: bool = True):
        try:
            info = detect_engine(d)
        except OSError:
            info = None
        if info and info["path"] not in seen:
            seen.add(info["path"]); out.append(info)
            if info["engine"] != "threejs":
                return                                            # engine projects do not nest
        if depth >= max_depth:
            return
        try:
            for c in sorted(d.iterdir()):
                if c.is_dir() and not _SKIP_DIRS.match(c.name) and not (skip_data and str(c).lower().startswith(data_dir)):
                    walk(c, depth + 1, skip_data)
        except OSError:
            pass

    for r in roots:
        walk(Path(r), 0, not str(Path(r)).lower().startswith(data_dir))
    out.sort(key=lambda x: (x["engine"], x["path"].lower()))
    if roots is None or len(roots) > 1:
        _cache.update(t=time.time(), items=out)
    return out


# ---------------------------------------------------------------------------------------------- helpers
def slug(s: str, default: str = "asset") -> str:
    s = re.sub(r"[^A-Za-z0-9_]+", "_", s).strip("_")
    return (s[:48] or default)


def _dest_dir(engine: str, project: Path, name: str, overwrite: bool) -> tuple[Path, str]:
    if engine == "godot":
        base = project / "assets" / "cutlab"
    elif engine == "unity":
        base = project / "Assets" / "CutLab"
    elif engine == "unreal":
        base = project / "SourceArt" / "CutLab"
    elif engine == "threejs":
        base = (project / "public" / "assets" / "cutlab") if (project / "public").is_dir() else (project / "assets" / "cutlab")
    else:
        base = project
    d = base / name
    if d.exists() and not overwrite:
        i = 2
        while (base / f"{name}_{i}").exists():
            i += 1
        d, name = base / f"{name}_{i}", f"{name}_{i}"
    d.mkdir(parents=True, exist_ok=True)
    return d, name


def _copy(src: str | Path, dst_dir: Path, name: str | None = None) -> Path:
    dst = dst_dir / (name or Path(src).name)
    shutil.copy2(src, dst)
    return dst


def _convert(ctx: JobContext, src: str, out: Path, engine: str, lo: float, hi: float) -> Path:
    from .anim import blender
    blender.run_script("convert_model.py", {"input": src, "output": str(out), "target": engine}, ctx, (lo, hi), timeout=1800)
    if not out.exists():
        raise RuntimeError(f"conversion to {out.suffix} failed")
    return out


def _unreal_script(dest: Path, name: str, files: list[Path]) -> Path:
    """Unreal Editor Python: import the written files into /Game/CutLab/<name> (skeletal mesh + skeleton + anim sequences for
    characters, static meshes for props, textures and sounds)."""
    rel = [f.name for f in files if f.suffix.lower() in (".fbx", ".glb", ".gltf", ".png", ".jpg", ".jpeg", ".tga", ".wav", ".exr", ".hdr")]
    script = f'''# Generated by CutLab - imports "{name}" into /Game/CutLab/{name}
# Run in the Unreal Editor: Tools > Execute Python Script... (Python Editor Script Plugin enabled), or from an Unreal MCP.
import json
import os
import unreal

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = "/Game/CutLab/{name}"
FILES = {json.dumps(rel)}
META = {{}}
_meta_path = os.path.join(HERE, "cutlab_export.json")
if os.path.exists(_meta_path):
    with open(_meta_path, encoding="utf-8") as f:
        META = json.load(f)

tasks = []
for fn in FILES:
    t = unreal.AssetImportTask()
    t.filename = os.path.join(HERE, fn)
    t.destination_path = DEST
    t.automated = True
    t.save = True
    t.replace_existing = True
    if fn.lower().endswith(".fbx"):
        skeletal = bool(META.get("skinned"))
        o = unreal.FbxImportUI()
        o.import_mesh = True
        o.import_materials = True
        o.import_textures = True
        o.import_as_skeletal = skeletal
        o.import_animations = bool(META.get("animations"))
        # enum member names differ between engine versions (FBXIT_* in UE5); look them up defensively
        kind = "SKELETAL_MESH" if skeletal else "STATIC_MESH"
        enum = getattr(unreal, "FBXImportType", None)
        val = next((getattr(enum, n) for n in ("FBXIT_" + kind, "FIT_" + kind, kind) if enum is not None and hasattr(enum, n)), None)
        if val is not None:
            o.set_editor_property("mesh_type_to_import", val)
        try:
            o.set_editor_property("automated_import_should_detect_type", True)
        except Exception:
            pass
        t.options = o
    tasks.append(t)

unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks(tasks)
done = [p for t in tasks for p in (t.imported_object_paths or [])]
unreal.log("CutLab: imported %d asset(s) into %s" % (len(done), DEST))
for p in done:
    unreal.log("  " + p)
'''
    p = dest / "import_to_unreal.py"
    p.write_text(script, "utf-8")
    return p


# ---------------------------------------------------------------------------------------------- export
def export(ctx: JobContext, asset: dict, engine: str, project: str, name: str | None = None, overwrite: bool = False) -> dict:
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {', '.join(ENGINES)}")
    proj = Path(project).expanduser()
    if engine == "folder":
        proj.mkdir(parents=True, exist_ok=True)
    else:
        info = detect_engine(proj)
        if not info or info["engine"] != engine:
            raise ValueError(f"{proj} is not a {engine} project" + (f" (it looks like {info['engine']})" if info else "") + ".")
    typ = asset.get("type")
    fmt_model = "fbx" if engine in ("unity", "unreal") else "glb"
    written: list[Path] = []
    meta: dict = {"schema": "cutlab.export/1", "engine": engine, "asset": asset, "exported": time.time()}
    notes: list[str] = []

    if typ == "character":
        from .anim import db as anim_db
        from .anim import library
        rig = anim_db.get_rig(asset.get("rig_id") or "")
        if not rig:
            raise ValueError("character (rig) not found")
        nm = slug(name or rig["name"])
        dest, nm = _dest_dir(engine, proj, nm, overwrite)
        target = {"threejs": "threejs", "godot": "godot", "unity": "unity", "unreal": "unreal"}.get(engine, "generic")
        fmts = ["glb", "fbx"] if engine == "folder" else [fmt_model]
        for i, f in enumerate(fmts):
            ctx.progress(0.05 + 0.4 * i, f"Exporting {f.upper()} in Blender")
            res = library.export(ctx, None, f, {"preset": "game", "target": target}, asset.get("clip_ids") or None, rig["id"])
            written.append(_copy(res["file"], dest, f"{nm}.{f}"))
            first = (res.get("exports") or [{}])[0]
            if first.get("sidecar") and not (dest / f"{nm}.anim.json").exists():
                written.append(_copy(first["sidecar"], dest, f"{nm}.anim.json"))
            meta.update(clips=[c.get("name") for c in res.get("clips") or []], verify=res.get("verify"))
        meta.update(skinned=True, animations=bool(meta.get("clips")))
    elif typ == "fur":
        from .anim import fur
        g = fur.get(asset.get("groom_id") or "")
        if not g:
            raise ValueError("fur groom not found")
        nm = slug(name or g["name"])
        dest, nm = _dest_dir(engine, proj, nm, overwrite)
        glb = g["files"]["glb"]
        if engine in ("unity", "unreal"):
            written.append(_convert(ctx, glb, dest / f"{nm}.fbx", engine, 0.05, 0.85))
            notes.append("Shell fur needs a shader in this engine: fur.json has the settings; the per-vertex fur fields are only in the GLB.")
            written.append(_copy(glb, dest, f"{nm}_fur_fields.glb.bytes" if engine == "unity" else f"{nm}_fur_fields.glb"))
        else:
            written.append(_copy(glb, dest, f"{nm}.glb"))
        written.append(_copy(Path(glb).parent / "fur.json", dest, f"{nm}.fur.json"))
        if engine == "threejs" and SHELL_FUR_TS.exists():
            src_dir = proj / "src" / "cutlab"
            if (proj / "src").is_dir():
                src_dir.mkdir(parents=True, exist_ok=True)
                tgt = src_dir / "shellFur.ts"
                if not tgt.exists() or overwrite:
                    written.append(_copy(SHELL_FUR_TS, src_dir, "shellFur.ts"))
                notes.append("import { addShellFur } from './cutlab/shellFur'; addShellFur(gltf.scene, furJson.shell)")
        meta.update(skinned=True, animations=bool((g.get("source") or {}).get("animated")))
    elif typ == "model":
        ref = asset.get("ref") or ""
        src, nm0, lic = _model_source(ref)
        nm = slug(name or nm0)
        dest, nm = _dest_dir(engine, proj, nm, overwrite)
        ext = Path(src).suffix.lower()
        want = [".glb", ".fbx"] if engine == "folder" else [f".{fmt_model}"]
        for i, w in enumerate(want):
            if ext == w or (w == ".glb" and ext == ".gltf" and engine == "folder"):
                written.append(_copy(src, dest, f"{nm}{ext}"))
            else:
                written.append(_convert(ctx, src, dest / f"{nm}{w}", engine, 0.05 + 0.4 * i, 0.45 + 0.4 * i))
        info = _model_info(src)
        meta.update(skinned=info.get("has_skin", False), animations=bool(info.get("clips")), clips=info.get("clips"))
        if lic:
            meta["license"] = lic
            (dest / "CREDITS.txt").write_text(lic.get("attribution") or f"{nm0} - {lic.get('license')}", "utf-8")
            written.append(dest / "CREDITS.txt")
    elif typ == "media":
        m = media_db.get_media(asset.get("media_id") or "")
        if not m:
            raise ValueError("media not found")
        nm = slug(name or m["name"])
        dest, nm = _dest_dir(engine, proj, nm, overwrite)
        written.append(_copy(m["path"], dest, f"{nm}{Path(m['path']).suffix.lower()}"))
        if engine == "unreal" and Path(m["path"]).suffix.lower() in (".mp4", ".mov", ".webm"):
            notes.append("Unreal plays video through a File Media Source: point one at this file (it is not imported as an asset).")
        meta.update(skinned=False, animations=False, kind=m["kind"])
    else:
        raise ValueError("asset.type must be character, fur, model or media")

    meta["files"] = [p.name for p in written]
    (dest / "cutlab_export.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False, default=str), "utf-8")
    script = None
    if engine == "unreal":
        script = _unreal_script(dest, nm, written)
        notes.insert(0, f"In Unreal: Tools > Execute Python Script... > {script} (imports into /Game/CutLab/{nm}).")
    elif engine == "godot":
        rel = dest.relative_to(proj).as_posix()
        notes.insert(0, f"Godot imports it when the editor regains focus: res://{rel}/")
    elif engine == "unity":
        notes.insert(0, "Unity imports it when the editor regains focus: Assets/" + dest.relative_to(proj / 'Assets').as_posix()
                     + ("  (for Mecanim retargeting set Rig > Animation Type = Humanoid)" if meta.get("skinned") else ""))
    elif engine == "threejs":
        rel = dest.relative_to(proj).as_posix()
        url = "/" + rel[len("public/"):] if rel.startswith("public/") else "/" + rel
        notes.insert(0, f"new GLTFLoader().load('{url}/{nm}.glb', ...)")
    ctx.progress(1.0, "Exported")
    return {"engine": engine, "project": str(proj), "folder": str(dest), "name": nm, "files": [str(p) for p in written],
            "unreal_script": str(script) if script else None, "unreal_content_path": f"/Game/CutLab/{nm}" if engine == "unreal" else None,
            "notes": notes, "meta": meta}


def _model_source(ref: str) -> tuple[str, str, dict | None]:
    kind, _, key = ref.partition(":")
    if kind == "hub":
        from .hub import db as hubdb
        p = hubdb.file_path(key, "main")
        a = hubdb.get(key) or {}
        if not p:
            raise ValueError("Asset Hub model not found")
        return p, a.get("name") or key, {"license": a.get("license"), "attribution": a.get("attribution"), "source_url": a.get("source_url")}
    if kind == "file":
        from .providers.gen3d import INCOMING
        p = INCOMING / key
        if not p.is_file() or p.parent != INCOMING:
            raise ValueError("model file not found")
        return str(p), p.stem, None
    if kind == "local":
        from . import asset_index
        a = asset_index.get(key)
        if not a or not Path(a["path"]).is_file():
            raise ValueError("local file not found (re-index?)")
        return a["path"], a["name"], None
    if kind == "rig":
        from .anim import db as anim_db
        r = anim_db.get_rig(key)
        if not r:
            raise ValueError("character not found")
        src = anim_db.rig_dir(key) / "source.glb"
        return str(src if src.exists() else r["file"]), r["name"], None
    raise ValueError("model ref must be hub:<id>, file:<name>, local:<id> or rig:<id>")


def _model_info(path: str) -> dict:
    from . import asset_index
    try:
        return asset_index.inspect(Path(path))
    except Exception:  # noqa: BLE001
        return {}
