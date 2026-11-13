"""Global paths / settings for CutLab. Everything user-visible lives under <ROOT>/data."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # D:\CutLab
DATA = Path(os.environ.get("CUTLAB_DATA", ROOT / "data"))

MEDIA_DIR = DATA / "media"         # downloaded / generated / uploaded files we own
THUMB_DIR = DATA / "thumbs"
CACHE_DIR = DATA / "cache"         # waveforms, proxies, temp
EXPORT_DIR = DATA / "exports"
MODELS_DIR = DATA / "models"       # local model weights (Real-ESRGAN, whisper, ...)
FONTS_DIR = DATA / "fonts"         # user fonts (.ttf/.otf/.ttc)
INDEX_DIR = DATA / "index"         # RAG index (owned by search service)
DB_PATH = DATA / "cutlab.db"

for _d in (MEDIA_DIR, THUMB_DIR, CACHE_DIR, EXPORT_DIR, MODELS_DIR, FONTS_DIR, INDEX_DIR):
    _d.mkdir(parents=True, exist_ok=True)

HOST = os.environ.get("CUTLAB_HOST", "127.0.0.1")
PORT = int(os.environ.get("CUTLAB_PORT", "8787"))


def _find(name: str) -> str | None:
    return shutil.which(name)


FFMPEG = os.environ.get("FFMPEG") or _find("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE") or _find("ffprobe") or "ffprobe"

# --- runtime-tunable settings (data/settings.json) -------------------------------------
_SETTINGS_PATH = DATA / "settings.json"
_DEFAULTS = {
    "ollama_url": "http://127.0.0.1:11434",
    "llm_model": "qwen3:8b",              # agent / planning model
    "embed_model": "nomic-embed-text",    # text embeddings for library RAG
    "acestep_url": "http://127.0.0.1:8001",  # ACE-Step 1.5 REST API (acestep-api)
    "whisper_model": "small",             # faster-whisper size
    "cookies_from_browser": "",           # e.g. "chrome" / "edge" / "firefox" for yt-dlp
    "proxy": "",                          # e.g. http://127.0.0.1:7890
    "pexels_api_key": "",
    "pixabay_api_key": "",
    # --- optional cloud LLM (off by default: local Ollama stays the default, nothing leaves the machine) ---
    "llm_provider": "ollama",             # "ollama" | "anthropic"
    "anthropic_api_key": "",              # entered by the user in Settings; stored only in data/settings.json
    "anthropic_model": "claude-opus-5-5", # used for agent planning, animation QA (frame review), bone mapping
    "blender_exe": "",                    # auto-detected when empty (C:\\Program Files\\Blender Foundation\\Blender *\\blender.exe)
    # --- optional reference-video generation (source material for motion capture) ---
    "videogen_provider": "auto",          # "auto" | "seedance" | "local"
    "seedance_api_key": "",               # entered by the user in Settings
    "seedance_base_url": "",              # filled in once the official endpoint is verified (BytePlus ModelArk / Volcengine Ark)
    "seedance_model": "",                 # model/endpoint id
    "comfyui_url": "http://127.0.0.1:8188",  # local video models (Wan / LTX …) are driven through ComfyUI's API
    # --- optional cloud 3D character generation (text/image -> rigged model). Spends the user's credits. ---
    "gen3d_provider": "auto",             # "auto" | "meshy" | "tripo"
    "meshy_api_key": "",
    "tripo_api_key": "",
}

SECRET_KEYS = {"anthropic_api_key", "pexels_api_key", "pixabay_api_key", "seedance_api_key", "meshy_api_key", "tripo_api_key"}
MASK = "••••••••"


def public_settings() -> dict:
    """Settings for the UI: secrets are never sent back, only whether they are set."""
    s = load_settings()
    return {k: (MASK if (k in SECRET_KEYS and v) else v) for k, v in s.items()}


def load_settings() -> dict:
    s = dict(_DEFAULTS)
    if _SETTINGS_PATH.exists():
        try:
            s.update(json.loads(_SETTINGS_PATH.read_text("utf-8")))
        except Exception:
            pass
    return s


def save_settings(patch: dict) -> dict:
    s = load_settings()
    # a masked placeholder coming back from the UI means "unchanged"
    s.update({k: v for k, v in patch.items() if k in _DEFAULTS and v != MASK})
    _SETTINGS_PATH.write_text(json.dumps(s, indent=2, ensure_ascii=False), "utf-8")
    return s
