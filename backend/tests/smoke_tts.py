"""Smoke test for /api/tts/*.  CUTLAB_BASE=http://127.0.0.1:8801 backend\\.venv\\Scripts\\python.exe backend\\tests\\smoke_tts.py
Never calls /api/tts/prepare (that downloads model weights)."""
import os
import sys
import time

import httpx

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.environ.get("CUTLAB_BASE", "http://127.0.0.1:8801")
cli = httpx.Client(base_url=BASE, timeout=300)


def wait(j):
    while j["status"] in ("queued", "running"):
        time.sleep(0.4)
        j = cli.get(f"/api/jobs/{j['id']}").json()
    assert j["status"] == "done", j
    return j


before = {m["id"] for m in cli.get("/api/media?limit=2000").json()}
ok = True


def step(name, fn):
    global ok
    t = time.time()
    try:
        print(f"[ok]   {name} ({time.time() - t:.1f}s)", fn() or "")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")


def providers():
    p = {x["id"]: x for x in cli.get("/api/tts/providers").json()}
    assert p["sapi"]["available"]
    return {k: v["available"] for k, v in p.items()}


def zh():
    j = wait(cli.post("/api/tts/generate", json={"text": "你好，欢迎使用 CutLab 配音。今天天气很好！我们一起来剪辑视频吧。", "provider": "sapi"}).json())
    m = j["result"]["media"]
    assert m["origin"] == "tts" and m["kind"] == "audio" and 2 < m["duration"] < 15, m
    return f"{j['result']['voice']} {m['duration']:.1f}s"


def en_speed():
    a = wait(cli.post("/api/tts/generate", json={"text": "This is a short test of the text to speech engine.", "speed": 1.0}).json())["result"]["media"]["duration"]
    b = wait(cli.post("/api/tts/generate", json={"text": "This is a short test of the text to speech engine.", "speed": 1.8}).json())["result"]["media"]["duration"]
    assert b < a * 0.7, (a, b)
    return f"1.0x={a:.1f}s 1.8x={b:.1f}s"


def long():
    text = "这是一个很长的段落。" * 40
    j = wait(cli.post("/api/tts/generate", json={"text": text}).json())
    return f"{j['result']['media']['duration']:.0f}s"


def batch():
    items = [{"id": i, "text": t} for i, t in enumerate(["第一句字幕。", "Second subtitle line.", "第三句比较长一点的字幕内容，用来测试适配时长。"])]
    items[2]["maxDuration"] = 2.0
    j = wait(cli.post("/api/tts/batch", json={"items": items}).json())
    r = j["result"]["items"]
    assert [x["id"] for x in r] == [0, 1, 2] and all(x["media"]["origin"] == "tts" for x in r)
    return [round(x["media"]["duration"], 1) for x in r]


def kokoro_missing():
    r = cli.post("/api/tts/generate", json={"text": "hi", "provider": "kokoro"})
    j = r.json()
    try:
        wait(j)
    except AssertionError as e:
        assert "Kokoro" in str(e)
        return "clear error when model missing"
    return "kokoro is installed and worked"


step("providers", providers)
step("voices", lambda: len(cli.get("/api/tts/voices").json()))
step("generate zh (sapi)", zh)
step("generate en + speed", en_speed)
step("long text", long)
step("batch", batch)
step("kokoro without weights", kokoro_missing)
n = 0
for m in cli.get("/api/media?limit=2000").json():
    if m["id"] not in before:
        cli.delete(f"/api/media/{m['id']}", params={"delete_file": True}); n += 1
print("cleanup", n)
sys.exit(0 if ok else 1)
