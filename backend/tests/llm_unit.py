"""Offline unit check of services/llm.py request shaping using httpx.MockTransport (no key, no network)."""
import json, sys
import httpx
sys.path.insert(0, r"D:\CutLab\backend")
from app.services import llm
from app import config

seen = {}
def handler(req: httpx.Request):
    seen["url"], seen["headers"], seen["body"] = str(req.url), dict(req.headers), json.loads(req.content)
    if "anthropic" in str(req.url):
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}, {"type": "tool_use", "id": "t1", "name": "f", "input": {"a": 1}}], "stop_reason": "tool_use"})
    return httpx.Response(200, json={"message": {"content": "hi", "tool_calls": [{"function": {"name": "f", "arguments": {"a": 1}}}]}})

c = httpx.Client(transport=httpx.MockTransport(handler))
msgs = [{"role": "user", "content": [{"type": "text", "text": "look"}, {"type": "image", "media_type": "image/jpeg", "data": "QUJD"}]}]
tools = [{"name": "f", "description": "d", "parameters": {"type": "object", "properties": {"a": {"type": "integer"}}}}]

# no key -> clear error, no request sent
config.save_settings({"anthropic_api_key": ""})
try:
    llm.chat(msgs, provider_override="anthropic", client=c); print("FAIL: expected LLMError")
except llm.LLMError as e:
    print("[ok] no key ->", e)

orig = config.load_settings
config.load_settings = lambda: {**orig(), "anthropic_api_key": "sk-test", "anthropic_model": "claude-opus-5-5"}
r = llm.chat(msgs, system="sys", tools=tools, provider_override="anthropic", client=c)
b = seen["body"]
assert seen["headers"]["x-api-key"] == "sk-test" and b["model"] == "claude-opus-5-5" and b["system"] == "sys"
assert b["messages"][0]["content"][1]["source"] == {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}
assert b["tools"][0]["input_schema"]["properties"]["a"]["type"] == "integer"
assert r["tool_calls"] == [{"id": "t1", "name": "f", "arguments": {"a": 1}}] and r["text"] == "ok"
print("[ok] anthropic request/response shaping")

r = llm.chat(msgs, system="sys", tools=tools, provider_override="ollama", client=c)
b = seen["body"]
assert b["messages"][0]["role"] == "system" and b["messages"][1]["images"] == ["QUJD"] and b["tools"][0]["function"]["name"] == "f" and b["think"] is False
assert r["tool_calls"][0]["arguments"] == {"a": 1}
print("[ok] ollama request/response shaping")

# masking
config.load_settings = lambda: {**orig(), "anthropic_api_key": "sk-secret"}
pub = config.public_settings()
assert pub["anthropic_api_key"] == config.MASK and "sk-secret" not in json.dumps(pub)
print("[ok] secrets masked in public_settings")
