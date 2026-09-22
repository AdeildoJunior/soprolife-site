"""M47 — the DPS #9 `database is locked` was a CALLER self-lock.

Every local HTTP 500 on DPS #9 (M45, then again after the M46 fixes) failed
at the same line: the durable-intent `db.commit()` in `nfse.operate()`,
with `sqlite3.OperationalError: database is locked`. M46 looked for the
holder inside the API process and did not find it, then raised
`busy_timeout` from 5s to 30s on the theory that the contention was merely
slow. It was not slow — it was unresolvable.

The holder was the orchestration script itself
(`scripts/nfse_m45_dps9_restricted_issue.py`). Its `main()` opened one
session at the top, issued its first SQL at `db.get(User, ...)` — which,
under this app's explicit-`BEGIN` SQLite configuration, opens a transaction
and takes a SHARED lock on the database file — and then closed that session
only in a `finally:` at the very end, i.e. AFTER the HTTP POST had already
returned. So while the API tried to escalate its own write transaction to
EXCLUSIVE for the durable commit, the caller still held SHARED; and the
caller could not release it, because it was blocked waiting for that very
POST's reply. A circular wait, which no `busy_timeout` can win: the writer
waits the whole bound and fails (measured: 3.008s at 3000ms, 30.050s at
30000ms — identical failure, just later).

These tests pin the caller-side contract that fixes it: the orchestrator
holds NO open session while the writer runs. Nothing here touches the
network, a real certificate, Sefin, or any real document — the provider is
a counting in-memory mock and the database is a per-test temporary file.
"""
import threading

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.models import FiscalAttempt, FiscalDocument, User
from app.services import nfse
from app.services.nfse_providers import MockNfseProvider
from tests.test_nfse_foundation import policy_payload, settings, source  # noqa: F401


class CountingProvider(MockNfseProvider):
    """Counts provider-boundary crossings, so a silently-introduced retry
    (or a duplicated issuance) fails the test instead of passing quietly."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def issue(self, request):
        self.calls += 1
        return super().issue(request)


def _orchestrator_reads(session, user_id):
    """The orchestrator's real pre-POST local DB work, verbatim in shape:
    read the lab actor row so a Bearer token can be minted from it.

    `issue_token` itself is a pure HMAC over (user_id, exp, password
    fingerprint) and performs no SQL, so this read is the ONLY database
    access the orchestrator needs before its HTTP calls — which is exactly
    why it can be scoped to a session that dies before them.
    """
    user = session.get(User, user_id)
    assert user is not None
    return user.password_hash


def _issue(db, doc_id, key, settings_obj, actor, provider):
    """Stands in for the API servicing POST /emitir-mock: the same
    `nfse.operate()` call, including the durable-intent commit."""
    return nfse.operate(db, doc_id, 'issue', key, settings_obj, actor, provider=provider)


# ------------------------------------------------- the bug, reproduced


def test_old_orchestrator_lifetime_self_locks_the_durable_commit(
        engine, db, users, settings, source):  # noqa: F811
    """OLD behaviour: caller opens a session, reads, and keeps it open
    across the writer — the exact shape of `main()` before M47.

    Proves all three legs of the circular wait: the caller IS in a
    transaction, the writer fails with the real error text, and it fails at
    the durable commit rather than at the INSERT.
    """
    nfse.create_policy(db, policy_payload(), users['admin'].id)
    doc = nfse.prepare(db, source[0].id, settings, users['gestor'].id)
    db.commit()

    Caller = sessionmaker(bind=engine, expire_on_commit=False)
    caller = Caller()
    _orchestrator_reads(caller, users['gestor'].id)

    # Leg 1 — the caller holds an open transaction (SHARED lock) right now.
    assert caller.in_transaction() is True
    assert caller.connection().connection.dbapi_connection.in_transaction is True

    # Leg 2 — the writer, on its own connection, cannot complete. This is
    # the API's position: it is NOT blocked on the INSERT, only on the
    # COMMIT's escalation to EXCLUSIVE.
    writer = Caller()
    provider = CountingProvider()
    try:
        with pytest.raises(OperationalError, match="database is locked"):
            _issue(writer, doc.id, 'm47-selflock-old', settings,
                   users['gestor'].id, provider)
    finally:
        writer.close()

    # Leg 3 — the caller is STILL holding it: in the real flow it only
    # closes after the POST it is blocked on returns, which is why no
    # timeout value can rescue this.
    assert caller.in_transaction() is True
    caller.close()

    # Nothing durable was written by the failed attempt.
    assert db.scalar(select(func.count()).select_from(FiscalAttempt)) == 0


# ------------------------------------------------- the fix, proven


def test_fixed_orchestrator_lifetime_allows_the_durable_commit(
        engine, db, users, settings, source):  # noqa: F811
    """NEW behaviour: the caller's reads live in a `with`-scoped session
    that is fully closed BEFORE the writer runs — the shape `main()` now
    has. The same durable commit then succeeds."""
    nfse.create_policy(db, policy_payload(), users['admin'].id)
    doc = nfse.prepare(db, source[0].id, settings, users['gestor'].id)
    db.commit()

    Caller = sessionmaker(bind=engine, expire_on_commit=False)

    with Caller() as caller:
        _orchestrator_reads(caller, users['gestor'].id)
        assert caller.in_transaction() is True  # open here, on purpose

    # Closed — and provably so, at the pool level, not just by inspection.
    assert engine.pool.checkedout() == 0, (
        "the orchestrator must hold no pooled connection when the POST is made")

    writer = Caller()
    provider = CountingProvider()
    try:
        result = _issue(writer, doc.id, 'm47-selflock-fixed', settings,
                        users['gestor'].id, provider)
        assert result.state == 'simulated'
    finally:
        writer.close()

    assert provider.calls == 1, "provider must be crossed exactly once — no retry"
    attempts = db.scalars(
        select(FiscalAttempt).where(FiscalAttempt.document_id == doc.id)).all()
    assert len([a for a in attempts if a.phase == 'started']) == 1
    assert len([a for a in attempts if a.phase == 'completed']) == 1, "no duplicate attempt"


def test_fixed_lifetime_preserves_idempotency(engine, db, users, settings, source):  # noqa: F811
    """Replaying the same idempotency key through the fixed lifetime must
    still short-circuit: one attempt, one provider call, no duplicate."""
    nfse.create_policy(db, policy_payload(), users['admin'].id)
    doc = nfse.prepare(db, source[0].id, settings, users['gestor'].id)
    db.commit()

    Caller = sessionmaker(bind=engine, expire_on_commit=False)
    provider = CountingProvider()

    for _ in range(2):
        with Caller() as caller:
            _orchestrator_reads(caller, users['gestor'].id)
        assert engine.pool.checkedout() == 0
        with Caller() as writer:
            _issue(writer, doc.id, 'm47-idempotent-key', settings,
                   users['gestor'].id, provider)

    assert provider.calls == 1, "the replay must not cross the provider a second time"
    completed = db.scalars(select(FiscalAttempt).where(
        FiscalAttempt.document_id == doc.id,
        FiscalAttempt.phase == 'completed')).all()
    assert len(completed) == 1


# ------------------------------------------------- the contract, pinned


def test_orchestrator_script_closes_every_session_before_its_http_calls():
    """Structural guard on the real script.

    The bug was not a wrong value — it was a session outliving the HTTP
    call below it. A future edit that reintroduces a module-level or
    `main()`-level session spanning the POST would restore the deadlock
    while every behavioural test above still passed, because those tests
    exercise the SHAPE, not this file. So assert on the file itself.
    """
    from pathlib import Path

    script = (Path(__file__).resolve().parents[1]
              / "scripts" / "nfse_m45_dps9_restricted_issue.py").read_text()

    # Every session in the script is context-managed...
    assert "with get_sessionmaker()() as db:" in script
    # ...and none is opened bare and closed in a trailing `finally`, which
    # is precisely what spanned the POST before M47.
    assert "db = get_sessionmaker()()" not in script
    # The pre-POST assertion that catches any regression at runtime, too.
    assert "_assert_no_open_local_transaction" in script

    body = script.split("def main():", 1)[1]
    post_at = body.index("_post(")
    before_post = body[:post_at]
    assert "_assert_no_open_local_transaction" in before_post, (
        "main() must assert it holds no open transaction before the POST")
    assert "_read_audit_detail()" in body[post_at:], (
        "the audit read must happen only AFTER the POST, in its own session")


def test_busy_timeout_is_not_used_to_paper_over_a_structural_lock():
    """M47 — `SQLITE_BUSY_TIMEOUT_MS` was raised to 30000 as an attempted
    fix for this very failure, and measurably did not help: the writer just
    waited 30.050s and failed identically. Legitimate contention here is
    between request-scoped sessions holding locks for milliseconds, so the
    bound stays short and a structural self-lock stays loud and fast
    instead of becoming a half-minute hang. Raising this number is not a
    valid response to `database is locked`; fixing the holder is.
    """
    import app.db as db_module

    assert db_module.SQLITE_BUSY_TIMEOUT_MS == 5000
