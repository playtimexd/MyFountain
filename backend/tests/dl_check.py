import httpx, time, json, sys
c = httpx.Client(base_url="http://127.0.0.1:8787", timeout=120)
def wait(j):
    while j["status"] in ("queued","running"):
        time.sleep(0.5); j = c.get(f"/api/jobs/{j['id']}").json()
    return j
# 1) share-text URL extraction + direct link download through yt-dlp generic extractor
share = "3.5 复制打开 看看这个视频 http://127.0.0.1:8899/b_vertical.mp4 复制此链接"
j = wait(c.post("/api/download", json={"url": share, "quality": "best"}).json())
print("direct download:", j["status"], j.get("error"), (j.get("result") or {}).get("media", {}).get("name"))
# 2) metadata only for real sites (no media download)
for u in ["https://www.youtube.com/watch?v=jNQXAC9IVRw", "https://www.bilibili.com/video/BV1GJ411x7h7"]:
    r = c.post("/api/download/info", json={"url": u})
    print(u.split('/')[2], r.status_code, (r.json().get("title") or r.json().get("detail"))[:90] if r.status_code else "")
