"""High-odds read models and durable command endpoints using existing auth."""
from datetime import date
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from db import connect
from high_odds_service import dashboard, live_opportunities, save_settings, enqueue, audit


class SettingsIn(BaseModel):
    settings: dict = Field(default_factory=dict)
    categories: list[dict] = Field(default_factory=list)


class GenerateIn(BaseModel):
    idempotency_key: str = Field(min_length=1,max_length=100)


def build_high_odds_router(auth, require_admin_key):
    router=APIRouter(prefix='/api/sports-ai/high-odds',tags=['Autonomous high odds'])

    @router.get('')
    def view(date_from:date|None=None,date_to:date|None=None,status:str|None=None,category_id:str|None=None,user=Depends(auth)):
        if date_from and date_to and date_from>date_to:
            raise HTTPException(422,'Start date must precede end date')
        with connect() as c:
            return dashboard(c,date_from=date_from.isoformat() if date_from else None,
                date_to=date_to.isoformat() if date_to else None,status=status,category_id=category_id)

    @router.get('/opportunities')
    def opportunities(sport:str|None=None,user=Depends(auth)):
        with connect() as c: return live_opportunities(c,sport=sport)

    @router.put('/settings')
    def settings(payload:SettingsIn,x_sports_admin_key:str|None=Header(default=None),user=Depends(auth)):
        require_admin_key(x_sports_admin_key)
        try:
            with connect() as c: return save_settings(c,payload.settings,payload.categories,str(user))
        except (ValueError,TypeError) as e: raise HTTPException(422,str(e)) from e

    @router.post('/generate',status_code=202)
    def generate(payload:GenerateIn,user=Depends(auth)):
        with connect() as c:
            identifier=enqueue(c,'operator.request',{'actor':str(user)},'operator:'+payload.idempotency_key)
            return {'event_id':identifier,'status':'queued','places_wager':False}

    @router.post('/events/{event_id}/retry',status_code=202)
    def retry(event_id:str,user=Depends(auth)):
        with connect() as c:
            row=c.execute('SELECT status FROM sports_ai_high_odds_events WHERE id=?',(event_id,)).fetchone()
            if not row: raise HTTPException(404,'Event not found')
            if row['status']!='failed': raise HTTPException(409,'Only failed events can be retried')
            c.execute("UPDATE sports_ai_high_odds_events SET status='queued',attempts=0,available_at=CURRENT_TIMESTAMP WHERE id=?",(event_id,))
            audit(c,'event.retry_requested',{'actor':str(user)},event_id=event_id)
            return {'event_id':event_id,'status':'queued'}

    @router.get('/tickets/{ticket_id}')
    def ticket(ticket_id:str,user=Depends(auth)):
        with connect() as c:
            row=c.execute('SELECT * FROM sports_ai_high_odds_tickets WHERE ticket_id=?',(ticket_id,)).fetchone()
            if not row: raise HTTPException(404,'High-odds ticket not found')
            import json
            from accumulator_engine import _ticket_payload
            result=_ticket_payload(c,ticket_id)
            result['snapshot']=json.loads(row['snapshot_json'])
            result['audit']=[dict(r) for r in c.execute('SELECT * FROM sports_ai_high_odds_audit WHERE ticket_id=? ORDER BY created_at',(ticket_id,))]
            return result
    return router
