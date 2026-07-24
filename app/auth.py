"""Authentication, session management, and brute-force protection."""

import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from app.config import ADMIN_USERNAME, LOGIN_LOCKOUT_SECONDS, LOGIN_MAX_ATTEMPTS
from app.models import find_user_by_email, load_admin_password_hash

logger = logging.getLogger(__name__)

# Env hash computed once at startup
_env_password_hash: str = ""


def init_admin_hash() -> None:
    """Call once at app startup to compute the env-derived admin hash."""
    global _env_password_hash
    raw = (os.environ.get("ADMIN_PASSWORD_HASH") or "").strip()
    if not raw:
        raw = generate_password_hash(os.environ.get("ADMIN_PASSWORD", "password123"))
    _env_password_hash = raw


def get_admin_password_hash() -> str:
    return load_admin_password_hash(_env_password_hash)


def authenticate(login_id: str, password: str):
    """Returns (ok, session_payload)."""
    login_id = (login_id or "").strip()
    if not login_id or not password:
        return False, None

    if login_id == ADMIN_USERNAME and check_password_hash(get_admin_password_hash(), password):
        return True, {
            "username": ADMIN_USERNAME, "display_name": ADMIN_USERNAME,
            "role": "admin", "email": "",
        }

    user = find_user_by_email(login_id)
    if user and check_password_hash(user.get("password_hash") or "", password):
        return True, {
            "username": user["email"],
            "display_name": user.get("name") or user["email"],
            "role": "user", "email": user["email"],
        }
    return False, None


def start_session(payload: dict) -> None:
    session.clear()
    session["logged_in"] = True
    session["username"] = payload["username"]
    session["display_name"] = payload["display_name"]
    session["role"] = payload["role"]
    session["email"] = payload.get("email") or ""
    session.permanent = True


# ---------------------------------------------------------------------------
# Brute-force protection (in-memory, resets on restart)
# ---------------------------------------------------------------------------
_login_attempts: dict[str, dict] = defaultdict(lambda: {"failures": 0, "locked_until": None})


def client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def login_lock_remaining(ip: str) -> int:
    rec = _login_attempts[ip]
    until = rec.get("locked_until")
    if not until:
        return 0
    remaining = int((until - datetime.now(timezone.utc)).total_seconds())
    if remaining <= 0:
        _login_attempts[ip] = {"failures": 0, "locked_until": None}
        return 0
    return remaining


def record_login_failure(ip: str) -> None:
    rec = _login_attempts[ip]
    rec["failures"] = int(rec.get("failures") or 0) + 1
    if rec["failures"] >= LOGIN_MAX_ATTEMPTS:
        rec["locked_until"] = datetime.now(timezone.utc) + timedelta(seconds=LOGIN_LOCKOUT_SECONDS)
        rec["failures"] = 0
    logger.warning("Failed login attempt from %s", ip)


def clear_login_failures(ip: str) -> None:
    _login_attempts.pop(ip, None)


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("main.login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function
