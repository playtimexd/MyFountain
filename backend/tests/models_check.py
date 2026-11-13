"""Check that the optional models (Whisper, Real-ESRGAN) are really used. Needs speech_en.m4a + small_320.mp4 in data/testmedia."""
import time, httpx

c = httpx.Client(base_url="http://127.0.0.1:8787", timeout=900)


def wait(j):
    while j["status"] in ("queued", "running"):
        time.sleep(1); j = c.get(f"/api/jobs/{j['id']}").json()
    return j


imp = wait(c.post("/api/media/import", json={"paths": [r"D:\CutLab\data\testmedia\speech_en.m4a", r"D:\CutLab\data\testmedia\small_320.mp4"]}).json())
items = {m["name"]: m for m in imp["result"]["items"]}
print("asr status:", c.get("/api/ai/asr/status").json())
print("upscale caps:", c.get("/api/capabilities").json()["upscale"])

t0 = time.time()
j = wait(c.post("/api/ai/subtitles", json={"mediaId": items["speech_en"]["id"], "language": "en"}).json())
print(f"\nsubtitles: {j['status']} {j.get('error')} in {time.time() - t0:.1f}s")
for s in (j.get("result") or {}).get("segments", []):
    print(f"  [{s['start']:.1f}-{s['end']:.1f}] {s['text']}")

t0 = time.time()
j = wait(c.post("/api/ai/upscale", json={"mediaId": items["small_320"]["id"], "scale": 4, "model": "realesrgan"}).json())
m = (j.get("result") or {}).get("media") or {}
print(f"\nupscale video: {j['status']} {j.get('error')} in {time.time() - t0:.1f}s | {j['message']} | -> {m.get('width')}x{m.get('height')} dur={m.get('duration')}")
