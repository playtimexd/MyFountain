"""Real-footage check: Wikimedia Commons brown bear (CC BY-SA 3.0, test only) -> mocap -> retarget onto the Sample Bear.
Usage: bear_real.py [start end] ; prints quality stats and writes overlay/preview frame grids for inspection."""
import json, subprocess, sys, time
from pathlib import Path
import httpx

MEDIA_ID = "82b24b10089b"
START, END = (float(sys.argv[1]), float(sys.argv[2])) if len(sys.argv) > 2 else (12.4, 15.2)
OUT = Path(r"D:\CutLab\data\anim\samples\bear")
c = httpx.Client(base_url="http://127.0.0.1:8787", timeout=900)


def wait(j):
    while j["status"] in ("queued", "running"):
        time.sleep(1); j = c.get(f"/api/jobs/{j['id']}").json()
    if j["status"] != "done":
        raise RuntimeError(f"{j['title']}: {j.get('error')}")
    return j["result"]


def grid(video, out, n=6, w=320):
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video], capture_output=True, text=True).stdout or 3)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", video, "-vf", f"fps={n / max(dur, 0.1):.4f},scale={w}:-1,tile={n}x1", "-frames:v", "1", str(out)], check=True)


t0 = time.time()
r = wait(c.post("/api/mocap", json={"mediaId": MEDIA_ID, "start": START, "end": END, "subject": "quadruped", "backend": "vitpose"}).json())
s = r["stats"]
print(f"mocap {time.time() - t0:.0f}s | frames={s['frames']} mean_conf={s['mean_conf']:.2f} subject_coverage={s.get('subject_coverage')} "
      f"reproj={s.get('reproj_px'):.1f}px jitter={s.get('jitter'):.1f} bone_cv={s.get('bone_length_cv'):.3f} gait={s.get('gait_period_s')}", flush=True)
ov = (r.get("overlay_media") or {}).get("path")
if ov:
    grid(ov, OUT / "overlay_grid.png")

clip = wait(c.post("/api/anim/clips/import", json={"path": r["motion_file"], "name": "Bear walk (real video test)"}).json())["clip"]
rig = next(x for x in c.get("/api/anim/rigs").json() if x["name"] == "Sample Bear")
rt = wait(c.post("/api/anim/retarget", json={"clipId": clip["id"], "rigId": rig["id"], "options": {"in_place": True, "loop": True, "smooth": True}}).json())
m = rt["metrics"]
print("retarget:", json.dumps({k: m.get(k) for k in ("frames", "foot_slide_ratio", "foot_slide_pct_height_per_s", "ground_penetration_pct", "loop_seam_ratio", "score")}))
pv = wait(c.post("/api/anim/preview", json={"clipId": rt["clip"]["id"], "camera": "side", "size": 480, "bg": "#7a8f6a"}).json())
p = Path(pv["media"]["path"])
mp4 = next(iter(sorted(p.parent.glob(p.stem + "*.mp4"))), p)
grid(str(mp4), OUT / "retarget_grid.png")
print("grids:", OUT / "overlay_grid.png", OUT / "retarget_grid.png")
print(json.dumps({"source_clip": clip["id"], "retarget_clip": rt["clip"]["id"]}))
