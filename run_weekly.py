#!/usr/bin/env python3
"""
Weekly multi-area orchestrator — supersedes send_digest.py for Level 1.

Each subscriber chooses their own area. subscribers.csv format:

    email,area,max_price
    investor1@example.com,Bradford,250000
    investor2@example.com,Leeds,300000
    investor3@example.com,Bradford,250000

The script groups subscribers by unique (area, max_price), runs the finder
once per group (not per subscriber — protects the API quota), then emails
each group its own digest via Resend.

API budget: each unique area ≈ 8 Homedata calls (5 search + 3x1 deep dives).
Free tier (100/month) supports ~12 areas weekly at --deep-dive 3, or take the
Starter plan. The script prints the projected call count and aborts if it
would exceed MAX_CALLS.

Env vars: HOMEDATA_API_KEY, RESEND_API_KEY, FROM_EMAIL
Optional:  MAX_CALLS (default 95), DEEP_DIVE (default 3)
"""

import csv
import json
import os
import subprocess
import sys
import requests
from collections import defaultdict
from datetime import date
from dotenv import load_dotenv

load_dotenv()

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "")
MAX_CALLS = int(os.environ.get("MAX_CALLS", "95"))
DEEP_DIVE = int(os.environ.get("DEEP_DIVE", "3"))
CALLS_PER_AREA = 5 + DEEP_DIVE * 1  # 5 for search + 1 per deep-dive property

SUBS_PATH = "data/subscribers.csv"


def load_groups():
    """{(area, max_price): [emails]}"""
    if not os.path.exists(SUBS_PATH):
        sys.exit(f"{SUBS_PATH} not found. Format: email,area,max_price (header row required).")
    groups = defaultdict(list)
    with open(SUBS_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            email = (row.get("email") or "").strip()
            if not email or email.startswith("#"):
                continue
            area = (row.get("area") or "Bradford").strip()
            max_price = (row.get("max_price") or "250000").strip()
            groups[(area, max_price)].append(email)
    if not groups:
        sys.exit("No subscribers found.")
    return groups


def run_finder(area, max_price, out_csv):
    cmd = [sys.executable, "motivated_seller_finder.py",
           "--area", area, "--max-price", str(max_price),
           "--deep-dive", str(DEEP_DIVE), "--out", out_csv]
    print(f"\n=== Scanning {area} (max £{max_price}) ===")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    print(r.stdout[-2000:])
    if r.returncode != 0:
        print(f"FINDER FAILED for {area}:\n{r.stderr[-2000:]}", file=sys.stderr)
        return False
    return True


def build_html(deals, area):
    rows = ""
    dashboard_url = os.environ.get("DASHBOARD_URL", "http://localhost:5000")
    for d in deals[:10]:
        price = f"£{int(d['price']):,}" if (d.get("price") or "").isdigit() else (d.get("price") or "POA")
        signals = (d.get("signals") or "").replace("; ", "<br>• ")
        
        rows += f"""
        <tr>
          <td style="padding:12px;border-bottom:1px solid #e5e5e5;vertical-align:top">
            <strong>#{d['rank']} — {d.get('address', 'Address available on reveal')}</strong> · 
            <a href="{dashboard_url}#deal-{d['listing_id']}" target="_blank" style="color:#1e5c3a;text-decoration:none;font-size:12px;font-weight:bold;">[Target Deal]</a><br>
            <span style="color:#555">{d.get('bedrooms', '?')} bed {d.get('type') or ''} · {price} ·
            {d.get('days_on_market', '?')} days on market</span><br>
            <span style="color:#1e5c3a;font-size:13px;margin-top:6px;display:block;">• {signals}</span>
          </td>
          <td style="padding:12px;border-bottom:1px solid #e5e5e5;text-align:center;vertical-align:top">
            <span style="background:#1e5c3a;color:#fff;border-radius:14px;padding:4px 12px;font-weight:700">{d['score']}</span>
          </td>
        </tr>"""
    return f"""
    <div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;margin:0 auto;color:#1c2321;padding:10px;">
      <h1 style="color:#1e5c3a;margin-bottom:6px;">Motivated Seller Digest — {area}</h1>
      <p style="color:#555;margin-top:0;">Week of {date.today().strftime('%d %B %Y')} · Top {min(len(deals), 10)} candidates,
      scored on price cuts, time on market, collapsed chains and withdrawal history.</p>
      
      <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
        <tr><th style="text-align:left;padding:8px 12px;background:#f3f7f3">Property</th>
            <th style="padding:8px 12px;background:#f3f7f3">Score</th></tr>
        {rows}
      </table>
      
      <!-- Direct Link to Campaign Dashboard -->
      <div style="margin:30px 0;padding:18px;background:#f3f7f3;border-left:4px solid #1e5c3a;border-radius:0 8px 8px 0;text-align:left;">
        <h4 style="margin:0 0 6px 0;color:#1e5c3a;font-size:15px;font-weight:700;">Take Action: Send Physical Postcards</h4>
        <p style="margin:0 0 14px 0;font-size:13px;color:#555;line-height:1.4;">Bypass estate agents and mail the owners directly. Click the button below to open your campaign dashboard, reveal their exact addresses, and send branded cards.</p>
        <a href="{dashboard_url}" target="_blank" style="background:#1e5c3a;color:#ffffff;text-decoration:none;padding:10px 18px;border-radius:6px;font-size:13px;font-weight:bold;display:inline-block;">Open Direct-Mail Dashboard</a>
      </div>
      
      <p style="color:#888;font-size:12px;margin-top:24px;border-top:1px solid #eee;padding-top:10px;">
        Source data: public listing activity via Homedata/Home.co.uk. Scores are indicative signals,
        not valuations or investment advice. Always do your own due diligence.<br>
        To change your area or price cap, or to unsubscribe, just reply to this email.
      </p>
    </div>"""


def send(to_addr, html, area):
    # Mock send if keys are not configured (useful for dry runs and testing)
    if not RESEND_API_KEY or not FROM_EMAIL:
        preview_filename = f"preview_digest_{area.lower().replace(' ', '_')}.html"
        with open(preview_filename, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  [MOCK PRINT] To: {to_addr} | Saved HTML preview to {preview_filename}")
        return "MOCK_200"

    payload = {
        "from": FROM_EMAIL, "to": [to_addr],
        "subject": f"{area} motivated sellers — week of {date.today().strftime('%d %b')}",
        "html": html,
    }
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    r = requests.post(
        "https://api.resend.com/emails",
        json=payload,
        headers=headers,
        timeout=30,
    )
    r.raise_for_status()
    return r.status_code


def main():
    if not RESEND_API_KEY or not FROM_EMAIL:
        print("💡 RESEND_API_KEY or FROM_EMAIL not set. Generating digest and saving HTML previews locally instead of sending.")
    
    groups = load_groups()

    projected = len(groups) * CALLS_PER_AREA
    print(f"{len(groups)} unique area group(s); projected API usage: {projected} calls "
          f"(budget {MAX_CALLS}).")
    if projected > MAX_CALLS:
        sys.exit(f"ABORT: would exceed call budget. Reduce areas/DEEP_DIVE or raise the Homedata plan.")

    for (area, max_price), emails in groups.items():
        out_csv = f"data/motivated_sellers_{area.lower().replace(' ', '_')}.csv"
        if not run_finder(area, max_price, out_csv):
            continue
        with open(out_csv, encoding="utf-8") as f:
            deals = list(csv.DictReader(f))
        if not deals:
            print(f"No qualifying deals in {area} this week — skipping send.")
            continue
        html = build_html(deals, area)
        for e in emails:
            print(f"  -> {e}: HTTP {send(e, html, area)}")


if __name__ == "__main__":
    main()
