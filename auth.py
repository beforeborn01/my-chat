"""Username-only auth. No passwords.

A signed cookie carries the logged-in username. The signature is HMAC-SHA256
with a server-side secret, so the cookie cannot be forged. There is no DB
session table — the cookie itself is the session.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
from functools import wraps

from flask import g, jsonify, redirect, request, url_for

SECRET = os.environ.get("SESSION_SECRET", "dev-only-insecure-secret-change-me").encode()
COOKIE_NAME = "mc_user"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

# 1-32 chars: letters, digits, dash, underscore, or unicode "word" chars (incl. Chinese).
_USERNAME_RE = re.compile(r"^[\w\-]{1,32}$", re.UNICODE)


def valid_username(name: str) -> bool:
    return bool(name) and bool(_USERNAME_RE.match(name)) and not name.startswith("_")


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def make_token(username: str) -> str:
    payload = _b64(username.encode("utf-8"))
    sig = _b64(hmac.new(SECRET, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def parse_token(token: str) -> str | None:
    if not token or "." not in token:
        return None
    payload, sig = token.split(".", 1)
    expected = _b64(hmac.new(SECRET, payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        username = _b64d(payload).decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None
    return username if valid_username(username) else None


def current_user() -> str | None:
    """Read the username from the request cookie."""
    if hasattr(g, "_mc_user"):
        return g._mc_user
    token = request.cookies.get(COOKIE_NAME)
    user = parse_token(token) if token else None
    g._mc_user = user
    return user


def attach_login_cookie(response, username: str):
    response.set_cookie(
        COOKIE_NAME,
        make_token(username),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
        path="/",
    )
    return response


def clear_login_cookie(response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


def login_required(view):
    """Redirect HTML requests to /login; return JSON 401 for API calls."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user():
            return view(*args, **kwargs)
        if request.path.startswith("/api/") or request.is_json:
            return jsonify({"error": "unauthorized"}), 401
        return redirect(url_for("login_page", next=request.path))

    return wrapper
