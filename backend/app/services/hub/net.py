"""Polite HTTP for connectors: descriptive User-Agent, per-host spacing, retries with backoff (429/5xx, honours
Retry-After), on-disk caching of JSON search pages, streamed downloads with size caps, and key redaction in errors.
Tests inject an httpx.MockTransport through `set_transport`."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

import httpx

from . import paths

USER_AGENT = "CutLab/0.1 (local asset manager)"
SECRET_PARAMS = {"key", "token", "api_key", "access_token"}

_transport: httpx.BaseTransport | None = None
_last: dict[str, float] = {}
_host_lock = threading.Lock()
_sleep: Callable[[float], None] = time.sleep


class HubHTTPError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(paths.redact(message))
        self.status = status


def set_transport(t: httpx.BaseTransport | None, *, no_sleep: bool = False) -> None:
    """Testing hook: route all hub HTTP through `t` (e.g. httpx.MockTransport)."""
    global _transport, _sleep
    _transport = t
    _sleep = (lambda _s: None) if no_sleep else time.sleep
    _last.clear()


def client(timeout: float = 30) -> httpx.Client:
    return httpx.Client(transport=_transport, timeout=timeout, follow_redirects=True,
                        headers={"User-Agent": USER_AGENT})


def _wait_turn(host: str, min_interval: float) -> None:
    if min_interval <= 0:
        return
    with _host_lock:
        now = time.monotonic()
        wait = _last.get(host, 0) + min_interval - now
        _last[host] = max(now, _last.get(host, 0) + min_interval)
    if wait > 0:
        _sleep(wait)


def _cache_key(url: str, params: dict | None) -> str:
    clean = {k: v for k, v in (params or {}).items() if k.lower() not in SECRET_PARAMS}
    return hashlib.sha1((url + "?" + urlencode(sorted(clean.items()), doseq=True)).encode()).hexdigest()


def request(method: str, url: str, *, params: dict | None = None, headers: dict | None = None,
            min_interval: float = 0.5, retries: int = 3, timeout: float = 30, json_body: Any = None) -> httpx.Response:
    host = httpx.URL(url).host
    last_err = ""
    for attempt in range(retries + 1):
        _wait_turn(host, min_interval)
        try:
            with client(timeout) as c:
                r = c.request(method, url, params=params, headers=headers, json=json_body)
        except httpx.HTTPError as e:
            last_err = f"{type(e).__name__}: {e}"
            if attempt < retries:
                _sleep(min(2 ** attempt, 20))
                continue
            raise HubHTTPError(f"Network error for {host}: {last_err}") from None
        if r.headers.get("x-amzn-waf-action") or r.headers.get("cf-mitigated"):
            # a bot-protection challenge: we never try to get around it
            raise HubHTTPError(f"{host} answered with a bot-protection challenge instead of data. CutLab does not bypass "
                               "such checks - try again later (an API key/token may help) or use the site in your browser.", r.status_code)
        if r.status_code == 429 or r.status_code >= 500:
            last_err = f"HTTP {r.status_code}"
            if attempt < retries:
                ra = r.headers.get("Retry-After")
                try:
                    delay = float(ra) if ra else 2 ** (attempt + 1)
                except ValueError:
                    delay = 2 ** (attempt + 1)
                _sleep(min(delay, 60))
                continue
        if r.status_code >= 400:
            detail = ""
            try:
                j = r.json()
                detail = str(j.get("detail") or j.get("error") or j.get("message") or "")[:200] if isinstance(j, dict) else ""
            except Exception:
                detail = r.text[:200] if "html" not in r.headers.get("content-type", "") else ""
            raise HubHTTPError(f"{host} answered HTTP {r.status_code}{': ' + detail if detail else ''}", r.status_code)
        return r
    raise HubHTTPError(f"{host} failed after retries ({last_err})")


def get_json(url: str, *, params: dict | None = None, headers: dict | None = None, cache_ttl: float = 0,
             min_interval: float = 0.5, timeout: float = 30) -> Any:
    """GET JSON; when cache_ttl > 0 the response is cached on disk (keys never part of the cache key)."""
    cpath: Path | None = None
    if cache_ttl > 0:
        cpath = paths.CACHE_DIR / f"{_cache_key(url, params)}.json"
        if cpath.exists() and time.time() - cpath.stat().st_mtime < cache_ttl:
            try:
                return json.loads(cpath.read_text("utf-8"))
            except Exception:
                pass
    r = request("GET", url, params=params, headers=headers, min_interval=min_interval, timeout=timeout)
    try:
        data = r.json()
    except Exception:
        raise HubHTTPError(f"{httpx.URL(url).host} did not return JSON") from None
    if cpath is not None:
        cpath.write_text(json.dumps(data), "utf-8")
    return data


def get_text(url: str, *, cache_ttl: float = 0, min_interval: float = 0.5, headers: dict | None = None) -> str:
    cpath: Path | None = None
    if cache_ttl > 0:
        cpath = paths.CACHE_DIR / f"{_cache_key(url, None)}.txt"
        if cpath.exists() and time.time() - cpath.stat().st_mtime < cache_ttl:
            return cpath.read_text("utf-8")
    r = request("GET", url, min_interval=min_interval, headers=headers)
    if cpath is not None:
        cpath.write_text(r.text, "utf-8")
    return r.text


class CapExceeded(RuntimeError):
    pass


def download(url: str, dest: Path, *, headers: dict | None = None, min_interval: float = 0.5,
             max_bytes: int | None = None, check: Callable[[], None] | None = None, retries: int = 2) -> dict:
    """Stream `url` to `dest` (atomic via .part). Returns {path, bytes, sha256}. `check` is called per chunk
    (raise to cancel). Raises CapExceeded when the file would exceed `max_bytes`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    host = httpx.URL(url).host
    tmp = dest.with_name(dest.name + ".part")
    for attempt in range(retries + 1):
        _wait_turn(host, min_interval)
        try:
            with client(timeout=120) as c, c.stream("GET", url, headers=headers) as r:
                if r.status_code == 429 or r.status_code >= 500:
                    if attempt < retries:
                        _sleep(min(float(r.headers.get("Retry-After") or 2 ** (attempt + 1)), 60))
                        continue
                if r.status_code >= 400:
                    raise HubHTTPError(f"Download from {host} failed: HTTP {r.status_code}", r.status_code)
                cl = int(r.headers.get("content-length") or 0)
                if max_bytes is not None and cl and cl > max_bytes:
                    raise CapExceeded(f"{dest.name} is {cl / 1e6:.1f} MB, over the remaining size budget")
                h, n = hashlib.sha256(), 0
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        n += len(chunk)
                        if max_bytes is not None and n > max_bytes:
                            raise CapExceeded(f"{dest.name} exceeded the remaining size budget")
                        if check:
                            check()
                        h.update(chunk)
                        f.write(chunk)
            tmp.replace(dest)
            return {"path": dest, "bytes": n, "sha256": h.hexdigest()}
        except httpx.HTTPError as e:
            if attempt < retries:
                _sleep(2 ** attempt)
                continue
            raise HubHTTPError(f"Download from {host} failed: {type(e).__name__}: {e}") from None
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
    raise HubHTTPError(f"Download from {host} failed after retries")
