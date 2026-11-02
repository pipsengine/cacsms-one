import os, json, uuid, threading, hmac, math
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
from fastapi import FastAPI, APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from typing import Literal
from fastapi import BackgroundTasks
from db import connect, initialize, DB
from engine import evaluate, build_tickets
from acquisition import CollectionError, import_basketball_rows
from acquisition_api import build_acquisition_router
from acquisition_engine import run_collection, validate_public_config
from prediction_engine import run_analysis, train_model

app=FastAPI(title='CACSMS Sports AI module',version='1.0.0')
router=APIRouter(prefix='/api/sports-ai',tags=['Sports AI'])
lock=threading.RLock()
initialize()

def auth(request:Request,x_sports_dev_key:str|None=Header(default=None)):
    # Development-only standalone adapter. Replace with CACSMS ONE session dependency.
    if os.environ.get('SPORTS_LOCAL_PREVIEW')=='1' and request.client and request.client.host in ('127.0.0.1','::1'):
        return 'local-preview-user'
    key=os.environ.get('SPORTS_DEV_API_KEY')
    if not key or x_sports_dev_key!=key:
        raise HTTPException(401,'Configure SPORTS_DEV_API_KEY and supply X-Sports-Dev-Key, or replace auth with CACSMS ONE session authentication')
    return 'local-dev-user'

class FixtureIn(BaseModel):
    external_id:str=Field(min_length=1,max_length=160)
    sport:Literal['football','basketball']
    competition:str=Field(min_length=1,max_length=200)
    home:str=Field(min_length=1,max_length=200)
    away:str=Field(min_length=1,max_length=200)
    starts_at:datetime
    source:str=Field(min_length=1,max_length=120)
    statistics:dict[str,float]=Field(default_factory=dict)
    odds:dict[str,float]=Field(default_factory=dict)
    observed_at:datetime

class BookingCodeIn(BaseModel):
    code:str=Field(min_length=2,max_length=120)
    verification_method:Literal['manual']='manual'
    evidence:str=Field(min_length=1,max_length=500)

class ProviderUpdate(BaseModel):
    enabled:bool
    terms_reviewed:bool
    config:dict

class BasketballImport(BaseModel):
    rows:list[dict]

class OddsQuoteIn(BaseModel):
    fixture_id:str=Field(min_length=1,max_length=160)
    provider_id:str=Field(min_length=1,max_length=160)
    bookmaker:str=Field(min_length=1,max_length=160)
    market:str=Field(min_length=1,max_length=80)
    outcome:str=Field(min_length=1,max_length=160)
    line:float|None=None
    decimal_odds:float=Field(gt=1,le=1000)
    source_url:str=Field(min_length=1,max_length=2000)
    observed_at:datetime

class TrainingIn(BaseModel):
    sport:Literal['football','basketball']|None=None

def allrows(c,query,args=()):return [dict(x) for x in c.execute(query,args).fetchall()]
def audit(c,actor,action,details):
    c.execute('INSERT INTO sports_ai_audit_events(id,actor,action,metadata_json) VALUES(?,?,?,?)',(str(uuid.uuid4()),actor,action,json.dumps(details)))

def require_admin_key(value):
    expected=os.environ.get('SPORTS_ADMIN_API_KEY')
    if not expected or not value or not hmac.compare_digest(expected,value):
        raise HTTPException(403,'A separately configured SPORTS_ADMIN_API_KEY is required for this operation')

@router.get('/health')
def health():
    with connect() as c:
        operational=c.execute(
            "SELECT COUNT(*) FROM sports_ai_provider_registry WHERE status='operational' AND enabled=1"
        ).fetchone()[0]
        enabled=c.execute(
            'SELECT COUNT(*) FROM sports_ai_provider_registry WHERE enabled=1'
        ).fetchone()[0]
    return {'status':'running','database':'SQLite','db_file':DB.name,
            'live_provider':operational>0,'operational_providers':operational,
            'enabled_providers':enabled,'bookmaker_code_api':False}

@router.get('/dashboard')
def dashboard(user=Depends(auth)):
    with connect() as c:
        counts={}
        for key,table in [('fixtures','fixtures'),('tickets','tickets'),('booking_codes','booking_codes')]:counts[key]=c.execute('SELECT COUNT(*) FROM sports_ai_'+table).fetchone()[0]
        counts['predictions']=c.execute('SELECT COUNT(*) FROM sports_ai_engine_predictions').fetchone()[0]
        counts['qualified_opportunities']=c.execute("SELECT COUNT(*) FROM sports_ai_value_opportunities WHERE status='qualified'").fetchone()[0]
        counts['value_candidates']=counts['qualified_opportunities']
        counts['alerts_sent']=c.execute("SELECT COUNT(*) FROM sports_ai_notifications WHERE status='sent'").fetchone()[0]
        counts['fixtures_today']=c.execute("SELECT COUNT(*) FROM sports_ai_fixtures WHERE date(starts_at)=date('now')").fetchone()[0]
        counts['football_fixtures']=c.execute("SELECT COUNT(*) FROM sports_ai_fixtures WHERE sport='football' AND starts_at>=?",(datetime.now(timezone.utc).isoformat(),)).fetchone()[0]
        counts['basketball_fixtures']=c.execute("SELECT COUNT(*) FROM sports_ai_fixtures WHERE sport='basketball' AND starts_at>=?",(datetime.now(timezone.utc).isoformat(),)).fetchone()[0]
        counts['active_providers']=c.execute("SELECT COUNT(*) FROM sports_ai_provider_registry WHERE enabled=1 AND status='operational'").fetchone()[0]
        counts['failed_jobs_24h']=c.execute("SELECT COUNT(*) FROM sports_ai_job_history WHERE status='failed' AND started_at>=datetime('now','-1 day')").fetchone()[0]
        counts['recent_predictions']=allrows(c,
            """SELECT f.home||' vs '||f.away AS fixture,f.sport,p.market,
                      q.decimal_odds AS odds,p.probability,o.expected_edge AS edge
               FROM sports_ai_engine_predictions p
               JOIN sports_ai_fixtures f ON f.id=p.fixture_id
               LEFT JOIN sports_ai_value_opportunities o ON o.prediction_id=p.id
               LEFT JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
               ORDER BY p.created_at DESC LIMIT 10""")
        counts['recent_opportunities']=allrows(c,
            """SELECT f.home||' vs '||f.away AS fixture,f.sport,p.market,
                      q.decimal_odds AS odds,p.probability,o.expected_edge AS edge,o.status
               FROM sports_ai_value_opportunities o
               JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id
               JOIN sports_ai_fixtures f ON f.id=p.fixture_id
               LEFT JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
               WHERE o.status='qualified' ORDER BY o.evaluated_at DESC LIMIT 10""")
        row=c.execute('SELECT created_at FROM sports_ai_analysis_runs ORDER BY created_at DESC LIMIT 1').fetchone()
        counts['last_run']=row[0] if row else None
        return counts

@router.get('/fixtures')
def fixtures(sport:str|None=None,user=Depends(auth)):
    with connect() as c:
        q='SELECT id,external_id,sport,competition,home,away,starts_at AS kickoff_at,status,source,observed_at FROM sports_ai_fixtures'
        return allrows(c,q+' WHERE sport=? ORDER BY starts_at' if sport else q+' ORDER BY starts_at',(sport,) if sport else ())

@router.post('/fixtures/import')
def import_fixtures(items:list[FixtureIn],user=Depends(auth)):
    if len(items)>500:raise HTTPException(413,'Maximum 500 fixtures per import')
    now=datetime.now(timezone.utc)
    with lock,connect() as c:
        for f in items:
            if f.source.upper()=='DEMONSTRATION_ONLY':raise HTTPException(422,'Demonstration source cannot be imported as real data')
            if f.starts_at.tzinfo is None or f.observed_at.tzinfo is None:raise HTTPException(422,'Timezone-aware timestamps required')
            if any(not math.isfinite(v) or v<0 or v>10000 for v in f.statistics.values()):raise HTTPException(422,'Statistics must be finite values between 0 and 10000')
            if any(not math.isfinite(v) or v<=1 or v>10000 for v in f.odds.values()):raise HTTPException(422,'Odds must be finite values greater than 1 and no greater than 10000')
            row=c.execute('SELECT id FROM sports_ai_fixtures WHERE source=? AND external_id=?',(f.source,f.external_id)).fetchone()
            fid=row['id'] if row else str(uuid.uuid4())
            c.execute('INSERT INTO sports_ai_fixtures(id,external_id,source,sport,competition,home,away,starts_at,statistics_json,odds_json,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source,external_id) DO UPDATE SET sport=excluded.sport,competition=excluded.competition,home=excluded.home,away=excluded.away,starts_at=excluded.starts_at,statistics_json=excluded.statistics_json,odds_json=excluded.odds_json,observed_at=excluded.observed_at,updated_at=CURRENT_TIMESTAMP',(fid,f.external_id,f.source,f.sport,f.competition,f.home,f.away,f.starts_at.isoformat(),json.dumps(f.statistics),json.dumps(f.odds),f.observed_at.isoformat()))
        for f in items:
            fid=c.execute('SELECT id FROM sports_ai_fixtures WHERE source=? AND external_id=?',(f.source,f.external_id)).fetchone()['id']
            for market,odd in f.odds.items():
                c.execute('INSERT INTO sports_ai_odds_history(id,fixture_id,market,decimal_odds,source,observed_at) VALUES(?,?,?,?,?,?)',(str(uuid.uuid4()),fid,market,odd,f.source,f.observed_at.isoformat()))
        audit(c,user,'fixtures.import',{'count':len(items)})
    return {'imported':len(items)}

@router.get('/providers')
def providers(user=Depends(auth)):
    with connect() as c:
        return allrows(c,'SELECT id,provider_key,display_name,sport,adapter,enabled,terms_reviewed,status,config_json,last_success_at,last_error FROM sports_ai_provider_registry ORDER BY display_name')

@router.put('/providers/{provider_id}')
def provider_update(
    provider_id:str,payload:ProviderUpdate,
    x_sports_admin_key:str|None=Header(default=None),user=Depends(auth),
):
    require_admin_key(x_sports_admin_key)
    if not isinstance(payload.config,dict):
        raise HTTPException(422,'Provider configuration must be a JSON object')
    try:
        validate_public_config(payload.config)
    except CollectionError as error:
        raise HTTPException(422,str(error)) from error
    with connect() as c:
        provider=c.execute('SELECT * FROM sports_ai_provider_registry WHERE id=?',(provider_id,)).fetchone()
        if not provider:raise HTTPException(404,'Provider not found')
        c.execute(
            """UPDATE sports_ai_provider_registry SET enabled=?,terms_reviewed=?,
               config_json=?,status=CASE WHEN ?=0 THEN 'disabled' ELSE status END
               WHERE id=?""",
            (int(payload.enabled),int(payload.terms_reviewed),
             json.dumps(payload.config,allow_nan=False),int(payload.enabled),provider_id),
        )
        audit(c,user,'provider.configure',{'provider_id':provider_id,
              'enabled':payload.enabled,'terms_reviewed':payload.terms_reviewed})
        return dict(c.execute('SELECT * FROM sports_ai_provider_registry WHERE id=?',(provider_id,)).fetchone())

@router.post('/providers/{provider_id}/collect')
def provider_collect(provider_id:str,user=Depends(auth)):
    try:
        return run_collection(provider_id,actor=user)
    except CollectionError as error:
        raise HTTPException(502,str(error)) from error

@router.post('/providers/{provider_id}/basketball-import')
def provider_basketball_import(provider_id:str,payload:BasketballImport,user=Depends(auth)):
    with lock,connect() as c:
        provider=c.execute('SELECT * FROM sports_ai_provider_registry WHERE id=?',(provider_id,)).fetchone()
        if not provider:raise HTTPException(404,'Provider not found')
        if not provider['enabled'] or not provider['terms_reviewed']:
            raise HTTPException(409,'Provider is disabled or its dataset terms have not been reviewed')
        try:
            imported=import_basketball_rows(c,dict(provider),payload.rows)
        except CollectionError as error:
            raise HTTPException(422,str(error)) from error
        c.execute(
            """UPDATE sports_ai_provider_registry SET status='operational',
               last_success_at=?,last_error=NULL WHERE id=?""",
            (datetime.now(timezone.utc).isoformat(),provider_id),
        )
        audit(c,user,'provider.basketball.import',{'provider_id':provider_id,'imported':imported})
    return {'imported':imported}

@router.post('/odds/import')
def odds_import(items:list[OddsQuoteIn],user=Depends(auth)):
    if len(items)>1000:raise HTTPException(413,'Maximum 1000 odds quotes per import')
    allowed_markets={'h2h','moneyline','totals','total','spreads','spread','btts','both_teams_to_score'}
    with lock,connect() as c:
        for item in items:
            provider=c.execute(
                'SELECT * FROM sports_ai_provider_registry WHERE id=?',(item.provider_id,)
            ).fetchone()
            if not provider or not provider['enabled'] or not provider['terms_reviewed']:
                raise HTTPException(409,'Odds source must be enabled and terms-reviewed')
            fixture=c.execute('SELECT sport FROM sports_ai_fixtures WHERE id=?',(item.fixture_id,)).fetchone()
            if not fixture:raise HTTPException(404,'Fixture not found')
            if provider['sport'] not in ('both',fixture['sport']):
                raise HTTPException(422,'Odds provider is not configured for this sport')
            if item.observed_at.tzinfo is None:
                raise HTTPException(422,'Odds timestamps must include a timezone')
            if item.market.lower() not in allowed_markets:
                raise HTTPException(422,'Unsupported odds market')
            source=urlsplit(item.source_url)
            provider_config=json.loads(provider['config_json'])
            approved_host=provider_config.get('approved_host')
            required_host='api.the-odds-api.com' if provider['adapter']=='the_odds_api' else approved_host
            if (source.scheme!='https' or not source.hostname or source.username
                    or source.password or not required_host
                    or source.hostname.casefold()!=str(required_host).casefold()):
                raise HTTPException(422,'Odds source URL does not match the authorized HTTPS provider host')
            if source.query:
                raise HTTPException(422,'Odds source reference must not contain credentials or query parameters')
            c.execute(
                """INSERT OR IGNORE INTO sports_ai_odds_snapshots
                   (id,fixture_id,provider_id,bookmaker,market,outcome,line,decimal_odds,source_url,observed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()),item.fixture_id,item.provider_id,item.bookmaker,
                 item.market.lower(),item.outcome,item.line,item.decimal_odds,
                 item.source_url,item.observed_at.isoformat()),
            )
            c.execute(
                """INSERT INTO sports_ai_odds_history
                   (id,fixture_id,market,decimal_odds,source,observed_at)
                   VALUES(?,?,?,?,?,?)""",
                (str(uuid.uuid4()),item.fixture_id,
                 f'{item.market.lower()}:{item.outcome}:{item.line}',
                 item.decimal_odds,provider['provider_key'],item.observed_at.isoformat()),
            )
            c.execute(
                "UPDATE sports_ai_provider_registry SET status='operational',last_success_at=?,last_error=NULL WHERE id=?",
                (datetime.now(timezone.utc).isoformat(),item.provider_id),
            )
        audit(c,user,'odds.import',{'count':len(items)})
    return {'imported':len(items)}

@router.post('/legacy-analysis/run')
def analysis(user=Depends(auth)):
    now=datetime.now(timezone.utc)
    with lock,connect() as c:
        records=allrows(c,'SELECT * FROM sports_ai_fixtures')
        picks=[]
        for x in records:
            try:
                obs=datetime.fromisoformat(x['observed_at'])
                start=datetime.fromisoformat(x['starts_at'])
                if obs.tzinfo is None or start.tzinfo is None or now-obs>timedelta(minutes=60) or start<=now:continue
                e={'id':x['id'],'sport':x['sport'],'home':x['home'],'away':x['away'],'start':x['starts_at'],'source':x['source'],'stats':json.loads(x['statistics_json']),'odds':json.loads(x['odds_json'])}
                picks.extend(evaluate(e))
            except (ValueError,TypeError,KeyError,OverflowError):continue
        setting_row=c.execute("SELECT value_json FROM sports_ai_settings WHERE key='engine'").fetchone()
        cfg=json.loads(setting_row[0]) if setting_row else {'min_edge':0.05,'max_legs':6,'odds_tolerance':0.15}
        picks=[p for p in picks if p['edge']>=cfg['min_edge']]
        groups=build_tickets(picks,max_legs=cfg['max_legs'],per_tier=12,tolerance=cfg['odds_tolerance'])
        run_id=str(uuid.uuid4());count=sum(map(len,groups.values()))
        c.execute('INSERT INTO sports_ai_analysis_runs(id,fixture_count,selection_count,ticket_count,status,details_json) VALUES(?,?,?,?,?,?)',(run_id,len(records),len(picks),count,'complete',json.dumps({'freshness_minutes':60,'calibrated':False})))
        for p in picks:c.execute('INSERT INTO sports_ai_predictions(id,run_id,fixture_id,sport,fixture,market,odds,probability,edge,model_name) VALUES(?,?,?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),run_id,p['event_id'],p['sport'],p['match'],p['market'],p['odds'],p['probability'],p['edge'],'baseline-v1'))
        for group in groups.values():
            for t in group:
                tid=str(uuid.uuid4())
                c.execute('INSERT INTO sports_ai_tickets(id,run_id,tier,combined_odds,estimated_probability,estimated_ev,selection_count) VALUES(?,?,?,?,?,?,?)',(tid,run_id,t['target'],t['odds'],t['probability'],t['estimated_ev'],len(t['legs'])))
                for leg in t['legs']:c.execute('INSERT INTO sports_ai_ticket_legs(id,ticket_id,fixture_id,market,decimal_odds,estimated_probability) VALUES(?,?,?,?,?,?)',(str(uuid.uuid4()),tid,leg['event_id'],leg['market'],leg['odds'],leg['probability']))
        audit(c,user,'analysis.run',{'run_id':run_id,'picks':len(picks),'tickets':count})
    return {'run_id':run_id,'fixtures':len(records),'qualifying_picks':len(picks),'tickets':count,'model_calibrated':False}

@router.post('/training/run')
def training_run(payload:TrainingIn,user=Depends(auth)):
    sports=[payload.sport] if payload.sport else ['football','basketball']
    with lock,connect() as c:
        models=[train_model(c,sport) for sport in sports]
        audit(c,user,'models.training',{'sports':sports,
              'model_ids':[model['model_id'] for model in models]})
    return {'models':models}

@router.get('/models')
def model_registry(user=Depends(auth)):
    with connect() as c:
        return allrows(c,
            """SELECT m.*,t.training_window_json,t.sample_size AS training_sample_size,
                      c.sample_size AS calibration_sample_size
               FROM sports_ai_model_registry m
               JOIN sports_ai_training_runs t ON t.id=m.training_run_id
               LEFT JOIN sports_ai_calibration_results c ON c.model_id=m.id
               ORDER BY m.created_at DESC""")

@router.post('/models/{model_id}/activate')
def model_activate(
    model_id:str,x_sports_admin_key:str|None=Header(default=None),user=Depends(auth),
):
    require_admin_key(x_sports_admin_key)
    with lock,connect() as c:
        model=c.execute(
            """SELECT m.*,t.status AS training_status,c.sample_size AS calibration_sample_size
               FROM sports_ai_model_registry m
               JOIN sports_ai_training_runs t ON t.id=m.training_run_id
               LEFT JOIN sports_ai_calibration_results c ON c.model_id=m.id
               WHERE m.id=?""",(model_id,),
        ).fetchone()
        if not model:raise HTTPException(404,'Model not found')
        metrics=json.loads(model['metrics_json'])
        if (model['status']!='validated' or model['training_status']!='validated'
                or int(model['calibration_sample_size'] or 0)<10
                or metrics.get('calibration_error') is None
                or metrics['calibration_error']>.20):
            raise HTTPException(409,'Model has insufficient chronological validation or held-out calibration evidence')
        c.execute(
            "UPDATE sports_ai_model_registry SET active=0,status='inactive' WHERE sport=? AND active=1",
            (model['sport'],),
        )
        c.execute(
            """UPDATE sports_ai_model_registry SET active=1,status='active',activated_at=?
               WHERE id=?""",(datetime.now(timezone.utc).isoformat(),model_id),
        )
        audit(c,user,'models.activate',{'model_id':model_id,'sport':model['sport']})
    return {'activated':True,'model_id':model_id}

@router.post('/engine/run')
@router.post('/analysis/run')
def prediction_engine_run(user=Depends(auth)):
    with lock,connect() as c:
        return run_analysis(c,user,audit)

@router.get('/opportunities')
def opportunities(
    sport:Literal['football','basketball']|None=None,
    market:str|None=None,
    status:Literal['under_analysis','awaiting_odds','awaiting_calibration','qualified','rejected','expired','suspended']|None=None,
    bookmaker:str|None=None,
    competition:str|None=None,
    min_edge:float|None=Query(default=None,ge=-1,le=100),
    starts_after:datetime|None=None,
    starts_before:datetime|None=None,
    limit:int=Query(default=500,ge=1,le=1000),
    user=Depends(auth),
):
    conditions=[];args=[]
    if sport:conditions.append('f.sport=?');args.append(sport)
    if market:conditions.append('p.market=?');args.append(market)
    if status:conditions.append('o.status=?');args.append(status)
    if bookmaker:conditions.append('q.bookmaker=?');args.append(bookmaker)
    if competition:conditions.append('f.competition LIKE ?');args.append('%'+competition+'%')
    if min_edge is not None:conditions.append('o.expected_edge>=?');args.append(min_edge)
    if starts_after:
        if starts_after.tzinfo is None:raise HTTPException(422,'Date filter must include a timezone')
        conditions.append('f.starts_at>=?');args.append(starts_after.isoformat())
    if starts_before:
        if starts_before.tzinfo is None:raise HTTPException(422,'Date filter must include a timezone')
        conditions.append('f.starts_at<=?');args.append(starts_before.isoformat())
    where=' WHERE '+' AND '.join(conditions) if conditions else ''
    with connect() as c:
        return allrows(c,
            """SELECT o.id,o.status,o.reason,o.implied_probability,o.expected_edge,
                      q.decimal_odds,q.bookmaker,q.observed_at AS odds_observed_at,
                      (julianday('now')-julianday(q.observed_at))*1440 AS odds_age_minutes,
                      p.id AS prediction_id,p.market,p.outcome,p.probability,p.settlement_json,
                      p.explanation_json,p.created_at AS predicted_at,
                      f.id AS fixture_id,f.sport,f.competition,f.home,f.away,f.starts_at,
                      m.model_name,m.version AS model_version,m.status AS model_status,
                      m.metrics_json AS model_metrics,q.source_url
               FROM sports_ai_value_opportunities o
               JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id
               JOIN sports_ai_fixtures f ON f.id=p.fixture_id
               JOIN sports_ai_model_registry m ON m.id=p.model_id
               LEFT JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id"""
            +where+' ORDER BY CASE o.status WHEN "qualified" THEN 0 ELSE 1 END,'
            +'o.expected_edge DESC,o.evaluated_at DESC LIMIT ?',tuple(args+[limit]))

@router.get('/fixtures/{fixture_id}/analysis')
def fixture_analysis(fixture_id:str,user=Depends(auth)):
    with connect() as c:
        if not c.execute('SELECT 1 FROM sports_ai_fixtures WHERE id=?',(fixture_id,)).fetchone():
            raise HTTPException(404,'Fixture not found')
        return allrows(c,
            """SELECT p.*,m.model_name,m.version AS model_version,m.status AS model_status,
                      m.metrics_json AS model_metrics,f.features_json,f.source_refs_json,
                      f.generated_at AS features_generated_at
               FROM sports_ai_engine_predictions p
               JOIN sports_ai_model_registry m ON m.id=p.model_id
               JOIN sports_ai_feature_snapshots f ON f.id=p.feature_id
               WHERE p.fixture_id=? ORDER BY p.created_at DESC,p.market""",(fixture_id,))

@router.get('/predictions')
def predictions(user=Depends(auth)):
    with connect() as c:
        rows=allrows(c,
            """SELECT p.id,p.fixture_id,p.sport,p.market,p.probability,
                      f.home||' vs '||f.away AS fixture,f.starts_at,
                      m.model_name,o.decimal_odds AS odds,o.expected_edge AS edge,
                      o.status AS opportunity_status
               FROM sports_ai_engine_predictions p
               JOIN sports_ai_fixtures f ON f.id=p.fixture_id
               JOIN sports_ai_model_registry m ON m.id=p.model_id
               LEFT JOIN sports_ai_value_opportunities o ON o.prediction_id=p.id
               ORDER BY p.created_at DESC LIMIT 500""")
        if rows:return rows
        return allrows(c,'SELECT * FROM sports_ai_predictions ORDER BY rowid DESC LIMIT 500')

@router.get('/tickets')
def tickets(tier:int|None=Query(None),user=Depends(auth)):
    with connect() as c:
        q='SELECT t.*,b.code AS booking_code,b.status AS booking_status FROM sports_ai_tickets t LEFT JOIN sports_ai_booking_codes b ON b.ticket_id=t.id'
        return allrows(c,q+(' WHERE tier=?' if tier else '')+' ORDER BY t.rowid DESC LIMIT 500',(tier,) if tier else ())

@router.post('/tickets/{ticket_id}/booking-code')
def booking_code(ticket_id:str,payload:BookingCodeIn,user=Depends(auth)):
    with lock,connect() as c:
        if not c.execute('SELECT 1 FROM sports_ai_tickets WHERE id=?',(ticket_id,)).fetchone():raise HTTPException(404,'Ticket not found')
        c.execute('INSERT INTO sports_ai_booking_codes(id,ticket_id,code,verification_method,evidence,status) VALUES(?,?,?,?,?,?) ON CONFLICT(ticket_id) DO UPDATE SET code=excluded.code,evidence=excluded.evidence,status=excluded.status',(str(uuid.uuid4()),ticket_id,payload.code,'manual',payload.evidence,'unverified'))
        audit(c,user,'booking_code.record',{'ticket_id':ticket_id})
    return {'status':'unverified','message':'Manually recorded; not independently validated by SportyBet'}

@router.get('/activity')
def activity(user=Depends(auth)):
    with connect() as c:return allrows(c,'SELECT * FROM sports_ai_audit_events ORDER BY created_at DESC LIMIT 100')


# ---- Operational CRUD and evaluation endpoints: all changes scoped to sports_ai_* tables ----
class ResultIn(BaseModel):
    fixture_id:str
    home_score:int=Field(ge=0,le=300)
    away_score:int=Field(ge=0,le=300)
    source:str=Field(min_length=2,max_length=120)
    settled_at:datetime|None=None
class WatchIn(BaseModel):
    fixture_id:str
    note:str=Field(default='',max_length=300)
class SourceIn(BaseModel):
    name:str=Field(min_length=2,max_length=100)
    sport:Literal['football','basketball','both']
    kind:Literal['manual','api','file']
    enabled:bool=False
    notes:str=Field(default='',max_length=500)
class ScheduleIn(BaseModel):
    name:str=Field(min_length=2,max_length=100)
    interval_minutes:int=Field(ge=5,le=10080)
    enabled:bool=False
class AlertRuleIn(BaseModel):
    event_type:Literal['analysis','value','code','result']
    channel:Literal['dashboard','email','telegram']
    enabled:bool=False
class SettingsIn(BaseModel):
    min_edge:float=Field(ge=0,le=1)
    max_legs:int=Field(ge=1,le=8)
    odds_tolerance:float=Field(ge=0.01,le=0.5)
    odds_max_age_minutes:int=Field(default=15,ge=1,le=1440)
class RoleIn(BaseModel):
    role_name:str=Field(min_length=2,max_length=80)
    permissions:list[str]

@router.get('/results')
def results(user=Depends(auth)):
    with connect() as c:return allrows(c,'SELECT r.*,f.home,f.away,f.sport,f.competition FROM sports_ai_results r JOIN sports_ai_fixtures f ON f.id=r.fixture_id ORDER BY r.settled_at DESC')
@router.post('/results')
def save_result(p:ResultIn,user=Depends(auth)):
    with lock,connect() as c:
        fixture=c.execute('SELECT starts_at FROM sports_ai_fixtures WHERE id=?',(p.fixture_id,)).fetchone()
        if not fixture:raise HTTPException(404,'Fixture not found')
        settled_at=p.settled_at or datetime.now(timezone.utc)
        if settled_at.tzinfo is None:raise HTTPException(422,'Settlement timestamp must include a timezone')
        try:
            kickoff=datetime.fromisoformat(fixture['starts_at'].replace('Z','+00:00'))
            if kickoff.tzinfo is None:raise ValueError('Naive kickoff timestamp')
        except ValueError as error:
            raise HTTPException(422,'Fixture kickoff timestamp must include a timezone') from error
        settled_at=settled_at.astimezone(timezone.utc)
        if settled_at<kickoff.astimezone(timezone.utc):
            raise HTTPException(422,'Settlement timestamp cannot precede fixture kickoff')
        c.execute('''INSERT INTO sports_ai_results
            (id,fixture_id,home_score,away_score,source,settled_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(fixture_id) DO UPDATE SET home_score=excluded.home_score,
            away_score=excluded.away_score,source=excluded.source,settled_at=excluded.settled_at''',
            (str(uuid.uuid4()),p.fixture_id,p.home_score,p.away_score,p.source,
             settled_at.isoformat()))
        audit(c,user,'results.upsert',p.model_dump(mode='json'))
    return {'saved':True}
@router.get('/odds-history')
def odds_history(fixture_id:str|None=None,user=Depends(auth)):
    with connect() as c:
        legacy=allrows(c,
            'SELECT fixture_id,market,decimal_odds,source,observed_at,NULL AS bookmaker,NULL AS outcome,NULL AS line FROM sports_ai_odds_history'
            +(' WHERE fixture_id=?' if fixture_id else '')+' ORDER BY observed_at DESC LIMIT 500',
            (fixture_id,) if fixture_id else ())
        imported=allrows(c,
            """SELECT fixture_id,market,decimal_odds,source_url AS source,observed_at,
                      bookmaker,outcome,line FROM sports_ai_odds_snapshots"""
            +(' WHERE fixture_id=?' if fixture_id else '')+' ORDER BY observed_at DESC LIMIT 500',
            (fixture_id,) if fixture_id else ())
        return sorted(legacy+imported,key=lambda row:row['observed_at'],reverse=True)[:500]
@router.get('/watchlist')
def watchlist(user=Depends(auth)):
    with connect() as c:return allrows(c,'SELECT w.*,f.home,f.away,f.sport,f.starts_at FROM sports_ai_watchlist w JOIN sports_ai_fixtures f ON f.id=w.fixture_id ORDER BY w.created_at DESC')
@router.post('/watchlist')
def watch_add(p:WatchIn,user=Depends(auth)):
    with lock,connect() as c:
        if not c.execute('SELECT 1 FROM sports_ai_fixtures WHERE id=?',(p.fixture_id,)).fetchone():raise HTTPException(404,'Fixture not found')
        c.execute('INSERT INTO sports_ai_watchlist(id,fixture_id,note) VALUES(?,?,?) ON CONFLICT(fixture_id) DO UPDATE SET note=excluded.note',(str(uuid.uuid4()),p.fixture_id,p.note))
        audit(c,user,'watchlist.add',p.model_dump())
    return {'saved':True}
@router.delete('/watchlist/{fixture_id}')
def watch_remove(fixture_id:str,user=Depends(auth)):
    with lock,connect() as c:c.execute('DELETE FROM sports_ai_watchlist WHERE fixture_id=?',(fixture_id,));audit(c,user,'watchlist.remove',{'fixture_id':fixture_id})
    return {'removed':True}
@router.get('/sources')
def sources(user=Depends(auth)):
    with connect() as c:return allrows(c,'SELECT * FROM sports_ai_data_sources ORDER BY name')
@router.post('/sources')
def source_add(p:SourceIn,user=Depends(auth)):
    with lock,connect() as c:
        c.execute('INSERT INTO sports_ai_data_sources(id,name,sport,kind,enabled,notes) VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET sport=excluded.sport,kind=excluded.kind,enabled=excluded.enabled,notes=excluded.notes',(str(uuid.uuid4()),p.name,p.sport,p.kind,int(p.enabled),p.notes))
        audit(c,user,'sources.upsert',p.model_dump())
    return {'saved':True,'note':'Registration does not automatically implement a provider connector'}
@router.get('/schedules')
def schedules(user=Depends(auth)):
    with connect() as c:return allrows(c,'SELECT * FROM sports_ai_schedules ORDER BY created_at DESC')
@router.post('/schedules')
def schedule_add(p:ScheduleIn,user=Depends(auth)):
    with lock,connect() as c:
        c.execute('INSERT INTO sports_ai_schedules(id,name,interval_minutes,enabled) VALUES(?,?,?,?)',(str(uuid.uuid4()),p.name,p.interval_minutes,int(p.enabled)))
        audit(c,user,'schedules.create',p.model_dump())
    return {'saved':True,'note':'Schedule definition saved; run backend/worker.py to execute enabled schedules'}
@router.get('/jobs')
def jobs(user=Depends(auth)):
    with connect() as c:return allrows(c,'SELECT * FROM sports_ai_job_history ORDER BY started_at DESC LIMIT 200')
@router.get('/alert-rules')
def alert_rules(user=Depends(auth)):
    with connect() as c:return allrows(c,'SELECT * FROM sports_ai_alert_rules ORDER BY created_at DESC')
@router.post('/alert-rules')
def alert_rule_add(p:AlertRuleIn,user=Depends(auth)):
    with lock,connect() as c:
        c.execute('INSERT INTO sports_ai_alert_rules(id,event_type,channel,enabled) VALUES(?,?,?,?)',(str(uuid.uuid4()),p.event_type,p.channel,int(p.enabled)))
        audit(c,user,'alert_rule.create',p.model_dump())
    return {'saved':True}
@router.get('/notifications')
def notifications(user=Depends(auth)):
    with connect() as c:return allrows(c,'SELECT * FROM sports_ai_notifications ORDER BY created_at DESC LIMIT 200')
@router.get('/settings')
def settings(user=Depends(auth)):
    with connect() as c:
        r=c.execute("SELECT value_json FROM sports_ai_settings WHERE key='engine'").fetchone()
    return json.loads(r[0]) if r else {'min_edge':0.05,'max_legs':6,'odds_tolerance':0.15,'odds_max_age_minutes':15}
@router.put('/settings')
def settings_save(p:SettingsIn,user=Depends(auth)):
    with lock,connect() as c:
        c.execute("INSERT INTO sports_ai_settings(key,value_json) VALUES('engine',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=CURRENT_TIMESTAMP",(p.model_dump_json(),))
        audit(c,user,'settings.update',p.model_dump())
    return p
@router.get('/roles')
def roles(user=Depends(auth)):
    with connect() as c:return allrows(c,'SELECT * FROM sports_ai_roles ORDER BY role_name')
@router.post('/roles')
def role_add(p:RoleIn,user=Depends(auth)):
    with lock,connect() as c:
        c.execute('INSERT INTO sports_ai_roles(id,role_name,permissions_json) VALUES(?,?,?) ON CONFLICT(role_name) DO UPDATE SET permissions_json=excluded.permissions_json',(str(uuid.uuid4()),p.role_name,json.dumps(p.permissions)))
        audit(c,user,'roles.upsert',p.model_dump())
    return {'saved':True,'note':'Permission templates require mapping to CACSMS ONE identity provider'}
@router.get('/ticket-legs/{ticket_id}')
def ticket_legs(ticket_id:str,user=Depends(auth)):
    with connect() as c:return allrows(c,'SELECT l.*,f.home,f.away,f.sport FROM sports_ai_ticket_legs l JOIN sports_ai_fixtures f ON f.id=l.fixture_id WHERE l.ticket_id=?',(ticket_id,))
@router.get('/analysis-runs')
def analysis_runs(user=Depends(auth)):
    with connect() as c:return allrows(c,'SELECT * FROM sports_ai_analysis_runs ORDER BY created_at DESC LIMIT 100')

@router.get('/engine/predictions')
def engine_predictions(
    sport:Literal['football','basketball']|None=None,
    user=Depends(auth),
):
    with connect() as c:
        query="""SELECT p.id,p.fixture_id,p.sport,p.market,p.outcome,p.probability,
                        p.settlement_json,p.explanation_json,p.created_at,
                        f.home||' vs '||f.away AS fixture,f.competition,f.starts_at,
                        m.model_name,m.version AS model_version,m.status AS model_status,
                        (SELECT o.status FROM sports_ai_value_opportunities o
                         WHERE o.prediction_id=p.id ORDER BY o.evaluated_at DESC LIMIT 1) AS opportunity_status
                 FROM sports_ai_engine_predictions p
                 JOIN sports_ai_fixtures f ON f.id=p.fixture_id
                 JOIN sports_ai_model_registry m ON m.id=p.model_id"""
        if sport:query+=' WHERE p.sport=?'
        return allrows(c,query+' ORDER BY p.created_at DESC LIMIT 1000',(sport,) if sport else ())
@router.get('/performance')
def performance(user=Depends(auth)):
    with connect() as c:
        rows=allrows(c,"""SELECT p.market,p.sport,p.probability,p.odds,p.edge,r.home_score,r.away_score
        FROM sports_ai_predictions p JOIN sports_ai_results r ON p.fixture_id=r.fixture_id""")
    def outcome(x):
        h,a=x['home_score'],x['away_score'];m=x['market']
        if m=='HOME':return int(h>a)
        if m=='AWAY':return int(a>h)
        if m=='DRAW':return int(h==a)
        if m=='OVER_2_5':return int(h+a>=3)
        if m=='UNDER_2_5':return int(h+a<=2)
        if m=='BTTS_YES':return int(h>0 and a>0)
        if m=='BTTS_NO':return int(h==0 or a==0)
        if m=='OVER_220_5':return int(h+a>=221)
        if m=='UNDER_220_5':return int(h+a<=220)
        return None
    evaluated=[(x,outcome(x)) for x in rows];evaluated=[(x,y) for x,y in evaluated if y is not None]
    n=len(evaluated)
    return {'evaluated_predictions':n,'hit_rate':sum(y for x,y in evaluated)/n if n else None,'brier_score':sum((x['probability']-y)**2 for x,y in evaluated)/n if n else None,'log_loss':sum(-(y*__import__('math').log(max(1e-9,min(1-1e-9,x['probability'])))+(1-y)*__import__('math').log(max(1e-9,min(1-1e-9,1-x['probability'])))) for x,y in evaluated)/n if n else None,'note':'Observed historical results only. Multiple runs may include the same fixture; no out-of-sample guarantee.'}

app.include_router(router)
app.include_router(build_acquisition_router(auth))
