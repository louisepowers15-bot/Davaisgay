# Football Bot Tips

Multi-bot football tipping board (same idea as the horse system).

## Coverage
- **England:** Premier League, Championship, League One, League Two, FA Cup, EFL Cup
- **Scotland:** Premiership, Scottish Cup (when listed)
- **Europe top leagues:** La Liga, Serie A, Bundesliga, Ligue 1, Eredivisie, Primeira Liga
- **UEFA:** Champions League, Europa League, Conference League

## What bots use (free ESPN feed)
- Recent form string (e.g. `WDLWW`)
- League record summary (W-D-L)
- Home advantage
- Recent results + H2H when `--enrich` is used
- Simple Poisson-style score model

**Not invented:** injuries, new signings, manager career stats (not on this free feed). Reasons say so.

## Bots
| Bot | Angle |
|-----|--------|
| FormFox | Recent form |
| HomeBanker | Home edge |
| ValueHunter | Form vs record disagreement |
| DrawSpecialist | Tight games / draws |
| AttackMinded | Goals / scorelines |
| H2HHistorian | Head-to-head |

Each bot picks **home win / draw / away win**, a **predicted score**, written **reasoning**, and a **bet builder** leg list.

## Run locally
```bash
pip install -r requirements.txt
python scrape_fixtures.py --date today --days 3 --enrich
python generate_tips.py --date today
```
Open `index.html` (or serve the folder).

## GitHub Actions
Copy `update-tips.yml` into `.github/workflows/` on a new repo and enable Pages.
