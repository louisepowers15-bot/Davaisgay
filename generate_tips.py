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

try:
    import history_store
except ImportError:
    history_store = None

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
    """Parse season record string as Wins-Draws-Losses (e.g. 4-3-6 = 4 wins, 3 draws, 6 losses)."""
    s = (summary or "").strip()
    m = re.match(r"(\d+)\s*[-–]\s*(\d+)\s*[-–]\s*(\d+)", s)
    if not m:
        return {"w": 0, "d": 0, "l": 0, "n": 0, "win_pct": 0.33, "draw_pct": 0.33, "pts_pg": 1.0}
    w, d, l = int(m.group(1)), int(m.group(2)), int(m.group(3))
    n = w + d + l
    if n <= 0:
        return {"w": 0, "d": 0, "l": 0, "n": 0, "win_pct": 0.33, "draw_pct": 0.33, "pts_pg": 1.0}
    return {
        "w": w, "d": d, "l": l, "n": n,
        "win_pct": w / n,
        "draw_pct": d / n,
        "pts_pg": (3 * w + d) / n,
    }


def parse_form_last5(form: str) -> dict:
    """
    Last 5 results only (most-recent first). ESPN form is usually overall, not split home/away.
    Returns points rate, wins, unbeaten flag, string.
    """
    chars = re.findall(r"[WDL]", (form or "").upper())[:5]
    if not chars:
        return {
            "chars": "",
            "n": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "pts": 0,
            "pts_pg": 1.0,
            "win_pct": 0.33,
            "unbeaten": False,
            "all_wins": False,
            "score": 0.45,
        }
    wins = sum(1 for c in chars if c == "W")
    draws = sum(1 for c in chars if c == "D")
    losses = sum(1 for c in chars if c == "L")
    pts = 3 * wins + draws
    n = len(chars)
    # recency-weighted score (newest counts more)
    score = 0.0
    weight = 0.0
    for i, c in enumerate(chars):
        w = 1.0 / (1.0 + i * 0.4)
        if c == "W":
            score += w
        elif c == "D":
            score += 0.45 * w
        weight += w
    return {
        "chars": "".join(chars),
        "n": n,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "pts": pts,
        "pts_pg": pts / n,
        "win_pct": wins / n,
        "unbeaten": losses == 0,
        "all_wins": wins == n and n >= 3,
        "score": max(0.12, min(0.92, score / weight if weight else 0.45)),
    }


def form_score_from_string(form: str) -> float:
    return parse_form_last5(form)["score"]


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


def score_match_sides(match: dict, hist_matches: list | None = None) -> dict:
    """
    Strengths from history last-5 (overall/home/away) when available,
    else ESPN form string + season record.
    """
    home = match["home"]
    away = match["away"]
    hr = parse_record(home.get("record") or "")
    ar = parse_record(away.get("record") or "")
    hf = parse_form_last5(home.get("form") or "")
    af = parse_form_last5(away.get("form") or "")

    home_pack = away_pack = None
    h2h = {"n": 0, "matches": [], "record": "0-0-0", "chars": ""}
    if hist_matches and history_store:
        try:
            home_pack = history_store.team_form_pack(hist_matches, home.get("name") or "")
            away_pack = history_store.team_form_pack(hist_matches, away.get("name") or "")
            h2h = history_store.head_to_head(
                hist_matches, home.get("name") or "", away.get("name") or "", limit=6
            )
            # Prefer venue-aware form for the side they play today
            if home_pack and home_pack["home"]["n"] >= 2:
                hf = {
                    **hf,
                    "chars": home_pack["home"]["chars"] or home_pack["overall"]["chars"],
                    "score": 0.55 * home_pack["home"]["score"] + 0.45 * home_pack["overall"]["score"],
                    "n": home_pack["home"]["n"],
                    "wins": home_pack["home"]["wins"],
                    "draws": home_pack["home"]["draws"],
                    "losses": home_pack["home"]["losses"],
                    "unbeaten": home_pack["home"]["unbeaten"],
                    "all_wins": home_pack["home"]["all_wins"],
                }
            elif home_pack and home_pack["overall"]["n"] >= 2:
                hf = {**hf, "chars": home_pack["overall"]["chars"], "score": home_pack["overall"]["score"],
                      "n": home_pack["overall"]["n"], "wins": home_pack["overall"]["wins"],
                      "draws": home_pack["overall"]["draws"], "losses": home_pack["overall"]["losses"],
                      "unbeaten": home_pack["overall"]["unbeaten"], "all_wins": home_pack["overall"]["all_wins"]}
            if away_pack and away_pack["away"]["n"] >= 2:
                af = {
                    **af,
                    "chars": away_pack["away"]["chars"] or away_pack["overall"]["chars"],
                    "score": 0.55 * away_pack["away"]["score"] + 0.45 * away_pack["overall"]["score"],
                    "n": away_pack["away"]["n"],
                    "wins": away_pack["away"]["wins"],
                    "draws": away_pack["away"]["draws"],
                    "losses": away_pack["away"]["losses"],
                    "unbeaten": away_pack["away"]["unbeaten"],
                    "all_wins": away_pack["away"]["all_wins"],
                }
            elif away_pack and away_pack["overall"]["n"] >= 2:
                af = {**af, "chars": away_pack["overall"]["chars"], "score": away_pack["overall"]["score"],
                      "n": away_pack["overall"]["n"], "wins": away_pack["overall"]["wins"],
                      "draws": away_pack["overall"]["draws"], "losses": away_pack["overall"]["losses"],
                      "unbeaten": away_pack["overall"]["unbeaten"], "all_wins": away_pack["overall"]["all_wins"]}
        except Exception:
            home_pack = away_pack = None

    # Season quality matters more when we have a real sample (stops 18% sides beating 45% sides on home alone)
    def blend(l5, rec):
        season = 0.25 + 0.7 * rec.get("win_pct", 0.33)  # maps 0%→0.25, 50%→0.60
        if rec.get("n", 0) >= 8:
            season_w, form_w = 0.55, 0.45  # enough games: trust table more
        elif rec.get("n", 0) >= 4:
            season_w, form_w = 0.40, 0.60
        else:
            season_w, form_w = 0.25, 0.75
        if l5.get("n"):
            return form_w * l5["score"] + season_w * season
        return season

    home_str = blend(hf, hr)
    away_str = blend(af, ar)

    return {
        "home_form": max(0.12, min(0.9, home_str)),
        "away_form": max(0.12, min(0.9, away_str)),
        "home_adv": 0.52,  # mild — must not override a large quality gap
        "home_rec": hr,
        "away_rec": ar,
        "home_l5": hf,
        "away_l5": af,
        "home_hist": home_pack,
        "away_hist": away_pack,
        "h2h": h2h,
        "goals": 0.5,
    }



def bot_pick(bot: str, match: dict, factors: dict, dec_odds: dict, rng: random.Random) -> dict:
    w = WEIGHTS[bot]
    imp = implied_probs(dec_odds)
    hf, af = factors["home_form"], factors["away_form"]
    gap = hf - af  # +ve = home stronger on blended form/season

    # Base from quality; home edge is small
    h = 0.30 + 0.40 * hf + 0.06 * factors["home_adv"] + 0.10 * max(0, gap)
    a = 0.30 + 0.40 * af + 0.10 * max(0, -gap)
    closeness = max(0.0, 1.0 - abs(gap) * 2.5)
    d = 0.20 + 0.20 * closeness

    h += w["home_form"] * hf * 0.10 + w["home_adv"] * factors["home_adv"] * 0.05
    a += w["away_form"] * af * 0.10
    d += w["upset"] * 0.04

    # If away is clearly better on season+form, do not let home edge invent a home win
    if gap <= -0.12:
        h *= 0.88
        a *= 1.12
        d *= 1.05
    elif gap >= 0.12:
        h *= 1.08
        a *= 0.92

    h2h = factors.get("h2h") or {}
    if h2h.get("n", 0) >= 2:
        n = h2h["n"]
        h += 0.05 * (h2h.get("wins", 0) / n)
        a += 0.05 * (h2h.get("losses", 0) / n)
        d += 0.07 * (h2h.get("draws", 0) / n)

    if bot == "HomeStrong":
        # Only lean home if home is not clearly inferior
        if gap >= -0.05:
            h *= 1.10
        else:
            h *= 1.02  # weak home lean only
        d *= 0.96
    if bot == "AwayHunter":
        a *= 1.10
        d *= 0.96
    if bot == "ValueSeeker":
        tot = h + d + a
        ph, pd, pa = h / tot, d / tot, a / tot
        h += max(0, ph - imp["home"]) * 0.4
        a += max(0, pa - imp["away"]) * 0.4
        d += max(0, pd - imp["draw"]) * 0.35
    if bot == "DevilAdvocate":
        shortest = min(dec_odds, key=dec_odds.get)
        if shortest == "home":
            h *= 0.9
            a *= 1.08
            d *= 1.05
        elif shortest == "away":
            a *= 0.9
            h *= 1.08
            d *= 1.05
        else:
            d *= 0.9
            h *= 1.05
            a *= 1.05
    if bot == "GoalsGuru":
        d *= 0.94

    if abs(gap) >= 0.20:
        d *= 0.78
        if gap > 0:
            h *= 1.06
        else:
            a *= 1.06
    elif abs(gap) <= 0.06:
        d *= 1.10
        h *= 0.97
        a *= 0.97

    h += rng.uniform(-0.02, 0.02)
    d += rng.uniform(-0.02, 0.02)
    a += rng.uniform(-0.02, 0.02)
    total = max(0.01, h + d + a)
    probs = {"home": h / total, "draw": d / total, "away": a / total}
    pick = max(probs, key=probs.get)
    conf = int(round(42 + probs[pick] * 48))
    conf = max(44, min(84, conf))
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
    hf = factors.get("home_l5") or parse_form_last5("")
    af = factors.get("away_l5") or parse_form_last5("")
    league = match.get("league") or ""
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

    # Last 5 overall (primary)
    if hf.get("n"):
        bit = f"{home} last 5 overall: {hf['chars']} ({hf['wins']}-{hf['draws']}-{hf['losses']} in that run"
        if hf.get("unbeaten"):
            bit += ", unbeaten"
        bit += ")."
        sentences.append(bit)
    if af.get("n"):
        bit = f"{away} last 5 overall: {af['chars']} ({af['wins']}-{af['draws']}-{af['losses']}"
        if af.get("unbeaten"):
            bit += ", unbeaten"
        bit += ")."
        sentences.append(bit)

    # Season record explained
    if hr.get("n", 0) >= 3:
        sentences.append(
            f"{home} season record is {hr['w']}-{hr['d']}-{hr['l']} (wins-draws-losses overall), "
            f"{hr['win_pct']*100:.0f}% wins, {hr['pts_pg']:.2f} pts/game."
        )
    if ar.get("n", 0) >= 3:
        sentences.append(
            f"{away} season record is {ar['w']}-{ar['d']}-{ar['l']} (W-D-L overall), "
            f"{ar['win_pct']*100:.0f}% wins, {ar['pts_pg']:.2f} pts/game."
        )

    gap = factors["home_form"] - factors["away_form"]
    if pick == "home":
        sentences.append(
            f"Recent form edges toward {home}"
            + (f" in {league}" if league else "")
            + f" (price about {dec_odds['home']:.2f})."
        )
    elif pick == "away":
        sentences.append(
            f"Recent form edges toward {away} away from home (price about {dec_odds['away']:.2f})."
        )
    else:
        sentences.append(
            f"Last-5 form is close (gap {gap:+.2f}), so the draw at about {dec_odds['draw']:.2f} is preferred over forcing a side."
        )

    h2h = factors.get("h2h") or {}
    if h2h.get("n", 0) >= 1:
        sentences.append(
            f"Head-to-head (last {h2h['n']} from {home}'s view): {h2h.get('chars') or h2h.get('record')}."
        )
    sentences.append(f"Confidence {conf}%.")
    return " ".join(" ".join(sentences).split())



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

    # Scoreline suggestion — keep humble when quality is thin
    gap = factors["home_form"] - factors["away_form"]
    if consensus_pick == "home":
        if factors["home_form"] < 0.42:
            scoreline = "1-0"
        elif gap < 0.08:
            scoreline = "1-1"  # not a thrashing
        elif factors["away_form"] >= 0.45:
            scoreline = "2-1"
        else:
            scoreline = "2-0"
    elif consensus_pick == "away":
        if factors["away_form"] < 0.42:
            scoreline = "0-1"
        elif gap > -0.08:
            scoreline = "1-1"
        elif factors["home_form"] >= 0.45:
            scoreline = "1-2"
        else:
            scoreline = "0-2"
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
    """
    Home card: home form only (+ overall context).
    Away card: away form only (+ overall context).
    Goals for/against/clean sheets from history when present.
    """
    hr, ar = factors["home_rec"], factors["away_rec"]
    home, away = match["home"], match["away"]
    hh = factors.get("home_hist")
    ah = factors.get("away_hist")

    def pack_home(team, rec, hist, espn_form):
        if hist and hist.get("played", 0) > 0:
            o, h = hist["overall"], hist["home"]
            gh = hist.get("goals_home") or {}
            go = hist.get("goals_overall") or {}
            return {
                "name": team.get("name"),
                "side": "home",
                "record": team.get("record") or f"{rec.get('w',0)}-{rec.get('d',0)}-{rec.get('l',0)}",
                "wins": rec.get("w", 0),
                "draws": rec.get("d", 0),
                "losses": rec.get("l", 0),
                "win_pct": round(100 * rec.get("win_pct", 0), 1),
                "pts_pg": round(rec.get("pts_pg", 0), 2),
                # Venue-relevant form only
                "venue_form_last5": h.get("chars") or "—",
                "venue_form_record": h.get("record") or "—",
                "venue_form_label": "Last 5 at home",
                "overall_form_last5": o.get("chars") or "—",
                "overall_form_record": o.get("record") or "—",
                "venue_matches": h.get("matches") or [],
                "overall_matches": o.get("matches") or [],
                "goals_for": gh.get("gf"),
                "goals_against": gh.get("ga"),
                "gf_pg": gh.get("gf_pg"),
                "ga_pg": gh.get("ga_pg"),
                "clean_sheets": gh.get("clean_sheets"),
                "cs_pct": gh.get("cs_pct"),
                "goals_sample": gh.get("played") or 0,
                "goals_scope": "home games in history",
                "goals_overall_for": go.get("gf"),
                "goals_overall_against": go.get("ga"),
                "cs_overall": go.get("clean_sheets"),
                "flags": [f for f in (hist.get("flags") or []) if "away" not in f.lower()],
                "note": "Home form and home goals only. Overall last-5 is secondary context.",
            }
        l5 = parse_form_last5(espn_form or team.get("form") or "")
        return {
            "name": team.get("name"),
            "side": "home",
            "record": team.get("record") or "",
            "wins": rec.get("w", 0), "draws": rec.get("d", 0), "losses": rec.get("l", 0),
            "win_pct": round(100 * rec.get("win_pct", 0), 1),
            "pts_pg": round(rec.get("pts_pg", 0), 2),
            "venue_form_last5": "—",
            "venue_form_record": "—",
            "venue_form_label": "Last 5 at home",
            "overall_form_last5": l5.get("chars") or "—",
            "overall_form_record": f"{l5.get('wins',0)}-{l5.get('draws',0)}-{l5.get('losses',0)}",
            "venue_matches": [],
            "overall_matches": [],
            "goals_for": None, "goals_against": None, "gf_pg": None, "ga_pg": None,
            "clean_sheets": None, "cs_pct": None, "goals_sample": 0,
            "goals_scope": "not enough history yet",
            "flags": [],
            "note": "Home-only form and goals appear after this team has home results in history.",
        }

    def pack_away(team, rec, hist, espn_form):
        if hist and hist.get("played", 0) > 0:
            o, a = hist["overall"], hist["away"]
            ga_ = hist.get("goals_away") or {}
            go = hist.get("goals_overall") or {}
            return {
                "name": team.get("name"),
                "side": "away",
                "record": team.get("record") or f"{rec.get('w',0)}-{rec.get('d',0)}-{rec.get('l',0)}",
                "wins": rec.get("w", 0),
                "draws": rec.get("d", 0),
                "losses": rec.get("l", 0),
                "win_pct": round(100 * rec.get("win_pct", 0), 1),
                "pts_pg": round(rec.get("pts_pg", 0), 2),
                "venue_form_last5": a.get("chars") or "—",
                "venue_form_record": a.get("record") or "—",
                "venue_form_label": "Last 5 away",
                "overall_form_last5": o.get("chars") or "—",
                "overall_form_record": o.get("record") or "—",
                "venue_matches": a.get("matches") or [],
                "overall_matches": o.get("matches") or [],
                "goals_for": ga_.get("gf"),
                "goals_against": ga_.get("ga"),
                "gf_pg": ga_.get("gf_pg"),
                "ga_pg": ga_.get("ga_pg"),
                "clean_sheets": ga_.get("clean_sheets"),
                "cs_pct": ga_.get("cs_pct"),
                "goals_sample": ga_.get("played") or 0,
                "goals_scope": "away games in history",
                "goals_overall_for": go.get("gf"),
                "goals_overall_against": go.get("ga"),
                "cs_overall": go.get("clean_sheets"),
                "flags": [f for f in (hist.get("flags") or []) if "home" not in f.lower() or "overall" in f.lower()],
                "note": "Away form and away goals only. Overall last-5 is secondary context.",
            }
        l5 = parse_form_last5(espn_form or team.get("form") or "")
        return {
            "name": team.get("name"),
            "side": "away",
            "record": team.get("record") or "",
            "wins": rec.get("w", 0), "draws": rec.get("d", 0), "losses": rec.get("l", 0),
            "win_pct": round(100 * rec.get("win_pct", 0), 1),
            "pts_pg": round(rec.get("pts_pg", 0), 2),
            "venue_form_last5": "—",
            "venue_form_record": "—",
            "venue_form_label": "Last 5 away",
            "overall_form_last5": l5.get("chars") or "—",
            "overall_form_record": f"{l5.get('wins',0)}-{l5.get('draws',0)}-{l5.get('losses',0)}",
            "venue_matches": [],
            "overall_matches": [],
            "goals_for": None, "goals_against": None, "gf_pg": None, "ga_pg": None,
            "clean_sheets": None, "cs_pct": None, "goals_sample": 0,
            "goals_scope": "not enough history yet",
            "flags": [],
            "note": "Away-only form and goals appear after this team has away results in history.",
        }

    h2h = factors.get("h2h") or {"n": 0, "matches": [], "record": "—", "chars": ""}
    return {
        "home": pack_home(home, hr, hh, home.get("form")),
        "away": pack_away(away, ar, ah, away.get("form")),
        "h2h": {
            "n": h2h.get("n", 0),
            "record": h2h.get("record") or "—",
            "chars": h2h.get("chars") or "",
            "label": f"H2H (from {home.get('name')} view)",
            "matches": h2h.get("matches") or [],
        },
    }



def build_tips(d: date, fixtures: dict) -> dict:
    leagues_out = []
    hot = []
    hist_matches = []
    if history_store:
        try:
            hist_matches = history_store.load_matches()
            print(f"  history: {len(hist_matches)} past matches")
        except Exception as e:
            print(f"  history load failed: {e}")
    for lg in fixtures.get("leagues") or []:
        matches_out = []
        for match in lg.get("matches") or []:
            # skip finished if you only want previews — still tip pre-match
            if (match.get("status") or "").lower() in ("post", "final"):
                # still show but flag
                pass
            dec = normalise_odds(match.get("odds"))
            factors = score_match_sides(match, hist_matches)
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
