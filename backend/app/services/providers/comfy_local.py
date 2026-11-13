"""CutLab's own ComfyUI (data/comfyui): started on demand, headless, on http://127.0.0.1:8188.

Layout (created by the installer): data/comfyui/ComfyUI (code), data/comfyui/venv (Python 3.12 + PyTorch CUDA),
data/comfyui/models (Wan 2.2 TI2V-5B etc.), extra_model_paths.yaml (also reads Comfy Desktop's shared model folder).
Only used when the ComfyUI URL in Settings is the local default; a user-run ComfyUI on that port is reused as is.
"""
from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import httpx

from ... import config

ROOT = config.DATA / "comfyui"
PY = ROOT / "venv" / "Scripts" / "python.exe"
MAIN = ROOT / "ComfyUI" / "main.py"
LOG = ROOT / "server.log"
PORT = 8188
_lock = threading.Lock()
_proc: dict = {}


def installed() -> bool:
    return PY.is_file() and MAIN.is_file()


def running(timeout: float = 1.5) -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{PORT}/system_stats", timeout=timeout).status_code == 200
    except Exception:  # noqa: BLE001
        return False


def start() -> bool:
    """Launch in the background if installed and not already up. Returns True when a start was triggered or it is running."""
    if running():
        return True
    if not installed():
        return False
    with _lock:
        p = _proc.get("p")
        if p is not None and p.poll() is None:
            return True
        args = [str(PY), str(MAIN), "--listen", "127.0.0.1", "--port", str(PORT), "--base-directory", str(ROOT),
                "--disable-auto-launch"]
        extra = ROOT / "extra_model_paths.yaml"
        if extra.is_file():
            args += ["--extra-model-paths-config", str(extra)]
        log = LOG.open("ab")
        _proc["p"] = subprocess.Popen(args, cwd=str(MAIN.parent), stdout=log, stderr=subprocess.STDOUT,
                                      creationflags=0x08000000 | 0x00000200)   # no window, own process group
        _proc["t"] = time.time()
    return True


def starting() -> bool:
    p = _proc.get("p")
    return p is not None and p.poll() is None and not running()


def ensure_running(wait_s: float = 240.0, progress=None) -> bool:
    if running():
        return True
    if not start():
        return False
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if running():
            return True
        p = _proc.get("p")
        if p is not None and p.poll() is not None:
            return False                                  # crashed: see data/comfyui/server.log
        if progress:
            progress(f"Starting local ComfyUI ({int(time.time() - t0)} s)")
        time.sleep(2)
    return running()


def stop() -> None:
    p = _proc.get("p")
    if p is not None and p.poll() is None:
        p.terminate()
