"""Inbound operational metadata only; no HTTP client and no message storage."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import re
import time

from fastapi import HTTPException
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..audit import audit
from ..ids import allocate_public_code
from ..models import (Lead, Person, PersonContact, WhatsAppPeer, WhatsAppIntake,
                      WhatsAppEvent, utcnow)
from ..normalize import normalize_phone
from . import funnel as f

MAX_BODY = 256 * 1024
MAX_EVENTS = 100
TYPES = frozenset(('text','image','audio','video','document','sticker','location','contacts','interactive','button'))
PHONE = re.compile(r'^\+?[1-9][0-9]{6,14}$')


def config_ready(settings):
    secrets = (settings.whatsapp_verify_token, settings.whatsapp_app_secret,
               settings.whatsapp_identity_key)
    return bool(all(s and s.get_secret_value().strip() for s in secrets)
                and len(settings.whatsapp_identity_key.get_secret_value()) >= 32
                and len(set(settings.whatsapp_identity_key.get_secret_value())) >= 10
                and re.fullmatch(r'[0-9]{1,40}', settings.whatsapp_phone_number_id or '')
                and re.fullmatch(r'[0-9]{1,40}', settings.whatsapp_waba_id or ''))


def require_processing(settings):
    if not settings.whatsapp_integration_enabled:
        raise HTTPException(503, 'whatsapp_disabled')
    if not config_ready(settings):
        raise HTTPException(503, 'whatsapp_configuration_incomplete')


def digest(settings, purpose, value):
    return hmac.new(settings.whatsapp_identity_key.get_secret_value().encode(),
                    (purpose+'\0'+value).encode(), hashlib.sha256).hexdigest()


def sender_key(settings, phone):
    return digest(settings, 'whatsapp-sender-v1',
                  settings.whatsapp_waba_id+':'+settings.whatsapp_phone_number_id+':'+phone)


@dataclass(frozen=True)
class Event:
    key: str
    sender: str
    phone: str  # memory only; discarded before database/audit/log boundaries
    direction: str
    at: datetime


def _items(value, limit):
    if not isinstance(value, list) or len(value) > limit:
        raise HTTPException(400, 'whatsapp_invalid_structure')
    return value


def parse(payload, settings):
    """Allowlist official shapes, ignore extensions without traversing content."""
    if not isinstance(payload, dict) or payload.get('object') != 'whatsapp_business_account':
        raise HTTPException(400, 'whatsapp_invalid_object')
    events = []
    now = utcnow()
    for entry in _items(payload.get('entry'), 20):
        if not isinstance(entry, dict):
            raise HTTPException(400, 'whatsapp_invalid_structure')
        if entry.get('id') != settings.whatsapp_waba_id:
            raise HTTPException(403, 'whatsapp_account_not_allowed')
        for change in _items(entry.get('changes'), 20):
            if not isinstance(change, dict):
                raise HTTPException(400, 'whatsapp_invalid_structure')
            field = change.get('field')
            if field not in ('messages', 'smb_message_echoes'):
                continue
            value = change.get('value')
            if not isinstance(value, dict) or value.get('messaging_product') != 'whatsapp':
                raise HTTPException(400, 'whatsapp_invalid_structure')
            metadata = value.get('metadata')
            if not isinstance(metadata, dict) or metadata.get('phone_number_id') != settings.whatsapp_phone_number_id:
                raise HTTPException(403, 'whatsapp_number_not_allowed')
            direction = 'in' if field == 'messages' else 'out'
            for msg in _items(value.get('messages' if direction == 'in' else 'message_echoes', []), MAX_EVENTS):
                if not isinstance(msg, dict):
                    continue
                if (not isinstance(msg.get('type'),str) or msg.get('type') not in TYPES
                    or msg.get('recipient_type') in ('group','broadcast')
                    or value.get('recipient_type') in ('group','broadcast') or any(msg.get(k) for k in
                    ('group_id','group','broadcast','broadcast_id','is_group','is_broadcast',
                     'is_automated','automated','system','errors'))
                    or any(value.get(k) for k in ('group_id','group','broadcast','broadcast_id'))
                    or any(isinstance(node,dict) and any(node.get(k) for k in
                        ('group_id','group','broadcast','broadcast_id','is_group','is_broadcast'))
                        for node in (metadata,msg.get('context')))):
                    continue
                phone = msg.get('from' if direction == 'in' else 'to')
                key = msg.get('id')
                stamp = msg.get('timestamp')
                if not isinstance(phone, str) or not PHONE.fullmatch(phone):
                    continue  # includes @g.us, broadcast, usernames and unrecognized identifiers
                phone = normalize_phone('+'+phone.lstrip('+'))  # Meta declares explicit DDI
                if direction == 'out':
                    business = metadata.get('display_phone_number')
                    origin = msg.get('from')
                    if (not isinstance(business, str) or not PHONE.fullmatch(business)
                        or not isinstance(origin, str) or not PHONE.fullmatch(origin)
                        or normalize_phone('+'+origin.lstrip('+')) != normalize_phone('+'+business.lstrip('+'))):
                        continue
                if not isinstance(key, str) or not 1 <= len(key) <= 256:
                    continue
                if isinstance(stamp, bool) or not isinstance(stamp, (str,int)) or not re.fullmatch(r'[0-9]{1,10}', str(stamp)):
                    continue
                try:
                    at = datetime.fromtimestamp(int(stamp), timezone.utc)
                except (ValueError, OverflowError, OSError):
                    continue
                if at < datetime(2020,1,1,tzinfo=timezone.utc) or at > now:
                    continue
                events.append(Event(digest(settings, 'whatsapp-event-v1',
                    settings.whatsapp_waba_id+':'+settings.whatsapp_phone_number_id+':'+key),
                    sender_key(settings, phone), phone, direction, at))
                if len(events) > MAX_EVENTS:
                    raise HTTPException(413, 'whatsapp_too_many_events')
    return sorted(events, key=lambda e:(e.sender,e.at,e.direction != 'in',e.key))


def lock_peer(db, key):
    insert = pg_insert if db.bind.dialect.name == 'postgresql' else sqlite_insert
    db.execute(insert(WhatsAppPeer).values(sender_key=key).on_conflict_do_nothing())
    db.scalar(select(WhatsAppPeer).where(WhatsAppPeer.sender_key==key).with_for_update())


def exact_people(db, phone):
    # Do not normalize historical records/backfill or fuzzy-match. DISTINCT persons.
    return db.scalars(select(Person).join(PersonContact).where(
        Person.arquivado.is_(False), Person.arquivado_em.is_(None),
        PersonContact.ativo.is_(True), PersonContact.tipo.in_(('telefone','whatsapp')),
        PersonContact.valor_normalizado==phone).distinct().limit(2)).unique().all()


def compatible_leads(db, person_id, at):
    return db.scalars(select(Lead).where(Lead.person_id==person_id,
        Lead.primeiro_contato_em.is_not(None), Lead.primeiro_contato_em<=at,
        Lead.status_funil.not_in(f.CLOSED), Lead.canal_entrada=='whatsapp',
        Lead.partner_id.is_(None), Lead.partner_unit_id.is_(None),
        Lead.origem_aquisicao.not_in(('pastore','parceiro')))
        .order_by(Lead.id).with_for_update()).all()


def bind(db, intake, person, *, user_id=None, request_id=None, lead_id=None, new_contact=False):
    db.refresh(person,with_for_update=True)
    if person.arquivado or person.arquivado_em:
        raise HTTPException(409, 'Pessoa indisponível.')
    options = compatible_leads(db, person.id, intake.first_at)
    if lead_id:
        lead = next((l for l in options if l.id==lead_id), None)
        if not lead:
            raise HTTPException(409, 'Contato incompatível com pessoa, canal, parceria ou horário.')
    elif len(options) > 1 and not new_contact:
        intake.state = 'ambiguous_lead'
        return False
    elif len(options)==1 and not new_contact:
        lead = options[0]
    else:
        lead = Lead(public_code=allocate_public_code(db,'leads'), person_id=person.id,
            origem_aquisicao='nao_informado', origem=f.ORIGINS['nao_informado'],
            canal_entrada='whatsapp', unidade_pretendida='nao_definido', servico_interesse='outro',
            primeiro_contato_em=intake.first_at,
            data_primeiro_contato=f.aware(intake.first_at).astimezone(f.TZ).date(),
            data_primeiro_contato_precisao='dia',data_primeiro_contato_dia_assumido=False,
            status_funil='NOVO_CONTATO',etapa='novo')
        db.add(lead); db.flush()
        f.record(db,lead,'NOVO_CONTATO',user_id,at=intake.first_at)
        audit(db,'whatsapp.contato_criado','leads',lead.id,user_id,request_id,{'canal':'whatsapp'})
    intake.lead_id = lead.id
    intake.state = 'identified'
    db.flush()
    reconcile_response(db,intake,lead)
    return True


def reconcile_response(db, intake, lead=None):
    if not intake.lead_id:
        return
    lead = lead or f.lead_for_update(db,intake.lead_id)
    # Echo can arrive before inbound or before human identification. Only the
    # event-time interval of this intake is eligible, never an earlier conversation.
    response = db.scalar(select(WhatsAppEvent).where(
        WhatsAppEvent.intake_id==intake.id, WhatsAppEvent.direction=='out',
        WhatsAppEvent.event_at>=intake.first_at,
        WhatsAppEvent.event_at>=lead.primeiro_contato_em,
        WhatsAppEvent.state=='pending').order_by(WhatsAppEvent.event_at,WhatsAppEvent.event_key).limit(1))
    if response and not lead.primeira_resposta_em and lead.status_funil not in f.CLOSED:
        lead.primeira_resposta_em = response.event_at
        if lead.status_funil=='NOVO_CONTATO':
            lead.status_funil='PRIMEIRA_RESPOSTA'; lead.etapa='em_contato'
        f.record(db,lead,'PRIMEIRA_RESPOSTA',at=response.event_at)
        audit(db,'whatsapp.primeira_resposta','leads',lead.id,detalhes={'canal':'whatsapp'})
    db.execute(update(WhatsAppEvent).where(WhatsAppEvent.intake_id==intake.id,
        WhatsAppEvent.state=='pending').values(state='processed'))


def transaction_limits(db):
    if db.bind.dialect.name=='postgresql':
        db.execute(text("SET LOCAL statement_timeout = '3000ms'"))
        db.execute(text("SET LOCAL lock_timeout = '1500ms'"))


def process(db, events, request_id=None):
    started=time.monotonic()
    transaction_limits(db)
    # Stable lock order across batches and manual identification.
    for key in sorted({e.sender for e in events}):
        if time.monotonic()-started > 5:
            raise HTTPException(503, 'whatsapp_processing_timeout')
        lock_peer(db,key)
    for e in events:
        if time.monotonic()-started > 5:
            raise HTTPException(503, 'whatsapp_processing_timeout')
        if db.get(WhatsAppEvent,e.key):
            continue
        intake=db.scalar(select(WhatsAppIntake).where(WhatsAppIntake.sender_key==e.sender)
            .order_by(WhatsAppIntake.created_at.desc(),WhatsAppIntake.id).limit(1).with_for_update())
        lead=f.lead_for_update(db,intake.lead_id) if intake and intake.lead_id else None
        # Reopening is explicit: closed opportunities are never recycled.
        # A late echo or inbound cannot open a new opportunity retroactively.
        if (intake and lead and lead.status_funil in f.CLOSED and e.direction=='in'
            and e.at > f.aware(lead.updated_at)):
            intake=None; lead=None
        if not intake and e.direction=='in':
            intake=WhatsAppIntake(sender_key=e.sender,phone_suffix=e.phone[-4:],first_at=e.at,state='unknown')
            db.add(intake);db.flush()
        event=WhatsAppEvent(event_key=e.key,sender_key=e.sender,intake_id=intake.id if intake else None,
            direction=e.direction,event_at=e.at,state='pending')
        db.add(event);db.flush()
        if not intake:
            continue  # out-of-order echo: await inbound, never creates patient/intake/lead
        if e.direction=='in':
            if e.at < f.aware(intake.first_at):
                if intake.lead_id:
                    # No silent rewriting of an immutable funnel chronology.
                    intake.state='chronology_review';event.state='ignored'
                    continue
                intake.first_at=e.at
            if not intake.lead_id:
                people=exact_people(db,e.phone)
                if len(people)==1:
                    bind(db,intake,people[0],request_id=request_id)
                else:
                    intake.state='ambiguous_person' if people else 'unknown'
            # Associate orphan echoes only if they belong after this arrival.
            db.execute(update(WhatsAppEvent).where(
                WhatsAppEvent.sender_key==e.sender,WhatsAppEvent.intake_id.is_(None),
                WhatsAppEvent.direction=='out', WhatsAppEvent.event_at>=intake.first_at)
                .values(intake_id=intake.id))
        db.flush()
        reconcile_response(db,intake)
    db.commit()
