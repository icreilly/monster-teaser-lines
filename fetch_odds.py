#!/usr/bin/env python3
"""
Fetch DraftKings NFL spreads and over/unders from The Odds API and store a
dated snapshot plus a `latest` pointer.

Cost: 2 markets x 1 region = 2 credits per run (~60/month on a daily cron).

Env:
  ODDS_API_KEY   required — your key from https://the-odds-api.com
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_BASE = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
BOOKMAKER = "draftkings"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def fetch_raw(api_key):
    params = {
        "apiKey": api_key,
        "regions": "us",
        "bookmakers": BOOKMAKER,
        "markets": "spreads,totals",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    url = f"{API_BASE}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "nfl-lines/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            quota = {
                "remaining": resp.headers.get("x-requests-remaining"),
                "used": resp.headers.get("x-requests-used"),
                "cost": resp.headers.get("x-requests-last"),
            }
            return body, quota
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        sys.exit(f"API returned HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach the API: {e.reason}")


def normalize(raw):
    """Flatten the API response into one row per game."""
    games = []
    for event in raw:
        book = next(
            (b for b in event.get("bookmakers", []) if b.get("key") == BOOKMAKER),
            None,
        )
        if book is None:
            continue  # DK hasn't posted this game yet

        home, away = event.get("home_team"), event.get("away_team")
        row = {
            "id": event.get("id"),
            "commence_time": event.get("commence_time"),
            "home_team": home,
            "away_team": away,
            "last_update": book.get("last_update"),
            "home_spread": None,
            "home_spread_price": None,
            "away_spread": None,
            "away_spread_price": None,
            "total": None,
            "over_price": None,
            "under_price": None,
        }

        for market in book.get("markets", []):
            key = market.get("key")
            if key == "spreads":
                for o in market.get("outcomes", []):
                    if o.get("name") == home:
                        row["home_spread"] = o.get("point")
                        row["home_spread_price"] = o.get("price")
                    elif o.get("name") == away:
                        row["away_spread"] = o.get("point")
                        row["away_spread_price"] = o.get("price")
            elif key == "totals":
                for o in market.get("outcomes", []):
                    if o.get("name") == "Over":
                        row["total"] = o.get("point")
                        row["over_price"] = o.get("price")
                    elif o.get("name") == "Under":
                        row["under_price"] = o.get("price")

        # Skip games where DK listed the book but posted no numbers yet.
        if row["home_spread"] is None and row["total"] is None:
            continue
        games.append(row)

    games.sort(key=lambda g: (g["commence_time"] or "", g["away_team"] or ""))
    return games


def write_snapshot(games, quota):
    os.makedirs(DATA_DIR, exist_ok=True)
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%d")

    snapshot = {
        "fetched_at": now.isoformat(timespec="seconds"),
        "date": stamp,
        "bookmaker": BOOKMAKER,
        "game_count": len(games),
        "games": games,
    }

    dated_path = os.path.join(DATA_DIR, f"{stamp}.json")
    for path in (dated_path, os.path.join(DATA_DIR, "latest.json")):
        with open(path, "w") as f:
            json.dump(snapshot, f, indent=2)
            f.write("\n")

    # Keep an index of available snapshot dates so the dashboard can diff
    # against yesterday without guessing at filenames.
    dates = sorted(
        f[:-5]
        for f in os.listdir(DATA_DIR)
        if f.endswith(".json") and f[:-5] not in ("latest", "index")
    )
    with open(os.path.join(DATA_DIR, "index.json"), "w") as f:
        json.dump({"dates": dates, "updated_at": snapshot["fetched_at"]}, f, indent=2)
        f.write("\n")

    print(f"Wrote {len(games)} games to data/{stamp}.json and data/latest.json")
    if quota.get("remaining") is not None:
        print(
            f"Credits: {quota['cost']} used this call, "
            f"{quota['remaining']} remaining this month"
        )


def main():
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        sys.exit("ODDS_API_KEY is not set.")

    raw, quota = fetch_raw(api_key)
    games = normalize(raw)

    if not games:
        # Off-season or a gap between slates. Don't overwrite good data
        # with an empty file — just exit cleanly so the cron isn't noisy.
        print("No DraftKings lines returned; leaving existing data untouched.")
        return

    write_snapshot(games, quota)


if __name__ == "__main__":
    main()
