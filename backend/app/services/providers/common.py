"""Shared helpers for the cloud / local generator adapters (video + 3D).

Rules enforced here:
  * API keys only ever travel in request headers; every string that leaves this package (errors, dry-run output)
    is passed through `scrub()`/`redact_*()` so a key can never be echoed to the UI, a log or a job result.
  * Download URLs returned by the providers are pre-signed: they are fetched with a *separate* client that
    carries no Authorization header.
  * HTTP clients are created through `make_client()` so tests can inject an `httpx.MockTransport`.
"""
from __future__ import annotations

import base64
import io
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

from ... import config
from ...jobs import JobCancelled

# --- test hooks -----------------------------------------------------------------------------------------------
TRANSPORT: httpx.BaseTransport | None = None      # tests set this to an httpx.MockTransport
FAST_POLL = False                                 # tests set True: nap() returns immediately

MASKED = "***"


class ProviderError(Exception):
    """Message is safe to show to the user (never contains a key)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --- settings -------------------------------------------------------------------------------------------------
def settings() -> dict:
    return config.load_settings()


def secret_values() -> list[str]:
    s = settings()
    return [str(s.get(k)) for k in config.SECRET_KEYS if s.get(k)]


def scrub(text: Any) -> str:
    """Remove every configured secret from a string (defence in depth)."""
    t = str(text)
    for sec in secret_values():
        if len(sec) >= 6:
            t = t.replace(sec, MASKED)
    return t


# --- http -----------------------------------------------------------------------------------------------------
def make_client(*, headers: dict | None = None, timeout: float = 60.0, local: bool = False,
                base_url: str = "") -> httpx.Client:
    kw: dict[str, Any] = dict(headers=headers or {}, timeout=timeout, follow_redirects=True)
    if base_url:
        kw["base_url"] = base_url
    if TRANSPORT is not None:
        kw["transport"] = TRANSPORT
    elif local:
        kw["trust_env"] = False                      # never route localhost through a proxy
    else:
        proxy = (settings().get("proxy") or "").strip()
        if proxy:
            kw["proxy"] = proxy
    return httpx.Client(**kw)


def bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def redact_headers(h: dict) -> dict:
    out = {}
    for k, v in h.items():
        if k.lower() in ("authorization", "x-api-key", "api-key", "x-tripo-key"):
            scheme = str(v).split(" ", 1)[0] if " " in str(v) else ""
            out[k] = f"{scheme} {MASKED}".strip()
        else:
            out[k] = v
    return out


def redact_body(o: Any) -> Any:
    """Shorten embedded base64 payloads and strip anything that looks like a secret."""
    if isinstance(o, dict):
        return {k: redact_body(v) for k, v in o.items()}
    if isinstance(o, list):
        return [redact_body(v) for v in o]
    if isinstance(o, str):
        if o.startswith("data:") and ";base64," in o:
            head, b64 = o.split(";base64,", 1)
            return f"{head};base64,<{len(b64) * 3 // 4} bytes omitted>"
        return scrub(o)
    return o


def describe_request(method: str, url: str, headers: dict | None = None, body: Any = None, note: str = "") -> dict:
    d: dict[str, Any] = {"method": method, "url": url, "headers": redact_headers(headers or {})}
    if body is not None:
        d["body"] = redact_body(body)
    if note:
        d["note"] = note
    return d


def error_message(resp: httpx.Response) -> str:
    """Best-effort human message from a provider error body. Never includes request headers."""
    msg = ""
    try:
        j = resp.json()
        if isinstance(j, dict):
            e = j.get("error", j)
            if isinstance(e, dict):
                code = e.get("code")
                m = e.get("message") or e.get("msg") or e.get("detail")
                msg = f"{code}: {m}" if code and m else str(m or code or "")
                if not msg and "suggestion" in j:
                    msg = str(j["suggestion"])
            elif isinstance(e, str):
                msg = e
            if not msg:
                m = j.get("message") or j.get("detail") or ""
                msg = f"{j.get('code')}: {m}" if j.get("code") not in (None, 0) and m else str(m)
            if j.get("suggestion") and j["suggestion"] not in msg:
                msg += f" ({j['suggestion']})"
    except Exception:
        t = (resp.text or "").strip()
        msg = "" if t.startswith("<") else t[:300]      # never dump an HTML error page into the UI
    return scrub(f"HTTP {resp.status_code}" + (f" - {msg}" if msg else ""))


def check(resp: httpx.Response, what: str) -> httpx.Response:
    if resp.status_code >= 400:
        hint = ""
        if resp.status_code == 401:
            hint = " Check the API key in Settings (gear icon)."
        elif resp.status_code == 403:
            hint = " (Forbidden: wrong key, content blocked, or insufficient credits - check Settings and your account balance.)"
        elif resp.status_code == 402:
            hint = " Not enough credits / a paid plan is required."
        raise ProviderError(f"{what} failed: {error_message(resp)}.{hint}".replace("..", "."), resp.status_code)
    return resp


# --- job helpers ----------------------------------------------------------------------------------------------
def nap(ctx, seconds: float) -> None:
    """Cancel-aware sleep (checks every 0.4 s). Raises JobCancelled."""
    if ctx is not None:
        ctx.check()
    if FAST_POLL:
        return
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        time.sleep(min(0.4, max(0.0, end - time.monotonic())))
        if ctx is not None:
            ctx.check()


def is_cancel(e: BaseException) -> bool:
    return isinstance(e, JobCancelled)


def download(url: str, dest: Path, ctx=None, *, max_bytes: int = 1_500_000_000) -> Path:
    """Fetch a pre-signed URL (no auth header) to `dest`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with make_client(timeout=300.0) as c:
        with c.stream("GET", url) as r:
            if r.status_code >= 400:
                raise ProviderError(f"Download failed (HTTP {r.status_code}). The link may have expired.", r.status_code)
            n = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    n += len(chunk)
                    if n > max_bytes:
                        raise ProviderError("Download exceeds the size limit.")
                    f.write(chunk)
                    if ctx is not None:
                        ctx.check()
    if tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise ProviderError("Downloaded file is empty.")
    tmp.replace(dest)
    return dest


def safe_name(s: str, fallback: str = "model", maxlen: int = 40) -> str:
    t = re.sub(r"[^A-Za-z0-9._-]+", "_", s.strip()).strip("._-")
    return (t[:maxlen] or fallback)


def stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


# --- images ---------------------------------------------------------------------------------------------------
def image_bytes_for_upload(path: str | Path, *, max_side: int = 2048, max_bytes: int = 8_000_000,
                           fmt: str | None = None,
                           allowed: tuple[str, ...] = ("image/jpeg", "image/png", "image/webp")) -> tuple[bytes, str]:
    """Return (bytes, mime) for a library image. Re-encodes (JPEG, or PNG when `fmt` says so) when the file is too
    big, too large in pixels, in an exotic format, or not in the requested `fmt`."""
    from PIL import Image  # pillow is installed in the venv

    p = Path(path)
    raw = p.read_bytes()
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(p.suffix.lower())
    with Image.open(io.BytesIO(raw)) as im:
        im.load()
        ok = mime in allowed and len(raw) <= max_bytes and max(im.size) <= max_side and (fmt is None or mime == fmt)
        if ok:
            return raw, mime  # type: ignore[return-value]
        if max(im.size) > max_side:
            im = im.copy()
            im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        if fmt == "image/png" or (fmt is None and im.mode in ("RGBA", "LA", "P")):
            im.convert("RGBA").save(buf, "PNG")
            return buf.getvalue(), "image/png"
        im.convert("RGB").save(buf, "JPEG", quality=92)
        return buf.getvalue(), "image/jpeg"


def data_uri(path: str | Path, **kw) -> str:
    b, mime = image_bytes_for_upload(path, **kw)
    return f"data:{mime};base64,{base64.b64encode(b).decode()}"


def media_row(mid: str) -> dict:
    from ... import db
    row = db.get_media(mid)
    if not row:
        raise ProviderError(f"Media {mid} not found in the library.", 404)
    return row


def media_image_path(mid: str) -> Path:
    row = media_row(mid)
    if row.get("kind") != "image":
        raise ProviderError("The start image must be an image from the library.", 400)
    p = Path(row["path"])
    if not p.exists():
        raise ProviderError("The image file is missing on disk.", 404)
    return p


def pretty_json(o: Any) -> str:
    return json.dumps(o, indent=2, ensure_ascii=False)
