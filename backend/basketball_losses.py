"""Immutable bookmaker evidence, pre-match forecast audit and basketball release gates.

Bookmaker cash returns and forecast scoring are deliberately separate. Imported
screenshots are user-supplied evidence, never an official final-score provider.
"""
import hashlib
import json
import math
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone

POLICY = {'version': 1, 'minimum_live_fixtures': 30, 'maximum_ece': .15,
          'minimum_team_games': 10,
          'maximum_brier': .25, 'maximum_log_loss': .80,
          'minimum_holdout': 60, 'minimum_league_holdout': 30,
          'promotion_ece': .08, 'minimum_brier_improvement': .005,
          'minimum_ece_improvement': .01, 'minimum_calibration': 40}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def dumps(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def moment(value):
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Evidence timestamps must include a timezone')
    return parsed.astimezone(timezone.utc)


def family(market):
    key = str(market).upper()
    if key in ('MONEYLINE_HOME', 'MONEYLINE_AWAY'):
        return 'moneyline'
    if re.fullmatch(r'(OVER|UNDER)_\d+(?:\.\d+)?', key):
        return 'total'
    if re.fullmatch(r'(HOME|AWAY)_SPREAD_-?\d+(?:\.\d+)?', key):
        return 'spread'
    return 'unsupported'


def identity(value):
    return re.sub(r'[^a-z0-9]', '', str(value).casefold())


def event(c, action, actor, details, evidence_id=None):
    c.execute('INSERT INTO sports_ai_basketball_evidence_events VALUES(?,?,?,?,?,?)',
              (str(uuid.uuid4()), action, actor, evidence_id, dumps(details), now_iso()))


def metrics(rows):
    """One binary selection per row; push/void/partial outcomes are not binary labels."""
    binary = [r for r in rows if r.get('result') in ('won', 'lost')]
    scored = [r for r in binary if r.get('probability') is not None]
    bins = []
    n = len(scored)
    for i in range(10):
        bucket = [r for r in scored if min(9, int(float(r['probability'])*10)) == i]
        if bucket:
            bins.append({'lower': i/10, 'count': len(bucket),
                         'confidence': sum(r['probability'] for r in bucket)/len(bucket),
                         'frequency': sum(r['result']=='won' for r in bucket)/len(bucket)})
    errors, losses = [], []
    for r in scored:
        p = max(1e-12, min(1-1e-12, r['probability']))
        y = int(r['result']=='won')
        errors.append((p-y)**2)
        losses.append(-math.log(p if y else 1-p))
    staked = [r for r in rows if r.get('stake') is not None and r.get('total_return') is not None]
    stake = sum(r['stake'] for r in staked)
    returns = sum(r['total_return'] for r in staked)
    return {'selections': len(rows), 'binary_selections': len(binary), 'scored': n,
            'distinct_fixtures':len({r.get('fixture_id') or ((r.get('home'),r.get('away'),r.get('starts_at')) if r.get('home') else r.get('id',str(i))) for i,r in enumerate(rows)}),
            'win_rate': sum(r['result']=='won' for r in binary)/len(binary) if binary else None,
            'brier_score': sum(errors)/n if n else None, 'log_loss': sum(losses)/n if n else None,
            'calibration_error': sum(b['count']*abs(b['confidence']-b['frequency']) for b in bins)/n if n else None,
            'bins': bins, 'stake': stake if staked else None, 'total_return': returns if staked else None,
            'profit': returns-stake if staked else None, 'roi': (returns-stake)/stake if stake else None,
            'cash_observations': len(staked)}


def odds_range(odds):
    if odds is None:
        return 'unpriced'
    for low, high in ((1,1.5),(1.5,2),(2,3),(3,5),(5,10),(10,float('inf'))):
        if low <= odds < high:
            return f'{low:g}–{high:g}'
    return 'invalid'


def period_from_label(label):
    text=str(label or '').casefold()
    if re.search(r'\b(?:including|incl\.?)[\s(]+overtime\b',text): return 'including_overtime'
    if 'regulation' in text or 'excluding overtime' in text: return 'regulation'
    return 'unknown'


def _snapshot(c, leg, placed_at=None):
    fixture = None
    candidates = c.execute("SELECT * FROM sports_ai_fixtures WHERE sport=? AND ABS(julianday(starts_at)-julianday(?))<0.0035",
                           (leg['sport'], leg['starts_at'])).fetchall()
    exact = [r for r in candidates if identity(r['home'])==identity(leg['home']) and identity(r['away'])==identity(leg['away'])]
    issues = []
    if len(exact)==1:
        fixture = exact[0]
    elif any(identity(r['home'])==identity(leg['away']) and identity(r['away'])==identity(leg['home']) for r in candidates):
        issues.append('HOME_AWAY_IDENTITY_REVERSED')
    else:
        issues.append('FIXTURE_AMBIGUOUS' if len(exact)>1 else 'FIXTURE_UNMATCHED')
    snapshot = {'evidence_market': leg['market'], 'period': leg['period'], 'issues': issues,
                'candidates': [], 'original_prediction': None}
    if fixture:
        snapshots = c.execute(
            """SELECT p.id,p.probability,p.created_at,p.model_id,p.market,p.run_id,p.settlement_json,
                      m.version,m.model_name,m.training_run_id,m.parameters_json,m.metrics_json,m.lifecycle,m.calibration_status,
                      p.explanation_json,s.features_json,s.source_refs_json,s.cutoff_at
               FROM sports_ai_engine_predictions p JOIN sports_ai_model_registry m ON m.id=p.model_id
               JOIN sports_ai_feature_snapshots s ON s.id=p.feature_id
               WHERE p.fixture_id=? AND p.market=? AND julianday(p.created_at)<julianday(?)
               ORDER BY julianday(p.created_at) DESC LIMIT 50""",
            (fixture['id'], leg['market'], fixture['starts_at'])).fetchall()
        snapshot['candidates'] = [dict(r) for r in snapshots]
    attribution, prediction_id = 'unresolved', None
    # Only an explicit reference confirms attribution. A latest forecast is not
    # evidence that the bettor received that forecast or that price.
    supplied = leg.get('prediction_id')
    if supplied:
        chosen = next((r for r in snapshot['candidates'] if r['id']==supplied), None)
        if not chosen:
            raise ValueError('Referenced prediction does not match this pre-match fixture and market')
        if not placed_at or moment(chosen['created_at']) > moment(placed_at):
            raise ValueError('Confirmed prediction attribution requires a later bookmaker placed_at')
        quote = c.execute("""SELECT q.id,q.observed_at,q.market,q.outcome,q.line,q.decimal_odds,q.bookmaker,q.source_url,q.original_label,q.settlement_period FROM sports_ai_value_opportunities o
                             JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
                             WHERE o.prediction_id=? AND q.bookmaker='SportyBet' AND q.decimal_odds=?
                               AND julianday(q.observed_at)<=julianday(?) ORDER BY q.observed_at DESC LIMIT 1""",
                          (supplied, leg['decimal_odds'], placed_at)).fetchone()
        if not quote:
            raise ValueError('Referenced prediction has no matching SportyBet pre-bet price')
        if leg['sport']=='basketball' and (leg['period']=='unknown' or quote['settlement_period']!=leg['period']):
            raise ValueError('Prediction attribution requires matching original basketball overtime/regulation definitions')
        snapshot['original_prediction'] = dict(chosen)
        snapshot['original_quote'] = dict(quote)
        prediction_id, attribution = supplied, 'explicit_prediction_reference'
    elif snapshot['candidates']:
        snapshot['issues'].append('PREDICTION_ATTRIBUTION_UNCONFIRMED')
    return fixture, prediction_id, attribution, snapshot


def import_evidence(c, records, actor='operator'):
    if not records or len(records)>200:
        raise ValueError('Import 1–200 settled tickets per batch')
    inserted, existing = [], []
    for raw in records:
        record = dict(raw)
        required = ('external_ticket_id','source_kind','source_reference','currency','stake','total_return','outcome','selections')
        if any(record.get(k) is None for k in required):
            raise ValueError('Ticket evidence requires identity, provenance, currency, stake, return, outcome and selections')
        if record['source_kind'] not in ('sportybet_export','user_screenshot') or not str(record['source_reference']).strip():
            raise ValueError('A traceable SportyBet export or user screenshot reference is required')
        stake, returned = float(record['stake']), float(record['total_return'])
        if not math.isfinite(stake) or not math.isfinite(returned) or stake<=0 or returned<0:
            raise ValueError('Invalid actual bookmaker stake or return')
        if record['outcome'] not in ('won','lost','void','partial'):
            raise ValueError('Only completed bookmaker tickets can be imported')
        if record['outcome']=='lost' and returned!=0:
            raise ValueError('A lost ticket must have zero return')
        for k in ('placed_at','settled_at'):
            if record.get(k): moment(record[k])
        if record.get('settled_at') and record.get('placed_at') and moment(record['settled_at'])<moment(record['placed_at']):
            raise ValueError('Settlement cannot precede placement')
        if not record['selections'] or not any(l.get('sport')=='basketball' for l in record['selections']):
            raise ValueError('The ticket must contain a basketball selection')
        hashed = digest(record)
        old = c.execute('SELECT id,content_hash FROM sports_ai_basketball_bet_evidence WHERE external_ticket_id=?',
                        (str(record['external_ticket_id']),)).fetchone()
        if old:
            if old['content_hash']!=hashed:
                raise ValueError('Conflicting duplicate ticket. Original bookmaker evidence cannot be overwritten')
            existing.append(old['id']); continue
        identifier = str(uuid.uuid4())
        c.execute('INSERT INTO sports_ai_basketball_bet_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (identifier,str(record['external_ticket_id']),record['source_kind'],record['source_reference'],hashed,dumps(record),
                   record['currency'],stake,returned,record['outcome'],record.get('placed_at'),record.get('settled_at'),now_iso()))
        for index, leg in enumerate(record['selections']):
            if not leg.get('home') or not leg.get('away') or leg.get('home')==leg.get('away'):
                raise ValueError('Selection teams are incomplete')
            moment(leg['starts_at'])
            if record.get('placed_at') and moment(record['placed_at'])>=moment(leg['starts_at']):
                raise ValueError('Only pre-match placed bets can be attributed to this prediction audit')
            odds = float(leg['decimal_odds'])
            if not math.isfinite(odds) or odds<=1:
                raise ValueError('Selection odds must exceed one')
            if leg.get('period') not in ('including_overtime','regulation','unknown'):
                raise ValueError('Keep the original overtime/regulation market definition')
            if leg.get('result') not in ('won','lost','pending','push','void','half_win','half_loss'):
                raise ValueError('Invalid bookmaker selection settlement')
            if leg['sport']=='basketball' and family(leg['market'])=='unsupported':
                raise ValueError('Unsupported basketball market definition')
            if leg.get('home_score') is not None and leg.get('away_score') is not None and leg['result'] in ('won','lost'):
                if any(not math.isfinite(float(leg[k])) or float(leg[k])<0 or not float(leg[k]).is_integer() for k in ('home_score','away_score')):
                    raise ValueError('Final basketball scores must be finite nonnegative integers')
                from accumulator_engine import settle_result
                expected = settle_result(leg['sport'],leg['market'],float(leg['home_score']),float(leg['away_score']))
                if expected!=leg['result']:
                    raise ValueError('Bookmaker result conflicts with the supplied score and selection')
            fixture, prediction_id, attribution, snapshot = _snapshot(c, leg, record.get('placed_at'))
            c.execute('INSERT INTO sports_ai_basketball_bet_selections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (str(uuid.uuid4()),identifier,index+1,fixture['id'] if fixture else None,leg['home'],leg['away'],leg['sport'],
                       fixture['competition'] if fixture else leg.get('competition'),leg['starts_at'],leg['market'],leg['period'],odds,
                       leg['result'],leg.get('home_score'),leg.get('away_score'),prediction_id,attribution,dumps(snapshot)))
        event(c,'evidence.imported',actor,{'hash':hashed,'source_kind':record['source_kind']},identifier)
        inserted.append(identifier)
    return {'inserted':len(inserted),'duplicates':len(existing),'evidence_ids':inserted+existing}


def reconcile_selection(c, selection_id, prediction_id, placed_at, source_reference, actor):
    row=c.execute('SELECT * FROM sports_ai_basketball_bet_selections WHERE id=?',(selection_id,)).fetchone()
    if not row or row['sport']!='basketball' or not str(source_reference).strip():
        raise ValueError('Reconciliation needs an existing basketball selection and placement evidence')
    if moment(placed_at)>=moment(row['starts_at']):
        raise ValueError('Placement evidence must precede kickoff')
    leg={**dict(row),'prediction_id':prediction_id}
    _,_,_,snapshot=_snapshot(c,leg,placed_at)
    identifier=str(uuid.uuid4())
    c.execute('INSERT INTO sports_ai_basketball_reconciliations VALUES(?,?,?,?,?,?,?,?)',
              (identifier,selection_id,prediction_id,placed_at,source_reference,dumps(snapshot),actor,now_iso()))
    event(c,'evidence.reconciled',actor,{'selection_id':selection_id,'prediction_id':prediction_id,
                                     'source_reference':source_reference},row['evidence_id'])
    return {'reconciliation_id':identifier,'attribution':'explicit_prediction_reference'}


def historical_predictions(c):
    """Score the last stored pre-kickoff forecast per fixture, market and model.

    Every probability remains the original stored value, not a recalculation.
    These are forecast evaluations, never claimed as actual bets or cash ROI.
    """
    from accumulator_engine import settle_result
    rows = c.execute(
        """SELECT p.id,p.fixture_id,p.run_id,p.market,p.probability,p.model_id,p.created_at,p.explanation_json,
                  f.competition,f.home,f.away,f.starts_at,m.version,m.lifecycle,m.active,
                  r.home_score,r.away_score,r.settled_at,s.features_json,
                  (SELECT q.decimal_odds FROM sports_ai_value_opportunities o JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
                   WHERE o.prediction_id=p.id AND q.bookmaker='SportyBet' AND julianday(q.observed_at)<julianday(f.starts_at)
                   ORDER BY q.observed_at DESC LIMIT 1) decimal_odds
           FROM sports_ai_engine_predictions p JOIN sports_ai_fixtures f ON f.id=p.fixture_id
           JOIN sports_ai_model_registry m ON m.id=p.model_id
           JOIN sports_ai_feature_snapshots s ON s.id=p.feature_id
           LEFT JOIN sports_ai_fixture_identity_links l ON l.source_fixture_id=f.id AND l.status='confirmed'
           JOIN sports_ai_results r ON r.fixture_id=COALESCE(l.canonical_fixture_id,f.id) AND r.status='final'
           WHERE f.sport='basketball' AND julianday(p.created_at)<julianday(f.starts_at)
             AND julianday(r.settled_at)>julianday(f.starts_at)
           ORDER BY julianday(p.created_at) DESC,p.id DESC""").fetchall()
    unique = {}
    for raw in rows:
        row = dict(raw)
        key = row['fixture_id'],row['market'],row['model_id']
        if key in unique or family(row['market'])=='unsupported': continue
        row['result'] = settle_result('basketball',row['market'],row['home_score'],row['away_score'])
        row['features'] = json.loads(row.pop('features_json') or '{}')
        row['odds_range'] = odds_range(row['decimal_odds'])
        row['model_version'] = row['model_id']+':'+row['version']
        row['market_family'] = family(row['market'])
        unique[key] = row
    return list(unique.values())


def grouped(rows, key):
    groups = defaultdict(list)
    for row in rows: groups[str(row.get(key) or 'unresolved')].append(row)
    return [{'group':k,**metrics(v)} for k,v in sorted(groups.items())]


def diagnostics(rows):
    flags = defaultdict(list)
    for r in rows:
        f = r.get('features') or {}
        checks = {'sparse_team_history': min(f.get('home_games') or 0,f.get('away_games') or 0)<10,
                  'extreme_winner_confidence': r['market_family']=='moneyline' and r['probability']>=.9,
                  'model_market_disagreement': r.get('decimal_odds') and r['probability']-1/r['decimal_odds']>.15,
                  'rest_days_missing': f.get('home_rest_days') is None or f.get('away_rest_days') is None,
                  'player_availability_missing': f.get('injuries_out_home') is None or f.get('injuries_out_away') is None,
                  'pace_missing': f.get('home_pace') is None or f.get('away_pace') is None,
                  'form_missing': f.get('home_form') is None or f.get('away_form') is None,
                  'back_to_back': min(f.get('home_rest_days') or 99,f.get('away_rest_days') or 99)<1.5}
        for name, applies in checks.items():
            if applies: flags[name].append(r)
    return [{'flag':k,**metrics(v),'interpretation':'Association/coverage diagnostic; not proof of causation.'} for k,v in sorted(flags.items())]


def contradiction_key(market):
    market = str(market).upper()
    if family(market)=='moneyline': return ('moneyline',), market.endswith('HOME')
    match = re.fullmatch(r'(OVER|UNDER)_(\d+(?:\.\d+)?)',market)
    if match: return ('total',float(match[2])), match[1]=='OVER'
    match = re.fullmatch(r'(HOME|AWAY)_SPREAD_(-?\d+(?:\.\d+)?)',market)
    if match:
        line=float(match[2]); home=match[1]=='HOME'
        return ('spread',line if home else -line),home
    return (market,),None


def conflicting_groups(items):
    grouped_items = defaultdict(list)
    for item in items:
        row = item.get('row',item)
        if row.get('sport')!='basketball': continue
        market_key, side = contradiction_key(row.get('market'))
        if side is None: continue
        # Do not infer identity across ambiguous team matches.
        key=(row.get('canonical_fixture_id') or row.get('fixture_id'),row.get('period','including_overtime'),market_key)
        grouped_items[key].append((side,item))
    return [[item for side,item in group] for group in grouped_items.values() if len({side for side,item in group})>1]


def quarantine(c, model_id, competition, market_family, reason, evidence):
    previous=c.execute("""SELECT reason,evidence_json FROM sports_ai_basketball_quarantines
                          WHERE scope='performance' AND model_id=? AND competition=? AND market_family=?""",
                        (model_id,competition,market_family)).fetchone()
    c.execute("""INSERT INTO sports_ai_basketball_quarantines VALUES(?,'performance',?,?,?,?,?,1,?)
                 ON CONFLICT(scope,model_id,competition,market_family) DO UPDATE SET
                   reason=excluded.reason,evidence_json=excluded.evidence_json,active=1""",
              (str(uuid.uuid4()),model_id,competition,market_family,reason,dumps(evidence),now_iso()))
    if not previous or previous['reason']!=reason or previous['evidence_json']!=dumps(evidence):
        event(c,'model_or_market.quarantined','sports-ai-worker',{'model_id':model_id,'competition':competition,
                                                               'market_family':market_family,'reason':reason,'metrics':evidence})
    decisions=c.execute("""SELECT d.id,d.competition,d.market FROM sports_ai_qualification_decisions d
                           JOIN sports_ai_engine_predictions p ON p.id=d.prediction_id
                           WHERE d.sport='basketball' AND p.model_id=?""",(model_id,)).fetchall()
    for decision in decisions:
        if competition not in ('*',decision['competition']) or market_family not in ('*',family(decision['market'])):continue
        c.execute("""UPDATE sports_ai_qualification_decisions SET state='SUSPENDED',reason_code='BASKETBALL_QUARANTINE',
                     reason=?,products_json='[]' WHERE id=? AND (state!='SUSPENDED' OR reason_code!='BASKETBALL_QUARANTINE')""",(reason,decision['id']))
    model=c.execute('SELECT parameters_json,active FROM sports_ai_model_registry WHERE id=?',(model_id,)).fetchone()
    candidate=json.loads(model['parameters_json'] or '{}').get('basketball_loss_candidate') if model else None
    if candidate:
        c.execute("""UPDATE sports_ai_basketball_market_approvals SET status='quarantined',updated_at=?
                     WHERE candidate_id=? AND (?='*' OR competition=?) AND (?='*' OR market_family=?)""",
                  (now_iso(),candidate,competition,competition,market_family,market_family))
        remaining=c.execute("SELECT 1 FROM sports_ai_basketball_market_approvals WHERE candidate_id=? AND status='validated'",(candidate,)).fetchone()
        if not remaining and model['active']:
            c.execute("UPDATE sports_ai_model_registry SET active=0,status='inactive',lifecycle='suspended' WHERE id=?",(model_id,))
            restored=None
            for prior in c.execute("SELECT id,parameters_json FROM sports_ai_model_registry WHERE sport='basketball' AND id<>? AND activated_at IS NOT NULL ORDER BY activated_at DESC",(model_id,)).fetchall():
                old=json.loads(prior['parameters_json'] or '{}').get('basketball_loss_candidate')
                if old and c.execute("SELECT 1 FROM sports_ai_basketball_market_approvals WHERE candidate_id=? AND status='validated'",(old,)).fetchone():
                    c.execute("UPDATE sports_ai_model_registry SET active=1,status='active',lifecycle='active',activated_at=? WHERE id=?",(now_iso(),prior['id']))
                    restored=prior['id'];break
            event(c,'candidate.rolled_back','sports-ai-worker',{'model_id':model_id,'restored_model_id':restored,'reason':reason})


def eligibility_reason(c, model_id, competition, market):
    """Legacy and cross-league basketball estimates cannot silently enter tickets."""
    model = c.execute('SELECT parameters_json FROM sports_ai_model_registry WHERE id=?',(model_id,)).fetchone()
    params = json.loads(model['parameters_json'] or '{}') if model else {}
    candidate = params.get('basketball_loss_candidate')
    if not candidate:
        return 'Basketball requires a loss-audited candidate with passing out-of-sample league and market evidence.'
    held = c.execute("""SELECT reason FROM sports_ai_basketball_quarantines WHERE active=1
                      AND model_id IN ('*',?) AND competition IN ('*',?) AND market_family IN ('*',?) LIMIT 1""",
                     (model_id,competition,family(market))).fetchone()
    if held: return held['reason']
    approval = c.execute("""SELECT 1 FROM sports_ai_basketball_market_approvals
                           WHERE candidate_id=? AND competition=? AND market_family=?
                             AND period='including_overtime' AND status='validated'""",
                         (candidate,competition,family(market))).fetchone()
    return None if approval else 'No passing out-of-sample approval exists for this basketball league, market and overtime definition.'


def investigate(c, actor='sports-ai-worker'):
    rows = historical_predictions(c)
    # An active legacy model may still predict for evaluation, but its old
    # qualification decisions cannot survive the new evidence gate.
    for model in c.execute("SELECT id,parameters_json FROM sports_ai_model_registry WHERE sport='basketball'").fetchall():
        if not json.loads(model['parameters_json'] or '{}').get('basketball_loss_candidate'):
            quarantine(c,model['id'],'*','*','Legacy basketball model has no loss-audited league/market release.',{'release_gate':'unvalidated'})
    groups = defaultdict(list)
    for row in rows: groups[(row['model_id'],row['competition'],row['market_family'])].append(row)
    breaches=[];watchlist=[]
    for (model,league,market_family), samples in groups.items():
        score=metrics(samples)
        fixtures=len({r['fixture_id'] for r in samples})
        poor=score['scored'] and (score['calibration_error']>POLICY['maximum_ece'] or score['brier_score']>POLICY['maximum_brier'] or score['log_loss']>POLICY['maximum_log_loss'])
        if poor:
            watchlist.append({'model_id':model,'competition':league,'market_family':market_family,
                              'evidence_strength':'threshold_breach' if fixtures>=POLICY['minimum_live_fixtures'] else 'insufficient_distinct_fixtures',**score})
        if fixtures>=POLICY['minimum_live_fixtures'] and score['scored'] and (
            score['calibration_error']>POLICY['maximum_ece'] or score['brier_score']>POLICY['maximum_brier']
            or score['log_loss']>POLICY['maximum_log_loss']):
            reason='Settled pre-match basketball forecasts breached frozen reliability limits.'
            quarantine(c,model,league,market_family,reason,score)
            breaches.append({'model_id':model,'competition':league,'market_family':market_family,**score})
    report={'forecast_summary':metrics(rows),'by_league':grouped(rows,'competition'),
            'by_market':grouped(rows,'market'),'by_odds_range':grouped(rows,'odds_range'),
            'by_model':grouped(rows,'model_version'),'diagnostics':diagnostics(rows),'breaches':breaches,
            'forecast_count':len(rows),'policy':POLICY,'created_at':now_iso(),'watchlist':watchlist,
            'scoring_diagnostics':scoring_diagnostics(rows),
            'note':'Forecast replay uses original pre-kickoff probabilities and official finals. It is not actual betting ROI.'}
    hashed=digest({'report_schema':2,'forecasts':[(r['id'],r['probability'],r['home_score'],r['away_score']) for r in rows]})
    previous=c.execute('SELECT dataset_hash FROM sports_ai_basketball_loss_runs ORDER BY created_at DESC LIMIT 1').fetchone()
    if not previous or previous['dataset_hash']!=hashed:
        c.execute('INSERT INTO sports_ai_basketball_loss_runs VALUES(?,?,?,?,?)',
                  (str(uuid.uuid4()),hashed,dumps(POLICY),dumps(report),now_iso()))
        event(c,'investigation.completed',actor,{'dataset_hash':hashed,'breaches':len(breaches)})
    return report


def scoring_diagnostics(rows):
    unique={}
    for r in rows: unique.setdefault((r['fixture_id'],r['model_id']),r)
    groups=defaultdict(list)
    for r in unique.values(): groups[(r['model_id'],r['competition'])].append(r)
    output=[]
    for (model,league),values in groups.items():
        errors=[]
        for r in values:
            f=r['features'];h=f.get('expected_home_points');a=f.get('expected_away_points');sd=f.get('total_sd')
            if h is None or a is None:continue
            errors.append((h-r['home_score'],a-r['away_score'],sd))
        if not errors:continue
        n=len(errors)
        output.append({'model_id':model,'competition':league,'distinct_fixtures':n,
                       'mean_home_error':sum(h for h,a,s in errors)/n,'mean_away_error':sum(a for h,a,s in errors)/n,
                       'margin_rmse':math.sqrt(sum((h-a)**2 for h,a,s in errors)/n),
                       'total_rmse':math.sqrt(sum((h+a)**2 for h,a,s in errors)/n),
                       'margin_95pct_coverage':sum(abs(h-a)<=1.96*s for h,a,s in errors if s)/sum(bool(s) for h,a,s in errors) if any(s for h,a,s in errors) else None,
                       'note':'Original score feature errors; later fitted rate-model adjustments may differ. No injury causality is inferred.'})
    return output


def dashboard(c):
    latest=c.execute('SELECT report_json FROM sports_ai_basketball_loss_runs ORDER BY created_at DESC LIMIT 1').fetchone()
    forecast=json.loads(latest['report_json']) if latest else None
    bets=[dict(r) for r in c.execute('SELECT * FROM sports_ai_basketball_bet_evidence ORDER BY imported_at DESC')]
    selections=[]
    for raw in c.execute('SELECT * FROM sports_ai_basketball_bet_selections ORDER BY evidence_id,ordinal'):
        row=dict(raw); snap=json.loads(row.pop('snapshot_json')); prediction=snap.get('original_prediction')
        reconciled=c.execute('SELECT * FROM sports_ai_basketball_reconciliations WHERE selection_id=? ORDER BY created_at DESC,id DESC LIMIT 1',(row['id'],)).fetchone()
        if reconciled:
            snap=json.loads(reconciled['snapshot_json']);prediction=snap.get('original_prediction')
            row.update(attribution='explicit_prediction_reference',prediction_id=reconciled['prediction_id'],
                       reconciliation_reference=reconciled['source_reference'])
        row.update(snapshot=snap,probability=prediction['probability'] if prediction else None,
                   model_version=(prediction['model_id']+':'+prediction['version']) if prediction else 'unresolved',
                   odds_range=odds_range(row['decimal_odds']),market_family=family(row['market']))
        bet=next(b for b in bets if b['id']==row['evidence_id'])
        if len(json.loads(bet['payload_json'])['selections'])==1:
            row.update(stake=bet['stake'],total_return=bet['total_return'])
        if row['sport']=='basketball': selections.append(row)
    cash=[]
    for currency in sorted({b['currency'] for b in bets}):
        subset=[{**b,'result':b['outcome']} for b in bets if b['currency']==currency]
        cash.append({'currency':currency,**metrics(subset)})
    for b in bets: b.pop('payload_json')
    return {'actual_cash':cash,'actual_selections':metrics(selections),'settled_selections':selections,
            'trends':[{'created_at':r['created_at'],'metrics':json.loads(r['report_json'])['forecast_summary']} for r in c.execute('SELECT created_at,report_json FROM sports_ai_basketball_loss_runs ORDER BY created_at DESC LIMIT 20')],
            'tickets':bets,'actual_by_league':grouped(selections,'competition'),
            'actual_by_market':grouped(selections,'market'),'actual_by_odds_range':grouped(selections,'odds_range'),
            'actual_by_model':grouped(selections,'model_version'),'forecast_audit':forecast,
            'quarantines':[dict(r) for r in c.execute('SELECT * FROM sports_ai_basketball_quarantines WHERE active=1')],
            'candidates':[dict(r) for r in c.execute('SELECT id,dataset_hash,recipe_version,status,metrics_json,created_at FROM sports_ai_basketball_candidates ORDER BY created_at DESC LIMIT 10')],
            'events':[dict(r) for r in c.execute('SELECT * FROM sports_ai_basketball_evidence_events ORDER BY created_at DESC LIMIT 50')],
            'policy':POLICY,'eligibility':'Blocked unless a candidate passes exact league/market out-of-sample approval.',
            'notes':['Actual cash is reported per ticket and currency. Mixed-ticket ROI includes football legs.',
                     'Group ROI uses singles only; accumulator stakes are not allocated to legs.',
                     'User screenshots are traceable user-supplied evidence, not independent bookmaker verification.',
                     'Unconfirmed prediction links are excluded from actual-bet calibration scores.']}
