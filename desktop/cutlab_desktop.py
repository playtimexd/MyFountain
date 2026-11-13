"""CutLab desktop app: one window around the local engine.

    backend\\.venv\\Scripts\\pythonw.exe desktop\\cutlab_desktop.py [--edge] [--update]

* checks the installation (packages, built interface) and fixes it when the checkout changed (desktop/updater.py sync)
* starts the engine (python -m app) or attaches to one that already runs on the same data folder; picks a free port
  when 8787 belongs to another program; stops the engine it started when the window closes
* shows CutLab in its own window: pywebview (Edge WebView2) when installed, else an Edge "--app" window
* a small control server on 127.0.0.1 serves the start-up / update / quit pages (desktop/ui/app.html), so they keep
  working while the engine restarts. Menu → Check for updates, or Settings → Check for updates inside CutLab.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import updater  # noqa: E402
from engine import (DESKTOP, ICON, NOWIN, ROOT, Engine, app_home, busy_jobs, config_path, data_dir,  # noqa: E402
                    has_library, health, known_libraries, load_config, pid_alive, pid_on_port, remember_library,
                    same_path, save_config, serves)

UI = DESKTOP / "ui"
APP_ID = "CutLab.Desktop"
LOCK = app_home() / "launcher.json"           # read by the engine's /api/app/info and by a second launcher instance


# --------------------------------------------------------------------------------------------- logging + state
class Log:
    def __init__(self) -> None:
        self.path = app_home() / "desktop.log"
        try:
            if self.path.stat().st_size > 2_000_000:
                self.path.replace(self.path.with_suffix(".old.log"))
        except OSError:
            pass
        self.lock = threading.Lock()

    def __call__(self, msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        with self.lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        if sys.stdout and not sys.executable.lower().endswith("pythonw.exe"):
            try:
                print(line, flush=True)
            except Exception:
                pass


class State:
    def __init__(self, log: Log):
        self._log = log
        self.lock = threading.Lock()
        self.phase = "checking"          # checking | installing | starting | ready | error | updating | stopping
        self.message = "Starting…"
        self.detail = ""
        self.lines: deque[str] = deque(maxlen=300)
        self.update = {"running": False, "done": False, "ok": False, "error": "", "lines": [], "result": None}
        self.confirm: dict | None = None    # set while start-up waits for the user to confirm the data folder

    def set(self, phase: str, message: str, detail: str = "") -> None:
        with self.lock:
            self.phase, self.message, self.detail = phase, message, detail
        self.log(f"[{phase}] {message}" + (f" - {detail}" if detail else ""))

    def log(self, line: str) -> None:
        with self.lock:
            self.lines.append(line)
            if self.update["running"]:
                self.update["lines"].append(line)
        self._log(line)


# --------------------------------------------------------------------------------------------- the app
class App:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.log = Log()
        self.state = State(self.log)
        self.cfg = load_config()
        self.data = data_dir(self.cfg)
        self.engine = Engine(self.data, self.state.log)
        self.window = None                # pywebview window
        self.mode = ""
        self.control_url = ""
        self.quitting = False
        self.force_quit = False
        self.relaunch = False
        self.boot_lock = threading.Lock()
        self.op_lock = threading.Lock()      # one at a time: engine restart, update, data-folder change
        self.confirm_event = threading.Event()
        self.confirmed_data = ""             # the user said "start with this (empty) folder"

    # ---------------------------------------------------------------- start-up
    def boot(self) -> None:
        if not self.boot_lock.acquire(blocking=False):
            return
        try:
            self._boot()
        except Exception as e:
            self.state.log(traceback.format_exc())
            self.state.set("error", "CutLab could not start.", str(e) + ("\n\n" + self.engine.tail(25) if self.engine.owned else ""))
        finally:
            self.boot_lock.release()

    def _boot(self) -> None:
        st = self.state
        st.set("checking", "Checking the installation…")
        try:
            needs = updater.sync_needs()
        except Exception as e:
            needs = {}
            st.log(f"could not check installation: {e}")
        if any(needs.values()):
            what = ", ".join(k for k, v in needs.items() if v)
            st.set("installing", "Preparing CutLab (first start or new version)…", f"needed: {what}")
            try:
                updater.sync(st.log)
            except Exception as e:
                if not (updater.DIST / "index.html").exists():
                    raise
                st.log(f"warning: {e} - starting with the previous build")
        if self.engine.running():
            self.write_lock()
            st.set("ready", "Ready")
            return
        how = self.engine.attach_or_pick_port(int(self.cfg.get("port") or 8787))
        while True:                                  # never open a different (empty) library silently
            issues = self.data_issues(attached=(how == "attached"))
            if not issues or self.confirmed_data == str(self.data):
                break
            data0 = self.data
            self.confirm_event.clear()
            with st.lock:
                st.confirm = {"issues": issues, "data_dir": str(self.data),
                              "libraries": known_libraries(exclude=self.data)}
            st.set("confirm", "Check the data folder before starting", "\n".join(issues))
            while not self.confirm_event.wait(0.5):
                if self.quitting or not same_path(self.data, data0):
                    break
            with st.lock:
                st.confirm = None
            if self.quitting:
                return
            if self.confirm_event.is_set() and same_path(self.data, data0):
                self.confirmed_data = str(self.data)
                break
            self.engine = Engine(self.data, st.log)           # another folder was chosen: look again
            how = self.engine.attach_or_pick_port(int(self.cfg.get("port") or 8787))
        if how == "start":
            st.set("starting", "Starting the engine…", f"data folder: {self.data}")
            self.engine.start({"CUTLAB_DESKTOP_URL": self.control_url})
            ok = self.engine.wait_ready(180, lambda t: st.set("starting", f"Starting the engine… {int(t)} s",
                                                              f"data folder: {self.data}") if int(t * 2) % 10 == 0 else None)
            if not ok:
                raise RuntimeError("The engine did not answer within 3 minutes.")
        self.write_lock()
        remember_library(self.data)
        self.set_title()
        st.set("ready", "Ready")

    def data_issues(self, attached: bool) -> list[str]:
        out = []
        if self.cfg.get("_error"):
            out.append(f"{config_path()} could not be read ({self.cfg['_error']}), so its settings (data folder, port) "
                       f"are ignored. Fix or delete that file.")
        if not attached and not has_library(self.data):
            out.append(f"{self.data} holds no CutLab library yet - starting will create a new, empty one there.")
        return out

    def set_title(self) -> None:
        if self.window is not None:
            try:
                self.window.set_title(f"CutLab — {self.data}")
            except Exception:
                pass

    def restart_engine(self) -> None:
        if not self.op_lock.acquire(blocking=False):
            self.state.log("restart ignored - an update or another restart is in progress")
            return
        try:
            self.state.set("stopping", "Stopping the engine…")
            self.engine.stop()
            self.engine = Engine(self.data, self.state.log)
            self.boot()
        finally:
            self.op_lock.release()

    def watchdog(self) -> None:
        while not self.quitting:
            time.sleep(3)
            e = self.engine
            if self.state.phase == "error" and not self.boot_lock.locked() and self._reattach():
                self.state.set("ready", "Ready")    # someone (a script, another tool) started the engine again
                continue
            if self.state.phase != "ready" or self.quitting or self.state.update["running"]:
                continue
            if e.proc is not None:                  # ours: did the process exit?
                if e.proc.poll() is None:
                    continue
                why = f"exit code {e.proc.returncode}"
            else:                                   # attached: does it still answer?
                if health(e.base, 5) or health(e.base, 5):
                    continue
                why = "it stopped answering"
            self.state.set("starting", "The engine stopped - reconnecting…", why)
            back = False
            for _ in range(60):                     # restarted from outside (a script, another tool)? re-attach
                if self.quitting or self.state.phase != "starting":
                    break
                if self._reattach():
                    back = True
                    break
                time.sleep(1)
            if self.quitting or self.state.phase != "starting":
                continue
            if back:
                self.state.set("ready", "Ready")
                continue
            self.state.set("error", "The engine stopped unexpectedly.", f"{why}\n\n{e.tail(25)}")
            self.show_page("/", new_edge_window=False)

    def _reattach(self) -> bool:
        """Attach to an engine on our port that serves our data folder (started outside the app)."""
        e = self.engine
        h = health(e.base, 1.5)
        if not h or not serves(h, self.data):
            return False
        e.proc, e.pid, e.owned = None, h.get("pid") or pid_on_port(e.port), False
        self.state.log(f"engine on :{e.port} was started outside the app - attached to it (pid {e.pid})")
        self.write_lock()
        return True

    # ---------------------------------------------------------------- update
    def start_update(self) -> str:
        u = self.state.update
        if u["running"]:
            return "already running"
        busy = busy_jobs(self.engine.base) if self.engine.running() else []
        if busy:
            return f"{len(busy)} job(s) are still running ({', '.join(j.get('title') or j.get('kind', '?') for j in busy[:3])}). " \
                   "Wait for them to finish or cancel them, then update."
        if not self.op_lock.acquire(blocking=False):
            return "The engine is restarting - try again in a moment."
        self.state.update = {"running": True, "done": False, "ok": False, "error": "", "lines": [], "result": None}
        threading.Thread(target=self._update, daemon=True).start()
        return ""

    def _update(self) -> None:
        """Runs with self.op_lock held (taken by start_update)."""
        st, u = self.state, self.state.update
        stopped = False
        try:
            info = updater.check(fetch=True, cfg=self.cfg)
            if info["blocked"]:
                raise updater.UpdateError(info["blocked"])
            if not info["behind"]:
                st.log("Already up to date.")
                u.update(ok=True, result={"updated": False})
                return
            st.set("updating", f"Updating to {info['target']}…")
            st.log("Stopping the engine…")
            self.engine.stop()
            stopped = True
            res = updater.apply(st.log, cfg=self.cfg)
            u["result"] = {"updated": res["updated"], "from": res.get("from", "")[:7], "to": res.get("to", "")[:7],
                           "launcher": bool(res["info"]["will"].get("launcher"))}
            u["ok"] = True
        except Exception as e:
            u["error"] = str(e)
            st.log(f"Update failed: {e}")
        finally:
            self._version = (0.0, "")
            relaunch = bool(u.get("ok") and (u.get("result") or {}).get("launcher"))
            try:
                if relaunch:
                    st.log("The app itself changed - restarting CutLab…")
                    self.relaunch = True
                elif stopped:                       # bring the engine back (new code, or the old one after a rollback)
                    self.engine = Engine(self.data, st.log)
                    self.boot()
            finally:
                u.update(running=False, done=True)
                self.op_lock.release()
            if relaunch:
                time.sleep(1.5)
                self.quit(force=True)

    # ---------------------------------------------------------------- window helpers
    def show_page(self, path: str, new_edge_window: bool = True) -> None:
        url = f"{self.control_url}{path}{'&' if '?' in path else '?'}t={int(time.time() * 1000)}"   # same URL would not reload
        if self.window is not None:
            try:
                self.window.load_url(url)
            except Exception as e:
                self.log(f"load_url failed: {e}")
        elif self.mode == "edge" and new_edge_window:
            open_edge_window(url, self.log)

    def focus(self) -> None:
        if self.window is not None:
            try:
                self.window.restore()
                self.window.show()
            except Exception:
                pass
            hwnd = find_own_window()
            if hwnd:
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        elif self.mode == "edge":
            open_edge_window(self.control_url + "/", self.log)   # Edge windows cannot be raised from here: open one

    def quit(self, force: bool = False) -> None:
        self.force_quit = self.force_quit or force
        if self.window is not None:
            self.window.destroy()
        elif self.mode == "edge":
            close_edge_windows(self.log)

    def on_closing(self) -> bool:
        """pywebview closing event: returning False keeps the window open."""
        if self.state.update["running"]:
            self.show_page("/update")          # never close half-way through an update
            return False
        if self.force_quit:
            return True
        busy = busy_jobs(self.engine.base) if (self.engine.owned and self.state.phase == "ready") else []
        if busy:
            self.show_page("/quit")
            return False
        return True

    def shutdown(self) -> None:
        self.quitting = True
        if self.engine.owned and self.engine.pid:
            self.state.set("stopping", "Stopping the engine…")
            self.engine.stop()
        try:
            if json.loads(LOCK.read_text("utf-8")).get("pid") == os.getpid():
                LOCK.unlink()
        except Exception:
            pass
        if self.relaunch:
            spawn_launcher(["--wait-pid", str(os.getpid())] + (["--edge"] if self.mode == "edge" else []))

    def write_lock(self) -> None:
        LOCK.write_text(json.dumps({"pid": os.getpid(), "url": self.control_url, "engine": self.engine.base,
                                    "root": str(ROOT), "data": str(self.data)}), "utf-8")

    def open_path(self, what: str) -> None:
        p = {"data": self.data, "logs": app_home(), "root": ROOT}.get(what)
        if p:
            os.startfile(str(p))                                                  # noqa: S606 - explorer on our folders

    _version: tuple[float, str] = (0.0, "")

    def version(self) -> str:
        """git describe, cached for 15 s (the pages poll /api/state twice a second)."""
        t, v = self._version
        if time.time() - t > 15:
            try:
                v = updater.git("describe", "--always", "--dirty", check=False, timeout=10)
            except Exception:
                pass
            self._version = (time.time(), v)
        return v

    def info(self) -> dict:
        st, version = self.state, self.version()
        with st.lock:
            return {"phase": st.phase, "message": st.message, "detail": st.detail, "lines": list(st.lines)[-80:],
                    "engine_url": self.engine.base, "owned": self.engine.owned, "data_dir": str(self.data),
                    "root": str(ROOT), "mode": self.mode, "version": version, "confirm": st.confirm,
                    "config_path": str(updater.config_path()), "update": dict(st.update, lines=st.update["lines"][-400:])}


# --------------------------------------------------------------------------------------------- control server
def make_handler(app: App):
    pages = {"/", "/update", "/quit", "/settings"}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def _origin_ok(self) -> bool:
            # browsers always send Origin on POST; only our own pages may call the actions
            o = self.headers.get("Origin")
            return o is None or o == app.control_url

        def do_GET(self):
            path = urlparse(self.path).path
            if path in pages:
                return self._send(200, (UI / "app.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/icon.png":
                return self._send(200, (UI / "icon.png").read_bytes(), "image/png")
            if path == "/api/state":
                return self._json(app.info())
            if path == "/api/busy":
                busy = busy_jobs(app.engine.base) if app.engine.running() else []
                return self._json([{"title": j.get("title"), "kind": j.get("kind"), "status": j.get("status"),
                                    "progress": j.get("progress")} for j in busy])
            self._send(404, b"{}")

        def do_POST(self):
            if not self._origin_ok():
                return self._json({"error": "forbidden"}, 403)
            path = urlparse(self.path).path
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except Exception:
                body = {}
            try:
                if path == "/api/update/check":
                    res = updater.check(fetch=True, cfg=app.cfg)
                    res["busy_jobs"] = [{"title": j.get("title"), "kind": j.get("kind"), "status": j.get("status")}
                                        for j in (busy_jobs(app.engine.base) if app.engine.running() else [])]
                    return self._json(res)
                if path == "/api/update/apply":
                    err = app.start_update()
                    return self._json({"ok": not err, "error": err}, 200 if not err else 409)
                if path == "/api/continue":
                    app.confirm_event.set()
                    return self._json({"ok": True})
                if path == "/api/retry":
                    threading.Thread(target=app.boot, daemon=True).start()
                    return self._json({"ok": True})
                if path == "/api/restart-engine":
                    threading.Thread(target=app.restart_engine, daemon=True).start()
                    return self._json({"ok": True})
                if path == "/api/open":
                    app.open_path(body.get("what", ""))
                    return self._json({"ok": True})
                if path == "/api/focus":
                    if body.get("page"):
                        app.show_page(body["page"])
                    if app.mode != "edge" or not body.get("page"):
                        app.focus()
                    return self._json({"ok": True})
                if path == "/api/quit":
                    threading.Thread(target=app.quit, kwargs={"force": True}, daemon=True).start()
                    return self._json({"ok": True})
                if path == "/api/data-dir":
                    return self._json(set_data_dir(app, body.get("path", "")))
            except Exception as e:
                app.log(traceback.format_exc())
                return self._json({"error": str(e)}, 500)
            self._json({"error": "not found"}, 404)

    return H


def set_data_dir(app: App, path: str) -> dict:
    """Point CutLab at another data folder (saved in desktop.json; existing files are not moved)."""
    path = path.strip().strip('"')
    if os.environ.get("CUTLAB_DATA"):
        return {"error": f"The CUTLAB_DATA environment variable ({os.environ['CUTLAB_DATA']}) decides the data folder. "
                         "Change or remove it instead."}
    if path:
        p = Path(os.path.expandvars(path)).expanduser()
        if not p.is_absolute():
            return {"error": "Use an absolute path, e.g. D:\\CutLab Data"}
        if updater._inside(p, ROOT) and not updater._inside(p, ROOT / "data"):
            return {"error": "Keep the data folder outside the program folder (or use its default, the git-ignored data\\ folder)."}
        p.mkdir(parents=True, exist_ok=True)
    busy = busy_jobs(app.engine.base) if app.engine.running() else []
    if busy:
        return {"error": f"{len(busy)} job(s) are running - try again when they are done."}
    if app.op_lock.locked():
        return {"error": "The engine is restarting or updating - try again in a moment."}
    try:
        app.cfg = save_config({"data_dir": path})
    except RuntimeError as e:
        return {"error": str(e)}
    app.data = data_dir(app.cfg)
    app.log(f"data folder set to {app.data}")
    threading.Thread(target=app.restart_engine, daemon=True).start()
    return {"ok": True, "data_dir": str(app.data)}


# --------------------------------------------------------------------------------------------- windows plumbing
def find_own_window() -> int:
    """Top-level visible window of this process (the pywebview form)."""
    if os.name != "nt":
        return 0
    user32, pid, found = ctypes.windll.user32, os.getpid(), []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        p = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(ctypes.c_void_p(hwnd)) and user32.GetWindowTextLengthW(ctypes.c_void_p(hwnd)):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(proc(cb), 0)
    return found[0] if found else 0


def set_window_icon() -> None:
    hwnd = find_own_window()
    if not hwnd or not ICON.exists():
        return
    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    for kind, size in ((0, 16), (1, 32), (1, 256)):          # ICON_SMALL, ICON_BIG
        h = user32.LoadImageW(None, str(ICON), 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
        if h:
            user32.SendMessageW(ctypes.c_void_p(hwnd), 0x80, ctypes.c_void_p(kind), ctypes.c_void_p(h))  # WM_SETICON
    try:   # dark title bar to match the app (DWMWA_USE_IMMERSIVE_DARK_MODE, Windows 10 2004+)
        on = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 20, ctypes.byref(on), ctypes.sizeof(on))
    except Exception:
        pass


def find_edge() -> str | None:
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            p = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if p.exists():
                return str(p)
    return None


EDGE_PROFILE = app_home() / "edge-profile"


def open_edge_window(url: str, log) -> bool:
    edge = find_edge()
    if not edge:
        log("Microsoft Edge not found - opening the default browser")
        os.startfile(url)                                                          # noqa: S606
        return False
    subprocess.Popen([edge, f"--app={url}", f"--user-data-dir={EDGE_PROFILE}", "--no-first-run", "--no-default-browser-check",
                      "--disable-background-mode", "--window-size=1440,900"])
    return True


def edge_running() -> bool:
    """Edge relaunches itself, so its first process says nothing. While a browser runs on our profile it holds
    <profile>\lockfile open exclusively; once all its windows are closed the file can be deleted."""
    lf = EDGE_PROFILE / "lockfile"
    if not lf.exists():
        return False
    try:
        lf.unlink()
        return False
    except OSError:
        return True


def close_edge_windows(log) -> None:
    """Stop the Edge processes that use our profile (never the user's own Edge)."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | Where-Object { $_.CommandLine -like '*"
          + str(EDGE_PROFILE).replace("'", "''") + "*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
          "-ErrorAction SilentlyContinue }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, **NOWIN)
    log("closed the Edge app window")


def spawn_launcher(extra: list[str]) -> None:
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pyw if pyw.exists() else sys.executable)
    subprocess.Popen([exe, str(Path(__file__).resolve()), *extra], cwd=str(ROOT), close_fds=True,
                     creationflags=0x00000008 | 0x00000200)                      # DETACHED_PROCESS | NEW_PROCESS_GROUP


def other_instance(page: str | None) -> bool:
    """If a launcher already runs, bring it to the front (optionally on a page) and return True."""
    try:
        lk = json.loads(LOCK.read_text("utf-8"))
    except Exception:
        return False
    if not lk.get("pid") or not pid_alive(int(lk["pid"])) or not lk.get("url"):
        return False
    import urllib.request
    try:
        req = urllib.request.Request(lk["url"] + "/api/focus", data=json.dumps({"page": page}).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3).read()
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description="CutLab desktop app")
    ap.add_argument("--edge", action="store_true", help="use an Edge --app window instead of pywebview")
    ap.add_argument("--update", action="store_true", help="open the update page")
    ap.add_argument("--wait-pid", type=int, default=0, help=argparse.SUPPRESS)
    args = ap.parse_args()

    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass
    if args.wait_pid:
        for _ in range(120):
            if not pid_alive(args.wait_pid):
                break
            time.sleep(0.25)
    first_page = "/update" if args.update else "/"
    if other_instance(first_page if args.update else None):
        return

    app = App(args)
    app.log(f"CutLab desktop starting - checkout {ROOT}, data {app.data}")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    app.control_url = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    app.write_lock()
    threading.Thread(target=app.boot, daemon=True).start()
    threading.Thread(target=app.watchdog, daemon=True).start()

    webview = None
    want = "edge" if args.edge else app.cfg.get("window", "auto")
    if want != "edge":
        try:
            import webview  # type: ignore
        except Exception as e:
            app.log(f"pywebview not available ({e}) - using an Edge app window")
    try:
        if webview is not None:
            app.mode = "webview"
            run_webview(app, webview, first_page)
        else:
            app.mode = "edge"
            if open_edge_window(app.control_url + first_page, app.log):
                for _ in range(60):                  # wait for the browser to come up ...
                    if edge_running():
                        break
                    time.sleep(0.5)
                while edge_running() and not app.force_quit:   # ... and run until its last window is closed
                    time.sleep(1)
            else:                                    # default browser: nothing to wait on - keep serving until quit
                while not app.force_quit:
                    time.sleep(1)
    finally:
        app.shutdown()
        server.shutdown()


def run_webview(app: App, webview, first_page: str) -> None:
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False
    except Exception:
        pass
    win = webview.create_window(f"CutLab — {app.data}", app.control_url + first_page, width=1440, height=900, min_size=(960, 600),
                                background_color="#0C0D10", text_select=True)
    app.window = win
    win.events.closing += app.on_closing
    win.events.shown += lambda: threading.Timer(0.3, set_window_icon).start()

    menu = None
    try:
        from webview.menu import Menu, MenuAction, MenuSeparator  # type: ignore
        menu = [Menu("CutLab", [
            MenuAction("Check for updates…", lambda: app.show_page("/update")),
            MenuAction("Reload", lambda: win.evaluate_js("location.reload()")),
            MenuSeparator(),
            MenuAction("Data folder…", lambda: app.show_page("/settings")),
            MenuAction("Open data folder", lambda: app.open_path("data")),
            MenuAction("Open log folder", lambda: app.open_path("logs")),
            MenuAction("Restart engine", lambda: (app.show_page("/"), threading.Thread(target=app.restart_engine, daemon=True).start())),
            MenuSeparator(),
            MenuAction("Quit", lambda: app.quit()),
        ])]
    except Exception as e:
        app.log(f"menu not available: {e}")
    storage = app_home() / "webview"           # persistent localStorage / IndexedDB (projects, layouts)
    kw = dict(gui="edgechromium", private_mode=False, storage_path=str(storage), icon=str(ICON))
    if menu:
        kw["menu"] = menu
    try:
        webview.start(**kw)
    except TypeError:                          # older pywebview without menu/icon arguments
        kw.pop("menu", None)
        kw.pop("icon", None)
        webview.start(**kw)


if __name__ == "__main__":
    main()
