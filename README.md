# Football Bot Tips

Horse-style UI: **region tab → league dropdown → match expand**.

## What drives the tips
**Primary (venue form)**  
- Home team: last **5 home** games — goals scored, conceded, clean sheets  
- Away team: last **5 away** games — same  
- Corners / yellow cards / shots averages when ESPN match summaries exist  

**Secondary**  
- Overall form string  
- League position (standings)  

**When available**  
- H2H from recent schedules  
- Injuries only if ESPN injuries feed has data (often empty)

**Not invented**  
Top scorers, injuries, and manager stats are **not faked** when the feed is empty.

## Bet builders
Built from venue stats + predicted score, e.g. result, O/U goals, BTTS, corners, cards.

## Run
```bash
pip install -r requirements.txt
# fixtures for a week (Europe weekends)
python scrape_fixtures.py --date today --days 7 --no-enrich
# enrich (venue form + corners/cards) — slower; optional max
python scrape_fixtures.py --date today --days 7 --max-enrich 40
python generate_tips.py --date today
```
Open `index.html`.

## Bots
FormFox, HomeBanker, ValueHunter, DrawSpecialist, AttackMinded, H2HHistorian — each picks independently (agreement allowed).
