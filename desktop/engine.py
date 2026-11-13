"""Paths, launcher config and control of the CutLab engine process (backend/ -> python -m app).

Shared by the desktop launcher (cutlab_desktop.py), the updater and setup.ps1. Standard library only, so it runs
before any dependency is installed.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
DESKTOP = ROOT / "desktop"
VENV = BACKEND / ".venv"
VENV_PY = VENV / "Scripts" / "python.exe"
VENV_PYW = VENV / "Scripts" / "pythonw.exe"
ICON = DESKTOP / "cutlab.ico"

NOWIN = {"creationflags": 0x08000000} if os.name == "nt" else {}          # CREATE_NO_WINDOW
DEFAULT_PORT = 8787


# --------------------------------------------------------------------------------------------- launcher config
def config_path() -> Path:
    """%LOCALAPPDATA%\\CutLab\\desktop.json - per user, outside the git checkout, never touched by updates."""
    if os.environ.get("CUTLAB_DESKTOP_CONFIG"):
        return Path(os.environ["CUTLAB_DESKTOP_CONFIG"])
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "CutLab" / "desktop.json"


def app_home() -> Path:
    """Launcher's own files (config, launcher log, Edge fallback profile)."""
    d = config_path().parent
    d.mkdir(parents=True, exist_ok=True)
    return d


DEFAULTS = {
    "data_dir": "",            # empty = <checkout>\data (git-ignored). Same rule as backend/app/config.py
    "port": DEFAULT_PORT,      # preferred engine port; a free one is picked when it is taken by another program
    "update_remote": "origin",
    "update_branch": "main",
    "window": "auto",          # auto | webview | edge
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(config_path().read_text("utf-8")))
    except Exception:
        pass
    return cfg


def save_config(patch: dict) -> dict:
    cfg = load_config()
    cfg.update(patch)
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")
    return cfg


def data_dir(cfg: dict | None = None) -> Path:
    """$CUTLAB_DATA, else desktop.json "data_dir", else <checkout>\\data. Must match backend/app/config.py:_data_dir."""
    if os.environ.get("CUTLAB_DATA"):
        return Path(os.environ["CUTLAB_DATA"])
    d = (cfg or load_config()).get("data_dir")
    if d:
        return Path(os.path.expandvars(d)).expanduser()
    return ROOT / "data"


# --------------------------------------------------------------------------------------------- engine probing
def http_json(url: str, timeout: float = 2.0, method: str = "GET") -> object | None:
    try:
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def health(base: str, timeout: float = 2.0) -> dict | None:
    """The engine's /api/health if a CutLab engine answers at `base`, else None."""
    h = http_json(f"{base}/api/health", timeout)
    return h if isinstance(h, dict) and "routers" in h else None


def busy_jobs(base: str) -> list[dict]:
    jobs = http_json(f"{base}/api/jobs?limit=500", 5) or []
    return [j for j in jobs if isinstance(j, dict) and j.get("status") in ("queued", "running")]


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def same_path(a: str | Path, b: str | Path) -> bool:
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except Exception:
        return False


def kill_tree(pid: int) -> None:
    """Stop a process and its children (ffmpeg / Blender jobs started by the engine)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, **NOWIN)
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


def pid_on_port(port: int) -> int | None:
    """PID listening on 127.0.0.1:<port> (for engines whose /api/health predates the "pid" field)."""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=10, **NOWIN).stdout
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[1].rsplit(":", 1)[-1] == str(port):
            return int(parts[4])
    return None


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, **NOWIN)
        return str(pid) in r.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------------------------- engine process
class Engine:
    """One CutLab engine. Either started by us (owned) or an already-running one we attached to."""

    def __init__(self, data: Path, log: Callable[[str], None] = print):
        self.data = data
        self.log = log
        self.port = DEFAULT_PORT
        self.proc: subprocess.Popen | None = None
        self.pid: int | None = None
        self.owned = False

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def log_files(self) -> tuple[Path, Path]:
        return self.data / "server.log", self.data / "server.err"

    def attach_or_pick_port(self, preferred: int) -> str:
        """Returns "attached" (a CutLab engine on this data folder already runs), or "start" with self.port set to
        the preferred port, or to a free one when the preferred port is taken by another program or another CutLab."""
        h = health(f"http://127.0.0.1:{preferred}")
        if h:
            if not h.get("data") or same_path(h["data"], self.data):
                self.port, self.pid, self.owned = preferred, h.get("pid") or pid_on_port(preferred), False
                self.log(f"attached to running engine on :{preferred} (pid {self.pid})")
                return "attached"
            self.log(f"port {preferred}: a CutLab engine with another data folder ({h['data']}) - using a free port")
            self.port = free_port()
        elif port_in_use(preferred):
            self.port = free_port()
            self.log(f"port {preferred} is used by another program - using :{self.port}")
        else:
            self.port = preferred
        return "start"

    def start(self, extra_env: dict | None = None) -> None:
        if not VENV_PY.exists():
            raise RuntimeError(f"Python environment missing ({VENV_PY}). Run setup.ps1 first.")
        self.data.mkdir(parents=True, exist_ok=True)
        out, err = self.log_files()
        env = dict(os.environ)
        env.update({"CUTLAB_DATA": str(self.data), "CUTLAB_PORT": str(self.port), "PYTHONIOENCODING": "utf-8"})
        env.update(extra_env or {})
        with open(out, "ab") as fo, open(err, "ab") as fe:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            fe.write(f"\n===== engine start {stamp} (desktop launcher, port {self.port}) =====\n".encode())
            fe.flush()
            self.proc = subprocess.Popen([str(VENV_PY), "-m", "app"], cwd=str(BACKEND), env=env, stdout=fo, stderr=fe,
                                         stdin=subprocess.DEVNULL, **NOWIN)
        self.pid, self.owned = self.proc.pid, True
        self.log(f"engine started on :{self.port} (pid {self.pid})")

    def wait_ready(self, timeout: float = 180, tick: Callable[[float], None] | None = None) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc and self.proc.poll() is not None:
                raise RuntimeError(f"The engine exited during start-up (code {self.proc.returncode}).")
            h = health(self.base, 2)
            if h:
                self.pid = h.get("pid") or self.pid
                return True
            if tick:
                tick(time.time() - t0)
            time.sleep(0.5)
        return False

    def running(self) -> bool:
        if self.proc is not None:
            return self.proc.poll() is None
        return bool(self.pid) and health(self.base) is not None

    def stop(self) -> None:
        """Stops the engine (also one we only attached to - used before an update/restart)."""
        pid = self.pid
        if pid and pid_alive(pid):
            kill_tree(pid)
            for _ in range(40):              # wait for the process only: the port may already belong to a new engine
                if not pid_alive(pid):
                    break
                time.sleep(0.25)
            self.log(f"engine stopped (pid {pid})")
        self.proc, self.pid = None, None

    def tail(self, n: int = 30) -> str:
        _, err = self.log_files()
        try:
            return "\n".join(err.read_text("utf-8", errors="replace").splitlines()[-n:])
        except Exception:
            return ""


if __name__ == "__main__":  # tiny CLI for scripts: python desktop/engine.py data-dir|config-path
    cmd = sys.argv[1] if len(sys.argv) > 1 else "data-dir"
    print({"data-dir": lambda: data_dir(), "config-path": config_path}[cmd]())
