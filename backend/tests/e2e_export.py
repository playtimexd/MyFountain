"""End-to-end check of import -> segment -> export against a running engine (default :8787)."""
import json, sys, time
import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
c = httpx.Client(base_url=BASE, timeout=120)


def wait(job):
    while job["status"] in ("queued", "running"):
        time.sleep(0.4)
        job = c.get(f"/api/jobs/{job['id']}").json()
    assert job["status"] == "done", job
    return job


imp = wait(c.post("/api/media/import", json={"paths": [r"D:\CutLab\data\testmedia"]}).json())
items = {m["name"]: m for m in imp["result"]["items"]}
print("imported:", list(items))
A, B, M = items["a_testsrc"], items["b_vertical"], items["music"]

seg = wait(c.post("/api/segment", json={"mediaId": A["id"], "method": "fixed", "every": 2}).json())
print("fixed segments:", seg["result"]["count"])
seg = wait(c.post("/api/segment", json={"mediaId": A["id"], "method": "scene"}).json())
print("scene segments:", seg["result"]["count"])
seg = wait(c.post("/api/segment", json={"mediaId": A["id"], "method": "silence"}).json())
print("silence segments:", seg["result"]["segments"])

T = lambda **k: {"x": 0, "y": 0, "scale": 1, "rotation": 0, "opacity": 1, **k}
ADJ = {"brightness": 0, "contrast": 1, "saturation": 1, "hue": 0, "blur": 0, "sharpen": 0, "vignette": 0, "sepia": 0, "grayscale": 0, "temperature": 0}


def clip(id, type, start, i, o, **kw):
    return {"id": id, "type": type, "start": start, "in": i, "out": o, "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0,
            "fit": "contain", "transform": T(), "adjust": dict(ADJ), **kw}


project = {
    "version": 1, "name": "e2e测试", "width": 1280, "height": 720, "fps": 30, "background": "#101018",
    "tracks": [
        {"id": "t1", "kind": "video", "name": "主轨", "muted": False, "hidden": False, "locked": False, "clips": [
            clip("c1", "video", 0, 0, 4, assetId=A["id"], adjust={**ADJ, "saturation": 1.4, "contrast": 1.1}, fadeOut=0.5),
            clip("c2", "video", 3.5, 0, 4, assetId=B["id"], speed=2, fit="cover", transform=T(scale=0.45, x=0.25, y=-0.05, rotation=12),
                 transitionIn={"type": "slideleft", "duration": 0.6}, adjust={**ADJ, "sepia": 0.6}),
        ]},
        {"id": "t2", "kind": "video", "name": "叠加", "muted": False, "hidden": False, "locked": False, "clips": [
            clip("c3", "sticker", 1.0, 0, 3, assetId="star", transform=T(scale=0.25, x=-0.3, y=-0.25), transitionIn={"type": "zoomin", "duration": 0.5}),
            clip("c4", "video", 5.0, 0, 2, assetId=A["id"], transform=T(scale=0.4, x=-0.25, y=0.25), transitionIn={"type": "wipeleft", "duration": 0.5}),
        ]},
        {"id": "t3", "kind": "text", "name": "文字", "muted": False, "hidden": False, "locked": False, "clips": [
            clip("c5", "text", 0.5, 0, 4, transform=T(y=0.32), text={
                "content": "你好 CutLab\n第二行字幕", "font": "Microsoft YaHei", "size": 0.08, "color": "#ffffff", "bold": True, "italic": False,
                "align": "center", "stroke": "#000000", "strokeWidth": 0.06, "shadow": True, "bg": "", "bgPadding": 0.25}, fadeIn=0.3),
        ]},
        {"id": "t4", "kind": "audio", "name": "音乐", "muted": False, "hidden": False, "locked": False, "clips": [
            clip("c6", "audio", 0, 0, 7, assetId=M["id"], volume=0.4, fadeOut=1.0),
        ]},
    ],
}

t0 = time.time()
job = wait(c.post("/api/export", json={"project": project, "preset": {"format": "mp4", "quality": "normal"}}).json())
print(f"export ok in {time.time() - t0:.1f}s ->", job["result"]["file"], "| msg:", job["message"])
m = job["result"]["media"]
print("result:", {k: m[k] for k in ("duration", "width", "height", "fps", "has_audio", "size")})
json.dump(project, open(r"D:\CutLab\data\testmedia\project.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
