#!/usr/bin/env python3
"""
Flight Price Tracker — Travelpayouts Edition
Free API, no monthly request cap for personal/non-commercial use.
Routes: India -> Colombo, Seoul, Tokyo, Guangzhou, Bali
Filters: Full-service carriers preferred, layovers < 8hrs, alerts on unusually low prices
"""

import requests
import json
import os
from datetime import datetime

# Config
TRAVELPAYOUTS_TOKEN = "e24da4a9785dc508f1212ed815f30906"
BASELINE_FILE = "price_baseline.json"
BASE_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"

ROUTES = [
    ("BLR", "CMB", "Bengaluru -> Colombo"),
    ("MAA", "CMB", "Chennai -> Colombo"),
    ("BLR", "ICN", "Bengaluru -> Seoul"),
    ("MAA", "ICN", "Chennai -> Seoul"),
    ("BLR", "NRT", "Bengaluru -> Tokyo"),
    ("MAA", "NRT", "Chennai -> Tokyo"),
    ("BLR", "CAN", "Bengaluru -> Guangzhou"),
    ("MAA", "CAN", "Chennai -> Guangzhou"),
    ("CCU", "CAN", "Kolkata -> Guangzhou"),
    ("BLR", "DPS", "Bengaluru -> Bali"),
    ("MAA", "DPS", "Chennai -> Bali"),
    ("CCU", "DPS", "Kolkata -> Bali"),
]

FULL_SERVICE = {"AI", "UL", "KE", "OZ", "JL", "NH", "CZ", "CA", "MH", "SQ", "EK", "QR", "EY"}
MAX_LAYOVER_HOURS = 8
LOW_PRICE_THRESHOLD = 0.80

DIRECT_FLY_HRS = {
    ("BLR", "CMB"): 1.5, ("MAA", "CMB"): 1.5,
    ("BLR", "ICN"): 7.0, ("MAA", "ICN"): 7.0,
    ("BLR", "NRT"): 9.0, ("MAA", "NRT"): 9.0,
    ("BLR", "CAN"): 6.5, ("MAA", "CAN"): 6.0, ("CCU", "CAN"): 4.5,
    ("BLR", "DPS"): 7.0, ("MAA", "DPS"): 7.0, ("CCU", "DPS"): 6.0,
}

SEARCH_MONTHS = ["2026-10", "2026-11"]
CUTOFF_DATE = "2026-11-20"


def load_baseline():
    if os.path.exists(BASELINE_FILE):
        with open(BASELINE_FILE) as f:
            return json.load(f)
    return {}


def save_baseline(bl):
    with open(BASELINE_FILE, "w") as f:
        json.dump(bl, f, indent=2)


def layover_ok(ticket):
    if ticket.get("transfers", 0) == 0:
        return True
    duration_hrs = ticket.get("duration", 0) / 60
    origin = ticket.get("origin", "")
    dest = ticket.get("destination", "")
    direct_hrs = DIRECT_FLY_HRS.get((origin, dest), 5.0)
    return (duration_hrs - direct_hrs) <= MAX_LAYOVER_HOURS


def within_date_range(ticket):
    dep = ticket.get("departure_at", "")
    if not dep:
        return True
    return dep[:10] <= CUTOFF_DATE


def search_route(origin, dest, month):
    params = {
        "origin": origin,
        "destination": dest,
        "departure_at": month,
        "one_way": "true",
        "currency": "inr",
        "market": "in",
        "sorting": "price",
        "limit": 30,
    }
    headers = {"x-access-token": TRAVELPAYOUTS_TOKEN}
    resp = requests.get(BASE_URL, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        return []
    return data.get("data", [])


def analyze_routes():
    baseline = load_baseline()
    results = []

    print("\n" + "="*60)
    print(f"Flight Tracker -- {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Checking {len(ROUTES)} routes | Oct 1 - Nov 20, 2026")
    print("="*60 + "\n")

    for origin, dest, label in ROUTES:
        route_key = f"{origin}-{dest}"
        all_tickets = []

        for month in SEARCH_MONTHS:
            print(f"  Fetching {label} [{month}]...", end=" ", flush=True)
            try:
                tickets = search_route(origin, dest, month)
            except Exception as e:
                print(f"Error: {e}")
                continue
            tickets = [t for t in tickets if within_date_range(t) and layover_ok(t)]
            all_tickets.extend(tickets)
            print(f"{len(tickets)} results")

        if not all_tickets:
            print(f"  No results for {label}\n")
            continue

        fs_tickets = [t for t in all_tickets if t.get("airline", "") in FULL_SERVICE]
        candidates = fs_tickets if fs_tickets else all_tickets
        is_fs = bool(fs_tickets)

        best = min(candidates, key=lambda t: t["price"])
        best_price = best["price"]
        best_date = best.get("departure_at", "")[:10]
        airline = best.get("airline", "?")
        stops = best.get("transfers", 0)
        duration = best.get("duration", 0)

        all_prices = [t["price"] for t in candidates]
        avg_price = sum(all_prices) / len(all_prices)

        if route_key in baseline:
            baseline[route_key] = baseline[route_key] * 0.7 + avg_price * 0.3
        else:
            baseline[route_key] = avg_price

        is_low = best_price <= baseline[route_key] * LOW_PRICE_THRESHOLD
        stops_str = "Direct" if stops == 0 else f"{stops} stop"
        dur_str = f"{duration // 60}h{duration % 60:02d}m" if duration else "?"
        fs_tag = "[FS]" if is_fs else "[LCC]"
        low_tag = " LOW" if is_low else ""

        print(f"  Best: {best_price:,} INR on {best_date} | {airline} | {stops_str} | {dur_str} {fs_tag}{low_tag}\n")

        results.append({
            "route": label,
            "best_price": best_price,
            "best_date": best_date,
            "airline": airline,
            "stops": stops,
            "duration_mins": duration,
            "baseline": baseline[route_key],
            "is_unusually_low": is_low,
            "full_service": is_fs,
        })

    save_baseline(baseline)

    print("\n" + "="*60)
    print("SUMMARY -- Best Fares (Oct 1 - Nov 20, 2026)")
    print("="*60 + "\n")

    alerts = [r for r in results if r["is_unusually_low"]]
    if alerts:
        print("UNUSUALLY LOW PRICES:")
        for r in sorted(alerts, key=lambda x: x["best_price"]):
            pct = round((1 - r["best_price"] / r["baseline"]) * 100)
            fs_tag = "[FS]" if r["full_service"] else "[LCC]"
            print(f"  {r['route']}: {r['best_price']:,} INR on {r['best_date']} {fs_tag} -- {pct}% below baseline")
        print()

    print("All routes (sorted by price):")
    for r in sorted(results, key=lambda x: x["best_price"]):
        flag = "LOW " if r["is_unusually_low"] else "    "
        fs_tag = "[FS]" if r["full_service"] else "[LCC]"
        stops_str = "Direct" if r["stops"] == 0 else f"{r['stops']}stop"
        dur_str = f"{r['duration_mins']//60}h{r['duration_mins']%60:02d}m" if r["duration_mins"] else "?"
        print(f"  {flag}{r['route']}: {r['best_price']:,} INR on {r['best_date']} | {r['airline']} | {stops_str} | {dur_str} {fs_tag}")

    print(f"\nBaseline saved to {BASELINE_FILE}")
    return results


if __name__ == "__main__":
    analyze_routes()
