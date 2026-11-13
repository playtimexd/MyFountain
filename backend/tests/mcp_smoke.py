"""Drive the CutLab MCP server over real stdio JSON-RPC, like Claude Code / Codex would."""
import json, subprocess, sys, base64, time

PY = r"D:\CutLab\backend\.venv\Scripts\python.exe"
p = subprocess.Popen([PY, "-m", "app.mcp_server"], cwd=r"D:\CutLab\backend", stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
_id = 0


def rpc(method, params=None, notify=False):
    global _id
    msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if not notify:
        _id += 1; msg["id"] = _id
    p.stdin.write((json.dumps(msg) + "\n").encode()); p.stdin.flush()
    if notify:
        return None
    return json.loads(p.stdout.readline())


def tool(name, args=None):
    r = rpc("tools/call", {"name": name, "arguments": args or {}})["result"]
    parts = []
    for c in r["content"]:
        parts.append(f"<image {len(base64.b64decode(c['data']))} bytes>" if c["type"] == "image" else c["text"][:300].replace("\n", " "))
    print(f"  {name}: {'ERROR ' if r['isError'] else ''}{' | '.join(parts)}")
    return r


init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "0"}})
print("initialize:", init["result"]["serverInfo"], init["result"]["protocolVersion"])
rpc("notifications/initialized", notify=True)
tools = rpc("tools/list")["result"]["tools"]
print(f"tools/list: {len(tools)} tools ->", ", ".join(t["name"] for t in tools))
assert all("inputSchema" in t for t in tools)

tool("status")
r = tool("import_media", {"paths": [r"D:\CutLab\data\testmedia\a_testsrc.mp4"]})
media = json.loads(r["content"][0]["text"])["items"][0]
tool("list_media", {"kind": "video", "limit": 3})
tool("view_media_frame", {"media_id": media["id"], "t": 1.5})
tool("segment_media", {"media_id": media["id"], "method": "fixed", "every": 2})
tool("get_timeline")                       # no editor connected yet -> expected error text
tool("cutlab_api", {"method": "GET", "path": "/api/assets/catalog"})
tool("cutlab_api", {"method": "PUT", "path": "/api/settings", "body": {"values": {}}})   # must be refused
print("unknown method:", rpc("foo/bar").get("error"))
p.stdin.close(); p.wait(timeout=10)
print("stderr:", p.stderr.read().decode(errors="replace")[-300:])
