#!/usr/bin/env python3
"""
Football tips: venue form (last 5 home / last 5 away) is primary.
Overall form & league position secondary. Bet builders from goals/corners/cards stats.
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
    "FormFox",
    "HomeBanker",
    "ValueHunter",
    "DrawSpecialist",
    "AttackMinded",
    "H2HHistorian",
]


def parse_date(arg: str) -> date:
    if arg.lower() == "today":
        return date.today()
    if arg.lower() == "tomorrow":
        return date.today() + timedelta(days=1)
    return datetime.strptime(arg, "%Y-%m-%d").date()


def fnum(x, default=None):
    if x is None:
        return default
    try:
        return float(x)
    except Exception:
        return default


def form_score(form: str) -> float:
    if not form:
        return 0.45
    chunk = form.upper().replace(" ", "")[-5:]
    pts = w = 0.0
    for i, c in enumerate(chunk):
        weight = 0.6 + 0.1 * i
        pts += (1.0 if c == "W" else 0.45 if c == "D" else 0.0) * weight
        w += weight
    return max(0.05, min(0.95, pts / max(w, 1)))


def poisson(lmbda: float, k: int) -> float:
    return math.exp(-lmbda) * (lmbda ** k) / math.factorial(k)


def predict_score(home_xg: float, away_xg: float):
    best, best_p = (1, 1), -1.0
    dist = {}
    for i in range(0, 6):
        for j in range(0, 6):
            p = poisson(max(0.2, home_xg), i) * poisson(max(0.2, away_xg), j)
            dist[(i, j)] = p
            if p > best_p:
                best_p, best = p, (i, j)
    ph = pd = pa = 0.0
    for (i, j), p in dist.items():
        if i > j:
            ph += p
        elif i == j:
            pd += p
        else:
            pa += p
    s = ph + pd + pa or 1
    return best[0], best[1], {"p_home": ph / s, "p_draw": pd / s, "p_away": pa / s, "p_score": best_p}


def match_features(m: dict) -> dict:
    home, away = m["home"], m["away"]
    hv = home.get("venue_form") or {}
    av = away.get("venue_form") or {}
    ho = home.get("overall_form") or {}
    ao = away.get("overall_form") or {}

    # PRIMARY: last 5 home / last 5 away
    h_gf = fnum(hv.get("gf_avg"), fnum(ho.get("gf_avg"), 1.2))
    h_ga = fnum(hv.get("ga_avg"), fnum(ho.get("ga_avg"), 1.2))
    a_gf = fnum(av.get("gf_avg"), fnum(ao.get("gf_avg"), 1.1))
    a_ga = fnum(av.get("ga_avg"), fnum(ao.get("ga_avg"), 1.3))
    h_cs = int(hv.get("cs") or 0)
    a_cs = int(av.get("cs") or 0)

    home_xg = 0.6 * h_gf + 0.4 * a_ga
    away_xg = 0.6 * a_gf + 0.4 * h_ga

    # SECONDARY: overall form string / overall last-5 (smaller weight)
    hf = form_score(home.get("form") or "")
    af = form_score(away.get("form") or "")
    home_xg = 0.8 * home_xg + 0.2 * home_xg * (0.85 + 0.3 * hf)
    away_xg = 0.8 * away_xg + 0.2 * away_xg * (0.85 + 0.3 * af)

    # Home advantage unless away in excellent form
    home_bump = 1.12
    if af >= 0.7:
        home_bump = 1.03
    elif af >= 0.6:
        home_bump = 1.07
    home_xg *= home_bump

    if h_cs >= 2:
        away_xg *= 0.9
    if a_cs >= 2:
        home_xg *= 0.92

    # League position tilt (small)
    hr = home.get("league_rank")
    ar = away.get("league_rank")
    if isinstance(hr, int) and isinstance(ar, int) and hr > 0 and ar > 0:
        # lower rank number = better
        if hr + 4 <= ar:
            home_xg *= 1.06
            away_xg *= 0.96
        elif ar + 4 <= hr:
            away_xg *= 1.06
            home_xg *= 0.96

    home_xg = max(0.25, min(3.6, home_xg))
    away_xg = max(0.25, min(3.4, away_xg))
    ph, pa, probs = predict_score(home_xg, away_xg)

    return {
        "home_gf": h_gf, "home_ga": h_ga, "away_gf": a_gf, "away_ga": a_ga,
        "home_cs": h_cs, "away_cs": a_cs,
        "home_xg": home_xg, "away_xg": away_xg,
        "pred_home": ph, "pred_away": pa, "probs": probs,
        "home_form": hf, "away_form": af,
        "home_corners": fnum(hv.get("corners_avg")),
        "away_corners": fnum(av.get("corners_avg")),
        "home_yellows": fnum(hv.get("yellows_avg")),
        "away_yellows": fnum(av.get("yellows_avg")),
        "home_shots": fnum(hv.get("shots_avg")),
        "away_shots": fnum(av.get("shots_avg")),
        "home_rank": hr, "away_rank": ar,
        "home_bump": home_bump,
        "hv": hv, "av": av,
    }


def bot_pick(bot: str, m: dict, feat: dict, rng: random.Random) -> dict:
    hf, af = feat["home_form"], feat["away_form"]
    home_s = 0.45 * (feat["home_gf"] / (feat["home_gf"] + feat["home_ga"] + 0.1)) + 0.25 * hf + 0.15
    away_s = 0.45 * (feat["away_gf"] / (feat["away_gf"] + feat["away_ga"] + 0.1)) + 0.25 * af + 0.10
    # venue edge into home
    home_s += 0.12 if feat["home_bump"] >= 1.1 else 0.04
    draw_s = 0.35 + 0.25 * (1 - abs(feat["home_xg"] - feat["away_xg"])) + 0.15 * (1 - abs(hf - af))

    if bot == "HomeBanker":
        home_s *= 1.18
    elif bot == "DrawSpecialist":
        draw_s *= 1.3
    elif bot == "ValueHunter":
        # lean against big rank gap if venue form close
        if feat["home_rank"] and feat["away_rank"]:
            if feat["home_rank"] + 5 <= feat["away_rank"] and abs(hf - af) < 0.1:
                away_s *= 1.1
            elif feat["away_rank"] + 5 <= feat["home_rank"] and abs(hf - af) < 0.1:
                home_s *= 1.1
    elif bot == "AttackMinded":
        draw_s *= 0.7
        if feat["home_xg"] >= feat["away_xg"]:
            home_s *= 1.1
        else:
            away_s *= 1.1
    elif bot == "H2HHistorian":
        h2h = m.get("h2h") or []
        if h2h:
            # crude: count results from home team perspective of THIS fixture
            hid = m["home"]["id"]
            hw = dr = aw = 0
            for g in h2h:
                hs, as_ = g["score"]
                if hs == as_:
                    dr += 1
                else:
                    home_won = (g["home_id"] == hid and hs > as_) or (g["away_id"] == hid and as_ > hs)
                    if home_won:
                        hw += 1
                    else:
                        aw += 1
            n = max(1, hw + dr + aw)
            home_s = 0.5 * home_s + 0.5 * (hw / n)
            draw_s = 0.5 * draw_s + 0.5 * (dr / n)
            away_s = 0.5 * away_s + 0.5 * (aw / n)
        # else no change

    home_s += rng.uniform(-0.02, 0.02)
    away_s += rng.uniform(-0.02, 0.02)
    draw_s += rng.uniform(-0.02, 0.02)
    scores = {"home": home_s, "draw": draw_s, "away": away_s}
    outcome = max(scores, key=scores.get)
    conf = int(max(44, min(82, 52 + 55 * (scores[outcome] - sorted(scores.values())[-2]))))

    ph, pa = feat["pred_home"], feat["pred_away"]
    if outcome == "home" and ph <= pa:
        ph = pa + 1
    if outcome == "away" and pa <= ph:
        pa = ph + 1
    if outcome == "draw":
        g = min(max(ph, pa, 1), 2)
        ph = pa = g if bot != "DrawSpecialist" else min(g, 1)

    if outcome == "home":
        selection, market = m["home"]["name"], "Home win"
    elif outcome == "away":
        selection, market = m["away"]["name"], "Away win"
    else:
        selection, market = "Draw", "Draw"

    reason = build_reason(bot, m, feat, outcome, ph, pa)
    builder = build_bet_builder(m, feat, outcome, ph, pa)

    return {
        "bot": bot,
        "outcome": outcome,
        "market": market,
        "selection": selection,
        "predicted_score": f"{ph}-{pa}",
        "conf": conf,
        "reason": reason,
        "bet_builder": builder,
        "probs": {k: round(v, 3) for k, v in feat["probs"].items() if k.startswith("p_")},
    }


def build_reason(bot, m, feat, outcome, ph, pa) -> str:
    home, away = m["home"]["name"], m["away"]["name"]
    league = m.get("league") or ""
    hv, av = feat.get("hv") or {}, feat.get("av") or {}
    bits = []

    bits.append(
        f"{league}: last-5 venue form is the main read — {home} at home "
        f"({feat['home_gf']:.1f} scored, {feat['home_ga']:.1f} conceded, {feat['home_cs']} clean sheets) "
        f"vs {away} away ({feat['away_gf']:.1f} scored, {feat['away_ga']:.1f} conceded, {feat['away_cs']} clean sheets)."
    )

    if bot == "FormFox":
        bits.append(
            f"Overall form strings (secondary): {home} {m['home'].get('form') or 'n/a'}, {away} {m['away'].get('form') or 'n/a'}."
        )
        if abs(feat["home_form"] - feat["away_form"]) < 0.1:
            bits.append("Overall form is close, so venue numbers decide more than the general string.")
    elif bot == "HomeBanker":
        if feat["home_bump"] <= 1.05:
            bits.append(f"Home edge is reduced because {away}’s recent form is strong.")
        else:
            bits.append(f"Home advantage still applies unless {away} clearly outplay these home numbers.")
    elif bot == "DrawSpecialist":
        bits.append(
            f"xG gap is {abs(feat['home_xg']-feat['away_xg']):.2f}; tight gaps and mutual clean-sheet habits support a stalemate."
        )
    elif bot == "AttackMinded":
        bits.append(
            f"Modelled goals ~{feat['home_xg']:.2f}–{feat['away_xg']:.2f}. "
            f"Shots averages (if known): home {feat.get('home_shots') or 'n/a'}, away {feat.get('away_shots') or 'n/a'}."
        )
    elif bot == "ValueHunter":
        bits.append(
            f"League positions: {home} #{feat.get('home_rank') or '?'} vs {away} #{feat.get('away_rank') or '?'}. "
            "If the table gap is large but venue form is close, the underdog venue line is interesting."
        )
    else:
        n = len(m.get("h2h") or [])
        bits.append(f"H2H meetings in loaded data: {n}." + (" Used as a light tilt only." if n else " None available — not invented."))

    if feat.get("home_corners") is not None or feat.get("away_corners") is not None:
        bits.append(
            f"Corners avg (venue sample): {home} {feat.get('home_corners') or 'n/a'}, {away} {feat.get('away_corners') or 'n/a'}. "
            f"Yellows avg: {feat.get('home_yellows') or 'n/a'} / {feat.get('away_yellows') or 'n/a'}."
        )

    if outcome == "home":
        bits.append(f"Pick: {home} to win; predicted score {ph}-{pa}.")
    elif outcome == "away":
        bits.append(f"Pick: {away} to win; predicted score {ph}-{pa}.")
    else:
        bits.append(f"Pick: draw; predicted score {ph}-{pa}.")

    bits.append(
        "Overall form and league position are secondary to home/away venue samples. "
        "Injuries/top scorers only used when present on the feed (often missing)."
    )
    return " ".join(bits)


def build_bet_builder(m, feat, outcome, ph, pa) -> dict:
    home, away = m["home"]["name"], m["away"]["name"]
    total = ph + pa
    legs = []
    if outcome == "home":
        legs.append(f"{home} to win")
    elif outcome == "away":
        legs.append(f"{away} to win")
    else:
        legs.append("Draw")

    if total >= 3:
        legs.append("Over 2.5 goals")
    else:
        legs.append("Under 2.5 goals")

    if ph > 0 and pa > 0:
        legs.append("BTTS — Yes")
    else:
        legs.append("BTTS — No")

    # corners from averages
    hc = feat.get("home_corners")
    ac = feat.get("away_corners")
    if hc is not None and ac is not None:
        est = hc + ac
        if est >= 10.5:
            legs.append("Over 9.5 corners")
        elif est <= 8.0:
            legs.append("Under 9.5 corners")
        else:
            legs.append("Over 8.5 corners")
    else:
        # fallback from goals tempo
        if total >= 3:
            legs.append("Over 8.5 corners")
        else:
            legs.append("Under 10.5 corners")

    hy = feat.get("home_yellows")
    ay = feat.get("away_yellows")
    if hy is not None and ay is not None and (hy + ay) >= 3.5:
        legs.append("Over 3.5 cards in match")
    else:
        legs.append("Card market: check book (sample thin)")

    safer = [legs[0], "Over 1.5 goals" if total >= 2 else "Under 3.5 goals"]
    return {
        "primary_legs": legs[:5],
        "safer_legs": safer,
        "note": "Builder legs come from venue goal/corner/card samples + predicted score. Prices not fetched — check your book.",
    }


def build_tips(fixtures: dict) -> dict:
    matches_out = []
    for idx, m in enumerate(fixtures.get("matches") or []):
        # skip if not enriched enough? still tip from form string
        feat = match_features(m)
        rng = random.Random(f"{m.get('id')}|{idx}")
        tips = [bot_pick(bot, m, feat, rng) for bot in BOTS]
        stars = Counter(t["selection"] for t in tips)
        for t in tips:
            t["stars"] = stars[t["selection"]]
        votes = Counter(t["outcome"] for t in tips)
        matches_out.append({
            "id": m.get("id"),
            "date": m.get("date"),
            "kickoff": m.get("kickoff"),
            "league": m.get("league"),
            "league_code": m.get("league_code"),
            "region": m.get("region"),
            "venue": m.get("venue"),
            "home": m["home"]["name"],
            "away": m["away"]["name"],
            "home_form": m["home"].get("form"),
            "away_form": m["away"].get("form"),
            "home_rank": m["home"].get("league_rank"),
            "away_rank": m["away"].get("league_rank"),
            "home_venue": m["home"].get("venue_form"),
            "away_venue": m["away"].get("venue_form"),
            "model_score": f"{feat['pred_home']}-{feat['pred_away']}",
            "model_probs": feat["probs"],
            "consensus": votes.most_common(1)[0][0],
            "tips": tips,
        })

    # group helper for UI
    by_region = {}
    for m in matches_out:
        by_region.setdefault(m.get("region") or "Other", {}).setdefault(m.get("league") or "Other", []).append(m)

    return {
        "start_date": fixtures.get("start_date"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "match_count": len(matches_out),
        "bots": BOTS,
        "matches": matches_out,
        "by_region": {r: {l: len(v) for l, v in leagues.items()} for r, leagues in by_region.items()},
        "note": (
            "Primary: last-5 home form for the home side and last-5 away form for the away side "
            "(goals, conceded, clean sheets; corners/cards when match stats exist). "
            "Secondary: overall form string and league position. "
            "Injuries/top scorers only when the feed provides them."
        ),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="today")
    args = ap.parse_args()
    d = parse_date(args.date)
    path = ROOT / f"fixtures-{d.isoformat()}.json"
    if not path.exists():
        out = {"start_date": d.isoformat(), "match_count": 0, "matches": [], "note": "No fixtures file."}
    else:
        out = build_tips(json.loads(path.read_text(encoding="utf-8")))
    out_path = ROOT / f"tips-{d.isoformat()}.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {out_path} matches={out.get('match_count')}")


if __name__ == "__main__":
    main()
