"""Git-based updates for CutLab, plus "sync": make dependencies and the built frontend match the checked-out code.

    python desktop/updater.py check            what an update would bring (fetches origin/main)
    python desktop/updater.py apply            pull (fast-forward only) + sync; the engine must be stopped by the caller
    python desktop/updater.py sync             pip install / npm ci / frontend build, only where something changed
    add --json for machine-readable output

Never touches the data folder: updates are `git merge --ff-only`, which leaves untracked and git-ignored files alone,
and they are refused when tracked files have local changes or the data folder is not git-ignored.
Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import FRONTEND, NOWIN, ROOT, VENV, VENV_PY, config_path, data_dir, load_config  # noqa: E402

PY_REQS = ["backend/requirements.txt", "desktop/requirements.txt"]
NODE_FILES = ["frontend/package-lock.json"]
DIST = FRONTEND / "dist"
PY_STAMP = VENV / ".cutlab-requirements"
NODE_STAMP = FRONTEND / "node_modules" / ".cutlab-lock"
DIST_STAMP = DIST / ".cutlab-build"

Log = Callable[[str], None]


class UpdateError(Exception):
    pass


# --------------------------------------------------------------------------------------------- process helpers
def _env() -> dict:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"                  # no hidden credential prompts (there is no console)
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=20")
    return env


def git(*args: str, check: bool = True, timeout: float = 120) -> str:
    try:
        r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=_env(), **NOWIN)
    except FileNotFoundError:
        raise UpdateError("git is not installed or not on PATH.")
    except subprocess.TimeoutExpired:
        raise UpdateError(f"git {args[0]} timed out.")
    if check and r.returncode:
        raise UpdateError(f"git {' '.join(args)} failed: {(r.stderr or r.stdout).strip()}")
    return r.stdout.rstrip()


def run(cmd: list[str], cwd: Path, log: Log, timeout: float = 1800) -> None:
    """Run a command, streaming its output to `log`; raise UpdateError on failure."""
    log("$ " + " ".join(Path(c).name if i == 0 else c for i, c in enumerate(cmd)))
    try:
        p = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             text=True, encoding="utf-8", errors="replace", env=_env(), **NOWIN)
    except FileNotFoundError:
        raise UpdateError(f"{cmd[0]} was not found.")
    t0 = time.time()
    assert p.stdout
    for line in p.stdout:
        line = line.rstrip()
        if line:
            log(line)
        if time.time() - t0 > timeout:
            p.kill()
            raise UpdateError(f"{Path(cmd[0]).name} timed out")
    if p.wait():
        raise UpdateError(f"{Path(cmd[0]).name} {' '.join(cmd[1:3])} failed (exit code {p.returncode}).")


def _hash(files: list[str]) -> str:
    h = hashlib.sha256()
    for f in files:
        p = ROOT / f
        h.update(f.encode() + b"\0" + (p.read_bytes() if p.exists() else b"-") + b"\0")
    return h.hexdigest()[:16]


def _read(p: Path) -> str:
    try:
        return p.read_text("utf-8").strip()
    except Exception:
        return ""


def frontend_rev() -> str:
    """Identity of the frontend sources: git tree hash of frontend/ (+ "-dirty" when it has local edits)."""
    try:
        tree = git("rev-parse", "HEAD:frontend")
        dirty = git("status", "--porcelain", "--", "frontend")
        return tree + ("-dirty" if dirty else "")
    except UpdateError:
        return ""


def npm() -> str:
    exe = shutil.which("npm.cmd") or shutil.which("npm")
    if not exe:
        raise UpdateError("Node.js (npm) was not found. Install Node.js 20+ from https://nodejs.org and run setup.ps1 again.")
    return exe


# --------------------------------------------------------------------------------------------- sync
def sync_needs() -> dict:
    """What has to be (re)done so that the venv, node_modules and frontend/dist match the checked-out code."""
    rev = frontend_rev()
    built = _read(DIST_STAMP)
    return {
        "python": _read(PY_STAMP) != _hash(PY_REQS),
        "node": not (FRONTEND / "node_modules").exists() or _read(NODE_STAMP) != _hash(NODE_FILES),
        # a build from a dirty tree (local frontend edits) is left alone: developers rebuild themselves
        "frontend": not (DIST / "index.html").exists() or (bool(rev) and not rev.endswith("-dirty") and built != rev),
    }


def pip_install(log: Log) -> None:
    reqs = [str(ROOT / r) for r in PY_REQS if (ROOT / r).exists()]
    uv = shutil.which("uv")
    if uv:
        cmd = [uv, "pip", "install", "--python", str(VENV_PY)]
    else:
        if subprocess.run([str(VENV_PY), "-m", "pip", "--version"], capture_output=True, **NOWIN).returncode:
            run([str(VENV_PY), "-m", "ensurepip", "--upgrade"], ROOT, log)
        cmd = [str(VENV_PY), "-m", "pip", "install", "--disable-pip-version-check"]
    for r in reqs:
        cmd += ["-r", r]
    run(cmd, ROOT, log)


def build_frontend(log: Log) -> None:
    """npm run build into dist.next, then swap - a failed build leaves the working dist in place."""
    nxt, old = FRONTEND / "dist.next", FRONTEND / "dist.old"
    for d in (nxt, old):
        shutil.rmtree(d, ignore_errors=True)
    run([npm(), "run", "build", "--", "--outDir", "dist.next", "--emptyOutDir"], FRONTEND, log)
    if not (nxt / "index.html").exists():
        raise UpdateError("The frontend build produced no index.html.")
    (nxt / ".cutlab-build").write_text(frontend_rev(), "utf-8")
    if DIST.exists():
        DIST.rename(old)
    nxt.rename(DIST)
    shutil.rmtree(old, ignore_errors=True)


def _npm_tree_ok() -> bool:
    try:
        return subprocess.run([npm(), "ls", "--depth=0"], cwd=str(FRONTEND), capture_output=True, timeout=120,
                              **NOWIN).returncode == 0
    except Exception:
        return False


def sync(log: Log = print, force: bool = False) -> dict:
    needs = sync_needs()
    if force:
        needs = {k: True for k in needs}
    if not VENV_PY.exists():
        raise UpdateError(f"No Python environment at {VENV}. Run setup.ps1.")
    if needs["python"]:
        log("Installing Python packages…")
        pip_install(log)
        PY_STAMP.write_text(_hash(PY_REQS), "utf-8")
    if needs["node"] and not force and not NODE_STAMP.exists() and (FRONTEND / "node_modules").exists() and _npm_tree_ok():
        log("Frontend packages already installed (npm ls ok).")      # existing dev install: do not wipe it with npm ci
        NODE_STAMP.write_text(_hash(NODE_FILES), "utf-8")
        needs["node"] = False
    if needs["node"]:
        log("Installing frontend packages (npm ci)…")
        run([npm(), "ci", "--no-audit", "--no-fund"], FRONTEND, log)
        NODE_STAMP.write_text(_hash(NODE_FILES), "utf-8")
    if needs["frontend"]:
        log("Building the interface…")
        build_frontend(log)
    if not any(needs.values()):
        log("Dependencies and interface are up to date.")
    return needs


# --------------------------------------------------------------------------------------------- update check
def _inside(child: Path, parent: Path) -> bool:
    try:
        Path(os.path.realpath(child)).relative_to(os.path.realpath(parent))
        return True
    except ValueError:
        return False


def check(fetch: bool = True, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    remote, branch = cfg["update_remote"], cfg["update_branch"]
    res: dict = {"remote": remote, "branch": branch, "remote_url": "", "current_branch": "", "head": "", "head_subject": "",
                 "target": "", "behind": 0, "ahead": 0, "commits": [], "files": [], "blocked": "", "dirty_files": [],
                 "warnings": [], "will": {}, "data_dir": str(data_dir(cfg)), "checked_at": time.strftime("%H:%M:%S")}
    try:
        git("rev-parse", "--is-inside-work-tree")
    except UpdateError as e:
        res["blocked"] = f"{ROOT} is not a git checkout ({e}). Updates need a `git clone` of the repository."
        return res
    res["current_branch"] = git("rev-parse", "--abbrev-ref", "HEAD")
    res["head"] = git("rev-parse", "--short", "HEAD")
    res["head_subject"] = git("log", "-1", "--format=%s")
    res["remote_url"] = git("remote", "get-url", remote, check=False)

    # the data folder must never be part of the checkout's tracked files
    data = data_dir(cfg)
    if _inside(data, ROOT):
        rel = os.path.relpath(os.path.realpath(data), os.path.realpath(ROOT)).replace("\\", "/")
        tracked = git("ls-files", "--", rel, check=False)
        ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", rel + "/"], **NOWIN).returncode == 0
        if tracked or not ignored:
            res["blocked"] = (f"The data folder {data} is inside the checkout and not git-ignored. Move it (\"data_dir\" "
                              f"in {config_path()}) or add it to .gitignore before updating.")
            return res

    if not res["remote_url"]:
        res["blocked"] = f"No git remote named '{remote}'."
        return res
    if res["current_branch"] != branch:
        res["blocked"] = (f"This checkout is on branch '{res['current_branch']}'. Updates follow '{branch}' only "
                          f"(switch with: git switch {branch}).")
        return res
    dirty = git("status", "--porcelain", "--untracked-files=no").splitlines()
    if dirty:
        res["dirty_files"] = [d[3:] for d in dirty][:30]
        res["blocked"] = (f"{len(dirty)} tracked file(s) have local changes. Commit or stash them first - "
                          "the update will not overwrite your edits.")
    untracked = git("status", "--porcelain", "--untracked-files=normal").splitlines()
    untracked = [u[3:] for u in untracked if u.startswith("??")]
    if untracked:
        res["warnings"].append(f"{len(untracked)} untracked file(s) in the checkout (e.g. {untracked[0]}). They are kept; "
                               "the update stops if it brings a file with the same name.")

    if fetch:
        try:
            git("fetch", "--quiet", remote, branch, timeout=90)
        except UpdateError as e:
            res["blocked"] = res["blocked"] or f"Could not reach {res['remote_url']}: {e}"
            return res
    target = f"{remote}/{branch}"
    try:
        res["target"] = git("rev-parse", "--short", target)
    except UpdateError:
        res["blocked"] = res["blocked"] or f"{target} is unknown - fetch failed?"
        return res
    res["behind"] = int(git("rev-list", "--count", f"HEAD..{target}") or 0)
    res["ahead"] = int(git("rev-list", "--count", f"{target}..HEAD") or 0)
    if res["behind"]:
        log = git("log", "--format=%h%x1f%an%x1f%ar%x1f%s", f"HEAD..{target}", "-n", "200")
        res["commits"] = [dict(zip(("hash", "author", "when", "subject"), l.split("\x1f"))) for l in log.splitlines()]
        files = git("diff", "--name-status", f"HEAD...{target}").splitlines()
        res["files"] = [{"status": f.split("\t")[0][:1], "path": f.split("\t")[-1]} for f in files]
        paths = [f["path"] for f in res["files"]]
        res["will"] = {
            "python": any(p in PY_REQS for p in paths),
            "node": any(p in NODE_FILES for p in paths),
            "frontend": any(p.startswith("frontend/") for p in paths),
            "engine": any(p.startswith("backend/") for p in paths),
            "launcher": any(p.startswith("desktop/") for p in paths),
        }
    if res["ahead"] and res["behind"]:
        res["blocked"] = res["blocked"] or (f"Local '{branch}' has {res['ahead']} commit(s) that are not on {target}, and "
                                            f"{target} has {res['behind']} new one(s). Merge or rebase manually.")
    elif res["ahead"]:
        res["warnings"].append(f"{res['ahead']} local commit(s) not pushed to {target}.")
    return res


def apply(log: Log = print, cfg: dict | None = None) -> dict:
    """Fast-forward to origin/main and sync. Caller stops the engine first and starts it afterwards.
    If installing/building fails, the checkout is put back on the previous commit."""
    info = check(fetch=True, cfg=cfg)
    if info["blocked"]:
        raise UpdateError(info["blocked"])
    if not info["behind"]:
        log("Already up to date.")
        return {"updated": False, "info": info}
    old = git("rev-parse", "HEAD")
    target = f"{info['remote']}/{info['branch']}"
    log(f"Updating {info['head']} → {info['target']} ({info['behind']} commit(s))…")
    git("merge", "--ff-only", "--quiet", target)
    try:
        sync(log)
    except Exception as e:
        log(f"Update failed: {e}")
        log(f"Rolling back to {old[:7]}…")
        git("reset", "--hard", "--quiet", old)       # safe: tracked files were verified clean; untracked/ignored untouched
        try:
            sync(log)
        except Exception as e2:
            log(f"Rollback sync also failed: {e2}")
        raise UpdateError(f"{e} - rolled back to the previous version.")
    log(f"Updated to {git('rev-parse', '--short', 'HEAD')}: {git('log', '-1', '--format=%s')}")
    return {"updated": True, "from": old, "to": git("rev-parse", "HEAD"), "info": info}


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv
    cmd = args[0] if args else "check"
    try:
        if cmd == "check":
            out = check()
        elif cmd == "sync":
            out = sync(force="--force" in sys.argv)
        elif cmd == "apply":
            from engine import DEFAULT_PORT, health
            base = f"http://127.0.0.1:{load_config().get('port', DEFAULT_PORT)}"
            if health(base):
                raise UpdateError(f"Stop the engine at {base} first (or use Check for updates in the app).")
            out = apply()
        else:
            raise SystemExit(__doc__)
    except UpdateError as e:
        print(json.dumps({"error": str(e)}) if as_json else f"error: {e}", file=sys.stdout if as_json else sys.stderr)
        sys.exit(1)
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    elif cmd == "check":
        print(f"{out['current_branch']} @ {out['head']}  →  {out['remote']}/{out['branch']} @ {out['target']}  "
              f"behind {out['behind']}, ahead {out['ahead']}")
        for c in out["commits"]:
            print(f"  {c['hash']} {c['subject']}  ({c['author']}, {c['when']})")
        for w in out["warnings"]:
            print("warning:", w)
        if out["blocked"]:
            print("blocked:", out["blocked"])
            sys.exit(2)
