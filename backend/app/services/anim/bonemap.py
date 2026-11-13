"""Bone-map suggestion: target rig bones  ->  source skeleton joints.

Convention (important for retargeting): a mapping `bone -> joint` means "the HEAD (start) of this bone sits at that joint"
(Mixamo `LeftUpLeg` -> `left_hip`, `LeftLeg` -> `left_knee`, `LeftFoot` -> `left_ankle`).

Heuristics: name parsing (Mixamo / Blender Rigify / Unreal mannequin / Biped / generic quadruped names, L/R tokens, front/hind),
geometric fallback for numbered limb chains (`leg_1/2/3`), hierarchy-based spine alignment. Optional LLM refinement through
`app.services.llm.chat` (only bone names + hierarchy are sent, never images).
"""
from __future__ import annotations

import hashlib
import json
import re

import numpy as np

from . import skeletons as sk

ROLE_LIMB = ("clav", "upper", "mid", "end", "toe")


def skeleton_key(skeleton: str, joint_names: list[str]) -> str:
    if skeleton in sk.BUILTIN:
        return skeleton
    return "custom:" + hashlib.sha1("|".join(joint_names).encode()).hexdigest()[:8]


def default_source(kind: str) -> str:
    return "ap10k17" if kind == "quadruped" else "smpl24"


def source_info(skeleton: str, joint_names: list[str] | None = None, parents: list[int] | None = None):
    """-> (joint_names, parents)"""
    if skeleton in sk.BUILTIN and not joint_names:
        n, p = sk.BUILTIN[skeleton]
        return list(n), list(p)
    return list(joint_names or []), (list(parents) if parents is not None else None)


# ----------------------------------------------------------------------------------------------- analysis of the target rig
def _analyse(bones: list[dict]):
    names = [b["name"] for b in bones]
    idx = {n: i for i, n in enumerate(names)}
    parent = [idx.get(b.get("parent"), -1) if b.get("parent") else -1 for b in bones]
    children = [[] for _ in bones]
    for i, p in enumerate(parent):
        if p >= 0:
            children[p].append(i)
    depth = [0] * len(bones)
    for i in range(len(bones)):
        d, p = 0, parent[i]
        while p >= 0:
            d, p = d + 1, parent[p]
        depth[i] = d
    head = np.array([b["head"] for b in bones], float)
    tail = np.array([b["tail"] for b in bones], float)
    parsed = [sk.parse_name(n) for n in names]
    sk.fix_chain_names(names, parsed)
    return names, idx, parent, children, depth, head, tail, parsed


def _is_desc(parent, a, anc):
    while a >= 0:
        if a == anc:
            return True
        a = parent[a]
    return False


def classify_target(bones: list[dict]) -> dict:
    """Parse + geometric completion. -> {parsed:[...], notes:[...], pelvis:int|None, head:int|None, forward:(x,y)|None}"""
    names, idx, parent, children, depth, head, tail, parsed = _analyse(bones)
    notes: list[str] = []
    n = len(names)
    conf = [0.95] * n
    # pelvis: prefer a side-less pelvis bone with the lowest depth
    cand = [i for i in range(n) if parsed[i]["role"] == "pelvis"]
    cand.sort(key=lambda i: (parsed[i]["side"] is not None, depth[i]))
    pelvis = cand[0] if cand else None
    for i in cand[1:]:
        parsed[i]["role"] = "other"            # Rigify pelvis.L/.R helpers
    # Mixamo-style: "Shoulder" is the clavicle when its child is also an upper-limb bone
    for i in range(n):
        p = parsed[i]
        if p["role"] == "upper" and p["limb"] == "front" and any(parsed[c]["role"] == "upper" and parsed[c]["limb"] == "front" for c in children[i]):
            p["role"] = "clav"
    # parent-based demotion for duplicates, e.g. both "arm_01"/"arm_02" parsed as upper
    for i in range(n):
        p = parsed[i]
        if p["role"] in ("upper", "mid", "end") and parent[i] >= 0:
            pp = parsed[parent[i]]
            if pp["role"] == p["role"] and pp["limb"] == p["limb"] and pp["side"] == p["side"]:
                order = {"upper": 0, "mid": 1, "end": 2}
                # numbered chain (leg_01, leg_02, leg_03): promote by order below
                p["_chain_dup"] = True
    # fill in missing side from geometry (character-left = +X in the normalised rig)
    fwd = None
    hd = [i for i in range(n) if parsed[i]["role"] == "head"] or [i for i in range(n) if parsed[i]["role"] == "neck"]
    head_i = hd[0] if hd else None
    if pelvis is not None and head_i is not None:
        v = head[head_i] - head[pelvis]
        h = v[:2]
        if np.linalg.norm(h) > 1e-4 and np.linalg.norm(h) > 0.5 * abs(v[2]):
            fwd = h / np.linalg.norm(h)
    height = float(max(tail[:, 2].max() - tail[:, 2].min(), 1e-3))
    centre_x = float(head[pelvis][0]) if pelvis is not None else 0.0

    def side_of(i):
        x = (head[i][0] + tail[i][0]) / 2 - centre_x
        return "L" if x > 0 else "R"
    for i in range(n):
        if parsed[i]["role"] in ROLE_LIMB and parsed[i]["side"] is None:
            if abs((head[i][0] + tail[i][0]) / 2 - centre_x) > 0.02 * height:
                parsed[i]["side"] = side_of(i)
                conf[i] = min(conf[i], 0.6)
    # geometric limb chains for unclassified bones (e.g. "leg_1", "Bone.012")
    unclassified = [i for i in range(n) if parsed[i]["role"] == "other" and not parsed[i]["tokens"] == []]
    spineish = {i for i in range(n) if parsed[i]["role"] in ("pelvis", "spine", "chest", "neck", "clav")}
    for i in unclassified:
        if parent[i] >= 0 and (parent[i] in spineish) and abs(head[i][0] - centre_x) > 0.03 * height:
            chain = [i]
            while True:
                kids = [c for c in children[chain[-1]] if parsed[c]["role"] in ("other", "mid", "end", "upper", "toe")]
                if not kids:
                    break
                chain.append(min(kids, key=lambda c: head[c][2]))
            if len(chain) >= 3:
                # which end of the body?
                ref = head[pelvis] if pelvis is not None else head[chain[0]]
                if fwd is not None and head_i is not None:
                    along = float(np.dot(head[chain[0]][:2] - (head[pelvis][:2] + head[head_i][:2]) / 2, fwd))
                    limb = "front" if along > 0 else "hind"
                else:
                    limb = "hind"
                for role, c in zip(("upper", "mid", "end", "toe"), chain):
                    parsed[c].update({"role": role, "limb": limb, "side": side_of(c)})
                    conf[c] = 0.6
                notes.append(f"Limb chain starting at '{names[i]}' classified geometrically as {limb} leg ({side_of(i)}).")
    # numbered-duplicates inside a limb (upper,upper,upper) -> upper, mid, end by order
    for i in range(n):
        if parsed[i].get("_chain_dup") and parsed[i]["role"] == "upper":
            chain = [i]
            while True:
                kid = [c for c in children[chain[-1]] if parsed[c]["role"] == "upper" and parsed[c]["limb"] == parsed[i]["limb"]]
                if not kid:
                    break
                chain.append(kid[0])
            start = i
            p = parent[i]
            if p >= 0 and parsed[p]["role"] == "upper":
                continue
            for role, c in zip(("upper", "mid", "end"), chain):
                parsed[c]["role"] = role
                conf[c] = 0.7
    for p in parsed:
        p.pop("_chain_dup", None)
    # quadruped limb group fallback: limbs without front/hind get it from geometry
    for i in range(n):
        p = parsed[i]
        if p["role"] in ROLE_LIMB and p["limb"] is None:
            if fwd is not None and head_i is not None and pelvis is not None:
                along = float(np.dot(head[i][:2] - (head[pelvis][:2] + head[head_i][:2]) / 2, fwd))
                p["limb"] = "front" if along > 0 else "hind"
            else:
                p["limb"] = "hind"
            conf[i] = min(conf[i], 0.6)
    return {"parsed": parsed, "conf": conf, "notes": notes, "pelvis": pelvis, "head": head_i, "forward": fwd,
            "names": names, "parent": parent, "children": children, "depth": depth, "height": height}


# ----------------------------------------------------------------------------------------------- suggestion
def _spine_order(joint_names, roles):
    """Spine joints bottom -> top: numbered spine joints by number, chest/upper-chest after them, ties by file order."""
    js = [((1 if roles[n]["role"] == "chest" else 0), roles[n]["idx"] if roles[n]["idx"] is not None else 0, k, n)
          for k, n in enumerate(joint_names) if roles[n]["role"] in ("spine", "chest")]
    js.sort(key=lambda t: t[:3])
    return [t[3] for t in js]


def suggest(bones: list[dict], skeleton: str, joint_names: list[str] | None = None, parents: list[int] | None = None) -> dict:
    jn, jp = source_info(skeleton, joint_names, parents)
    if not jn:
        return {"map": {}, "confidence": {}, "unmapped": [b["name"] for b in bones], "notes": ["Unknown source skeleton."], "overall": 0.0}
    ci = classify_target(bones)
    parsed, conf, names, parent, depth = ci["parsed"], ci["conf"], ci["names"], ci["parent"], ci["depth"]
    sroles = sk.source_roles(jn)
    out: dict[str, str] = {}
    cf: dict[str, float] = {}
    notes: list[str] = list(ci["notes"])
    used_j: set[str] = set()

    def put(bone_i, joint, c):
        if joint in used_j or names[bone_i] in out:
            return
        out[names[bone_i]] = joint
        cf[names[bone_i]] = round(float(c), 2)
        used_j.add(joint)

    # same skeleton family (e.g. UE5 Manny/Quinn animations from GASP / Lyra): identical bone names win. Helper bones such as
    # 'spine_04_latissimus_l' otherwise look like spine joints to the heuristics below and fold the torso.
    core = {"pelvis", "spine", "chest", "neck", "head", "clav", "upper", "mid", "end", "toe"}
    jl = {j.lower(): j for j in jn}
    core_bones = [i for i, p in enumerate(parsed) if p["role"] in core]
    exact = [i for i in core_bones if names[i].lower() in jl]
    if core_bones and len(exact) >= 0.6 * len(core_bones):
        for i in exact:
            put(i, jl[names[i].lower()], 0.98)
        notes.append(f"Same skeleton naming: {len(exact)} bones matched by exact name.")

    def bones_with(role, side=None, limb=None, any_side=False):
        r = [i for i, p in enumerate(parsed) if p["role"] == role and (any_side or p["side"] == side) and (limb is None or p["limb"] == limb)]
        r.sort(key=lambda i: depth[i])
        return r

    # pelvis
    jp_ = [j for j in jn if sroles[j]["role"] == "pelvis"]
    if jp_ and ci["pelvis"] is not None:
        put(ci["pelvis"], jp_[0], 0.95)
    elif ci["pelvis"] is None:
        notes.append("No pelvis/hips bone recognised.")
    # spine
    sj = _spine_order(jn, sroles)
    sb = [i for i, p in enumerate(parsed) if p["role"] in ("spine", "chest") and (ci["pelvis"] is None or _is_desc(parent, i, ci["pelvis"]))]
    sb.sort(key=lambda i: depth[i])
    if sj and sb:
        k, m = len(sj), len(sb)
        if k == m:
            pairs = list(zip(sb, sj))
            c = 0.95
        elif m > k:
            sel = [int(round(x)) for x in np.linspace(0, m - 1, k)]
            pairs = [(sb[s], sj[q]) for q, s in enumerate(sel)]
            c = 0.7
        else:
            sel = [int(round(x)) for x in np.linspace(0, k - 1, m)]
            pairs = [(sb[q], sj[s]) for q, s in enumerate(sel)]
            c = 0.7
        for b, j in pairs:
            put(b, j, c)
        if k != m:
            notes.append(f"Spine: {m} bones vs {k} source joints - matched evenly.")
    # neck / head
    jneck = [j for j in jn if sroles[j]["role"] == "neck"]
    jhead = [j for j in jn if sroles[j]["role"] == "head"]
    bneck = bones_with("neck", any_side=True)
    bhead = bones_with("head", any_side=True)
    if jneck and jhead:
        if bneck:
            put(bneck[0], jneck[0], 0.95)
        if bhead:
            put(bhead[0], jhead[0], 0.95)
    elif jneck and not jhead:
        # AP-10K / COCO-like: the 'neck' joint is the head-base; the head bone starts there, the face points give its direction
        if bhead:
            put(bhead[0], jneck[0], 0.8)
            notes.append("Source has no head joint: head bone is driven from the 'neck' joint and the face points.")
        elif bneck:
            put(bneck[0], jneck[0], 0.8)
    elif jhead and bhead:
        put(bhead[0], jhead[0], 0.9)
    # limbs
    for role in ROLE_LIMB:
        for j in jn:
            sr = sroles[j]
            if sr["role"] != role or sr["side"] is None:
                continue
            limb = sr["limb"]
            cand = bones_with(role, sr["side"], limb)
            if not cand and role == "clav":
                cand = bones_with(role, sr["side"], None)
            if not cand and limb is None:
                cand = bones_with(role, sr["side"], "front") + bones_with(role, sr["side"], "hind")
            if cand:
                i = cand[0]
                c = conf[i] if conf[i] < 0.95 else 0.95
                put(i, j, c)
    # validity: along a limb the mapped bones must descend upper -> mid -> end
    inv = {names.index(b): j for b, j in out.items()}
    for role_a, role_b in (("upper", "mid"), ("mid", "end"), ("end", "toe")):
        for i, p in list(enumerate(parsed)):
            if p["role"] == role_b and i in inv:
                a_candidates = [k for k in inv if parsed[k]["role"] == role_a and parsed[k]["side"] == p["side"] and parsed[k]["limb"] == p["limb"]]
                for a in a_candidates:
                    if not _is_desc(parent, i, a):
                        notes.append(f"'{names[i]}' is not below '{names[a]}' in the hierarchy; mapping dropped.")
                        out.pop(names[i], None)
                        cf.pop(names[i], None)
                        inv.pop(i, None)
                        break
    unmapped = [n for n in names if n not in out]
    deform = {b["name"]: b.get("deform", True) for b in bones}
    missing_j = [j for j in jn if j not in used_j and sroles[j]["role"] in ("pelvis", "spine", "chest", "neck", "head", "upper", "mid", "end")]
    if missing_j:
        notes.append("Source joints without a matching bone: " + ", ".join(missing_j) + ".")
    essential = [n for n in unmapped if deform.get(n, True) and parsed[names.index(n)]["role"] in ("upper", "mid", "end", "head", "pelvis")]
    if essential:
        notes.append("Important bones left unmapped: " + ", ".join(essential) + ".")
    overall = float(np.mean(list(cf.values()))) if cf else 0.0
    # coverage penalty: the share of source limb joints that found a bone
    limbj = [j for j in jn if sroles[j]["role"] in ("upper", "mid", "end", "pelvis", "head", "neck")]
    cover = (sum(1 for j in limbj if j in used_j) / len(limbj)) if limbj else 0
    return {"map": out, "confidence": cf, "unmapped": unmapped, "notes": notes, "overall": round(overall * (0.4 + 0.6 * cover), 2),
            "coverage": round(cover, 2), "source": skeleton, "source_joints": jn}


# ----------------------------------------------------------------------------------------------- LLM refinement
LLM_SYSTEM = (
    "You map bones of a 3D character rig onto the joints of a motion-capture skeleton. "
    "A mapping 'bone -> joint' means the START (head) of the bone sits at that joint. "
    "Answer with ONE JSON object only: {\"map\": {\"<bone name>\": \"<joint name or null>\"}, \"notes\": [\"...\"]}. "
    "Use only the exact bone and joint names given. Each joint may be used at most once. Leave bones with no counterpart out of the map or null. "
    "Left/right and front/hind must match."
)


def _extract_json(text: str):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON in reply")
    return json.loads(m.group(0))


def refine_with_llm(bones: list[dict], suggestion: dict, provider: str | None = None) -> dict:
    """Ask the configured LLM to fix the heuristic map. Only names + hierarchy are sent. Validated before merging."""
    from .. import llm
    joints = suggestion["source_joints"]
    bone_names = [b["name"] for b in bones]
    tree = [{"bone": b["name"], "parent": b.get("parent"), "head": [round(x, 2) for x in b["head"]]} for b in bones]
    prompt = ("Rig bones (name, parent, rest head position x,y,z with Z up and the character facing -Y, left side = +X):\n"
              + json.dumps(tree) + "\n\nSource joints (name -> parent):\n"
              + json.dumps({j: (suggestion["source_joints"][p] if p is not None and p >= 0 else None)
                            for j, p in zip(joints, sk.builtin_parents(suggestion["source"], joints) or [None] * len(joints))})
              + "\n\nCurrent heuristic map:\n" + json.dumps(suggestion["map"]) + "\n\nReturn the corrected full map as JSON.")
    try:
        r = llm.chat([{"role": "user", "content": prompt}], system=LLM_SYSTEM, provider_override=provider, max_tokens=2500, temperature=0.0)
        data = _extract_json(r.get("text", ""))
    except Exception as e:  # noqa: BLE001
        suggestion = dict(suggestion)
        suggestion["notes"] = list(suggestion.get("notes", [])) + [f"LLM refinement unavailable: {str(e)[:160]}"]
        return suggestion
    new = data.get("map", {})
    out = dict(suggestion["map"])
    cf = dict(suggestion["confidence"])
    jset = set(joints)
    used = {j for j in out.values()}
    changes = 0
    for b, j in new.items():
        if b not in bone_names or (j is not None and j not in jset):
            continue
        if j is None:
            continue
        if out.get(b) == j:
            continue
        if cf.get(b, 0) >= 0.9:
            continue                      # keep confident heuristic matches
        if j in used and out.get(b) != j:
            continue
        if b in out:
            used.discard(out[b])
        out[b] = j
        used.add(j)
        cf[b] = 0.7
        changes += 1
    res = dict(suggestion)
    res.update({"map": out, "confidence": cf, "unmapped": [n for n in bone_names if n not in out],
                "notes": list(suggestion.get("notes", [])) + [f"LLM ({r.get('provider')}/{r.get('model')}) changed {changes} mapping(s)."] + [str(x)[:200] for x in data.get("notes", [])[:4]]})
    return res
