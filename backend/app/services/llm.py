"""One chat() for every LLM use in CutLab (agent planning, animation QA, bone mapping, captions).

provider = settings["llm_provider"]:
  * "ollama"    local, default. Nothing leaves the machine.
  * "anthropic" cloud (user-supplied API key in Settings). Text AND images are sent to Anthropic's API —
                callers that attach images should surface that to the user.
Messages use a neutral shape: {"role": "user"|"assistant", "content": str | [ {"type":"text","text":..} | {"type":"image","media_type":"image/jpeg","data":<base64>} ]}
Tools use JSON-schema (name, description, parameters). Returns {"text", "tool_calls":[{"id","name","arguments"}], "provider","model","stop_reason"}.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from .. import config

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class LLMError(RuntimeError):
    pass


def provider() -> str:
    return config.load_settings().get("llm_provider") or "ollama"


def available() -> dict:
    s = config.load_settings()
    return {"ollama": True, "anthropic": bool(s.get("anthropic_api_key")), "active": s.get("llm_provider", "ollama")}


def _norm(content: Any) -> list[dict]:
    return [{"type": "text", "text": content}] if isinstance(content, str) else list(content)


def chat(messages: list[dict], *, system: str | None = None, tools: list[dict] | None = None,
         provider_override: str | None = None, model: str | None = None, max_tokens: int = 2048,
         temperature: float = 0.2, timeout: float = 180, client: httpx.Client | None = None) -> dict:
    s = config.load_settings()
    prov = provider_override or s.get("llm_provider") or "ollama"
    own = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        return _anthropic(client, s, messages, system, tools, model, max_tokens, temperature) if prov == "anthropic" \
            else _ollama(client, s, messages, system, tools, model, max_tokens, temperature)
    finally:
        if own:
            client.close()


# ------------------------------------------------------------------------------------------ anthropic
def _anthropic(client, s, messages, system, tools, model, max_tokens, temperature) -> dict:
    key = s.get("anthropic_api_key")
    if not key:
        raise LLMError("未设置 Anthropic API Key（设置 → 云端模型）。")
    model = model or s.get("anthropic_model") or "claude-opus-5-5"
    msgs = []
    for m in messages:
        blocks = []
        for b in _norm(m["content"]):
            if b["type"] == "image":
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": b.get("media_type", "image/jpeg"), "data": b["data"]}})
            else:
                blocks.append(b)
        msgs.append({"role": m["role"], "content": blocks})
    body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": msgs}
    if system:
        body["system"] = system
    if tools:
        body["tools"] = [{"name": t["name"], "description": t.get("description", ""), "input_schema": t.get("parameters", {"type": "object", "properties": {}})} for t in tools]
    r = client.post(ANTHROPIC_URL, json=body, headers={"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"})
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except Exception:
            msg = r.text
        raise LLMError(f"Anthropic API {r.status_code}: {msg[:300]}")
    j = r.json()
    text = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
    calls = [{"id": b["id"], "name": b["name"], "arguments": b.get("input", {})} for b in j.get("content", []) if b.get("type") == "tool_use"]
    return {"text": text, "tool_calls": calls, "provider": "anthropic", "model": model, "stop_reason": j.get("stop_reason")}


# ------------------------------------------------------------------------------------------ ollama
def _ollama(client, s, messages, system, tools, model, max_tokens, temperature) -> dict:
    model = model or s.get("llm_model") or "qwen3:8b"
    msgs = [{"role": "system", "content": system}] if system else []
    for m in messages:
        blocks = _norm(m["content"])
        msg: dict[str, Any] = {"role": m["role"], "content": "".join(b.get("text", "") for b in blocks if b["type"] == "text")}
        imgs = [b["data"] for b in blocks if b["type"] == "image"]
        if imgs:
            msg["images"] = imgs                      # needs a vision-capable local model
        msgs.append(msg)
    body: dict[str, Any] = {"model": model, "messages": msgs, "stream": False, "think": False,
                            "options": {"temperature": temperature, "num_predict": max_tokens}}
    if tools:
        body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                           "parameters": t.get("parameters", {"type": "object", "properties": {}})}} for t in tools]
    try:
        r = client.post(f"{s['ollama_url']}/api/chat", json=body)
    except httpx.HTTPError as e:
        raise LLMError(f"无法连接 Ollama: {e}")
    if r.status_code >= 400:
        raise LLMError(f"Ollama {r.status_code}: {r.text[:300]}")
    m = r.json().get("message", {})
    calls = []
    for i, tc in enumerate(m.get("tool_calls") or []):
        fn = tc.get("function", {})
        args = fn.get("arguments", {})
        calls.append({"id": f"call_{i}", "name": fn.get("name", ""), "arguments": json.loads(args) if isinstance(args, str) else args})
    return {"text": m.get("content", ""), "tool_calls": calls, "provider": "ollama", "model": model, "stop_reason": "tool_use" if calls else "end_turn"}
