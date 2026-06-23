#!/usr/bin/env python3
"""
Flight Price Tracker
Routes: India → Colombo, Seoul, Tokyo, Guangzhou
Filters: Full-service carriers first, layovers < 8hrs, unusually low prices
"""

import requests
import json
import time
import os
from datetime import datetime, date, timedelta

# ── Config ────────────────────────────────────────────────────────────────────
RAPIDAPI_KEY = "5c1381f79cmsh4dc355afa7b6359p12ba40jsn1ff2296fb33f"
BASELINE_FILE = "price_baseline.json"

ROUTES = [
    # (origin, destination, label)
    ("BLR", "CMB", "Bengaluru → Colombo"),
    ("MAA", "CMB", "Chennai → Colombo"),
    ("BLR", "ICN", "Bengaluru → Seoul"),
    ("MAA", "ICN", "Chennai → Seoul"),
    ("BLR", "NRT", "Bengaluru → Tokyo"),
    ("MAA", "NRT", "Chennai → Tokyo"),
    ("BLR", "CAN", "Bengaluru → Guangzhou"),
    ("MAA", "CAN", "Chennai → Guangzhou"),
    ("CCU", "CAN", "Kolkata → Guangzhou"),
    ("BLR", "DPS", "Bengaluru → Bali"),
    ("MAA", "DPS", "Chennai → Bali"),
    ("CCU", "DPS", "Kolkata → Bali"),
]

# Full-service carriers (IATA codes) — ranked by preference
FULL_SERVICE_CARRIERS = {
    "AI",  # Air India
    "UL",  # SriLankan Airlines
    "KE",  # Korean Air
    "OZ",  # Asiana Airlines
    "JL",  # Japan Airlines
    "NH",  # ANA
    "CZ",  # China Southern
    "CA",  # Air China
    "MH",  # Malaysia Airlines
    "SQ",  # Singapore Airlines
    "EK",  # Emirates
    "QR",  # Qatar Airways
    "EY",  # Etihad
}

MAX_LAYOVER_HOURS = 8
LOW_PRICE_THRESHOLD = 0.80  # Alert if price is 80% or less of baseline

HEADERS = {
    "x-rapidapi-host": "flights-sky.p.rapidapi.com",
    "x-rapidapi-key": RAPIDAPI_KEY,
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_baseline():
    if os.path.exists(BASELINE_FILE):
        with open(BASELINE_FILE) as f:
            return json.load(f)
    return {}

def save_baseline(baseline):
    with open(BASELINE_FILE, "w") as f:
        json.dump(baseline, f, indent=2)

def get_sample_dates(n=5):
    """Return n evenly spaced dates between Oct 1 and Nov 20."""
    start = date(2026, 10, 1)
    end = date(2026, 11, 20)
    total_days = (end - start).days
    step = total_days // (n - 1)
    return [(start + timedelta(days=i * step)).isoformat() for i in range(n)]

def search_flights(origin, destination, depart_date):
    """Two-step API call: initiate then poll."""
    url = "https://flights-sky.p.rapidapi.com/flights/search-one-way"
    params = {
        "fromEntityId": origin,
        "toEntityId": destination,
        "departDate": depart_date,
        "currency": "INR",
        "cabinClass": "economy",
    }

    # Step 1: initiate
    resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if not data.get("status"):
        return []

    session_id = data["data"]["context"].get("sessionId")
    itineraries = data["data"].get("itineraries", [])

    # Step 2: poll if incomplete
    if session_id and data["data"]["context"]["status"] == "incomplete":
        time.sleep(2)
        params["sessionId"] = session_id
        resp2 = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if resp2.status_code == 200:
            data2 = resp2.json()
            if data2.get("status"):
                itineraries = data2["data"].get("itineraries", [])

    return itineraries

def get_carrier_codes(leg):
    codes = set()
    for c in leg.get("carriers", {}).get("marketing", []):
        codes.add(c.get("alternateId", ""))
    return codes

def is_full_service(itinerary):
    for leg in itinerary.get("legs", []):
        codes = get_carrier_codes(leg)
        if codes & FULL_SERVICE_CARRIERS:
            return True
    return False

def max_layover_ok(itinerary):
    """Check that no single layover exceeds MAX_LAYOVER_HOURS."""
    for leg in itinerary.get("legs", []):
        stops = leg.get("stopCount", 0)
        if stops == 0:
            continue
        # Total duration minus flying time heuristic not available per-stop,
        # so use total leg duration as proxy: if > 8h likely has long layover
        # We flag anything where total duration exceeds flight time significantly
        duration_hrs = leg.get("durationInMinutes", 0) / 60
        # For direct flights, durationInMinutes is pure fly time
        # For connecting, it includes layover — cap at 8h + expected flight time
        # Route-specific rough fly times (hours)
        origin = leg.get("origin", {}).get("id", "")
        dest = leg.get("destination", {}).get("id", "")
        direct_fly = {
            ("BLR", "CMB"): 1.5, ("MAA", "CMB"): 1.5,
            ("BLR", "ICN"): 7.0, ("MAA", "ICN"): 7.0,
            ("BLR", "NRT"): 9.0, ("MAA", "NRT"): 9.0,
            ("BLR", "CAN"): 6.5, ("MAA", "CAN"): 6.0, ("CCU", "CAN"): 4.5,
            ("BLR", "DPS"): 7.0, ("MAA", "DPS"): 7.0, ("CCU", "DPS"): 6.0,
        }.get((origin, dest), 5.0)
        layover_hrs = duration_hrs - direct_fly
        if layover_hrs > MAX_LAYOVER_HOURS:
            return False
    return True

def analyze_routes():
    baseline = load_baseline()
    results = []
    sample_dates = get_sample_dates(5)

    print(f"\n{'='*60}")
    print(f"Flight Tracker — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Checking {len(ROUTES)} routes across {len(sample_dates)} dates")
    print(f"{'='*60}\n")

    for origin, dest, label in ROUTES:
        route_key = f"{origin}-{dest}"
        best_price = None
        best_itinerary = None
        best_date = None
        all_prices = []

        for depart_date in sample_dates:
            print(f"  Searching {label} on {depart_date}...", end=" ", flush=True)
            try:
                itineraries = search_flights(origin, dest, depart_date)
            except Exception as e:
                print(f"Error: {e}")
                continue

            # Filter: full-service first, then any; layover < 8h
            filtered = [i for i in itineraries if max_layover_ok(i)]
            fs = [i for i in filtered if is_full_service(i)]
            candidates = fs if fs else filtered

            if not candidates:
                print("no results")
                continue

            cheapest = min(candidates, key=lambda x: x["price"]["raw"])
            price = cheapest["price"]["raw"]
            all_prices.append(price)
            print(f"₹{price:,.0f} {'[FS]' if cheapest in fs else '[LCC]'}")

            if best_price is None or price < best_price:
                best_price = price
                best_itinerary = cheapest
                best_date = depart_date

            time.sleep(0.5)  # rate limit courtesy

        if not all_prices:
            continue

        avg_price = sum(all_prices) / len(all_prices)

        # Update baseline (rolling average)
        if route_key in baseline:
            baseline[route_key] = (baseline[route_key] * 0.7) + (avg_price * 0.3)
        else:
            baseline[route_key] = avg_price

        is_low = best_price <= baseline[route_key] * LOW_PRICE_THRESHOLD
        leg = best_itinerary["legs"][0]
        carrier_names = [c.get("name", "") for c in leg.get("carriers", {}).get("marketing", [])]
        stops = leg.get("stopCount", 0)
        duration = leg.get("durationInMinutes", 0)

        result = {
            "route": label,
            "best_price": best_price,
            "best_date": best_date,
            "baseline": baseline[route_key],
            "is_unusually_low": is_low,
            "carriers": ", ".join(carrier_names),
            "stops": stops,
            "duration_mins": duration,
            "full_service": is_full_service(best_itinerary),
        }
        results.append(result)

    save_baseline(baseline)

    # ── Print Summary ──────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("SUMMARY — Best Fares Found")
    print(f"{'='*60}")

    alerts = [r for r in results if r["is_unusually_low"]]
    normal = [r for r in results if not r["is_unusually_low"]]

    if alerts:
        print("\n🔥 UNUSUALLY LOW PRICES:")
        for r in sorted(alerts, key=lambda x: x["best_price"]):
            stops_str = "Direct" if r["stops"] == 0 else f"{r['stops']} stop"
            fs_tag = "[Full-Service]" if r["full_service"] else "[LCC]"
            print(f"  {r['route']}")
            print(f"    ₹{r['best_price']:,.0f} on {r['best_date']} {fs_tag}")
            print(f"    {r['carriers']} | {stops_str} | {r['duration_mins']//60}h{r['duration_mins']%60}m")
            print(f"    Baseline avg: ₹{r['baseline']:,.0f} — {round((1 - r['best_price']/r['baseline'])*100)}% below")

    print("\nAll routes:")
    for r in sorted(results, key=lambda x: x["best_price"]):
        flag = "⚡" if r["is_unusually_low"] else "  "
        fs_tag = "[FS]" if r["full_service"] else "[LCC]"
        print(f"  {flag} {r['route']}: ₹{r['best_price']:,.0f} on {r['best_date']} {fs_tag}")

    print(f"\nBaseline updated: {BASELINE_FILE}")
    return results

if __name__ == "__main__":
    analyze_routes()
