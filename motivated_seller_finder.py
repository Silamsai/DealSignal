#!/usr/bin/env python3
"""
Motivated Seller Finder — prototype v1
Scores active UK property listings for seller motivation using the Homedata API.

Usage:
    export HOMEDATA_API_KEY=your_key          (Windows: set HOMEDATA_API_KEY=your_key)
    python motivated_seller_finder.py --area "Bradford" --max-price 250000

API budget (free tier = 100 calls/month):
    boundary autocomplete .......... FREE (no key needed)
    live-listings search ........... 5 calls flat (up to 200 results)
    sale-events timeline (deep dive) 1 call per property, top-N only
    Default run: 5 + (5 x 1 deep dives) = 10 calls. Weekly runs fit the free tier.

Docs: https://homedata.co.uk/docs/endpoints
"""

import argparse
import csv
import json
import os
import sys
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://api.homedata.co.uk"
API_KEY = os.environ.get("HOMEDATA_API_KEY", "")

calls_used = 0  # rough tracker against monthly quota


# ---------------------------------------------------------------- HTTP helper
def get(path: str, params: dict | None = None, cost: int = 0, auth: bool = True) -> dict:
    global calls_used
    url = f"{API_BASE}{path}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    if auth:
        if not API_KEY:
            sys.exit("ERROR: set the HOMEDATA_API_KEY environment variable (free key: https://homedata.co.uk/register)")
        headers["Authorization"] = f"Api-Key {API_KEY}"
    
    try:
        r = requests.get(url, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        calls_used += cost
        return r.json()
    except requests.exceptions.HTTPError as e:
        if e.response is not None:
            if e.response.status_code == 429:
                sys.exit("\n🚫 API LIMIT REACHED: You have used 100% of your Homedata monthly API budget (429 Too Many Requests).\nPlease upgrade your account plan at https://homedata.co.uk or wait for your monthly quota reset date.")
            elif e.response.status_code == 403:
                sys.exit("\n🚫 FORBIDDEN (403): The key starting with 'wt_' is a public Widget Key and cannot be used in python scripts.\nPlease configure a private backend API key from your Homedata Settings panel.")
        raise


def days_since(iso_date: str | None) -> int | None:
    if not iso_date:
        return None
    try:
        d = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).days
    except ValueError:
        return None


# ---------------------------------------------------------------- scoring
def base_score(listing: dict) -> tuple[int, list[str]]:
    """Score from fields returned by the search itself (no extra API cost)."""
    score, reasons = 0, []

    reductions = listing.get("times_reduced") or (1 if listing.get("reduced_date") or listing.get("is_reduced") else 0)
    if reductions:
        pts = min(reductions * 2, 6)
        score += pts
        reasons.append(f"{reductions}x price reduction ({pts}pts)")

    dom = listing.get("days_on_market") or days_since(listing.get("added_date"))
    if dom is not None:
        if dom >= 180:
            score += 4
            reasons.append(f"{dom} days on market (4pts)")
        elif dom >= 90:
            score += 2
            reasons.append(f"{dom} days on market (2pts)")
    listing["_dom"] = dom

    if listing.get("sale_cancelled_date"):
        score += 5
        reasons.append("previous sale fell through (5pts)")

    if listing.get("first_offer_date") and listing.get("latest_status", "").lower() == "for sale":
        score += 3
        reasons.append("had an offer, back on market (3pts)")

    return score, reasons


def deep_dive_score(listing_id: str) -> tuple[int, list[str]]:
    """Timeline analysis via Market Activity API (1 call). Detects chain
    collapse, withdrawn-and-relisted, and total % price drop.

    Uses GET /property_sale_events/?listing=...&ordering=date which returns all
    events for the listing. Docs: homedata.co.uk/docs/endpoints#market-activity

    Real event types: Added, Added by another agent, Added by new agent,
    Added by another undetermined agent, Reduced, Price Increased,
    Under offer, Sold STC, Sold STCM, Completed, Withdrawn, Sale Cancelled, Let agreed.
    """
    score, reasons = 0, []
    try:
        data = get("/property_sale_events/", {"listing": listing_id, "ordering": "date"}, cost=1)
    except Exception as e:  # noqa: BLE001 — prototype: skip property on any API error
        return 0, [f"deep dive failed: {e}"]

    events = data.get("results", data if isinstance(data, list) else [])
    types = [e.get("event_type", "") for e in events]
    prices = [e.get("price") for e in events if e.get("price")]

    # Chain collapse: Sold STC followed by any later re-listing/reduction
    # Real API event types for re-listing: "Added", "Added by new agent",
    # "Added by another agent", "Added by another undetermined agent"
    RELISTED_TYPES = ("Added", "Reduced", "Added by new agent",
                      "Added by another agent", "Added by another undetermined agent")
    if "Sold STC" in types:
        after = types[types.index("Sold STC") + 1:]
        if any(t in RELISTED_TYPES for t in after):
            score += 5
            reasons.append("chain collapse: Sold STC then back on market (5pts)")

    # Also check "Sale Cancelled" (real event type) as a collapse signal
    if "Sale Cancelled" in types:
        after_cancel = types[types.index("Sale Cancelled") + 1:]
        if any(t in RELISTED_TYPES for t in after_cancel):
            score += 4
            reasons.append("sale cancelled then re-listed (4pts)")

    if "Withdrawn" in types and types and types[-1] not in ("Withdrawn", "Completed"):
        score += 3
        reasons.append("withdrawn then re-listed (3pts)")

    if len(prices) >= 2 and prices[0]:
        drop = (prices[0] - prices[-1]) / prices[0] * 100
        if drop >= 10:
            score += 4
            reasons.append(f"asking price down {drop:.0f}% overall (4pts)")
        elif drop >= 5:
            score += 2
            reasons.append(f"asking price down {drop:.0f}% overall (2pts)")

    return score, reasons


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description="Find motivated sellers in a UK area via Homedata")
    ap.add_argument("--area", default="Bradford", help="Town/city/local authority name")
    ap.add_argument("--postcode", help="Exact postcode(s), comma-separated (skips boundary lookup)")
    ap.add_argument("--max-price", type=int, help="Max asking price GBP")
    ap.add_argument("--min-bedrooms", type=int, help="Min bedrooms")
    ap.add_argument("--min-dom", type=int, default=90, help="Pre-filter: min days on market (default 90)")
    ap.add_argument("--reduced-only", action="store_true", help="Pre-filter: only reduced listings")
    ap.add_argument("--deep-dive", type=int, default=5, help="Timeline analysis for top N (5 API calls each, 0 to disable)")
    ap.add_argument("--top", type=int, default=20, help="How many results to report")
    ap.add_argument("--out", default="motivated_sellers.csv", help="CSV output path")
    args = ap.parse_args()

    # 1. Resolve area to boundary_id (free, no key needed)
    params: dict = {"transaction_type": "Sale", "page_size": 200}
    if args.postcode:
        params["postcode"] = args.postcode
        print(f"Area: postcode(s) {args.postcode}")
    else:
        b = get("/boundaries/autocomplete/", {"q": args.area}, cost=0, auth=True)
        results = b.get("results", [])
        if not results:
            sys.exit(f"No boundary found for '{args.area}'")
        params["boundary_id"] = results[0]["id"]
        print(f"Area: {results[0].get('name', args.area)} (boundary {results[0]['id']})")

    if args.max_price:
        params["max_price"] = args.max_price
    if args.min_bedrooms:
        params["bedrooms"] = args.min_bedrooms
    if args.min_dom:
        params["min_dom"] = args.min_dom
    if args.reduced_only:
        params["reduced_only"] = "true"

    # 2. One wide search — flat 5 calls whether 1 or 200 results
    data = get("/live-listings/search/", params, cost=5)
    listings = data.get("results", [])
    print(f"Fetched {len(listings)} of {data.get('count', '?')} active listings  [API calls so far: {calls_used}]")

    # 3. Score locally (free)
    for l in listings:
        l["_score"], l["_reasons"] = base_score(l)
    listings.sort(key=lambda x: x["_score"], reverse=True)

    # 4. Deep-dive the leaders (5 calls each)
    if args.deep_dive:
        for l in listings[: args.deep_dive]:
            if l.get("id"):
                extra, why = deep_dive_score(l["id"])
                l["_score"] += extra
                l["_reasons"] += why
        listings.sort(key=lambda x: x["_score"], reverse=True)

    # 5. Report
    top = [l for l in listings[: args.top] if l["_score"] > 0]
    print(f"\n{'='*74}\nTOP MOTIVATED-SELLER CANDIDATES - {args.postcode or args.area}\n{'='*74}")
    for i, l in enumerate(top, 1):
        price = f"GBP {l['latest_price']:,}" if l.get("latest_price") else "POA"
        addr = l.get("display_address") or (f"{l['street']}, {l['postcode']}" if l.get("street") else l.get("postcode", "address hidden"))
        prop_type = l.get("property_type") or l.get("listing_property_type") or ""
        print(f"\n{i}. [score {l['_score']}] {addr} - {price}")
        print(f"   {l.get('bedrooms', '?')} bed {prop_type} | "
              f"{l.get('_dom', '?')} days on market | agent: {l.get('agent_name', 'n/a')}")
        for r in l["_reasons"]:
            print(f"   - {r}")

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "score", "address", "price", "bedrooms", "type",
                    "days_on_market", "agent", "uprn", "listing_id", "signals"])
        for i, l in enumerate(top, 1):
            addr = l.get("display_address") or (f"{l['street']}, {l['postcode']}" if l.get("street") else l.get("postcode", "address hidden"))
            prop_type = l.get("property_type") or l.get("listing_property_type") or ""
            w.writerow([i, l["_score"], addr, l.get("latest_price"),
                        l.get("bedrooms"), prop_type, l.get("_dom"),
                        l.get("agent_name"), l.get("property_uprn"), l.get("id"),
                        "; ".join(l["_reasons"])])

    print(f"\nSaved {len(top)} candidates -> {args.out}")
    print(f"Total API calls used this run: ~{calls_used} (free tier: 100/month)")
    print("Full addresses: reveal via Homedata Explore or /listing-address endpoint.")


if __name__ == "__main__":
    main()
