"""Project JSON  ->  one ffmpeg filter graph  ->  mp4/webm/gif/mp3.

Mirrors the preview compositor in the frontend (types/project.ts is the contract):
  * tracks render bottom -> top in array order; per-clip: fit(contain/cover) -> scale -> flip -> adjust -> rotate -> opacity -> fades/transition
  * text clips are rasterised with Pillow (stroke/shadow/box) and then treated like transparent images
  * audio: per-clip trim/speed/volume/fades/delay -> amix -> limiter
"""
from __future__ import annotations

import hashlib
import math
import re
import time
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from .. import db
from ..config import CACHE_DIR, EXPORT_DIR
from ..ffutil import has_nvenc, run_ffmpeg
from ..jobs import JobContext
from . import fonts, stickers

PI = math.pi


def _f(d: dict, k: str, default: float) -> float:
    v = d.get(k, default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def clip_duration(c: dict) -> float:
    return max(0.01, (_f(c, "out", 0) - _f(c, "in", 0)) / (_f(c, "speed", 1) or 1))


def _hex(color: str, default="000000") -> str:
    m = re.match(r"#?([0-9a-fA-F]{6})", color or "")
    return m.group(1) if m else default


def _rgba(color: str, default=(0, 0, 0, 255)) -> tuple[int, int, int, int]:
    m = re.match(r"#?([0-9a-fA-F]{6})([0-9a-fA-F]{2})?$", color or "")
    if not m:
        return default
    h = m.group(1)
    a = int(m.group(2), 16) if m.group(2) else 255
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a


# --------------------------------------------------------------------------------------- text -> PNG
def text_png(t: dict, canvas_h: int) -> Path:
    key = hashlib.md5(f"{sorted(t.items())}|{canvas_h}".encode()).hexdigest()[:16]
    out = CACHE_DIR / f"text_{key}.png"
    if out.exists():
        return out
    size = max(8.0, _f(t, "size", 0.07) * canvas_h)
    font = fonts.load(t.get("font") or "Microsoft YaHei", size, bool(t.get("bold")), bool(t.get("italic")))
    content = str(t.get("content") or " ")
    sw = int(round(_f(t, "strokeWidth", 0) * size))
    align = t.get("align", "center")
    pad_bg = _f(t, "bgPadding", 0.25) * size if t.get("bg") else 0
    shadow = bool(t.get("shadow"))
    margin = int(sw + (size * 0.12 if shadow else 0) + pad_bg + 8)

    tmp = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    l, tp, r, b = tmp.multiline_textbbox((0, 0), content, font=font, align=align, stroke_width=sw, spacing=int(size * 0.2))
    tw, th = r - l, b - tp
    W, H = int(tw + margin * 2), int(th + margin * 2)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if t.get("bg"):
        d.rounded_rectangle([margin - pad_bg, margin - pad_bg, margin + tw + pad_bg, margin + th + pad_bg],
                            radius=int(size * 0.2), fill=_rgba(t["bg"]))
    pos = (margin - l, margin - tp)
    if shadow:
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(sh).multiline_text((pos[0] + size * 0.05, pos[1] + size * 0.06), content, font=font, fill=(0, 0, 0, 200),
                                          align=align, stroke_width=sw, stroke_fill=(0, 0, 0, 200), spacing=int(size * 0.2))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(size * 0.04)))
        d = ImageDraw.Draw(img)
    d.multiline_text(pos, content, font=font, fill=_rgba(t.get("color", "#ffffff")), align=align,
                     stroke_width=sw, stroke_fill=_rgba(t.get("stroke", "#000000")), spacing=int(size * 0.2))
    img.save(out)
    return out


# --------------------------------------------------------------------------------------- filter pieces
def _atempo(speed: float) -> str:
    parts, s = [], speed
    while s > 2.0:
        parts.append("atempo=2.0"); s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5"); s /= 0.5
    parts.append(f"atempo={s:.5f}")
    return ",".join(parts)


def _adjust_chain(a: dict, H: int) -> list[str]:
    b, c, s = _f(a, "brightness", 0), _f(a, "contrast", 1), _f(a, "saturation", 1)
    gray, sep, temp = _f(a, "grayscale", 0), _f(a, "sepia", 0), _f(a, "temperature", 0)
    hue, blur, sharp, vig = _f(a, "hue", 0), _f(a, "blur", 0), _f(a, "sharpen", 0), _f(a, "vignette", 0)
    out: list[str] = []
    s_eff = s * (1 - gray)
    if abs(b) > 1e-3 or abs(c - 1) > 1e-3 or abs(s_eff - 1) > 1e-3:
        out.append(f"eq=brightness={b:.3f}:contrast={c:.3f}:saturation={max(s_eff, 0):.3f}")
    if abs(hue) > 0.5:
        out.append(f"hue=h={hue:.1f}")
    if sep > 1e-3:
        m = [.393, .769, .189, .349, .686, .168, .272, .534, .131]
        ident = [1, 0, 0, 0, 1, 0, 0, 0, 1]
        v = [ident[i] * (1 - sep) + m[i] * sep for i in range(9)]
        out.append("colorchannelmixer=" + ":".join(f"{k}={x:.4f}" for k, x in zip(
            ["rr", "rg", "rb", "gr", "gg", "gb", "br", "bg", "bb"], v)))
    if abs(temp) > 1e-3:
        k = 0.22 * temp
        out.append(f"colorbalance=rs={k:.3f}:rm={k:.3f}:rh={k:.3f}:bs={-k:.3f}:bm={-k:.3f}:bh={-k:.3f}")
    if sharp > 1e-3:
        out.append(f"unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount={sharp * 1.5:.2f}")
    if blur > 1e-3:
        out.append(f"gblur=sigma={max(blur * H / 1080 / 2, 0.1):.2f}")
    if vig > 1e-3:
        out.append(f"vignette=angle={max(0.2, 0.75 * (1 - 0.7 * vig)):.3f}:eval=init")
    return out


def _ease(S: float, D: float) -> str:
    return f"(1-pow(1-min(max((t-{S:.3f})/{max(D, 0.05):.3f},0),1),3))"


class Builder:
    def __init__(self, project: dict, preset: dict):
        self.p = project
        self.W = int(preset.get("width") or project.get("width") or 1920)
        self.H = int(preset.get("height") or project.get("height") or 1080)
        self.W -= self.W % 2
        self.H -= self.H % 2
        self.fps = float(preset.get("fps") or project.get("fps") or 30)
        self.inputs: list[list[str]] = []     # arg groups per input
        self.chains: list[str] = []
        self.n = 0
        self.audio_labels: list[str] = []
        self.dur = 0.0

    def _in(self, args: list[str]) -> int:
        self.inputs.append(args)
        return len(self.inputs) - 1

    def _uid(self, p="v") -> str:
        self.n += 1
        return f"{p}{self.n}"

    # ------------------------------------------------------------------ visual clip
    def visual(self, base: str, c: dict) -> str:
        W, H, fps = self.W, self.H, self.fps
        typ = c.get("type")
        start, dur = _f(c, "start", 0), clip_duration(c)
        end = start + dur
        speed = _f(c, "speed", 1) or 1
        tr = c.get("transform") or {}
        src: Path
        alpha_src = typ != "video"
        vp9_alpha = False
        if typ == "text":
            src = text_png(c.get("text") or {}, H)
        elif typ == "sticker":
            src = stickers.sticker_path(c["assetId"])
        else:
            m = db.get_media(c.get("assetId", ""))
            if not m:
                raise RuntimeError(f"Clip references a missing asset: {c.get('assetId')}")
            src = Path(m["path"])
            # transparent VP9 WebM (animation previews etc.): ffmpeg's native vp9 decoder drops alpha -> force libvpx
            vp9_alpha = bool(src.suffix.lower() == ".webm" and (m.get("origin") == "anim" or (m.get("meta") or {}).get("alpha")))
            alpha_src = m["kind"] == "image" or vp9_alpha
        if typ == "video":
            dec = ["-c:v", "libvpx-vp9"] if vp9_alpha else []
            idx = self._in(["-ss", f"{_f(c, 'in', 0):.3f}", "-t", f"{_f(c, 'out', 0) - _f(c, 'in', 0):.3f}", *dec, "-i", str(src)])
            ch = [f"setpts=(PTS-STARTPTS)/{speed:.5f}", f"fps={fps:g}"]
        else:
            idx = self._in(["-loop", "1", "-framerate", f"{fps:g}", "-t", f"{dur:.3f}", "-i", str(src)])
            ch = ["setpts=PTS-STARTPTS"]
        if typ != "text":                                           # fit into canvas
            if c.get("fit") == "cover":
                ch.append(f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}")
            else:
                ch.append(f"scale={W}:{H}:force_original_aspect_ratio=decrease")
        sc = _f(tr, "scale", 1)
        if abs(sc - 1) > 1e-3:
            ch.append(f"scale=w='max(2,trunc(iw*{sc:.4f}/2)*2)':h='max(2,trunc(ih*{sc:.4f}/2)*2)'")
        if tr.get("flipX"): ch.append("hflip")
        if tr.get("flipY"): ch.append("vflip")
        adj = _adjust_chain(c.get("adjust") or {}, H)
        label = self._uid("c")
        if adj and alpha_src:                                       # keep alpha through yuv-only filters
            self.chains.append(f"[{idx}:v]{','.join(ch)},format=rgba,split[{label}a][{label}b]")
            self.chains.append(f"[{label}b]alphaextract[{label}al]")
            self.chains.append(f"[{label}a]{','.join(adj)}[{label}c]")
            self.chains.append(f"[{label}c][{label}al]alphamerge[{label}d]")
            cur, ch = f"{label}d", []
        else:
            cur = f"{idx}:v"
            ch += adj
        ch.append("format=rgba")
        rot = _f(tr, "rotation", 0)
        if abs(rot) > 0.01:
            ch.append(f"rotate=a={rot * PI / 180:.5f}:ow='hypot(iw,ih)':oh='hypot(iw,ih)':c=none")
        op = _f(tr, "opacity", 1)
        if op < 0.999:
            ch.append(f"colorchannelmixer=aa={max(op, 0):.3f}")
        fi, fo = _f(c, "fadeIn", 0), _f(c, "fadeOut", 0)
        ti = c.get("transitionIn") or {}
        ttype, td = ti.get("type"), min(_f(ti, "duration", 0.5), dur)
        if ttype in ("fade", "zoomin", "wipeleft", "wiperight", "slideleft", "slideright", "slideup", "slidedown") and ttype in ("fade", "zoomin"):
            fi = max(fi, td)
        if fi > 0:
            ch.append(f"fade=t=in:st=0:d={fi:.3f}:alpha=1")
        if fo > 0:
            ch.append(f"fade=t=out:st={max(dur - fo, 0):.3f}:d={fo:.3f}:alpha=1")
        ch.append(f"setpts=PTS+{start:.3f}/TB")
        if ttype == "zoomin":
            ch.append(f"scale=w='max(2,trunc(iw*(0.55+0.45*{_ease(start, td)})/2)*2)':h='max(2,trunc(ih*(0.55+0.45*{_ease(start, td)})/2)*2)':eval=frame")
        if ttype in ("wipeleft", "wiperight"):
            p = f"min(max((T-{start:.3f})/{max(td, 0.05):.3f},0),1)"
            cond = f"lte(X,W*{p})" if ttype == "wipeleft" else f"gte(X,W*(1-{p}))"
            ch.append(f"format=gbrap,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='if({cond},alpha(X,Y),0)',format=rgba")
        out = self._uid("o")
        self.chains.append(f"[{cur}]{','.join(ch)}[{out}]")

        ox, oy = _f(tr, "x", 0) * W, _f(tr, "y", 0) * H
        x = f"(W-w)/2+({ox:.2f})"
        y = f"(H-h)/2+({oy:.2f})"
        if ttype and td > 0:
            e = _ease(start, td)
            if ttype == "slideleft": x += f"+(1-{e})*W"
            elif ttype == "slideright": x += f"-(1-{e})*W"
            elif ttype == "slideup": y += f"+(1-{e})*H"
            elif ttype == "slidedown": y += f"-(1-{e})*H"
        nxt = self._uid("b")
        self.chains.append(f"[{base}][{out}]overlay=x='{x}':y='{y}':enable='between(t,{start:.3f},{end:.3f})':eof_action=pass[{nxt}]")
        return nxt

    # ------------------------------------------------------------------ audio clip
    def audio(self, c: dict, track_muted: bool) -> None:
        if track_muted or c.get("type") not in ("video", "audio"):
            return
        if c.get("type") == "video" and c.get("audioDetached"):     # sound was split onto its own audio clip
            return
        m = db.get_media(c.get("assetId", ""))
        if not m or not m["has_audio"] and m["kind"] != "audio":
            return
        speed = _f(c, "speed", 1) or 1
        start, dur = _f(c, "start", 0), clip_duration(c)
        idx = self._in(["-ss", f"{_f(c, 'in', 0):.3f}", "-t", f"{_f(c, 'out', 0) - _f(c, 'in', 0):.3f}", "-i", m["path"]])
        ch = ["asetpts=PTS-STARTPTS"]
        if abs(speed - 1) > 1e-3:
            ch.append(_atempo(speed))
        vol = _f(c, "volume", 1)
        if abs(vol - 1) > 1e-3:
            ch.append(f"volume={vol:.3f}")
        fi, fo = _f(c, "fadeIn", 0), _f(c, "fadeOut", 0)
        if fi > 0: ch.append(f"afade=t=in:st=0:d={fi:.3f}")
        if fo > 0: ch.append(f"afade=t=out:st={max(dur - fo, 0):.3f}:d={fo:.3f}")
        ch.append("aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo")
        if start > 0.001:
            ms = int(start * 1000)
            ch.append(f"adelay=delays={ms}:all=1")
        lab = self._uid("a")
        self.chains.append(f"[{idx}:a]{','.join(ch)}[{lab}]")
        self.audio_labels.append(lab)

    # ------------------------------------------------------------------ whole graph
    def build(self, want_video=True) -> tuple[str, str | None, bool]:
        tracks = self.p.get("tracks") or []
        ends = [_f(c, "start", 0) + clip_duration(c) for t in tracks for c in t.get("clips", [])]
        if not ends:
            raise RuntimeError("The timeline is empty")
        self.dur = max(ends)
        bg = _hex(self.p.get("background", "#000000"))
        base_idx = self._in(["-f", "lavfi", "-i", f"color=c=0x{bg}:s={self.W}x{self.H}:r={self.fps:g}:d={self.dur:.3f}"])
        cur = f"{base_idx}:v"
        if want_video:
            for t in tracks:
                if t.get("hidden"):
                    continue
                for c in sorted(t.get("clips", []), key=lambda c: _f(c, "start", 0)):
                    if c.get("type") in ("video", "image", "text", "sticker"):
                        cur = self.visual(cur, c)
        for t in tracks:
            for c in t.get("clips", []):
                self.audio(c, bool(t.get("muted")))
        vout = self._uid("vout")
        self.chains.append(f"[{cur}]format=yuv420p[{vout}]")
        aout = None
        if self.audio_labels:
            aout = "aout"
            ins = "".join(f"[{l}]" for l in self.audio_labels)
            self.chains.append(f"{ins}amix=inputs={len(self.audio_labels)}:normalize=0:dropout_transition=0,alimiter=limit=0.95[{aout}]")
        return vout, aout, bool(self.audio_labels)


QUALITY = {"draft": (30, 28, "p3", "veryfast", 0.5), "normal": (23, 21, "p5", "medium", 1.0), "high": (18, 17, "p6", "slow", 1.0)}


def render(project: dict, preset: dict, ctx: JobContext) -> Path:
    fmt = preset.get("format", "mp4")
    q = QUALITY.get(preset.get("quality", "normal"), QUALITY["normal"])
    pr = dict(preset)
    if q[4] != 1.0 and not pr.get("width"):
        pr["width"] = int(project.get("width", 1920) * q[4]); pr["height"] = int(project.get("height", 1080) * q[4])
    b = Builder(project, pr)
    ctx.progress(0.01, "Building filter graph…")
    vout, aout, has_audio = b.build(want_video=fmt != "mp3")

    script = CACHE_DIR / f"graph_{uuid.uuid4().hex[:8]}.txt"
    script.write_text(";\n".join(b.chains), encoding="utf-8")
    name = re.sub(r'[\\/:*?"<>|]+', "_", project.get("name") or "project")[:40]
    stamp = time.strftime("%Y%m%d_%H%M%S")
    ext = {"mp4": "mp4", "webm": "webm", "gif": "mp4", "mp3": "mp3"}.get(fmt, "mp4")
    out = EXPORT_DIR / f"{name}_{stamp}.{ext}"

    def make_args(hw: bool) -> list[str]:
        args: list[str] = []
        for grp in b.inputs:
            args += grp
        args += ["-/filter_complex", str(script)]
        if fmt == "mp3":
            args += ["-map", f"[{aout}]", "-c:a", "libmp3lame", "-q:a", "2"]
        else:
            args += ["-map", f"[{vout}]"]
            if aout:
                args += ["-map", f"[{aout}]"]
            if fmt == "webm":
                args += ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1", "-c:a", "libopus"]
            elif hw:
                args += ["-c:v", "h264_nvenc", "-preset", q[2], "-rc", "vbr", "-cq", str(q[0]), "-b:v", "0", "-pix_fmt", "yuv420p"]
            else:
                args += ["-c:v", "libx264", "-preset", q[3], "-crf", str(q[1]), "-pix_fmt", "yuv420p"]
            if aout and fmt != "webm":
                args += ["-c:a", "aac", "-b:a", "192k"]
            if fmt in ("mp4", "gif"):
                args += ["-movflags", "+faststart"]
            args += ["-r", f"{b.fps:g}"]
        return [*args, "-t", f"{b.dur:.3f}", str(out)]

    hw = bool(preset.get("hwaccel", True)) and has_nvenc() and fmt in ("mp4", "gif")
    scale = (0.03, 0.97 if fmt != "gif" else 0.6)
    try:
        ctx.progress(0.03, f"Rendering {b.W}x{b.H} @{b.fps:g}fps · {'NVENC' if hw else 'CPU'}")
        try:
            run_ffmpeg(make_args(hw), total_duration=b.dur, ctx=ctx, progress_scale=scale)
        except RuntimeError as e:
            if not hw or "nvenc" not in str(e).lower() and "bitstream" not in str(e).lower() and "encoder" not in str(e).lower():
                raise
            ctx.progress(0.03, "NVENC failed, retrying with CPU encoding…")      # e.g. GPU memory pressure / odd sizes
            run_ffmpeg(make_args(False), total_duration=b.dur, ctx=ctx, progress_scale=scale)
    finally:
        script.unlink(missing_ok=True)

    if fmt == "gif":
        gif = out.with_suffix(".gif")
        run_ffmpeg(["-i", str(out), "-vf", "fps=15,scale=480:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
                    str(gif)], total_duration=b.dur, ctx=ctx, progress_scale=(0.6, 0.99))
        out.unlink(missing_ok=True)
        out = gif
    return out
