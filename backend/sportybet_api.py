"""Authenticated controls for the supervised SportyBet browser."""

import hmac
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Literal

from browser_operator import BrowserResearchError
from db import connect
from sportybet_browser import OperatorBusy, capture_directory, operator
from sportybet_research import (
    BOOKMAKER,
    confirm_observation,
    import_observation,
    list_observations,
    map_market,
    match_observation,
    rank_fixtures,
    store_observations,
    unattended_status,
    validate_decimal,
    validate_observation_market,
)


class StartIn(BaseModel):
    section: Literal['home', 'football', 'basketball'] = 'home'
    mode: Literal['supervised', 'unattended'] = 'supervised'


class NavigateIn(BaseModel):
    section: Literal['home', 'football', 'basketball']


class ObservationIn(BaseModel):
    sport: Literal['football', 'basketball']
    competition: str = Field(default='', max_length=200)
    home: str = Field(min_length=2, max_length=120)
    away: str = Field(min_length=2, max_length=120)
    starts_at: datetime | None = None
    market_label: str = Field(min_length=2, max_length=160)
    selection_label: str = Field(min_length=1, max_length=160)
    decimal_odds: float = Field(gt=1, le=1000)


class MatchIn(BaseModel):
    fixture_id: str = Field(min_length=8, max_length=80)
    acknowledge_ambiguity: bool = False


class AuthorizationIn(BaseModel):
    evidence: str = Field(min_length=20, max_length=2000)
    expires_at: datetime | None = None


def _operator_permitted(user, admin_key):
    if user == 'local-preview-user':
        return True
    expected = os.environ.get('SPORTS_ADMIN_API_KEY')
    return bool(expected and admin_key and hmac.compare_digest(expected, admin_key))


def build_sportybet_router(auth_dependency, require_admin):
    router = APIRouter(prefix='/api/sports-ai/sportybet', tags=['SportyBet browser'])

    def operator_user(user=Depends(auth_dependency), x_sports_admin_key: str | None = Header(default=None)):
        if not _operator_permitted(user, x_sports_admin_key):
            raise HTTPException(403, 'Browser control and quote approval require the SportyBet operator role or the admin key.')
        return user

    @router.get('/browser/status')
    def browser_status(user=Depends(auth_dependency)):
        return operator.status()

    @router.get('/browser/health')
    def browser_health(user=Depends(auth_dependency)):
        return operator.health()

    @router.post('/browser/start')
    def browser_start(payload: StartIn, user=Depends(operator_user)):
        if payload.mode == 'unattended':
            with connect() as connection:
                state = unattended_status(connection)
            raise HTTPException(403, state['reason'])
        try:
            return operator.start(user, section=payload.section)
        except (OperatorBusy, BrowserResearchError) as error:
            raise HTTPException(409 if isinstance(error, OperatorBusy) else 422, str(error)) from error

    @router.post('/browser/pause')
    def browser_pause(user=Depends(operator_user)):
        try:
            return operator.pause()
        except OperatorBusy as error:
            raise HTTPException(409, str(error)) from error

    @router.post('/browser/resume')
    def browser_resume(user=Depends(operator_user)):
        try:
            return operator.resume()
        except OperatorBusy as error:
            raise HTTPException(409, str(error)) from error

    @router.post('/browser/stop')
    def browser_stop(user=Depends(operator_user)):
        return operator.stop()

    @router.post('/browser/restart')
    def browser_restart(payload: StartIn, user=Depends(operator_user)):
        if payload.mode == 'unattended':
            raise HTTPException(403, 'Unattended SportyBet extraction is not operational.')
        try:
            return operator.restart(user, section=payload.section)
        except (OperatorBusy, BrowserResearchError) as error:
            raise HTTPException(409 if isinstance(error, OperatorBusy) else 422, str(error)) from error

    @router.post('/browser/navigate')
    def browser_navigate(payload: NavigateIn, user=Depends(operator_user)):
        try:
            return operator.navigate(payload.section)
        except (OperatorBusy, BrowserResearchError) as error:
            raise HTTPException(409 if isinstance(error, OperatorBusy) else 422, str(error)) from error

    @router.get('/observations')
    def observations(
        sport: str | None = None,
        confirmation: str | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=8, ge=1, le=50),
        user=Depends(auth_dependency),
    ):
        with connect() as connection:
            return list_observations(connection, sport, confirmation, page, page_size)

    @router.get('/observations/{observation_id}')
    def observation_detail(observation_id: str, user=Depends(auth_dependency)):
        with connect() as connection:
            row = connection.execute(
                'SELECT * FROM sports_ai_sportybet_observations WHERE id=?', (observation_id,),
            ).fetchone()
            if not row:
                raise HTTPException(404, 'Observation not found')
            ranked = rank_fixtures(connection, dict(row))
            mapped = map_market(row['sport'], row['market_label'], row['selection_label'], row['line']) if row['sport'] else None
            return {'observation': dict(row), 'candidates': ranked, 'market': mapped}

    @router.post('/observations')
    def add_observation(payload: ObservationIn, user=Depends(operator_user)):
        current = operator.status()
        if current['state'] != 'AWAITING_CONFIRMATION' or not current['session_id']:
            raise HTTPException(409, 'Record a visible quote only while a supervised session is waiting for confirmation.')
        if not current['source_url']:
            raise HTTPException(409, 'The browser has no source page to attach to this quote.')
        odds = validate_decimal(payload.decimal_odds)
        if odds is None:
            raise HTTPException(422, 'Enter the decimal odds shown on the page.')
        observed_at = datetime.now(timezone.utc).isoformat()
        row = {
            'sport': payload.sport,
            'competition': payload.competition.strip(),
            'home': payload.home.strip(),
            'away': payload.away.strip(),
            'starts_at': payload.starts_at.isoformat() if payload.starts_at else None,
            'market_label': payload.market_label.strip(),
            'selection_label': payload.selection_label.strip(),
            'decimal_odds': odds,
        }
        with connect() as connection:
            stored = store_observations(connection, current['session_id'], [row], current['source_url'], observed_at)
            if not stored['saved']:
                raise HTTPException(409, 'That visible quote is already recorded for this page and time.')
            connection.execute(
                """INSERT INTO sports_ai_sportybet_events
                   (id,session_id,state,operation,source_url,detail,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()), current['session_id'], 'AWAITING_CONFIRMATION', 'record quote',
                    current['source_url'], f"{row['home']} vs {row['away']} {row['market_label']} recorded for confirmation.",
                    observed_at,
                ),
            )
            saved = connection.execute(
                'SELECT * FROM sports_ai_sportybet_observations WHERE id=?', (stored['saved'][0],),
            ).fetchone()
            return dict(saved)

    @router.post('/observations/{observation_id}/confirm')
    def confirm(observation_id: str, user=Depends(operator_user)):
        try:
            with connect() as connection:
                return dict(confirm_observation(connection, observation_id, user, datetime.now(timezone.utc)))
        except LookupError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.post('/observations/{observation_id}/match')
    def match(observation_id: str, payload: MatchIn, user=Depends(operator_user)):
        try:
            with connect() as connection:
                return dict(match_observation(connection, observation_id, payload.fixture_id, payload.acknowledge_ambiguity))
        except LookupError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.post('/observations/{observation_id}/market')
    def market(observation_id: str, user=Depends(operator_user)):
        try:
            with connect() as connection:
                return dict(validate_observation_market(connection, observation_id))
        except LookupError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.post('/observations/{observation_id}/import')
    def import_quote(observation_id: str, user=Depends(operator_user)):
        try:
            with connect() as connection:
                result = import_observation(connection, observation_id, user)
                connection.execute(
                    """INSERT INTO sports_ai_audit_events(id,actor,action,metadata_json)
                       VALUES(?,?,?,?)""",
                    (
                        str(uuid.uuid4()), user, 'sportybet.quote.import',
                        json.dumps({
                            'observation_id': observation_id,
                            'value_status': result['value_status'],
                            'places_wager': False,
                        }),
                    ),
                )
                result['places_wager'] = False
                return result
        except LookupError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get('/audit')
    def audit(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=8, ge=1, le=50),
        user=Depends(auth_dependency),
    ):
        offset = (page - 1) * page_size
        with connect() as connection:
            total = connection.execute('SELECT COUNT(*) FROM sports_ai_sportybet_events').fetchone()[0]
            rows = connection.execute(
                """SELECT id,session_id,state,operation,source_url,detail,screenshot_path IS NOT NULL AS has_screenshot,created_at
                   FROM sports_ai_sportybet_events ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (page_size, offset),
            ).fetchall()
            return {'items': [dict(row) for row in rows], 'total': total, 'page': page, 'page_size': page_size}

    @router.get('/screenshots/{event_id}')
    def screenshot(event_id: str, user=Depends(auth_dependency)):
        with connect() as connection:
            row = connection.execute(
                'SELECT screenshot_path FROM sports_ai_sportybet_events WHERE id=?', (event_id,),
            ).fetchone()
        if not row or not row['screenshot_path']:
            raise HTTPException(404, 'No screenshot is stored for that event.')
        path = Path(row['screenshot_path']).resolve()
        root = capture_directory().resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(404, 'The screenshot file is not available.')
        return FileResponse(path)

    @router.post('/authorization')
    def authorize(payload: AuthorizationIn, user=Depends(auth_dependency), x_sports_admin_key: str | None = Header(default=None)):
        require_admin(x_sports_admin_key)
        with connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_collection_authorizations
                   (id,bookmaker,scope,status,evidence,actor,created_at,expires_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()), BOOKMAKER, 'unattended_extraction', 'authorized',
                    payload.evidence.strip(), user, datetime.now(timezone.utc).isoformat(),
                    payload.expires_at.isoformat() if payload.expires_at else None,
                ),
            )
            return unattended_status(connection)

    return router
