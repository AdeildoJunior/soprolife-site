"""Central inline: synthetic identities, atomic ownership and legacy retries."""
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from app.models import Person, EegEnrollment, EegExam, FinancialEntry, EegReportDocument, FiscalDocument, Consent, EegCase
from app.services.idempotency import payload_fingerprint

API = '/api/v1/eeg'


def payload(**updates):
    return dict(nova_pessoa={'nome_completo': 'Pessoa Inline Sintetica',
                    'data_nascimento': '2000-01-02', 'consentimento_whatsapp': 'revogado'},
                modalidade_codigo='vigilia', agendado_para='2026-10-20T10:30:00-03:00',
                local_atendimento='Unidade Sintetica', municipio_atendimento_ibge='3304557',
                origem_canal='direto', valor='250.00', pagamento_status='Pendente',
                idempotency_key='eeg-inline-synthetic-01') | updates


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_inline_new_person_atomic_idempotent_no_clinical_or_fiscal(client, auth, db):
    data = payload()
    first = client.post(API+'/atendimentos', headers=auth(), json=data)
    assert first.status_code == 201, first.text
    again = client.post(API+'/atendimentos', headers=auth(), json=data)
    assert first.json()['id'] == again.json()['id']
    for model in (Person, EegEnrollment, EegExam, FinancialEntry):
        assert count(db, model) == 1
    assert db.scalar(select(EegEnrollment)).consentimento_whatsapp == 'revogado'
    assert db.scalar(select(FinancialEntry)).status == 'Pendente'
    for model in (EegReportDocument, FiscalDocument, Consent):
        assert count(db, model) == 0
    db.rollback()
    assert client.post(API+'/atendimentos', headers=auth(), json=data | {'valor':'251.00'}).status_code == 409


@pytest.mark.parametrize('change', [dict(municipio_atendimento_ibge='9999999'),
    dict(modalidade_codigo='video-eeg'), dict(origem_tipo='parceiro', partner_unit_id='nao-existe')])
def test_invalid_attendance_rolls_back_new_identity_contacts_enrollment(client, auth, db, change):
    result = client.post(API+'/atendimentos', headers=auth(), json=payload(**change))
    assert result.status_code in (404,422), result.text
    for model in (Person,EegEnrollment,EegExam,FinancialEntry):
        assert count(db, model) == 0


def test_financial_failure_rolls_back_everything(client, auth, db):
    from app.routers import eeg
    original = eeg.allocate_public_code
    def fail_finance(session, table):
        if table == 'financial_entries':
            raise RuntimeError('synthetic finance failure')
        return original(session, table)
    with patch.object(eeg, 'allocate_public_code', fail_finance):
        with pytest.raises(RuntimeError, match='synthetic finance'):
            client.post(API+'/atendimentos', headers=auth(), json=payload())
    for model in (Person,EegEnrollment,EegExam,FinancialEntry):
        assert count(db, model) == 0


def test_existing_corporate_person_preserves_uuid_pes_consent(client, auth, db):
    person = client.post('/api/v1/pessoas',headers=auth(),json={'nome_completo':'Pessoa Corporativa Inline Sintetica'}).json()
    data = payload();data.pop('nova_pessoa');data['person_id']=person['id']
    first = client.post(API+'/atendimentos',headers=auth(),json=data)
    assert first.status_code == 201, first.text
    assert first.json()['paciente']['id'] == person['id']
    assert first.json()['paciente']['public_code'] == person['public_code']
    assert count(db, Person) == 1
    enrollment = db.scalar(select(EegEnrollment)); enrollment_id=enrollment.id
    assert enrollment.consentimento_whatsapp == 'desconhecido'
    db.rollback()
    data.update(idempotency_key='eeg-inline-synthetic-02',consentimento_whatsapp='concedido')
    assert client.post(API+'/atendimentos',headers=auth(),json=data).status_code == 201
    assert db.get(EegEnrollment,enrollment_id).consentimento_whatsapp == 'concedido'
    assert count(db,EegEnrollment) == 1
    assert count(db,Consent) == 0
    db.rollback()
    data.pop('consentimento_whatsapp'); data['idempotency_key']='eeg-inline-synthetic-03'
    assert client.post(API+'/atendimentos',headers=auth(),json=data).status_code == 201
    assert db.get(EegEnrollment,enrollment_id).consentimento_whatsapp == 'concedido'


def test_duplicate_cpf_never_creates_second_identity(client,auth,db):
    data=payload(); data['nova_pessoa']['cpf']='52998224725'
    assert client.post(API+'/atendimentos',headers=auth(),json=data).status_code == 201
    data['idempotency_key']='eeg-inline-synthetic-02';data['nova_pessoa']['confirmar_nova_pessoa']=True
    assert client.post(API+'/atendimentos',headers=auth(),json=data).status_code == 409
    assert count(db,Person)==count(db,EegExam)==count(db,FinancialEntry)==1


def test_similar_identity_requires_human_choice(client,auth,db):
    person=client.post('/api/v1/pessoas',headers=auth(),json={'nome_completo':'Pessoa Inline Sintetica','data_nascimento':'2000-01-02'}).json()
    data=payload()
    assert client.post(API+'/atendimentos',headers=auth(),json=data).status_code==409
    assert count(db,EegExam)==count(db,EegEnrollment)==0
    db.rollback()
    data['nova_pessoa']['confirmar_nova_pessoa']=True
    assert client.post(API+'/atendimentos',headers=auth(),json=data).status_code==201
    assert count(db,Person)==2


@pytest.mark.parametrize('identity', [dict(nova_pessoa=None),dict(person_id='other'),dict(enrollment_id='other')])
def test_exactly_one_identity_source(client,auth,identity):
    assert client.post(API+'/atendimentos',headers=auth(),json=payload(**identity)).status_code==422


@pytest.mark.parametrize('status', ['agendado','confirmado','realizado'])
def test_initial_status_does_not_invent_report(client,auth,db,status):
    r=client.post(API+'/atendimentos',headers=auth(),json=payload(status_inicial=status))
    assert r.status_code==201,r.text
    exam=db.scalar(select(EegExam));assert exam.status==status
    assert bool(exam.realizado_em)==(status=='realizado')
    assert count(db,EegReportDocument)==0


def test_retest_inline_without_revenue(client,auth,db):
    r=client.post(API+'/atendimentos',headers=auth(),json=payload(valor='0',reteste_sem_cobranca=True))
    assert r.status_code==201,r.text
    assert count(db,FinancialEntry)==0
    assert db.scalar(select(EegCase)).reteste_sem_cobranca


def test_legacy_payload_fingerprint_remains_replayable(client,auth,db):
    from app.schemas import EegAtendimentoCreate
    data=payload();person=client.post(API+'/pessoas',headers=auth(),json=data.pop('nova_pessoa')).json()
    data['enrollment_id']=person['id']
    first=client.post(API+'/atendimentos',headers=auth(),json=data)
    assert first.status_code==201,first.text
    identity=EegAtendimentoCreate(**data).model_dump(mode='json',exclude={
        'idempotency_key','person_id','nova_pessoa','status_inicial','consentimento_whatsapp'})
    assert db.scalar(select(EegExam)).idempotency_fingerprint==payload_fingerprint(identity)
    db.rollback()
    assert client.post(API+'/atendimentos',headers=auth(),json=data).json()['id']==first.json()['id']
