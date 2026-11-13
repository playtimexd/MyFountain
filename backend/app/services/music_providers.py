"""Music provider abstraction: acestep (REST server), procedural (numpy, always available), musicgen (audiocraft, optional)."""
from __future__ import annotations

import importlib.util
import json
import re
import time
import uuid
from pathlib import Path

import httpx

from .. import config
from ..ffutil import run_ffmpeg
from ..ingest import ingest_file
from ..jobs import JobContext
from . import music_procedural as proc

ACESTEP_NOTE = ("Requires the ACE-Step 1.5 acestep-api server (default http://127.0.0.1:8001). "
                "Install: powershell -File D:\\CutLab\\scripts\\setup_acestep.ps1 (about 8-10 GB download); start: D:\\CutLab\\scripts\\start_acestep.ps1")
MUSICGEN_NOTE = "Optional: uv pip install --dry-run audiocraft first (it pulls torch); downloads facebook/musicgen-small (~2 GB) on first use."


def _acestep_up(url: str) -> bool:
    try:
        return httpx.get(url.rstrip("/") + "/health", timeout=1.5).status_code < 500
    except Exception:
        return False


def providers() -> list[dict]:
    s = config.load_settings()
    up = _acestep_up(s["acestep_url"])
    mg = importlib.util.find_spec("audiocraft") is not None
    return [
        {"id": "acestep", "name": "ACE-Step 1.5 (local AI model, best quality)", "available": up,
         "note": f"Connected to {s['acestep_url']}" if up else ACESTEP_NOTE},
        {"id": "procedural", "name": "Procedural (offline, instant)", "available": True,
         "note": "Pure numpy synthesis: builds chords + bass + arpeggio + melody + drums from keywords (mood / genre / BPM / key). No model needed."},
        {"id": "musicgen", "name": "MusicGen (audiocraft, optional)", "available": mg,
         "note": "audiocraft is installed" if mg else MUSICGEN_NOTE},
    ]


def pick_provider(requested: str | None) -> str:
    if requested and requested != "auto":
        return requested
    for p in providers():
        if p["id"] in ("acestep", "musicgen") and p["available"]:
            return p["id"]
    return "procedural"


def _title(prompt: str, info: dict | None = None) -> str:
    base = re.sub(r"\s+", " ", prompt or "").strip()[:24] or (info or {}).get("mood", "BGM")
    return f"AI Music - {base}"


def _wav_to_mp3(wav: Path, mp3: Path, ctx: JobContext | None = None, dur: float | None = None, lo=0.9, hi=0.98) -> None:
    run_ffmpeg(["-i", str(wav), "-c:a", "libmp3lame", "-b:a", "192k", str(mp3)], total_duration=dur, ctx=ctx, progress_scale=(lo, hi))


def run_procedural(ctx: JobContext, req: dict) -> dict:
    dur = int(max(5, min(600, req.get("duration") or 30)))
    ctx.progress(0.02, "Analysing prompt")
    audio, info = proc.generate(req.get("prompt") or "", dur, bpm=req.get("bpm"), mood=req.get("mood"),
                                genre=req.get("genre"), seed=req.get("seed"),
                                progress=lambda p, m: ctx.progress(p, m))
    uid = uuid.uuid4().hex[:8]
    wav = config.CACHE_DIR / f"music_{uid}.wav"
    mp3 = config.MEDIA_DIR / f"bgm_{uid}.mp3"
    proc.write_wav(wav, audio)
    ctx.progress(0.9, "Encoding mp3")
    _wav_to_mp3(wav, mp3, ctx, dur)
    wav.unlink(missing_ok=True)
    row = ingest_file(mp3, origin="music", name=_title(req.get("prompt", ""), info),
                      tags=["AI music", info["mood"], info["genre"], f"{info['bpm']}bpm"], meta={"music": info, "provider": "procedural"})
    return {"media": row, "provider": "procedural", "info": info}


# ---------------------------------------------------------------- ACE-Step -------------------------------

def run_acestep(ctx: JobContext, req: dict) -> dict:
    s = config.load_settings()
    base = s["acestep_url"].rstrip("/")
    if not _acestep_up(base):
        raise RuntimeError("The ACE-Step server is not running. " + ACESTEP_NOTE)
    dur = int(max(10, min(600, req.get("duration") or 30)))
    bits = [req.get("prompt") or "", req.get("genre") or "", req.get("mood") or ""]
    caption = ", ".join(b for b in bits if b) or "background music"
    if req.get("instrumental", True):
        caption += ", instrumental"
    lyrics = req.get("lyrics") or ("[Instrumental]" if req.get("instrumental", True) else "")
    body = {"prompt": caption, "lyrics": lyrics, "audio_duration": dur, "batch_size": 1, "audio_format": "mp3",
            "thinking": False, "inference_steps": 8, "vocal_language": "unknown" if req.get("instrumental", True) else "en"}
    if req.get("bpm"):
        body["bpm"] = int(req["bpm"])
    if req.get("seed") is not None:
        body.update(seed=int(req["seed"]), use_random_seed=False)
    ctx.progress(0.03, "Submitting task to ACE-Step")
    with httpx.Client(timeout=30) as cli:
        r = cli.post(base + "/release_task", json=body)
        r.raise_for_status()
        j = r.json()
        d = j.get("data", j)
        task_id = d.get("task_id") if isinstance(d, dict) else None
        if not task_id:
            raise RuntimeError(f"Unexpected ACE-Step response: {str(j)[:300]}")
        t0 = time.time()
        item = None
        while True:
            ctx.check()
            time.sleep(1.5)
            q = cli.post(base + "/query_result", json={"task_id_list": [task_id]})
            q.raise_for_status()
            data = q.json().get("data", [])
            item = next((x for x in data if x.get("task_id") == task_id), data[0] if data else None)
            if item:
                st = item.get("status")
                if st == 1:
                    break
                if st == 2:
                    raise RuntimeError("ACE-Step generation failed: " + str(item.get("result"))[:300])
            el = time.time() - t0
            ctx.progress(min(0.85, 0.05 + 0.8 * (1 - 1 / (1 + el / (dur * 0.6 + 10)))), f"ACE-Step generating {int(el)}s")
            if el > 1800:
                raise RuntimeError("ACE-Step timed out")
        res = item.get("result")  # type: ignore[union-attr]
        if isinstance(res, str):
            res = json.loads(res)
        first = res[0] if isinstance(res, list) else res
        fpath = first.get("file") or first.get("audio_path") or first.get("path")
        if not fpath:
            raise RuntimeError("ACE-Step result contains no audio file")
        url = fpath if fpath.startswith("http") else base + (fpath if fpath.startswith("/") else "/" + fpath)
        ctx.progress(0.9, "Downloading audio")
        dl = cli.get(url, timeout=120)
        dl.raise_for_status()
    out = config.MEDIA_DIR / f"acestep_{uuid.uuid4().hex[:8]}.mp3"
    out.write_bytes(dl.content)
    metas = first.get("metas", {}) if isinstance(first, dict) else {}
    row = ingest_file(out, origin="music", name=_title(req.get("prompt", "")), tags=["AI music", "ACE-Step"],
                      meta={"provider": "acestep", "metas": metas, "task_id": task_id})
    return {"media": row, "provider": "acestep", "info": metas}


# ---------------------------------------------------------------- MusicGen -------------------------------

def run_musicgen(ctx: JobContext, req: dict) -> dict:
    if importlib.util.find_spec("audiocraft") is None:
        raise RuntimeError("audiocraft is not installed. " + MUSICGEN_NOTE)
    import numpy as np
    from audiocraft.models import MusicGen  # type: ignore

    ctx.progress(0.05, "Loading MusicGen (downloads the model on first use)")
    model = MusicGen.get_pretrained("facebook/musicgen-small")
    dur = int(max(5, min(30, req.get("duration") or 15)))
    model.set_generation_params(duration=dur)
    ctx.progress(0.3, "Generating")
    wav = model.generate([req.get("prompt") or "background music"])[0].cpu().numpy()  # [C, N]
    sr = model.sample_rate
    uid = uuid.uuid4().hex[:8]
    raw = config.CACHE_DIR / f"mg_{uid}.f32"
    mono = wav.mean(0) if wav.ndim > 1 else wav
    raw.write_bytes(np.stack([mono, mono], 1).astype("<f4").tobytes())
    mp3 = config.MEDIA_DIR / f"musicgen_{uid}.mp3"
    run_ffmpeg(["-f", "f32le", "-ar", str(sr), "-ac", "2", "-i", str(raw), "-c:a", "libmp3lame", "-b:a", "192k", str(mp3)])
    raw.unlink(missing_ok=True)
    row = ingest_file(mp3, origin="music", name=_title(req.get("prompt", "")), tags=["AI music", "MusicGen"], meta={"provider": "musicgen"})
    return {"media": row, "provider": "musicgen"}


RUNNERS = {"procedural": run_procedural, "acestep": run_acestep, "musicgen": run_musicgen}


def generate(ctx: JobContext, req: dict) -> dict:
    prov = pick_provider(req.get("provider"))
    if prov not in RUNNERS:
        raise RuntimeError(f"Unknown provider: {prov}")
    try:
        return RUNNERS[prov](ctx, req)
    except Exception as e:  # noqa: BLE001
        if req.get("provider") in (None, "auto") and prov != "procedural" and "cancel" not in type(e).__name__.lower():
            ctx.progress(0.05, f"{prov} failed, falling back to procedural: {e}")
            out = run_procedural(ctx, req)
            out["fallback_reason"] = str(e)
            return out
        raise
