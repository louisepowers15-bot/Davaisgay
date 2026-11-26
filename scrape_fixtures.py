#!/usr/bin/env python3
"""
Scrape today's (or given date) football fixtures from ESPN public scoreboard API.
UK leagues + cups, top European leagues, and major UEFA cups.
"""
from __future__ import annotations

import argparse
import json
import re
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

# ESPN league codes we care about (UK + top Europe + cups)
LEAGUES = [
    # England
    {"code": "eng.1", "name": "Premier League", "region": "UK", "priority": 1},
    {"code": "eng.2", "name": "Championship", "region": "UK", "priority": 2},
    {"code": "eng.3", "name": "League One", "region": "UK", "priority": 3},
    {"code": "eng.4", "name": "League Two", "region": "UK", "priority": 4},
    {"code": "eng.5", "name": "National League", "region": "UK", "priority": 5},
    {"code": "eng.fa", "name": "FA Cup", "region": "UK", "priority": 6},
    {"code": "eng.league_cup", "name": "EFL Cup", "region": "UK", "priority": 7},
    {"code": "eng.trophy", "name": "EFL Trophy", "region": "UK", "priority": 8},
    # Scotland
    {"code": "sco.1", "name": "Scottish Premiership", "region": "UK", "priority": 9},
    {"code": "sco.2", "name": "Scottish Championship", "region": "UK", "priority": 10},
    {"code": "sco.cup", "name": "Scottish Cup", "region": "UK", "priority": 11},
    {"code": "sco.cis", "name": "Scottish League Cup", "region": "UK", "priority": 12},
    # Europe top 5 + NL/PT
    {"code": "esp.1", "name": "La Liga", "region": "Europe", "priority": 20},
    {"code": "ita.1", "name": "Serie A", "region": "Europe", "priority": 21},
    {"code": "ger.1", "name": "Bundesliga", "region": "Europe", "priority": 22},
    {"code": "fra.1", "name": "Ligue 1", "region": "Europe", "priority": 23},
    {"code": "ned.1", "name": "Eredivisie", "region": "Europe", "priority": 24},
    {"code": "por.1", "name": "Primeira Liga", "region": "Europe", "priority": 25},
    # UEFA
    {"code": "uefa.champions", "name": "Champions League", "region": "Europe", "priority": 15},
    {"code": "uefa.europa", "name": "Europa League", "region": "Europe", "priority": 16},
    {"code": "uefa.europa.conf", "name": "Conference League", "region": "Europe", "priority": 17},
]


def parse_date(arg: str) -> date:
    if arg.lower() == "today":
        return date.today()
    if arg.lower() == "tomorrow":
        return date.today() + timedelta(days=1)
    if arg.lower() == "yesterday":
        return date.today() - timedelta(days=1)
    return datetime.strptime(arg, "%Y-%m-%d").date()


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def parse_event(ev: dict, league_meta: dict) -> dict | None:
    comps = ev.get("competitions") or []
    if not comps:
        return None
    c0 = comps[0]
    teams = c0.get("competitors") or []
    home = away = None
    for t in teams:
        side = (t.get("homeAway") or "").lower()
        team = t.get("team") or {}
        rec = t.get("records") or []
        record = ""
        if rec:
            record = rec[0].get("summary") or ""
        form = t.get("form") or ""  # sometimes present
        entry = {
            "name": team.get("displayName") or team.get("name") or "Unknown",
            "short": team.get("abbreviation") or "",
            "id": str(team.get("id") or ""),
            "record": record,
            "form": form,
            "score": t.get("score"),
            "winner": bool(t.get("winner")),
        }
        if side == "home":
            home = entry
        elif side == "away":
            away = entry
    if not home or not away:
        return None

    status = (c0.get("status") or {}).get("type") or {}
    state = status.get("state") or status.get("name") or "pre"  # pre / in / post
    kick = None
    # date in event
    date_str = ev.get("date") or c0.get("date") or ""
    try:
        # 2026-11-26T20:00Z
        if date_str:
            kick_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            kick = kick_dt.strftime("%H:%M")
    except Exception:
        kick = ""

    venue = ((c0.get("venue") or {}).get("fullName")) or ""
    odds = None
    # ESPN sometimes embeds odds
    for od in c0.get("odds") or []:
        if not isinstance(od, dict):
            continue
        details = od.get("details") or ""
        home_odds = od.get("homeTeamOdds") or {}
        away_odds = od.get("awayTeamOdds") or {}
        draw_odds = od.get("drawOdds") or {}
        if not isinstance(home_odds, dict):
            home_odds = {}
        if not isinstance(away_odds, dict):
            away_odds = {}
        if not isinstance(draw_odds, dict):
            draw_odds = {}
        def dec(o):
            # american or decimal?
            if o is None:
                return None
            if isinstance(o, (int, float)):
                return float(o)
            try:
                return float(str(o).replace(",", ""))
            except Exception:
                return None
        # prefer decimal if present
        h = home_odds.get("odds") or home_odds.get("moneyLine")
        a = away_odds.get("odds") or away_odds.get("moneyLine")
        dr = draw_odds.get("odds") or draw_odds.get("moneyLine")
        odds = {
            "home": dec(h) if not isinstance(h, dict) else dec((h or {}).get("decimal") or (h or {}).get("value")),
            "draw": dec(dr) if not isinstance(dr, dict) else dec((dr or {}).get("decimal") or (dr or {}).get("value")),
            "away": dec(a) if not isinstance(a, dict) else dec((a or {}).get("decimal") or (a or {}).get("value")),
            "raw": details,
        }
        break

    return {
        "id": str(ev.get("id") or c0.get("id") or ""),
        "league": league_meta["name"],
        "league_code": league_meta["code"],
        "region": league_meta["region"],
        "priority": league_meta["priority"],
        "kickoff": kick or "",
        "kickoff_iso": date_str,
        "status": state,
        "venue": venue,
        "home": home,
        "away": away,
        "odds": odds,
        "name": f"{home['name']} vs {away['name']}",
    }


def scrape_day(d: date) -> dict:
    date_compact = d.strftime("%Y%m%d")
    leagues_out = []
    all_matches = []
    for meta in LEAGUES:
        url = (
            f"https://site.api.espn.com/apis/site/v2/sports/soccer/"
            f"{meta['code']}/scoreboard?dates={date_compact}"
        )
        try:
            data = fetch_json(url)
        except Exception as e:
            print(f"  skip {meta['code']}: {e}", file=sys.stderr)
            time.sleep(0.2)
            continue
        events = data.get("events") or []
        matches = []
        for ev in events:
            m = parse_event(ev, meta)
            if m:
                matches.append(m)
                all_matches.append(m)
        if matches:
            matches.sort(key=lambda x: x.get("kickoff") or "99:99")
            leagues_out.append({
                "name": meta["name"],
                "code": meta["code"],
                "region": meta["region"],
                "priority": meta["priority"],
                "matches": matches,
            })
            print(f"  {meta['name']}: {len(matches)} matches")
        time.sleep(0.15)

    leagues_out.sort(key=lambda x: x["priority"])
    return {
        "date": d.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "espn_scoreboard",
        "leagues": leagues_out,
        "match_count": len(all_matches),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", default="today")
    args = p.parse_args()
    d = parse_date(args.date)
    print(f"Scraping fixtures for {d.isoformat()}")
    data = scrape_day(d)
    out = ROOT / f"fixtures-{d.isoformat()}.json"
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Wrote {out} leagues={len(data['leagues'])} matches={data['match_count']}")


if __name__ == "__main__":
    main()
