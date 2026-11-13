"""Unreal Engine mannequins (Manny / Quinn + the unarmed animation set) exported from the user's local UE5 install.

Licence: Unreal Engine EULA - fine for Unreal projects (the usual case: retarget new motion onto the UE skeleton and send it back to
Unreal); do not ship these files in other engines. Nothing is downloaded: the content comes from <UE>/Templates/TemplateResources.

How: a throwaway project in data/hub/tmp with the template mannequin folder, opened by the full editor off-screen (skeletal-mesh FBX
export needs render resources, which the -run=pythonscript commandlet does not create), runs an export script and quits.
"""
from __future__ import annotations

import glob
import shutil
import subprocess
from pathlib import Path

from ...jobs import JobContext
from . import paths

PACK = {
    "name": "Unreal Engine mannequins (Manny & Quinn + unarmed animations)",
    "desc": "Exported from your local Unreal Engine 5 install: the UE5 mannequins on the standard SK_Mannequin skeleton plus the idle, "
            "walk, jog, jump and attack clips. Animations you make in CutLab on these characters fit Unreal's skeleton directly. "
            "Licence: Unreal Engine EULA - use them in Unreal projects.",
    "license": "custom:Unreal Engine EULA (use in Unreal Engine projects)", "author": "Epic Games", "source_url": "https://www.unrealengine.com/eula",
    "size_mb": 0, "rigged": True, "local_unreal": True, "files": [],
}

UE_SCRIPT = r'''
import os, unreal
OUT = r"__OUT__"
os.makedirs(OUT, exist_ok=True)
reg = unreal.AssetRegistryHelpers.get_asset_registry()
reg.scan_paths_synchronous(["/Game/Characters/Mannequins"], True)

def export(asset, path):
    t = unreal.AssetExportTask()
    t.object = asset
    t.filename = path
    t.automated = True
    t.prompt = False
    t.replace_identical = True
    o = unreal.FbxExportOption()
    for k, v in (("ascii", False), ("force_front_x_axis", False), ("level_of_detail", False), ("collision", False), ("export_morph_targets", True), ("vertex_color", False)):
        try:
            o.set_editor_property(k, v)
        except Exception:
            pass
    t.options = o
    ok = unreal.Exporter.run_asset_export_task(t)
    print("CUTLAB_EXPORT", "ok" if ok and os.path.exists(path) else "FAIL", os.path.basename(path))

try:
    for m in ("SKM_Manny_Simple", "SKM_Quinn_Simple"):
        a = unreal.load_asset("/Game/Characters/Mannequins/Meshes/" + m)
        if a:
            export(a, os.path.join(OUT, m + ".fbx"))
    for a in reg.get_assets_by_path("/Game/Characters/Mannequins/Anims/Unarmed", recursive=True):
        if str(a.asset_class_path.asset_name) == "AnimSequence":
            export(unreal.load_asset(str(a.package_name)), os.path.join(OUT, "anim_" + str(a.asset_name) + ".fbx"))
finally:
    print("CUTLAB_DONE")
    unreal.SystemLibrary.quit_editor()
'''


def find_unreal() -> tuple[Path, Path] | None:
    """(UnrealEditor.exe, template mannequin folder) of the newest UE5 install that ships the mannequins."""
    roots = glob.glob(r"C:\Program Files\Epic Games\UE_5*") + glob.glob(r"D:\Epic Games\UE_5*") + glob.glob(r"D:\Program Files\Epic Games\UE_5*")
    for root in sorted(roots, reverse=True):
        exe = Path(root) / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe"
        man = Path(root) / "Templates" / "TemplateResources" / "High" / "Characters" / "Content" / "Mannequins"
        if exe.exists() and man.is_dir():
            return exe, man
    return None


def export_mannequins(ctx: JobContext, out_dir: Path, lo: float, hi: float) -> list[Path]:
    found = find_unreal()
    if not found:
        raise RuntimeError("No Unreal Engine 5 install with the template mannequins was found (looked in Program Files\\Epic Games).")
    exe, man = found
    proj = paths.TMP_DIR / "ue_mannequin_export"
    if not (proj / "Content" / "Characters" / "Mannequins").exists():
        ctx.progress(lo, "Preparing a temporary Unreal project")
        (proj / "Content" / "Characters").mkdir(parents=True, exist_ok=True)
        shutil.copytree(man, proj / "Content" / "Characters" / "Mannequins", dirs_exist_ok=True)
    ver = exe.parents[3].name.replace("UE_", "")
    (proj / "Export.uproject").write_text('{"FileVersion": 3, "EngineAssociation": "%s", "Plugins": [{"Name": "PythonScriptPlugin", "Enabled": true}]}' % ver, "utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    script = proj / "export_mannequins.py"
    script.write_text(UE_SCRIPT.replace("__OUT__", str(out_dir)), "utf-8")
    ctx.progress(lo + (hi - lo) * 0.05, "Unreal is exporting the mannequins (the first run compiles shaders and can take several minutes)")
    p = subprocess.Popen([str(exe), str(proj / "Export.uproject"), f"-ExecutePythonScript={script}", "-unattended", "-nop4", "-nosplash",
                          "-RenderOffscreen", "-stdout", "-FullStdOutLogOutput"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         errors="replace", creationflags=0x08000000)
    ctx.register_process(p)
    n = 0
    for line in p.stdout:                                           # type: ignore[union-attr]
        if "CUTLAB_EXPORT" in line:
            n += 1
            ctx.progress(lo + (hi - lo) * min(0.95, 0.1 + n / 30), f"Unreal exported {n} file(s)")
        elif "CUTLAB_DONE" in line:
            ctx.progress(hi, "Unreal finished")
        ctx.check()
    p.wait(timeout=600)
    files = sorted(out_dir.glob("*.fbx"))
    if not any(f.name.startswith("SKM_") for f in files):
        raise RuntimeError("Unreal did not export the mannequins (the editor may have failed to start; see its log in the project's Saved/Logs).")
    return files


def install(ctx: JobContext, pid: str, tmp: Path, stage: Path, render_thumbs) -> dict:
    from ..anim import library
    from . import imports
    files = export_mannequins(ctx, tmp / "fbx", 0.02, 0.6)
    meshes = [f for f in files if f.name.startswith("SKM_")]
    anims = [f for f in files if f.name.startswith("anim_")]
    for f in meshes:
        shutil.copy2(f, stage / f.name.replace("SKM_", "UE5_").replace("_Simple", ""))
    ctx.progress(0.62, "Adding to the Asset Hub")
    res = imports.import_pack(str(stage), source="local", license=PACK["license"], pack_name=PACK["name"], author=PACK["author"],
                              source_url=PACK["source_url"], kind_hint="model", tags=[f"pack:{pid}", "starter", "rigged", "unreal"],
                              progress=lambda q, m: ctx.progress(0.62 + 0.05 * q, m))
    try:
        render_thumbs(ctx, None, 0.67, 0.7)
    except Exception:  # noqa: BLE001
        pass
    rigs = []
    for f in sorted(stage.glob("*.fbx")):
        ctx.progress(0.7, f"Adding {f.stem} to Animation")
        try:
            r = library.import_rig(ctx, str(f), None, f.stem.replace("_", " "), None)
            rig = r.get("rig") or r
            rigs.append({"id": rig.get("id"), "name": rig.get("name"), "kind": rig.get("kind")})
        except Exception as e:  # noqa: BLE001
            rigs.append({"name": f.stem, "error": str(e)[:200]})
    clips = []
    for i, f in enumerate(anims):
        nice = f.stem[5:].replace("MF_Unarmed_", "").replace("MM_", "").replace("_", " ")
        ctx.progress(0.75 + 0.24 * i / max(len(anims), 1), f"Importing animation {nice}")
        try:
            c = library.import_clip(ctx, str(f), f"UE5 {nice}", {})
            clips.append((c.get("clip") or {}).get("name"))
        except Exception as e:  # noqa: BLE001
            clips.append(f"{nice}: failed ({str(e)[:100]})")
    ctx.progress(1.0, "Installed")
    return {"pack": pid, "name": PACK["name"], "license": PACK["license"], "imported": res, "rigs": rigs, "clips": clips}
