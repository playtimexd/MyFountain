from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import agent as ag

router = APIRouter(prefix="/api/agent", tags=["agent"])


class Msg(BaseModel):
    role: str
    content: str


class ChatReq(BaseModel):
    messages: list[Msg]
    project: dict[str, Any] | None = None
    selection: dict[str, Any] | None = None


@router.post("/chat")
def chat(req: ChatReq):
    if not req.messages:
        raise HTTPException(400, "messages must not be empty")
    try:
        return ag.chat([m.model_dump() for m in req.messages], req.project, req.selection)
    except Exception as e:  # noqa: BLE001
        name = type(e).__name__
        if "Connect" in name or "Timeout" in name:
            raise HTTPException(503, f"Cannot reach Ollama ({e}). Make sure Ollama is running and the llm_model is installed.")
        raise HTTPException(500, f"{name}: {e}")
