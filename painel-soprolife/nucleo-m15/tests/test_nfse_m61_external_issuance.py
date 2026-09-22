"""M61 — importing an NFS-e this system did not issue, and never issuing it twice.

Two real NFS-e were issued by hand in production on 2026-09-15. The facts that
generated them are ordinary rows in the operational database, so the
automation would happily invoice them a second time. A duplicate NFS-e is not
a row you delete — it is a real fiscal document that has to be cancelled, with
its own rules and deadlines. This file pins the machinery that makes the
duplicate impossible instead.

The two access keys below are the real ones. They are fiscal identifiers, not
personal data, and anti-duplication is exactly what they are needed for.

Entirely offline and synthetic apart from those keys: no provider, no DPS, no
socket.
"""
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models import (FinancialEntry, FiscalArtifact, FiscalAttempt, FiscalDocument,
                        Person, SpirometryExam)
from app.services import nfse
from app.services.nfse_external_issuance import (
    EXTERNAL_PROVIDER,
    ExternalIssuance,
    IMPORT_OPERATION,
    already_imported,
    normalize_access_key,
    production_document_for,
    register_external_issuance,
)
from app.services.nfse_national.transport import TransportResponse
from app.services.nfse_validity import fiscal_validity

from tests.test_nfse_m59_production_wiring import (  # noqa: F401
    PRODUCTION_RECIPIENT_NAME, SYNTHETIC_CPF, certificate_file,
    fake_production_wire, production_settings, sefin_success)

# The two manually issued production NFS-e, in the bare 50-character form a
# portal shows.
MANUAL_A = "33045572263544026000110000000000000626096136136469"   # R$ 279,00
MANUAL_B = "33045572263544026000110000000000000726091190747856"   # R$ 219,00
MANUAL_ISSUED_ON = "2026-09-15"


def _synthetic_cpf(seed: int) -> str:
    """A distinct, structurally valid, entirely invented CPF per fixture.

    ``people.cpf`` is UNIQUE, so reusing one CPF across fixtures fails on the
    second insert. Generated rather than listed so no real number can creep in
    by being typed from memory.
    """
    base = [(seed * 7 + i * 3) % 10 for i in range(9)]
    if len(set(base)) == 1:                     # never a repeated-digit CPF
        base[0] = (base[0] + 1) % 10
    for _ in range(2):
        length = len(base)
        total = sum(base[i] * ((length + 1) - i) for i in range(length))
        remainder = (total * 10) % 11
        base.append(0 if remainder == 10 else remainder)
    return "".join(str(d) for d in base)


_CPF_SEED = iter(range(1, 500))


def _exam(db, users, *, slug, valor, broncodilatador=True):
    """An exam plus its financial entry — a fato gerador, invented data only."""
    person = Person(public_code=f"PES-M61{slug}"[:20],
                    nome_completo=PRODUCTION_RECIPIENT_NAME,
                    nome_normalizado=PRODUCTION_RECIPIENT_NAME.lower(),
                    cpf=_synthetic_cpf(next(_CPF_SEED)))
    db.add(person)
    db.flush()
    exam = SpirometryExam(public_code=f"ESP-M61{slug}"[:20], person_id=person.id,
                          status="Realizado", data_exame=date(2026, 9, 15),
                          data_exame_precisao="dia", modalidade="residencial",
                          broncodilatador=broncodilatador,
                          municipio_atendimento_ibge="3304557")
    db.add(exam)
    db.flush()
    db.add(FinancialEntry(public_code=f"LAN-M61{slug}"[:20], tipo="receita",
                          categoria="Espirometria", valor=Decimal(valor),
                          status="Recebido", spirometry_exam_id=exam.id,
                          data_competencia=date(2026, 9, 15)))
    db.commit()
    return exam


# --------------------------------------------------- a chave de acesso


def test_the_two_real_keys_normalize_to_tsidnfse():
    for bare in (MANUAL_A, MANUAL_B):
        assert len(bare) == 50
        normalized = normalize_access_key(bare)
        assert normalized == "NFS" + bare
        assert len(normalized) == 53


def test_an_already_prefixed_key_is_accepted_unchanged():
    assert normalize_access_key("NFS" + MANUAL_A) == "NFS" + MANUAL_A


def test_normalization_is_idempotent():
    once = normalize_access_key(MANUAL_A)
    assert normalize_access_key(once) == once


@pytest.mark.parametrize("bad", [
    None, "", "   ", 12345, MANUAL_A[:-1], MANUAL_A + "0",
    "NFS-NOT-A-REAL-KEY", "MOCK-11111111-1111-1111-1111-111111111111",
])
def test_a_malformed_key_is_refused(bad):
    with pytest.raises(HTTPException) as error:
        normalize_access_key(bad)
    assert error.value.detail["codigo"] == "external_access_key_malformed"


# --------------------------------------------------- o registo


def test_importing_records_an_issued_production_document(db, users):
    exam = _exam(db, users, slug="A", valor="279.00")
    document = register_external_issuance(
        db, ExternalIssuance(spirometry_exam_id=exam.id, access_key=MANUAL_A,
                             issued_on=MANUAL_ISSUED_ON, note="emitida manualmente"),
        users["gestor"].id)

    assert document.environment == "production"
    assert document.state == "issued"

    attempts = db.query(FiscalAttempt).filter_by(document_id=document.id).all()
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.operation == IMPORT_OPERATION
    assert attempt.provider == EXTERNAL_PROVIDER
    assert attempt.phase == "completed"
    assert attempt.outcome == "issued"
    assert attempt.reconciliation_required is False
    assert attempt.external_id == "NFS" + MANUAL_A


def test_no_artifact_is_invented(db, users):
    """We have the key and nothing else. Fabricating an nfse_xml would be
    inventing the government's own document."""
    exam = _exam(db, users, slug="B", valor="219.00")
    document = register_external_issuance(
        db, ExternalIssuance(exam.id, MANUAL_B, MANUAL_ISSUED_ON), users["gestor"].id)
    assert db.query(FiscalArtifact).filter_by(document_id=document.id).count() == 0


def test_an_imported_document_is_not_fiscally_valid(db, users):
    """Correctly, and on three independent counts of the M58 contract: the
    operation is not issue/reconcile, the provider is not 'production', and
    there is no nfse_xml. M58 needed no weakening for this."""
    exam = _exam(db, users, slug="C", valor="279.00")
    document = register_external_issuance(
        db, ExternalIssuance(exam.id, MANUAL_A, MANUAL_ISSUED_ON), users["gestor"].id)
    assert fiscal_validity(db, document) is False
    assert nfse.serialize_document(db, document)["fiscal_validity"] is False


def test_the_import_is_visible_as_an_import_not_an_issuance(db, users):
    """A human reading the trail must be able to tell that this system did not
    issue this note."""
    exam = _exam(db, users, slug="D", valor="279.00")
    document = register_external_issuance(
        db, ExternalIssuance(exam.id, MANUAL_A, MANUAL_ISSUED_ON), users["gestor"].id)
    attempt = db.query(FiscalAttempt).filter_by(document_id=document.id).one()
    assert attempt.operation != "issue"
    assert attempt.provider not in ("production", "restricted", "mock")


# --------------------------------------------------- anti-duplicação


def test_an_imported_fact_can_never_be_issued_again(db, users, production_settings,
                                                    fake_production_wire):
    """THE point of the whole mission. The fact already has a real NFS-e; a
    later issue must not produce a second one, and must not reach the
    provider at all."""
    exam = _exam(db, users, slug="E", valor="279.00")
    document = register_external_issuance(
        db, ExternalIssuance(exam.id, MANUAL_A, MANUAL_ISSUED_ON), users["gestor"].id)

    fake = fake_production_wire([TransportResponse(201, sefin_success())])
    again = nfse.operate(db, document.id, "issue", "m61-dup",
                         production_settings, users["gestor"].id)

    assert again.id == document.id
    assert again.state == "issued"
    assert fake.received == []          # no POST, no GET, nothing
    assert db.query(FiscalAttempt).filter_by(document_id=document.id).count() == 1


def test_a_second_production_document_for_the_same_exam_is_refused(db, users):
    exam = _exam(db, users, slug="F", valor="279.00")
    register_external_issuance(
        db, ExternalIssuance(exam.id, MANUAL_A, MANUAL_ISSUED_ON), users["gestor"].id)
    with pytest.raises(HTTPException) as error:
        register_external_issuance(
            db, ExternalIssuance(exam.id, MANUAL_B, MANUAL_ISSUED_ON), users["gestor"].id)
    assert error.value.detail["codigo"] == "production_document_already_exists_for_exam"


def test_the_same_key_cannot_be_attached_to_two_different_facts(db, users):
    """One NFS-e, one fato gerador. Attaching a real invoice to the wrong
    service is as bad as issuing a duplicate."""
    first = _exam(db, users, slug="G", valor="279.00")
    second = _exam(db, users, slug="H", valor="279.00")
    register_external_issuance(
        db, ExternalIssuance(first.id, MANUAL_A, MANUAL_ISSUED_ON), users["gestor"].id)
    with pytest.raises(HTTPException) as error:
        register_external_issuance(
            db, ExternalIssuance(second.id, MANUAL_A, MANUAL_ISSUED_ON), users["gestor"].id)
    assert error.value.detail["codigo"] == \
        "external_access_key_already_imported_for_another_document"


def test_re_registering_the_same_pair_is_idempotent(db, users):
    """A re-run after an interruption must be safe."""
    exam = _exam(db, users, slug="I", valor="279.00")
    first = register_external_issuance(
        db, ExternalIssuance(exam.id, MANUAL_A, MANUAL_ISSUED_ON), users["gestor"].id)
    again = register_external_issuance(
        db, ExternalIssuance(exam.id, MANUAL_A, MANUAL_ISSUED_ON), users["gestor"].id)
    assert again.id == first.id
    assert db.query(FiscalAttempt).filter_by(document_id=first.id).count() == 1
    assert db.query(FiscalDocument).filter_by(spirometry_exam_id=exam.id).count() == 1


def test_an_unknown_exam_is_refused(db, users):
    with pytest.raises(HTTPException) as error:
        register_external_issuance(
            db, ExternalIssuance("nao-existe", MANUAL_A, MANUAL_ISSUED_ON),
            users["gestor"].id)
    assert error.value.detail["codigo"] == "external_issuance_exam_not_found"


def test_the_lookup_helpers_agree_with_what_was_written(db, users):
    exam = _exam(db, users, slug="J", valor="219.00")
    assert already_imported(db, MANUAL_B) is None
    assert production_document_for(db, exam.id) is None
    document = register_external_issuance(
        db, ExternalIssuance(exam.id, MANUAL_B, MANUAL_ISSUED_ON), users["gestor"].id)
    assert already_imported(db, MANUAL_B).document_id == document.id
    assert production_document_for(db, exam.id).id == document.id


def test_both_manual_notes_can_coexist_on_different_facts(db, users):
    a = _exam(db, users, slug="K", valor="279.00")
    b = _exam(db, users, slug="L", valor="219.00")
    first = register_external_issuance(
        db, ExternalIssuance(a.id, MANUAL_A, MANUAL_ISSUED_ON), users["gestor"].id)
    second = register_external_issuance(
        db, ExternalIssuance(b.id, MANUAL_B, MANUAL_ISSUED_ON), users["gestor"].id)
    assert first.id != second.id
    keys = {r.external_id for r in db.query(FiscalAttempt)
            .filter_by(operation=IMPORT_OPERATION).all()}
    assert keys == {"NFS" + MANUAL_A, "NFS" + MANUAL_B}


# --------------------------------------------------- auditoria sem PII


def test_the_audit_entry_carries_the_key_but_no_personal_data(db, users):
    from app.models import AuditLog

    exam = _exam(db, users, slug="M", valor="279.00")
    cpf = db.get(Person, exam.person_id).cpf
    register_external_issuance(
        db, ExternalIssuance(exam.id, MANUAL_A, MANUAL_ISSUED_ON,
                             note="nome do paciente que NUNCA pode ser auditado"),
        users["gestor"].id)
    row = db.query(AuditLog).filter_by(acao="fiscal.external_issuance_imported").one()
    serialized = str(row.detalhes)
    assert "NFS" + MANUAL_A in serialized          # fiscal identifier: wanted
    assert PRODUCTION_RECIPIENT_NAME not in serialized
    assert cpf not in serialized
    # And the free-text note never reaches the trail at all.
    assert "NUNCA pode ser auditado" not in serialized
    assert "note" not in row.detalhes
    assert row.detalhes["fiscal_validity_claimed"] is False
    assert row.detalhes["nfse_xml_present"] is False


# --------------------------------------------------- a migração


def test_the_import_operation_is_admitted_and_the_others_are_kept(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    url = f"sqlite:///{tmp_path}/m61.db"
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    with engine.connect() as conn:
        ddl = conn.scalar(text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='fiscal_attempts'"))
        triggers = {r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND tbl_name='fiscal_attempts'")).all()}
    check = next(line for line in ddl.splitlines() if "operation IN" in line)
    for operation in ("issue", "reconcile", "cancel", "import"):
        assert f"'{operation}'" in check
    # The SQLite table rebuild drops triggers; losing the append-only guarantee
    # on fiscal evidence would be far worse than the problem being solved.
    assert triggers == {"fiscal_attempts_no_update", "fiscal_attempts_no_delete"}
    engine.dispose()


def test_the_head_is_the_m61_revision(tmp_path, monkeypatch):
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["c4e8b1f37a92"]
    assert script.get_revision("c4e8b1f37a92").down_revision == "a7d2c95e4f13"


# --------------------------------------------------- FASE 9: nada de rede


def _exploding_client(*args, **kwargs):  # pragma: no cover - must never run
    raise AssertionError("httpx.Client foi construído — houve tentativa de rede")


def test_with_the_imports_in_place_nothing_reaches_the_network(
        db, users, production_settings, monkeypatch):
    """Everything M60 prepared, plus M61's imports, and still no socket.

    ``production_settings`` has the production network gate OPEN — the
    configuration one environment variable would produce — and the real
    transport is used, not a fake.
    """
    import httpx
    from app.services.nfse_national import clock as clock_module

    exam = _exam(db, users, slug="N", valor="279.00")
    document = register_external_issuance(
        db, ExternalIssuance(exam.id, MANUAL_A, MANUAL_ISSUED_ON), users["gestor"].id)

    monkeypatch.setattr(clock_module, "read_clock_status",
                        lambda: clock_module.ClockStatus(True, "clock_synchronized"))
    monkeypatch.setattr(httpx, "Client", _exploding_client)

    # The imported document short-circuits before the provider is resolved.
    again = nfse.operate(db, document.id, "issue", "m61-nonet",
                         production_settings, users["gestor"].id)
    assert again.state == "issued"
    assert fiscal_validity(db, again) is False


def test_the_gate_closed_case_still_refuses_first(db, users, production_settings,
                                                  monkeypatch):
    """With the SHIPPED value of the network flag, a fresh production fact
    refuses at get_provider — before a document is even read."""
    import httpx
    monkeypatch.setattr(httpx, "Client", _exploding_client)
    closed = production_settings.model_copy(
        update={"nfse_production_network_enabled": False})
    exam = _exam(db, users, slug="O", valor="279.00")
    with pytest.raises(HTTPException) as error:
        nfse.operate(db, exam.id, "issue", "m61-closed", closed, users["gestor"].id)
    assert error.value.detail["codigo"] == "production_network_gate_disabled"
