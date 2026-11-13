"""Auto-cut: decide which time ranges of a clip to KEEP (tight = remove silences, highlights = best audio+motion windows,
talking-head = tight + filler words when ASR is available)."""
from __future__ import annotations

import re
import subprocess

import numpy as np

from ..config import FFMPEG
from ..ffutil import _NOWIN
from ..jobs import JobContext
from . import asr

FILLERS = {"嗯", "呃", "额", "啊", "哦", "唔", "um", "uh", "er", "erm", "hmm", "uhm", "emm", "eh", "那个", "呃呃", "嗯嗯"}


def audio_db(path: str, hop: float = 0.05) -> np.ndarray:
    """RMS level in dBFS per `hop` seconds (mono 16k)."""
    p = subprocess.run([FFMPEG, "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                       capture_output=True, **_NOWIN)
    x = np.frombuffer(p.stdout[: len(p.stdout) // 2 * 2], np.int16).astype(np.float32) / 32768.0
    if x.size == 0:
        return np.zeros(0, np.float32)
    n = int(16000 * hop)
    k = x.size // n
    if k == 0:
        return np.zeros(0, np.float32)
    rms = np.sqrt((x[: k * n].reshape(k, n) ** 2).mean(1) + 1e-12)
    return (20 * np.log10(rms)).astype(np.float32)


def detect_silences(path: str, noise_db: float | None = None, min_d: float = 0.4) -> tuple[list[tuple[float, float]], float]:
    if noise_db is None:
        db = audio_db(path)
        if db.size == 0:
            return [], -35.0
        floor = float(np.percentile(db, 10))
        noise_db = float(np.clip(floor + 12, -50, -24))
    p = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", path, "-vn", "-af", f"silencedetect=noise={noise_db:.1f}dB:d={min_d}", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", **_NOWIN)
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", p.stderr)]
    out = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        out.append((max(0.0, s), e if e is not None else -1.0))
    return out, noise_db


def complement(ranges: list[tuple[float, float]], total: float) -> list[tuple[float, float]]:
    out, cur = [], 0.0
    for s, e in sorted(ranges):
        if s > cur:
            out.append((cur, s))
        cur = max(cur, e)
    if cur < total:
        out.append((cur, total))
    return out


def motion_curve(path: str, fps: float = 2.0) -> np.ndarray:
    w, h = 96, 54
    p = subprocess.run([FFMPEG, "-v", "error", "-i", path, "-an", "-vf", f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
                       capture_output=True, **_NOWIN)
    buf = np.frombuffer(p.stdout, np.uint8)
    n = buf.size // (w * h)
    if n < 2:
        return np.zeros(0, np.float32)
    fr = buf[: n * w * h].reshape(n, w * h).astype(np.float32)
    d = np.abs(np.diff(fr, axis=0)).mean(1)
    return np.concatenate([[d[0]], d])


def _norm(a: np.ndarray) -> np.ndarray:
    if a.size == 0:
        return a
    lo, hi = np.percentile(a, 5), np.percentile(a, 97)
    return np.clip((a - lo) / max(hi - lo, 1e-6), 0, 1)


def tight(media: dict, ctx: JobContext | None = None) -> tuple[list[dict], str]:
    dur = media["duration"]
    if not media.get("has_audio"):
        return [{"start": 0.0, "end": dur, "score": 0.5, "reason": "No audio track, cannot detect silence"}], "The media has no audio track; keeping everything."
    sil, thr = detect_silences(media["path"])
    pad = 0.1
    sil2 = []
    for s, e in sil:
        e = dur if e < 0 else e
        s2, e2 = s + pad, e - pad
        if e2 - s2 >= 0.25:
            sil2.append((s2, e2))
    keep = [(s, e) for s, e in complement(sil2, dur) if e - s >= 0.25]
    # merge tiny gaps
    merged: list[list[float]] = []
    for s, e in keep:
        if merged and s - merged[-1][1] < 0.12:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    if not merged:
        merged = [[0.0, dur]]
    return [{"start": round(s, 3), "end": round(e, 3), "score": 1.0, "reason": "Speech / sound"} for s, e in merged], f"Silence threshold {thr:.0f} dB"


def highlights(media: dict, target: float | None, ctx: JobContext | None = None) -> tuple[list[dict], str]:
    dur = media["duration"]
    target = target or max(8.0, min(60.0, dur * 0.3))
    target = min(target, dur)
    hop = 0.5
    a = audio_db(media["path"], hop) if media.get("has_audio") else np.zeros(0)
    if ctx:
        ctx.progress(0.3, "Analysing motion")
    m = motion_curve(media["path"], 1 / hop) if media.get("kind") == "video" else np.zeros(0)
    n = int(np.ceil(dur / hop))
    A = np.zeros(n); M = np.zeros(n)
    if a.size:
        A[: min(n, a.size)] = _norm(a[:n])[: min(n, a.size)]
    if m.size:
        M[: min(n, m.size)] = _norm(m[:n])[: min(n, m.size)]
    wa = 0.5 if (a.size and m.size) else 1.0
    score = (wa * A + (1 - wa) * M) if (a.size or m.size) else np.ones(n) * 0.5
    win = max(2, int(4.0 / hop))   # 4 s windows
    ker = np.ones(win) / win
    sm = np.convolve(score, ker, mode="same")
    order = np.argsort(-sm)
    taken = np.zeros(n, bool)
    picks: list[tuple[int, int, float]] = []
    total = 0.0
    for i in order:
        if total >= target:
            break
        s, e = max(0, i - win // 2), min(n, i + win // 2)
        if taken[s:e].any():
            continue
        taken[s:e] = True
        picks.append((s, e, float(sm[i])))
        total += (e - s) * hop
    picks.sort()
    merged: list[list[float]] = []
    for s, e, sc in picks:
        s_, e_ = s * hop, min(dur, e * hop)
        if merged and s_ - merged[-1][1] < 0.6:
            merged[-1][1] = e_
            merged[-1][2] = max(merged[-1][2], sc)
        else:
            merged.append([s_, e_, sc])
    keep = [{"start": round(s, 2), "end": round(e, 2), "score": round(sc, 3),
             "reason": "Loudness + motion peak" if (a.size and m.size) else ("Loudness peak" if a.size else "Motion peak")} for s, e, sc in merged]
    return keep, f"Target {target:.0f}s, picked {len(keep)} segment(s)"


def remove_fillers(keep: list[dict], words: list[dict]) -> tuple[list[dict], int]:
    bad = []
    for w in words:
        tok = re.sub(r"[^\w一-鿿]", "", w["word"].lower())
        if tok in FILLERS:
            bad.append((max(0.0, w["start"] - 0.02), w["end"] + 0.02))
    if not bad:
        return keep, 0
    out = []
    for k in keep:
        segs = [(k["start"], k["end"])]
        for bs, be in bad:
            nxt = []
            for s, e in segs:
                if be <= s or bs >= e:
                    nxt.append((s, e))
                else:
                    if bs - s >= 0.2:
                        nxt.append((s, bs))
                    if e - be >= 0.2:
                        nxt.append((be, e))
            segs = nxt
        for s, e in segs:
            out.append({**k, "start": round(s, 3), "end": round(e, 3)})
    return out, len(bad)


def auto_cut(ctx: JobContext, media: dict, style: str, target: float | None) -> dict:
    dur = media["duration"]
    if dur <= 0:
        raise ValueError("The media has no duration")
    ctx.progress(0.05, "Analysing audio")
    note = ""
    if style == "highlights":
        keep, note = highlights(media, target, ctx)
    else:
        keep, note = tight(media, ctx)
        if style == "talking-head":
            st = asr.status()
            if st["ready"]:
                ctx.progress(0.3, "Transcribing (for filler-word removal)")
                res = asr.transcribe(media["path"], words=True, ctx=None)
                words = [w for s in res["segments"] for w in s.get("words", [])]
                keep, nbad = remove_fillers(keep, words)
                note += f"; removed {nbad} filler word(s)"
            else:
                note += f"; filler-word removal skipped ({st['message']})"
        if target:
            # trim to the target by dropping the shortest segments first
            total = sum(k["end"] - k["start"] for k in keep)
            if total > target:
                for k in sorted(keep, key=lambda k: k["end"] - k["start"])[:]:
                    if total <= target:
                        break
                    keep.remove(k)
                    total -= k["end"] - k["start"]
    ctx.progress(0.98, "Done")
    kept = sum(k["end"] - k["start"] for k in keep)
    remove = [{"start": round(s, 3), "end": round(e, 3)} for s, e in complement([(k["start"], k["end"]) for k in keep], dur) if e - s > 0.02]
    return {"keep": keep, "remove": remove, "duration": dur, "kept_duration": round(kept, 2), "style": style, "note": note}
