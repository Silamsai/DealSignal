"""External API integrations: Homedata address reveal, Stannp postcard sending."""

import logging
import uuid

import requests

from app.config import HOMEDATA_API_KEY, STANNP_API_KEY, STANNP_TEST_MODE

logger = logging.getLogger(__name__)


def stannp_key_ready() -> bool:
    """True when STANNP_API_KEY looks like a real key (not the .env placeholder)."""
    key = (STANNP_API_KEY or "").strip()
    if not key:
        return False
    lowered = key.lower()
    if "your_stannp" in lowered or "your_api" in lowered or key.endswith("_api_key"):
        return False
    return True


def reveal_address(listing_id: str) -> dict:
    """Homedata full-address reveal (£0.20 first reveal, repeats free)."""
    headers = {
        "Authorization": f"Api-Key {HOMEDATA_API_KEY}",
        "Idempotency-Key": str(uuid.uuid4()),
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
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


def flyer_front_html(brand: dict, deal) -> str:
    """A5 postcard front HTML. Stannp renders this to print."""
    brand_color = brand.get('colour', '#1e5c3a')
    brand_name = brand.get('name', 'DealSignal')
    brand_tagline = brand.get('tagline', 'Direct Property Buyers')
    brand_phone = brand.get('phone', '')
    brand_email = brand.get('email', '')
    brand_website = brand.get('website', '')
    
    return f"""<!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;700;800&display=swap" rel="stylesheet">
      <style>
        body {{
          width: 148mm;
          height: 105mm;
          margin: 0;
          padding: 0;
          font-family: 'Outfit', -apple-system, sans-serif;
          background: #ffffff;
          box-sizing: border-box;
          color: #111827;
          overflow: hidden;
        }}
        .card {{
          display: flex;
          width: 148mm;
          height: 105mm;
          overflow: hidden;
          box-sizing: border-box;
        }}
        .sidebar {{
          width: 53mm;
          background: {brand_color};
          padding: 8mm 5mm;
          display: flex;
          flex-direction: column;
          justify-content: space-between;
          box-sizing: border-box;
          color: #ffffff;
          position: relative;
        }}
        .sidebar::after {{
          content: "";
          position: absolute;
          right: 0;
          top: 0;
          bottom: 0;
          width: 4px;
          background: rgba(0, 0, 0, 0.15);
        }}
        .main-content {{
          width: 95mm;
          padding: 8mm 7mm;
          display: flex;
          flex-direction: column;
          justify-content: space-between;
          box-sizing: border-box;
          background-color: #fafbfc;
        }}
        .badge {{
          background: rgba(255, 255, 255, 0.18);
          border: 1px solid rgba(255, 255, 255, 0.3);
          padding: 1.5mm 3mm;
          border-radius: 50px;
          font-size: 7.5pt;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.8px;
          display: inline-block;
          text-align: center;
        }}
        .category {{
          font-size: 8.5pt;
          color: {brand_color};
          font-weight: 700;
          letter-spacing: 1px;
          text-transform: uppercase;
          margin-bottom: 1.5mm;
        }}
        .headline {{
          font-size: 15.5pt;
          font-weight: 800;
          color: #0f172a;
          line-height: 1.25;
          letter-spacing: -0.4px;
          margin-bottom: 3mm;
        }}
        .feature-list {{
          margin-top: 1mm;
          margin-bottom: 2mm;
        }}
        .feature-item {{
          display: flex;
          align-items: center;
          font-size: 9pt;
          color: #475569;
          margin-bottom: 1.2mm;
          font-weight: 600;
        }}
        .feature-icon {{
          width: 14px;
          height: 14px;
          margin-right: 2mm;
          flex-shrink: 0;
        }}
        .cta-box {{
          background: #ffffff;
          border: 1px solid #e2e8f0;
          border-left: 4.5px solid {brand_color};
          padding: 3mm 4mm;
          border-radius: 6px;
          box-sizing: border-box;
          box-shadow: 0 2px 6px rgba(0, 0, 0, 0.02);
        }}
        .phone-row {{
          display: flex;
          align-items: center;
          font-size: 13pt;
          font-weight: 800;
          color: #0f172a;
          margin-bottom: 1mm;
        }}
        .info-row {{
          display: flex;
          align-items: center;
          font-size: 8pt;
          color: #64748b;
          gap: 3mm;
        }}
        .contact-item {{
          display: flex;
          align-items: center;
        }}
        .contact-icon {{
          width: 11px;
          height: 11px;
          margin-right: 1mm;
          opacity: 0.7;
          color: {brand_color};
        }}
      </style>
    </head>
    <body>
      <div class="card">
        <div class="sidebar">
          <div>
            <div style="font-size: 16pt; font-weight: 800; letter-spacing: -0.5px; line-height: 1.15; margin-bottom: 2mm; word-wrap: break-word;">{brand_name}</div>
            <div style="font-size: 8.5pt; opacity: 0.9; font-weight: 600; line-height: 1.35; margin-bottom: 4mm;">{brand_tagline}</div>
            <div style="width: 80%; height: 2px; background: rgba(255,255,255,0.25); margin-bottom: 4mm;"></div>
          </div>
          <div>
            <div class="badge">Direct Buyer</div>
          </div>
        </div>
        <div class="main-content">
          <div>
            <div class="category">Hassle-Free Property Sale</div>
            <div class="headline">Thinking of selling your property? We can help.</div>
            
            <div class="feature-list">
              <div class="feature-item">
                <svg class="feature-icon" viewBox="0 0 24 24" fill="none" stroke="{brand_color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="20 6 9 17 4 12"></polyline>
                </svg>
                <span>Guaranteed Direct Cash Offer</span>
              </div>
              <div class="feature-item">
                <svg class="feature-icon" viewBox="0 0 24 24" fill="none" stroke="{brand_color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="20 6 9 17 4 12"></polyline>
                </svg>
                <span>Zero Estate Agent Fees or Commission</span>
              </div>
              <div class="feature-item">
                <svg class="feature-icon" viewBox="0 0 24 24" fill="none" stroke="{brand_color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="20 6 9 17 4 12"></polyline>
                </svg>
                <span>No Chain Delays — Sell on Your Timeline</span>
              </div>
            </div>
          </div>
          
          <div class="cta-box">
            <div class="phone-row">
              <svg style="width: 14px; height: 14px; margin-right: 2.2mm; fill: none; stroke: {brand_color}; stroke-width: 2.5; stroke-linecap: round; stroke-linejoin: round;" viewBox="0 0 24 24">
                <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"></path>
              </svg>
              <span>Call: {brand_phone}</span>
            </div>
            <div class="info-row">
              <div class="contact-item">
                <svg class="contact-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path>
                  <polyline points="22,6 12,13 2,6"></polyline>
                </svg>
                <span>{brand_email}</span>
              </div>
              <div class="contact-item">
                <svg class="contact-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                  <circle cx="12" cy="12" r="10"></circle>
                  <line x1="2" y1="12" x2="22" y2="12"></line>
                  <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path>
                </svg>
                <span>{brand_website}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </body>
    </html>"""


def flyer_back_message(brand: dict) -> str:
    """Back of card: message area (Stannp adds the address block)."""
    return (
        f"Dear Homeowner,\n\n{brand['message']}\n\n"
        f"Kind regards,\n{brand['name']}\n{brand['phone']} · {brand['email']}\n\n"
        f"If you'd prefer not to hear from us again, call or email us and "
        f"we'll remove your address immediately."
    )


def send_postcard(brand: dict, deal: dict, addr: dict) -> dict:
    """Stannp create-postcard."""
    payload = {
        "test": "true" if STANNP_TEST_MODE else "false",
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
        data=payload, auth=(STANNP_API_KEY, ""), timeout=60,
    )
    r.raise_for_status()
    return r.json()
