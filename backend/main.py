import os, json, re, sqlite3, uuid, threading, hmac, math, time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
from pathlib import Path
from fastapi import FastAPI, APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Literal
from fastapi import BackgroundTasks
from db import connect, initialize, DB
from engine import evaluate
from acquisition import CollectionError, import_basketball_rows
from acquisition_api import build_acquisition_router
from acquisition_engine import run_collection, validate_public_config
from prediction_engine import (
    enqueue_prediction_job, execute_prediction_job, rollback_model, train_model,
    upcoming_fixture_sql, upcoming_order_sql,
)
from accumulator_api import build_accumulator_router
from match_analysis import AnalysisBusy, analyse_upcoming_batch, analyze_fixture, latest_report
from operations_api import build_operations_router
from browser_api import build_browser_router
from sportybet_api import build_sportybet_router
from sportybet_booking import build_booking_router
from draw_engine import build_draw_router
from weekly_draw_engine import build_weekly_draw_router
from rollover_engine import build_rollover_router
from learning_engine import build_learning_router

app=FastAPI(title='CACSMS Sports AI module',version='1.0.0')
router=APIRouter(prefix='/api/sports-ai',tags=['Sports AI'])
ERROR_LOG=Path(__file__).parent/'logs'/'api-errors.log'

def _log_error(request:Request, error:BaseException):
    import traceback
    try:
        ERROR_LOG.parent.mkdir(parents=True,exist_ok=True)
        with ERROR_LOG.open('a',encoding='utf-8') as handle:
            handle.write(f"\n=== {datetime.now(timezone.utc).isoformat()} {request.method} {request.url.path}\n")
            handle.write(''.join(traceback.format_exception(type(error),error,error.__traceback__)))
    except OSError:
        pass

@app.exception_handler(sqlite3.OperationalError)
async def _database_busy(request:Request, error:sqlite3.OperationalError):
    # The worker writes to the same SQLite file; a long write can outlast the 20 s busy timeout.
    _log_error(request,error)
    if 'locked' in str(error).lower() or 'busy' in str(error).lower():
        return JSONResponse({'detail':'The database is busy with a background job, so nothing was saved. Try again in a minute.'},status_code=503)
    return JSONResponse({'detail':f'Database error: {error}. Nothing was saved; details are in backend/logs/api-errors.log.'},status_code=500)

@app.exception_handler(Exception)
async def _unhandled(request:Request, error:Exception):
    _log_error(request,error)
    return JSONResponse({'detail':f'Server error: {type(error).__name__}: {str(error)[:200]}. Details are in backend/logs/api-errors.log.'},status_code=500)
lock=threading.RLock()
from sport_support import ANALYSED_SPORTS, GOAL_SPORTS, WINNER_SPORTS
SportCode = Literal[ANALYSED_SPORTS]
initialize()

def _assert_production_configuration():
    if os.environ.get('SPORTS_PRODUCTION')!='1':
        return
    if os.environ.get('SPORTS_LOCAL_PREVIEW')=='1':
        raise RuntimeError('SPORTS_LOCAL_PREVIEW cannot be enabled when SPORTS_PRODUCTION=1')
    if not os.environ.get('SPORTS_ADMIN_API_KEY') or not os.environ.get('SPORTS_DEV_API_KEY'):
        raise RuntimeError('Production requires SPORTS_ADMIN_API_KEY and SPORTS_DEV_API_KEY')

def _load_local_admin_key():
    """Local development: read the admin key from backend/.secrets/admin.key (git-ignored)
    when the process environment does not carry it. Editors such as VS Code keep the
    environment they started with, so a newly set Windows variable often never arrives.
    An explicitly set environment variable always wins; production sets it explicitly."""
    if os.environ.get('SPORTS_ADMIN_API_KEY') or os.environ.get('SPORTS_PRODUCTION') == '1':
        return
    from pathlib import Path
    key_file = Path(os.environ.get('SPORTS_SECRETS_DIR') or Path(__file__).resolve().parent / '.secrets') / 'admin.key'
    try:
        key = key_file.read_text(encoding='utf-8').strip()
    except OSError:
        return
    if len(key) >= 20:
        os.environ['SPORTS_ADMIN_API_KEY'] = key


_load_local_admin_key()
_assert_production_configuration()
_RATE_BUCKETS={}
_RATE_LIMIT=240
_RATE_WINDOW=60

@app.middleware('http')
async def limit_requests(request:Request,call_next):
    if request.url.path.endswith('/health'):
        return await call_next(request)
    host=(request.client.host if request.client else 'unknown')
    now=time.monotonic()
    bucket=[stamp for stamp in _RATE_BUCKETS.get(host,[]) if now-stamp<_RATE_WINDOW]
    if len(bucket)>=_RATE_LIMIT:
        return JSONResponse({'detail':'Rate limit exceeded'},status_code=429)
    bucket.append(now)
    _RATE_BUCKETS[host]=bucket
    return await call_next(request)

def _key_matches(name, presented):
    expected=os.environ.get(name)
    return bool(expected and presented and hmac.compare_digest(expected, presented))

def _role_permissions(role):
    fallback={
        'super_administrator':['read','models.activate','policy.write','booking.execute','diagnostics.admin','config.write','credentials.manage'],
        'administrator':['read','models.activate','policy.write','booking.execute','diagnostics.admin','config.write'],
        'analyst':['read'],
        'operator':['read','booking.execute'],
        'read_only':['read'],
        'local_preview':['read'],
    }
    if role=='local_preview':
        return list(fallback['local_preview'])
    try:
        with connect() as connection:
            row=connection.execute('SELECT permissions_json FROM sports_ai_roles WHERE role_name=?',(role,)).fetchone()
        loaded=json.loads(row['permissions_json']) if row else None
        if isinstance(loaded, list) and loaded:
            return loaded
    except (json.JSONDecodeError, TypeError, sqlite3.Error):
        pass
    return list(fallback.get(role, ['read']))

def auth(request:Request,x_sports_dev_key:str|None=Header(default=None),x_sports_admin_key:str|None=Header(default=None),x_sports_operator_key:str|None=Header(default=None)):
    # Loopback preview is development only. SPORTS_PRODUCTION=1 refuses it.
    production=os.environ.get('SPORTS_PRODUCTION')=='1'
    preview=os.environ.get('SPORTS_LOCAL_PREVIEW')=='1'
    if production and preview:
        raise HTTPException(503,'Loopback preview authentication is disabled when SPORTS_PRODUCTION=1')
    if not production and preview and request.client and request.client.host in ('127.0.0.1','::1'):
        role, actor='local_preview','local-preview-user'
    elif _key_matches('SPORTS_ADMIN_API_KEY', x_sports_admin_key):
        role, actor='super_administrator','production-admin'
    elif _key_matches('SPORTS_OPERATOR_API_KEY', x_sports_operator_key):
        role, actor='operator','production-operator'
    elif _key_matches('SPORTS_DEV_API_KEY', x_sports_dev_key):
        role, actor='analyst','local-dev-user'
    else:
        raise HTTPException(401,'Configure SPORTS_DEV_API_KEY and supply X-Sports-Dev-Key, or replace auth with CACSMS ONE session authentication')
    request.state.sports_role=role
    request.state.sports_permissions=_role_permissions(role)
    return actor

def authorize(request:Request, permission, admin_key=None):
    role=getattr(request.state,'sports_role','')
    if role=='local_preview':
        require_admin_key(admin_key)
        return
    if permission not in set(getattr(request.state,'sports_permissions',[])):
        raise HTTPException(403,'This role cannot perform that operation')

class FixtureIn(BaseModel):
    external_id:str=Field(min_length=1,max_length=160)
    sport:SportCode
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
    sport:SportCode|None=None

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

_DISPLAYED_CODE_METHODS = ('displayed', 'sportybet_page', 'page')

@router.get('/maintenance/snapshot-cleanup')
def snapshot_cleanup_status(user=Depends(auth)):
    with connect() as connection:
        row=connection.execute("SELECT value_json FROM sports_ai_settings WHERE key='snapshot_cleanup'").fetchone()
    return json.loads(row[0]) if row else {'status':'not_run','deleted_odds_snapshots':0}

def _code_still_valid(expires_at, now):
    if not expires_at:
        return True
    try:
        expiry = datetime.fromisoformat(str(expires_at).replace('Z', '+00:00'))
    except ValueError:
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry > now

def _verified_displayed_code(row, now):
    code = (row['code'] or '').strip()
    evidence = (row['evidence'] or '').strip()
    method = (row['verification_method'] or '').strip()
    if not code or not evidence or not row['verified_at'] or row['status'] != 'verified':
        return False
    if method not in _DISPLAYED_CODE_METHODS:
        return False
    return _code_still_valid(row['expires_at'], now)

def _ticket_code(ticket, code_row, now):
    if code_row and _verified_displayed_code(code_row, now):
        return {
            'code': code_row['code'].strip(),
            'code_status': 'verified',
            'code_label': 'Verified',
            'generated_at': code_row['verified_at'] or code_row['created_at'],
            'expires_at': code_row['expires_at'],
        }
    stored = (code_row['code'] or '').strip() if code_row else ''
    if stored and code_row and not _code_still_valid(code_row['expires_at'], now):
        return {'code': stored, 'code_status': 'expired', 'code_label': 'Expired', 'generated_at': code_row['created_at'], 'expires_at': code_row['expires_at']}
    if stored:
        return {'code': stored, 'code_status': 'unverified', 'code_label': 'Recorded, not verified', 'generated_at': code_row['created_at'], 'expires_at': code_row['expires_at']}
    if ticket['status'] == 'expired':
        return {'code': None, 'code_status': 'expired', 'code_label': 'Expired', 'generated_at': ticket['created_at'], 'expires_at': None}
    return {'code': None, 'code_status': 'awaiting_code', 'code_label': 'Awaiting Code', 'generated_at': ticket['created_at'], 'expires_at': None}

def booking_centre(connection):
    """Prepared tickets stay separate from a code SportyBet has actually displayed."""
    from sportybet_adapter import integration_status
    now = datetime.now(timezone.utc)
    codes = connection.execute(
        """SELECT id, accumulator_ticket_id, code, status, verification_method, evidence,
                  verified_at, expires_at, created_at
           FROM sports_ai_booking_codes
           ORDER BY created_at DESC""",
    ).fetchall()
    linked = {}
    verified_codes = set()
    for row in codes:
        if row['accumulator_ticket_id'] and row['accumulator_ticket_id'] not in linked:
            linked[row['accumulator_ticket_id']] = row
        if _verified_displayed_code(row, now):
            verified_codes.add(row['code'].strip())
    jobs = connection.execute(
        """SELECT code FROM sports_ai_booking_jobs
           WHERE status='CODE_GENERATED'
             AND TRIM(COALESCE(code,''))<>''
             AND TRIM(COALESCE(screenshot,''))<>''""",
    ).fetchall()
    for row in jobs:
        verified_codes.add(row['code'].strip())
    prepared = connection.execute('SELECT COUNT(*) FROM sports_ai_accumulator_tickets').fetchone()[0]
    awaiting = connection.execute(
        """SELECT COUNT(*) FROM sports_ai_accumulator_tickets t
           WHERE t.status NOT IN ('expired','settled','void')
             AND NOT EXISTS (
               SELECT 1 FROM sports_ai_booking_codes b
               WHERE b.accumulator_ticket_id=t.id
                 AND b.status='verified'
                 AND b.verified_at IS NOT NULL
                 AND b.verification_method IN ('displayed','sportybet_page','page')
                 AND TRIM(COALESCE(b.evidence,''))<>''
                 AND TRIM(COALESCE(b.code,''))<>''
                 AND (b.expires_at IS NULL OR b.expires_at>?)
             )""",
        (now.isoformat(),),
    ).fetchone()[0]
    ready = connection.execute(
        """SELECT COUNT(*) FROM sports_ai_accumulator_tickets
           WHERE status='qualified' AND publication_state='READY_FOR_WINDOW'""",
    ).fetchone()[0]
    not_bookable = connection.execute(
        """SELECT COUNT(*) FROM sports_ai_accumulator_tickets t
           WHERE NOT (t.status='qualified' AND t.publication_state='READY_FOR_WINDOW')
             AND NOT EXISTS (
               SELECT 1 FROM sports_ai_booking_codes b
               WHERE b.accumulator_ticket_id=t.id
                 AND b.status='verified'
                 AND b.verification_method IN ('displayed','sportybet_page','page')
                 AND TRIM(COALESCE(b.code,''))<>''
                 AND (b.expires_at IS NULL OR b.expires_at>?)
             )""",
        (now.isoformat(),),
    ).fetchone()[0]
    tickets = allrows(
        connection,
        """SELECT t.id, t.sport_mode, t.tier, t.leg_count, t.combined_odds, t.joint_probability,
                  t.status, t.publication_state, t.created_at,
                  (SELECT group_concat(l.home || ' vs ' || l.away, ' · ')
                   FROM sports_ai_accumulator_legs l WHERE l.ticket_id=t.id) AS matchup
           FROM sports_ai_accumulator_tickets t
           WHERE (t.status='qualified' AND t.probability_basis='independent' AND t.joint_probability>0.70)
              OR EXISTS (
                SELECT 1 FROM sports_ai_booking_codes b
                WHERE b.accumulator_ticket_id=t.id AND b.status='verified'
                  AND b.verification_method IN ('displayed','sportybet_page','page')
                  AND TRIM(COALESCE(b.code,''))<>''
              )
           ORDER BY CASE WHEN EXISTS (
                SELECT 1 FROM sports_ai_booking_codes b
                WHERE b.accumulator_ticket_id=t.id AND b.status='verified'
                  AND TRIM(COALESCE(b.code,''))<>''
              ) THEN 0 ELSE 1 END, t.created_at DESC
           LIMIT 8""",
    )
    legs_by = {ticket['id']: [] for ticket in tickets}
    if tickets:
        marks = ','.join('?' * len(tickets))
        for leg in allrows(
            connection,
            f"""SELECT id, ticket_id, fixture_id, sport, competition, home, away, starts_at,
                       market, selection, decimal_odds, probability
                FROM sports_ai_accumulator_legs
                WHERE ticket_id IN ({marks})
                ORDER BY starts_at""",
            [ticket['id'] for ticket in tickets],
        ):
            legs_by[leg['ticket_id']].append(leg)
    items = []
    for ticket in tickets:
        ticket.update(_ticket_code(ticket, linked.get(ticket['id']), now))
        if ticket['code_status'] == 'awaiting_code' and ticket.get('publication_state') == 'READY_FOR_WINDOW':
            ticket['code_status'] = 'ready'
            ticket['code_label'] = 'Ready for the next window'
        elif ticket['code_status'] == 'awaiting_code' and ticket.get('publication_state') == 'BOOKING_PENDING':
            ticket['code_status'] = 'pending'
            ticket['code_label'] = 'Request in progress'
        ticket['legs'] = legs_by.get(ticket['id'], [])
        items.append(ticket)
    integration = integration_status()
    latest = connection.execute(
        'SELECT status, error, code, updated_at FROM sports_ai_booking_jobs ORDER BY updated_at DESC LIMIT 1',
    ).fetchone()
    return {
        'counts': {
            'verified_codes': len(verified_codes),
            'prepared_tickets': prepared,
            'awaiting_codes': awaiting,
            'ready_for_window': ready,
            'not_bookable': not_bookable,
        },
        'generation_available': bool(integration.get('automatic_booking_code')),
        'last_attempt': {
            'status': latest['status'],
            'error': latest['error'],
            'code': latest['code'],
            'updated_at': latest['updated_at'],
        } if latest else None,
        'tickets': items,
    }

@router.get('/sure-odds')
def sure_odds(user=Depends(auth)):
    from command_view import sure_odds_view
    with connect() as connection:
        return sure_odds_view(connection)


@router.get('/dashboard')
def dashboard(user=Depends(auth)):
    # ~12 s to build on the live database; served from a 30 s background-refreshed copy (view_cache).
    from view_cache import cached_view
    return cached_view('dashboard', lambda: _build_dashboard(user), ttl=30)

def _build_dashboard(user):
    with connect() as c:
        counts={}
        for key,table in [('fixtures','fixtures'),('tickets','tickets'),('booking_codes','booking_codes')]:counts[key]=c.execute('SELECT COUNT(*) FROM sports_ai_'+table).fetchone()[0]
        counts['legacy_tickets']=counts['tickets']
        counts['accumulator_tickets']=c.execute('SELECT COUNT(*) FROM sports_ai_accumulator_tickets').fetchone()[0]
        counts['tickets']=counts['legacy_tickets']+counts['accumulator_tickets']
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
               WHERE p.market IN ('HOME','DRAW','AWAY','MONEYLINE_HOME','MONEYLINE_AWAY')
               ORDER BY p.created_at DESC LIMIT 8""")
        counts['recent_opportunities']=allrows(c,
            """SELECT f.home||' vs '||f.away AS fixture,f.sport,p.market,
                      q.decimal_odds AS odds,p.probability,o.expected_edge AS edge,o.status
               FROM sports_ai_value_opportunities o
               JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id
               JOIN sports_ai_fixtures f ON f.id=p.fixture_id
               LEFT JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
               WHERE o.status='qualified' AND p.probability>0.80 ORDER BY p.probability DESC LIMIT 10""")
        row=c.execute('SELECT created_at FROM sports_ai_analysis_runs ORDER BY created_at DESC LIMIT 1').fetchone()
        counts['last_run']=row[0] if row else None
        counts['last_analysis']=c.execute('SELECT MAX(created_at) FROM sports_ai_match_analyses').fetchone()[0]
        counts['analyses_today']=c.execute("SELECT COUNT(*) FROM sports_ai_match_analyses WHERE date(created_at)=date('now')").fetchone()[0]
        counts['predictions_today']=c.execute("SELECT COUNT(*) FROM sports_ai_engine_predictions WHERE date(created_at)=date('now')").fetchone()[0]
        counts['fixtures_analysed']=c.execute('SELECT COUNT(DISTINCT fixture_id) FROM sports_ai_match_analyses').fetchone()[0]
        counts['awaiting_odds']=c.execute("SELECT COUNT(*) FROM sports_ai_value_opportunities WHERE status='awaiting_odds'").fetchone()[0]
        counts['awaiting_calibration']=c.execute("SELECT COUNT(*) FROM sports_ai_value_opportunities WHERE status='awaiting_calibration'").fetchone()[0]
        counts['odds_snapshots']=c.execute('SELECT COUNT(*) FROM sports_ai_odds_snapshots').fetchone()[0]
        odds=c.execute("SELECT enabled,status,credential_env FROM sports_ai_provider_registry WHERE provider_key='the-odds-api'").fetchone()
        counts['odds_provider']=None if not odds else {'enabled':bool(odds['enabled']),'status':odds['status'],'credential_env':odds['credential_env'],'configured':bool(os.environ.get(odds['credential_env'] or ''))}
        leader=c.execute("SELECT owner,heartbeat_at,expires_at FROM sports_ai_worker_leases WHERE id='scheduler-leader'").fetchone()
        counts['scheduler']=None if not leader else dict(leader)
        nxt=c.execute("SELECT name,next_run_at,enabled FROM sports_ai_schedules WHERE enabled=1 ORDER BY COALESCE(next_run_at,'9999') LIMIT 1").fetchone()
        counts['next_schedule']=None if not nxt else dict(nxt)
        counts['failed_jobs']=c.execute("SELECT COUNT(*) FROM sports_ai_jobs WHERE status IN ('failed','dead_letter')").fetchone()[0]
        counts['providers']=[dict(item) for item in c.execute("SELECT provider_key,display_name,enabled,status,last_success_at FROM sports_ai_provider_registry ORDER BY provider_key")]
        counts['schedules']=[dict(item) for item in c.execute("SELECT name,enabled,job_kind,last_run_at,next_run_at FROM sports_ai_schedules ORDER BY COALESCE(next_run_at,'9999')")]
        counts['activity_today']={
            'fixtures':c.execute("SELECT COUNT(*) FROM sports_ai_fixtures WHERE date(observed_at)=date('now')").fetchone()[0],
            'predictions':counts['predictions_today'],
            'odds':c.execute("SELECT COUNT(*) FROM sports_ai_odds_snapshots WHERE date(observed_at)=date('now')").fetchone()[0],
            'opportunities':c.execute("SELECT COUNT(*) FROM sports_ai_value_opportunities WHERE date(evaluated_at)=date('now')").fetchone()[0],
            'tickets':c.execute("SELECT COUNT(*) FROM sports_ai_accumulator_tickets WHERE date(created_at)=date('now')").fetchone()[0],
            'results':c.execute("SELECT COUNT(*) FROM sports_ai_results WHERE date(settled_at)=date('now')").fetchone()[0],
        }
        counts['prediction_sports']=[dict(item) for item in c.execute("SELECT f.sport AS name, COUNT(*) AS total FROM sports_ai_engine_predictions p JOIN sports_ai_fixtures f ON f.id=p.fixture_id GROUP BY f.sport")]
        counts['tier_counts']=[dict(item) for item in c.execute("SELECT tier, COUNT(*) AS total FROM sports_ai_accumulator_tickets GROUP BY tier")]
        def tier_key(value):
            number=float(value)
            return int(number) if number.is_integer() else number
        filled={tier_key(item['tier']) for item in counts['tier_counts'] if item['total']}
        gaps={}
        for run in c.execute("SELECT summary_json FROM sports_ai_accumulator_runs WHERE summary_json IS NOT NULL ORDER BY finished_at DESC LIMIT 6"):
            try:
                summary=json.loads(run['summary_json'])
            except (TypeError, json.JSONDecodeError):
                continue
            for tier in summary.get('tiers') or []:
                target=tier.get('target')
                reason=tier.get('reason')
                if target is None or not reason or tier_key(target) in filled:
                    continue
                key=tier_key(target)
                previous=gaps.get(key)
                if previous and 'search stopped' not in previous.lower():
                    continue
                if previous and 'search stopped' in reason.lower():
                    continue
                gaps[key]=reason
        counts['tier_gaps']=[{'tier':tier,'reason':reason} for tier,reason in sorted(gaps.items())]
        counts['highlights']=allrows(c,
            f"""WITH chosen AS (
                 SELECT f.id,f.sport,f.competition,f.home,f.away,f.starts_at
                 FROM sports_ai_fixtures f
                 WHERE {upcoming_fixture_sql('f')}
                   AND EXISTS (SELECT 1 FROM sports_ai_match_analyses a WHERE a.fixture_id=f.id)
                 ORDER BY {upcoming_order_sql('f')}
                 LIMIT 5
               )
               SELECT chosen.id AS fixture_id,chosen.sport,chosen.competition,chosen.home,chosen.away,chosen.starts_at,
                      p.market,p.probability,
                      (SELECT q.decimal_odds FROM sports_ai_odds_snapshots q
                       WHERE q.fixture_id=chosen.id AND q.bookmaker='SportyBet'
                         AND (
                           (p.market IN ('HOME','MONEYLINE_HOME') AND q.market='h2h' AND q.outcome='home')
                           OR (p.market IN ('AWAY','MONEYLINE_AWAY') AND q.market='h2h' AND q.outcome='away')
                           OR (p.market='DRAW' AND q.market='h2h' AND q.outcome='draw')
                         )
                       ORDER BY q.observed_at DESC LIMIT 1) AS decimal_odds
               FROM chosen
               JOIN sports_ai_engine_predictions p ON p.id=(
                 SELECT p2.id FROM sports_ai_engine_predictions p2
                 WHERE p2.fixture_id=chosen.id AND p2.market IN ('HOME','DRAW','AWAY','MONEYLINE_HOME','MONEYLINE_AWAY')
                 ORDER BY p2.created_at DESC, p2.probability DESC LIMIT 1)""")
        age_row=c.execute("SELECT value_json FROM sports_ai_settings WHERE key='engine'").fetchone()
        try:
            odds_age=int(json.loads(age_row[0]).get('odds_max_age_minutes',15)) if age_row else 15
        except (TypeError,ValueError,json.JSONDecodeError):
            odds_age=15
        fresh_quote=(datetime.now(timezone.utc)-timedelta(minutes=odds_age)).isoformat()
        counts['top_opportunities']=allrows(c,
            f"""SELECT fixture_id,sport,competition,home,away,starts_at,market,probability,edge,rank_score,decimal_odds,bookmaker,observed_at
               FROM (
                 SELECT f.id AS fixture_id,f.sport,f.competition,f.home,f.away,f.starts_at,p.market,p.probability,
                        o.expected_edge AS edge,o.rank_score,q.decimal_odds,q.bookmaker,q.observed_at,
                        ROW_NUMBER() OVER (
                          PARTITION BY f.id
                          ORDER BY p.probability DESC, o.expected_edge DESC
                        ) AS pick
                 FROM sports_ai_value_opportunities o
                 JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id
                 JOIN sports_ai_fixtures f ON f.id=p.fixture_id
                 JOIN sports_ai_model_registry m ON m.id=p.model_id
                 JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
                 JOIN sports_ai_qualification_decisions d
                   ON d.prediction_id=p.id AND d.market=p.market AND d.state='QUALIFIED'
                 WHERE o.status='qualified' AND o.expected_edge>0 AND m.active=1 AND m.status='active'
                   AND p.probability>0.80
                   AND {upcoming_fixture_sql('f')} AND q.observed_at>=? AND q.decimal_odds>1
               )
               WHERE pick=1
               ORDER BY probability DESC, edge DESC
               LIMIT 10""", (fresh_quote,))
        settled=c.execute(
            """SELECT
                 SUM(CASE WHEN (p.market IN ('HOME','MONEYLINE_HOME') AND r.home_score>r.away_score)
                            OR (p.market IN ('AWAY','MONEYLINE_AWAY') AND r.away_score>r.home_score)
                            OR (p.market='DRAW' AND r.home_score=r.away_score) THEN 1 ELSE 0 END) AS won,
                 SUM(CASE WHEN (p.market IN ('HOME','MONEYLINE_HOME') AND r.home_score<r.away_score)
                            OR (p.market IN ('AWAY','MONEYLINE_AWAY') AND r.away_score<r.home_score)
                            OR (p.market='DRAW' AND r.home_score!=r.away_score) THEN 1 ELSE 0 END) AS lost
               FROM sports_ai_engine_predictions p
               JOIN sports_ai_fixtures f ON f.id=p.fixture_id
               JOIN sports_ai_results r ON r.fixture_id=f.id AND r.status='final'
               WHERE p.market IN ('HOME','DRAW','AWAY','MONEYLINE_HOME','MONEYLINE_AWAY')
                 AND p.created_at<f.starts_at"""
        ).fetchone()
        counts['settled_outcomes']={'won':settled['won'] or 0,'lost':settled['lost'] or 0}
        from browser_jobs import browser_summary
        counts['browser']=browser_summary(c)
        governance_row=c.execute("SELECT value_json FROM sports_ai_settings WHERE key='model_governance'").fetchone()
        value_row=c.execute("SELECT value_json FROM sports_ai_settings WHERE key='value_cycle'").fetchone()
        try:
            governance=json.loads(governance_row[0]) if governance_row else {}
        except (TypeError, json.JSONDecodeError):
            governance={}
        try:
            value_cycle=json.loads(value_row[0]) if value_row else {}
        except (TypeError, json.JSONDecodeError):
            value_cycle={}
        discovery=c.execute("SELECT enabled FROM sports_ai_schedules WHERE id='schedule-discovery'").fetchone()
        fresh_odds=c.execute(
            """SELECT COUNT(DISTINCT fixture_id) FROM sports_ai_odds_snapshots
               WHERE bookmaker='SportyBet' AND observed_at>=?""",
            (fresh_quote,),
        ).fetchone()[0]
        blockers=[]
        for decision in governance.get('decisions') or []:
            if decision.get('action') in ('inactive','blocked','missing') and decision.get('reasons'):
                blockers.append({'stage': f"{decision.get('sport','model').title()} model", 'reason': ' '.join(decision['reasons'])})
        if not discovery or not discovery['enabled']:
            blockers.append({'stage': 'Fixture discovery', 'reason': 'The discovery schedule is off.'})
        if not fresh_odds:
            blockers.append({'stage': 'Odds', 'reason': value_cycle.get('reason') or 'No SportyBet quote is inside the freshness window.'})
        if not counts['qualified_opportunities']:
            blockers.append({'stage': 'Qualification', 'reason': value_cycle.get('reason') or 'No selection has cleared an active model, a fresh price and the configured edge.'})
        if not counts['accumulator_tickets']:
            blockers.append({'stage': 'Tickets', 'reason': 'An accumulator is built only after a qualified selection exists.'})
        counts['model_governance']=governance
        counts['value_cycle']=value_cycle
        counts['fresh_odds_fixtures']=fresh_odds
        counts['discovery_enabled']=bool(discovery and discovery['enabled'])
        counts['pipeline_blockers']=blockers
        counts['booking_centre']=booking_centre(c)
        from booking_schedule import describe as describe_booking
        from pipeline_engine import latest_snapshot
        snapshot = latest_snapshot(c)
        stages = {item['stage']: item['count'] for item in (snapshot or {}).get('stages') or []}
        schedule = describe_booking(datetime.now(timezone.utc))
        counts['risk_review']=c.execute(
            "SELECT COUNT(*) FROM sports_ai_qualification_decisions WHERE state='MONITORING' AND reason_code='RISK_REVIEW'",
        ).fetchone()[0]
        counts['qualified_by_sport']=[dict(item) for item in c.execute(
            "SELECT sport AS name, COUNT(*) AS total FROM sports_ai_qualification_decisions WHERE state='QUALIFIED' GROUP BY sport",
        )]
        counts['operating']={
            'timezone': schedule['timezone'],
            'local_time': schedule['local_time'],
            'phase': schedule['phase'],
            'publishes': schedule['publishes'],
            'next_publication_at': schedule['next_publication_at'],
            'places_wager': False,
            'qualified_now': stages.get('QUALIFIED'),
            'ready_tickets': stages.get('TICKET_READY'),
            'fresh_prices': stages.get('ODDS_FRESH'),
            'codes_verified': stages.get('CODE_VERIFIED'),
            'codes_displayed': stages.get('DISPLAYED'),
            'risk_review': counts['risk_review'],
            'snapshot_at': None if not snapshot else snapshot['created_at'],
        }
        from command_view import command_view
        counts['command_view']=command_view(c, datetime.now(timezone.utc))
        counts['command_view']['next_window']=schedule['next_publication_at']
        counts['command_view']['publication_open']=schedule['publishes']
        return counts

def _fixture_select():
    return '''SELECT f.id,f.external_id,f.sport,f.competition,f.home,f.away,f.starts_at AS kickoff_at,
                    f.status,f.source,f.observed_at,
                    (SELECT m.source_url FROM sports_ai_fixture_source_map m
                     WHERE m.fixture_id=f.id AND m.source_url<>'' ORDER BY m.observed_at DESC LIMIT 1) AS source_url,
                    (SELECT p.display_name FROM sports_ai_fixture_source_map m
                     JOIN sports_ai_provider_registry p ON p.id=m.provider_id
                     WHERE m.fixture_id=f.id ORDER BY m.observed_at DESC LIMIT 1) AS provider_name
             FROM sports_ai_fixtures f'''


def _fixture_window(window, now):
    today = now.date().isoformat()
    instant = now.replace(microsecond=0).isoformat()
    date_only = """IFNULL((
        SELECT m.time_precision FROM sports_ai_fixture_source_map m
        WHERE m.fixture_id=f.id ORDER BY m.observed_at DESC LIMIT 1
    ), 'instant')='date'"""
    ahead = f"""(
        ({date_only} AND date(f.starts_at)>=?)
        OR (NOT ({date_only}) AND f.starts_at>=?)
    )"""
    if window == 'upcoming':
        return f"f.status='scheduled' AND {ahead}", [today, instant], 'f.starts_at'
    if window == 'results':
        return f"(f.status='finished' OR NOT {ahead})", [today, instant], 'f.starts_at DESC'
    if window in (None, 'all'):
        return '', [], 'f.starts_at'
    raise HTTPException(422, 'window must be upcoming, results or all')


@router.get('/fixtures')
def fixtures(
    sport: str | None = None,
    window: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    team: str | None = None,
    competition: str | None = None,
    provider: str | None = None,
    fixture_id: str | None = None,
    user=Depends(auth),
):
    if sport and not re.fullmatch(r'[a-z][a-z0-9_]{0,40}', sport):
        raise HTTPException(422, 'Invalid sport code')
    now = datetime.now(timezone.utc)
    window_sql, window_args, order = _fixture_window(window, now)
    filters, args = [], []
    if sport:
        filters.append('f.sport=?')
        args.append(sport)
    if team:
        filters.append('(f.home LIKE ? OR f.away LIKE ?)')
        args.extend([f'%{team}%', f'%{team}%'])
    if competition:
        filters.append('f.competition LIKE ?')
        args.append(f'%{competition}%')
    if provider:
        filters.append('f.source LIKE ?')
        args.append(f'%{provider}%')
    if fixture_id:
        filters.append('f.id=?')
        args.append(fixture_id)
    if window_sql:
        filters.append(window_sql)
        args.extend(window_args)
    clause = (' WHERE ' + ' AND '.join(filters)) if filters else ''
    with connect() as c:
        if window is None:
            return allrows(c, _fixture_select() + clause + ' ORDER BY ' + order, args)
        total = c.execute(
            'SELECT COUNT(*) FROM sports_ai_fixtures f' + clause, args,
        ).fetchone()[0]
        rows = allrows(
            c,
            _fixture_select() + clause + f' ORDER BY {order} LIMIT ? OFFSET ?',
            args + [page_size, (page - 1) * page_size],
        )
        return {'items': rows, 'total': total, 'page': page, 'page_size': page_size}

_BOARD_EXTRA = {}
_BOARD_EXTRA_LOCK = threading.Lock()
_SUMMARY_CACHE = {}
_SUMMARY_LOCK = threading.Lock()


def _board_extra(sport):
    """Full price and settlement counts take several seconds. Serve the last one and refresh it off the request."""
    now = time.monotonic()
    with _BOARD_EXTRA_LOCK:
        hit = _BOARD_EXTRA.get(sport)
        if hit and (now - hit[0] < 45 or hit[2]):
            return hit[1]
        _BOARD_EXTRA[sport] = (now, None if not hit else hit[1], True)
    previous = None if not hit else hit[1]

    def work():
        value = previous
        try:
            with connect() as connection:
                opportunities = connection.execute(
                    """SELECT COUNT(*) FROM sports_ai_value_opportunities o
                       JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id
                       JOIN sports_ai_fixtures f ON f.id=p.fixture_id
                       WHERE f.sport=? AND o.status='qualified'""",
                    (sport,),
                ).fetchone()[0]
                settled = connection.execute(
                    """SELECT
                         SUM(CASE WHEN (p.market IN ('HOME','MONEYLINE_HOME') AND r.home_score>r.away_score)
                                    OR (p.market IN ('AWAY','MONEYLINE_AWAY') AND r.away_score>r.home_score)
                                    OR (p.market='DRAW' AND r.home_score=r.away_score) THEN 1 ELSE 0 END) AS won,
                         SUM(CASE WHEN (p.market IN ('HOME','MONEYLINE_HOME') AND r.home_score<r.away_score)
                                    OR (p.market IN ('AWAY','MONEYLINE_AWAY') AND r.away_score<r.home_score)
                                    OR (p.market='DRAW' AND r.home_score!=r.away_score) THEN 1 ELSE 0 END) AS lost
                       FROM sports_ai_engine_predictions p
                       JOIN sports_ai_fixtures f ON f.id=p.fixture_id
                       JOIN sports_ai_results r ON r.fixture_id=f.id AND r.status='final'
                       WHERE f.sport=? AND p.market IN ('HOME','DRAW','AWAY','MONEYLINE_HOME','MONEYLINE_AWAY')
                         AND p.created_at<f.starts_at""",
                    (sport,),
                ).fetchone()
                won, lost = (settled['won'] or 0), (settled['lost'] or 0)
                value = {
                    'opportunities': opportunities,
                    'performance': {
                        'won': won, 'lost': lost, 'settled': won + lost,
                        'accuracy': None if won + lost == 0 else round(won / (won + lost), 4),
                        'roi': None,
                    },
                }
        except Exception:
            value = previous
        with _BOARD_EXTRA_LOCK:
            _BOARD_EXTRA[sport] = (time.monotonic(), value, False)

    threading.Thread(target=work, daemon=True).start()
    return previous


@router.get('/fixtures/board')
def fixtures_board(
    sport: str = 'football',
    view: str = 'upcoming',
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    team: str | None = None,
    competition: str | None = None,
    starts_after: str | None = None,
    starts_before: str | None = None,
    analysis: str = 'all',
    user=Depends(auth),
):
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,40}', sport):
        raise HTTPException(422, 'Invalid sport code')
    if view not in ('upcoming', 'results', 'standings', 'opportunities', 'tickets', 'logs'):
        raise HTTPException(422, 'Unknown fixtures view')
    if analysis not in ('all', 'analysed', 'pending'):
        raise HTTPException(422, 'Analysis filter must be all, analysed or pending')
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    week = (now + timedelta(days=7)).isoformat()
    markets = "('HOME','DRAW','AWAY')" if sport == 'football' else "('MONEYLINE_HOME','MONEYLINE_AWAY')"
    with connect() as connection:
        summary = {
            'total': connection.execute('SELECT COUNT(*) FROM sports_ai_fixtures WHERE sport=?', (sport,)).fetchone()[0],
            'today': connection.execute('SELECT COUNT(*) FROM sports_ai_fixtures WHERE sport=? AND date(starts_at)=?', (sport, today)).fetchone()[0],
            'live': connection.execute("SELECT COUNT(*) FROM sports_ai_fixtures WHERE sport=? AND status IN ('live','inplay')", (sport,)).fetchone()[0],
            'upcoming_7d': connection.execute(
                "SELECT COUNT(*) FROM sports_ai_fixtures WHERE sport=? AND status='scheduled' AND starts_at>=? AND starts_at<?",
                (sport, now.isoformat(), week),
            ).fetchone()[0],
            'competitions': connection.execute('SELECT COUNT(DISTINCT competition) FROM sports_ai_fixtures WHERE sport=?', (sport,)).fetchone()[0],
            'analysed': connection.execute(
                """SELECT COUNT(DISTINCT a.fixture_id) FROM sports_ai_match_analyses a
                   JOIN sports_ai_fixtures f ON f.id=a.fixture_id WHERE f.sport=?""",
                (sport,),
            ).fetchone()[0],
            'tickets': connection.execute('SELECT COUNT(*) FROM sports_ai_accumulator_tickets').fetchone()[0],
        }
        extra = _board_extra(sport) or {}
        summary['odds'] = None
        summary['opportunities'] = extra.get('opportunities')
        leader = connection.execute("SELECT heartbeat_at FROM sports_ai_worker_leases WHERE id='scheduler-leader'").fetchone()
        heartbeat = None if not leader else leader['heartbeat_at']
        fresh = False
        if heartbeat:
            try:
                seen = datetime.fromisoformat(str(heartbeat).replace('Z', '+00:00'))
                fresh = (now - seen).total_seconds() < 120
            except ValueError:
                fresh = False
        last_odds = None
        competitions = [row['competition'] for row in connection.execute(
            "SELECT DISTINCT competition FROM sports_ai_fixtures WHERE sport=? AND competition<>'' ORDER BY competition LIMIT 300",
            (sport,),
        )]
        logs = [dict(row) for row in connection.execute(
            'SELECT created_at, actor, action FROM sports_ai_audit_events ORDER BY created_at DESC LIMIT 20',
        )]
        logs.extend(
            {'created_at': row['finished_at'] or row['started_at'] or row['created_at'], 'actor': 'sports-ai-worker',
             'action': f"{row['status']} {row['title'] or 'browser'} ({row['observations'] or 0} observations)"}
            for row in connection.execute(
                'SELECT created_at, started_at, finished_at, status, title, observations FROM sports_ai_browser_jobs ORDER BY created_at DESC LIMIT 12',
            )
        )
        logs.extend(
            {'created_at': row['started_at'], 'actor': 'scheduler', 'action': f"{row['status']} {row['schedule_id'] or 'job'}"}
            for row in connection.execute(
                'SELECT started_at, status, schedule_id FROM sports_ai_job_history ORDER BY started_at DESC LIMIT 12',
            )
        )
        logs.sort(key=lambda row: row['created_at'] or '', reverse=True)
        logs = logs[:24]
        items, total = [], 0
        if view in ('upcoming', 'results'):
            window_sql, window_args, order = _fixture_window('upcoming' if view == 'upcoming' else 'results', now)
            filters, args = ['f.sport=?', window_sql], [sport, *window_args]
            if team:
                filters.append('(f.home LIKE ? OR f.away LIKE ?)')
                args.extend([f'%{team}%', f'%{team}%'])
            if competition:
                filters.append('f.competition=?')
                args.append(competition)
            if starts_after:
                filters.append('date(f.starts_at)>=?')
                args.append(starts_after)
            if starts_before:
                filters.append('date(f.starts_at)<=?')
                args.append(starts_before)
            if analysis == 'analysed':
                filters.append('EXISTS (SELECT 1 FROM sports_ai_match_analyses a WHERE a.fixture_id=f.id)')
            elif analysis == 'pending':
                filters.append('NOT EXISTS (SELECT 1 FROM sports_ai_match_analyses a WHERE a.fixture_id=f.id)')
            clause = ' WHERE ' + ' AND '.join(filters)
            total = connection.execute('SELECT COUNT(*) FROM sports_ai_fixtures f' + clause, args).fetchone()[0]
            items = allrows(
                connection,
                f"""SELECT f.id, f.home, f.away, f.competition, f.starts_at, f.status, f.source,
                           IFNULL((SELECT m.time_precision FROM sports_ai_fixture_source_map m
                                   WHERE m.fixture_id=f.id ORDER BY m.observed_at DESC LIMIT 1), 'instant') AS time_precision,
                           EXISTS (SELECT 1 FROM sports_ai_match_analyses a WHERE a.fixture_id=f.id) AS analysed,
                           (SELECT q.decimal_odds FROM sports_ai_odds_snapshots q
                            WHERE q.fixture_id=f.id AND q.market='h2h' AND q.outcome='home'
                            ORDER BY q.observed_at DESC LIMIT 1) AS odds_home,
                           (SELECT q.decimal_odds FROM sports_ai_odds_snapshots q
                            WHERE q.fixture_id=f.id AND q.market='h2h' AND q.outcome='draw'
                            ORDER BY q.observed_at DESC LIMIT 1) AS odds_draw,
                           (SELECT q.decimal_odds FROM sports_ai_odds_snapshots q
                            WHERE q.fixture_id=f.id AND q.market='h2h' AND q.outcome='away'
                            ORDER BY q.observed_at DESC LIMIT 1) AS odds_away,
                           (SELECT p.market FROM sports_ai_engine_predictions p
                            WHERE p.fixture_id=f.id AND p.market IN {markets}
                            ORDER BY substr(p.created_at,1,19) DESC, p.probability DESC LIMIT 1) AS market,
                           (SELECT p.probability FROM sports_ai_engine_predictions p
                            WHERE p.fixture_id=f.id AND p.market IN {markets}
                            ORDER BY substr(p.created_at,1,19) DESC, p.probability DESC LIMIT 1) AS probability,
                           (SELECT p.probability FROM sports_ai_engine_predictions p
                            WHERE p.fixture_id=f.id AND p.market IN ('HOME','MONEYLINE_HOME')
                            ORDER BY p.created_at DESC LIMIT 1) AS probability_home,
                           (SELECT p.probability FROM sports_ai_engine_predictions p
                            WHERE p.fixture_id=f.id AND p.market='DRAW'
                            ORDER BY p.created_at DESC LIMIT 1) AS probability_draw,
                           (SELECT p.probability FROM sports_ai_engine_predictions p
                            WHERE p.fixture_id=f.id AND p.market IN ('AWAY','MONEYLINE_AWAY')
                            ORDER BY p.created_at DESC LIMIT 1) AS probability_away,
                           (SELECT o.expected_edge FROM sports_ai_value_opportunities o
                            JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id
                            WHERE p.fixture_id=f.id AND o.expected_edge IS NOT NULL
                            ORDER BY o.evaluated_at DESC LIMIT 1) AS expected_edge
                    FROM sports_ai_fixtures f{clause}
                    ORDER BY {order} LIMIT ? OFFSET ?""",
                args + [page_size, (page - 1) * page_size],
            )
        elif view == 'standings':
            items = allrows(
                connection,
                """SELECT competition, season, team, position, played, won, drawn, lost, points, goal_difference, observed_at
                   FROM sports_ai_standings_snapshots
                   WHERE competition IN (SELECT DISTINCT competition FROM sports_ai_fixtures WHERE sport=?)
                   ORDER BY observed_at DESC, competition, position LIMIT ? OFFSET ?""",
                (sport, page_size, (page - 1) * page_size),
            )
            total = connection.execute(
                """SELECT COUNT(*) FROM sports_ai_standings_snapshots
                   WHERE competition IN (SELECT DISTINCT competition FROM sports_ai_fixtures WHERE sport=?)""",
                (sport,),
            ).fetchone()[0]
        elif view == 'opportunities':
            items = allrows(
                connection,
                """SELECT f.id AS fixture_id, f.home, f.away, f.competition, f.starts_at,
                          p.market, p.probability, o.status, o.expected_edge, o.reason, o.evaluated_at
                   FROM sports_ai_value_opportunities o
                   JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id
                   JOIN sports_ai_fixtures f ON f.id=p.fixture_id
                   WHERE f.sport=?
                   ORDER BY o.evaluated_at DESC LIMIT ? OFFSET ?""",
                (sport, page_size, (page - 1) * page_size),
            )
            total = connection.execute(
                """SELECT COUNT(*) FROM sports_ai_value_opportunities o
                   JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id
                   JOIN sports_ai_fixtures f ON f.id=p.fixture_id WHERE f.sport=?""",
                (sport,),
            ).fetchone()[0]
        elif view == 'tickets':
            items = allrows(
                connection,
                'SELECT id, tier, status, created_at FROM sports_ai_accumulator_tickets ORDER BY created_at DESC LIMIT ? OFFSET ?',
                (page_size, (page - 1) * page_size),
            )
            total = summary['tickets']
    return {
        'summary': summary,
        'engine': {'running': fresh, 'heartbeat_at': heartbeat, 'last_odds_at': last_odds, 'places_wager': False},
        'performance': extra.get('performance') or {
            'won': 0, 'lost': 0, 'settled': 0, 'accuracy': None, 'roi': None,
        },
        'competitions': competitions,
        'logs': logs,
        'items': items,
        'total': total,
        'page': page,
        'page_size': page_size,
    }

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
    provider_id:str,payload:ProviderUpdate,request:Request,
    x_sports_admin_key:str|None=Header(default=None),user=Depends(auth),
):
    authorize(request,'config.write',x_sports_admin_key)
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
        enqueue_prediction_job(c,'odds_updated')
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
        run_id=str(uuid.uuid4());count=0
        c.execute('INSERT INTO sports_ai_analysis_runs(id,fixture_count,selection_count,ticket_count,status,details_json) VALUES(?,?,?,?,?,?)',(run_id,len(records),len(picks),count,'complete',json.dumps({'freshness_minutes':60,'calibrated':False,'tickets':'qualification_required'})))
        for p in picks:c.execute('INSERT INTO sports_ai_predictions(id,run_id,fixture_id,sport,fixture,market,odds,probability,edge,model_name) VALUES(?,?,?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),run_id,p['event_id'],p['sport'],p['match'],p['market'],p['odds'],p['probability'],p['edge'],'baseline-v1'))
        audit(c,user,'analysis.run',{'run_id':run_id,'picks':len(picks),'tickets':0,'reason':'qualification_required'})
    return {'run_id':run_id,'fixtures':len(records),'qualifying_picks':len(picks),'tickets':0,'model_calibrated':False,'qualification_required':True}

@router.post('/training/run')
def training_run(payload:TrainingIn,user=Depends(auth)):
    sports=[payload.sport] if payload.sport else list(ANALYSED_SPORTS)
    models=[]
    with lock:
        for sport in sports:
            with connect() as c:
                models.append(train_model(c,sport))
        with connect() as c:
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
    model_id:str,request:Request,x_sports_admin_key:str|None=Header(default=None),user=Depends(auth),
):
    authorize(request,'models.activate',x_sports_admin_key)
    with lock,connect() as c:
        model=c.execute(
            """SELECT m.*,t.status AS training_status,c.sample_size AS calibration_sample_size
               FROM sports_ai_model_registry m
               JOIN sports_ai_training_runs t ON t.id=m.training_run_id
               LEFT JOIN sports_ai_calibration_results c ON c.model_id=m.id
               WHERE m.id=?""",(model_id,),
        ).fetchone()
        if not model:raise HTTPException(404,'Model not found')
        if model['sport']=='basketball':
            candidate=json.loads(model['parameters_json'] or '{}').get('basketball_loss_candidate')
            if not candidate or not c.execute("SELECT 1 FROM sports_ai_basketball_market_approvals WHERE candidate_id=? AND status='validated'",(candidate,)).fetchone():
                raise HTTPException(409,'Basketball activation requires passing loss-audit league and market holdouts')
        metrics=json.loads(model['metrics_json'])
        if (model['status']!='validated' or model['training_status']!='validated'
                or int(model['calibration_sample_size'] or 0)<10
                or metrics.get('calibration_error') is None
                or metrics['calibration_error']>.20):
            raise HTTPException(409,'Model has insufficient chronological validation or held-out calibration evidence')
        c.execute(
            "UPDATE sports_ai_model_registry SET active=0,status='inactive',lifecycle='suspended' WHERE sport=? AND active=1",
            (model['sport'],),
        )
        c.execute(
            """UPDATE sports_ai_model_registry SET active=1,status='active',lifecycle='active',activated_at=?
               WHERE id=?""",(datetime.now(timezone.utc).isoformat(),model_id),
        )
        from suitability import sync_lifecycles
        sync_lifecycles(c)
        audit(c,user,'models.activate',{'model_id':model_id,'sport':model['sport']})
    return {'activated':True,'model_id':model_id}

@router.post('/models/{model_id}/rollback')
def model_rollback(
    model_id:str,request:Request,x_sports_admin_key:str|None=Header(default=None),user=Depends(auth),
):
    authorize(request,'models.activate',x_sports_admin_key)
    with lock,connect() as c:
        result=rollback_model(c,model_id)
        if result is None:raise HTTPException(404,'Model not found')
        if not result['rolled_back']:raise HTTPException(409,result['reason'])
        audit(c,user,'models.rollback',{'model_id':model_id,**result})
    return result

@router.get('/models/lifecycle')
def model_lifecycle(user=Depends(auth)):
    from suitability import list_lifecycle
    with connect() as c:
        return list_lifecycle(c)

@router.get('/models/comparison')
def model_comparison(sport: str = 'football', user=Depends(auth)):
    from suitability import compare_models
    with connect() as c:
        return compare_models(c, sport)

@router.get('/predictions/evaluation-report')
def prediction_evaluation_report(sport: str = 'football', user=Depends(auth)):
    from suitability import evaluation_report
    with connect() as c:
        return evaluation_report(c, sport)

@router.post('/models/{model_id}/suspend')
def model_suspend(model_id:str,request:Request,x_sports_admin_key:str|None=Header(default=None),user=Depends(auth)):
    authorize(request,'models.activate',x_sports_admin_key)
    with lock,connect() as c:
        model=c.execute('SELECT id,sport,active FROM sports_ai_model_registry WHERE id=?',(model_id,)).fetchone()
        if not model:raise HTTPException(404,'Model not found')
        c.execute(
            """UPDATE sports_ai_model_registry
               SET active=0,status='inactive',lifecycle='suspended' WHERE id=?""",
            (model_id,),
        )
        audit(c,user,'models.suspend',{'model_id':model_id,'sport':model['sport']})
    return {'suspended':True,'model_id':model_id,'lifecycle':'suspended'}

class QualificationPolicyIn(BaseModel):
    odds_max_age_minutes:int|None=Field(default=None,ge=1,le=180)
    min_completeness:float|None=Field(default=None,ge=0,le=1)
    max_calibration_error:float|None=Field(default=None,gt=0,le=1)
    min_holdout_fixtures:int|None=Field(default=None,ge=1,le=10000)
    max_uncertainty:float|None=Field(default=None,ge=0,le=1)
    max_same_team:int|None=Field(default=None,ge=1,le=10)
    max_open_qualified:int|None=Field(default=None,ge=1,le=200)
    markets:dict|None=None

@router.get('/markets/registry')
def market_registry_report(user=Depends(auth)):
    from market_registry import list_registry, refresh_market_registry
    with connect() as connection:
        existing = connection.execute('SELECT COUNT(*) FROM sports_ai_market_registry').fetchone()[0]
        if not existing:
            refresh_market_registry(connection)
        return list_registry(connection)


@router.get('/qualification/summary')
def qualification_summary(user=Depends(auth)):
    from qualification_engine import summary
    with connect() as c:
        return summary(c)

@router.get('/qualification/decisions')
def qualification_decisions(
    state:str|None=None, sport:str|None=None, market:str|None=None, competition:str|None=None,
    product:str|None=None, page:int=Query(default=1,ge=1), page_size:int=Query(default=25,ge=1,le=100),
    user=Depends(auth),
):
    from qualification_engine import list_decisions
    if state and state not in ('QUALIFIED','MONITORING','REJECTED','AWAITING_DATA','EXPIRED','SUSPENDED'):
        raise HTTPException(422,'Unknown qualification state')
    if product and product not in ('booking','weekly_draw','daily_rollover'):
        raise HTTPException(422,'Unknown qualification product')
    with connect() as c:
        return list_decisions(c, state, sport, market, competition, product, page, page_size)

@router.get('/qualification/decisions/{decision_id}')
def qualification_decision(decision_id:str, user=Depends(auth)):
    from qualification_engine import decision_detail
    with connect() as c:
        detail=decision_detail(c, decision_id)
    if not detail:
        raise HTTPException(404,'Qualification decision not found')
    return detail

@router.get('/qualification/policy')
def qualification_policy(user=Depends(auth)):
    from qualification_engine import load_policy
    with connect() as c:
        return load_policy(c)

@router.put('/qualification/policy')
def qualification_policy_save(body:QualificationPolicyIn, request:Request, x_sports_admin_key:str|None=Header(default=None), user=Depends(auth)):
    authorize(request,'policy.write',x_sports_admin_key)
    from qualification_engine import revalidate, save_policy
    with lock, connect() as c:
        try:
            policy=save_policy(c, body.model_dump(exclude_none=True), user)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        audit(c, user, 'qualification.policy', {'version': policy['version']})
        revalidate(c, user)
    return policy

@router.post('/qualification/revalidate')
def qualification_revalidate(user=Depends(auth)):
    from qualification_engine import revalidate
    with lock, connect() as c:
        result=revalidate(c, user)
        audit(c, user, 'qualification.revalidate', result)
    return result

@router.get('/qualification/model-health')
def qualification_model_health(user=Depends(auth)):
    from qualification_engine import model_health
    with connect() as c:
        return model_health(c)

@router.post('/analysis/cycle')
def analysis_cycle(user=Depends(auth)):
    with lock, connect() as c:
        return analyse_upcoming_batch(c, user, audit, limit=8)


@router.post('/engine/run')
@router.post('/analysis/run')
def prediction_engine_run(
    user=Depends(auth),
    idempotency_key:str|None=Header(default=None,alias='Idempotency-Key'),
):
    key=idempotency_key or str(uuid.uuid4())
    if len(key)>160:raise HTTPException(422,'Idempotency key is too long')
    with lock,connect() as c:
        return execute_prediction_job(c,user,audit,key,'manual')

@router.get('/opportunities')
def opportunities(
    sport:SportCode|None=None,
    market:str|None=None,
    status:Literal['under_analysis','awaiting_odds','awaiting_calibration','qualified','rejected','expired','suspended']|None=None,
    bookmaker:str|None=None,
    competition:str|None=None,
    min_edge:float|None=Query(default=None,ge=-1,le=100),
    max_calibration_error:float|None=Query(default=None,ge=0,le=1),
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
    if min_edge is not None:
        conditions.append('(o.expected_edge IS NULL OR o.expected_edge>=?)')
        args.append(min_edge)
    if max_calibration_error is not None:
        conditions.append("json_extract(m.metrics_json,'$.calibration_error')<=?")
        args.append(max_calibration_error)
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
                      o.rank_score,o.uncertainty,o.data_completeness,
                      q.decimal_odds,q.bookmaker,q.observed_at AS odds_observed_at,
                      (julianday('now')-julianday(q.observed_at))*1440 AS odds_age_minutes,
                      p.id AS prediction_id,p.market,p.outcome,p.probability,p.settlement_json,
                      p.explanation_json,p.created_at AS predicted_at,
                      f.id AS fixture_id,f.sport,f.competition,f.home,f.away,f.starts_at,
                      m.id AS model_id,m.model_name,m.version AS model_version,m.status AS model_status,
                      m.metrics_json AS model_metrics,m.artifact_ref,q.source_url
               FROM sports_ai_value_opportunities o
               JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id
               JOIN sports_ai_fixtures f ON f.id=p.fixture_id
               JOIN sports_ai_model_registry m ON m.id=p.model_id
               LEFT JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id"""
            +where+' ORDER BY CASE o.status WHEN "qualified" THEN 0 ELSE 1 END,'
            +'o.rank_score IS NULL,o.rank_score DESC,o.expected_edge DESC,o.evaluated_at DESC LIMIT ?',
            tuple(args+[limit]))

@router.get('/fixtures/{fixture_id}/match-analysis')
def stored_match_analysis(fixture_id:str,user=Depends(auth)):
    with connect() as c:
        if not c.execute('SELECT 1 FROM sports_ai_fixtures WHERE id=?',(fixture_id,)).fetchone():
            raise HTTPException(404,'Fixture not found')
        report=latest_report(c,fixture_id)
    return report or {'fixture_id':fixture_id,'stored_status':None,'missing':['The worker is writing this match report from stored history. This page updates when that report is stored.']}

@router.post('/fixtures/{fixture_id}/analyze')
def run_match_analysis(fixture_id:str,user=Depends(auth)):
    with lock,connect() as c:
        try:
            return analyze_fixture(c,fixture_id,user,audit)
        except AnalysisBusy:
            raise HTTPException(409,'An analysis job is already running for this fixture')
        except LookupError:
            raise HTTPException(404,'Fixture not found')

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

_PREDICTION_MARKETS = {
    'result': "p2.market IN ('HOME','DRAW','AWAY','DOUBLE_CHANCE_1X','DOUBLE_CHANCE_12','DOUBLE_CHANCE_X2','DNB_HOME','DNB_AWAY')",
    'goals': "(p2.market GLOB 'OVER_*' OR p2.market GLOB 'UNDER_*' OR p2.market GLOB 'BTTS_*' OR p2.market GLOB 'HOME_OVER_*' OR p2.market GLOB 'HOME_UNDER_*' OR p2.market GLOB 'AWAY_OVER_*' OR p2.market GLOB 'AWAY_UNDER_*')",
    'handicap': "p2.market GLOB '*HANDICAP*'",
    'moneyline': "p2.market GLOB 'MONEYLINE_*'",
    'totals': "(p2.market GLOB 'OVER_*' OR p2.market GLOB 'UNDER_*')",
    'spreads': "p2.market GLOB '*SPREAD*'",
}
_PREDICTION_FOCUS = {
    'over': "(p2.market GLOB 'OVER_*' OR p2.market GLOB 'HOME_OVER_*' OR p2.market GLOB 'AWAY_OVER_*')",
    'under': "(p2.market GLOB 'UNDER_*' OR p2.market GLOB 'HOME_UNDER_*' OR p2.market GLOB 'AWAY_UNDER_*')",
    'spread': "p2.market GLOB '*SPREAD*'",
    'handicap': "p2.market GLOB '*HANDICAP*'",
    'moneyline': "p2.market GLOB 'MONEYLINE_*'",
    'home': "p2.market IN ('HOME','MONEYLINE_HOME','DNB_HOME')",
    'away': "p2.market IN ('AWAY','MONEYLINE_AWAY','DNB_AWAY')",
    'draw': "p2.market='DRAW'",
    'btts': "p2.market GLOB 'BTTS_*'",
    'double_chance': "p2.market GLOB 'DOUBLE_CHANCE_*'",
}
_OPPORTUNITY_STATUSES = {'under_analysis', 'awaiting_odds', 'awaiting_calibration', 'qualified', 'rejected', 'expired', 'suspended'}
_HEADLINE_MARKETS = "p2.market IN ('HOME','DRAW','AWAY','MONEYLINE_HOME','MONEYLINE_AWAY')"


def _prediction_day(value):
    if value is None or value == '':
        return None
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise HTTPException(422, 'Date filters use YYYY-MM-DD')
    return value


def _prediction_market_sql(market_group, market):
    market_sql = _PREDICTION_MARKETS.get(market_group or '', _HEADLINE_MARKETS)
    if market_group and market_group not in _PREDICTION_MARKETS:
        market_sql = _HEADLINE_MARKETS
    if market:
        focus = _PREDICTION_FOCUS.get(market)
        if not focus:
            raise HTTPException(422, 'Unknown market filter')
        market_sql = f'({market_sql}) AND ({focus})'
    return market_sql


def _prediction_filters(sport, market_group, market, team, competition, status, starts_after, starts_before):
    if sport and not re.fullmatch(r'[a-z][a-z0-9_]{0,40}', sport):
        raise HTTPException(422, 'Invalid sport code')
    market_sql = _prediction_market_sql(market_group, market)
    filters, args = [upcoming_fixture_sql('f')], []
    market_sql = f"({market_sql}) AND NOT EXISTS (SELECT 1 FROM sports_ai_settings setting, json_each(json_extract(setting.value_json,'$.blocked_ids')) blocked WHERE setting.key='model_governance' AND blocked.value=p2.model_id)"
    if sport:
        filters.append('f.sport=?')
        args.append(sport)
    if competition:
        filters.append('f.competition=?')
        args.append(competition[:120])
    if team:
        cleaned = team.replace('%', '').replace('_', ' ').strip()[:60]
        if cleaned:
            filters.append('(f.home LIKE ? OR f.away LIKE ?)')
            args.extend([f'%{cleaned}%', f'%{cleaned}%'])
    after, before = _prediction_day(starts_after), _prediction_day(starts_before)
    if after:
        filters.append("date(f.starts_at)>=?")
        args.append(after)
    if before:
        filters.append("date(f.starts_at)<=?")
        args.append(before)
    if status:
        if status not in _OPPORTUNITY_STATUSES:
            raise HTTPException(422, 'Unknown status filter')
        filters.append('o.status=?')
        args.append(status)
    return market_sql, ' AND '.join(filters), args

def _store_prediction_summary(key, sport, market_group):
    payload = None
    try:
        payload = _build_prediction_summary(sport, market_group)
    except Exception:
        payload = None
    with _SUMMARY_LOCK:
        previous = _SUMMARY_CACHE.get(key)
        stored = payload if payload is not None else (None if not previous else previous[1])
        _SUMMARY_CACHE[key] = (time.monotonic(), stored, False)


@router.get('/predictions/summary')
def prediction_summary(sport: SportCode, market_group: str | None = None, user=Depends(auth)):
    key = (sport, market_group or '')
    with _SUMMARY_LOCK:
        hit = _SUMMARY_CACHE.get(key)
        running = bool(hit and hit[2])
        stale = hit is None or time.monotonic() - hit[0] >= 15
        if stale and not running:
            _SUMMARY_CACHE[key] = (time.monotonic() if not hit else hit[0], None if not hit else hit[1], True)
            threading.Thread(
                target=_store_prediction_summary, args=(key, sport, market_group), daemon=True,
            ).start()
        payload = None if not hit else hit[1]
    if payload:
        return payload
    return {'sport': sport, 'pending': True, 'places_wager': False}


def _build_prediction_summary(sport, market_group):
    now = datetime.now(timezone.utc)
    market_sql = _prediction_market_sql(market_group, None)
    families = (
        [('moneyline', 'Moneyline', 'moneyline'), ('totals', 'Totals (O/U)', 'totals'), ('spreads', 'Spreads', 'spreads')]
        if sport == 'basketball'
        else [('result', '1X2 & Double Chance', 'result'), ('goals', 'Goals & BTTS', 'goals'), ('handicap', 'Handicaps', 'handicap')]
    )
    if sport in WINNER_SPORTS:
        families = [('moneyline', 'Match winner', 'moneyline')]
    with connect() as c:
        analysed = c.execute(
            f"""SELECT COUNT(*) FROM sports_ai_fixtures f
               WHERE f.sport=? AND {upcoming_fixture_sql('f')}
                 AND EXISTS (SELECT 1 FROM sports_ai_engine_predictions p WHERE p.fixture_id=f.id)""",
            (sport,),
        ).fetchone()[0]
        stored = c.execute(
            """SELECT COUNT(*) FROM sports_ai_engine_predictions p
               JOIN sports_ai_fixtures f ON f.id=p.fixture_id WHERE f.sport=?""",
            (sport,),
        ).fetchone()[0]
        qualified = c.execute(
            """SELECT COUNT(*) FROM sports_ai_value_opportunities o
               JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id
               JOIN sports_ai_fixtures f ON f.id=p.fixture_id
               WHERE f.sport=? AND o.status='qualified' AND o.expected_edge>0""",
            (sport,),
        ).fetchone()[0]
        edge = c.execute(
            """SELECT AVG(o.expected_edge) FROM sports_ai_value_opportunities o
               JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id
               JOIN sports_ai_fixtures f ON f.id=p.fixture_id
               WHERE f.sport=? AND o.status='qualified' AND o.expected_edge>0""",
            (sport,),
        ).fetchone()[0]
        tickets = c.execute(
            "SELECT COUNT(*) FROM sports_ai_accumulator_tickets WHERE sport_mode=?",
            (sport,),
        ).fetchone()[0]
        tickets_today = c.execute(
            "SELECT COUNT(*) FROM sports_ai_accumulator_tickets WHERE sport_mode=? AND date(created_at)=date('now')",
            (sport,),
        ).fetchone()[0]
        opportunities_today = c.execute(
            """SELECT COUNT(*) FROM sports_ai_value_opportunities o
               JOIN sports_ai_engine_predictions p ON p.id=o.prediction_id
               JOIN sports_ai_fixtures f ON f.id=p.fixture_id
               WHERE f.sport=? AND o.status='qualified' AND date(o.evaluated_at)=date('now')""",
            (sport,),
        ).fetchone()[0]
        bands = c.execute(
            f"""SELECT
                  SUM(CASE WHEN p.probability>=0.65 THEN 1 ELSE 0 END) AS high,
                  SUM(CASE WHEN p.probability>=0.55 AND p.probability<0.65 THEN 1 ELSE 0 END) AS medium,
                  SUM(CASE WHEN p.probability<0.55 THEN 1 ELSE 0 END) AS low
                FROM sports_ai_fixtures f
                JOIN sports_ai_engine_predictions p ON p.id=(
                  SELECT p2.id FROM sports_ai_engine_predictions p2
                  WHERE p2.fixture_id=f.id AND {market_sql}
                  ORDER BY p2.created_at DESC, p2.probability DESC LIMIT 1)
                WHERE f.sport=? AND {upcoming_fixture_sql('f')}""",
            (sport,),
        ).fetchone()
        coverage = []
        for key, label, group in families:
            family_sql = _PREDICTION_MARKETS[group].replace('p2.', 'p.')
            count = c.execute(
                f"""SELECT COUNT(*) FROM sports_ai_fixtures f
                    WHERE f.sport=? AND {upcoming_fixture_sql('f')}
                      AND EXISTS (SELECT 1 FROM sports_ai_engine_predictions p
                                  WHERE p.fixture_id=f.id AND {family_sql})""",
                (sport,),
            ).fetchone()[0]
            coverage.append({'key': key, 'label': label, 'count': count})
        settled = c.execute(
            """SELECT
                 SUM(CASE WHEN (p.market IN ('HOME','MONEYLINE_HOME') AND r.home_score>r.away_score)
                            OR (p.market IN ('AWAY','MONEYLINE_AWAY') AND r.away_score>r.home_score)
                            OR (p.market='DRAW' AND r.home_score=r.away_score) THEN 1 ELSE 0 END) AS won,
                 COUNT(*) AS total
               FROM sports_ai_engine_predictions p
               JOIN sports_ai_fixtures f ON f.id=p.fixture_id
               JOIN sports_ai_results r ON r.fixture_id=f.id AND r.status='final'
               WHERE f.sport=? AND p.market IN ('HOME','DRAW','AWAY','MONEYLINE_HOME','MONEYLINE_AWAY')
                 AND p.created_at<f.starts_at""",
            (sport,),
        ).fetchone()
        series = allrows(c,
            """SELECT date(f.starts_at) AS day,
                      SUM(CASE WHEN (p.market IN ('HOME','MONEYLINE_HOME') AND r.home_score>r.away_score)
                                 OR (p.market IN ('AWAY','MONEYLINE_AWAY') AND r.away_score>r.home_score)
                                 OR (p.market='DRAW' AND r.home_score=r.away_score) THEN 1 ELSE 0 END) AS won,
                      COUNT(*) AS settled
               FROM sports_ai_engine_predictions p
               JOIN sports_ai_fixtures f ON f.id=p.fixture_id
               JOIN sports_ai_results r ON r.fixture_id=f.id AND r.status='final'
               WHERE f.sport=? AND p.market IN ('HOME','DRAW','AWAY','MONEYLINE_HOME','MONEYLINE_AWAY')
                 AND p.created_at<f.starts_at AND date(f.starts_at)>=date('now','-30 days')
               GROUP BY date(f.starts_at) ORDER BY day""",
            (sport,))
        model = c.execute(
            """SELECT model_name, version, status, active, lifecycle FROM sports_ai_model_registry
               WHERE sport=? ORDER BY created_at DESC LIMIT 1""",
            (sport,),
        ).fetchone()
        provider_keys = ('openfootball', 'openligadb', 'football-data') if sport == 'football' else ('sportsdataverse', 'balldontlie', 'basketball-csv')
        sources = [row['display_name'] for row in c.execute(
            f"""SELECT display_name FROM sports_ai_provider_registry
                WHERE enabled=1 AND status='operational' AND provider_key IN ({','.join('?' for _ in provider_keys)})
                ORDER BY display_name""",
            provider_keys,
        )]
        priced = c.execute(
            """SELECT 1 FROM sports_ai_odds_snapshots q
               JOIN sports_ai_fixtures f ON f.id=q.fixture_id
               WHERE f.sport=? AND q.bookmaker='SportyBet' LIMIT 1""",
            (sport,),
        ).fetchone()
        if priced:
            sources.append('SportyBet displayed prices')
        leader = c.execute("SELECT heartbeat_at FROM sports_ai_worker_leases WHERE id='scheduler-leader'").fetchone()
        fresh = False
        if leader and leader['heartbeat_at']:
            try:
                seen = datetime.fromisoformat(str(leader['heartbeat_at']).replace('Z', '+00:00'))
                fresh = (now - seen).total_seconds() < 120
            except ValueError:
                fresh = False
        nxt = c.execute(
            """SELECT next_run_at FROM sports_ai_schedules
               WHERE enabled=1 AND job_kind='prediction' ORDER BY COALESCE(next_run_at,'9999') LIMIT 1""",
        ).fetchone()
        competitions = [row['competition'] for row in c.execute(
            f"""SELECT DISTINCT f.competition FROM sports_ai_fixtures f
               WHERE f.sport=? AND {upcoming_fixture_sql('f')} AND f.competition<>''
                 AND EXISTS (SELECT 1 FROM sports_ai_engine_predictions p WHERE p.fixture_id=f.id)
               ORDER BY f.competition LIMIT 200""",
            (sport,),
        )]
        won, total_settled = (settled['won'] or 0), (settled['total'] or 0)
        high, medium, low = (bands['high'] or 0), (bands['medium'] or 0), (bands['low'] or 0)
        return {
            'sport': sport,
            'fixtures_analysed': analysed,
            'predictions_stored': stored,
            'qualified': qualified,
            'accuracy': None if not total_settled else round(100 * won / total_settled, 1),
            'settled': total_settled,
            'average_edge': None if edge is None else round(100 * edge, 1),
            'tickets': tickets,
            'tickets_today': tickets_today,
            'opportunities_today': opportunities_today,
            'bands': {'high': high, 'medium': medium, 'low': low, 'total': high + medium + low},
            'families': coverage,
            'series': series,
            'model': None if not model else {
                'name': model['model_name'], 'version': model['version'],
                'status': model['status'], 'active': bool(model['active']),
                'lifecycle': model['lifecycle'] if 'lifecycle' in model.keys() else 'research',
            },
            'sources': sources,
            'next_run_at': None if not nxt else nxt['next_run_at'],
            'scheduler_fresh': fresh,
            'competitions': competitions,
            'places_wager': False,
        }


@router.get('/predictions')
def predictions(
    group: str | None = None,
    sport: str | None = None,
    market_group: str | None = None,
    market: str | None = None,
    team: str | None = None,
    competition: str | None = None,
    status: str | None = None,
    starts_after: str | None = None,
    starts_before: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    user=Depends(auth),
):
    with connect() as c:
        if group == 'fixture':
            market_sql, where, args = _prediction_filters(
                sport, market_group, market, team, competition, status, starts_after, starts_before,
            )
            joined = f"""
                FROM sports_ai_fixtures f
                JOIN sports_ai_engine_predictions p ON p.id=(
                    SELECT p2.id FROM sports_ai_engine_predictions p2
                    WHERE p2.fixture_id=f.id AND {market_sql}
                    ORDER BY p2.created_at DESC, p2.probability DESC LIMIT 1
                )
                JOIN sports_ai_model_registry m ON m.id=p.model_id
                LEFT JOIN sports_ai_market_suitability s ON s.prediction_id=p.id
                LEFT JOIN sports_ai_value_opportunities o ON o.id=(
                    SELECT o2.id FROM sports_ai_value_opportunities o2
                    WHERE o2.prediction_id=p.id
                    ORDER BY CASE WHEN o2.odds_id IS NULL THEN 1 ELSE 0 END, o2.evaluated_at DESC
                    LIMIT 1
                )
                LEFT JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
                WHERE {where}"""
            total = c.execute(
                f'SELECT COUNT(*) {joined}',
                args,
            ).fetchone()[0]
            rows = allrows(c, f"""
                SELECT f.id AS fixture_id, f.sport, f.competition, f.home, f.away, f.starts_at, f.status AS fixture_status,
                       m.model_name, m.version AS model_version, m.status AS model_status, m.active AS model_active,
                       m.lifecycle, s.rank_score AS suitability_rank, s.booking_ready,
                       p.id AS prediction_id, p.explanation_json,
                       p.market, p.probability, p.run_id, o.expected_edge AS edge, o.status AS opportunity_status,
                       o.reason AS opportunity_reason, q.decimal_odds AS odds,
                       (SELECT COUNT(*) FROM sports_ai_engine_predictions x
                        WHERE x.fixture_id=f.id AND x.run_id=p.run_id) AS market_count,
                       (SELECT a.status FROM sports_ai_match_analyses a
                        WHERE a.fixture_id=f.id ORDER BY a.created_at DESC LIMIT 1) AS analysis_status
                {joined}
                ORDER BY {upcoming_order_sql('f')}
                LIMIT ? OFFSET ?""", args + [page_size, (page - 1) * page_size])
            for row in rows:
                row.pop('explanation_json', None)
                row['booking_ready'] = bool(row.get('booking_ready'))
            return {'items': rows, 'total': total, 'page': page, 'page_size': page_size}
        rows=allrows(c,
            """SELECT p.id,p.fixture_id,f.sport,p.market,p.probability,
                      f.home||' vs '||f.away AS fixture,f.starts_at,
                      m.model_name,q.decimal_odds AS odds,o.expected_edge AS edge,
                      o.status AS opportunity_status
               FROM sports_ai_engine_predictions p
               JOIN sports_ai_fixtures f ON f.id=p.fixture_id
               JOIN sports_ai_model_registry m ON m.id=p.model_id
               LEFT JOIN sports_ai_value_opportunities o ON o.prediction_id=p.id
               LEFT JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
               ORDER BY p.created_at DESC LIMIT 500""")
        if rows:return rows
        return allrows(c,'SELECT * FROM sports_ai_predictions ORDER BY rowid DESC LIMIT 500')

@router.get('/tickets')
def tickets(tier:int|None=Query(None),user=Depends(auth)):
    with connect() as c:
        q='SELECT t.*,b.code AS booking_code,b.status AS booking_status FROM sports_ai_tickets t LEFT JOIN sports_ai_booking_codes b ON b.ticket_id=t.id'
        return allrows(c,q+(' WHERE tier=?' if tier else '')+' ORDER BY t.rowid DESC LIMIT 500',(tier,) if tier else ())

@router.post('/tickets/{ticket_id}/booking-code')
def booking_code(ticket_id:str,payload:BookingCodeIn,request:Request,x_sports_admin_key:str|None=Header(default=None),user=Depends(auth)):
    authorize(request,'booking.execute',x_sports_admin_key)
    with lock,connect() as c:
        if not c.execute('SELECT 1 FROM sports_ai_tickets WHERE id=?',(ticket_id,)).fetchone():raise HTTPException(404,'Ticket not found')
        from qualification_engine import ticket_is_bookable
        if not ticket_is_bookable(c, ticket_id, 'sports_ai_ticket_legs'):
            raise HTTPException(409,'A booking code requires a current QUALIFIED decision for every leg')
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
    max_legs:int=Field(ge=1,le=25)
    odds_tolerance:float=Field(ge=0.01,le=0.5)
    odds_max_age_minutes:int=Field(default=15,ge=1,le=1440)
    form_half_life_days:float=Field(default=180,gt=0,le=3650)
    autonomous_tickets:bool=True
class RoleIn(BaseModel):
    role_name:str=Field(min_length=2,max_length=80)
    permissions:list[str]

@router.get('/results')
def results(user=Depends(auth)):
    with connect() as c:return allrows(c,'SELECT r.*,f.home,f.away,f.sport,f.competition FROM sports_ai_results r JOIN sports_ai_fixtures f ON f.id=r.fixture_id ORDER BY r.settled_at DESC')
@router.post('/results')
def save_result(p:ResultIn,request:Request,x_sports_admin_key:str|None=Header(default=None),user=Depends(auth)):
    authorize(request,'config.write',x_sports_admin_key)
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
        enqueue_prediction_job(c,'results_updated')
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
    defaults={'min_edge':0.05,'max_legs':6,'odds_tolerance':0.15,'odds_max_age_minutes':15,'form_half_life_days':180}
    return {**defaults, **(json.loads(r[0]) if r else {})}
@router.put('/settings')
def settings_save(p:SettingsIn,request:Request,x_sports_admin_key:str|None=Header(default=None),user=Depends(auth)):
    authorize(request,'config.write',x_sports_admin_key)
    with lock,connect() as c:
        c.execute("INSERT INTO sports_ai_settings(key,value_json) VALUES('engine',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=CURRENT_TIMESTAMP",(p.model_dump_json(),))
        audit(c,user,'settings.update',p.model_dump())
    return p
@router.get('/roles')
def roles(user=Depends(auth)):
    with connect() as c:return allrows(c,'SELECT * FROM sports_ai_roles ORDER BY role_name')
@router.post('/roles')
def role_add(p:RoleIn,request:Request,x_sports_admin_key:str|None=Header(default=None),user=Depends(auth)):
    authorize(request,'credentials.manage',x_sports_admin_key)
    with lock,connect() as c:
        c.execute('INSERT INTO sports_ai_roles(id,role_name,permissions_json) VALUES(?,?,?) ON CONFLICT(role_name) DO UPDATE SET permissions_json=excluded.permissions_json',(str(uuid.uuid4()),p.role_name,json.dumps(p.permissions)))
        audit(c,user,'roles.upsert',p.model_dump())
    return {'saved':True,'note':'Stored role permissions are enforced on privileged API operations. Loopback preview remains development-only.'}
@router.get('/ticket-legs/{ticket_id}')
def ticket_legs(ticket_id:str,user=Depends(auth)):
    with connect() as c:return allrows(c,'SELECT l.*,f.home,f.away,f.sport FROM sports_ai_ticket_legs l JOIN sports_ai_fixtures f ON f.id=l.fixture_id WHERE l.ticket_id=?',(ticket_id,))
@router.get('/analysis-runs')
def analysis_runs(user=Depends(auth)):
    with connect() as c:return allrows(c,'SELECT * FROM sports_ai_analysis_runs ORDER BY created_at DESC LIMIT 100')

@router.get('/engine/predictions')
def engine_predictions(
    sport:SportCode|None=None,
    user=Depends(auth),
):
    with connect() as c:
        query="""SELECT p.id,p.fixture_id,f.sport,p.market,p.outcome,p.probability,
                        p.settlement_json,p.explanation_json,p.created_at,
                        f.home||' vs '||f.away AS fixture,f.competition,f.starts_at,
                        m.model_name,m.version AS model_version,m.status AS model_status,
                        (SELECT o.status FROM sports_ai_value_opportunities o
                         WHERE o.prediction_id=p.id ORDER BY o.evaluated_at DESC LIMIT 1) AS opportunity_status
                 FROM sports_ai_engine_predictions p
                 JOIN sports_ai_fixtures f ON f.id=p.fixture_id
                 JOIN sports_ai_model_registry m ON m.id=p.model_id"""
        if sport:query+=' WHERE f.sport=?'
        return allrows(c,query+' ORDER BY p.created_at DESC LIMIT 1000',(sport,) if sport else ())
@router.get('/engine/jobs')
def prediction_jobs(user=Depends(auth)):
    with connect() as c:
        return allrows(c,'SELECT * FROM sports_ai_prediction_jobs ORDER BY created_at DESC LIMIT 200')

@router.get('/odds/intelligence')
def odds_intelligence(user=Depends(auth)):
    with connect() as c:
        latest=allrows(c,"""
            SELECT id,fixture_id,sport,competition,home,away,starts_at,bookmaker,
                   market,outcome,line,decimal_odds,observed_at,source_url,
                   provider,provider_status,age_minutes,opening_decimal_odds
            FROM (
              SELECT q.id,q.fixture_id,f.sport,f.competition,f.home,f.away,f.starts_at,q.bookmaker,
                     q.market,q.outcome,q.line,q.decimal_odds,q.observed_at,q.source_url,
                     p.display_name AS provider,p.status AS provider_status,
                     (julianday('now')-julianday(q.observed_at))*1440 AS age_minutes,
                     (SELECT o.decimal_odds FROM sports_ai_odds_snapshots o
                      WHERE o.fixture_id=q.fixture_id AND o.bookmaker=q.bookmaker
                        AND o.market=q.market AND o.outcome=q.outcome
                        AND IFNULL(o.line,-999999)=IFNULL(q.line,-999999)
                      ORDER BY o.observed_at ASC, o.id ASC LIMIT 1) AS opening_decimal_odds,
                     ROW_NUMBER() OVER (
                       PARTITION BY q.fixture_id,q.bookmaker,q.market,q.outcome,IFNULL(q.line,-999999)
                       ORDER BY q.observed_at DESC, q.id DESC
                     ) AS quote_rank
              FROM sports_ai_odds_snapshots q
              JOIN sports_ai_fixtures f ON f.id=q.fixture_id
              JOIN sports_ai_provider_registry p ON p.id=q.provider_id
            )
            WHERE quote_rank=1
            ORDER BY observed_at DESC LIMIT 500""")
        providers=allrows(c,"""
            SELECT id,provider_key,display_name,sport,adapter,enabled,terms_reviewed,status,
                   last_success_at,last_error,last_attempt_at
            FROM sports_ai_provider_registry ORDER BY display_name""")
        duplicates=allrows(c,"""
            SELECT fixture_id,bookmaker,market,outcome,line,observed_at,COUNT(*) AS quotes,
                   MIN(decimal_odds) AS low_odds,MAX(decimal_odds) AS high_odds
            FROM sports_ai_odds_snapshots
            GROUP BY fixture_id,bookmaker,market,outcome,line,observed_at
            HAVING COUNT(*)>1 AND MIN(decimal_odds)<>MAX(decimal_odds)
            LIMIT 100""")
    return {'quotes':latest,'providers':providers,'conflicting_duplicates':duplicates,
            'note':'Only imported provider quotes are listed. Missing prices are not estimated.'}

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

class AutonomousControl(BaseModel):
    action:Literal['start','pause','resume','stop']

@router.get('/autonomous/overview')
def autonomous_overview(user=Depends(auth)):
    # 27-45 s to build (several scans of the 4.6M-row price table); served from a background-refreshed copy.
    from autonomous_engine import build_overview
    from view_cache import cached_view

    def build():
        with connect() as c:
            return build_overview(c)
    return cached_view('autonomous_overview', build, ttl=60)

@router.post('/bookings/diagnostic')
def booking_diagnostic(request:Request, x_sports_admin_key:str|None=Header(default=None), user=Depends(auth)):
    authorize(request,'diagnostics.admin',x_sports_admin_key)
    from sportybet_booking import diagnose_booking
    with connect() as c:
        report=diagnose_booking(c)
        audit(c, user, 'booking.diagnostic', {'places_wager': False, 'publishes_code': False, 'blockers': [item['code'] for item in report['blockers']]})
    return report

@router.get('/autonomous/readiness')
def autonomous_readiness(user=Depends(auth)):
    from autonomous_engine import production_readiness
    with connect() as c:
        return production_readiness(c)

@router.post('/autonomous/control')
def autonomous_control(body:AutonomousControl,request:Request,x_sports_admin_key:str|None=Header(default=None),user=Depends(auth)):
    authorize(request,'config.write',x_sports_admin_key)
    from autonomous_engine import apply_control
    with lock,connect() as c:
        return apply_control(c,body.action,audit,user)

app.include_router(router)
app.include_router(build_acquisition_router(auth))
app.include_router(build_accumulator_router(auth, require_admin_key))
from high_odds_api import build_high_odds_router
app.include_router(build_high_odds_router(auth, require_admin_key))
app.include_router(build_operations_router(auth, require_admin_key))
app.include_router(build_sportybet_router(auth, require_admin_key))
app.include_router(build_browser_router(auth))
app.include_router(build_booking_router(auth))
app.include_router(build_draw_router(auth))
app.include_router(build_weekly_draw_router(auth, require_admin_key))
app.include_router(build_rollover_router(auth))
app.include_router(build_learning_router(auth, audit))
from basketball_loss_api import build_basketball_loss_router
app.include_router(build_basketball_loss_router(auth, require_admin_key))
from autonomy_api import build_autonomy_router
app.include_router(build_autonomy_router(auth, require_admin_key))
from universal_intelligence import build_universal_router
app.include_router(build_universal_router(auth))
from prediction_integrity import build_integrity_router
app.include_router(build_integrity_router(auth, require_admin_key))
from single_betting import build_single_betting_router
app.include_router(build_single_betting_router(auth, require_admin_key))
from sportybet_account import build_account_router
app.include_router(build_account_router(auth, require_admin_key))
from league_coverage import build_coverage_router
app.include_router(build_coverage_router(auth))


@app.on_event('startup')
def _warm_slow_views():
    """Build the two slow page payloads in the background at start, so the first visitor does not wait."""
    def warm():
        for name, build in (('dashboard', lambda: dashboard(user='startup-warmup')),
                            ('autonomous_overview', lambda: autonomous_overview(user='startup-warmup'))):
            try:
                build()
            except Exception as error:
                print('view warm-up failed', name, error)
    threading.Thread(target=warm, daemon=True, name='view-cache-warmup').start()
