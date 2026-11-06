"""30-day daily rollover simulation.

One qualified single may be chosen per campaign day. A day without a candidate
is SKIPPED_DAY. Stakes are illustrative. The engine never places a wager and
never raises a stake to recover a loss.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

from booking_schedule import WAT
from display_policy import LIVE_PROBABILITY, above

MODES = ('FULL_ROLLOVER_SIMULATION', 'CAPITAL_CONTROLLED_SIMULATION')
CALIBRATED = {
    'football': {'HOME', 'DRAW', 'AWAY'},
    'basketball': {'MONEYLINE_HOME', 'MONEYLINE_AWAY'},
}
WARNING = (
    'This is an illustrative simulation. A 30-day rollover cannot guarantee 30 consecutive wins '
    'or a zero-loss result. One loss can end a full-rollover campaign. A day with no qualified '
    'selection is skipped. No real-money wager is placed.'
)
SELECTION_WINDOW = {'start': '06:00', 'end': '09:00'}
STATUS_KEY = 'daily_rollover_status'


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


def local_date(now=None):
    return _now(now).astimezone(WAT).date()


def _minute(value):
    hour, minute = value.split(':')
    return int(hour) * 60 + int(minute)


def selection_open(now=None):
    local = _now(now).astimezone(WAT)
    minute = local.hour * 60 + local.minute
    return _minute(SELECTION_WINDOW['start']) <= minute <= _minute(SELECTION_WINDOW['end'])


def describe_schedule(now=None):
    current = _now(now)
    return {
        'timezone': 'Africa/Lagos',
        'offset': '+01:00',
        'local_time': current.astimezone(WAT).isoformat(),
        'local_date': local_date(current).isoformat(),
        'selects_now': selection_open(current),
        'window': SELECTION_WINDOW,
        'places_wager': False,
        'warning': WARNING,
    }


def default_campaign(overrides=None):
    payload = {
        'name': '30-day simulation',
        'mode': 'CAPITAL_CONTROLLED_SIMULATION',
        'length_days': 30,
        'opening_balance': 1000.0,
        'currency': 'NGN',
        'odds_target': 1.50,
        'odds_low': 1.40,
        'odds_high': 1.60,
        'stake_mode': 'fixed',
        'fixed_stake': 100.0,
        'stake_fraction': 0.05,
        'max_stake_fraction': 0.10,
        'protected_profit_fraction': 0.50,
        'profit_lock_multiple': 1.50,
        'stop_loss_fraction': 0.20,
    }
    payload.update(overrides or {})
    return payload


def _validate(spec):
    if spec['mode'] not in MODES:
        raise ValueError('Mode must be FULL_ROLLOVER_SIMULATION or CAPITAL_CONTROLLED_SIMULATION')
    if spec['stake_mode'] not in ('fixed', 'fractional'):
        raise ValueError('Stake mode must be fixed or fractional')
    length = int(spec['length_days'])
    if not 1 <= length <= 366:
        raise ValueError('A campaign is between 1 and 366 calendar days')
    if float(spec['opening_balance']) <= 0:
        raise ValueError('Opening balance must be positive')
    low, target, high = float(spec['odds_low']), float(spec['odds_target']), float(spec['odds_high'])
    if not 1 < low <= target <= high:
        raise ValueError('The odds target must sit inside the low and high bounds, and every bound must be above 1')
    for key in ('stake_fraction', 'max_stake_fraction', 'protected_profit_fraction', 'stop_loss_fraction'):
        if not 0 <= float(spec[key]) <= 1 or (key != 'protected_profit_fraction' and float(spec[key]) == 0):
            raise ValueError(f'{key} is outside its allowed range')
    if float(spec['fixed_stake']) <= 0 or float(spec['fixed_stake']) > float(spec['opening_balance']):
        raise ValueError('The fixed stake must be positive and no larger than the opening balance')
    if float(spec['profit_lock_multiple']) < 1:
        raise ValueError('Profit lock must be at least 1')
    return spec


def create_campaign(connection, spec, now=None):
    current = _now(now)
    clean = _validate(default_campaign(spec))
    campaign_id = str(uuid.uuid4())
    opening = float(clean['opening_balance'])
    connection.execute(
        """INSERT INTO sports_ai_rollover_campaigns
           (id,name,mode,status,start_date,length_days,opening_balance,currency,odds_target,odds_low,odds_high,
            stake_mode,fixed_stake,stake_fraction,max_stake_fraction,protected_profit_fraction,profit_lock_multiple,
            stop_loss_fraction,bankroll,protected_capital,peak_equity,places_wager,created_at,updated_at)
           VALUES(?,?,?,'active',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,0,?,?)""",
        (
            campaign_id, clean['name'], clean['mode'], local_date(current).isoformat(), int(clean['length_days']),
            opening, 'NGN', float(clean['odds_target']), float(clean['odds_low']), float(clean['odds_high']),
            clean['stake_mode'], float(clean['fixed_stake']), float(clean['stake_fraction']),
            float(clean['max_stake_fraction']), float(clean['protected_profit_fraction']),
            float(clean['profit_lock_multiple']), float(clean['stop_loss_fraction']),
            opening, opening, current.isoformat(), current.isoformat(),
        ),
    )
    return get_campaign(connection, campaign_id)


def list_campaigns(connection):
    return [_public_campaign(row) for row in connection.execute(
        'SELECT * FROM sports_ai_rollover_campaigns ORDER BY created_at DESC',
    )]


def get_campaign(connection, campaign_id):
    row = connection.execute('SELECT * FROM sports_ai_rollover_campaigns WHERE id=?', (campaign_id,)).fetchone()
    if not row:
        return None
    item = _public_campaign(row)
    item['days'] = list_days(connection, campaign_id)
    item['warning'] = WARNING
    item['places_wager'] = False
    return item


def _public_campaign(row):
    item = dict(row)
    equity = float(item['bankroll']) + float(item['protected_capital'])
    item['equity'] = equity
    item['return_multiple'] = equity / float(item['opening_balance'])
    item['survival'] = item['status'] == 'active' or (item['status'] == 'completed' and item['losses'] == 0)
    item['path_probability'] = None
    item['probability_note'] = 'Future days have no stored probability, so a 30-day win chance is not estimated.'
    item['places_wager'] = False
    item['warning'] = WARNING
    return item


def list_days(connection, campaign_id):
    rows = []
    probability = 1.0
    counted = 0
    for row in connection.execute(
        'SELECT * FROM sports_ai_rollover_days WHERE campaign_id=? ORDER BY campaign_date',
        (campaign_id,),
    ):
        item = dict(row)
        item['score'] = json.loads(item.pop('score_json') or '{}')
        if item['probability'] is not None and item['status'] in ('SELECTED', 'WON', 'LOST'):
            probability *= float(item['probability'])
            counted += 1
        rows.append(item)
    return rows


def campaign_path_probability(days):
    values = [float(day['probability']) for day in days if day.get('probability') is not None and day['status'] in ('SELECTED', 'WON', 'LOST')]
    if not values:
        return None
    product = 1.0
    for value in values:
        product *= value
    return product


def load_candidates(connection, campaign, day, now=None):
    current = _now(now)
    policy_row = connection.execute("SELECT value_json FROM sports_ai_settings WHERE key='qualification_policy'").fetchone()
    max_age = int((json.loads(policy_row['value_json']) if policy_row else {}).get('odds_max_age_minutes') or 15)
    rows = connection.execute(
        """SELECT d.fixture_id, d.sport, d.market, d.evidence_json, f.home, f.away, f.starts_at, f.status AS fixture_status
           FROM sports_ai_qualification_decisions d
           JOIN sports_ai_fixtures f ON f.id=d.fixture_id
           WHERE d.state='QUALIFIED'
             AND EXISTS (SELECT 1 FROM json_each(d.products_json) WHERE value='booking')""",
    ).fetchall()
    eligible = []
    excluded = []
    for row in rows:
        market = str(row['market'] or '').upper()
        from market_registry import is_active
        if market not in CALIBRATED.get(row['sport'], set()) and not is_active(connection, row['sport'], market):
            continue
        kickoff = _parse(row['starts_at'])
        identity = f"{row['home']} vs {row['away']}"
        if kickoff is None or kickoff.astimezone(WAT).date().isoformat() != day or kickoff <= current:
            continue
        if row['fixture_status'] not in ('scheduled', 'not_started'):
            continue
        evidence = json.loads(row['evidence_json'] or '{}')
        if not above(evidence.get('probability'), LIVE_PROBABILITY):
            excluded.append({'fixture': identity, 'market': market, 'reason': 'The stored win probability is not above 80%.'})
            continue
        quote = _latest_quote(connection, row['fixture_id'], market, current, max_age)
        if quote is None:
            excluded.append({'fixture': identity, 'market': market, 'reason': 'No price inside the qualification window is stored.'})
            continue
        odds = float(quote['decimal_odds'])
        if odds < float(campaign['odds_low']) or odds > float(campaign['odds_high']):
            excluded.append({'fixture': identity, 'market': market, 'reason': f'Stored odds {odds:.2f} are outside {campaign["odds_low"]:.2f}–{campaign["odds_high"]:.2f}.'})
            continue
        eligible.append(_candidate(row, quote, evidence, campaign))
    from market_registry import choose_best
    eligible = choose_best(eligible)
    eligible.sort(key=lambda item: (-item['probability'], item['starts_at'], item['fixture_id']))
    return {'eligible': eligible, 'excluded': excluded[:40]}


def _latest_quote(connection, fixture_id, market, now, max_age):
    row = connection.execute(
        """SELECT o.expected_edge, o.data_completeness, p.probability, q.decimal_odds, q.bookmaker, q.observed_at
           FROM sports_ai_value_opportunities o
           JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id
           JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
           WHERE p.fixture_id=? AND upper(p.market)=upper(?)
           ORDER BY q.observed_at DESC LIMIT 1""",
        (fixture_id, market),
    ).fetchone()
    if not row or row['decimal_odds'] is None or row['probability'] is None:
        return None
    observed = _parse(row['observed_at'])
    if observed is None or now - observed > timedelta(minutes=max_age):
        return None
    return row


def _candidate(row, quote, evidence, campaign):
    odds = float(quote['decimal_odds'])
    probability = float(quote['probability'])
    edge = float(quote['expected_edge']) if quote['expected_edge'] is not None else evidence.get('expected_edge')
    uncertainty = evidence.get('uncertainty')
    completeness = quote['data_completeness'] if quote['data_completeness'] is not None else evidence.get('completeness')
    distance = abs(odds - float(campaign['odds_target']))
    score = (float(edge) if edge is not None else 0) - distance
    if completeness is not None:
        score += float(completeness) * 0.01
    if uncertainty is not None:
        score -= float(uncertainty) * 0.01
    market = str(row['market']).upper()
    selection = 'Draw' if market == 'DRAW' else row['home'] if market in ('HOME', 'MONEYLINE_HOME') else row['away']
    return {
        'fixture_id': row['fixture_id'], 'sport': row['sport'], 'market': market, 'selection': selection,
        'home': row['home'], 'away': row['away'], 'starts_at': row['starts_at'],
        'decimal_odds': odds, 'probability': probability, 'edge': None if edge is None else float(edge),
        'uncertainty': None if uncertainty is None else float(uncertainty),
        'completeness': None if completeness is None else float(completeness),
        'bookmaker': quote['bookmaker'], 'score': score,
    }


def equity(campaign):
    return float(campaign['bankroll']) + float(campaign['protected_capital'])


def stop_loss_hit(campaign):
    if campaign['mode'] != 'CAPITAL_CONTROLLED_SIMULATION':
        return float(campaign['bankroll']) <= 0
    opening = float(campaign['opening_balance'])
    current = equity(campaign)
    peak = float(campaign['peak_equity']) or current
    loss_from_open = (opening - current) / opening if opening else 0
    drawdown = (peak - current) / peak if peak else 0
    limit = float(campaign['stop_loss_fraction'])
    return loss_from_open >= limit or drawdown >= limit


def next_stake(campaign, previous=None):
    """Stake for the next day. A loss never increases the following stake."""
    bankroll = float(campaign['bankroll'])
    if bankroll <= 0:
        return 0
    if campaign['mode'] == 'FULL_ROLLOVER_SIMULATION':
        stake = bankroll
    elif campaign['stake_mode'] == 'fractional':
        stake = float(campaign['stake_fraction']) * bankroll
    else:
        stake = float(campaign['fixed_stake'])
    if campaign['mode'] == 'CAPITAL_CONTROLLED_SIMULATION':
        stake = min(stake, float(campaign['max_stake_fraction']) * bankroll, bankroll)
        if previous and previous.get('status') == 'LOST' and previous.get('stake'):
            stake = min(stake, float(previous['stake']))
    return round(stake, 2)


def run_day(connection, campaign_id, now=None, candidates=None):
    current = _now(now)
    campaign = connection.execute('SELECT * FROM sports_ai_rollover_campaigns WHERE id=?', (campaign_id,)).fetchone()
    if not campaign:
        raise ValueError('Campaign not found')
    campaign = dict(campaign)
    day = local_date(current).isoformat()
    existing = connection.execute(
        'SELECT * FROM sports_ai_rollover_days WHERE campaign_id=? AND campaign_date=?',
        (campaign_id, day),
    ).fetchone()
    if existing:
        return {'status': existing['status'], 'idempotent': True, 'day': dict(existing), 'places_wager': False}
    if campaign['status'] != 'active':
        return {'status': campaign['status'], 'idempotent': True, 'places_wager': False}
    if day < campaign['start_date'] or _day_index(campaign, day) > int(campaign['length_days']):
        _finish_if_elapsed(connection, campaign, current)
        return {'status': 'outside_campaign', 'places_wager': False}
    if stop_loss_hit(campaign):
        _set_status(connection, campaign_id, 'stopped', current)
        return {'status': 'stopped', 'reason': 'The stop-loss has been reached. The stake was not increased.', 'places_wager': False}
    loaded = candidates if candidates is not None else load_candidates(connection, campaign, day, current)
    eligible = loaded.get('eligible') or []
    previous = connection.execute(
        'SELECT status, stake FROM sports_ai_rollover_days WHERE campaign_id=? ORDER BY campaign_date DESC LIMIT 1',
        (campaign_id,),
    ).fetchone()
    if not eligible:
        _insert_day(connection, campaign, day, 'SKIPPED_DAY', None, 0, 'No qualified selection is inside the odds range.', current)
        connection.execute('UPDATE sports_ai_rollover_campaigns SET skipped=skipped+1, updated_at=? WHERE id=?', (current.isoformat(), campaign_id))
        return {'status': 'SKIPPED_DAY', 'places_wager': False, 'warning': WARNING}
    chosen = eligible[0]
    stake = next_stake(campaign, dict(previous) if previous else None)
    if stake <= 0 or stake > float(campaign['bankroll']):
        _insert_day(connection, campaign, day, 'SKIPPED_DAY', None, 0, 'The simulated bankroll cannot fund a stake.', current)
        return {'status': 'SKIPPED_DAY', 'places_wager': False}
    _insert_day(connection, campaign, day, 'SELECTED', chosen, stake, 'One qualified single selected for this calendar day.', current)
    return {'status': 'SELECTED', 'selection': chosen, 'stake': stake, 'places_wager': False, 'warning': WARNING}


def _day_index(campaign, day):
    start = datetime.fromisoformat(campaign['start_date']).date()
    current = datetime.fromisoformat(day).date()
    return (current - start).days + 1


def _insert_day(connection, campaign, day, status, chosen, stake, reason, now):
    chosen = chosen or {}
    connection.execute(
        """INSERT INTO sports_ai_rollover_days
           (id,campaign_id,day_index,campaign_date,status,fixture_id,sport,market,selection,home,away,
            decimal_odds,probability,edge,uncertainty,completeness,stake,bankroll_before,bankroll_after,
            protected_before,protected_after,reason,score_json,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            str(uuid.uuid4()), campaign['id'], _day_index(campaign, day), day, status,
            chosen.get('fixture_id'), chosen.get('sport'), chosen.get('market'), chosen.get('selection'),
            chosen.get('home'), chosen.get('away'), chosen.get('decimal_odds'), chosen.get('probability'),
            chosen.get('edge'), chosen.get('uncertainty'), chosen.get('completeness'), stake,
            campaign['bankroll'], campaign['bankroll'], campaign['protected_capital'], campaign['protected_capital'],
            reason, json.dumps({'score': chosen.get('score'), 'bookmaker': chosen.get('bookmaker')}), now.isoformat(),
        ),
    )


def settle_campaigns(connection, now=None):
    current = _now(now)
    settled = 0
    rows = connection.execute(
        """SELECT id, campaign_id, fixture_id, sport, market, decimal_odds, stake, campaign_date
           FROM sports_ai_rollover_days WHERE status='SELECTED' ORDER BY campaign_date, created_at""",
    ).fetchall()
    for row in rows:
        result = connection.execute(
            "SELECT home_score, away_score, status FROM sports_ai_results WHERE fixture_id=? AND status='final'",
            (row['fixture_id'],),
        ).fetchone()
        if not result:
            fixture = connection.execute('SELECT starts_at FROM sports_ai_fixtures WHERE id=?', (row['fixture_id'],)).fetchone()
            kickoff = _parse(fixture['starts_at']) if fixture else None
            if kickoff and kickoff > current:
                from qualification_engine import selection_is_qualified
                if not selection_is_qualified(connection, row['fixture_id'], row['market']):
                    connection.execute(
                        """UPDATE sports_ai_rollover_days
                           SET status='VOID', reason=?
                           WHERE id=? AND status='SELECTED'""",
                        ('The selection no longer has a current QUALIFIED decision, so the simulated day is void. No wager is placed.', row['id']),
                    )
            continue
        outcome = _outcome(row['sport'], row['market'], int(result['home_score']), int(result['away_score']))
        if outcome is None:
            continue
        campaign = connection.execute('SELECT * FROM sports_ai_rollover_campaigns WHERE id=?', (row['campaign_id'],)).fetchone()
        merged = dict(campaign)
        merged.update({'id': row['id'], 'campaign_id': row['campaign_id'], 'fixture_id': row['fixture_id'], 'sport': row['sport'], 'market': row['market'], 'decimal_odds': row['decimal_odds'], 'stake': row['stake']})
        _apply_outcome(connection, merged, outcome, current)
        settled += 1
    _close_elapsed(connection, current)
    return settled


def _outcome(sport, market, home, away):
    from accumulator_engine import settle_result
    settled = settle_result(sport, market, home, away)
    mapped = {'won': 'WON', 'lost': 'LOST', 'push': 'VOID', 'half_win': 'VOID', 'half_loss': 'VOID'}
    if settled in mapped:
        return mapped[settled]
    market = str(market or '').upper()
    if sport == 'football' and market == 'HOME':
        return 'WON' if home > away else 'LOST'
    if sport == 'football' and market == 'DRAW':
        return 'WON' if home == away else 'LOST'
    if sport == 'football' and market == 'AWAY':
        return 'WON' if away > home else 'LOST'
    if sport == 'basketball' and market == 'MONEYLINE_HOME':
        if home == away:
            return 'VOID'
        return 'WON' if home > away else 'LOST'
    if sport == 'basketball' and market == 'MONEYLINE_AWAY':
        if home == away:
            return 'VOID'
        return 'WON' if away > home else 'LOST'
    return None


def _apply_outcome(connection, row, outcome, now):
    bankroll = float(row['bankroll'])
    protected = float(row['protected_capital'])
    stake = float(row['stake'] or 0)
    odds = float(row['decimal_odds'] or 0)
    streak = int(row['consecutive_wins'])
    wins, losses = int(row['wins']), int(row['losses'])
    if outcome == 'WON' and odds > 1:
        if row['mode'] == 'FULL_ROLLOVER_SIMULATION':
            bankroll = stake * odds
        else:
            bankroll = bankroll + stake * (odds - 1)
            if bankroll >= float(row['opening_balance']) * float(row['profit_lock_multiple']):
                profit = max(0.0, bankroll - float(row['opening_balance']))
                locked = profit * float(row['protected_profit_fraction'])
                bankroll -= locked
                protected += locked
        streak += 1
        wins += 1
    elif outcome == 'LOST':
        bankroll = max(0.0, bankroll - stake)
        streak = 0
        losses += 1
    equity_now = bankroll + protected
    peak = max(float(row['peak_equity']), equity_now)
    drawdown = (peak - equity_now) / peak if peak else 0
    max_drawdown = max(float(row['max_drawdown']), drawdown)
    connection.execute(
        """UPDATE sports_ai_rollover_days
           SET status=?, bankroll_after=?, protected_after=? WHERE id=?""",
        (outcome, bankroll, protected, row['id']),
    )
    status = 'active'
    if outcome == 'LOST' and row['mode'] == 'FULL_ROLLOVER_SIMULATION':
        status = 'stopped'
    campaign = dict(row)
    campaign['bankroll'] = bankroll
    campaign['protected_capital'] = protected
    campaign['peak_equity'] = peak
    if stop_loss_hit(campaign) and row['mode'] == 'CAPITAL_CONTROLLED_SIMULATION':
        status = 'stopped'
    connection.execute(
        """UPDATE sports_ai_rollover_campaigns
           SET bankroll=?, protected_capital=?, peak_equity=?, consecutive_wins=?, max_consecutive_wins=?,
               max_drawdown=?, wins=?, losses=?, status=CASE WHEN status='completed' THEN status ELSE ? END, updated_at=?
           WHERE id=?""",
        (
            bankroll, protected, peak, streak, max(int(row['max_consecutive_wins']), streak),
            max_drawdown, wins, losses, status, now.isoformat(), row['campaign_id'],
        ),
    )


def _close_elapsed(connection, now):
    today = local_date(now).isoformat()
    for campaign in connection.execute("SELECT * FROM sports_ai_rollover_campaigns WHERE status='active'"):
        start = datetime.fromisoformat(campaign['start_date']).date()
        end = start + timedelta(days=int(campaign['length_days']) - 1)
        cursor = start
        while cursor < local_date(now) and cursor <= end:
            day = cursor.isoformat()
            if not connection.execute(
                'SELECT 1 FROM sports_ai_rollover_days WHERE campaign_id=? AND campaign_date=?',
                (campaign['id'], day),
            ).fetchone():
                _insert_day(connection, dict(campaign), day, 'SKIPPED_DAY', None, 0, 'The calendar day ended without a qualified selection.', now)
                connection.execute(
                    'UPDATE sports_ai_rollover_campaigns SET skipped=skipped+1, updated_at=? WHERE id=?',
                    (now.isoformat(), campaign['id']),
                )
            cursor += timedelta(days=1)
        if today > end.isoformat():
            _set_status(connection, campaign['id'], 'completed', now)


def _finish_if_elapsed(connection, campaign, now):
    start = datetime.fromisoformat(campaign['start_date']).date()
    end = start + timedelta(days=int(campaign['length_days']) - 1)
    if local_date(now) > end:
        _set_status(connection, campaign['id'], 'completed', now)


def _set_status(connection, campaign_id, status, now):
    connection.execute(
        'UPDATE sports_ai_rollover_campaigns SET status=?, updated_at=? WHERE id=?',
        (status, _now(now).isoformat(), campaign_id),
    )


def compare_campaigns(connection):
    rows = []
    for campaign in list_campaigns(connection):
        days = list_days(connection, campaign['id'])
        campaign['path_probability'] = campaign_path_probability(days)
        campaign['days_recorded'] = len(days)
        campaign['selections'] = sum(1 for day in days if day['status'] in ('SELECTED', 'WON', 'LOST', 'VOID'))
        rows.append(campaign)
    return rows


def overview(connection, now=None):
    current = _now(now)
    return {
        'schedule': describe_schedule(current),
        'warning': WARNING,
        'places_wager': False,
        'defaults': default_campaign(),
        'campaigns': compare_campaigns(connection),
    }


def process_rollover(connection, now=None):
    current = _now(now)
    settled = settle_campaigns(connection, current)
    selected = 0
    if selection_open(current) and _auto_due(connection, current):
        for campaign in connection.execute("SELECT id FROM sports_ai_rollover_campaigns WHERE status='active'"):
            result = run_day(connection, campaign['id'], current)
            if result.get('status') in ('SELECTED', 'SKIPPED_DAY'):
                selected += 1
        _mark_auto(connection, current)
    return {'settled': settled, 'selected': selected, 'selects_now': selection_open(current), 'places_wager': False, 'warning': WARNING}


def _auto_due(connection, now):
    row = connection.execute('SELECT value_json FROM sports_ai_settings WHERE key=?', (STATUS_KEY,)).fetchone()
    stored = json.loads(row['value_json']) if row else {}
    return stored.get('last_date') != local_date(now).isoformat()


def _mark_auto(connection, now):
    connection.execute(
        """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES(?,?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
        (STATUS_KEY, json.dumps({'last_date': local_date(now).isoformat()}), _now(now).isoformat()),
    )


def build_rollover_router(auth):
    from fastapi import APIRouter, Depends, HTTPException
    from pydantic import BaseModel, Field
    from db import connect

    router = APIRouter(prefix='/api/sports-ai/rollover', tags=['Daily rollover'])

    class CampaignIn(BaseModel):
        name: str = '30-day simulation'
        mode: str = 'CAPITAL_CONTROLLED_SIMULATION'
        length_days: int = Field(default=30, ge=1, le=366)
        opening_balance: float = 1000
        odds_target: float = 1.50
        odds_low: float = 1.40
        odds_high: float = 1.60
        stake_mode: str = 'fixed'
        fixed_stake: float = 100
        stake_fraction: float = 0.05
        max_stake_fraction: float = 0.10
        protected_profit_fraction: float = 0.50
        profit_lock_multiple: float = 1.50
        stop_loss_fraction: float = 0.20

    @router.get('/overview')
    def rollover_overview(_actor=Depends(auth)):
        with connect() as connection:
            return overview(connection)

    @router.get('/campaigns')
    def rollover_campaigns(_actor=Depends(auth)):
        with connect() as connection:
            return {'items': compare_campaigns(connection), 'warning': WARNING, 'places_wager': False}

    @router.post('/campaigns')
    def rollover_create(body: CampaignIn, _actor=Depends(auth)):
        try:
            with connect() as connection:
                return create_campaign(connection, body.model_dump())
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @router.get('/campaigns/{campaign_id}')
    def rollover_campaign(campaign_id: str, _actor=Depends(auth)):
        with connect() as connection:
            item = get_campaign(connection, campaign_id)
        if not item:
            raise HTTPException(404, 'Campaign not found')
        item['path_probability'] = campaign_path_probability(item['days'])
        return item

    @router.get('/campaigns/{campaign_id}/candidates')
    def rollover_candidates(campaign_id: str, _actor=Depends(auth)):
        with connect() as connection:
            campaign = connection.execute('SELECT * FROM sports_ai_rollover_campaigns WHERE id=?', (campaign_id,)).fetchone()
            if not campaign:
                raise HTTPException(404, 'Campaign not found')
            return load_candidates(connection, dict(campaign), local_date().isoformat())

    @router.post('/campaigns/{campaign_id}/run')
    def rollover_run(campaign_id: str, _actor=Depends(auth)):
        try:
            with connect() as connection:
                return run_day(connection, campaign_id)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error

    @router.get('/history')
    def rollover_history(_actor=Depends(auth)):
        with connect() as connection:
            return {'items': compare_campaigns(connection), 'warning': WARNING, 'places_wager': False}

    return router
