"""Match Results: only the bets the system itself placed, settled bet by bet.

A row is one bet of the Single Betting engine (a single, a combined selection or a coded
ticket), never a model pick that nobody bet on.

* Real money: the bet has a SUBMISSION order in sports_ai_single_live_orders. The engine
  never rewrites a bet's paper ``mode`` column, so the order is the only proof. Its result
  and payout come from SportyBet Bet History when reconciled; until then the result from
  the verified final score is shown and marked as awaiting SportyBet confirmation.
* Paper bets (rehearsed in the account, Place Bet not clicked), cancelled and still-queued
  bets were never placed and are not shown. Their rows stay in the database: rehearsals
  are what real-money authorization is granted on.

Figures are in naira on the stored stake. Nothing is estimated: an open bet has no result
and an unconfirmed submission is shown as unconfirmed, not as won or lost.
"""
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

REAL_ORDER = ('SUBMITTING', 'UNKNOWN', 'PLACED', 'SETTLED_WON', 'SETTLED_LOST', 'SETTLED_VOID')
# The page is used in Lagos (UTC+1, no daylight saving); a bet's "day" is the local kick-off date.
LOCAL_OFFSET = timedelta(hours=1)
PAGE_SIZES = (10, 25, 50, 100)
KIND_LABEL = {'SINGLE': 'Single', 'FAVOURITE': 'Single (favourite)', 'COMBO': 'Combined', 'TICKET': 'Ticket'}
KIND_GROUP = {'SINGLE': 'single', 'FAVOURITE': 'single', 'COMBO': 'combined', 'TICKET': 'ticket'}
SETTLED = ('Won', 'Lost', 'Void')


def local_day(starts_at):
    text = str(starts_at or '')
    try:
        moment = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return text[:10]
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (moment.astimezone(timezone.utc) + LOCAL_OFFSET).date().isoformat()


def _money(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _orders(connection):
    """Latest real-money submission per bet."""
    latest = {}
    for row in connection.execute(
        f"""SELECT * FROM sports_ai_single_live_orders
            WHERE kind='SUBMISSION' AND status IN ({','.join('?' * len(REAL_ORDER))})
            ORDER BY created_at""", REAL_ORDER,
    ):
        latest[row['bet_id']] = dict(row)
    return latest


def _codes(connection):
    codes = defaultdict(set)
    for row in connection.execute(
        """SELECT accumulator_ticket_id, code FROM sports_ai_booking_codes
           WHERE accumulator_ticket_id IS NOT NULL AND code IS NOT NULL AND trim(code)!=''""",
    ):
        codes[row['accumulator_ticket_id']].add(str(row['code']).strip().upper())
    return codes


def _result(bet, order):
    """(result, payout, profit, source, mismatch) for one real-money bet."""
    stake = _money(bet['stake']) or 0.0
    ours = {'WON': 'Won', 'LOST': 'Lost', 'VOID': 'Void'}.get(bet['status'])
    status = order['status']
    if status.startswith('SETTLED_'):
        theirs = status.replace('SETTLED_', '').title()
        payout = _money(order['payout'])
        if payout is None:
            payout = stake * (_money(bet['decimal_odds']) or 0) if theirs == 'Won' else (stake if theirs == 'Void' else 0.0)
        return theirs, round(payout, 2), round(payout - stake, 2), 'SportyBet Bet History', bool(ours and ours != theirs)
    if status in ('UNKNOWN', 'SUBMITTING'):
        return 'Unconfirmed', None, None, None, False
    if ours:
        return ours, _money(bet['payout']), _money(bet['profit']), 'Verified final score (awaiting SportyBet)', False
    return 'Awaiting result', None, None, None, False


def collect(connection):
    orders, codes = _orders(connection), _codes(connection)
    legs = defaultdict(list)
    for leg in connection.execute('SELECT * FROM sports_ai_single_bet_legs ORDER BY bet_id, leg_no'):
        legs[leg['bet_id']].append(dict(leg))
    rows = []
    for bet in connection.execute('SELECT * FROM sports_ai_single_bets ORDER BY created_at'):
        order = orders.get(bet['id'])
        if order is None:
            continue  # paper, queued or cancelled: never placed with money
        kind = bet['kind'] or 'SINGLE'
        bet_legs = legs.get(bet['id']) or [{
            'fixture_id': bet['fixture_id'], 'sport': bet['sport'], 'competition': bet['competition'],
            'home': bet['home'], 'away': bet['away'], 'market': bet['market'], 'selection': bet['selection'],
            'decimal_odds': bet['decimal_odds'], 'starts_at': bet['starts_at'], 'status': bet['status']}]
        last_kickoff = max(str(leg['starts_at']) for leg in bet_legs)
        result, payout, profit, source, mismatch = _result(bet, order)
        ticket_id = bet['id'][len('ticket:'):] if str(bet['id']).startswith('ticket:') else None
        first = bet_legs[0]
        sports = {leg['sport'] for leg in bet_legs}
        code_status = {'Won': 'WON', 'Lost': 'LOST', 'Void': 'VOID'}.get(result)
        rows.append({
            'id': bet['id'], 'date': local_day(last_kickoff), 'starts_at': last_kickoff,
            'kind': kind, 'kind_label': KIND_LABEL.get(kind, kind.title()), 'group': KIND_GROUP.get(kind, 'single'),
            'sport': first['sport'] if len(sports) == 1 else 'mixed', 'competition': first['competition'] or '—',
            'fixture': f"{first['home']} vs {first['away']}", 'extra_legs': len(bet_legs) - 1,
            'selection': bet['selection'], 'odds': _money(bet['decimal_odds']), 'stake': _money(bet['stake']),
            'codes': sorted(codes.get(ticket_id, ())) if ticket_id else [],
            'result': result, 'payout': payout, 'profit': profit,
            # Shown as the total return of a bet that has not settled yet (stake x accepted odds).
            'potential_return': None if result in SETTLED else round((_money(bet['stake']) or 0) * (_money(bet['decimal_odds']) or 0), 2),
            'result_source': source, 'mismatch': mismatch,
            'bookmaker_ref': order.get('bookmaker_ref'), 'order_status': order['status'],
            'placed_at': order.get('submitted_at') or order.get('created_at'),
            'settled_at': order.get('reconciled_at') if order['status'].startswith('SETTLED_') else bet['settled_at'],
            'legs': [{'fixture': f"{leg['home']} vs {leg['away']}", 'sport': leg['sport'], 'competition': leg['competition'],
                      'selection': leg['selection'], 'odds': _money(leg['decimal_odds']), 'starts_at': leg['starts_at'],
                      # One code = one result: a settled code's selections all show the code's result.
                      'status': code_status or leg.get('status')} for leg in bet_legs],
        })
    rows.sort(key=lambda row: (str(row['starts_at']), str(row['placed_at'] or '')), reverse=True)
    return rows


def metrics(rows):
    settled = [r for r in rows if r['result'] in SETTLED]
    won = sum(r['result'] == 'Won' for r in settled)
    lost = sum(r['result'] == 'Lost' for r in settled)
    staked_settled = sum(r['stake'] or 0 for r in settled)
    profit = sum(r['profit'] or 0 for r in settled)
    return {
        'placed': len(rows), 'settled': len(settled), 'won': won, 'lost': lost,
        'void': sum(r['result'] == 'Void' for r in settled),
        'awaiting': sum(r['result'] == 'Awaiting result' for r in rows),
        'unconfirmed': sum(r['result'] == 'Unconfirmed' for r in rows),
        'win_rate': won / (won + lost) if won + lost else None,
        # Staked, returned and profit cover the same (settled) codes, so returned - staked = profit.
        'staked': round(staked_settled, 2),
        'pending_staked': round(sum(r['stake'] or 0 for r in rows if r['result'] not in SETTLED), 2),
        'returned': round(sum(r['payout'] or 0 for r in settled), 2),
        # Per-outcome money for the Won / Lost / Awaiting cards.
        'won_returned': round(sum(r['payout'] or 0 for r in settled if r['result'] == 'Won'), 2),
        'won_profit': round(sum(r['profit'] or 0 for r in settled if r['result'] == 'Won'), 2),
        'lost_staked': round(sum(r['stake'] or 0 for r in settled if r['result'] == 'Lost'), 2),
        'awaiting_potential': round(sum(r.get('potential_return') or 0 for r in rows if r['result'] == 'Awaiting result'), 2),
        'avg_odds': (round(sum(priced) / len(priced), 2) if (priced := [r['odds'] for r in rows if r.get('odds')]) else None),
        'profit': round(profit, 2) if settled else None,
        'roi': profit / staked_settled if staked_settled else None,
    }


def _filter(rows, kind=None, sport=None, date_from=None, date_to=None):
    return [r for r in rows
            if (not kind or r['group'] == kind)
            and (not sport or r['sport'] == sport)
            and (not date_from or r['date'] >= date_from) and (not date_to or r['date'] <= date_to)]


def _account_state(connection, now):
    try:
        from sportybet_account import active_authorization, execution_mode
        grant = active_authorization(connection, now)
        return {'mode': execution_mode(connection), 'authorized': bool(grant),
                'authorization_expires': grant['expires_at'] if grant else None}
    except Exception:  # account tables missing on an old database
        return {'mode': 'PAPER', 'authorized': False, 'authorization_expires': None}


def placed_view(connection, kind=None, sport=None, date_from=None, date_to=None,
                page=1, page_size=25, now=None):
    current = now or datetime.now(timezone.utc)
    if kind and kind not in ('single', 'combined', 'ticket'):
        raise ValueError(f'Unknown bet kind: {kind}')
    for value in (date_from, date_to):
        if value:
            date.fromisoformat(value)
    rows = collect(connection)
    chosen = _filter(rows, kind, sport, date_from, date_to)
    stats = metrics(chosen)
    end_day = date.fromisoformat(date_to) if date_to else (current + LOCAL_OFFSET).date()
    window = _filter(rows, kind, sport, (end_day - timedelta(days=6)).isoformat(), end_day.isoformat())
    trend = []
    for offset in range(6, -1, -1):
        day = (end_day - timedelta(days=offset)).isoformat()
        cumulative = metrics([r for r in window if r['date'] <= day])
        trend.append({'date': day, 'accuracy': cumulative['win_rate'], 'realized_return': cumulative['roi'],
                      'settled': cumulative['settled']})
    size = int(page_size) if int(page_size) in PAGE_SIZES else 25
    pages = max(1, math.ceil(len(chosen) / size))
    page = min(max(1, int(page)), pages)
    settled_recent = sorted((r for r in chosen if r['result'] in SETTLED),
                            key=lambda r: str(r['settled_at'] or ''), reverse=True)[:6]
    return {
        'generated_at': current.isoformat(),
        'stats': stats, 'trend': trend,
        'breakdown': {group: metrics([r for r in chosen if r['group'] == group]) for group in ('single', 'combined', 'ticket')},
        'verification': {
            'confirmed_by_sportybet': sum(r['result_source'] == 'SportyBet Bet History' for r in chosen),
            'awaiting_sportybet': sum(r['order_status'] == 'PLACED' for r in chosen),
            'unconfirmed': stats['unconfirmed'],
            'mismatches': sum(r['mismatch'] for r in chosen),
            'awaiting_result': stats['awaiting'],
        },
        'activity': [{'fixture': r['fixture'], 'extra_legs': r['extra_legs'], 'selection': r['selection'],
                      'result': r['result'], 'odds': r['odds'], 'profit': r['profit'],
                      'at': r['settled_at']} for r in settled_recent],
        'rows': chosen[(page - 1) * size:page * size], 'total': len(chosen),
        'page': page, 'page_size': size, 'pages': pages,
        'totals': {'real': len(rows), 'all_time': metrics(rows)},
        'account': _account_state(connection, current),
        'options': {'sports': sorted({r['sport'] for r in rows})},
    }
