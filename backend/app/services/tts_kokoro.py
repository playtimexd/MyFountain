"""Kokoro-82M (Apache-2.0) through `kokoro-onnx` (MIT). Weights are NEVER downloaded implicitly: only `prepare()` does that.

Packs (files live in data/models/kokoro/):
  zh  (default)  kokoro-v1.1-zh[.int8|.fp16].onnx + voices-v1.1-zh.bin + config.json   -> Mandarin voices (zf_*/zm_*, via misaki ZHG2P)
                 plus a few English voices (af_maple, af_sol, bf_vale). int8 114 MB / fp16 164 MB / fp32 326 MB model, 54 MB voices.
  en             kokoro-v1.0[.int8|.fp16].onnx + voices-v1.0.bin                      -> English/British/other-language voices (espeak G2P)
Sources: https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1 and
         https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh (config.json).
Python deps (already in the venv): kokoro-onnx, misaki-fork[zh], espeakng-loader, phonemizer, soundfile."""
from __future__ import annotations

import importlib.util
import re
import threading
from pathlib import Path

import httpx
import numpy as np

from .. import config
from ..jobs import JobContext

KDIR = config.MODELS_DIR / "kokoro"
KDIR.mkdir(parents=True, exist_ok=True)
REL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
CONFIG_URL = "https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh/resolve/main/config.json"
SIZES = {"int8": 114_120_125, "fp16": 163_528_759, "fp32": 325_506_167}
VOICES_ZH = ("voices-v1.1-zh.bin", 53_815_880)
VOICES_EN = ("voices-v1.0.bin", 28_214_398)
EN_MODELS = {"int8": ("kokoro-v1.0.int8.onnx", 114_119_327), "fp16": ("kokoro-v1.0.fp16.onnx", 163_527_961), "fp32": ("kokoro-v1.0.onnx", 325_505_369)}

_lock = threading.Lock()
_engines: dict[str, object] = {}
_g2p = None


def pkg_ok() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in ("kokoro_onnx", "espeakng_loader"))


def zh_pkg_ok() -> bool:
    return importlib.util.find_spec("misaki") is not None and importlib.util.find_spec("jieba") is not None


def _zh_files() -> tuple[Path, Path, Path] | None:
    voices, cfg = KDIR / VOICES_ZH[0], KDIR / "config.json"
    if not (voices.exists() and cfg.exists()):
        return None
    for v in ("int8", "fp16", "fp32"):
        suffix = "" if v == "fp32" else f".{v}"
        m = KDIR / f"kokoro-v1.1-zh{suffix}.onnx"
        if m.exists() and m.stat().st_size > 1_000_000:
            return m, voices, cfg
    return None


def _en_files() -> tuple[Path, Path] | None:
    voices = KDIR / VOICES_EN[0]
    if not voices.exists():
        return None
    for v in ("int8", "fp16", "fp32"):
        m = KDIR / EN_MODELS[v][0]
        if m.exists() and m.stat().st_size > 1_000_000:
            return m, voices
    return None


def status() -> dict:
    pk = pkg_ok()
    zh, en = _zh_files(), _en_files()
    if not pk:
        msg = "kokoro-onnx is not installed: uv pip install --dry-run kokoro-onnx misaki-fork[zh] soundfile (then without --dry-run)"
    elif not (zh or en):
        msg = "Kokoro model files must be downloaded first (Mandarin + English pack: model 114 MB + voices 54 MB + config, about 170 MB). Use the Download button in the Voice-over panel or POST /api/tts/prepare."
    elif zh and not zh_pkg_ok():
        msg = "Missing the Chinese G2P dependency misaki-fork[zh]"
    else:
        msg = "Ready" + (" · zh pack " + zh[0].name if zh else "") + (" · en pack " + en[0].name if en else "")
    return {"package": pk, "zh_pack": bool(zh), "en_pack": bool(en), "ready": pk and bool(zh or en), "message": msg}


def ready() -> bool:
    return status()["ready"]


def provider_info() -> dict:
    st = status()
    return {"id": "kokoro", "name": "Kokoro-82M (local neural TTS, English + Mandarin)", "available": st["ready"], "offline": True, "quality": "good",
            "needs_download": st["package"] and not st["ready"], "download": "about 170 MB (int8) / 218 MB (fp16) / 380 MB (fp32)",
            "note": st["message"], "license": "Apache-2.0"}


def _voice_names(path: Path) -> list[str]:
    try:
        with np.load(path) as z:
            return sorted(z.files)
    except Exception:
        return []


def _meta(vid: str) -> dict:
    lang = {"a": "en-US", "b": "en-GB", "z": "zh-CN", "j": "ja-JP", "e": "es-ES", "f": "fr-FR", "h": "hi-IN", "i": "it-IT", "p": "pt-BR"}.get(vid[:1], "")
    gender = "female" if vid[1:2] == "f" else "male" if vid[1:2] == "m" else ""
    return {"lang": lang, "gender": gender}


def voices() -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    zh, en = _zh_files(), _en_files()
    for files, pack in ((zh, "zh"), (en, "en")):
        if not files:
            continue
        for n in _voice_names(files[1]):
            if n in seen or (pack == "en" and n[:1] == "z"):   # zh voices come from the zh pack
                continue
            seen.add(n)
            m = _meta(n)
            out.append({"id": f"kokoro:{n}", "provider": "kokoro", "name": n, "full_name": n, "available": True, **m})
    return out


def default_voice(want_zh: bool) -> str:
    vs = [v["name"] for v in voices()]
    if want_zh:
        for pref in ("zf_001", "zf_xiaoxiao", "zf_xiaobei", "zm_yunxi"):
            if pref in vs:
                return pref
        z = [v for v in vs if v.startswith("z")]
        if z:
            return z[0]
    for pref in ("af_maple", "af_heart", "af_sol", "bf_vale", "af_bella"):
        if pref in vs:
            return pref
    return vs[0] if vs else ""


def _engine(pack: str):
    from kokoro_onnx import Kokoro
    with _lock:
        if pack in _engines:
            return _engines[pack]
        if pack == "zh":
            f = _zh_files()
            if not f:
                raise RuntimeError("The Kokoro zh pack is not downloaded")
            eng = Kokoro(str(f[0]), str(f[1]), vocab_config=str(f[2]))
        else:
            f2 = _en_files()
            if not f2:
                raise RuntimeError("The Kokoro en pack is not downloaded")
            eng = Kokoro(str(f2[0]), str(f2[1]))
        _engines[pack] = eng
        return eng


def _zh_g2p():
    global _g2p
    if _g2p is None:
        from misaki import zh
        _g2p = zh.ZHG2P(version="1.1")
    return _g2p


def synth(ctx: JobContext, chunks: list[tuple[str, str | None]], speed: float, lang: str | None) -> list[np.ndarray]:
    from .tts import SR, resample
    out: list[np.ndarray] = []
    names = {v["name"] for v in voices()}
    for i, (text, voice) in enumerate(chunks):
        ctx.progress(0.05 + 0.8 * i / max(len(chunks), 1), f"Kokoro synthesising {i + 1}/{len(chunks)}")
        v = voice if voice in names else default_voice(bool(re.search(r"[㐀-鿿]", text)))
        is_zh = v.startswith("z")
        pack = "zh" if (_zh_files() and (is_zh or not _en_files())) else "en"
        eng = _engine(pack)
        if is_zh:
            # English words inside Chinese text are dropped by ZHG2P unless an en callable is given -> keep digits/latin as is via espeak fallback
            ph, _ = _zh_g2p()(text)
            samples, sr = eng.create(ph, voice=v, speed=float(speed), is_phonemes=True)  # type: ignore[attr-defined]
        else:
            samples, sr = eng.create(text, voice=v, speed=float(speed), lang="en-gb" if v.startswith("b") else "en-us")  # type: ignore[attr-defined]
        out.append(resample(np.asarray(samples, np.float32), sr, SR))
    return out


# ------------------------------------------------------------------------------------------- prepare (explicit download)
def _download(url: str, dst: Path, expect: int, ctx: JobContext, lo: float, hi: float, label: str) -> None:
    if dst.exists() and dst.stat().st_size >= expect * 0.99:
        ctx.progress(hi, f"{label} already present")
        return
    part = dst.with_suffix(dst.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or expect)
        got = 0
        with part.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                got += len(chunk)
                ctx.progress(lo + (hi - lo) * min(1.0, got / max(total, 1)), f"Downloading {label} {got / 1e6:.0f}/{total / 1e6:.0f} MB")
    part.replace(dst)


def prepare(ctx: JobContext, pack: str = "zh", variant: str = "int8") -> dict:
    """Download the weights for `pack` ('zh' or 'en') in `variant` precision ('int8' 114MB, 'fp16' 164MB, 'fp32' 326MB)."""
    if variant not in SIZES:
        variant = "int8"
    if not pkg_ok():
        raise RuntimeError("kokoro-onnx is not installed; run: uv pip install kokoro-onnx misaki-fork[zh] soundfile")
    if pack == "zh":
        suffix = "" if variant == "fp32" else f".{variant}"
        mname = f"kokoro-v1.1-zh{suffix}.onnx"
        _download(REL + mname, KDIR / mname, SIZES[variant], ctx, 0.0, 0.65, mname)
        _download(REL + VOICES_ZH[0], KDIR / VOICES_ZH[0], VOICES_ZH[1], ctx, 0.65, 0.95, VOICES_ZH[0])
        _download(CONFIG_URL, KDIR / "config.json", 3000, ctx, 0.95, 0.99, "config.json")
    else:
        mname, msz = EN_MODELS[variant]
        _download(REL + mname, KDIR / mname, msz, ctx, 0.0, 0.8, mname)
        _download(REL + VOICES_EN[0], KDIR / VOICES_EN[0], VOICES_EN[1], ctx, 0.8, 0.99, VOICES_EN[0])
    _engines.clear()
    return {"pack": pack, "variant": variant, "dir": str(KDIR), "status": status()}
