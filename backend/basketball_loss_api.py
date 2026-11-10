"""Basketball forensic read models and administrator evidence import."""
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from db import connect


class EvidenceIn(BaseModel):
    records: list[dict] = Field(min_length=1,max_length=200)


class ReconcileIn(BaseModel):
    prediction_id:str
    placed_at:str
    source_reference:str=Field(min_length=1,max_length=2000)


def build_basketball_loss_router(auth, require_admin_key):
    router=APIRouter(prefix='/api/sports-ai/basketball-losses',tags=['Basketball loss investigation'])

    @router.get('')
    def view(user=Depends(auth)):
        from basketball_losses import dashboard
        with connect() as c: return dashboard(c)

    @router.post('/import')
    def import_records(body:EvidenceIn,x_sports_admin_key:str|None=Header(default=None),user=Depends(auth)):
        require_admin_key(x_sports_admin_key)
        from basketball_losses import import_evidence,investigate
        try:
            with connect() as c:
                result=import_evidence(c,body.records,str(user));investigate(c,str(user));return result
        except (ValueError,TypeError,KeyError) as error: raise HTTPException(422,str(error)) from error

    @router.post('/investigate')
    def run(x_sports_admin_key:str|None=Header(default=None),user=Depends(auth)):
        require_admin_key(x_sports_admin_key)
        from basketball_losses import investigate
        from basketball_validation import validate
        with connect() as c: return {'audit':investigate(c,str(user)),'validation':validate(c,str(user))}

    @router.post('/reconcile/{selection_id}')
    def reconcile(selection_id:str,body:ReconcileIn,x_sports_admin_key:str|None=Header(default=None),user=Depends(auth)):
        require_admin_key(x_sports_admin_key)
        from basketball_losses import reconcile_selection
        try:
            with connect() as c: return reconcile_selection(c,selection_id,body.prediction_id,body.placed_at,body.source_reference,str(user))
        except (ValueError,TypeError,KeyError) as error: raise HTTPException(422,str(error)) from error

    @router.post('/promote/{candidate_id}')
    def activate(candidate_id:str,x_sports_admin_key:str|None=Header(default=None),user=Depends(auth)):
        require_admin_key(x_sports_admin_key)
        from basketball_validation import promote
        try:
            with connect() as c: return promote(c,candidate_id,str(user))
        except ValueError as error: raise HTTPException(409,str(error)) from error

    return router
