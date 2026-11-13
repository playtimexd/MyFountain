"""End-to-end test of /api/mocap with the dev-only 'mock' 2D-pose backend (no weights needed).

Usage:  backend\\.venv\\Scripts\\python.exe backend\\tests\\mocap_e2e.py [--spawn] [--base http://127.0.0.1:8803]
--spawn starts a private engine on :8803 with CUTLAB_MOCAP_DEV=1 and an isolated data dir, and stops it afterwards.
Pipeline verified: synthetic video in -> job -> motion.npz + overlay video out (+ download endpoint, error paths).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import cv2
import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.mocap import metrics as M, motion_io, pose2d, synthetic as S      # noqa: E402
from app.services.mocap.skeletons import draw_edges, get_skeleton                    # noqa: E402

PY = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
OK, FAIL = [], []


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (f"   [{extra}]" if extra else ""), flush=True)


def make_video(path: Path, subject: str, fps=25.0, frames=120, seed=1, size=(1280, 720)):
    gt = S.walk_sequence(subject, n_frames=frames, fps=fps, heading_deg=0.0 if subject == "quadruped" else 180.0, seed=seed)
    kp, conf, clean = S.simulate_2d(gt["joints3d"], scale=330, pitch_deg=2.0, noise_px=2.5, drop_rate=0.06, seed=seed,
                                    image_size=size, skeleton=gt["skeleton"])
    S.render_stick_video(path, clean, gt["skeleton"], fps, size)
    pose2d.save_mock_sidecar(path, kp, conf, fps, gt["skeleton"])
    return gt, kp, conf


def wait(cli, job, label, timeout=600):
    t0 = time.time()
    while job["status"] in ("queued", "running"):
        if time.time() - t0 > timeout:
            raise TimeoutError(label)
        time.sleep(0.5)
        job = cli.get(f"/api/jobs/{job['id']}").json()
    return job


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spawn", action="store_true")
    ap.add_argument("--base", default=os.environ.get("CUTLAB_BASE", "http://127.0.0.1:8803"))
    a = ap.parse_args()
    data = ROOT / "data" / "test" / "mocap_e2e"
    data.mkdir(parents=True, exist_ok=True)
    srv = None
    if a.spawn:
        env = dict(os.environ, CUTLAB_PORT=a.base.rsplit(":", 1)[1], CUTLAB_MOCAP_DEV="1", CUTLAB_DATA=str(data / "data"))
        srv = subprocess.Popen([str(PY), "-m", "app"], cwd=str(ROOT / "backend"), env=env,
                               stdout=open(data / "server.log", "w"), stderr=subprocess.STDOUT)
    cli = httpx.Client(base_url=a.base, timeout=600)
    try:
        for _ in range(80):
            try:
                if cli.get("/api/health").status_code == 200:
                    break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("engine did not start")
        print("engine up")
        bs = cli.get("/api/mocap/backends").json()
        ids = {b["id"]: b for b in bs}
        print("backends:", {k: v["available"] for k, v in ids.items()})
        check("backends listing has vitpose/gem/sam3d/mock", {"vitpose", "gem", "sam3d", "mock"} <= set(ids))
        check("backends carry size/license/subjects", all({"id", "name", "available", "note", "size", "license_note", "subjects"} <= set(b) for b in bs))
        check("vitpose reports missing weights (not downloaded)", ids["vitpose"]["available"] is False and "prepare" in ids["vitpose"]["note"].lower())
        check("gem/sam3d report how-to without being installed", not ids["gem"]["available"] and "gem-x" in ids["gem"]["note"].lower())

        for subject in ("quadruped", "humanoid"):
            vid = data / f"synthetic_{subject}.mp4"
            gt, kp, conf = make_video(vid, subject)
            row = cli.post("/api/media/import", json={"paths": [str(vid)], "recursive": False, "copy": False}).json()
            job = wait(cli, row, "import")
            media = job["result"]["items"][0]
            job = wait(cli, cli.post("/api/mocap", json={"mediaId": media["id"], "subject": subject, "backend": "mock",
                                                         "fps": 25}).json(), "mocap")
            check(f"[{subject}] job done", job["status"] == "done", str(job.get("error")))
            if job["status"] != "done":
                continue
            r = job["result"]
            m = motion_io.load_motion(r["motion_file"])
            check(f"[{subject}] motion.npz valid", not motion_io.validate_motion(m), str(motion_io.validate_motion(m)))
            T, J, _ = m["joints3d"].shape
            check(f"[{subject}] shape (T,17,3) and names", J == 17 and T >= 100 and m["joint_names"] == get_skeleton(gt["skeleton"]).out_names, f"T={T}")
            check(f"[{subject}] skeleton name", m["skeleton"] == gt["skeleton"])
            check(f"[{subject}] Y-up: head above feet, ground ~0", m["joints3d"][..., 1].min() > -0.08 and m["joints3d"][..., 1].max() > 0.7,
                  f"y[{m['joints3d'][..., 1].min():.2f},{m['joints3d'][..., 1].max():.2f}]")
            n = min(T, len(gt["joints3d"]))
            sk = get_skeleton(gt["skeleton"])
            pa = M.pa_mpjpe_seq(m["joints3d"][:n] - m["joints3d"][:n].mean(1, keepdims=True),
                                gt["joints3d"][:n] - gt["joints3d"][:n].mean(1, keepdims=True)) / sk.size_ref
            check(f"[{subject}] PA-MPJPE small vs truth", pa < 0.10, f"{pa * 100:.1f}% of body size")
            gait = m["meta"]["gait"]
            true_period = 1.0 / gt["stride_hz"]
            check(f"[{subject}] gait period detected", gait["period_s"] is not None and abs(gait["period_s"] - true_period) < 0.12 * true_period,
                  f"{gait['period_s']} vs {true_period:.2f}")
            check(f"[{subject}] loop points reported", gait["loop_start"] is not None and gait["loop_cycles"] >= 1, str({k: gait[k] for k in ('loop_start', 'loop_end', 'loop_cycles', 'loop_error')}))
            st = r["stats"]
            check(f"[{subject}] stats present", all(k in st for k in ("frames", "fps", "mean_conf", "jitter", "coverage")), str({k: round(v, 3) if isinstance(v, float) else v for k, v in st.items()}))
            ov = r["overlay_media"]
            check(f"[{subject}] overlay video ingested", ov and ov["kind"] == "video" and ov["duration"] > 3, str(ov and (ov["width"], ov["height"], ov["duration"])))
            if ov:
                f = cli.get(ov["url"])
                check(f"[{subject}] overlay downloadable", f.status_code in (200, 206) and len(f.content) > 10_000)
            d = cli.get(r["motion_url"])
            check(f"[{subject}] motion download endpoint", d.status_code == 200 and d.content[:2] == b"PK")
            js = cli.get(r["motion_url"].replace(".npz", ".json"))
            check(f"[{subject}] json summary endpoint", js.status_code == 200 and js.json()["skeleton"] == gt["skeleton"])
            check(f"[{subject}] skeleton edges in result", len(r["skeleton"]["edges"]) >= 14)
            # sub-range
            job2 = wait(cli, cli.post("/api/mocap", json={"mediaId": media["id"], "subject": subject, "backend": "mock", "start": 0.8, "end": 3.2, "fps": 25}).json(), "range")
            check(f"[{subject}] start/end range", job2["status"] == "done" and 55 <= job2["result"]["stats"]["frames"] <= 62, str(job2.get("error") or job2["result"]["stats"]["frames"]))

        # error paths
        r = cli.post("/api/mocap", json={"mediaId": "nope", "backend": "mock"})
        check("unknown media -> 404", r.status_code == 404)
        r = cli.post("/api/mocap", json={"mediaId": media["id"], "subject": "dragon"})
        check("bad subject -> 400", r.status_code == 400)
        r = cli.post("/api/mocap", json={"mediaId": media["id"], "backend": "gem", "subject": "quadruped"})
        check("gem + quadruped -> 400", r.status_code == 400)
        j = wait(cli, cli.post("/api/mocap", json={"mediaId": media["id"], "backend": "vitpose", "subject": "quadruped"}).json(), "noweights")
        check("vitpose without weights -> clear job error", j["status"] == "error" and "needs weights" in j["error"] and "Run prepare" in j["error"], str(j.get("error")))
        j = wait(cli, cli.post("/api/mocap", json={"mediaId": media["id"], "backend": "gem", "subject": "humanoid"}).json(), "gem")
        check("gem without install -> clear job error", j["status"] == "error" and "needs weights" in j["error"], str(j.get("error"))[:120])
        rt = cli.get("/api/mocap/motion/..%2f..%2fcutlab.db")
        check("path traversal rejected", not rt.content.startswith(b"SQLite format"), str(rt.status_code))
        rt = cli.get("/api/mocap/motion/..%5c..%5ccutlab.db")
        check("path traversal (backslash) rejected", not rt.content.startswith(b"SQLite format"), str(rt.status_code))
        check("unknown motion -> 404", cli.get("/api/mocap/motion/motion_zzzz.npz").status_code == 404)
    finally:
        if srv:
            srv.terminate()
            try:
                srv.wait(10)
            except Exception:
                srv.kill()
    print(f"\n{len(OK)} passed, {len(FAIL)} failed", "" if not FAIL else FAIL)
    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    main()
