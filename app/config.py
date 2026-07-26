"""Application configuration loaded from environment variables."""

import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

# ---------------------------------------------------------------------------
# Flask core
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get("FLASK_SECRET", "dev-only-change-me")
SESSION_HOURS = max(1, int(os.environ.get("SESSION_HOURS", "8")))
PERMANENT_SESSION_LIFETIME = timedelta(hours=SESSION_HOURS)

# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin"

# ---------------------------------------------------------------------------
# External API keys
# ---------------------------------------------------------------------------
HOMEDATA_API_KEY = os.environ.get("HOMEDATA_API_KEY", "")
STANNP_API_KEY = os.environ.get("STANNP_API_KEY", "")
STANNP_TEST_MODE = os.environ.get("STANNP_TEST_MODE", "1") != "0"

# ---------------------------------------------------------------------------
# Brute-force protection
# ---------------------------------------------------------------------------
LOGIN_MAX_ATTEMPTS = max(3, int(os.environ.get("LOGIN_MAX_ATTEMPTS", "5")))
LOGIN_LOCKOUT_SECONDS = max(60, int(os.environ.get("LOGIN_LOCKOUT_SECONDS", "900")))

# ---------------------------------------------------------------------------
# Data file paths
# ---------------------------------------------------------------------------
CSV_PATH = os.environ.get("DEALS_CSV", os.path.join(DATA_DIR, "motivated_sellers.csv"))
BRAND_PATH = os.path.join(DATA_DIR, "brand.json")
SENT_LOG = os.path.join(DATA_DIR, "sent_log.csv")
SCAN_PATH = os.path.join(DATA_DIR, "scan.json")
ADMIN_AUTH_PATH = os.path.join(DATA_DIR, "admin_auth.json")
USERS_PATH = os.path.join(DATA_DIR, "users.json")
LEADS_CSV = os.path.join(DATA_DIR, "seller_leads.csv")

# ---------------------------------------------------------------------------
# Demo / default data
# ---------------------------------------------------------------------------
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
    "name": "DealSignal",
    "tagline": "We buy houses in any condition",
    "phone": "01274 000 000",
    "email": "hello@dealsignal.co.uk",
    "website": "www.dealsignal.co.uk",
    "colour": "#14273d",
    "message": (
        "We noticed your property has been on the market for a while. "
        "We are a local buyer able to move quickly, with no chains and no fees. "
        "If you would like a no-obligation cash offer, we would love to hear from you."
    ),
}
