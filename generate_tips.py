#!/usr/bin/env python3
"""
Football tip bots: 1X2 (home / draw / away) with brief reasoning + bet builder.
First successful tips file of the day is frozen unless --force.
"""
from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


# How leagues map into site tabs
LEAGUE_CATEGORY = {
    "Premier League": "uk",
    "Championship": "uk",
    "League One": "uk",
    "League Two": "uk",
    "National League": "uk",
    "Scottish Premiership": "uk",
    "Scottish Championship": "uk",
    "FA Cup": "cups",
    "EFL Cup": "cups",
    "EFL Trophy": "cups",
    "Scottish Cup": "cups",
    "Scottish League Cup": "cups",
    "La Liga": "europe",
    "Serie A": "europe",
    "Bundesliga": "europe",
    "Ligue 1": "europe",
    "Eredivisie": "europe",
    "Primeira Liga": "europe",
    "Champions League": "uefa",
    "Europa League": "uefa",
    "Conference League": "uefa",
}

BOT_NAMES = [
    "FormFox",
    "HomeStrong",
    "AwayHunter",
    "ValueSeeker",
    "GoalsGuru",
    "DevilAdvocate",
]

# Each bot leans differently on 1X2 factors
WEIGHTS = {
    "FormFox":       {"home_form": 0.28, "away_form": 0.28, "home_adv": 0.12, "value": 0.12, "goals": 0.10, "upset": 0.10},
    "HomeStrong":    {"home_form": 0.22, "away_form": 0.12, "home_adv": 0.35, "value": 0.10, "goals": 0.11, "upset": 0.10},
    "AwayHunter":    {"home_form": 0.12, "away_form": 0.30, "home_adv": 0.08, "value": 0.20, "goals": 0.15, "upset": 0.15},
    "ValueSeeker":   {"home_form": 0.15, "away_form": 0.15, "home_adv": 0.10, "value": 0.40, "goals": 0.10, "upset": 0.10},
    "GoalsGuru":     {"home_form": 0.15, "away_form": 0.15, "home_adv": 0.10, "value": 0.15, "goals": 0.35, "upset": 0.10},
    "DevilAdvocate": {"home_form": 0.18, "away_form": 0.18, "home_adv": 0.08, "value": 0.18, "goals": 0.13, "upset": 0.25},
}


def parse_date(arg: str) -> date:
    if arg.lower() == "today":
        return date.today()
    if arg.lower() == "tomorrow":
        return date.today() + timedelta(days=1)
    if arg.lower() == "yesterday":
        return date.today() - timedelta(days=1)
    return datetime.strptime(arg, "%Y-%m-%d").date()


def parse_record(summary: str) -> dict:
    """Parse 'W-D-L' or '10-5-3' style records into win rate."""
    s = (summary or "").strip()
    m = re.match(r"(\d+)\s*[-–]\s*(\d+)\s*[-–]\s*(\d+)", s)
    if not m:
        return {"w": 0, "d": 0, "l": 0, "n": 0, "win_pct": 0.33, "pts_pg": 1.0}
    w, d, l = int(m.group(1)), int(m.group(2)), int(m.group(3))
    n = w + d + l
    if n <= 0:
        return {"w": 0, "d": 0, "l": 0, "n": 0, "win_pct": 0.33, "pts_pg": 1.0}
    return {
        "w": w, "d": d, "l": l, "n": n,
        "win_pct": w / n,
        "draw_pct": d / n,
        "pts_pg": (3 * w + d) / n,
    }


def form_score_from_string(form: str) -> float:
    """W/D/L string most-recent first if available."""
    if not form:
        return 0.5
    chars = re.findall(r"[WDL]", form.upper())
    if not chars:
        return 0.5
    score = 0.0
    weight = 0.0
    for i, c in enumerate(chars[:6]):
        w = 1.0 / (1.0 + i * 0.35)
        if c == "W":
            score += w
        elif c == "D":
            score += 0.4 * w
        weight += w
    return max(0.15, min(0.9, score / weight if weight else 0.5))


def american_to_decimal(a) -> float | None:
    try:
        a = float(a)
    except (TypeError, ValueError):
        return None
    if a == 0:
        return None
    if a > 0:
        return round(1 + a / 100.0, 2)
    return round(1 + 100.0 / abs(a), 2)


def normalise_odds(odds: dict | None) -> dict:
    """Return decimal 1X2 odds dict with sensible fallbacks."""
    if not odds:
        return {"home": 2.5, "draw": 3.3, "away": 2.9}
    out = {}
    for k in ("home", "draw", "away"):
        v = odds.get(k)
        if v is None:
            out[k] = {"home": 2.5, "draw": 3.3, "away": 2.9}[k]
            continue
        v = float(v)
        # ESPN often uses American moneyline for soccer
        if v > 20 or v < -20 or (v < 1 and v != 0):
            d = american_to_decimal(v)
            out[k] = d if d and d > 1.01 else {"home": 2.5, "draw": 3.3, "away": 2.9}[k]
        elif v > 1.01:
            out[k] = round(v, 2)
        else:
            d = american_to_decimal(v)
            out[k] = d if d else {"home": 2.5, "draw": 3.3, "away": 2.9}[k]
    return out


def implied_probs(dec: dict) -> dict:
    raw = {k: 1.0 / v for k, v in dec.items()}
    s = sum(raw.values()) or 1.0
    return {k: raw[k] / s for k in raw}


def score_match_sides(match: dict) -> dict:
    home = match["home"]
    away = match["away"]
    hr = parse_record(home.get("record") or "")
    ar = parse_record(away.get("record") or "")
    hf = form_score_from_string(home.get("form") or "")
    af = form_score_from_string(away.get("form") or "")
    # blend record + form string
    home_str = 0.55 * hr["win_pct"] + 0.45 * hf if hr["n"] >= 3 else (hf if home.get("form") else 0.45 + 0.1 * hr["win_pct"])
    away_str = 0.55 * ar["win_pct"] + 0.45 * af if ar["n"] >= 3 else (af if away.get("form") else 0.45 + 0.1 * ar["win_pct"])
    home_adv = 0.58  # baseline home edge in football
    # goals tendency proxy from pts (high scoring leagues not known — use mid)
    goals = 0.52
    return {
        "home_form": max(0.12, min(0.88, home_str)),
        "away_form": max(0.12, min(0.88, away_str)),
        "home_adv": home_adv,
        "home_rec": hr,
        "away_rec": ar,
        "goals": goals,
    }


def bot_pick(bot: str, match: dict, factors: dict, dec_odds: dict, rng: random.Random) -> dict:
    w = WEIGHTS[bot]
    imp = implied_probs(dec_odds)
    # model probs before value
    h = (
        w["home_form"] * factors["home_form"]
        + w["home_adv"] * factors["home_adv"]
        + w["away_form"] * (1 - factors["away_form"]) * 0.5
    )
    a = (
        w["away_form"] * factors["away_form"]
        + w["home_form"] * (1 - factors["home_form"]) * 0.35
        + (0.15 if bot == "AwayHunter" else 0.05)
    )
    d = 0.28 + w["upset"] * 0.15 + (0.05 if abs(factors["home_form"] - factors["away_form"]) < 0.08 else 0.0)
    # value adjustments
    if bot == "ValueSeeker":
        # prefer outcomes where model > implied
        edge_h = h / (h + d + a) - imp["home"]
        edge_a = a / (h + d + a) - imp["away"]
        edge_d = d / (h + d + a) - imp["draw"]
        h += max(0, edge_h) * 0.4
        a += max(0, edge_a) * 0.4
        d += max(0, edge_d) * 0.35
    if bot == "DevilAdvocate":
        # lean against shortest price
        shortest = min(dec_odds, key=dec_odds.get)
        if shortest == "home":
            h *= 0.85
            a *= 1.08
            d *= 1.05
        elif shortest == "away":
            a *= 0.85
            h *= 1.08
            d *= 1.05
        else:
            d *= 0.88
            h *= 1.05
            a *= 1.05
    if bot == "HomeStrong":
        h *= 1.12
    if bot == "GoalsGuru":
        # still 1X2 but lean BTTS-friendly sides (slightly prefer not-draw for goals markets narrative)
        d *= 0.92

    h += rng.uniform(-0.03, 0.03)
    d += rng.uniform(-0.03, 0.03)
    a += rng.uniform(-0.03, 0.03)
    total = max(0.01, h + d + a)
    probs = {"home": h / total, "draw": d / total, "away": a / total}
    pick = max(probs, key=probs.get)
    conf = int(round(40 + probs[pick] * 50))
    conf = max(42, min(82, conf))
    return {"pick": pick, "probs": probs, "conf": conf}


def outcome_label(pick: str, match: dict) -> str:
    if pick == "home":
        return f"{match['home']['name']} win"
    if pick == "away":
        return f"{match['away']['name']} win"
    return "Draw"


def reason_for(bot: str, match: dict, factors: dict, pick: str, conf: int, dec_odds: dict) -> str:
    home, away = match["home"]["name"], match["away"]["name"]
    hr, ar = factors["home_rec"], factors["away_rec"]
    league = match.get("league") or ""
    kick = match.get("kickoff") or ""
    sentences = []

    openers = {
        "FormFox": f"FormFox leans {outcome_label(pick, match)} in {home} vs {away}.",
        "HomeStrong": f"HomeStrong backs the home angle: {outcome_label(pick, match)}.",
        "AwayHunter": f"AwayHunter's read is {outcome_label(pick, match)}.",
        "ValueSeeker": f"ValueSeeker finds the price for {outcome_label(pick, match)}.",
        "GoalsGuru": f"GoalsGuru's 1X2 call is {outcome_label(pick, match)}.",
        "DevilAdvocate": f"DevilAdvocate goes against the obvious: {outcome_label(pick, match)}.",
    }
    sentences.append(openers.get(bot, f"Selection: {outcome_label(pick, match)}."))

    if hr["n"] >= 3:
        sentences.append(
            f"{home} season record on the card is {hr['w']}-{hr['d']}-{hr['l']} "
            f"(win rate {hr['win_pct']*100:.0f}%, {hr['pts_pg']:.2f} pts/game)."
        )
    else:
        sentences.append(f"{home} have limited record data on the feed for this season snapshot.")
    if ar["n"] >= 3:
        sentences.append(
            f"{away} sit at {ar['w']}-{ar['d']}-{ar['l']} "
            f"({ar['win_pct']*100:.0f}% wins, {ar['pts_pg']:.2f} pts/game)."
        )
    else:
        sentences.append(f"{away} similarly have sparse record detail on this feed.")

    if pick == "home":
        sentences.append(
            f"Home advantage still matters in {league or 'this competition'}, and the model rates "
            f"{home}'s relative strength above {away} enough to prefer the home win at about {dec_odds['home']:.2f}."
        )
    elif pick == "away":
        sentences.append(
            f"The away case rests on {away}'s comparative numbers versus {home}, "
            f"with the away price around {dec_odds['away']:.2f} offering the side the bot wants."
        )
    else:
        sentences.append(
            f"The sides look close enough that a draw at about {dec_odds['draw']:.2f} is the percentage play rather than forcing a winner."
        )

    if match.get("venue"):
        sentences.append(f"Venue listed as {match['venue']}.")
    if kick:
        sentences.append(f"Kick-off {kick} (feed time).")
    sentences.append(f"Confidence {conf}% on a 1X2 reading only — not a guaranteed result.")

    text = " ".join(sentences)
    # soft min length
    if len(text.split()) < 45:
        text += (
            f" Market snapshot on the card: home {dec_odds['home']:.2f}, "
            f"draw {dec_odds['draw']:.2f}, away {dec_odds['away']:.2f}."
        )
    return " ".join(text.split())


def bet_builder(match: dict, factors: dict, consensus_pick: str, dec_odds: dict, conf: float) -> dict:
    """Richer builder legs from record proxies (honest about limited free data)."""
    home, away = match["home"]["name"], match["away"]["name"]
    hr, ar = factors["home_rec"], factors["away_rec"]
    legs = []

    if consensus_pick == "home":
        legs.append({"market": "Match result", "selection": f"{home} to win", "note": "Consensus 1X2"})
    elif consensus_pick == "away":
        legs.append({"market": "Match result", "selection": f"{away} to win", "note": "Consensus 1X2"})
    else:
        legs.append({"market": "Match result", "selection": "Draw", "note": "Consensus 1X2"})

    both_ok = factors["home_form"] >= 0.4 and factors["away_form"] >= 0.4
    if both_ok:
        legs.append({"market": "Both teams to score", "selection": "Yes", "note": "Both sides show enough attacking form proxy"})
    else:
        legs.append({"market": "Both teams to score", "selection": "No", "note": "One side looks weaker going forward"})

    if factors["home_form"] + factors["away_form"] >= 1.05:
        legs.append({"market": "Total goals", "selection": "Over 2.5", "note": "Open-game lean from combined form"})
    else:
        legs.append({"market": "Total goals", "selection": "Under 2.5", "note": "Tighter total expected"})

    # Corners proxy: higher combined form → more corners
    if factors["home_form"] + factors["away_form"] >= 1.0:
        legs.append({"market": "Total corners", "selection": "Over 9.5", "note": "Proxy only — free feed has no corner averages yet"})
    else:
        legs.append({"market": "Total corners", "selection": "Under 10.5", "note": "Proxy only — free feed has no corner averages yet"})

    # Cards proxy: competitive tight games
    if abs(factors["home_form"] - factors["away_form"]) < 0.12:
        legs.append({"market": "Total cards", "selection": "Over 3.5", "note": "Even match often means more fouls/cards (proxy)"})
    else:
        legs.append({"market": "Total cards", "selection": "Under 4.5", "note": "One-sided lean may mean fewer cards (proxy)"})

    # Scoreline suggestion
    if consensus_pick == "home":
        scoreline = "2-1" if factors["away_form"] >= 0.4 else "2-0"
    elif consensus_pick == "away":
        scoreline = "1-2" if factors["home_form"] >= 0.4 else "0-2"
    else:
        scoreline = "1-1"

    if consensus_pick == "home":
        safer = f"{home} win or draw (1X)"
    elif consensus_pick == "away":
        safer = f"{away} win or draw (X2)"
    else:
        safer = "Double chance 12 if you do not want the pure draw"

    return {
        "legs": legs,
        "scoreline": scoreline,
        "safer_alternative": safer,
        "disclaimer": "Corners/cards legs are proxies until richer stats are stored. Always check live prices. Not betting advice.",
    }


def match_stats(match: dict, factors: dict) -> dict:
    """Card-facing stats block from what the free feed actually has."""
    hr, ar = factors["home_rec"], factors["away_rec"]
    home, away = match["home"], match["away"]
    return {
        "home": {
            "name": home.get("name"),
            "record": home.get("record") or "",
            "form": home.get("form") or "",
            "wins": hr.get("w", 0),
            "draws": hr.get("d", 0),
            "losses": hr.get("l", 0),
            "win_pct": round(100 * hr.get("win_pct", 0), 1),
            "pts_pg": round(hr.get("pts_pg", 0), 2),
            "note": "Goals for/against, clean sheets, corners not on free ESPN scoreboard — added when history store has them.",
        },
        "away": {
            "name": away.get("name"),
            "record": away.get("record") or "",
            "form": away.get("form") or "",
            "wins": ar.get("w", 0),
            "draws": ar.get("d", 0),
            "losses": ar.get("l", 0),
            "win_pct": round(100 * ar.get("win_pct", 0), 1),
            "pts_pg": round(ar.get("pts_pg", 0), 2),
            "note": "Same limit as home until richer stats pipeline is live.",
        },
    }


def build_tips(d: date, fixtures: dict) -> dict:
    leagues_out = []
    hot = []
    for lg in fixtures.get("leagues") or []:
        matches_out = []
        for match in lg.get("matches") or []:
            # skip finished if you only want previews — still tip pre-match
            if (match.get("status") or "").lower() in ("post", "final"):
                # still show but flag
                pass
            dec = normalise_odds(match.get("odds"))
            factors = score_match_sides(match)
            rng = random.Random(f"{d}|{match.get('id')}|{match.get('name')}")
            tips = []
            for bot in BOT_NAMES:
                res = bot_pick(bot, match, factors, dec, rng)
                tips.append({
                    "bot": bot,
                    "pick": res["pick"],  # home|draw|away
                    "pick_label": outcome_label(res["pick"], match),
                    "conf": res["conf"],
                    "odds": dec.get(res["pick"]),
                    "probs": {k: round(v, 3) for k, v in res["probs"].items()},
                    "reason": reason_for(bot, match, factors, res["pick"], res["conf"], dec),
                })
            counts = Counter(t["pick"] for t in tips)
            top_pick, n = counts.most_common(1)[0]
            avg_conf = sum(t["conf"] for t in tips if t["pick"] == top_pick) / max(1, n)
            strength = "strong" if n >= 5 else ("solid" if n >= 4 else ("lean" if n >= 3 else "split"))
            # Skip forced tips: too tight / low agreement (same idea as horse "split")
            tippable = (n >= 3 and avg_conf >= 54 and strength != "split")
            consensus = {
                "pick": top_pick if tippable else None,
                "pick_label": outcome_label(top_pick, match) if tippable else "No tip — too tight",
                "bot_count": n,
                "avg_conf": round(avg_conf, 1),
                "strength": strength if tippable else "skip",
                "bots": [t["bot"] for t in tips if t["pick"] == top_pick] if tippable else [],
                "odds": dec.get(top_pick) if tippable else None,
                "tippable": tippable,
            }
            bb = bet_builder(match, factors, top_pick, dec, avg_conf) if tippable else {
                "legs": [],
                "scoreline": None,
                "safer_alternative": None,
                "disclaimer": "No builder — match skipped as too tight for a tip.",
            }
            stats = match_stats(match, factors)
            for t in tips:
                t["stars"] = counts[t["pick"]]
            matches_out.append({
                "id": match.get("id"),
                "name": match.get("name"),
                "kickoff": match.get("kickoff"),
                "kickoff_iso": match.get("kickoff_iso"),
                "status": match.get("status"),
                "venue": match.get("venue"),
                "home": match["home"],
                "away": match["away"],
                "odds": dec,
                "consensus": consensus,
                "bet_builder": bb,
                "stats": stats,
                "tippable": tippable,
                "tips": tips if tippable else [],  # hide bot noise on skips
            })
            if tippable:
                hot.append({
                    "league": lg["name"],
                    "category": LEAGUE_CATEGORY.get(lg["name"], "europe"),
                    "match": match["name"],
                    "kickoff": match.get("kickoff"),
                    "pick": top_pick,
                    "pick_label": consensus["pick_label"],
                    "bot_count": n,
                    "avg_conf": consensus["avg_conf"],
                    "odds": consensus["odds"],
                    "strength": strength,
                    "scoreline": bb.get("scoreline"),
                })
        if matches_out:
            leagues_out.append({
                "name": lg["name"],
                "code": lg.get("code"),
                "region": lg.get("region"),
                "category": LEAGUE_CATEGORY.get(lg["name"], "europe"),
                "priority": lg.get("priority", 99),
                "matches": matches_out,
                "tipped_count": sum(1 for m in matches_out if m.get("tippable")),
            })
    hot.sort(key=lambda x: (-x["bot_count"], -x["avg_conf"]))
    return {
        "date": d.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "frozen": True,
        "leagues": leagues_out,
        "hot_picks": hot[:15],
        "meta": {
            "bots": BOT_NAMES,
            "freeze_policy": "first_write_wins",
            "note": "1X2 tips from season records/form proxies on ESPN feed. Not betting advice.",
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="today")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    d = parse_date(args.date)
    out = ROOT / f"tips-{d.isoformat()}.json"
    if out.exists() and not args.force:
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
            n = sum(len(lg.get("matches") or []) for lg in existing.get("leagues") or [])
            if n > 0:
                print(f"SKIP: {out.name} already has {n} matches — frozen. Use --force to overwrite.")
                return
        except Exception:
            pass

    fx_path = ROOT / f"fixtures-{d.isoformat()}.json"
    if not fx_path.exists():
        print(f"No fixtures file {fx_path} — run scrape_fixtures.py first")
        tips = {
            "date": d.isoformat(),
            "leagues": [],
            "hot_picks": [],
            "published": False,
            "note": "No fixtures scraped yet.",
        }
    else:
        fixtures = json.loads(fx_path.read_text(encoding="utf-8"))
        tips = build_tips(d, fixtures)
        tips["published"] = True

    out.write_text(json.dumps(tips, indent=2), encoding="utf-8")
    n = sum(len(lg.get("matches") or []) for lg in tips.get("leagues") or [])
    print(f"Wrote {out} matches={n} hot={len(tips.get('hot_picks') or [])}")


if __name__ == "__main__":
    main()
