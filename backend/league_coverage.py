"""League coverage KPI for the target sports: how many leagues with upcoming matches the
active models actually predict, against a 90% target, and what blocks the rest.

Every figure is a count of stored rows. Computed by the worker each cycle and stored, so
the API never runs the heavy queries on a page load.
"""
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from db import connect

TARGET_SPORTS = ('football', 'basketball', 'ice_hockey', 'frauenfussball', 'tennis', 'volleyball')
TARGET = 0.90
WINDOW_DAYS = 7
SETTING = 'league_coverage'
LABELS = {'football': 'Football', 'basketball': 'Basketball', 'ice_hockey': 'Ice hockey',
          'frauenfussball': "Women's football", 'tennis': 'Tennis', 'volleyball': 'Volleyball'}


def compute(connection, now=None):
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start, end = current.isoformat(), (current + timedelta(days=WINDOW_DAYS)).isoformat()
    fresh = (current - timedelta(minutes=60)).isoformat()
    sports = []
    for sport in TARGET_SPORTS:
        model = connection.execute(
            "SELECT version, lifecycle FROM sports_ai_model_registry WHERE sport=? AND active=1", (sport,),
        ).fetchone()
        rows = connection.execute(
            """SELECT f.competition,
                      MAX(EXISTS (SELECT 1 FROM sports_ai_odds_snapshots o WHERE o.fixture_id=f.id
                                  AND o.bookmaker='SportyBet' AND o.observed_at>=?)) AS priced,
                      MAX(EXISTS (SELECT 1 FROM sports_ai_engine_predictions p
                                  JOIN sports_ai_model_registry m ON m.id=p.model_id AND m.active=1
                                  WHERE p.fixture_id=f.id)) AS predicted,
                      MAX(EXISTS (SELECT 1 FROM sports_ai_qualification_decisions d
                                  WHERE d.fixture_id=f.id AND d.state='QUALIFIED')) AS qualified,
                      SUM(cv.status='insufficient_history') AS no_history,
                      SUM(cv.status IS NULL) AS unattempted,
                      COUNT(*) AS fixtures
               FROM sports_ai_fixtures f
               LEFT JOIN sports_ai_analysis_coverage cv ON cv.fixture_id=f.id
               WHERE f.sport=? AND f.status='scheduled' AND f.starts_at>? AND f.starts_at<?
                 AND lower(COALESCE(f.competition,'')) NOT LIKE '%simulated%'
                 AND lower(COALESCE(f.competition,'')) NOT LIKE '%srl%'
               GROUP BY f.competition""",
            (fresh, sport, start, end),
        ).fetchall()
        leagues = len(rows)
        predicted = sum(1 for r in rows if r['predicted'])
        blockers = {'no_validated_model': 0, 'insufficient_history': 0, 'awaiting_analysis': 0, 'not_priced': 0}
        for r in rows:
            if r['predicted']:
                continue
            if not model:
                blockers['no_validated_model'] += 1
            elif not r['priced']:
                blockers['not_priced'] += 1
            elif r['no_history']:
                blockers['insufficient_history'] += 1
            else:
                blockers['awaiting_analysis'] += 1
        depth = connection.execute(
            """SELECT MIN(f.starts_at), MAX(f.starts_at), COUNT(*) FROM sports_ai_fixtures f
               JOIN sports_ai_results r ON r.fixture_id=f.id WHERE f.sport=? AND r.status='final'
                 AND f.starts_at>=?""",
            (sport, (current - timedelta(days=400)).isoformat()),
        ).fetchone()
        span = None
        if depth[0] and depth[1]:
            span = (datetime.fromisoformat(depth[1].replace('Z', '+00:00')) -
                    datetime.fromisoformat(depth[0].replace('Z', '+00:00'))).days
        sports.append({
            'sport': sport, 'label': LABELS[sport],
            'model': None if not model else {'version': model['version'], 'lifecycle': model['lifecycle']},
            'leagues': leagues, 'fixtures': sum(r['fixtures'] for r in rows),
            'priced': sum(1 for r in rows if r['priced']), 'predicted': predicted,
            'qualified': sum(1 for r in rows if r['qualified']),
            'coverage': None if not leagues else round(predicted / leagues, 4),
            'blockers': blockers, 'history_days': span, 'finals_400d': depth[2],
        })
    total = sum(s['leagues'] for s in sports)
    covered = sum(s['predicted'] for s in sports)
    return {'computed_at': current.isoformat(), 'window_days': WINDOW_DAYS, 'target': TARGET,
            'leagues': total, 'predicted': covered, 'coverage': None if not total else round(covered / total, 4),
            'sports': sports}


def refresh(connection, now=None):
    report = compute(connection, now)
    connection.execute(
        """INSERT INTO sports_ai_settings(key,value_json,updated_at) VALUES(?,?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
        (SETTING, json.dumps(report), report['computed_at']),
    )
    return report


def stored(connection):
    row = connection.execute('SELECT value_json FROM sports_ai_settings WHERE key=?', (SETTING,)).fetchone()
    return json.loads(row['value_json']) if row else None


def build_coverage_router(auth):
    router = APIRouter(prefix='/api/sports-ai/coverage', tags=['Coverage'])

    @router.get('/leagues')
    def get_leagues(actor: str = Depends(auth)):
        with connect() as connection:
            return stored(connection) or {'computed_at': None, 'target': TARGET, 'sports': [],
                                          'note': 'Computed by the worker each cycle; not available yet.'}

    return router
