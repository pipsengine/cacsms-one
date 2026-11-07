"""Central market registry.

A market becomes active only when the stored score model has a large enough
holdout, calibration stays inside the existing ceiling, and SportyBet has
already displayed that selection. Names without those facts stay blocked,
unsupported, or in research. This module does not invent prices or results.
"""

import json
import math
import uuid
from datetime import datetime, timedelta, timezone

from prediction_engine import calibration_metrics, market_probability
from sport_adapters import UNSUPPORTED, probability as unsupported_probability
from sport_support import ANALYSED_SPORTS, GOAL_SPORTS, WINNER_SPORTS, ensure_collected_sports

MIN_SAMPLE = 30
MAX_CALIBRATION_ERROR = 0.20
MIN_QUOTE_FIXTURES = 30

FOOTBALL_DERIVED = (
    ('DOUBLE_CHANCE_1X', '1X', None, 'Home or draw from the score distribution.'),
    ('DOUBLE_CHANCE_12', '12', None, 'Home or away from the score distribution.'),
    ('DOUBLE_CHANCE_X2', 'X2', None, 'Draw or away from the score distribution.'),
    ('DNB_HOME', 'Home, draw no bet', None, 'Home win. A draw is a push.'),
    ('DNB_AWAY', 'Away, draw no bet', None, 'Away win. A draw is a push.'),
    ('BTTS_YES', 'Yes', None, 'Both teams score at least one goal.'),
    ('BTTS_NO', 'No', None, 'At least one team fails to score.'),
)
for _line in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5):
    FOOTBALL_DERIVED += (
        (f'OVER_{_line:g}', f'Over {_line:g}', _line, 'Full-time goals over the line.'),
        (f'UNDER_{_line:g}', f'Under {_line:g}', _line, 'Full-time goals under the line.'),
    )
for _side, _label in (('HOME', 'Home'), ('AWAY', 'Away')):
    for _line in (0.5, 1.5, 2.5, 3.5):
        FOOTBALL_DERIVED += (
            (f'{_side}_OVER_{_line:g}', f'{_label} over {_line:g}', _line, 'Team goals over the line.'),
            (f'{_side}_UNDER_{_line:g}', f'{_label} under {_line:g}', _line, 'Team goals under the line.'),
        )
for _side, _label in (('HOME', 'Home'), ('AWAY', 'Away')):
    for _line in (-2.5, -2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2, 2.5):
        FOOTBALL_DERIVED += (
            (f'{_side}_HANDICAP_{_line:g}', f'{_label} {_line:g}', _line, 'Asian handicap settled from the full-time margin.'),
        )
FOOTBALL_DERIVED += (
    ('ODD_GOALS', 'Odd', None, 'Full-time goals are odd.'),
    ('EVEN_GOALS', 'Even', None, 'Full-time goals are even.'),
    ('CLEAN_SHEET_HOME', 'Home clean sheet', None, 'The away team scores zero.'),
    ('CLEAN_SHEET_AWAY', 'Away clean sheet', None, 'The home team scores zero.'),
    ('MARGIN_HOME_1', 'Home by 1', None, 'Home wins by one goal.'),
    ('MARGIN_HOME_2', 'Home by 2', None, 'Home wins by two goals.'),
    ('MARGIN_HOME_3PLUS', 'Home by 3 or more', None, 'Home wins by three or more goals.'),
    ('MARGIN_AWAY_1', 'Away by 1', None, 'Away wins by one goal.'),
    ('MARGIN_AWAY_2', 'Away by 2', None, 'Away wins by two goals.'),
    ('MARGIN_AWAY_3PLUS', 'Away by 3 or more', None, 'Away wins by three or more goals.'),
    ('EUROPEAN_HOME_0', 'Home 0', 0, 'Three-way handicap with no line.'),
    ('EUROPEAN_DRAW_0', 'Draw 0', 0, 'Three-way handicap with no line.'),
    ('EUROPEAN_AWAY_0', 'Away 0', 0, 'Three-way handicap with no line.'),
    ('EUROPEAN_HOME_-1', 'Home -1', -1, 'Three-way handicap, home gives one goal.'),
    ('EUROPEAN_DRAW_-1', 'Draw -1', -1, 'Three-way handicap, home gives one goal.'),
    ('EUROPEAN_AWAY_-1', 'Away -1', -1, 'Three-way handicap, home gives one goal.'),
)

ICE_SCORE_DERIVED = (
    ('BTTS_YES', 'Yes', None, 'Both teams score in the stored final score.'),
    ('BTTS_NO', 'No', None, 'At least one team fails to score in the stored final score.'),
    ('ODD_GOALS', 'Odd', None, 'Stored final goals are odd.'),
    ('EVEN_GOALS', 'Even', None, 'Stored final goals are even.'),
    ('MARGIN_HOME_1', 'Home by 1', None, 'Home wins the stored final score by one goal.'),
    ('MARGIN_HOME_2', 'Home by 2', None, 'Home wins the stored final score by two goals.'),
    ('MARGIN_HOME_3PLUS', 'Home by 3 or more', None, 'Home wins the stored final score by three or more.'),
    ('MARGIN_AWAY_1', 'Away by 1', None, 'Away wins the stored final score by one goal.'),
    ('MARGIN_AWAY_2', 'Away by 2', None, 'Away wins the stored final score by two goals.'),
    ('MARGIN_AWAY_3PLUS', 'Away by 3 or more', None, 'Away wins the stored final score by three or more.'),
)
for _line in (3.5, 4.5, 5.5, 6.5, 7.5):
    ICE_SCORE_DERIVED += (
        (f'OVER_{_line:g}', f'Over {_line:g}', _line, 'Stored final goals over the line. A regulation total is a different market.'),
        (f'UNDER_{_line:g}', f'Under {_line:g}', _line, 'Stored final goals under the line. A regulation total is a different market.'),
    )
for _side, _label in (('HOME', 'Home'), ('AWAY', 'Away')):
    for _line in (1.5, 2.5, 3.5):
        ICE_SCORE_DERIVED += (
            (f'{_side}_OVER_{_line:g}', f'{_label} over {_line:g}', _line, 'Team goals from the stored final score.'),
            (f'{_side}_UNDER_{_line:g}', f'{_label} under {_line:g}', _line, 'Team goals from the stored final score.'),
        )
    for _line in (-2.5, -1.5, 1.5, 2.5):
        ICE_SCORE_DERIVED += (
            (f'{_side}_HANDICAP_{_line:g}', f'{_label} puck line {_line:g}', _line, 'Puck line on the stored final-score margin.'),
        )

BLOCKED = (
    ('football', 'FIRST_HALF_1X2', 'First-half result', '1h', 'Requires a stored half-time score. Results store the full-time score only.'),
    ('football', 'FIRST_HALF_OVER_0.5', 'First-half over 0.5', '1h', 'Requires stored first-half goals.'),
    ('football', 'HTFT', 'Half-time / full-time', 'htft', 'Requires a stored half-time score and the full-time score.'),
    ('football', 'CORNERS', 'Corners', 'ft', 'No corner count is stored on results or in the score model.'),
    ('football', 'CARDS', 'Cards', 'ft', 'No card count is stored on results or in the score model.'),
    ('basketball', 'FIRST_HALF_TOTAL', 'First-half total', '1h', 'Holdout rows do not carry a usable first-half total.'),
    ('basketball', 'QUARTER_TOTAL', 'Quarter total', 'quarter', 'No quarter score is stored.'),
)


def utc_now():
    return datetime.now(timezone.utc)


def unsupported_market_probability(sport, market, features=None):
    return unsupported_probability(sport, market, features)


def assign_status(sample_size, calibration_error, quote_fixtures, *, blocked=False, unsupported=False, parent_active=True):
    """Apply the existing sample and calibration ceilings. Do not loosen them."""
    if unsupported:
        return 'unsupported'
    if blocked:
        return 'data_pending'
    if not parent_active:
        return 'suspended'
    sample = int(sample_size or 0)
    quotes = int(quote_fixtures or 0)
    if sample < MIN_SAMPLE or calibration_error is None or not math.isfinite(float(calibration_error)):
        return 'research'
    if float(calibration_error) > MAX_CALIBRATION_ERROR:
        return 'research'
    if quotes < MIN_QUOTE_FIXTURES:
        return 'validated'
    return 'active'


def choose_best(rows):
    """One market per fixture. The highest win probability wins.

    Model confidence breaks a tie. A missing probability cannot outrank a stored one.
    """
    grouped = {}
    for row in rows:
        grouped.setdefault(row.get('fixture_id'), []).append(row)
    chosen = []
    for items in grouped.values():
        items.sort(key=lambda item: (
            -_rank_number(item.get('probability')),
            -_rank_number(item.get('confidence')),
            str(item.get('market') or ''),
        ))
        chosen.append(items[0])
    return chosen


def _rank_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return -1.0
    if number != number:
        return -1.0
    return number


def active_keys(connection):
    try:
        rows = connection.execute(
            "SELECT sport, market_key FROM sports_ai_market_registry WHERE status='active'",
        ).fetchall()
    except Exception:
        return set()
    return {(row['sport'], row['market_key']) for row in rows}


def is_active(connection, sport, market):
    return (sport, str(market or '').upper()) in active_keys(connection)


def holdout_passes(evidence):
    """A market line is usable only when its own holdout clears the existing ceilings."""
    if not isinstance(evidence, dict):
        return False
    error = evidence.get('calibration_error')
    sample = evidence.get('sample_size')
    if sample is None:
        sample = evidence.get('holdout_fixtures')
    try:
        sample = int(sample or 0)
        error = float(error)
    except (TypeError, ValueError):
        return False
    return sample >= MIN_SAMPLE and math.isfinite(error) and error <= MAX_CALIBRATION_ERROR


def holdout_ready(connection, sport, market):
    """True when this exact market key has its own passing holdout. A missing line does not."""
    row = connection.execute(
        """SELECT evidence_json FROM sports_ai_market_registry
           WHERE sport=? AND market_key=?""",
        (sport, str(market or '').upper()),
    ).fetchone()
    if not row:
        return False
    try:
        evidence = json.loads(row['evidence_json'] or '{}')
    except json.JSONDecodeError:
        return False
    return holdout_passes(evidence)


def list_registry(connection):
    rows = [
        dict(row) for row in connection.execute(
            """SELECT sport, market_key, selection, period, line_value, settlement_rule,
                      data_requirements, model_source, model_version, status, evidence_json, updated_at
               FROM sports_ai_market_registry
               ORDER BY sport, status, market_key""",
        )
    ]
    for row in rows:
        try:
            row['evidence'] = json.loads(row.pop('evidence_json') or '{}')
        except json.JSONDecodeError:
            row['evidence'] = {}
    counts = {}
    coverage = {}
    for row in rows:
        counts[row['status']] = counts.get(row['status'], 0) + 1
        cell = coverage.setdefault(row['sport'], {'sport': row['sport'], 'quote_fixtures': 0})
        cell[row['status']] = cell.get(row['status'], 0) + 1
        cell['quote_fixtures'] += int((row.get('evidence') or {}).get('quote_fixtures') or 0)
    return {
        'items': rows,
        'counts': counts,
        'coverage': sorted(coverage.values(), key=lambda item: (-int(item.get('active') or 0), item['sport'])),
        'min_sample': MIN_SAMPLE,
        'max_calibration_error': MAX_CALIBRATION_ERROR,
        'min_quote_fixtures': MIN_QUOTE_FIXTURES,
        'places_wager': False,
        'thresholds_changed': False,
    }


def refresh_market_registry(connection, now=None):
    """Score stored holdout rows and stored SportyBet quotes. Does not refit a model."""
    current = now or utc_now()
    quote_counts = _quote_counts(connection)
    records = []
    for sport in ANALYSED_SPORTS:
        model = connection.execute(
            """SELECT id, version, active, metrics_json, training_run_id, parameters_json
               FROM sports_ai_model_registry
               WHERE sport=? AND active=1 AND status='active'
               ORDER BY created_at DESC LIMIT 1""",
            (sport,),
        ).fetchone()
        holdout = _holdout(connection, model) if model else []
        rho = 0.0
        if model:
            try:
                rho = float(json.loads(model['parameters_json'] or '{}').get('dixon_coles_rho') or 0.0)
            except (TypeError, ValueError, json.JSONDecodeError):
                rho = 0.0
        parent_active = bool(model and model['active'])
        version = None if not model else model['version']
        temperature, dependence = 1.0, 1.0
        if model and sport == 'football':
            try:
                stored = json.loads(model['parameters_json'] or '{}')
                temperature = float(stored.get('temperature') or 1.0)
                dependence = float(stored.get('score_dependence') or 1.0)
            except (TypeError, ValueError, json.JSONDecodeError):
                temperature, dependence = 1.0, 1.0
        if sport in GOAL_SPORTS:
            records.extend(_result_rows(sport, model, quote_counts, 'HOME', 'Home win', 'DRAW', 'Draw', 'AWAY', 'Away win'))
            if sport == 'ice_hockey':
                for record in records:
                    if record['sport'] == 'ice_hockey' and record['market_key'] in ('HOME', 'DRAW', 'AWAY'):
                        record['settlement_rule'] = 'Regulation 60-minute result. This is not the winner after overtime or a shootout.'
                        record['evidence']['outcome_definition'] = 'regulation'
                for key, selection, line, rule in ICE_SCORE_DERIVED:
                    records.append(_derived_row(sport, key, selection, 'ft', line, rule, holdout, rho, quote_counts, parent_active, version))
            if sport == 'football':
                for key, selection, line, rule in FOOTBALL_DERIVED:
                    records.append(_derived_row(
                        sport, key, selection, 'ft', line, rule, holdout, rho, quote_counts,
                        parent_active, version, temperature, dependence,
                    ))
                records.append(_correct_score_row(holdout, rho, version))
        else:
            records.extend(_result_rows(
                sport, model, quote_counts, 'MONEYLINE_HOME', 'Home moneyline', 'MONEYLINE_AWAY', 'Away moneyline',
            ))
            team_totals = [
                ('HOME_OVER_99.5', 'Home over 99.5', 99.5),
                ('HOME_UNDER_99.5', 'Home under 99.5', 99.5),
                ('AWAY_OVER_99.5', 'Away over 99.5', 99.5),
                ('AWAY_UNDER_99.5', 'Away under 99.5', 99.5),
            ]
            for key, selection, line in _basketball_lines(quote_counts) + team_totals:
                records.append(_derived_row(
                    sport, key, selection, 'ft', line, 'Full-time points or margin from the normal point model.',
                    holdout, 0.0, quote_counts, parent_active, version,
                ))
        half_ready = sum(1 for features, _home, _away in holdout if features.get('expected_home_h1') is not None and features.get('h1_total_sd'))
        if sport == 'basketball' and half_ready >= MIN_SAMPLE:
            records.append(_static(
                sport, 'FIRST_HALF_TOTAL', 'First-half total', '1h', None,
                'First-half total from stored half scoring rates.',
                'A first-half line and a stored half-time score.',
                'normal_points', version, 'research',
                {'holdout_rows_with_half': half_ready, 'reason': 'Half rates exist on the holdout, and no half-time result is stored to settle them.'},
            ))
        if holdout:
            known_keys = {item['market_key'] for item in records if item['sport'] == sport}
            for key in _extra_keys(sport, quote_counts, known_keys):
                records.append(_derived_row(
                    sport, key, key.replace('_', ' ').title(), 'ft', _line_value(key),
                    'Line discovered from a stored SportyBet quote and settled from the same score or point model.',
                    holdout, rho, quote_counts, parent_active, version, temperature, dependence,
                ))
    for sport, key, selection, period, requirement in BLOCKED:
        if any(item['market_key'] == key and item['sport'] == sport for item in records):
            continue
        records.append(_static(
            sport, key, selection, period, None, 'Cannot be settled from stored data.',
            requirement, 'none', None, 'data_pending', {'quote_fixtures': quote_counts.get((sport, key), 0)},
        ))
    for sport, markets in UNSUPPORTED.items():
        for key in markets:
            records.append(_static(
                sport, key, key.replace('_', ' ').title(), 'ft', None,
                'No settlement is attempted.',
                'A modelled sport, historical scores, and a verified bookmaker mapping.',
                'none', None, 'unsupported',
                {'adapter': 'sport_adapters.probability', 'probability': unsupported_market_probability(sport, key)},
            ))
    from discovery_catalogue import catalogue_rows, ensure_reference_catalogue, ensure_starting_competitions, reference_market_rows
    known = {(item['sport'], item['market_key']) for item in records}
    records.extend(catalogue_rows(known))
    known.update((item['sport'], item['market_key']) for item in records)
    records.extend(reference_market_rows(known))
    known.update((item['sport'], item['market_key']) for item in records)
    for sport in WINNER_SPORTS:
        for key, selection in (('MONEYLINE_HOME', 'Home or player 1'), ('MONEYLINE_AWAY', 'Away or player 2')):
            if (sport, key) in known:
                continue
            records.append(_static(
                sport, key, selection, 'ft', None,
                'Match winner from the stored score. A draw is a push. Sets, games and points are not inferred.',
                'A final score for this sport and a model trained on this sport’s own results.',
                'catalogue', None, 'research',
                {'probability': None, 'activated': False, 'kind': 'winner'},
            ))
            known.add((sport, key))
    _discover_quotes(records, quote_counts, _raw_families(connection))
    connection.execute('DELETE FROM sports_ai_market_registry')
    for index, record in enumerate(records, start=1):
        connection.execute(
            """INSERT INTO sports_ai_market_registry
               (id,sport,market_key,selection,period,line_value,settlement_rule,data_requirements,
                model_source,model_version,status,evidence_json,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(sport, market_key) DO UPDATE SET
                 selection=excluded.selection, period=excluded.period, line_value=excluded.line_value,
                 settlement_rule=excluded.settlement_rule, data_requirements=excluded.data_requirements,
                 model_source=excluded.model_source, model_version=excluded.model_version,
                 status=excluded.status, evidence_json=excluded.evidence_json, updated_at=excluded.updated_at""",
            (
                str(uuid.uuid4()), record['sport'], record['market_key'], record['selection'], record['period'],
                record['line_value'], record['settlement_rule'], record['data_requirements'], record['model_source'],
                record['model_version'], record['status'], json.dumps(record['evidence']), current.isoformat(),
            ),
        )
        if index % 40 == 0:
            connection.commit()
    ensure_collected_sports(connection)
    summary = list_registry(connection)
    summary['refreshed'] = len(records)
    summary['catalogue'] = ensure_starting_competitions(connection, current)
    summary['reference_catalogue'] = ensure_reference_catalogue(connection, current)
    return summary


def store_recommendations(connection, now=None):
    """Persist the best qualified market on each fixture."""
    current = now or utc_now()
    rows = [
        dict(row) for row in connection.execute(
            """SELECT d.fixture_id, d.prediction_id, d.market, p.probability, o.expected_edge
               FROM sports_ai_qualification_decisions d
               JOIN sports_ai_engine_predictions p ON p.id=d.prediction_id
               LEFT JOIN sports_ai_value_opportunities o ON o.id=d.opportunity_id
               WHERE d.state='QUALIFIED'""",
        )
    ]
    best = choose_best(rows)
    connection.execute('DELETE FROM sports_ai_market_recommendations')
    for item in best:
        connection.execute(
            """INSERT INTO sports_ai_market_recommendations
               (fixture_id,prediction_id,market,probability,expected_edge,reason,updated_at)
               VALUES(?,?,?,?,?,?,?)""",
            (
                item['fixture_id'], item.get('prediction_id'), item['market'], item.get('probability'),
                item.get('expected_edge'),
                'Highest stored win probability on this fixture. Confidence is used only when probabilities are equal.',
                current.isoformat(),
            ),
        )
    return {'fixtures': len(best), 'places_wager': False}


def link_stored_quotes(connection, now=None, limit=400):
    """Attach fresh stored SportyBet prices to active markets. Does not create a price."""
    from sportybet_research import _assess_value
    current = now or utc_now()
    keys = active_keys(connection)
    if not keys:
        return {'linked': 0, 'places_wager': False}
    settings = connection.execute("SELECT value_json FROM sports_ai_settings WHERE key='engine'").fetchone()
    try:
        minutes = int(json.loads(settings['value_json']).get('odds_max_age_minutes', 15)) if settings else 15
    except (TypeError, ValueError, json.JSONDecodeError):
        minutes = 15
    fresh_after = (current - timedelta(minutes=minutes)).isoformat()
    quotes = connection.execute(
        """SELECT id, fixture_id, market, outcome, line, decimal_odds, sport, home, away
           FROM (
             SELECT q.id, q.fixture_id, q.market, q.outcome, q.line, q.decimal_odds,
                    f.sport, f.home, f.away, f.starts_at,
                    ROW_NUMBER() OVER (
                      PARTITION BY q.fixture_id, q.market, q.outcome, IFNULL(q.line, -1)
                      ORDER BY q.observed_at DESC
                    ) AS rn
             FROM sports_ai_odds_snapshots q
             JOIN sports_ai_fixtures f ON f.id=q.fixture_id
             WHERE q.bookmaker='SportyBet' AND q.observed_at>=? AND q.market='h2h'
               AND f.status='scheduled' AND f.starts_at>?
           ) AS priced
           WHERE rn=1
           ORDER BY starts_at
           LIMIT ?""",
        (fresh_after, current.isoformat(), max(1, limit)),
    ).fetchall()
    linked = 0
    seen = set()
    for quote in quotes:
        canonical = _canonical(quote['sport'], quote['market'], quote['outcome'], quote['line'])
        if (quote['sport'], canonical) not in keys or (quote['fixture_id'], canonical) in seen:
            continue
        seen.add((quote['fixture_id'], canonical))
        status, _edge = _assess_value(
            connection,
            {'fixture_id': quote['fixture_id'], 'canonical_market': canonical},
            quote['id'], float(quote['decimal_odds']), current,
        )
        if status == 'qualified':
            linked += 1
    return {'checked': len(seen), 'qualified': linked, 'places_wager': False}


def _result_rows(sport, model, quote_counts, *pairs):
    metrics = {}
    if model:
        try:
            metrics = json.loads(model['metrics_json'] or '{}')
        except json.JSONDecodeError:
            metrics = {}
    sample = int(metrics.get('holdout_fixtures') or 0)
    error = metrics.get('calibration_error')
    version = None if not model else model['version']
    parent = bool(model and model['active'])
    labels = {
        'HOME': ('Home win', 'Full-time result.'),
        'DRAW': ('Draw', 'Full-time result.'),
        'AWAY': ('Away win', 'Full-time result.'),
        'MONEYLINE_HOME': ('Home moneyline', 'Full-time moneyline.'),
        'MONEYLINE_AWAY': ('Away moneyline', 'Full-time moneyline.'),
    }
    rows = []
    keys = pairs[0::2]
    for key in keys:
        selection, rule = labels[key]
        quotes = quote_counts.get((sport, key), 0)
        status = assign_status(sample, error, quotes, parent_active=parent)
        rows.append(_static(
            sport, key, selection, 'ft', None, rule,
            'Final score and a SportyBet head-to-head price.',
            'match_outcome', version, status,
            {
                'holdout_fixtures': sample,
                'calibration_error': error,
                'brier_score': metrics.get('brier_score'),
                'log_loss': metrics.get('log_loss'),
                'quote_fixtures': quotes,
                'source': 'Stored active-model holdout. The result market was not rescored.',
            },
        ))
    return rows


def _derived_row(sport, key, selection, period, line, rule, holdout, rho, quote_counts, parent_active, version, temperature=1.0, dependence=1.0):
    metrics = _score_market(sport, key, holdout, rho, temperature, dependence)
    quotes = quote_counts.get((sport, key), 0)
    status = assign_status(metrics['sample_size'], metrics['calibration_error'], quotes, parent_active=parent_active)
    source = 'score_distribution' if sport == 'football' else 'normal_points'
    return _static(
        sport, key, selection, period, line, rule,
        'Final score from the stored model holdout, plus a SportyBet price for activation.',
        source, version, status, {**metrics, 'quote_fixtures': quotes},
    )


def _correct_score_row(holdout, rho, version):
    losses = []
    for features, home, away in holdout:
        settlement = market_probability('football', f'CORRECT_{int(home)}_{int(away)}', features, rho)
        if not settlement or not settlement.get('p_win'):
            continue
        losses.append(-math.log(min(1 - 1e-15, max(1e-15, settlement['p_win']))))
    sample = len(losses)
    return _static(
        'football', 'CORRECT_SCORE', 'Correct score', 'ft', None,
        'Each scoreline is a cell of the same Dixon-Coles grid. It is not a rescaled 1X2 probability.',
        'A bookmaker correct-score ladder and a score-level calibration. Neither price ladder is stored.',
        'score_distribution', version, 'research',
        {
            'holdout_fixtures': sample,
            'mean_log_loss': (sum(losses) / sample) if sample else None,
            'quote_fixtures': 0,
            'reason': 'The grid can price a scoreline. No correct-score book is stored, so the market stays in research.',
        },
    )


def _score_market(sport, market, holdout, rho, temperature=1.0, dependence=1.0):
    from accumulator_engine import settle_result
    samples = []
    for features, home, away in holdout:
        settlement = market_probability(
            sport, market, features, rho if sport == 'football' else 0.0, temperature, dependence,
        )
        if not settlement:
            continue
        result = settle_result(sport, market, home, away)
        if result in (None, 'push', 'half_win', 'half_loss'):
            continue
        probability = float(settlement['p_win'])
        push = float(settlement.get('p_push') or 0)
        if push > 1e-9:
            denom = float(settlement['p_win']) + float(settlement['p_loss'])
            if denom <= 0:
                continue
            probability = float(settlement['p_win']) / denom
        if result == 'won':
            samples.append((probability, 1))
        elif result == 'lost':
            samples.append((probability, 0))
    metrics = calibration_metrics(samples)
    return {
        'sample_size': metrics['sample_size'],
        'brier_score': metrics['brier_score'],
        'log_loss': metrics['log_loss'],
        'calibration_error': metrics['calibration_error'],
    }


def _holdout(connection, model):
    rows = connection.execute(
        """SELECT features_json, label_json FROM sports_ai_training_datasets
           WHERE training_run_id=? AND split='holdout' ORDER BY kickoff_at""",
        (model['training_run_id'],),
    ).fetchall()
    parsed = []
    for row in rows:
        try:
            features = json.loads(row['features_json'])
            label = json.loads(row['label_json'])
            parsed.append((features, int(label['home_score']), int(label['away_score'])))
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            continue
    return parsed


def _line_value(key):
    try:
        return float(str(key).rsplit('_', 1)[-1])
    except (TypeError, ValueError):
        return None


def _book_key(market):
    cleaned = ''.join(character if character.isalnum() else '_' for character in str(market).upper()).strip('_')
    return 'BOOK_' + (cleaned or 'UNKNOWN')[:40]


def _extra_keys(sport, quote_counts, existing, limit=16):
    from accumulator_engine import supported_market
    ranked = []
    for (row_sport, key), count in quote_counts.items():
        if row_sport != sport or key in existing or not supported_market(sport, key):
            continue
        ranked.append((int(count), key))
    ranked.sort(reverse=True)
    return [key for _count, key in ranked[:limit]]


def _raw_families(connection):
    rows = connection.execute(
        """SELECT f.sport, lower(q.market) AS market, COUNT(DISTINCT q.fixture_id) AS fixtures
           FROM sports_ai_odds_snapshots q
           JOIN sports_ai_fixtures f ON f.id=q.fixture_id
           WHERE q.bookmaker='SportyBet' AND lower(q.market) NOT IN ('h2h','totals','spreads')
           GROUP BY f.sport, lower(q.market)""",
    ).fetchall()
    return [(row['sport'], row['market'], int(row['fixtures'])) for row in rows]


def _discover_quotes(records, quote_counts, raw_families, limit=80):
    """Register bookmaker families that have no validated model. A quote does not activate them."""
    known = {(item['sport'], item['market_key']) for item in records}
    added = {}
    families = [(sport, key, count) for (sport, key), count in quote_counts.items()]
    families.extend((sport, _book_key(market), count) for sport, market, count in raw_families)
    families.sort(key=lambda item: -item[2])
    for sport, key, count in families:
        if not key or (sport, key) in known or added.get(sport, 0) >= limit:
            continue
        records.append(_static(
            sport, key, key.replace('_', ' ').title(), 'ft', _line_value(key),
            'Seen on a stored SportyBet quote. No validated model settles this selection.',
            'A sport-specific model, historical settlement, and the existing calibration ceilings.',
            'none', None, 'discovered',
            {'quote_fixtures': count, 'probability': None, 'activated': False},
        ))
        known.add((sport, key))
        added[sport] = added.get(sport, 0) + 1


def _quote_counts(connection):
    counts = {}
    rows = connection.execute(
        """SELECT f.sport, q.market, q.outcome, q.line, COUNT(DISTINCT q.fixture_id) AS fixtures
           FROM sports_ai_odds_snapshots q
           JOIN sports_ai_fixtures f ON f.id=q.fixture_id
           WHERE q.bookmaker='SportyBet'
           GROUP BY f.sport, q.market, q.outcome, q.line""",
    ).fetchall()
    for row in rows:
        key = _canonical(row['sport'], row['market'], row['outcome'], row['line'])
        if not key:
            continue
        counts[(row['sport'], key)] = counts.get((row['sport'], key), 0) + int(row['fixtures'])
    return counts


def _canonical(sport, market, outcome, line):
    from sportybet_research import map_market
    mapped = map_market(sport, market, outcome, line)
    if mapped.get('supported') and mapped.get('canonical_market'):
        return mapped['canonical_market']
    market = str(market or '').lower()
    outcome = str(outcome or '')
    if market == 'h2h':
        goal = sport in GOAL_SPORTS
        if outcome == 'draw':
            return 'DRAW' if goal else None
        if outcome == 'home':
            return 'HOME' if goal else 'MONEYLINE_HOME'
        if outcome == 'away':
            return 'AWAY' if goal else 'MONEYLINE_AWAY'
    if market == 'totals' and outcome.upper() in ('OVER', 'UNDER') and line is not None:
        return f'{outcome.upper()}_{float(line):g}'
    if market == 'spreads' and outcome:
        return str(outcome).upper()
    return None


def _basketball_lines(quote_counts):
    ranked = []
    for (sport, key), count in quote_counts.items():
        if sport != 'basketball':
            continue
        if key.startswith('OVER_') or key.startswith('UNDER_') or 'SPREAD_' in key:
            line = key.rsplit('_', 1)[-1]
            try:
                value = float(line)
            except ValueError:
                continue
            ranked.append((count, key, value))
    ranked.sort(reverse=True)
    chosen = []
    seen = set()
    for _count, key, value in ranked:
        family = key.split('_')[0]
        if family in seen and len([item for item in chosen if item[0].startswith(family)]) >= 6:
            continue
        if len(chosen) >= 16:
            break
        label = key.replace('_', ' ').title()
        chosen.append((key, label, value))
        seen.add(family)
    return chosen


def _static(sport, key, selection, period, line, rule, requirements, source, version, status, evidence):
    return {
        'sport': sport,
        'market_key': key,
        'selection': selection,
        'period': period,
        'line_value': line,
        'settlement_rule': rule,
        'data_requirements': requirements,
        'model_source': source,
        'model_version': version,
        'status': status,
        'evidence': evidence,
    }
