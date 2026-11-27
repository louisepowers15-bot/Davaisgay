#!/usr/bin/env python3
"""Bootstrap history/matches.json from free football-data.co.uk season CSVs."""
from __future__ import annotations
import csv, io, json, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "history"
HISTORY.mkdir(exist_ok=True)
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

LEAGUES = {
    "E0": ("Premier League", "eng.1"),
    "E1": ("Championship", "eng.2"),
    "E2": ("League One", "eng.3"),
    "E3": ("League Two", "eng.4"),
    "SC0": ("Scottish Premiership", "sco.1"),
    "SP1": ("La Liga", "esp.1"),
    "I1": ("Serie A", "ita.1"),
    "D1": ("Bundesliga", "ger.1"),
    "F1": ("Ligue 1", "fra.1"),
    "N1": ("Eredivisie", "ned.1"),
    "P1": ("Primeira Liga", "por.1"),
}
SEASONS = ["2627", "2526"]

def fetch(season, div):
    url = f"https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
    req = urllib.request.Request(url, headers=H)
    with urllib.request.urlopen(req, timeout=50) as r:
        return r.read().decode("utf-8-sig", errors="replace")

def parse_date(s):
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None

def gint(row, *keys):
    for k in keys:
        v = row.get(k)
        if v is None or v == "":
            continue
        try:
            return int(float(v))
        except ValueError:
            continue
    return None

def main():
    all_matches = []
    for season in SEASONS:
        for div, (league, code) in LEAGUES.items():
            try:
                text = fetch(season, div)
            except Exception as e:
                print(f"  skip {season}/{div}: {e}")
                continue
            if "HomeTeam" not in text[:300]:
                print(f"  skip {season}/{div}: bad file")
                continue
            n = 0
            for row in csv.DictReader(io.StringIO(text)):
                home = (row.get("HomeTeam") or "").strip()
                away = (row.get("AwayTeam") or "").strip()
                if not home or not away:
                    continue
                try:
                    hs, as_ = int(row["FTHG"]), int(row["FTAG"])
                except Exception:
                    continue
                day = parse_date(row.get("Date") or "")
                if not day:
                    continue
                outcome = "home" if hs > as_ else ("away" if as_ > hs else "draw")
                mid = f"fd:{season}:{code}:{day}:{home}:{away}"
                all_matches.append({
                    "id": mid, "date": day, "league": league, "league_code": code,
                    "home_name": home, "away_name": away, "home_id": "", "away_id": "",
                    "home_score": hs, "away_score": as_, "ft": f"{hs}-{as_}", "outcome": outcome,
                    "red_cards_home": gint(row, "HR"), "red_cards_away": gint(row, "AR"),
                    "penalties": None,
                    "corners_home": gint(row, "HC"), "corners_away": gint(row, "AC"),
                    "shots_home": gint(row, "HS"), "shots_away": gint(row, "AS"),
                    "shots_on_home": gint(row, "HST"), "shots_on_away": gint(row, "AST"),
                    "yellow_home": gint(row, "HY"), "yellow_away": gint(row, "AY"),
                    "source": "football-data.co.uk", "season": season,
                })
                n += 1
            print(f"{season}/{div} {league}: {n}")
            time.sleep(0.3)

    path = HISTORY / "matches.json"
    existing = []
    if path.exists():
        try:
            existing = json.loads(path.read_text())
            if isinstance(existing, dict):
                existing = existing.get("matches") or []
        except Exception:
            existing = []
    by_id = {m["id"]: m for m in existing if m.get("id")}
    for m in all_matches:
        by_id[m["id"]] = m
    merged = sorted(by_id.values(), key=lambda x: (x.get("date") or "", x.get("id") or ""))
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    teams = {m.get("home_name") for m in merged} | {m.get("away_name") for m in merged}
    (HISTORY / "index.json").write_text(json.dumps({
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "matches": len(merged),
        "teams": len([t for t in teams if t]),
        "source": "football-data.co.uk + local results",
    }, indent=2), encoding="utf-8")
    print(f"history/matches.json = {len(merged)} matches, {len(teams)} teams")

if __name__ == "__main__":
    main()
