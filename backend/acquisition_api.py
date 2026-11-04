"""Sports-only source administration, collections, research and browser endpoints."""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from acquisition import CollectionError
from acquisition_engine import ADAPTER_INFO, run_collection, test_provider, validate_public_config
from browser_operator import BrowserResearchError, research_pages, validate_research_url
from db import DB, connect

_FIELDS = {'h2h', 'form', 'standings', 'statistics', 'odds'}
_SUPPORTED_ADAPTERS = {
    'football_data','openligadb','openfootball','balldontlie',
    'sportsdataverse_csv','the_odds_api','browser','authorized_json',
}
_ADMIN_SOURCE_KEYS = {'csv_import': 'basketball-csv'}


class SourceCreate(BaseModel):
    source_key: str = Field(min_length=2, max_length=80, pattern=r'^[a-z0-9][a-z0-9_-]+$')
    display_name: str = Field(min_length=2, max_length=120)
    adapter: str
    config: dict = Field(default_factory=dict)


class SourceUpdate(BaseModel):
    config: dict
    terms_reviewed: bool
    enabled: bool
    interval_minutes: int = Field(ge=5, le=10080)


class PermissionUpdate(BaseModel):
    url: str = Field(min_length=10, max_length=2000)
    terms_url: str = Field(min_length=10, max_length=2000)
    notes: str = Field(default='', max_length=1000)
    approved: bool


class ResearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    urls: list[str] = Field(min_length=1, max_length=10)
    fixture_id: str | None = None


class ExtractionUpdate(BaseModel):
    fields: dict[str, str]


class CatalogSportCreate(BaseModel):
    code: str = Field(min_length=2, max_length=41, pattern=r'^[a-z][a-z0-9_]+$')
    name: str = Field(min_length=2, max_length=120)
    notes: str = ''


class CatalogBackfillRequest(BaseModel):
    source_ids: list[str] = Field(default_factory=list, max_length=50)


class CatalogPolicy(BaseModel):
    per_day: int = Field(ge=0, le=24)


def _safe_json(value, fallback):
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return fallback


def _insert_quality(connection, source_id, issue_type, message, severity='medium'):
    connection.execute(
        """INSERT INTO sports_ai_data_quality_issues
           (id,provider_id,severity,issue_type,details_json)
           VALUES(?,?,?,?,?)""",
        (str(uuid.uuid4()),source_id,severity,issue_type,
         json.dumps({'message':str(message)[:500]})),
    )


def build_acquisition_router(auth_dependency):
    router = APIRouter(prefix='/api/sports-ai', tags=['Sports AI Acquisition'])

    @router.get('/acquisition/providers')
    def list_sources(user=Depends(auth_dependency)):
        with connect() as connection:
            rows = connection.execute(
                """SELECT p.*,s.enabled AS schedule_enabled,s.interval_minutes AS schedule_interval,
                          s.paused AS schedule_paused
                   FROM sports_ai_provider_registry p
                   LEFT JOIN sports_ai_acquisition_schedules s ON s.provider_id=p.id
                   ORDER BY p.display_name"""
            ).fetchall()
            result = []
            for row in rows:
                source = dict(row)
                source['config'] = _safe_json(source.pop('config_json', '{}'), {})
                source['credential_configured'] = bool(
                    source.get('credential_env') and os.environ.get(source['credential_env'])
                )
                source['requires_api_key'] = bool(source.get('credential_env'))
                if source['schedule_interval'] is not None:
                    source['interval_minutes'] = source['schedule_interval']
                    source['enabled'] = bool(source['schedule_enabled']) and not bool(source['schedule_paused'])
                source['terms_reviewed'] = bool(source['terms_reviewed'])
                source['enabled'] = bool(source['enabled'])
                source['next_run_at'] = source.get('next_run_at')
                source['sync_info'] = {}
                result.append(source)
            return result

    @router.post('/acquisition/sources')
    def add_source(payload: SourceCreate, user=Depends(auth_dependency)):
        info = ADAPTER_INFO.get(payload.adapter)
        if not info or payload.adapter not in _SUPPORTED_ADAPTERS:
            raise HTTPException(422, 'Choose a supported provider adapter')
        try:
            validate_public_config(payload.config)
        except CollectionError as error:
            raise HTTPException(422,str(error)) from error
        provider_key = _ADMIN_SOURCE_KEYS.get(payload.adapter, payload.source_key)
        if payload.adapter == 'authorized_json':
            catalog_sport = str(payload.config.get('catalog_sport') or '')
            feed_url = str(payload.config.get('feed_url') or '')
            if not re.fullmatch(r'[a-z][a-z0-9_]{1,40}', catalog_sport) or not feed_url.startswith('https://'):
                raise HTTPException(422, 'authorized_json requires catalog_sport and an https feed_url')
        with connect() as connection:
            if payload.adapter == 'authorized_json':
                known = connection.execute(
                    'SELECT 1 FROM sports_ai_sports WHERE code=?',
                    (str(payload.config.get('catalog_sport')),),
                ).fetchone()
                if not known:
                    raise HTTPException(422, 'Register the sport before attaching an authorized feed')
            duplicate = connection.execute(
                'SELECT 1 FROM sports_ai_provider_registry WHERE provider_key=? OR id=?',
                (provider_key,payload.source_key),
            ).fetchone()
            if duplicate:
                raise HTTPException(409, 'A source with this key is already registered')
            provider_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO sports_ai_provider_registry
                   (id,provider_key,display_name,sport,adapter,status,config_json,
                    terms_url,credential_env)
                   VALUES(?,?,?,?,?,'disabled',?,?,?)""",
                (provider_id,provider_key,payload.display_name.strip(),
                 info['sport'],payload.adapter,
                 json.dumps(payload.config,allow_nan=False),info['terms_url'],
                 info['credential_env']),
            )
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """INSERT INTO sports_ai_acquisition_schedules
                   (provider_id,enabled,interval_minutes,paused,updated_by,updated_at)
                   VALUES(?,0,360,1,?,?)""",
                (provider_id,user,now),
            )
            connection.execute(
                """INSERT INTO sports_ai_audit_events(id,actor,action,metadata_json)
                   VALUES(?,?,?,?)""",
                (str(uuid.uuid4()),user,'acquisition.source.created',
                 json.dumps({'provider_id':provider_id,'adapter':payload.adapter})),
            )
        return {'id':provider_id,'provider_key':provider_key,'status':'disabled'}

    @router.put('/acquisition/sources/{source_id}')
    def update_source(source_id: str, payload: SourceUpdate, user=Depends(auth_dependency)):
        try:
            validate_public_config(payload.config)
        except CollectionError as error:
            raise HTTPException(422,str(error)) from error
        with connect() as connection:
            source = connection.execute(
                'SELECT * FROM sports_ai_provider_registry WHERE id=?', (source_id,),
            ).fetchone()
            if not source:
                raise HTTPException(404, 'Source not found')
            if payload.enabled and not payload.terms_reviewed:
                raise HTTPException(422, 'Review source terms before enabling collection')
            if payload.enabled and source['credential_env'] and not os.environ.get(source['credential_env']):
                raise HTTPException(
                    422, f"Configure {source['credential_env']} in the backend environment before enabling this source",
                )
            adapter = source['adapter']
            if adapter == 'browser' and payload.enabled:
                raise HTTPException(422, 'Browser research references cannot be scheduled as API providers')
            connection.execute(
                """UPDATE sports_ai_provider_registry SET config_json=?,terms_reviewed=?,
                   enabled=?,status=CASE WHEN ?=0 THEN 'disabled' ELSE
                   CASE WHEN status IN ('disabled','untested') THEN 'untested' ELSE status END END
                   WHERE id=?""",
                (json.dumps(payload.config,allow_nan=False),int(payload.terms_reviewed),
                 int(payload.enabled),int(payload.enabled),source_id),
            )
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """INSERT INTO sports_ai_acquisition_schedules
                   (provider_id,enabled,interval_minutes,paused,updated_by,updated_at)
                   VALUES(?,?,?,0,?,?)
                   ON CONFLICT(provider_id) DO UPDATE SET enabled=excluded.enabled,
                   interval_minutes=excluded.interval_minutes,paused=0,
                   updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                (source_id,int(payload.enabled),payload.interval_minutes,user,now),
            )
            connection.execute(
                """UPDATE sports_ai_provider_registry SET interval_minutes=?,next_run_at=?
                   WHERE id=?""",
                (payload.interval_minutes,
                 (datetime.now(timezone.utc).replace(microsecond=0)).isoformat(),
                 source_id),
            )
            connection.execute(
                """INSERT INTO sports_ai_audit_events(id,actor,action,metadata_json)
                   VALUES(?,?,?,?)""",
                (str(uuid.uuid4()),user,'acquisition.source.updated',
                 json.dumps({'provider_id':source_id,'enabled':payload.enabled,
                             'terms_reviewed':payload.terms_reviewed})),
            )
            return {'saved':True}

    @router.post('/acquisition/sources/{source_id}/test')
    def test_source(source_id: str, user=Depends(auth_dependency)):
        with connect() as connection:
            row = connection.execute(
                'SELECT * FROM sports_ai_provider_registry WHERE id=?', (source_id,),
            ).fetchone()
        if not row:
            raise HTTPException(404, 'Source not found')
        try:
            result = test_provider(dict(row))
        except (CollectionError, ValueError, TypeError) as error:
            with connect() as connection:
                connection.execute(
                    """UPDATE sports_ai_provider_registry SET status='error',
                       last_attempt_at=?,last_error=? WHERE id=?""",
                    (datetime.now(timezone.utc).isoformat(),str(error)[:500],source_id),
                )
                _insert_quality(connection,source_id,'provider_connection_test_failed',error)
            raise HTTPException(502, str(error)) from error
        with connect() as connection:
            connection.execute(
                """UPDATE sports_ai_provider_registry SET status=CASE
                   WHEN status='operational' THEN status ELSE 'tested' END,
                   last_attempt_at=?,last_error=NULL WHERE id=?""",
                (datetime.now(timezone.utc).isoformat(),source_id),
            )
        return result

    @router.post('/acquisition/sources/{source_id}/collect')
    def collect_source(source_id: str, user=Depends(auth_dependency)):
        try:
            return run_collection(source_id,actor=user)
        except CollectionError as error:
            raise HTTPException(502, str(error)) from error
        except Exception as error:
            raise HTTPException(502, 'Collection failed; inspect persisted acquisition job logs') from error

    @router.post('/acquisition/sources/{source_id}/pause')
    def pause_source(source_id: str, user=Depends(auth_dependency)):
        with connect() as connection:
            if not connection.execute(
                'SELECT 1 FROM sports_ai_provider_registry WHERE id=?', (source_id,),
            ).fetchone():
                raise HTTPException(404, 'Source not found')
            connection.execute(
                """UPDATE sports_ai_acquisition_schedules SET paused=1,enabled=0,
                   updated_by=?,updated_at=? WHERE provider_id=?""",
                (user,datetime.now(timezone.utc).isoformat(),source_id),
            )
            connection.execute(
                "UPDATE sports_ai_provider_registry SET enabled=0,status='paused' WHERE id=?",
                (source_id,),
            )
        return {'paused':True}

    @router.post('/acquisition/sources/{source_id}/disable')
    def disable_source(source_id: str, user=Depends(auth_dependency)):
        with connect() as connection:
            if not connection.execute(
                'SELECT 1 FROM sports_ai_provider_registry WHERE id=?', (source_id,),
            ).fetchone():
                raise HTTPException(404, 'Source not found')
            connection.execute(
                """UPDATE sports_ai_provider_registry SET enabled=0,status='disabled'
                   WHERE id=?""", (source_id,),
            )
            connection.execute(
                """UPDATE sports_ai_acquisition_schedules SET enabled=0,paused=1,
                   updated_by=?,updated_at=? WHERE provider_id=?""",
                (user,datetime.now(timezone.utc).isoformat(),source_id),
            )
        return {'disabled':True}

    @router.get('/acquisition/jobs')
    def acquisition_jobs(limit: int = Query(default=100, ge=1, le=500), user=Depends(auth_dependency)):
        with connect() as connection:
            rows = connection.execute(
                """SELECT j.id,j.provider_id,j.kind,j.status,j.attempt,j.started_at AS created_at,
                          j.finished_at,j.records_seen,j.records_saved,j.error,
                          COALESCE(p.display_name,'Browser research') AS source_name
                   FROM sports_ai_acquisition_jobs j
                   LEFT JOIN sports_ai_provider_registry p ON p.id=j.provider_id
                   ORDER BY j.started_at DESC LIMIT ?""", (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    @router.get('/acquisition/jobs/{job_id}/logs')
    def acquisition_job_logs(job_id: str, user=Depends(auth_dependency)):
        with connect() as connection:
            row = connection.execute(
                'SELECT * FROM sports_ai_acquisition_jobs WHERE id=?', (job_id,),
            ).fetchone()
            if not row:
                raise HTTPException(404, 'Acquisition job not found')
            job = dict(row)
            job['logs'] = _safe_json(job.pop('logs_json'), [])
            return job

    @router.post('/acquisition/jobs/{job_id}/retry')
    def retry_job(job_id: str, user=Depends(auth_dependency)):
        with connect() as connection:
            row = connection.execute(
                'SELECT provider_id,session_id,status,kind FROM sports_ai_acquisition_jobs WHERE id=?',
                (job_id,),
            ).fetchone()
        if not row:
            raise HTTPException(404, 'Acquisition job not found')
        if row['session_id'] and row['kind']=='browser_research':
            if row['status'] not in ('failed','partial'):
                raise HTTPException(409,'Only failed or partial browser sessions can be retried')
            return retry_browser_session(row['session_id'],user)
        if row['status'] != 'failed' or not row['provider_id']:
            raise HTTPException(409, 'Only failed provider collection jobs can be retried')
        try:
            return run_collection(row['provider_id'],retry_of=job_id,actor=user)
        except (CollectionError, ValueError, TypeError) as error:
            raise HTTPException(502, str(error)) from error

    @router.get('/acquisition/quality')
    def data_quality(user=Depends(auth_dependency)):
        with connect() as connection:
            return [
                dict(row) for row in connection.execute(
                    """SELECT q.*,p.display_name AS source_name FROM sports_ai_data_quality_issues q
                       LEFT JOIN sports_ai_provider_registry p ON p.id=q.provider_id
                       WHERE q.resolved_at IS NULL ORDER BY q.created_at DESC LIMIT 500"""
                ).fetchall()
            ]

    @router.post('/acquisition/quality/{issue_id}/resolve')
    def resolve_quality(issue_id: str, user=Depends(auth_dependency)):
        with connect() as connection:
            changed = connection.execute(
                """UPDATE sports_ai_data_quality_issues SET resolved_at=?
                   WHERE id=? AND resolved_at IS NULL""",
                (datetime.now(timezone.utc).isoformat(),issue_id),
            ).rowcount
            if not changed:
                raise HTTPException(404, 'Unresolved quality issue not found')
        return {'resolved':True}

    @router.get('/acquisition/permissions')
    def source_permissions(user=Depends(auth_dependency)):
        with connect() as connection:
            return [
                {**dict(row),'approved':bool(row['approved'])}
                for row in connection.execute(
                    'SELECT * FROM sports_ai_source_permissions ORDER BY hostname',
                ).fetchall()
            ]

    @router.put('/acquisition/permissions')
    def update_permission(payload: PermissionUpdate, user=Depends(auth_dependency)):
        source = urlsplit(payload.url)
        terms = urlsplit(payload.terms_url)
        if (source.scheme != 'https' or not source.hostname or source.username
                or source.password or source.port not in (None,443)):
            raise HTTPException(422, 'Website permission must refer to an HTTPS hostname')
        if (terms.scheme != 'https' or not terms.hostname or terms.username or terms.password):
            raise HTTPException(422, 'Terms reference must use HTTPS')
        hostname = source.hostname.casefold().rstrip('.')
        with connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_source_permissions
                   (id,hostname,terms_url,notes,approved,approved_by,reviewed_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(hostname) DO UPDATE SET terms_url=excluded.terms_url,
                   notes=excluded.notes,approved=excluded.approved,
                   approved_by=excluded.approved_by,reviewed_at=excluded.reviewed_at""",
                (str(uuid.uuid4()),hostname,payload.terms_url,payload.notes,
                 int(payload.approved),user,datetime.now(timezone.utc).isoformat()),
            )
            connection.execute(
                """INSERT INTO sports_ai_audit_events(id,actor,action,metadata_json)
                   VALUES(?,?,?,?)""",
                (str(uuid.uuid4()),user,
                 'acquisition.website.permission.approved' if payload.approved
                 else 'acquisition.website.permission.revoked',
                 json.dumps({'hostname':hostname,'terms_url':payload.terms_url})),
            )
            if hostname == 'www.betexplorer.com':
                connection.execute(
                    """UPDATE sports_ai_provider_registry SET terms_reviewed=?,
                       enabled=0,status='disabled' WHERE adapter='browser'""",
                    (int(payload.approved),),
                )
        return {'hostname':hostname,'approved':payload.approved}

    @router.get('/acquisition/extraction-config')
    def get_extraction_config(user=Depends(auth_dependency)):
        with connect() as connection:
            return [
                {'provider_id':row['provider_id'],
                 'fields':_safe_json(row['fields_json'],{})}
                for row in connection.execute(
                    'SELECT * FROM sports_ai_extraction_configs ORDER BY updated_at DESC',
                ).fetchall()
            ]

    @router.put('/acquisition/extraction-config/{source_id}')
    def set_extraction_config(source_id: str, payload: ExtractionUpdate, user=Depends(auth_dependency)):
        if set(payload.fields) - _FIELDS:
            raise HTTPException(422, 'Unsupported extraction field')
        if any(not isinstance(selector,str) or not selector.strip() or len(selector)>500
               for selector in payload.fields.values()):
            raise HTTPException(422, 'CSS selectors must be non-empty and no longer than 500 characters')
        with connect() as connection:
            source = connection.execute(
                "SELECT id,adapter FROM sports_ai_provider_registry WHERE id=?",
                (source_id,),
            ).fetchone()
            if not source or source['adapter'] != 'browser':
                raise HTTPException(404, 'Browser research source not found')
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """INSERT INTO sports_ai_extraction_configs
                   (id,provider_id,fields_json,updated_by,updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(provider_id) DO UPDATE SET
                   fields_json=excluded.fields_json,updated_by=excluded.updated_by,
                   updated_at=excluded.updated_at""",
                (str(uuid.uuid4()),source_id,
                 json.dumps(payload.fields,allow_nan=False),user,now),
            )
        return {'saved':True}

    @router.post('/acquisition/browser/research')
    def browser_research(payload: ResearchRequest, user=Depends(auth_dependency)):
        with connect() as connection:
            approved = {
                row['hostname'] for row in connection.execute(
                    'SELECT hostname FROM sports_ai_source_permissions WHERE approved=1',
                ).fetchall()
            }
            if payload.fixture_id and not connection.execute(
                'SELECT 1 FROM sports_ai_fixtures WHERE id=?', (payload.fixture_id,),
            ).fetchone():
                raise HTTPException(404, 'Match fixture not found')
            for url in payload.urls:
                try:
                    validate_research_url(url,approved)
                except BrowserResearchError as error:
                    raise HTTPException(403,str(error)) from error
            browser_source = connection.execute(
                "SELECT id FROM sports_ai_provider_registry WHERE adapter='browser' ORDER BY provider_key LIMIT 1",
            ).fetchone()
            source_id = browser_source['id'] if browser_source else None
            fields = {}
            if source_id:
                config = connection.execute(
                    'SELECT fields_json FROM sports_ai_extraction_configs WHERE provider_id=?',
                    (source_id,),
                ).fetchone()
                fields = _safe_json(config['fields_json'],{}) if config else {}
        session_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        started = datetime.now(timezone.utc).isoformat()
        with connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_browser_sessions
                   (id,fixture_id,query,status,started_at,requested_urls_json,actor)
                   VALUES(?,?,?,'running',?,?,?)""",
                (session_id,payload.fixture_id,payload.query,started,
                 json.dumps(payload.urls),user),
            )
            connection.execute(
                """INSERT INTO sports_ai_acquisition_jobs
                   (id,session_id,kind,status,started_at,logs_json)
                   VALUES(?,?,'browser_research','running',?,?)""",
                (job_id,session_id,started,
                 json.dumps([{'at':started,'level':'info','message':'Approved browser research started'}])),
            )
        capture_dir = DB.parent / 'browser-captures' / session_id
        try:
            pages = research_pages(payload.urls,approved,fields,capture_dir)
            completed = datetime.now(timezone.utc).isoformat()
            succeeded = sum(not page.get('error') for page in pages)
            session_status = 'complete' if succeeded == len(pages) else 'partial' if succeeded else 'failed'
            serialized_pages = []
            saved_count = 0
            with connect() as connection:
                for index, page in enumerate(pages,1):
                    page_id = str(uuid.uuid4())
                    screenshot_name = Path(page['screenshot']).name if page.get('screenshot') else None
                    connection.execute(
                        """INSERT INTO sports_ai_browser_pages
                           (id,session_id,source_id,requested_url,final_url,title,status_code,
                            screenshot_path,captured_at,error)
                           VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (page_id,session_id,source_id,payload.urls[index-1],page.get('url'),
                         page.get('title'),page.get('status_code'),
                         screenshot_name,completed,page.get('error')),
                    )
                    page_url = page.get('url') or payload.urls[index-1]
                    serialized_pages.append({
                        'url':page_url,'title':page.get('title'),
                        'text':page.get('text',''),'status_code':page.get('status_code'),
                        'screenshot':screenshot_name,'error':page.get('error'),
                    })
                    if page.get('screenshot') and not page.get('error'):
                        saved_count += 1
                    for field, values in page.get('fields',{}).items():
                        for value in values:
                            connection.execute(
                                """INSERT INTO sports_ai_extraction_evidence
                                   (id,session_id,page_id,fixture_id,field,value_json,source_url,captured_at)
                                   VALUES(?,?,?,?,?,?,?,?)""",
                                (str(uuid.uuid4()),session_id,page_id,payload.fixture_id,
                                 field,json.dumps(value,ensure_ascii=False),page_url,completed),
                            )
                error = next((page['error'] for page in pages if page.get('error')),None)
                connection.execute(
                    """UPDATE sports_ai_browser_sessions SET status=?,finished_at=?,
                       pages_json=?,error=? WHERE id=?""",
                    (session_status,completed,json.dumps(serialized_pages,ensure_ascii=False),
                     error,session_id),
                )
                logs = [
                    {'at':completed,'level':'info',
                     'message':f'{saved_count} page(s) inspected; {len(pages)} requested.'}
                ]
                connection.execute(
                    """UPDATE sports_ai_acquisition_jobs SET status=?,finished_at=?,
                       records_seen=?,records_saved=?,logs_json=?,error=? WHERE id=?""",
                    (session_status,completed,len(pages),saved_count,json.dumps(logs),
                     error,job_id),
                )
                if source_id and saved_count:
                    connection.execute(
                        """UPDATE sports_ai_provider_registry SET status='operational',
                           last_success_at=?,last_attempt_at=?,last_error=NULL WHERE id=?""",
                        (completed,completed,source_id),
                    )
                if error:
                    _insert_quality(connection,source_id,'browser_page_research_failed',error)
            return {'session_id':session_id,'job_id':job_id,'status':session_status,
                    'pages':serialized_pages}
        except BrowserResearchError as error:
            _finish_browser_error(session_id,job_id,error)
            raise HTTPException(502,str(error)) from error
        except Exception as error:
            _finish_browser_error(session_id,job_id,error)
            raise HTTPException(502,'Browser research failed; inspect the saved session and job logs') from error

    def retry_browser_session(session_id,user):
        with connect() as connection:
            session = connection.execute(
                """SELECT fixture_id,query,requested_urls_json,status
                   FROM sports_ai_browser_sessions WHERE id=?""",
                (session_id,),
            ).fetchone()
        if not session:
            raise HTTPException(404,'Browser session not found')
        if session['status'] not in ('failed','partial'):
            raise HTTPException(409,'Only failed or partial browser sessions can be retried')
        urls=_safe_json(session['requested_urls_json'],[])
        if not urls:
            raise HTTPException(409,'This browser session has no saved URLs to retry')
        payload=ResearchRequest(
            query=session['query'],urls=urls,fixture_id=session['fixture_id'],
        )
        return browser_research(payload,user)

    @router.post('/acquisition/browser/sessions/{session_id}/retry')
    def retry_browser_session_endpoint(session_id: str, user=Depends(auth_dependency)):
        return retry_browser_session(session_id,user)

    @router.get('/acquisition/browser/sessions')
    def browser_sessions(limit: int = Query(default=100, ge=1, le=500), user=Depends(auth_dependency)):
        with connect() as connection:
            return [
                {**dict(row),'pages':_safe_json(row['pages_json'],[])}
                for row in connection.execute(
                    """SELECT id,fixture_id,query,status,started_at,finished_at,pages_json,error
                       FROM sports_ai_browser_sessions ORDER BY started_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            ]

    @router.get('/acquisition/browser/sessions/{session_id}/evidence')
    def browser_evidence(session_id: str, user=Depends(auth_dependency)):
        with connect() as connection:
            return [
                {**dict(row),'value':_safe_json(row['value_json'],row['value_json'])}
                for row in connection.execute(
                    """SELECT id,field,value_json,source_url,captured_at FROM sports_ai_extraction_evidence
                       WHERE session_id=? ORDER BY captured_at,field""",
                    (session_id,),
                ).fetchall()
            ]

    @router.get('/acquisition/browser/sessions/{session_id}/screenshots/{filename}')
    def browser_screenshot(session_id: str, filename: str, user=Depends(auth_dependency)):
        if not re.fullmatch(r'page-\d+\.png',filename):
            raise HTTPException(404, 'Screenshot not found')
        with connect() as connection:
            found = connection.execute(
                """SELECT 1 FROM sports_ai_browser_pages
                   WHERE session_id=? AND screenshot_path=?""",
                (session_id,filename),
            ).fetchone()
        if not found:
            raise HTTPException(404, 'Screenshot not found')
        path = (DB.parent/'browser-captures'/session_id/filename).resolve()
        root = (DB.parent/'browser-captures'/session_id).resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(404, 'Screenshot not found')
        return FileResponse(path,media_type='image/png')

    @router.get('/research/matches')
    def research_matches(
        limit: int = Query(default=500, ge=1, le=1000),
        sport: str | None = Query(default=None, pattern=r'^(football|basketball)$'),
        q: str | None = Query(default=None, max_length=200),
        user=Depends(auth_dependency),
    ):
        query = """SELECT id,external_id,sport,competition,home,away,starts_at,status,
                          source,observed_at FROM sports_ai_fixtures"""
        clauses, args = [], []
        if sport:
            clauses.append('sport=?')
            args.append(sport)
        if q:
            clauses.append('(home LIKE ? OR away LIKE ? OR competition LIKE ?)')
            value = f'%{q}%'
            args.extend((value,value,value))
        if clauses:
            query += ' WHERE ' + ' AND '.join(clauses)
        query += ' ORDER BY starts_at DESC LIMIT ?'
        args.append(limit)
        with connect() as connection:
            return [dict(row) for row in connection.execute(query,args).fetchall()]

    @router.get('/research/matches/{fixture_id}')
    def research_match(fixture_id: str, user=Depends(auth_dependency)):
        with connect() as connection:
            fixture = connection.execute(
                'SELECT * FROM sports_ai_fixtures WHERE id=?', (fixture_id,),
            ).fetchone()
            if not fixture:
                raise HTTPException(404, 'Match not found')
            match = dict(fixture)
            match['result'] = (dict(row) if (row := connection.execute(
                'SELECT home_score,away_score,source,settled_at FROM sports_ai_results WHERE fixture_id=?',
                (fixture_id,),
            ).fetchone()) else None)
            cutoff = match['starts_at']
            home, away = match['home'], match['away']
            h2h = connection.execute(
                """SELECT f.starts_at,f.home,r.home_score,r.away_score,f.away,
                          r.source,f.observed_at FROM sports_ai_fixtures f
                   JOIN sports_ai_results r ON r.fixture_id=f.id
                   WHERE f.id!=? AND ((lower(f.home)=lower(?) AND lower(f.away)=lower(?))
                     OR (lower(f.home)=lower(?) AND lower(f.away)=lower(?)))
                     AND julianday(f.starts_at)<julianday(?)
                     AND julianday(r.settled_at)<julianday(?)
                   ORDER BY f.starts_at DESC LIMIT 20""",
                (fixture_id,home,away,away,home,cutoff,cutoff),
            ).fetchall()
            form = {}
            for team in (home,away):
                games = connection.execute(
                    """SELECT f.starts_at,f.home,r.home_score,r.away_score,f.away,
                              r.source,f.observed_at FROM sports_ai_fixtures f
                       JOIN sports_ai_results r ON r.fixture_id=f.id
                       WHERE (lower(f.home)=lower(?) OR lower(f.away)=lower(?))
                         AND julianday(f.starts_at)<julianday(?)
                         AND julianday(r.settled_at)<julianday(?)
                       ORDER BY f.starts_at DESC LIMIT 10""",
                    (team,team,cutoff,cutoff),
                ).fetchall()
                form[team] = [dict(game) for game in games]
            standings = connection.execute(
                """SELECT * FROM sports_ai_standings_snapshots
                   WHERE lower(competition)=lower(?) AND observed_at=(
                     SELECT MAX(observed_at) FROM sports_ai_standings_snapshots
                     WHERE lower(competition)=lower(?))
                   ORDER BY position LIMIT 200""",
                (match['competition'],match['competition']),
            ).fetchall()
            statistics = connection.execute(
                """SELECT s.metric,s.value_json AS value,'' AS unit,
                          p.provider_key AS source,s.source_updated_at AS source_updated_at,
                          s.observed_at FROM sports_ai_match_statistics s
                   JOIN sports_ai_provider_registry p ON p.id=s.provider_id
                   WHERE s.fixture_id=? ORDER BY s.observed_at DESC,s.metric""",
                (fixture_id,),
            ).fetchall()
            odds = connection.execute(
                """SELECT q.bookmaker,q.market,q.outcome,q.line,q.decimal_odds,
                          p.provider_key AS source,q.observed_at
                   FROM sports_ai_odds_snapshots q
                   JOIN sports_ai_provider_registry p ON p.id=q.provider_id
                   WHERE q.fixture_id=? ORDER BY q.observed_at DESC LIMIT 500""",
                (fixture_id,),
            ).fetchall()
            analyses = connection.execute(
                """SELECT p.market,p.odds,p.probability,p.edge,p.model_name,
                          r.created_at AS analyzed_at
                   FROM sports_ai_predictions p
                   JOIN sports_ai_analysis_runs r ON r.id=p.run_id
                   WHERE p.fixture_id=?
                   UNION ALL
                   SELECT p.market,q.decimal_odds,p.probability,o.expected_edge,
                          m.model_name,p.created_at
                   FROM sports_ai_engine_predictions p
                   JOIN sports_ai_model_registry m ON m.id=p.model_id
                   LEFT JOIN sports_ai_value_opportunities o ON o.prediction_id=p.id
                   LEFT JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
                   WHERE p.fixture_id=? ORDER BY analyzed_at DESC LIMIT 100""",
                (fixture_id,fixture_id),
            ).fetchall()
            evidence = connection.execute(
                """SELECT field,value_json,source_url,captured_at FROM sports_ai_extraction_evidence
                   WHERE fixture_id=? ORDER BY captured_at DESC LIMIT 500""",
                (fixture_id,),
            ).fetchall()
            return {
                'match':{'fixture':match,'source':match['source'],
                         'observed_at':match['observed_at'],'result':match['result']},
                'head_to_head':[dict(row) for row in h2h],
                'team_form':form,
                'standings':[
                    {**dict(row),'details':_safe_json(row['details_json'],{})}
                    for row in standings
                ],
                'statistics':[
                    {**dict(row),'value':_safe_json(row['value'],row['value'])}
                    for row in statistics
                ],
                'odds':[dict(row) for row in odds],
                'analysis':[dict(row) for row in analyses],
                'evidence':[
                    {**dict(row),'value':_safe_json(row['value_json'],row['value_json'])}
                    for row in evidence
                ],
            }

    @router.get('/acquisition/catalog/overview')
    def catalog_overview(user=Depends(auth_dependency)):
        from catalog_engine import coverage, list_errors
        with connect() as connection:
            payload = coverage(connection)
            payload['errors'] = list_errors(connection, limit=30)
            return payload

    @router.get('/acquisition/catalog/sports')
    def catalog_sports(user=Depends(auth_dependency)):
        from catalog_engine import list_sports
        with connect() as connection:
            return list_sports(connection)

    @router.post('/acquisition/catalog/sports')
    def catalog_add_sport(payload: CatalogSportCreate, user=Depends(auth_dependency)):
        from catalog_engine import register_sport
        try:
            with connect() as connection:
                return register_sport(connection, payload.code, payload.name, user, payload.notes)
        except CollectionError as error:
            raise HTTPException(422, str(error)) from error

    @router.get('/acquisition/catalog/countries')
    def catalog_countries(q: str = '', user=Depends(auth_dependency)):
        from catalog_engine import list_countries
        with connect() as connection:
            return list_countries(connection, q.strip())

    @router.get('/acquisition/catalog/leagues')
    def catalog_leagues(
        sport: str = '', country: str = '', q: str = '', freshness: str = '',
        page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200),
        user=Depends(auth_dependency),
    ):
        from catalog_engine import list_leagues
        with connect() as connection:
            return list_leagues(connection, sport, country, q.strip(), freshness, page, page_size)

    @router.get('/acquisition/catalog/teams')
    def catalog_teams(
        sport: str = '', q: str = '',
        page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200),
        user=Depends(auth_dependency),
    ):
        from catalog_engine import list_teams
        with connect() as connection:
            return list_teams(connection, sport, q.strip(), page, page_size)

    @router.get('/acquisition/catalog/errors')
    def catalog_errors(user=Depends(auth_dependency)):
        from catalog_engine import list_errors
        with connect() as connection:
            return list_errors(connection)

    @router.post('/acquisition/catalog/discover')
    def catalog_discover(user=Depends(auth_dependency)):
        from catalog_engine import run_catalog_discovery
        result = run_catalog_discovery()
        with connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_audit_events(id,actor,action,metadata_json) VALUES(?,?,?,?)""",
                (str(uuid.uuid4()), user, 'catalog.discovery.requested',
                 json.dumps({'status': result.get('status'), 'saved': result.get('saved')})),
            )
        return result

    @router.post('/acquisition/catalog/backfill')
    def catalog_backfill(payload: CatalogBackfillRequest, user=Depends(auth_dependency)):
        from catalog_engine import queue_backfill
        try:
            with connect() as connection:
                result = queue_backfill(connection, payload.source_ids)
                connection.execute(
                    """INSERT INTO sports_ai_audit_events(id,actor,action,metadata_json) VALUES(?,?,?,?)""",
                    (str(uuid.uuid4()), user, 'catalog.backfill.queued', json.dumps(result)),
                )
                return result
        except CollectionError as error:
            raise HTTPException(422, str(error)) from error

    @router.put('/acquisition/catalog/backfill-policy')
    def catalog_policy(payload: CatalogPolicy, user=Depends(auth_dependency)):
        from catalog_engine import set_backfill_policy
        with connect() as connection:
            return set_backfill_policy(connection, payload.per_day, user)

    return router


def _finish_browser_error(session_id, job_id, error):
    finished = datetime.now(timezone.utc).isoformat()
    message = str(error)[:500]
    with connect() as connection:
        connection.execute(
            """UPDATE sports_ai_browser_sessions SET status='failed',
               finished_at=?,error=? WHERE id=?""",
            (finished,message,session_id),
        )
        connection.execute(
            """UPDATE sports_ai_acquisition_jobs SET status='failed',
               finished_at=?,error=?,logs_json=? WHERE id=?""",
            (finished,message,json.dumps([
                {'at':finished,'level':'error','message':message}
            ]),job_id),
        )
