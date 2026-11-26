#!/usr/bin/env python3
"""
Settle football 1X2 tips against results and rebuild leaderboard.json.

Level £1 stake per tip:
  correct pick -> return = decimal odds (profit = odds - 1)
  wrong        -> return = 0 (loss = 1)

Idempotent: writes settled-YYYY-MM-DD.json, then rebuilds leaderboard from ALL settled-*.json.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BOTS = [
    "FormFox", "HomeStrong", "AwayHunter",
    "ValueSeeker", "GoalsGuru", "DevilAdvocate",
]


def parse_date(arg: str) -> date:
    if arg.lower() == "today":
        return date.today()
    if arg.lower() == "yesterday":
        return date.today() - timedelta(days=1)
    return datetime.strptime(arg, "%Y-%m-%d").date()


def norm(name: str) -> str:
    return " ".join(str(name or "").lower().replace(".", "").replace("fc", "").split())


def load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def result_lookup(results: dict) -> dict:
    """Map match id and normalised name -> outcome."""
    by_id = {}
    by_name = {}
    for m in results.get("matches") or []:
        by_id[str(m.get("id") or "")] = m
        by_name[norm(m.get("name") or "")] = m
        # also home vs away key
        h = norm((m.get("home") or {}).get("name") or "")
        a = norm((m.get("away") or {}).get("name") or "")
        if h and a:
            by_name[f"{h} vs {a}"] = m
            by_name[f"{h} v {a}"] = m
    return {"by_id": by_id, "by_name": by_name}


def find_result(match: dict, idx: dict):
    mid = str(match.get("id") or "")
    if mid and mid in idx["by_id"]:
        return idx["by_id"][mid]
    name = norm(match.get("name") or "")
    if name in idx["by_name"]:
        return idx["by_name"][name]
    h = norm((match.get("home") or {}).get("name") or "")
    a = norm((match.get("away") or {}).get("name") or "")
    for key in (f"{h} vs {a}", f"{h} v {a}"):
        if key in idx["by_name"]:
            return idx["by_name"][key]
    return None


def settle_day(d: date) -> dict | None:
    tips = load_json(ROOT / f"tips-{d.isoformat()}.json")
    results = load_json(ROOT / f"results-{d.isoformat()}.json")
    if not tips or not results:
        print(f"Missing tips or results for {d}")
        return None
    if not results.get("match_count"):
        print(f"No finished matches in results for {d}")
        return None

    idx = result_lookup(results)
    settled_tips = []
    bot_day = {b: {"tips": 0, "wins": 0, "stake": 0.0, "returns": 0.0, "odds_sum": 0.0} for b in BOTS}
    consensus_day = {"tips": 0, "wins": 0, "stake": 0.0, "returns": 0.0}

    for lg in tips.get("leagues") or []:
        for match in lg.get("matches") or []:
            res = find_result(match, idx)
            if not res:
                continue
            outcome = res.get("outcome")  # home|draw|away
            if outcome not in ("home", "draw", "away"):
                continue

            # per-bot tips
            for t in match.get("tips") or []:
                bot = t.get("bot")
                if bot not in bot_day:
                    continue
                pick = t.get("pick")
                try:
                    odds = float(t.get("odds") or (match.get("odds") or {}).get(pick) or 2.5)
                except (TypeError, ValueError):
                    odds = 2.5
                odds = max(1.01, odds)
                win = pick == outcome
                ret = odds if win else 0.0
                bot_day[bot]["tips"] += 1
                bot_day[bot]["stake"] += 1.0
                bot_day[bot]["returns"] += ret
                bot_day[bot]["odds_sum"] += odds
                if win:
                    bot_day[bot]["wins"] += 1
                settled_tips.append({
                    "bot": bot,
                    "match": match.get("name"),
                    "league": lg.get("name"),
                    "pick": pick,
                    "pick_label": t.get("pick_label"),
                    "odds": odds,
                    "conf": t.get("conf"),
                    "outcome": outcome,
                    "ft": res.get("ft"),
                    "won": win,
                    "return": round(ret, 4),
                })

            # consensus (3+ bots)
            cons = match.get("consensus") or {}
            if int(cons.get("bot_count") or 0) >= 3 and cons.get("pick") in ("home", "draw", "away"):
                pick = cons["pick"]
                try:
                    odds = float(cons.get("odds") or (match.get("odds") or {}).get(pick) or 2.5)
                except (TypeError, ValueError):
                    odds = 2.5
                odds = max(1.01, odds)
                win = pick == outcome
                ret = odds if win else 0.0
                consensus_day["tips"] += 1
                consensus_day["stake"] += 1.0
                consensus_day["returns"] += ret
                if win:
                    consensus_day["wins"] += 1

    payload = {
        "date": d.isoformat(),
        "settled_at": datetime.now(timezone.utc).isoformat(),
        "tips": settled_tips,
        "bots": bot_day,
        "consensus": consensus_day,
        "n_settled": len(settled_tips),
    }
    out = ROOT / f"settled-{d.isoformat()}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Settled {d}: {len(settled_tips)} tip rows, consensus tips={consensus_day['tips']}")
    return payload


def rebuild_leaderboard() -> dict:
    empty = lambda: {
        "tips": 0, "wins": 0, "stake": 0.0, "returns": 0.0,
        "odds_sum": 0.0, "win_pct": 0.0, "roi": 0.0, "avg_odds": 0.0, "pnl": 0.0,
    }
    bots = {b: empty() for b in BOTS}
    consensus = empty()
    by_day = []
    last = None

    for path in sorted(ROOT.glob("settled-*.json")):
        data = load_json(path)
        if not data:
            continue
        last = data.get("date")
        day_pnl = 0.0
        for b, st in (data.get("bots") or {}).items():
            if b not in bots:
                bots[b] = empty()
            bots[b]["tips"] += int(st.get("tips") or 0)
            bots[b]["wins"] += int(st.get("wins") or 0)
            bots[b]["stake"] += float(st.get("stake") or 0)
            bots[b]["returns"] += float(st.get("returns") or 0)
            bots[b]["odds_sum"] += float(st.get("odds_sum") or 0)
            day_pnl += float(st.get("returns") or 0) - float(st.get("stake") or 0)
        c = data.get("consensus") or {}
        consensus["tips"] += int(c.get("tips") or 0)
        consensus["wins"] += int(c.get("wins") or 0)
        consensus["stake"] += float(c.get("stake") or 0)
        consensus["returns"] += float(c.get("returns") or 0)
        by_day.append({"date": data.get("date"), "pnl": round(day_pnl, 2)})

    def finalise(st: dict) -> dict:
        tips = st["tips"]
        stake = st["stake"] or 1e-9
        st["win_pct"] = round(100.0 * st["wins"] / tips, 1) if tips else 0.0
        st["roi"] = round(100.0 * (st["returns"] - st["stake"]) / stake, 1) if tips else 0.0
        st["pnl"] = round(st["returns"] - st["stake"], 2)
        st["avg_odds"] = round(st["odds_sum"] / tips, 2) if tips else 0.0
        return st

    for b in bots:
        bots[b] = finalise(bots[b])
    consensus = finalise(consensus)

    lb = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "last_settled": last,
        "stake_unit": 1.0,
        "bots": bots,
        "consensus": consensus,
        "daily_pnl": by_day[-14:],
        "note": "Level £1 win stakes on 1X2 tips. ROI = (returns - stake) / stake.",
    }
    (ROOT / "leaderboard.json").write_text(json.dumps(lb, indent=2), encoding="utf-8")
    print(f"Leaderboard updated last_settled={last}")
    for b, st in sorted(bots.items(), key=lambda x: -x[1]["roi"]):
        print(f"  {b}: tips={st['tips']} win%={st['win_pct']} ROI={st['roi']}%")
    print(f"  Consensus: tips={consensus['tips']} win%={consensus['win_pct']} ROI={consensus['roi']}%")
    return lb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="yesterday")
    ap.add_argument("--rebuild-only", action="store_true")
    args = ap.parse_args()
    if not args.rebuild_only:
        settle_day(parse_date(args.date))
    rebuild_leaderboard()


if __name__ == "__main__":
    main()
