#!/usr/bin/env python3
"""Scrape finished football results for a date from ESPN scoreboard API."""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://www.espn.com/soccer/",
}

# Same league set as fixtures
LEAGUES = [
    {"code": "eng.1", "name": "Premier League", "region": "UK", "priority": 1},
    {"code": "eng.2", "name": "Championship", "region": "UK", "priority": 2},
    {"code": "eng.3", "name": "League One", "region": "UK", "priority": 3},
    {"code": "eng.4", "name": "League Two", "region": "UK", "priority": 4},
    {"code": "eng.5", "name": "National League", "region": "UK", "priority": 5},
    {"code": "eng.fa", "name": "FA Cup", "region": "UK", "priority": 6},
    {"code": "eng.league_cup", "name": "EFL Cup", "region": "UK", "priority": 7},
    {"code": "eng.trophy", "name": "EFL Trophy", "region": "UK", "priority": 8},
    {"code": "sco.1", "name": "Scottish Premiership", "region": "UK", "priority": 9},
    {"code": "sco.2", "name": "Scottish Championship", "region": "UK", "priority": 10},
    {"code": "sco.cis", "name": "Scottish League Cup", "region": "UK", "priority": 12},
    {"code": "esp.1", "name": "La Liga", "region": "Europe", "priority": 20},
    {"code": "ita.1", "name": "Serie A", "region": "Europe", "priority": 21},
    {"code": "ger.1", "name": "Bundesliga", "region": "Europe", "priority": 22},
    {"code": "fra.1", "name": "Ligue 1", "region": "Europe", "priority": 23},
    {"code": "ned.1", "name": "Eredivisie", "region": "Europe", "priority": 24},
    {"code": "por.1", "name": "Primeira Liga", "region": "Europe", "priority": 25},
    {"code": "uefa.champions", "name": "Champions League", "region": "Europe", "priority": 15},
    {"code": "uefa.europa", "name": "Europa League", "region": "Europe", "priority": 16},
    {"code": "uefa.europa.conf", "name": "Conference League", "region": "Europe", "priority": 17},
]


def parse_date(arg: str) -> date:
    if arg.lower() == "today":
        return date.today()
    if arg.lower() == "yesterday":
        return date.today() - timedelta(days=1)
    return datetime.strptime(arg, "%Y-%m-%d").date()


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def parse_result(ev: dict, league_meta: dict) -> dict | None:
    comps = ev.get("competitions") or []
    if not comps:
        return None
    c0 = comps[0]
    status = (c0.get("status") or {}).get("type") or {}
    completed = bool(status.get("completed")) or (status.get("state") or "").lower() == "post"
    if not completed:
        return None  # only finished games

    home = away = None
    home_score = away_score = None
    for t in c0.get("competitors") or []:
        side = (t.get("homeAway") or "").lower()
        team = t.get("team") or {}
        try:
            score = int(t.get("score")) if t.get("score") is not None else None
        except (TypeError, ValueError):
            score = None
        entry = {
            "name": team.get("displayName") or team.get("name") or "Unknown",
            "id": str(team.get("id") or ""),
            "score": score,
            "winner": bool(t.get("winner")),
        }
        if side == "home":
            home, home_score = entry, score
        elif side == "away":
            away, away_score = entry, score
    if not home or not away or home_score is None or away_score is None:
        return None

    if home_score > away_score:
        outcome = "home"
    elif away_score > home_score:
        outcome = "away"
    else:
        outcome = "draw"

    date_str = ev.get("date") or c0.get("date") or ""
    kick = ""
    try:
        if date_str:
            kick = datetime.fromisoformat(date_str.replace("Z", "+00:00")).strftime("%H:%M")
    except Exception:
        pass

    return {
        "id": str(ev.get("id") or c0.get("id") or ""),
        "league": league_meta["name"],
        "league_code": league_meta["code"],
        "region": league_meta["region"],
        "kickoff": kick,
        "kickoff_iso": date_str,
        "home": home,
        "away": away,
        "home_score": home_score,
        "away_score": away_score,
        "outcome": outcome,  # home | draw | away
        "name": f"{home['name']} vs {away['name']}",
        "ft": f"{home_score}-{away_score}",
    }


def scrape_day(d: date) -> dict:
    date_compact = d.strftime("%Y%m%d")
    matches = []
    by_league = []
    for meta in LEAGUES:
        url = (
            f"https://site.api.espn.com/apis/site/v2/sports/soccer/"
            f"{meta['code']}/scoreboard?dates={date_compact}"
        )
        try:
            data = fetch_json(url)
        except Exception as e:
            print(f"  skip {meta['code']}: {e}", file=sys.stderr)
            time.sleep(0.15)
            continue
        league_matches = []
        for ev in data.get("events") or []:
            m = parse_result(ev, meta)
            if m:
                league_matches.append(m)
                matches.append(m)
        if league_matches:
            by_league.append({
                "name": meta["name"],
                "code": meta["code"],
                "region": meta["region"],
                "matches": league_matches,
            })
            print(f"  {meta['name']}: {len(league_matches)} finished")
        time.sleep(0.12)

    return {
        "date": d.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "espn_scoreboard",
        "leagues": by_league,
        "matches": matches,
        "match_count": len(matches),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="yesterday")
    args = ap.parse_args()
    d = parse_date(args.date)
    print(f"Scraping results for {d.isoformat()}")
    data = scrape_day(d)
    out = ROOT / f"results-{d.isoformat()}.json"
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Wrote {out} finished={data['match_count']}")


if __name__ == "__main__":
    main()
