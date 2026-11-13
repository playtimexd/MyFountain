"""One-click starter packs for the 3D Model library: free characters and base meshes with clear licences, from official sources only.

Each pack downloads its files, (for .blend bundles) splits them into one GLB per model with Blender, files them in the Asset Hub with
licence + attribution, and registers rigged characters in the Animation rig library so they work in "Make animation" right away.
Sites that only allow interactive downloads (Kenney, Quaternius, Mixamo) are not automated: download those packs yourself and use
Asset Hub -> Import pack zip.
"""
from __future__ import annotations

import shutil
import uuid
import zipfile
from pathlib import Path

import httpx

from ...jobs import JobContext
from . import paths

UA = {"User-Agent": "CutLab/1.0 (local asset library; starter packs)"}
RAW_KHR = "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models"
RAW_THREE = "https://raw.githubusercontent.com/mrdoob/three.js/dev/examples/models/gltf"

PACKS: dict[str, dict] = {
    "blender-human-base-meshes": {
        "name": "Blender Human Base Meshes (white models)",
        "desc": "Untextured human base meshes from Blender Studio: realistic and stylised bodies, heads, hands and feet. "
                "Ideal blockout / sculpting starting points. Not rigged.",
        "license": "CC0-1.0", "author": "Blender Studio", "source_url": "https://www.blender.org/download/demo/asset-bundles/",
        "size_mb": 47, "rigged": False,
        "files": [{"url": "https://mirror.blender.org/demo/asset-bundles/human-base-meshes/human-base-meshes-bundle-v1.4.1.zip",
                   "name": "human-base-meshes-bundle-v1.4.1.zip", "blend_bundle": True}],
    },
    "robot-expressive": {
        "name": "RobotExpressive (rigged humanoid, 14 animations)",
        "desc": "Low-poly robot by Tomás Laulhé (Quaternius) with idle, walk, run, jump, dance, punch, wave... and facial morphs.",
        "license": "CC0-1.0", "author": "Tomás Laulhé (Quaternius); modifications by Don McCurdy",
        "source_url": "https://github.com/mrdoob/three.js/tree/dev/examples/models/gltf/RobotExpressive", "size_mb": 0.5, "rigged": True,
        "files": [{"url": f"{RAW_THREE}/RobotExpressive/RobotExpressive.glb", "name": "RobotExpressive.glb", "character": "humanoid"}],
    },
    "khronos-fox": {
        "name": "Fox (rigged quadruped: survey, walk, run)",
        "desc": "Model by PixelMannen (CC0), rigging and animation by tomkranis (CC BY 4.0), glTF by @AsoboStudio and @scurest (CC BY 4.0).",
        "license": "CC-BY-4.0", "author": "PixelMannen (model, CC0); tomkranis (rig/animation); AsoboStudio, scurest (glTF)",
        "source_url": "https://github.com/KhronosGroup/glTF-Sample-Assets/tree/main/Models/Fox", "size_mb": 0.2, "rigged": True,
        "files": [{"url": f"{RAW_KHR}/Fox/glTF-Binary/Fox.glb", "name": "Fox.glb", "character": "quadruped"}],
    },
    "khronos-cesium-figures": {
        "name": "Cesium rigged figures (walking man + test figure)",
        "desc": "CesiumMan (textured, walk cycle) and RiggedFigure (untextured, skinned) from the Khronos glTF sample assets.",
        "license": "CC-BY-4.0", "author": "Cesium", "source_url": "https://github.com/KhronosGroup/glTF-Sample-Assets", "size_mb": 0.6, "rigged": True,
        "files": [{"url": f"{RAW_KHR}/CesiumMan/glTF-Binary/CesiumMan.glb", "name": "CesiumMan.glb", "character": "humanoid"},
                  {"url": f"{RAW_KHR}/RiggedFigure/glTF-Binary/RiggedFigure.glb", "name": "RiggedFigure.glb", "character": "humanoid"}],
    },
}
from . import starter_unreal  # noqa: E402

PACKS["unreal-mannequins"] = starter_unreal.PACK
UNITY_NOTE = {"name": "Unity mannequin (Starter Assets - Third Person)", "license": "Unity Asset Store EULA",
              "url": "https://assetstore.unity.com/packages/essentials/starter-assets-thirdperson-updates-in-new-charactercontroller-pa-196526",
              "note": "Needs an Asset Store login and Unity, so it is not automated. Unity retargets any Humanoid rig: the free Robot, Cesium "
                      "figures or your Mixamo characters work the same way (Rig > Animation Type = Humanoid)."}
MANUAL = [UNITY_NOTE, 
    {"name": "Kenney - Animated Characters (1/2/3, Retro, Survivors), Mini Characters, Blocky Characters", "license": "CC0-1.0",
     "url": "https://kenney.nl/assets/category:3D", "note": "Rigged low-poly characters with animations (FBX/GLB). Download the zip, then Import pack zip."},
    {"name": "Quaternius - Ultimate Animated Character Pack, Animal Pack, Modular Characters", "license": "CC0-1.0",
     "url": "https://quaternius.com/", "note": "Hundreds of rigged + animated low-poly characters and animals. Download, then Import pack zip."},
    {"name": "Poly Pizza (thousands of low-poly CC0 / CC-BY models incl. Quaternius and Kenney)", "license": "CC0 / CC-BY",
     "url": "https://poly.pizza/", "note": "Add a free Poly Pizza API key in Asset Hub -> Sources & keys and search directly."},
    {"name": "Mixamo characters + animations", "license": "Adobe Mixamo terms (free for projects)", "url": "https://www.mixamo.com/",
     "note": "Download FBX; your local Mixamo files are already indexed in 3D Models -> On this PC."},
]


def catalog() -> dict:
    from . import db as hubdb
    out = []
    for pid, p in PACKS.items():
        installed = hubdb.conn().execute("SELECT COUNT(*) FROM assets WHERE tags LIKE ?", (f'%"pack:{pid}"%',)).fetchone()[0]
        item = {"id": pid, **{k: v for k, v in p.items() if k != "files"}, "files": len(p["files"]), "installed": installed}
        if p.get("local_unreal"):
            ue = starter_unreal.find_unreal()
            item["available"] = bool(ue)
            item["note"] = f"Uses {ue[0].parents[3].name}" if ue else "Needs Unreal Engine 5 installed"
        out.append(item)
    return {"packs": out, "manual": MANUAL}


def _download(url: str, dest: Path, ctx: JobContext, lo: float, hi: float) -> Path:
    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30.0, read=300.0), headers=UA) as cl, cl.stream("GET", url) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        got = 0
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                got += len(chunk)
                if total:
                    ctx.progress(lo + (hi - lo) * got / total, f"Downloading {dest.name}: {got / 1e6:.0f}/{total / 1e6:.0f} MB")
                ctx.check()
    return dest


def install(ctx: JobContext, pid: str) -> dict:
    from ..anim import blender, library
    from . import imports
    p = PACKS.get(pid)
    if not p:
        raise ValueError(f"unknown pack {pid}")
    tmp = paths.TMP_DIR / f"starter_{uuid.uuid4().hex[:8]}"
    stage = tmp / "stage"
    stage.mkdir(parents=True, exist_ok=True)
    rigs: list[dict] = []
    if p.get("local_unreal"):
        try:
            return starter_unreal.install(ctx, pid, tmp, stage, render_model_thumbs)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    try:
        n = len(p["files"])
        for i, f in enumerate(p["files"]):
            lo, hi = 0.02 + 0.5 * i / n, 0.02 + 0.5 * (i + 1) / n
            dl = _download(f["url"], tmp / f["name"], ctx, lo, hi)
            if f.get("blend_bundle"):
                ctx.progress(hi, "Unpacking")
                with zipfile.ZipFile(dl) as z:
                    for m in z.infolist():
                        tgt = (tmp / "unz" / m.filename).resolve()
                        if not str(tgt).startswith(str((tmp / "unz").resolve())):
                            continue                                   # zip-slip guard
                        if m.is_dir():
                            continue
                        tgt.parent.mkdir(parents=True, exist_ok=True)
                        with z.open(m) as s, tgt.open("wb") as d:
                            shutil.copyfileobj(s, d)
                blends = [str(x) for x in (tmp / "unz").rglob("*.blend")]
                if not blends:
                    raise RuntimeError("the bundle contains no .blend file")
                res = blender.run_script("export_assets.py", {"blends": blends, "out_dir": str(stage)}, ctx, (hi, 0.8), timeout=1800)
                if not res.get("files"):
                    raise RuntimeError("no models could be exported from the bundle")
            else:
                shutil.copy2(dl, stage / f["name"])
        ctx.progress(0.82, "Adding to the Asset Hub")
        res = imports.import_pack(str(stage), source="local", license=p["license"], pack_name=p["name"], author=p["author"],
                                  source_url=p["source_url"], kind_hint="model", tags=[f"pack:{pid}", "starter", *(["rigged"] if p["rigged"] else ["white-model"])],
                                  progress=lambda q, m: ctx.progress(0.82 + 0.08 * q, m))
        try:
            render_model_thumbs(ctx, None, 0.86, 0.9)
        except Exception:  # noqa: BLE001  (thumbnails are cosmetic)
            pass
        # rigged characters -> Animation rig library
        chars = [f for f in p["files"] if f.get("character")]
        for i, f in enumerate(chars):
            ctx.progress(0.9 + 0.09 * i / max(len(chars), 1), f"Adding {f['name']} to Animation")
            try:
                r = library.import_rig(ctx, str(stage / f["name"]), None, Path(f["name"]).stem, None)
                rig = r.get("rig") or r
                rigs.append({"id": rig.get("id"), "name": rig.get("name"), "kind": rig.get("kind")})
            except Exception as e:  # noqa: BLE001
                rigs.append({"name": f["name"], "error": str(e)[:200]})
        ctx.progress(1.0, "Installed")
        return {"pack": pid, "name": p["name"], "license": p["license"], "imported": res, "rigs": rigs}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def render_model_thumbs(ctx: JobContext, ids: list[str] | None = None, lo: float = 0.0, hi: float = 1.0) -> dict:
    """Workbench thumbnails for hub models that have none (stored next to the model file, inside the hub folder)."""
    from ..anim import blender
    from . import db as hubdb
    rows = hubdb.conn().execute("SELECT id FROM assets WHERE kind='model' AND (thumb IS NULL OR thumb='')").fetchall()
    todo = [r[0] for r in rows if not ids or r[0] in ids]
    items = []
    for aid in todo:
        main = hubdb.file_path(aid, "main")
        if main and Path(main).exists():
            items.append({"id": aid, "input": main, "output": str(Path(main).with_suffix(".thumb.png"))})
    if not items:
        return {"rendered": 0}
    blender.run_script("model_thumbs.py", {"items": [{"input": i["input"], "output": i["output"]} for i in items], "size": 384}, ctx, (lo, hi), timeout=1800)
    n = 0
    for i in items:
        if Path(i["output"]).exists():
            hubdb.update_fields(i["id"], thumb=i["output"]); n += 1
    return {"rendered": n}
