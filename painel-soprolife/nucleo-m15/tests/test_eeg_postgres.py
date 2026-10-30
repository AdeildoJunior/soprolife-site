"""Prova EEG de unicidade, corrida e imutabilidade em PostgreSQL descartável."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from alembic import command
from sqlalchemy import create_engine, select, func, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
import pytest
from starlette.requests import Request
from app.config import Settings
from app.models import EegExam, FinancialEntry, FiscalDocument, FiscalPreparation
from app.schemas import EegAtendimentoCreate
from app.services import nfse_eeg
from tests.test_nfse_migrations import postgres_url, config
from tests.test_eeg_fiscal import eeg_source, eeg_policy


@pytest.fixture
def engine(postgres_url):
    command.upgrade(config(postgres_url), 'head')
    engine = create_engine(postgres_url)
    yield engine
    engine.dispose()


def test_corrida_atendimento_tem_um_exame_e_uma_receita(engine, db, users, eeg_source):
    from app.routers.eeg import novo_atendimento
    enrollment_id, actor_id = eeg_source[0].enrollment_id, users['admin'].id
    payload = EegAtendimentoCreate(enrollment_id=enrollment_id, modalidade_codigo='vigilia',
        agendado_para='2026-10-28T10:30:00-03:00', local_atendimento='Local Sintetico',
        municipio_atendimento_ibge='3304557', origem_canal='site', valor='250.00',
        pagamento_status='Pendente', idempotency_key='eeg-pg-synthetic-attendance-01')
    db.rollback()
    barrier = Barrier(2)
    def create(_):
        with Session(engine) as session:
            actor = session.get(type(users['admin']), actor_id)
            request = Request({'type': 'http', 'state': {'request_id': 'eeg-pg-synthetic'}})
            barrier.wait(timeout=10)
            return novo_atendimento(payload, request, session, actor)['id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(create, range(2)))
    assert ids[0] == ids[1]
    assert db.scalar(select(func.count()).select_from(EegExam).where(EegExam.idempotency_key == payload.idempotency_key)) == 1
    assert db.scalar(select(func.count()).select_from(FinancialEntry).where(FinancialEntry.eeg_exam_id == ids[0])) == 1


def test_corrida_preparo_e_identidade_fiscal_protegida(engine, db, users, eeg_source):
    eeg_policy(db, users)
    exam_id, actor_id = eeg_source[0].id, users['admin'].id
    db.rollback()
    barrier = Barrier(2)
    settings = Settings(nfse_enabled=True, nfse_environment='mock')
    def prepare(_):
        with Session(engine) as session:
            barrier.wait(timeout=10)
            return nfse_eeg.prepare(session, exam_id, settings, actor_id).id
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(prepare, range(2)))
    assert ids[0] == ids[1]
    assert db.scalar(select(func.count()).select_from(FiscalDocument)) == 1
    assert db.scalar(select(func.count()).select_from(FiscalPreparation)) == 1
    db.rollback()
    for statement in ["UPDATE fiscal_documents SET eeg_exam_id=NULL WHERE id=:id",
                      "DELETE FROM fiscal_documents WHERE id=:id"]:
        with engine.connect() as conn:
            with pytest.raises(DBAPIError, match='immutable fiscal'):
                conn.execute(text(statement), {'id': ids[0]})
            conn.rollback()
    # Mesmo que um chamador tente usar outra chave, o banco ocupa o EEG uma
    # única vez por ambiente, sem converter o exame em espirometria.
    with engine.connect() as conn:
        with pytest.raises(DBAPIError, match='uq_fiscal_eeg_environment'):
            conn.execute(text("INSERT INTO fiscal_documents (id, eeg_exam_id, kind, environment, state, eligibility, blocking_reasons, created_by, idempotency_key, idempotency_fingerprint, created_at, updated_at) VALUES ('another-synthetic-doc', :exam, 'eeg', 'mock', 'blocked', 'blocked', '[]', :actor, 'another-key', 'another-fingerprint', now(), now())"), {'exam': exam_id, 'actor': actor_id})
        conn.rollback()


@pytest.mark.parametrize('identity_mode', ['nova_pessoa', 'person_id'])
def test_inline_identity_race_is_atomic(engine, users, identity_mode):
    """Same key racing before enrollment exists must not leave duplicate people."""
    from app.models import Person, EegEnrollment, EegReportDocument
    from app.routers.eeg import novo_atendimento
    actor_id=users['admin'].id
    data=dict(modalidade_codigo='vigilia',agendado_para='2026-10-20T10:30:00-03:00',
        local_atendimento='Unidade PG Sintetica',municipio_atendimento_ibge='3304557',
        origem_canal='direto',valor='250.00',idempotency_key='eeg-inline-pg-race-01')
    if identity_mode=='nova_pessoa':
        data['nova_pessoa']={'nome_completo':'Pessoa Inline PG Sintetica','cpf':'52998224725'}
    else:
        with Session(engine) as session:
            p=Person(public_code='PES-000901',nome_completo='Pessoa PG Compartilhada',nome_normalizado='pessoa pg compartilhada')
            session.add(p);session.commit();data['person_id']=p.id
    payload=EegAtendimentoCreate(**data)
    barrier=Barrier(2)
    def create(_):
        with Session(engine) as session:
            actor=session.get(type(users['admin']),actor_id)
            request=Request({'type':'http','state':{'request_id':'inline-pg-synthetic'}})
            barrier.wait(timeout=10)
            return novo_atendimento(payload,request,session,actor)['id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids=list(pool.map(create,range(2)))
    assert ids[0]==ids[1]
    with Session(engine) as session:
        for model in (Person,EegEnrollment,EegExam,FinancialEntry):
            assert session.scalar(select(func.count()).select_from(model))==1
        assert session.scalar(select(func.count()).select_from(EegReportDocument))==0


def test_inline_retry_when_identity_check_sees_concurrent_commit(engine, users, monkeypatch):
    """Force the winner to commit after key lookup, before duplicate checking."""
    from threading import Event
    from app.routers import eeg
    from app.models import Person, EegEnrollment
    actor_id=users['admin'].id
    payload=EegAtendimentoCreate(nova_pessoa={'nome_completo':'Pessoa Sintetica Corrida Tardia',
        'cpf':'52998224725'}, modalidade_codigo='vigilia', agendado_para='2026-10-20T10:30:00-03:00',
        local_atendimento='Unidade PG Sintetica',municipio_atendimento_ibge='3304557',
        origem_canal='direto',valor='250.00',idempotency_key='eeg-inline-pg-late-commit-01')
    original=eeg._criar_pessoa_vinculo
    entered=Barrier(2)
    committed=Event()
    def create_person(payload,request,session,actor):
        entered.wait(timeout=10)
        if request.state.request_id=='loser':
            assert committed.wait(timeout=10)
        return original(payload,request,session,actor)
    monkeypatch.setattr(eeg,'_criar_pessoa_vinculo',create_person)
    def create(label):
        with Session(engine) as session:
            actor=session.get(type(users['admin']),actor_id)
            request=Request({'type':'http','state':{'request_id':label}})
            result=eeg.novo_atendimento(payload,request,session,actor)['id']
            if label=='winner':committed.set()
            return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids=list(pool.map(create,['winner','loser']))
    assert ids[0]==ids[1]
    with Session(engine) as session:
        for model in (Person,EegEnrollment,EegExam,FinancialEntry):
            assert session.scalar(select(func.count()).select_from(model))==1
