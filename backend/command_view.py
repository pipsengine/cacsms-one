"""Command-centre view model. Every figure is read from stored rows."""

import math
import os
from datetime import datetime, timedelta, timezone

from booking_schedule import WAT, opportunity_week
from workflow_status import workflow_board
from display_policy import DRAW_PROBABILITY, LIVE_LIMIT, LIVE_PROBABILITY, SLIP_PROBABILITY, confidence_from_metrics
from sport_support import GOAL_SPORTS, sql_in


def _status_label(state, reason):
    if state == 'QUALIFIED':
        return 'Qualified'
    if reason == 'RISK_REVIEW':
        return 'Risk Review'
    if state == 'MONITORING':
        return 'Monitoring'
    return (state or 'Unknown').replace('_', ' ').title()


def market_parts(market):
    name = str(market or '').upper()
    if name == 'HOME':
        return 'Match Result', 'Home Win'
    if name == 'AWAY':
        return 'Match Result', 'Away Win'
    if name == 'DRAW':
        return 'Match Result', 'Draw'
    if name.startswith('DOUBLE_CHANCE_'):
        return 'Double Chance', name.rsplit('_', 1)[-1]
    if name.startswith('BTTS_'):
        return 'Both Teams to Score', 'Yes' if name.endswith('YES') else 'No'
    if name.startswith('DNB_'):
        return 'Draw No Bet', 'Home' if name.endswith('HOME') else 'Away'
    if name.startswith('MONEYLINE_'):
        return 'Moneyline', 'Home' if name.endswith('HOME') else 'Away'
    if name.startswith(('OVER_', 'UNDER_')):
        side, line = name.split('_', 1)
        return 'Over/Under', f'{side.title()} {line}'
    if 'SPREAD_' in name or 'HANDICAP_' in name:
        return 'Handicap', name.replace('_', ' ').title()
    if '_OVER_' in name or '_UNDER_' in name:
        return 'Team Total', name.replace('_', ' ').title()
    return name.replace('_', ' ').title(), name.replace('_', ' ').title()


def command_view(connection, now=None):
    current = now or datetime.now(timezone.utc)
    local = current.astimezone(WAT)
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    week_start, week_end = opportunity_week(current)
    opportunities = _opportunities(connection, current, week_end)
    draws = _draws(connection, week_start.astimezone(timezone.utc), week_end.astimezone(timezone.utc))
    draw_count = connection.execute(
        """SELECT COUNT(*) FROM sports_ai_qualification_decisions d
           JOIN sports_ai_fixtures f ON f.id=d.fixture_id
           JOIN sports_ai_engine_predictions p ON p.id=d.prediction_id
           WHERE d.market='DRAW' AND d.state IN ('QUALIFIED','MONITORING')
             AND f.sport IN (""" + sql_in(GOAL_SPORTS) + """)
             AND p.probability>?
             AND f.starts_at>=? AND f.starts_at<?""",
        (DRAW_PROBABILITY, week_start.astimezone(timezone.utc).isoformat(), week_end.astimezone(timezone.utc).isoformat()),
    ).fetchone()[0]
    sports = _sports(connection, current)
    centre = _booking_centre(connection, current)
    codes = centre['active']
    slips = centre['slips']
    slip_count = centre['counts']['slips']
    heartbeat = connection.execute(
        "SELECT heartbeat_at FROM sports_ai_worker_leases WHERE id='scheduler-leader'",
    ).fetchone()
    online = False
    if heartbeat and heartbeat['heartbeat_at']:
        try:
            beat = datetime.fromisoformat(str(heartbeat['heartbeat_at']).replace('Z', '+00:00'))
            if beat.tzinfo is None:
                beat = beat.replace(tzinfo=timezone.utc)
            online = (current - beat).total_seconds() < 120
        except ValueError:
            online = False
    qualified_visible = connection.execute(
        """SELECT COUNT(*) FROM (
             SELECT d.fixture_id FROM sports_ai_qualification_decisions d
             JOIN sports_ai_engine_predictions p ON p.id=d.prediction_id
             JOIN sports_ai_fixtures f ON f.id=d.fixture_id
             WHERE d.state='QUALIFIED' AND p.probability>? AND f.starts_at>? AND f.starts_at<?
             GROUP BY d.fixture_id
           )""",
        (LIVE_PROBABILITY, current.isoformat(), week_end.astimezone(timezone.utc).isoformat()),
    ).fetchone()[0]
    qualified_new = connection.execute(
        """SELECT COUNT(*) FROM (
             SELECT d.fixture_id FROM sports_ai_qualification_decisions d
             JOIN sports_ai_engine_predictions p ON p.id=d.prediction_id
             WHERE d.state='QUALIFIED' AND p.probability>? AND d.created_at>=?
             GROUP BY d.fixture_id
           )""",
        (LIVE_PROBABILITY, day_start.isoformat()),
    ).fetchone()[0]
    risk = connection.execute(
        """SELECT COUNT(*) FROM sports_ai_qualification_decisions
           WHERE state='MONITORING' AND reason_code='RISK_REVIEW'""",
    ).fetchone()[0]
    unresolved = connection.execute(
        "SELECT COUNT(*) FROM sports_ai_fixture_identity_links WHERE status='unresolved'",
    ).fetchone()[0]
    settled = connection.execute(
        """SELECT
             SUM(CASE WHEN settled_outcome='won' THEN 1 ELSE 0 END) AS won,
             SUM(CASE WHEN settled_outcome='lost' THEN 1 ELSE 0 END) AS lost
           FROM sports_ai_engine_predictions
           WHERE result_settled_at IS NOT NULL AND observation_class='live_observed'""",
    ).fetchone()
    won = int(settled['won'] or 0)
    lost = int(settled['lost'] or 0)
    average = connection.execute(
        """SELECT AVG(combined_odds) FROM sports_ai_accumulator_tickets
           WHERE status='qualified' AND publication_state='READY_FOR_WINDOW'""",
    ).fetchone()[0]
    from pipeline_engine import latest_snapshot
    snapshot = latest_snapshot(connection) or {}
    stages = {item['stage']: item['count'] for item in snapshot.get('stages') or []}
    alerts = []
    if risk:
        alerts.append({'id': 'risk', 'message': f'{risk} selection{"s" if risk != 1 else ""} held for risk review.'})
    if unresolved:
        alerts.append({'id': 'identity', 'message': f'{unresolved} past-kickoff fixture{"s" if unresolved != 1 else ""} unresolved.'})
    if not stages.get('ODDS_FRESH'):
        alerts.append({'id': 'odds', 'message': 'No SportyBet price is inside 15 minutes.'})
    active_names = [item['name'] for item in sports if item['status'] == 'Active']
    preview = os.environ.get('SPORTS_LOCAL_PREVIEW', '0') == '1'
    return {
        'environment': 'Development (Preview)' if preview else 'Development',
        'engine_online': online,
        'updated_at': None if not heartbeat else heartbeat['heartbeat_at'],
        'places_wager': False,
        'kpis': {
            'qualified': qualified_visible,
            'qualified_new': qualified_new,
            'slips': slip_count,
            'codes': centre['counts']['active'],
            'codes_on_qualified_ticket': centre['counts']['active_qualified'],
            'settled': won + lost,
            'draws': draw_count,
            'active_sports': len(active_names),
            'active_sport_names': active_names,
        },
        'opportunities': opportunities,
        'draws': draws,
        'sports': sports,
        'codes': codes,
        'slips': slips,
        'history': centre['history'],
        'booking_counts': centre['counts'],
        'pipeline': [
            {'name': 'Fixtures discovered', 'count': stages.get('DISCOVERED', 0)},
            {'name': 'Predictions generated', 'count': stages.get('PREDICTED', 0)},
            {'name': 'Qualified selections', 'count': qualified_visible},
            {'name': 'Bet slips ready', 'count': slip_count},
            {'name': 'Booking codes stored', 'count': stages.get('CODE_VERIFIED', 0)},
        ],
        'performance': {
            'settled': won + lost,
            'won': won,
            'lost': lost,
            'win_rate': round(100 * won / (won + lost), 1) if won + lost else None,
            'predictions_30d': connection.execute(
                "SELECT COUNT(*) FROM sports_ai_engine_predictions WHERE created_at>=?",
                ((current - timedelta(days=30)).isoformat(),),
            ).fetchone()[0],
            'average_odds': None if average is None else round(float(average), 2),
        },
        'alerts': alerts,
        'workflow': workflow_board(
            connection, current,
            ready_slips=slip_count,
            qualified_visible=qualified_visible,
            win_rate=round(100 * won / (won + lost), 1) if won + lost else None,
        ),
    }


def _opportunities(connection, now, week_end):
    from market_registry import choose_best
    rows = connection.execute(
        """SELECT d.fixture_id, d.state, d.reason_code, d.market, p.probability, f.sport, f.home, f.away, f.competition, f.starts_at,
                  q.decimal_odds, m.metrics_json
           FROM sports_ai_qualification_decisions d
           JOIN sports_ai_fixtures f ON f.id=d.fixture_id
           JOIN sports_ai_engine_predictions p ON p.id=d.prediction_id
           LEFT JOIN sports_ai_model_registry m ON m.id=p.model_id
           LEFT JOIN sports_ai_value_opportunities o ON o.id=d.opportunity_id
           LEFT JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
           WHERE f.starts_at>? AND f.starts_at<?
             AND p.probability>?
             AND (d.state='QUALIFIED' OR (d.state='MONITORING' AND d.reason_code='RISK_REVIEW'))""",
        (now.isoformat(), week_end.astimezone(timezone.utc).isoformat(), LIVE_PROBABILITY),
    ).fetchall()
    prepared = []
    for row in rows:
        prepared.append({
            'fixture_id': row['fixture_id'],
            'state': row['state'],
            'reason_code': row['reason_code'],
            'market_key': row['market'],
            'probability': float(row['probability']),
            'confidence': confidence_from_metrics(row['metrics_json']),
            'sport': str(row['sport'] or '').lower(),
            'home': row['home'],
            'away': row['away'],
            'starts_at': row['starts_at'],
            'competition': row['competition'],
            'decimal_odds': row['decimal_odds'],
        })
    ranked = choose_best(prepared)
    ranked.sort(key=lambda item: (-item['probability'], -(item['confidence'] if item['confidence'] is not None else -1)))
    items = []
    for row in ranked[:LIVE_LIMIT]:
        family, selection = market_parts(row['market_key'])
        items.append({
            'fixture_id': row['fixture_id'],
            'sport': row['sport'],
            'sport_label': str(row['sport'] or '').replace('_', ' ').title(),
            'fixture': f"{row['home']} vs {row['away']}",
            'home': row['home'],
            'away': row['away'],
            'starts_at': row['starts_at'],
            'competition': row['competition'],
            'market': family,
            'selection': selection,
            'probability': round(row['probability'] * 100, 1),
            'confidence': None if row['confidence'] is None else round(row['confidence'] * 100, 1),
            'odds': None if row['decimal_odds'] is None else round(float(row['decimal_odds']), 2),
            'status': _status_label(row['state'], row['reason_code']),
        })
    return items


def _draws(connection, start, end):
    rows = connection.execute(
        f"""SELECT d.state, d.reason_code, p.probability, f.sport, f.home, f.away, f.competition, f.starts_at, q.decimal_odds
           FROM sports_ai_qualification_decisions d
           JOIN sports_ai_fixtures f ON f.id=d.fixture_id
           JOIN sports_ai_engine_predictions p ON p.id=d.prediction_id
           LEFT JOIN sports_ai_value_opportunities o ON o.id=d.opportunity_id
           LEFT JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
           WHERE d.market='DRAW' AND d.state IN ('QUALIFIED','MONITORING')
             AND f.sport IN ({sql_in(GOAL_SPORTS)})
             AND p.probability>?
             AND f.starts_at>=? AND f.starts_at<?
           ORDER BY p.probability DESC, f.starts_at
           LIMIT 40""",
        (DRAW_PROBABILITY, start.isoformat(), end.isoformat()),
    ).fetchall()
    return [
        {
            'sport': row['sport'],
            'sport_label': str(row['sport'] or '').replace('_', ' ').title(),
            'fixture': f"{row['home']} vs {row['away']}",
            'competition': row['competition'],
            'starts_at': row['starts_at'],
            'probability': None if row['probability'] is None else round(float(row['probability']) * 100, 1),
            'odds': None if row['decimal_odds'] is None else round(float(row['decimal_odds']), 2),
            'status': _status_label(row['state'], row['reason_code']),
        }
        for row in rows
    ]


def _sports(connection, now):
    counts = {
        row['sport']: row['fixtures']
        for row in connection.execute(
            """SELECT sport, COUNT(*) AS fixtures FROM sports_ai_fixtures
               WHERE status='scheduled' AND starts_at>=? GROUP BY sport""",
            (now.isoformat(),),
        )
    }
    registry = {
        row['sport']: row['active']
        for row in connection.execute(
            """SELECT sport, SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS active
               FROM sports_ai_market_registry GROUP BY sport""",
        )
    }
    modelled = {
        row['code'] for row in connection.execute(
            "SELECT code FROM sports_ai_sports WHERE prediction_status='modelled'",
        )
    }
    from sport_support import coverage_status
    names = list(dict.fromkeys([*counts.keys(), *registry.keys()]))
    rows = []
    for sport in names:
        status = coverage_status(sport, modelled, registry.get(sport))
        rows.append({'name': str(sport).replace('_', ' ').title(), 'fixtures': counts.get(sport, 0), 'status': status})
    rows.sort(key=lambda item: ({'Active': 0, 'Research': 1, 'Unsupported': 2}[item['status']], -item['fixtures'], item['name']))
    return [item for item in rows if item['fixtures'] or item['status'] == 'Active']


def ticket_probability(basis, joint):
    """Win probability only when the stored joint model treated the legs as independent.

    A correlated or partial-settlement ticket keeps a null probability. Nothing is multiplied here.
    """
    if basis != 'independent' or joint is None:
        return None
    try:
        value = float(joint)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0 or value > 1:
        return None
    return round(value * 100, 1)


def ticket_confidence(reliabilities, leg_count):
    """Lowest stored model-reliability on the ticket.

    Reliability is the calibration figure already saved on each leg. It is not a win probability.
    A missing leg leaves the ticket without a confidence figure.
    """
    if not leg_count or reliabilities is None or len(reliabilities) != int(leg_count):
        return None
    values = []
    for item in reliabilities:
        if item is None:
            return None
        try:
            value = float(item)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value) or value < 0 or value > 1:
            return None
        values.append(value)
    return round(min(values) * 100, 1)


def _verification_label(row, now, coded, verified):
    if verified:
        return 'Verified'
    if not coded or not str(row['code'] or '').strip():
        return 'Awaiting code'
    expired = row['code_status'] == 'expired' or (row['expires_at'] and str(row['expires_at']) <= now.isoformat())
    if expired:
        return 'Expired'
    return 'Unverified'


def _displayed_code(row, now):
    """A code is verified only when SportyBet displayed it and the evidence is still in date."""
    code = str(row['code'] or '').strip()
    evidence = str(row['evidence'] or '').strip()
    method = str(row['verification_method'] or '').strip()
    if not code or not evidence or not row['verified_at'] or row['code_status'] != 'verified':
        return False
    if method not in ('displayed', 'sportybet_page', 'page'):
        return False
    expires = row['expires_at']
    if not expires:
        return True
    return str(expires) > now.isoformat()


def _settlement(status, won, lost, pending, total):
    won, lost, pending, total = (int(won or 0), int(lost or 0), int(pending or 0), int(total or 0))
    if status == 'settled':
        if total and lost == 0 and won == total:
            return 'Won'
        if lost:
            return 'Lost'
        return 'Settled'
    if status == 'void':
        return 'Void'
    if status == 'expired':
        return 'Expired'
    if pending and (won or lost):
        return 'In progress'
    if status == 'qualified':
        return 'Open'
    return str(status or 'Stored').replace('_', ' ').title()


# A ticket stays on the live tabs only while every selection is still pending and has not kicked off.
# One started or settled match removes the whole ticket. History keeps the booking code.
_UPCOMING_TICKET = """
    t.id IS NOT NULL
    AND EXISTS (SELECT 1 FROM sports_ai_accumulator_legs l WHERE l.ticket_id=t.id)
    AND NOT EXISTS (
        SELECT 1 FROM sports_ai_accumulator_legs l
        WHERE l.ticket_id=t.id AND (
            l.starts_at IS NULL OR l.starts_at<=?
            OR COALESCE(l.result, 'pending') NOT IN ('pending', '')
        )
    )
"""
_PLAYED_TICKET = """
    (
        t.id IS NULL
        OR NOT EXISTS (SELECT 1 FROM sports_ai_accumulator_legs l WHERE l.ticket_id=t.id)
        OR EXISTS (
            SELECT 1 FROM sports_ai_accumulator_legs l
            WHERE l.ticket_id=t.id AND (
                l.starts_at IS NULL OR l.starts_at<=?
                OR COALESCE(l.result, 'pending') NOT IN ('pending', '')
            )
        )
    )
"""


def _booking_centre(connection, now):
    moment = now.isoformat()
    active_filter = f"""
        b.status='verified' AND b.verification_method IN ('displayed','sportybet_page','page')
        AND b.verified_at IS NOT NULL AND TRIM(COALESCE(b.evidence,''))<>''
        AND TRIM(COALESCE(b.code,''))<>''
        AND (b.expires_at IS NULL OR b.expires_at>?)
        AND COALESCE(t.status,'') NOT IN ('settled','void','expired')
        AND {_UPCOMING_TICKET}
    """
    history_filter = f"""
        TRIM(COALESCE(b.code,''))<>''
        AND (
            b.status='expired' OR (b.expires_at IS NOT NULL AND b.expires_at<=?)
            OR t.status IN ('settled','void','expired')
            OR {_PLAYED_TICKET}
        )
    """
    slip_filter = f"""
        t.status='qualified' AND t.publication_state='READY_FOR_WINDOW'
        AND {_UPCOMING_TICKET}
        AND t.probability_basis='independent' AND t.joint_probability>?
        AND NOT EXISTS (
            SELECT 1 FROM sports_ai_booking_codes b
            WHERE b.accumulator_ticket_id=t.id AND b.status='verified'
              AND b.verification_method IN ('displayed','sportybet_page','page')
              AND b.verified_at IS NOT NULL AND TRIM(COALESCE(b.evidence,''))<>''
              AND TRIM(COALESCE(b.code,''))<>''
              AND (b.expires_at IS NULL OR b.expires_at>?)
        )
    """
    code_args = (moment, moment)
    slip_args = (moment, SLIP_PROBABILITY, moment)
    counts = {
        'active': connection.execute(f'SELECT COUNT(*) FROM sports_ai_booking_codes b LEFT JOIN sports_ai_accumulator_tickets t ON t.id=b.accumulator_ticket_id WHERE {active_filter}', code_args).fetchone()[0],
        'active_qualified': connection.execute(f"SELECT COUNT(*) FROM sports_ai_booking_codes b LEFT JOIN sports_ai_accumulator_tickets t ON t.id=b.accumulator_ticket_id WHERE {active_filter} AND t.status='qualified'", code_args).fetchone()[0],
        'slips': connection.execute(f'SELECT COUNT(*) FROM sports_ai_accumulator_tickets t WHERE {slip_filter}', slip_args).fetchone()[0],
        'history': connection.execute(f'SELECT COUNT(*) FROM sports_ai_booking_codes b LEFT JOIN sports_ai_accumulator_tickets t ON t.id=b.accumulator_ticket_id WHERE {history_filter}', code_args).fetchone()[0],
    }
    active = _code_rows(connection, now, active_filter, code_args, 'starts_at ASC')
    history = _code_rows(connection, now, history_filter, code_args, 'COALESCE(b.verified_at, b.created_at) DESC')
    slips = _slip_rows(connection, now, slip_filter, slip_args)
    counts['slips'] = len(slips)
    return {'active': active, 'history': history, 'slips': slips[:12], 'counts': counts}


_CODE_SELECT = """
    SELECT t.id AS ticket_id, t.status AS ticket_status, t.leg_count, t.combined_odds,
           t.joint_probability, t.probability_basis,
           b.code, b.status AS code_status, b.verification_method, b.evidence, b.expires_at,
           b.verified_at, b.created_at AS code_created_at, b.bookmaker,
           (SELECT MIN(starts_at) FROM sports_ai_accumulator_legs WHERE ticket_id=t.id) AS starts_at,
           (SELECT COUNT(*) FROM sports_ai_accumulator_legs WHERE ticket_id=t.id AND reliability IS NOT NULL) AS reliability_count,
           (SELECT MIN(reliability) FROM sports_ai_accumulator_legs WHERE ticket_id=t.id) AS reliability_min,
           (SELECT SUM(CASE WHEN result='won' THEN 1 ELSE 0 END) FROM sports_ai_accumulator_legs WHERE ticket_id=t.id) AS won,
           (SELECT SUM(CASE WHEN result='lost' THEN 1 ELSE 0 END) FROM sports_ai_accumulator_legs WHERE ticket_id=t.id) AS lost,
           (SELECT SUM(CASE WHEN result='pending' THEN 1 ELSE 0 END) FROM sports_ai_accumulator_legs WHERE ticket_id=t.id) AS pending
    FROM sports_ai_booking_codes b
    LEFT JOIN sports_ai_accumulator_tickets t ON t.id=b.accumulator_ticket_id
"""


def _code_rows(connection, now, where, args, order):
    rows = connection.execute(
        f'{_CODE_SELECT} WHERE {where} ORDER BY {order} LIMIT 12',
        args,
    ).fetchall()
    return _attach_legs(connection, [_public_ticket(row, now, coded=True) for row in rows])


def _slip_rows(connection, now, where, args):
    rows = connection.execute(
        f"""SELECT t.id AS ticket_id, t.status AS ticket_status, t.leg_count, t.combined_odds,
                  t.joint_probability, t.probability_basis, t.created_at AS code_created_at,
                  NULL AS code, NULL AS code_status, NULL AS verification_method, NULL AS evidence, NULL AS expires_at,
                  NULL AS verified_at, 'SportyBet' AS bookmaker,
                  (SELECT MIN(starts_at) FROM sports_ai_accumulator_legs WHERE ticket_id=t.id) AS starts_at,
                  (SELECT COUNT(*) FROM sports_ai_accumulator_legs WHERE ticket_id=t.id AND reliability IS NOT NULL) AS reliability_count,
                  (SELECT MIN(reliability) FROM sports_ai_accumulator_legs WHERE ticket_id=t.id) AS reliability_min,
                  (SELECT SUM(CASE WHEN result='won' THEN 1 ELSE 0 END) FROM sports_ai_accumulator_legs WHERE ticket_id=t.id) AS won,
                  (SELECT SUM(CASE WHEN result='lost' THEN 1 ELSE 0 END) FROM sports_ai_accumulator_legs WHERE ticket_id=t.id) AS lost,
                  (SELECT SUM(CASE WHEN result='pending' THEN 1 ELSE 0 END) FROM sports_ai_accumulator_legs WHERE ticket_id=t.id) AS pending
           FROM sports_ai_accumulator_tickets t
           WHERE {where}
           ORDER BY t.created_at DESC
           LIMIT 400""",
        args,
    ).fetchall()
    slips = _attach_legs(connection, [_public_ticket(row, now, coded=False) for row in rows])
    return _one_slip_per_selection(slips)


def _one_slip_per_selection(rows):
    chosen = {}
    for row in rows:
        legs = row.get('legs') or []
        key = '|'.join(sorted(
            f"{leg['home']}|{leg['away']}|{leg['market']}|{leg['starts_at']}" for leg in legs
        )) or row.get('ticket_id')
        if key not in chosen:
            chosen[key] = row
    return sorted(chosen.values(), key=lambda row: row.get('starts_at') or '')


def _public_ticket(row, now, coded):
    verified = coded and _displayed_code(row, now)
    leg_count = int(row['leg_count'] or 0)
    reliabilities = None
    if row['ticket_id'] and leg_count and int(row['reliability_count'] or 0) == leg_count and row['reliability_min'] is not None:
        reliabilities = [row['reliability_min']] * leg_count
    ticket_id = row['ticket_id']
    return {
        'id': ticket_id or row['code'],
        'ticket_id': ticket_id,
        'ticket_no': None if not ticket_id else str(ticket_id).replace('-', '')[:8].upper(),
        'code': str(row['code']).strip() if row['code'] else None,
        'verified': verified,
        'verification': _verification_label(row, now, coded, verified),
        'settlement': _settlement(row['ticket_status'], row['won'], row['lost'], row['pending'], leg_count),
        'selection_count': leg_count or None,
        'odds': None if row['combined_odds'] is None else round(float(row['combined_odds']), 2),
        'combined_odds': None if row['combined_odds'] is None else round(float(row['combined_odds']), 2),
        'starts_at': row['starts_at'],
        'probability': ticket_probability(row['probability_basis'], row['joint_probability']),
        'confidence': None if reliabilities is None else ticket_confidence(reliabilities, leg_count),
        'probability_basis': row['probability_basis'],
        'bookmaker': row['bookmaker'] or 'SportyBet',
        'created_at': row['verified_at'] or row['code_created_at'],
        'historical': row['ticket_status'] not in (None, 'qualified'),
        'status': 'Ready' if not coded else row['ticket_status'],
    }


def _attach_legs(connection, rows):
    ids = [row['ticket_id'] for row in rows if row.get('ticket_id')]
    grouped = {}
    if ids:
        marks = ','.join('?' for _ in ids)
        for leg in connection.execute(
            f"""SELECT ticket_id, home, away, competition, market, selection, decimal_odds, probability, starts_at, result
                FROM sports_ai_accumulator_legs WHERE ticket_id IN ({marks}) ORDER BY starts_at""",
            ids,
        ):
            grouped.setdefault(leg['ticket_id'], []).append({
                'home': leg['home'],
                'away': leg['away'],
                'competition': leg['competition'],
                'market': leg['market'],
                'selection': leg['selection'],
                'odds': None if leg['decimal_odds'] is None else round(float(leg['decimal_odds']), 2),
                'probability': None if leg['probability'] is None else round(float(leg['probability']) * 100, 1),
                'starts_at': leg['starts_at'],
                'result': leg['result'],
            })
    for row in rows:
        row['legs'] = grouped.get(row['ticket_id'], [])
    return rows
