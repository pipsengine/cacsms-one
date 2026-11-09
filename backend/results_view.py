"""Match Results (Autonomous): settled picks with server-side performance figures.

A row is a pick the system made before kickoff and settled against an official final
result, once per fixture and market. Picks come from, in order of preference:
  1. qualified    a selection the AI qualified (qualification history, before kickoff)
  2. ticket       a leg placed on a booking-code ticket
  3. prediction   the most likely outcome of the match-winner market (1X2 / moneyline)
Accuracy, Brier score and log loss use the stored pre-kickoff probability. Returns
are per 1-unit stake and use only picks with a stored SportyBet price. Nothing is
estimated or invented: a pick without a probability or price is left out of the
figure that needs it.
"""
import json
import math
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

WINNER = {'HOME', 'DRAW', 'AWAY', 'MONEYLINE_HOME', 'MONEYLINE_AWAY'}
PRIORITY = {'qualified': 0, 'ticket': 1, 'prediction': 2}
_CACHE = {'at': 0.0, 'rows': None}
CACHE_SECONDS = 60


def market_group(market):
    m = str(market or '').upper()
    if m in ('HOME', 'DRAW', 'AWAY'):
        return '1X2'
    if m.startswith('MONEYLINE'):
        return 'Match Winner'
    if m.startswith('DOUBLE_CHANCE'):
        return 'Double Chance'
    if m.startswith('BTTS'):
        return 'Both Teams To Score'
    if 'SPREAD' in m or 'HANDICAP' in m:
        return 'Handicap'
    if m.startswith(('OVER', 'UNDER', 'HOME_OVER', 'HOME_UNDER', 'AWAY_OVER', 'AWAY_UNDER')):
        return 'Totals'
    if m.startswith('DNB'):
        return 'Draw No Bet'
    return m.replace('_', ' ').title() or 'Other'


def selection_label(market, home, away):
    m = str(market or '').upper()
    named = {'HOME': f'{home} (Home)', 'AWAY': f'{away} (Away)', 'DRAW': 'Draw',
             'MONEYLINE_HOME': home, 'MONEYLINE_AWAY': away, 'BTTS_YES': 'Both score: Yes', 'BTTS_NO': 'Both score: No'}
    if m in named:
        return named[m]
    return m.replace('_', ' ').title()


def _float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _collect(connection):
    """Every settled pick, deduplicated per fixture and market."""
    from accumulator_engine import settle_result
    picks = {}

    def offer(key, item):
        current = picks.get(key)
        if current is None or PRIORITY[item['kind']] < PRIORITY[current['kind']]:
            picks[key] = item

    base = """f.id fixture_id,f.sport,f.competition,f.home,f.away,f.starts_at,r.home_score,r.away_score,r.settled_at,r.source result_source"""
    # 1. qualified picks: last QUALIFIED record before kickoff
    for row in connection.execute(
        f"""SELECT h.market,h.evidence_json,h.created_at decided_at,p.model_id,{base}
            FROM sports_ai_qualification_history h
            JOIN sports_ai_fixtures f ON f.id=h.fixture_id
            JOIN sports_ai_results r ON r.fixture_id=f.id AND r.status='final'
            LEFT JOIN sports_ai_engine_predictions p ON p.id=h.prediction_id
            WHERE h.state='QUALIFIED' AND h.created_at<f.starts_at
            ORDER BY h.created_at""",
    ):
        evidence = json.loads(row['evidence_json'] or '{}')
        implied = _float(evidence.get('implied_probability'))
        offer((row['fixture_id'], row['market']), {
            **dict(row), 'kind': 'qualified',
            'probability': _float(evidence.get('anchored_probability') or evidence.get('probability')),
            'odds': round(1 / implied, 2) if implied else None,
        })
    # 2. ticket legs
    for row in connection.execute(
        f"""SELECT l.market,l.probability,l.decimal_odds odds,l.model_id,t.created_at decided_at,{base}
            FROM sports_ai_accumulator_legs l
            JOIN sports_ai_accumulator_tickets t ON t.id=l.ticket_id
            JOIN sports_ai_fixtures f ON f.id=l.fixture_id
            JOIN sports_ai_results r ON r.fixture_id=f.id AND r.status='final'
            WHERE t.created_at<f.starts_at AND COALESCE(json_extract(t.parameters_json,'$.tier_mode'),'standard')!='simulation'""",
    ):
        offer((row['fixture_id'], row['market']), {**dict(row), 'kind': 'ticket',
                                                    'probability': _float(row['probability']), 'odds': _float(row['odds'])})
    # 3. match-winner predictions: most likely outcome of the latest pre-kickoff prediction
    latest = {}
    for row in connection.execute(
        f"""SELECT p.id prediction_id,p.market,p.probability,p.model_id,p.created_at decided_at,{base},
                   (SELECT q.decimal_odds FROM sports_ai_value_opportunities v JOIN sports_ai_odds_snapshots q ON q.id=v.odds_id
                     WHERE v.prediction_id=p.id AND q.bookmaker='SportyBet' ORDER BY q.observed_at DESC LIMIT 1) odds
            FROM sports_ai_engine_predictions p
            JOIN sports_ai_fixtures f ON f.id=p.fixture_id
            JOIN sports_ai_results r ON r.fixture_id=f.id AND r.status='final'
            WHERE p.created_at<f.starts_at AND (p.market IN ('HOME','DRAW','AWAY') OR p.market LIKE 'MONEYLINE_%')""",
    ):
        key = (row['fixture_id'], row['market'])
        if key not in latest or str(latest[key]['decided_at']) < str(row['decided_at']):
            latest[key] = dict(row)
    by_fixture = defaultdict(list)
    for item in latest.values():
        by_fixture[item['fixture_id']].append(item)
    for items in by_fixture.values():
        best = max(items, key=lambda item: _float(item['probability']) or 0)
        offer((best['fixture_id'], best['market']), {**best, 'kind': 'prediction',
                                                      'probability': _float(best['probability']), 'odds': _float(best['odds'])})
    models = {r['id']: (r['model_name'], r['version']) for r in connection.execute(
        'SELECT id,model_name,version FROM sports_ai_model_registry')}
    rows = []
    for (fixture_id, market), item in picks.items():
        outcome = settle_result(item['sport'], market, item['home_score'], item['away_score'])
        if outcome not in ('won', 'lost', 'void', 'push'):
            continue
        name, version = models.get(item.get('model_id'), (None, None))
        rows.append({
            'id': f'{fixture_id}:{market}', 'fixture_id': fixture_id, 'date': str(item['starts_at'])[:10],
            'starts_at': item['starts_at'], 'sport': item['sport'], 'competition': item['competition'] or '—',
            'fixture': f"{item['home']} vs {item['away']}", 'market_code': market, 'market': market_group(market),
            'prediction': selection_label(market, item['home'], item['away']), 'odds': item.get('odds'),
            'probability': item.get('probability'), 'result': outcome.title(), 'status': 'Settled',
            'score': f"{item['home_score']}–{item['away_score']}", 'kind': item['kind'],
            'model': f'{name} v{version}' if name else '—', 'decided_at': item.get('decided_at'),
            'settled_at': item['settled_at'], 'source': item['result_source'],
        })
    rows.sort(key=lambda row: (str(row['starts_at']), row['id']), reverse=True)
    return rows


def settled_rows(connection, fresh=False):
    if fresh or _CACHE['rows'] is None or time.monotonic() - _CACHE['at'] > CACHE_SECONDS:
        _CACHE.update(rows=_collect(connection), at=time.monotonic())
    return _CACHE['rows']


def metrics(rows):
    decided = [r for r in rows if r['result'] in ('Won', 'Lost')]
    scored = [r for r in decided if r['probability'] is not None and 0 < r['probability'] < 1]
    priced = [r for r in scored if r['odds'] and r['odds'] > 1]
    wins = sum(r['result'] == 'Won' for r in decided)
    out = {
        'settled': len(rows), 'decided': len(decided), 'voids': len(rows) - len(decided),
        'accuracy': wins / len(decided) if decided else None,
        'brier': sum((r['probability'] - (r['result'] == 'Won')) ** 2 for r in scored) / len(scored) if scored else None,
        'log_loss': (sum(-math.log(max(1e-6, r['probability'] if r['result'] == 'Won' else 1 - r['probability'])) for r in scored)
                     / len(scored)) if scored else None,
        'expected_return': sum(r['probability'] * r['odds'] - 1 for r in priced) / len(priced) if priced else None,
        'realized_return': sum((r['odds'] - 1) if r['result'] == 'Won' else -1 for r in priced) / len(priced) if priced else None,
        'scored': len(scored), 'priced': len(priced),
    }
    return out


def _filter(rows, sport=None, market=None, competition=None, model=None, date_from=None, date_to=None):
    return [r for r in rows
            if (not sport or r['sport'] == sport) and (not market or r['market'] == market)
            and (not competition or r['competition'] == competition) and (not model or r['model'] == model)
            and (not date_from or r['date'] >= date_from) and (not date_to or r['date'] <= date_to)]


def _settlement_schedule(connection, now):
    runs = [row[0] for row in connection.execute(
        "SELECT created_at FROM sports_ai_autonomous_cycles WHERE stage='settle' ORDER BY created_at DESC LIMIT 6")]
    stamps = []
    for value in runs:
        try:
            stamps.append(datetime.fromisoformat(value))
        except ValueError:
            continue
    if not stamps:
        return {'last': None, 'next': None, 'active': False}
    gaps = sorted((a - b).total_seconds() for a, b in zip(stamps, stamps[1:]) if a > b)
    interval = gaps[len(gaps) // 2] if gaps else 1800
    upcoming = stamps[0] + timedelta(seconds=interval)
    return {'last': stamps[0].isoformat(), 'next': upcoming.isoformat(), 'interval_seconds': interval,
            'active': (now - stamps[0]).total_seconds() <= max(3 * interval, 3600)}


def results_view(connection, sport=None, market=None, competition=None, model=None,
                 date_from=None, date_to=None, limit=200, now=None):
    current = now or datetime.now(timezone.utc)
    rows = settled_rows(connection)
    chosen = _filter(rows, sport, market, competition, model, date_from, date_to)
    stats = metrics(chosen)
    # Change against the previous window of equal length, when a range is given.
    previous = None
    if date_from and date_to:
        start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
        span = (end - start).days + 1
        prev = _filter(rows, sport, market, competition, model,
                       (start - timedelta(days=span)).isoformat(), (start - timedelta(days=1)).isoformat())
        previous = metrics(prev) if prev else None
    end_day = date.fromisoformat(date_to) if date_to else current.date()
    trend = []
    for offset in range(6, -1, -1):
        day = (end_day - timedelta(days=offset)).isoformat()
        cumulative = metrics([r for r in chosen if r['date'] <= day])
        trend.append({'date': day, 'accuracy': cumulative['accuracy'], 'realized_return': cumulative['realized_return'],
                      'settled': cumulative['settled']})
    missing = connection.execute(
        """SELECT COUNT(DISTINCT f.id) FROM sports_ai_fixtures f
           WHERE julianday(f.starts_at) < julianday(?) - (4.0/24) AND julianday(f.starts_at) > julianday(?) - 30
             AND EXISTS (SELECT 1 FROM sports_ai_engine_predictions p WHERE p.fixture_id=f.id)
             AND NOT EXISTS (SELECT 1 FROM sports_ai_results r WHERE r.fixture_id=f.id)""",
        (current.isoformat(), current.isoformat()),
    ).fetchone()[0]
    activity = sorted(chosen, key=lambda r: str(r['settled_at'] or ''), reverse=True)[:6]
    return {
        'generated_at': current.isoformat(),
        'stats': stats, 'previous': previous, 'trend': trend,
        'rows': chosen[:max(1, min(int(limit), 2000))], 'total': len(chosen),
        'verification': {
            'official_results': len(chosen),
            'predictions_matched': sum(1 for r in chosen if r['probability'] is not None),
            'settlements_completed': stats['decided'],
            'exceptions': stats['voids'] + int(missing),
            'awaiting_result': int(missing),
        },
        'activity': [{'fixture': r['fixture'], 'prediction': r['prediction'], 'result': r['result'], 'odds': r['odds'],
                      'at': r['settled_at'], 'source': r['source']} for r in activity],
        'schedule': _settlement_schedule(connection, current),
        'options': {
            'sports': sorted({r['sport'] for r in rows}), 'markets': sorted({r['market'] for r in rows}),
            'competitions': sorted({r['competition'] for r in rows}), 'models': sorted({r['model'] for r in rows}),
        },
        'places_wager': False,
    }
