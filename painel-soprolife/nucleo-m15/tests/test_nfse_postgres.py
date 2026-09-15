"""Real transaction races; requires an explicitly configured disposable PG server."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from alembic import command
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import FiscalDocument, FiscalAttempt, FiscalPreparation
from app.services import nfse
from tests.test_nfse_migrations import postgres_url, config
from tests.test_nfse_foundation import settings, source, ready


@pytest.fixture
def engine(postgres_url):
    command.upgrade(config(postgres_url), 'head')
    eng = create_engine(postgres_url)
    yield eng
    eng.dispose()


def test_concurrent_preparation_has_one_document_and_one_snapshot(engine, db, users, source, settings):
    from tests.test_nfse_foundation import policy_payload
    nfse.create_policy(db, policy_payload(), users['admin'].id)
    exam_id, actor_id = source[0].id, users['gestor'].id
    barrier = Barrier(2)
    def worker(_):
        with Session(engine, expire_on_commit=False) as session:
            barrier.wait(timeout=10)
            return nfse.prepare(session, exam_id, settings, actor_id).id
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(worker, range(2)))
    assert ids[0] == ids[1]
    assert db.scalar(select(func.count()).select_from(FiscalDocument)) == 1
    assert db.scalar(select(func.count()).select_from(FiscalPreparation)) == 1


@pytest.mark.parametrize('same_key', [True, False])
def test_concurrent_issue_never_calls_provider_twice(engine, db, users, settings, ready, same_key):
    from fastapi import HTTPException
    from app.services.nfse_providers import MockNfseProvider
    doc_id, actor_id = ready.id, users['gestor'].id
    barrier = Barrier(2)
    calls = []
    class Counter(MockNfseProvider):
        def issue(self, request):
            calls.append(request.operation_id)
            return super().issue(request)
    def worker(index):
        with Session(engine, expire_on_commit=False) as session:
            barrier.wait(timeout=10)
            try:
                return nfse.operate(session, doc_id, 'issue',
                                    'concurrent-key' if same_key else f'concurrent-{index}',
                                    settings, actor_id, provider=Counter()).state
            except HTTPException as exc:
                # A different key may observe the durably committed start.
                assert not same_key
                assert exc.detail['codigo'] == 'reconciliation_required'
                session.rollback()
                return 'in_progress'
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(worker, range(2)))
    assert len(calls) == 1
    assert db.scalar(select(func.count()).select_from(FiscalAttempt)) == 2
    assert nfse.get_document(db, doc_id).state == 'simulated'


def test_concurrent_dps_number_allocation_never_collides(engine, db, users, settings, source):
    """M36 — real transaction race: two DIFFERENT documents allocating a
    numero_dps in the SAME scope at the same instant must never receive the
    same number. Row-locked allocation (DpsNumberSequence.with_for_update())
    is only genuinely race-safe against real PostgreSQL locking — this is
    the one place that guarantee is actually exercised under true
    concurrency, matching test_concurrent_issue_never_calls_provider_twice
    above for the exact same reason.
    """
    from app.services.nfse_national.dps_numbering import allocate_dps_number
    from tests.test_nfse_foundation import policy_payload

    nfse.create_policy(db, policy_payload(), users['admin'].id)
    doc_a = nfse.prepare(db, source[0].id, settings, users['gestor'].id)

    # A second, independent document — same scope, never sharing exam/prep with A.
    from datetime import date
    from decimal import Decimal
    from app.models import FinancialEntry, Person, SpirometryExam
    p = Person(public_code='PES-PGRACE-B', nome_completo='Pessoa Sintética PG Race B',
              nome_normalizado='pessoa sintetica pg race b')
    db.add(p)
    db.flush()
    e = SpirometryExam(public_code='ESP-PGRACE-B', person_id=p.id, status='Realizado',
                       data_exame=date(2026, 8, 10), data_exame_precisao='dia',
                       modalidade='residencial', broncodilatador=True)
    db.add(e)
    db.flush()
    f = FinancialEntry(public_code='LAN-PGRACE-B', tipo='receita', categoria='Espirometria',
                       valor=Decimal('123.45'), status='Recebido', spirometry_exam_id=e.id,
                       data_competencia=date(2026, 9, 1))
    db.add(f)
    db.commit()
    doc_b = nfse.prepare(db, e.id, settings, users['gestor'].id)

    scope = dict(codigo_municipio='3304557', tipo_inscricao_federal=2,
                inscricao_federal='63544026000110', serie_dps='00001')
    barrier = Barrier(2)

    def worker(document_id):
        with Session(engine, expire_on_commit=False) as session:
            barrier.wait(timeout=10)
            number = allocate_dps_number(session, document_id=document_id, **scope)
            session.commit()
            return number

    with ThreadPoolExecutor(max_workers=2) as pool:
        numbers = list(pool.map(worker, [doc_a.id, doc_b.id]))

    assert numbers[0] != numbers[1]
    assert sorted(numbers) == [1, 2]
    # Re-running for the SAME two documents (sequential now) must return the
    # SAME numbers already allocated — no new allocation, no drift.
    with Session(engine, expire_on_commit=False) as session:
        again_a = allocate_dps_number(session, document_id=doc_a.id, **scope)
        again_b = allocate_dps_number(session, document_id=doc_b.id, **scope)
        session.commit()
    assert {again_a, again_b} == set(numbers)
