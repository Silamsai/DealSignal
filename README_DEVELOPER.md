# DealSignal — Developer Guide

This repository contains the motivated-seller digest system (Level 1 email automation) and the direct-mail flyer dashboard (Level 2 web app) designed for property investors.

---

## File Inventory & Workspace Structure

```text
weekly-digest/
│
├── .github/
│   └── workflows/
│       └── weekly-digest.yml        <-- Scheduled cron workflow for GHA
│
├── data/                            <-- Persistent application data files (Git ignored)
│   ├── brand.json                   <-- Dashboard postcard branding configuration
│   ├── subscribers.csv              <-- Master email list of subscribing investors
│   ├── motivated_sellers.csv        <-- Output cache of identified motivated sellers
│   └── sent_log.csv                 <-- Postcard delivery audit trail
│
├── docs/                            <-- Technical instructions & reports
│   ├── DEPLOYMENT.md                <-- Production setup & secret environment guides
│   ├── HOW_IT_WORKS.md              <-- Point scoring engine & API pipeline guides
│   └── UK_Lead_Gen_Niche_Research_Report.md
│
├── deal_alerts_landing.html         <-- Subscription sales page for investors
├── flyer_app.py                     <-- Web dashboard app (Flask)
├── motivated_seller_finder.py       <-- Real-time property scanner & scorer
├── run_weekly.py                    <-- Weekly batch email automation coordinator
├── send_digest.py                   <-- Manual email execution tool (legacy)
├── requirements.txt                 <-- Python package dependencies index
├── .gitignore                       <-- Version control exclusion configuration
└── README_DEVELOPER.md              <-- This file
```

## Prerequisites

- Python 3.11+ (scripts use stdlib only, except flyer_app: `pip install flask requests`)
- Accounts: GitHub, Homedata (free), Resend (free), Stripe, Stannp (L2 only), Netlify or Cloudflare Pages

## Environment variables

| Var | Used by | Notes |
|---|---|---|
| `HOMEDATA_API_KEY` | finder, flyer_app | homedata.co.uk/register — free 100 calls/mo |
| `RESEND_API_KEY` | send_digest | resend.com — verify sending domain |
| `FROM_EMAIL` | send_digest | e.g. `Deal Alerts <alerts@domain.co.uk>` |
| `AREA_NAME` / `MAX_PRICE` | workflow | Optional; defaults Bradford / 250000 |
| `STANNP_API_KEY` | flyer_app | stannp.com, pay-per-item |
| `STANNP_TEST_MODE` | flyer_app | **Defaults ON (proofs only).** Set `0` to post for real |
| `FLASK_SECRET` | flyer_app | Any random string in production |
| `DEALS_CSV` | flyer_app | Path to finder output (default `motivated_sellers.csv`) |

## Deployment

### A. Static sites (tree site + landing page) — Netlify, ~5 min
1. Rename the HTML file to `index.html`.
2. Replace placeholders: phone number + Formspree ID (tree site); Stripe payment link + contact email (landing page).
3. Drag-and-drop at app.netlify.com/drop, or connect the repo (publish dir = `site/`).
4. Add custom domain in Site settings; Netlify handles SSL.

### B-L1. Weekly digest — GitHub Actions, ~15 min
1. Push repo (structure above). Workflow file must be at `.github/workflows/weekly-digest.yml`.
2. Add secrets `HOMEDATA_API_KEY`, `RESEND_API_KEY`, `FROM_EMAIL` (Settings → Secrets → Actions); optional vars `AREA_NAME`, `MAX_PRICE`.
3. Put your own email in `subscribers.txt`, push.
4. Actions tab → Run workflow (manual test). Fix any live-API field mismatches, then it self-runs Mondays 07:00 UTC.
5. New paying subscriber (Stripe emails you) → append email to `subscribers.txt`, push.

Full walkthrough with troubleshooting: see `DEPLOYMENT.md`.

### B-L2. Flyer app — Render, ~20 min
1. Ensure `requirements.txt` includes `flask`, `requests`, `gunicorn`.
2. render.com → New → Web Service → connect repo.
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn flyer_app:app --bind 0.0.0.0:$PORT`
3. Add env vars from the table (keep `STANNP_TEST_MODE=1` until verified).
4. The app reads `motivated_sellers.csv` — simplest wiring: commit the weekly CSV from the Actions run (add a commit step to the workflow), or upload manually while prototyping.
5. **Access control**: the prototype has none. Before exposing publicly, at minimum enable Render's basic auth / put it behind Cloudflare Access; properly, add login + per-user Stripe billing.

### First-run verification checklist (L2)
- [ ] Homedata address-reveal endpoint path & response fields (coded as `GET /listing-address/{listing_id}/` — docs were unreachable at build time)
- [ ] Stannp postcard params (`/v1/postcards/create`, recipient[] fields, HTML front)
- [ ] Send one TEST flyer → review Stannp proof PDF for layout
- [ ] Confirm `sent_log.csv` dedupe (re-selecting a sent property is blocked)
- [ ] Only then: `STANNP_TEST_MODE=0`, send 1 live flyer to your own address

## Running costs

| Item | Cost |
|---|---|
| Static hosting, GitHub Actions, Resend, Stripe | £0 fixed (Stripe ~1.5% + 20p per transaction) |
| Homedata | Free ≤100 calls/mo (≈3 digest runs + reveals); Starter plan when scaling |
| Render web service | Free tier (sleeps when idle) or $7/mo always-on |
| Per flyer | ~£0.20 reveal + ~£0.85 Stannp = ~£1.05 (suggest £2+ resale) |

## Compliance notes

- Flyers address "The Homeowner" — no personal data processed; keep the printed opt-out line and honour it (maintain a suppression list as it grows).
- Digest sells derived scores, not raw data redistribution — confirm scale use with Homedata (their industry packs cover property sourcers).
- Landing page keeps the "not investment advice" disclaimer — leave it in.
