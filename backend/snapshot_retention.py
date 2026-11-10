"""Remove redundant odds history only after every related ticket is settled.

Ticket legs retain their immutable price/model evidence. Feature snapshots and
predictions are retained for evaluation; this job deletes raw odds snapshots.
"""
import json
import uuid
from collections import Counter
from datetime import datetime, timezone


REQUIRED_INDEXES = ('sports_ai_value_opportunities_odds', 'sports_ai_sportybet_observations_odds')


def cleanup_settled_odds(connection, now=None, batch_size=5000):
    # Without the odds_id indexes (migration 052) each delete scans millions of rows
    # while holding the write lock, which stalls the whole worker. Never run that way.
    present = {row[0] for row in connection.execute(
        f"SELECT name FROM sqlite_master WHERE type='index' AND name IN ({','.join('?' * len(REQUIRED_INDEXES))})",
        REQUIRED_INDEXES,
    )}
    if present != set(REQUIRED_INDEXES):
        return {'deleted_odds_snapshots': 0, 'status': 'skipped',
                'reason': 'odds_id indexes missing; apply migration 052'}
    # Verification and deletion see the same database snapshot. A concurrent
    # result correction must cause a retry, never a partially committed cleanup.
    if not connection.in_transaction:
        connection.execute('BEGIN IMMEDIATE')
    connection.execute('SAVEPOINT settled_snapshot_cleanup')
    try:
        result = _cleanup_settled_odds(connection, now, batch_size)
        connection.execute('RELEASE SAVEPOINT settled_snapshot_cleanup')
        return result
    except BaseException:
        connection.execute('ROLLBACK TO SAVEPOINT settled_snapshot_cleanup')
        connection.execute('RELEASE SAVEPOINT settled_snapshot_cleanup')
        raise


def _cleanup_settled_odds(connection, now, batch_size):
    from accumulator_engine import _parse_time, settle_result, ticket_outcome

    current = now or datetime.now(timezone.utc)
    batch_size = max(1, min(int(batch_size), 5000))
    verified = {}
    blocked_tickets = {}

    def blocked(ticket_id, reason):
        blocked_tickets[ticket_id] = reason
        return False

    def fully_settled(ticket_id):
        if ticket_id in verified:
            return verified[ticket_id]
        verified[ticket_id] = False
        settlement = connection.execute(
            'SELECT outcome FROM sports_ai_ticket_settlements WHERE ticket_id=?',
            (ticket_id,),
        ).fetchone()
        legs = [dict(row) for row in connection.execute(
            'SELECT * FROM sports_ai_accumulator_legs WHERE ticket_id=?', (ticket_id,))]
        if not legs:
            return blocked(ticket_id, 'ticket_has_no_legs')
        for leg in legs:
            kickoff = _parse_time(leg['starts_at'])
            result = connection.execute('''SELECT r.*, f.status AS fixture_status
                FROM sports_ai_results r JOIN sports_ai_fixtures f ON f.id=r.fixture_id
                WHERE r.fixture_id=?''', (leg['fixture_id'],)).fetchone()
            if not kickoff:
                return blocked(ticket_id, 'invalid_kickoff')
            if kickoff > current:
                return blocked(ticket_id, 'future_match')
            if not result or result['status'] != 'final':
                return blocked(ticket_id, 'missing_final_result')
            settled_at = _parse_time(result['settled_at'])
            if not settled_at or not kickoff <= settled_at <= current:
                return blocked(ticket_id, 'invalid_result_timestamp')
            if result['fixture_status'] in ('postponed', 'abandoned', 'cancelled'):
                return blocked(ticket_id, 'fixture_not_final')
            if connection.execute('''SELECT 1 FROM sports_ai_data_quality_issues
                WHERE fixture_id=? AND resolved_at IS NULL
                  AND severity IN ('high','critical') LIMIT 1''', (leg['fixture_id'],)).fetchone():
                return blocked(ticket_id, 'unresolved_data_issue')
            outcome = settle_result(leg['sport'], leg['market'], result['home_score'], result['away_score'])
            if outcome is None or outcome != leg['result']:
                return blocked(ticket_id, 'leg_result_unverified')
        if not settlement or settlement['outcome'] not in ('won', 'lost', 'void', 'partial'):
            return blocked(ticket_id, 'ticket_not_settled')
        if ticket_outcome(legs)[0] != settlement['outcome']:
            return blocked(ticket_id, 'ticket_outcome_mismatch')
        verified[ticket_id] = True
        return True

    # Process distinct fixtures, bounding deletions to keep writes small.
    fixtures = connection.execute('''SELECT DISTINCT l.fixture_id
        FROM sports_ai_accumulator_legs l
        JOIN sports_ai_ticket_settlements s ON s.ticket_id=l.ticket_id
        WHERE s.outcome IN ('won','lost','void','partial')
          AND EXISTS (SELECT 1 FROM sports_ai_odds_snapshots q WHERE q.fixture_id=l.fixture_id)
        ORDER BY l.fixture_id''').fetchall()
    removed = 0
    cleaned = []
    blockers = Counter()
    checked = 0
    for row in fixtures:
        if removed >= batch_size:
            break
        fixture_id = row['fixture_id']
        checked += 1
        # Never remove shared evidence while any other ticket is unfinished.
        tickets = connection.execute('''SELECT DISTINCT ticket_id
            FROM sports_ai_accumulator_legs WHERE fixture_id=?''', (fixture_id,)).fetchall()
        unverified = [ticket['ticket_id'] for ticket in tickets if not fully_settled(ticket['ticket_id'])]
        if unverified:
            blockers['shared_unsettled_ticket'] += 1
            continue
        # Legacy tickets lack the verified settlement ledger used above.
        if connection.execute('SELECT 1 FROM sports_ai_ticket_legs WHERE fixture_id=? LIMIT 1',
                              (fixture_id,)).fetchone():
            blockers['legacy_ticket_reference'] += 1
            continue
        ids = [r['id'] for r in connection.execute('''SELECT id FROM sports_ai_odds_snapshots
            WHERE fixture_id=? ORDER BY id LIMIT ?''', (fixture_id, batch_size - removed))]
        if not ids:
            continue
        # Chunk parameters for SQLite builds with the older variable limit.
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            placeholders = ','.join('?' for _ in chunk)
            connection.execute(f'''UPDATE sports_ai_sportybet_observations SET odds_id=NULL
                WHERE odds_id IN ({placeholders})''', chunk)
            # Value-opportunity references use ON DELETE SET NULL. Preserve
            # opportunities, predictions, ticket legs, codes and settlements.
            connection.execute(f'DELETE FROM sports_ai_odds_snapshots WHERE id IN ({placeholders})', chunk)
        removed += len(ids)
        cleaned.append(fixture_id)
    if removed:
        connection.execute('''INSERT INTO sports_ai_audit_events
            (id,actor,action,metadata_json,created_at) VALUES(?,?,?,?,?)''',
            (str(uuid.uuid4()), 'sports-ai-worker', 'snapshots.settled_cleanup',
             json.dumps({'deleted_odds_snapshots': removed, 'fixture_ids': cleaned,
                         'verified_ticket_ids': [key for key, value in verified.items() if value]}),
             current.isoformat()))
    summary = {'deleted_odds_snapshots': removed, 'fixtures': len(cleaned),
               'checked_at': current.isoformat(), 'candidate_fixtures': len(fixtures),
               'fixtures_checked': checked, 'blocked_fixtures': sum(blockers.values()),
               'blocker_counts': dict(blockers),
               'ticket_blocker_counts': dict(Counter(blocked_tickets.values())),
               'status': 'deleted' if removed else 'blocked' if blockers else 'nothing_to_clean'}
    connection.execute('''INSERT INTO sports_ai_settings(key,value_json,updated_at)
        VALUES('snapshot_cleanup',?,?) ON CONFLICT(key) DO UPDATE SET
        value_json=excluded.value_json,updated_at=excluded.updated_at''',
        (json.dumps(summary), current.isoformat()))
    return summary
