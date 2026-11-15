"""Official webhook is sessionless; management retains existing RBAC."""
import asyncio
import hashlib
import hmac
import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from ..config import get_settings
from ..db import get_db
from ..models import WhatsAppIntake, WhatsAppEvent, User, utcnow
from ..security import require_role, ROLE_ADMIN, ROLE_OPERACIONAL
from ..audit import audit
from ..services import whatsapp as w
from ..services.funnel import aware
from .funnel import ContactCreate, resolve_person

router=APIRouter(prefix='/integrations/whatsapp',tags=['whatsapp'])
log=logging.getLogger(__name__)


@router.get('/webhook',response_class=PlainTextResponse)
def verify(request:Request):
    settings=get_settings()
    token=settings.whatsapp_verify_token
    query=request.query_params
    supplied=query.get('hub.verify_token','')
    challenge=query.get('hub.challenge','')
    if (not token or not token.get_secret_value().strip() or query.get('hub.mode')!='subscribe'
        or not hmac.compare_digest(supplied.encode(),token.get_secret_value().encode())
        or not re.fullmatch(r'[0-9]{1,128}',challenge)):
        raise HTTPException(403,'whatsapp_verification_failed')
    # Verification is deliberately available BEFORE enabling event processing.
    return PlainTextResponse(challenge,headers={'Cache-Control':'no-store'})


async def read_body(request):
    length=request.headers.get('content-length')
    if length is not None:
        if not re.fullmatch(r'[0-9]{1,12}',length):
            raise HTTPException(400,'whatsapp_invalid_length')
        if int(length)>w.MAX_BODY:
            raise HTTPException(413,'whatsapp_body_too_large')
    body=bytearray()
    try:
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body)>w.MAX_BODY:
                    raise HTTPException(413,'whatsapp_body_too_large')
    except TimeoutError:
        raise HTTPException(408,'whatsapp_body_timeout') from None
    except ClientDisconnect:
        raise HTTPException(400,'whatsapp_body_incomplete') from None
    return bytes(body)


def _process(db,events,request_id,app_state):
    try:
        w.process(db,events,request_id)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        # Never attach exception/SQL parameters, body or headers to logs.
        db.rollback()
        app_state.whatsapp_last_error={'code':'whatsapp_processing_failed','at':utcnow().isoformat()}
        log.warning('whatsapp_processing_failed')
        raise HTTPException(503,'whatsapp_processing_failed') from None


@router.post('/webhook')
async def webhook(request:Request,db:Session=Depends(get_db)):
    settings=get_settings()
    w.require_processing(settings)  # before reading/parsing any bytes
    if request.headers.get('origin'):
        raise HTTPException(403,'whatsapp_browser_origin_forbidden')
    if request.headers.get('content-type','').split(';',1)[0].strip().lower()!='application/json':
        raise HTTPException(415,'whatsapp_json_required')
    signature=request.headers.get('x-hub-signature-256','')
    if not re.fullmatch(r'sha256=[a-fA-F0-9]{64}',signature):
        raise HTTPException(403,'whatsapp_signature_invalid')
    body=await read_body(request)
    expected=hmac.new(settings.whatsapp_app_secret.get_secret_value().encode(),body,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature[7:].lower(),expected):
        raise HTTPException(403,'whatsapp_signature_invalid')
    try:
        payload=json.loads(body)
    except (ValueError,UnicodeError,RecursionError):
        raise HTTPException(400,'whatsapp_invalid_json') from None
    events=w.parse(payload,settings)
    if events:
        await run_in_threadpool(_process,db,events,request.state.request_id,request.app.state)
    return {'status':'ok'}


@router.get('/status')
def status(request:Request,db:Session=Depends(get_db),user:User=Depends(require_role(ROLE_ADMIN))):
    settings=get_settings();ready=w.config_ready(settings)
    last=db.scalar(select(func.max(WhatsAppEvent.received_at)))
    return {'enabled':settings.whatsapp_integration_enabled,'configuration_ready':ready,
        'processing_enabled':settings.whatsapp_integration_enabled and ready,
        'webhook':'operacional' if settings.whatsapp_integration_enabled and ready and last else ('configurável' if ready else 'não configurado'),
        'last_error':getattr(request.app.state,'whatsapp_last_error',None),
        'last_valid_event_at':aware(last).isoformat() if last else None,
        'diagnostic':('whatsapp_configuration_incomplete' if settings.whatsapp_integration_enabled and not ready else None),
        'message':'Integração ativa.' if settings.whatsapp_integration_enabled and ready else
            ('Configuração incompleta; processamento bloqueado.' if settings.whatsapp_integration_enabled else 'Preparado, aguardando ativação do número.')}


@router.get('/intakes')
def intakes(pagina:int=Query(1,ge=1),db:Session=Depends(get_db),user:User=Depends(require_role(ROLE_OPERACIONAL))):
    rows=db.scalars(select(WhatsAppIntake).where(WhatsAppIntake.state!='identified')
        .order_by(WhatsAppIntake.first_at,WhatsAppIntake.id).offset((pagina-1)*50).limit(51)).all()
    return {'itens':[{'id':r.id,'first_at':aware(r.first_at).isoformat(),
        'phone_masked':'•••• '+r.phone_suffix,'state':r.state,'lead_id':r.lead_id} for r in rows[:50]],
        'tem_proxima':len(rows)>50,'pagina':pagina}


class Identify(ContactCreate):
    lead_id: str | None = None
    new_contact: bool = False


@router.post('/intakes/{intake_id}/identify')
def identify(intake_id:str,payload:Identify,request:Request,db:Session=Depends(get_db),user:User=Depends(require_role(ROLE_OPERACIONAL))):
    # The human can resolve queued intake while disabled; disabled POST webhook
    # still cannot create anything. Identity comes from explicit selection only.
    w.transaction_limits(db)
    intake=db.get(WhatsAppIntake,intake_id)
    if not intake:
        raise HTTPException(404,'Entrada não encontrada.')
    w.lock_peer(db,intake.sender_key)
    db.refresh(intake,with_for_update=True)
    if intake.lead_id:
        return {'lead_id':intake.lead_id,'idempotente':True}
    if payload.primeiro_contato_em or payload.partner_id or payload.partner_unit_id or payload.origem_aquisicao!='nao_informado' or payload.canal_entrada not in ('nao_informado','whatsapp'):
        raise HTTPException(422,'Identificação preserva horário, canal e aquisição do intake.')
    # Existing duplicate checks and human-confirmed registration from the funnel.
    person=resolve_person(db,payload,request,user)
    if not w.bind(db,intake,person,user_id=user.id,request_id=request.state.request_id,
                  lead_id=payload.lead_id,new_contact=payload.new_contact):
        options=w.compatible_leads(db,person.id,intake.first_at)
        db.rollback()
        raise HTTPException(409,{'codigo':'whatsapp_ambiguous_lead',
            'mensagem':'Escolha o contato existente ou confirme um novo contato.',
            'contatos':[{'id':l.id,'public_code':l.public_code} for l in options]})
    audit(db,'whatsapp.identificado','whatsapp_intakes',intake.id,user.id,request.state.request_id,{'decisao':'identificar'})
    db.commit()
    return {'lead_id':intake.lead_id,'idempotente':False}
