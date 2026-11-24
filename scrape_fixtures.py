#!/usr/bin/env python3
"""
Scrape UK + major European football fixtures from ESPN public API (no key).
Covers: English pyramid top 4, Scottish Prem, big-5 Europe, UCL/UEL/UECL, domestic cups when listed.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://www.espn.com/",
}

# ESPN soccer league path codes
LEAGUES = [
    ("eng.1", "Premier League", "England"),
    ("eng.2", "Championship", "England"),
    ("eng.3", "League One", "England"),
    ("eng.4", "League Two", "England"),
    ("eng.fa", "FA Cup", "England"),
    ("eng.league_cup", "EFL Cup", "England"),
    ("sco.1", "Scottish Premiership", "Scotland"),
    ("sco.cup", "Scottish Cup", "Scotland"),
    ("esp.1", "La Liga", "Spain"),
    ("ita.1", "Serie A", "Italy"),
    ("ger.1", "Bundesliga", "Germany"),
    ("fra.1", "Ligue 1", "France"),
    ("ned.1", "Eredivisie", "Netherlands"),
    ("por.1", "Primeira Liga", "Portugal"),
    ("uefa.champions", "Champions League", "Europe"),
    ("uefa.europa", "Europa League", "Europe"),
    ("uefa.europa.conf", "Conference League", "Europe"),
]


def parse_date(arg: str) -> date:
    if arg.lower() == "today":
        return date.today()
    if arg.lower() == "tomorrow":
        return date.today() + timedelta(days=1)
    return datetime.strptime(arg, "%Y-%m-%d").date()


def fetch_scoreboard(league_code: str, day: date) -> list[dict]:
    ds = day.strftime("%Y%m%d")
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/scoreboard?dates={ds}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200 or not r.text:
            print(f"  empty/HTTP {r.status_code} {league_code} {day}")
            return []
        data = r.json()
    except Exception as e:
        print(f"  fail {league_code} {day}: {e}")
        return []

    out = []
    for ev in data.get("events") or []:
        comps = ev.get("competitions") or []
        if not comps:
            continue
        c0 = comps[0]
        status = (c0.get("status") or {}).get("type") or {}
        if status.get("completed"):
            continue  # only upcoming / in-play for tips board; skip finished
        state = (status.get("state") or "").lower()
        # include pre and in
        competitors = c0.get("competitors") or []
        home = next((x for x in competitors if x.get("homeAway") == "home"), None)
        away = next((x for x in competitors if x.get("homeAway") == "away"), None)
        if not home or not away:
            continue

        def pack(side):
            team = side.get("team") or {}
            rec = ""
            for r0 in side.get("records") or []:
                if r0.get("type") == "total" or r0.get("name") == "All Splits":
                    rec = r0.get("summary") or ""
                    break
            if not rec and side.get("records"):
                rec = (side.get("records") or [{}])[0].get("summary") or ""
            return {
                "id": str(team.get("id") or side.get("id") or ""),
                "name": team.get("displayName") or team.get("name") or "Unknown",
                "short": team.get("abbreviation") or team.get("shortDisplayName") or "",
                "form": (side.get("form") or "").strip().upper(),
                "record": rec,  # W-D-L style summary from ESPN
            }

        venue = c0.get("venue") or {}
        addr = venue.get("address") or {}
        out.append({
            "id": str(ev.get("id") or c0.get("id") or ""),
            "date": (ev.get("date") or "")[:10],
            "kickoff": ev.get("date") or "",
            "league_code": league_code,
            "status": status.get("name") or "",
            "status_detail": status.get("shortDetail") or status.get("description") or "",
            "home": pack(home),
            "away": pack(away),
            "venue": venue.get("fullName") or "",
            "city": addr.get("city") or "",
            "country": addr.get("country") or "",
        })
    return out


def fetch_recent_results(league_code: str, team_id: str, limit: int = 8) -> list[dict]:
    """Recent results for a team in this league (for H2H/form depth)."""
    if not team_id:
        return []
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/teams/{team_id}/schedule"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []
    results = []
    for ev in data.get("events") or []:
        comps = ev.get("competitions") or []
        if not comps:
            continue
        c0 = comps[0]
        st = (c0.get("status") or {}).get("type") or {}
        if not st.get("completed"):
            continue
        competitors = c0.get("competitors") or []
        home = next((x for x in competitors if x.get("homeAway") == "home"), None)
        away = next((x for x in competitors if x.get("homeAway") == "away"), None)
        if not home or not away:
            continue
        try:
            hs = int(float(home.get("score") or 0))
            as_ = int(float(away.get("score") or 0))
        except Exception:
            continue
        results.append({
            "date": (ev.get("date") or "")[:10],
            "home": (home.get("team") or {}).get("displayName") or "",
            "away": (away.get("team") or {}).get("displayName") or "",
            "home_id": str((home.get("team") or {}).get("id") or ""),
            "away_id": str((away.get("team") or {}).get("id") or ""),
            "score": [hs, as_],
            "home_win": home.get("winner") is True,
            "away_win": away.get("winner") is True,
        })
    results.sort(key=lambda x: x["date"], reverse=True)
    return results[:limit]


def enrich_match(m: dict) -> dict:
    """Attach recent results + simple H2H from both teams' schedules."""
    lc = m["league_code"]
    hid, aid = m["home"]["id"], m["away"]["id"]
    home_res = fetch_recent_results(lc, hid, 8)
    time.sleep(0.15)
    away_res = fetch_recent_results(lc, aid, 8)
    time.sleep(0.15)
    m["home"]["recent"] = home_res
    m["away"]["recent"] = away_res
    # H2H: matches in either list involving both teams
    h2h = []
    for res in home_res + away_res:
        teams = {res.get("home_id"), res.get("away_id")}
        if hid in teams and aid in teams:
            key = (res["date"], res["home"], res["away"])
            if key not in {(x["date"], x["home"], x["away"]) for x in h2h}:
                h2h.append(res)
    h2h.sort(key=lambda x: x["date"], reverse=True)
    m["h2h"] = h2h[:5]
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="today")
    ap.add_argument("--days", type=int, default=3, help="Include this many days starting from --date")
    ap.add_argument("--enrich", action="store_true", help="Fetch recent results + H2H (slower)")
    args = ap.parse_args()
    start = parse_date(args.date)
    days = [start + timedelta(days=i) for i in range(max(1, args.days))]

    matches = []
    for day in days:
        print(f"Day {day.isoformat()}")
        for code, name, region in LEAGUES:
            batch = fetch_scoreboard(code, day)
            for m in batch:
                m["league"] = name
                m["region"] = region
            if batch:
                print(f"  {name}: {len(batch)}")
            matches.extend(batch)
            time.sleep(0.12)

    if args.enrich:
        print(f"Enriching {len(matches)} matches with recent form/H2H…")
        matches = [enrich_match(m) for m in matches]

    # de-dupe by id
    seen = set()
    uniq = []
    for m in matches:
        if m["id"] in seen:
            continue
        seen.add(m["id"])
        uniq.append(m)

    out = {
        "start_date": start.isoformat(),
        "days": args.days,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "source": "espn",
        "match_count": len(uniq),
        "matches": uniq,
    }
    path = ROOT / f"fixtures-{start.isoformat()}.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {path} matches={len(uniq)}")


if __name__ == "__main__":
    main()
