"""Synthetic read projections, search isolation, privacy and actual queue targets."""
from datetime import datetime,timedelta,timezone
import json
import pytest
from sqlalchemy import func,select
from app.config import get_settings
from app.models import (EegEnrollment,EegExam,FinancialEntry,FiscalDocument,Followup,Lead,Partner,PartnerUnit,Person,PersonContact,PhysicianProfile,ReportAssignment,ReportDocument,SpirometryExam,User)
from app.normalize import normalize_name
from app.security import get_role,hash_password,issue_token
from app.services import command_center as c,command_search as s

@pytest.fixture(autouse=True)
def config(monkeypatch):
    s.limiter.calls.clear()
    for key,value in [('M15_REPORTS_ENABLED','true'),('M15_REPORTS_MODE','pilot'),('M15_NFSE_ENABLED','true')]:monkeypatch.setenv(key,value)
    get_settings.cache_clear();yield;get_settings.cache_clear();s.limiter.calls.clear()

def person(db,name='Pessoa Sintética',code='PES-900001',**kw):
    p=Person(nome_completo=name,nome_normalizado=normalize_name(name),public_code=code,**kw);db.add(p);db.flush();return p

def exam(db,p,code='ESP-900001',**kw):
    e=SpirometryExam(person_id=p.id,public_code=code,**kw);db.add(e);db.flush();return e

def physician(db,users,name='medico'):
    u=User(email=name+'@synthetic.local',nome='Conta Sintética',password_hash=hash_password('synthetic-password'),roles=[get_role(db,'medico')]);db.add(u);db.flush()
    p=PhysicianProfile(user_id=u.id,professional_name='Profissional Sintético',crm_number='999999' if name=='medico' else '999998',crm_state='RJ',active=True,verification_status='verified',verified_at=datetime.now(timezone.utc),verified_by_user_id=users['admin'].id,verification_reference='SYNTHETIC-ONLY');db.add(p);db.flush();return u,p

def document(db,e,users,profile,code='LAU-900001',**kw):
    d=ReportDocument(spirometry_exam_id=e.id,public_code=code,created_by_user_id=users['admin'].id,**kw);db.add(d);db.flush()
    db.add(ReportAssignment(report_document_id=d.id,physician_profile_id=profile.id,assigned_by_user_id=users['admin'].id,reason_code='initial_assignment'));db.flush();return d

def find(client,auth,q,role='admin',brand='soprolife',limit=12):
    return client.post('/api/v1/comando/busca',json={'termo':q,'marca':brand,'limite':limit},headers=auth(role))

def test_auth_and_brand(client,auth):
    assert client.get('/api/v1/comando/resumo').status_code==401
    assert client.post('/api/v1/comando/busca',json={'termo':'Pessoa'}).status_code==401
    assert client.get('/api/v1/comando/resumo?marca=eeglife',headers=auth('operacional')).status_code==403
    assert client.get('/api/v1/comando/resumo?marca=other',headers=auth()).status_code==422

def test_empty_home_confirmed_zero_readonly(client,auth,db):
    before=db.scalar(select(func.count()).select_from(FinancialEntry))
    r=client.get('/api/v1/comando/resumo',headers=auth());assert r.status_code==200,r.text
    d=r.json();assert d['estado']=='ready' and d['hoje']['exames']==d['mes']['exames']==d['pendencias']['total']==0
    assert d['financeiro']['bruto']==d['financeiro']['despesas']=='0.00'
    assert r.headers['cache-control']=='private, no-store'
    assert db.scalar(select(func.count()).select_from(FinancialEntry))==before

@pytest.mark.parametrize('role',['operacional','leitura'])
def test_no_financial_or_fiscal_data(client,auth,db,users,role):
    p=person(db);e=exam(db,p,status='Realizado',data_exame=c.today(),data_exame_precisao='dia')
    db.add(FiscalDocument(spirometry_exam_id=e.id,environment='production',kind='single',state='pending',created_by=users['admin'].id,idempotency_key='synthetic-fiscal',idempotency_fingerprint='a'*64));db.commit()
    d=client.get('/api/v1/comando/resumo',headers=auth(role)).json()
    assert 'financeiro' not in d and not d['capacidades']['fiscal'] and 'fiscal' not in d['pendencias']['categorias']
    r=find(client,auth,e.public_code,role).json()
    assert all(g['tipo']!='fiscal' for g in r['grupos'])
    assert not any(k in json.dumps(r) for k in ('valor','amount','cpf','financial'))

def test_counts_precision_and_targets(client,auth,db):
    p=person(db);when=c.today();e=exam(db,p,status='Realizado',data_exame=when,data_exame_precisao='dia')
    exam(db,p,'ESP-900002',status='Aguardando',data_exame=when+timedelta(days=1),data_exame_precisao='dia')
    exam(db,p,'ESP-900003',status='Realizado',data_exame=when,data_exame_precisao='mes',data_exame_dia_assumido=True)
    db.add(Lead(person_id=p.id,public_code='LEA-900001',primeiro_contato_em=datetime.now(timezone.utc),status_funil='NOVO_CONTATO'))
    db.add(Followup(person_id=p.id,public_code='FUP-900001',tipo='manual',due_date=when-timedelta(days=1)));db.commit()
    r=client.get('/api/v1/comando/resumo',headers=auth()).json()
    assert r['hoje']['exames']==1 and r['mes']['exames']==2 and r['mes']['contatos']==1
    assert r['pendencias']['categorias']['sem_laudo']==2
    full=client.get('/api/v1/comando/pendencias?categoria=sem_laudo',headers=auth()).json()
    assert full['total']==2 and e.id in {i['destino']['id'] for i in full['itens']}
    assert all(i['destino']['tipo']=='exame' for i in full['itens'])
    assert r['financeiro']['bruto'] is None and r['financeiro']['resultado'] is None

def test_noncontact_partner_control(client,auth,db):
    p=person(db,nao_contatar=True);db.add(Followup(person_id=p.id,public_code='FUP-900001',tipo='manual',due_date=c.today()))
    db.add(Lead(person_id=p.id,public_code='LEA-900001',primeiro_contato_em=datetime.now(timezone.utc),status_funil='NOVO_CONTATO'))
    p2=person(db,code='PES-900002');db.add(Followup(person_id=p2.id,public_code='FUP-900002',tipo='manual',due_date=c.today(),controlado_por_parceiro=True));db.commit()
    assert not client.get('/api/v1/comando/resumo',headers=auth()).json()['pendencias']['itens']

def test_search_types_limit_and_wildcards(client,auth,db):
    p=person(db);e=exam(db,p);partner=Partner(public_code='CLI-900001',nome='Maclin Sintética');db.add(partner);db.flush()
    db.add(PartnerUnit(public_code='UNI-900001',partner_id=partner.id,nome='Maclin Norte Sintética'))
    for i in range(8):person(db,'Pessoa Sintética '+str(i),f'PES-91000{i}')
    db.commit()
    assert find(client,auth,e.public_code).json()['grupos'][0]['itens'][0]['destino']['id']==e.id
    assert any(g['tipo']=='pessoas' for g in find(client,auth,'Sintética').json()['grupos'])
    assert {g['tipo'] for g in find(client,auth,'Maclin').json()['grupos']}=={'parceiros','unidades'}
    assert find(client,auth,'Pessoa',limit=2).json()['total']==2
    for q in ['a','   ','123','%__','* *']:
        r=find(client,auth,q);assert r.status_code==422 or r.json()['total']==0

def test_exact_phone_cpf_masking_logs(client,auth,db,caplog):
    p=person(db,cpf='52998224725');db.add(PersonContact(person_id=p.id,tipo='telefone',valor='(21) 0000-9001',valor_normalizado='552100009001'));db.commit()
    r=find(client,auth,'(21) 0000-9001');assert r.status_code==200 and r.json()['total']
    assert '(21) 0000-9001' not in r.text and '552100009001' not in r.text
    assert find(client,auth,'52998224725','operacional').json()['total']==0
    cpf=find(client,auth,'52998224725');assert cpf.json()['total'] and '52998224725' not in cpf.text
    assert '52998224725' not in caplog.text and '0000-9001' not in caplog.text

def test_physician_assignment_isolation(client,db,users):
    u,profile=physician(db,users);_,other_profile=physician(db,users,'other-medico')
    p=person(db);e=exam(db,p);d=document(db,e,users,profile)
    p2=person(db,'Pessoa Restrita','PES-900002');e2=exam(db,p2,'ESP-900002');document(db,e2,users,other_profile,'LAU-900002')
    db.commit();headers={'Authorization':'Bearer '+issue_token(u.id,u.password_hash)}
    assert client.get('/api/v1/comando/resumo',headers=headers).status_code==403
    for term in [p.public_code,e.public_code,d.public_code,'Sintética']:
        r=client.post('/api/v1/comando/busca',json={'termo':term},headers=headers);assert r.status_code==200,r.text
        rows=[i for g in r.json()['grupos'] for i in g['itens']]
        assert rows and all(i['destino']['tipo']=='laudo' and i['destino']['id']==d.id for i in rows)
    for term in [p2.public_code,e2.public_code,'Maclin']:
        assert client.post('/api/v1/comando/busca',json={'termo':term},headers=headers).json()['total']==0
    assert client.post('/api/v1/comando/busca',json={'marca':'eeglife','termo':e.public_code},headers=headers).status_code==403

def test_limiter_no_terms(client,auth):
    for _ in range(30):assert find(client,auth,'Sintética').status_code==200
    assert find(client,auth,'Sintética').status_code==429 and 'Sintética' not in repr(s.limiter.calls)

def test_eeg_isolation(client,auth,db):
    p=person(db);exam(db,p,status='Realizado',data_exame=c.today(),data_exame_precisao='dia')
    en=EegEnrollment(person_id=p.id);db.add(en);db.flush()
    db.add(EegExam(public_code='EEG-900001',enrollment_id=en.id,modalidade_codigo='eeg_rotina',status='agendado',agendado_para=datetime.now(timezone.utc)));db.commit()
    r=client.get('/api/v1/comando/resumo?marca=eeglife',headers=auth());assert r.status_code==200,r.text
    d=r.json();assert d['hoje']['exames']==1 and d['mes']['exames']==0
    assert all(i['destino']['tipo'].startswith('eeg') for i in d['pendencias']['itens'])
    assert find(client,auth,'ESP-900001',brand='eeglife').json()['total']==0

def test_canonical_finance_no_double_deduction(monkeypatch,client,auth):
    source={'exames':[{'receita':'70.00','bruto':'100.00','custos':{'parceiro':'0.00'}}],'kpis':{'lucro':'45.00','incompletos':0,'exames':1,'total_exames':1}}
    monkeypatch.setattr(c.profit_projection,'build_projection',lambda *_:source)
    d=client.get('/api/v1/comando/resumo',headers=auth()).json()['financeiro']
    assert (d['bruto'],d['soprolife'],d['parceiros'],d['resultado'])==('100.00','70.00','30.00','45.00')
    source['exames'][0]['bruto']=None;d=client.get('/api/v1/comando/resumo',headers=auth()).json()['financeiro']
    assert d['bruto'] is None and d['parceiros'] is None and d['soprolife']=='70.00'


def test_direct_record_scopes_and_today_list(client,auth,db,users):
    p=person(db);e=exam(db,p,status='Realizado',data_exame=c.today(),data_exame_precisao='dia')
    en=EegEnrollment(person_id=p.id);db.add(en);db.flush()
    f=FiscalDocument(spirometry_exam_id=e.id,environment='production',kind='single',state='blocked',created_by=users['admin'].id,idempotency_key='synthetic-direct',idempotency_fingerprint='b'*64);db.add(f);db.commit()
    def lookup(kind,id,role='admin',brand='soprolife'):
        return client.post('/api/v1/comando/registro',json={'marca':brand,'tipo':kind,'id':id},headers=auth(role))
    assert lookup('fiscal',f.id).status_code==200
    for role in ('leitura','operacional'):
        assert lookup('fiscal',f.id,role).status_code==404
        assert lookup('pessoa',p.id,role).status_code==200
    assert lookup('exame',e.id,brand='eeglife').status_code==404
    assert lookup('eeg_pessoa',en.id,brand='eeglife').status_code==200
    assert find(client,auth,p.public_code,brand='eeglife').json()['grupos'][0]['itens'][0]['destino']['tipo']=='eeg_pessoa'
    today=client.get('/api/v1/comando/pendencias?categoria=hoje',headers=auth()).json()
    assert today['total']==1 and today['itens'][0]['destino']['id']==e.id


def test_partial_finance_explicit_coverage(client,auth,monkeypatch):
    source={'exames':[{'receita':'86.42','bruto':'123.45','custos':{'parceiro':'0.00'}},{'receita':None,'bruto':None,'custos':{'parceiro':None}}],
        'kpis':{'lucro':'61.42','incompletos':1,'exames':1,'total_exames':2}}
    monkeypatch.setattr(c.profit_projection,'build_projection',lambda *_:source)
    d=client.get('/api/v1/comando/resumo',headers=auth()).json()['financeiro']
    assert d['bruto']=='123.45' and d['soprolife']=='86.42' and d['parceiros']=='37.03'
    assert d['parcial']['bruto'] and d['parcial']['resultado']
    assert d['cobertura_valores']['bruto']=={'com_dado':1,'total':2}


def test_operational_windows_share_pending_source(client,auth,db,users,monkeypatch):
    when=c.today();p=person(db)
    for n,days in enumerate((-1,0,1,7,8)):
        db.add(Followup(person_id=person(db,code=f'PES-94000{n}').id,public_code=f'FUP-92000{n}',tipo='manual',due_date=when+timedelta(days=days)))
    db.add(Followup(person_id=p.id,public_code='FUP-920009',tipo='manual',due_date=None))
    for n,days in enumerate((0,1,7,8)):
        exam(db,p,f'ESP-92000{n}',status='Agendado',data_exame=when+timedelta(days=days),data_exame_precisao='dia')
    exam(db,p,'ESP-920008',status='Aguardando',data_exame=when,data_exame_precisao='mes',data_exame_dia_assumido=True)
    exam(db,p,'ESP-920009',status='Aguardando')
    for n,days in enumerate((None,-1,0,1,8)):
        db.add(Lead(person_id=p.id,public_code=f'LEA-92000{n}',primeiro_contato_em=datetime.now(timezone.utc),status_funil='AGUARDANDO_RETORNO',data_retomada_manual=when+timedelta(days=days) if days is not None else None))
    db.commit()
    def unexpected(*args,**kwargs):raise AssertionError('BI must not run on operational home')
    monkeypatch.setattr(c,'_finance',unexpected);monkeypatch.setattr(c.funnel,'metrics',unexpected)
    d=client.get('/api/v1/comando/resumo?operacional=true',headers=auth()).json()
    p=d['pendencias'];full=client.get('/api/v1/comando/pendencias',headers=auth()).json()
    assert p['total']==full['total']==10  # 2 due follow-ups + 3 agenda + 5 open contacts.
    assert p['categorias']==full['categorias']
    assert d['mes'] is None and 'financeiro' not in d and d['hoje']['exames']==1
    assert p['grupos']=={'hoje':{'agenda':1,'contato':1,'followup':1},
                         'atencao':{'contato':2,'followup':1},
                         'proximos':{'agenda':2,'contato':1,'followup':2}}
    for window,counts in p['grupos'].items():
        for category,total in counts.items():
            r=client.get(f'/api/v1/comando/pendencias?janela={window}&categoria={category}&limite=1',headers=auth()).json()
            assert r['total']==total and len(r['itens'])==1
            assert r['tem_proxima']==(total>1)
    assert client.get('/api/v1/comando/pendencias?janela=invalid',headers=auth()).status_code==422
    doctor,_=physician(db,users);db.commit()
    headers={'Authorization':'Bearer '+issue_token(doctor.id,doctor.password_hash)}
    assert client.get('/api/v1/comando/pendencias?janela=proximos',headers=headers).status_code==403


def test_upcoming_followups_respect_existing_contact_guards(client,auth,db):
    p=person(db,nao_contatar=True);p2=person(db,code='PES-920002')
    for n,who,controlled in ((1,p,False),(2,p2,True)):
        db.add(Followup(person_id=who.id,public_code=f'FUP-93000{n}',tipo='manual',due_date=c.today()+timedelta(days=1),controlado_por_parceiro=controlled))
    # The patient's do-not-contact state still applies with a distinct guardian.
    from app.models import PersonRelationship
    guardian=person(db,code='PES-920003')
    db.add(PersonRelationship(minor_person_id=p.id,guardian_person_id=guardian.id,relationship_type='mother',is_legal_guardian=True,active=True))
    db.add(Followup(person_id=p.id,contact_person_id=guardian.id,public_code='FUP-930003',tipo='pos_exame',due_date=c.today()+timedelta(days=1)))
    db.commit()
    d=client.get('/api/v1/comando/resumo?operacional=true',headers=auth()).json()
    assert not d['pendencias']['grupos']['proximos'] and d['pendencias']['total']==0
