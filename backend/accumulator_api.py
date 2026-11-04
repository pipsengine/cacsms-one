"""Accumulator HTTP API. Authorization matches the Sports AI session plus the admin key."""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from accumulator_engine import (
    RANGE_TIERS,
    TARGETS,
    accept_ticket,
    command_centre,
    export_ticket,
    generate_accumulators,
    list_tickets,
    load_config,
    load_opportunity_rows,
    load_tier_policy,
    monitor_accumulators,
    qualify_candidates,
    recalculate_ticket,
    remove_leg,
    replace_leg,
    run_backtest,
    save_config,
    save_ticket,
    save_tier_policy,
)
from db import connect
from sportybet_adapter import (
    automatic_booking_code,
    integration_status,
    list_codes,
    prepare_betslip,
    register_manual_code,
)


class ConfigIn(BaseModel):
    targets: list[int] = Field(default_factory=lambda: list(TARGETS))
    tolerance: float = 0.15
    min_legs: int = Field(default=2, ge=2, le=12)
    max_legs: int = Field(default=8, ge=2, le=12)
    max_per_competition: int = Field(default=3, ge=1, le=12)
    max_per_team: int = Field(default=1, ge=1, le=6)
    min_probability: float = Field(default=0.02, ge=0, lt=1)
    min_edge: float = 0.05
    min_model_reliability: float = Field(default=0, ge=0, le=1)
    min_completeness: float = Field(default=0.5, ge=0, le=1)
    odds_max_age_minutes: int = Field(default=15, ge=1, le=1440)
    max_tickets_per_tier: int = Field(default=3, ge=1, le=20)
    beam_width: int = Field(default=24, ge=4, le=200)
    search_limit: int = Field(default=8000, ge=100, le=200000)
    timeout_seconds: int = Field(default=8, ge=1, le=120)
    allow_same_fixture: int = Field(default=0, ge=0, le=1)


class TierChoiceIn(BaseModel):
    id: str
    enabled: bool | None = None
    mode: str | None = None


class TierPolicyIn(BaseModel):
    min_settled_before_activation: int | None = Field(default=None, ge=1, le=1000)
    tiers: list[TierChoiceIn] = Field(default_factory=list)


class GenerateIn(BaseModel):
    sport_mode: str = 'mixed'
    tiers: list[int] | None = None
    range_ids: list[str] | None = None
    min_edge: float | None = None
    min_legs: int | None = Field(default=None, ge=1, le=12)
    max_legs: int | None = Field(default=None, ge=1, le=12)
    markets: list[str] = Field(default_factory=list)
    competitions: list[str] = Field(default_factory=list)
    bookmakers: list[str] = Field(default_factory=list)
    idempotency_key: str | None = Field(default=None, max_length=160)


class RemoveIn(BaseModel):
    opportunity_id: str = Field(min_length=1, max_length=160)


class ReplaceIn(BaseModel):
    remove_opportunity_id: str = Field(min_length=1, max_length=160)
    add_opportunity_id: str = Field(min_length=1, max_length=160)


class BookingIn(BaseModel):
    code: str = Field(min_length=2, max_length=120)
    evidence: str = Field(min_length=1, max_length=500)


def build_accumulator_router(auth, require_admin):
    router = APIRouter(prefix='/api/sports-ai/accumulator', tags=['Accumulator'])

    def audit(connection, actor, action, details):
        import uuid
        connection.execute(
            'INSERT INTO sports_ai_audit_events(id,actor,action,metadata_json) VALUES(?,?,?,?)',
            (str(uuid.uuid4()), actor, action, json.dumps(details)),
        )

    def admin(x_sports_admin_key: str | None = Header(default=None)):
        require_admin(x_sports_admin_key)
        return x_sports_admin_key

    @router.get('/config')
    def get_config(user=Depends(auth)):
        with connect() as connection:
            config = load_config(connection)
            config['tier_policy'] = load_tier_policy(connection)
        config['supported_targets'] = list(TARGETS)
        config['supported_ranges'] = [
            {'id': item['id'], 'label': item['label'], 'low': item['low'], 'high': item['high'], 'tier': item['tier']}
            for item in RANGE_TIERS
        ]
        return config

    @router.put('/config')
    def put_config(payload: ConfigIn, user=Depends(auth), _admin=Depends(admin)):
        try:
            with connect() as connection:
                saved = save_config(connection, payload.model_dump())
                saved['tier_policy'] = load_tier_policy(connection)
                audit(connection, user, 'accumulator.config', {'targets': saved['targets']})
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return saved

    @router.put('/tiers')
    def put_tiers(payload: TierPolicyIn, user=Depends(auth), _admin=Depends(admin)):
        with connect() as connection:
            try:
                saved = save_tier_policy(connection, payload.model_dump())
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            audit(connection, user, 'accumulator.tiers', {
                'tiers': [{'id': item['id'], 'mode': item['mode'], 'enabled': item['enabled']} for item in saved['tiers']],
            })
        return saved

    @router.get('/compare')
    def compare(left: str, right: str, user=Depends(auth)):
        with connect() as connection:
            tickets = list_tickets(connection)
        chosen = {ticket['id']: ticket for ticket in tickets}
        if left not in chosen or right not in chosen:
            raise HTTPException(404, 'Both tickets must be stored')
        return {
            'left': _comparison(chosen[left]),
            'right': _comparison(chosen[right]),
            'note': 'Comparison uses the odds, probabilities and correlation checks stored on the tickets.',
        }

    @router.post('/generate')
    def generate(payload: GenerateIn, user=Depends(auth)):
        if payload.sport_mode not in ('football', 'basketball', 'mixed'):
            raise HTTPException(422, 'Sport mode must be football, basketball or mixed')
        known_ranges = {item['id'] for item in RANGE_TIERS}
        if payload.tiers and payload.range_ids:
            raise HTTPException(422, 'Send either legacy point targets or odds ranges')
        if payload.tiers and any(item not in TARGETS for item in payload.tiers):
            raise HTTPException(422, 'Each tier must be one of the eight target odds')
        if payload.range_ids and any(item not in known_ranges for item in payload.range_ids):
            raise HTTPException(422, 'Each range must be one of the ten configured odds tiers')
        overrides = {}
        if payload.tiers:
            overrides['targets'] = payload.tiers
        elif payload.range_ids is not None:
            overrides['range_ids'] = payload.range_ids
        if payload.min_edge is not None:
            overrides['min_edge'] = payload.min_edge
        if payload.min_legs is not None:
            overrides['min_legs'] = payload.min_legs
        if payload.max_legs is not None:
            overrides['max_legs'] = payload.max_legs
        overrides['markets'] = payload.markets
        overrides['competitions'] = payload.competitions
        overrides['bookmakers'] = payload.bookmakers
        with connect() as connection:
            try:
                return generate_accumulators(
                    connection, user, audit, payload.sport_mode, overrides, payload.idempotency_key,
                )
            except ValueError as error:
                raise HTTPException(422, str(error)) from error

    @router.get('/runs')
    def runs(user=Depends(auth)):
        with connect() as connection:
            rows = [dict(row) for row in connection.execute(
                """SELECT id,sport_mode,status,trigger,started_at,finished_at,summary_json,error
                   FROM sports_ai_accumulator_runs ORDER BY started_at DESC LIMIT 50""",
            )]
        for row in rows:
            row['summary'] = json.loads(row.pop('summary_json') or '{}')
        return rows

    @router.get('/tickets')
    def tickets(tier: int | None = None, sport_mode: str | None = None, status: str | None = None, family_id: str | None = None, user=Depends(auth)):
        with connect() as connection:
            return list_tickets(connection, tier, sport_mode, status, family_id)

    @router.get('/tickets/{ticket_id}')
    def ticket_detail(ticket_id: str, user=Depends(auth)):
        with connect() as connection:
            row = connection.execute(
                'SELECT id FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,),
            ).fetchone()
            if not row:
                raise HTTPException(404, 'Ticket not found')
            from accumulator_engine import _ticket_payload
            return _ticket_payload(connection, ticket_id)

    @router.post('/tickets/{ticket_id}/recalculate')
    def recalculate(ticket_id: str, user=Depends(auth)):
        with connect() as connection:
            payload = recalculate_ticket(connection, user, audit, ticket_id)
        if payload is None:
            raise HTTPException(404, 'Ticket not found')
        return payload

    @router.post('/tickets/{ticket_id}/legs/remove')
    def remove(ticket_id: str, payload: RemoveIn, user=Depends(auth)):
        with connect() as connection:
            updated = remove_leg(connection, user, audit, ticket_id, payload.opportunity_id)
        if updated is None:
            raise HTTPException(404, 'Ticket selection not found')
        return updated

    @router.post('/tickets/{ticket_id}/legs/replace')
    def replace(ticket_id: str, payload: ReplaceIn, user=Depends(auth)):
        with connect() as connection:
            updated = replace_leg(
                connection, user, audit, ticket_id, payload.remove_opportunity_id, payload.add_opportunity_id,
            )
        if updated is None:
            raise HTTPException(404, 'Ticket not found')
        if isinstance(updated, dict) and updated.get('error') and 'id' not in updated:
            raise HTTPException(409, updated['error'])
        return updated

    @router.post('/tickets/{ticket_id}/save')
    def save(ticket_id: str, user=Depends(auth)):
        with connect() as connection:
            payload = save_ticket(connection, user, audit, ticket_id)
        if payload is None:
            raise HTTPException(404, 'Ticket not found')
        return payload

    @router.post('/tickets/{ticket_id}/accept')
    def accept(ticket_id: str, user=Depends(auth)):
        with connect() as connection:
            payload = accept_ticket(connection, user, audit, ticket_id)
        if payload is None:
            raise HTTPException(404, 'Ticket not found')
        if payload.get('error'):
            raise HTTPException(409, payload['error'])
        return payload

    @router.get('/tickets/{ticket_id}/export')
    def export(ticket_id: str, user=Depends(auth)):
        with connect() as connection:
            payload = export_ticket(connection, user, audit, ticket_id)
        if payload is None:
            raise HTTPException(404, 'Ticket not found')
        if payload.get('error'):
            raise HTTPException(409, payload['error'])
        return payload

    @router.get('/command')
    def command(user=Depends(auth)):
        with connect() as connection:
            payload = command_centre(connection)
        payload['integration'] = integration_status()
        return payload

    @router.post('/monitor')
    def monitor(user=Depends(auth)):
        hour = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H')
        with connect() as connection:
            return monitor_accumulators(connection, user, audit, f'accumulator-monitor:{hour}')

    @router.get('/performance')
    def performance(user=Depends(auth)):
        with connect() as connection:
            rows = [dict(row) for row in connection.execute(
                'SELECT * FROM sports_ai_accumulator_performance ORDER BY tier,sport_mode',
            )]
        for row in rows:
            row['success_rate'] = (row['wins'] / row['settled_count']) if row['settled_count'] else None
            row['hypothetical'] = True
            row['label'] = 'Hypothetical settlement of stored tickets. Not an actual betting return.'
        return rows

    @router.post('/backtest')
    def backtest(user=Depends(auth)):
        with connect() as connection:
            return run_backtest(connection, user, audit)

    @router.get('/integration')
    def integration(user=Depends(auth)):
        return integration_status()

    @router.get('/betslip/{ticket_id}')
    def betslip(ticket_id: str, user=Depends(auth)):
        with connect() as connection:
            payload = prepare_betslip(connection, ticket_id)
        if payload is None:
            raise HTTPException(404, 'Ticket not found')
        payload['automatic_request'] = automatic_booking_code(ticket_id)
        return payload

    @router.post('/tickets/{ticket_id}/booking-code')
    def booking(ticket_id: str, payload: BookingIn, user=Depends(auth), _admin=Depends(admin)):
        with connect() as connection:
            stored = register_manual_code(connection, user, audit, ticket_id, payload.code, payload.evidence)
        if stored is None:
            raise HTTPException(404, 'Ticket not found')
        if stored.get('error'):
            raise HTTPException(409, stored['error'])
        return stored

    @router.get('/booking-codes')
    def codes(user=Depends(auth)):
        with connect() as connection:
            return list_codes(connection, accumulator_only=True)

    @router.get('/candidates')
    def candidates(sport_mode: str = 'mixed', user=Depends(auth)):
        if sport_mode not in ('football', 'basketball', 'mixed'):
            raise HTTPException(422, 'Sport mode must be football, basketball or mixed')
        with connect() as connection:
            config = load_config(connection)
            config['sport_mode'] = sport_mode
            accepted, decisions = qualify_candidates(
                load_opportunity_rows(connection), config, blocked_fixtures=_blocked(connection),
            )
        return {'accepted': accepted, 'rejected': [item for item in decisions if item['decision'] == 'rejected'][:50]}

    return router


def _blocked(connection):
    from accumulator_engine import _blocked_fixtures
    return _blocked_fixtures(connection)


def _comparison(ticket):
    parameters = ticket.get('parameters') or {}
    failed = [row for row in ticket.get('correlation') or [] if row.get('result') != 'pass']
    return {
        'id': ticket['id'],
        'label': parameters.get('tier_label') or str(ticket['tier']),
        'mode': parameters.get('tier_mode') or 'standard',
        'status': ticket['status'],
        'sport_mode': ticket['sport_mode'],
        'leg_count': ticket['leg_count'],
        'combined_odds': ticket.get('combined_odds'),
        'joint_probability': ticket.get('joint_probability'),
        'uncertainty': ticket.get('uncertainty'),
        'estimated_edge': ticket.get('estimated_edge'),
        'probability_basis': ticket.get('probability_basis'),
        'correlation_exposure': parameters.get('correlation_exposure', len(failed)),
    }
