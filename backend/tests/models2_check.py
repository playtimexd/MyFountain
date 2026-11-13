"""Real-model checks after the user approved the downloads:
A) Kokoro TTS (zh + en) -> Whisper ASR round trip (is the speech intelligible?)
B) ViTPose++/RT-DETR mocap on a rendered video of the sample bear -> motion.npz -> retarget back onto the bear."""
import glob, json, time, sys
from pathlib import Path
import httpx

c = httpx.Client(base_url="http://127.0.0.1:8787", timeout=900)


def wait(j, t=1800):
    t0 = time.time()
    while j["status"] in ("queued", "running"):
        if time.time() - t0 > t: raise TimeoutError(j)
        time.sleep(1); j = c.get(f"/api/jobs/{j['id']}").json()
    if j["status"] != "done":
        raise RuntimeError(f"{j['title']}: {j.get('error')}")
    return j


print("== A) Kokoro TTS -> Whisper ==", flush=True)
voices = [v for v in c.get("/api/tts/voices", params={"provider": "kokoro"}).json() if v.get("available")]
print("kokoro voices:", len(voices), [v["id"] for v in voices][:8])
for lang, text in [("zh", "欢迎使用 CutLab。这是一段本地生成的中文配音测试。"), ("en", "Welcome to CutLab. This voice was generated locally on your computer.")]:
    v = next((v["id"] for v in voices if v.get("lang", "").lower().startswith(lang)), None)
    t0 = time.time()
    j = wait(c.post("/api/tts/generate", json={"text": text, "voice": v, "provider": "kokoro"}).json())
    m = j["result"]["media"]
    asr = wait(c.post("/api/ai/subtitles", json={"mediaId": m["id"], "language": lang}).json())["result"]
    heard = " ".join(s["text"] for s in asr["segments"])
    print(f"[{lang}] voice={j['result'].get('voice')} {m['duration']:.1f}s in {time.time()-t0:.1f}s\n   said : {text}\n   heard: {heard}", flush=True)

print("\n== B) mocap on a rendered bear video ==", flush=True)
clips = [x for x in c.get("/api/anim/clips").json() if x["name"] == "Sample Bear Walk"]
clip = clips[0]
rig_id = clip["rig_id"]
pv = wait(c.post("/api/anim/preview", json={"clipId": clip["id"], "camera": "side", "size": 720, "bg": "#7a8f6a"}).json())["result"]
src = pv["media"]
if src["path"].lower().endswith(".webm"):       # want the opaque mp4 version for the detector
    mp4 = sorted(glob.glob(str(Path(src["path"]).with_suffix("")) + "*.mp4"))
    if mp4:
        src = wait(c.post("/api/media/import", json={"paths": [mp4[0]]}).json())["result"]["items"][0]
print("source video:", Path(src["path"]).name, src["width"], "x", src["height"], f"{src['duration']:.2f}s")
t0 = time.time()
mj = wait(c.post("/api/mocap", json={"mediaId": src["id"], "subject": "quadruped", "backend": "vitpose", "rigId": rig_id}).json())
r = mj["result"]
print(f"mocap done in {time.time()-t0:.0f}s: skeleton={r.get('skeleton')} stats={json.dumps(r.get('stats'))[:300]}", flush=True)
print("overlay video:", (r.get("overlay_media") or {}).get("path"))
imp = c.post("/api/anim/clips/import", json={"path": r["motion_file"], "name": "Bear walk (from video)"}).json()
mclip = wait(imp)["result"]["clip"] if "status" in imp else imp["clip"]
rt = wait(c.post("/api/anim/retarget", json={"clipId": mclip["id"], "rigId": rig_id, "options": {"in_place": True, "loop": True}}).json())["result"]
print("retarget from video -> bear metrics:", json.dumps(rt["metrics"])[:400])
print("ALL DONE")
