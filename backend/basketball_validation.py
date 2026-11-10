"""Frozen basketball recipe: league-specific score residuals and chronological calibration.

Moneyline uses margin residuals; totals use total residuals; spreads use margin
residuals with their signed handicap. Calibration is fitted on calibration rows
only. The final holdout cannot tune coefficients, variance, or calibration.
"""
import json
import math
import random
import re
import uuid
from collections import defaultdict

from basketball_losses import POLICY, digest, dumps, family, metrics, moment, now_iso, event
from prediction_math import fit_ridge, predict_ridge

RECIPE = 'basketball-loss-audit-v1'
CORE = ('expected_home_points','expected_away_points','home_rest_days','away_rest_days',
        'home_games_3d','away_games_3d','home_form','away_form')
OPTIONAL = ('home_pace','away_pace','injuries_out_home','injuries_out_away')


def cdf(value):
    return (1+math.erf(value/math.sqrt(2)))/2


def calibrated(p, temperature):
    p=min(1-1e-12,max(1e-12,p))
    x=math.log(p/(1-p))/temperature
    return 1/(1+math.exp(-max(-40,min(40,x))))


def fit_temperature(pairs):
    # Fixed grid; never consult final holdout labels here.
    choices=[.5+i*.05 for i in range(71)]
    return min(choices,key=lambda t:sum(-math.log(max(1e-12,calibrated(p,t) if y else 1-calibrated(p,t))) for p,y in pairs))


def predict_distribution(artifact, features):
    margin=predict_ridge(artifact['margin_model'],features)
    total=predict_ridge(artifact['total_model'],features)
    if margin is None or total is None: return None
    return {'margin':margin,'total':total,'margin_sd':artifact['margin_sd'],'total_sd':artifact['total_sd']}


def probability(artifact, features, market, apply_calibration=True):
    predicted=predict_distribution(artifact,features)
    if not predicted: return None
    key=market.upper(); which=family(key)
    if which=='moneyline':
        p=cdf(predicted['margin']/predicted['margin_sd'])
        if key=='MONEYLINE_AWAY': p=1-p
    elif which=='total':
        side,line=key.split('_'); line=float(line)
        p=cdf((predicted['total']-line)/predicted['total_sd'])
        if side=='UNDER': p=1-p
    elif which=='spread':
        side,_,line=key.split('_'); line=float(line)
        mean=predicted['margin'] if side=='HOME' else -predicted['margin']
        p=cdf((mean+line)/predicted['margin_sd'])
    else: return None
    # Push-capable lines need a separate multiclass calibration, not a binary
    # probability masquerading as settlement-aware expected value.
    if which in ('spread','total') and line.is_integer(): return None
    temperature=artifact.get('temperature',{}).get(which,1.0)
    return calibrated(p,temperature) if apply_calibration else p


def _observations(c):
    """Use frozen cutoff-safe feature datasets, not current team statistics."""
    latest=c.execute("""SELECT t.id FROM sports_ai_training_runs t WHERE t.sport='basketball'
                        AND EXISTS(SELECT 1 FROM sports_ai_training_datasets d WHERE d.training_run_id=t.id)
                        ORDER BY t.created_at DESC,t.id DESC LIMIT 1""").fetchone()
    if not latest: return []
    rows=c.execute("""SELECT d.fixture_id,d.kickoff_at,d.features_json,f.competition,f.home,f.away,r.home_score,r.away_score,r.settled_at
                     FROM sports_ai_training_datasets d JOIN sports_ai_fixtures f ON f.id=d.fixture_id
                     JOIN sports_ai_results r ON r.fixture_id=f.id AND r.status='final'
                     WHERE d.training_run_id=? ORDER BY julianday(d.kickoff_at),d.fixture_id""",(latest['id'],)).fetchall()
    unique={}
    for raw in rows:
        row=dict(raw); row['features']=json.loads(row.pop('features_json'))
        if moment(row['settled_at'])<=moment(row['kickoff_at']): continue
        # A final including-overtime score cannot be a tie.
        if row['home_score']==row['away_score']: continue
        if not all(isinstance(row['features'].get(k),(int,float)) and math.isfinite(row['features'][k]) for k in CORE): continue
        if min(row['features'].get('home_games') or 0,row['features'].get('away_games') or 0)<POLICY['minimum_team_games']: continue
        unique[row['fixture_id']]=row
    return sorted(unique.values(),key=lambda r:(moment(r['kickoff_at']),r['fixture_id']))


def split_rows(rows):
    """Keep simultaneous kickoffs in one partition and embargo unsettled labels."""
    if len(rows)<20: return [],[],[]
    first=moment(rows[int(len(rows)*.5)]['kickoff_at'])
    last=moment(rows[int(len(rows)*.7)]['kickoff_at'])
    train=[r for r in rows if moment(r['kickoff_at'])<first and moment(r['settled_at'])<first]
    calibration=[r for r in rows if first<=moment(r['kickoff_at'])<last and moment(r['settled_at'])<last]
    holdout=[r for r in rows if moment(r['kickoff_at'])>=last]
    return train,calibration,holdout


def _fit(rows):
    optional=[k for k in OPTIONAL if all(isinstance(r['features'].get(k),(int,float)) for r in rows)]
    columns=('intercept',)+CORE+tuple(optional)
    margin=fit_ridge([(r['features'],r['home_score']-r['away_score']) for r in rows],columns,'margin',penalty=10)
    total=fit_ridge([(r['features'],r['home_score']+r['away_score']) for r in rows],columns,'total',penalty=10)
    if not margin or not total: return None
    margin_errors=[r['home_score']-r['away_score']-predict_ridge(margin,r['features']) for r in rows]
    total_errors=[r['home_score']+r['away_score']-predict_ridge(total,r['features']) for r in rows]
    # Degrees of freedom account for fitted coefficients. A fixed one-point
    # floor guards degenerate imports; it is not tuned against winning bets.
    denominator=max(1,len(rows)-len(columns))
    return {'margin_model':margin,'total_model':total,
            'margin_sd':max(1,math.sqrt(sum(x*x for x in margin_errors)/denominator)),
            'total_sd':max(1,math.sqrt(sum(x*x for x in total_errors)/denominator)),
            'optional_features_used':optional,
            'unavailable_features':[k for k in OPTIONAL if k not in optional]}


def _markets(c, rows):
    """Only source-backed, unambiguous full-game quotes with a recorded period.

    No invented historical totals or handicap lines. Moneyline has a score
    benchmark even without a quote, but is explicitly excluded from release
    until a pre-match including-overtime quote supplies its definition.
    """
    result={}
    from prediction_engine import normalize_quote_market
    for r in rows:
        markets={'MONEYLINE_HOME':{'period':'unknown','odds':None}}
        quotes=c.execute("""SELECT * FROM sports_ai_odds_snapshots WHERE fixture_id=?
                            AND julianday(observed_at)<julianday(?) ORDER BY observed_at""",
                         (r['fixture_id'],r['kickoff_at'])).fetchall()
        for q in quotes:
            normalized=normalize_quote_market('basketball',{'home':r.get('home',''),'away':r.get('away','')},dict(q))
            if not normalized or family(normalized)=='unsupported': continue
            # Do not guess whether a provider's 'winner' means regulation.
            raw=str(q['market']).casefold()
            period=q['settlement_period']
            markets[normalized]={'period':period,'odds':q['decimal_odds']}
        result[r['fixture_id']]=markets
    return result


def paired_interval(rows, statistic='brier', draws=400):
    """Paired fixture bootstrap; repeated alternate lines do not inflate n."""
    groups=defaultdict(list)
    for r in rows: groups[r['fixture_id']].append(r)
    values=[]
    for group in groups.values():
        if statistic=='brier':
            values.append(sum((r['probability']-int(r['result']=='won'))**2-(r['baseline']-int(r['result']=='won'))**2 for r in group)/len(group))
        else:
            values.append(sum(-math.log(max(1e-12,r['probability'] if r['result']=='won' else 1-r['probability']))
                              +math.log(max(1e-12,r['baseline'] if r['result']=='won' else 1-r['baseline'])) for r in group)/len(group))
    if len(values)<2: return [None,None]
    rng=random.Random(4701)
    means=sorted(sum(values[rng.randrange(len(values))] for _ in values)/len(values) for _ in range(draws))
    return [means[int(draws*.025)],means[min(draws-1,int(draws*.975))]]


def _score(c, rows, artifact, market_map):
    from prediction_engine import market_probability
    from accumulator_engine import settle_result
    scored=[]
    for r in rows:
        for key,quote in market_map[r['fixture_id']].items():
            p=probability(artifact,r['features'],key)
            base=market_probability('basketball',key,r['features'])
            result=settle_result('basketball',key,r['home_score'],r['away_score'])
            if p is None or not base or result not in ('won','lost'): continue
            scored.append({'fixture_id':r['fixture_id'],'market':key,'market_family':family(key),'probability':p,
                           'baseline':base['p_win'],'result':result,'period':quote['period'],'odds':quote['odds']})
    return scored


def validate(c, actor='sports-ai-worker'):
    observations=_observations(c)
    quoted=[]
    if observations:
        identifiers=[r['fixture_id'] for r in observations]
        marks=','.join('?' for _ in identifiers)
        quoted=[dict(r) for r in c.execute(f"""SELECT q.id,q.fixture_id,q.market,q.outcome,q.line,q.decimal_odds,q.observed_at,q.settlement_period
                   FROM sports_ai_odds_snapshots q JOIN sports_ai_fixtures f ON f.id=q.fixture_id
                   WHERE q.fixture_id IN ({marks}) AND julianday(q.observed_at)<julianday(f.starts_at)
                   ORDER BY q.id""",identifiers)]
    hashed=digest({'observations':observations,'original_quotes':quoted,'policy':POLICY})
    old=c.execute('SELECT * FROM sports_ai_basketball_candidates WHERE dataset_hash=? AND recipe_version=?',(hashed,RECIPE)).fetchone()
    if old: return {'candidate_id':old['id'],'status':old['status'],'metrics':json.loads(old['metrics_json']),'reused':True}
    by_league=defaultdict(list)
    for r in observations: by_league[r['competition']].append(r)
    artifacts, reports, approvals={},[],[]
    for league,rows in sorted(by_league.items()):
        train,calibration,holdout=split_rows(rows)
        counts={'train':len(train),'calibration':len(calibration),'holdout':len(holdout)}
        report={'competition':league,'counts':counts,'available_fixtures':len(rows),'markets':[],
                'split_boundaries':{'train_last':train[-1]['kickoff_at'] if train else None,
                                    'calibration_first':calibration[0]['kickoff_at'] if calibration else None,
                                    'holdout_first':holdout[0]['kickoff_at'] if holdout else None}}
        if len(train)<100 or len(calibration)<POLICY['minimum_calibration'] or len(holdout)<POLICY['minimum_holdout']:
            report.update(status='insufficient_data',reason=f'Only {len(rows)} usable fixtures in this league; frozen chronological sample requirements not met.')
            reports.append(report);continue
        artifact=_fit(train)
        if not artifact:
            report.update(status='insufficient_data',reason='Scoring regressions could not be fitted.');reports.append(report);continue
        market_map=_markets(c,calibration+holdout)
        pairs=defaultdict(list)
        from accumulator_engine import settle_result
        for r in calibration:
            for market,quote in market_map[r['fixture_id']].items():
                p=probability(artifact,r['features'],market,False)
                result=settle_result('basketball',market,r['home_score'],r['away_score'])
                if p is not None and result in ('won','lost'):
                    pairs[family(market)].append((p,int(result=='won')))
        artifact['temperature']={key:fit_temperature(v) for key,v in pairs.items() if len(v)>=POLICY['minimum_calibration']}
        scored=_score(c,holdout,artifact,market_map)
        for market_family in ('moneyline','total','spread'):
            selected=[r for r in scored if r['market_family']==market_family]
            baseline=[{**r,'probability':r['baseline']} for r in selected]
            score,base=metrics(selected),metrics(baseline)
            ci=paired_interval(selected)
            fixture_count=len({r['fixture_id'] for r in selected})
            cal_count=len(pairs.get(market_family,[]))
            reasons=[]
            if fixture_count<POLICY['minimum_league_holdout']: reasons.append('Fewer than 30 distinct holdout fixtures for this market.')
            if cal_count<POLICY['minimum_calibration']: reasons.append('Insufficient market-specific calibration samples.')
            if not score['scored']: reasons.append('No source-backed binary lines to score.')
            else:
                if score['calibration_error']>POLICY['promotion_ece']: reasons.append('Holdout ECE exceeds 0.08.')
                if base['brier_score']-score['brier_score']<POLICY['minimum_brier_improvement']: reasons.append('Brier improvement below 0.005.')
                if base['calibration_error']-score['calibration_error']<POLICY['minimum_ece_improvement']: reasons.append('ECE improvement below 0.01.')
                if score['log_loss']>=base['log_loss']: reasons.append('Log loss does not improve on the legacy baseline.')
                if ci[1] is None or ci[1]>=0: reasons.append('Paired fixture bootstrap does not establish Brier improvement.')
            proven=[r for r in selected if r['period']=='including_overtime' and r.get('odds')]
            if len({r['fixture_id'] for r in proven})<POLICY['minimum_league_holdout']:
                reasons.append('Insufficient pre-match quotes with an explicit including-overtime definition.')
            detail={'family':market_family,'candidate':score,'legacy_baseline':base,'brier_delta_95pct':ci,
                    'distinct_holdout_fixtures':fixture_count,'calibration_samples':cal_count,
                    'status':'research' if reasons else 'validated','release_blockers':reasons}
            report['markets'].append(detail)
            approvals.append((league,market_family,detail))
        report.update(status='evaluated',temperatures=artifact['temperature'],
                      residual_sd={'margin':artifact['margin_sd'],'total':artifact['total_sd']},
                      unavailable_features=artifact['unavailable_features'])
        artifacts[league]=artifact;reports.append(report)
    identifier=str(uuid.uuid4())
    status='validated' if any(d['status']=='validated' for _,_,d in approvals) else 'research'
    summary={'leagues':reports,'observations':len(observations),'policy':POLICY,
             'recipe':RECIPE,'benchmark':'Original analytical basketball scoring distribution; not selected past winning tickets.',
             'selection':'One frozen recipe, fixed ridge penalty, per-league chronological 50/20/30, settlement embargo.',
             'release':'No automatic relaxation of sample, calibration, price-period or uncertainty gates.'}
    c.execute('INSERT INTO sports_ai_basketball_candidates VALUES(?,?,?,?,?,?,?)',
              (identifier,hashed,RECIPE,dumps(artifacts),dumps(summary),status,now_iso()))
    for league,market_family,detail in approvals:
        c.execute('INSERT INTO sports_ai_basketball_market_approvals VALUES(?,?,?,?,?,?,?)',
                  (identifier,league,market_family,'including_overtime',detail['status'],dumps(detail),now_iso()))
    event(c,'candidate.evaluated',actor,{'candidate_id':identifier,'dataset_hash':hashed,'status':status})
    return {'candidate_id':identifier,'status':status,'metrics':summary,'reused':False}


def candidate_settlement(c, candidate_id, competition, market, features):
    row=c.execute('SELECT artifact_json FROM sports_ai_basketball_candidates WHERE id=?',(candidate_id,)).fetchone()
    artifact=json.loads(row['artifact_json']).get(competition) if row else None
    p=probability(artifact,features,market) if artifact else None
    if p is None: return None
    return {'p_win':p,'p_half_win':0,'p_push':0,'p_half_loss':0,'p_loss':1-p}


def promote(c, candidate_id, actor='sports-ai-worker'):
    """Promote only frozen, passing artifacts; never waive a failed holdout."""
    candidate=c.execute('SELECT * FROM sports_ai_basketball_candidates WHERE id=?',(candidate_id,)).fetchone()
    approvals=c.execute("SELECT * FROM sports_ai_basketball_market_approvals WHERE candidate_id=? AND status='validated'",(candidate_id,)).fetchall()
    if not candidate or candidate['status']!='validated' or not approvals:
        raise ValueError('Candidate has no passing out-of-sample league/market approval')
    existing=c.execute("SELECT id FROM sports_ai_model_registry WHERE json_extract(parameters_json,'$.basketball_loss_candidate')=?",(candidate_id,)).fetchone()
    if existing: return {'model_id':existing['id'],'promoted':False,'reason':'Already registered; quarantines require a new holdout.'}
    details=[json.loads(r['evidence_json']) for r in approvals]
    n=sum(d['candidate']['scored'] for d in details)
    mean=lambda key:sum(d['candidate'][key]*d['candidate']['scored'] for d in details)/n
    base_loss=sum(d['legacy_baseline']['log_loss']*d['candidate']['scored'] for d in details)/n
    summary={'fixtures_scored':sum(d['distinct_holdout_fixtures'] for d in details),
             'holdout_fixtures':min(d['distinct_holdout_fixtures'] for d in details),
             'calibration_error':max(d['candidate']['calibration_error'] for d in details),
             'brier_score':mean('brier_score'),'log_loss':mean('log_loss'),
             'selected_family':'basketball_loss_audit',
             'family_log_loss':{'baseline':base_loss,'basketball_loss_audit':mean('log_loss')}}
    params={'basketball_loss_candidate':candidate_id,'basketball_loss_artifacts':json.loads(candidate['artifact_json']),
            'temperature':1,'selected_family':'basketball_loss_audit','dataset_hash':candidate['dataset_hash']}
    run_id,model_id=str(uuid.uuid4()),str(uuid.uuid4()); stamp=now_iso()
    version=RECIPE+':'+candidate_id[:8]; name='sports-ai-basketball-loss-audited'
    c.execute("""INSERT INTO sports_ai_training_runs(id,sport,model_name,version,status,training_window_json,parameters_json,metrics_json,sample_size,created_at)
                 VALUES(?,'basketball',?,?,'validated','{}',?,?,?,?)""",(run_id,name,version,dumps(params),dumps(summary),summary['fixtures_scored'],stamp))
    c.execute("UPDATE sports_ai_model_registry SET active=0,lifecycle='validated' WHERE sport='basketball' AND active=1")
    c.execute("""INSERT INTO sports_ai_model_registry(id,training_run_id,sport,model_name,version,market,status,active,parameters_json,metrics_json,
                 feature_schema_json,calibration_status,lifecycle,created_at,activated_at)
                 VALUES(?,?,'basketball',?,?,'match_outcome','active',1,?,?,'[]','validated','active',?,?)""",
              (model_id,run_id,name,version,dumps(params),dumps(summary),stamp,stamp))
    c.execute("""INSERT INTO sports_ai_calibration_results(id,model_id,market,method,sample_size,metrics_json)
                 VALUES(?,?,'match_outcome','chronological_market_temperature',?,?)""",
              (str(uuid.uuid4()),model_id,summary['holdout_fixtures'],dumps(summary)))
    c.execute("""INSERT INTO sports_ai_model_versions(id,model_id,version,status,parameters_json,metrics_json,artifact_ref,created_at)
                 VALUES(?,?,?,'validated',?,?,?,?)""",(str(uuid.uuid4()),model_id,version,dumps(params),dumps(summary),
                                                      'sqlite:sports_ai_basketball_candidates:'+candidate_id,stamp))
    event(c,'candidate.promoted',actor,{'candidate_id':candidate_id,'model_id':model_id,'approvals':len(approvals)})
    return {'model_id':model_id,'promoted':True}
