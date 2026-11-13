"""End-to-end test of the animation pipeline WITHOUT any model weights.

test bear (procedural) + synthetic quadruped walk -> import rig -> suggest-map -> retarget (in place / root motion) -> export GLB + FBX
-> re-import the GLB in a fresh Blender and assert armature / mesh / actions -> transparent preview (webm alpha + mp4) -> metrics / review
-> BVH import + humanoid retarget -> search -> my-engine hand-off (expects a clear error when its asset server is down).

Usage:  python tests/anim_e2e.py [base_url]      (default http://127.0.0.1:8802; starts its own engine on :8802 when nothing answers)
Frames of the preview are extracted to data/anim/test_e2e/frames for a visual check.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
OUT = ROOT / "data" / "anim" / "test_e2e"
OUT.mkdir(parents=True, exist_ok=True)
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8802"
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
OK, FAIL = [], []


def check(cond: bool, msg: str):
    (OK if cond else FAIL).append(msg)
    print(("  PASS " if cond else "  FAIL ") + msg, flush=True)


def wait(c: httpx.Client, job: dict, timeout: float = 900) -> dict:
    t0 = time.time()
    last = ""
    while job["status"] in ("queued", "running"):
        time.sleep(0.5)
        job = c.get(f"/api/jobs/{job['id']}").json()
        if job["message"] != last:
            last = job["message"]
        if time.time() - t0 > timeout:
            raise TimeoutError(job)
    if job["status"] != "done":
        raise RuntimeError(f"job {job['title']} -> {job['status']}: {job.get('error')}")
    return job["result"]


def blender_helper(script: Path, args: dict) -> dict:
    from app.services.anim import blender
    return blender.run_script(script.name, args) if script.parent == blender.SCRIPTS else {}


def main() -> int:
    proc = None
    try:
        httpx.get(BASE + "/api/health", timeout=2)
    except Exception:
        env = dict(os.environ, CUTLAB_PORT=BASE.rsplit(":", 1)[1])
        proc = subprocess.Popen([str(BACKEND / ".venv" / "Scripts" / "python.exe"), "-m", "app"], cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            time.sleep(0.5)
            try:
                httpx.get(BASE + "/api/health", timeout=1)
                break
            except Exception:
                pass
    c = httpx.Client(base_url=BASE, timeout=60)
    try:
        print("== status")
        st = c.get("/api/anim/status").json()
        check(st["blender"]["available"], f"Blender found: {st['blender']['version']}")

        print("== test rigs")
        from app.services.anim import blender
        bear = OUT / "bear.glb"
        hum = OUT / "humanoid.glb"
        blender.run_script("make_test_rig.py", {"kind": "bear", "out_blend": str(OUT / "bear.blend"), "out_glb": str(bear)})
        blender.run_script("make_test_rig.py", {"kind": "humanoid", "out_blend": str(OUT / "humanoid.blend"), "out_glb": str(hum)})
        check(bear.exists() and hum.exists(), "procedural test bear + humanoid built")

        print("== import rig")
        rig = wait(c, c.post("/api/anim/rigs/import", json={"path": str(bear), "name": "Test Bear"}).json())["rig"]
        check(rig["kind"] == "quadruped" and rig["bone_count"] == 24 and rig["mesh_count"] == 1, f"rig imported: {rig['kind']}, {rig['bone_count']} bones, {rig['mesh_count']} mesh")
        check(rig["thumb_url"] and c.get(rig["thumb_url"]).status_code == 200, "rig thumbnail served")
        hrig = wait(c, c.post("/api/anim/rigs/import", json={"path": str(hum), "name": "Test Human"}).json())["rig"]
        check(hrig["kind"] == "humanoid", "humanoid rig imported")

        print("== clips + bone map")
        clip = wait(c, c.post("/api/anim/clips/demo", json={"kind": "quadruped_walk"}).json())["clip"]
        check(clip["frames"] == 108 and clip["skeleton"] == "ap10k17", f"synthetic quadruped walk: {clip['frames']} frames, {clip['skeleton']}")
        check("mp4" in clip["files"] and c.get(clip["files"]["mp4"]).status_code == 200, "stick-figure preview video served")
        sug = c.post(f"/api/anim/rigs/{rig['id']}/suggest-map", json={"skeleton": "ap10k17"}).json()
        m = sug["map"]
        check(m.get("hind_upper_leg.L") == "left_hip" and m.get("front_paw.R") == "right_front_paw" and m.get("pelvis") == "root_of_tail", "suggest-map: legs, paws, pelvis mapped correctly")
        check(sug["overall"] > 0.8 and sug["coverage"] >= 0.99, f"suggest-map confidence {sug['overall']}, coverage {sug['coverage']}")
        put = c.put(f"/api/anim/rigs/{rig['id']}/bonemap", json={"map": m, "skeleton": "ap10k17"}).json()
        check(put["source"] == "user", "bone map stored")
        bad = c.put(f"/api/anim/rigs/{rig['id']}/bonemap", json={"map": {"nope": "left_hip"}, "skeleton": "ap10k17"})
        check(bad.status_code == 400, "bone map validation rejects unknown bones")

        print("== retarget")
        r_rm = wait(c, c.post("/api/anim/retarget", json={"clipId": clip["id"], "rigId": rig["id"], "options": {"loop": True, "in_place": False, "preview": False, "name": "Bear walk (root motion)"}}).json())
        met = r_rm["metrics"]
        print("   metrics:", {k: round(v, 3) for k, v in met.items() if isinstance(v, float)})
        check(r_rm["clip"]["rig_id"] == rig["id"] and r_rm["clip"]["root_motion"], "retargeted clip with root motion stored")
        check(r_rm["clip"]["source"]["check"]["fk_max_err_m"] < 0.005, f"solver FK matches Blender pose evaluation ({r_rm['clip']['source']['check']['fk_max_err_m'] * 1000:.2f} mm)")
        check(met["ground_penetration_max_pct_height"] < 3, f"no ground penetration ({met['ground_penetration_max_pct_height']:.2f}% of height)")
        check(met["floating_mean_pct_height"] < 3, f"not floating ({met['floating_mean_pct_height']:.2f}%)")
        check(met["foot_slide_pct_height_per_s"] < 25, f"foot sliding {met['foot_slide_pct_height_per_s']:.1f}% of height/s")
        check(met["loop_seam_vs_step"] < 2.5, f"loop seam {met['loop_seam_vs_step']:.2f}x a normal frame step")
        check(met["bone_stretch_max_pct"] < 1, "no bone stretching")
        r_ip = wait(c, c.post("/api/anim/retarget", json={"clipId": clip["id"], "rigId": rig["id"], "options": {"loop": True, "in_place": True, "preview": False, "name": "Bear walk (in place)"}}).json())
        check(not r_ip["clip"]["root_motion"], "in-place variant stored")
        bad_rt = c.post("/api/anim/retarget", json={"clipId": "nope", "rigId": rig["id"]})
        check(bad_rt.status_code == 404, "retarget with a bad clip id -> 404")
        cid_ip, cid_rm = r_ip["clip"]["id"], r_rm["clip"]["id"]
        c.patch(f"/api/anim/clips/{cid_ip}", json={"role": "walk"})
        check(c.patch(f"/api/anim/clips/{cid_rm}", json={"role": "bogus"}).status_code == 400, "role validation")

        print("== export GLB (game preset) + FBX")
        exp = wait(c, c.post("/api/anim/export", json={"clipId": cid_ip, "format": "glb", "options": {"preset": "game"}}).json())
        v = exp["verify"]
        check(v["ok"], f"export verified: {v.get('problems')}")
        names = sorted(a["name"] for a in v["actions"])
        check(len(names) == 2 and "Walk" in names, f"GLB holds all clips of the character, role name applied: {names}")
        glb = Path(exp["file"])
        check(glb.exists() and glb.stat().st_size > 50_000, f"GLB written: {glb.name} ({glb.stat().st_size // 1024} KB)")
        sidecar = c.get(exp["sidecar_url"]).json()
        check(sidecar["upAxis"] == "Y" and sidecar["forward"] == "+Z" and sidecar["skeleton"]["boneCount"] == 24 and len(sidecar["clips"]) == 2, "sidecar JSON lists skeleton + clips")
        check(c.get(exp["url"]).status_code == 200, "GLB downloadable")
        fbx = wait(c, c.post("/api/anim/export", json={"clipId": cid_ip, "format": "fbx", "options": {"preset": "game"}}).json())
        check(Path(fbx["file"]).exists() and fbx["verify"]["ok"], f"FBX exported and re-imported OK: {fbx['verify'].get('problems')}")
        per = wait(c, c.post("/api/anim/export", json={"clipId": cid_ip, "format": "glb", "options": {"per_clip": True}}).json())
        check(len(per["exports"]) == 2 and all(e["verify"]["ok"] for e in per["exports"]), "per-clip armature-only GLBs")

        print("== re-import the GLB in a fresh Blender and assert")
        ver = blender.run_script("verify_export.py", {"input": str(glb)})
        check(ver["armatures"] and ver["bones"][0] == 24, f"armature with 24 bones ({ver['bones']})")
        check(ver["skinned_meshes"] == ["Body"], "skinned mesh present")
        exp_frames = r_ip["clip"]["frames"]
        walk = next(a for a in ver["actions"] if a["name"] == "Walk")
        want = (exp_frames - 1) / 30
        check(abs(walk["duration_s"] - want) < 0.1, f"Walk lasts {walk['duration_s']:.2f}s (expected {want:.2f}s = {exp_frames} frames @30fps)")
        check(walk["fcurves"] >= 24, f"Walk has {walk['fcurves']} animation channels")
        check(ver["bbox_min"][2] > -0.05 and 0.8 < (ver["bbox_max"][2] - ver["bbox_min"][2]) < 1.3, "metres + feet at the origin (bbox height %.2f m)" % (ver["bbox_max"][2] - ver["bbox_min"][2]))

        print("== preview (transparent webm + mp4)")
        pv = wait(c, c.post("/api/anim/preview", json={"clipId": cid_rm, "camera": "side", "size": 384, "bg": "transparent"}).json())
        media = pv["media"]
        check(media["origin"] == "anim" and media["kind"] == "video", f"media row registered (origin=anim, {media['width']}x{media['height']}, {media['duration']:.1f}s)")
        webm = OUT / "preview.webm"
        webm.write_bytes(c.get(media["url"]).content)
        r = subprocess.run([FFMPEG, "-v", "error", "-c:v", "libvpx-vp9", "-i", str(webm), "-vf", "select=eq(n\\,20),alphaextract,format=gray", "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True)
        alpha = np.frombuffer(r.stdout, np.uint8)
        check(alpha.size > 0 and alpha.min() == 0 and alpha.max() == 255, f"webm keeps alpha when decoded with libvpx-vp9 (alpha range {alpha.min() if alpha.size else '-'}..{alpha.max() if alpha.size else '-'})")
        mp4 = c.get(pv["files"]["mp4"])
        check(mp4.status_code == 200 and len(mp4.content) > 5000, "mp4 (solid background) served")
        fdir = OUT / "frames"
        fdir.mkdir(exist_ok=True)
        subprocess.run([FFMPEG, "-v", "error", "-y", "-c:v", "libvpx-vp9", "-i", str(webm), "-vf", "select='not(mod(n\\,25))',format=rgba,scale=256:-1", "-vsync", "0", str(fdir / "f%02d.png")], capture_output=True)
        print("   frames for a visual check:", sorted(p.name for p in fdir.glob("*.png"))[:6], "in", fdir)

        print("== review (host mode: no LLM) + metrics")
        rv = wait(c, c.post("/api/anim/review", json={"clipId": cid_rm, "mode": "host", "frames": 6}).json())
        check(rv["mode"] == "host" and not rv["llm"]["used"] and len(rv["frames"]) >= 6 and rv["rubric"], f"host review returns {len(rv['frames'])} JPEG frames + rubric, no model call")
        check("foot_slide_pct_height_per_s" in rv["metrics"] and "score" in rv, f"metrics measured, score {rv['score']}")

        print("== annotate + search")
        ann = wait(c, c.post("/api/anim/annotate", json={"clipId": cid_ip}).json())
        tags = ann["clip"]["tags"]
        check("walk" in tags and "quadruped" in tags and ann["clip"]["caption"], f"tags/caption written (llm used: {ann['llm'].get('used')}): {tags[:8]}")
        hit = c.get("/api/anim/search", params={"q": "bear walking loop"}).json()
        check(hit["hits"] and "walk" in hit["hits"][0]["clip"]["name"].lower(), f"search ({hit['mode']}) finds walk clips: {[h['clip']['name'] for h in hit['hits'][:3]]}")
        none = c.get("/api/anim/search", params={"q": "dragon breathing fire"}).json()
        check(len(none["hits"]) <= len(hit["hits"]), "unrelated query ranks lower / fewer hits")

        print("== BVH import + humanoid retarget")
        bvh = OUT / "human_walk.bvh"
        helper = OUT / "gen_bvh.py"
        helper.write_text(
            "import sys; sys.path.insert(0, r'%s')\nimport bpy, common\nbpy.ops.wm.open_mainfile(filepath=sys.argv[-2])\narm = common.pick_armature()\n"
            "bake = common.load_bake(sys.argv[-1])\ncommon.apply_bake(arm, bake, 'Walk', in_place=False)\nbpy.ops.object.select_all(action='DESELECT'); arm.select_set(True)\n"
            "bpy.context.view_layer.objects.active = arm\nbpy.ops.export_anim.bvh(filepath=r'%s', root_transform_only=False, global_scale=100.0)\n" % (str(BACKEND / "blender"), str(bvh)), "utf-8")
        hclip = wait(c, c.post("/api/anim/clips/demo", json={"kind": "biped_walk"}).json())["clip"]
        hr = wait(c, c.post("/api/anim/retarget", json={"clipId": hclip["id"], "rigId": hrig["id"], "options": {"loop": True, "preview": False, "in_place": True}}).json())
        check(hr["metrics"]["ground_penetration_max_pct_height"] < 3 and hr["metrics"]["foot_slide_pct_height_per_s"] < 15, f"humanoid walk retargeted (score {hr['metrics']['score']})")
        bake_h = hr["clip"]["id"]
        from app.services.anim import db as adb
        hb = adb.get_clip(bake_h)["_files"]["bake"]
        exe = blender.find_exe()
        subprocess.run([exe, "-b", "--factory-startup", "-P", str(helper), "--", adb.get_rig(hrig["id"])["file"], hb], capture_output=True)
        check(bvh.exists(), "BVH generated with Blender's exporter")
        bclip = wait(c, c.post("/api/anim/clips/import", json={"path": str(bvh), "name": "Human BVH"}).json())["clip"]
        check(bclip["skeleton"] == "custom" and bclip["frames"] > 50 and bclip["stats"]["height_m"] > 1.2, f"BVH imported: {bclip['frames']} frames, height {bclip['stats']['height_m']:.2f} m, units {bclip['stats'].get('units')}")
        bm = c.get(f"/api/anim/rigs/{hrig['id']}/bonemap", params={"clipId": bclip["id"]}).json()
        check(bm["map"].get("mixamorig:LeftLeg") == "mixamorig:LeftLeg" and len(bm["map"]) >= 17, f"bone map against a custom (BVH) skeleton: {len(bm['map'])} bones")
        br = wait(c, c.post("/api/anim/retarget", json={"clipId": bclip["id"], "rigId": hrig["id"], "options": {"preview": False, "in_place": True}}).json())
        check(br["metrics"]["ground_penetration_max_pct_height"] < 4, f"BVH retargeted (score {br['metrics']['score']}, slide {br['metrics']['foot_slide_pct_height_per_s']:.1f}%/s)")

        print("== my-engine hand-off")
        r = c.post("/api/anim/send/my-engine", json={"rigId": rig["id"]})
        if r.status_code == 503:
            check("pnpm assets" in r.json()["detail"], "asset server down -> clear message mentioning `pnpm assets`")
        else:
            j = wait(c, r.json())
            check(j.get("sent"), "uploaded to my-engine")

        print("== cleanup (API deletes; rig delete cascades to its retargeted clips)")
        check(c.delete(f"/api/anim/clips/{bclip['id']}").status_code == 200, "delete clip")
        for rid in (rig["id"], hrig["id"]):
            check(c.delete(f"/api/anim/rigs/{rid}").status_code == 200, "delete rig")
        for cl in (clip, hclip):
            c.delete(f"/api/anim/clips/{cl['id']}")
        left = c.get("/api/anim/clips").json()
        check(not any(x["id"] in (cid_ip, cid_rm, bake_h) for x in left), "retargeted clips removed with their rig")
    finally:
        if proc:
            proc.terminate()
    print(f"\n{len(OK)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
