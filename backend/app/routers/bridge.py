"""Live bridge between the open Video Editor (browser) and external drivers (MCP / Claude Code / Codex / scripts).

The timeline lives in the browser, so:
  * the editor POSTs a snapshot of its project on every change (+ heartbeat)            -> POST /api/bridge/project
  * a driver reads it                                                                  -> GET  /api/bridge/project
  * a driver queues a command and (optionally) waits for the editor to apply it        -> POST /api/bridge/command
  * the editor polls for commands, applies them with its own applyOps (one undo step)   -> GET  /api/bridge/pending
    and acknowledges with the resulting project                                         -> POST /api/bridge/ack
Command types: 'ops' {ops:[Op]} | 'seek' {t} | 'set_project' {project} | 'select' {clipIds}.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/bridge", tags=["bridge"])

_lock = threading.Condition()
_state: dict[str, Any] = {"project": None, "selection": None, "playhead": 0.0, "updated": 0.0, "client": None}
_queue: list[dict] = []                  # pending commands (not yet delivered)
_inflight: dict[str, dict] = {}          # id -> {cmd, delivered_at, client}: delivered but not yet acked
_results: dict[str, dict] = {}           # id -> {ok, error, project, done_at}
CONNECTED_S = 6.0                        # editor counts as connected if it reported within this window
REDELIVER_S = 4.0                        # a delivered command without an ack is re-queued after this (stale tab / dropped poll)


def _requeue_stale() -> None:
    """Commands handed to a poll that never acked (e.g. the page was reloaded mid long-poll) go back to the queue."""
    now = time.time()
    for cid, f in list(_inflight.items()):
        if now - f["delivered_at"] > REDELIVER_S and cid not in _results:
            _inflight.pop(cid)
            _queue.insert(0, f["cmd"])


class Snapshot(BaseModel):
    project: dict
    selection: dict | None = None
    playhead: float = 0.0
    client: str | None = None


@router.post("/project")
def push_snapshot(s: Snapshot):
    with _lock:
        _state.update(project=s.project, selection=s.selection, playhead=s.playhead, client=s.client, updated=time.time())
    return {"ok": True}


@router.post("/heartbeat")
def heartbeat(client: str | None = None):
    with _lock:
        _state["updated"] = time.time()
        _state["client"] = client or _state["client"]
    return {"ok": True}


def connected() -> bool:
    return time.time() - (_state["updated"] or 0) < CONNECTED_S


@router.get("/project")
def get_snapshot():
    age = time.time() - _state["updated"] if _state["updated"] else None
    return {"connected": connected(), "age_s": round(age, 2) if age is not None else None, "project": _state["project"],
            "selection": _state["selection"], "playhead": _state["playhead"]}


class Command(BaseModel):
    type: str                            # ops | seek | set_project | select
    payload: dict = {}
    wait: float = 15.0                   # seconds to wait for the editor's ack (0 = fire and forget)


@router.post("/command")
def command(c: Command):
    if c.type not in ("ops", "seek", "set_project", "select"):
        raise HTTPException(400, f"unknown command type {c.type}")
    if not connected():
        raise HTTPException(409, "No Video Editor is connected. Open http://localhost:5173 (Video Editor tab) and try again.")
    cid = uuid.uuid4().hex[:10]
    with _lock:
        _queue.append({"id": cid, "type": c.type, "payload": c.payload, "queued": time.time()})
        _lock.notify_all()
        if c.wait <= 0:
            return {"id": cid, "status": "queued"}
        deadline = time.time() + c.wait
        while cid not in _results and time.time() < deadline:
            _lock.wait(timeout=0.25)
            _requeue_stale()
            if _queue:
                _lock.notify_all()
        r = _results.pop(cid, None)
        if r is None:                      # give up: make sure it is not applied later by surprise
            _inflight.pop(cid, None)
            _queue[:] = [q for q in _queue if q["id"] != cid]
    if r is None:
        return {"id": cid, "status": "timeout", "detail": "The editor did not acknowledge in time (is the tab in the background?)."}
    return {"id": cid, "status": "done" if r["ok"] else "error", **r}


@router.get("/pending")
def pending(client: str | None = None, wait: float = 0.0):
    """Editor poll. With wait>0 this long-polls until a command arrives (max 20 s)."""
    deadline = time.time() + min(max(wait, 0.0), 20.0)
    with _lock:
        _state["updated"] = time.time()
        if client:
            _state["client"] = client
        # Only the most recently active client (last heartbeat/snapshot/poll) receives commands; a stale
        # long-poll left over from a reloaded page returns empty instead of swallowing a command.
        while time.time() < deadline:
            _requeue_stale()
            if _queue and (client is None or client == _state["client"]):
                break
            _lock.wait(timeout=0.25)
        if not _queue or (client is not None and client != _state["client"]):
            return {"commands": []}
        out = list(_queue)
        _queue.clear()
        now = time.time()
        for c in out:
            _inflight[c["id"]] = {"cmd": c, "delivered_at": now, "client": client}
    return {"commands": out}


class Ack(BaseModel):
    id: str
    ok: bool = True
    error: str | None = None
    project: dict | None = None
    info: dict | None = None             # e.g. {"applied": 3, "dropped": [...]}


@router.post("/ack")
def ack(a: Ack):
    with _lock:
        _inflight.pop(a.id, None)
        _results[a.id] = {"ok": a.ok, "error": a.error, "info": a.info, "done_at": time.time()}
        if a.project is not None:
            _state.update(project=a.project, updated=time.time())
        _lock.notify_all()
    return {"ok": True}
