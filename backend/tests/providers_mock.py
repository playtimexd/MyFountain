"""Offline tests for the generator providers (videogen: seedance + ComfyUI; gen3d: meshy + tripo).

Everything runs against httpx.MockTransport - no network, no keys, no spending. Uses a throw-away CUTLAB_DATA dir.
Run:  D:\\CutLab\\backend\\.venv\\Scripts\\python.exe D:\\CutLab\\backend\\tests\\providers_mock.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="cutlab_prov_")
os.environ["CUTLAB_DATA"] = _tmp
sys.path.insert(0, r"D:\CutLab\backend")

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config, db  # noqa: E402
from app.jobs import jobs  # noqa: E402
from app.routers import gen3d as r_gen3d, videogen as r_videogen  # noqa: E402
from app.services.providers import common as C, comfyui, gen3d as g3, meshy, seedance, tripo  # noqa: E402

KEYS = {"seedance_api_key": "sk-seed-SECRET-123456", "meshy_api_key": "msy-SECRET-abcdef", "tripo_api_key": "tsk_SECRET_ghijkl"}
SETTINGS = {**config.load_settings(), **KEYS, "videogen_provider": "auto", "gen3d_provider": "auto", "comfyui_url": "http://127.0.0.1:8188"}
config.load_settings = lambda: dict(SETTINGS)
C.FAST_POLL = True

app = FastAPI()
app.include_router(r_videogen.router)
app.include_router(r_gen3d.router)
client = TestClient(app)

# ---- fixtures: a real 1 s mp4 (for ffprobe/ingest), a png, fake glb --------------------------------------------
MP4 = Path(_tmp) / "fixture.mp4"
subprocess.run([config.FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=24",
                "-pix_fmt", "yuv420p", str(MP4)], check=True)
MP4_BYTES = MP4.read_bytes()
from PIL import Image  # noqa: E402
PNG = Path(_tmp) / "bear.png"
Image.new("RGB", (640, 480), (120, 80, 40)).save(PNG)
PNG_BYTES = PNG.read_bytes()
GLB = b"glTF" + b"\x02\x00\x00\x00" + b"\x00" * 64

from app.ingest import ingest_file  # noqa: E402
IMG_ROW = ingest_file(PNG, origin="import", name="bear photo")

LOG: list[httpx.Request] = []


def wait_job(job_id: str, timeout: float = 60.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = jobs.get(job_id).public()
        if j["status"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    raise AssertionError("job timeout")


def no_secret(obj) -> None:
    s = json.dumps(obj, default=str)
    for v in KEYS.values():
        assert v not in s, f"secret leaked: {v[:6]}..."


def ok(msg: str) -> None:
    print("[ok]", msg)


# =================================================================================================================
# 1. SEEDANCE
# =================================================================================================================
SD = {"polls": 0, "created": None, "fail": None, "auth_on_download": None, "mode": "ok"}


def seedance_handler(req: httpx.Request) -> httpx.Response:
    LOG.append(req)
    u = str(req.url)
    if u.startswith("https://tos.example/"):
        SD["auth_on_download"] = req.headers.get("authorization")
        return httpx.Response(200, content=MP4_BYTES)
    assert u.startswith("https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks"), u
    if SD["mode"] == "401":
        return httpx.Response(401, json={"error": {"code": "AuthenticationError", "message": f"Invalid key {KEYS['seedance_api_key']}"}})
    if req.method == "POST":
        assert req.headers["authorization"] == f"Bearer {KEYS['seedance_api_key']}"
        SD["created"] = json.loads(req.content)
        return httpx.Response(200, json={"id": "cgt-2026-test"})
    if req.method == "DELETE":
        return httpx.Response(200, json={})
    SD["polls"] += 1
    if SD["mode"] == "fail":
        return httpx.Response(200, json={"id": "cgt-2026-test", "status": "failed", "error": {"code": "OutputVideoSensitiveContentDetected", "message": "blocked"}})
    if SD["polls"] < 3:
        return httpx.Response(200, json={"id": "cgt-2026-test", "status": "queued" if SD["polls"] == 1 else "running"})
    return httpx.Response(200, json={"id": "cgt-2026-test", "status": "succeeded", "content": {"video_url": "https://tos.example/out.mp4"},
                                     "usage": {"completion_tokens": 48000, "total_tokens": 48000}, "duration": 5, "resolution": "480p",
                                     "ratio": "16:9", "framespersecond": 24})


C.TRANSPORT = httpx.MockTransport(seedance_handler)

# 1a. listing never leaks keys, flags cloud + verified
lst = client.get("/api/videogen/providers").json()
no_secret(lst)
sd = next(p for p in lst if p["id"] == "seedance")
assert sd["needs_key"] and sd["key_set"] and sd["verified"] and "cost_note" in sd and sd["capabilities"]["max_duration"] <= 10
ok("providers listing: fields present, no secrets")

# 1b. 409 without confirm_cost and NO network call
n0 = len(LOG)
r = client.post("/api/videogen", json={"prompt": "a brown bear walking", "provider": "seedance", "duration": 5})
assert r.status_code == 409, r.text
body = r.json()
assert "detail" in body and body["estimate"]["amount"] and body["estimate"]["currency"] == "USD", body
assert len(LOG) == n0, "network call made before cost confirmation"
ok(f"seedance 409 without confirm (estimate ~${body['estimate']['amount']}), no request sent")

# 1c. dry run: request shown, key redacted, no network
r = client.post("/api/videogen", json={"prompt": "a brown bear walking", "provider": "seedance", "duration": 5, "capture_friendly": True, "dry_run": True})
assert r.status_code == 200, r.text
dr = r.json()
no_secret(dr)
first = dr["requests"][0]
assert first["method"] == "POST" and first["url"].endswith("/contents/generations/tasks")
assert first["headers"]["Authorization"] == "Bearer ***"
assert "Static locked-off camera" in first["body"]["content"][0]["text"]
assert first["body"]["model"] == seedance.DEFAULT_MODEL and first["body"]["duration"] == 5 and first["body"]["ratio"] == "16:9"
assert len(LOG) == n0
ok("seedance dry_run redacts key, shows capture-friendly prompt, no network")

# 1d. hard caps
r = client.post("/api/videogen", json={"prompt": "x", "provider": "seedance", "duration": 30, "confirm_cost": True})
assert r.status_code == 400 and "cap" in r.json()["detail"], r.text
r = client.post("/api/videogen", json={"prompt": "x", "provider": "seedance", "resolution": "1080p", "confirm_cost": True})
assert r.status_code == 400, r.text
assert len(LOG) == n0
ok("seedance hard caps (duration, resolution) -> 400, no network")

# 1e. success path (text-to-video)
r = client.post("/api/videogen", json={"prompt": "a brown bear walking, side view", "provider": "seedance", "duration": 5, "capture_friendly": True, "confirm_cost": True})
assert r.status_code == 200, r.text
j = wait_job(r.json()["id"])
assert j["status"] == "done", j
no_secret(j)
m = j["result"]["media"]
assert m["origin"] == "gen" and m["tags"] == ["videogen", "seedance"] and m["kind"] == "video", m
b = SD["created"]
assert b["content"][0]["type"] == "text" and "walk cycle" in b["content"][0]["text"] and b["watermark"] is False
assert b["generate_audio"] is False and b["resolution"] == "480p" and b["execution_expires_after"] == 3600
assert SD["auth_on_download"] is None, "Authorization header must not be sent to the pre-signed download host"
ok("seedance success: media row origin=gen tags=[videogen, seedance]; download without auth")

# 1f. image-to-video -> first_frame + adaptive + data URI
SD["polls"] = 0
r = client.post("/api/videogen", json={"prompt": "bear walks", "provider": "seedance", "duration": 4, "image_mediaId": IMG_ROW["id"], "confirm_cost": True})
j = wait_job(r.json()["id"])
assert j["status"] == "done", j
img_item = SD["created"]["content"][1]
assert img_item["role"] == "first_frame" and img_item["image_url"]["url"].startswith("data:image/") and SD["created"]["ratio"] == "adaptive"
ok("seedance image-to-video: first_frame data URI, ratio adaptive")

# 1g. provider failure surfaces, no key
SD["polls"], SD["mode"] = 0, "fail"
r = client.post("/api/videogen", json={"prompt": "x", "provider": "seedance", "confirm_cost": True})
j = wait_job(r.json()["id"])
assert j["status"] == "error" and "OutputVideoSensitiveContentDetected" in j["error"], j
SD["mode"] = "401"
r = client.post("/api/videogen", json={"prompt": "x", "provider": "seedance", "confirm_cost": True})
j = wait_job(r.json()["id"])
assert j["status"] == "error" and "API key" in j["error"], j
no_secret(j)
SD["mode"] = "ok"
ok("seedance errors (failed task, 401 with key echoed by server) surfaced and scrubbed")

# 1h. missing key
_saved = SETTINGS["seedance_api_key"]
SETTINGS["seedance_api_key"] = ""
r = client.post("/api/videogen", json={"prompt": "x", "provider": "seedance", "confirm_cost": True})
j = wait_job(r.json()["id"])
assert j["status"] == "error" and "not set" in j["error"], j
SETTINGS["seedance_api_key"] = _saved
ok("seedance without key -> clear error")

# =================================================================================================================
# 2. COMFYUI (local)
# =================================================================================================================
CF = {"running": True, "prompts": [], "hist_calls": 0, "reject_first_save": True, "uploaded": None, "freed": False}
WAN_NODES = {n for w in comfyui.workflows().values() for n in w["required_nodes"]} - {"UnetLoaderGGUF"}


def combo(opts):
    return {"input": {"required": {"x": [opts]}}}


def comfy_handler(req: httpx.Request) -> httpx.Response:
    if not CF["running"]:
        raise httpx.ConnectError("refused")
    LOG.append(req)
    p = req.url.path
    if p == "/system_stats":
        return httpx.Response(200, json={"system": {"comfyui_version": "0.9.9"}, "devices": [{"vram_free": 8_000_000_000, "vram_total": 24_000_000_000}]})
    if p.startswith("/object_info/"):
        cls = p.split("/")[-1]
        if cls not in WAN_NODES:
            return httpx.Response(200, json={})
        info = {"input": {"required": {}}}
        if cls == "UNETLoader":
            info["input"]["required"]["unet_name"] = [["wan2.2_ti2v_5B_fp16.safetensors"]]
        if cls == "CLIPLoader":
            info["input"]["required"]["clip_name"] = [["umt5_xxl_fp8_e4m3fn_scaled.safetensors"]]
        if cls == "VAELoader":
            info["input"]["required"]["vae_name"] = [["wan2.2_vae.safetensors"]]
        if cls == "CheckpointLoaderSimple":
            info["input"]["required"]["ckpt_name"] = [[]]          # no LTX checkpoint installed
        return httpx.Response(200, json={cls: info})
    if p == "/upload/image":
        CF["uploaded"] = True
        return httpx.Response(200, json={"name": "cutlab_x.jpg", "subfolder": "", "type": "input"})
    if p == "/prompt":
        g = json.loads(req.content)["prompt"]
        CF["prompts"].append(g)
        if CF["reject_first_save"] and len(CF["prompts"]) == 1:
            return httpx.Response(400, json={"error": {"type": "prompt_outputs_failed_validation", "message": "Prompt outputs failed validation"},
                                             "node_errors": {"58": {"class_type": "SaveVideo", "errors": [{"message": "Required input is missing", "details": "format.codec"}]}}})
        return httpx.Response(200, json={"prompt_id": "pid-1", "number": 1, "node_errors": {}})
    if p == "/history/pid-1":
        CF["hist_calls"] += 1
        if CF["hist_calls"] < 2:
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"pid-1": {"status": {"status_str": "success", "completed": True, "messages": []},
                                                   "outputs": {"58": {"images": [{"filename": "cutlab_00001_.mp4", "subfolder": "video", "type": "output"}], "animated": [True]}}}})
    if p == "/queue":
        return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
    if p == "/view":
        assert req.url.params["filename"] == "cutlab_00001_.mp4" and req.url.params["subfolder"] == "video"
        return httpx.Response(200, content=MP4_BYTES)
    if p == "/free":
        CF["freed"] = True
        return httpx.Response(200, json={})
    return httpx.Response(404)


C.TRANSPORT = httpx.MockTransport(comfy_handler)

# 2a. not running -> available false + how-to
CF["running"] = False
lc = comfyui.listing()
assert lc["available"] is False and "not running" in lc["howto"] and "127.0.0.1:8188" in lc["howto"], lc["howto"]
r = client.post("/api/videogen", json={"prompt": "bear", "provider": "local"})
j = wait_job(r.json()["id"])
assert j["status"] == "error" and "ComfyUI is not running" in j["error"], j
ok("local: ComfyUI down -> available:false with install how-to; job error explains")

# 2b. running, Wan native ready, GGUF + LTX not
CF["running"] = True
lc = comfyui.listing()
ready = {w["id"]: w for w in lc["workflows"]}
assert lc["available"] and ready["wan22_ti2v_5b"]["ready"] and not ready["wan22_ti2v_5b_gguf"]["ready"] and not ready["ltxv_2b"]["ready"], ready
assert any("UnetLoaderGGUF" in h for h in ready["wan22_ti2v_5b_gguf"]["howto"])
assert any("ltx-video-2b-v0.9.5.safetensors" in h for h in ready["ltxv_2b"]["howto"])
assert lc["vram_free_gb"] == 8.0
ok("local: detects ready Wan workflow, reports missing nodes/models for GGUF + LTX")

# 2c. no confirm needed for local; text-to-video; SaveVideo variant retry; free VRAM
r = client.post("/api/videogen", json={"prompt": "a brown bear walking", "provider": "local", "duration": 5, "aspect": "16:9", "capture_friendly": True})
assert r.status_code == 200, r.text
j = wait_job(r.json()["id"])
assert j["status"] == "done", j
m = j["result"]["media"]
assert m["origin"] == "gen" and m["tags"] == ["videogen", "local"], m
g1, g2 = CF["prompts"][0], CF["prompts"][1]
assert g2["55"]["inputs"]["width"] == 832 and g2["55"]["inputs"]["height"] == 480 and g2["55"]["inputs"]["length"] == 121
assert g2["37"]["inputs"]["unet_name"] == "wan2.2_ti2v_5B_fp16.safetensors" and g2["38"]["inputs"]["type"] == "wan"
assert "start_image" not in g2["55"]["inputs"] and "56" not in g2
assert g1["58"]["inputs"]["format"] == "mp4" and "format.codec" in g1["58"]["inputs"]
assert g2["58"]["inputs"] == {"video": ["57", 0], "filename_prefix": g2["58"]["inputs"]["filename_prefix"], "format": "auto", "codec": "auto"}
assert "{{" not in json.dumps(g2) and "walk cycle" in g2["6"]["inputs"]["text"]
assert CF["freed"], "should free VRAM afterwards"
ok("local: Wan t2v graph (832x480, 121 frames), SaveVideo spelling retried, VRAM freed, media tagged [videogen, local]")

# 2d. image-to-video uses upload + start_image
CF.update(prompts=[], hist_calls=0, reject_first_save=False)
r = client.post("/api/videogen", json={"prompt": "bear walks", "provider": "local", "duration": 3, "aspect": "9:16", "image_mediaId": IMG_ROW["id"]})
j = wait_job(r.json()["id"])
assert j["status"] == "done", j
g = CF["prompts"][0]
assert CF["uploaded"] and g["56"]["inputs"]["image"] == "cutlab_x.jpg" and g["55"]["inputs"]["start_image"] == ["56", 0]
assert (g["55"]["inputs"]["width"], g["55"]["inputs"]["height"]) == (480, 832) and g["55"]["inputs"]["length"] == 73
ok("local: image-to-video uploads the start image and wires start_image (9:16, 3 s -> 73 frames)")

# 2e. caps and dry-run
r = client.post("/api/videogen", json={"prompt": "x", "provider": "local", "duration": 12})
assert r.status_code == 400 and "cap" in r.json()["detail"]
r = client.post("/api/videogen", json={"prompt": "x", "provider": "local", "dry_run": True})
assert r.status_code == 200 and r.json()["requests"][0]["url"].endswith("/prompt") and r.json()["ready"] is True
ok("local: duration cap -> 400; dry_run returns the /prompt graph")

# 2f. auto provider prefers a ready local; falls back to seedance when ComfyUI is down
r = client.post("/api/videogen", json={"prompt": "x", "dry_run": True})
assert r.json()["provider"] == "local"
CF["running"] = False
r = client.post("/api/videogen", json={"prompt": "x", "dry_run": True})
assert r.json()["provider"] == "seedance"
CF["running"] = True
ok("auto provider: local when ready, else seedance (still needs confirm_cost)")

# =================================================================================================================
# 3. MESHY
# =================================================================================================================
MS = {"polls": {}, "bodies": {}, "rig_import": None}
RIGGED = "https://assets.meshy.example/rigged.glb"


def meshy_handler(req: httpx.Request) -> httpx.Response:
    LOG.append(req)
    u, p = str(req.url), req.url.path
    if req.url.host == "assets.meshy.example":
        assert "authorization" not in req.headers
        return httpx.Response(200, content=PNG_BYTES if u.endswith(".png") else GLB)
    if req.url.host == "127.0.0.1" and p == "/api/anim/rigs/import":
        MS["rig_import"] = json.loads(req.content)
        return httpx.Response(200, json={"id": "imp1", "status": "queued", "progress": 0, "result": None})
    if req.url.host == "127.0.0.1" and p == "/api/jobs/imp1":
        return httpx.Response(200, json={"id": "imp1", "status": "done", "result": {"rig": {"id": "rig_42", "name": "bear_rig"}}})
    assert req.url.host == "api.meshy.ai", u
    assert req.headers["authorization"] == f"Bearer {KEYS['meshy_api_key']}"
    if req.method == "POST":
        b = json.loads(req.content)
        MS["bodies"][p + ":" + str(b.get("mode", ""))] = b
        tid = {"/openapi/v2/text-to-3d:preview": "pv1", "/openapi/v2/text-to-3d:refine": "rf1", "/openapi/v1/rigging:": "rg1", "/openapi/v1/image-to-3d:": "im1"}[p + ":" + str(b.get("mode", ""))]
        return httpx.Response(200, json={"result": tid})
    if req.method == "DELETE":
        return httpx.Response(200)
    tid = p.rsplit("/", 1)[-1]
    MS["polls"][tid] = MS["polls"].get(tid, 0) + 1
    if MS["polls"][tid] < 2:
        return httpx.Response(200, json={"id": tid, "status": "IN_PROGRESS", "progress": 40})
    credits = {"pv1": 5, "rf1": 10, "rg1": 5, "im1": 15}[tid]
    out = {"id": tid, "status": "SUCCEEDED", "progress": 100, "consumed_credits": credits, "thumbnail_url": "https://assets.meshy.example/t.png",
           "model_urls": {"glb": f"https://assets.meshy.example/{tid}.glb"}}
    if tid == "rg1":
        out["result"] = {"rigged_character_glb_url": RIGGED, "rigged_character_fbx_url": "x",
                         "basic_animations": {"walking_glb_url": "https://assets.meshy.example/walk.glb", "running_glb_url": "https://assets.meshy.example/run.glb"}}
    return httpx.Response(200, json=out)


C.TRANSPORT = httpx.MockTransport(meshy_handler)

lm = next(p for p in client.get("/api/gen3d/providers").json() if p["id"] == "meshy")
assert lm["capabilities"]["rig"] == ["biped"] and lm["capabilities"]["animals_rig"] is False and lm["verified"]
no_secret(lm)
ok("meshy listing: rig capability = biped only")

n0 = len(LOG)
r = client.post("/api/gen3d", json={"prompt": "knight character", "provider": "meshy", "rig": {"type": "biped"}})
assert r.status_code == 409 and r.json()["estimate"]["amount"] == 5 + 10 + 5 and r.json()["estimate"]["currency"] == "credits", r.text
assert len(LOG) == n0
ok("meshy 409 without confirm (estimate 20 credits: 5 preview + 10 refine + 5 rig), no request sent")

r = client.post("/api/gen3d", json={"prompt": "bear", "provider": "meshy", "rig": {"type": "quadruped"}, "confirm_cost": True})
assert r.status_code == 400 and "humanoid" in r.json()["detail"] and len(LOG) == n0, r.text
ok("meshy rejects quadruped rig up-front (400), no spend")

r = client.post("/api/gen3d", json={"prompt": "knight", "provider": "meshy", "rig": {"type": "biped"}, "dry_run": True})
dr = r.json()
no_secret(dr)
assert [q["url"].split("api.meshy.ai")[-1] for q in dr["requests"][:3]] == ["/openapi/v2/text-to-3d", "/openapi/v2/text-to-3d", "/openapi/v1/rigging"]
assert dr["requests"][0]["headers"]["Authorization"] == "Bearer ***" and dr["requests"][0]["body"]["pose_mode"] == "t-pose" and len(LOG) == n0
ok("meshy dry_run: 3-step plan, key redacted")

r = client.post("/api/gen3d", json={"prompt": "knight character", "provider": "meshy", "rig": {"type": "biped"}, "animation": "walk", "confirm_cost": True, "name": "knight"})
assert r.status_code == 200, r.text
j = wait_job(r.json()["id"])
assert j["status"] == "done", j
res = j["result"]
no_secret(res)
assert res["rigged"] and res["file"].endswith("_rigged.glb") and Path(res["file"]).parent == g3.INCOMING and Path(res["file"]).read_bytes() == GLB
assert res["credits"] == 20 and res["rig"] == {"id": "rig_42", "name": "bear_rig"} and MS["rig_import"] == {"path": res["file"]}
assert res["url"].startswith("/api/gen3d/file/") and "walk" in res["animations"] and res["preview_media"]["origin"] == "gen"
assert MS["bodies"]["/openapi/v2/text-to-3d:refine"]["preview_task_id"] == "pv1" and MS["bodies"]["/openapi/v2/text-to-3d:refine"]["remove_lighting"] is False
assert MS["bodies"]["/openapi/v1/rigging:"] == {"input_task_id": "rf1", "height_meters": 1.7}
fr = client.get(res["url"])
assert fr.status_code == 200 and fr.content == GLB
assert client.get("/api/gen3d/file/..%2Fsecret.glb").status_code in (400, 404)
assert client.get("/api/gen3d/file/nothere.glb").status_code == 404
ok("meshy text->preview->refine->rig: files in anim/incoming, rig imported via /api/anim/rigs/import, file endpoint safe")

# image path
MS["polls"].clear()
r = client.post("/api/gen3d", json={"image_mediaId": IMG_ROW["id"], "provider": "meshy", "confirm_cost": True})
j = wait_job(r.json()["id"])
assert j["status"] == "done" and not j["result"]["rigged"] and "rig_import" not in j["result"] and j["result"]["credits"] == 15, j
assert MS["bodies"]["/openapi/v1/image-to-3d:"]["image_url"].startswith("data:image/") and MS["bodies"]["/openapi/v1/image-to-3d:"]["should_texture"] is True
ok("meshy image-to-3D (no rig): 15 credits, data-URI upload")

# =================================================================================================================
# 4. TRIPO
# =================================================================================================================
TP = {"polls": {}, "bodies": {}, "mode": "ok"}


def tripo_handler(req: httpx.Request) -> httpx.Response:
    LOG.append(req)
    p = req.url.path
    if req.url.host == "cdn.tripo.example":
        assert "authorization" not in req.headers
        return httpx.Response(200, content=PNG_BYTES if p.endswith(".png") else GLB)
    if req.url.host == "127.0.0.1":
        if p == "/api/anim/rigs/import":
            return httpx.Response(404, json={"detail": "Not Found"})
    assert req.url.host == "openapi.tripo3d.com", str(req.url)
    assert req.headers["authorization"] == f"Bearer {KEYS['tripo_api_key']}"
    if TP["mode"] == "403":
        return httpx.Response(403, json={"code": 2010, "message": f"You don't have enough credit {KEYS['tripo_api_key']}", "suggestion": "Please purchase more credit"})
    if p == "/v3/files":
        assert b'name="file"' in req.content
        return httpx.Response(200, json={"code": 0, "data": {"file_token": "file_abc"}})
    if p == "/v3/account/balance":
        return httpx.Response(200, json={"code": 0, "data": {"balance": 1234.5, "frozen": 0}})
    if req.method == "POST":
        b = json.loads(req.content)
        TP["bodies"][p] = b
        tid = {"/v3/generation/image-to-model": "g1", "/v3/generation/text-to-model": "g1", "/v3/animations/rig-check": "c1", "/v3/animations/rig": "r1", "/v3/animations/retarget": "a1"}[p]
        return httpx.Response(200, json={"code": 0, "data": {"task_id": tid}})
    tid = p.rsplit("/", 1)[-1]
    TP["polls"][tid] = TP["polls"].get(tid, 0) + 1
    if TP["mode"] == "fail" and tid == "r1":
        return httpx.Response(200, json={"code": 0, "data": {"task_id": tid, "status": "failed", "error_code": 5001, "error_message": "rigging failed"}})
    if TP["polls"][tid] < 2:
        return httpx.Response(200, json={"code": 0, "data": {"task_id": tid, "status": "running", "progress": 30}})
    out = {"model_url": f"https://cdn.tripo.example/{tid}.glb"}
    if tid == "g1":
        out["rendered_image_url"] = "https://cdn.tripo.example/g1.png"
    if tid == "c1":
        out = {"riggable": True, "rig_type": "quadruped"}
    return httpx.Response(200, json={"code": 0, "data": {"task_id": tid, "status": "success", "progress": 100, "output": out, "credits_consumed": {"g1": 30, "c1": 0, "r1": 25, "a1": 10}[tid]}})


C.TRANSPORT = httpx.MockTransport(tripo_handler)

lt = next(p for p in client.get("/api/gen3d/providers").json() if p["id"] == "tripo")
assert "quadruped" in lt["capabilities"]["rig"] and lt["capabilities"]["animals_rig"] and "auto" in lt["capabilities"]["rig"]
no_secret(lt)
ok("tripo listing: rig types include quadruped")

n0 = len(LOG)
r = client.post("/api/gen3d", json={"image_mediaId": IMG_ROW["id"], "provider": "tripo", "rig": {"type": "quadruped"}})
assert r.status_code == 409 and r.json()["estimate"]["amount"] is None and "no price table" in r.json()["estimate"]["note"] and len(LOG) == n0
ok("tripo 409 without confirm (cost unknown, stated), no request sent")

# auto provider: quadruped must go to tripo even though meshy has a key
r = client.post("/api/gen3d", json={"prompt": "brown bear", "rig": {"type": "quadruped"}, "dry_run": True})
assert r.json()["provider"] == "tripo"
ok("auto provider routes animal rigs to Tripo")

r = client.post("/api/gen3d", json={"image_mediaId": IMG_ROW["id"], "provider": "tripo", "rig": {"type": "auto"}, "animation": "walk", "confirm_cost": True, "name": "bear"})
assert r.status_code == 200, r.text
j = wait_job(r.json()["id"])
assert j["status"] == "done", j
res = j["result"]
no_secret(res)
assert TP["bodies"]["/v3/generation/image-to-model"] == {"input": "file_abc", "model": "v3.1-20260211"}
assert TP["bodies"]["/v3/animations/rig"] == {"input": "g1", "model": "v2.5-20260210", "rig_type": "quadruped", "out_format": "glb"}
assert TP["bodies"]["/v3/animations/retarget"]["animation"] == "preset:quadruped:walk"
assert res["rigged"] and res["rig_type"] == "quadruped" and res["credits"] == 65 and "walk" in res["animations"]
assert res["rig_import"]["imported"] is False and "not available" in res["rig_import"]["reason"] and "rig" not in res
assert res["preview_media"]["tags"] == ["gen3d", "tripo"]
ok("tripo image->3D->rig-check(auto)->quadruped rig (v2.5)->walk preset; anim import absent handled gracefully")

# text + explicit rig + failure + credits error
TP["polls"].clear()
r = client.post("/api/gen3d", json={"prompt": "a brown bear", "provider": "tripo", "rig": {"type": "quadruped"}, "dry_run": True})
assert "full body" in r.json()["requests"][0]["body"]["prompt"] and r.json()["requests"][2]["body"]["rig_type"] == "quadruped"
TP["mode"] = "fail"
r = client.post("/api/gen3d", json={"prompt": "a brown bear", "provider": "tripo", "rig": {"type": "biped", "spec": "mixamo"}, "confirm_cost": True})
j = wait_job(r.json()["id"])
assert j["status"] == "error" and "rigging failed" in j["error"], j
assert TP["bodies"]["/v3/animations/rig"] == {"input": "g1", "model": "v3.0-20260909", "rig_type": "biped", "spec": "mixamo", "out_format": "glb"}
TP["mode"] = "403"
r = client.post("/api/gen3d", json={"prompt": "a brown bear", "provider": "tripo", "confirm_cost": True})
j = wait_job(r.json()["id"])
assert j["status"] == "error" and "enough credit" in j["error"], j
no_secret(j)
TP["mode"] = "ok"
ok("tripo: text path, biped spec/model selection, task failure + insufficient-credit errors scrubbed")

r = client.get("/api/gen3d/balance", params={"provider": "tripo"})
assert r.json() == {"balance": 1234.5, "frozen": 0, "unit": "credits"}
ok("tripo balance endpoint")

# rig-import helper endpoint (file inside incoming only)
C.TRANSPORT = httpx.MockTransport(meshy_handler)
name = Path(res["file"]).name
r = client.post("/api/gen3d/rig-import", json={"file": name})
j = wait_job(r.json()["id"])
assert j["status"] == "done" and j["result"]["rig"]["id"] == "rig_42", j
assert client.post("/api/gen3d/rig-import", json={"file": "../../etc/passwd"}).status_code in (400, 404)
ok("rig-import helper")

print("\nALL PROVIDER MOCK TESTS PASSED")
