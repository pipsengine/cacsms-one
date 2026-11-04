"""Settled-result evaluation for selections, tickets and specialised strategies.

A prediction counts only when it was stored before kickoff and the final result
was stored at or after kickoff. Training and this evaluation never activate a model.
"""

import csv
import io
import json
import math
import uuid
from datetime import datetime, timedelta, timezone

from accumulator_engine import RANGE_TIERS, settle_result
from db import connect

WINDOW = timedelta(days=90)
SUSPEND_SAMPLE = 30
BRIER_MARGIN = 0.05
CALIBRATION_LIMIT = 0.25
NOTE = (
    'Selection accuracy is not a ticket win rate and is not a staking profit. '
    'Returns use a hypothetical unit stake on a price observed before kickoff. '
    'No wager is placed. Training does not activate a model.'
)


def _cohorts(connection):
    try:
        row = connection.execute(
            """SELECT
                 SUM(CASE WHEN cohort='prospective' THEN 1 ELSE 0 END) AS prospective,
                 SUM(CASE WHEN cohort='historical' THEN 1 ELSE 0 END) AS historical,
                 SUM(CASE WHEN observation_class='live_observed' THEN 1 ELSE 0 END) AS live_observed,
                 SUM(CASE WHEN observation_class='timestamp_eligible' THEN 1 ELSE 0 END) AS timestamp_eligible,
                 SUM(CASE WHEN observation_class='live_observed' AND EXISTS (
                   SELECT 1 FROM sports_ai_results r
                   JOIN sports_ai_fixtures f ON f.id=r.fixture_id
                   WHERE r.fixture_id=sports_ai_engine_predictions.fixture_id AND r.status='final'
                     AND r.settled_at>=f.starts_at
                 ) THEN 1 ELSE 0 END) AS live_settled
               FROM sports_ai_engine_predictions""",
        ).fetchone()
    except Exception:
        return {'prospective': None, 'historical': None, 'live_observed': None, 'timestamp_eligible': None, 'live_settled': None}
    return {key: int(row[key] or 0) for key in ('prospective', 'historical', 'live_observed', 'timestamp_eligible', 'live_settled')}


def settle_live_forecasts(connection, now=None):
    """Write an outcome only when an official final result exists for a live_observed forecast."""
    from accumulator_engine import settle_result
    current = _now(now)
    rows = list(connection.execute(
        """SELECT p.id, p.market, f.sport, f.starts_at, r.home_score, r.away_score, r.settled_at
           FROM sports_ai_engine_predictions p
           JOIN sports_ai_fixtures f ON f.id=p.fixture_id
           JOIN sports_ai_results r ON r.fixture_id=f.id AND r.status='final'
           WHERE p.observation_class='live_observed' AND p.settled_outcome IS NULL""",
    ).fetchall())
    rows.extend(connection.execute(
        """SELECT p.id, p.market, f.sport, c.starts_at, r.home_score, r.away_score, r.settled_at
           FROM sports_ai_engine_predictions p
           JOIN sports_ai_fixture_identity_links m ON m.source_fixture_id=p.fixture_id AND m.status='confirmed'
           JOIN sports_ai_fixtures c ON c.id=m.canonical_fixture_id
           JOIN sports_ai_results r ON r.fixture_id=c.id AND r.status='final'
           JOIN sports_ai_fixtures f ON f.id=p.fixture_id
           WHERE p.observation_class='live_observed' AND p.settled_outcome IS NULL""",
    ).fetchall())
    settled = 0
    skipped = 0
    seen = set()
    for row in rows:
        if row['id'] in seen:
            continue
        seen.add(row['id'])
        kickoff, done = _parse(row['starts_at']), _parse(row['settled_at'])
        outcome = settle_result(row['sport'], row['market'], row['home_score'], row['away_score'])
        if not kickoff or not done or done < kickoff or outcome not in ('won', 'lost', 'void', 'push'):
            skipped += 1
            continue
        connection.execute(
            'UPDATE sports_ai_engine_predictions SET settled_outcome=?, result_settled_at=? WHERE id=?',
            (outcome, row['settled_at'], row['id']),
        )
        settled += 1
    awaiting = connection.execute(
        """SELECT COUNT(DISTINCT f.id)
           FROM sports_ai_engine_predictions p
           JOIN sports_ai_fixtures f ON f.id=p.fixture_id
           WHERE p.observation_class='live_observed' AND f.status='scheduled' AND f.starts_at<=?""",
        (current.isoformat(),),
    ).fetchone()[0]
    linked = connection.execute(
        "SELECT COUNT(*) FROM sports_ai_engine_predictions WHERE observation_class='live_observed' AND settled_outcome IS NOT NULL",
    ).fetchone()[0]
    summary = {
        'settled': settled,
        'skipped_unusable_result': skipped,
        'awaiting_official_result': int(awaiting),
        'live_linked': int(linked),
        'places_wager': False,
    }
    connection.execute(
        """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES('forecast_settlement',?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
        (json.dumps(summary), current.isoformat()),
    )
    return summary


def describe_schedule():
    return {
        'job': 'learning',
        'promoted': False,
        'places_wager': False,
        'reason': 'The worker scores settled results once a day. A completed training run does not activate a model.',
    }


def refresh_if_due(connection, actor, audit, now=None):
    current = _now(now)
    marker = current.date().isoformat()
    row = connection.execute("SELECT value_json FROM sports_ai_settings WHERE key='learning_status'").fetchone()
    stored = json.loads(row['value_json']) if row else {}
    if stored.get('last_date') == marker:
        return {'status': 'current', 'run_date': marker, 'promoted': False, 'places_wager': False}
    result = refresh(connection, actor, audit, current)
    connection.execute(
        """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES('learning_status',?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
        (json.dumps({'last_date': marker}), current.isoformat()),
    )
    return result


def refresh(connection, actor, audit, now=None):
    current = _now(now)
    marker = current.date().isoformat()
    existing = connection.execute('SELECT id, summary_json FROM sports_ai_learning_runs WHERE run_date=?', (marker,)).fetchone()
    if existing:
        summary = json.loads(existing['summary_json'])
        summary.update({'status': 'stored', 'run_id': existing['id'], 'promoted': False, 'places_wager': False})
        return summary
    report = build_report(connection, now=current)
    run_id = str(uuid.uuid4())
    controls = _apply_controls(connection, report, actor, audit, current)
    summary = {
        'selection_sample': report['selections']['sample_size'],
        'ticket_sample': report['tickets']['sample_size'],
        'leakage_excluded': report['exceptions']['leakage'],
        'suspended': controls['suspended'],
        'recommendations': controls['recommendations'],
        'promoted': False,
        'places_wager': False,
    }
    connection.execute(
        """INSERT INTO sports_ai_learning_runs
           (id,run_date,status,leakage_excluded,selection_sample,ticket_sample,summary_json,created_at)
           VALUES(?,?,'complete',?,?,?,?,?)""",
        (run_id, marker, summary['leakage_excluded'], summary['selection_sample'], summary['ticket_sample'],
         json.dumps(summary), current.isoformat()),
    )
    for name in ('selections', 'walk_forward', 'calibration', 'tickets', 'tiers', 'selection_tiers', 'draws', 'rollover', 'models'):
        connection.execute(
            """INSERT INTO sports_ai_learning_slices(id,run_id,report,metrics_json) VALUES(?,?,?,?)""",
            (str(uuid.uuid4()), run_id, name, json.dumps(report[name])),
        )
    summary.update({'status': 'complete', 'run_id': run_id, 'run_date': marker})
    return summary


def build_report(connection, filters=None, now=None):
    current = _now(now)
    filters = filters or {}
    valid, excluded, queue, queue_meta = _selections(connection)
    chosen = _latest(valid, ('fixture_id', 'market'))
    per_model = _latest(valid, ('fixture_id', 'market', 'model_id'))
    filtered = _filter_rows(chosen, filters)
    model_rows = _filter_rows(per_model, filters)
    tickets = _tickets(connection)
    return {
        'note': NOTE,
        'promoted': False,
        'places_wager': False,
        'filters': {key: filters.get(key) or '' for key in ('sport', 'market', 'competition', 'model_version', 'date_from', 'date_to')},
        'selections': _score(filtered),
        'models': _model_board(connection, model_rows, current),
        'walk_forward': _walk_forward(filtered),
        'calibration': _score(filtered)['bins'],
        'selection_tiers': _selection_tiers(filtered),
        'tickets': _ticket_summary(tickets),
        'tiers': _ticket_groups(tickets, 'tier'),
        'draws': _draw_report(tickets),
        'rollover': _rollover(connection),
        'cohorts': _cohorts(connection),
        'exceptions': {
            'leakage': sum(1 for row in excluded if str(row.get('reason', '')).startswith('leakage')),
            'unsupported_market': sum(1 for row in excluded if row.get('reason') == 'unsupported_market'),
            'timestamp_eligible': queue_meta.get('timestamp_eligible', 0),
            'items': [row for row in excluded if row.get('fixture_id')][:50],
            'total': len([row for row in excluded if row.get('fixture_id')]),
        },
        'queue': {'total': len(queue), 'items': queue[:50]},
        'equity': _equity(filtered),
        'stored_validation': _stored_validation(connection),
        'evaluated_at': current.isoformat(),
    }


def export_csv(connection, report_name, filters=None, now=None):
    report = build_report(connection, filters, now)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if report_name == 'tickets':
        writer.writerow(['ticket_id', 'group', 'outcome', 'combined_odds', 'hypothetical_profit', 'settled_at'])
        for row in report['tickets']['rows']:
            writer.writerow([row['ticket_id'], row['group'], row['outcome'], _cell(row['combined_odds']), _cell(row['hypothetical_profit']), row['settled_at'] or ''])
    elif report_name == 'tiers':
        writer.writerow(['tier', 'settled', 'wins', 'losses', 'win_rate', 'hypothetical_profit', 'max_drawdown'])
        for row in report['tiers']:
            writer.writerow([row['label'], row['sample_size'], row['wins'], row['losses'], _cell(row['win_rate']), _cell(row['hypothetical_profit']), _cell(row['max_drawdown'])])
    elif report_name == 'draws':
        writer.writerow(['mode', 'picks', 'settled_tickets', 'ticket_wins', 'ticket_win_rate', 'leg_wins', 'leg_losses', 'leg_accuracy'])
        for row in report['draws']['by_size']:
            writer.writerow([row['mode'], row['picks'], row['sample_size'], row['wins'], _cell(row['win_rate']), row['leg_wins'], row['leg_losses'], _cell(row['leg_accuracy'])])
    elif report_name == 'rollover':
        writer.writerow(['campaign', 'mode', 'status', 'opening', 'equity', 'realized_return', 'max_drawdown', 'wins', 'losses', 'skipped', 'path_probability'])
        for row in report['rollover']:
            writer.writerow([row['name'], row['mode'], row['status'], row['opening_balance'], row['equity'], _cell(row['realized_return']), _cell(row['max_drawdown']), row['wins'], row['losses'], row['skipped'], _cell(row['path_probability'])])
    elif report_name == 'models':
        writer.writerow(['model', 'version', 'sport', 'role', 'sample', 'brier', 'log_loss', 'calibration_error', 'accuracy', 'active'])
        for row in report['models']['versions']:
            writer.writerow([row['model_name'], row['version'], row['sport'], row['role'], row['sample_size'], _cell(row['brier_score']), _cell(row['log_loss']), _cell(row['calibration_error']), _cell(row['accuracy']), row['active']])
    else:
        writer.writerow(['fixture_id', 'sport', 'competition', 'market', 'model_version', 'kickoff', 'probability', 'outcome', 'decimal_odds', 'expected_return', 'realized_return', 'policy_version', 'uncertainty'])
        rows = _filter_rows(_latest(_selections(connection)[0], ('fixture_id', 'market')), filters or {})
        for row in rows:
            writer.writerow([
                row['fixture_id'], row['sport'], row['competition'], row['market'], row['model_version'],
                row['kickoff'].isoformat(), row['probability'], row['outcome'], _cell(row['decimal_odds']),
                _cell(row['expected_return']), _cell(row['realized_return']), _cell(row['policy_version']), _cell(row['uncertainty']),
            ])
    return buffer.getvalue()


def _selections(connection):
    predictions = connection.execute(
        """SELECT p.id AS prediction_id, p.fixture_id, p.model_id, p.market, p.outcome, p.probability,
                  p.explanation_json, p.created_at, p.observation_class, f.sport, f.competition, f.home, f.away, f.starts_at,
                  r.home_score, r.away_score, r.settled_at, m.version AS model_version, m.model_name
           FROM sports_ai_engine_predictions p
           JOIN sports_ai_fixtures f ON f.id=p.fixture_id
           JOIN sports_ai_model_registry m ON m.id=p.model_id
           LEFT JOIN sports_ai_results r ON r.fixture_id=f.id AND r.status='final'""",
    ).fetchall()
    quotes = {}
    for quote in connection.execute(
        'SELECT fixture_id, market, outcome, decimal_odds, observed_at FROM sports_ai_odds_snapshots',
    ):
        observed = _parse(quote['observed_at'])
        if not observed:
            continue
        key = (quote['fixture_id'], (quote['market'] or '').upper(), (quote['outcome'] or '').upper())
        quotes.setdefault(key, []).append((observed, float(quote['decimal_odds'])))
    history = {}
    for decision in connection.execute(
        'SELECT prediction_id, policy_version, evidence_json, created_at FROM sports_ai_qualification_history',
    ):
        created = _parse(decision['created_at'])
        if not created:
            continue
        history.setdefault(decision['prediction_id'], []).append((created, decision['policy_version'], decision['evidence_json']))
    valid, excluded, queue = [], [], []
    timestamp_eligible = 0
    for row in predictions:
        created, kickoff, settled = _parse(row['created_at']), _parse(row['starts_at']), _parse(row['settled_at'])
        if not created or not kickoff or created >= kickoff:
            excluded.append(_exception(row, 'leakage_prediction_not_before_kickoff'))
            continue
        if row['observation_class'] in ('historical', 'timestamp_eligible'):
            timestamp_eligible += 1
            if row['settled_at'] is not None:
                excluded.append(_exception(row, 'timestamp_eligible_not_live_observed'))
            continue
        if row['settled_at'] is None:
            queue.append({
                'fixture_id': row['fixture_id'], 'fixture': f"{row['home']} vs {row['away']}",
                'sport': row['sport'], 'market': row['market'], 'kickoff': kickoff.isoformat(),
            })
            continue
        if not settled or settled < kickoff:
            excluded.append(_exception(row, 'leakage_result_before_kickoff'))
            continue
        outcome = settle_result(row['sport'], row['market'], row['home_score'], row['away_score'])
        if outcome not in ('won', 'lost'):
            excluded.append(_exception(row, 'unsupported_market' if outcome is None else 'not_binary'))
            continue
        candidates = [item for item in quotes.get((row['fixture_id'], (row['market'] or '').upper(), (row['outcome'] or '').upper()), []) if item[0] < kickoff]
        odds = max(candidates, key=lambda item: item[0])[1] if candidates else None
        policy, uncertainty = _decision_before(history.get(row['prediction_id'], []), kickoff, row['explanation_json'])
        won = outcome == 'won'
        valid.append({
            'prediction_id': row['prediction_id'], 'fixture_id': row['fixture_id'], 'model_id': row['model_id'],
            'sport': row['sport'], 'competition': row['competition'] or '', 'market': row['market'],
            'model_version': row['model_version'], 'model_name': row['model_name'],
            'home': row['home'], 'away': row['away'], 'kickoff': kickoff, 'created': created,
            'probability': float(row['probability']), 'outcome': outcome, 'won': won,
            'decimal_odds': odds, 'policy_version': policy, 'uncertainty': uncertainty,
            'expected_return': None if odds is None else float(row['probability']) * odds - 1,
            'realized_return': None if odds is None else (odds - 1 if won else -1),
        })
    return valid, excluded, queue, {'timestamp_eligible': timestamp_eligible}


def _tickets(connection):
    rows = connection.execute(
        """SELECT t.id, t.tier, t.sport_mode, t.parameters_json, t.combined_odds, t.joint_probability,
                  t.uncertainty, t.created_at, s.outcome, s.hypothetical_profit, s.settled_at, l.result
           FROM sports_ai_accumulator_tickets t
           LEFT JOIN sports_ai_ticket_settlements s ON s.ticket_id=t.id
           LEFT JOIN sports_ai_accumulator_legs l ON l.ticket_id=t.id""",
    ).fetchall()
    grouped = {}
    for row in rows:
        item = grouped.setdefault(row['id'], {
            'ticket_id': row['id'], 'tier': row['tier'], 'sport_mode': row['sport_mode'],
            'parameters': _json(row['parameters_json']), 'combined_odds': row['combined_odds'],
            'joint_probability': row['joint_probability'], 'uncertainty': row['uncertainty'],
            'created_at': row['created_at'], 'outcome': row['outcome'],
            'hypothetical_profit': row['hypothetical_profit'], 'settled_at': row['settled_at'],
            'legs': [],
        })
        if row['result']:
            item['legs'].append(row['result'])
    prepared = []
    for item in grouped.values():
        parameters = item['parameters']
        product = parameters.get('product')
        item['product'] = product or 'odds_tier'
        item['mode'] = parameters.get('mode')
        item['picks'] = parameters.get('pick_count')
        item['group'] = _tier_label(item['tier']) if product != 'weekly_draw' else f"{item['mode']} {item['picks']}-pick"
        prepared.append(item)
    return prepared


def _score(rows):
    total = len(rows)
    if not total:
        return _empty_score()
    wins = sum(1 for row in rows if row['won'])
    brier = sum((row['probability'] - (1 if row['won'] else 0)) ** 2 for row in rows) / total
    log_loss = sum(_log_loss(row['probability'], 1 if row['won'] else 0) for row in rows) / total
    priced = [row for row in rows if row['decimal_odds'] is not None]
    uncertainties = [row['uncertainty'] for row in rows if row['uncertainty'] is not None]
    mean = sum(row['probability'] for row in rows) / total
    variance = sum((row['probability'] - mean) ** 2 for row in rows) / total
    return {
        'sample_size': total,
        'wins': wins,
        'losses': total - wins,
        'accuracy': wins / total,
        'brier_score': brier,
        'log_loss': log_loss,
        'calibration_error': _calibration_error(rows),
        'bins': _bins(rows),
        'expected_return': None if not priced else sum(row['expected_return'] for row in priced) / len(priced),
        'realized_return': None if not priced else sum(row['realized_return'] for row in priced) / len(priced),
        'priced_sample': len(priced),
        'max_drawdown': _max_drawdown([row['realized_return'] for row in sorted(priced, key=lambda item: item['kickoff'])]),
        'uncertainty': None if not uncertainties else sum(uncertainties) / len(uncertainties),
        'probability_dispersion': math.sqrt(variance),
        'note': 'Accuracy counts binary settled selections. It is not a ticket win rate.',
    }


def _empty_score():
    return {
        'sample_size': 0, 'wins': 0, 'losses': 0, 'accuracy': None, 'brier_score': None, 'log_loss': None,
        'calibration_error': None, 'bins': [], 'expected_return': None, 'realized_return': None,
        'priced_sample': 0, 'max_drawdown': None, 'uncertainty': None, 'probability_dispersion': None,
        'note': 'No settled prediction was stored before kickoff.',
    }


def _walk_forward(rows):
    ordered = sorted(rows, key=lambda row: (row['kickoff'], row['prediction_id']))
    folds = []
    size = max(1, math.ceil(len(ordered) / 4)) if ordered else 1
    for index in range(4):
        chunk = ordered[index * size:(index + 1) * size] if ordered else []
        scored = _score(chunk)
        folds.append({
            'fold': index + 1,
            'from': chunk[0]['kickoff'].date().isoformat() if chunk else None,
            'to': chunk[-1]['kickoff'].date().isoformat() if chunk else None,
            'sample_size': scored['sample_size'],
            'brier_score': scored['brier_score'],
            'log_loss': scored['log_loss'],
            'accuracy': scored['accuracy'],
            'calibration_error': scored['calibration_error'],
        })
    return {
        'folds': folds,
        'note': 'Each fold contains only later kickoffs than the previous fold, and only predictions stored before those kickoffs.',
    }


def _selection_tiers(rows):
    groups = {}
    for row in rows:
        label = _price_band(row['decimal_odds'])
        groups.setdefault(label, []).append(row)
    return [_group_metrics(label, items) for label, items in groups.items()]


def _ticket_summary(tickets):
    settled = [row for row in tickets if row['outcome'] in ('won', 'lost')]
    wins = sum(1 for row in settled if row['outcome'] == 'won')
    profits = [row['hypothetical_profit'] for row in sorted(settled, key=lambda item: item['settled_at'] or '') if row['hypothetical_profit'] is not None]
    legs = [result for row in tickets for result in row['legs'] if result in ('won', 'lost')]
    leg_wins = sum(1 for result in legs if result == 'won')
    return {
        'sample_size': len(settled),
        'wins': wins,
        'losses': len(settled) - wins,
        'win_rate': None if not settled else wins / len(settled),
        'hypothetical_profit': None if not profits else sum(profits),
        'max_drawdown': _max_drawdown(profits),
        'leg_sample': len(legs),
        'leg_accuracy': None if not legs else leg_wins / len(legs),
        'rows': [
            {'ticket_id': row['ticket_id'], 'group': row['group'], 'outcome': row['outcome'],
             'combined_odds': row['combined_odds'], 'hypothetical_profit': row['hypothetical_profit'],
             'settled_at': row['settled_at']}
            for row in settled[:100]
        ],
        'note': 'Ticket win rate counts a complete settled ticket. Leg accuracy is separate and is not the ticket win rate.',
    }


def _ticket_groups(tickets, kind):
    groups = {}
    for row in tickets:
        if kind == 'tier' and row['product'] == 'weekly_draw':
            continue
        if row['outcome'] not in ('won', 'lost'):
            continue
        label = row['group'] if kind == 'tier' else row['product']
        groups.setdefault(label, []).append(row)
    output = []
    for label, items in groups.items():
        wins = sum(1 for row in items if row['outcome'] == 'won')
        profits = [row['hypothetical_profit'] for row in sorted(items, key=lambda item: item['settled_at'] or '') if row['hypothetical_profit'] is not None]
        output.append({
            'label': label, 'sample_size': len(items), 'wins': wins, 'losses': len(items) - wins,
            'win_rate': wins / len(items), 'hypothetical_profit': None if not profits else sum(profits),
            'max_drawdown': _max_drawdown(profits),
        })
    return output


def _draw_report(tickets):
    draws = [row for row in tickets if row['product'] == 'weekly_draw']
    by_size = []
    for mode in ('WEEKLY', 'SATURDAY_ONLY'):
        for picks in (2, 5, 8, 10):
            chosen = [row for row in draws if row['mode'] == mode and row['picks'] == picks and row['outcome'] in ('won', 'lost')]
            wins = sum(1 for row in chosen if row['outcome'] == 'won')
            legs = [result for row in chosen for result in row['legs'] if result in ('won', 'lost')]
            leg_wins = sum(1 for result in legs if result == 'won')
            by_size.append({
                'mode': mode, 'picks': picks, 'sample_size': len(chosen), 'wins': wins,
                'losses': len(chosen) - wins, 'win_rate': None if not chosen else wins / len(chosen),
                'leg_wins': leg_wins, 'leg_losses': len(legs) - leg_wins,
                'leg_accuracy': None if not legs else leg_wins / len(legs),
            })
    modes = []
    for mode in ('WEEKLY', 'SATURDAY_ONLY'):
        chosen = [row for row in draws if row['mode'] == mode and row['outcome'] in ('won', 'lost')]
        wins = sum(1 for row in chosen if row['outcome'] == 'won')
        modes.append({'mode': mode, 'sample_size': len(chosen), 'win_rate': None if not chosen else wins / len(chosen)})
    return {'by_size': by_size, 'by_mode': modes, 'note': 'Eight- and ten-pick tickets stay in their own rows. A short sample is not filled.'}


def _rollover(connection):
    campaigns = connection.execute('SELECT * FROM sports_ai_rollover_campaigns ORDER BY created_at').fetchall()
    days = connection.execute(
        'SELECT campaign_id, status, probability FROM sports_ai_rollover_days',
    ).fetchall()
    by_campaign = {}
    for day in days:
        by_campaign.setdefault(day['campaign_id'], []).append(day)
    output = []
    for campaign in campaigns:
        stored = [day['probability'] for day in by_campaign.get(campaign['id'], []) if day['status'] in ('SELECTED', 'WON', 'LOST') and day['probability'] is not None]
        equity = float(campaign['bankroll']) + float(campaign['protected_capital'])
        opening = float(campaign['opening_balance'])
        output.append({
            'id': campaign['id'], 'name': campaign['name'], 'mode': campaign['mode'], 'status': campaign['status'],
            'opening_balance': opening, 'equity': equity,
            'realized_return': None if opening <= 0 else (equity - opening) / opening,
            'max_drawdown': campaign['max_drawdown'], 'wins': campaign['wins'], 'losses': campaign['losses'],
            'skipped': campaign['skipped'], 'consecutive_wins': campaign['max_consecutive_wins'],
            'path_probability': None if not stored else math.prod(float(value) for value in stored),
            'places_wager': False,
            'note': 'Future days have no stored probability, so a 30-day win chance is not estimated.',
        })
    return output


def _stored_validation(connection):
    """Copy the training record. These figures are not prospective pre-kickoff scores."""
    output = []
    for model in connection.execute(
        """SELECT model_name, version, sport, active, metrics_json
           FROM sports_ai_model_registry ORDER BY created_at DESC""",
    ):
        metrics = _json(model['metrics_json'])
        bins = []
        for item in metrics.get('bins') or []:
            if not isinstance(item, dict) or not item.get('sample_size'):
                continue
            bins.append({
                'lower': item.get('lower'), 'upper': item.get('upper'), 'count': item.get('sample_size'),
                'confidence': item.get('mean_predicted'), 'frequency': item.get('observed_frequency'),
            })
        output.append({
            'model_name': model['model_name'], 'version': model['version'], 'sport': model['sport'],
            'active': model['active'], 'brier_score': metrics.get('brier_score'), 'log_loss': metrics.get('log_loss'),
            'calibration_error': metrics.get('calibration_error'), 'holdout_fixtures': metrics.get('holdout_fixtures'),
            'fixtures_scored': metrics.get('fixtures_scored'), 'walk_forward_folds': metrics.get('walk_forward_folds'),
            'bins': bins,
            'note': 'Copied from the stored training record. It is not a prospective score of predictions made before kickoff.',
        })
    return output


def _model_board(connection, rows, now):
    versions = []
    grouped = {}
    for row in rows:
        grouped.setdefault(row['model_id'], []).append(row)
    registry = connection.execute(
        """SELECT id, model_name, version, sport, status, active, lifecycle, metrics_json
           FROM sports_ai_model_registry ORDER BY created_at""",
    ).fetchall()
    for model in registry:
        scored = _score(grouped.get(model['id'], []))
        versions.append({
            'model_id': model['id'], 'model_name': model['model_name'], 'version': model['version'],
            'sport': model['sport'], 'status': model['status'], 'active': model['active'],
            'lifecycle': model['lifecycle'], 'role': 'champion' if model['active'] else ('challenger' if model['status'] == 'validated' else model['status']),
            'sample_size': scored['sample_size'], 'brier_score': scored['brier_score'], 'log_loss': scored['log_loss'],
            'calibration_error': scored['calibration_error'], 'accuracy': scored['accuracy'],
        })
    comparisons = []
    for sport in ('football', 'basketball'):
        champion = next((row for row in versions if row['sport'] == sport and row['active']), None)
        challengers = [row for row in versions if row['sport'] == sport and row['role'] == 'challenger' and row['sample_size'] >= 10 and row['brier_score'] is not None]
        best = min(challengers, key=lambda row: row['brier_score'], default=None)
        better = bool(champion and best and champion['sample_size'] >= 10 and champion['brier_score'] is not None and best['brier_score'] < champion['brier_score'])
        comparisons.append({
            'sport': sport,
            'champion': None if not champion else champion['version'],
            'challenger': None if not best else best['version'],
            'challenger_has_lower_brier': better,
            'activated': False,
            'recommendation': 'Challenger has a lower stored Brier score on settled results. Activation still requires the held-out validation gate and an administrator.' if better else 'No stored challenger has enough settled evidence to replace the active model.',
        })
    signals = _deterioration(grouped, registry, now)
    return {'versions': versions, 'comparisons': comparisons, 'deterioration': signals, 'promoted': False}


def _deterioration(grouped, registry, now):
    signals = []
    by_id = {model['id']: model for model in registry}
    for model_id, rows in grouped.items():
        model = by_id.get(model_id)
        if not model:
            continue
        recent_start = now - WINDOW
        prior_start = now - WINDOW - WINDOW
        recent = [row for row in rows if prior_start <= row['kickoff'] and row['kickoff'] >= recent_start]
        prior = [row for row in rows if prior_start <= row['kickoff'] < recent_start]
        recent_score, prior_score = _score(recent), _score(prior)
        deteriorated = (
            recent_score['sample_size'] >= SUSPEND_SAMPLE
            and prior_score['sample_size'] >= SUSPEND_SAMPLE
            and recent_score['calibration_error'] is not None
            and recent_score['calibration_error'] > CALIBRATION_LIMIT
            and recent_score['brier_score'] > prior_score['brier_score'] + BRIER_MARGIN
        )
        signals.append({
            'model_id': model_id, 'sport': model['sport'], 'version': model['version'], 'active': model['active'],
            'recent_sample': recent_score['sample_size'], 'prior_sample': prior_score['sample_size'],
            'recent_brier': recent_score['brier_score'], 'prior_brier': prior_score['brier_score'],
            'recent_calibration_error': recent_score['calibration_error'],
            'deteriorated': deteriorated,
        })
    return signals


def _apply_controls(connection, report, actor, audit, now):
    suspended, recommendations = [], []
    active = {row['model_id']: row for row in report['models']['deterioration']}
    for signal in report['models']['deterioration']:
        if signal['deteriorated'] and signal['active']:
            connection.execute(
                """UPDATE sports_ai_model_registry
                   SET active=0, status='inactive', lifecycle='suspended' WHERE id=? AND active=1""",
                (signal['model_id'],),
            )
            _control(connection, signal['model_id'], 'suspend', 'suspended', 'Recent settled calibration and Brier score deteriorated against the previous 90 days.', signal, now)
            if audit:
                audit(connection, actor, 'models.learning_suspend', {'model_id': signal['model_id'], 'version': signal['version'], 'promoted': False})
            suspended.append(signal['model_id'])
        elif signal['recent_sample'] >= SUSPEND_SAMPLE and signal['recent_calibration_error'] is not None and signal['recent_calibration_error'] > 0.20:
            _control(connection, signal['model_id'], 'recommend_retrain', 'recorded', 'Recent calibration is outside the activation ceiling. Training would not activate a replacement.', signal, now)
            recommendations.append(signal['model_id'])
        else:
            _control(connection, signal['model_id'], 'hold', 'recorded', 'The settled sample does not meet the suspension gate.', signal, now)
    for comparison in report['models']['comparisons']:
        if comparison['challenger_has_lower_brier']:
            recommendations.append(comparison['sport'])
    if suspended:
        from suitability import sync_lifecycles
        sync_lifecycles(connection)
    return {'suspended': suspended, 'recommendations': recommendations, 'promoted': False, 'active_seen': list(active)}


def _control(connection, model_id, action, status, reason, evidence, now):
    connection.execute(
        """INSERT INTO sports_ai_learning_controls
           (id,model_id,action,status,reason,evidence_json,created_at) VALUES(?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), model_id, action, status, reason, json.dumps(evidence, default=str), now.isoformat()),
    )


def _group_metrics(label, rows):
    scored = _score(rows)
    return {'label': label, 'sample_size': scored['sample_size'], 'accuracy': scored['accuracy'], 'brier_score': scored['brier_score'], 'expected_return': scored['expected_return'], 'realized_return': scored['realized_return'], 'max_drawdown': scored['max_drawdown']}


def _equity(rows):
    priced = sorted((row for row in rows if row['realized_return'] is not None), key=lambda row: row['kickoff'])
    curve, equity = [], 0
    for row in priced:
        equity += row['realized_return']
        curve.append({'kickoff': row['kickoff'].date().isoformat(), 'equity': equity})
    if len(curve) > 240:
        stride = math.ceil(len(curve) / 240)
        curve = curve[::stride]
        if curve[-1] != {'kickoff': priced[-1]['kickoff'].date().isoformat(), 'equity': equity}:
            curve.append({'kickoff': priced[-1]['kickoff'].date().isoformat(), 'equity': equity})
    return {'points': curve, 'sample_size': len(priced), 'note': 'Cumulative hypothetical unit-stake profit. Points are stored outcomes, not an interpolated forecast.'}


def _filter_rows(rows, filters):
    output = rows
    if filters.get('sport'):
        output = [row for row in output if row['sport'] == filters['sport']]
    if filters.get('market'):
        output = [row for row in output if row['market'] == filters['market']]
    if filters.get('competition'):
        output = [row for row in output if row['competition'] == filters['competition']]
    if filters.get('model_version'):
        output = [row for row in output if row['model_version'] == filters['model_version']]
    start, end = _parse(filters.get('date_from')), _parse(filters.get('date_to'))
    if start:
        output = [row for row in output if row['kickoff'] >= start]
    if end:
        output = [row for row in output if row['kickoff'] <= end]
    return output


def _latest(rows, keys):
    chosen = {}
    for row in sorted(rows, key=lambda item: (item['created'], item['prediction_id'])):
        chosen[tuple(row[key] for key in keys)] = row
    return list(chosen.values())


def _decision_before(history, kickoff, explanation_json):
    eligible = [item for item in history if item[0] < kickoff]
    uncertainty = None
    policy = None
    if eligible:
        created, policy, raw = max(eligible, key=lambda item: item[0])
        evidence = _json(raw)
        if 'uncertainty' in evidence and evidence['uncertainty'] is not None:
            uncertainty = float(evidence['uncertainty'])
    if uncertainty is None:
        explanation = _json(explanation_json)
        if 'uncertainty' in explanation and explanation['uncertainty'] is not None:
            uncertainty = float(explanation['uncertainty'])
    return policy, uncertainty


def _bins(rows):
    bins = []
    for index in range(10):
        low, high = index / 10, (index + 1) / 10
        chosen = [row for row in rows if (low <= row['probability'] < high) or (index == 9 and row['probability'] == 1)]
        if not chosen:
            continue
        confidence = sum(row['probability'] for row in chosen) / len(chosen)
        frequency = sum(1 for row in chosen if row['won']) / len(chosen)
        bins.append({'lower': low, 'upper': high, 'count': len(chosen), 'confidence': confidence, 'frequency': frequency})
    return bins


def _calibration_error(rows):
    bins = _bins(rows)
    if not bins:
        return None
    return sum(abs(item['confidence'] - item['frequency']) for item in bins) / len(bins)


def _max_drawdown(profits):
    if not profits:
        return None
    equity = peak = worst = 0
    for profit in profits:
        equity += profit
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def _log_loss(probability, outcome):
    clipped = min(1 - 1e-12, max(1e-12, probability))
    return -(outcome * math.log(clipped) + (1 - outcome) * math.log(1 - clipped))


def _price_band(odds):
    if odds is None:
        return 'No pre-kickoff price'
    for band in RANGE_TIERS:
        if band['low'] <= odds <= band['high']:
            return band['label']
    return 'Outside configured ranges'


def _tier_label(tier):
    for band in RANGE_TIERS:
        if band['tier'] == tier:
            return band['label']
    return f'Stored tier {tier}'


def _exception(row, reason):
    return {'fixture_id': row['fixture_id'], 'market': row['market'], 'reason': reason}


def _json(value):
    try:
        parsed = json.loads(value or '{}')
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse(value):
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _now(value):
    return _parse(value) or datetime.now(timezone.utc)


def _cell(value):
    return '' if value is None else value


def build_learning_router(auth, audit):
    from fastapi import APIRouter, Depends, HTTPException, Query
    from fastapi.responses import Response

    router = APIRouter(prefix='/api/sports-ai/learning', tags=['learning'])

    def _filters(sport, market, competition, model_version, date_from, date_to):
        return {'sport': sport, 'market': market, 'competition': competition, 'model_version': model_version, 'date_from': date_from, 'date_to': date_to}

    @router.get('/report')
    def report(sport: str = '', market: str = '', competition: str = '', model_version: str = '', date_from: str = '', date_to: str = '', user=Depends(auth)):
        with connect() as connection:
            return build_report(connection, _filters(sport, market, competition, model_version, date_from, date_to))

    @router.get('/runs')
    def runs(user=Depends(auth)):
        with connect() as connection:
            rows = [dict(row) for row in connection.execute('SELECT id, run_date, status, leakage_excluded, selection_sample, ticket_sample, created_at FROM sports_ai_learning_runs ORDER BY run_date DESC LIMIT 30')]
            controls = [dict(row) for row in connection.execute('SELECT model_id, action, status, reason, created_at FROM sports_ai_learning_controls ORDER BY created_at DESC LIMIT 30')]
        return {'runs': rows, 'controls': controls, 'promoted': False, 'note': NOTE}

    @router.post('/refresh')
    def run_refresh(user=Depends(auth)):
        with connect() as connection:
            return refresh(connection, user, audit)

    @router.get('/export')
    def download(report: str = Query(default='selections'), sport: str = '', market: str = '', competition: str = '', model_version: str = '', date_from: str = '', date_to: str = '', user=Depends(auth)):
        allowed = {'selections', 'tickets', 'tiers', 'draws', 'rollover', 'models'}
        if report not in allowed:
            raise HTTPException(422, 'Report must be selections, tickets, tiers, draws, rollover or models')
        with connect() as connection:
            body = export_csv(connection, report, _filters(sport, market, competition, model_version, date_from, date_to))
        return Response(content=body, media_type='text/csv', headers={'Content-Disposition': f'attachment; filename="cacsms-{report}.csv"'})

    return router
