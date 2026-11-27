#!/usr/bin/env python3
"""
Football match history from settled results.

Each finished match is stored once. Used for:
  - last 5 overall / home / away form with opponents + scorelines
  - simple strength-of-schedule tags (opponent win-rate in our store)
  - unbeaten home/away/overall flags

Optional: FOOTBALL_DATA_TOKEN env for football-data.org standings
(top/bottom half tags when available).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HISTORY_DIR = ROOT / "history"
HISTORY_DIR.mkdir(exist_ok=True)
MATCHES_PATH = HISTORY_DIR / "matches.json"
INDEX_PATH = HISTORY_DIR / "index.json"

# football-data.org competition codes we care about (free tier)
FD_COMPS = {
    "Premier League": "PL",
    "Championship": "ELC",
    "La Liga": "PD",
    "Serie A": "SA",
    "Bundesliga": "BL1",
    "Ligue 1": "FL1",
    "Eredivisie": "DED",
    "Primeira Liga": "PPL",
    "Champions League": "CL",
}


# Common short names from football-data.co.uk → expand before norm
ALIASES = {
    "nott'm forest": "nottingham forest",
    "nottm forest": "nottingham forest",
    "man united": "manchester united",
    "man utd": "manchester united",
    "manchester utd": "manchester united",
    "man city": "manchester city",
    "spurs": "tottenham hotspur",
    "tottenham": "tottenham hotspur",
    "wolves": "wolverhampton wanderers",
    "wolverhampton": "wolverhampton wanderers",
    "newcastle": "newcastle united",
    "west ham": "west ham united",
    "west brom": "west bromwich albion",
    "brighton": "brighton and hove albion",
    "brighton hove": "brighton and hove albion",
    "nottingham forest": "nottingham forest",
    "sheffield united": "sheffield united",
    "sheff utd": "sheffield united",
    "sheff wed": "sheffield wednesday",
    "qpr": "queens park rangers",
    "mk dons": "milton keynes dons",
    "inter": "internazionale",
    "milan": "ac milan",
    "atletico": "atletico madrid",
    "atl madrid": "atletico madrid",
    "betis": "real betis",
    "sociedad": "real sociedad",
    "psg": "paris saint germain",
    "paris sg": "paris saint germain",
    "bayern": "bayern munich",
    "dortmund": "borussia dortmund",
    "m'gladbach": "borussia monchengladbach",
    "gladbach": "borussia monchengladbach",
    "frankfurt": "eintracht frankfurt",
    "leverkusen": "bayer leverkusen",
}

def norm(name: str) -> str:
    s = (name or "").lower().strip()
    s = ALIASES.get(s, s)
    s = ALIASES.get(s, s)  # double in case chained
    s = re.sub(r"\b(fc|afc|cf|sc|united|utd)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def load_matches() -> list:
    if not MATCHES_PATH.exists():
        return []
    try:
        data = json.loads(MATCHES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("matches") or []
    except Exception:
        return []


def save_matches(matches: list) -> None:
    MATCHES_PATH.write_text(json.dumps(matches, indent=2), encoding="utf-8")
    # light index
    by_team = defaultdict(int)
    for m in matches:
        by_team[norm(m.get("home_name") or "")] += 1
        by_team[norm(m.get("away_name") or "")] += 1
    INDEX_PATH.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "matches": len(matches),
                "teams": len([k for k in by_team if k]),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def ingest_results_file(path: Path, matches: list) -> int:
    """Add finished matches from a results-YYYY-MM-DD.json. Returns new count."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    existing = {m.get("id") for m in matches if m.get("id")}
    # also key by date+home+away
    existing_keys = {
        (m.get("date"), norm(m.get("home_name") or ""), norm(m.get("away_name") or ""))
        for m in matches
    }
    added = 0
    day = data.get("date") or ""
    for m in data.get("matches") or []:
        mid = str(m.get("id") or "")
        h = (m.get("home") or {}).get("name") or ""
        a = (m.get("away") or {}).get("name") or ""
        key = (day, norm(h), norm(a))
        if mid and mid in existing:
            continue
        if key in existing_keys:
            continue
        hs = m.get("home_score")
        as_ = m.get("away_score")
        if hs is None or as_ is None:
            continue
        try:
            hs, as_ = int(hs), int(as_)
        except (TypeError, ValueError):
            continue
        if hs > as_:
            outcome = "home"
        elif as_ > hs:
            outcome = "away"
        else:
            outcome = "draw"
        entry = {
            "id": mid or f"{day}:{norm(h)}:{norm(a)}",
            "date": day,
            "league": m.get("league") or "",
            "league_code": m.get("league_code") or "",
            "home_name": h,
            "away_name": a,
            "home_id": str((m.get("home") or {}).get("id") or ""),
            "away_id": str((m.get("away") or {}).get("id") or ""),
            "home_score": hs,
            "away_score": as_,
            "ft": f"{hs}-{as_}",
            "outcome": outcome,
            # placeholders for richer feeds
            "red_cards_home": None,
            "red_cards_away": None,
            "penalties": None,
            "corners_home": None,
            "corners_away": None,
            "shots_home": None,
            "shots_away": None,
        }
        matches.append(entry)
        existing.add(entry["id"])
        existing_keys.add(key)
        added += 1
    return added


def rebuild_from_results() -> dict:
    matches = []
    total_new = 0
    for path in sorted(ROOT.glob("results-*.json")):
        n = ingest_results_file(path, matches)
        total_new += n
        print(f"  {path.name}: +{n}")
    matches.sort(key=lambda m: (m.get("date") or "", m.get("id") or ""))
    save_matches(matches)
    print(f"History: {len(matches)} matches stored")
    return {"matches": len(matches), "added_this_run": total_new}


def team_results(matches: list, team: str) -> list:
    """All results for team, newest first, with venue and result from their POV."""
    nt = norm(team)
    out = []
    for m in matches:
        hn, an = norm(m.get("home_name") or ""), norm(m.get("away_name") or "")
        if nt == hn:
            venue = "H"
            gf, ga = m["home_score"], m["away_score"]
            opp = m.get("away_name") or ""
        elif nt == an:
            venue = "A"
            gf, ga = m["away_score"], m["home_score"]
            opp = m.get("home_name") or ""
        else:
            continue
        if gf > ga:
            res = "W"
        elif gf < ga:
            res = "L"
        else:
            res = "D"
        out.append({
            "date": m.get("date"),
            "venue": venue,
            "opponent": opp,
            "gf": gf,
            "ga": ga,
            "ft": f"{gf}-{ga}",
            "result": res,
            "league": m.get("league") or "",
            "red_cards": m.get("red_cards_home") if venue == "H" else m.get("red_cards_away"),
            "penalties": m.get("penalties"),
        })
    out.sort(key=lambda x: x.get("date") or "", reverse=True)
    return out


def form_slice(rows: list, n: int = 5) -> dict:
    rows = rows[:n]
    if not rows:
        return {
            "n": 0,
            "chars": "",
            "record": "0-0-0",
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "unbeaten": False,
            "all_wins": False,
            "matches": [],
            "score": 0.45,
        }
    wins = sum(1 for r in rows if r["result"] == "W")
    draws = sum(1 for r in rows if r["result"] == "D")
    losses = sum(1 for r in rows if r["result"] == "L")
    chars = "".join(r["result"] for r in rows)
    score = 0.0
    weight = 0.0
    for i, r in enumerate(rows):
        w = 1.0 / (1.0 + i * 0.4)
        if r["result"] == "W":
            score += w
        elif r["result"] == "D":
            score += 0.45 * w
        weight += w
    return {
        "n": len(rows),
        "chars": chars,
        "record": f"{wins}-{draws}-{losses}",
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "unbeaten": losses == 0,
        "all_wins": wins == len(rows) and len(rows) >= 3,
        "matches": rows,
        "score": max(0.12, min(0.92, score / weight if weight else 0.45)),
    }


def team_win_rate(matches: list, team: str) -> float | None:
    rows = team_results(matches, team)
    if len(rows) < 3:
        return None
    return sum(1 for r in rows if r["result"] == "W") / len(rows)


def opponent_strength_tag(matches: list, opponent: str) -> str:
    """Rough SoS tag from opponent's win rate in our history."""
    wr = team_win_rate(matches, opponent)
    if wr is None:
        return ""
    if wr >= 0.55:
        return "top"
    if wr <= 0.30:
        return "bottom"
    return "mid"


def enrich_form_matches(matches: list, form_rows: list) -> list:
    out = []
    for r in form_rows:
        tag = opponent_strength_tag(matches, r["opponent"])
        item = dict(r)
        item["opp_strength"] = tag  # top | mid | bottom | ""
        item["opp_label"] = {
            "top": "vs stronger side",
            "bottom": "vs weaker side",
            "mid": "vs mid-table form",
        }.get(tag, "")
        out.append(item)
    return out



def team_goal_stats_from_rows(rows: list) -> dict:
    if not rows:
        return {
            "played": 0, "gf": 0, "ga": 0, "gf_pg": None, "ga_pg": None,
            "clean_sheets": 0, "cs_pct": None, "failed_to_score": 0,
        }
    gf = sum(r["gf"] for r in rows)
    ga = sum(r["ga"] for r in rows)
    cs = sum(1 for r in rows if r["ga"] == 0)
    fts = sum(1 for r in rows if r["gf"] == 0)
    n = len(rows)
    return {
        "played": n,
        "gf": gf,
        "ga": ga,
        "gf_pg": round(gf / n, 2),
        "ga_pg": round(ga / n, 2),
        "clean_sheets": cs,
        "cs_pct": round(100.0 * cs / n, 1),
        "failed_to_score": fts,
    }


def team_goal_stats(matches: list, team: str, venue: str | None = None) -> dict:
    """
    Goals for/against and clean sheets from history.
    venue: None = overall, 'H' = home only, 'A' = away only.
    """
    rows = team_results(matches, team)
    if venue in ("H", "A"):
        rows = [r for r in rows if r.get("venue") == venue]
    if not rows:
        return {
            "played": 0,
            "gf": 0,
            "ga": 0,
            "gf_pg": None,
            "ga_pg": None,
            "clean_sheets": 0,
            "cs_pct": None,
            "failed_to_score": 0,
        }
    gf = sum(r["gf"] for r in rows)
    ga = sum(r["ga"] for r in rows)
    cs = sum(1 for r in rows if r["ga"] == 0)
    fts = sum(1 for r in rows if r["gf"] == 0)
    n = len(rows)
    return {
        "played": n,
        "gf": gf,
        "ga": ga,
        "gf_pg": round(gf / n, 2),
        "ga_pg": round(ga / n, 2),
        "clean_sheets": cs,
        "cs_pct": round(100.0 * cs / n, 1),
        "failed_to_score": fts,
    }



def head_to_head(matches: list, team_a: str, team_b: str, limit: int = 6) -> dict:
    """
    Previous meetings between team_a and team_b, newest first.
    Results from team_a's perspective in each meeting.
    """
    na, nb = norm(team_a), norm(team_b)
    rows = []
    for m in matches:
        hn, an = norm(m.get("home_name") or ""), norm(m.get("away_name") or "")
        if {hn, an} != {na, nb}:
            continue
        if hn == na:
            venue = "H"
            gf, ga = m["home_score"], m["away_score"]
            opp = m.get("away_name") or ""
        else:
            venue = "A"
            gf, ga = m["away_score"], m["home_score"]
            opp = m.get("home_name") or ""
        if gf > ga:
            res = "W"
        elif gf < ga:
            res = "L"
        else:
            res = "D"
        rows.append({
            "date": m.get("date"),
            "venue": venue,
            "opponent": opp,
            "gf": gf,
            "ga": ga,
            "ft": f"{gf}-{ga}",
            "result": res,
            "league": m.get("league") or "",
            "home_name": m.get("home_name"),
            "away_name": m.get("away_name"),
            "score_home": m.get("home_score"),
            "score_away": m.get("away_score"),
        })
    rows.sort(key=lambda x: x.get("date") or "", reverse=True)
    rows = rows[:limit]
    wins = sum(1 for r in rows if r["result"] == "W")
    draws = sum(1 for r in rows if r["result"] == "D")
    losses = sum(1 for r in rows if r["result"] == "L")
    return {
        "n": len(rows),
        "chars": "".join(r["result"] for r in rows),
        "record": f"{wins}-{draws}-{losses}",  # from team_a POV
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "matches": rows,
    }



def streak_stats(rows: list) -> dict:
    """
    Current streaks from newest result first.
    rows = team_results order (newest first).
    """
    if not rows:
        return {
            "unbeaten": 0,
            "wins": 0,
            "clean_sheets": 0,
            "scoring": 0,
            "winless": 0,
        }
    unbeaten = 0
    for r in rows:
        if r["result"] in ("W", "D"):
            unbeaten += 1
        else:
            break
    wins = 0
    for r in rows:
        if r["result"] == "W":
            wins += 1
        else:
            break
    cs = 0
    for r in rows:
        if r["ga"] == 0:
            cs += 1
        else:
            break
    scoring = 0
    for r in rows:
        if r["gf"] > 0:
            scoring += 1
        else:
            break
    winless = 0
    for r in rows:
        if r["result"] in ("D", "L"):
            winless += 1
        else:
            break
    return {
        "unbeaten": unbeaten,
        "wins": wins,
        "clean_sheets": cs,
        "scoring": scoring,
        "winless": winless,
    }



def league_position_estimate(matches: list, team: str, league: str, season_prefix: str | None = None) -> dict | None:
    """
    Rough table position from stored results in this league (current-ish season).
    Points from all matches for each team in that league since season start.
    """
    # Prefer 2627 / current season rows if present
    league_rows = [m for m in matches if (m.get("league") or "") == league]
    if not league_rows:
        return None
    # take most recent season tag if present
    seasons = sorted({m.get("season") for m in league_rows if m.get("season")}, reverse=True)
    if seasons:
        league_rows = [m for m in league_rows if m.get("season") == seasons[0]]
    pts = {}
    played = {}
    for m in league_rows:
        h, a = m.get("home_name") or "", m.get("away_name") or ""
        hs_, as_ = m.get("home_score"), m.get("away_score")
        if hs_ is None or as_ is None:
            continue
        for name, gf, ga in ((h, hs_, as_), (a, as_, hs_)):
            key = norm(name)
            if not key:
                continue
            played[key] = played.get(key, 0) + 1
            if gf > ga:
                pts[key] = pts.get(key, 0) + 3
            elif gf == ga:
                pts[key] = pts.get(key, 0) + 1
            else:
                pts[key] = pts.get(key, 0) + 0
    if not pts:
        return None
    table = sorted(pts.items(), key=lambda x: (-x[1], -played.get(x[0], 0)))
    nt = norm(team)
    for i, (k, p) in enumerate(table, 1):
        if k == nt:
            n = len(table)
            band = "top" if i <= max(3, n // 4) else ("bottom" if i > n - max(3, n // 4) else "mid")
            return {"position": i, "teams": n, "points": p, "played": played.get(k, 0), "band": band, "label": f"{i}/{n}"}
    return None


def team_form_pack(matches: list, team: str) -> dict:
    all_rows = team_results(matches, team)
    home_rows = [r for r in all_rows if r["venue"] == "H"]
    away_rows = [r for r in all_rows if r["venue"] == "A"]
    overall = form_slice(all_rows, 5)
    home = form_slice(home_rows, 5)
    away = form_slice(away_rows, 5)
    overall3 = form_slice(all_rows, 3)
    home3 = form_slice(home_rows, 3)
    away3 = form_slice(away_rows, 3)
    overall["matches"] = enrich_form_matches(matches, overall["matches"])
    home["matches"] = enrich_form_matches(matches, home["matches"])
    away["matches"] = enrich_form_matches(matches, away["matches"])
    overall3["matches"] = enrich_form_matches(matches, overall3["matches"])
    home3["matches"] = enrich_form_matches(matches, home3["matches"])
    away3["matches"] = enrich_form_matches(matches, away3["matches"])

    goals_overall = team_goal_stats(matches, team, None)
    goals_home = team_goal_stats(matches, team, "H")
    goals_away = team_goal_stats(matches, team, "A")
    goals_last3 = team_goal_stats_from_rows(all_rows[:3])
    goals_last5 = team_goal_stats_from_rows(all_rows[:5])
    goals_home_last3 = team_goal_stats_from_rows(home_rows[:3])
    goals_away_last3 = team_goal_stats_from_rows(away_rows[:3])
    goals_home_last5 = team_goal_stats_from_rows(home_rows[:5])
    goals_away_last5 = team_goal_stats_from_rows(away_rows[:5])
    streaks = streak_stats(all_rows)
    streaks_home = streak_stats(home_rows)
    streaks_away = streak_stats(away_rows)

    # Highlight flags for notable runs (not full-season averages)
    flags = []
    if streaks["unbeaten"] >= 5:
        flags.append(f"Unbeaten in last {streaks['unbeaten']} overall")
    if streaks["wins"] >= 4:
        flags.append(f"Won last {streaks['wins']} overall")
    if streaks["clean_sheets"] >= 3:
        flags.append(f"{streaks['clean_sheets']} clean sheets in a row overall")
    if streaks["clean_sheets"] >= 5:
        flags.append(f"No goals conceded in last {streaks['clean_sheets']} overall")
    if streaks_home["unbeaten"] >= 5:
        flags.append(f"Unbeaten in last {streaks_home['unbeaten']} at home")
    if streaks_away["unbeaten"] >= 5:
        flags.append(f"Unbeaten in last {streaks_away['unbeaten']} away")
    if streaks_home["clean_sheets"] >= 3:
        flags.append(f"{streaks_home['clean_sheets']} home clean sheets in a row")
    if streaks_away["clean_sheets"] >= 3:
        flags.append(f"{streaks_away['clean_sheets']} away clean sheets in a row")
    if streaks["winless"] >= 5:
        flags.append(f"Winless in last {streaks['winless']} overall")

    return {
        "overall": overall,
        "home": home,
        "away": away,
        "overall3": overall3,
        "home3": home3,
        "away3": away3,
        "goals_last3": goals_last3,
        "goals_last5": goals_last5,
        "goals_home_last3": goals_home_last3,
        "goals_away_last3": goals_away_last3,
        "goals_home_last5": goals_home_last5,
        "goals_away_last5": goals_away_last5,
        "streaks": streaks,
        "streaks_home": streaks_home,
        "streaks_away": streaks_away,
        "flags": flags,
        "played": len(all_rows),
        # season totals kept only for win% context elsewhere, not primary goals display
        "goals_overall": goals_overall,
        "goals_home": goals_home,
        "goals_away": goals_away,
    }


def fetch_standings_optional() -> dict:
    """If FOOTBALL_DATA_TOKEN set, pull position maps for free comps."""
    token = (os.environ.get("FOOTBALL_DATA_TOKEN") or "").strip()
    if not token:
        return {}
    out = {}
    headers = {"X-Auth-Token": token, "User-Agent": "FootballBot/1.0"}
    for name, code in FD_COMPS.items():
        url = f"https://api.football-data.org/v4/competitions/{code}/standings"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
            table = []
            for block in data.get("standings") or []:
                if block.get("type") == "TOTAL":
                    table = block.get("table") or []
                    break
            if not table and data.get("standings"):
                table = (data["standings"][0] or {}).get("table") or []
            positions = {}
            n = len(table)
            for row in table:
                tname = ((row.get("team") or {}).get("name") or "").strip()
                pos = row.get("position")
                if tname and pos:
                    positions[norm(tname)] = {
                        "position": int(pos),
                        "played": row.get("playedGames"),
                        "points": row.get("points"),
                        "band": "top" if int(pos) <= max(3, n // 4) else (
                            "bottom" if int(pos) > n - max(3, n // 4) else "mid"
                        ),
                    }
            out[name] = positions
            print(f"  standings {name}: {len(positions)} teams")
        except Exception as e:
            print(f"  standings {name} failed: {e}")
    path = HISTORY_DIR / "standings.json"
    path.write_text(json.dumps({"updated_at": datetime.now(timezone.utc).isoformat(), "leagues": out}, indent=2), encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="Rebuild history from all results-*.json")
    ap.add_argument("--standings", action="store_true", help="Fetch football-data.org standings if token set")
    args = ap.parse_args()
    if args.rebuild or not MATCHES_PATH.exists():
        rebuild_from_results()
    else:
        # incremental: ingest any new results files
        matches = load_matches()
        before = len(matches)
        for path in sorted(ROOT.glob("results-*.json")):
            ingest_results_file(path, matches)
        if len(matches) != before:
            matches.sort(key=lambda m: (m.get("date") or "", m.get("id") or ""))
            save_matches(matches)
            print(f"History updated {before} -> {len(matches)}")
        else:
            print(f"History unchanged ({len(matches)} matches)")
    if args.standings:
        fetch_standings_optional()


if __name__ == "__main__":
    main()
