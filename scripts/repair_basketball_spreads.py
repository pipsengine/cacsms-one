"""One-off repair (12 Nov 2026): basketball handicap prices stored before the mapping fix.

Before the fix (sportybet_research._side_line) every away handicap was stored with the market's (home)
line, i.e. the wrong sign: "Away (-2.5)" became AWAY_SPREAD_2.5. Three-way "Handicap 0:2" prices were
also stored as point spreads. The SportyBet labels are kept on each observation, so the correct value
is read from the label itself.

* Away handicap with the wrong sign: snapshot outcome/line and observation canonical_market/line set
  to the selection's own handicap.
* Three-way score handicap: snapshot moved to market 'european_handicap' (no point-spread prediction
  matches it any more); observation canonical_market cleared.

Small batches with a pause in between, so the worker and the betting lane are never locked out for
long. Idempotent: rows already correct are skipped, so it can be stopped and re-run.

Usage: python scripts/repair_basketball_spreads.py [--dry-run] [--batch 500] [--log path]
"""
import argparse
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

OWN = re.compile(r'\(\s*([+-]?\d+(?:\.\d+)?)\s*\)')
SCORE = re.compile(r'\d+\s*:\s*\d+')


def plan(rows):
    """(snapshot_id, observation_id, kind, outcome, line) for rows that need a change."""
    out = []
    for r in rows:
        if SCORE.search(r['market_label'] or ''):
            out.append((r['odds_id'], r['id'], 'euro', None, None))
            continue
        m = re.fullmatch(r'(HOME|AWAY)_SPREAD_(-?\d+(?:\.\d+)?)', r['canonical_market'] or '')
        own = OWN.search(r['selection_label'] or '')
        if not m or not own:
            continue
        correct = float(own.group(1)) + 0.0
        if abs(correct - float(m.group(2))) > 1e-9:
            out.append((r['odds_id'], r['id'], 'sign', f'{m.group(1)}_SPREAD_{correct:g}', correct))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--batch', type=int, default=500)
    ap.add_argument('--pause', type=float, default=0.5)
    ap.add_argument('--log', default=None)
    args = ap.parse_args()
    log = open(args.log, 'a', encoding='utf-8') if args.log else sys.stdout
    db = os.environ.get('SPORTS_SQLITE_PATH') or str(ROOT / 'backend' / 'sports_demo.sqlite3')
    c = sqlite3.connect(db, timeout=120)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA busy_timeout=120000')
    fixed_sign, fixed_euro, conflicts, started = 0, 0, 0, time.time()
    # One read of the affected observations (a scan per batch took minutes each), then small write batches.
    everything = plan(c.execute(
        """SELECT id, odds_id, market_label, selection_label, canonical_market FROM sports_ai_sportybet_observations
           WHERE sport='basketball' AND canonical_market LIKE '%SPREAD%' AND odds_id IS NOT NULL""").fetchall())
    if c.in_transaction:
        c.commit()
    print(f'{time.strftime("%H:%M:%S")} to repair: {sum(1 for t in everything if t[2] == "sign"):,} wrong-sign, '
          f'{sum(1 for t in everything if t[2] == "euro"):,} three-way ({time.time() - started:,.0f}s to read)', file=log, flush=True)
    for start in range(0, len(everything), args.batch):
        todo = everything[start:start + args.batch]
        last = f'{start + len(todo):,}/{len(everything):,}'
        if todo and not args.dry_run:
            with c:  # one short transaction per batch
                for snap, obs, kind, outcome, line in todo:
                    if kind == 'euro':
                        c.execute("UPDATE sports_ai_odds_snapshots SET market='european_handicap' WHERE id=? AND market!='european_handicap'", (snap,))
                        c.execute('UPDATE sports_ai_sportybet_observations SET canonical_market=NULL WHERE id=?', (obs,))
                        fixed_euro += 1
                        continue
                    # Two steps: a unique temporary outcome first, so swapping +x and -x never collides.
                    c.execute("UPDATE sports_ai_odds_snapshots SET outcome='REPAIR|'||id WHERE id=? AND outcome!=?", (snap, outcome))
                for snap, obs, kind, outcome, line in todo:
                    if kind != 'sign':
                        continue
                    try:
                        c.execute("UPDATE sports_ai_odds_snapshots SET outcome=?, line=? WHERE id=? AND outcome LIKE 'REPAIR|%'",
                                  (outcome, line, snap))
                    except sqlite3.IntegrityError:
                        # The correct price for that time is already stored: this copy is redundant.
                        c.execute("UPDATE sports_ai_odds_snapshots SET market='spreads_superseded', outcome='REPAIR|'||id WHERE id=?", (snap,))
                        conflicts += 1
                    c.execute('UPDATE sports_ai_sportybet_observations SET canonical_market=?, line=? WHERE id=?', (outcome, line, obs))
                    fixed_sign += 1
        elif todo:
            fixed_sign += sum(1 for t in todo if t[2] == 'sign')
            fixed_euro += sum(1 for t in todo if t[2] == 'euro')
        print(f'{time.strftime("%H:%M:%S")} {last}: sign fixed {fixed_sign:,}, three-way moved {fixed_euro:,}, '
              f'duplicates {conflicts:,} ({time.time() - started:,.0f}s){" [dry run]" if args.dry_run else ""}', file=log, flush=True)
        time.sleep(args.pause)
    print(f'DONE: sign fixed {fixed_sign:,}, three-way moved {fixed_euro:,}, duplicates {conflicts:,} '
          f'in {time.time() - started:,.0f}s{" [dry run]" if args.dry_run else ""}', file=log, flush=True)


if __name__ == '__main__':
    main()
