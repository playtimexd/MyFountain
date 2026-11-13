"""Offline procedural background-music generator (numpy only, no model downloads).

prompt keywords -> (mood, genre, bpm, key/mode) -> chord progression -> layers:
pad + bass + pluck/arpeggio + lead melody + drums -> light algorithmic reverb -> soft limiter -> stereo 44.1 kHz.
Deterministic for a given seed. Ends with a fade-out so it loops/cuts cleanly.
"""
from __future__ import annotations

import re
import wave
from typing import Callable

import numpy as np
from scipy.signal import lfilter, oaconvolve

SR = 44100
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "lydian": [0, 2, 4, 6, 7, 9, 11],
    "phrygian": [0, 1, 3, 5, 7, 8, 10],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
}

# --- keyword tables -------------------------------------------------------------------------------------
MOOD_KW = {
    "happy": ["happy", "upbeat", "cheerful", "bright", "joy", "fun", "playful", "uplifting", "positive", "sunny", "lively", "carefree", "feel good", "feel-good",
              "开心", "欢快", "快乐", "愉快", "阳光", "明亮", "活泼", "轻快"],
    "sad": ["sad", "melanchol", "sorrow", "emotional", "somber", "sombre", "nostalgi", "tearful", "lonely", "bittersweet", "heartbreak", "grief",
            "悲伤", "伤感", "忧郁", "难过", "感人", "怀旧"],
    "calm": ["calm", "relax", "peaceful", "chill", "soft", "gentle", "sleep", "meditat", "soothing", "serene", "tranquil", "mellow", "lullaby", "slow", "zen",
             "平静", "放松", "治愈", "舒缓", "温柔", "安静", "冥想"],
    "epic": ["epic", "heroic", "grand", "powerful", "trailer", "battle", "dramatic", "triumph", "majestic", "intense", "hero", "victory", "war",
             "史诗", "宏大", "震撼", "激昂", "热血", "壮丽", "战斗"],
    "dark": ["dark", "tense", "suspense", "horror", "creepy", "thriller", "ominous", "eerie", "scary", "menacing", "sinister", "anxious", "tension",
             "黑暗", "紧张", "悬疑", "恐怖", "阴森"],
    "romantic": ["romantic", "love", "warm", "sweet", "wedding", "tender", "intimate", "passion", "valentine", "affection",
                 "浪漫", "温馨", "爱情", "甜蜜"],
    "energetic": ["energetic", "energy", "dynamic", "workout", "sport", "driving", "fast", "party", "hype", "exciting", "racing", "action", "pumping", "gym", "fitness",
                  "活力", "动感", "运动", "燃", "劲爆", "嗨"],
    "mysterious": ["mysterious", "mystery", "dreamy", "ethereal", "magic", "fantasy", "mystic", "enigmatic", "surreal", "wonder", "dream", "floating",
                   "神秘", "梦幻", "空灵", "奇幻"],
}
GENRE_KW = {
    "lofi": ["lofi", "lo-fi", "lo fi", "chillhop", "study", "低保真", "学习"],
    "edm": ["edm", "electronic", "house", "techno", "dance", "synthwave", "trance", "club", "dubstep", "electro", "synthpop", "future bass", "电子", "舞曲"],
    "cinematic": ["cinematic", "trailer", "orchestral", "orchestra", "score", "soundtrack", "film", "movie", "strings", "电影", "配乐", "预告", "管弦"],
    "ambient": ["ambient", "drone", "atmospheric", "soundscape", "background", "氛围", "纯音", "空间", "背景"],
    "piano": ["piano", "keys", "钢琴"],
    "rock": ["rock", "punk", "metal", "grunge", "guitar riff", "band", "摇滚"],
    "jazz": ["jazz", "bossa", "swing", "lounge", "blues", "saxophone", "爵士"],
    "hiphop": ["hiphop", "hip-hop", "hip hop", "trap", "rap", "boom bap", "boom-bap", "urban", "嘻哈", "说唱"],
    "pop": ["pop", "vlog", "commercial", "corporate", "advert", "promo", "tiktok", "youtube", "social media", "流行", "广告", "商业"],
    "folk": ["acoustic", "folk", "guitar", "ukulele", "banjo", "country", "strum", "民谣", "吉他", "国风", "古风"],
}

# mood -> mode, bpm, progressions (0-based scale degrees, 1 chord per bar), energy 0..1, brightness 0..1
MOODS = {
    "happy":      dict(mode="major",      bpm=118, energy=0.75, bright=0.8, prog=[[0, 4, 5, 3], [0, 5, 3, 4], [0, 3, 4, 4]]),
    "sad":        dict(mode="minor",      bpm=72,  energy=0.35, bright=0.35, prog=[[5, 3, 0, 4], [0, 5, 2, 6], [0, 3, 6, 2]]),
    "calm":       dict(mode="major",      bpm=78,  energy=0.25, bright=0.4, prog=[[0, 2, 3, 4], [0, 3, 0, 4], [0, 5, 3, 0]]),
    "epic":       dict(mode="minor",      bpm=100, energy=0.95, bright=0.7, prog=[[0, 5, 2, 6], [0, 5, 3, 4], [0, 6, 5, 6]]),
    "dark":       dict(mode="phrygian",   bpm=84,  energy=0.5, bright=0.25, prog=[[0, 1, 0, 6], [0, 5, 6, 0], [0, 0, 3, 1]]),
    "romantic":   dict(mode="major",      bpm=84,  energy=0.4, bright=0.55, prog=[[0, 5, 3, 4], [3, 4, 2, 5], [0, 2, 3, 4]]),
    "energetic":  dict(mode="mixolydian", bpm=128, energy=0.95, bright=0.85, prog=[[0, 6, 3, 0], [5, 3, 0, 4], [0, 4, 5, 3]]),
    "mysterious": dict(mode="dorian",     bpm=90,  energy=0.4, bright=0.45, prog=[[0, 3, 0, 6], [0, 1, 3, 6], [0, 6, 5, 6]]),
}
# genre -> drums pattern, arp rate (steps of 16th per note), instruments / tweaks
GENRES = {
    "lofi":      dict(bpm=78,  drums="lofi",   arp=2, lead=True,  swing=0.18, bass="sub", pad=0.8, seventh=True),
    "edm":       dict(bpm=126, drums="four",   arp=1, lead=True,  swing=0.0,  bass="pump", pad=0.9, seventh=False, duck=True),
    "cinematic": dict(bpm=92,  drums="taiko",  arp=2, lead=True,  swing=0.0,  bass="sub", pad=1.2, seventh=False, swell=True),
    "ambient":   dict(bpm=70,  drums="none",   arp=4, lead=False, swing=0.0,  bass="sub", pad=1.3, seventh=True),
    "piano":     dict(bpm=76,  drums="none",   arp=2, lead=True,  swing=0.0,  bass="sub", pad=0.5, seventh=True, piano=True),
    "rock":      dict(bpm=120, drums="rock",   arp=2, lead=True,  swing=0.0,  bass="eighth", pad=0.5, seventh=False),
    "jazz":      dict(bpm=104, drums="jazz",   arp=2, lead=True,  swing=0.33, bass="walk", pad=0.6, seventh=True),
    "hiphop":    dict(bpm=88,  drums="trap",   arp=2, lead=False, swing=0.1,  bass="sub", pad=0.7, seventh=True),
    "pop":       dict(bpm=110, drums="pop",    arp=2, lead=True,  swing=0.0,  bass="eighth", pad=0.8, seventh=False),
    "folk":      dict(bpm=96,  drums="soft",   arp=2, lead=True,  swing=0.0,  bass="sub", pad=0.4, seventh=False, piano=True),
}
DEFAULT_GENRE = {"happy": "pop", "sad": "piano", "calm": "ambient", "epic": "cinematic", "dark": "cinematic",
                 "romantic": "piano", "energetic": "edm", "mysterious": "ambient"}

# 16-step drum grids per bar: (kick, snare, hat, open-hat/ride)
DRUMS = {
    "four":  dict(k=[0, 4, 8, 12], s=[4, 12], h=[2, 6, 10, 14], o=[]),
    "pop":   dict(k=[0, 8, 10], s=[4, 12], h=list(range(0, 16, 2)), o=[14]),
    "rock":  dict(k=[0, 6, 8, 10], s=[4, 12], h=list(range(0, 16, 2)), o=[]),
    "lofi":  dict(k=[0, 7, 10], s=[4, 12], h=list(range(0, 16, 2)), o=[]),
    "trap":  dict(k=[0, 7, 10, 13], s=[8], h=list(range(0, 16, 1)), o=[]),
    "taiko": dict(k=[0, 6, 8], s=[], h=[], o=[]),
    "jazz":  dict(k=[0, 10], s=[], h=[0, 4, 6, 8, 12, 14], o=[]),
    "soft":  dict(k=[0, 8], s=[12], h=list(range(0, 16, 4)), o=[]),
    "none":  dict(k=[], s=[], h=[], o=[]),
}

# --- helpers ---------------------------------------------------------------------------------------------------


def midi_to_hz(m):
    return 440.0 * 2.0 ** ((np.asarray(m, dtype=np.float64) - 69.0) / 12.0)


def _has(text: str, kws: list[str]) -> bool:
    return any(k in text for k in kws)


def analyze_prompt(prompt: str, mood: str | None, genre: str | None, bpm: int | None, rng: np.random.Generator) -> dict:
    """Map free text (Chinese/English) to generator parameters."""
    text = f"{prompt or ''} {mood or ''} {genre or ''}".lower()
    scored = {m: sum(text.count(k) for k in kws) for m, kws in MOOD_KW.items()}
    mood_key = mood if mood in MOODS else None
    if not mood_key:
        best = max(scored.items(), key=lambda kv: kv[1])
        mood_key = best[0] if best[1] > 0 else str(rng.choice(["happy", "calm", "romantic", "mysterious"]))
    gscored = {g: sum(text.count(k) for k in kws) for g, kws in GENRE_KW.items()}
    genre_key = genre if genre in GENRES else None
    if not genre_key:
        best = max(gscored.items(), key=lambda kv: kv[1])
        genre_key = best[0] if best[1] > 0 else DEFAULT_GENRE[mood_key]
    m, g = MOODS[mood_key], GENRES[genre_key]
    # bpm: explicit > "120bpm" in text > blend of mood and genre
    bpm_val = bpm
    if not bpm_val:
        mm = re.search(r"(\d{2,3})\s*(?:bpm|BPM)", prompt or "")
        if mm:
            bpm_val = int(mm.group(1))
    if not bpm_val:
        bpm_val = int(round((m["bpm"] + g["bpm"]) / 2 + rng.integers(-3, 4)))
    bpm_val = int(min(200, max(50, bpm_val)))
    # key
    mode = m["mode"]
    root = int(rng.integers(0, 12))
    km = re.search(r"\b([A-G][#b]?)\s*(major|minor|maj|min|大调|小调)?", prompt or "")
    km2 = re.search(r"([A-G][#b]?)\s*(大调|小调)", prompt or "")
    km = km2 or (km if km and km.group(2) else None)
    if km:
        n = km.group(1).replace("b", "#") if len(km.group(1)) == 2 and km.group(1)[1] == "b" else km.group(1)
        flat = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}
        n = flat.get(km.group(1), n)
        if n in NOTE_NAMES:
            root = NOTE_NAMES.index(n)
            q = (km.group(2) or "").lower()
            mode = "minor" if q in ("minor", "min", "小调") else "major"
    prog = list(m["prog"][int(rng.integers(0, len(m["prog"])))])
    return dict(mood=mood_key, genre=genre_key, bpm=bpm_val, root=root, mode=mode, prog=prog,
                energy=m["energy"], bright=m["bright"], g=g)


class Scale:
    def __init__(self, root: int, mode: str):
        self.root, self.steps = root, SCALES[mode]

    def note(self, idx: int, octave_base: int = 48) -> int:
        """Scale degree index (can be negative / >6) -> MIDI note; octave_base = MIDI of the tonic."""
        return octave_base + self.root + self.steps[idx % 7] + 12 * (idx // 7)

    def chord(self, deg: int, seventh: bool, octave_base: int = 48) -> list[int]:
        idx = [deg, deg + 2, deg + 4] + ([deg + 6] if seventh else [])
        return [self.note(i, octave_base) for i in idx]


# --- voices ----------------------------------------------------------------------------------------------------

def _env_adsr(n: int, a: float, d: float, s: float, r: float, hold_end: int | None = None) -> np.ndarray:
    """ADSR over n samples; release starts at hold_end (default n - r)."""
    he = n - int(r * SR) if hold_end is None else hold_end
    he = max(1, min(n, he))
    t = np.arange(n) / SR
    env = np.empty(n)
    ai, di = max(1, int(a * SR)), max(1, int(d * SR))
    env[:] = s
    up = min(ai, n)
    env[:up] = np.linspace(0, 1, up)
    dn = min(ai + di, n)
    if dn > ai:
        env[ai:dn] = np.linspace(1, s, dn - ai)
    if he < n:
        env[he:] = env[he - 1] * np.exp(-(t[he:] - t[he]) * (5.0 / max(r, 1e-3)))
    return env


def _harm(phase: np.ndarray, f: float, nh: int, p: float, odd_only=False) -> np.ndarray:
    out = np.zeros_like(phase)
    for h in range(1, nh + 1):
        if odd_only and h % 2 == 0:
            continue
        if f * h > 9000:
            break
        out += np.sin(h * phase) / (h ** p)
    return out


def voice_pad(f: float, dur: float, bright: float, rng) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    stereo = np.zeros((n, 2), dtype=np.float32)
    for det, pan in ((-0.0035, 0.0), (0.0, 0.5), (0.0035, 1.0)):
        ph = 2 * np.pi * f * (1 + det) * t + rng.random() * 6.28
        s = _harm(ph, f, 6, 1.6 - 0.8 * bright)
        s *= (0.8 + 0.2 * np.sin(2 * np.pi * (0.15 + 0.1 * rng.random()) * t + rng.random() * 6.28))
        stereo[:, 0] += (s * (1 - pan * 0.7)).astype(np.float32)
        stereo[:, 1] += (s * (0.3 + pan * 0.7)).astype(np.float32)
    env = _env_adsr(n, 0.5, 0.3, 0.85, 0.9)
    return stereo * env[:, None].astype(np.float32) * 0.12


def voice_bass(f: float, dur: float, kind: str) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    ph = 2 * np.pi * f * t
    s = np.sin(ph) + 0.35 * np.sin(2 * ph) * np.exp(-t * 6) + 0.12 * np.sin(3 * ph)
    if kind in ("eighth", "walk", "pump"):
        s += 0.25 * np.sin(2 * ph + 0.5 * np.sin(ph)) * np.exp(-t * 3)
    env = _env_adsr(n, 0.008, 0.12, 0.7, 0.08)
    return (s * env * 0.34).astype(np.float32)


def voice_pluck(f: float, dur: float, bright: float, piano: bool = False) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    ph = 2 * np.pi * f * t
    out = np.zeros(n)
    nh = 10 if not piano else 8
    for h in range(1, nh + 1):
        if f * h > 9000:
            break
        decay = (3.0 if piano else 5.0) + h * (1.6 if piano else 2.4) * (1.3 - bright * 0.6)
        inh = 1 + (0.0004 * h * h if piano else 0.0)
        out += np.sin(h * ph * inh) * np.exp(-t * decay) / (h ** (1.1 if piano else 1.3))
    env = np.minimum(1, t / 0.004) * np.minimum(1, (n / SR - t) / 0.03)
    return (out * env * 0.2).astype(np.float32)


def voice_lead(f: float, dur: float, bright: float, vib_delay: float = 0.18) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    vib = np.clip((t - vib_delay) / 0.3, 0, 1) * 0.0045 * np.sin(2 * np.pi * 5.2 * t)
    ph = 2 * np.pi * f * np.cumsum(1 + vib) / SR
    s = _harm(ph, f, 6, 1.8 - 0.6 * bright, odd_only=False)
    s += 0.5 * _harm(ph * 1.003, f, 4, 2.0)
    env = _env_adsr(n, 0.02, 0.15, 0.7, 0.18)
    return (s * env * 0.16).astype(np.float32)


class Drums:
    def __init__(self, rng):
        t = np.arange(int(0.45 * SR)) / SR
        ph = 2 * np.pi * (48 * t + 120 * (1 - np.exp(-28 * t)) / 28)
        self.kick = (np.sin(ph) * np.exp(-t * 9) + 0.4 * np.exp(-t * 400) * rng.standard_normal(t.size)).astype(np.float32) * 0.9
        ts = np.arange(int(0.3 * SR)) / SR
        nz = rng.standard_normal(ts.size)
        nz = nz - lfilter([0.15], [1, -0.85], nz)  # high-passed noise
        self.snare = ((nz * np.exp(-ts * 22) * 0.55) + np.sin(2 * np.pi * 185 * ts) * np.exp(-ts * 28) * 0.45).astype(np.float32) * 0.7
        tc = np.arange(int(0.25 * SR)) / SR
        nc = rng.standard_normal(tc.size)
        nc = nc - lfilter([0.2], [1, -0.8], nc)
        burst = np.exp(-tc * 80) + np.exp(-((tc - 0.012) ** 2) * 6000) + np.exp(-((tc - 0.024) ** 2) * 6000)
        self.clap = (nc * (burst * 0.5 + np.exp(-tc * 18) * 0.5)).astype(np.float32) * 0.5
        th = np.arange(int(0.08 * SR)) / SR
        nh = rng.standard_normal(th.size)
        nh = np.diff(nh, prepend=0, n=1)
        nh = np.diff(nh, prepend=0, n=1)
        self.hat = (nh * np.exp(-th * 70)).astype(np.float32) * 0.25
        to = np.arange(int(0.35 * SR)) / SR
        no = rng.standard_normal(to.size)
        no = np.diff(np.diff(no, prepend=0), prepend=0)
        self.open = (no * np.exp(-to * 14)).astype(np.float32) * 0.2
        tt = np.arange(int(1.2 * SR)) / SR
        self.taiko = (np.sin(2 * np.pi * (45 * tt + 55 * (1 - np.exp(-6 * tt)) / 6)) * np.exp(-tt * 3.2)).astype(np.float32) * 0.9


def _place(buf: np.ndarray, sig: np.ndarray, pos: int, gain: float = 1.0, pan: float = 0.5) -> None:
    if pos >= buf.shape[0] or pos < -sig.shape[0]:
        return
    s0 = max(0, -pos)
    sig = sig[s0:]
    pos = max(0, pos)
    n = min(sig.shape[0], buf.shape[0] - pos)
    if n <= 0:
        return
    if sig.ndim == 1:
        l, r = np.cos(pan * np.pi / 2), np.sin(pan * np.pi / 2)
        buf[pos:pos + n, 0] += sig[:n] * gain * l * 1.414
        buf[pos:pos + n, 1] += sig[:n] * gain * r * 1.414
    else:
        buf[pos:pos + n] += sig[:n] * gain


def _reverb_ir(rng, secs=1.9, damp=0.55) -> np.ndarray:
    n = int(secs * SR)
    t = np.arange(n) / SR
    ir = np.zeros((n, 2), dtype=np.float32)
    for c in range(2):
        nz = rng.standard_normal(n)
        nz = lfilter([1 - damp], [1, -damp], nz)  # darken
        ir[:, c] = nz * np.exp(-t * 3.4) * np.minimum(1, t / 0.02)
    ir[:int(0.012 * SR)] = 0
    ir /= np.sqrt((ir ** 2).sum(0).max()) + 1e-9
    return ir


def _reverb(x: np.ndarray, ir: np.ndarray) -> np.ndarray:
    out = np.zeros_like(x)
    for c in range(2):
        out[:, c] = oaconvolve(x[:, c], ir[:, c])[: x.shape[0]]
    return out


def generate(prompt: str, duration: float, *, bpm: int | None = None, mood: str | None = None, genre: str | None = None,
             seed: int | None = None, progress: Callable[[float, str], None] | None = None) -> tuple[np.ndarray, dict]:
    """Return (float32 stereo array [N,2] in -1..1, info dict)."""
    seed = int(seed if seed is not None else np.random.SeedSequence().entropy % (2 ** 31))
    rng = np.random.default_rng(seed)
    P = analyze_prompt(prompt, mood, genre, bpm, rng)
    g = P["g"]
    bpm_ = P["bpm"]
    sc = Scale(P["root"], P["mode"])
    beat = 60.0 / bpm_
    bar_s = beat * 4
    step_s = beat / 4
    n_bars = max(2, int(np.ceil(duration / bar_s)))
    total = int(duration * SR)
    tail = int(2.0 * SR)
    N = int(n_bars * bar_s * SR) + tail
    stems = {k: np.zeros((N, 2), dtype=np.float32) for k in ("pad", "bass", "arp", "lead", "drums")}
    dr = Drums(rng)
    pattern = DRUMS[g["drums"]]
    seventh = g.get("seventh", False) or P["mood"] in ("romantic",)
    piano = g.get("piano", False)
    arp_step = g["arp"]
    swing = g.get("swing", 0.0)
    energy = P["energy"]
    prog = P["prog"]
    # structure: intro / A / B ... / outro
    intro = 2 if n_bars >= 8 else 0
    outro = 2 if n_bars >= 6 else 1

    def step_time(bar: int, step: int) -> float:
        off = swing * step_s if (step % 2 == 1) else 0.0
        return bar * bar_s + step * step_s + off

    def S(t_: float) -> int:
        return int(round(t_ * SR))

    # motif for lead (list of (step, length_steps, scale-degree-delta)); varied per bar
    motifs = [
        [(0, 4, 0), (4, 2, 1), (6, 2, 2), (8, 6, 1), (14, 2, 0)],
        [(0, 2, 2), (2, 2, 1), (4, 4, 0), (8, 4, 2), (12, 4, 3)],
        [(2, 4, 2), (6, 2, 3), (8, 4, 4), (12, 4, 2)],
        [(0, 8, 2), (8, 4, 1), (12, 4, 0)],
        [(0, 3, 4), (3, 3, 2), (6, 2, 3), (8, 8, 2)],
    ]
    last_lead = 4  # scale degree index (about octave above tonic)
    arp_dir_cache: dict[int, list[int]] = {}

    for bar in range(n_bars):
        if progress:
            progress(0.05 + 0.6 * bar / n_bars, f"Rendering bar {bar + 1}/{n_bars}")
        deg = prog[bar % len(prog)]
        is_outro = bar >= n_bars - outro
        in_intro = bar < intro
        block = (bar - intro) // 4
        lead_on = g.get("lead", True) and not in_intro and not is_outro and (block % 2 == 1 or energy > 0.8 and block >= 1)
        drums_on = pattern is not DRUMS["none"] and not in_intro and not is_outro and bar >= (2 if n_bars >= 8 else 0)
        chord_pad = sc.chord(deg, seventh, 48)          # root around C3
        chord_hi = sc.chord(deg, seventh, 60)
        t0 = bar * bar_s
        # PAD (whole bar, slight overlap into next)
        if g["pad"] > 0:
            for nidx, m in enumerate(chord_pad):
                _place(stems["pad"], voice_pad(float(midi_to_hz(m)), bar_s + 0.9, P["bright"], rng), S(t0), g["pad"] * (1.0 if nidx else 0.8), pan=0.5)
            if g.get("swell") and not in_intro:
                pass
        # BASS
        root_m = sc.note(deg, 36)
        kind = g["bass"]
        bass_notes: list[tuple[int, float, int]] = []  # (step, dur_steps, midi)
        if is_outro or in_intro and kind != "pump":
            bass_notes = [(0, 16, root_m)] if bar >= (0 if not in_intro else 1) else []
        elif kind == "sub":
            bass_notes = [(0, 10, root_m), (10, 6, root_m)] if energy > 0.5 else [(0, 16, root_m)]
        elif kind == "eighth":
            bass_notes = [(s, 2, root_m if s % 8 else root_m) for s in range(0, 16, 2)]
        elif kind == "pump":
            bass_notes = [(s, 2, root_m) for s in range(2, 16, 4)] + [(0, 2, root_m)]
        elif kind == "walk":
            ch = sc.chord(deg, True, 36)
            walk = [ch[0], ch[1], ch[2], ch[1]]
            bass_notes = [(s * 4, 4, walk[s]) for s in range(4)]
        for st, dl, m in bass_notes:
            _place(stems["bass"], voice_bass(float(midi_to_hz(m)), dl * step_s * 0.95, kind), S(step_time(bar, st)), 1.0, pan=0.5)
        # ARP / PLUCK
        if not (is_outro and bar == n_bars - 1) and arp_step:
            ch = chord_hi + [chord_hi[0] + 12, chord_hi[1] + 12]
            pat_type = (bar // 2) % 3
            order = {0: [0, 1, 2, 3, 2, 1, 4, 2], 1: [0, 2, 1, 3, 2, 4, 3, 5], 2: [3, 2, 1, 0, 1, 2, 3, 4]}[pat_type]
            k = 0
            for st in range(0, 16, arp_step):
                if arp_step >= 4 and st % 4:
                    continue
                m = ch[order[k % len(order)] % len(ch)]
                k += 1
                vel = 0.75 + 0.25 * (1 if st % 4 == 0 else 0)
                if in_intro or is_outro:
                    vel *= 0.65
                pan = 0.3 + 0.4 * ((k % 4) / 3)
                _place(stems["arp"], voice_pluck(float(midi_to_hz(m)), max(0.5, 0.9 if piano else 0.7), P["bright"], piano),
                       S(step_time(bar, st)), vel * (1.1 if piano else 0.85), pan=pan)
        # LEAD
        if lead_on:
            motif = motifs[int(rng.integers(0, len(motifs)))]
            chord_set = {(x - sc.root) % 12 for x in chord_pad}
            for (st, ln, dd) in motif:
                if rng.random() < 0.12:
                    continue
                idx = 7 + deg + dd + int(rng.integers(-1, 2))
                idx = int(np.clip(idx, 4, 12))
                # snap strong beats to chord tones
                if st % 4 == 0:
                    for c in (0, 1, -1, 2, -2):
                        if sc.steps[(idx + c) % 7] in chord_set:
                            idx += c
                            break
                last_lead = idx
                m = sc.note(idx, 60)
                _place(stems["lead"], voice_lead(float(midi_to_hz(m)), ln * step_s * 0.97 + 0.12, P["bright"]),
                       S(step_time(bar, st)), 0.9, pan=0.55)
        # DRUMS
        if drums_on:
            fill = (bar - intro) % 8 == 7 and bar < n_bars - outro - 1
            kv = 1.0 if energy > 0.3 else 0.7
            for st in pattern["k"]:
                if g["drums"] == "taiko":
                    _place(stems["drums"], dr.taiko, S(step_time(bar, st)), 0.9 * kv, pan=0.5)
                else:
                    _place(stems["drums"], dr.kick, S(step_time(bar, st)), kv, pan=0.5)
            for st in pattern["s"]:
                if fill and st >= 12:
                    continue
                sn = dr.clap if g["drums"] == "four" else dr.snare
                _place(stems["drums"], sn, S(step_time(bar, st)), 0.9 if g["drums"] != "lofi" else 0.6, pan=0.5)
            if fill:
                for j, st in enumerate(range(12, 16)):
                    _place(stems["drums"], dr.snare, S(step_time(bar, st)), 0.5 + 0.12 * j, pan=0.5)
            for st in pattern["h"]:
                v = 0.7 + 0.3 * (st % 4 == 2)
                if g["drums"] == "lofi":
                    v *= 0.6 * (0.7 + 0.5 * rng.random())
                if g["drums"] == "trap" and rng.random() < 0.25:
                    continue
                _place(stems["drums"], dr.hat, S(step_time(bar, st)), v * (0.8 if energy < 0.5 else 1.0), pan=0.62 if st % 4 else 0.45)
            for st in pattern["o"]:
                _place(stems["drums"], dr.open, S(step_time(bar, st)), 0.7, pan=0.55)
        elif pattern is not DRUMS["none"] and bar == intro - 1 + 0 and intro:
            pass
        # taiko / swell hit on first bar after intro for cinematic
        if g.get("swell") and bar == intro and intro:
            _place(stems["drums"], dr.taiko, S(t0), 1.0, pan=0.5)

    if progress:
        progress(0.7, "Mixing / reverb")
    # sidechain "pump" on pad/arp/bass for edm-like genres
    if g.get("duck"):
        tt = np.arange(N) / SR
        ph_ = (tt % beat)
        duck = (1 - 0.55 * np.exp(-ph_ / 0.11)).astype(np.float32)
        duck[: int(intro * bar_s * SR)] = 1.0
        for k in ("pad", "arp", "lead"):
            stems[k] *= duck[:, None]
    ir = _reverb_ir(rng, 1.9 if g["drums"] != "none" else 2.8)
    wet = _reverb(stems["pad"] * 0.55 + stems["arp"] * 0.45 + stems["lead"] * 0.5 + stems["drums"] * 0.07 + stems["bass"] * 0.0, ir)
    mix = (stems["pad"] * 1.0 + stems["bass"] * 1.0 + stems["arp"] * 0.9 + stems["lead"] * 0.9 + stems["drums"] * 0.85) + wet * 0.55
    if g["drums"] == "lofi":  # gentle tape-ish lowpass + vinyl noise
        mix = lfilter([0.35], [1, -0.65], mix, axis=0).astype(np.float32) * 1.5
        mix += (rng.standard_normal(mix.shape).astype(np.float32) * 0.0025)
    mix = mix[:total]
    if progress:
        progress(0.85, "Mastering")
    # fades
    fi = int(0.03 * SR)
    mix[:fi] *= np.linspace(0, 1, fi)[:, None]
    fo = int(min(4.0, max(1.0, duration * 0.12)) * SR)
    fo = min(fo, mix.shape[0] // 2)
    curve = np.linspace(1, 0, fo) ** 2
    mix[-fo:] *= curve[:, None].astype(np.float32)
    # soft limiter + normalise to about -1 dBFS
    body = mix[: max(1, mix.shape[0] - fo)]
    rms = float(np.sqrt((body.astype(np.float64) ** 2).mean())) or 1.0
    mix = mix * (0.17 / rms)              # loudness-match all genres (~ -15 dBFS RMS)
    mix = (np.tanh(mix) * 0.95).astype(np.float32)   # soft limiter
    info = dict(mood=P["mood"], genre=P["genre"], bpm=bpm_, key=f"{NOTE_NAMES[P['root']]} {P['mode']}", seed=seed,
                bars=n_bars, progression=[int(d) + 1 for d in prog])
    return mix, info


def write_wav(path, audio: np.ndarray) -> None:
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
