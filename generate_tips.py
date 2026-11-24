#!/usr/bin/env python3
"""
Multi-bot football tips: 1X2 lean, predicted score, reasoning, bet builder.
Uses form strings, league records, recent results and H2H when present.
Injuries / new signings / manager stats are only used when present in the fixture JSON
(free feeds rarely include them — reasons stay honest).
"""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent

BOTS = [
    "FormFox",        # recent form
    "HomeBanker",     # home advantage / solid favourites
    "ValueHunter",    # upset / price-style lean without needing odds feed
    "DrawSpecialist", # stalemate patterns
    "AttackMinded",   # goals / scorelines
    "H2HHistorian",   # head-to-head
]

# Outcome weights differ by bot personality
WEIGHTS = {
    "FormFox":        {"form": 0.40, "home": 0.15, "record": 0.20, "h2h": 0.10, "goals": 0.15},
    "HomeBanker":     {"form": 0.20, "home": 0.40, "record": 0.20, "h2h": 0.10, "goals": 0.10},
    "ValueHunter":    {"form": 0.25, "home": 0.10, "record": 0.20, "h2h": 0.15, "goals": 0.15},
    "DrawSpecialist": {"form": 0.25, "home": 0.10, "record": 0.20, "h2h": 0.20, "goals": 0.15},
    "AttackMinded":   {"form": 0.25, "home": 0.15, "record": 0.15, "h2h": 0.10, "goals": 0.35},
    "H2HHistorian":   {"form": 0.15, "home": 0.15, "record": 0.15, "h2h": 0.40, "goals": 0.15},
}


def parse_date(arg: str) -> date:
    if arg.lower() == "today":
        return date.today()
    if arg.lower() == "tomorrow":
        return date.today() + timedelta(days=1)
    return datetime.strptime(arg, "%Y-%m-%d").date()


def form_score(form: str) -> float:
    """0-1 from ESPN form string e.g. WDLWW (most recent last or first — ESPN is oldest→newest often)."""
    if not form:
        return 0.45
    f = form.upper().replace(" ", "")
    # treat last 5 chars as most recent
    chunk = f[-5:] if len(f) > 5 else f
    pts = 0.0
    w = 0.0
    for i, c in enumerate(chunk):
        weight = 0.6 + 0.1 * i  # later chars weighted a bit higher
        if c == "W":
            pts += 1.0 * weight
        elif c == "D":
            pts += 0.45 * weight
        elif c == "L":
            pts += 0.0 * weight
        w += weight
    return max(0.05, min(0.95, pts / max(w, 1.0)))


def record_score(record: str) -> float:
    """Parse W-D-L summary like '2-3-6' → rough points rate."""
    if not record or "-" not in record:
        return 0.45
    parts = record.replace("–", "-").split("-")
    try:
        nums = [int(p.strip()) for p in parts[:3]]
    except Exception:
        return 0.45
    while len(nums) < 3:
        nums.append(0)
    w, d, l = nums[0], nums[1], nums[2]
    games = w + d + l
    if games <= 0:
        return 0.45
    return max(0.05, min(0.95, (w * 3 + d) / (games * 3)))


def recent_goal_stats(recent: list[dict], team_id: str) -> tuple[float, float]:
    """Average goals scored / conceded from recent list."""
    if not recent or not team_id:
        return 1.2, 1.2
    gf = ga = n = 0
    for m in recent:
        try:
            hs, as_ = m["score"]
        except Exception:
            continue
        if m.get("home_id") == team_id:
            gf += hs
            ga += as_
        elif m.get("away_id") == team_id:
            gf += as_
            ga += hs
        else:
            continue
        n += 1
    if n == 0:
        return 1.2, 1.2
    return gf / n, ga / n


def h2h_edge(h2h: list[dict], home_id: str, away_id: str) -> tuple[float, float, float]:
    """Return (home_win_rate, draw_rate, away_win_rate) from H2H list."""
    if not h2h:
        return 0.33, 0.34, 0.33
    hw = dr = aw = 0
    for m in h2h:
        hs, as_ = m["score"]
        if hs == as_:
            dr += 1
        elif m.get("home_id") == home_id and hs > as_:
            hw += 1
        elif m.get("away_id") == home_id and as_ > hs:
            hw += 1
        elif m.get("home_id") == away_id and hs > as_:
            aw += 1
        elif m.get("away_id") == away_id and as_ > hs:
            aw += 1
        else:
            if hs > as_:
                # home team of that match won
                if m.get("home_id") == home_id:
                    hw += 1
                else:
                    aw += 1
            else:
                if m.get("away_id") == home_id:
                    hw += 1
                else:
                    aw += 1
    n = max(1, hw + dr + aw)
    return hw / n, dr / n, aw / n


def poisson(lmbda: float, k: int) -> float:
    return math.exp(-lmbda) * (lmbda ** k) / math.factorial(k)


def predict_score(home_xg: float, away_xg: float) -> tuple[int, int, dict]:
    """Most likely scoreline under independent Poisson."""
    best = (1, 1)
    best_p = -1.0
    dist = {}
    for i in range(0, 6):
        for j in range(0, 6):
            p = poisson(max(0.2, home_xg), i) * poisson(max(0.2, away_xg), j)
            dist[(i, j)] = p
            if p > best_p:
                best_p = p
                best = (i, j)
    # also 1X2 probs
    p_home = p_draw = p_away = 0.0
    for (i, j), p in dist.items():
        if i > j:
            p_home += p
        elif i == j:
            p_draw += p
        else:
            p_away += p
    s = p_home + p_draw + p_away
    return best[0], best[1], {
        "p_home": p_home / s,
        "p_draw": p_draw / s,
        "p_away": p_away / s,
        "p_score": best_p,
    }


def match_features(m: dict) -> dict:
    home, away = m["home"], m["away"]
    hf = form_score(home.get("form") or "")
    af = form_score(away.get("form") or "")
    hr = record_score(home.get("record") or "")
    ar = record_score(away.get("record") or "")
    h_gf, h_ga = recent_goal_stats(home.get("recent") or [], home.get("id") or "")
    a_gf, a_ga = recent_goal_stats(away.get("recent") or [], away.get("id") or "")
    # blend form into expected goals
    home_xg = max(0.3, 0.55 * h_gf + 0.45 * a_ga) * (0.85 + 0.3 * hf)
    away_xg = max(0.3, 0.55 * a_gf + 0.45 * h_ga) * (0.85 + 0.3 * af)
    # slight home bump
    home_xg *= 1.12
    away_xg *= 0.95
    hs, as_, probs = predict_score(home_xg, away_xg)
    h2h_h, h2h_d, h2h_a = h2h_edge(m.get("h2h") or [], home.get("id") or "", away.get("id") or "")
    return {
        "home_form": hf,
        "away_form": af,
        "home_record": hr,
        "away_record": ar,
        "home_xg": home_xg,
        "away_xg": away_xg,
        "pred_home": hs,
        "pred_away": as_,
        "probs": probs,
        "h2h": (h2h_h, h2h_d, h2h_a),
        "home_gf": h_gf,
        "home_ga": h_ga,
        "away_gf": a_gf,
        "away_ga": a_ga,
    }


def bot_pick(bot: str, m: dict, feat: dict, rng: random.Random) -> dict:
    w = WEIGHTS[bot]
    hf, af = feat["home_form"], feat["away_form"]
    hr, ar = feat["home_record"], feat["away_record"]
    h2h_h, h2h_d, h2h_a = feat["h2h"]
    probs = feat["probs"]

    # strength scores for home win / draw / away win
    home_s = (
        w["form"] * hf
        + w["home"] * 0.62
        + w["record"] * hr
        + w["h2h"] * h2h_h
        + w["goals"] * min(0.9, feat["home_xg"] / 2.2)
    )
    away_s = (
        w["form"] * af
        + w["home"] * 0.38
        + w["record"] * ar
        + w["h2h"] * h2h_a
        + w["goals"] * min(0.9, feat["away_xg"] / 2.2)
    )
    draw_s = (
        w["form"] * (1 - abs(hf - af))
        + w["home"] * 0.35
        + w["record"] * (1 - abs(hr - ar))
        + w["h2h"] * h2h_d
        + w["goals"] * (0.55 if abs(feat["home_xg"] - feat["away_xg"]) < 0.35 else 0.3)
    )

    if bot == "DrawSpecialist":
        draw_s *= 1.25
    if bot == "ValueHunter":
        # lean against the side with much stronger record when forms are close
        if hr > ar + 0.2 and abs(hf - af) < 0.12:
            away_s *= 1.12
        elif ar > hr + 0.2 and abs(hf - af) < 0.12:
            home_s *= 1.12
    if bot == "HomeBanker":
        home_s *= 1.15
    if bot == "AttackMinded":
        # prefer the higher xG side; less draw
        draw_s *= 0.75

    home_s += rng.uniform(-0.02, 0.02)
    away_s += rng.uniform(-0.02, 0.02)
    draw_s += rng.uniform(-0.02, 0.02)

    scores = {"home": home_s, "draw": draw_s, "away": away_s}
    outcome = max(scores, key=scores.get)
    conf = int(max(42, min(82, round(50 + 40 * (scores[outcome] - sorted(scores.values())[-2])))))

    # Predicted score — bots can nudge
    ph, pa = feat["pred_home"], feat["pred_away"]
    if bot == "AttackMinded":
        ph, pa = min(5, ph + 1), min(5, pa + (0 if outcome == "home" else 1))
    if outcome == "home" and ph <= pa:
        ph, pa = max(ph, pa) + 1, pa
    if outcome == "away" and pa <= ph:
        pa, ph = max(pa, ph) + 1, ph
    if outcome == "draw":
        g = max(ph, pa, 1)
        if bot == "DrawSpecialist" and abs(hf - af) < 0.1:
            ph = pa = min(g, 1)
        else:
            ph = pa = g if g <= 2 else 1

    pick_team = None
    if outcome == "home":
        pick_team = m["home"]["name"]
        market = "Home win"
    elif outcome == "away":
        pick_team = m["away"]["name"]
        market = "Away win"
    else:
        pick_team = "Draw"
        market = "Draw"

    reason = build_reason(bot, m, feat, outcome, ph, pa)
    builder = build_bet_builder(bot, m, feat, outcome, ph, pa)

    return {
        "bot": bot,
        "outcome": outcome,  # home | draw | away
        "market": market,
        "selection": pick_team,
        "predicted_score": f"{ph}-{pa}",
        "conf": conf,
        "reason": reason,
        "bet_builder": builder,
        "probs": {
            "home": round(probs["p_home"], 3),
            "draw": round(probs["p_draw"], 3),
            "away": round(probs["p_away"], 3),
        },
    }


def _h(seed: str, *opts: str) -> str:
    opts = [o for o in opts if o]
    return opts[sum(ord(c) for c in seed) % len(opts)] if opts else ""


def build_reason(bot: str, m: dict, feat: dict, outcome: str, ph: int, pa: int) -> str:
    home, away = m["home"]["name"], m["away"]["name"]
    hf, af = home, away
    form_h = m["home"].get("form") or "n/a"
    form_a = m["away"].get("form") or "n/a"
    rec_h = m["home"].get("record") or "n/a"
    rec_a = m["away"].get("record") or "n/a"
    league = m.get("league") or ""
    seed = f"{bot}|{home}|{away}|{outcome}|{ph}-{pa}"
    bits = []

    if bot == "FormFox":
        bits.append(
            f"Form is the main read for {league}: {home} ({form_h}) against {away} ({form_a})."
        )
        if feat["home_form"] > feat["away_form"] + 0.08:
            bits.append(f"{home} have the clearer recent pattern.")
        elif feat["away_form"] > feat["home_form"] + 0.08:
            bits.append(f"{away} arrive in better nick on the last few results.")
        else:
            bits.append("Neither side’s form string is dominant, so the margin looks thin.")
    elif bot == "HomeBanker":
        bits.append(
            f"Home context matters in {league}. {home} at home (form {form_h}, record {rec_h}) against {away} ({form_a}, {rec_a})."
        )
        bits.append("Unless the visitors’ form is clearly superior, the home side gets the nod.")
    elif bot == "ValueHunter":
        bits.append(
            f"Looking for the side the card under-sells: {home} ({form_h}, {rec_h}) vs {away} ({form_a}, {rec_a})."
        )
        bits.append(
            "When league position/record and short-term form disagree, the form side is often the better speculative angle."
        )
    elif bot == "DrawSpecialist":
        bits.append(
            f"Draw case in {league}: {home} ({form_h}) vs {away} ({form_a})."
        )
        if abs(feat["home_form"] - feat["away_form"]) < 0.12:
            bits.append("Recent form is tightly matched, which is where stalemates cluster.")
        else:
            bits.append("Even with a form edge, both sides have enough structure that a shared point is live.")
    elif bot == "AttackMinded":
        bits.append(
            f"Goal expectation leans this way: modelled ~{feat['home_xg']:.1f} vs ~{feat['away_xg']:.1f} for {home} vs {away}."
        )
        bits.append(
            f"Recent scoring rates (from available results) sit around {feat['home_gf']:.1f} GF for the hosts and {feat['away_gf']:.1f} for the visitors."
        )
    else:  # H2HHistorian
        n_h2h = len(m.get("h2h") or [])
        bits.append(
            f"Head-to-head is the lens: {n_h2h} recent meetings in the loaded data between {home} and {away}."
        )
        if n_h2h:
            hh, hd, ha = feat["h2h"]
            bits.append(
                f"In those games the split is roughly home-win {hh:.0%} / draw {hd:.0%} / away-win {ha:.0%} (from the home team’s perspective in each fixture)."
            )
        else:
            bits.append(
                "No usable H2H sample in the feed, so this falls back to form and home advantage rather than inventing history."
            )

    # Outcome line
    if outcome == "home":
        bits.append(f"Most likely result: {home} to win, predicted score {ph}-{pa}.")
    elif outcome == "away":
        bits.append(f"Most likely result: {away} to win, predicted score {ph}-{pa}.")
    else:
        bits.append(f"Most likely result: draw, predicted score {ph}-{pa}.")

    # Honesty on missing data
    missing = []
    if not (m.get("home", {}).get("recent") or m.get("away", {}).get("recent")):
        missing.append("full recent match lists")
    if not m.get("h2h"):
        missing.append("deep H2H")
    # injuries / signings / managers not on free ESPN scoreboard
    missing.append("injuries")
    missing.append("new signings")
    missing.append("manager career stats")
    bits.append(
        "Not used (not on this free feed): " + ", ".join(missing) + " — so those angles are not invented."
    )

    text = " ".join(bits)
    if len(text.split()) < 50:
        text += (
            f" League record strings on the card are {home} {rec_h} and {away} {rec_a}. "
            f"Kick-off reference: {m.get('kickoff') or m.get('date') or 'tbc'} at {m.get('venue') or 'venue tbc'}."
        )
    return " ".join(text.split())


def build_bet_builder(bot: str, m: dict, feat: dict, outcome: str, ph: int, pa: int) -> dict:
    """Simple multi-leg builder from predicted score / style — not matched to bookie prices."""
    home, away = m["home"]["name"], m["away"]["name"]
    legs = []
    total = ph + pa
    if outcome == "home":
        legs.append(f"{home} to win")
    elif outcome == "away":
        legs.append(f"{away} to win")
    else:
        legs.append("Draw")
    legs.append(f"Correct score {ph}-{pa}")
    if total >= 3:
        legs.append("Over 2.5 goals")
    else:
        legs.append("Under 2.5 goals")
    if ph > 0 and pa > 0:
        legs.append("Both teams to score")
    elif outcome == "home":
        legs.append(f"{home} to score")
    elif outcome == "away":
        legs.append(f"{away} to score")

    # Drop correct score for "safer" alt builder
    safer = [legs[0]]
    if total >= 3:
        safer.append("Over 1.5 goals")
    else:
        safer.append("Under 3.5 goals")
    if outcome != "draw":
        safer.append("Win–draw (double chance) on the selected side" if False else legs[0].replace("to win", "win or draw"))

    return {
        "primary_legs": legs[:4],
        "safer_legs": [
            legs[0],
            "Over 1.5 goals" if total >= 2 else "Under 3.5 goals",
        ],
        "note": (
            "Bet builder legs are derived from the predicted scoreline and match profile. "
            "They are not priced here — check your bookmaker for the combo price."
        ),
    }


def build_tips(fixtures: dict) -> dict:
    matches_out = []
    for idx, m in enumerate(fixtures.get("matches") or []):
        feat = match_features(m)
        rng = random.Random(f"{m.get('id')}|{m.get('date')}|{idx}")
        tips = [bot_pick(bot, m, feat, rng) for bot in BOTS]
        # consensus
        votes = Counter(t["outcome"] for t in tips)
        consensus_outcome = votes.most_common(1)[0][0]
        stars = Counter(t["selection"] for t in tips)
        for t in tips:
            t["stars"] = stars[t["selection"]]

        matches_out.append({
            "id": m.get("id"),
            "date": m.get("date"),
            "kickoff": m.get("kickoff"),
            "league": m.get("league"),
            "region": m.get("region"),
            "venue": m.get("venue"),
            "home": m["home"]["name"],
            "away": m["away"]["name"],
            "home_form": m["home"].get("form"),
            "away_form": m["away"].get("form"),
            "home_record": m["home"].get("record"),
            "away_record": m["away"].get("record"),
            "model_score": f"{feat['pred_home']}-{feat['pred_away']}",
            "model_probs": feat["probs"],
            "consensus": consensus_outcome,
            "tips": tips,
        })

    return {
        "start_date": fixtures.get("start_date"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "match_count": len(matches_out),
        "bots": BOTS,
        "matches": matches_out,
        "note": (
            "Tips use form, league record, home factor, optional recent results/H2H and a simple score model. "
            "Injuries, new signings and manager stats are not on the free ESPN feed so they are not faked."
        ),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="today")
    args = ap.parse_args()
    d = parse_date(args.date)
    path = ROOT / f"fixtures-{d.isoformat()}.json"
    if not path.exists():
        print(f"No fixtures file {path} — run scrape_fixtures.py first")
        out = {
            "start_date": d.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "match_count": 0,
            "matches": [],
            "note": "No fixtures file found.",
        }
    else:
        fixtures = json.loads(path.read_text(encoding="utf-8"))
        out = build_tips(fixtures)
    out_path = ROOT / f"tips-{d.isoformat()}.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {out_path} matches={out.get('match_count')}")


if __name__ == "__main__":
    main()
