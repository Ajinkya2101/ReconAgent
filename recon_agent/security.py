"""Security plumbing shared by the worklist and the source simulator.

* Signed sessions: a cookie that carries a small payload plus an expiry, signed with HMAC-SHA256
  over RECON_SECRET_KEY. Editing it in the browser invalidates it.
* Passwords: PBKDF2-SHA256 hashes for the accounts file (users.py).
* `harden(app)`: security headers on every response, and state-changing requests started by
  another site refused (CSRF), on top of the SameSite cookie.
* `RateLimiter`: a small in-memory sliding window (failed sign-ins).
* `write_json`: atomic JSON writes, so a crash never leaves a half-written state file.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import config

PBKDF2_ITERATIONS = 600_000
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; "
       "object-src 'none'")
# FastAPI's /api/docs page loads Swagger UI from a CDN with an inline bootstrap script.
DOCS_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data: https://fastapi.tiangolo.com; frame-ancestors 'none'")

_generated_key: bytes | None = None


def secret_key() -> bytes:
    """RECON_SECRET_KEY, or a random per-process key (everyone signs in again after a restart)."""
    global _generated_key
    if config.SECRET_KEY:
        return config.SECRET_KEY.encode()
    if _generated_key is None:
        _generated_key = secrets.token_bytes(32)
        print("RECON_SECRET_KEY is not set: sessions end when the server restarts. Set it in .env "
              "(python -c \"import secrets; print(secrets.token_urlsafe(48))\").")
    return _generated_key


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(payload: dict, purpose: str, max_age: float | None = None) -> str:
    """A tamper-proof token for `payload`. `purpose` keeps one app's cookie out of the other's."""
    max_age = config.SESSION_HOURS * 3600 if max_age is None else max_age
    body = _b64(json.dumps({**payload, "exp": int(time.time() + max_age)}, separators=(",", ":")).encode())
    mac = hmac.new(secret_key(), f"{purpose}.{body}".encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(mac)}"


def unsign(token: str | None, purpose: str) -> dict | None:
    """The payload of a token from `sign`, or None when it is missing, forged or expired."""
    if not token or token.count(".") != 1:
        return None
    body, mac = token.split(".")
    want = hmac.new(secret_key(), f"{purpose}.{body}".encode(), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(_unb64(mac), want):
            return None
        payload = json.loads(_unb64(body))
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("exp", 0) < time.time():
        return None
    return payload


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, digest = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), _unb64(salt), int(iterations))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(got, _unb64(digest))


def same_secret(given: str | None, expected: str) -> bool:
    """Constant-time comparison for a shared token. An empty expected value never matches."""
    return bool(expected) and hmac.compare_digest((given or "").encode(), expected.encode())


def safe_next(target: str | None) -> str:
    """A local path to go to after sign-in; anything that could leave the site becomes /."""
    target = target or "/"
    if not target.startswith("/") or target.startswith("//") or any(c in target for c in "\\\r\n\t"):
        return "/"
    return target


def write_json(path: Path, data, mode: int | None = None, **dump_args) -> None:
    """Write JSON so a crash or a concurrent reader never sees a half-written file.
    `mode` sets the file's permissions (e.g. 0o600 for the accounts file)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, **dump_args)
        fh.flush()
        os.fsync(fh.fileno())
    if mode is not None:
        os.chmod(tmp, mode)
    for attempt in range(10):   # Windows refuses the replace while another process has the file open
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 9:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


class RateLimiter:
    """At most `limit` hits per `window` seconds per key."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self.hits: dict[str, deque] = {}
        self.lock = threading.Lock()

    def _recent(self, key: str) -> deque:
        q = self.hits.setdefault(key, deque())
        cutoff = time.monotonic() - self.window
        while q and q[0] <= cutoff:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        with self.lock:
            return len(self._recent(key)) >= self.limit

    def hit(self, key: str) -> None:
        with self.lock:
            self._recent(key).append(time.monotonic())
            if len(self.hits) > 10_000:   # forget idle keys
                for k in [k for k, q in self.hits.items() if not q]:
                    del self.hits[k]

    def reset(self, key: str) -> None:
        with self.lock:
            self.hits.pop(key, None)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def cross_site(request: Request) -> bool:
    """True when the browser says another site started this request."""
    site = request.headers.get("Sec-Fetch-Site")
    if site:
        return site not in ("same-origin", "none")
    origin = request.headers.get("Origin")
    if not origin:
        return False   # not a browser form or fetch (curl, the worklist's own server-to-server calls)
    allowed = config.ALLOWED_ORIGINS or {f"{request.url.scheme}://{request.headers.get('host', '')}"}
    return origin.rstrip("/") not in allowed


def harden(app: FastAPI, docs_path: str | None = None) -> None:
    """Security headers on every response; cross-site POST/PUT/PATCH/DELETE refused."""

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if request.method in UNSAFE_METHODS and cross_site(request):
            return JSONResponse({"detail": "Cross-site request refused."}, status_code=403)
        response = await call_next(request)
        h = response.headers
        path = request.url.path
        h.setdefault("Content-Security-Policy", DOCS_CSP if docs_path and path.startswith(docs_path) else CSP)
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "same-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if request.url.scheme == "https":
            h.setdefault("Strict-Transport-Security", "max-age=31536000")
        if not path.startswith(("/static/", "/sim-static/")):
            h.setdefault("Cache-Control", "no-store")
        return response
