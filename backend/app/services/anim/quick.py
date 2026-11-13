"""Four-step animation flow (the Animation tab's default view):

  1. inputs   reference video (+ optional range) and a 3D character
  2. generate one job: motion capture -> source clip -> retarget onto the character -> preview video -> GLB for the 3D viewer
  3. edit     plain-language instruction ("slower", "less foot sliding", "make it loop", "only the first 2 seconds") -> LLM turns it into
              retarget settings -> re-run retarget + GLB (keyword rules when no LLM is configured)
  4. export   handled by the normal export / Export-to-engine endpoints

Every run returns the same `result` shape so the UI can just replace its state.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ...jobs import JobContext
from . import db, library

DEFAULTS: dict[str, Any] = {"in_place": True, "loop": True, "foot_lock": True, "ground_fix": True, "smooth": 0.5, "scale": 1.0, "speed": 1.0}
EDITABLE = {
    "speed": ("float 0.25..3", "playback speed multiplier (0.5 = half speed, 2 = twice as fast)"),
    "smooth": ("float 0..1", "temporal smoothing; higher removes jitter but softens fast moves"),
    "foot_lock": ("bool", "pin feet while they touch the ground (removes foot sliding)"),
    "ground_fix": ("bool", "keep feet on the ground plane (no floating / sinking)"),
    "loop": ("bool", "blend the end into the start so the clip loops seamlessly"),
    "in_place": ("bool", "remove forward travel (game moves the character) - true for game loops"),
    "scale": ("float 0.5..2", "motion amplitude relative to the character size"),
    "start_s": ("float seconds or null", "trim: start time inside the captured motion"),
    "end_s": ("float seconds or null", "trim: end time inside the captured motion"),
}


class _Sub:
    """Maps a sub-task's 0..1 progress into [lo, hi] of the parent job."""

    def __init__(self, ctx: JobContext, lo: float, hi: float, label: str):
        self.ctx, self.lo, self.hi, self.label = ctx, lo, hi, label
        self.job = ctx.job

    def progress(self, p: float, message: str | None = None) -> None:
        self.ctx.progress(self.lo + (self.hi - self.lo) * max(0.0, min(1.0, float(p))), f"{self.label}: {message}" if message else self.label)

    @property
    def cancelled(self) -> bool:
        return self.ctx.cancelled

    def check(self) -> None:
        self.ctx.check()

    def register_process(self, proc) -> None:
        self.ctx.register_process(proc)


def _to_retarget_opts(o: dict, fps: float) -> dict:
    out = {k: o[k] for k in ("in_place", "loop", "loop_window", "foot_lock", "ground_fix", "smooth", "scale", "speed") if k in o and o[k] is not None}
    if o.get("start_s") is not None:
        out["start"] = int(round(float(o["start_s"]) * fps))
    if o.get("end_s") is not None:
        out["end"] = int(round(float(o["end_s"]) * fps))
    return out


def _retarget_and_export(ctx: JobContext, src_id: str, rig_id: str, opts: dict, lo: float, name: str | None = None) -> dict:
    """Retarget -> three-view preview video (front | side | top) -> game GLB."""
    from . import multiview
    src = db.get_clip(src_id)
    if not src:
        raise RuntimeError("source motion not found")
    ropts = {**_to_retarget_opts(opts, float(src["fps"] or 30)), "preview": False}
    if name:
        ropts["name"] = name
    span = 0.97 - lo
    rt = library.retarget(_Sub(ctx, lo, lo + span * 0.45, "Retargeting"), src_id, rig_id, ropts)
    clip = rt["clip"]
    media = None
    try:
        media = multiview.render(_Sub(ctx, lo + span * 0.45, lo + span * 0.85, "Preview (front | side | top)"), clip["id"]).get("media")
        db.update_clip(clip["id"], preview_media_id=media["id"])
    except Exception as e:  # noqa: BLE001  (the preview is cosmetic: never lose the clip over it)
        rt.setdefault("notes", []).append(f"three-view preview failed: {str(e)[:200]}")
    ex = library.export(_Sub(ctx, lo + span * 0.85, 0.97, "Building GLB"), clip["id"], "glb", {"preset": "game", "verify": False, "all_actions": False}, None, rig_id)
    ctx.progress(0.99, "Done")
    rig = db.get_rig(rig_id) or {}
    clip = library._pub(db.get_clip(clip["id"])) or clip
    return {"source_clip": library._pub(src), "clip": clip, "metrics": rt.get("metrics"), "notes": rt.get("notes"), "media": media,
            "glb_url": ex["url"], "export_id": (ex.get("exports") or [{}])[0].get("id"), "options": opts, "rig": {"id": rig_id, "name": rig.get("name")},
            "score": clip.get("score")}


def generate(ctx: JobContext, media_id: str, rig_id: str, start: float | None = None, end: float | None = None, options: dict | None = None,
             subject: str | None = None) -> dict:
    from ..mocap.pipeline import MocapRequest, run_mocap
    from ... import db as media_db
    rig = db.get_rig(rig_id)
    if not rig:
        raise RuntimeError("character not found")
    row = media_db.get_media(media_id)
    if not row or row["kind"] != "video":
        raise RuntimeError("reference video not found")
    subj = subject or ("quadruped" if rig.get("kind") == "quadruped" else "humanoid" if rig.get("kind") in ("humanoid", "biped") else "auto")
    opts = {**DEFAULTS, **{k: v for k, v in (options or {}).items() if v is not None and k not in ("camera_pitch", "pose_variant")}}
    ctx.progress(0.01, "Motion capture: starting")
    # generated reference clips are made with a level, locked-off side-view prompt: tell the fitter instead of letting it guess the pitch
    pitch = (options or {}).get("camera_pitch")
    if pitch is None and row.get("origin") == "gen" and ((row.get("meta") or {}).get("capture_friendly") or "videogen" in (row.get("tags") or [])):
        pitch = 0.0
    mr = MocapRequest(mediaId=media_id, start=start, end=end, subject=subj, backend="auto", rigId=rig_id, overlay=False, camera_pitch=pitch,
                     pose_variant=(options or {}).get("pose_variant"))   # None = most accurate installed ViTPose++
    r = run_mocap(_Sub(ctx, 0.01, 0.55, "Motion capture"), mr)
    r = r.get("result", r)
    motion = r.get("motion_file")
    if not motion:
        raise RuntimeError("Motion capture finished without a motion file.")
    imp = library.import_clip(_Sub(ctx, 0.55, 0.6, "Saving motion"), motion, f"{row['name'][:60]} (motion)", {})
    src = imp["clip"]
    out = _retarget_and_export(ctx, src["id"], rig_id, opts, 0.6, f"{row['name'][:50]} on {rig['name']}")
    out["mocap"] = {"stats": r.get("stats"), "gait": r.get("gait"), "notes": r.get("notes"), "backend": r.get("backend")}
    out["history"] = [{"instruction": "Generated from the reference video", "options": opts}]
    return out


# ---------------------------------------------------------------------------------------------- LLM edit
SYSTEM = ("You edit game animation clips. The user describes a change in plain language (any language). You may ONLY change these "
          "retarget settings:\n" + "\n".join(f"- {k} ({t}): {d}" for k, (t, d) in EDITABLE.items()) +
          "\nReply with JSON only: {\"options\": {<only the keys you change>}, \"explanation\": \"<one short English sentence>\", "
          "\"unsupported\": \"<what part of the request these settings cannot do, or empty>\"}")


def _rules(instruction: str, cur: dict, duration: float) -> tuple[dict, str, str]:
    t = instruction.lower()
    ch: dict[str, Any] = {}
    sp = float(cur.get("speed") or 1)
    if re.search(r"slow|慢", t):
        ch["speed"] = round(max(0.25, sp * (0.5 if re.search(r"much|very|很|非常|一半|half", t) else 0.75)), 3)
    if re.search(r"fast|quick|快", t):
        ch["speed"] = round(min(3.0, sp * (2.0 if re.search(r"much|very|很|非常|double|两倍", t) else 1.33)), 3)
    if re.search(r"jitter|shak|smooth|抖|平滑|顺", t):
        ch["smooth"] = round(min(1.0, float(cur.get("smooth", 0.5)) + 0.2), 2)
    if re.search(r"sharp|snapp|crisp|less smooth|干脆", t):
        ch["smooth"] = round(max(0.0, float(cur.get("smooth", 0.5)) - 0.2), 2)
    if re.search(r"slid|skat|滑", t):
        ch["foot_lock"] = True
    if re.search(r"float|sink|ground|地面|飘|穿", t):
        ch["ground_fix"] = True
    if re.search(r"loop|循环", t):
        ch["loop"] = not re.search(r"no loop|don't loop|不循环", t)
    if re.search(r"in place|原地", t):
        ch["in_place"] = True
    if re.search(r"root motion|move forward|向前|位移", t):
        ch["in_place"] = False
    if re.search(r"bigger|exaggerat|larger|夸张|幅度大", t):
        ch["scale"] = round(min(2.0, float(cur.get("scale", 1)) * 1.2), 2)
    if re.search(r"smaller|subtle|幅度小", t):
        ch["scale"] = round(max(0.5, float(cur.get("scale", 1)) / 1.2), 2)
    m = re.search(r"(first|前)\s*(\d+(?:\.\d+)?)\s*(s|sec|秒)", t)
    if m:
        ch["start_s"], ch["end_s"] = 0.0, float(m.group(2))
    return ch, ("Applied: " + ", ".join(f"{k} = {v}" for k, v in ch.items())) if ch else "", "" if ch else "No setting matched this request."


def plan_edit(instruction: str, cur: dict, metrics: dict | None, duration: float) -> dict:
    from ...services import llm
    used = "rules"
    try:
        msg = (f"Current settings: {json.dumps(cur)}\nClip duration: {duration:.2f}s\nQuality metrics: {json.dumps(metrics or {})[:1500]}\n"
               f"Request: {instruction}")
        r = llm.chat([{"role": "user", "content": msg}], system=SYSTEM, max_tokens=400, temperature=0.1, timeout=90)
        txt = r.get("text") or ""
        m = re.search(r"\{.*\}", txt, re.S)
        j = json.loads(m.group(0)) if m else {}
        ch = {k: v for k, v in (j.get("options") or {}).items() if k in EDITABLE}
        expl, uns = j.get("explanation") or "", j.get("unsupported") or ""
        used = f"{r.get('provider')}:{r.get('model')}"
        if not ch and not uns:
            ch, expl, uns = _rules(instruction, cur, duration)
            used += " + rules"
    except Exception as e:  # noqa: BLE001  (no LLM configured / offline)
        ch, expl, uns = _rules(instruction, cur, duration)
        used = f"rules (LLM unavailable: {str(e)[:80]})"
    # clamp
    if "speed" in ch:
        ch["speed"] = max(0.25, min(3.0, float(ch["speed"])))
    if "smooth" in ch:
        ch["smooth"] = max(0.0, min(1.0, float(ch["smooth"])))
    if "scale" in ch:
        ch["scale"] = max(0.5, min(2.0, float(ch["scale"])))
    for k in ("start_s", "end_s"):
        if k in ch and ch[k] is not None:
            ch[k] = max(0.0, min(duration, float(ch[k])))
    return {"changes": ch, "explanation": expl, "unsupported": uns, "planner": used}


def edit(ctx: JobContext, source_clip_id: str, rig_id: str, instruction: str, options: dict | None = None, metrics: dict | None = None,
         history: list | None = None) -> dict:
    src = db.get_clip(source_clip_id)
    if not src:
        raise RuntimeError("source motion not found")
    cur = {**DEFAULTS, **(options or {})}
    ctx.progress(0.03, "Understanding the request")
    plan = plan_edit(instruction, cur, metrics, float(src.get("duration") or 0))
    if not plan["changes"]:
        raise RuntimeError(plan["unsupported"] or "Nothing to change for this request.")
    new = {**cur, **plan["changes"]}
    out = _retarget_and_export(ctx, source_clip_id, rig_id, new, 0.08)
    out["edit"] = plan
    out["history"] = list(history or []) + [{"instruction": instruction, "options": new, "explanation": plan["explanation"], "planner": plan["planner"]}]
    return out


def rerun(ctx: JobContext, source_clip_id: str, rig_id: str, options: dict, history: list | None = None) -> dict:
    """Manual settings change (sliders) or undo to an earlier step."""
    out = _retarget_and_export(ctx, source_clip_id, rig_id, {**DEFAULTS, **options}, 0.03)
    out["history"] = list(history or [])
    return out


# ---------------------------------------------------------------------------------------------- text -> motion (Kimodo)
_CYCLIC = re.compile(r"\b(walk|walks|walking|jog|jogs|jogging|run|runs|running|sprint\w*|march\w*|sneak\w*|tiptoe\w*|stroll\w*|limp\w*|"
                     r"crawl\w*|swim\w*|idle\w*|breath\w*|loop\w*|cycle|in place)\b|循环|走路|行走|慢跑|跑步|奔跑|潜行|待机|呼吸|原地", re.I)
_ONE_SHOT = re.compile(r"\b(sit|sits|sitting|stand up|stands up|gets? up|lies? down|jump\w*|fall\w*|pick\w* up|throw\w*|attack\w*|punch\w*|"
                       r"kick\w*|swings? (?:a|an|the|his|her|their|its)|strike\w*|wave\w*|turns?|stops?|starts?|then|toward\w*|into|reach\w*|grab\w*|climb\w*)\b|"
                       r"坐|站起|躺|跳|摔|倒|捡|扔|攻击|出拳|踢|挥|转身|停下|然后|走向|跑向|爬上", re.I)


def is_cyclic_prompt(prompt: str) -> bool:
    """Motions that repeat (walk, run, idle...) and are not a one-off action ('walks to the chair and sits down')."""
    return bool(_CYCLIC.search(prompt or "")) and not _ONE_SHOT.search(prompt or "")

def generate_text(ctx: JobContext, prompt: str, rig_id: str, duration: float = 4.0, seed: int | None = None, options: dict | None = None) -> dict:
    """'Describe the motion' path of step 1: Kimodo text-to-motion -> BVH -> source clip -> same retarget/preview/GLB as the video path."""
    from . import kimodo
    rig = db.get_rig(rig_id)
    if not rig:
        raise RuntimeError("character not found")
    if rig.get("kind") == "quadruped":
        raise RuntimeError("Text-to-motion (Kimodo) makes humanoid motion only; use a reference video for four-legged characters.")
    bvhs = kimodo.generate(_Sub(ctx, 0.0, 0.5, "Text to motion"), prompt, duration, 1, seed)
    short = re.sub(r"[^\w\- ]+", "", prompt).strip()[:48] or "motion"
    imp = library.import_clip(_Sub(ctx, 0.5, 0.58, "Saving motion"), str(bvhs[0]), f"Kimodo: {short}", {})
    src = imp["clip"]
    try:                                                             # remember the prompt on the clip (search, provenance)
        cur = db.get_clip(src["id"]) or {}
        db.update_clip(src["id"], caption=prompt, tags=sorted(set((cur.get("tags") or []) + ["kimodo", "text-to-motion"])),
                       source={**(cur.get("source") or {}), "kimodo": {"prompt": prompt, "seed": seed, "duration": duration}})
    except Exception:  # noqa: BLE001
        pass
    user = {k: v for k, v in (options or {}).items() if v is not None}
    opts = {**DEFAULTS, "loop": False, **user}
    if "loop" not in user and is_cyclic_prompt(prompt):
        # walk / run / idle ...: loop it, but only if the retargeter finds a repeating cycle (else it stays a one-shot clip)
        opts.update(loop=True, loop_window="require")
    out = _retarget_and_export(ctx, src["id"], rig_id, opts, 0.6, f"{short} on {rig['name']}")
    if opts.get("loop") and not out["clip"].get("loop"):
        out["options"] = opts = {**opts, "loop": False}
    opts.pop("loop_window", None)
    out["text"] = {"prompt": prompt, "seed": seed, "duration": duration}
    out["history"] = [{"instruction": f"Generated from text: {prompt}", "options": opts}]
    return out


# ---------------------------------------------------------------------------------------------- MotionBricks transitions
def soma_source(clip_id: str) -> tuple[dict, str]:
    """A clip (source or retargeted) -> its SOMA source clip and BVH path, or a clear error."""
    from . import motionbricks
    c = db.get_clip(clip_id)
    if not c:
        raise RuntimeError(f"clip {clip_id} not found")
    src = db.get_clip(c["parent_clip_id"]) if c.get("rig_id") and c.get("parent_clip_id") else c
    path = ((src or {}).get("source") or {}).get("path") or ""
    if not path.lower().endswith(".bvh"):
        raise RuntimeError(f"'{c['name']}' has no SOMA BVH motion; transitions work on Kimodo (text-to-motion) clips.")
    motionbricks._check_soma(motionbricks.read_bvh(path))
    return src, path


def transition(ctx: JobContext, clip_a: str, clip_b: str | None, rig_id: str | None, loop: bool = False, distance: float | None = None,
               turn_deg: float = 0.0, options: dict | None = None) -> dict:
    """MotionBricks: A + generated transition + B (or A closed into a seamless loop) -> SOMA source clip -> retarget onto a character."""
    import time as _time
    from . import motionbricks
    src_a, pa = soma_source(clip_a)
    src_b, pb = (src_a, pa) if (loop or not clip_b) else soma_source(clip_b)
    loop = loop or not clip_b
    if not rig_id:
        ca = db.get_clip(clip_a) or {}
        rig_id = ca.get("rig_id") or next((r["id"] for r in db.list_rigs() if r.get("kind") == "humanoid"), None)
    rig = db.get_rig(rig_id or "")
    if not rig:
        raise RuntimeError("character not found")
    ctx.progress(0.03, "MotionBricks: generating the transition")
    out = motionbricks.OUT_DIR / _time.strftime("%Y%m%d_%H%M%S") / ("loop.bvh" if loop else "transition.bvh")
    info = motionbricks.join(pa, pb, out, distance=distance, turn_deg=turn_deg, loop=loop)
    nice = lambda n: re.sub(r"^(Kimodo|MotionBricks):\s*|A person ", "", n).strip()[:32]
    na, nb = nice(src_a["name"]), nice(src_b["name"])
    title = f"Loop: {na}" if loop else f"{na} -> {nb}"
    imp = library.import_clip(_Sub(ctx, 0.1, 0.18, "Saving motion"), str(out), f"MotionBricks: {title}", {})
    src = imp["clip"]
    try:
        cur = db.get_clip(src["id"]) or {}
        db.update_clip(src["id"], caption=("Seamless loop of " if loop else "Transition from ") + src_a["name"] + ("" if loop else f" to {src_b['name']}"),
                       tags=sorted(set((cur.get("tags") or []) + ["motionbricks", "loop" if loop else "transition"])),
                       source={**(cur.get("source") or {}), "motionbricks": {**info, "clip_a": src_a["id"], "clip_b": None if loop else src_b["id"]}})
    except Exception:  # noqa: BLE001
        pass
    user = {k: v for k, v in (options or {}).items() if v is not None}
    opts = {**DEFAULTS, "loop": loop, **user}
    if opts.get("loop") and loop:
        opts["loop_window"] = "none"                 # the whole clip is the loop: A plus the way back to its start
    res = _retarget_and_export(ctx, src["id"], rig["id"], opts, 0.2, f"{title} on {rig['name']}")
    opts.pop("loop_window", None)
    res["options"] = opts
    res["motionbricks"] = info
    res["history"] = [{"instruction": "MotionBricks " + ("loop" if loop else "transition"), "options": opts}]
    return res


# ---------------------------------------------------------------------------------------------- auto-tune
def penalties(m: dict) -> dict[str, float]:
    """The same terms as metrics.score(), so we know which problem costs the most points."""
    return {
        "foot_slide": min(35, max(0, m.get("foot_slide_pct_height_per_s", 0) - 3) * 0.9),
        "penetration": min(20, m.get("ground_penetration_max_pct_height", 0) * 2),
        "floating": min(15, m.get("floating_mean_pct_height", 0) * 2),
        "jitter": min(15, max(0, m.get("jitter_p95_deg", 0) - 1) * 3),
        "loop_seam": min(10, max(0, m.get("loop_seam_vs_step", 0) - 1) * 2) if m.get("is_loop", True) else 0.0,
        "stretch": min(5, m.get("bone_stretch_max_pct", 0)),
        "pose_back_lean": min(25, max(0, m.get("torso_back_lean_p90_deg", 0) - 25) * 0.8),
        "pose_knees": min(20, max(0, m.get("knee_backward_p90_pct_height", 0) - 2) * 3),
    }


MIN_SCALE_FACTOR = 0.8      # auto-tune never shrinks the motion below 80% of the scale the user started from


def _candidates(worst: str, o: dict, m: dict, fps: float, frames: int, scale0: float = 1.0) -> list[tuple[str, dict]]:
    """Settings changes to try against the worst problem. Never touches what the user chose on purpose: `loop` and `in_place`
    stay as they are (the retargeter already picks the best whole-cycle window for loops), and strides shrink at most to
    MIN_SCALE_FACTOR of the starting scale - smaller strides hide sliding but visibly change the motion."""
    sm = float(o.get("smooth", 0.5))
    sc = float(o.get("scale", 1.0))
    floor = round(scale0 * MIN_SCALE_FACTOR, 3)
    c: list[tuple[str, dict]] = []

    def shrink(f: float) -> list[tuple[str, dict]]:
        s = round(max(floor, sc * f), 3)
        return [(f"smaller strides (scale {s:g})", {"scale": s})] if s < sc - 1e-3 else []
    if worst == "foot_slide":
        c += [("lock feet to the ground", {"foot_lock": True, "ground_fix": True}), *shrink(0.9),
              ("more smoothing", {"smooth": round(min(0.95, sm + 0.2), 2)})]
    elif worst in ("penetration", "floating"):
        c += [("snap feet to the ground", {"ground_fix": True, "foot_lock": True}), *shrink(0.9),
              ("more smoothing", {"smooth": round(min(0.95, sm + 0.2), 2)})]
    elif worst == "jitter":
        c += [("more smoothing", {"smooth": round(min(0.95, sm + 0.2), 2)}),
              ("much more smoothing", {"smooth": round(min(0.95, sm + 0.4), 2)})]
    elif worst in ("loop_seam", "stretch"):
        c += [("more smoothing", {"smooth": round(min(0.95, sm + 0.2), 2)})]
    return c


def autotune(ctx: JobContext, source_clip_id: str, rig_id: str, options: dict | None = None, target: float = 90.0, max_tries: int = 10,
             history: list | None = None) -> dict:
    """Try settings changes aimed at whatever costs the most points (foot sliding, ground contact, jitter, loop seam), keep the best,
    stop at `target`. Trial runs skip the preview and GLB and are deleted; only the winner is kept and rendered."""
    from . import metrics as M
    src = db.get_clip(source_clip_id)
    if not src:
        raise RuntimeError("source motion not found")
    fps = float(src["fps"] or 30)
    best_o = {**DEFAULTS, **(options or {})}
    scale0 = float(best_o.get("scale") or 1.0)
    log: list[dict] = []
    tried: set[str] = set()

    def trial(o: dict, label: str) -> float:
        key = json.dumps(o, sort_keys=True)
        if key in tried:
            return -1.0
        tried.add(key)
        n = len(log)
        sub = _Sub(ctx, 0.02 + 0.8 * n / max_tries, 0.02 + 0.8 * (n + 1) / max_tries, f"Try {n + 1}: {label}")
        r = library.retarget(sub, source_clip_id, rig_id, {**_to_retarget_opts(o, fps), "preview": False, "name": f"autotune trial {n + 1}"})
        m = r.get("metrics") or {}
        s = float(M.score(m))
        log.append({"try": n + 1, "change": label, "score": s, "options": o, "penalties": {k: round(v, 1) for k, v in penalties(m).items() if v > 0.5},
                    "metrics": m, "frames": r["clip"]["frames"]})
        try:
            library.delete_clip(r["clip"]["id"])
        except Exception:  # noqa: BLE001
            pass
        return s

    best = trial(best_o, "current settings")
    best_m = log[-1]["metrics"]
    while best < target and len(log) < max_tries:
        pens = penalties(best_m)
        order = [k for k, v in sorted(pens.items(), key=lambda kv: -kv[1]) if v > 0.5]
        improved = False
        for worst in order:
            for label, change in _candidates(worst, best_o, best_m, fps, log[0]["frames"], scale0):
                if len(log) >= max_tries:
                    break
                o = {**best_o, **change}
                s = trial(o, label)
                if s > best + 0.5:
                    best, best_o, best_m, improved = s, o, log[-1]["metrics"], True
                    break
            if improved or len(log) >= max_tries:
                break
        if not improved:
            break
    ctx.progress(0.83, f"Best score {best:.0f}: building the final clip")
    out = _retarget_and_export(ctx, source_clip_id, rig_id, best_o, 0.84)
    reached = (out["clip"].get("score") or best) >= target
    left = {k: round(v, 1) for k, v in penalties(out.get("metrics") or {}).items() if v > 0.5}
    out["autotune"] = {"target": target, "reached": reached, "best_score": out["clip"].get("score"), "tries": [{k: v for k, v in t.items() if k != "metrics"} for t in log],
                       "remaining": left}
    out["history"] = list(history or []) + [{"instruction": f"Auto-tune to {target:.0f}+ ({'reached' if reached else 'best ' + str(round(best))})",
                                             "options": best_o}]
    return out
