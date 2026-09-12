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
