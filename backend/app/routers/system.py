"""Health, capability detection (what local engines/models are available) and settings."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from .. import config
from ..ffutil import _NOWIN, encoders, has_nvenc

router = APIRouter(prefix="/api", tags=["system"])


def _mod(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _gpu() -> dict:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5, **_NOWIN).stdout.strip().splitlines()[0]
        n, total, used = [x.strip() for x in out.split(",")]
        return {"name": n, "vram_total_mb": int(total), "vram_used_mb": int(used)}
    except Exception:
        return {}


def _ollama_models(url: str) -> list[str] | None:
    try:
        r = httpx.get(f"{url}/api/tags", timeout=2)
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return None


def _up(url: str, path: str = "/health") -> bool:
    try:
        return httpx.get(url + path, timeout=2).status_code < 500
    except Exception:
        return False


@router.get("/health")
def health():
    from ..main import FAILED, LOADED
    # "app"/"data"/"pid" let the desktop launcher recognise (and, for updates, restart) an engine it did not start itself
    return {"ok": True, "app": "cutlab", "data": str(config.DATA), "pid": os.getpid(), "routers": LOADED, "failed": FAILED}


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(config.ROOT), *args], capture_output=True, text=True, timeout=5,
                              **_NOWIN).stdout.strip()
    except Exception:
        return ""


@router.get("/app/info")
def app_info():
    """Version + where things live. "desktop" is set when running inside the CutLab desktop app (desktop/cutlab_desktop.py):
    its control server opens the update window."""
    url = os.environ.get("CUTLAB_DESKTOP_URL", "")
    if not url:      # an engine started outside the app, then attached to by the desktop launcher
        try:
            lk = json.loads((config.desktop_config_path().parent / "launcher.json").read_text("utf-8"))
            if lk.get("url") and Path(lk.get("root", "")).resolve() == config.ROOT and _up(lk["url"], "/api/state"):
                url = lk["url"]
        except Exception:
            pass
    return {"version": _git("describe", "--always", "--dirty"), "commit": _git("rev-parse", "HEAD"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"), "root": str(config.ROOT), "data": str(config.DATA),
            "desktop": {"url": url} if url else None}


@router.get("/capabilities")
def capabilities():
    s = config.load_settings()
    ollama = _ollama_models(s["ollama_url"])
    models = config.MODELS_DIR
    return {
        "gpu": _gpu(),
        "ffmpeg": {"path": shutil.which(config.FFMPEG) or config.FFMPEG, "nvenc": has_nvenc(),
                   "libx265": "libx265" in encoders()},
        "download": {"yt_dlp": _mod("yt_dlp")},
        "segment": {"scenedetect": _mod("scenedetect"), "faster_whisper": _mod("faster_whisper")},
        "llm": {"ollama": ollama is not None, "models": ollama or [], "model": s["llm_model"],
                "embed_model": s["embed_model"]},
        "music": {"acestep_api": _up(s["acestep_url"]), "acestep_url": s["acestep_url"],
                  "audiocraft": _mod("audiocraft"), "procedural": True},
        "upscale": {"realesrgan_ncnn": bool(list(models.glob("**/realesrgan-ncnn-vulkan*"))) or bool(shutil.which("realesrgan-ncnn-vulkan")),
                    "realesrgan_py": _mod("realesrgan"), "ffmpeg_lanczos": True},
        "inpaint": {"opencv": _mod("cv2"), "lama": _mod("simple_lama_inpainting")},
        "matting": {"rembg": _mod("rembg")},
        "search": {"open_clip": _mod("open_clip"), "text_embed": ollama is not None},
        "blender": _blender(s),
        "cloud": {"llm_provider": s["llm_provider"], "anthropic_key_set": bool(s["anthropic_api_key"]),
                  "anthropic_model": s["anthropic_model"], "seedance_key_set": bool(s["seedance_api_key"]),
                  "meshy_key_set": bool(s["meshy_api_key"]), "tripo_key_set": bool(s["tripo_api_key"]),
                  "comfyui": _up(s["comfyui_url"], "/system_stats")},
        "settings": config.public_settings(),     # secrets are masked
    }


def _blender(s: dict) -> dict:
    import glob
    exe = s.get("blender_exe") or next(iter(sorted(glob.glob(r"C:\Program Files\Blender Foundation\Blender *\blender.exe"), reverse=True)), "")
    return {"available": bool(exe) and __import__("os").path.exists(exe), "exe": exe}


class SettingsPatch(BaseModel):
    values: dict


@router.get("/settings")
def get_settings():
    return config.public_settings()           # API keys come back masked


@router.put("/settings")
def put_settings(p: SettingsPatch):
    config.save_settings(p.values)            # a masked value from the UI means "unchanged"
    return config.public_settings()
