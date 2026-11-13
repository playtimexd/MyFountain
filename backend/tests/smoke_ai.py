"""Smoke test for the backend-ai endpoints.  Usage:
    set CUTLAB_BASE=http://127.0.0.1:8801   (default)
    backend\\.venv\\Scripts\\python.exe backend\\tests\\smoke_ai.py [--agent] [--slow]
Generates tiny test media with ffmpeg into data/test/, imports them, then calls every endpoint."""
from __future__ import annotations

import base64
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ.get("CUTLAB_BASE", "http://127.0.0.1:8801")
ROOT = Path(__file__).resolve().parents[2]
TEST = ROOT / "data" / "test"
TEST.mkdir(parents=True, exist_ok=True)
cli = httpx.Client(base_url=BASE, timeout=600)
OK, FAIL = [], []


def ff(*a):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True, cwd=TEST)


def wait(job: dict, label: str, timeout=900) -> dict:
    t0 = time.time()
    while job["status"] in ("queued", "running"):
        if time.time() - t0 > timeout:
            raise TimeoutError(label)
        time.sleep(0.5)
        job = cli.get(f"/api/jobs/{job['id']}").json()
    if job["status"] != "done":
        raise RuntimeError(f"{label}: {job['status']} {job.get('error')}")
    return job


def check(name):
    def deco(fn):
        def run():
            t0 = time.time()
            try:
                msg = fn()
                OK.append(name)
                print(f"[ok]   {name} ({time.time() - t0:.1f}s) {msg or ''}")
            except Exception as e:  # noqa: BLE001
                FAIL.append(name)
                print(f"[FAIL] {name}: {type(e).__name__}: {e}")
        return run
    return deco


def post(url, body=None, ok=(200,)):
    r = cli.post(url, json=body or {})
    if r.status_code not in ok:
        raise RuntimeError(f"{r.status_code} {r.text[:300]}")
    return r.json()


def import_file(p: Path) -> dict:
    j = wait(post("/api/media/import", {"paths": [str(p)]}), "import")
    return j["result"]["items"][0]


def prepare_media() -> dict:
    if not (TEST / "arial.ttf").exists():
        import shutil
        shutil.copy("C:/Windows/Fonts/arial.ttf", TEST / "arial.ttf")
    ff("-f", "lavfi", "-i", "nullsrc=s=640x360:r=25:d=8,geq=r='128+100*sin(X/20+T*3)':g='128+100*sin(Y/15-T*2)':b='128+100*sin((X+Y)/25+T*4)'",
       "-f", "lavfi", "-i", "sine=f=440:d=8",
       "-vf", "drawtext=text='CUTLAB LOGO':x=w-tw-20:y=20:fontsize=28:fontcolor=white:box=1:boxcolor=black@0.5:fontfile=arial.ttf",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "wm_test.mp4")
    ff("-f", "lavfi", "-i", "color=c=gray:s=320x180:r=25:d=6", "-f", "lavfi", "-i",
       "aevalsrc='if(lt(mod(t,3),1.5),0.5*sin(2*PI*300*t),0)':d=6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "sil_test.mp4")
    ff("-f", "lavfi", "-i", "testsrc2=s=400x300:d=1", "-frames:v", "1", "img_test.png")
    return {"wm": import_file(TEST / "wm_test.mp4"), "sil": import_file(TEST / "sil_test.mp4"), "img": import_file(TEST / "img_test.png")}


def cleanup(before: set):
    """Remove the media rows (and generated files) this run created so the library stays clean."""
    n = 0
    for m in cli.get("/api/media?limit=2000").json():
        if m["id"] not in before:
            cli.delete(f"/api/media/{m['id']}", params={"delete_file": True}); n += 1
    print(f"cleanup: removed {n} media rows created by this run")


def main():
    slow = "--slow" in sys.argv
    before = {m["id"] for m in cli.get("/api/media?limit=2000").json()}
    M = prepare_media()
    state: dict = {}

    @check("music/providers")
    def _():
        p = cli.get("/api/music/providers").json()
        assert any(x["id"] == "procedural" and x["available"] for x in p)
        return ",".join(f"{x['id']}={x['available']}" for x in p)
    _()

    @check("music/generate procedural (auto)")
    def _():
        j = wait(post("/api/music/generate", {"prompt": "happy upbeat pop vlog background music", "duration": 20, "provider": "procedural", "seed": 7}), "music")
        m = j["result"]["media"]
        assert m["kind"] == "audio" and abs(m["duration"] - 20) < 1.5, m
        state["music"] = m
        return str(j["result"]["info"])
    _()

    @check("music/generate acestep (expects clear error if server down)")
    def _():
        r = cli.post("/api/music/generate", json={"prompt": "x", "duration": 10, "provider": "acestep"})
        job = r.json()
        try:
            wait(job, "acestep", 60)
            return "acestep server is up and produced audio"
        except TimeoutError:
            cli.post(f"/api/jobs/{job['id']}/cancel")
            return "acestep server is up and still generating after 60s (cancelled)"
        except RuntimeError as e:
            assert "ACE-Step" in str(e)
            return "error as expected: " + str(e)[:80]
    _()

    @check("ai/capabilities + asr/status")
    def _():
        c = cli.get("/api/ai/capabilities").json()
        return f"upscale={c['upscale']['engine']} asr_ready={c['asr']['ready']} ({c['asr']['message']})"
    _()

    @check("ai/frame")
    def _():
        r = cli.get("/api/ai/frame", params={"mediaId": M["wm"]["id"], "t": 2})
        assert r.headers["content-type"] == "image/jpeg" and len(r.content) > 1000
    _()

    @check("ai/upscale video (fallback lanczos)")
    def _():
        j = wait(post("/api/ai/upscale", {"mediaId": M["sil"]["id"], "scale": 2, "model": "auto"}), "upscale")
        m = j["result"]["media"]
        assert m["width"] == 640 and m["has_audio"], m
        return f"{j['result']['engine']} {m['width']}x{m['height']} {j['result']['note']}"
    _()

    @check("ai/upscale image")
    def _():
        j = wait(post("/api/ai/upscale", {"mediaId": M["img"]["id"], "scale": 2}), "upscale-img")
        m = j["result"]["media"]
        assert m["width"] == 800, m
    _()

    @check("ai/watermark/detect")
    def _():
        r = post("/api/ai/watermark/detect", {"mediaId": M["wm"]["id"]})
        assert r["regions"], r
        reg = r["regions"][0]
        state["region"] = reg
        # logo is top-right: x ~ 640-?-20
        assert reg["x"] > 320 and reg["y"] < 120, reg
        return str(r["regions"][:2]) + " " + r["message"]
    _()

    @check("ai/watermark inpaint (video)")
    def _():
        reg = state.get("region") or {"x": 460, "y": 12, "w": 170, "h": 44}
        j = wait(post("/api/ai/watermark", {"mediaId": M["wm"]["id"], "regions": [reg], "method": "inpaint"}), "wm")
        m = j["result"]["media"]
        assert m["has_audio"] and abs(m["duration"] - 8) < 0.5, m
        return j["result"]["engine"]
    _()

    @check("ai/watermark delogo (video)")
    def _():
        j = wait(post("/api/ai/watermark", {"mediaId": M["wm"]["id"], "regions": [{"x": 460, "y": 12, "w": 170, "h": 44}], "method": "delogo", "start": 0, "end": 5}), "wm2")
        assert j["result"]["media"]["has_audio"]
    _()

    @check("ai/inpaint (base64) + remove-bg")
    def _():
        raw = (TEST / "img_test.png").read_bytes()
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), 1)
        mask = np.zeros(img.shape[:2], np.uint8)
        mask[100:140, 100:200] = 255
        ok, mb = cv2.imencode(".png", mask)
        r = post("/api/ai/inpaint", {"imageBase64": base64.b64encode(raw).decode(), "maskBase64": base64.b64encode(mb.tobytes()).decode()})
        assert base64.b64decode(r["imageBase64"])[:4] == b"\x89PNG"
        # remove-bg on a synthetic subject: red disc on blue-grey
        bg = np.full((300, 400, 3), (200, 160, 120), np.uint8)
        cv2.circle(bg, (200, 150), 80, (30, 30, 220), -1)
        ok, bb = cv2.imencode(".png", bg)
        r2 = post("/api/ai/remove-bg", {"imageBase64": base64.b64encode(bb.tobytes()).decode()})
        out = cv2.imdecode(np.frombuffer(base64.b64decode(r2["imageBase64"]), np.uint8), cv2.IMREAD_UNCHANGED)
        assert out.shape[2] == 4
        a = out[:, :, 3]
        assert a[150, 200] > 200 and a[10, 10] < 50, (a[150, 200], a[10, 10])
        return f"inpaint={r['engine']} bg={r2['engine']}"
    _()

    @check("ai/auto-cut tight")
    def _():
        j = wait(post("/api/ai/auto-cut", {"mediaId": M["sil"]["id"], "style": "tight"}), "cut")
        keep = j["result"]["keep"]
        assert 1 <= len(keep) <= 3 and j["result"]["kept_duration"] < 4.2, j["result"]
        return f"{keep} {j['result']['note']}"
    _()

    @check("ai/auto-cut highlights")
    def _():
        j = wait(post("/api/ai/auto-cut", {"mediaId": M["wm"]["id"], "style": "highlights", "target_duration": 3}), "hl")
        assert j["result"]["keep"], j["result"]
        return str(j["result"]["keep"])
    _()

    @check("ai/auto-cut talking-head")
    def _():
        j = wait(post("/api/ai/auto-cut", {"mediaId": M["sil"]["id"], "style": "talking-head"}), "th")
        return j["result"]["note"]
    _()

    @check("ai/subtitles (409 + clear message when model missing)")
    def _():
        st = cli.get("/api/ai/asr/status").json()
        r = cli.post("/api/ai/subtitles", json={"mediaId": M["sil"]["id"]})
        if st["ready"]:
            wait(r.json(), "subs")
            return "ready: transcribed"
        assert r.status_code == 409 and "Whisper" in r.json()["detail"], r.text
        return r.json()["detail"]
    _()

    @check("search/index + query")
    def _():
        wait(post("/api/search/index", {"paths": [str(TEST)], "recursive": False, "caption": "--slow" in sys.argv}), "index", 1800)
        s = cli.get("/api/search/status").json()
        assert s["indexed"] >= 3, s
        r = post("/api/search/query", {"q": "sil test", "limit": 5})
        assert r["hits"] and "sil_test" in r["hits"][0]["path"], r
        r2 = post("/api/search/query", {"q": "wm test video"})
        return f"indexed={s['indexed']} docs={s['docs']} caps(clip={s['capabilities']['clip']['ready']},vision={s['capabilities']['vision_model']}) signals={r['signals']} top={r['hits'][0]['snippet'][:40]}"
    _()

    if "--agent" in sys.argv:
        @check("agent/chat (real Ollama)")
        def _():
            clip = {"id": "c1", "type": "video", "assetId": M["sil"]["id"], "start": 0, "in": 0, "out": 6, "speed": 1, "volume": 1}
            proj = {"version": 1, "name": "t", "width": 1280, "height": 720, "fps": 30, "background": "#000",
                    "tracks": [{"id": "v1", "kind": "video", "name": "V1", "muted": False, "hidden": False, "locked": False, "clips": [clip]}]}
            r = post("/api/agent/chat", {"messages": [{"role": "user", "content": "Cut out the silent parts of the clip"}], "project": proj})
            print("   reply:", r["reply"][:200])
            print("   ops:", r["ops"])
            print("   tools:", [t["tool"] for t in r["toolTrace"]], "dropped:", r.get("droppedOps"))
            assert r["reply"]
        _()
        @check("agent/chat music + search")
        def _():
            r = post("/api/agent/chat", {"messages": [{"role": "user", "content": "Generate 20 seconds of relaxing background music, then add it at the start of the timeline at volume 0.3"}],
                                          "project": {"version": 1, "name": "t", "width": 1280, "height": 720, "fps": 30, "background": "#000", "tracks": []}})
            print("   reply:", r["reply"][:200]); print("   ops:", r["ops"]); print("   tools:", [t["tool"] for t in r["toolTrace"]])
            assert any(o["op"] == "add_music" for o in r["ops"]), r
        _()

    if "--keep" not in sys.argv:
        cleanup(before)
    print(f"\n{len(OK)} passed, {len(FAIL)} failed", FAIL)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
