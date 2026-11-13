"""Text-to-speech core: provider abstraction (sapi = Windows System.Speech, always available on Windows; kokoro = Kokoro-82M
via kokoro-onnx; cosyvoice / f5tts = documented stubs), sentence splitting, concat, mp3 encoding, media ingest (origin 'tts')."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import numpy as np

from .. import config
from ..ffutil import _NOWIN, run_ffmpeg
from ..ingest import ingest_file
from ..jobs import JobContext

SR = 24000           # working sample rate for concatenation
PS = shutil.which("powershell.exe") or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

STUBS = {
    "cosyvoice": ("CosyVoice 2 (Alibaba, Mandarin / dialects / emotion, optional)",
                  "Not integrated yet. git clone https://github.com/FunAudioLLM/CosyVoice and download iic/CosyVoice2-0.5B (~5 GB); needs its own Python environment. No adapter yet."),
    "f5tts": ("F5-TTS (voice cloning, optional)",
              "Not integrated yet. pip install f5-tts (use --dry-run first: it touches torch); model SWivid/F5-TTS (~1.3 GB); needs a reference recording. No adapter yet."),
}


# ------------------------------------------------------------------------------------------- text helpers
_SENT = re.compile(r"(?<=[。！？!?；;…])\s*|\n+|(?<=[.])\s+(?=[A-Z\u4e00-\u9fff])")


def split_text(text: str, max_len: int = 110) -> list[str]:
    """Split into sentence-sized chunks (<= max_len chars, broken at commas / spaces when a sentence is longer)."""
    out: list[str] = []
    for s in _SENT.split(text or ""):
        s = (s or "").strip()
        if not s:
            continue
        while len(s) > max_len:
            cut = max(s.rfind(c, 0, max_len) for c in "，,、：: ")
            if cut < max_len // 3:
                cut = max_len - 1
            out.append(s[: cut + 1].strip())
            s = s[cut + 1:].strip()
        if s:
            out.append(s)
    return out


def has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", text or ""))


def guess_lang(text: str) -> str:
    cjk = len(re.findall(r"[\u3400-\u9fff]", text or ""))
    return "zh" if cjk >= max(1, len(re.findall(r"[A-Za-z]", text or "")) // 3) else "en"


# ------------------------------------------------------------------------------------------- audio helpers
def load_audio(path: str | Path, sr: int = SR) -> np.ndarray:
    p = subprocess.run([config.FFMPEG, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True, **_NOWIN)
    if not p.stdout:
        raise RuntimeError("Cannot decode the synthesized audio: " + p.stderr.decode("utf-8", "replace")[-200:])
    return np.frombuffer(p.stdout, "<f4").copy()


def resample(x: np.ndarray, sr_in: int, sr_out: int = SR) -> np.ndarray:
    if sr_in == sr_out:
        return x.astype(np.float32)
    n = int(round(len(x) * sr_out / sr_in))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def concat(parts: list[np.ndarray], pause: float = 0.12) -> np.ndarray:
    if not parts:
        return np.zeros(int(SR * 0.2), np.float32)
    gap = np.zeros(int(SR * pause), np.float32)
    out: list[np.ndarray] = []
    for i, p in enumerate(parts):
        if i:
            out.append(gap)
        out.append(p.astype(np.float32))
    return np.concatenate(out)


def trim_silence(x: np.ndarray, thr: float = 0.004, keep: float = 0.05) -> np.ndarray:
    idx = np.where(np.abs(x) > thr)[0]
    if idx.size == 0:
        return x
    k = int(SR * keep)
    return x[max(0, idx[0] - k): idx[-1] + k]


def encode_mp3(audio: np.ndarray, out: Path, speed: float = 1.0, max_duration: float | None = None, ctx: JobContext | None = None) -> float:
    """float32 mono @SR -> mp3. `speed` is applied with atempo (0.5-2); `max_duration` speeds up (<=1.6x) to fit a subtitle slot."""
    dur = len(audio) / SR / max(speed, 0.01)
    if max_duration and dur > max_duration:
        extra = min(1.6, dur / max_duration)
        speed *= extra
        dur /= extra
    af = []
    s = float(np.clip(speed, 0.5, 2.0 if not max_duration else 3.2))
    while s > 2.0:
        af.append("atempo=2.0"); s /= 2.0
    if abs(s - 1.0) > 0.01:
        af.append(f"atempo={s:.4f}")
    # gentle loudness normalisation so every voice has a similar level
    af.append("loudnorm=I=-18:TP=-2:LRA=11")
    raw = config.CACHE_DIR / f"tts_{uuid.uuid4().hex[:8]}.f32"
    raw.write_bytes(audio.astype("<f4").tobytes())
    try:
        run_ffmpeg(["-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", str(raw), "-af", ",".join(af), "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "128k", str(out)])
    finally:
        raw.unlink(missing_ok=True)
    return dur


# ------------------------------------------------------------------------------------------- SAPI
_voices_cache: list[dict] | None = None
_voices_lock = threading.Lock()

_LIST_PS = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$r = @($s.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { $v = $_.VoiceInfo; [pscustomobject]@{ name = $v.Name; lang = $v.Culture.Name; gender = [string]$v.Gender; age = [string]$v.Age } })
ConvertTo-Json -InputObject $r -Compress
"""

_SPEAK_PS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$jobs = ConvertFrom-Json (Get-Content -Raw -Encoding UTF8 $env:CUTLAB_TTS_JOBS)
if ($jobs -isnot [array]) { $jobs = @($jobs) }
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$cur = ''
foreach ($j in $jobs) {
  if ($j.voice -and $j.voice -ne $cur) { $s.SelectVoice($j.voice); $cur = $j.voice }
  $s.Volume = 100
  $s.SetOutputToWaveFile($j.out)
  $s.Speak($j.text)
  $s.SetOutputToNull()
}
$s.Dispose()
"""


def _ps(script: str, env: dict | None = None, timeout: float = 60) -> subprocess.CompletedProcess:
    import os
    e = {**os.environ, **(env or {})}
    return subprocess.run([PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=e, **_NOWIN)


def sapi_voices(refresh: bool = False) -> list[dict]:
    global _voices_cache
    with _voices_lock:
        if _voices_cache is not None and not refresh:
            return _voices_cache
        if sys.platform != "win32":
            _voices_cache = []
            return _voices_cache
        try:
            r = _ps(_LIST_PS)
            j = json.loads(r.stdout.strip() or "[]")
            if isinstance(j, dict):
                j = [j]
        except Exception:
            j = []
        _voices_cache = [{"id": f"sapi:{v['name']}", "provider": "sapi", "name": v["name"].replace("Microsoft ", "").replace(" Desktop", ""),
                          "lang": v["lang"], "gender": v["gender"].lower(), "full_name": v["name"]} for v in j]
        return _voices_cache


def sapi_synth(items: list[tuple[str, str]], voice: str | None, ctx: JobContext | None = None, lo: float = 0.05, hi: float = 0.85) -> list[np.ndarray]:
    """items: [(text, voice_full_name or None)] -> mono float32 @SR arrays (one PowerShell process for all)."""
    work = config.CACHE_DIR / f"tts_{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True)
    try:
        jobs = [{"text": t, "voice": v or voice or "", "out": str(work / f"{i:05d}.wav")} for i, (t, v) in enumerate(items)]
        jf = work / "jobs.json"
        jf.write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8-sig")
        import os
        proc = subprocess.Popen([PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _SPEAK_PS],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                env={**os.environ, "CUTLAB_TTS_JOBS": str(jf)}, **_NOWIN)
        if ctx:
            ctx.register_process(proc)
        errbuf: list[str] = []
        threading.Thread(target=lambda: errbuf.append(proc.stderr.read()), daemon=True).start()  # type: ignore[union-attr]
        t0 = time.time()
        while proc.poll() is None:
            time.sleep(0.25)
            if ctx:
                n = len(list(work.glob("*.wav")))
                ctx.progress(lo + (hi - lo) * min(1.0, n / max(len(jobs), 1)), f"System voice synthesis {min(n, len(jobs))}/{len(jobs)}")
            if time.time() - t0 > 900:
                proc.kill()
                raise RuntimeError("System voice synthesis timed out")
        if ctx:
            ctx.check()
        if proc.returncode != 0:
            time.sleep(0.2)
            raise RuntimeError("System voice synthesis failed: " + ("".join(errbuf).strip()[-300:] or f"exit {proc.returncode}"))
        return [trim_silence(load_audio(j["out"])) for j in jobs]
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------------------------------- providers
def providers() -> list[dict]:
    from . import tts_kokoro as kk
    sv = sapi_voices()
    zh = [v for v in sv if v["lang"].lower().startswith("zh")]
    out = [{"id": "sapi", "name": "Windows system voices (SAPI)", "available": bool(sv), "offline": True, "quality": "basic",
            "note": (f"{len(sv)} system voice(s) installed" + ("" if zh else "; no Chinese voice: Settings > Time & language > Speech > add a Chinese voice pack")) if sv else
                    "No system voices detected (Windows only)."},
           kk.provider_info()]
    for pid, (name, note) in STUBS.items():
        out.append({"id": pid, "name": name, "available": False, "offline": True, "quality": "high", "note": note})
    return out


def voices(provider: str | None = None) -> list[dict]:
    from . import tts_kokoro as kk
    out: list[dict] = []
    if provider in (None, "", "all", "sapi"):
        out += [{**v, "available": True} for v in sapi_voices()]
    if provider in (None, "", "all", "kokoro"):
        out += kk.voices()
    return out


def pick(provider: str | None, voice: str | None, lang: str | None, text: str) -> tuple[str, str | None]:
    """Resolve (provider, voice-name) for a request. voice may be 'provider:name' or a bare name."""
    from . import tts_kokoro as kk
    prov = provider if provider and provider != "auto" else None
    vname = voice or None
    if vname and ":" in vname and vname.split(":", 1)[0] in ("sapi", "kokoro"):
        prov, vname = vname.split(":", 1)
    lang = (lang or guess_lang(text)).lower()
    want_zh = lang.startswith("zh")
    if prov is None:
        if kk.ready() and not (vname and any(v["name"] == vname or v["full_name"] == vname for v in sapi_voices())):
            prov = "kokoro"
        else:
            prov = "sapi"
    if prov in STUBS:
        raise RuntimeError(f"{prov} is unavailable: {STUBS[prov][1]}")
    if prov == "kokoro":
        if not kk.ready():
            raise RuntimeError("Kokoro is not ready: " + kk.status()["message"])
        if vname is None:
            vname = kk.default_voice(want_zh)
        return "kokoro", vname
    if prov == "sapi":
        sv = sapi_voices()
        if not sv:
            raise RuntimeError("No SAPI voices are installed")
        if vname:
            m = next((v for v in sv if vname in (v["full_name"], v["name"], v["id"])), None)
            if not m:
                raise RuntimeError(f"System voice {vname} not found")
            return "sapi", m["full_name"]
        pref = [v for v in sv if v["lang"].lower().startswith("zh" if want_zh else "en")] or sv
        return "sapi", pref[0]["full_name"]
    raise RuntimeError(f"Unknown provider: {prov}")


def synth_chunks(ctx: JobContext, prov: str, chunks: list[tuple[str, str | None]], speed: float, lang: str | None) -> list[np.ndarray]:
    """chunks: [(text, voice)] -> audio arrays at SR. For sapi speed is applied later (atempo); kokoro uses native speed."""
    if prov == "sapi":
        return sapi_synth(chunks, None, ctx)
    from . import tts_kokoro as kk
    return kk.synth(ctx, chunks, speed, lang)


def _name(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return "Voice-over: " + (t[:18] + ("…" if len(t) > 18 else ""))


def generate(ctx: JobContext, req: dict) -> dict:
    text = (req.get("text") or "").strip()
    if not text:
        raise ValueError("Text must not be empty")
    speed = float(np.clip(req.get("speed") or 1.0, 0.5, 2.0))
    lang = req.get("lang")
    prov, voice = pick(req.get("provider"), req.get("voice"), lang, text)
    ctx.progress(0.02, f"{prov}: {voice}")
    chunks = [(c, voice) for c in split_text(text)]
    parts = synth_chunks(ctx, prov, chunks, speed, lang)
    audio = concat(parts)
    out = config.MEDIA_DIR / f"tts_{uuid.uuid4().hex[:8]}.mp3"
    ctx.progress(0.9, "Encoding mp3")
    eff = speed if prov == "sapi" else 1.0
    encode_mp3(audio, out, eff, None, ctx)
    row = ingest_file(out, origin="tts", name=_name(text), tags=["voice-over", prov],
                      meta={"tts": {"provider": prov, "voice": voice, "speed": speed, "text": text[:2000]}})
    return {"media": row, "provider": prov, "voice": voice}


def batch(ctx: JobContext, req: dict) -> dict:
    items = [it for it in (req.get("items") or []) if str(it.get("text", "")).strip()]
    if not items:
        raise ValueError("items is empty")
    speed = float(np.clip(req.get("speed") or 1.0, 0.5, 2.0))
    prov, voice = pick(req.get("provider"), req.get("voice"), req.get("lang"), " ".join(str(i["text"]) for i in items[:5]))
    ctx.progress(0.02, f"{prov}: {voice} · {len(items)} item(s)")
    flat: list[tuple[str, str | None]] = []
    owner: list[int] = []
    for k, it in enumerate(items):
        for c in split_text(str(it["text"])) or [str(it["text"]).strip()]:
            flat.append((c, voice)); owner.append(k)
    parts = synth_chunks(ctx, prov, flat, speed, req.get("lang"))
    outs = []
    eff = speed if prov == "sapi" else 1.0
    for k, it in enumerate(items):
        ctx.progress(0.86 + 0.13 * k / len(items), f"Encoding {k + 1}/{len(items)}")
        audio = concat([p for p, o in zip(parts, owner) if o == k])
        out = config.MEDIA_DIR / f"tts_{uuid.uuid4().hex[:8]}.mp3"
        maxd = it.get("maxDuration") or it.get("max_duration")
        encode_mp3(audio, out, eff, float(maxd) if maxd else None, ctx)
        row = ingest_file(out, origin="tts", name=_name(str(it["text"])), tags=["voice-over", prov],
                          meta={"tts": {"provider": prov, "voice": voice, "speed": speed, "text": str(it["text"])[:500], "itemId": it.get("id")}})
        outs.append({"id": it.get("id"), "media": row})
    return {"items": outs, "provider": prov, "voice": voice}
