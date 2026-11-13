"""Speech recognition (faster-whisper). Never downloads weights implicitly: `ensure_ready()` raises NeedModel
with a clear Chinese message; `prepare()` downloads explicitly (POST /api/ai/subtitles/prepare)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Callable

import httpx

from .. import config

WHISPER_DIR = config.MODELS_DIR / "whisper"
WHISPER_DIR.mkdir(parents=True, exist_ok=True)
SIZES = {"tiny": "~75MB", "base": "~145MB", "small": "~484MB", "medium": "~1.5GB", "large-v3": "~3GB",
         "large-v2": "~3GB", "turbo": "~1.6GB", "large-v3-turbo": "~1.6GB", "distil-large-v3": "~1.5GB"}


class NeedModel(RuntimeError):
    pass


def model_name() -> str:
    return str(config.load_settings().get("whisper_model") or "small")


def package_ok() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def _repo(name: str) -> str:
    from faster_whisper.utils import _MODELS
    return _MODELS.get(name, name)


def local_path(name: str | None = None) -> Path | None:
    """Return the cached model dir if the weights are already on disk (does not touch the network)."""
    name = name or model_name()
    p = Path(name)
    if p.is_dir() and (p / "model.bin").exists():
        return p
    if not package_ok():
        return None
    # faster-whisper only fetches the runtime files (no README/.gitattributes), which huggingface_hub>=1.x
    # reports as an "incomplete snapshot" — so look for the weights in the HF cache layout ourselves.
    repo_dir = "models--" + _repo(name).replace("/", "--")
    roots = [WHISPER_DIR]
    try:
        from huggingface_hub import constants
        roots.append(Path(constants.HF_HUB_CACHE))
    except Exception:
        pass
    for root in roots:
        snaps = root / repo_dir / "snapshots"
        if snaps.is_dir():
            for d in sorted(snaps.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if (d / "model.bin").exists() and (d / "config.json").exists():
                    return d
    return None


def status() -> dict:
    name = model_name()
    pk = package_ok()
    cached = bool(pk and local_path(name))
    size = SIZES.get(name, "")
    if not pk:
        msg = "faster-whisper is not installed: uv pip install faster-whisper"
    elif not cached:
        msg = f'Whisper model "{name}" ({size}) must be downloaded first'
    else:
        msg = "Ready"
    return {"package": pk, "model": name, "size": size, "cached": cached, "ready": pk and cached, "message": msg}


def ensure_ready() -> Path:
    st = status()
    if not st["package"]:
        raise NeedModel(st["message"])
    if not st["cached"]:
        raise NeedModel(st["message"])
    return local_path()  # type: ignore[return-value]


def prepare(ctx=None) -> dict:
    """Explicit download of the configured model into data/models/whisper."""
    if not package_ok():
        raise RuntimeError("faster-whisper is not installed")
    from faster_whisper.utils import download_model
    name = model_name()
    if ctx:
        ctx.progress(0.05, f"Downloading Whisper model {name} {SIZES.get(name, '')} ...")
    path = download_model(name, cache_dir=str(WHISPER_DIR))
    if ctx:
        ctx.progress(1.0, "Done")
    return {"model": name, "path": str(path)}


_MODEL = {}


def _load(path: Path):
    key = str(path)
    if key in _MODEL:
        return _MODEL[key]
    from faster_whisper import WhisperModel
    try:
        import ctranslate2
        use_gpu = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        use_gpu = False
    m = None
    if use_gpu:
        try:
            m = WhisperModel(key, device="cuda", compute_type="int8_float16")
            # force a tiny warm-up to surface missing cublas dlls early
        except Exception:
            m = None
    if m is None:
        m = WhisperModel(key, device="cpu", compute_type="int8", cpu_threads=0)
    _MODEL.clear()
    _MODEL[key] = m
    return m


def transcribe(path: str | Path, *, language: str | None = None, words: bool = False,
               ctx=None, duration: float = 0) -> dict:
    """Return {language, segments:[{start,end,text,words?:[{start,end,word}]}]}."""
    mp = ensure_ready()
    model = _load(mp)
    lang = None if (not language or language == "auto") else language
    try:
        segs_iter, info = model.transcribe(str(path), language=lang, vad_filter=True, word_timestamps=words,
                                           beam_size=1 if not words else 3)
        out = []
        for s in segs_iter:
            seg = {"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip()}
            if words and s.words:
                seg["words"] = [{"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word.strip()} for w in s.words]
            out.append(seg)
            if ctx and (duration or info.duration):
                ctx.progress(min(0.95, s.end / (duration or info.duration)), f"Transcribing {int(s.end)}s")
    except Exception as e:  # noqa: BLE001 - e.g. cuda dll missing -> retry on cpu once
        if "cublas" in str(e).lower() or "cuda" in str(e).lower() or "cudnn" in str(e).lower():
            from faster_whisper import WhisperModel
            m = WhisperModel(str(mp), device="cpu", compute_type="int8")
            _MODEL.clear(); _MODEL[str(mp)] = m
            return transcribe(path, language=language, words=words, ctx=ctx, duration=duration)
        raise
    return {"language": info.language, "segments": out}


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(segments: list[dict]) -> str:
    lines = []
    for i, s in enumerate(segments, 1):
        lines += [str(i), f"{srt_time(s['start'])} --> {srt_time(s['end'])}", s["text"], ""]
    return "\n".join(lines)


def translate_segments(segments: list[dict], target: str, ctx=None) -> list[dict]:
    """LLM translation through Ollama (batches of 20 lines, JSON array in/out)."""
    s = config.load_settings()
    out = [dict(x) for x in segments]
    names = {"zh": "Simplified Chinese", "en": "English", "ja": "日本語", "ko": "한국어", "fr": "Français", "es": "Español", "de": "Deutsch", "ru": "Русский"}
    tname = names.get(target, target)
    B = 20
    for i in range(0, len(out), B):
        chunk = out[i:i + B]
        prompt = (f"Translate every subtitle line in the JSON array below into {tname}. Keep the array length and order unchanged. Return only a JSON array of strings, no explanation.\n"
                  + json.dumps([c["text"] for c in chunk], ensure_ascii=False))
        try:
            r = httpx.post(s["ollama_url"] + "/api/chat", timeout=180, json={
                "model": s["llm_model"], "stream": False, "think": False,
                "messages": [{"role": "user", "content": prompt}], "options": {"temperature": 0.2}})
            txt = r.json()["message"]["content"]
            a = txt[txt.index("["): txt.rindex("]") + 1]
            arr = json.loads(a)
            if len(arr) == len(chunk):
                for c, t in zip(chunk, arr):
                    c["text"] = str(t)
        except Exception:
            pass
        if ctx:
            ctx.progress(0.9 + 0.08 * min(1, (i + B) / len(out)), "Translating subtitles")
    return out
