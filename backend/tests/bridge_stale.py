"""Regression: a command must not be lost to a stale long-poll (page reloaded) or a dropped delivery."""
import threading, time, httpx, sys

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8810"
c = httpx.Client(base_url=BASE, timeout=40)
proj = {"version": 1, "name": "t", "width": 640, "height": 360, "fps": 30, "background": "#000", "tracks": []}

# 1) stale page A: snapshot + long-poll that will never ack
c.post("/api/bridge/project", json={"project": proj, "client": "A"})
stale = {}
threading.Thread(target=lambda: stale.update(r=c.get("/api/bridge/pending", params={"client": "A", "wait": 15}).json()), daemon=True).start()
time.sleep(0.5)

# 2) new page B becomes active and processes commands, acking each
stop = threading.Event()
drop_first = {"on": False, "dropped": 0}
def page_b():
    cb = httpx.Client(base_url=BASE, timeout=40)
    while not stop.is_set():
        cb.post("/api/bridge/heartbeat", params={"client": "B"})
        for cmd in cb.get("/api/bridge/pending", params={"client": "B", "wait": 2}).json()["commands"]:
            if drop_first["on"] and drop_first["dropped"] == 0:     # simulate a delivery lost in transit
                drop_first["dropped"] += 1
                continue
            cb.post("/api/bridge/ack", json={"id": cmd["id"], "ok": True, "project": proj, "info": {"by": "B"}})
threading.Thread(target=page_b, daemon=True).start()
time.sleep(0.5)

t0 = time.time()
r = c.post("/api/bridge/command", json={"type": "seek", "payload": {"t": 1}, "wait": 15}).json()
print(f"[{'ok' if r['status'] == 'done' and r['info']['by'] == 'B' else 'FAIL'}] stale long-poll did not swallow the command: {r['status']} in {time.time() - t0:.1f}s; stale A got {stale.get('r')}")

drop_first["on"] = True
t0 = time.time()
r = c.post("/api/bridge/command", json={"type": "seek", "payload": {"t": 2}, "wait": 15}).json()
print(f"[{'ok' if r['status'] == 'done' and drop_first['dropped'] == 1 else 'FAIL'}] un-acked delivery was re-queued and applied: {r['status']} in {time.time() - t0:.1f}s")
stop.set()
