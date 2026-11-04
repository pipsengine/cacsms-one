"""Weekly and Saturday full-time draw tickets.

Candidates must be qualified by the central qualification service and ranked by
the draw intelligence engine. A category with too few selections is reported as
INSUFFICIENT_QUALIFIED_DRAWS. Eight- and ten-pick tickets stay in simulation
until stored settled results and an admin save activate them.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

from accumulator_engine import (
    CONFIG_ID,
    _insert_ticket,
    _notify,
    _settlement_kind,
    combine_ticket,
    ticket_outcome,
)
from booking_schedule import WAT
from draw_engine import list_fixtures

SIZES = (2, 5, 8, 10)
SIMULATION_SIZES = {8, 10}
MIN_SETTLED_BEFORE_ACTIVATION = 20
POLICY_KEY = 'weekly_draw_policy'
STATUS_KEY = 'weekly_draw_status'
INSUFFICIENT = 'INSUFFICIENT_QUALIFIED_DRAWS'
TICKET_CONFIG = {'allow_same_fixture': 0, 'max_per_team': 1, 'max_per_competition': 12}
WINDOWS = (
    {'id': 'weekly_discovery', 'label': 'Weekly discovery', 'weekday': 0, 'start': '06:00', 'end': '09:00', 'mode': 'WEEKLY'},
    {'id': 'saturday_watchlist', 'label': 'Friday Saturday watchlist', 'weekday': 4, 'start': '16:00', 'end': '20:00', 'mode': 'SATURDAY_ONLY'},
    {'id': 'saturday_revalidation', 'label': 'Saturday revalidation', 'weekday': 5, 'start': '08:00', 'end': '12:00', 'mode': 'SATURDAY_ONLY'},
)


def _now(now=None):
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _minute(value):
    hour, minute = value.split(':')
    return int(hour) * 60 + int(minute)


def week_id(now=None):
    monday, _end = week_bounds(_now(now))
    return monday.date().isoformat()


def week_bounds(now):
    local = _now(now).astimezone(WAT)
    monday = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    sunday_end = monday + timedelta(days=7) - timedelta(microseconds=1)
    return monday, sunday_end


def saturday_bounds(now):
    monday, _end = week_bounds(now)
    saturday = monday + timedelta(days=5)
    return saturday, saturday + timedelta(days=1) - timedelta(microseconds=1)


def mode_bounds(mode, now):
    if mode == 'SATURDAY_ONLY':
        return saturday_bounds(now)
    if mode == 'WEEKLY':
        return week_bounds(now)
    raise ValueError('Mode must be WEEKLY or SATURDAY_ONLY')


def phase_at(now=None):
    local = _now(now).astimezone(WAT)
    minute = local.hour * 60 + local.minute
    for window in WINDOWS:
        if window['weekday'] == local.weekday() and _minute(window['start']) <= minute <= _minute(window['end']):
            return window['id']
    return 'monitoring'


def describe_schedule(now=None):
    current = _now(now)
    return {
        'timezone': 'Africa/Lagos',
        'offset': '+01:00',
        'local_time': current.astimezone(WAT).isoformat(),
        'phase': phase_at(current),
        'week_id': week_id(current),
        'places_wager': False,
        'windows': [
            {'id': item['id'], 'label': item['label'], 'day': item['weekday'], 'start': item['start'], 'end': item['end'], 'mode': item['mode']}
            for item in WINDOWS
        ],
        'note': 'Tickets are built in the weekly discovery and Saturday revalidation windows. Booking codes are requested only by the existing publication windows, and only for a standard 2-pick or 5-pick ticket.',
    }


def default_policy():
    return {
        'sizes': {
            '2': {'mode': 'standard', 'min_settled_before_activation': 0},
            '5': {'mode': 'standard', 'min_settled_before_activation': 0},
            '8': {'mode': 'simulation', 'min_settled_before_activation': MIN_SETTLED_BEFORE_ACTIVATION},
            '10': {'mode': 'simulation', 'min_settled_before_activation': MIN_SETTLED_BEFORE_ACTIVATION},
        },
        'note': 'Eight- and ten-pick tickets stay in simulation until that many settled tickets are stored and an admin saves the change.',
    }


def load_policy(connection):
    row = connection.execute('SELECT value_json FROM sports_ai_settings WHERE key=?', (POLICY_KEY,)).fetchone()
    if not row:
        return default_policy()
    stored = json.loads(row['value_json'] or '{}')
    policy = default_policy()
    for size, item in (stored.get('sizes') or {}).items():
        if size in policy['sizes'] and item.get('mode') in ('standard', 'simulation'):
            policy['sizes'][size]['mode'] = item['mode']
    return policy


def save_policy(connection, updates):
    policy = load_policy(connection)
    for size in ('8', '10'):
        requested = (updates.get('sizes') or {}).get(size, {}).get('mode')
        if requested not in ('standard', 'simulation'):
            continue
        if requested == 'standard':
            settled = settled_count(connection, int(size))
            required = MIN_SETTLED_BEFORE_ACTIVATION
            if settled < required:
                raise ValueError(f'{size}-pick tickets stay in simulation until {required} settled tickets are stored. {settled} are stored.')
        policy['sizes'][size]['mode'] = requested
    connection.execute(
        """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES(?,?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
        (POLICY_KEY, json.dumps(policy), _now().isoformat()),
    )
    return policy


def settled_count(connection, size):
    row = connection.execute(
        """SELECT COUNT(*) AS n FROM sports_ai_accumulator_tickets
           WHERE status='settled'
             AND json_extract(parameters_json,'$.product')='weekly_draw'
             AND json_extract(parameters_json,'$.pick_count')=?""",
        (int(size),),
    ).fetchone()
    return int(row['n'] or 0)


def size_mode(policy, size):
    stored = (policy.get('sizes') or {}).get(str(size), {})
    if size in SIMULATION_SIZES and stored.get('mode') != 'standard':
        return 'simulation'
    if size in SIMULATION_SIZES:
        return 'standard'
    return 'standard'


def load_candidates(connection, mode, now=None):
    """Qualified full-time draws that the draw engine currently ranks."""
    current = _now(now)
    start, end = mode_bounds(mode, current)
    ranked = {
        item['fixture_id']: item
        for item in list_fixtures(connection, now=current)
        if item.get('quality') == 'ranked' and item.get('calibrated')
    }
    policy_row = connection.execute(
        "SELECT value_json FROM sports_ai_settings WHERE key='qualification_policy'",
    ).fetchone()
    max_age = int((json.loads(policy_row['value_json']) if policy_row else {}).get('odds_max_age_minutes') or 15)
    rows = connection.execute(
        """SELECT d.fixture_id, d.evidence_json, d.products_json,
                  f.home, f.away, f.competition, f.starts_at, f.sport
           FROM sports_ai_qualification_decisions d
           JOIN sports_ai_fixtures f ON f.id=d.fixture_id
           WHERE d.state='QUALIFIED' AND upper(d.market)='DRAW' AND f.sport='football'
             AND f.status IN ('scheduled','not_started')""",
    ).fetchall()
    eligible = []
    excluded = []
    seen = set()
    for row in rows:
        products = json.loads(row['products_json'] or '[]')
        kickoff = _parse(row['starts_at'])
        identity = f"{row['home']} vs {row['away']}"
        if 'weekly_draw' not in products:
            excluded.append({'fixture_id': row['fixture_id'], 'fixture': identity, 'reason': 'The qualification decision does not include the weekly draw product.'})
            continue
        if kickoff is None or kickoff <= current or kickoff < start or kickoff > end:
            excluded.append({'fixture_id': row['fixture_id'], 'fixture': identity, 'reason': 'Kickoff is outside this mode window.'})
            continue
        intel = ranked.get(row['fixture_id'])
        if not intel:
            excluded.append({'fixture_id': row['fixture_id'], 'fixture': identity, 'reason': 'Draw intelligence has no calibrated probability and fresh price for this fixture.'})
            continue
        quote = _latest_draw_quote(connection, row['fixture_id'], current, max_age)
        if row['fixture_id'] in seen or quote is None:
            excluded.append({'fixture_id': row['fixture_id'], 'fixture': identity, 'reason': 'No draw price inside the qualification window is stored.'})
            continue
        seen.add(row['fixture_id'])
        evidence = json.loads(row['evidence_json'] or '{}')
        eligible.append({
            'opportunity_id': quote['opportunity_id'],
            'fixture_id': row['fixture_id'],
            'sport': 'football',
            'competition': row['competition'] or '',
            'home': row['home'],
            'away': row['away'],
            'starts_at': row['starts_at'],
            'market': 'DRAW',
            'selection': 'Draw',
            'decimal_odds': float(quote['decimal_odds']),
            'probability': float(quote['probability']),
            'edge': float(quote['expected_edge']) if quote['expected_edge'] is not None else intel.get('edge'),
            'bookmaker': quote['bookmaker'] or '',
            'odds_observed_at': quote['observed_at'],
            'quote_market': quote['quote_market'] or 'DRAW',
            'quote_outcome': quote['quote_outcome'] or 'draw',
            'quote_line': quote['quote_line'],
            'model_id': quote['model_id'] or '',
            'model_version': quote['model_version'] or '',
            'completeness': quote['data_completeness'],
            'reliability': None,
            'settlement_kind': _settlement_kind(quote['settlement_json']),
            'rank_score': intel.get('rank_score'),
            'uncertainty': evidence.get('uncertainty'),
        })
    eligible.sort(key=lambda item: (-(item.get('rank_score') or 0), -(item.get('edge') or 0), item['starts_at'], item['fixture_id']))
    return {'eligible': eligible, 'excluded': excluded[:50], 'window': {'start': start.isoformat(), 'end': end.isoformat()}}


def _latest_draw_quote(connection, fixture_id, now, max_age):
    row = connection.execute(
        """SELECT o.id AS opportunity_id, o.expected_edge, o.data_completeness,
                  p.probability, p.model_id, p.settlement_json,
                  q.decimal_odds, q.bookmaker, q.observed_at, q.market AS quote_market,
                  q.outcome AS quote_outcome, q.line AS quote_line, m.version AS model_version
           FROM sports_ai_value_opportunities o
           JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id
           JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
           JOIN sports_ai_model_registry m ON m.id=p.model_id
           WHERE p.fixture_id=? AND p.market='DRAW' AND lower(q.outcome)='draw'
           ORDER BY q.observed_at DESC LIMIT 1""",
        (fixture_id,),
    ).fetchone()
    if not row or row['decimal_odds'] is None or row['probability'] is None:
        return None
    observed = _parse(row['observed_at'])
    if observed is None or now - observed > timedelta(minutes=max_age):
        return None
    return row


def best_combination(candidates, size):
    """One combination of exactly `size` independent draws, or none."""
    unique = []
    seen = set()
    for item in sorted(candidates, key=lambda row: (-(row.get('rank_score') or 0), -(row.get('edge') or 0), row['starts_at'], row['fixture_id'])):
        if item['fixture_id'] in seen:
            continue
        seen.add(item['fixture_id'])
        unique.append(item)
    if len(unique) < size:
        return None
    pool = unique[:{2: 20, 5: 12, 8: 12, 10: 12}[size]]
    best = None
    evaluated = 0

    def visit(start, chosen):
        nonlocal best, evaluated
        if evaluated > 4000:
            return
        if len(chosen) == size:
            evaluated += 1
            combined = combine_ticket(chosen, TICKET_CONFIG)
            if combined['probability_basis'] != 'independent' or combined.get('joint_probability') is None:
                return
            components = _components(combined, chosen)
            if best is None or components['score'] > best['components']['score']:
                best = {'combined': combined, 'legs': list(chosen), 'components': components}
            return
        for index in range(start, len(pool)):
            visit(index + 1, chosen + [pool[index]])

    visit(0, [])
    return best


def _components(combined, legs):
    counts = {}
    for leg in legs:
        counts[leg['competition']] = counts.get(leg['competition'], 0) + 1
    correlation = sum(count - 1 for count in counts.values() if count > 1) / len(legs)
    probability = combined.get('joint_probability')
    value = combined.get('estimated_edge')
    uncertainty = combined.get('uncertainty')
    score = (value or 0) + (probability or 0) - (uncertainty or 0) - correlation
    return {
        'score': score,
        'joint_probability': probability,
        'expected_value': value,
        'uncertainty': uncertainty,
        'combined_odds': combined.get('combined_odds'),
        'correlation_risk': correlation,
    }


def build_tickets(connection, mode, now=None, actor='sports-ai-worker', candidates=None, revalidate_first=True):
    current = _now(now)
    if mode not in ('WEEKLY', 'SATURDAY_ONLY'):
        raise ValueError('Mode must be WEEKLY or SATURDAY_ONLY')
    if revalidate_first and candidates is None:
        from qualification_engine import revalidate
        revalidate(connection, actor, current)
    loaded = candidates if isinstance(candidates, dict) else None
    if loaded is None:
        loaded = {'eligible': candidates} if candidates is not None else load_candidates(connection, mode, current)
    eligible = list(loaded.get('eligible') or [])
    policy = load_policy(connection)
    current_week = week_id(current)
    categories = []
    stored = []
    for size in SIZES:
        open_ticket = _open_ticket(connection, mode, size, current_week)
        if open_ticket:
            categories.append({'pick_count': size, 'status': 'open', 'ticket_id': open_ticket, 'reason': 'An open ticket already exists for this category and week.'})
            continue
        if len(eligible) < size:
            categories.append(_insufficient(connection, mode, size, current_week, len(eligible)))
            continue
        chosen = best_combination(eligible, size)
        if not chosen:
            categories.append(_insufficient(connection, mode, size, current_week, len(eligible), 'No independent combination passed the duplicate and correlation checks.'))
            continue
        ticket_id = _store_ticket(connection, mode, size, chosen, policy, current_week, current, actor)
        stored.append(ticket_id)
        categories.append({'pick_count': size, 'status': 'stored', 'ticket_id': ticket_id, 'mode': size_mode(policy, size)})
    summary = {
        'mode': mode,
        'week_id': current_week,
        'eligible': len(eligible),
        'categories': categories,
        'ticket_ids': stored,
        'places_wager': False,
    }
    _remember(connection, summary, current)
    return summary


def _insufficient(connection, mode, size, current_week, available, detail=None):
    notice = f'{mode}:{size}:{current_week}'
    if not connection.execute(
        """SELECT 1 FROM sports_ai_notifications
           WHERE event_type='weekly_draw.insufficient' AND payload_json LIKE ? LIMIT 1""",
        (f'%{notice}%',),
    ).fetchone():
        _notify(connection, 'weekly_draw.insufficient', {
            'notice_key': notice, 'mode': mode, 'pick_count': size, 'week_id': current_week,
            'available': available, 'status': INSUFFICIENT,
        })
    return {
        'pick_count': size,
        'status': INSUFFICIENT,
        'available': available,
        'reason': detail or f'{available} qualified ranked draws are stored. This category needs {size}.',
    }


def _store_ticket(connection, mode, size, chosen, policy, current_week, current, actor):
    ticket_mode = size_mode(policy, size)
    sportybet = all(leg.get('bookmaker') == 'SportyBet' for leg in chosen['legs'])
    status = 'review_required' if ticket_mode == 'simulation' or not sportybet else 'qualified'
    explanation = chosen['combined']['explanation']
    if ticket_mode == 'simulation':
        explanation += f' This {size}-pick ticket stays in simulation until stored settled tickets support activation. No booking code is requested.'
    elif not sportybet:
        explanation += ' A stored SportyBet price is not present on every leg, so no booking code is requested.'
    run_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO sports_ai_accumulator_runs
           (id,config_id,sport_mode,status,trigger,idempotency_key,parameters_json,actor,started_at,finished_at,summary_json)
           VALUES(?,?,?,'complete','weekly_draw',?,?,?,?,?,?)""",
        (
            run_id, CONFIG_ID, 'football', f"weekly-draw:{run_id}",
            json.dumps({'product': 'weekly_draw', 'mode': mode, 'pick_count': size, 'week_id': current_week}),
            actor, current.isoformat(), current.isoformat(), '{}',
        ),
    )
    signature = 'weekly-draw:' + mode + ':' + str(size) + ':' + ','.join(sorted(leg['fixture_id'] for leg in chosen['legs']))
    ticket = {
        'signature': signature,
        'tier': 20000 + size,
        'sport_mode': 'football',
        'status': status,
        'legs': chosen['legs'],
        'tier_id': f'draw-{size}',
        'tier_label': f'{size}-pick draw',
        'tier_mode': ticket_mode,
        'correlation_exposure': chosen['components']['correlation_risk'],
        'components': chosen['components'],
        'score': chosen['components']['score'],
        **chosen['combined'],
        'explanation': explanation,
    }
    ticket_id = _insert_ticket(connection, run_id, ticket, {'targets': [20000 + size], 'min_legs': size, 'max_legs': size}, current, 'created', 'Weekly draw category')
    connection.execute(
        """UPDATE sports_ai_accumulator_tickets
           SET parameters_json=json_set(parameters_json,'$.product','weekly_draw','$.mode',?,'$.pick_count',?,'$.week_id',?)
           WHERE id=?""",
        (mode, size, current_week, ticket_id),
    )
    connection.execute(
        """INSERT INTO sports_ai_ticket_monitoring_events(id,ticket_id,run_id,event_type,details_json,created_at)
           VALUES(?,?,?,?,?,?)""",
        (str(uuid.uuid4()), ticket_id, run_id, 'weekly_draw.created', json.dumps({'mode': mode, 'pick_count': size, 'status': status}), current.isoformat()),
    )
    _notify(connection, 'weekly_draw.ticket', {'ticket_id': ticket_id, 'mode': mode, 'pick_count': size, 'status': status, 'week_id': current_week})
    return ticket_id


def _open_ticket(connection, mode, size, current_week):
    row = connection.execute(
        """SELECT id FROM sports_ai_accumulator_tickets
           WHERE status IN ('qualified','review_required','awaiting_odds')
             AND json_extract(parameters_json,'$.product')='weekly_draw'
             AND json_extract(parameters_json,'$.mode')=?
             AND json_extract(parameters_json,'$.pick_count')=?
             AND json_extract(parameters_json,'$.week_id')=?
           ORDER BY created_at DESC LIMIT 1""",
        (mode, int(size), current_week),
    ).fetchone()
    return row['id'] if row else None


def prepare_watchlist(connection, now=None, actor='sports-ai-worker', candidates=None):
    current = _now(now)
    loaded = candidates if candidates is not None else load_candidates(connection, 'SATURDAY_ONLY', current)
    current_week = week_id(current)
    connection.execute('DELETE FROM sports_ai_weekly_draw_watch WHERE week_id=? AND mode=?', (current_week, 'SATURDAY_ONLY'))
    for item in loaded['eligible']:
        connection.execute(
            """INSERT INTO sports_ai_weekly_draw_watch
               (fixture_id,week_id,mode,home,away,competition,starts_at,state,detail_json,prepared_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                item['fixture_id'], current_week, 'SATURDAY_ONLY', item['home'], item['away'], item['competition'],
                item['starts_at'], 'QUALIFIED', json.dumps({'rank_score': item.get('rank_score'), 'edge': item.get('edge')}),
                current.isoformat(),
            ),
        )
    _notify(connection, 'weekly_draw.watchlist', {'week_id': current_week, 'count': len(loaded['eligible']), 'actor': actor})
    return {'week_id': current_week, 'count': len(loaded['eligible']), 'status': 'prepared' if loaded['eligible'] else INSUFFICIENT, 'places_wager': False}


def list_watchlist(connection, now=None):
    current_week = week_id(now)
    return [dict(row) for row in connection.execute(
        """SELECT * FROM sports_ai_weekly_draw_watch WHERE week_id=? AND mode='SATURDAY_ONLY' ORDER BY starts_at""",
        (current_week,),
    )]


def settle_weekly_tickets(connection, now=None):
    current = _now(now)
    changed = 0
    tickets = connection.execute(
        """SELECT id FROM sports_ai_accumulator_tickets
           WHERE json_extract(parameters_json,'$.product')='weekly_draw'
             AND status IN ('qualified','review_required','awaiting_odds')""",
    ).fetchall()
    for ticket in tickets:
        legs = [dict(row) for row in connection.execute('SELECT * FROM sports_ai_accumulator_legs WHERE ticket_id=?', (ticket['id'],))]
        for leg in legs:
            if leg['result'] != 'pending':
                continue
            result = connection.execute(
                "SELECT home_score, away_score, status FROM sports_ai_results WHERE fixture_id=? AND status='final'",
                (leg['fixture_id'],),
            ).fetchone()
            if not result:
                continue
            outcome = 'won' if int(result['home_score']) == int(result['away_score']) else 'lost'
            connection.execute('UPDATE sports_ai_accumulator_legs SET result=? WHERE id=?', (outcome, leg['id']))
            leg['result'] = outcome
        ticket_result, multiple = ticket_outcome(legs)
        if ticket_result == 'pending':
            continue
        connection.execute("UPDATE sports_ai_accumulator_tickets SET status='settled', finalized=1 WHERE id=?", (ticket['id'],))
        connection.execute(
            """INSERT OR IGNORE INTO sports_ai_ticket_settlements
               (id,ticket_id,outcome,payout_multiple,stake_assumption,hypothetical_profit,settled_at,details_json)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()), ticket['id'], ticket_result, multiple, 1.0,
                None if multiple is None else multiple - 1,
                current.isoformat(), json.dumps({'source': 'stored final scores', 'places_wager': False}),
            ),
        )
        _notify(connection, 'weekly_draw.settled', {'ticket_id': ticket['id'], 'outcome': ticket_result})
        changed += 1
    return changed


def monitor_open_tickets(connection, now=None):
    current = _now(now)
    events = 0
    tickets = connection.execute(
        """SELECT id, status FROM sports_ai_accumulator_tickets
           WHERE json_extract(parameters_json,'$.product')='weekly_draw'
             AND status IN ('qualified','review_required','awaiting_odds')""",
    ).fetchall()
    for ticket in tickets:
        expired = False
        for leg in connection.execute('SELECT * FROM sports_ai_accumulator_legs WHERE ticket_id=?', (ticket['id'],)):
            decision = connection.execute(
                """SELECT state FROM sports_ai_qualification_decisions
                   WHERE fixture_id=? AND upper(market)='DRAW' ORDER BY updated_at DESC LIMIT 1""",
                (leg['fixture_id'],),
            ).fetchone()
            state = decision['state'] if decision else 'MISSING'
            stored = json.loads(leg['qualification_json'] or '{}')
            if stored.get('qualification_state') == state:
                continue
            stored['qualification_state'] = state
            connection.execute('UPDATE sports_ai_accumulator_legs SET qualification_json=? WHERE id=?', (json.dumps(stored), leg['id']))
            connection.execute(
                """INSERT INTO sports_ai_ticket_monitoring_events(id,ticket_id,event_type,details_json,created_at)
                   VALUES(?,?,?,?,?)""",
                (str(uuid.uuid4()), ticket['id'], 'weekly_draw.fixture', json.dumps({'fixture_id': leg['fixture_id'], 'state': state}), current.isoformat()),
            )
            events += 1
            if state != 'QUALIFIED':
                expired = True
        if expired and ticket['status'] != 'expired':
            connection.execute("UPDATE sports_ai_accumulator_tickets SET status='expired' WHERE id=?", (ticket['id'],))
            _notify(connection, 'weekly_draw.expired', {'ticket_id': ticket['id'], 'reason': 'A leg is no longer QUALIFIED.'})
    return events


def list_weekly_tickets(connection, mode=''):
    clauses = ["json_extract(parameters_json,'$.product')='weekly_draw'"]
    args = []
    if mode:
        clauses.append("json_extract(parameters_json,'$.mode')=?")
        args.append(mode)
    rows = []
    for ticket in connection.execute(
        f"""SELECT * FROM sports_ai_accumulator_tickets WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC LIMIT 100""",
        args,
    ):
        item = dict(ticket)
        item['parameters'] = json.loads(item.pop('parameters_json') or '{}')
        item['legs'] = [dict(leg) for leg in connection.execute(
            'SELECT fixture_id, home, away, competition, starts_at, market, decimal_odds, probability, result FROM sports_ai_accumulator_legs WHERE ticket_id=? ORDER BY starts_at',
            (ticket['id'],),
        )]
        rows.append(item)
    return rows


def list_codes(connection):
    return [dict(row) for row in connection.execute(
        """SELECT b.code, b.status, b.verification_method, b.evidence, b.created_at, b.verified_at, b.expires_at,
                  t.id AS ticket_id, t.combined_odds, t.parameters_json
           FROM sports_ai_booking_codes b
           JOIN sports_ai_accumulator_tickets t ON t.id=b.accumulator_ticket_id
           WHERE json_extract(t.parameters_json,'$.product')='weekly_draw'
             AND b.verification_method='displayed' AND b.status='verified' AND TRIM(COALESCE(b.code,''))<>''
           ORDER BY b.created_at DESC LIMIT 100""",
    )]


def performance(connection):
    report = []
    for size in SIZES:
        for mode in ('WEEKLY', 'SATURDAY_ONLY'):
            legs = connection.execute(
                """SELECT l.result FROM sports_ai_accumulator_legs l
                   JOIN sports_ai_accumulator_tickets t ON t.id=l.ticket_id
                   WHERE json_extract(t.parameters_json,'$.product')='weekly_draw'
                     AND json_extract(t.parameters_json,'$.pick_count')=?
                     AND json_extract(t.parameters_json,'$.mode')=?
                     AND l.result IN ('won','lost')""",
                (size, mode),
            ).fetchall()
            won_legs = sum(1 for row in legs if row['result'] == 'won')
            tickets = connection.execute(
                """SELECT s.outcome FROM sports_ai_ticket_settlements s
                   JOIN sports_ai_accumulator_tickets t ON t.id=s.ticket_id
                   WHERE json_extract(t.parameters_json,'$.product')='weekly_draw'
                     AND json_extract(t.parameters_json,'$.pick_count')=?
                     AND json_extract(t.parameters_json,'$.mode')=?
                     AND s.outcome IN ('won','lost')""",
                (size, mode),
            ).fetchall()
            won_tickets = sum(1 for row in tickets if row['outcome'] == 'won')
            report.append({
                'pick_count': size,
                'mode': mode,
                'leg_sample': len(legs),
                'leg_wins': won_legs,
                'draw_accuracy': None if not legs else won_legs / len(legs),
                'ticket_sample': len(tickets),
                'ticket_wins': won_tickets,
                'ticket_win_rate': None if not tickets else won_tickets / len(tickets),
            })
    return report


def overview(connection, now=None):
    current = _now(now)
    policy = load_policy(connection)
    categories = []
    for mode in ('WEEKLY', 'SATURDAY_ONLY'):
        loaded = load_candidates(connection, mode, current)
        for size in SIZES:
            open_id = _open_ticket(connection, mode, size, week_id(current))
            categories.append({
                'mode': mode,
                'pick_count': size,
                'eligible': len(loaded['eligible']),
                'ticket_mode': size_mode(policy, size),
                'open_ticket_id': open_id,
                'status': 'open' if open_id else INSUFFICIENT if len(loaded['eligible']) < size else 'ready',
            })
    return {
        'schedule': describe_schedule(current),
        'policy': policy,
        'categories': categories,
        'settled': {str(size): settled_count(connection, size) for size in SIZES},
        'places_wager': False,
    }


def process_weekly_draw(connection, now=None, actor='sports-ai-worker'):
    current = _now(now)
    settled = settle_weekly_tickets(connection, current)
    monitored = monitor_open_tickets(connection, current)
    phase = phase_at(current)
    built = None
    watch = None
    if phase in ('weekly_discovery', 'saturday_revalidation') and _auto_due(connection, phase, current):
        mode = 'WEEKLY' if phase == 'weekly_discovery' else 'SATURDAY_ONLY'
        built = build_tickets(connection, mode, current, actor)
        _mark_auto(connection, phase, current, built)
    elif phase == 'saturday_watchlist' and _auto_due(connection, phase, current):
        watch = prepare_watchlist(connection, current, actor)
        _mark_auto(connection, phase, current, watch)
    return {'phase': phase, 'settled': settled, 'monitored': monitored, 'built': built, 'watchlist': watch, 'places_wager': False}


def _auto_due(connection, phase, now):
    row = connection.execute('SELECT value_json FROM sports_ai_settings WHERE key=?', (STATUS_KEY,)).fetchone()
    stored = json.loads(row['value_json']) if row else {}
    stamp = (stored.get('auto') or {}).get(phase)
    return stamp != _now(now).astimezone(WAT).date().isoformat()


def _mark_auto(connection, phase, now, summary):
    row = connection.execute('SELECT value_json FROM sports_ai_settings WHERE key=?', (STATUS_KEY,)).fetchone()
    stored = json.loads(row['value_json']) if row else {}
    stored.setdefault('auto', {})[phase] = _now(now).astimezone(WAT).date().isoformat()
    stored['last'] = summary
    connection.execute(
        """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES(?,?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
        (STATUS_KEY, json.dumps(stored), _now(now).isoformat()),
    )


def _remember(connection, summary, now):
    row = connection.execute('SELECT value_json FROM sports_ai_settings WHERE key=?', (STATUS_KEY,)).fetchone()
    stored = json.loads(row['value_json']) if row else {}
    stored['last'] = summary
    connection.execute(
        """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES(?,?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
        (STATUS_KEY, json.dumps(stored), _now(now).isoformat()),
    )


def build_weekly_draw_router(auth, require_admin):
    from fastapi import APIRouter, Depends, Header, HTTPException
    from pydantic import BaseModel, Field
    from db import connect

    router = APIRouter(prefix='/api/sports-ai/weekly-draw', tags=['Weekly draw tickets'])

    class BuildIn(BaseModel):
        mode: str = Field(pattern='^(WEEKLY|SATURDAY_ONLY)$')

    class PolicyIn(BaseModel):
        sizes: dict

    def admin(x_sports_admin_key: str | None = Header(default=None)):
        require_admin(x_sports_admin_key)
        return x_sports_admin_key

    @router.get('/overview')
    def weekly_overview(_actor=Depends(auth)):
        with connect() as connection:
            return overview(connection)

    @router.get('/candidates')
    def weekly_candidates(mode: str = 'WEEKLY', _actor=Depends(auth)):
        if mode not in ('WEEKLY', 'SATURDAY_ONLY'):
            raise HTTPException(400, 'Mode must be WEEKLY or SATURDAY_ONLY')
        with connect() as connection:
            return load_candidates(connection, mode)

    @router.post('/build')
    def weekly_build(body: BuildIn, actor=Depends(auth)):
        with connect() as connection:
            return build_tickets(connection, body.mode, actor=actor)

    @router.get('/tickets')
    def weekly_tickets(mode: str = '', _actor=Depends(auth)):
        with connect() as connection:
            return {'items': list_weekly_tickets(connection, mode)}

    @router.get('/watchlist')
    def weekly_watchlist(_actor=Depends(auth)):
        with connect() as connection:
            return {'items': list_watchlist(connection)}

    @router.get('/codes')
    def weekly_codes(_actor=Depends(auth)):
        with connect() as connection:
            return {'items': list_codes(connection), 'places_wager': False}

    @router.get('/performance')
    def weekly_performance(_actor=Depends(auth)):
        with connect() as connection:
            return {'items': performance(connection)}

    @router.get('/schedule')
    def weekly_schedule(_actor=Depends(auth)):
        return describe_schedule()

    @router.put('/policy')
    def weekly_policy(body: PolicyIn, _admin=Depends(admin)):
        try:
            with connect() as connection:
                return save_policy(connection, body.model_dump())
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    return router
