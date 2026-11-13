"""Fast time-window reads of sports_ai_odds_snapshots without an observed_at index.

The table holds millions of price rows and has no index on observed_at, so "prices since X" counts read
the whole table (10-30 s each on the live database, 13 Nov 2026). Rows are appended as prices are
observed, so rowid grows with observed_at. A binary search over rowid (each probe is one rowid lookup)
finds the first row at or after X; the query then reads only rows from there and still filters on
observed_at itself, so the result is exact for rows stored in time order.
"""


def first_rowid_since(connection, since_iso):
    """Smallest rowid whose observed_at is >= since_iso (rowid order follows observation order)."""
    # Two statements: SQLite optimises MIN(rowid) or MAX(rowid) alone to one lookup, but both in one
    # statement is a full-table scan (13 s on the live database).
    lo = connection.execute('SELECT MIN(rowid) FROM sports_ai_odds_snapshots').fetchone()[0]
    hi = connection.execute('SELECT MAX(rowid) FROM sports_ai_odds_snapshots').fetchone()[0]
    if lo is None:
        return 0
    probe = 'SELECT rowid, observed_at FROM sports_ai_odds_snapshots WHERE rowid>=? ORDER BY rowid LIMIT 1'
    last = connection.execute('SELECT observed_at FROM sports_ai_odds_snapshots WHERE rowid=?', (hi,)).fetchone()
    if last is None or str(last[0]) < since_iso:
        return hi + 1
    while lo < hi:
        mid = (lo + hi) // 2
        row = connection.execute(probe, (mid,)).fetchone()
        if row is None or str(row[1]) >= since_iso:
            hi = mid if row is None else min(hi, row[0])
        else:
            lo = row[0] + 1
    return lo
