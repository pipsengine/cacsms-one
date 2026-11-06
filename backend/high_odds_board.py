"""Alternate high-odds tickets for the overview card.

Each selection must be at least 30%. The builder uses a selection at 85%
probability and 80% model confidence before any weaker selection. The joint
figure is the product of those stored probabilities. No price or code is invented.
"""

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
            row['probability'],
            row['confidence'] if row['confidence'] is not None else -1,
        )
        current = chosen.get(row['fixture_id'])
        if current is None or rank > current['rank']:
            row['rank'] = rank
            chosen[row['fixture_id']] = row
    return list(chosen.values())


def _pack(pool, target, longest_first):
    ordered = sorted(
        pool,
        key=lambda item: (
            0 if item['preferred'] else 1,
            -item['decimal_odds'] if longest_first else item['decimal_odds'],
            -item['probability'],
        ),
    )
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


def build_band(candidates, target):
    """Reach the odds target with at least two selections. Preferred selections are used first."""
    for longest_first in (False, True):
        built = _pack(candidates, target, longest_first)
        if built:
            return built
    return None


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
    return {
        'day': day['name'],
        'date': day['start'].astimezone(WAT).date().isoformat(),
        'tickets': tickets,
    }


def _rows(connection, start, end):
    stored = connection.execute(
        """SELECT p.fixture_id, p.market, p.probability, p.created_at,
                  f.sport, f.home, f.away, f.competition, f.starts_at,
                  m.metrics_json, q.decimal_odds, q.observed_at
           FROM sports_ai_fixtures f
           JOIN sports_ai_engine_predictions p ON p.fixture_id=f.id
           JOIN sports_ai_model_registry m ON m.id=p.model_id
           JOIN sports_ai_value_opportunities o ON o.prediction_id=p.id
           JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
           WHERE f.status IN ('scheduled', 'not_started')
             AND f.starts_at>=? AND f.starts_at<?
             AND p.probability>=?
             AND q.bookmaker='SportyBet' AND q.decimal_odds>1""",
        (start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat(), SELECTION_FLOOR),
    ).fetchall()
    latest = {}
    for row in stored:
        try:
            probability = float(row['probability'])
            odds = float(row['decimal_odds'])
        except (TypeError, ValueError):
            continue
        if probability != probability or odds != odds or probability < SELECTION_FLOOR or odds <= 1:
            continue
        key = (row['fixture_id'], row['market'])
        current = latest.get(key)
        stamp = (str(row['created_at'] or ''), str(row['observed_at'] or ''))
        if current and stamp <= current['stamp']:
            continue
        latest[key] = {
            'stamp': stamp,
            'fixture_id': row['fixture_id'],
            'market': row['market'],
            'probability': probability,
            'decimal_odds': odds,
            'confidence': confidence_from_metrics(row['metrics_json']),
            'sport': row['sport'],
            'home': row['home'],
            'away': row['away'],
            'competition': row['competition'],
            'starts_at': row['starts_at'],
        }
    return choose_one_per_fixture(latest.values())


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
            'and 80% model confidence before any other selection. The joint probability is the product '
            'of the stored selections. A booking code is added only when SportyBet displays it.'
        ),
        'days': days,
    }
