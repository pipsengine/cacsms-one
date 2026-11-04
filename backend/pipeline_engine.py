"""Connect discovery, prices, qualification and tickets.

The monitor counts persisted rows. It does not invent prices, forecasts or booking codes,
and it does not place a wager.
"""

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from booking_schedule import next_publication, phase_at, publication_open

STAGES = (
    'DISCOVERED', 'DATA_READY', 'PREDICTED', 'ODDS_AVAILABLE', 'ODDS_FRESH',
    'QUALIFIED', 'TICKET_READY', 'BOOKING_PENDING', 'CODE_VERIFIED', 'DISPLAYED',
)
HEADLINE = ('HOME', 'DRAW', 'AWAY', 'MONEYLINE_HOME', 'MONEYLINE_AWAY')
QUOTE_OUTCOME = {
    'HOME': 'home', 'AWAY': 'away', 'DRAW': 'draw',
    'MONEYLINE_HOME': 'home', 'MONEYLINE_AWAY': 'away',
}
MAX_ATTEMPTS = 4


def _now(value=None):
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return None
    return current.astimezone(timezone.utc)


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def freshness_minutes(connection):
    """The stored freshness window, defaulting to 15 minutes and never above the policy cap."""
    row = connection.execute("SELECT value_json FROM sports_ai_settings WHERE key='engine'").fetchone()
    minutes = 15
    if row:
        try:
            minutes = int(json.loads(row['value_json']).get('odds_max_age_minutes', 15))
        except (TypeError, ValueError, json.JSONDecodeError):
            minutes = 15
    if not 1 <= minutes <= 180:
        return 15
    return minutes


def autonomous_tickets_enabled(connection):
    row = connection.execute("SELECT value_json FROM sports_ai_settings WHERE key='engine'").fetchone()
    if not row:
        return True
    try:
        stored = json.loads(row['value_json'])
    except (TypeError, json.JSONDecodeError):
        return True
    return stored.get('autonomous_tickets', True) is not False


def classify_observations(connection):
    """Separate timestamp order from an independently observed pre-match forecast.

    created_at is not rewritten. A row is live_observed only when the feature snapshot
    was generated before kickoff, was available when the forecast was stored, uses the
    fixture kickoff as its cutoff, and no final result was already stored.
    """
    rows = connection.execute(
        """SELECT p.id, p.created_at, p.feature_id, f.starts_at,
                  s.generated_at, s.cutoff_at, r.settled_at
           FROM sports_ai_engine_predictions p
           JOIN sports_ai_fixtures f ON f.id=p.fixture_id
           LEFT JOIN sports_ai_feature_snapshots s ON s.id=p.feature_id
           LEFT JOIN sports_ai_results r ON r.fixture_id=p.fixture_id AND r.status='final'
           WHERE p.observation_class IS NULL OR p.observation_class=''""",
    ).fetchall()
    updates = []
    for row in rows:
        created, kickoff = _parse(row['created_at']), _parse(row['starts_at'])
        if created and kickoff and created >= kickoff:
            label = 'historical'
        else:
            generated, cutoff, settled = _parse(row['generated_at']), _parse(row['cutoff_at']), _parse(row['settled_at'])
            cutoff_is_kickoff = bool(created and kickoff and cutoff and abs((cutoff - kickoff).total_seconds()) < 1)
            features_ready = bool(
                created and kickoff and generated
                and generated < kickoff
                and generated <= created + timedelta(seconds=5)
            )
            result_already_stored = bool(created and settled and settled < created)
            label = 'live_observed' if cutoff_is_kickoff and features_ready and not result_already_stored else 'timestamp_eligible'
        updates.append((label, row['id']))
    for offset in range(0, len(updates), 1000):
        connection.executemany(
            'UPDATE sports_ai_engine_predictions SET observation_class=? WHERE id=?',
            updates[offset:offset + 1000],
        )
    summary = connection.execute(
        """SELECT
             SUM(CASE WHEN observation_class='live_observed' THEN 1 ELSE 0 END) AS live_observed,
             SUM(CASE WHEN observation_class='timestamp_eligible' THEN 1 ELSE 0 END) AS timestamp_eligible,
             SUM(CASE WHEN observation_class='historical' THEN 1 ELSE 0 END) AS historical,
             SUM(CASE WHEN observation_class='live_observed' AND EXISTS (
               SELECT 1 FROM sports_ai_results r
               WHERE r.fixture_id=sports_ai_engine_predictions.fixture_id AND r.status='final'
                 AND r.settled_at>=(
                   SELECT starts_at FROM sports_ai_fixtures f WHERE f.id=sports_ai_engine_predictions.fixture_id
                 )
             ) THEN 1 ELSE 0 END) AS live_settled
           FROM sports_ai_engine_predictions""",
    ).fetchone()
    return {key: int(summary[key] or 0) for key in ('live_observed', 'timestamp_eligible', 'historical', 'live_settled')}


def enqueue_odds_refresh(connection, now=None, limit=80):
    """Queue fixtures whose active forecast has no current SportyBet price. No price is created."""
    current = _now(now) or datetime.now(timezone.utc)
    minutes = freshness_minutes(connection)
    fresh_after = (current - timedelta(minutes=minutes)).isoformat()
    from prediction_engine import upcoming_fixture_sql
    rows = connection.execute(
        f"""SELECT f.id AS fixture_id, f.starts_at, o.expected_edge, q.observed_at
            FROM sports_ai_fixtures f
            JOIN sports_ai_engine_predictions p ON p.fixture_id=f.id
            JOIN sports_ai_model_registry m ON m.id=p.model_id AND m.active=1
            LEFT JOIN sports_ai_value_opportunities o ON o.prediction_id=p.id
              AND o.evaluated_at=(
                SELECT MAX(o2.evaluated_at) FROM sports_ai_value_opportunities o2 WHERE o2.prediction_id=p.id
              )
            LEFT JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id AND q.bookmaker='SportyBet'
            WHERE {upcoming_fixture_sql('f')}
              AND p.market IN ('HOME','DRAW','AWAY','MONEYLINE_HOME','MONEYLINE_AWAY')
              AND p.created_at=(
                SELECT MAX(p2.created_at) FROM sports_ai_engine_predictions p2
                WHERE p2.fixture_id=p.fixture_id AND p2.market=p.market
              )
              AND (q.id IS NULL OR q.observed_at<?)
            GROUP BY f.id
            ORDER BY f.starts_at
            LIMIT ?""",
        (fresh_after, limit),
    ).fetchall()
    queued = 0
    for row in rows:
        existing = connection.execute(
            'SELECT status, next_attempt_at FROM sports_ai_odds_refresh_queue WHERE fixture_id=?',
            (row['fixture_id'],),
        ).fetchone()
        nxt = _parse(existing['next_attempt_at']) if existing else None
        if existing and existing['status'] == 'deferred' and nxt and nxt > current:
            continue
        kickoff = _parse(row['starts_at'])
        distance = 10**6 if not kickoff else max(1, (kickoff - current).total_seconds() / 60)
        priority = 10000 / distance
        if row['expected_edge'] is not None:
            priority += float(row['expected_edge']) * 100
        reason = 'price_missing' if not row['observed_at'] else 'price_stale'
        if not row['observed_at']:
            priority += 30
        connection.execute(
            """INSERT INTO sports_ai_odds_refresh_queue
               (fixture_id,priority,status,attempt,next_attempt_at,last_error,reason,updated_at)
               VALUES(?,?,'queued',0,NULL,NULL,?,?)
               ON CONFLICT(fixture_id) DO UPDATE SET
                 priority=excluded.priority, status='queued', reason=excluded.reason, updated_at=excluded.updated_at
               WHERE sports_ai_odds_refresh_queue.status!='deferred'
                  OR sports_ai_odds_refresh_queue.next_attempt_at IS NULL
                  OR sports_ai_odds_refresh_queue.next_attempt_at<=excluded.updated_at""",
            (row['fixture_id'], priority, reason, current.isoformat()),
        )
        queued += 1
    return {'queued': queued, 'places_wager': False, 'odds_invented': 0}


def _feed_blocked(connection):
    row = connection.execute(
        """SELECT status, error FROM sports_ai_browser_jobs
           WHERE url='https://www.sportybet.com/ng/sport/basketball'
           ORDER BY created_at DESC LIMIT 1""",
    ).fetchone()
    if not row or row['status'] != 'failed':
        return False
    error = (row['error'] or '').lower()
    return 'challenge' in error or 'blocked' in error


def _fresh_quotes(connection, fixture_id, fresh_after):
    chosen = {}
    rows = connection.execute(
        """SELECT id, market, outcome, decimal_odds, observed_at
           FROM sports_ai_odds_snapshots
           WHERE fixture_id=? AND bookmaker='SportyBet' AND observed_at>=?
           ORDER BY observed_at DESC""",
        (fixture_id, fresh_after),
    ).fetchall()
    for row in rows:
        market = (row['market'] or '').upper()
        outcome = (row['outcome'] or '').lower()
        for canonical, expected in QUOTE_OUTCOME.items():
            if canonical in chosen:
                continue
            if market == canonical or (market == 'H2H' and outcome == expected):
                chosen[canonical] = row
    return chosen


def process_odds_refresh(connection, now=None, limit=40):
    """Attach a verified current price when one is already stored. Never synthesise a price."""
    current = _now(now) or datetime.now(timezone.utc)
    before = connection.execute('SELECT COUNT(*) FROM sports_ai_odds_snapshots').fetchone()[0]
    minutes = freshness_minutes(connection)
    fresh_after = (current - timedelta(minutes=minutes)).isoformat()
    blocked = _feed_blocked(connection)
    from sportybet_research import _assess_value
    items = connection.execute(
        """SELECT * FROM sports_ai_odds_refresh_queue
           WHERE status IN ('queued','blocked')
             AND (next_attempt_at IS NULL OR next_attempt_at<=?)
           ORDER BY priority DESC LIMIT ?""",
        (current.isoformat(), limit),
    ).fetchall()
    refreshed = 0
    deferred = 0
    refreshed_ids = []
    for item in items:
        quotes = _fresh_quotes(connection, item['fixture_id'], fresh_after)
        if quotes:
            for market, quote in quotes.items():
                prediction = connection.execute(
                    """SELECT p.id FROM sports_ai_engine_predictions p
                       JOIN sports_ai_fixtures f ON f.id=p.fixture_id
                       WHERE p.fixture_id=? AND p.market=? AND p.created_at<f.starts_at
                       ORDER BY p.created_at DESC LIMIT 1""",
                    (item['fixture_id'], market),
                ).fetchone()
                if not prediction:
                    continue
                linked = connection.execute(
                    """SELECT odds_id FROM sports_ai_value_opportunities
                       WHERE prediction_id=? ORDER BY evaluated_at DESC LIMIT 1""",
                    (prediction['id'],),
                ).fetchone()
                if linked and linked['odds_id'] == quote['id']:
                    continue
                _assess_value(
                    connection,
                    {'fixture_id': item['fixture_id'], 'canonical_market': market},
                    quote['id'], float(quote['decimal_odds']), current,
                )
            connection.execute(
                """UPDATE sports_ai_odds_refresh_queue
                   SET status='refreshed', last_error=NULL, next_attempt_at=NULL, reason='verified_price', updated_at=?
                   WHERE fixture_id=?""",
                (current.isoformat(), item['fixture_id']),
            )
            refreshed += 1
            refreshed_ids.append(item['fixture_id'])
            continue
        if not blocked:
            continue
        attempt = int(item['attempt'] or 0) + 1
        if attempt >= MAX_ATTEMPTS:
            status = 'deferred'
            wait = None
            deferred += 1
        else:
            status = 'blocked'
            wait = (current + timedelta(minutes=min(60, 2 ** attempt))).isoformat()
        connection.execute(
            """UPDATE sports_ai_odds_refresh_queue
               SET status=?, attempt=?, next_attempt_at=?, last_error=?, updated_at=?
               WHERE fixture_id=?""",
            (
                status, attempt, wait,
                'SportyBet returned a challenge or access block. No price was stored.',
                current.isoformat(), item['fixture_id'],
            ),
        )
    after = connection.execute('SELECT COUNT(*) FROM sports_ai_odds_snapshots').fetchone()[0]
    return {
        'checked': len(items), 'refreshed': refreshed, 'deferred': deferred, 'fixture_ids': refreshed_ids,
        'feed_blocked': blocked, 'odds_invented': after - before, 'places_wager': False,
    }


def build_autonomous_tickets(connection, actor, audit, now=None):
    """Build tickets only from current QUALIFIED selections. An empty set stores nothing."""
    current = _now(now) or datetime.now(timezone.utc)
    if not autonomous_tickets_enabled(connection):
        return {'status': 'manual', 'tickets': 0, 'places_wager': False, 'reason': 'Autonomous ticket building is switched off.'}
    from accumulator_engine import generate_accumulators, load_opportunity_rows
    rows = load_opportunity_rows(connection)
    if not rows:
        return {
            'status': 'insufficient', 'tickets': 0, 'qualified_inputs': 0, 'places_wager': False,
            'reason': 'No current QUALIFIED selection has a linked qualified price.',
        }
    identity = '|'.join(sorted(f"{row['opportunity_id']}:{row.get('decimal_odds')}" for row in rows))
    key = 'autonomous-tickets:v2:' + hashlib.sha256(identity.encode()).hexdigest()
    result = generate_accumulators(
        connection, actor, audit, idempotency_key=key, now=current, trigger='autonomous',
    )
    from accumulator_engine import TARGETS
    products = generate_accumulators(
        connection, actor, audit,
        overrides={'targets': list(TARGETS), 'max_legs': 25, 'search_limit': 20000, 'timeout_seconds': 20},
        idempotency_key=key + ':products', now=current, trigger='autonomous',
    )
    result['product_targets'] = {
        'status': products.get('status'),
        'ticket_count': 0 if products.get('idempotent') else products.get('ticket_count', 0),
        'tiers': [
            {'target': item.get('target'), 'status': item.get('status'), 'reason': item.get('reason')}
            for item in products.get('tiers') or []
        ],
        'places_wager': False,
    }
    live = generate_accumulators(
        connection, actor, audit,
        overrides={'live_opportunities': True, 'max_legs': 8},
        idempotency_key=key + ':live', now=current, trigger='autonomous',
    )
    result['live_opportunities'] = {
        'status': live.get('status'),
        'ticket_count': 0 if live.get('idempotent') else live.get('ticket_count', 0),
        'places_wager': False,
    }
    result['places_wager'] = False
    range_created = 0 if result.get('idempotent') else result.get('ticket_count', 0)
    result['tickets_created'] = range_created + result['product_targets']['ticket_count'] + result['live_opportunities']['ticket_count']
    return result


def sync_ticket_lifecycle(connection, now=None):
    """Drop qualification that has lapsed, and record the publication state separately."""
    current = _now(now) or datetime.now(timezone.utc)
    from qualification_engine import ticket_is_bookable
    phase = phase_at(current)
    window = publication_open(phase)
    changed = 0
    states = {name: 0 for name in ('READY_FOR_WINDOW', 'BOOKING_PENDING', 'CODE_VERIFIED', 'FAILED', 'EXPIRED')}
    tickets = connection.execute('SELECT id, status, publication_state FROM sports_ai_accumulator_tickets').fetchall()
    for ticket in tickets:
        status = ticket['status']
        if status == 'qualified' and not ticket_is_bookable(connection, ticket['id']):
            connection.execute(
                "UPDATE sports_ai_accumulator_tickets SET status='review_required', publication_state=NULL WHERE id=?",
                (ticket['id'],),
            )
            connection.execute(
                """INSERT INTO sports_ai_ticket_monitoring_events
                   (id,ticket_id,event_type,details_json,created_at) VALUES(?,?,?,?,?)""",
                (
                    str(uuid.uuid4()), ticket['id'], 'qualification_lapsed',
                    json.dumps({'detail': 'A required selection is no longer QUALIFIED.', 'reason_code': 'TICKET_NOT_QUALIFIED'}),
                    current.isoformat(),
                ),
            )
            from sportybet_booking import _record_diagnostic
            _record_diagnostic(connection, ticket['id'], 'TICKET_NOT_QUALIFIED', 'A required selection is no longer QUALIFIED.', current)
            changed += 1
            continue
        publication = _publication_state(connection, ticket['id'], status)
        states[publication] = states.get(publication, 0) + (1 if publication else 0)
        if publication != ticket['publication_state']:
            connection.execute(
                'UPDATE sports_ai_accumulator_tickets SET publication_state=? WHERE id=?',
                (publication, ticket['id']),
            )
    return {'demoted': changed, 'states': states, 'phase': phase, 'publication_open': window, 'places_wager': False}


def _publication_state(connection, ticket_id, status):
    if status in ('expired', 'void', 'settled'):
        return 'EXPIRED'
    if status != 'qualified':
        return None
    verified = connection.execute(
        """SELECT 1 FROM sports_ai_booking_codes
           WHERE accumulator_ticket_id=? AND status='verified' AND verification_method='displayed'
             AND TRIM(COALESCE(code,''))<>'' LIMIT 1""",
        (ticket_id,),
    ).fetchone()
    if verified:
        return 'CODE_VERIFIED'
    job = connection.execute(
        """SELECT status FROM sports_ai_booking_jobs
           WHERE signature LIKE ? || '|%'
           ORDER BY updated_at DESC LIMIT 1""",
        (ticket_id,),
    ).fetchone()
    if job and job['status'] in ('QUEUED', 'PREPARING'):
        return 'BOOKING_PENDING'
    if job and job['status'] in ('FAILED', 'ODDS_CHANGED'):
        return 'FAILED'
    return 'READY_FOR_WINDOW' if status == 'qualified' else None


def measure_pipeline(connection, now=None):
    """Counts from stored rows. A missing price stays missing."""
    current = _now(now) or datetime.now(timezone.utc)
    minutes = freshness_minutes(connection)
    fresh_after = (current - timedelta(minutes=minutes)).isoformat()
    from prediction_engine import upcoming_fixture_sql
    upcoming = upcoming_fixture_sql('f')
    discovered = _count(connection, f"""
        SELECT COUNT(*) FROM sports_ai_fixtures f
        JOIN sports_ai_sports s ON s.code=f.sport AND s.prediction_status='modelled'
        WHERE {upcoming}""")
    data_ready = _count(connection, f"""
        SELECT COUNT(*) FROM sports_ai_fixtures f
        JOIN sports_ai_sports s ON s.code=f.sport AND s.prediction_status='modelled'
        WHERE {upcoming} AND TRIM(COALESCE(f.canonical_key,''))<>''
          AND TRIM(COALESCE(f.home,''))<>'' AND TRIM(COALESCE(f.away,''))<>''
          AND NOT EXISTS (
            SELECT 1 FROM sports_ai_data_quality_issues i
            WHERE i.fixture_id=f.id AND i.resolved_at IS NULL AND i.severity IN ('high','critical')
          )""")
    predicted = _count(connection, f"""
        SELECT COUNT(DISTINCT f.id) FROM sports_ai_fixtures f
        JOIN sports_ai_sports s ON s.code=f.sport AND s.prediction_status='modelled'
        JOIN sports_ai_engine_predictions p ON p.fixture_id=f.id AND p.created_at<f.starts_at
        JOIN sports_ai_model_registry m ON m.id=p.model_id AND m.active=1
        WHERE {upcoming} AND p.market IN ('HOME','DRAW','AWAY','MONEYLINE_HOME','MONEYLINE_AWAY')""")
    odds_available = _count(connection, f"""
        SELECT COUNT(DISTINCT f.id) FROM sports_ai_fixtures f
        JOIN sports_ai_engine_predictions p ON p.fixture_id=f.id AND p.created_at<f.starts_at
        JOIN sports_ai_model_registry m ON m.id=p.model_id AND m.active=1
        JOIN sports_ai_odds_snapshots q ON q.fixture_id=f.id AND q.bookmaker='SportyBet'
        WHERE {upcoming}""")
    odds_fresh = _count(connection, f"""
        SELECT COUNT(DISTINCT f.id) FROM sports_ai_fixtures f
        JOIN sports_ai_engine_predictions p ON p.fixture_id=f.id AND p.created_at<f.starts_at
        JOIN sports_ai_model_registry m ON m.id=p.model_id AND m.active=1
        JOIN sports_ai_odds_snapshots q ON q.fixture_id=f.id AND q.bookmaker='SportyBet' AND q.observed_at>=?
        WHERE {upcoming}""", (fresh_after,))
    qualified = _count(connection, "SELECT COUNT(*) FROM sports_ai_qualification_decisions WHERE state='QUALIFIED'")
    ticket_ready = _count(connection, "SELECT COUNT(*) FROM sports_ai_accumulator_tickets WHERE status='qualified'")
    pending = _count(connection, "SELECT COUNT(*) FROM sports_ai_booking_jobs WHERE status IN ('QUEUED','PREPARING')")
    verified = _count(connection, "SELECT COUNT(*) FROM sports_ai_booking_codes WHERE status='verified' AND verification_method='displayed'")
    displayed = _count(connection, """
        SELECT COUNT(*) FROM sports_ai_booking_codes b
        JOIN sports_ai_accumulator_tickets t ON t.id=b.accumulator_ticket_id
        WHERE b.status='verified' AND b.verification_method='displayed' AND t.status='qualified'""")
    counts = {
        'DISCOVERED': discovered, 'DATA_READY': data_ready, 'PREDICTED': predicted,
        'ODDS_AVAILABLE': odds_available, 'ODDS_FRESH': odds_fresh, 'QUALIFIED': qualified,
        'TICKET_READY': ticket_ready, 'BOOKING_PENDING': pending,
        'CODE_VERIFIED': verified, 'DISPLAYED': displayed,
    }
    reasons = [
        {'reason_code': row['reason_code'], 'count': row['total']}
        for row in connection.execute(
            """SELECT reason_code, COUNT(*) AS total FROM sports_ai_qualification_decisions
               WHERE state!='QUALIFIED' GROUP BY reason_code ORDER BY total DESC LIMIT 12""",
        )
    ]
    queue = connection.execute(
        """SELECT
             SUM(CASE WHEN status='queued' THEN 1 ELSE 0 END) AS queued,
             SUM(CASE WHEN status='blocked' THEN 1 ELSE 0 END) AS blocked,
             SUM(CASE WHEN status='refreshed' THEN 1 ELSE 0 END) AS refreshed,
             SUM(CASE WHEN status='deferred' THEN 1 ELSE 0 END) AS deferred
           FROM sports_ai_odds_refresh_queue""",
    ).fetchone()
    observations = connection.execute(
        """SELECT
             SUM(CASE WHEN observation_class='live_observed' THEN 1 ELSE 0 END) AS live_observed,
             SUM(CASE WHEN observation_class='timestamp_eligible' THEN 1 ELSE 0 END) AS timestamp_eligible,
             SUM(CASE WHEN observation_class='historical' THEN 1 ELSE 0 END) AS historical
           FROM sports_ai_engine_predictions""",
    ).fetchone()
    weekly = connection.execute(
        """SELECT status, COUNT(*) AS total FROM sports_ai_accumulator_tickets
           WHERE json_extract(parameters_json,'$.product')='weekly_draw' GROUP BY status""",
    ).fetchall()
    rollover = [
        dict(row) for row in connection.execute(
            """SELECT name, mode, status, opening_balance, bankroll, wins, losses, skipped, places_wager
               FROM sports_ai_rollover_campaigns""",
        )
    ]
    heartbeat = connection.execute(
        "SELECT heartbeat_at FROM sports_ai_worker_leases WHERE id='scheduler-leader'",
    ).fetchone()
    blockers = []
    if odds_fresh == 0 and predicted:
        blockers.append({'stage': 'ODDS_FRESH', 'reason': f'No SportyBet price for a predicted fixture is inside {minutes} minutes.'})
    if qualified == 0:
        blockers.append({'stage': 'QUALIFIED', 'reason': 'No selection currently clears the stored qualification rule.'})
    if ticket_ready == 0:
        blockers.append({'stage': 'TICKET_READY', 'reason': 'No ticket has every leg still QUALIFIED with a current price.'})
    if not publication_open(phase_at(current)):
        blockers.append({'stage': 'BOOKING_PENDING', 'reason': 'Outside an Africa/Lagos publication window. Analysis can continue. No code is requested.'})
    return {
        'stages': [{'stage': name, 'count': counts[name]} for name in STAGES],
        'counts': counts,
        'blockers': blockers,
        'rejection_reasons': reasons,
        'refresh': {
            'queued': int(queue['queued'] or 0), 'blocked': int(queue['blocked'] or 0),
            'refreshed': int(queue['refreshed'] or 0), 'deferred': int(queue['deferred'] or 0),
            'freshness_minutes': minutes,
        },
        'observations': {
            'live_observed': int(observations['live_observed'] or 0),
            'timestamp_eligible': int(observations['timestamp_eligible'] or 0),
            'historical': int(observations['historical'] or 0),
        },
        'weekly': [{'status': row['status'], 'count': row['total']} for row in weekly],
        'rollover': rollover,
        'worker': {'heartbeat_at': heartbeat['heartbeat_at'] if heartbeat else None},
        'phase': phase_at(current),
        'next_publication_at': next_publication(current),
        'booking_diagnostics': [
            dict(row) for row in connection.execute(
                """SELECT reason_code, COUNT(*) AS total, MAX(created_at) AS created_at,
                          (SELECT detail FROM sports_ai_booking_diagnostics latest
                           WHERE latest.reason_code=sports_ai_booking_diagnostics.reason_code
                           ORDER BY created_at DESC LIMIT 1) AS detail
                   FROM sports_ai_booking_diagnostics
                   GROUP BY reason_code
                   ORDER BY created_at DESC""",
            )
        ],
        'prediction_reviews': [
            dict(row) for row in connection.execute(
                """SELECT reason_code, COUNT(*) AS total FROM sports_ai_prediction_reviews
                   GROUP BY reason_code ORDER BY total DESC""",
            )
        ],
        'reconciliation': [
            dict(row) for row in connection.execute(
                """SELECT status, COUNT(*) AS total FROM sports_ai_fixture_identity_links
                   GROUP BY status ORDER BY total DESC""",
            )
        ],
        'feature_audit': [
            dict(row) for row in connection.execute(
                """SELECT finding, COUNT(*) AS total FROM sports_ai_feature_audits
                   GROUP BY finding ORDER BY total DESC""",
            )
        ],
        'places_wager': False,
        'evaluated_at': current.isoformat(),
    }


def capture_snapshot(connection, now=None):
    current = _now(now) or datetime.now(timezone.utc)
    measured = measure_pipeline(connection, current)
    connection.execute(
        'INSERT INTO sports_ai_pipeline_snapshots(id,created_at,stages_json,blockers_json) VALUES(?,?,?,?)',
        (
            str(uuid.uuid4()), current.isoformat(),
            json.dumps(measured['stages']), json.dumps(measured['blockers']),
        ),
    )
    cutoff = (current - timedelta(days=2)).isoformat()
    connection.execute('DELETE FROM sports_ai_pipeline_snapshots WHERE created_at<?', (cutoff,))
    return measured


def review_qualified_predictions(connection, now=None):
    """Flag implausible qualified prices. Qualification thresholds stay unchanged."""
    current = _now(now) or datetime.now(timezone.utc)
    rows = connection.execute(
        """SELECT d.prediction_id, d.fixture_id, d.market, p.probability, p.observation_class,
                  o.expected_edge, q.decimal_odds
           FROM sports_ai_qualification_decisions d
           JOIN sports_ai_engine_predictions p ON p.id=d.prediction_id
           LEFT JOIN sports_ai_value_opportunities o ON o.id=d.opportunity_id
           LEFT JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
           WHERE d.state='QUALIFIED'""",
    ).fetchall()
    inserted = 0
    for row in rows:
        flags = []
        if row['expected_edge'] is not None and float(row['expected_edge']) >= 0.50:
            flags.append((
                'EXTREME_EDGE',
                f"Estimated edge is {float(row['expected_edge']):.2f}. The qualification threshold was not changed.",
            ))
        if row['decimal_odds'] and row['probability'] is not None and str(row['market']).upper() == 'DRAW':
            implied = 1 / float(row['decimal_odds'])
            gap = float(row['probability']) - implied
            if gap >= 0.10:
                flags.append((
                    'DRAW_MARKET_GAP',
                    f"Draw probability {float(row['probability']):.3f} is {gap:.3f} above the implied price probability {implied:.3f}.",
                ))
        if row['observation_class'] not in (None, '', 'live_observed'):
            flags.append(('NOT_LIVE_OBSERVED', f"The forecast class is {row['observation_class']}."))
        for code, detail in flags:
            cursor = connection.execute(
                """INSERT INTO sports_ai_prediction_reviews
                   (id,prediction_id,fixture_id,market,reason_code,detail,created_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(prediction_id, reason_code) DO NOTHING""",
                (str(uuid.uuid4()), row['prediction_id'], row['fixture_id'], row['market'], code, detail, current.isoformat()),
            )
            inserted += cursor.rowcount
    return {'inserted': inserted, 'thresholds_changed': False, 'places_wager': False}


def latest_snapshot(connection):
    row = connection.execute(
        'SELECT created_at, stages_json, blockers_json FROM sports_ai_pipeline_snapshots ORDER BY created_at DESC LIMIT 1',
    ).fetchone()
    if not row:
        return None
    return {
        'created_at': row['created_at'],
        'stages': json.loads(row['stages_json']),
        'blockers': json.loads(row['blockers_json']),
        'places_wager': False,
    }


def _count(connection, sql, args=()):
    return int(connection.execute(sql, args).fetchone()[0] or 0)

