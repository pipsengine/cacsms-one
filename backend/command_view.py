"""Command-centre view model. Every figure is read from stored rows."""

import os
from datetime import datetime, timedelta, timezone

from booking_schedule import WAT


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
    week_start = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=7)
    opportunities = _opportunities(connection, current)
    draws = _draws(connection, week_start.astimezone(timezone.utc), week_end.astimezone(timezone.utc))
    draw_count = connection.execute(
        """SELECT COUNT(*) FROM sports_ai_qualification_decisions d
           JOIN sports_ai_fixtures f ON f.id=d.fixture_id
           WHERE d.market='DRAW' AND d.state IN ('QUALIFIED','MONITORING')
             AND f.starts_at>=? AND f.starts_at<?""",
        (week_start.astimezone(timezone.utc).isoformat(), week_end.astimezone(timezone.utc).isoformat()),
    ).fetchone()[0]
    sports = _sports(connection, current)
    codes = _codes(connection)
    slips = _slips(connection)
    slip_count = connection.execute(
        """SELECT COUNT(*) FROM sports_ai_accumulator_tickets
           WHERE status='qualified' AND publication_state='READY_FOR_WINDOW'""",
    ).fetchone()[0]
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
    qualified_new = connection.execute(
        """SELECT COUNT(*) FROM sports_ai_qualification_decisions
           WHERE state='QUALIFIED' AND created_at>=?""",
        (day_start.isoformat(),),
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
            'qualified': stages.get('QUALIFIED', 0),
            'qualified_new': qualified_new,
            'slips': slip_count,
            'codes': stages.get('CODE_VERIFIED', 0),
            'codes_on_qualified_ticket': stages.get('DISPLAYED', 0),
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
        'pipeline': [
            {'name': 'Fixtures discovered', 'count': stages.get('DISCOVERED', 0)},
            {'name': 'Predictions generated', 'count': stages.get('PREDICTED', 0)},
            {'name': 'Qualified selections', 'count': stages.get('QUALIFIED', 0)},
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
    }


def _opportunities(connection, now):
    rows = connection.execute(
        """SELECT d.fixture_id, d.state, d.reason_code, d.market, p.probability, f.sport, f.home, f.away, f.competition, f.starts_at,
                  q.decimal_odds, o.expected_edge
           FROM sports_ai_qualification_decisions d
           JOIN sports_ai_fixtures f ON f.id=d.fixture_id
           JOIN sports_ai_engine_predictions p ON p.id=d.prediction_id
           LEFT JOIN sports_ai_value_opportunities o ON o.id=d.opportunity_id
           LEFT JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
           WHERE f.starts_at>?
             AND (d.state='QUALIFIED' OR (d.state='MONITORING' AND d.reason_code='RISK_REVIEW'))
           ORDER BY CASE d.state WHEN 'QUALIFIED' THEN 0 ELSE 1 END,
                    o.expected_edge IS NULL, o.expected_edge DESC
           LIMIT 8""",
        (now.isoformat(),),
    ).fetchall()
    items = []
    for row in rows:
        family, selection = market_parts(row['market'])
        items.append({
            'fixture_id': row['fixture_id'] if 'fixture_id' in row.keys() else None,
            'sport': str(row['sport'] or '').lower(),
            'sport_label': str(row['sport'] or '').title(),
            'fixture': f"{row['home']} vs {row['away']}",
            'home': row['home'],
            'away': row['away'],
            'starts_at': row['starts_at'] if 'starts_at' in row.keys() else None,
            'competition': row['competition'],
            'market': family,
            'selection': selection,
            'probability': None if row['probability'] is None else round(float(row['probability']) * 100, 1),
            'odds': None if row['decimal_odds'] is None else round(float(row['decimal_odds']), 2),
            'status': _status_label(row['state'], row['reason_code']),
        })
    return items


def _draws(connection, start, end):
    rows = connection.execute(
        """SELECT d.state, d.reason_code, p.probability, f.home, f.away, f.competition, q.decimal_odds
           FROM sports_ai_qualification_decisions d
           JOIN sports_ai_fixtures f ON f.id=d.fixture_id
           JOIN sports_ai_engine_predictions p ON p.id=d.prediction_id
           LEFT JOIN sports_ai_value_opportunities o ON o.id=d.opportunity_id
           LEFT JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
           WHERE d.market='DRAW' AND d.state IN ('QUALIFIED','MONITORING')
             AND f.starts_at>=? AND f.starts_at<?
           ORDER BY CASE d.state WHEN 'QUALIFIED' THEN 0 ELSE 1 END, f.starts_at
           LIMIT 8""",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return [
        {
            'fixture': f"{row['home']} vs {row['away']}",
            'competition': row['competition'],
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
    names = list(dict.fromkeys([*counts.keys(), *registry.keys()]))
    rows = []
    for sport in names:
        if sport in modelled and registry.get(sport):
            status = 'Active'
        elif sport in modelled:
            status = 'Research'
        else:
            status = 'Unsupported'
        rows.append({'name': str(sport).replace('_', ' ').title(), 'fixtures': counts.get(sport, 0), 'status': status})
    rows.sort(key=lambda item: ({'Active': 0, 'Research': 1, 'Unsupported': 2}[item['status']], -item['fixtures'], item['name']))
    return [item for item in rows if item['fixtures'] or item['status'] == 'Active'][:8]


def _codes(connection):
    rows = connection.execute(
        """SELECT b.code, b.verified_at, b.created_at, t.leg_count, t.combined_odds, t.status AS ticket_status
           FROM sports_ai_booking_codes b
           LEFT JOIN sports_ai_accumulator_tickets t ON t.id=b.accumulator_ticket_id
           WHERE b.status='verified' AND b.verification_method='displayed' AND TRIM(COALESCE(b.code,''))<>''
           ORDER BY COALESCE(b.verified_at, b.created_at) DESC
           LIMIT 5""",
    ).fetchall()
    return [
        {
            'code': row['code'],
            'verified': True,
            'historical': row['ticket_status'] != 'qualified',
            'selection_count': row['leg_count'],
            'combined_odds': None if row['combined_odds'] is None else round(float(row['combined_odds']), 2),
            'created_at': row['verified_at'] or row['created_at'],
            'bookmaker': 'SportyBet',
        }
        for row in rows
    ]


def _slips(connection):
    rows = connection.execute(
        """SELECT t.id, t.leg_count, t.combined_odds, t.joint_probability,
                  (SELECT group_concat(l.home || ' vs ' || l.away, ' · ')
                   FROM sports_ai_accumulator_legs l WHERE l.ticket_id=t.id) AS matchup
           FROM sports_ai_accumulator_tickets t
           WHERE t.status='qualified' AND t.publication_state='READY_FOR_WINDOW'
           ORDER BY t.created_at DESC
           LIMIT 6""",
    ).fetchall()
    return [
        {
            'id': row['id'],
            'matchup': row['matchup'],
            'selections': row['leg_count'],
            'probability': None if row['joint_probability'] is None else round(float(row['joint_probability']) * 100, 1),
            'odds': None if row['combined_odds'] is None else round(float(row['combined_odds']), 2),
            'status': 'Ready',
        }
        for row in rows
    ]
