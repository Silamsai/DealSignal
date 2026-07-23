#!/usr/bin/env python3
"""
DealSignal Flyer Sender — Level 2 prototype
Web dashboard: pick scored properties, add your brand, send targeted postal
flyers to "The Homeowner" at each address.

Pipeline per flyer:
    1. Reveal full address via Homedata  (£0.20 first reveal)
    2. Print & post A5 postcard via Stannp (~£0.85 inc. 2nd class postage)
    Suggested resale price to your users: £2.00–2.50 per flyer.

Setup:
    pip install flask requests
    set HOMEDATA_API_KEY=...      # homedata.co.uk/register
    set STANNP_API_KEY=...        # stannp.com — free account, pay per item
    python flyer_app.py           # then open http://localhost:5000

SAFETY: STANNP_TEST_MODE defaults to ON — Stannp generates a proof PDF but
posts nothing and charges nothing. Set STANNP_TEST_MODE=0 only when ready.

Auth flow: public home (/) with Sign up + Login. Sign up stores demo accounts
in data/users.json (no OTP). Login accepts signup email+password or admin
credentials, then opens /app. Account at /app/account. Set ADMIN_USERNAME +
ADMIN_PASSWORD (or ADMIN_PASSWORD_HASH), FLASK_SECRET, optional SESSION_HOURS /
LOGIN_MAX_ATTEMPTS / LOGIN_LOCKOUT_SECONDS. Optional local admin password
override: data/admin_auth.json (dev-only).

VERIFY ON FIRST RUN (docs were unreachable when this was written):
    1. Homedata address reveal — coded as GET /listing-address/{listing_id}/ ;
       confirm exact path & response field at homedata.co.uk/docs/endpoints
    2. Stannp create-postcard params — confirm at developers.stannp.com
"""

import csv
import json
import os
import subprocess
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps

import requests
from dotenv import load_dotenv
from flask import Flask, render_template_string, request, redirect, url_for, flash, session
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "dev-only-change-me")

# Admin sessions expire (default 8 hours). Override with SESSION_HOURS.
SESSION_HOURS = max(1, int(os.environ.get("SESSION_HOURS", "8")))
app.permanent_session_lifetime = timedelta(hours=SESSION_HOURS)

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin"
ADMIN_AUTH_PATH = "data/admin_auth.json"
USERS_PATH = "data/users.json"
# Env hash used when no local override file exists.
_env_password_hash = (os.environ.get("ADMIN_PASSWORD_HASH") or "").strip()
if not _env_password_hash:
    _env_password_hash = generate_password_hash(
        os.environ.get("ADMIN_PASSWORD", "password123")
    )


def get_admin_password_hash() -> str:
    """Prefer data/admin_auth.json (dev password change), else env-derived hash."""
    if os.path.exists(ADMIN_AUTH_PATH):
        try:
            with open(ADMIN_AUTH_PATH, encoding="utf-8") as f:
                data = json.load(f)
            h = (data.get("password_hash") or "").strip()
            if h:
                return h
        except (OSError, json.JSONDecodeError):
            pass
    return _env_password_hash


def save_admin_password(new_password: str) -> None:
    os.makedirs("data", exist_ok=True)
    with open(ADMIN_AUTH_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"password_hash": generate_password_hash(new_password), "updated_at": datetime.now().isoformat(timespec="seconds")},
            f,
            indent=2,
        )


def load_users() -> list:
    """Demo client accounts stored in data/users.json (no OTP)."""
    if not os.path.exists(USERS_PATH):
        return []
    try:
        with open(USERS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_users(users: list) -> None:
    os.makedirs("data", exist_ok=True)
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


def authenticate(login_id: str, password: str):
    """
    Returns (ok, session_payload) where session_payload has
    username, display_name, role ('admin'|'user'), email.
    """
    login_id = (login_id or "").strip()
    if not login_id or not password:
        return False, None

    if login_id == ADMIN_USERNAME and check_password_hash(get_admin_password_hash(), password):
        return True, {
            "username": ADMIN_USERNAME,
            "display_name": ADMIN_USERNAME,
            "role": "admin",
            "email": "",
        }

    user = find_user_by_email(login_id)
    if user and check_password_hash(user.get("password_hash") or "", password):
        return True, {
            "username": user["email"],
            "display_name": user.get("name") or user["email"],
            "role": "user",
            "email": user["email"],
        }
    return False, None


def _start_session(payload: dict) -> None:
    session.clear()
    session["logged_in"] = True
    session["username"] = payload["username"]
    session["display_name"] = payload["display_name"]
    session["role"] = payload["role"]
    session["email"] = payload.get("email") or ""
    session.permanent = True

# Brute-force protection for /login (per IP, in-memory — resets on process restart).
LOGIN_MAX_ATTEMPTS = max(3, int(os.environ.get("LOGIN_MAX_ATTEMPTS", "5")))
LOGIN_LOCKOUT_SECONDS = max(60, int(os.environ.get("LOGIN_LOCKOUT_SECONDS", "900")))
_login_attempts: dict[str, dict] = defaultdict(lambda: {"failures": 0, "locked_until": None})


def _client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _login_lock_remaining(ip: str) -> int:
    rec = _login_attempts[ip]
    until = rec.get("locked_until")
    if not until:
        return 0
    remaining = int((until - datetime.utcnow()).total_seconds())
    if remaining <= 0:
        _login_attempts[ip] = {"failures": 0, "locked_until": None}
        return 0
    return remaining


def _record_login_failure(ip: str) -> None:
    rec = _login_attempts[ip]
    rec["failures"] = int(rec.get("failures") or 0) + 1
    if rec["failures"] >= LOGIN_MAX_ATTEMPTS:
        rec["locked_until"] = datetime.utcnow() + timedelta(seconds=LOGIN_LOCKOUT_SECONDS)
        rec["failures"] = 0


def _clear_login_failures(ip: str) -> None:
    _login_attempts.pop(ip, None)


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function

LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en-GB">
<head>
  <meta charset="UTF-8">
  <title>Login | DealSignal</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      background: #f6f8fa;
      color: #1c2128;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }
    .top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      max-width: 960px;
      width: 100%;
      margin: 0 auto;
      padding: 24px 20px 0;
    }
    .logo {
      font-weight: 800;
      font-size: 1.15rem;
      color: #14273d;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 10px;
      letter-spacing: -0.03em;
    }
    .brand-mark {
      width: 34px;
      height: 34px;
      border-radius: 8px;
      background: #14273d;
      color: #fff;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      font-weight: 800;
      font-size: 1.15rem;
      letter-spacing: -0.05em;
      line-height: 1;
    }
    .logo strong { font-size: 1.15rem; letter-spacing: -0.04em; }
    .top a.back {
      color: #5a6472;
      font-size: 0.9rem;
      font-weight: 600;
      text-decoration: none;
    }
    .top a.back:hover { color: #14273d; }
    .main {
      flex: 1;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 40px 20px 64px;
    }
    .login-card {
      width: 100%;
      max-width: 420px;
      background: #fff;
      border: 1px solid #e2e8ee;
      border-radius: 12px;
      padding: 36px 32px;
    }
    .login-card h1 {
      font-size: 1.45rem;
      color: #14273d;
      margin-bottom: 6px;
    }
    .subtitle {
      font-size: 0.95rem;
      color: #5a6472;
      margin-bottom: 28px;
      line-height: 1.5;
    }
    .form-group { margin-bottom: 16px; text-align: left; }
    .form-label {
      display: block;
      font-size: 0.8rem;
      font-weight: 700;
      color: #14273d;
      margin-bottom: 6px;
    }
    .form-input {
      width: 100%;
      background: #f6f8fa;
      border: 1px solid #cdd6dd;
      border-radius: 8px;
      padding: 12px 14px;
      color: #1c2128;
      font-family: inherit;
      font-size: 0.95rem;
      outline: none;
    }
    .form-input:focus {
      background: #fff;
      border-color: #14273d;
    }
    .password-wrapper { position: relative; }
    .password-wrapper .form-input { padding-right: 48px; }
    .toggle-password {
      position: absolute;
      right: 12px;
      top: 50%;
      transform: translateY(-50%);
      background: none;
      border: none;
      cursor: pointer;
      padding: 4px;
      color: #5a6472;
      display: flex;
      align-items: center;
    }
    .toggle-password:hover { color: #14273d; }
    .btn {
      width: 100%;
      background: #14273d;
      color: #fff;
      border: none;
      border-radius: 8px;
      padding: 13px;
      font-family: inherit;
      font-weight: 700;
      font-size: 0.98rem;
      cursor: pointer;
      margin-top: 8px;
    }
    .btn:hover { background: #1c3228; }
    .btn:disabled { opacity: 0.55; cursor: not-allowed; }
    .alert {
      background: #fdeaea;
      border: 1px solid #eec3c3;
      color: #991b1b;
      padding: 12px;
      border-radius: 8px;
      font-size: 0.88rem;
      margin-bottom: 18px;
      text-align: left;
    }
    .footer-link {
      margin-top: 22px;
      text-align: center;
      font-size: 0.88rem;
    }
    .footer-link a {
      color: #1e5c3a;
      font-weight: 600;
      text-decoration: none;
    }
  </style>
</head>
<body>
  <div class="top">
    <a class="logo" href="/">
      <span class="brand-mark" aria-hidden="true">D</span>
      <strong>DealSignal</strong>
    </a>
    <a class="back" href="/">← Back to home</a>
  </div>
  <div class="main">
    <div class="login-card">
      <h1>Login</h1>
      <p class="subtitle">Enter your email and password from Sign up.</p>

      {% with messages = get_flashed_messages(category_filter=["login_error"]) %}
        {% if messages %}
          {% for msg in messages %}
            <div class="alert">{{ msg }}</div>
          {% endfor %}
        {% endif %}
      {% endwith %}

      <form method="POST" action="{{ url_for('login') }}">
        <input type="hidden" name="next" value="{{ next_url }}">
        <div class="form-group">
          <label class="form-label" for="username">Email or username</label>
          <input class="form-input" type="text" id="username" name="username" placeholder="admin or you@example.com" required autofocus {% if login_locked %}disabled{% endif %}>
        </div>
        <div class="form-group">
          <label class="form-label" for="password">Password</label>
          <div class="password-wrapper">
            <input class="form-input" type="password" id="password" name="password" placeholder="••••••••" required {% if login_locked %}disabled{% endif %}>
            <button type="button" class="toggle-password" id="togglePassword" aria-label="Show password">
              <svg id="eyeOpen" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                <circle cx="12" cy="12" r="3"/>
              </svg>
              <svg id="eyeClosed" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:none;">
                <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/>
                <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/>
                <line x1="1" y1="1" x2="23" y2="23"/>
                <path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"/>
              </svg>
            </button>
          </div>
        </div>
        <button class="btn" type="submit" {% if login_locked %}disabled{% endif %}>{% if login_locked %}Temporarily locked{% else %}Sign In{% endif %}</button>
      </form>

      <div class="footer-link">
        No account yet? <a href="{{ url_for('signup') }}">Sign up</a>
        · <a href="/">Home</a>
      </div>
    </div>
  </div>

  <script>
    document.getElementById('togglePassword').addEventListener('click', function() {
      const pwd = document.getElementById('password');
      const eyeOpen = document.getElementById('eyeOpen');
      const eyeClosed = document.getElementById('eyeClosed');
      if (pwd.type === 'password') {
        pwd.type = 'text';
        eyeOpen.style.display = 'none';
        eyeClosed.style.display = 'block';
        this.setAttribute('aria-label', 'Hide password');
      } else {
        pwd.type = 'password';
        eyeOpen.style.display = 'block';
        eyeClosed.style.display = 'none';
        this.setAttribute('aria-label', 'Show password');
      }
    });
  </script>
</body>
</html>
"""

HOMEDATA_KEY = os.environ.get("HOMEDATA_API_KEY", "")
STANNP_KEY = os.environ.get("STANNP_API_KEY", "")
TEST_MODE = os.environ.get("STANNP_TEST_MODE", "1") != "0"

CSV_PATH = os.environ.get("DEALS_CSV", "data/motivated_sellers.csv")
BRAND_PATH = "data/brand.json"
SENT_LOG = "data/sent_log.csv"
SCAN_PATH = "data/scan.json"


def stannp_key_ready() -> bool:
    """True when STANNP_API_KEY looks like a real key (not the .env placeholder)."""
    key = (STANNP_KEY or "").strip()
    if not key:
        return False
    lowered = key.lower()
    if "your_stannp" in lowered or "your_api" in lowered or key.endswith("_api_key"):
        return False
    return True


DEMO_DEALS = [
    {
        "rank": "1", "score": "15", "address": "Semi-detached, BD9 (Heaton)",
        "price": "165000", "bedrooms": "3", "type": "Semi-detached",
        "days_on_market": "214", "listing_id": "demo-1",
        "signals": "Chain collapse: Sold STC then back on market; 3 price cuts, down 12%",
    },
    {
        "rank": "2", "score": "11", "address": "Terraced, BD4 (Tong)",
        "price": "89950", "bedrooms": "2", "type": "Terraced",
        "days_on_market": "156", "listing_id": "demo-2",
        "signals": "Withdrawn with previous agent, re-listed; 2 price cuts, down 10%",
    },
    {
        "rank": "3", "score": "9", "address": "Detached, BD16 (Bingley)",
        "price": "280000", "bedrooms": "4", "type": "Detached",
        "days_on_market": "121", "listing_id": "demo-3",
        "signals": "2 price cuts; had an offer, back on market",
    },
    {
        "rank": "4", "score": "8", "address": "Flat, BD1 (City Centre)",
        "price": "95000", "bedrooms": "2", "type": "Flat",
        "days_on_market": "190", "listing_id": "demo-4",
        "signals": "98+ days on market; repeated reductions",
    },
    {
        "rank": "5", "score": "7", "address": "Terraced, BD7 (Great Horton)",
        "price": "120000", "bedrooms": "3", "type": "Terraced",
        "days_on_market": "140", "listing_id": "demo-5",
        "signals": "Price reduced twice; long DOM",
    },
]

DEFAULT_BRAND = {
    "name": "DealSignal", "tagline": "We buy houses in any condition",
    "phone": "01274 000 000", "email": "hello@example.co.uk",
    "website": "www.example.co.uk", "colour": "#14273d",
    "message": ("We noticed your property has been on the market for a while. "
                "We are a local buyer able to move quickly, with no chains and no fees. "
                "If you would like a no-obligation cash offer, we would love to hear from you."),
}


# ------------------------------------------------------------------ storage
def load_scan():
    if os.path.exists(SCAN_PATH):
        with open(SCAN_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"area": "Bradford", "max_price": "250000", "last_run": None}


def save_scan(data):
    with open(SCAN_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_brand():
    if os.path.exists(BRAND_PATH):
        with open(BRAND_PATH, encoding="utf-8") as f:
            return {**DEFAULT_BRAND, **json.load(f)}
    return dict(DEFAULT_BRAND)


def save_brand(data):
    with open(BRAND_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_deals():
    if not os.path.exists(CSV_PATH):
        return []
    with open(CSV_PATH, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def sample_deals(limit: int = 5):
    """Return (deals, is_demo) for the public home."""
    deals = load_deals()[:limit]
    if deals:
        return deals, False
    return DEMO_DEALS[:limit], True


def log_sent(row):
    exists = os.path.exists(SENT_LOG)
    with open(SENT_LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["timestamp", "listing_id", "address", "stannp_id", "test_mode", "status"])
        w.writerow(row)


def already_sent():
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
                continue  # test-mode proofs stay selectable
            lid = (r.get("listing_id") or "").strip()
            if lid:
                sent.add(lid)
    return sent


LEADS_CSV = "data/seller_leads.csv"


def load_leads():
    if not os.path.exists(LEADS_CSV):
        return []
    leads = []
    try:
        with open(LEADS_CSV, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)  # skip header
            for row in reader:
                if len(row) >= 6:
                    leads.append({
                        "timestamp": row[0],
                        "postcode": row[1],
                        "name": row[2],
                        "phone": row[3],
                        "email": row[4],
                        "reason": row[5]
                    })
    except Exception as e:
        print(f"Error loading leads: {e}")
    return list(reversed(leads))


def save_lead(postcode, name, phone, email, reason):
    os.makedirs("data", exist_ok=True)
    file_exists = os.path.exists(LEADS_CSV)
    with open(LEADS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["Timestamp", "Postcode", "Name", "Phone", "Email", "Reason"])
        writer.writerow([
            datetime.now().isoformat(timespec="seconds"),
            postcode,
            name,
            phone,
            email,
            reason
        ])


# ------------------------------------------------------------------ APIs
def reveal_address(listing_id):
    """Homedata full-address reveal (£0.20 first reveal, repeats free)."""
    headers = {
        "Authorization": f"Api-Key {HOMEDATA_KEY}",
        "Idempotency-Key": str(uuid.uuid4()),
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    r = requests.post(
        "https://api.homedata.co.uk/listing-address/",
        json={"listing_id": listing_id},
        headers=headers,
        timeout=30,
    )
    r.raise_for_status()
    d = r.json()
    addr = d.get("address") or {}
    return {
        "address1": addr.get("address_line_1") or addr.get("full_address", ""),
        "address2": addr.get("address_line_2") or "",
        "city": addr.get("post_town") or "Bradford",
        "postcode": addr.get("postcode", ""),
    }


def flyer_front_html(brand, deal):
    """A5 postcard front. Stannp accepts raw HTML and renders it to print."""
    return f"""
    <html>
    <body style="width:148mm;height:105mm;margin:0;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;background:#ffffff;box-sizing:border-box;">
      <div style="display:flex;width:148mm;height:105mm;overflow:hidden;box-sizing:border-box;">
        <!-- LEFT PANEL: Brand Accent -->
        <div style="width:53mm;background:{brand['colour']};padding:10mm 6mm;display:flex;flex-direction:column;justify-content:space-between;box-sizing:border-box;color:#ffffff;">
          <div>
            <div style="font-size:18pt;font-weight:800;letter-spacing:-0.5px;line-height:1.2;margin-bottom:3mm;">{brand['name']}</div>
            <div style="font-size:9.5pt;opacity:0.9;font-weight:400;line-height:1.4;">{brand['tagline']}</div>
          </div>
          <div style="font-size:8pt;opacity:0.7;letter-spacing:0.5px;text-transform:uppercase;font-weight:600;">
            Direct Buyer Notice
          </div>
        </div>
        
        <!-- RIGHT PANEL: Headline & Contact -->
        <div style="width:95mm;padding:10mm 8mm;display:flex;flex-direction:column;justify-content:space-between;box-sizing:border-box;">
          <div>
            <div style="font-size:9.5pt;color:{brand['colour']};font-weight:700;letter-spacing:0.8px;text-transform:uppercase;margin-bottom:2mm;">
              Hassle-Free Home Sale
            </div>
            <div style="font-size:17pt;font-weight:700;color:#1c2321;line-height:1.3;letter-spacing:-0.3px;">
              Thinking of a fresh start with your property sale?
            </div>
            <div style="font-size:10.5pt;color:#556b60;margin-top:3mm;line-height:1.5;">
              We buy properties directly for cash. Get a guaranteed, fee-free offer today with no chain delays.
            </div>
          </div>
          
          <!-- CALL TO ACTION -->
          <div style="background:#f4f7f5;border-left:4px solid {brand['colour']};padding:4mm 5mm;border-radius:0 2mm 2mm 0;box-sizing:border-box;">
            <div style="font-size:12.5pt;font-weight:800;color:#1c2321;margin-bottom:1mm;">
              Call: {brand['phone']}
            </div>
            <div style="font-size:9.5pt;color:#44554b;font-weight:500;">
              {brand['email']} &nbsp;|&nbsp; {brand['website']}
            </div>
          </div>
        </div>
      </div>
    </body>
    </html>"""


def flyer_back_message(brand):
    """Back of card: Stannp prints the recipient address block automatically;
    this is the message area."""
    return (f"Dear Homeowner,\n\n{brand['message']}\n\n"
            f"Kind regards,\n{brand['name']}\n{brand['phone']} · {brand['email']}\n\n"
            f"If you'd prefer not to hear from us again, call or email us and "
            f"we'll remove your address immediately.")


def send_postcard(brand, deal, addr):
    """Stannp create-postcard. VERIFY params against developers.stannp.com."""
    payload = {
        "test": "true" if TEST_MODE else "false",
        "size": "A5",
        "front": flyer_front_html(brand, deal),
        "message": flyer_back_message(brand),
        "recipient[title]": "The",
        "recipient[firstname]": "Homeowner",
        "recipient[lastname]": "",
        "recipient[address1]": addr["address1"],
        "recipient[address2]": addr["address2"],
        "recipient[city]": addr["city"],
        "recipient[postcode]": addr["postcode"],
        "recipient[country]": "GB",
    }
    r = requests.post(
        "https://api-eu1.stannp.com/v1/postcards/create",
        data=payload, auth=(STANNP_KEY, ""), timeout=60,
    )
    r.raise_for_status()
    return r.json()


# ------------------------------------------------------------------ UI
PAGE = """
<!DOCTYPE html><html lang="en-GB"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DealSignal — Flyer Sender</title>
<style>
 body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#f6f8fa;color:#1c2128;margin:0}
 .wrap{max-width:1100px;margin:0 auto;padding:20px 20px 80px}
 h1{color:#14273d} h2{color:#14273d;font-size:1.15rem;margin:24px 0 10px}
 .banner{background:{{ '#fbf6ea' if test_mode else '#fdeaea' }};border:1px solid #e5d9b8;
         border-radius:8px;padding:10px 16px;font-size:.9rem;margin-bottom:16px}
 .flash{background:#e8f5ec;border:1px solid #bfe3c9;border-radius:8px;padding:10px 16px;margin-bottom:12px;font-size:.9rem}
 .flash.error{background:#fdeaea;border-color:#eec3c3}
 form.brand{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;background:#fff;
            border:1px solid #e2e8ee;border-radius:12px;padding:18px}
 form.brand label{font-size:.8rem;font-weight:600;display:block;margin-bottom:3px}
 form.brand input,form.brand textarea{width:100%;padding:8px;border:1px solid #cdd6dd;border-radius:6px;font:inherit;box-sizing:border-box}
 form.brand .full{grid-column:1/-1}
 table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e2e8ee;border-radius:12px;overflow:hidden}
 th{background:#14273d;color:#fff;text-align:left;padding:10px 12px;font-size:.85rem}
 td{padding:10px 12px;border-top:1px solid #eef2f6;font-size:.88rem;vertical-align:top}
 tr.sent td{opacity:.45}
 tr:target{background:#fff8db!important;outline:2px solid #f5a623;outline-offset:-2px}
 .sig{color:#1e5c3a;font-size:.8rem}
 .btn{background:#14273d;color:#fff;border:0;border-radius:8px;padding:11px 22px;font-weight:700;cursor:pointer;font-size:.95rem}
 .btn.amber{background:#f5a623;color:#1c2128}
 .btn.logout{background:#dc2626;color:#fff;padding:9px 16px;font-size:.85rem;text-decoration:none;display:inline-block}
 .btn.ghost{background:transparent;color:#14273d;border:1px solid #cdd6dd;padding:9px 16px;font-size:.85rem;text-decoration:none;display:inline-block}
 .cost{font-size:.85rem;color:#5a6472;margin:8px 0 14px}
 .preview{border:1px solid #e2e8ee;border-radius:12px;background:#fff;padding:18px;margin-top:10px}
 .app-bar{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;
          background:#fff;border:1px solid #e2e8ee;border-radius:12px;padding:14px 18px;margin-bottom:20px}
 .app-bar__brand{font-weight:800;color:#14273d;font-size:1.05rem;text-decoration:none;display:inline-flex;align-items:center;gap:10px;letter-spacing:-0.03em}
 .brand-mark{width:28px;height:28px;border-radius:4px;background:#14273d;color:#fff;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0;font-weight:800;font-size:0.95rem;letter-spacing:-0.02em}
 .app-bar__brand-text{display:flex;flex-direction:column;line-height:1.05}
 .app-bar__brand-text strong{font-size:1.02rem;letter-spacing:-0.04em}
 .app-bar__brand-text span{font-size:.58rem;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#5a6472}
 .app-bar__nav{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
 .app-bar__nav a.nav{color:#5a6472;text-decoration:none;font-weight:600;font-size:.88rem;padding:8px 10px;border-radius:6px}
 .app-bar__nav a.nav:hover,.app-bar__nav a.nav.active{color:#14273d;background:#f4f6f8}
 .app-bar__user{font-size:.82rem;color:#5a6472;margin-right:6px}
</style></head><body><div class="wrap">
<div class="app-bar">
  <a class="app-bar__brand" href="{{ url_for('app_dashboard') }}">
    <span class="brand-mark" aria-hidden="true">D</span>
    <span class="app-bar__brand-text"><strong>DealSignal</strong><span>Workspace</span></span>
  </a>
  <div class="app-bar__nav">
    <a class="nav {% if nav == 'deals' %}active{% endif %}" href="{{ url_for('app_dashboard') }}">Workspace</a>
    <a class="nav {% if nav == 'account' %}active{% endif %}" href="{{ url_for('account') }}">Account</a>
    <span class="app-bar__user">{{ display_name }}</span>
    <a class="btn logout" href="{{ url_for('logout') }}">Logout</a>
  </div>
</div>
<h1>Flyer workspace</h1>
<p style="color:#5a6472;margin:-8px 0 18px;font-size:.95rem;line-height:1.5;">Three steps: scan an area → set your brand → pick properties and send.</p>
<div class="banner">{% if test_mode %}<b>Safe test mode</b> — flyers are only proofed as PDFs. Nothing is printed, posted, or charged. When you are ready to post for real, set <code>STANNP_TEST_MODE=0</code> in your <code>.env</code> file.{% else %}<b>Live posting mode</b> — selected flyers will be printed, posted, and charged to your Stannp account.{% endif %}</div>
{% with msgs = get_flashed_messages(with_categories=true) %}{% for cat,m in msgs %}<div class="flash {{cat}}">{{m}}</div>{% endfor %}{% endwith %}

<h2>1. Scan an area</h2>
<form class="brand" method="post" action="{{ url_for('scan') }}" style="grid-template-columns:2fr 1fr 1fr">
  <div><label>Town / city / local authority</label><input name="area" value="{{scan_cfg.area}}" placeholder="e.g. Leeds"></div>
  <div><label>Max price (£)</label><input name="max_price" type="number" value="{{scan_cfg.max_price}}"></div>
  <div style="align-self:end"><button class="btn amber" onclick="this.disabled=true;this.textContent='Scanning…';this.form.submit()">Run scan</button></div>
  <div class="full" style="font-size:.82rem;color:#5a6472">
    {% if scan_cfg.last_run %}Last scan: {{scan_cfg.area}} · {{scan_cfg.last_run}}{% else %}No scan yet — run one to fill the property list below.{% endif %}
    Each scan uses ~30 Homedata API calls and takes about a minute.
  </div>
</form>

<h2 id="brand">2. Set your brand</h2>
<form class="brand" method="post" action="{{ url_for('brand') }}">
  <div><label>Company name</label><input name="name" value="{{brand.name}}"></div>
  <div><label>Tagline</label><input name="tagline" value="{{brand.tagline}}"></div>
  <div><label>Brand colour</label><input name="colour" type="color" value="{{brand.colour}}"></div>
  <div><label>Phone</label><input name="phone" value="{{brand.phone}}"></div>
  <div><label>Email</label><input name="email" value="{{brand.email}}"></div>
  <div><label>Website</label><input name="website" value="{{brand.website}}"></div>
  <div class="full"><label>Message to the homeowner</label><textarea name="message" rows="3">{{brand.message}}</textarea></div>
  <div class="full"><button class="btn">Save brand</button></div>
</form>

<h2>3. Pick properties &amp; send flyers</h2>
<div class="cost">About <b>£1.05</b> per flyer (~£0.20 address reveal + ~£0.85 print &amp; post). Rows grey out only after a successful live send (not test mode / not failed attempts).</div>
<form method="post" action="{{ url_for('send') }}">
<table>
<tr><th></th><th>#</th><th>Score</th><th>Property</th><th>Price</th><th>DOM</th><th>Motivation signals</th></tr>
{% for d in deals %}
<tr id="deal-{{d.listing_id}}" class="{{ 'sent' if d.listing_id in sent else '' }}">
  <td>{% if d.listing_id not in sent %}<input type="checkbox" name="ids" value="{{d.listing_id}}">{% else %}✓{% endif %}</td>
  <td>{{d.rank}}</td><td><b>{{d.score}}</b></td>
  <td>{{d.address}}<br><span style="color:#5a6472">{{d.bedrooms}} bed {{d.type}}</span></td>
  <td>£{{d.price}}</td><td>{{d.days_on_market}}</td>
  <td class="sig">{{d.signals}}</td>
</tr>
{% endfor %}
</table>
<p>
  <button class="btn amber" formaction="{{ url_for('preview') }}">Preview flyer</button>
  <button class="btn" onclick="return confirm('Send flyers to all selected properties?')">Send flyers to selected</button>
</p>
</form>

{% if preview_html %}
<div id="flyer-preview" style="scroll-margin-top:24px;">
<h2>Flyer preview</h2>
<p style="color:#5a6472;font-size:.9rem;margin:-4px 0 12px;">This is how your A5 postcard looks. Preview does not send or charge anything.</p>
<div style="display:flex;flex-wrap:wrap;gap:20px;margin-top:10px;">
  <div style="flex: 0 0 148mm;">
    <h3 style="font-size:0.92rem;margin:0 0 8px 0;color:#5a6472;font-weight:600;">Front (A5)</h3>
    <iframe srcdoc="{{ preview_html|escape }}" style="width:148mm;height:105mm;border:1px solid #e2e8ee;box-shadow:0 4px 18px rgba(0,0,0,0.06);border-radius:8px;background:#fff;display:block;"></iframe>
  </div>
  <div style="flex: 1 1 300px;min-width:300px;display:flex;flex-direction:column;justify-content:space-between;background:#fff;border:1px solid #e2e8ee;border-radius:8px;padding:20px;box-sizing:border-box;height:105mm;">
    <div>
      <h3 style="font-size:0.92rem;margin:0 0 12px 0;color:#5a6472;font-weight:600;">Back message</h3>
      <pre style="white-space:pre-wrap;font-family:inherit;margin:0;font-size:0.9rem;line-height:1.55;color:#1c2128;">{{ preview_back }}</pre>
    </div>
    <div style="font-size:0.75rem;color:#8a94a6;border-top:1px solid #f0f4f8;padding-top:10px;margin-top:10px;">
      * The homeowner address prints on the back automatically when you send.
    </div>
  </div>
</div>
</div>
<script>
  (function () {
    var el = document.getElementById('flyer-preview');
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  })();
</script>
{% endif %}

<h2>4. Seller leads</h2>
<p style="color:#5a6472;font-size:.9rem;margin:-4px 0 12px;">Inquiries from the homeowner page at <a href="/sell" target="_blank" style="color:#1e5c3a;font-weight:600;">/sell</a>.</p>
<div style="background:#fff;border:1px solid #e2e8ee;border-radius:12px;overflow:hidden;margin-bottom:40px;">
  <table style="border:none;">
    <tr style="background:#14273d;color:#fff;">
      <th style="padding:10px 12px;font-size:.85rem;">Timestamp</th>
      <th style="padding:10px 12px;font-size:.85rem;">Name</th>
      <th style="padding:10px 12px;font-size:.85rem;">Phone</th>
      <th style="padding:10px 12px;font-size:.85rem;">Email</th>
      <th style="padding:10px 12px;font-size:.85rem;">Postcode</th>
      <th style="padding:10px 12px;font-size:.85rem;">Reason</th>
    </tr>
    {% if leads %}
      {% for l in leads %}
      <tr>
        <td style="padding:10px 12px;border-top:1px solid #eef2f6;font-size:.88rem;">{{ l.timestamp }}</td>
        <td style="padding:10px 12px;border-top:1px solid #eef2f6;font-size:.88rem;"><b>{{ l.name }}</b></td>
        <td style="padding:10px 12px;border-top:1px solid #eef2f6;font-size:.88rem;"><a href="tel:{{ l.phone }}" style="color:#1e5c3a;font-weight:700;">{{ l.phone }}</a></td>
        <td style="padding:10px 12px;border-top:1px solid #eef2f6;font-size:.88rem;">{{ l.email }}</td>
        <td style="padding:10px 12px;border-top:1px solid #eef2f6;font-size:.88rem;"><span style="background:#f4f6f8;padding:2px 6px;border-radius:4px;font-family:monospace;font-size:0.85rem;">{{ l.postcode }}</span></td>
        <td style="padding:10px 12px;border-top:1px solid #eef2f6;font-size:.88rem;">{{ l.reason }}</td>
      </tr>
      {% endfor %}
    {% else %}
      <tr><td colspan="6" style="text-align:center;color:#5a6472;padding:20px;font-size:.88rem;">No leads yet. Share <a href="/sell" target="_blank" style="color:#1e5c3a;font-weight:600;">/sell</a> with homeowners.</td></tr>
    {% endif %}
  </table>
</div>

</div></body></html>
"""

HOME_PAGE = """<!DOCTYPE html>
<html lang="en-GB"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DealSignal — Motivated seller intelligence</title>
<meta name="description" content="DealSignal finds motivated UK sellers for investors, and gives homeowners a simple cash-offer path. Weekly digests, ranked signals, flyer tools.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@500;600;700;800&family=Source+Serif+4:opsz,wght@8..60,500;8..60,600&display=swap" rel="stylesheet">
<style>
:root{
  --ink:#14273d; --ink-soft:#24384f; --muted:#5a6472; --line:#d9e1ea;
  --paper:#f3f6f9; --white:#fff; --green:#1e5c3a; --green-soft:#e8f2ec;
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{font-family:Manrope,Segoe UI,sans-serif;background:var(--paper);color:var(--ink);line-height:1.55;overflow-x:hidden}
a{color:inherit}
.site-bg{
  position:fixed;inset:0;z-index:-1;pointer-events:none;
  background:
    radial-gradient(ellipse 80% 50% at 10% -10%, rgba(30,92,58,.12), transparent 55%),
    radial-gradient(ellipse 70% 45% at 95% 5%, rgba(20,39,61,.10), transparent 50%),
    linear-gradient(180deg, #eef3f7 0%, #f3f6f9 40%, #e8eef4 100%);
}
.site-bg::after{
  content:"";position:absolute;inset:0;opacity:.35;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='60' height='60'%3E%3Cpath d='M60 0H0V60' fill='none' stroke='%2314273d' stroke-opacity='0.04'/%3E%3C/svg%3E");
}
.wrap{max-width:1080px;margin:0 auto;padding:0 24px}
.header{
  position:sticky;top:0;z-index:40;backdrop-filter:blur(12px);
  background:rgba(243,246,249,.86);border-bottom:1px solid transparent;
  transition:border-color .2s, box-shadow .2s;
}
.header.is-scrolled{border-color:var(--line);box-shadow:0 8px 24px rgba(20,39,61,.04)}
.header__inner{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:16px 0;flex-wrap:wrap}
.logo{font-weight:800;color:var(--ink);text-decoration:none;display:inline-flex;align-items:center;gap:12px;letter-spacing:-0.03em}
.brand-mark{
  width:36px;height:36px;border-radius:9px;background:var(--ink);color:#fff;
  display:inline-flex;align-items:center;justify-content:center;flex-shrink:0;
  font-family:Manrope,sans-serif;font-weight:800;font-size:1.2rem;letter-spacing:-0.05em;line-height:1;
  box-shadow:inset 0 0 0 1px rgba(255,255,255,.08);
}
.logo > strong{font-size:1.22rem;letter-spacing:-0.04em;font-weight:800}
.nav-links{display:flex;gap:18px;align-items:center;margin-left:auto;margin-right:18px}
.nav-links a{font-size:.88rem;font-weight:600;color:var(--muted);text-decoration:none}
.nav-links a:hover{color:var(--ink)}
.actions{display:flex;gap:10px;align-items:center}
.btn{display:inline-block;text-decoration:none;border-radius:8px;padding:11px 18px;font-weight:700;font-size:.9rem;border:0;cursor:pointer;transition:transform .15s ease, background .15s}
.btn:hover{transform:translateY(-1px)}
.btn-line{background:var(--white);color:var(--ink);border:1px solid var(--line)}
.btn-primary{background:var(--ink);color:#fff}
.btn-green{background:var(--green);color:#fff}
.flash{background:var(--green-soft);border:1px solid #bfe3c9;border-radius:8px;padding:10px 16px;margin:12px 0 0;font-size:.9rem}

/* Hero */
.hero{padding:56px 0 72px;min-height:calc(100vh - 72px);display:flex;flex-direction:column;justify-content:center}
.hero__brand{font-family:"Source Serif 4",Georgia,serif;font-size:clamp(2.6rem,6vw,4.2rem);font-weight:600;letter-spacing:-0.03em;line-height:1.05;color:var(--ink);margin:0 0 18px;max-width:14ch;animation:rise .7s ease both}
.hero__line{font-size:clamp(1.05rem,2vw,1.2rem);color:var(--muted);max-width:38ch;margin:0 0 28px;line-height:1.65;animation:rise .7s .08s ease both}
.hero__cta{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:48px;animation:rise .7s .16s ease both}
.hero__visual{
  border-radius:18px;overflow:hidden;min-height:220px;position:relative;
  background:
    linear-gradient(135deg, rgba(20,39,61,.92), rgba(30,92,58,.75)),
    url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='800' height='320' viewBox='0 0 800 320'%3E%3Cpath d='M0 220 L80 160 L160 200 L240 120 L320 180 L400 90 L480 150 L560 70 L640 140 L720 100 L800 160 L800 320 L0 320Z' fill='%23ffffff' fill-opacity='0.08'/%3E%3Cpath d='M0 250 L100 200 L200 240 L300 170 L400 230 L500 150 L600 210 L700 160 L800 200 L800 320 L0 320Z' fill='%23ffffff' fill-opacity='0.05'/%3E%3C/svg%3E") center/cover;
  animation:rise .7s .22s ease both;
}
.hero__visual-inner{padding:28px 28px 24px;color:#fff}
.hero__visual-inner p{font-size:.82rem;letter-spacing:.08em;text-transform:uppercase;opacity:.7;margin-bottom:10px;font-weight:600}
.hero__visual-inner strong{display:block;font-size:clamp(1.3rem,2.5vw,1.7rem);font-weight:800;letter-spacing:-0.02em;max-width:28ch;line-height:1.25}
.hero__chips{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}
.hero__chips span{font-size:.8rem;font-weight:600;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.18);padding:7px 12px;border-radius:6px}

/* Sections */
section{padding:72px 0}
.section-label{font-size:.75rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--green);margin-bottom:10px}
.section-title{font-family:"Source Serif 4",Georgia,serif;font-size:clamp(1.7rem,3.2vw,2.35rem);font-weight:600;letter-spacing:-0.02em;line-height:1.15;margin:0 0 12px;max-width:18ch}
.section-copy{color:var(--muted);font-size:1.02rem;max-width:48ch;margin:0 0 36px;line-height:1.65}
.reveal{opacity:0;transform:translateY(18px);transition:opacity .55s ease, transform .55s ease}
.reveal.is-in{opacity:1;transform:none}

.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
.step{
  background:var(--white);border:1px solid var(--line);border-radius:14px;padding:24px 20px;
  transition:border-color .15s, transform .15s;
}
.step:hover{border-color:#b8c4d0;transform:translateY(-2px)}
.step__num{font-size:.78rem;font-weight:800;color:var(--green);letter-spacing:.08em;margin-bottom:12px}
.step h3{font-size:1.1rem;margin:0 0 8px;letter-spacing:-0.02em}
.step p{font-size:.92rem;color:var(--muted);line-height:1.55;margin:0}

.why{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
.why-card{
  background:var(--white);border:1px solid var(--line);border-radius:14px;padding:24px 20px;
}
.why-card h3{font-size:1.05rem;margin:0 0 8px;letter-spacing:-0.02em}
.why-card p{font-size:.92rem;color:var(--muted);line-height:1.55;margin:0}

.paths{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.path{
  background:var(--white);border:1px solid var(--line);border-radius:14px;padding:28px 24px;
  text-decoration:none;color:inherit;display:block;transition:border-color .15s, transform .15s;
}
.path:hover{border-color:var(--ink);transform:translateY(-2px)}
.path h2{font-size:1.25rem;margin:0 0 8px;letter-spacing:-0.02em}
.path p{font-size:.92rem;color:var(--muted);margin:0 0 18px;line-height:1.55}
.path .go{font-size:.9rem;font-weight:700;color:var(--ink)}

.features{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
.feature{background:var(--white);border:1px solid var(--line);border-radius:14px;padding:24px 20px}
.feature h3{font-size:1.02rem;margin:0 0 8px}
.feature p{font-size:.9rem;color:var(--muted);margin:0;line-height:1.55}

.band{
  margin:20px 0 0;border-radius:18px;padding:48px 40px;
  background:linear-gradient(135deg, var(--ink) 0%, #1c3a2a 100%);color:#fff;
  display:flex;justify-content:space-between;align-items:center;gap:24px;flex-wrap:wrap;
}
.band h2{font-family:"Source Serif 4",Georgia,serif;font-size:clamp(1.5rem,2.5vw,2rem);font-weight:600;letter-spacing:-0.02em;margin:0 0 8px;max-width:16ch}
.band p{margin:0;opacity:.85;max-width:36ch;font-size:.95rem}
.band .actions .btn-line{border-color:rgba(255,255,255,.35);background:transparent;color:#fff}
.band .actions .btn-primary{background:#fff;color:var(--ink)}

.site-footer{padding:40px 0 56px;border-top:1px solid var(--line);margin-top:20px}
.site-footer__inner{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;align-items:center}
.site-footer p{font-size:.82rem;color:var(--muted);margin:0}
.site-footer a{color:var(--ink);font-weight:600;text-decoration:none;font-size:.85rem;margin-left:14px}

@keyframes rise{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}

@media(max-width:800px){
  .nav-links{display:none}
  .steps,.features,.paths,.why{grid-template-columns:1fr}
  .hero{min-height:auto;padding:40px 0 56px}
  .band{padding:32px 24px}
  section{padding:56px 0}
}
</style></head><body>
<div class="site-bg" aria-hidden="true"></div>
<header class="header" id="siteHeader">
  <div class="wrap header__inner">
    <a class="logo" href="/">
      <span class="brand-mark" aria-hidden="true">D</span>
      <strong>DealSignal</strong>
    </a>
    <nav class="nav-links" aria-label="Page">
      <a href="#why">Why it’s used</a>
      <a href="#how">How it works</a>
      <a href="#audience">Who it’s for</a>
    </nav>
    <div class="actions">
      <a class="btn btn-line" href="{{ url_for('login') }}">Login</a>
      <a class="btn btn-primary" href="{{ url_for('signup') }}">Sign up</a>
    </div>
  </div>
</header>

<div class="wrap">
  {% with msgs = get_flashed_messages(with_categories=true) %}
    {% for cat,m in msgs %}<div class="flash">{{ m }}</div>{% endfor %}
  {% endwith %}

  <section class="hero">
    <h1 class="hero__brand">DealSignal</h1>
    <p class="hero__line">Find motivated UK sellers faster — or request a simple cash offer if you’re selling your home.</p>
    <div class="hero__cta">
      <a class="btn btn-primary" href="{{ url_for('signup') }}">Create free account</a>
      <a class="btn btn-line" href="{{ url_for('login') }}">Login</a>
    </div>
    <div class="hero__visual" aria-hidden="true">
      <div class="hero__visual-inner">
        <p>What you get</p>
        <strong>A clear shortlist of sellers who are more likely to negotiate — not a long raw property search.</strong>
        <div class="hero__chips">
          <span>1 · Sign up</span>
          <span>2 · Login</span>
          <span>3 · Use workspace</span>
        </div>
      </div>
    </div>
  </section>

  <section id="why">
    <h2 class="section-title reveal">Why it’s used</h2>
    <p class="section-copy reveal">Most property sites show every listing. DealSignal focuses on sellers who look ready to negotiate.</p>
    <div class="why">
      <div class="why-card reveal">
        <h3>Less noise</h3>
        <p>Get a ranked shortlist instead of scrolling hundreds of adverts.</p>
      </div>
      <div class="why-card reveal">
        <h3>Clear next step</h3>
        <p>Sign up, login, then use the workspace to scan, brand, and preview flyers.</p>
      </div>
      <div class="why-card reveal">
        <h3>Two audiences</h3>
        <p>Investors and homeowners each get a simple path that fits what they need.</p>
      </div>
    </div>
  </section>

  <section id="how">
    <h2 class="section-title reveal">How it works</h2>
    <p class="section-copy reveal">Three easy steps — follow this path and you’re in.</p>
    <div class="steps">
      <div class="step reveal">
        <div class="step__num">01</div>
        <h3>Sign up</h3>
        <p>Enter your name, email, and password. This creates your account for demos and feedback.</p>
      </div>
      <div class="step reveal">
        <div class="step__num">02</div>
        <h3>Login</h3>
        <p>Use that same email and password.</p>
      </div>
      <div class="step reveal">
        <div class="step__num">03</div>
        <h3>Open workspace</h3>
        <p>Scan an area, set your brand, preview a flyer poster, then send when keys are set up.</p>
      </div>
    </div>
  </section>

  <section id="audience">
    <h2 class="section-title reveal">Who it’s for</h2>
    <p class="section-copy reveal">Public pages below need no login. Use Sign up / Login only when you want the workspace tools.</p>
    <div class="paths">
      <a class="path reveal" href="/landing">
        <h2>I’m an investor</h2>
        <p>Learn how weekly digests and motivation scores help you spot deals earlier.</p>
        <span class="go">Open investor page →</span>
      </a>
      <a class="path reveal" href="/sell">
        <h2>I’m selling my home</h2>
        <p>Ask for a cash offer — no agent fees and no chain. We’ll contact you.</p>
        <span class="go">Request cash offer →</span>
      </a>
    </div>
  </section>

  <section id="included">
    <h2 class="section-title reveal">What you get</h2>
    <p class="section-copy reveal">The product in plain words.</p>
    <div class="features">
      <div class="feature reveal">
        <h3>Finds motivated sellers</h3>
        <p>Looks for price cuts, long time on market, and similar signals — then ranks the strongest ones.</p>
      </div>
      <div class="feature reveal">
        <h3>Flyer poster tools</h3>
        <p>After login you can preview the postcard design before anything is posted.</p>
      </div>
      <div class="feature reveal">
        <h3>Homeowner enquiries</h3>
        <p>Sellers can leave details on the sell page. Those leads show in the workspace for follow-up.</p>
      </div>
    </div>
  </section>

  <section>
    <div class="band reveal">
      <div>
        <h2>Ready to try it?</h2>
        <p>Create an account, then login with those details to open the workspace.</p>
      </div>
      <div class="actions">
        <a class="btn btn-line" href="{{ url_for('login') }}">Login</a>
        <a class="btn btn-primary" href="{{ url_for('signup') }}">Sign up</a>
      </div>
    </div>
  </section>

  <footer class="site-footer">
    <div class="site-footer__inner">
      <p>© DealSignal · Motivated seller intelligence</p>
      <div>
        <a href="/landing">Investors</a>
        <a href="/sell">Sellers</a>
        <a href="{{ url_for('login') }}">Login</a>
      </div>
    </div>
  </footer>
</div>

<script>
(function(){
  var header = document.getElementById('siteHeader');
  function onScroll(){ header.classList.toggle('is-scrolled', window.scrollY > 8); }
  onScroll();
  window.addEventListener('scroll', onScroll, {passive:true});

  var nodes = document.querySelectorAll('.reveal');
  if('IntersectionObserver' in window){
    var io = new IntersectionObserver(function(entries){
      entries.forEach(function(e){ if(e.isIntersecting){ e.target.classList.add('is-in'); io.unobserve(e.target); }});
    }, {threshold:0.12, rootMargin:'0px 0px -40px 0px'});
    nodes.forEach(function(n){ io.observe(n); });
  } else {
    nodes.forEach(function(n){ n.classList.add('is-in'); });
  }
})();
</script>
</body></html>
"""

SIGNUP_PAGE = """<!DOCTYPE html>
<html lang="en-GB">
<head>
  <meta charset="UTF-8">
  <title>Sign up | DealSignal</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      background: #f6f8fa; color: #1c2128; min-height: 100vh;
      display: flex; flex-direction: column;
    }
    .top {
      display: flex; justify-content: space-between; align-items: center;
      max-width: 960px; width: 100%; margin: 0 auto; padding: 24px 20px 0;
    }
    .logo {
      font-weight: 800; color: #14273d; text-decoration: none;
      display: inline-flex; align-items: center; gap: 10px; letter-spacing: -0.03em;
    }
    .brand-mark {
      width: 34px; height: 34px; border-radius: 8px; background: #14273d; color: #fff;
      display: inline-flex; align-items: center; justify-content: center;
      font-weight: 800; font-size: 1.15rem; letter-spacing: -0.05em; line-height: 1;
    }
    .logo strong { font-size: 1.15rem; letter-spacing: -0.04em; }
    .top a.back { color: #5a6472; font-size: 0.9rem; font-weight: 600; text-decoration: none; }
    .top a.back:hover { color: #14273d; }
    .main {
      flex: 1; display: flex; align-items: center; justify-content: center;
      padding: 40px 20px 64px;
    }
    .card {
      width: 100%; max-width: 420px; background: #fff;
      border: 1px solid #e2e8ee; border-radius: 12px; padding: 36px 32px;
    }
    .card h1 { font-size: 1.45rem; color: #14273d; margin-bottom: 6px; }
    .subtitle { font-size: 0.95rem; color: #5a6472; margin-bottom: 28px; line-height: 1.5; }
    .form-group { margin-bottom: 16px; text-align: left; }
    .form-label {
      display: block; font-size: 0.8rem; font-weight: 700; color: #14273d; margin-bottom: 6px;
    }
    .form-input {
      width: 100%; background: #f6f8fa; border: 1px solid #cdd6dd; border-radius: 8px;
      padding: 12px 14px; color: #1c2128; font-family: inherit; font-size: 0.95rem; outline: none;
    }
    .form-input:focus { background: #fff; border-color: #14273d; }
    .btn {
      width: 100%; background: #14273d; color: #fff; border: none; border-radius: 8px;
      padding: 13px; font-family: inherit; font-weight: 700; font-size: 0.98rem;
      cursor: pointer; margin-top: 8px;
    }
    .btn:hover { background: #1c3228; }
    .alert {
      background: #eef4fa; border: 1px solid #c5d4e4; color: #14273d;
      padding: 12px; border-radius: 8px; font-size: 0.88rem; margin-bottom: 18px; text-align: left; line-height: 1.5;
    }
    .alert.error { background: #fdeaea; border-color: #eec3c3; color: #991b1b; }
    .footer-link { margin-top: 22px; text-align: center; font-size: 0.88rem; color: #5a6472; }
    .footer-link a { color: #1e5c3a; font-weight: 600; text-decoration: none; }
  </style>
</head>
<body>
  <div class="top">
    <a class="logo" href="/">
      <span class="brand-mark" aria-hidden="true">D</span>
      <strong>DealSignal</strong>
    </a>
    <a class="back" href="/">← Back to home</a>
  </div>
  <div class="main">
    <div class="card">
      <h1>Create account</h1>
      <p class="subtitle">Enter your details below. Then use the same email and password on the Login page.</p>

      {% with messages = get_flashed_messages(with_categories=true) %}
        {% for cat, msg in messages %}
          <div class="alert {% if cat == 'error' %}error{% endif %}">{{ msg }}</div>
        {% endfor %}
      {% endwith %}

      <form method="POST" action="{{ url_for('signup') }}">
        <div class="form-group">
          <label class="form-label" for="name">Full name</label>
          <input class="form-input" type="text" id="name" name="name" placeholder="Your name" required autofocus>
        </div>
        <div class="form-group">
          <label class="form-label" for="email">Email</label>
          <input class="form-input" type="email" id="email" name="email" placeholder="you@example.com" required>
        </div>
        <div class="form-group">
          <label class="form-label" for="password">Password</label>
          <input class="form-input" type="password" id="password" name="password" placeholder="At least 8 characters" minlength="8" required>
        </div>
        <div class="form-group">
          <label class="form-label" for="confirm_password">Confirm password</label>
          <input class="form-input" type="password" id="confirm_password" name="confirm_password" placeholder="Repeat password" minlength="8" required>
        </div>
        <button class="btn" type="submit">Create account</button>
      </form>

      <div class="footer-link">
        Already have an account? <a href="{{ url_for('login') }}">Login</a>
      </div>
    </div>
  </div>
</body>
</html>
"""

ACCOUNT_PAGE = """<!DOCTYPE html>
<html lang="en-GB"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Account — DealSignal</title>
<style>
 body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#f6f8fa;color:#1c2128;margin:0}
 .wrap{max-width:720px;margin:0 auto;padding:20px 20px 80px}
 .app-bar{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;
          background:#fff;border:1px solid #e2e8ee;border-radius:12px;padding:14px 18px;margin-bottom:20px}
 .app-bar__brand{font-weight:800;color:#14273d;font-size:1.05rem;text-decoration:none;display:inline-flex;align-items:center;gap:10px;letter-spacing:-0.03em}
 .brand-mark{width:28px;height:28px;border-radius:4px;background:#14273d;color:#fff;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0;font-weight:800;font-size:0.95rem;letter-spacing:-0.02em}
 .app-bar__brand-text{display:flex;flex-direction:column;line-height:1.05}
 .app-bar__brand-text strong{font-size:1.02rem;letter-spacing:-0.04em}
 .app-bar__brand-text span{font-size:.58rem;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#5a6472}
 .app-bar__nav{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
 .app-bar__nav a.nav{color:#5a6472;text-decoration:none;font-weight:600;font-size:.88rem;padding:8px 10px;border-radius:6px}
 .app-bar__nav a.nav:hover,.app-bar__nav a.nav.active{color:#14273d;background:#f4f6f8}
 .app-bar__user{font-size:.82rem;color:#5a6472;margin-right:6px}
 .btn{background:#14273d;color:#fff;border:0;border-radius:8px;padding:11px 22px;font-weight:700;cursor:pointer;font-size:.95rem;text-decoration:none;display:inline-block}
 .btn.logout{background:#dc2626;padding:9px 16px;font-size:.85rem}
 .card{background:#fff;border:1px solid #e2e8ee;border-radius:12px;padding:22px;margin-bottom:16px}
 h1{color:#14273d;font-size:1.4rem;margin:0 0 8px}
 h2{color:#14273d;font-size:1.05rem;margin:0 0 12px}
 .muted{color:#5a6472;font-size:.9rem;margin:0 0 16px;line-height:1.55}
 .row{display:flex;justify-content:space-between;gap:12px;padding:10px 0;border-bottom:1px solid #eef2f6;font-size:.92rem}
 .row:last-child{border-bottom:0}
 label{display:block;font-size:.8rem;font-weight:600;margin-bottom:4px}
 input{width:100%;padding:10px;border:1px solid #cdd6dd;border-radius:6px;font:inherit;box-sizing:border-box;margin-bottom:12px}
 .flash{background:#e8f5ec;border:1px solid #bfe3c9;border-radius:8px;padding:10px 16px;margin-bottom:12px;font-size:.9rem}
 .flash.error{background:#fdeaea;border-color:#eec3c3}
 .links a{display:inline-block;margin:6px 12px 6px 0;color:#1e5c3a;font-weight:600;text-decoration:none;font-size:.9rem}
</style></head><body><div class="wrap">
<div class="app-bar">
  <a class="app-bar__brand" href="{{ url_for('app_dashboard') }}">
    <span class="brand-mark" aria-hidden="true">D</span>
    <span class="app-bar__brand-text"><strong>DealSignal</strong><span>Workspace</span></span>
  </a>
  <div class="app-bar__nav">
    <a class="nav" href="{{ url_for('app_dashboard') }}">Workspace</a>
    <a class="nav active" href="{{ url_for('account') }}">Account</a>
    <span class="app-bar__user">{{ display_name }}</span>
    <a class="btn logout" href="{{ url_for('logout') }}">Logout</a>
  </div>
</div>
{% with msgs = get_flashed_messages(with_categories=true) %}
  {% for cat,m in msgs %}<div class="flash {{ cat }}">{{ m }}</div>{% endfor %}
{% endwith %}
<h1>Account settings</h1>
<p class="muted">Demo accounts for client feedback — no email OTP in this build.</p>
<div class="card">
  <h2>Profile</h2>
  <div class="row"><span>Name</span><strong>{{ display_name }}</strong></div>
  {% if email %}<div class="row"><span>Email</span><strong>{{ email }}</strong></div>{% endif %}
  <div class="row"><span>Login</span><strong>{{ username }}</strong></div>
  <div class="row"><span>Role</span><strong>{% if role == 'admin' %}Administrator{% else %}Member{% endif %}</strong></div>
  <div class="row"><span>Session length</span><strong>{{ session_hours }} hour(s)</strong></div>
</div>
<div class="card">
  <h2>Change password</h2>
  <p class="muted">{% if role == 'admin' %}Saves to <code>data/admin_auth.json</code> for this install.{% else %}Updates your demo account password in <code>data/users.json</code>.{% endif %}</p>
  <form method="post" action="{{ url_for('account') }}">
    <label for="current_password">Current password</label>
    <input type="password" id="current_password" name="current_password" required>
    <label for="new_password">New password</label>
    <input type="password" id="new_password" name="new_password" minlength="8" required>
    <label for="confirm_password">Confirm new password</label>
    <input type="password" id="confirm_password" name="confirm_password" minlength="8" required>
    <button class="btn" type="submit">Update password</button>
  </form>
</div>
<div class="card">
  <h2>Shortcuts</h2>
  <div class="links">
    <a href="{{ url_for('app_dashboard') }}#brand">Edit brand on dashboard</a>
    <a href="/landing" target="_blank">Investor landing</a>
    <a href="/sell" target="_blank">Seller landing</a>
    <a href="{{ url_for('logout') }}">Logout</a>
  </div>
</div>
</div></body></html>
"""


def _render_dashboard(preview_html=None, preview_back=None):
    return render_template_string(
        PAGE,
        brand=load_brand(),
        deals=load_deals(),
        sent=already_sent(),
        test_mode=TEST_MODE,
        scan_cfg=load_scan(),
        leads=load_leads(),
        preview_html=preview_html,
        preview_back=preview_back,
        display_name=session.get("display_name") or session.get("username") or ADMIN_USERNAME,
        nav="deals",
    )


@app.route("/")
def home():
    """Public hub — Login / Sign up, plus investor or seller paths."""
    return render_template_string(HOME_PAGE)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    """Always show the sign-up form. After create, user must Login with that email."""
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""

        if not name or not email or not password:
            flash("Please fill in name, email, and password.", "error")
            return render_template_string(SIGNUP_PAGE)
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template_string(SIGNUP_PAGE)
        if password != confirm:
            flash("Password and confirmation do not match.", "error")
            return render_template_string(SIGNUP_PAGE)
        if email == ADMIN_USERNAME.lower() or find_user_by_email(email):
            flash("That email is already registered. Please Login instead.", "error")
            return render_template_string(SIGNUP_PAGE)

        create_user(name, email, password)
        # End any old session so Login asks for the new email/password.
        session.clear()
        flash("Account created. Now Login with your email and password.", "message")
        return redirect(url_for("login"))
    return render_template_string(SIGNUP_PAGE)


@app.route("/login", methods=["GET", "POST"])
def login():
    """Always show the login form. Accepts signup email+password or admin credentials."""
    next_url = request.args.get("next") or request.form.get("next") or url_for("app_dashboard")

    ip = _client_ip()
    lock_secs = _login_lock_remaining(ip)
    if lock_secs > 0:
        mins = max(1, (lock_secs + 59) // 60)
        flash(
            f"Too many failed attempts. Try again in about {mins} minute(s).",
            "login_error",
        )
        return render_template_string(LOGIN_PAGE, next_url=next_url, login_locked=True)

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        ok, payload = authenticate(username, password)
        if ok:
            _clear_login_failures(ip)
            _start_session(payload)
            flash(f"Welcome back, {payload['display_name']}!", "message")
            return redirect(next_url)

        _record_login_failure(ip)
        if _login_lock_remaining(ip) > 0:
            flash(
                "Too many failed attempts. Login temporarily locked.",
                "login_error",
            )
            return render_template_string(LOGIN_PAGE, next_url=next_url, login_locked=True)
        flash("Invalid email/username or password.", "login_error")

    return render_template_string(LOGIN_PAGE, next_url=next_url, login_locked=False)


@app.route("/logout")
def logout():
    session.clear()
    flash("You have logged out.", "message")
    return redirect(url_for("home"))


@app.route("/app")
@login_required
def app_dashboard():
    return _render_dashboard()


@app.route("/app/account", methods=["GET", "POST"])
@login_required
def account():
    role = session.get("role") or "admin"
    email = session.get("email") or ""
    username = session.get("username") or ADMIN_USERNAME
    display_name = session.get("display_name") or username

    if request.method == "POST":
        current = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        if role == "admin":
            current_ok = check_password_hash(get_admin_password_hash(), current)
        else:
            user = find_user_by_email(email)
            current_ok = bool(user) and check_password_hash(user.get("password_hash") or "", current)

        if not current_ok:
            flash("Current password is incorrect.", "error")
        elif len(new_pw) < 8:
            flash("New password must be at least 8 characters.", "error")
        elif new_pw != confirm:
            flash("New password and confirmation do not match.", "error")
        else:
            if role == "admin":
                save_admin_password(new_pw)
            else:
                update_user_password(email, new_pw)
            flash("Password updated.", "message")
        return redirect(url_for("account"))

    return render_template_string(
        ACCOUNT_PAGE,
        display_name=display_name,
        username=username,
        email=email,
        role=role,
        session_hours=SESSION_HOURS,
    )


@app.route("/scan", methods=["POST"])
@login_required
def scan():
    """Subscriber sets their own area; runs the finder for it on demand."""
    area = (request.form.get("area") or "Bradford").strip()
    max_price = (request.form.get("max_price") or "250000").strip()
    if not HOMEDATA_KEY:
        flash("Set HOMEDATA_API_KEY first.", "error")
        return redirect(url_for("app_dashboard"))
    cmd = [sys.executable, "motivated_seller_finder.py",
           "--area", area, "--max-price", max_price,
           "--deep-dive", "3", "--out", CSV_PATH]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                           env={**os.environ, "HOMEDATA_API_KEY": HOMEDATA_KEY})
    except subprocess.TimeoutExpired:
        flash("Scan timed out — try a smaller area or higher price filter.", "error")
        return redirect(url_for("app_dashboard"))
    if r.returncode != 0:
        flash(f"Scan failed: {r.stderr.strip().splitlines()[-1] if r.stderr else 'unknown error'}", "error")
        return redirect(url_for("app_dashboard"))
    save_scan({"area": area, "max_price": max_price,
               "last_run": datetime.now().strftime("%d %b %Y %H:%M")})
    n = len(load_deals())
    flash(f"Scan complete: {area} — {n} motivated-seller candidate(s) found.")
    return redirect(url_for("app_dashboard"))


@app.route("/brand", methods=["POST"])
@login_required
def brand():
    save_brand({k: request.form.get(k, "").strip() for k in DEFAULT_BRAND})
    flash("Brand saved.")
    return redirect(url_for("app_dashboard"))


@app.route("/preview", methods=["POST"])
@login_required
def preview():
    b = load_brand()
    return _render_dashboard(
        preview_html=flyer_front_html(b, None),
        preview_back=flyer_back_message(b),
    )


@app.route("/send", methods=["POST"])
@login_required
def send():
    ids = request.form.getlist("ids")
    if not ids:
        flash("No properties selected. Tick at least one property first.", "error")
        return redirect(url_for("app_dashboard"))
    if not HOMEDATA_KEY:
        flash("Homedata API key is missing. Add HOMEDATA_API_KEY to your .env file.", "error")
        return redirect(url_for("app_dashboard"))
    if not stannp_key_ready():
        flash(
            "Flyer send failed: Stannp API key is missing or still the placeholder. "
            "Put your real key in .env as STANNP_API_KEY=... then restart the app. "
            "(Preview flyer still works without it.)",
            "error",
        )
        return redirect(url_for("app_dashboard"))

    b = load_brand()
    deals = {d["listing_id"]: d for d in load_deals()}
    ok = fail = 0
    last_error = ""
    for lid in ids:
        deal = deals.get(lid)
        if not deal:
            continue
        try:
            addr = reveal_address(lid)
            resp = send_postcard(b, deal, addr)
            sid = resp.get("data", {}).get("id", "?")
            log_sent([datetime.now().isoformat(timespec="seconds"), lid,
                      f"{addr['address1']}, {addr['postcode']}", sid, TEST_MODE, "ok"])
            ok += 1
        except Exception as e:  # noqa: BLE001 — prototype
            last_error = str(e)
            log_sent([datetime.now().isoformat(timespec="seconds"), lid, "", "", TEST_MODE, f"error: {e}"])
            fail += 1

    if fail and not ok:
        reason = last_error
        if "401" in reason:
            reason = "Stannp rejected the API key (401 Unauthorized). Check STANNP_API_KEY in .env."
        flash(f"Could not send flyer(s). {reason}", "error")
    elif fail:
        flash(
            f"{ok} flyer(s) {'proofed in test mode' if TEST_MODE else 'sent'}, "
            f"{fail} failed. Last error: {last_error}",
            "error",
        )
    else:
        flash(
            f"{ok} flyer(s) {'proofed in test mode (PDF only — nothing posted)' if TEST_MODE else 'sent successfully'}.",
            "message",
        )
    return redirect(url_for("app_dashboard"))


@app.route("/landing")
def landing():
    """Serves the marketing landing page from the root folder."""
    try:
        with open("deal_alerts_landing.html", "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Landing page file not found: {e}", 404


@app.route("/sell")
def sell_landing():
    """Serves the homeowner-facing cash offer landing page."""
    try:
        with open("seller_landing.html", "r", encoding="utf-8") as f:
            tmpl = f.read()
        return render_template_string(tmpl, brand=load_brand())
    except Exception as e:
        return f"Seller landing page template not found: {e}", 404


@app.route("/sell-inquiry", methods=["POST"])
def sell_inquiry():
    """Saves a homeowner inquiry to data/seller_leads.csv."""
    postcode = request.form.get("postcode", "").strip()
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    email = request.form.get("email", "").strip()
    reason = request.form.get("reason", "").strip()

    if not postcode or not name or not phone:
        flash("Please fill out all required fields.", "error")
        return redirect(url_for("sell_landing"))

    try:
        save_lead(postcode, name, phone, email, reason)
        flash("Your cash offer request has been received. We will contact you shortly.", "inquiry_success")
    except Exception as e:
        flash(f"Error saving details: {e}", "error")

    return redirect(url_for("sell_landing"))


if __name__ == "__main__":
    print("DealSignal")
    print("  Home (choose path): http://localhost:5000/")
    print("  Investor page:      http://localhost:5000/landing")
    print("  Seller page:        http://localhost:5000/sell")
    print("  Staff login:        http://localhost:5000/login → /app")
    app.run(debug=True, port=5000)
