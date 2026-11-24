#!/usr/bin/env python3
"""
Football fixtures + team context from ESPN (no API key).

Primary signals stored per side:
  - last 5 home games (for home team) / last 5 away (for away team): GF, GA, CS
  - overall recent form (smaller weight later in tips)
  - league position from standings
  - avg corners / yellow cards / shots from recent match summaries (when available)
  - H2H if both appear in recent schedules
Injuries: stored when ESPN injuries feed is non-empty (often empty).
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

LEAGUES = [
    ("eng.1", "Premier League", "England"),
    ("eng.2", "Championship", "England"),
    ("eng.3", "League One", "England"),
    ("eng.4", "League Two", "England"),
    ("eng.fa", "FA Cup", "England"),
    ("eng.league_cup", "EFL Cup", "England"),
    ("sco.1", "Scottish Premiership", "Scotland"),
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

_SUMMARY_CACHE: dict[str, dict] = {}
_STANDINGS_CACHE: dict[str, dict] = {}


def parse_date(arg: str) -> date:
    if arg.lower() == "today":
        return date.today()
    if arg.lower() == "tomorrow":
        return date.today() + timedelta(days=1)
    return datetime.strptime(arg, "%Y-%m-%d").date()


def score_value(side: dict) -> int | None:
    sc = side.get("score")
    if sc is None:
        return None
    if isinstance(sc, dict):
        try:
            return int(float(sc.get("value") if sc.get("value") is not None else sc.get("displayValue") or 0))
        except Exception:
            return None
    try:
        return int(float(sc))
    except Exception:
        return None


def fetch_scoreboard(league_code: str, day: date) -> list[dict]:
    ds = day.strftime("%Y%m%d")
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/scoreboard?dates={ds}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200 or not r.text:
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
            continue
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
                "short": team.get("abbreviation") or "",
                "form": (side.get("form") or "").strip().upper(),
                "record": rec,
            }

        venue = c0.get("venue") or {}
        out.append({
            "id": str(ev.get("id") or c0.get("id") or ""),
            "date": (ev.get("date") or "")[:10],
            "kickoff": ev.get("date") or "",
            "league_code": league_code,
            "status": status.get("name") or "",
            "home": pack(home),
            "away": pack(away),
            "venue": venue.get("fullName") or "",
        })
    return out


def fetch_standings(league_code: str) -> dict[str, dict]:
    """team_id -> {rank, points, played, gf, ga, name}"""
    if league_code in _STANDINGS_CACHE:
        return _STANDINGS_CACHE[league_code]
    url = f"https://site.api.espn.com/apis/v2/sports/soccer/{league_code}/standings"
    out = {}
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200 or not r.text:
            _STANDINGS_CACHE[league_code] = {}
            return {}
        data = r.json()
    except Exception:
        _STANDINGS_CACHE[league_code] = {}
        return {}

    def walk(node):
        if isinstance(node, dict):
            entries = None
            if isinstance(node.get("standings"), dict):
                entries = node["standings"].get("entries")
            if entries:
                for e in entries:
                    team = e.get("team") or {}
                    tid = str(team.get("id") or "")
                    stats = {s.get("name"): s for s in (e.get("stats") or [])}
                    def val(name, default=None):
                        s = stats.get(name) or {}
                        v = s.get("value")
                        if v is None:
                            v = s.get("displayValue")
                        return v if v is not None else default
                    try:
                        rank = int(float(val("rank") or 0))
                    except Exception:
                        rank = None
                    out[tid] = {
                        "rank": rank,
                        "points": val("points"),
                        "played": val("gamesPlayed"),
                        "gf": val("pointsFor"),
                        "ga": val("pointsAgainst"),
                        "name": team.get("displayName") or "",
                    }
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(data)
    _STANDINGS_CACHE[league_code] = out
    return out


def fetch_team_matches(league_code: str, team_id: str, limit: int = 15) -> list[dict]:
    if not team_id:
        return []
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/teams/{team_id}/schedule"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200 or not r.text:
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
        hs, as_ = score_value(home), score_value(away)
        if hs is None or as_ is None:
            continue
        results.append({
            "event_id": str(ev.get("id") or c0.get("id") or ""),
            "date": (ev.get("date") or "")[:10],
            "home": (home.get("team") or {}).get("displayName") or "",
            "away": (away.get("team") or {}).get("displayName") or "",
            "home_id": str((home.get("team") or {}).get("id") or ""),
            "away_id": str((away.get("team") or {}).get("id") or ""),
            "score": [hs, as_],
        })
    results.sort(key=lambda x: x["date"], reverse=True)
    return results[:limit]


def fetch_match_team_stats(league_code: str, event_id: str) -> dict[str, dict]:
    """Return {team_id: {corners, yellows, shots, shots_on}} for a completed event."""
    if not event_id:
        return {}
    if event_id in _SUMMARY_CACHE:
        return _SUMMARY_CACHE[event_id]
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/summary?event={event_id}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200 or not r.text:
            _SUMMARY_CACHE[event_id] = {}
            return {}
        data = r.json()
    except Exception:
        _SUMMARY_CACHE[event_id] = {}
        return {}

    out = {}
    for t in ((data.get("boxscore") or {}).get("teams") or []):
        team = t.get("team") or {}
        tid = str(team.get("id") or "")
        stats = {s.get("name"): s for s in (t.get("statistics") or [])}
        def num(name):
            s = stats.get(name) or {}
            v = s.get("value")
            if v is None:
                v = s.get("displayValue")
            try:
                return float(v)
            except Exception:
                return None
        out[tid] = {
            "corners": num("wonCorners"),
            "yellows": num("yellowCards"),
            "reds": num("redCards"),
            "shots": num("totalShots"),
            "shots_on": num("shotsOnTarget"),
            "possession": num("possessionPct"),
            "fouls": num("foulsCommitted"),
        }
    _SUMMARY_CACHE[event_id] = out
    return out


def venue_form(matches: list[dict], team_id: str, venue: str, limit: int = 5, league_code: str = "") -> dict:
    """
    venue: 'H' or 'A'
    Aggregate goals + optional corners/cards/shots from match summaries.
    """
    rows = []
    for m in matches:
        if venue == "H" and m.get("home_id") == team_id:
            rows.append((m["score"][0], m["score"][1], m))
        elif venue == "A" and m.get("away_id") == team_id:
            rows.append((m["score"][1], m["score"][0], m))
        if len(rows) >= limit:
            break

    if not rows:
        return {
            "played": 0, "gf_avg": None, "ga_avg": None, "cs": 0,
            "corners_avg": None, "yellows_avg": None, "shots_avg": None, "shots_on_avg": None,
            "results": [],
        }

    gf = [r[0] for r in rows]
    ga = [r[1] for r in rows]
    cs = sum(1 for x in ga if x == 0)

    corners, yellows, shots, shots_on = [], [], [], []
    for _, _, m in rows[:limit]:
        eid = m.get("event_id")
        if not eid or not league_code:
            continue
        st = fetch_match_team_stats(league_code, eid)
        time.sleep(0.08)
        mine = st.get(team_id) or {}
        if mine.get("corners") is not None:
            corners.append(mine["corners"])
        if mine.get("yellows") is not None:
            yellows.append(mine["yellows"])
        if mine.get("shots") is not None:
            shots.append(mine["shots"])
        if mine.get("shots_on") is not None:
            shots_on.append(mine["shots_on"])

    def avg(xs):
        return round(sum(xs) / len(xs), 2) if xs else None

    return {
        "played": len(rows),
        "gf_avg": round(sum(gf) / len(gf), 2),
        "ga_avg": round(sum(ga) / len(ga), 2),
        "cs": cs,
        "corners_avg": avg(corners),
        "yellows_avg": avg(yellows),
        "shots_avg": avg(shots),
        "shots_on_avg": avg(shots_on),
        "results": [{"date": r[2]["date"], "gf": r[0], "ga": r[1], "opp": r[2]["away"] if venue == "H" else r[2]["home"]} for r in rows],
    }


def overall_stats(matches: list[dict], team_id: str, limit: int = 5) -> dict:
    rows = []
    for m in matches[:limit]:
        if m.get("home_id") == team_id:
            rows.append((m["score"][0], m["score"][1]))
        elif m.get("away_id") == team_id:
            rows.append((m["score"][1], m["score"][0]))
    if not rows:
        return {"played": 0, "gf_avg": None, "ga_avg": None, "cs": 0, "pts_rate": None}
    gf = [r[0] for r in rows]
    ga = [r[1] for r in rows]
    pts = sum(3 if a > b else 1 if a == b else 0 for a, b in rows)
    return {
        "played": len(rows),
        "gf_avg": round(sum(gf) / len(gf), 2),
        "ga_avg": round(sum(ga) / len(ga), 2),
        "cs": sum(1 for x in ga if x == 0),
        "pts_rate": round(pts / (len(rows) * 3), 3),
    }


def enrich_match(m: dict, standings: dict) -> dict:
    lc = m["league_code"]
    hid, aid = m["home"]["id"], m["away"]["id"]
    home_res = fetch_team_matches(lc, hid, 15)
    time.sleep(0.1)
    away_res = fetch_team_matches(lc, aid, 15)
    time.sleep(0.1)

    m["home"]["recent"] = home_res
    m["away"]["recent"] = away_res
    # Key: last 5 at home for home team, last 5 away for away team
    m["home"]["venue_form"] = venue_form(home_res, hid, "H", 5, lc)
    m["away"]["venue_form"] = venue_form(away_res, aid, "A", 5, lc)
    m["home"]["overall_form"] = overall_stats(home_res, hid, 5)
    m["away"]["overall_form"] = overall_stats(away_res, aid, 5)

    for side, tid in (("home", hid), ("away", aid)):
        st = standings.get(tid) or {}
        m[side]["league_rank"] = st.get("rank")
        m[side]["league_points"] = st.get("points")
        m[side]["league_gf"] = st.get("gf")
        m[side]["league_ga"] = st.get("ga")

    # H2H
    h2h, seen = [], set()
    for res in home_res + away_res:
        teams = {res.get("home_id"), res.get("away_id")}
        if hid in teams and aid in teams:
            key = (res["date"], res["home"], res["away"])
            if key not in seen:
                seen.add(key)
                h2h.append(res)
    m["h2h"] = sorted(h2h, key=lambda x: x["date"], reverse=True)[:6]
    m["home"]["injuries"] = []  # filled if feed has data
    m["away"]["injuries"] = []
    m["home"]["top_scorer"] = None
    m["away"]["top_scorer"] = None
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="today")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--enrich", action="store_true", default=True)
    ap.add_argument("--no-enrich", action="store_true")
    ap.add_argument("--max-enrich", type=int, default=0, help="0 = all matches")
    args = ap.parse_args()
    enrich = not args.no_enrich
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
            time.sleep(0.08)

    seen, uniq = set(), []
    for m in matches:
        if m["id"] in seen:
            continue
        seen.add(m["id"])
        uniq.append(m)

    if enrich and uniq:
        # standings once per league
        league_codes = sorted({m["league_code"] for m in uniq})
        standings_by_league = {}
        for lc in league_codes:
            standings_by_league[lc] = fetch_standings(lc)
            print(f"Standings {lc}: {len(standings_by_league[lc])} teams")
            time.sleep(0.15)

        limit = args.max_enrich or len(uniq)
        print(f"Enriching up to {limit}/{len(uniq)} matches…")
        for i, m in enumerate(uniq[:limit]):
            try:
                uniq[i] = enrich_match(m, standings_by_league.get(m["league_code"]) or {})
            except Exception as e:
                print(f"  enrich fail {m.get('id')}: {e}")
            if (i + 1) % 5 == 0:
                print(f"  …{i+1}/{limit}")

    out = {
        "start_date": start.isoformat(),
        "days": args.days,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "source": "espn",
        "enriched": enrich,
        "match_count": len(uniq),
        "matches": uniq,
    }
    path = ROOT / f"fixtures-{start.isoformat()}.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {path} matches={len(uniq)}")


if __name__ == "__main__":
    main()
