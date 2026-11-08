import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'backend'))
from db import connect
from high_odds_board import _candidates, _day_window, build_day
from ticket_diversity import unallocated

now = datetime.now(timezone.utc)
with connect() as connection:
    for day in _day_window(now):
        rows = unallocated(connection, _candidates(connection, day, now), now)
        counts = Counter()
        for row in rows:
            odds = float(row['decimal_odds'])
            if odds < 3:
                band = '<3'
            elif odds < 4:
                band = '3-4'
            elif odds <= 5:
                band = '4-5'
            else:
                band = '>5'
            if float(row['probability']) >= 0.80 and row.get('sport') in ('football', 'basketball'):
                counts[f"{row['sport']}:{band}"] += 1
        print(day['name'], dict(counts), 'rows', len(rows))
        built = build_day(rows, day)
        for ticket in built['tickets']:
            if ticket['band'] not in ('3-4', '4-5', 'above-5'):
                continue
            print(
                day['name'], ticket['band'], ticket['status'],
                ticket.get('selection_count'), ticket.get('combined_odds'),
                (ticket.get('reason') or '')[:220],
            )
