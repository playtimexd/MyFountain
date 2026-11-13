"""Headless Blender runner:  blender -b --factory-startup -P <script> -- <args.json>

Scripts live in backend/blender/.  They print `PROGRESS <0..1> <msg>` lines (-> job progress), `CUTLAB_ERROR <msg>` on failure
and write their result JSON to args["result"].  Cancelling the job kills the Blender process.

Verified on Blender 5.2.0 LTS (see docs/ANIMATION.md addendum / the anim-core report):
  glTF export+import, FBX export+import (both importers), BVH import+export, Workbench and EEVEE renders with a transparent
  background all work in background mode.
"""
from __future__ import annotations

import functools
import glob
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from ... import config
from ...jobs import JobContext

SCRIPTS = Path(__file__).resolve().parents[3] / "blender"
TMP = config.CACHE_DIR / "anim_tmp"
TMP.mkdir(parents=True, exist_ok=True)
_NOWIN = {"creationflags": 0x08000000} if sys.platform == "win32" else {}


class BlenderError(RuntimeError):
    pass


def find_exe() -> str | None:
    exe = (config.load_settings().get("blender_exe") or "").strip()
    if exe and os.path.exists(exe):
        return exe
    cands = sorted(glob.glob(r"C:\Program Files\Blender Foundation\Blender *\blender.exe"), reverse=True)
    cands += sorted(glob.glob(r"C:\Program Files (x86)\Blender Foundation\Blender *\blender.exe"), reverse=True)
    for c in cands:
        if os.path.exists(c):
            return c
    import shutil
    return shutil.which("blender")


@functools.lru_cache(maxsize=4)
def version(exe: str) -> str:
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30, **_NOWIN).stdout
        m = re.search(r"Blender\s+([\d.]+(?:\s+LTS)?)", out)
        return m.group(1) if m else out.strip().splitlines()[0]
    except Exception as e:  # noqa: BLE001
        return f"unknown ({e})"


def status() -> dict:
    exe = find_exe()
    return {"available": bool(exe), "exe": exe or "", "version": version(exe) if exe else "",
            "note": "" if exe else "Blender not found. Install Blender 4.2+ or set 'blender_exe' in Settings."}


def run_script(script: str, args: dict, ctx: JobContext | None = None, progress: tuple[float, float] = (0.0, 1.0),
               timeout: float = 3600) -> dict:
    """Run backend/blender/<script> with `args`; returns the result JSON the script wrote ({} if none)."""
    exe = find_exe()
    if not exe:
        raise BlenderError("Blender was not found. Install it (https://www.blender.org) or set 'blender_exe' in Settings.")
    path = SCRIPTS / script
    if not path.exists():
        raise BlenderError(f"Blender script missing: {path}")
    run_id = uuid.uuid4().hex[:10]
    args = dict(args)
    res_path = TMP / f"{run_id}.result.json"
    args.setdefault("result", str(res_path))
    arg_path = TMP / f"{run_id}.args.json"
    arg_path.write_text(json.dumps(args, ensure_ascii=False), "utf-8")
    cmd = [exe, "-b", "--factory-startup", "--python-exit-code", "3", "-P", str(path), "--", str(arg_path)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                            bufsize=1, **_NOWIN)
    if ctx:
        ctx.register_process(proc)
    lo, hi = progress
    tail: list[str] = []
    err_line = [""]
    timer = threading.Timer(timeout, lambda: proc.kill())
    timer.start()
    try:
        for line in proc.stdout:                                    # type: ignore[union-attr]
            line = line.rstrip()
            if line.startswith("PROGRESS "):
                parts = line.split(" ", 2)
                try:
                    p = float(parts[1])
                except (IndexError, ValueError):
                    continue
                if ctx:
                    ctx.progress(lo + (hi - lo) * p, (parts[2] if len(parts) > 2 else None))
            elif line.startswith("CUTLAB_ERROR "):
                err_line[0] = line[len("CUTLAB_ERROR "):]
            elif line.strip():
                tail.append(line)
                tail[:] = tail[-40:]
            if ctx and ctx.cancelled:
                proc.kill()
        proc.wait()
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
    if ctx:
        ctx.check()                                                 # raises JobCancelled when cancelled
    try:
        if proc.returncode != 0 or err_line[0]:
            msg = err_line[0] or next((l for l in reversed(tail) if "Error" in l or "error" in l), "") or (tail[-1] if tail else f"exit {proc.returncode}")
            raise BlenderError(f"Blender script {script} failed: {msg}")
        if res_path.exists():
            return json.loads(res_path.read_text("utf-8"))
        return {}
    finally:
        for p in (arg_path, res_path):
            try:
                p.unlink()
            except OSError:
                pass
