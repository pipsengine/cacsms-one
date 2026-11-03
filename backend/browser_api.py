"""Authenticated job queue for the autonomous allowlisted browser worker."""

import os
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from browser_jobs import browser_summary, create_job, get_job, list_jobs, list_observations, record_quote
from db import connect


class JobRequest(BaseModel):
    url: str


class QuoteRequest(BaseModel):
    fixture_name: str
    market: str
    selection: str
    decimal_odds: float = Field(gt=1, lt=100000)
    source_url: str


def _permitted(user, admin_key):
    if user == 'local-preview-user':
        return True
    expected = os.environ.get('SPORTS_ADMIN_API_KEY')
    return bool(expected and admin_key and hmac.compare_digest(expected, admin_key))


def build_browser_router(auth_dependency):
    router = APIRouter(prefix='/api/sports-ai/browser', tags=['Browser jobs'])

    def operator_user(user=Depends(auth_dependency), x_sports_admin_key: str | None = Header(default=None)):
        if not _permitted(user, x_sports_admin_key):
            raise HTTPException(403, 'Creating a browser job requires the local preview or the admin key.')
        return user

    @router.get('/status')
    def status(user=Depends(auth_dependency)):
        with connect() as connection:
            return browser_summary(connection)

    @router.get('/jobs')
    def jobs(user=Depends(auth_dependency)):
        return {'items': list_jobs()}

    @router.get('/jobs/{job_id}')
    def job(job_id: str, user=Depends(auth_dependency)):
        row = get_job(job_id)
        if not row:
            raise HTTPException(404, 'Unknown browser job')
        row['observations'] = list_observations(job_id)
        return row

    @router.post('/jobs')
    def add_job(body: JobRequest, user=Depends(operator_user)):
        try:
            job_id = create_job(body.url)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return get_job(job_id)

    @router.post('/jobs/{job_id}/quotes')
    def add_quote(job_id: str, body: QuoteRequest, user=Depends(operator_user)):
        try:
            observation_id = record_quote(
                job_id, body.fixture_name, body.market, body.selection, body.decimal_odds, body.source_url,
            )
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return {
            'id': observation_id,
            'confirmed': 0,
            'qualification': 'pending_validation',
            'places_wager': False,
        }

    return router
