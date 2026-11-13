"""Cross-module check on the MAIN engine (:8787): test bear rig -> demo quadruped walk -> retarget -> transparent preview
-> placed on a video timeline over a red background -> rendered frame keeps alpha -> game GLB export.
Leaves the demo bear + clip in the animation library on purpose (a ready-made sample for the user)."""
import io, sys, time
from pathlib import Path
import httpx
from PIL import Image

sys.path.insert(0, r"D:\CutLab\backend")
from app.services.anim import blender  # noqa: E402

c = httpx.Client(base_url="http://127.0.0.1:8787", timeout=900)


def wait(j):
    while j["status"] in ("queued", "running"):
        time.sleep(1); j = c.get(f"/api/jobs/{j['id']}").json()
    assert j["status"] == "done", j
    return j["result"]


def ok(msg): print("[ok]  ", msg, flush=True)


rigs = [r for r in c.get("/api/anim/rigs").json() if r["name"] == "Sample Bear"]
if rigs:
    rig = rigs[0]
else:
    out = Path(r"D:\CutLab\data\anim\samples"); out.mkdir(parents=True, exist_ok=True)
    blender.run_script("make_test_rig.py", {"kind": "bear", "out_blend": str(out / "bear.blend"), "out_glb": str(out / "bear.glb")})
    rig = wait(c.post("/api/anim/rigs/import", json={"path": str(out / "bear.glb"), "name": "Sample Bear"}).json())["rig"]
ok(f"rig {rig['name']} ({rig['id']})")

src = wait(c.post("/api/anim/clips/demo", json={"kind": "quadruped_walk"}).json())["clip"]
r = wait(c.post("/api/anim/retarget", json={"clipId": src["id"], "rigId": rig["id"], "options": {"in_place": True, "loop": True}}).json())
clip = r["clip"]
ok(f"retarget -> clip {clip['id']} metrics={ {k: r['metrics'][k] for k in list(r['metrics'])[:4]} }")
c.patch(f"/api/anim/clips/{clip['id']}", json={"name": "Sample Bear Walk", "role": "Walk", "tags": ["sample", "quadruped", "walk"]})

pv = wait(c.post("/api/anim/preview", json={"clipId": clip["id"], "camera": "side", "size": 512, "bg": "transparent"}).json())
media = pv["media"]
assert media["path"].lower().endswith(".webm") and media["origin"] == "anim", media
ok(f"transparent preview media {media['id']} {media['width']}x{media['height']} {media['duration']:.2f}s")

T = {"x": 0, "y": 0, "scale": 1, "rotation": 0, "opacity": 1}
A = {"brightness": 0, "contrast": 1, "saturation": 1, "hue": 0, "blur": 0, "sharpen": 0, "vignette": 0, "sepia": 0, "grayscale": 0, "temperature": 0}
project = {"version": 1, "name": "bear-over-red", "width": 640, "height": 360, "fps": 30, "background": "#ff0000", "tracks": [
    {"id": "v1", "kind": "video", "name": "Video 1", "muted": False, "hidden": False, "locked": False, "clips": [
        {"id": "b1", "type": "video", "assetId": media["id"], "start": 0, "in": 0, "out": media["duration"], "speed": 1, "volume": 1,
         "fadeIn": 0, "fadeOut": 0, "fit": "contain", "transform": T, "adjust": A}]}]}
img = Image.open(io.BytesIO(c.post("/api/export/frame", json={"project": project, "t": min(1.0, media["duration"] / 2), "width": 640}).content)).convert("RGB")
img.save(r"D:\CutLab\data\anim\samples\bear_over_red.jpg")
w, h = img.size
corner = img.getpixel((w // 2 - h // 2 + 6, 6))          # inside the square preview area, top-left: should be background red
px = [img.getpixel((x, y)) for x in range(w // 2 - 60, w // 2 + 60, 6) for y in range(h // 2 - 60, h // 2 + 60, 6)]
non_red = sum(1 for p in px if not (p[0] > 180 and p[1] < 80 and p[2] < 80))
assert corner[0] > 180 and corner[1] < 80 and corner[2] < 80, f"alpha lost: corner={corner}"
assert non_red > 10, "bear not visible"
ok(f"alpha kept in export pipeline: corner={corner}, bear pixels in centre={non_red}/{len(px)}")

ex = wait(c.post("/api/export", json={"project": project, "preset": {"format": "mp4", "quality": "draft"}}).json())
ok(f"timeline export {ex['file']}")

g = wait(c.post("/api/anim/export", json={"clipId": clip["id"], "rigId": rig["id"], "format": "glb", "options": {"preset": "game"}}).json())
ok(f"game GLB export: { {k: v for k, v in g.items() if k in ('file', 'files', 'clips', 'bytes')} }")
print("ALL OK")
