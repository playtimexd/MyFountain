"""Manual imports: a local folder or zip (Kenney / Quaternius / Truebones / any pack) with a user-chosen licence
applied to every file, and Mixamo FBX folders (also handed to /api/anim/clips/import when that API exists)."""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path
from typing import Callable

import httpx

from ... import config
from . import licenses, meta, paths
from .connectors import REGISTRY
from .pipeline import finalize
from .ziputil import safe_extract

SKIP_DIRS = {"__macosx", ".git", "node_modules"}
SKIP_EXT = {".txt", ".md", ".url", ".html", ".htm", ".pdf", ".json", ".mtl", ".bin", ".ini", ".db", ".ds_store", ".meta",
            ".import", ".tres", ".tscn", ".unitypackage", ".zip", ".blend1"}


def pretty(stem: str) -> str:
    s = re.sub(r"[_\-]+", " ", stem).strip()
    return s[:1].upper() + s[1:] if s else stem


def _anim_import_available() -> bool:
    try:
        r = httpx.get(f"http://127.0.0.1:{config.PORT}/api/anim/clips", timeout=3)
        return r.status_code < 400
    except Exception:
        return False


def _anim_import(path: Path) -> str | None:
    try:
        r = httpx.post(f"http://127.0.0.1:{config.PORT}/api/anim/clips/import", json={"path": str(path)}, timeout=10)
        return (r.json() or {}).get("id") if r.status_code < 400 else None
    except Exception:
        return None


def import_pack(src: str, *, source: str = "local", license: str = "CC0-1.0", pack_name: str | None = None,
                author: str | None = None, author_url: str | None = None, source_url: str | None = None,
                kind_hint: str | None = None, tags: list[str] | None = None, to_anim: bool = False,
                progress: Callable[[float, str], None] = lambda p, m: None) -> dict:
    """Import every usable file under `src` (folder or .zip). Files are copied into data/hub/files/<source>/<pack>/."""
    sp = Path(src).expanduser()
    if not sp.exists():
        raise FileNotFoundError(f"Not found: {src}")
    if source not in REGISTRY:
        source = "local"
    lic = license if license in licenses.LICENSES or license.startswith("custom:") else licenses.from_text(license)
    pack = paths.safe_name(pack_name or sp.stem)
    dest = paths.asset_dir(source, pack).resolve()
    progress(0.02, "Copying files")
    if sp.is_file() and zipfile.is_zipfile(sp):
        files = safe_extract(sp, dest)
    elif sp.is_dir():
        files = []
        for p in sp.rglob("*"):
            if p.is_file() and not any(part.lower() in SKIP_DIRS for part in p.relative_to(sp).parts):
                t = dest / p.relative_to(sp)
                t.parent.mkdir(parents=True, exist_ok=True)
                if not t.exists():
                    shutil.copy2(p, t)
                files.append(t)
    elif sp.is_file():
        t = dest / sp.name
        shutil.copy2(sp, t)
        files = [t]
    else:
        raise ValueError("Give a folder, a .zip file or a single asset file")

    usable = [f for f in files if f.suffix.lower() not in SKIP_EXT and not f.name.startswith(".")]
    by_stem: dict[str, list[Path]] = {}
    for f in usable:
        by_stem.setdefault(f.stem.lower(), []).append(f)
    model_stems = {f.stem.lower() for f in usable if f.suffix.lower() in meta.MODEL_EXT | meta.ANIM_EXT}
    # images sharing a stem with a model are its preview, not separate sprites
    preview_for: dict[str, Path] = {}
    for f in usable:
        if f.suffix.lower() in meta.IMAGE_EXT and f.stem.lower() in model_stems:
            preview_for.setdefault(f.stem.lower(), f)
    # gltf companions (.bin + textures referenced by a .gltf) are part of that model
    companions: set[Path] = set()
    for f in usable:
        if f.suffix.lower() == ".gltf":
            j = meta.read_gltf_json(f) or {}
            for b in (j.get("buffers") or []) + (j.get("images") or []):
                if b.get("uri") and not b["uri"].startswith("data:"):
                    companions.add((f.parent / b["uri"]).resolve())
    # prefer GLB over other formats of the same model stem (others become 'lod'/alt files)
    added, dups, skipped, errors, anim_jobs = 0, 0, 0, [], 0
    want_anim = to_anim and _anim_import_available()
    used_alt: set[Path] = set()
    work = [f for f in usable if f not in preview_for.values() and f.resolve() not in companions]
    pref = {".glb": 0, ".gltf": 1, ".fbx": 2, ".obj": 3, ".blend": 4}
    for i, f in enumerate(sorted(work, key=lambda p: (str(p.parent), p.stem, pref.get(p.suffix.lower(), 9)))):
        progress(0.05 + 0.9 * i / max(1, len(work)), f"{i + 1}/{len(work)} {f.name}")
        if f in used_alt:
            continue
        kind = meta.kind_for_file(f, kind_hint)
        if not kind:
            skipped += 1
            continue
        if kind in ("model", "animation") and source == "mixamo" and f.suffix.lower() == ".fbx":
            kind = "animation"
        rel = f.relative_to(dest).as_posix()
        alts = []
        if f.suffix.lower() in meta.MODEL_EXT:
            for o in by_stem.get(f.stem.lower(), []):
                if o != f and o.suffix.lower() in meta.MODEL_EXT and o.parent == f.parent and o not in preview_for.values():
                    alts.append((o, f"alt:{o.suffix.lower().lstrip('.')}", None))
                    used_alt.add(o)
        folder_tags = [t.lower() for t in Path(rel).parts[:-1] if len(t) < 30]
        base = {"source": source, "source_id": f"{pack}/{rel}", "kind": kind, "name": pretty(f.stem),
                "description": f"From pack '{pack_name or pack}'" + (f" by {author}" if author else ""),
                "tags": list(dict.fromkeys([*(tags or []), pack.lower(), *folder_tags])), "source_url": source_url,
                "author": author, "author_url": author_url, "license": lic,
                "extra": {"pack": pack_name or pack, "relpath": rel}}
        con = REGISTRY.get(source)
        base["attribution"] = licenses.attribution(base["name"], author, (con.name if con and source != "local" else (pack_name or pack)),
                                                   source_url, lic)
        try:
            tb = dest / ".thumbs" / paths.safe_name(rel, 150)
            res = finalize(base, dest, [(f, "main", None), *alts], preview=preview_for.get(f.stem.lower()), thumb_base=tb)
            if res["status"] == "duplicate":
                dups += 1
                continue
            added += 1
            if want_anim and kind == "animation" and f.suffix.lower() in (".fbx", ".bvh", ".glb"):
                if _anim_import(f):
                    anim_jobs += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"{f.name}: {paths.redact(str(e))[:200]}")
    progress(1.0, f"Imported {added}")
    return {"pack": pack_name or pack, "source": source, "license": lic, "added": added, "duplicates": dups, "skipped": skipped,
            "errors": errors[:50], "files_seen": len(files), "anim_import": {"available": want_anim, "submitted": anim_jobs}}
