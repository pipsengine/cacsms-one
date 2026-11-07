"""Alternate high-odds tickets for the overview card.

Each selection is at least 80%. The number of selections follows the price:
15 to 30 when every price is 1.95 or below, 12 from 2.00 up to 3.00, 10 from
3.00 up to 4.00, 8 from 4.00 through 5.00, and 8 or fewer when every price is
above 5.00 and the combined price is above 10,000. A booking code is stored
only when SportyBet displays it. No wager is placed.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

from booking_schedule import WAT, opportunity_week
from display_policy import confidence_from_metrics

SELECTION_FLOOR = 0.80
PREFERRED_PROBABILITY = 0.85
PREFERRED_CONFIDENCE = 0.80
MIN_LEG_ODDS = 2.0
LOW_ODDS_CEILING = 1.95
COMBINED_TARGET = 10000
ODDS_BANDS = (
    {'id': 'to-1.95', 'count': 30, 'minimum': 15, 'at_most': True, 'odds_label': '1.95 and below'},
    {'id': '2-3', 'count': 12, 'odds_label': '2.00–2.99'},
    {'id': '3-4', 'count': 10, 'odds_label': '3.00–3.99'},
    {'id': '4-5', 'count': 8, 'odds_label': '4.00–5.00'},
    {'id': 'above-5', 'count': 8, 'at_most': True, 'minimum_product': COMBINED_TARGET, 'odds_label': 'above 5.00'},
)
BOOKABLE_SPORTS = {'football', 'basketball'}
HIGHER_ODDS_BANDS = ('2-3', '3-4', '4-5', 'above-5')
MAX_TICKETS_PER_BAND = 4
SURE_COMBINED_MIN = 2.0
SURE_COMBINED_MAX = 2.95


def _certain(row):
    """A selection shown as 100% may be used on more than one ticket."""
    return round(float(row['probability']) * 100, 1) >= 100


def _market_family(market):
    parts = str(market or 'SELECTION').upper().split('_')
    while parts:
        try:
            float(parts[-1])
        except ValueError:
            break
        parts.pop()
    return '_'.join(parts) or 'SELECTION'


def _in_band(odds, band_id):
    if band_id == 'to-1.95':
        return 1 < odds <= LOW_ODDS_CEILING
    if band_id == '2-3':
        return 2 <= odds < 3
    if band_id == '3-4':
        return 3 <= odds < 4
    if band_id == '4-5':
        return 4 <= odds <= 5
    if band_id == 'above-5':
        return odds > 5
    return False


def _line(value):
    if value is None or value == '':
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _selection_name(market, outcome, home, away):
    if outcome:
        return str(outcome)
    name = str(market or '').upper()
    if name in ('HOME', 'MONEYLINE_HOME'):
        return home
    if name in ('AWAY', 'MONEYLINE_AWAY'):
        return away
    return name or 'Selection'


def _risk_review(connection, rows):
    """A selection held for risk review stays off this card and is not booked."""
    fixture_ids = list({row['fixture_id'] for row in rows})
    if not fixture_ids:
        return set()
    decisions = _chunked(
        connection,
        """SELECT fixture_id, market, state, reason_code, updated_at
           FROM sports_ai_qualification_decisions
           WHERE fixture_id IN ({marks})""",
        fixture_ids,
    )
    latest = {}
    for row in decisions:
        key = (row['fixture_id'], str(row['market'] or '').upper())
        if key in latest and str(row['updated_at'] or '') < latest[key][0]:
            continue
        latest[key] = (str(row['updated_at'] or ''), row['state'], row['reason_code'])
    return {key for key, item in latest.items() if item[2] == 'RISK_REVIEW'}


def _label(market):
    name = str(market or '').upper().replace('_', ' ')
    return name.title() if name else 'Selection'


def _day_window(now):
    monday, next_monday = opportunity_week(now)
    saturday = monday + timedelta(days=5)
    sunday = monday + timedelta(days=6)
    return (
        {'name': 'Saturday', 'start': saturday, 'end': sunday},
        {'name': 'Sunday', 'start': sunday, 'end': next_monday},
    )


def _preferred(row):
    confidence = row.get('confidence')
    return (
        row['probability'] >= PREFERRED_PROBABILITY
        and confidence is not None
        and confidence >= PREFERRED_CONFIDENCE
    )


def choose_one_per_fixture(rows):
    """Keep the strongest stored market on a fixture. A second market on the same match is not an independent selection."""
    chosen = {}
    for row in rows:
        if row['probability'] < SELECTION_FLOOR or row['decimal_odds'] <= 1:
            continue
        row = dict(row)
        row['preferred'] = _preferred(row)
        rank = (
            1 if row['preferred'] else 0,
            row['decimal_odds'] if row['preferred'] else row['probability'],
            row['probability'],
            row['confidence'] if row['confidence'] is not None else -1,
        )
        current = chosen.get(row['fixture_id'])
        if current is None or rank > current['rank']:
            row['rank'] = rank
            chosen[row['fixture_id']] = row
    return list(chosen.values())


def _rotate(pool, longest_first):
    """Take one sport, then the next, and a new league before a second price in a league already used."""
    if not pool:
        return []

    def rank(item):
        odds = -float(item['decimal_odds']) if longest_first else float(item['decimal_odds'])
        return (odds, -float(item['probability']))

    by_sport = {}
    for item in pool:
        by_sport.setdefault(item.get('sport') or '', {}).setdefault(item.get('competition') or '', []).append(item)
    sport_names = sorted(
        by_sport,
        key=lambda sport: min(rank(leg) for competitions in by_sport[sport].values() for leg in competitions),
    )
    queues = []
    for sport in sport_names:
        competitions = by_sport[sport]
        names = sorted(competitions, key=lambda name: min(rank(leg) for leg in competitions[name]))
        buckets = [_mix_markets(competitions[name], rank) for name in names]
        queue = []
        while any(buckets):
            for bucket in buckets:
                if bucket:
                    queue.append(bucket.pop(0))
        queues.append(queue)
    ordered = []
    while any(queues):
        for queue in queues:
            if queue:
                ordered.append(queue.pop(0))
    return ordered


def _mix_markets(legs, rank):
    """Take a different market before repeating one. A 100% selection stays available."""
    groups = {}
    order = []
    for leg in sorted(legs, key=rank):
        family = _market_family(leg.get('market'))
        if family not in groups:
            order.append(family)
            groups[family] = []
        groups[family].append(leg)
    mixed = []
    while any(groups.values()):
        for family in order:
            if groups[family]:
                mixed.append(groups[family].pop(0))
    return mixed


def _span(pool):
    """One selection from each sport in turn. Within a sport, 85% selections and a new league come first."""
    by_sport = {}
    for item in pool:
        if float(item['probability']) < SELECTION_FLOOR or float(item['decimal_odds']) <= 1:
            continue
        by_sport.setdefault(item.get('sport') or '', []).append(item)
    queues = []
    for sport in sorted(by_sport, key=lambda name: (0 if any(leg['preferred'] for leg in by_sport[name]) else 1, name)):
        legs = by_sport[sport]
        preferred = [leg for leg in legs if leg['preferred']]
        reserve = [leg for leg in legs if not leg['preferred']]
        queues.append(_rotate(preferred, True) + _rotate(reserve, True))
    ordered = []
    while any(queues):
        for queue in queues:
            if queue:
                ordered.append(queue.pop(0))
    certain = [leg for leg in ordered if _certain(leg)]
    return certain + [leg for leg in ordered if leg not in certain]


def _selection_available(row, used):
    """A fixture already used on a ticket stays off the next one, unless that selection is 100%."""
    market = used.get(row['fixture_id'])
    if market is None:
        return True
    return _certain(row) and market == row['market']


def build_slip(candidates, count, at_most=False, minimum=None, minimum_product=None):
    """Take the selections for one odds band. A short pool is not padded."""
    ordered = _span(candidates)
    floor = minimum if minimum is not None else (2 if at_most else count)
    if len(ordered) < floor:
        return None
    if at_most:
        count = min(len(ordered), count)
    chosen = ordered[:count]
    product = 1.0
    joint = 1.0
    for leg in chosen:
        product *= float(leg['decimal_odds'])
        joint *= float(leg['probability'])
    if minimum_product is not None and product <= minimum_product:
        return None
    return chosen, product, joint


def pack_sure_tickets(candidates, day):
    """Combine analyzed selections until the ticket price is from 2.00 to 2.95.

    Every selection stays at least 80%. One match is used once. A single price
    is not a ticket. No wager is placed.
    """
    pool = choose_one_per_fixture([
        row for row in candidates
        if float(row['probability']) >= SELECTION_FLOOR
        and row.get('sport') in BOOKABLE_SPORTS
        and 1 < float(row['decimal_odds']) <= SURE_COMBINED_MAX
    ])
    ordered = sorted(pool, key=lambda leg: (-float(leg['probability']), float(leg['decimal_odds']), leg.get('starts_at') or ''))
    band = {'id': '2-2.95', 'odds_label': '2.00–2.95'}
    used = set()
    tickets = []
    while len(tickets) < MAX_TICKETS_PER_BAND:
        chosen = []
        product = 1.0
        joint = 1.0
        for leg in ordered:
            if leg['fixture_id'] in used:
                continue
            odds = float(leg['decimal_odds'])
            if product * odds > SURE_COMBINED_MAX:
                continue
            if not chosen:
                partners = [
                    float(other['decimal_odds']) for other in ordered
                    if other['fixture_id'] not in used and other is not leg
                ]
                if not partners or odds * min(partners) > SURE_COMBINED_MAX:
                    continue
            chosen.append(leg)
            product *= odds
            joint *= float(leg['probability'])
        if len(chosen) < 2 or product < SURE_COMBINED_MIN:
            break
        for leg in chosen:
            used.add(leg['fixture_id'])
        ticket = _ready_ticket(day, band, chosen, product, joint)
        body = '|'.join(sorted(f"{leg['fixture_id']}|{leg['market']}" for leg in chosen))
        ticket['signature'] = f"sure-odds|{day['name']}|2-2.95|{body}"
        tickets.append(ticket)
    return tickets


def _slip_signature(day_name, band, legs):
    body = '|'.join(sorted(f"{leg['fixture_id']}|{leg['market']}" for leg in legs))
    return f'high-odds|{day_name}|{band}|{body}'


def _signature_body(signature):
    pieces = str(signature or '').split('|', 3)
    return pieces[3] if len(pieces) == 4 else ''


def _short_reason(band, pool):
    count = band['count']
    available = len(_span(pool))
    if band.get('minimum'):
        needed = f'{band["minimum"]} to {count} are required.'
    elif band.get('at_most'):
        needed = 'At least 2 are required.'
        ordered = _span(pool)[:count]
        target = band.get('minimum_product')
        if target and len(ordered) >= 2:
            product = 1.0
            for leg in ordered:
                product *= float(leg['decimal_odds'])
            if product <= target:
                needed = (
                    f'Those {len(ordered)} combine to {product:,.2f}. '
                    f'Combined odds above {target:,.0f} are required, with {count} selections at most.'
                )
    else:
        needed = f'{count} are required.'
    return f'{available} stored selections are at least 80% and priced {band["odds_label"]}. {needed}'


def _short_label(band):
    if band.get('minimum'):
        return f'{band["minimum"]}–{band["count"]} selections'
    if band.get('at_most'):
        return f'Up to {band["count"]} selections'
    return f'{band["count"]} selections'


def _priced(odds):
    return any(_in_band(odds, band['id']) for band in ODDS_BANDS)


def build_day(candidates, day):
    eligible = [
        row for row in candidates
        if float(row['probability']) >= SELECTION_FLOOR
        and row.get('sport') in BOOKABLE_SPORTS
        and _priced(float(row['decimal_odds']))
    ]
    used = {}
    made = {band['id']: [] for band in ODDS_BANDS}
    for band in sorted(ODDS_BANDS, key=lambda item: 0 if item['id'] == 'above-5' else 1):
        for _slot in range(MAX_TICKETS_PER_BAND):
            pool = choose_one_per_fixture([
                row for row in eligible
                if _in_band(float(row['decimal_odds']), band['id']) and _selection_available(row, used)
            ])
            built = build_slip(
                pool, band['count'],
                at_most=bool(band.get('at_most')),
                minimum=band.get('minimum'),
                minimum_product=band.get('minimum_product'),
            )
            if not built:
                if not made[band['id']]:
                    made[band['id']].append(_short_ticket(band, pool))
                break
            chosen, product, joint = built
            for leg in chosen:
                used[leg['fixture_id']] = leg['market']
            made[band['id']].append(_ready_ticket(day, band, chosen, product, joint))
    tickets = []
    for band in ODDS_BANDS:
        tickets.extend(made[band['id']])
    return {
        'day': day['name'],
        'date': day['start'].astimezone(WAT).date().isoformat(),
        'tickets': tickets,
    }


def _short_ticket(band, pool):
    return {
        'target': band['id'],
        'band': band['id'],
        'odds_band': band['odds_label'],
        'label': _short_label(band),
        'status': 'short',
        'reason': _short_reason(band, pool),
        'selections': [],
        'selection_count': 0,
        'combined_odds': None,
        'joint_probability': None,
        'confidence': None,
        'preferred_only': False,
        'signature': None,
        'booking_code': None,
        'booking_note': None,
    }


def _ready_ticket(day, band, chosen, product, joint):
    confidences = [leg['confidence'] for leg in chosen]
    confidence = None if any(item is None for item in confidences) else min(confidences)
    return {
        'target': band['id'],
        'band': band['id'],
        'odds_band': band['odds_label'],
        'label': f'{len(chosen)} selections',
        'status': 'ready',
        'reason': '',
        'signature': _slip_signature(day['name'], band['id'], chosen),
        'selection_count': len(chosen),
        'combined_odds': round(product, 2),
        'joint_probability': round(joint * 100, 1),
        'confidence': None if confidence is None else round(confidence * 100, 1),
        'preferred_only': all(leg['preferred'] for leg in chosen),
        'booking_code': None,
        'booking_note': 'Waiting for SportyBet.',
        'legs': chosen,
        'selections': [
            {
                'fixture': f"{leg['home']} vs {leg['away']}",
                'sport': str(leg['sport']).replace('_', ' ').title(),
                'competition': leg.get('competition') or '',
                'market': _label(leg['market']),
                'odds': round(float(leg['decimal_odds']), 2),
                'probability': round(float(leg['probability']) * 100, 1),
                'confidence': None if leg['confidence'] is None else round(leg['confidence'] * 100, 1),
                'preferred': leg['preferred'],
                'starts_at': leg['starts_at'],
            }
            for leg in sorted(chosen, key=lambda item: item['starts_at'] or '')
        ],
    }


def _chunked(connection, sql, ids, extra=()):
    rows = []
    for offset in range(0, len(ids), 200):
        batch = ids[offset:offset + 200]
        marks = ','.join('?' for _ in batch)
        rows.extend(connection.execute(sql.format(marks=marks), (*extra, *batch)).fetchall())
    return rows


def _rows(connection, start, end):
    fixtures = {
        row['id']: row
        for row in connection.execute(
            """SELECT id, sport, home, away, competition, starts_at
               FROM sports_ai_fixtures
               WHERE status IN ('scheduled', 'not_started')
                 AND starts_at>=? AND starts_at<?""",
            (start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()),
        )
    }
    if not fixtures:
        return []
    predictions = _chunked(
        connection,
        """SELECT id, fixture_id, market, probability, created_at, model_id
           FROM sports_ai_engine_predictions
           WHERE probability>=? AND fixture_id IN ({marks})""",
        list(fixtures),
        (SELECTION_FLOOR,),
    )
    latest_prediction = {}
    for row in predictions:
        try:
            probability = float(row['probability'])
        except (TypeError, ValueError):
            continue
        if probability != probability or probability < SELECTION_FLOOR:
            continue
        key = (row['fixture_id'], row['market'])
        if key in latest_prediction and str(row['created_at'] or '') <= latest_prediction[key]['created_at']:
            continue
        latest_prediction[key] = {
            'id': row['id'],
            'created_at': str(row['created_at'] or ''),
            'fixture_id': row['fixture_id'],
            'market': row['market'],
            'probability': probability,
            'model_id': row['model_id'],
        }
    if not latest_prediction:
        return []
    opportunities = _chunked(
        connection,
        """SELECT id, prediction_id, odds_id, evaluated_at
           FROM sports_ai_value_opportunities
           WHERE odds_id IS NOT NULL AND prediction_id IN ({marks})""",
        [row['id'] for row in latest_prediction.values()],
    )
    linked = {}
    for row in opportunities:
        current = linked.get(row['prediction_id'])
        if current is None or str(row['evaluated_at'] or '') >= current[0]:
            linked[row['prediction_id']] = (str(row['evaluated_at'] or ''), row['odds_id'], row['id'])
    if not linked:
        return []
    prices = {
        row['id']: row
        for row in _chunked(
            connection,
            """SELECT id, decimal_odds, observed_at, bookmaker, outcome, line
               FROM sports_ai_odds_snapshots
               WHERE id IN ({marks})""",
            [item[1] for item in linked.values()],
        )
        if row['bookmaker'] == 'SportyBet'
    }
    models = {
        row['id']: confidence_from_metrics(row['metrics_json'])
        for row in connection.execute('SELECT id, metrics_json FROM sports_ai_model_registry')
    }
    stored = []
    for item in latest_prediction.values():
        link = linked.get(item['id'])
        if not link or link[1] not in prices:
            continue
        quote = prices[link[1]]
        try:
            odds = float(quote['decimal_odds'])
        except (TypeError, ValueError):
            continue
        if odds != odds or odds <= 1 or not quote['observed_at']:
            continue
        if odds > LOW_ODDS_CEILING and odds < MIN_LEG_ODDS:
            continue
        fixture = fixtures[item['fixture_id']]
        if fixture['sport'] not in BOOKABLE_SPORTS or not fixture['starts_at']:
            continue
        outcome = quote['outcome'] if 'outcome' in quote.keys() else None
        stored.append({
            'stamp': (item['created_at'], str(quote['observed_at'] or '')),
            'fixture_id': item['fixture_id'],
            'market': item['market'],
            'probability': item['probability'],
            'decimal_odds': odds,
            'confidence': models.get(item['model_id']),
            'sport': fixture['sport'],
            'home': fixture['home'],
            'away': fixture['away'],
            'competition': fixture['competition'] or 'League not stored',
            'starts_at': fixture['starts_at'],
            'opportunity_id': link[2],
            'selection': _selection_name(item['market'], outcome, fixture['home'], fixture['away']),
            'bookmaker': 'SportyBet',
            'odds_observed_at': str(quote['observed_at'] or ''),
            'quote_line': _line(quote['line'] if 'line' in quote.keys() else None),
            'model_id': item['model_id'] or '',
            'edge': item['probability'] * odds - 1,
        })
    held = _risk_review(connection, stored)
    from market_registry import holdout_ready
    ready = {}
    return [
        row for row in stored
        if (row['fixture_id'], str(row['market']).upper()) not in held
        and ready.setdefault((row['sport'], str(row['market']).upper()), holdout_ready(connection, row['sport'], row['market']))
    ]


def _candidates(connection, day, now):
    if day['end'] <= now.astimezone(WAT):
        return []
    cutoff = now.astimezone(timezone.utc).isoformat()
    return [row for row in _rows(connection, day['start'], day['end']) if str(row['starts_at']) > cutoff]


def _booking_index(connection):
    found = {}
    rows = connection.execute(
        """SELECT t.id, t.signature,
                  (SELECT b.code FROM sports_ai_booking_codes b
                   WHERE b.accumulator_ticket_id=t.id AND b.verification_method='displayed'
                     AND b.verified_at IS NOT NULL AND TRIM(COALESCE(b.code,''))<>''
                   ORDER BY b.verified_at DESC LIMIT 1) AS code,
                  (SELECT j.status FROM sports_ai_booking_jobs j
                   WHERE j.signature LIKE t.id || '|%'
                   ORDER BY j.created_at DESC LIMIT 1) AS job_status,
                  (SELECT j.id FROM sports_ai_booking_jobs j
                   WHERE j.signature LIKE t.id || '|%'
                   ORDER BY j.created_at DESC LIMIT 1) AS job_id,
                  (SELECT j.updated_at FROM sports_ai_booking_jobs j
                   WHERE j.signature LIKE t.id || '|%'
                   ORDER BY j.created_at DESC LIMIT 1) AS job_updated
           FROM sports_ai_accumulator_tickets t
           WHERE json_extract(t.parameters_json, '$.product') IN ('high_odds', 'sure_odds')
             AND t.status!='expired'""",
    )
    for row in rows:
        item = {
            'id': row['id'], 'code': row['code'], 'job_status': row['job_status'],
            'job_id': row['job_id'], 'job_updated': row['job_updated'],
        }
        found[row['signature']] = item
        found['body:' + _signature_body(row['signature'])] = item
    return found


def _attach_booking(connection, days):
    index = _booking_index(connection)
    for day in days:
        for ticket in day['tickets']:
            match = index.get(ticket.get('signature')) or index.get('body:' + _signature_body(ticket.get('signature')))
            if not match:
                continue
            ticket['ticket_id'] = match['id']
            if match['code']:
                ticket['booking_code'] = str(match['code']).strip()
                ticket['booking_note'] = None
            elif match['job_status'] in ('QUEUED', 'PREPARING'):
                ticket['booking_note'] = 'SportyBet is opening this ticket.'
            elif match['job_status'] in ('FAILED', 'ODDS_CHANGED'):
                ticket['booking_note'] = 'The stored line was not on the page. The next request uses a line SportyBet has priced.'
            else:
                ticket['booking_note'] = 'Waiting for SportyBet.'


def _coded_payload(connection, day, band_id):
    """Show the ticket SportyBet already coded, with the selections that were on the page."""
    row = connection.execute(
        """SELECT t.id, t.signature FROM sports_ai_accumulator_tickets t
           WHERE json_extract(t.parameters_json, '$.product')='high_odds'
             AND json_extract(t.parameters_json, '$.day')=?
             AND json_extract(t.parameters_json, '$.band')=?
             AND t.status!='expired'
             AND EXISTS (
               SELECT 1 FROM sports_ai_booking_codes b
               WHERE b.accumulator_ticket_id=t.id AND b.verification_method='displayed'
                 AND TRIM(COALESCE(b.code, ''))<>''
             )
           ORDER BY t.created_at DESC LIMIT 1""",
        (day.get('name') or day.get('day'), band_id),
    ).fetchone()
    if not row:
        return None
    code = connection.execute(
        """SELECT code FROM sports_ai_booking_codes
           WHERE accumulator_ticket_id=? AND verification_method='displayed'
             AND TRIM(COALESCE(code, ''))<>''
           ORDER BY verified_at DESC LIMIT 1""",
        (row['id'],),
    ).fetchone()
    legs = connection.execute(
        'SELECT * FROM sports_ai_accumulator_legs WHERE ticket_id=? ORDER BY starts_at',
        (row['id'],),
    ).fetchall()
    if not legs or not code:
        return None
    band = next(item for item in ODDS_BANDS if item['id'] == band_id)
    chosen = []
    product = 1.0
    joint = 1.0
    for leg in legs:
        confidence = leg['reliability']
        chosen.append({
            'home': leg['home'], 'away': leg['away'], 'sport': leg['sport'],
            'competition': leg['competition'], 'market': leg['market'],
            'decimal_odds': leg['decimal_odds'], 'probability': leg['probability'],
            'confidence': confidence, 'preferred': False, 'starts_at': leg['starts_at'],
            'fixture_id': leg['fixture_id'],
        })
        product *= float(leg['decimal_odds'])
        joint *= float(leg['probability'])
    payload = _ready_ticket(day, band, chosen, product, joint)
    payload['signature'] = row['signature']
    payload['booking_code'] = str(code['code']).strip()
    payload['booking_note'] = None
    return payload


def _prefer_coded_ticket(connection, day, built):
    for index, ticket in enumerate(list(built['tickets'])):
        if ticket.get('status') != 'ready':
            continue
        coded = _coded_payload(connection, day, ticket.get('band'))
        if coded:
            built['tickets'][index] = coded


def high_odds_board(connection, now):
    days = []
    sure = []
    today = now.astimezone(WAT).date().isoformat()
    for day in _day_window(now):
        candidates = _candidates(connection, day, now)
        built = build_day(candidates, day)
        _prefer_coded_ticket(connection, day, built)
        days.append(built)
        if built['date'] == today:
            sure.extend(pack_sure_tickets(candidates, day))
    _attach_booking(connection, days)
    _attach_booking(connection, [{'tickets': sure}])
    for day in days:
        for ticket in day['tickets']:
            ticket.pop('legs', None)
    for ticket in sure:
        ticket.pop('legs', None)
    return {
        'places_wager': False,
        'selection_floor': 80,
        'minimum_odds': MIN_LEG_ODDS,
        'combined_target': COMBINED_TARGET,
        'bands': [band['odds_label'] for band in ODDS_BANDS],
        'preferred_probability': 85,
        'preferred_confidence': 80,
        'note': (
            'Every selection is at least 80%. A ticket has 15 to 30 selections when each price is 1.95 or below, '
            '12 when each price is 2.00–2.99, 10 when each price is 3.00–3.99, and 8 when each price is 4.00–5.00. '
            'Prices above 5.00 use 8 selections or fewer, enough for combined odds above 10,000. '
            'A selection is used on one ticket. A selection at 100% may be used again. '
            'A booking code is added only when SportyBet displays it.'
        ),
        'days': days,
        'sure_tickets': sure,
    }


def _upsert_ticket(connection, day, ticket, now, product='high_odds'):
    existing = connection.execute(
        """SELECT id, signature FROM sports_ai_accumulator_tickets
           WHERE json_extract(parameters_json, '$.product')=?
             AND status!='expired'""",
        (product,),
    ).fetchall()
    body = _signature_body(ticket['signature'])
    for row in existing:
        if row['signature'] == ticket['signature'] or _signature_body(row['signature']) == body:
            return row['id']
    sports = {leg['sport'] for leg in ticket['legs']}
    sport_mode = next(iter(sports)) if len(sports) == 1 else 'mixed'
    ticket_id = str(uuid.uuid4())
    moment = now.astimezone(timezone.utc).isoformat()
    day_name = day.get('name') or day.get('day')
    parameters = {
        'product': product,
        'day': day_name,
        'date': day['date'],
        'band': ticket['band'],
        'leg_count': len(ticket['legs']),
        'odds_band': ticket['odds_band'],
        'places_wager': False,
    }
    connection.execute(
        """INSERT INTO sports_ai_accumulator_tickets
           (id,run_id,family_id,version,signature,tier,sport_mode,status,combined_odds,joint_probability,
            estimated_edge,leg_count,probability_basis,uncertainty,explanation,parameters_json,score,finalized,created_at)
           VALUES(?,NULL,?,1,?,?,?,'unverified_probability',?,?,?,?,'independent',NULL,?,?,NULL,0,?)""",
        (
            ticket_id, ticket_id, ticket['signature'], len(ticket['legs']), sport_mode,
            ticket['combined_odds'], (ticket['joint_probability'] or 0) / 100,
            None, len(ticket['legs']),
            (
                'Sure-odds ticket. Each selection is at least 80%. Combined odds are from 2.00 to 2.95. No wager is placed.'
                if product == 'sure_odds' else
                f'High-odds ticket. Each selection is at least 80% and priced {ticket["odds_band"]}. No wager is placed.'
            ),
            json.dumps(parameters), moment,
        ),
    )
    for leg in ticket['legs']:
        connection.execute(
            """INSERT INTO sports_ai_accumulator_legs
               (id,ticket_id,opportunity_id,fixture_id,sport,competition,home,away,starts_at,market,selection,
                decimal_odds,probability,edge,bookmaker,odds_observed_at,quote_market,quote_outcome,quote_line,
                model_id,model_version,data_completeness,reliability,settlement_kind,qualification_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()), ticket_id, leg['opportunity_id'], leg['fixture_id'], leg['sport'],
                leg['competition'], leg['home'], leg['away'], leg['starts_at'], leg['market'], leg['selection'],
                leg['decimal_odds'], leg['probability'], leg.get('edge'), 'SportyBet', leg['odds_observed_at'],
                leg['market'], leg['selection'], leg.get('quote_line'), leg.get('model_id') or '', '',
                None, leg.get('confidence'), 'binary',
                json.dumps({'decision': 'high_odds', 'preferred': bool(leg.get('preferred'))}),
            ),
        )
    return ticket_id


def _retire_replaced(connection, day_name, band, signatures, product='high_odds'):
    """Drop an older ticket in this band when its selections are no longer the ones being kept."""
    rows = connection.execute(
        """SELECT id, signature FROM sports_ai_accumulator_tickets
           WHERE json_extract(parameters_json, '$.product')=?
             AND json_extract(parameters_json, '$.day')=?
             AND json_extract(parameters_json, '$.band')=?
             AND status='unverified_probability'
             AND NOT EXISTS (
               SELECT 1 FROM sports_ai_booking_codes b
               WHERE b.accumulator_ticket_id=sports_ai_accumulator_tickets.id
                 AND b.verification_method='displayed' AND TRIM(COALESCE(b.code,''))<>''
             )""",
        (product, day_name, band),
    ).fetchall()
    for row in rows:
        if row['signature'] in signatures:
            continue
        connection.execute(
            "UPDATE sports_ai_accumulator_tickets SET status='expired' WHERE id=?",
            (row['id'],),
        )


def _quote_arrived_since(connection, match, now):
    """A line mismatch is sent again after 15 minutes, or as soon as a newer price is stored."""
    raw = match.get('job_updated')
    if not raw:
        return True
    try:
        updated = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except ValueError:
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    if now - updated >= timedelta(minutes=15):
        return True
    row = connection.execute(
        """SELECT 1 FROM sports_ai_odds_snapshots
           WHERE bookmaker='SportyBet' AND observed_at>? LIMIT 1""",
        (updated.isoformat(),),
    ).fetchone()
    return row is not None


def prepare_high_odds_booking(now=None, bands=None):
    """Store the band tickets and return the next code request.

    A line mismatch is requested again once a newer SportyBet price is stored,
    or after 15 minutes. A code is kept only when SportyBet displays it. No wager is placed.
    """
    from db import connect
    from sportybet_booking import create_high_odds_job

    allowed = None if bands is None else set(bands)
    moment = now or datetime.now(timezone.utc)
    with connect() as connection:
        built_days = []
        for day in _day_window(moment):
            built = build_day(_candidates(connection, day, moment), day)
            built_days.append(built)
            kept = {}
            for ticket in built['tickets']:
                if ticket['status'] != 'ready':
                    continue
                if allowed is not None and ticket['band'] not in allowed:
                    continue
                _upsert_ticket(connection, built, ticket, moment)
                kept.setdefault(ticket['band'], set()).add(ticket['signature'])
            for band, signatures in kept.items():
                _retire_replaced(connection, built.get('day') or built.get('name'), band, signatures)
        index = _booking_index(connection)
        for built in built_days:
            pending = sorted(built['tickets'], key=lambda ticket: 0 if ticket.get('band') == 'above-5' else 1)
            for ticket in pending:
                if ticket['status'] != 'ready':
                    continue
                if allowed is not None and ticket.get('band') not in allowed:
                    continue
                match = index.get(ticket['signature']) or index.get('body:' + _signature_body(ticket['signature']))
                if not match or match['code'] or match['job_status'] in ('PREPARING', 'CODE_GENERATED'):
                    continue
                if match['job_status'] == 'QUEUED' and match['job_id']:
                    return match['job_id']
                if (
                    match['job_status'] in ('FAILED', 'ODDS_CHANGED')
                    and ticket.get('band') != 'to-1.95'
                    and not _quote_arrived_since(connection, match, moment)
                ):
                    continue
                try:
                    created = create_high_odds_job(connection, match['id'])
                except ValueError:
                    continue
                if not created.get('duplicate'):
                    return created['id']
    return None


def prepare_sure_odds_booking(now=None):
    """Store today's 2.00–2.95 tickets and return the next code request.

    The worker selects the legs. A code is kept only when SportyBet displays it.
    No wager is placed.
    """
    from db import connect
    from sportybet_booking import create_sure_odds_job

    moment = now or datetime.now(timezone.utc)
    today = moment.astimezone(WAT).date().isoformat()
    with connect() as connection:
        tickets = []
        for day in _day_window(moment):
            dated = dict(day)
            dated['date'] = day['start'].astimezone(WAT).date().isoformat()
            if dated['date'] != today:
                continue
            tickets = pack_sure_tickets(_candidates(connection, day, moment), dated)
            signatures = {ticket['signature'] for ticket in tickets}
            for ticket in tickets:
                _upsert_ticket(connection, dated, ticket, moment, product='sure_odds')
            if signatures:
                _retire_replaced(connection, dated.get('name'), '2-2.95', signatures, product='sure_odds')
            break
        if not tickets:
            return None
        index = _booking_index(connection)
        for ticket in tickets:
            match = index.get(ticket['signature']) or index.get('body:' + _signature_body(ticket['signature']))
            if not match or match['code'] or match['job_status'] in ('PREPARING', 'CODE_GENERATED'):
                continue
            if match['job_status'] == 'QUEUED' and match['job_id']:
                return match['job_id']
            if match['job_status'] in ('FAILED', 'ODDS_CHANGED') and not _quote_arrived_since(connection, match, moment):
                continue
            created = create_sure_odds_job(connection, match['id'])
            if not created.get('duplicate'):
                return created['id']
    return None
