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
    return f"""
    <html>
    <body style="width:148mm;height:105mm;margin:0;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;background:#ffffff;box-sizing:border-box;">
      <div style="display:flex;width:148mm;height:105mm;overflow:hidden;box-sizing:border-box;">
        <div style="width:53mm;background:{brand['colour']};padding:10mm 6mm;display:flex;flex-direction:column;justify-content:space-between;box-sizing:border-box;color:#ffffff;">
          <div>
            <div style="font-size:18pt;font-weight:800;letter-spacing:-0.5px;line-height:1.2;margin-bottom:3mm;">{brand['name']}</div>
            <div style="font-size:9.5pt;opacity:0.9;font-weight:400;line-height:1.4;">{brand['tagline']}</div>
          </div>
          <div style="font-size:8pt;opacity:0.7;letter-spacing:0.5px;text-transform:uppercase;font-weight:600;">Direct Buyer Notice</div>
        </div>
        <div style="width:95mm;padding:10mm 8mm;display:flex;flex-direction:column;justify-content:space-between;box-sizing:border-box;">
          <div>
            <div style="font-size:9.5pt;color:{brand['colour']};font-weight:700;letter-spacing:0.8px;text-transform:uppercase;margin-bottom:2mm;">Hassle-Free Home Sale</div>
            <div style="font-size:17pt;font-weight:700;color:#1c2321;line-height:1.3;letter-spacing:-0.3px;">Thinking of a fresh start with your property sale?</div>
            <div style="font-size:10.5pt;color:#556b60;margin-top:3mm;line-height:1.5;">We buy properties directly for cash. Get a guaranteed, fee-free offer today with no chain delays.</div>
          </div>
          <div style="background:#f4f7f5;border-left:4px solid {brand['colour']};padding:4mm 5mm;border-radius:0 2mm 2mm 0;box-sizing:border-box;">
            <div style="font-size:12.5pt;font-weight:800;color:#1c2321;margin-bottom:1mm;">Call: {brand['phone']}</div>
            <div style="font-size:9.5pt;color:#44554b;font-weight:500;">{brand['email']} &nbsp;|&nbsp; {brand['website']}</div>
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
