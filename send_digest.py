#!/usr/bin/env python3
"""
Sends the weekly motivated-seller digest email to all subscribers.

Reads the CSV produced by motivated_seller_finder.py, formats an HTML digest,
and sends one email per subscriber via the Resend API (https://resend.com).

Env vars (set as GitHub Actions secrets):
    RESEND_API_KEY   Resend API key
    FROM_EMAIL       Verified sender, e.g. "Deal Alerts <alerts@yourdomain.co.uk>"
    AREA_NAME        Display name for the subject line (default: Bradford)

Subscribers: one email per line in subscribers.txt (comments with #).
"""

import csv
import json
import os
import sys
import requests
from datetime import date
from dotenv import load_dotenv

load_dotenv()

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "")
AREA = os.environ.get("AREA_NAME", "Bradford")
CSV_PATH = sys.argv[1] if len(sys.argv) > 1 else "data/motivated_sellers.csv"


def load_subscribers(path="data/subscribers.txt"):
    if not os.path.exists(path):
        sys.exit(f"{path} not found — add one subscriber email per line.")
    with open(path, encoding="utf-8") as f:
        subs = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    if not subs:
        sys.exit("No subscribers in subscribers.txt — nothing to send.")
    return subs


def load_deals(path):
    if not os.path.exists(path):
        sys.exit(f"{path} not found — run motivated_seller_finder.py first.")
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_html(deals):
    rows = ""
    dashboard_url = os.environ.get("DASHBOARD_URL", "http://localhost:5000")
    for d in deals[:10]:
        price = f"£{int(d['price']):,}" if d.get("price") and d["price"].isdigit() else (d.get("price") or "POA")
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
      <h1 style="color:#1e5c3a;margin-bottom:6px;">Motivated Seller Digest — {AREA}</h1>
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
        To unsubscribe, reply with "unsubscribe".
      </p>
    </div>"""


def send(to_addr, html):
    payload = {
        "from": FROM_EMAIL,
        "to": [to_addr],
        "subject": f"{AREA} motivated sellers — week of {date.today().strftime('%d %b')}",
        "html": html,
    }
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        r = requests.post(
            "https://api.resend.com/emails",
            json=payload,
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        return r.status_code
    except requests.exceptions.HTTPError as e:
        if e.response is not None:
            if e.response.status_code == 401:
                sys.exit("\n🚫 RESEND UNAUTHORIZED (401): Your RESEND_API_KEY is invalid or missing.\nPlease set a valid key in your config.")
            elif e.response.status_code == 403:
                sys.exit("\n🚫 RESEND FORBIDDEN (403): Your FROM_EMAIL domain is not verified in your Resend Dashboard.")
            elif e.response.status_code == 422:
                try:
                    res = e.response.json()
                    msg = res.get("message") or res.get("error", {}).get("message", "validation error")
                    sys.exit(f"\n🚫 RESEND VALIDATION ERROR (422): {msg}")
                except Exception:
                    sys.exit(f"\n🚫 RESEND VALIDATION ERROR (422): {e.response.text}")
        raise


def main():
    if not RESEND_API_KEY or not FROM_EMAIL:
        sys.exit("Set RESEND_API_KEY and FROM_EMAIL environment variables.")
    deals = load_deals(CSV_PATH)
    if not deals:
        print("CSV is empty — no digest sent this week.")
        return
    html = build_html(deals)
    for sub in load_subscribers():
        status = send(sub, html)
        print(f"Sent to {sub}: HTTP {status}")


if __name__ == "__main__":
    main()
