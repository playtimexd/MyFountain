"""Turn a connector candidate (or a local file) into a hub record: download -> hash/dedupe -> metadata ->
thumbnail -> record -> media-library registration (images/audio/video) -> auto-tags + embeddings."""
from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Callable

from . import ai, db, licenses, meta, net, paths, thumbs
from .connectors import REGISTRY, ConnectorError, DLContext
from .connectors import get as get_connector

MEDIA_KINDS = {"image", "sprite", "texture", "sfx", "music", "voice", "video"}
_ingest_lock = threading.Lock()


class Skipped(Exception):
    """Candidate intentionally not added (licence policy, duplicate, manual-only…)."""


def _ingest_media(rec: dict, path: str) -> str | None:
    try:
        from ... import ingest
        with _ingest_lock:
            row = ingest.ingest_file(path, origin="hub", source_url=rec.get("source_url"), name=rec["name"],
                                     tags=["hub", rec["source"], rec["kind"], *rec.get("tags", [])[:8]],
                                     meta={"hub_id": rec["id"], "license": rec["license"], "license_url": rec.get("license_url"),
                                           "attribution": rec.get("attribution"), "author": rec.get("author"), "source": rec["source"]})
        return row["id"]
    except Exception:
        return None


def enrich(aid: str, *, use_clip: bool = True, use_text: bool = True) -> dict:
    """Zero-shot CLIP tags + CLIP image embedding (from the thumbnail) and text embedding (name/description/tags)."""
    rec = db.get(aid)
    if not rec:
        return {}
    upd: dict = {}
    thumb = db.file_path(aid, "thumb")
    if use_clip and thumb and Path(thumb).exists():
        v = ai.clip_image(thumb)
        if v is not None:
            upd["clip"] = v
            auto = ai.zero_shot_tags(v)
            if auto:
                st = dict(rec.get("stats") or {})
                st["auto_tags"] = auto
                upd["stats"] = st
                upd["tags"] = list(dict.fromkeys(rec["tags"] + auto))
    if use_text:
        text = f"{rec['kind']}: {rec['name']}. {rec.get('description') or ''} Tags: {', '.join(upd.get('tags') or rec['tags'])}"
        tv = ai.text_embed([text])
        if tv is not None:
            upd["embedding"] = tv[0]
    if upd:
        db.update_fields(aid, **upd)
    return {k: True for k in upd}


def _make_thumb(kind: str, name: str, tb: Path, files: list[tuple[Path, str, str | None]], preview: Path | None) -> Path | None:
    tb.parent.mkdir(parents=True, exist_ok=True)
    if preview and preview.exists():
        return thumbs.image_thumb(preview, tb) or preview
    main = next((p for p, r, _ in files if r == "main"), None)
    albedo = next((p for p, r, _ in files if r == "map:albedo"), None)
    if albedo:
        return thumbs.image_thumb(albedo, tb)
    if main is None:
        return None
    e = main.suffix.lower()
    if e in meta.IMAGE_EXT:
        return thumbs.image_thumb(main, tb)
    if e in meta.FONT_EXT and e in (".ttf", ".otf"):
        return thumbs.font_preview(main, tb, name)
    if e in meta.AUDIO_EXT:
        return thumbs.audio_wave(main, tb)
    return None   # GLB/FBX/BVH: no renderer here (Blender is owned by anim-core)


def finalize(base: dict, dest: Path, files: list[tuple[Path, str, str | None]], *, preview: Path | None = None,
             extra_stats: dict | None = None, register_media: bool = True, do_enrich: bool = True,
             dedupe_sha: bool = True, thumb_base: Path | None = None) -> dict:
    """Common tail: entries + sha dedupe + stats + thumb + upsert + media registration + enrichment.
    Returns {status:'added'|'duplicate', asset}."""
    entries = []
    for p, role, res in files:
        if p.exists():
            entries.append(meta.file_entry(p, role, res))
    main = next((e for e in entries if e["role"] == "main"), None)
    if not main:
        raise ConnectorError("No main file was produced")
    if dedupe_sha:
        dup = db.find_by_sha(main["sha256"])
        if dup and not (dup["source"] == base["source"] and dup["source_id"] == str(base["source_id"])):
            return {"status": "duplicate", "asset": dup}
    stats = {**(base.get("stats") or {}), **(extra_stats or {}), **meta.extract(main["path"])}
    stats = {k: v for k, v in stats.items() if v not in (None, "", [])} | ({"clips": stats["clips"]} if stats.get("clips") else {})
    kind = base["kind"]
    if kind == "model" and stats.get("clips") and not stats.get("meshes") and main["format"] in ("glb", "gltf"):
        kind = "animation"
    if preview and preview.exists():
        entries.append(meta.file_entry(preview, "preview"))
    thumb = _make_thumb(kind, base.get("name") or "", thumb_base or dest / "_thumb", files, preview)
    lic = base.get("license") or "custom:unknown"
    rec = {**base, "kind": kind, "stats": stats, "files": entries, "thumb": str(thumb) if thumb else None, "license": lic}
    if not rec.get("attribution"):
        src = REGISTRY.get(base["source"])
        rec["attribution"] = licenses.attribution(rec["name"], rec.get("author"), src.name if src else base["source"],
                                                  rec.get("source_url"), lic)
    saved = db.upsert(rec)
    if register_media and kind in MEDIA_KINDS | {"material"}:
        media_path = main["path"]
        if kind == "material":
            alb = next((e for e in entries if e["role"] == "map:albedo"), None)
            media_path = alb["path"] if alb else None
        if media_path and Path(media_path).suffix.lower() in (meta.IMAGE_EXT | meta.AUDIO_EXT | meta.VIDEO_EXT) - {".tga"}:
            mid = _ingest_media(saved, media_path)
            if mid:
                db.update_fields(saved["id"], media_id=mid)
    if do_enrich:
        try:
            enrich(saved["id"])
        except Exception:
            pass
    return {"status": "added", "asset": db.get(saved["id"])}


def add_candidate(cand: dict, *, variant: dict | None = None, policy: str | None = None, max_bytes: int | None = None,
                  check: Callable[[], None] = lambda: None, do_enrich: bool = True) -> dict:
    """Download a search candidate into the hub. Returns {status:'added'|'exists'|'duplicate', asset, bytes}."""
    con = get_connector(cand["source"])
    if not con.automated:
        raise Skipped(f"{con.name} packs must be downloaded manually - open the page, then use Import pack zip")
    if policy and not licenses.allowed(cand.get("license") or "", policy):
        raise Skipped(f"licence {cand.get('license')} is excluded by the '{policy}' policy")
    if not cand.get("downloadable", True):
        raise Skipped("not downloadable")
    existing = db.get_by_source(cand["source"], cand["source_id"])
    if existing:
        return {"status": "exists", "asset": existing, "bytes": 0}
    dest = paths.asset_dir(cand["source"], cand["source_id"])
    ctx = DLContext(variant=variant or {}, max_bytes=max_bytes, check=check)
    try:
        got = con.download(cand["source_id"], dest, ctx, cand)
        cand = {**cand, **{k: v for k, v in got.updates.items() if v}}
        preview = None
        if cand.get("preview_url"):
            try:
                ext = Path(cand["preview_url"].split("?")[0]).suffix.lower()
                if "format=webp" in cand["preview_url"]:
                    ext = ".webp"
                preview = ctx.fetch(cand["preview_url"], dest / f"preview{ext if ext in meta.IMAGE_EXT else '.jpg'}",
                                    min_interval=con.min_interval)
            except (net.CapExceeded, net.HubHTTPError):
                preview = None
        base = {k: cand.get(k) for k in ("source", "source_id", "kind", "name", "description", "tags", "source_url", "author",
                                         "author_url", "license", "stats")}
        base["attribution"] = con.attribution(cand)
        base["extra"] = {k: v for k, v in (cand.get("extra") or {}).items() if k not in ("downloads", "previews", "metadata")}
        res = finalize(base, dest, got.files, preview=preview, extra_stats=got.stats, do_enrich=do_enrich)
        if res["status"] == "duplicate":
            shutil.rmtree(dest, ignore_errors=True)
        res["bytes"] = ctx.used
        return res
    except BaseException:
        if not db.get_by_source(cand["source"], cand["source_id"]):
            shutil.rmtree(dest, ignore_errors=True)
        raise


def delete_asset(aid: str, *, delete_files: bool = True) -> dict | None:
    """Remove a record (and its media-library row). Files are deleted only inside data/hub/files."""
    thumb = db.file_path(aid, "thumb")
    rec = db.delete(aid)
    if not rec:
        return None
    if rec.get("media_id"):
        try:
            from ... import db as media_db
            media_db.delete_media(rec["media_id"])
        except Exception:
            pass
    if delete_files:
        hub_root = paths.FILES_DIR.resolve()
        targets = {Path(f["path"]).resolve() for f in rec.get("files") or []}
        if thumb:
            targets.add(Path(thumb).resolve())
        dirs = set()
        for p in targets:
            if hub_root in p.parents and p.exists():
                p.unlink(missing_ok=True)
                dirs.add(p.parent)
        for d in sorted(dirs, key=lambda x: -len(x.parts)):
            while hub_root in d.parents:
                try:
                    if any(d.iterdir()):
                        break
                    d.rmdir()
                except OSError:
                    break
                d = d.parent
    return rec
