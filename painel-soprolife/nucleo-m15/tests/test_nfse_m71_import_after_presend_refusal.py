"""M71 — a manual NFS-e issued after the button was refused before the provider.

ESP-000063: the button prepared a production document, the worker refused
all three confirmations BEFORE the provider (certificate margin; zero POST,
zero GET), and the note was then issued by hand on the portal. The exam
already holds a production document, so the M61 import refused it — and the
document stayed ``pending``, one click away from a duplicate.

These tests pin the one narrow exception: a pending document PROVEN never
sent may receive the import; anything short of that proof is refused.

The access key is the real one (a fiscal identifier, not personal data).
Everything else is synthetic. No provider, no DPS, no socket.
"""
import importlib.util
import json
import socket
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.models import (AuditLog, DpsNumberAllocation, DpsNumberSequence, FinancialEntry,
                        FiscalArtifact, FiscalAttempt, FiscalDocument, FiscalIssuanceRequest,
                        FiscalPreparation, utcnow)
from app.services.idempotency import payload_fingerprint
from app.services.nfse_external_issuance import (
    EXTERNAL_PROVIDER, IMPORT_OPERATION, ExternalIssuance, never_sent_blockers,
    register_external_issuance)
from app.services.nfse_validity import fiscal_validity

from tests.test_nfse_m61_external_issuance import MANUAL_A, _exam

MANUAL_15 = "33045572263544026000110000000000001526108703991057"
SERVICE_DATE = "2026-10-09"
ISSUED_ON = "2026-10-10"
AUTOMATIC_SCOPE = "3304557:2:63544026000110:00001"

_SEED = iter(range(1, 1000))


@pytest.fixture
def worker_dirs(tmp_path):
    spool, runtime = tmp_path / "spool", tmp_path / "runtime"
    spool.mkdir()
    runtime.mkdir()
    return {"spool_dir": spool, "runtime_dir": runtime}


def _pending(db, users, *, slug, requests=(("refused", 0, 0),) * 3, state="pending"):
    """An exam whose production document the button prepared and the worker
    refused before the provider, ``requests`` as (status, post, get)."""
    exam = _exam(db, users, slug=slug, valor="219.00")
    actor = users["gestor"].id
    doc = FiscalDocument(spirometry_exam_id=exam.id, environment="production", state=state,
                         eligibility="eligible", blocking_reasons=[], created_by=actor,
                         idempotency_key=payload_fingerprint({"m71": slug}),
                         idempotency_fingerprint=payload_fingerprint({"m71": slug}))
    db.add(doc)
    db.flush()
    prep = FiscalPreparation(document_id=doc.id, flow="DIRECT", blocking_reasons=[],
                             service_date=date(2026, 10, 9), competence=date(2026, 10, 9),
                             service_municipio_ibge="3304557",
                             fingerprint=payload_fingerprint({"prep": slug}), created_by=actor,
                             created_at=utcnow() - timedelta(days=2))
    db.add(prep)
    db.flush()
    now = utcnow() - timedelta(days=2)
    for status, post, get in requests:
        db.add(FiscalIssuanceRequest(
            document_id=doc.id, spirometry_exam_id=exam.id, preparation_id=prep.id,
            preparation_fingerprint=prep.fingerprint, recipient_fingerprint="0" * 64,
            amount_confirmed=Decimal("219.00"), kind="issue", status=status,
            authorized_by=actor, authorized_at=now, expires_at=now + timedelta(minutes=15),
            claimed_at=now, finished_at=None if status in ("authorized", "running") else now,
            result_code="certificate_margin_insufficient" if status == "refused" else None,
            provider_post_count=post, provider_get_count=get,
            idempotency_key=payload_fingerprint({"req": slug, "n": next(_SEED)})))
    db.commit()
    return exam, doc


def _import(db, users, exam, key=MANUAL_15, **dirs):
    return register_external_issuance(
        db, ExternalIssuance(exam.id, key, ISSUED_ON), users["gestor"].id, **dirs)


def _refused(db, users, exam, code, **dirs):
    with pytest.raises(HTTPException) as error:
        _import(db, users, exam, **dirs)
    assert error.value.detail["codigo"] == code


def _snapshot_requests(db, doc_id):
    return [(r.id, r.status, r.result_code, r.provider_post_count, r.provider_get_count,
             r.operation_id, r.external_id, r.preparation_id)
            for r in db.query(FiscalIssuanceRequest).filter_by(document_id=doc_id)
            .order_by(FiscalIssuanceRequest.id)]


# ------------------------------------------------------------ 1–2 o caminho


def test_1_no_existing_document_imports_as_before(db, users, worker_dirs):
    exam = _exam(db, users, slug="M71A", valor="219.00")
    document = _import(db, users, exam, **worker_dirs)
    assert document.state == "issued"
    audit = db.query(AuditLog).filter_by(acao="fiscal.external_issuance_imported").one()
    assert audit.detalhes["reused_pending_document"] is False


def test_2_pending_never_sent_document_is_reused(db, users, worker_dirs):
    exam, doc = _pending(db, users, slug="M71B")
    document = _import(db, users, exam, **worker_dirs)

    assert document.id == doc.id
    assert document.state == "issued"
    assert db.query(FiscalDocument).filter_by(spirometry_exam_id=exam.id).count() == 1
    attempt = db.query(FiscalAttempt).filter_by(document_id=doc.id).one()
    assert (attempt.operation, attempt.phase, attempt.provider, attempt.outcome) == (
        IMPORT_OPERATION, "completed", EXTERNAL_PROVIDER, "issued")
    assert attempt.reconciliation_required is False
    assert attempt.external_id == "NFS" + MANUAL_15
    assert db.query(FiscalAttempt).filter_by(operation="issue").count() == 0
    flows = sorted(p.flow for p in db.query(FiscalPreparation).filter_by(document_id=doc.id))
    assert flows == ["DIRECT", "EXTERNAL"]          # the old one kept, a new one added
    assert attempt.preparation_id != db.query(FiscalPreparation).filter_by(flow="DIRECT").one().id
    assert db.query(FiscalArtifact).filter_by(document_id=doc.id).count() == 0
    assert fiscal_validity(db, document) is False
    audit = db.query(AuditLog).filter_by(acao="fiscal.external_issuance_imported").one()
    assert audit.detalhes["reused_pending_document"] is True
    assert audit.detalhes["prior_refused_requests"] == 3


# ------------------------------------------------------------ 3–11 recusas


def test_3_pending_with_an_attempt_is_refused(db, users, worker_dirs):
    exam, doc = _pending(db, users, slug="M71C")
    prep = db.query(FiscalPreparation).filter_by(document_id=doc.id).one()
    db.add(FiscalAttempt(document_id=doc.id, preparation_id=prep.id, operation_id="op",
                         operation="issue", phase="started", number=1, provider="production",
                         environment="production", outcome="pending",
                         actor_id=users["gestor"].id, started_at=utcnow(),
                         idempotency_key="k" * 64, idempotency_fingerprint="k" * 64))
    db.commit()
    assert "document_has_attempt" in never_sent_blockers(db, doc, **worker_dirs)
    _refused(db, users, exam, "pending_document_not_provably_unsent", **worker_dirs)


def test_4_pending_with_a_dps_number_is_refused(db, users, worker_dirs):
    exam, doc = _pending(db, users, slug="M71D")
    db.add(DpsNumberAllocation(document_id=doc.id, scope_key=AUTOMATIC_SCOPE, dps_number=99))
    db.commit()
    assert "document_has_dps_number" in never_sent_blockers(db, doc, **worker_dirs)
    _refused(db, users, exam, "pending_document_not_provably_unsent", **worker_dirs)


def test_5_pending_with_an_artifact_is_refused(db, users, worker_dirs):
    exam, doc = _pending(db, users, slug="M71E")
    db.add(FiscalArtifact(document_id=doc.id, kind="dps_unsigned_xml", storage_relative_path="x",
                          sha256="0" * 64, size_bytes=1, created_by=users["gestor"].id))
    db.commit()
    assert "document_has_artifact" in never_sent_blockers(db, doc, **worker_dirs)
    _refused(db, users, exam, "pending_document_not_provably_unsent", **worker_dirs)


@pytest.mark.parametrize("status", ["authorized", "running"])
def test_6_7_pending_with_a_live_request_is_refused(db, users, worker_dirs, status):
    exam, doc = _pending(db, users, slug=f"M71F{status[:3]}",
                         requests=[("refused", 0, 0), (status, 0, 0)])
    assert f"request_{status}" in never_sent_blockers(db, doc, **worker_dirs)
    _refused(db, users, exam, "pending_document_not_provably_unsent", **worker_dirs)


def test_8_a_request_with_a_post_is_refused(db, users, worker_dirs):
    exam, doc = _pending(db, users, slug="M71G", requests=[("refused", 1, 0)])
    assert "request_post_count" in never_sent_blockers(db, doc, **worker_dirs)
    _refused(db, users, exam, "pending_document_not_provably_unsent", **worker_dirs)


def test_9_a_request_with_a_get_is_refused(db, users, worker_dirs):
    exam, doc = _pending(db, users, slug="M71H", requests=[("refused", 0, 1)])
    assert "request_get_count" in never_sent_blockers(db, doc, **worker_dirs)
    _refused(db, users, exam, "pending_document_not_provably_unsent", **worker_dirs)


@pytest.mark.parametrize("status", ["uncertain", "interrupted", "reconciled", "issued",
                                    "rejected"])
def test_10_an_uncertain_or_settled_request_is_refused(db, users, worker_dirs, status):
    exam, doc = _pending(db, users, slug=f"M71I{status[:3]}", requests=[(status, 0, 0)])
    assert f"request_{status}" in never_sent_blockers(db, doc, **worker_dirs)
    _refused(db, users, exam, "pending_document_not_provably_unsent", **worker_dirs)


def test_10a_a_reconcile_request_is_refused_even_if_refused(db, users, worker_dirs):
    """A reconcile request exists only because something was thought to be in
    flight — reconcile_required by definition, whatever its outcome."""
    exam, doc = _pending(db, users, slug="M71Irec")
    first = db.query(FiscalIssuanceRequest).filter_by(document_id=doc.id).first()
    now = utcnow() - timedelta(days=1)
    db.add(FiscalIssuanceRequest(
        document_id=doc.id, spirometry_exam_id=exam.id, preparation_id=first.preparation_id,
        preparation_fingerprint=first.preparation_fingerprint, recipient_fingerprint="0" * 64,
        amount_confirmed=Decimal("219.00"), kind="reconcile", status="refused",
        authorized_by=users["gestor"].id, authorized_at=now,
        expires_at=now + timedelta(minutes=15), finished_at=now,
        provider_post_count=0, provider_get_count=0,
        idempotency_key=payload_fingerprint({"req": "M71Irec", "kind": "reconcile"})))
    db.commit()
    assert "request_reconcile" in never_sent_blockers(db, doc, **worker_dirs)
    _refused(db, users, exam, "pending_document_not_provably_unsent", **worker_dirs)


def test_10b_a_doorbell_or_post_intent_left_behind_is_refused(db, users, worker_dirs):
    exam, doc = _pending(db, users, slug="M71J", requests=[("refused", 0, 0)])
    request_id = db.query(FiscalIssuanceRequest).filter_by(document_id=doc.id).one().id
    (worker_dirs["spool_dir"] / f"{request_id}.issue").write_text(request_id)
    assert "doorbell_present" in never_sent_blockers(db, doc, **worker_dirs)
    (worker_dirs["spool_dir"] / f"{request_id}.issue").unlink()
    (worker_dirs["runtime_dir"] / request_id).mkdir()
    (worker_dirs["runtime_dir"] / request_id / "post-intent.json").write_text("{}")
    assert "post_intent_present" in never_sent_blockers(db, doc, **worker_dirs)
    _refused(db, users, exam, "pending_document_not_provably_unsent", **worker_dirs)


def test_10c_worker_dirs_that_cannot_be_checked_are_refused(db, users, tmp_path):
    """Absence of evidence in an unconfigured or missing directory is not proof."""
    exam, doc = _pending(db, users, slug="M71K")
    assert "worker_dirs_not_configured" in never_sent_blockers(
        db, doc, spool_dir=None, runtime_dir=None)
    assert "worker_dirs_unreadable" in never_sent_blockers(
        db, doc, spool_dir=tmp_path / "nope", runtime_dir=tmp_path / "nope2")
    _refused(db, users, exam, "pending_document_not_provably_unsent")


@pytest.mark.parametrize("state", ["issued", "failed", "uncertain", "issuing", "blocked"])
def test_11_a_document_in_any_other_state_is_refused(db, users, worker_dirs, state):
    exam, doc = _pending(db, users, slug=f"M71L{state[:3]}", state=state)
    _refused(db, users, exam, "production_document_already_exists_for_exam", **worker_dirs)
    assert db.get(FiscalDocument, doc.id).state == state


# ------------------------------------------------------------ 12–13 a chave


def test_12_the_same_key_on_another_document_is_refused(db, users, worker_dirs):
    other = _exam(db, users, slug="M71M", valor="219.00")
    _import(db, users, other, **worker_dirs)
    exam, doc = _pending(db, users, slug="M71N")
    _refused(db, users, exam, "external_access_key_already_imported_for_another_document",
             **worker_dirs)
    assert db.get(FiscalDocument, doc.id).state == "pending"


def test_13_the_same_key_on_the_same_document_is_idempotent(db, users, worker_dirs):
    exam, doc = _pending(db, users, slug="M71O")
    first = _import(db, users, exam, **worker_dirs)
    again = _import(db, users, exam, **worker_dirs)
    assert first.id == again.id == doc.id
    assert db.query(FiscalAttempt).filter_by(document_id=doc.id).count() == 1


# ------------------------------------------------------------ 14–17 nada mais muda


def test_14_15_16_17_nothing_else_moves(db, users, worker_dirs):
    db.add(DpsNumberSequence(scope_key=AUTOMATIC_SCOPE, next_value=7))
    db.commit()
    exam, doc = _pending(db, users, slug="M71P")
    entries = [(e.id, e.valor, e.status) for e in db.query(FinancialEntry).all()]
    before = _snapshot_requests(db, doc.id)

    _import(db, users, exam, **worker_dirs)

    # 14 — no financial entry created or changed
    assert [(e.id, e.valor, e.status) for e in db.query(FinancialEntry).all()] == entries
    # 15 — the automatic allocator is untouched
    assert [(s.scope_key, s.next_value) for s in db.query(DpsNumberSequence)] == [
        (AUTOMATIC_SCOPE, 7)]
    assert db.query(DpsNumberAllocation).count() == 0
    # 16 — the manual series 70000 never reaches it
    assert not any("70000" in s.scope_key for s in db.query(DpsNumberSequence))
    # 17 — the three refused requests are exactly as they were
    assert _snapshot_requests(db, doc.id) == before
    assert all(r[1] == "refused" and r[3] == 0 and r[4] == 0 for r in before)


# ------------------------------------------------------------ 18 issued_on


def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "nfse_m61_import_manual_nfse.py"
    spec = importlib.util.spec_from_file_location("nfse_m61_import_manual_nfse", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_18_issued_on_is_the_emission_date_not_the_service_date(db, users, worker_dirs):
    script = _load_script()
    note = next(n for n in script.MANUAL_NOTES if n["label"] == "F")
    assert note["public_code"] == "ESP-000063"
    assert note["access_key"] == MANUAL_15
    assert note["amount"] == Decimal("219.00")
    assert str(note["service_date"]) == SERVICE_DATE
    assert script.issued_on(note) == ISSUED_ON != SERVICE_DATE
    # A–E keep the service date they were recorded with.
    assert all(script.issued_on(n) == str(n["service_date"])
               for n in script.MANUAL_NOTES if n["label"] != "F")

    exam, _ = _pending(db, users, slug="M71Q")
    _import(db, users, exam, **worker_dirs)
    audit = db.query(AuditLog).filter_by(acao="fiscal.external_issuance_imported").one()
    assert audit.detalhes["issued_on"] == ISSUED_ON


def test_18b_the_script_dry_run_recognises_the_pending_document(
        db, users, engine, worker_dirs, monkeypatch, capsys):
    """The canonical script, dry run, against an ESP-000063-shaped fact: it
    names the existing pending document and writes nothing."""
    from sqlalchemy.orm import sessionmaker

    import app.config
    import app.db

    exam, doc = _pending(db, users, slug="M71R")
    exam.public_code, exam.data_exame = "ESP-000063", date(2026, 10, 9)
    db.query(FinancialEntry).filter_by(spirometry_exam_id=exam.id).one().data_competencia = \
        date(2026, 10, 9)
    db.commit()

    class _Settings:
        nfse_production_worker_spool_dir = worker_dirs["spool_dir"]
        nfse_production_worker_runtime_dir = worker_dirs["runtime_dir"]

    monkeypatch.setenv("M15_DATABASE_URL", "sqlite://")
    monkeypatch.setattr(app.db, "get_sessionmaker", lambda: sessionmaker(bind=engine))
    monkeypatch.setattr(app.config, "get_settings", lambda: _Settings())
    monkeypatch.setattr("sys.argv", ["nfse_m61_import_manual_nfse.py"])
    _load_script().main()

    report = json.loads(capsys.readouterr().out)
    f = next(n for n in report["notes"] if n["label"] == "F")
    assert f["status"] == "would_link_existing_pending_document"
    assert f["existing_document_id"] == doc.id
    assert f["issued_on"] == ISSUED_ON
    assert report["applied"] is False and report["called_provider"] is False
    db.expire_all()
    assert db.get(FiscalDocument, doc.id).state == "pending"
    assert db.query(FiscalAttempt).count() == 0


# ------------------------------------------------------------ 19 zero rede


def test_19_the_import_never_touches_the_network(db, users, worker_dirs, monkeypatch):
    import httpx

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("tentativa de rede durante a importação")

    exam, doc = _pending(db, users, slug="M71S")
    monkeypatch.setattr(httpx, "Client", explode)
    monkeypatch.setattr(socket, "socket", explode)
    monkeypatch.setattr(socket, "create_connection", explode)
    document = _import(db, users, exam, **worker_dirs)
    assert document.state == "issued"


def test_20_after_the_import_the_exam_is_no_longer_issuable(
        db, users, worker_dirs, monkeypatch):
    """The point of the whole exception: the pending document no longer sits
    one click away from a duplicate."""
    from app.config import get_settings
    from app.services import nfse_production_issuance as production

    exam, doc = _pending(db, users, slug="M71V")
    _import(db, users, exam, **worker_dirs)

    monkeypatch.setenv("M15_NFSE_ENABLED", "true")
    monkeypatch.setenv("M15_NFSE_ENVIRONMENT", "production")
    monkeypatch.setenv("M15_NFSE_PRODUCTION_WORKER_SPOOL_DIR", str(worker_dirs["spool_dir"]))
    get_settings.cache_clear()
    try:
        settings = get_settings()
        with pytest.raises(HTTPException) as error:
            production.prepare_for_confirmation(db, exam.id, settings, users["gestor"].id)
        assert error.value.detail["codigo"] == "document_not_issuable"
        with pytest.raises(HTTPException) as error:
            production.authorize_issue(
                db, doc.id, preparation_id="x", amount="219,00", confirmation="x",
                idempotency_key="m71-after", settings=settings, actor=users["gestor"].id)
        assert error.value.detail["codigo"] == "document_not_pending_eligible"
        assert production._row_status(db, db.get(FiscalDocument, doc.id), []) == "imported"
    finally:
        get_settings.cache_clear()
    assert db.query(FiscalIssuanceRequest).filter_by(document_id=doc.id).count() == 3
    assert list(worker_dirs["spool_dir"].iterdir()) == []


def test_the_m61_keys_still_import_alongside(db, users, worker_dirs):
    a = _exam(db, users, slug="M71T", valor="279.00")
    exam, _ = _pending(db, users, slug="M71U")
    register_external_issuance(db, ExternalIssuance(a.id, MANUAL_A, "2026-09-15"),
                               users["gestor"].id)
    _import(db, users, exam, **worker_dirs)
    assert db.query(FiscalAttempt).filter_by(operation=IMPORT_OPERATION).count() == 2


# ------------------------------------------------------------ PostgreSQL

from tests.test_nfse_migrations import config as _alembic_config, postgres_url  # noqa: E402,F401


def test_on_postgresql_the_pending_document_is_reused_under_its_triggers(
        postgres_url, tmp_path):  # noqa: F811
    """Production's engine: the row lock is real, and the identity and
    append-only triggers must accept pending -> issued on the same row while
    leaving the refused requests untouched."""
    from alembic import command
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from tests.conftest import _make_user

    command.upgrade(_alembic_config(postgres_url), "head")
    engine = create_engine(postgres_url)
    dirs = {"spool_dir": tmp_path, "runtime_dir": tmp_path}
    try:
        with Session(engine) as db:
            users = {"gestor": _make_user(db, "m71@teste.local", "gestor")}
            exam, doc = _pending(db, users, slug="M71PG")
            before = _snapshot_requests(db, doc.id)
            document = _import(db, users, exam, **dirs)
            assert document.id == doc.id and document.state == "issued"
            assert db.query(FiscalDocument).filter_by(spirometry_exam_id=exam.id).count() == 1
            assert db.query(FiscalAttempt).filter_by(document_id=doc.id).one().operation == \
                IMPORT_OPERATION
            assert _snapshot_requests(db, doc.id) == before
            assert _import(db, users, exam, **dirs).id == doc.id
    finally:
        engine.dispose()
