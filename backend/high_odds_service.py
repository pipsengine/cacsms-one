"""Durable, qualification-driven accumulator optimization. Never submits a wager.

Final selections are immutable snapshots. Independence is used only after the
existing dependency checks pass; calibration sensitivity is shown separately
from that estimate and is not a statistical confidence interval.
"""
import hashlib
import json
import math
import time
import threading
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

from accumulator_engine import (
    _blocked_fixtures, _candidate, _insert_ticket, _parse_time, _reject_reason,
    _ticket_payload, combine_ticket, default_config, load_opportunity_rows,
    selection_signature,
)
from ticket_diversity import event_keys, keys_for, unallocated

PRODUCT = 'autonomous_high_odds'
_WORK_LOCK=threading.Lock()
_BACKGROUND=None
_WAKE=threading.Event()
PRIMARY = '2-4.99'
JACKPOT = 'jackpot'
JACKPOT_TARGET = 2_000_000
JACKPOT_MAX = 2_200_000
JACKPOT_STAKE = 100
DAILY_TARGET = 5
DEFAULTS = dict(min_legs=2, max_legs=16, min_leg_odds=1.01, max_per_competition=1, max_per_team=1,
                min_probability=0.05, min_edge=0.01, min_completeness=0.5,
                min_model_reliability=0.8, odds_max_age_minutes=15, min_lead_minutes=30,
                beam_width=100, search_limit=12000, timeout_seconds=3,
                max_registration_attempts=3, registration_enabled=True, jackpot_max_legs=25)


def now_utc(now=None):
    return now or datetime.now(timezone.utc)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def audit(connection, action, details, ticket_id=None, event_id=None, now=None):
    connection.execute(
        'INSERT INTO sports_ai_high_odds_audit VALUES(?,?,?,?,?,?)',
        (str(uuid.uuid4()), ticket_id, event_id, action, encoded(details), now_utc(now).isoformat()),
    )


def config(connection):
    row = connection.execute("SELECT value_json FROM sports_ai_settings WHERE key='high_odds_optimizer'").fetchone()
    return {**default_config(), **DEFAULTS, **(json.loads(row[0]) if row else {}),
            'sport_mode': 'mixed', 'allow_same_fixture': 0, 'bookmakers': ['SportyBet']}


def categories(connection):
    return [dict(r) for r in connection.execute('SELECT * FROM sports_ai_high_odds_categories ORDER BY min_odds')]


def save_settings(connection, updates, category_updates, actor):
    current = config(connection)
    if set(updates) - DEFAULTS.keys():
        raise ValueError('Unknown optimizer setting')
    merged = {**current, **updates}
    for name, low, high in (
        ('min_legs',2,25),('max_legs',2,25),('jackpot_max_legs',2,25),('max_per_competition',1,1),
        ('min_leg_odds',1.01,1000),
        ('max_per_team',1,1),('min_probability',0.01,0.99),('min_edge',0.001,1),
        ('min_completeness',0.5,1),('min_model_reliability',0.8,1),
        ('odds_max_age_minutes',1,15),('min_lead_minutes',5,240),('beam_width',10,200),
        ('search_limit',100,50000),('timeout_seconds',0.1,10),('max_registration_attempts',1,5),
    ):
        value = float(merged[name])
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'{name} must be between {low} and {high}')
        if isinstance(DEFAULTS[name], int) and not value.is_integer():
            raise ValueError(f'{name} must be an integer')
    if merged['max_legs'] < merged['min_legs']:
        raise ValueError('Maximum selections must be at least the minimum')
    if merged['jackpot_max_legs'] < merged['min_legs']:
        raise ValueError('Jackpot maximum selections must be at least the minimum')
    if not isinstance(merged['registration_enabled'], bool):
        raise ValueError('registration_enabled must be boolean')
    known = {c['id']: c for c in categories(connection)}
    for item in category_updates:
        if item.get('id') not in known or set(item) - {'id','enabled','max_active'}:
            raise ValueError('Category updates support id, enabled and max_active')
        maximum = item.get('max_active',known[item['id']]['max_active'])
        if not isinstance(maximum,int) or not 1 <= maximum <= 10:
            raise ValueError('Category maximum active tickets must be 1–10')
        enabled = item.get('enabled',known[item['id']]['enabled'])
        if enabled not in (True,False,0,1):
            raise ValueError('Category enabled must be boolean')
    stamp = now_utc().isoformat()
    connection.execute("""INSERT INTO sports_ai_settings VALUES('high_odds_optimizer',?,?)
        ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
        (encoded({k:merged[k] for k in DEFAULTS}), stamp))
    for item in category_updates:
        previous = known[item['id']]
        connection.execute('UPDATE sports_ai_high_odds_categories SET enabled=?,max_active=? WHERE id=?',
            (int(item.get('enabled',previous['enabled'])),item.get('max_active',previous['max_active']),item['id']))
    audit(connection,'settings.updated',{'actor':actor,'settings':updates,'categories':category_updates})
    enqueue(connection,'settings.changed',{'version':stamp},'settings:'+stamp)
    return {'settings':config(connection),'categories':categories(connection)}


def enqueue(connection, event_type, payload, event_key, now=None):
    stamp = now_utc(now).isoformat()
    identifier = str(uuid.uuid4())
    created=connection.execute("""INSERT OR IGNORE INTO sports_ai_high_odds_events
        (id,event_key,event_type,payload_json,available_at,created_at) VALUES(?,?,?,?,?,?)""",
        (identifier,event_key,event_type,encoded(payload),stamp,stamp))
    if created.rowcount: _WAKE.set()
    return connection.execute('SELECT id FROM sports_ai_high_odds_events WHERE event_key=?',(event_key,)).fetchone()[0]


def eligible(connection, now=None, ignore_family=None, exclude_allocated=True):
    current = now_utc(now)
    settings = config(connection)
    rows = load_opportunity_rows(connection,current,keep_markets=True)
    blocked = _blocked_fixtures(connection)
    accepted, rejected = [], Counter()
    # Central decisions are necessary but the latest model, market and price
    # are checked again. No hard-coded list of sports or leagues is used here.
    enabled = {r[0] for r in connection.execute("SELECT code FROM sports_ai_sports WHERE prediction_status='modelled'")}
    for row in rows:
        reason = _reject_reason(row,settings,current,blocked,set(),set(),{'sportybet'},'mixed')
        metrics = json.loads(row.get('metrics_json') or '{}')
        error = metrics.get('calibration_error')
        if row['sport'] not in enabled:
            reason = 'Sport is disabled'
        if row.get('fixture_status') != 'scheduled':
            reason = 'Fixture is not scheduled'
        # A code generated minutes before kickoff is in play before anyone can use it.
        kickoff = _parse_time(row.get('starts_at'))
        if kickoff and kickoff <= current + timedelta(minutes=float(settings['min_lead_minutes'])):
            reason = 'Kickoff is too close to register and use a booking code'
        if row.get('model_sport')!=row['sport'] or row.get('model_lifecycle')!='active':
            reason = 'Prediction requires this sport’s activated model'
        from urllib.parse import urlsplit
        source=urlsplit(row.get('source_url') or '')
        if source.scheme!='https' or source.hostname not in ('sportybet.com','www.sportybet.com'):
            reason = 'Bookmaker quote has no verified SportyBet source URL'
        if row.get('calibration_status') != 'validated':
            reason = 'Model calibration is not validated'
        if error is None or not math.isfinite(float(error)) or not 0 <= float(error) <= 0.20:
            reason = 'A reliable calibration error is required'
        if int(metrics.get('holdout_fixtures') or 0)<10:
            reason = 'At least ten holdout fixtures are required for calibrated probabilities'
        if not 0 < float(row.get('probability') or 0) < 1:
            reason = 'Probability must be a finite estimate strictly between zero and one'
        if float(row.get('probability') or 0)*float(row.get('decimal_odds') or 0)-1 <= 0:
            reason = 'Selection expected value is not positive'
        if reason:
            rejected[reason] += 1
            continue
        leg = _candidate(row,current,settings)
        latest=connection.execute("""SELECT decimal_odds,observed_at FROM sports_ai_odds_snapshots
            WHERE fixture_id=? AND bookmaker=? AND market=? AND outcome=? AND line IS ?
            ORDER BY julianday(observed_at) DESC,rowid DESC LIMIT 1""",
            (leg['fixture_id'],leg['bookmaker'],leg['quote_market'],leg['quote_outcome'],leg['quote_line'])).fetchone()
        if not latest or abs(latest['decimal_odds']-leg['decimal_odds'])>0.000001:
            rejected['Latest bookmaker price requires requalification']+=1
            continue
        if leg['settlement_kind'] != 'binary':
            rejected['No validated joint payout model for a partial-settlement market'] += 1
            continue
        leg['calibration_error'] = float(error)
        leg['odds_source_url']=row['source_url']
        from fixture_reconciliation import _team_ids
        leg['team_keys']=[]
        for name in (leg['home'],leg['away']):
            identities=_team_ids(connection,leg['sport'],name)
            leg['team_keys'].extend((leg['sport'],'id:'+identity) for identity in identities)
            if not identities:leg['team_keys'].append((leg['sport'],name.casefold()))
        accepted.append(leg)
    free = unallocated(connection,accepted,current,ignore_family) if exclude_allocated else accepted
    rejected['Match already allocated to another active ticket'] += len(accepted)-len(free)
    # Keep market alternatives in the beam; event keys enforce one per match.
    return free, {key:value for key,value in rejected.items() if value}, len(rows)


def live_opportunities(connection, now=None, sport=None):
    current=now_utc(now)
    pool,rejected,total=eligible(connection,current,exclude_allocated=False)
    if sport: pool=[l for l in pool if l['sport']==sport]
    pool.sort(key=lambda l:(-l['probability'],-l['edge'],l['starts_at']))
    chosen=[]; seen=set()
    from command_view import market_parts
    for leg in pool:
        if event_keys(leg)&seen: continue
        seen.update(event_keys(leg)); family,selection=market_parts(leg['market'])
        chosen.append({**leg,'sport_label':leg['sport'].replace('_',' ').title(),
            'fixture':leg['home']+' vs '+leg['away'],'market':family,'selection':selection,
            'probability':round(100*leg['probability'],1),'odds':leg['decimal_odds'],'status':'Qualified'})
        if len(chosen)==10: break
    reasons=[dict(r) for r in connection.execute("""SELECT d.state,d.reason_code,COUNT(*) total
        FROM sports_ai_qualification_decisions d JOIN sports_ai_fixtures f ON f.id=d.fixture_id
        WHERE julianday(f.starts_at)>julianday(?) AND d.state!='QUALIFIED'
        GROUP BY d.state,d.reason_code ORDER BY total DESC LIMIT 8""",(current.isoformat(),))]
    return {'items':chosen,'generated_at':current.isoformat(),'qualified_inputs':total,
        'qualified_count':len({leg['fixture_id'] for leg in pool}),
        'exclusions':rejected,'qualification_reasons':reasons,
        'sports':[dict(r) for r in connection.execute('SELECT code,name FROM sports_ai_sports ORDER BY name')],
        'empty_reason':'No current qualified opportunity passed model, price freshness and data-quality checks.' if not chosen else None}


def inside(odds, category):
    if not category or not math.isfinite(odds):
        return False
    ceiling = JACKPOT_MAX if category.get('id') == JACKPOT else 1000
    return odds <= ceiling and category['min_odds'] <= round(odds,2) <= category['max_odds']


def category_settings(settings, category):
    return {**settings, 'max_legs': settings['jackpot_max_legs']} if category['id'] == JACKPOT else settings


def optimize(pool, category, settings):
    """Bounded beam search, ranking probability and conservative value first.

    Categories run with separate search budgets. The caller allocates winners
    only after searching alternatives, so a failed category cannot stop others.
    """
    deadline = time.monotonic()+float(settings['timeout_seconds'])
    pool = sorted(pool,key=lambda l:(-l['probability'],-l['edge'],l['fixture_id'],l['market']))
    beam = [((),1.0,1.0,frozenset(),frozenset(),frozenset())]
    found = {}
    explored = 0
    truncated = False
    for size in range(1,int(settings['max_legs'])+1):
        next_beam = []
        level_evaluations=0
        level_budget=max(100,int(settings['search_limit'])//int(settings['max_legs']))
        exhausted=False
        for indices, odds, prob, events, teams, leagues in beam:
            for index in range(indices[-1]+1 if indices else 0,len(pool)):
                if explored >= int(settings['search_limit']) or time.monotonic() >= deadline:
                    truncated = True
                    exhausted=True
                    break
                if level_evaluations>=level_budget:
                    truncated=True
                    break
                explored += 1
                level_evaluations += 1
                leg = pool[index]
                keys = event_keys(leg)
                team_keys = {tuple(key) for key in leg.get('team_keys',[(leg['sport'],str(n).casefold()) for n in (leg['home'],leg['away'])])}
                league = (leg['sport'],leg['competition'])
                # No joint model exists for shared fixtures, teams, or league
                # effects. Conservatively exclude those exposures entirely.
                if keys & events or team_keys & teams or league in leagues:
                    continue
                product = odds*leg['decimal_odds']
                ceiling = JACKPOT_MAX if category.get('id') == JACKPOT else 1000
                if not math.isfinite(product) or product>ceiling or round(product,2)>category['max_odds']:
                    continue
                indexes = indices+(index,)
                joint = prob*leg['probability']
                state = (indexes,product,joint,events|keys,teams|team_keys,leagues|{league})
                next_beam.append(state)
                if size >= int(settings['min_legs']) and inside(product,category):
                    legs = [pool[i] for i in indexes]
                    combined = combine_ticket(legs,settings)
                    if combined['probability_basis']!='independent' or combined['estimated_edge']<=0:
                        continue
                    combined['combined_odds']=product
                    combined['estimated_edge']=joint*product-1
                    lower = math.prod(max(0,l['probability']-l['calibration_error']) for l in legs)
                    confidence = min(l['reliability'] for l in legs)
                    score = joint*0.7 + max(-1,min(1,lower*product-1))*0.2 + confidence*0.1
                    signature = selection_signature(legs)
                    found[signature] = {**combined,'legs':legs,'signature':signature,
                        'lower_joint_probability':lower,'confidence':confidence,'risk':1-lower,
                        'score':score,'model_uncertainty':joint-lower,
                        'dependency_policy':'distinct fixtures, teams and competitions; conditional independence estimate'}
            if exhausted or level_evaluations>=level_budget:
                break
        # Multiple odds buckets preserve paths that can reach the range, even
        # when the most probable path initially has low combined odds.
        buckets = {}
        for state in next_beam:
            bucket = int(math.log(state[1])*12)
            buckets.setdefault(bucket,[]).append(state)
        for bucket in buckets.values():
            bucket.sort(key=lambda s:(-s[2],s[1]))
        beam = []
        for slot in range(int(settings['beam_width'])):
            for key in sorted(buckets):
                if slot < len(buckets[key]): beam.append(buckets[key][slot])
            if len(beam)>=int(settings['beam_width']): break
        beam = beam[:int(settings['beam_width'])]
        if exhausted or not beam:
            break
    results = sorted(found.values(),key=lambda t:(-t['score'],-t['joint_probability'],t['combined_odds']))
    return results, {'evaluated':explored,'truncated':truncated,'candidate_count':len(results)}


def persist_ticket(connection, candidate, category, event_id, settings, now):
    # Recheck the daily cap under the caller's write lock before reserving legs.
    if category['id']==JACKPOT and connection.execute("""SELECT COUNT(*)
        FROM sports_ai_high_odds_tickets WHERE category_id=?
        AND date(created_at,'+1 hour')=date(?,'+1 hour')""",(JACKPOT,now.isoformat())).fetchone()[0]>=DAILY_TARGET:
        return None
    snapshot = {'category_id':category['id'],'settings':{k:settings[k] for k in DEFAULTS},
        'legs':candidate['legs'],'combined_odds':candidate['combined_odds'],
        'joint_probability':candidate['joint_probability'],'expected_value':candidate['estimated_edge'],
        'confidence':candidate['confidence'],'model_uncertainty':candidate['model_uncertainty'],
        'lower_joint_probability':candidate['lower_joint_probability'],
        'risk':candidate['risk'],'dependency_policy':candidate['dependency_policy'],
        'stake_assumption':JACKPOT_STAKE if category['id']==JACKPOT else None}
    signature = digest({'category':category['id'],'legs':sorted(
        [(l['fixture_id'],l['market'],l['decimal_odds'],l['model_id'],l['model_version']) for l in candidate['legs']])})
    if connection.execute('SELECT 1 FROM sports_ai_high_odds_tickets WHERE signature=?',(signature,)).fetchone():
        return None
    ticket = {**candidate,'tier':category['min_odds'],'tier_id':category['id'],
        'tier_label':category['label'],'tier_mode':'live','status':'qualified','coverage':PRODUCT,
        'components':{'confidence':candidate['confidence'],'lower_joint_probability':candidate['lower_joint_probability']}}
    ticket_id = _insert_ticket(connection,None,ticket,settings,now,'created','Qualification event optimized this category')
    connection.execute('UPDATE sports_ai_accumulator_tickets SET finalized=1 WHERE id=?',(ticket_id,))
    connection.execute("""INSERT INTO sports_ai_high_odds_tickets
        (ticket_id,category_id,event_id,signature,snapshot_json,snapshot_hash,confidence,
         lower_joint_probability,risk,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (ticket_id,category['id'],event_id,signature,encoded(snapshot),digest(snapshot),candidate['confidence'],
         candidate['lower_joint_probability'],candidate['risk'],now.isoformat(),now.isoformat()))
    audit(connection,'ticket.finalized',{'snapshot_hash':digest(snapshot),'category':category['id']},ticket_id,event_id,now)
    return ticket_id


def generate_event(connection, event_id, now=None):
    current = now_utc(now)
    caller_transaction=connection.in_transaction
    settings = config(connection)
    pool, rejected, total = eligible(connection,current)
    reports = []
    # The 2.00–4.99 core is reserved first. Every other enabled category is
    # still searched in this same pass, using the selections the core did not take.
    for category in sorted(categories(connection),key=lambda c:(c['id']!=PRIMARY,c['priority'],c['min_odds'])):
        if connection.execute('SELECT 1 FROM sports_ai_high_odds_runs WHERE event_id=? AND category_id=?',(event_id,category['id'])).fetchone():
            continue
        # Search without a database write lock. Reserve the winning fixtures
        # atomically afterward, using the shared allocation guard.
        try:
            active = connection.execute("""SELECT COUNT(*) FROM sports_ai_high_odds_tickets h
                JOIN sports_ai_accumulator_tickets t ON t.id=h.ticket_id WHERE h.category_id=?
                AND t.status IN ('qualified','review_required','awaiting_odds','unverified_probability')""",(category['id'],)).fetchone()[0]
            slots = max(0,category['max_active']-active) if category['enabled'] else 0
            daily_created = connection.execute("""SELECT COUNT(*) FROM sports_ai_high_odds_tickets
                WHERE category_id=? AND date(created_at,'+1 hour')=date(?,'+1 hour')""",
                (category['id'],current.isoformat())).fetchone()[0]
            if category['id']==JACKPOT:
                slots=min(slots,max(0,DAILY_TARGET-daily_created))
            search_settings=category_settings(settings,category)
            report = {'rejections':rejected,'qualified_inputs':total,'unused_inputs':len(pool),
                'created_ids':[],'priority':category['priority'],'policy':'one_active_ticket_per_match'}
            candidates, search = optimize(pool,category,search_settings) if slots else ([],{'candidate_count':0,'evaluated':0,'truncated':False})
            report.update(search)
            if not connection.in_transaction: connection.execute('BEGIN IMMEDIATE')
            connection.execute('SAVEPOINT high_odds_category')
            used = set()
            for candidate in candidates:
                if len(report['created_ids'])>=slots: break
                if keys_for(candidate['legs']) & used: continue
                identifier = persist_ticket(connection,candidate,category,event_id,search_settings,current)
                if identifier:
                    report['created_ids'].append(identifier)
                    used.update(keys_for(candidate['legs']))
            remaining = [leg for leg in pool if not event_keys(leg)&used]
            status = 'created' if report['created_ids'] else 'disabled' if not category['enabled'] else 'capacity' if not slots else 'insufficient_eligible_data'
            if category['enabled'] and category['id']==JACKPOT and daily_created>=DAILY_TARGET:
                status='daily_target'
                report['reason']='Five jackpot tickets have already been generated for this Africa/Lagos day.'
            if status=='insufficient_eligible_data':
                report['reason']='No unused, calibrated, fresh-price combination passed this category. No ticket was forced.'
            connection.execute('INSERT INTO sports_ai_high_odds_runs VALUES(?,?,?,?,?,?,?,?)',
                (str(uuid.uuid4()),event_id,category['id'],status,len(candidates),len(report['created_ids']),encoded(report),current.isoformat()))
            reports.append({'category_id':category['id'],'status':status,**report})
            connection.execute('RELEASE high_odds_category')
            if not caller_transaction:connection.commit()
            pool=remaining
        except Exception as exc:
            if connection.in_transaction:
                try:
                    connection.execute('ROLLBACK TO high_odds_category')
                    connection.execute('RELEASE high_odds_category')
                except Exception:
                    if not caller_transaction:connection.rollback()
            report={'reason':str(exc)[:500],'created_ids':[]}
            connection.execute('INSERT INTO sports_ai_high_odds_runs VALUES(?,?,?,?,?,?,?,?)',
                (str(uuid.uuid4()),event_id,category['id'],'failed',0,0,encoded(report),current.isoformat()))
            audit(connection,'category.failed',{'category_id':category['id'],**report},event_id=event_id,now=current)
            reports.append({'category_id':category['id'],'status':'failed',**report})
            if not caller_transaction:connection.commit()
    return reports


def process_events(connection, now=None):
    current = now_utc(now)
    stamp = current.isoformat()
    if not connection.in_transaction: connection.execute('BEGIN IMMEDIATE')
    connection.execute("""UPDATE sports_ai_high_odds_events SET status='queued',locked_at=NULL,
        error='Recovered abandoned event lease' WHERE status='processing' AND julianday(locked_at)<julianday(?)""",
        ((current-timedelta(minutes=5)).isoformat(),))
    # A minute sweep recovers missed upstream notifications and model/expiry
    # changes. Business processing is still deduplicated in the durable outbox.
    enqueue(connection,'recovery.sweep',{},'sweep:'+current.strftime('%Y-%m-%dT%H:%M'),current)
    events = connection.execute("""SELECT * FROM sports_ai_high_odds_events WHERE status='queued'
        AND julianday(available_at)<=julianday(?) ORDER BY created_at LIMIT 100""",(stamp,)).fetchall()
    if not events: return {'status':'idle','events':0}
    # Coalesce a price-import burst into one optimization, retaining each event.
    event = events[-1]
    for item in events:
        connection.execute("UPDATE sports_ai_high_odds_events SET status='processing',locked_at=?,attempts=attempts+1 WHERE id=?",(stamp,item['id']))
    # Commit the lease so health readers see work in progress and a crashed
    # process can be recovered without losing upstream qualification events.
    connection.commit()
    try:
        reports = generate_event(connection,event['id'],current)
        for item in events:
            connection.execute("UPDATE sports_ai_high_odds_events SET status='completed',completed_at=?,error=NULL WHERE id=?",(stamp,item['id']))
        audit(connection,'event.completed',{'coalesced_events':len(events),'categories':len(reports)},event_id=event['id'],now=current)
        return {'status':'complete','events':len(events),'categories':reports}
    except Exception as exc:
        connection.rollback()
        attempt = int(event['attempts'])+1
        for item in events:
            connection.execute("""UPDATE sports_ai_high_odds_events SET status=?,error=?,available_at=?,locked_at=NULL WHERE id=?""",
                ('failed' if attempt>=5 else 'queued',str(exc)[:500],(current+timedelta(seconds=30*2**attempt)).isoformat(),item['id']))
        audit(connection,'event.failed',{'error':str(exc)[:500],'attempt':attempt},event_id=event['id'],now=current)
        return {'status':'failed','error':str(exc),'events':1}


def validate_snapshot(connection, ticket_id, now=None):
    current = now_utc(now)
    row = connection.execute("""SELECT h.*,t.family_id,t.status FROM sports_ai_high_odds_tickets h
        JOIN sports_ai_accumulator_tickets t ON t.id=h.ticket_id WHERE h.ticket_id=?""",(ticket_id,)).fetchone()
    if not row: return []
    if row['status']!='qualified': return ['Ticket is no longer qualified']
    snapshot = json.loads(row['snapshot_json'])
    if digest(snapshot)!=row['snapshot_hash']: return ['Final selection snapshot integrity failed']
    pool, _rejected, _total = eligible(connection,current,row['family_id'])
    available = {(l['fixture_id'],l['market']):l for l in pool}
    errors = []
    stored = [dict(l) for l in connection.execute('SELECT * FROM sports_ai_accumulator_legs WHERE ticket_id=?',(ticket_id,))]
    immutable = lambda legs: sorted((l['fixture_id'],l['market'],l['decimal_odds'],l['probability'],l['model_id']) for l in legs)
    if immutable(stored)!=immutable(snapshot['legs']): errors.append('Stored selections differ from the finalized snapshot')
    for leg in snapshot['legs']:
        current_leg = available.get((leg['fixture_id'],leg['market']))
        if not current_leg:
            errors.append(f"{leg['home']} vs {leg['away']}: qualification, market or current price is no longer valid")
        elif any(current_leg[key]!=leg[key] for key in ('decimal_odds','probability','model_id','model_version')):
            errors.append(f"{leg['home']} vs {leg['away']}: price or model changed; a new snapshot is required")
    category = connection.execute('SELECT * FROM sports_ai_high_odds_categories WHERE id=?',(row['category_id'],)).fetchone()
    if not inside(math.prod(l['decimal_odds'] for l in snapshot['legs']),dict(category) if category else None):
        errors.append('Combined odds are outside the configured category limits')
    if category and not category['enabled']:
        errors.append('This odds category is disabled')
    return errors


def monitor(connection, now=None):
    current = now_utc(now)
    changed = 0
    rows = connection.execute("""SELECT h.ticket_id,h.registration_state,t.status,MIN(l.starts_at) kickoff FROM sports_ai_high_odds_tickets h
        JOIN sports_ai_accumulator_tickets t ON t.id=h.ticket_id JOIN sports_ai_accumulator_legs l ON l.ticket_id=t.id
        WHERE t.status IN ('qualified','review_required','awaiting_odds','expired')
        AND NOT EXISTS (SELECT 1 FROM sports_ai_ticket_settlements s WHERE s.ticket_id=t.id)
        GROUP BY h.ticket_id""").fetchall()
    for row in rows:
        started = _parse_time(row['kickoff'])<=current
        if started:
            from accumulator_engine import _settle_if_ready
            legs=[dict(l) for l in connection.execute('SELECT * FROM sports_ai_accumulator_legs WHERE ticket_id=?',(row['ticket_id'],))]
            if _settle_if_ready(connection,row['ticket_id'],legs,current)=='settled':
                connection.execute("UPDATE sports_ai_high_odds_tickets SET registration_state='settled',updated_at=? WHERE ticket_id=?",(current.isoformat(),row['ticket_id']))
                audit(connection,'ticket.settled',{'source':'stored official final scores'},row['ticket_id'],now=current)
                changed+=1
                continue
        if row['status']=='expired' and row['registration_state'] in ('expired','monitoring'):continue
        coded = connection.execute("SELECT 1 FROM sports_ai_booking_codes WHERE accumulator_ticket_id=? AND verification_method='displayed'",(row['ticket_id'],)).fetchone()
        from booking_policy import load as booking_policy
        policy = booking_policy(connection)
        if started and not coded:
            from accumulator_engine import _trim_started_legs
            ticket = connection.execute('SELECT id,status FROM sports_ai_accumulator_tickets WHERE id=?',(row['ticket_id'],)).fetchone()
            legs = [dict(l) for l in connection.execute('SELECT * FROM sports_ai_accumulator_legs WHERE ticket_id=?',(row['ticket_id'],))]
            if ticket and _trim_started_legs(connection,dict(ticket),legs,current):
                audit(connection,'ticket.started_legs_dropped',{'remaining':len(legs)},row['ticket_id'],now=current)
                changed += 1
                continue
        # Under the booking policy a moved price is booked as displayed, not a reason to expire.
        errors = [] if coded or policy['accept_displayed_odds'] else validate_snapshot(connection,row['ticket_id'],current)
        if started or errors:
            # Started tickets remain eligible for the existing settlement worker.
            state = 'monitoring' if started and coded else 'expired'
            if state=='expired': connection.execute("UPDATE sports_ai_accumulator_tickets SET status='expired' WHERE id=?",(row['ticket_id'],))
            connection.execute("""UPDATE sports_ai_high_odds_tickets SET registration_state=?,last_error=?,updated_at=? WHERE ticket_id=?""",
                (state,'; '.join(errors) or 'Kickoff has passed',current.isoformat(),row['ticket_id']))
            connection.execute("""UPDATE sports_ai_booking_jobs SET status='FAILED',reason_code='TICKET_EXPIRED',error='Finalized ticket expired before registration',updated_at=?
                WHERE ticket_id=? AND status='QUEUED'""",(current.isoformat(),row['ticket_id']))
            if errors or not coded:
                audit(connection,'ticket.expired',{'problems':errors,'kickoff_passed':started},row['ticket_id'],now=current)
            changed += 1
    return changed


def queue_registration(connection, now=None):
    from sportybet_booking import create_job, ticket_selections
    current = now_utc(now)
    settings = config(connection)
    if not settings['registration_enabled']: return 0
    queued = 0
    rows = connection.execute("""SELECT h.* FROM sports_ai_high_odds_tickets h
        JOIN sports_ai_accumulator_tickets t ON t.id=h.ticket_id WHERE t.status='qualified'
        AND h.registration_state IN ('pending','retry') AND h.attempts<?
        AND (h.next_retry_at IS NULL OR julianday(h.next_retry_at)<=julianday(?))
        ORDER BY CASE WHEN h.category_id=? THEN 0 ELSE 1 END,h.ticket_no LIMIT 24""",
        (settings['max_registration_attempts'],current.isoformat(),PRIMARY)).fetchall()
    from booking_policy import load as booking_policy
    accept_displayed = booking_policy(connection)['accept_displayed_odds']
    for row in rows:
        problems = [] if accept_displayed else validate_snapshot(connection,row['ticket_id'],current)
        if problems: continue
        # The existing booking API and browser restrictions remain authoritative.
        try:
            job = create_job(connection,ticket_selections(connection,row['ticket_id']))
            connection.execute("UPDATE sports_ai_high_odds_tickets SET registration_state='queued',booking_job_id=?,updated_at=? WHERE ticket_id=?",
                (job['id'],current.isoformat(),row['ticket_id']))
            audit(connection,'registration.queued',{'job_id':job['id'],'duplicate':job['duplicate']},row['ticket_id'],now=current)
            queued += 1
        except ValueError as exc:
            registration_result(connection,row['ticket_id'],{'status':'FAILED','error':str(exc)},current)
    return queued


def registration_result(connection, ticket_id, result, now=None):
    current = now_utc(now)
    row = connection.execute('SELECT * FROM sports_ai_high_odds_tickets WHERE ticket_id=?',(ticket_id,)).fetchone()
    if not row: return
    if result.get('status') in ('QUEUED','PREPARING'): return
    if row['registration_state'] in ('registered','expired'): return
    verified = connection.execute("""SELECT code FROM sports_ai_booking_codes WHERE accumulator_ticket_id=?
        AND verification_method='displayed' AND TRIM(COALESCE(code,''))<>''""",(ticket_id,)).fetchone()
    attempts = row['attempts']+1
    maximum = config(connection)['max_registration_attempts']
    state = 'registered' if verified else 'retry' if attempts<maximum else 'failed'
    error = None if verified else result.get('error') or result.get('reason_code') or 'No displayed bookmaker code was verified'
    connection.execute("""UPDATE sports_ai_high_odds_tickets SET registration_state=?,attempts=?,next_retry_at=?,last_error=?,updated_at=? WHERE ticket_id=?""",
        (state,attempts,(current+timedelta(minutes=2**attempts)).isoformat() if state=='retry' else None,error,current.isoformat(),ticket_id))
    audit(connection,'registration.'+state,{'status':result.get('status'),'attempt':attempts,'error':error},ticket_id,now=current)


def check_displayed_prices(ticket_id, selections, seen):
    """Final guard before requesting a real code; legacy products are unchanged."""
    if not ticket_id: return
    from db import connect
    with connect() as c:
        row=c.execute('SELECT snapshot_json,category_id FROM sports_ai_high_odds_tickets WHERE ticket_id=?',(ticket_id,)).fetchone()
        if not row: return
        problems=validate_snapshot(c,ticket_id)
        snapshot=json.loads(row[0])
        category=c.execute('SELECT * FROM sports_ai_high_odds_categories WHERE id=?',(row['category_id'],)).fetchone()
    if problems: raise ValueError('; '.join(problems))
    actual={(item['fixture_id'],item.get('market')):float(item['page_odds']) for item in seen if item.get('page_odds') is not None}
    if len(selections)!=len(snapshot['legs']): raise ValueError('Finalized ticket cannot drop selections during registration')
    for leg in snapshot['legs']:
        price=actual.get((leg['fixture_id'],leg['market']))
        if price is None or abs(price-leg['decimal_odds'])>0.000001:
            raise ValueError('Displayed price changed; requalification and a new finalized snapshot are required')
    if not inside(math.prod(actual.values()),dict(category) if category else None):
        raise ValueError('Displayed combined odds are outside the configured category limits')


def requalify_displayed_ticket(connection, ticket_id, seen, now=None):
    """Replace a price-drifted ticket, preserving its immutable original snapshot."""
    from qualification_engine import revalidate
    from sportybet_research import _assess_value
    current = now_utc(now)
    row = connection.execute('''SELECT h.*,t.family_id FROM sports_ai_high_odds_tickets h
        JOIN sports_ai_accumulator_tickets t ON t.id=h.ticket_id WHERE h.ticket_id=?''', (ticket_id,)).fetchone()
    if not row:
        return None
    original = json.loads(row['snapshot_json'])
    if digest(original) != row['snapshot_hash']:
        raise ValueError('Final selection snapshot integrity failed')
    shown = {(item.get('fixture_id'),item.get('market')):item for item in seen}
    expected = {(leg['fixture_id'],leg['market']) for leg in original['legs']}
    if set(shown) != expected or len(seen) != len(expected):
        raise ValueError('Repricing requires the exact finalized selections on the page')
    category = connection.execute('SELECT * FROM sports_ai_high_odds_categories WHERE id=?', (row['category_id'],)).fetchone()
    if not category or not category['enabled']:
        raise ValueError('This odds category is disabled')
    prices = [float(shown[key]['page_odds']) for key in expected]
    if any(not math.isfinite(price) or price <= 1 for price in prices):
        raise ValueError('Displayed prices must be finite decimal odds above one')
    if not inside(math.prod(prices),dict(category)):
        raise ValueError('Displayed combined odds are outside the configured category limits')
    if connection.execute("SELECT 1 FROM sports_ai_booking_codes WHERE accumulator_ticket_id=? AND verification_method='displayed'", (ticket_id,)).fetchone():
        raise ValueError('A verified booking code already exists for this ticket')
    for leg in original['legs']:
        quote = connection.execute('''SELECT * FROM sports_ai_odds_snapshots
            WHERE fixture_id=? AND bookmaker='SportyBet' AND market=? AND outcome=? AND line IS ?
            ORDER BY julianday(observed_at) DESC LIMIT 1''',
            (leg['fixture_id'],leg['quote_market'],leg['quote_outcome'],leg.get('quote_line'))).fetchone()
        if not quote:
            raise ValueError('The original verified quote identity is unavailable')
        item = shown[(leg['fixture_id'],leg['market'])]
        if item.get('selection') is not None and item['selection'] != leg['selection']:
            raise ValueError('Displayed selection changed during repricing')
        identifier = str(uuid.uuid4())
        price = float(item['page_odds'])
        connection.execute('''INSERT INTO sports_ai_odds_snapshots
            (id,fixture_id,provider_id,bookmaker,market,outcome,line,decimal_odds,source_url,observed_at,evidence_json)
            VALUES(?,?,?,'SportyBet',?,?,?,?,?,?,?)''',
            (identifier,leg['fixture_id'],quote['provider_id'],leg['quote_market'],leg['quote_outcome'],
             leg.get('quote_line'),price,quote['source_url'],current.isoformat(),
             encoded({'source':'booking_page','previous_ticket_id':ticket_id})))
        _assess_value(connection,{'fixture_id':leg['fixture_id'],'canonical_market':leg['market']},identifier,price,current)
        connection.execute('''UPDATE sports_ai_value_opportunities SET data_completeness=?
            WHERE odds_id=?''', (leg.get('completeness'),identifier))
    revalidate(connection,'booking-page',current,fixture_ids=[leg['fixture_id'] for leg in original['legs']])
    pool,rejected,_total = eligible(connection,current,row['family_id'])
    available = {(leg['fixture_id'],leg['market']):leg for leg in pool}
    if not expected <= available.keys():
        raise ValueError('Displayed prices did not pass requalification: '+encoded(rejected))
    legs = [available[(leg['fixture_id'],leg['market'])] for leg in original['legs']]
    settings = category_settings(config(connection),dict(category))
    combined = combine_ticket(legs,settings)
    if combined['probability_basis'] != 'independent' or combined['estimated_edge'] <= 0:
        raise ValueError('Repriced ticket lacks a validated independent positive-value estimate')
    lower = math.prod(max(0,leg['probability']-leg['calibration_error']) for leg in legs)
    joint = combined['joint_probability']
    candidate = {**combined,'legs':legs,'signature':selection_signature(legs),
        'combined_odds':math.prod(leg['decimal_odds'] for leg in legs),
        'confidence':min(leg['reliability'] for leg in legs),'lower_joint_probability':lower,
        'risk':1-lower,'model_uncertainty':joint-lower,'score':joint,
        'dependency_policy':original['dependency_policy']}
    connection.execute('SAVEPOINT reprice_replacement')
    try:
        connection.execute("UPDATE sports_ai_accumulator_tickets SET status='expired',publication_state='REPRICED' WHERE id=?", (ticket_id,))
        replacement = persist_ticket(connection,candidate,dict(category),row['event_id'],settings,current)
        if not replacement:
            raise ValueError('A replacement snapshot could not be created within category limits')
        connection.execute("UPDATE sports_ai_high_odds_tickets SET registration_state='expired',last_error='Superseded by requalified displayed prices',updated_at=? WHERE ticket_id=?", (current.isoformat(),ticket_id))
        audit(connection,'ticket.repriced',{'previous_ticket_id':ticket_id,'replacement_ticket_id':replacement},replacement,now=current)
        connection.execute('RELEASE SAVEPOINT reprice_replacement')
        return replacement
    except BaseException:
        connection.execute('ROLLBACK TO SAVEPOINT reprice_replacement')
        connection.execute('RELEASE SAVEPOINT reprice_replacement')
        raise


def worker_tick(now=None):
    if not _WORK_LOCK.acquire(blocking=False): return {'status':'busy','events':0}
    try: return _worker_tick(now)
    finally: _WORK_LOCK.release()


def start_background(owner):
    """One optimizer lane per scheduler leader, independent of slow providers."""
    global _BACKGROUND
    if _BACKGROUND and _BACKGROUND.is_alive():return _BACKGROUND
    def run():
        from db import connect
        while True:
            try:
                with connect() as c:
                    lease=c.execute("SELECT owner,expires_at FROM sports_ai_worker_leases WHERE id='scheduler-leader'").fetchone()
                if lease and lease['owner']==owner and _parse_time(lease['expires_at'])>now_utc():
                    worker_tick()
            except Exception as exc:
                try:
                    with connect() as c:audit(c,'worker.failed',{'error':str(exc)[:500]})
                except Exception:
                    pass  # Database recovery is retried on the next wake-up.
            _WAKE.wait(10);_WAKE.clear()
    _BACKGROUND=threading.Thread(target=run,name='high-odds-events',daemon=True)
    _BACKGROUND.start()
    return _BACKGROUND


def _worker_tick(now=None):
    """Drain durable events, recover registration, and execute one permitted job."""
    from db import connect
    from sportybet_booking import execute_job
    import asyncio
    current = now_utc(now)
    with connect() as connection:
        from sportybet_booking import recover_stopped_bookings
        recover_stopped_bookings(connection,current)
        monitor(connection,current)
        result = process_events(connection,current)
        # Recover completions after a worker dies between booking and callback.
        for row in connection.execute("""SELECT h.ticket_id,j.status,j.error FROM sports_ai_high_odds_tickets h
            JOIN sports_ai_booking_jobs j ON j.id=h.booking_job_id WHERE h.registration_state='queued'
            AND j.status NOT IN ('QUEUED','PREPARING')""").fetchall():
            registration_result(connection,row['ticket_id'],dict(row),current)
        queue_registration(connection,current)
        job = connection.execute("""SELECT h.ticket_id,j.id FROM sports_ai_high_odds_tickets h
            JOIN sports_ai_booking_jobs j ON j.id=h.booking_job_id WHERE h.registration_state='queued' AND j.status='QUEUED'
            ORDER BY CASE WHEN h.category_id=? THEN 0 ELSE 1 END,h.ticket_no LIMIT 1""",(PRIMARY,)).fetchone()
        connection.execute("""INSERT INTO sports_ai_settings VALUES('high_odds_worker',?,?)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
            (encoded({'heartbeat_at':current.isoformat(),'last_status':result['status']}),current.isoformat()))
    if job:
        booked = asyncio.run(execute_job(job['id']))
        with connect() as connection: registration_result(connection,job['ticket_id'],booked)
        result['booking_status'] = booked['status']
    return result


def _category_for(odds, cats):
    for category in cats:
        if category['min_odds'] <= round(float(odds), 2) <= category['max_odds']:
            return category['id']
    return None


def coded_accumulator_tickets(connection, now=None, cats=None, limit=500):
    """Tickets from any builder with a SportyBet-displayed code, outside the optimizer table.

    Each is placed in the category its combined odds fall in and dated by when the
    code was verified. Tickets below 2.00 belong to no category and are left out.
    """
    current = now_utc(now)
    cats = cats or categories(connection)
    rows = connection.execute(
        """SELECT t.id,t.combined_odds,t.joint_probability,t.estimated_edge,t.leg_count,t.status,t.created_at ticket_created_at,
                  b.code,b.status code_status,b.verified_at,s.outcome,s.hypothetical_profit
           FROM sports_ai_booking_codes b
           JOIN sports_ai_accumulator_tickets t ON t.id=b.accumulator_ticket_id
           LEFT JOIN sports_ai_ticket_settlements s ON s.ticket_id=t.id
           WHERE b.verification_method='displayed' AND TRIM(COALESCE(b.code,''))<>'' AND b.verified_at IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM sports_ai_high_odds_tickets h WHERE h.ticket_id=t.id)
           ORDER BY b.verified_at DESC LIMIT ?""", (limit,),
    ).fetchall()
    tickets, seen_codes = [], set()
    for row in rows:
        if row['code'] in seen_codes:
            continue  # one row per displayed code (newest first)
        seen_codes.add(row['code'])
        category = _category_for(row['combined_odds'] or 0, cats)
        if not category:
            continue
        legs = [dict(l) for l in connection.execute(
            """SELECT home,away,competition,starts_at,market,selection,decimal_odds,probability,reliability model_reliability
               FROM sports_ai_accumulator_legs WHERE ticket_id=? ORDER BY starts_at""", (row['id'],),
        )]
        if not legs:
            continue
        start = min(l['starts_at'] for l in legs)
        started = _parse_time(start) and _parse_time(start) <= current
        state = 'settled' if row['outcome'] else 'monitoring' if started else 'registered'
        tickets.append({
            'ticket_id': row['id'], 'ticket_no': f"AC-{row['id'][:8].upper()}", 'category_id': category,
            'created_at': row['verified_at'], 'ticket_created_at': row['ticket_created_at'],
            'registration_state': state, 'status': row['status'], 'combined_odds': row['combined_odds'],
            'joint_probability': row['joint_probability'], 'estimated_edge': row['estimated_edge'],
            'leg_count': len(legs), 'legs': legs, 'start_at': start, 'booking_code': row['code'],
            'code_status': row['code_status'], 'outcome': row['outcome'], 'hypothetical_profit': row['hypothetical_profit'],
            'stake_assumption': None, 'source': 'accumulator',
        })
    return tickets


def dashboard(connection, now=None, date_from=None, date_to=None, status=None, category_id=None):
    current = now_utc(now)
    filters, args = [], []
    if date_from: filters.append('date(h.created_at)>=?'); args.append(date_from)
    if date_to: filters.append('date(h.created_at)<=?'); args.append(date_to)
    if status: filters.append('(h.registration_state=? OR t.status=?)'); args.extend([status,status])
    if category_id: filters.append('h.category_id=?'); args.append(category_id)
    where = (' WHERE '+' AND '.join(filters)) if filters else ''
    rows = connection.execute("""SELECT h.*,t.combined_odds,t.joint_probability,t.estimated_edge,t.leg_count,t.status,
        s.outcome,s.hypothetical_profit FROM sports_ai_high_odds_tickets h
        JOIN sports_ai_accumulator_tickets t ON t.id=h.ticket_id LEFT JOIN sports_ai_ticket_settlements s ON s.ticket_id=t.id"""+
        where+' ORDER BY h.ticket_no DESC LIMIT 500',args).fetchall()
    tickets=[]
    for row in rows:
        item=dict(row); snapshot=json.loads(item.pop('snapshot_json'))
        code=connection.execute("""SELECT code,status,verification_method FROM sports_ai_booking_codes
            WHERE accumulator_ticket_id=? AND verification_method='displayed' ORDER BY created_at DESC LIMIT 1""",(item['ticket_id'],)).fetchone()
        item.update(ticket_no=f"HO-{item['ticket_no']:06}",legs=snapshot['legs'],
            start_at=min(l['starts_at'] for l in snapshot['legs']),booking_code=code['code'] if code else None,
            code_status=code['status'] if code else None,model_uncertainty=snapshot['model_uncertainty'],
            dependency_policy=snapshot['dependency_policy'],stake_assumption=snapshot.get('stake_assumption'))
        tickets.append(item)
    cats=categories(connection)
    coded=coded_accumulator_tickets(connection,current,cats)
    if date_from: coded=[t for t in coded if t['created_at'][:10]>=date_from]
    if date_to: coded=[t for t in coded if t['created_at'][:10]<=date_to]
    if status: coded=[t for t in coded if status in (t['registration_state'],t['status'])]
    if category_id: coded=[t for t in coded if t['category_id']==category_id]
    tickets.extend(coded)
    today=current.astimezone(timezone(timedelta(hours=1))).date().isoformat()
    def wat_day(value):
        moment=_parse_time(value)
        return moment.astimezone(timezone(timedelta(hours=1))).date().isoformat() if moment else None
    summaries=[]
    for category in cats:
        stats=dict(connection.execute("""SELECT COUNT(*) total,SUM(CASE WHEN t.status='qualified' THEN 1 ELSE 0 END) active,
            AVG(t.joint_probability) avg_probability,COUNT(s.ticket_id) settled,
            SUM(CASE WHEN s.outcome='won' THEN 1 ELSE 0 END) wins,SUM(s.hypothetical_profit) hypothetical_profit
            FROM sports_ai_high_odds_tickets h JOIN sports_ai_accumulator_tickets t ON t.id=h.ticket_id
            LEFT JOIN sports_ai_ticket_settlements s ON s.ticket_id=t.id WHERE h.category_id=?""",(category['id'],)).fetchone())
        run=connection.execute('SELECT status,report_json,created_at FROM sports_ai_high_odds_runs WHERE category_id=? ORDER BY created_at DESC LIMIT 1',(category['id'],)).fetchone()
        daily=dict(connection.execute("""SELECT COUNT(*) daily_created,
            SUM(CASE WHEN registration_state IN ('registered','monitoring','settled') AND EXISTS(
                SELECT 1 FROM sports_ai_booking_codes b WHERE b.accumulator_ticket_id=h.ticket_id
                AND b.verification_method='displayed' AND TRIM(COALESCE(b.code,''))<>'') THEN 1 ELSE 0 END) daily_verified
            FROM sports_ai_high_odds_tickets h WHERE category_id=?
            AND date(created_at,'+1 hour')=date(?,'+1 hour')""",(category['id'],current.isoformat())).fetchone())
        extra=[t for t in coded if t['category_id']==category['id'] and wat_day(t['created_at'])==today]
        daily['daily_created']=(daily['daily_created'] or 0)+len(extra)
        daily['daily_verified']=(daily['daily_verified'] or 0)+len(extra)
        summaries.append({**category,**stats,**daily,'daily_target':DAILY_TARGET,
            'stake_assumption':JACKPOT_STAKE if category['id']==JACKPOT else None,'primary':category['id']==PRIMARY,
            'last_run':None if not run else {'status':run['status'],'created_at':run['created_at'],**json.loads(run['report_json'])}})
    queue={r['status']:r['n'] for r in connection.execute('SELECT status,COUNT(*) n FROM sports_ai_high_odds_events GROUP BY status')}
    registrations={r['registration_state']:r['n'] for r in connection.execute('SELECT registration_state,COUNT(*) n FROM sports_ai_high_odds_tickets GROUP BY registration_state')}
    worker=connection.execute("SELECT value_json FROM sports_ai_settings WHERE key='high_odds_worker'").fetchone()
    health=json.loads(worker[0]) if worker else {}
    heartbeat=_parse_time(health.get('heartbeat_at'))
    health['healthy']=bool(heartbeat and (current-heartbeat).total_seconds()<180)
    logs=[dict(r) for r in connection.execute('SELECT action,details_json,created_at,ticket_id FROM sports_ai_high_odds_audit ORDER BY created_at DESC LIMIT 20')]
    return {'generated_at':current.isoformat(),'primary_category':PRIMARY,'categories':summaries,'tickets':tickets,
        'settings':config(connection),'worker':health,'queue':queue,'registration_queue':registrations,'audit':logs,
        'kpis':{'total':sum(c['total'] for c in summaries),'active':sum(c['active'] or 0 for c in summaries),
            'registered':registrations.get('registered',0),'pending':sum(registrations.get(k,0) for k in ('pending','queued','retry')),
            'settled':sum(c['settled'] for c in summaries)},'places_wager':False,
        'note':'All nine categories are searched in the same pass. Regular categories retain a 1,000 combined-odds limit. Jackpot targets 2,000,000–2,200,000 with a hypothetical NGN100 stake and at most five new tickets per Africa/Lagos day. Each match is used on one active ticket. Only verified displayed bookmaker codes count as registered. No wager is placed.'}
