# DealSignal Motivated-Seller System: How It Works & Architecture Guide

Welcome to the **DealSignal Property Digest** documentation. This system is designed for property sourcing companies, investors, and direct-to-vendor marketers to scan the UK property market, score seller motivation, email weekly digests to subscribers, and automate direct-mail postcard campaigns.

---

## 🏗️ System Overview

The system is split into two primary operational branches:
1. **Level 1 (Weekly Alerts):** A scheduled batch scraper and emailer that groups subscribers by target areas and budget, scores market listings, and sends HTML newsletters.
2. **Level 2 (Direct-Mail Dashboard):** A local/web dashboard to visually inspect candidates, reveal street addresses, and order physical postcards automatically.

```
                  ┌──────────────────────┐
                  │   subscribers.csv    │
                  └──────────┬───────────┘
                             │ (Reads entries)
                             ▼
                  ┌──────────────────────┐
                  │    run_weekly.py     │
                  └──────────┬───────────┘
                             │ (Groups by Area & Budget, scans)
                             ▼
              ┌──────────────────────────────┐
              │  motivated_seller_finder.py  │
              └──────────────┬───────────────┘
                             │ (Queries live Homedata API)
                             ├──────────────────────────┐
                             ▼                          ▼
                 [motivated_sellers_*.csv]       [HTML Newsletter]
                             │                          │
                             ▼ (Web Dashboard UI)       ▼ (Resend Delivery)
                     ┌───────────────┐          ┌───────────────┐
                     │ flyer_app.py  │          │   Email list  │
                     └───────┬───────┘          └───────────────┘
                             │ (POST reveal + Stannp API)
                             ▼
                    ┌─────────────────┐
                    │ Glossy Postcard │ (Royal Mail to Homeowner)
                    └─────────────────┘
```

---

## 🔍 Phase 1: Automated Scanning & Scouting (`motivated_seller_finder.py`)
This is the core data analysis module. It queries listing boards and runs a custom scoring metric.

### 1. Endpoint Sequence
* **Boundary Lookup:** Calls `/boundaries/autocomplete/?q={Area}`. This resolves human locations (e.g. "Bradford") to an official Homedata Boundary ID (e.g. `14679`).
* **Search Listings:** Calls `/live-listings/search/` filtering by boundary, asking price cap, and active listings with a minimum Days on Market (DOM) of **90 days** (aiming for mature listings).
* **Timeline Analysis:** For the top candidate properties, the script executes deep dives into their `/timeline/?listing_id={ID}` to read all historic events.

### 2. The Motivation Scoring Engine
Each property is scored out of a max of 20+ points based on historical red flags:
* **Price Cuts:** +2 points per recorded reduction (scored up to +6 maximum).
* **Significant Drop:** If the total price has dropped by more than 10%, it adds +4 points. If dropped by more than 15%, it adds +6 points.
* **Chain Collapse (Fell Through):** If the listing was marked *"Sold STC"* (Subject to Contract) but went back on the market, it represents a failed sale. This adds **+5 points**.
* **Listing Age (DOM):**
  * `90 - 180 days` = +2 points
  * `181 - 365 days` = +3 points
  * `365+ days` = +5 points

### 3. File Outputs
Once complete, the finder saves a CSV matrix (e.g. `motivated_sellers_bradford.csv`) sorting properties by highest motivation score first.

---

## 📧 Phase 2: Weekly Digest Orchestration (`run_weekly.py`)
This script acts as the master orchestrator, running once a week.

### 1. Grouping Logic
Instead of executing one API scan per email address (which would drain API limits), `run_weekly.py` parses `subscribers.csv` and groups subscribers together:
* If 5 investors signed up for "Bradford" under 250k, the script runs the search script **once** for Bradford at 250k.
* It parses the output, builds a shared HTML template, and sends individual emails to those 5 subscribers.

### 2. Email Delivery & Fallback Mode
* **Live Mode:** Connects to the **Resend API** and emails the formatted HTML newsletter using a verified company domain.
* **Mock/Preview Mode:** If no Resend API credentials are provided, the script runs in **test mode**. It generates local preview files (e.g., `preview_digest_bradford.html`) so you can read and inspect the newsletter without sending real emails.

---

## 🖥️ Phase 3: Flyer Direct-Mail Dashboard (`flyer_app.py`)
A Flask web application serving as the control deck for Level 2 operations.

### 1. Custom Branding Options
Users can customize their pitch variables:
* **Company info:** Name, tagline, brand color (hex), phone, email, and website.
* **Pitch copy:** A text field where the user writes their pitch (e.g., *"We buy houses cash"*).
* Saved into `brand.json` and loaded automatically on startup.

### 2. Isolated IFrame Postcard Previews
To display how the A5 postcard front looks without breaking the browser dashboard structure, the HTML is encapsulated into an isolated `<iframe>`:
* **Left half (Col 1):** Renders the A5 glossy postcard front design using a vertical brand-color accent panel, company logo headings, and your callback action card.
* **Right half (Col 2):** Renders the back message layout showing the greeting, message block, and standard opt-out footer.

### 3. Address Unlocks & Stannp Dispatch
When you click **"Send flyers to selected"**:
* **Address Reveal POST:** Calls Homedata `POST /listing-address/` with the listing ID and an `Idempotency-Key` header. This cost £0.20 and returns the full mailing address (repeats are free).
* **Stannp Dispatch:** Sends the mailing address, front, and back HTML payloads to Stannp. 
* **Safe Test Mode:** In test mode (`STANNP_TEST_MODE=1`), Stannp returns draft PDF proofs for free. In live mode (`STANNP_TEST_MODE=0`), Stannp prints, registers postage, and mails the physical card.
* **Deduplication:** Successful sends are saved to `sent_log.csv` so the user never mails the same property twice.

---

## ⚙️ Running in Production vs. Development

### 1. Local Development Mode
Run in your terminal using debug variables:
```powershell
$env:HOMEDATA_API_KEY="your_key_here"
python flyer_app.py
# Opens locally on http://localhost:5000
```

### 2. High-Performance Live Production
Do not use `python flyer_app.py` in production as the built-in Flask server is not secured for public internet traffic.

* **On a Windows Server (Waitress):**
  ```powershell
  pip install waitress
  waitress-serve --port=5000 flyer_app:app
  ```
* **On a Linux Cloud Server (Gunicorn on AWS/Render/DigitalOcean):**
  ```bash
  pip install gunicorn
  gunicorn -w 4 -b 0.0.0.0:5000 flyer_app:app
  ```

---

## 🔑 Required API Credentials Reference

Set these in your hosting config (e.g. Render environment variables or GitHub Actions Secrets):

* `HOMEDATA_API_KEY` — Your developer token from Homedata Dashboard.
* `RESEND_API_KEY` — To automate weekly emails to subscribers.
* `FROM_EMAIL` — Verified Resend sender address (e.g. `alerts@yourdomain.co.uk`).
* `STANNP_API_KEY` — Your Stannp direct-mail key.
* `STANNP_TEST_MODE` — Set to `0` for live mailings, or `1` for free digital proofs.
