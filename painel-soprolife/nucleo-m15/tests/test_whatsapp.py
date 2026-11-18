"""Synthetic official-shaped fixtures, no provider I/O or real identities."""
from datetime import timedelta
import hashlib
import hmac
import json
import sys
import pytest
from sqlalchemy import select, text
from app.config import Settings, get_settings
from app.main import create_app
from app.models import Person, Lead, FunnelEvent, WhatsAppPeer, WhatsAppIntake, WhatsAppEvent, utcnow
from app.services import whatsapp as w
from app.services.funnel import aware
from tests.conftest import SYNTH_PHONE_NORM

BASE='/api/v1/integrations/whatsapp'
SECRET='synthetic-app-secret-not-a-real-credential'
IDENTITY='synthetic-identity-key-not-a-real-credential-0123456789'
STAMP=int((utcnow()-timedelta(minutes=10)).timestamp())

@pytest.fixture(autouse=True)
def synthetic_message_clock(monkeypatch):
    # A suíte ampla pode levar >5 min: o evento deve ser relativo a ESTE teste,
    # não à coleta do módulo, para não tornar o lead sintético mais novo que ele.
    monkeypatch.setattr(sys.modules[__name__], 'STAMP', int((utcnow()-timedelta(minutes=10)).timestamp()))

@pytest.fixture
def active(monkeypatch):
    for k,v in {'INTEGRATION_ENABLED':'true','VERIFY_TOKEN':'synthetic-verify','APP_SECRET':SECRET,
        'PHONE_NUMBER_ID':'100000000000001','WABA_ID':'100000000000002','IDENTITY_KEY':IDENTITY}.items():
        monkeypatch.setenv('WHATSAPP_'+k,v)
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()

def payload(message_id='synthetic-message-1',phone=SYNTH_PHONE_NORM,direction='in',stamp=None,kind='text',**extra):
    stamp = STAMP if stamp is None else stamp
    msg={'id':message_id,'timestamp':str(stamp),'type':kind,'from':phone if direction=='in' else '552100000000',
         'text':{'body':'SYNTHETIC_PRIVATE_BODY clinical content never persisted'},**extra}
    if direction=='out':msg['to']=phone
    return {'object':'whatsapp_business_account','entry':[{'id':'100000000000002','changes':[{
        'field':'messages' if direction=='in' else 'smb_message_echoes',
        'value':{'messaging_product':'whatsapp','metadata':{'phone_number_id':'100000000000001','display_phone_number':'552100000000'},
        'messages' if direction=='in' else 'message_echoes':[msg]}}]}]}

def post(client,data,**headers):
    raw=data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False).encode()
    sig='sha256='+hmac.new(SECRET.encode(),raw,hashlib.sha256).hexdigest()
    return client.post(BASE+'/webhook',content=raw,headers={'Content-Type':'application/json','X-Hub-Signature-256':sig,**headers})

def rows(db,model):
    db.expire_all()
    result=db.scalars(select(model)).all()
    db.commit()  # release test reader before the next request-scoped writer
    return result

def test_disabled_boot_without_credentials_and_zero_writes(client,db,monkeypatch):
    for k in ('INTEGRATION_ENABLED','VERIFY_TOKEN','APP_SECRET','PHONE_NUMBER_ID','WABA_ID','IDENTITY_KEY'):
        monkeypatch.delenv('WHATSAPP_'+k,raising=False)
    get_settings.cache_clear()
    assert not Settings().whatsapp_integration_enabled and create_app()
    assert post(client,payload()).status_code==503
    for model in (Person,Lead,WhatsAppPeer,WhatsAppIntake,WhatsAppEvent):assert not rows(db,model)
    get_settings.cache_clear()

def test_challenge_correct_wrong_and_disabled(client,monkeypatch):
    monkeypatch.setenv('WHATSAPP_VERIFY_TOKEN','synthetic-verify');get_settings.cache_clear()
    p={'hub.mode':'subscribe','hub.verify_token':'synthetic-verify','hub.challenge':'123456'}
    r=client.get(BASE+'/webhook',params=p)
    assert r.status_code==200 and r.text=='123456' and r.headers['cache-control']=='no-store'
    p['hub.verify_token']='wrong';assert client.get(BASE+'/webhook',params=p).status_code==403
    p['hub.verify_token']='synthetic-verify';p['hub.challenge']='<script>'
    assert client.get(BASE+'/webhook',params=p).status_code==403
    get_settings.cache_clear()

def test_incomplete_fail_closed_core_healthy(active,client,auth,monkeypatch):
    monkeypatch.setattr(active,'whatsapp_app_secret',None)
    assert create_app() and post(client,payload()).status_code==503
    assert client.get('/api/v1/health').status_code==200
    s=client.get(BASE+'/status',headers=auth()).json()
    assert not s['processing_enabled'] and s['diagnostic']=='whatsapp_configuration_incomplete'

@pytest.mark.parametrize('data,headers,status',[
    (b'{',{},400),(b'[]',{},400),(b'null',{},400),(b'{"object":"wrong"}',{},400),
    (b'{}',{'X-Hub-Signature-256':'sha256='+'0'*64},403),(b'{}',{'X-Hub-Signature-256':'bad'},403),
    (b'{}',{'Content-Type':'text/plain'},415),(b'{}',{'Origin':'https://untrusted.invalid'},403),
    (b'{}',{'Content-Length':'-1'},400),(b'{}',{'Content-Length':str(w.MAX_BODY+1)},413),
    (b' '*(w.MAX_BODY+1),{},413)])
def test_invalid_signature_payload_and_limits(active,client,db,data,headers,status):
    assert post(client,data,**headers).status_code==status
    assert not rows(db,WhatsAppEvent)

def test_signed_wrong_origin(active,client,db):
    p=payload();p['entry'][0]['id']='999';assert post(client,p).status_code==403
    p=payload();p['entry'][0]['changes'][0]['value']['metadata']['phone_number_id']='999'
    assert post(client,p).status_code==403 and not rows(db,WhatsAppEvent)

def test_unknown_no_patient_content_logs_or_media(active,client,db,caplog,monkeypatch):
    import httpx,requests,urllib.request
    def forbidden(*a,**k):raise AssertionError('No external I/O allowed')
    monkeypatch.setattr(requests.sessions.Session,'request',forbidden)
    monkeypatch.setattr(httpx,'get',forbidden);monkeypatch.setattr(urllib.request,'urlopen',forbidden)
    assert post(client,payload(kind='image',image={'id':'PRIVATE_MEDIA','caption':'PRIVATE_CAPTION','url':'https://media.invalid/private'})).status_code==200
    assert not rows(db,Person) and not rows(db,Lead)
    intake=rows(db,WhatsAppIntake)[0]
    assert intake.phone_suffix=='9001' and intake.state=='unknown' and aware(intake.first_at).timestamp()==STAMP
    for table in ('whatsapp_intakes','whatsapp_events','whatsapp_peers','audit_logs'):
        content=str(db.execute(text('SELECT * FROM '+table)).all())+caplog.text
        for value in ('SYNTHETIC_PRIVATE_BODY',SYNTH_PHONE_NORM,'clinical content','PRIVATE_MEDIA','PRIVATE_CAPTION','media.invalid'):
            assert value not in content

def test_exact_one_person_twenty_replays_first_timestamp_channel_origin(active,client,person,db):
    for _ in range(20):assert post(client,payload()).status_code==200
    assert len(rows(db,Person))==len(rows(db,Lead))==1
    lead=rows(db,Lead)[0];first=lead.primeiro_contato_em
    assert lead.person_id==person['id'] and lead.canal_entrada=='whatsapp'
    assert lead.origem_aquisicao=='nao_informado' and lead.origem=='Não informado' and aware(first).timestamp()==STAMP
    assert post(client,payload('synthetic-message-2',stamp=STAMP+60)).status_code==200
    assert len(rows(db,Lead))==1 and rows(db,Lead)[0].primeiro_contato_em==first
    assert len(rows(db,WhatsAppEvent))==2 and len(rows(db,FunnelEvent))==1

def test_ambiguous_person_no_auto_choice(active,client,person,auth,db):
    r=client.post('/api/v1/pessoas',headers=auth(),json={'nome_completo':'Outra Pessoa Sintética',
        'contatos':[{'tipo':'telefone','valor':SYNTH_PHONE_NORM}]})
    assert r.status_code==201,r.text
    assert post(client,payload()).status_code==200
    assert len(rows(db,Person))==2 and not rows(db,Lead)
    assert rows(db,WhatsAppIntake)[0].state=='ambiguous_person'

@pytest.mark.parametrize('early_echo',[False,True])
def test_echo_timestamp_replays_and_out_of_order(active,client,person,db,early_echo):
    if early_echo:assert post(client,payload('echo-1',direction='out',stamp=STAMP+60)).status_code==200
    assert post(client,payload()).status_code==200
    for _ in range(3):assert post(client,payload('echo-1',direction='out',stamp=STAMP+60)).status_code==200
    lead=rows(db,Lead)[0]
    assert aware(lead.primeira_resposta_em).timestamp()==STAMP+60 and lead.status_funil=='PRIMEIRA_RESPOSTA'
    assert post(client,payload('echo-2',direction='out',stamp=STAMP+120)).status_code==200
    assert aware(rows(db,Lead)[0].primeira_resposta_em).timestamp()==STAMP+60
    assert [e.event_type for e in rows(db,FunnelEvent)].count('PRIMEIRA_RESPOSTA')==1

def test_earlier_echo_automated_system_and_status_not_response(active,client,person,db):
    assert post(client,payload('early',direction='out',stamp=STAMP-60)).status_code==200
    assert post(client,payload()).status_code==200
    assert post(client,payload('system',direction='out',kind='system')).status_code==200
    assert post(client,payload('auto',direction='out',is_automated=True)).status_code==200
    p=payload();v=p['entry'][0]['changes'][0]['value'];v.pop('messages');v['statuses']=[{'status':'read','id':'synthetic'}]
    assert post(client,p).status_code==200 and rows(db,Lead)[0].primeira_resposta_em is None

@pytest.mark.parametrize('extra',[{'group_id':'synthetic'},{'broadcast':True},{'from':'synthetic@g.us'},
    {'type':'system'},{'type':'future_type'},{'timestamp':[]},{'timestamp':'9999999999'},
    {'timestamp':True},{'from':{}},{'id':[]},{'type':[]},{'recipient_type':'group'},
    {'context':{'group_id':'synthetic-group'}},{'context':{'broadcast_id':'synthetic-broadcast'}}])
def test_unknown_group_broadcast_and_malformed_message_ignored(active,client,db,extra):
    assert post(client,payload(**extra)).status_code==200
    assert not rows(db,WhatsAppIntake) and not rows(db,Person)

def test_unknown_extension_and_wrong_nesting(active,client,db):
    p=payload();p['entry'][0]['changes'][0]['field']='unknown_future';assert post(client,p).status_code==200
    p=payload();p['entry'][0]['changes'][0]['value']['messages']={};assert post(client,p).status_code==400
    assert not rows(db,WhatsAppEvent)

def test_manual_identification_preserves_time_echo_and_rbac(active,client,auth,person,db):
    phone='552100009002'
    assert post(client,payload(phone=phone)).status_code==200
    assert post(client,payload('echo-unknown',phone=phone,direction='out',stamp=STAMP+90)).status_code==200
    intake=rows(db,WhatsAppIntake)[0];db.commit()
    body={'person_id':person['id'],'idempotency_key':'manual-synthetic','canal_entrada':'whatsapp'}
    route=BASE+'/intakes/'+intake.id+'/identify'
    assert client.post(route,json=body).status_code==401
    assert client.post(route,json=body,headers=auth('leitura')).status_code==403
    assert client.post(route,json=body,headers=auth('operacional')).status_code==200
    assert client.post(route,json=body,headers=auth('operacional')).json()['idempotente']
    lead=rows(db,Lead)[0]
    assert aware(lead.primeiro_contato_em).timestamp()==STAMP and aware(lead.primeira_resposta_em).timestamp()==STAMP+90
    assert len(rows(db,Person))==len(rows(db,Lead))==1
    assert client.get(BASE+'/status').status_code==401
    assert client.get(BASE+'/status',headers=auth('gestor')).status_code==403
    assert client.get(BASE+'/intakes',headers=auth('leitura')).status_code==403
    assert not client.get(BASE+'/intakes',headers=auth('operacional')).json()['itens']

def test_manual_registration_not_webhook_creates_person(active,client,auth,db):
    assert post(client,payload()).status_code==200
    intake=rows(db,WhatsAppIntake)[0];db.commit()
    r=client.post(BASE+'/intakes/'+intake.id+'/identify',headers=auth('operacional'),
        json={'pessoa':{'nome_completo':'Pessoa Manual Sintética'},'idempotency_key':'manual-new'})
    assert r.status_code==200,r.text
    assert len(rows(db,Person))==1 and aware(rows(db,Lead)[0].primeiro_contato_em).timestamp()==STAMP

def test_compatible_reuse_preserves_acquisition(active,client,person,db):
    lead=Lead(public_code='LEA-WATEST',person_id=person['id'],primeiro_contato_em=utcnow()-timedelta(minutes=15),
        canal_entrada='whatsapp',origem_aquisicao='google_maps',status_funil='NOVO_CONTATO',unidade_pretendida='barra',servico_interesse='espirometria')
    db.add(lead);db.commit()
    assert post(client,payload()).status_code==200
    assert len(rows(db,Lead))==1 and rows(db,Lead)[0].origem_aquisicao=='google_maps'

def test_failed_transaction_no_sensitive_exception_logging(active,client,person,db,caplog,monkeypatch):
    def fail(*a,**k):raise RuntimeError('SYNTHETIC_PRIVATE_BODY '+SYNTH_PHONE_NORM+' '+SECRET)
    monkeypatch.setattr(w,'bind',fail)
    r=post(client,payload());assert r.status_code==503
    for value in ('SYNTHETIC_PRIVATE_BODY',SYNTH_PHONE_NORM,SECRET):assert value not in caplog.text and value not in r.text
    assert not rows(db,WhatsAppEvent) and not rows(db,WhatsAppIntake)


def test_chunked_body_actual_limit_and_signed_bytes(active,client):
    raw=json.dumps(payload(),ensure_ascii=False).encode()
    sig='sha256='+hmac.new(SECRET.encode(),raw,hashlib.sha256).hexdigest()
    # Signature on original bytes only: reserialization cannot validate.
    altered=json.dumps(payload(),separators=(',',':')).encode()
    r=client.post(BASE+'/webhook',content=altered,headers={'Content-Type':'application/json','X-Hub-Signature-256':sig})
    assert r.status_code==403
    r=client.post(BASE+'/webhook',content=iter([b' '*(w.MAX_BODY//2),b' '*w.MAX_BODY]),headers={
        'Content-Type':'application/json','X-Hub-Signature-256':sig})
    assert r.status_code==413


def test_ambiguous_open_leads_requires_explicit_selection(active,client,person,auth,db):
    for i in range(2):
        db.add(Lead(public_code='LEA-WA'+str(i),person_id=person['id'],primeiro_contato_em=utcnow()-timedelta(minutes=15),
            canal_entrada='whatsapp',origem_aquisicao='site',status_funil='NOVO_CONTATO'))
    db.commit()
    assert post(client,payload()).status_code==200
    intake=rows(db,WhatsAppIntake)[0]
    assert intake.state=='ambiguous_lead' and intake.lead_id is None
    intake_id=intake.id;lead_id=rows(db,Lead)[0].id;db.commit()
    body={'person_id':person['id'],'idempotency_key':'ambiguous-manual'}
    route=BASE+'/intakes/'+intake_id+'/identify'
    r=client.post(route,headers=auth(),json=body)
    assert r.status_code==409 and len(r.json()['erro']['mensagem']['contatos'])==2
    r=client.post(route,headers=auth(),json={**body,'lead_id':lead_id})
    assert r.status_code==200 and r.json()['lead_id']==lead_id
    assert len(rows(db,Lead))==2


def test_no_pastore_acquisition_or_partner_inference(active,client,person,db):
    db.add(Lead(public_code='LEA-WAPAST',person_id=person['id'],primeiro_contato_em=utcnow()-timedelta(minutes=15),
        canal_entrada='whatsapp',origem_aquisicao='pastore',status_funil='NOVO_CONTATO'))
    db.commit()
    assert post(client,payload()).status_code==200
    leads=rows(db,Lead)
    assert len(leads)==2
    fresh=next(l for l in leads if l.origem_aquisicao=='nao_informado')
    assert fresh.partner_id is None and fresh.partner_unit_id is None


def test_closed_conversation_new_inbound_creates_new_opportunity_only(active,client,person,db):
    assert post(client,payload()).status_code==200
    lead=rows(db,Lead)[0];lead.status_funil='PERDIDO';lead.updated_at=utcnow()-timedelta(minutes=5);db.commit()
    assert post(client,payload('late-echo',direction='out',stamp=STAMP+60)).status_code==200
    assert len(rows(db,Lead))==1 and rows(db,Lead)[0].primeira_resposta_em is None
    assert post(client,payload('new-conversation',stamp=int((utcnow()-timedelta(minutes=1)).timestamp()))).status_code==200
    assert len(rows(db,Lead))==len(rows(db,WhatsAppIntake))==2 and len(rows(db,Person))==1


def test_callback_request_id_is_generated_not_sender_controlled(active,client,person,db):
    r=post(client,payload(),**{'X-Request-ID':SYNTH_PHONE_NORM})
    assert r.status_code==200 and r.headers['X-Request-ID']!=SYNTH_PHONE_NORM
    assert SYNTH_PHONE_NORM not in str(db.execute(text('SELECT request_id FROM audit_logs')).all())


def test_body_timeout_is_safe(monkeypatch):
    import asyncio
    from fastapi import HTTPException
    from app.routers import whatsapp as router
    original=asyncio.timeout
    monkeypatch.setattr(router.asyncio,'timeout',lambda _:original(0.001))
    class SlowRequest:
        headers={}
        async def stream(self):
            await asyncio.sleep(0.02)
            yield b'{}'
    with pytest.raises(HTTPException) as exc:
        asyncio.run(router.read_body(SlowRequest()))
    assert exc.value.status_code==408


def test_verify_empty_configuration_is_not_a_token(client,monkeypatch):
    monkeypatch.setenv('WHATSAPP_VERIFY_TOKEN','');get_settings.cache_clear()
    assert client.get(BASE+'/webhook',params={'hub.mode':'subscribe','hub.verify_token':'','hub.challenge':'123'}).status_code==403
    get_settings.cache_clear()


def test_existing_manual_first_response_never_overwritten(active,client,person,db):
    assert post(client,payload()).status_code==200
    lead=rows(db,Lead)[0];from datetime import datetime,timezone
    lead.primeira_resposta_em=datetime.fromtimestamp(STAMP+30,timezone.utc);db.commit()
    assert post(client,payload('echo-after-manual',direction='out',stamp=STAMP+60)).status_code==200
    assert aware(rows(db,Lead)[0].primeira_resposta_em).timestamp()==STAMP+30


def test_original_pending_timestamp_survives_messages(active,client,db):
    assert post(client,payload(stamp=STAMP+10)).status_code==200
    assert post(client,payload('earlier-inbound',stamp=STAMP)).status_code==200
    assert post(client,payload('later-inbound',stamp=STAMP+100)).status_code==200
    assert len(rows(db,WhatsAppIntake))==1 and aware(rows(db,WhatsAppIntake)[0].first_at).timestamp()==STAMP
    assert not rows(db,Person)
