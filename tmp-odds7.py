import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'backend'))
from db import connect
from high_odds_board import _candidates, _day_window

now = datetime.now(timezone.utc)
with connect() as connection:
    for day in _day_window(now):
        rows = _candidates(connection, day, now)
        counts = Counter()
        for row in rows:
            odds = float(row['decimal_odds'])
            if odds < 2:
                band = '<2'
            elif odds < 3:
                band = '2-3'
            elif odds < 5:
                band = '3-5'
            else:
                band = '5+'
            counts[f"{row.get('sport')}:{band}"] += 1
        print(day['name'], 'candidates', len(rows), dict(counts))
        sample = sorted(rows, key=lambda row: -float(row['decimal_odds']))[:8]
        for row in sample:
            print(' ', round(float(row['decimal_odds']), 2), round(float(row['probability']), 3), row.get('sport'), row.get('home'), 'vs', row.get('away'), row.get('market'))
