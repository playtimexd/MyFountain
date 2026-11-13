"""Harvest jobs: walk sources x queries x kinds x pages, add every candidate allowed by the licence policy until the
item / size caps are reached. State lives in data/hub/harvest/<id>.json after every item, so a cancelled or crashed
harvest can be resumed. Live per-source progress is exposed as the job's `result` while it runs."""
from __future__ import annotations

import json
import math
import threading
import time
import uuid

from ...jobs import JobContext, JobCancelled
from . import paths
from .connectors import REGISTRY, ConnectorError, NeedsKey
from .net import CapExceeded, HubHTTPError
from .pipeline import Skipped, add_candidate

MAX_PAGES = 25
_lock = threading.Lock()
_live: dict[str, dict] = {}


def _path(hid: str):
    return paths.HARVEST_DIR / f"{paths.safe_name(hid)}.json"


def save(st: dict) -> None:
    st["updated"] = time.time()
    with _lock:
        _live[st["id"]] = st
        _path(st["id"]).write_text(json.dumps(st, indent=1), "utf-8")


def load(hid: str) -> dict | None:
    with _lock:
        if hid in _live:
            return _live[hid]
    p = _path(hid)
    if p.exists():
        return json.loads(p.read_text("utf-8"))
    return None


def list_harvests(limit: int = 20) -> list[dict]:
    out = []
    for p in sorted(paths.HARVEST_DIR.glob("*.json"), key=lambda p: -p.stat().st_mtime)[:limit]:
        try:
            st = load(p.stem) or {}
            out.append({k: st.get(k) for k in ("id", "status", "params", "totals", "created", "updated", "job_id")})
        except Exception:
            continue
    return out


def normalize_params(p: dict) -> dict:
    sources = [s for s in (p.get("sources") or []) if s in REGISTRY] or ["polyhaven", "ambientcg"]
    policy = p.get("license_policy") or "commercial"
    if policy not in ("cc0", "commercial", "any"):
        policy = "commercial"
    return {"sources": sources, "queries": [q.strip() for q in (p.get("queries") or []) if isinstance(q, str)] or [""],
            "kinds": [k for k in (p.get("kinds") or []) if k] or [None],
            "max_items": max(1, min(int(p.get("max_items") or 50), 20000)),
            "max_gb": max(0.001, min(float(p.get("max_gb") or 2.0), 500.0)),
            "license_policy": policy, "variants": p.get("variants") or {"texture_res": "1k"}}


def new_state(params: dict) -> dict:
    prm = normalize_params(params)
    n = len(prm["sources"])
    st = {"id": uuid.uuid4().hex[:10], "status": "queued", "params": prm, "created": time.time(), "updated": time.time(),
          "totals": {"added": 0, "exists": 0, "duplicates": 0, "skipped": 0, "errors": 0, "bytes": 0},
          "sources": {s: {"cursor": [0, 0, 1], "added": 0, "exists": 0, "duplicates": 0, "skipped": 0, "errors": 0, "bytes": 0,
                          "done": False, "note": "", "quota": math.ceil(prm["max_items"] / n)} for s in prm["sources"]},
          "log": [], "recent": []}
    save(st)
    return st


def _log(st: dict, msg: str) -> None:
    st["log"] = (st["log"] + [f"{time.strftime('%H:%M:%S')} {paths.redact(msg)}"])[-200:]


def run(st: dict, ctx: JobContext) -> dict:
    prm = st["params"]
    max_items, max_bytes = prm["max_items"], int(prm["max_gb"] * 1e9)
    st["status"] = "running"
    st["job_id"] = ctx.job.id
    ctx.job.result = st
    save(st)
    tot = st["totals"]
    try:
        for src in prm["sources"]:
            ss = st["sources"][src]
            if ss["done"]:
                continue
            con = REGISTRY[src]
            ok, note = con.available()
            if not con.automated or (con.key_required and not con.key()):
                ss["done"], ss["note"] = True, ("needs an API key" if con.automated else "catalog only (manual download)")
                _log(st, f"{con.name}: skipped - {ss['note']}")
                save(st)
                continue
            quota = min(ss["quota"], getattr(con, "max_per_harvest", 10 ** 9))
            qi, ki, page = ss["cursor"]
            while qi < len(prm["queries"]) and not ss["done"]:
                q = prm["queries"][qi]
                kind = prm["kinds"][ki]
                if kind and kind not in con.kinds:
                    qi, ki, page = _advance(qi, ki, prm)
                    ss["cursor"] = [qi, ki, page]
                    continue
                ctx.check()
                try:
                    kw = {"policy": prm["license_policy"]} if src in ("freesound", "sketchfab") else {}
                    res = con.search(q, kind, page, **kw)
                except NeedsKey as e:
                    ss["done"], ss["note"] = True, str(e)
                    break
                except (ConnectorError, HubHTTPError) as e:
                    ss["errors"] += 1
                    tot["errors"] += 1
                    _log(st, f"{con.name} search '{q}' p{page}: {e}")
                    qi, ki, page = _advance(qi, ki, prm)
                    ss["cursor"] = [qi, ki, page]
                    save(st)
                    continue
                for cand in res["items"]:
                    ctx.check()
                    if ss["added"] >= quota or tot["added"] >= max_items or tot["bytes"] >= max_bytes:
                        break
                    try:
                        r = add_candidate(cand, variant=prm["variants"], policy=prm["license_policy"],
                                          max_bytes=max_bytes - tot["bytes"], check=ctx.check)
                        key = {"added": "added", "exists": "exists", "duplicate": "duplicates"}[r["status"]]
                        ss[key] += 1
                        tot[key] += 1
                        ss["bytes"] += r.get("bytes", 0)
                        tot["bytes"] += r.get("bytes", 0)
                        if r["status"] == "added":
                            a = r["asset"]
                            st["recent"] = ([{"id": a["id"], "name": a["name"], "kind": a["kind"], "source": src,
                                              "thumb": a.get("thumb_url"), "license": a["license"]}] + st["recent"])[:24]
                    except Skipped as e:
                        ss["skipped"] += 1
                        tot["skipped"] += 1
                        if ss["skipped"] <= 20:
                            _log(st, f"{con.name} '{cand.get('name')}': skipped ({e})")
                    except CapExceeded as e:
                        ss["skipped"] += 1
                        tot["skipped"] += 1
                        _log(st, f"{con.name} '{cand.get('name')}': {e}")
                    except (JobCancelled, KeyboardInterrupt):
                        raise
                    except Exception as e:  # noqa: BLE001
                        ss["errors"] += 1
                        tot["errors"] += 1
                        _log(st, f"{con.name} '{cand.get('name')}': {type(e).__name__}: {e}")
                    ctx.progress(min(0.99, max(tot["added"] / max_items, tot["bytes"] / max_bytes)),
                                 f"{con.name}: {ss['added']} added · total {tot['added']}/{max_items}")
                    save(st)
                if ss["added"] >= quota or tot["added"] >= max_items or tot["bytes"] >= max_bytes:
                    ss["done"] = True
                    ss["note"] = "quota reached" if ss["added"] >= quota else "global cap reached"
                    break
                if res.get("has_more") and page < MAX_PAGES and res["items"]:
                    page += 1
                else:
                    qi, ki, page = _advance(qi, ki, prm)
                ss["cursor"] = [qi, ki, page]
                save(st)
            if not ss["done"]:
                ss["done"], ss["note"] = True, ss["note"] or "all queries searched"
            save(st)
        st["status"] = "done"
    except JobCancelled:
        st["status"] = "cancelled"
        _log(st, "Cancelled - use Resume to continue")
        save(st)
        raise
    except Exception as e:
        st["status"] = "error"
        _log(st, f"Stopped: {type(e).__name__}: {e}")
        save(st)
        raise
    save(st)
    return st


def _advance(qi: int, ki: int, prm: dict) -> tuple[int, int, int]:
    ki += 1
    if ki >= len(prm["kinds"]):
        ki = 0
        qi += 1
    return qi, ki, 1


def prepare_resume(hid: str) -> dict:
    st = load(hid)
    if not st:
        raise KeyError(hid)
    if st["status"] in ("running", "queued"):
        from ...jobs import jobs
        j = jobs.get(st.get("job_id") or "")
        if j is not None and j.status in ("queued", "running"):
            raise RuntimeError("This harvest is still running")
        # the engine restarted (or crashed) mid-harvest: the saved cursor lets it continue
    n_left = [s for s, v in st["sources"].items() if not v["done"]]
    if not n_left and st["totals"]["added"] < st["params"]["max_items"]:
        # extend: let finished-by-quota sources continue toward the global cap
        for s, v in st["sources"].items():
            if v["note"] == "quota reached":
                v["done"], v["note"] = False, ""
                v["quota"] = v["quota"] + (st["params"]["max_items"] - st["totals"]["added"])
    st["status"] = "queued"
    save(st)
    return st
