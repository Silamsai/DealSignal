"""File-based data access layer with thread-safe I/O."""

import csv
import json
import logging
import os
import threading
import uuid
from datetime import datetime

from werkzeug.security import generate_password_hash

from app.config import (
    ADMIN_AUTH_PATH, BRAND_PATH, CSV_PATH, DATA_DIR, DEFAULT_BRAND,
    LEADS_CSV, SCAN_PATH, SENT_LOG, USERS_PATH,
)

logger = logging.getLogger(__name__)

# Thread lock for file I/O safety (use gunicorn --workers 1 with file storage)
_file_lock = threading.Lock()


# ------------------------------------------------------------------ scan
def load_scan() -> dict:
    if os.path.exists(SCAN_PATH):
        with open(SCAN_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"area": "Bradford", "max_price": "250000", "last_run": None}


def save_scan(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with _file_lock:
        with open(SCAN_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


# ------------------------------------------------------------------ brand
def load_brand() -> dict:
    if os.path.exists(BRAND_PATH):
        with open(BRAND_PATH, encoding="utf-8") as f:
            return {**DEFAULT_BRAND, **json.load(f)}
    return dict(DEFAULT_BRAND)


def save_brand(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with _file_lock:
        with open(BRAND_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


# ------------------------------------------------------------------ deals
def load_deals() -> list:
    if not os.path.exists(CSV_PATH):
        return []
    with open(CSV_PATH, encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ------------------------------------------------------------------ sent log
def log_sent(row: list) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with _file_lock:
        exists = os.path.exists(SENT_LOG)
        with open(SENT_LOG, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["timestamp", "listing_id", "address", "stannp_id", "test_mode", "status"])
            w.writerow(row)


def already_sent() -> set:
    """Only grey out rows that were successfully posted for real (not test/failed)."""
    if not os.path.exists(SENT_LOG):
        return set()
    sent = set()
    with open(SENT_LOG, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r.get("status") or "").strip() != "ok":
                continue
            test_val = str(r.get("test_mode") or "").strip().lower()
            if test_val in ("true", "1", "yes"):
                continue
            lid = (r.get("listing_id") or "").strip()
            if lid:
                sent.add(lid)
    return sent


# ------------------------------------------------------------------ leads
def load_leads() -> list:
    if not os.path.exists(LEADS_CSV):
        return []
    leads = []
    try:
        with open(LEADS_CSV, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)  # skip header
            for row in reader:
                if len(row) >= 6:
                    leads.append({
                        "timestamp": row[0], "postcode": row[1],
                        "name": row[2], "phone": row[3],
                        "email": row[4], "reason": row[5],
                    })
    except Exception:
        logger.exception("Error loading leads")
    return list(reversed(leads))


def save_lead(postcode: str, name: str, phone: str, email: str, reason: str) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with _file_lock:
        file_exists = os.path.exists(LEADS_CSV)
        with open(LEADS_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["Timestamp", "Postcode", "Name", "Phone", "Email", "Reason"])
            writer.writerow([
                datetime.now().isoformat(timespec="seconds"),
                postcode, name, phone, email, reason,
            ])


# ------------------------------------------------------------------ users
def load_users() -> list:
    if not os.path.exists(USERS_PATH):
        return []
    try:
        with open(USERS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_users(users: list) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with _file_lock:
        with open(USERS_PATH, "w", encoding="utf-8") as f:
            json.dump(users, f, indent=2)


def find_user_by_email(email: str):
    email_l = (email or "").strip().lower()
    if not email_l:
        return None
    for u in load_users():
        if (u.get("email") or "").strip().lower() == email_l:
            return u
    return None


def create_user(name: str, email: str, password: str) -> dict:
    users = load_users()
    user = {
        "id": str(uuid.uuid4()),
        "name": name.strip(),
        "email": email.strip().lower(),
        "password_hash": generate_password_hash(password),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    users.append(user)
    save_users(users)
    return user


def update_user_password(email: str, new_password: str) -> bool:
    users = load_users()
    email_l = email.strip().lower()
    for u in users:
        if (u.get("email") or "").strip().lower() == email_l:
            u["password_hash"] = generate_password_hash(new_password)
            save_users(users)
            return True
    return False


# ------------------------------------------------------------------ admin auth
def load_admin_password_hash(env_hash: str) -> str:
    """Prefer data/admin_auth.json, else env-derived hash."""
    if os.path.exists(ADMIN_AUTH_PATH):
        try:
            with open(ADMIN_AUTH_PATH, encoding="utf-8") as f:
                data = json.load(f)
            h = (data.get("password_hash") or "").strip()
            if h:
                return h
        except (OSError, json.JSONDecodeError):
            pass
    return env_hash


def save_admin_password(new_password: str) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with _file_lock:
        with open(ADMIN_AUTH_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "password_hash": generate_password_hash(new_password),
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                },
                f, indent=2,
            )
