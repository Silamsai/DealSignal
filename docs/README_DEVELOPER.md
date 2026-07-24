# DealSignal — Developer Guide

This repository contains the motivated-seller digest system (Level 1 email automation) and the direct-mail flyer dashboard (Level 2 web app) designed for property investors.

---

## File Inventory & Workspace Structure

```text
weekly-digest/
│
├── app/                             <-- Flask application package
│   ├── __init__.py                  <-- App factory (create_app)
│   ├── config.py                    <-- All env-var configuration
│   ├── auth.py                      <-- Authentication & brute-force protection
│   ├── models.py                    <-- File-based data access (thread-safe)
│   ├── routes.py                    <-- All route handlers (Blueprint)
│   ├── services.py                  <-- Homedata & Stannp API integrations
│   ├── templates/                   <-- Jinja2 HTML templates
│   │   ├── home.html                <-- Public landing page
│   │   ├── login.html               <-- Login form
│   │   ├── signup.html              <-- Signup form
│   │   ├── dashboard.html           <-- Flyer workspace (auth required)
│   │   ├── account.html             <-- Account settings (auth required)
│   │   ├── deal_alerts_landing.html <-- Subscription sales page for investors
│   │   └── seller_landing.html      <-- Homeowner cash-offer landing page
│   └── static/                      <-- Static assets
│
├── bin/                             <-- Command line automation scripts
│   ├── motivated_seller_finder.py   <-- Real-time property scanner & scorer
│   ├── run_weekly.py                <-- Weekly batch email automation
│   └── send_digest.py               <-- Manual email tool (legacy)
│
├── .github/
│   └── workflows/
│       └── weekly-digest.yml        <-- Scheduled cron workflow for GHA
│
├── data/                            <-- Persistent data files (gitignored)
│   ├── brand.json                   <-- Postcard branding configuration
│   ├── subscribers.csv              <-- Master email list
│   ├── motivated_sellers.csv        <-- Identified motivated sellers cache
│   ├── seller_leads.csv             <-- Homeowner inquiry leads
│   └── sent_log.csv                 <-- Postcard delivery audit trail
│
├── docs/                            <-- Technical instructions & reports (this folder)
│   ├── DEPLOYMENT.md                <-- Production setup guides
│   ├── HOW_IT_WORKS.md              <-- Scoring engine & API pipeline
│   ├── UK_Lead_Gen_Niche_Research_Report.md
│   └── README_DEVELOPER.md          <-- This guide
│
├── run.py                           <-- App entry point (dev & production)
├── flyer.py                         <-- Backward-compatible wrapper
├── Procfile                         <-- Production process config
├── requirements.txt                 <-- Python dependencies
├── .env.template                    <-- Environment variable template
└── .gitignore                       <-- Version control exclusions
```

## Prerequisites

- Python 3.11+
- `pip install -r requirements.txt`
- Accounts: GitHub, Homedata (free), Resend (free), Stripe, Stannp (L2 only)

## Quick Start (Development)

```bash
# 1. Clone and enter the project
git clone <repo-url> && cd weekly-digest

# 2. Create virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
copy .env.template .env      # Windows
# cp .env.template .env      # macOS/Linux
# Edit .env with your real API keys

# 5. Run the app
python run.py                # or: python flyer.py (backward compat)
```

Open http://localhost:5000/

## Environment Variables

| Var | Used by | Notes |
|---|---|---|
| `HOMEDATA_API_KEY` | finder, app | homedata.co.uk/register — free 100 calls/mo |
| `RESEND_API_KEY` | send_digest | resend.com — verify sending domain |
| `FROM_EMAIL` | send_digest | e.g. `Deal Alerts <alerts@domain.co.uk>` |
| `STANNP_API_KEY` | app | stannp.com, pay-per-item |
| `STANNP_TEST_MODE` | app | **Defaults ON (proofs only).** Set `0` to post for real |
| `FLASK_SECRET` | app | **Generate a strong random string for production** |
| `ADMIN_USERNAME` | app | Default: `admin` |
| `ADMIN_PASSWORD` | app | **Change before deploying** |
| `SESSION_HOURS` | app | Default: 8 |
| `LOGIN_MAX_ATTEMPTS` | app | Default: 5 |
| `LOGIN_LOCKOUT_SECONDS` | app | Default: 900 (15 min) |

## Deployment

### Production (AWS EC2 — Recommended)

```bash
# On the server:
git clone <repo-url> && cd weekly-digest
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure production .env (generate a real FLASK_SECRET!)
python -c "import secrets; print(secrets.token_hex(32))"

# Run with gunicorn
gunicorn run:app --bind 0.0.0.0:5000 --workers 1 --timeout 120

# Or use the Procfile with a process manager
```

**Important:** Use `--workers 1` because the app uses file-based storage. Multiple workers would cause data corruption.

### Production (Render)

1. Connect repo at render.com → New → Web Service
2. Build: `pip install -r requirements.txt`
3. Start: auto-detected from `Procfile`
4. Add env vars from the table above

### Weekly Digest (GitHub Actions)

The workflow at `.github/workflows/weekly-digest.yml` runs automatically every Monday 07:00 UTC. Add secrets in Settings → Secrets → Actions.

## Architecture

```
Request → Flask Blueprint (routes.py)
              ├── auth.py (session, brute-force)
              ├── models.py (file I/O with thread locks)
              └── services.py (Homedata, Stannp APIs)
```

- **Thread-safe file I/O**: All write operations use `threading.Lock`
- **Structured logging**: `logging` module throughout (replaces `print()`)
- **Health check**: `GET /healthz` returns `{"status": "ok"}`
- **Blueprint-based routing**: All routes in a single `main` blueprint

## Running Costs

| Item | Cost |
|---|---|
| GitHub Actions, Resend free tier | £0 |
| Homedata | Free ≤100 calls/mo |
| EC2 t3.micro | ~$8/mo (or free tier) |
| Per flyer | ~£1.05 (suggest £2+ resale) |

## Compliance Notes

- Flyers address "The Homeowner" — no personal data processed; keep the printed opt-out line.
- Digest sells derived scores, not raw data redistribution — confirm scale use with Homedata.
- Landing page keeps the "not investment advice" disclaimer — leave it in.
