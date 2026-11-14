"""Synthetic patient funnel contract and regression tests. No real identities."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import json
import pytest
from sqlalchemy import select, func
from app.models import Lead, Person, SpirometryExam, FunnelEvent, utcnow
from app.services.funnel import metrics

BASE='/api/v1/funil'

def contact(client,auth,person=None,**kw):
    payload={'idempotency_key':str(uuid4()),'origem_aquisicao':'google_maps',
        'canal_entrada':'whatsapp','unidade_pretendida':'barra',**kw}
    payload.update({'person_id':person['id']} if person else {'pessoa':{'nome_completo':'Pessoa Sintética '+uuid4().hex[:8]}})
    r=client.post(BASE+'/contatos',json=payload,headers=auth('operacional'))
    assert r.status_code==201,r.text
    return r.json()

def transition(client,auth,lead,event,**kw):
    r=client.post(BASE+'/contatos/'+lead['id']+'/eventos',json={'evento':event,**kw},headers=auth('operacional'))
    assert r.status_code==200,r.text
    return r.json()

def exam_for(db,lead):
    db.expire_all()
    exam=db.scalar(select(SpirometryExam).where(SpirometryExam.lead_id==lead['id'])); db.commit(); return exam


def test_contact_existing_new_and_idempotent(client,auth,person,db):
    before=db.scalar(select(func.count()).select_from(Person)); db.commit()
    lead=contact(client,auth,person)
    assert lead['person_id']==person['id'] and lead['primeiro_contato_em']
    assert db.scalar(select(func.count()).select_from(Person))==before; db.commit()
    assert lead['origem_aquisicao']=='google_maps' and lead['canal_entrada']=='whatsapp'
    payload={'pessoa':{'nome_completo':'Pessoa Sintética Idempotente'},'idempotency_key':'contact-replay'}
    a=client.post(BASE+'/contatos',json=payload,headers=auth())
    b=client.post(BASE+'/contatos',json=payload,headers=auth())
    assert a.status_code==b.status_code==201,(a.text,b.text)
    assert a.json()['id']==b.json()['id'] and b.json()['idempotente']
    assert db.scalar(select(func.count()).select_from(Person))==before+1


def test_response_idempotent_and_immutable(client,auth,person,db):
    lead=contact(client,auth,person)
    a=transition(client,auth,lead,'PRIMEIRA_RESPOSTA')
    b=transition(client,auth,lead,'PRIMEIRA_RESPOSTA')
    assert a['primeira_resposta_em']==b['primeira_resposta_em']
    assert db.scalar(select(func.count()).select_from(FunnelEvent).where(FunnelEvent.event_type=='PRIMEIRA_RESPOSTA'))==1
    e=db.scalar(select(FunnelEvent))
    e.event_type='PERDIDO'
    with pytest.raises(ValueError,match='append-only'): db.flush()
    db.rollback()


def test_schedule_reschedule_cancel_and_realize(client,auth,person,db):
    lead=contact(client,auth,person)
    day=utcnow().date()+timedelta(days=2)
    a=transition(client,auth,lead,'AGENDADO',data_prevista=day.isoformat())
    exam=exam_for(db,lead); exam_id=exam.id
    transition(client,auth,lead,'REMARCADO',data_prevista=(day+timedelta(days=1)).isoformat())
    assert exam_for(db,lead).id==exam_id
    e=db.scalar(select(FunnelEvent).where(FunnelEvent.event_type=='REMARCADO'))
    assert e.previous_date==day; db.commit()
    transition(client,auth,lead,'CANCELADO',motivo='horario')
    assert exam_for(db,lead).status=='Cancelado'
    transition(client,auth,lead,'AGENDADO',data_prevista=(day+timedelta(days=2)).isoformat())
    r=client.patch('/api/v1/espirometrias/'+exam_id,json={'status':'Realizado'},headers=auth())
    assert r.status_code==200,r.text
    db.expire_all(); l=db.get(Lead,lead['id'])
    assert l.status_funil=='EXAME_REALIZADO' and l.realizado_em and l.agendado_em==datetime.fromisoformat(a['agendado_em']).replace(tzinfo=None)


def test_untracked_history_and_multiple_contacts(client,auth,person,db):
    r=client.post('/api/v1/espirometrias',json={'person_id':person['id'],'status':'Realizado','data_exame':'2026-10-20'},headers=auth())
    assert r.status_code==201,r.text
    assert r.json()['lead_id'] is None
    a=contact(client,auth,person); b=contact(client,auth,person)
    payload={'person_id':person['id'],'data_exame':utcnow().date().isoformat()}
    r=client.post('/api/v1/espirometrias',json=payload,headers=auth())
    assert r.status_code==201 and r.json()['lead_id'] is None
    r=client.post('/api/v1/espirometrias',json={**payload,'lead_id':a['id']},headers=auth())
    assert r.status_code==201 and r.json()['lead_id']==a['id'],r.text
    r=client.post('/api/v1/espirometrias',json={**payload,'continuar_sem_rastreamento':True},headers=auth())
    assert r.status_code==201 and r.json()['lead_id'] is None
    # Existing historical exam is never attributed to a later contact.
    old=db.scalar(select(SpirometryExam).where(SpirometryExam.data_exame==datetime(2026,10,20).date())); db.commit()
    r=client.post(BASE+'/contatos/'+b['id']+'/vincular-exame',json={'exam_id':old.id},headers=auth())
    assert r.status_code==422


def test_synthetic_cohort_and_coverage(client,auth,db):
    leads=[contact(client,auth) for _ in range(10)]
    day=utcnow().date()
    for l in leads[:8]: transition(client,auth,l,'PRIMEIRA_RESPOSTA')
    for l in leads[:6]: transition(client,auth,l,'AGENDADO',data_prevista=day.isoformat())
    transition(client,auth,leads[0],'REMARCADO',data_prevista=(day+timedelta(days=1)).isoformat())
    transition(client,auth,leads[5],'CANCELADO')
    for l in leads[:5]:
        ex=exam_for(db,l)
        r=client.patch('/api/v1/espirometrias/'+ex.id,json={'status':'Realizado'},headers=auth())
        assert r.status_code==200,r.text
    # Retest is another exam on same contact, not another arrival/conversion.
    r=client.post('/api/v1/espirometrias',json={'person_id':leads[0]['person_id'],'lead_id':leads[0]['id'],'status':'Realizado','data_exame':day.isoformat()},headers=auth())
    assert r.status_code==201,r.text
    r=client.post('/api/v1/espirometrias',json={'person_id':leads[9]['person_id'],'status':'Realizado','data_exame':day.isoformat(),'continuar_sem_rastreamento':True},headers=auth())
    assert r.status_code==201
    r=client.get(BASE+'/metricas',params={'inicio':(day-timedelta(days=7)).isoformat(),'fim':(day+timedelta(days=7)).isoformat()},headers=auth('leitura'))
    assert r.status_code==200,r.text
    d=r.json()
    assert [d[k] for k in ('contatos','respondidos','agendados','realizados','cancelados')]==[10,8,6,5,1]
    assert d['conversoes']=={'contato_resposta':80,'contato_agendamento':60,'agendamento_exame':83.33,'contato_exame':50}
    assert d['semanas'][-1]['parcial']
    assert d['tempos']['contato_resposta']['disponiveis']==8
    assert d['cobertura']['exames_rastreados']['percentual']<100
    assert 'Pessoa Sintética' not in r.text and 'person_id' not in r.text and 'lead_id' not in r.text
    assert db.scalar(select(func.count()).select_from(Lead))==10
    filtered=client.get(BASE+'/metricas',params={'inicio':day.isoformat(),'fim':day.isoformat(),'unidade':'zona_norte'},headers=auth()).json()
    assert filtered['contatos']==0 and filtered['conversoes']['contato_exame'] is None


def test_validation_auth_and_person_mismatch(client,auth,person):
    assert client.get(BASE+'/config').status_code==401
    payload={'person_id':person['id'],'idempotency_key':'four','origem_aquisicao':'whatsapp'}
    assert client.post(BASE+'/contatos',json=payload,headers=auth()).status_code==422
    assert client.post(BASE+'/contatos',json={**payload,'origem_aquisicao':'site'},headers=auth('leitura')).status_code==403
    l=contact(client,auth)
    r=client.post('/api/v1/espirometrias',json={'person_id':person['id'],'lead_id':l['id']},headers=auth())
    assert r.status_code==422


def test_attendance_completes_existing_reservation(client,auth,person,db):
    lead=contact(client,auth,person)
    day=utcnow().date().isoformat()
    transition(client,auth,lead,'AGENDADO',data_prevista=day)
    reserved_id=exam_for(db,lead).id
    r=client.post('/api/v1/atendimentos',json={'person_id':person['id'],'tipo':'espirometria_soprolife',
        'idempotency_key':'attendance-from-reservation','espirometria':{'data_exame':day,'status':'Realizado',
        'modalidade':'cowork','local_atendimento':'Local sintético','broncodilatador':True}},headers=auth())
    assert r.status_code==201,r.text
    assert r.json()['espirometria']['id']==reserved_id
    assert r.json()['espirometria']['lead_id']==lead['id']
    assert r.json()['espirometria']['broncodilatador'] is True
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(SpirometryExam))==1
    assert db.get(Lead,lead['id']).status_funil=='EXAME_REALIZADO'


@pytest.mark.parametrize('new_person', [False, True])
@pytest.mark.parametrize('pastore', [False, True])
def test_attendance_without_lead_keeps_production_outside_prospective_metrics(
    client, auth, person, db, new_person, pastore,
):
    from app.models import Partner, PartnerUnit, FinancialEntry, FiscalIssuanceRequest
    day = utcnow().date().isoformat()
    exam = {'data_exame': day, 'status': 'Realizado', 'broncodilatador': True,
            'municipio_atendimento_ibge': '3304557'}
    if pastore:
        partner = Partner(public_code='CLI-SYNTHETIC', nome='Pastore', tipo='clinica')
        db.add(partner); db.flush()
        unit = PartnerUnit(public_code='UNI-SYNTHETIC', partner_id=partner.id,
                           nome='Pastore Ipanema', ativo=True)
        db.add(unit); db.commit()
        exam.update(partner_id=partner.id, partner_unit_id=unit.id)
    payload = {'tipo': 'espirometria_pastore' if pastore else 'espirometria_soprolife',
               'espirometria': exam}
    if new_person:
        payload['pessoa'] = {'nome_completo': 'Pessoa Sintética Atendimento Sem Contato'}
    else:
        payload['person_id'] = person['id']
    route = '/atendimentos/novo-paciente' if new_person else '/atendimentos'
    result = client.post('/api/v1'+route, json=payload, headers=auth())
    assert result.status_code == 201, result.text
    saved = result.json()['espirometria']
    assert saved['lead_id'] is None and saved['broncodilatador'] is True
    if pastore:
        assert saved['partner_id'] == partner.id and saved['partner_unit_id'] == unit.id
        assert saved['modalidade'] == 'clinica_parceira'
        assert saved['origem'] == 'Pastore' and saved['local_atendimento'] == 'Pastore Ipanema'
        production = client.get('/api/v1/pastore/fechamentos', headers=auth()).json()
        assert any(row['unidade']['id'] == unit.id for row in production['fechamentos'])
    for model in (Lead, FunnelEvent, FinancialEntry, FiscalIssuanceRequest):
        assert db.scalar(select(func.count()).select_from(model)) == 0
    db.commit()
    data = client.get(BASE+'/metricas', params={'inicio':day,'fim':day}, headers=auth()).json()
    assert data['contatos'] == data['respondidos'] == data['agendados'] == 0
    assert all(value is None for value in data['conversoes'].values())
    assert all(row['disponiveis'] == 0 for row in data['tempos'].values())
    assert data['cobertura']['exames_rastreados'] == {'com_dado':0,'total':1,'percentual':0}


def test_automatic_contact_never_attributes_earlier_exam(client, auth, person, db):
    lead = contact(client, auth, person)
    yesterday = (utcnow().date()-timedelta(days=2)).isoformat()
    result = client.post('/api/v1/atendimentos', headers=auth(), json={
        'person_id':person['id'], 'tipo':'espirometria_soprolife',
        'espirometria':{'data_exame':yesterday,'status':'Realizado'},
    })
    assert result.status_code == 201, result.text
    assert result.json()['espirometria']['lead_id'] is None
    db.expire_all()
    stored = db.get(Lead, lead['id'])
    assert stored.primeira_resposta_em is stored.agendado_em is stored.realizado_em is None


def test_deferred_link_preserves_real_contact_without_fabricating_previous_steps(client, auth, person):
    lead = contact(client, auth, person)
    exam = client.post('/api/v1/espirometrias', headers=auth(), json={
        'person_id':person['id'], 'status':'Realizado',
        'data_exame':utcnow().date().isoformat(), 'continuar_sem_rastreamento':True,
    }).json()
    assert exam['lead_id'] is None
    result = client.post(BASE+'/contatos/'+lead['id']+'/vincular-exame',
                         headers=auth(), json={'exam_id':exam['id']})
    assert result.status_code == 200, result.text
    updated = client.get(BASE+'/contatos', headers=auth()).json()['itens'][0]
    assert updated['primeiro_contato_em'] == lead['primeiro_contato_em']
    assert not updated.get('primeira_resposta_em') and not updated.get('agendado_em')
    assert updated['realizado_em'] and updated['exam_ids'] == [exam['id']]


def test_invalid_new_contact_rolls_back_person_and_mass_assignment(client,auth,db):
    before=db.scalar(select(func.count()).select_from(Person));db.commit()
    payload={'pessoa':{'nome_completo':'Pessoa Sintética Rollback'},'idempotency_key':'invalid-contact',
        'origem_aquisicao':'phone-is-not-source'}
    assert client.post(BASE+'/contatos',json=payload,headers=auth()).status_code==422
    assert db.scalar(select(func.count()).select_from(Person))==before;db.commit()
    payload['origem_aquisicao']='site';payload['status_funil']='EXAME_REALIZADO'
    assert client.post(BASE+'/contatos',json=payload,headers=auth()).status_code==422


def test_origins_channels_filters_and_non_converters(client,auth,db):
    lead=contact(client,auth,origem_aquisicao='nao_informado',canal_entrada='telefone',unidade_pretendida='domiciliar')
    transition(client,auth,lead,'AGUARDANDO_RETORNO')
    day=utcnow().date().isoformat()
    res=client.get(BASE+'/metricas',params={'inicio':day,'fim':day,'origem':'nao_informado','unidade':'domiciliar','canal':'telefone'},headers=auth()).json()
    assert res['contatos']==1 and res['aguardando_retorno']==1 and res['perdidos']==res['cancelados']==0
    assert res['cobertura']['origem_registrada']['percentual']==0
    # Zero is different from an unknown denominator.
    assert res['conversoes']['contato_exame']==0
    res=client.get(BASE+'/metricas',params={'inicio':day,'fim':day,'origem':'google_maps'},headers=auth()).json()
    assert res['contatos']==0 and res['conversoes']['contato_exame'] is None


def test_cohorts_by_first_contact_not_production_date_and_brazil_week(client,auth,db):
    from app.models import Person
    from app.ids import allocate_public_code
    p=Person(public_code=allocate_public_code(db,'people'),nome_completo='Pessoa Sintética Coorte',nome_normalizado='pessoa sintetica coorte')
    db.add(p);db.flush()
    l=Lead(public_code=allocate_public_code(db,'leads'),person_id=p.id,
        primeiro_contato_em=datetime(2026,11,2,2,0,tzinfo=timezone.utc),
        agendado_em=datetime(2026,11,3,tzinfo=timezone.utc),
        realizado_em=datetime(2026,11,10,tzinfo=timezone.utc),status_funil='EXAME_REALIZADO',origem_aquisicao='site',canal_entrada='whatsapp',unidade_pretendida='barra')
    db.add(l);db.commit()
    # 02/11 02:00 UTC is Sunday 01/11 in Brazil: previous week's arrival.
    a=metrics(db,datetime(2026,10,26).date(),datetime(2026,11,1).date(),now=datetime(2026,11,13,tzinfo=timezone.utc))
    b=metrics(db,datetime(2026,11,9).date(),datetime(2026,11,15).date(),now=datetime(2026,11,13,tzinfo=timezone.utc))
    assert a['contatos']==a['realizados']==1 and not a['semanas'][0]['parcial']
    assert b['contatos']==b['realizados']==0 and b['semanas'][0]['parcial']


def test_duplicate_candidate_does_not_create_second_patient(client,auth,person,db):
    r=client.post(BASE+'/contatos',json={'pessoa':{'nome_completo':person['nome_completo']},'idempotency_key':'candidate-contact'},headers=auth())
    assert r.status_code==409,r.text
    assert db.scalar(select(func.count()).select_from(Person))==1


def test_explicit_wait_loss_no_show_and_dimension_correction(client,auth,person):
    lead=contact(client,auth,person)
    r=client.post(BASE+'/contatos/'+lead['id']+'/eventos',json={'evento':'NAO_COMPARECEU'},headers=auth())
    assert r.status_code==422  # Date passing alone never creates a no-show.
    day=utcnow().date().isoformat()
    transition(client,auth,lead,'AGENDADO',data_prevista=day)
    assert transition(client,auth,lead,'NAO_COMPARECEU')['status_funil']=='NAO_COMPARECEU'
    assert transition(client,auth,lead,'AGENDADO',data_prevista=day)['status_funil']=='AGENDADO'
    r=client.patch(BASE+'/contatos/'+lead['id'],json={'origem_aquisicao':'site','canal_entrada':'telefone'},headers=auth())
    assert r.status_code==200,r.text
    assert r.json()['origem_aquisicao']=='site' and r.json()['unidade_pretendida']=='barra'
    events=client.get(BASE+'/contatos/'+lead['id']+'/eventos',headers=auth()).json()['itens']
    assert events[0]['dimensoes']['origem_aquisicao']=='google_maps'
    assert events[-1]['dimensoes']['origem_aquisicao']=='site'
    assert transition(client,auth,lead,'PERDIDO',motivo='outro')['status_funil']=='PERDIDO'
