"""Alternate high-odds tickets for the overview card.

Each selection must be at least 30%. The builder uses a selection at 85%
probability and 80% model confidence before any weaker selection. The joint
figure is the product of those stored probabilities. No price or code is invented.
"""

import math
from datetime import timedelta, timezone

from booking_schedule import WAT, opportunity_week
from display_policy import confidence_from_metrics

SELECTION_FLOOR = 0.30
PREFERRED_PROBABILITY = 0.85
PREFERRED_CONFIDENCE = 0.80
MAX_LEGS = 25
BANDS = (5, 10, 20, 50, 100, 500, 1000, 5000, 10000)


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
        buckets = [sorted(competitions[name], key=rank) for name in names]
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


def _ordered(pool, longest_first):
    preferred = [item for item in pool if item['preferred']]
    reserve = [item for item in pool if not item['preferred']]
    return _rotate(preferred, longest_first) + _rotate(reserve, longest_first)


def _pack(pool, target, longest_first):
    ordered = _ordered(pool, longest_first)
    chosen = []
    product = 1.0
    joint = 1.0
    for leg in ordered:
        if len(chosen) >= 2 and product >= target:
            break
        if len(chosen) >= MAX_LEGS:
            break
        chosen.append(leg)
        product *= float(leg['decimal_odds'])
        joint *= float(leg['probability'])
    if len(chosen) < 2 or product < target:
        return None
    return chosen, product, joint


def _tighten(chosen, product, joint, target):
    """Drop a repeated sport or league while the combined price still clears the band."""
    chosen = list(chosen)
    while len(chosen) > 2:
        sports = {}
        leagues = {}
        for leg in chosen:
            sports[leg.get('sport') or ''] = sports.get(leg.get('sport') or '', 0) + 1
            key = (leg.get('sport') or '', leg.get('competition') or '')
            leagues[key] = leagues.get(key, 0) + 1
        removable = sorted(chosen, key=lambda leg: (leg['preferred'], leg['decimal_odds']))
        removed = False
        for leg in removable:
            sport = leg.get('sport') or ''
            league = (sport, leg.get('competition') or '')
            if sports[sport] == 1 and len(sports) > 1:
                continue
            if leagues[league] == 1 and len(leagues) > 1:
                continue
            next_product = product / float(leg['decimal_odds'])
            if next_product >= target:
                chosen.remove(leg)
                product = next_product
                joint /= float(leg['probability'])
                removed = True
                break
        if not removed:
            break
    return chosen, product, joint


def build_band(candidates, target):
    """Reach the odds target with at least two selections. Preferred selections are used first."""
    best = None
    for longest_first in (True, False):
        built = _pack(candidates, target, longest_first)
        if not built:
            continue
        chosen, product, joint = _tighten(*built, target)
        score = (
            1 if all(leg['preferred'] for leg in chosen) else 0,
            -sum(1 for leg in chosen if not leg['preferred']),
            len({leg.get('sport') or '' for leg in chosen}),
            -abs(math.log(product / target)),
            len({(leg.get('sport') or '', leg.get('competition') or '') for leg in chosen}),
            -len(chosen),
        )
        if best is None or score > best[0]:
            best = (score, chosen, product, joint)
    if best is None:
        return None
    return best[1], best[2], best[3]


def build_day(candidates, day):
    tickets = []
    for target in BANDS:
        built = build_band(candidates, target)
        if not built:
            tickets.append({
                'target': target,
                'label': f'{target:,}×' if target < 10000 else '10,000× and above',
                'status': 'short',
                'reason': f'Not enough stored selections reach {target:,}× with at least two prices.',
                'selections': [],
                'selection_count': 0,
                'combined_odds': None,
                'joint_probability': None,
                'confidence': None,
                'preferred_only': False,
            })
            continue
        chosen, product, joint = built
        confidences = [leg['confidence'] for leg in chosen]
        confidence = None if any(item is None for item in confidences) else min(confidences)
        tickets.append({
            'target': target,
            'label': f'{target:,}×' if target < 10000 else '10,000× and above',
            'status': 'ready',
            'reason': '',
            'selection_count': len(chosen),
            'combined_odds': round(product, 2),
            'joint_probability': round(joint * 100, 1),
            'confidence': None if confidence is None else round(confidence * 100, 1),
            'preferred_only': all(leg['preferred'] for leg in chosen),
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
        })
    merged = []
    for ticket in tickets:
        previous = merged[-1] if merged else None
        same = previous and previous['status'] == 'ready' and ticket['status'] == 'ready' and [
            (item['fixture'], item['market'], item['odds']) for item in previous['selections']
        ] == [
            (item['fixture'], item['market'], item['odds']) for item in ticket['selections']
        ]
        if same:
            previous['label'] = f"{previous['label'].split(' to ')[0]} to {ticket['label']}"
            continue
        merged.append(ticket)
    return {
        'day': day['name'],
        'date': day['start'].astimezone(WAT).date().isoformat(),
        'tickets': merged,
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
        """SELECT prediction_id, odds_id, evaluated_at
           FROM sports_ai_value_opportunities
           WHERE odds_id IS NOT NULL AND prediction_id IN ({marks})""",
        [row['id'] for row in latest_prediction.values()],
    )
    linked = {}
    for row in opportunities:
        current = linked.get(row['prediction_id'])
        if current is None or str(row['evaluated_at'] or '') >= current[0]:
            linked[row['prediction_id']] = (str(row['evaluated_at'] or ''), row['odds_id'])
    if not linked:
        return []
    prices = {
        row['id']: row
        for row in _chunked(
            connection,
            """SELECT id, decimal_odds, observed_at, bookmaker
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
        if odds != odds or odds <= 1:
            continue
        fixture = fixtures[item['fixture_id']]
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
            'competition': fixture['competition'],
            'starts_at': fixture['starts_at'],
        })
    return choose_one_per_fixture(stored)


def high_odds_board(connection, now):
    days = []
    for day in _day_window(now):
        if day['end'] <= now.astimezone(WAT):
            candidates = []
        else:
            candidates = [row for row in _rows(connection, day['start'], day['end']) if str(row['starts_at']) > now.astimezone(timezone.utc).isoformat()]
        days.append(build_day(candidates, day))
    return {
        'places_wager': False,
        'selection_floor': 30,
        'preferred_probability': 85,
        'preferred_confidence': 80,
        'note': (
            'Each selection is 30% or higher. The builder uses a selection at 85% probability '
            'and 80% model confidence before any other selection, and spreads the selections across '
            'the sports and leagues that have a stored price. The joint probability is the product '
            'of the stored selections. A booking code is added only when SportyBet displays it.'
        ),
        'days': days,
    }
