"""M46 — SQLite concurrency hardening.

DPS #9 (M45 mission) hit a real HTTP 500 with `sqlite3.OperationalError:
database is locked` at the durable-intent `db.commit()` inside
`nfse.operate()`, followed by a SECOND, unrelated-looking failure on the
very next request (`cannot start a transaction within a transaction`). Both
symptoms are reproduced deterministically here (no threads needed for the
"old" reproduction — SQLite's own locking rules make it reproducible in a
single thread with two overlapping sessions).

CORRECTION made within this same mission, kept here for the record: Python's
own ``sqlite3`` module already defaults ``connect(timeout=5.0)`` regardless
of any PRAGMA — so the OLD configuration was never failing "instantly"; a
lock held for the ENTIRE test (as below) always fails after ~5s either way.
Restating `PRAGMA busy_timeout=5000` therefore changed nothing for real
contention that genuinely outlasts 5 seconds (confirmed on a second real
DPS #9 attempt, still failing identically after that "fix"). The two real,
structural improvements this file proves are: (1) the `handle_error`
listener that discards a poisoned pooled connection instead of returning it
— this DOES fully eliminate the second-order "transaction within a
transaction" cascade, confirmed on that same second real attempt.

M47 CORRECTION — the second thing this file used to claim (that raising
`SQLITE_BUSY_TIMEOUT_MS` to 30s "narrows the window") was wrong about the
real failure, and the constant has been returned to 5000. There was no
window to narrow: the lock holder was the ORCHESTRATION SCRIPT's own
session, held open across the HTTP POST whose reply that same process was
blocked on. The writer therefore waited the full bound and failed
identically at any value (30.050s at 30000ms, 3.008s at 3000ms). The
scenarios below remain valid as descriptions of GENUINE transient
contention between request-scoped sessions; they are simply not what
DPS #9 hit. The real root cause, its deterministic reproduction and the
caller-side fix live in tests/test_nfse_orchestrator_self_lock.py.

No change here weakens durable-intent semantics, adds a fiscal-network
retry, or touches PostgreSQL (every listener is registered only inside
`build_engine()`'s `if url.startswith("sqlite")` branch).
"""
import sqlite3
import tempfile
import threading
import time

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

import app.db as db_module
from app.db import Base, build_engine
from app.models import AuditLog, FiscalAttempt, FiscalDocument
from app.services import nfse
from app.services.nfse_providers import MockNfseProvider
from tests.test_nfse_foundation import policy_payload, settings, source


def _old_style_engine(url, **kwargs):
    """The EXACT pre-M46 SQLite listener setup (no busy_timeout, no
    handle_error discard) — used only to prove the bug was real, never to
    claim it as today's behavior."""
    engine = create_engine(url, future=True, connect_args={"check_same_thread": False}, **kwargs)

    @event.listens_for(engine, "connect")
    def _connect(dbapi_conn, _record):
        dbapi_conn.isolation_level = None
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")

    return engine


def _audit_row(n=""):
    return AuditLog(acao=f"teste{n}", entidade="x", entidade_id="1", detalhes={})


# ============================================================ Phase B — reproduce OLD bug


def test_old_configuration_reproduces_database_is_locked(tmp_path):
    """A read transaction left open FOR THE WHOLE TEST (mirrors a GET
    session that never releases its lock) makes a concurrent durable-intent
    COMMIT fail — after Python's own ~5s default busy_timeout is exhausted
    (this takes ~5s to run; see the module docstring's correction)."""
    engine = _old_style_engine(f"sqlite:///{tmp_path}/old.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    reader, writer = Session(), Session()
    try:
        reader.execute(select(AuditLog))  # opens a deferred txn, SHARED lock, left open
        writer.add(_audit_row())
        with pytest.raises(OperationalError, match="database is locked"):
            writer.commit()
    finally:
        reader.close()
        writer.close()
        engine.dispose()


def test_old_configuration_poisons_the_pooled_connection_after_lock_failure(tmp_path):
    """The exact second-order symptom seen in the real M45 incident: after a
    failed commit, later reuse of the SAME pooled connection fails with an
    unrelated-looking error, even though nothing else is wrong."""
    engine = _old_style_engine(f"sqlite:///{tmp_path}/old2.db", pool_size=2, max_overflow=0)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    reader, writer = Session(), Session()
    reader.execute(select(AuditLog))
    writer.add(_audit_row())
    with pytest.raises(OperationalError, match="database is locked"):
        writer.commit()
    writer.close()
    reader.close()

    fresh = Session()
    try:
        fresh.add(_audit_row("-depois"))
        with pytest.raises(OperationalError, match="cannot start a transaction within a transaction"):
            fresh.commit()
    finally:
        fresh.close()
        engine.dispose()


# ============================================================ Phase C — fix proven


def test_configured_timeout_matches_pythons_own_default():
    """Python's ``sqlite3`` module defaults ``connect(timeout=5.0)``
    regardless of any PRAGMA (confirmed directly: a bare
    ``sqlite3.connect(":memory:")`` already reports `PRAGMA busy_timeout`
    == 5000). We restate that same value explicitly so the bound is visible
    and pinned at the SQLAlchemy layer instead of being inherited silently
    from the driver.

    M47 CORRECTION to this file's own earlier reasoning: raising this past
    the default was tried (30000) and measurably did NOT fix the real DPS #9
    failure — the holder was the calling script's own session, kept open
    across the HTTP POST it was blocked waiting on, so the writer simply
    waited the entire bound and failed identically (30.050s at 30000ms vs
    3.008s at 3000ms). A longer timeout cannot win a circular wait; it only
    turns a fast, legible error into a long hang. See
    tests/test_nfse_orchestrator_self_lock.py for the proven root cause.
    """
    default_conn = sqlite3.connect(":memory:")
    try:
        python_default_ms = default_conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        default_conn.close()
    assert python_default_ms == 5000
    assert db_module.SQLITE_BUSY_TIMEOUT_MS == python_default_ms


def test_new_engine_sets_busy_timeout(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path}/new_pragma.db")
    with engine.connect() as conn:
        value = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
    assert value == db_module.SQLITE_BUSY_TIMEOUT_MS
    engine.dispose()


def test_concurrent_read_then_durable_commit_waits_and_succeeds(tmp_path, monkeypatch):
    """The busy_timeout genuinely makes SQLite WAIT for the lock to clear
    instead of failing instantly — proven with a real background thread that
    releases the read transaction shortly after the write's commit is
    issued, and by measuring that the commit actually took that long."""
    monkeypatch.setattr(db_module, "SQLITE_BUSY_TIMEOUT_MS", 2000)
    engine = build_engine(f"sqlite:///{tmp_path}/new_wait.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    reader = Session()
    reader.execute(select(AuditLog))

    def release():
        time.sleep(0.3)
        reader.close()

    releaser = threading.Thread(target=release)
    releaser.start()
    writer = Session()
    writer.add(_audit_row())
    start = time.monotonic()
    writer.commit()  # must NOT raise
    elapsed = time.monotonic() - start
    releaser.join()
    writer.close()
    engine.dispose()
    assert elapsed >= 0.25, "commit returned before the lock was actually released — busy_timeout not waiting"


def test_session_not_poisoned_after_a_genuine_lock_timeout(tmp_path, monkeypatch):
    """Contention that genuinely OUTLASTS busy_timeout must still raise (a
    bounded timeout is not a promise of eventual success) — but the failed
    connection must be discarded, never handed back to the pool broken. A
    fresh session right after must work exactly like nothing happened, and
    the failed commit must not have left any partial row behind."""
    monkeypatch.setattr(db_module, "SQLITE_BUSY_TIMEOUT_MS", 100)
    engine = build_engine(f"sqlite:///{tmp_path}/new_timeout.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    reader = Session()
    reader.execute(select(AuditLog))

    def release_late():
        time.sleep(0.35)  # outlasts the 100ms busy_timeout on purpose
        reader.close()

    releaser = threading.Thread(target=release_late)
    releaser.start()
    writer = Session()
    writer.add(_audit_row("-timeout"))
    with pytest.raises(OperationalError, match="database is locked"):
        writer.commit()
    writer.close()  # must not itself raise
    releaser.join()

    fresh = Session()
    try:
        fresh.add(_audit_row("-fresh"))
        fresh.commit()  # must succeed cleanly — no "transaction within a transaction"
        count = fresh.scalar(select(func.count()).select_from(AuditLog))
        assert count == 1, "the failed writer's row must not have been persisted"
    finally:
        fresh.close()
        engine.dispose()


def test_postgresql_url_gets_no_sqlite_listeners(monkeypatch):
    """The fix must be invisible to any non-SQLite backend."""
    captured = {}
    real_create_engine = db_module.create_engine

    def spy(url, **kwargs):
        captured["kwargs"] = kwargs
        # Never actually connect to a real Postgres in a unit test — just
        # inspect what build_engine WOULD have configured.
        raise RuntimeError("stop-before-connect")

    monkeypatch.setattr(db_module, "create_engine", spy)
    with pytest.raises(RuntimeError, match="stop-before-connect"):
        db_module.build_engine("postgresql://user:pass@localhost/db")
    assert "connect_args" not in captured["kwargs"]
    assert captured["kwargs"].get("pool_recycle") == db_module.POOL_RECYCLE_SEGUNDOS


# ============================================================ Phase D — end-to-end: nfse.operate()


def test_concurrent_get_session_does_not_break_durable_issuance_commit(engine, db, users, settings, source):
    """The exact production pattern: a GET request's session (an open read
    transaction on a SEPARATE connection from the same pool) overlapping
    with the fiscal durable-intent commit inside `nfse.operate()`. Proves:
    the commit succeeds (no `database is locked` reaching the caller), the
    provider boundary is crossed exactly once (no automatic retry was
    introduced), exactly one completed FiscalAttempt exists (no duplicate),
    and the document reaches its normal terminal state."""
    nfse.create_policy(db, policy_payload(), users['admin'].id)
    doc = nfse.prepare(db, source[0].id, settings, users['gestor'].id)

    reader_session = sessionmaker(bind=engine, expire_on_commit=False)()
    reader_session.execute(select(FiscalDocument).where(FiscalDocument.id == doc.id))

    def release_reader():
        time.sleep(0.2)
        reader_session.close()

    releaser = threading.Thread(target=release_reader)
    releaser.start()

    calls = {"count": 0}

    class CountingProvider(MockNfseProvider):
        def issue(self, request):
            calls["count"] += 1
            return super().issue(request)

    result_doc = nfse.operate(db, doc.id, 'issue', 'm46-concurrency-key-1', settings,
                              users['gestor'].id, provider=CountingProvider())
    releaser.join()

    assert calls["count"] == 1, "provider must be invoked exactly once — no automatic retry"
    attempts = db.scalars(select(FiscalAttempt).where(FiscalAttempt.document_id == doc.id)).all()
    completed = [a for a in attempts if a.phase == 'completed']
    started = [a for a in attempts if a.phase == 'started']
    assert len(completed) == 1, "durable intent must be committed exactly once, no duplicate attempt"
    assert len(started) == 1
    assert result_doc.state == 'simulated'  # MockNfseProvider's normal outcome


def test_existing_idempotent_retry_semantics_unaffected(db, users, settings, source):
    """A second call with the SAME idempotency key must still short-circuit
    to the already-recorded result, exactly as before M46 — the fix must
    not change idempotency behavior at all."""
    nfse.create_policy(db, policy_payload(), users['admin'].id)
    doc = nfse.prepare(db, source[0].id, settings, users['gestor'].id)
    first = nfse.operate(db, doc.id, 'issue', 'm46-idempotency-key', settings, users['gestor'].id)
    second = nfse.operate(db, doc.id, 'issue', 'm46-idempotency-key', settings, users['gestor'].id)
    assert first.id == second.id
    attempts = db.scalars(select(FiscalAttempt).where(FiscalAttempt.document_id == doc.id)).all()
    completed = [a for a in attempts if a.phase == 'completed']
    assert len(completed) == 1, "replaying the same idempotency key must not create a second attempt"
