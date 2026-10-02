"""M70 — Pastore exams issue SoproLife's own share (partnership rule) from the Command Center.

Synthetic data only: invented names, check-digit-valid synthetic CPFs, a
throwaway certificate and an ``httpx.MockTransport`` where SEFIN would be. No
test can reach the network (the M66 worker harness is reused unchanged).

What must hold:

- PASTORE is a fiscal flow only through structured IDs (canonical partner,
  its unit, the partnership rule in force on the service date) and only for
  the validated variant (with bronchodilator);
- the amount shown, stored, re-checked and sent is the partnership's
  ``valor_recebido_por_exame`` — never the gross price, never a ledger entry;
- the tomador is the patient and the description never mentions the split;
- activation is prospective: an exam before the PASTORE policy never gets a
  button, so a manual NFS-e this database does not know about cannot be
  duplicated;
- issuance creates no FinancialEntry and touches no PartnerSettlement;
- DIRECT/HOME are byte-identical to pre-M70.
"""
import base64
import gzip
import json
from datetime import date
from decimal import Decimal

import pytest
from lxml import etree
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.models import (FinancialEntry, FiscalDocument, FiscalIssuanceRequest, FiscalPreparation,
                        Partner, PartnerSettlement, PartnerSettlementItem, Partnership,
                        PartnerUnit, Person, SpirometryExam)
from app.services import nfse
from app.services import nfse_production_issuance as production
from app.services import partner_pricing
from app.services.idempotency import payload_fingerprint
from app.services.nfse_external_issuance import ExternalIssuance, register_external_issuance
from app.services.nfse_national import fiscal_config
from app.services.nfse_national.production_profile import (PASTORE_DIFFERS_FROM_DIRECT,
                                                           PASTORE_POLICY_EFFECTIVE_FROM,
                                                           PRODUCTION_EFFECTIVE_FROM,
                                                           pastore_production_policy,
                                                           production_configuration,
                                                           production_policies,
                                                           production_policy)
from tests.test_nfse_m66_production_command_center import (  # noqa: F401 — fixtures
    GOOD_NAME, _code, _count, _methods, _request_row, envelope, prod_env, spool,
    synthetic_cpf, worker)

PASTORE_DAY = PASTORE_POLICY_EFFECTIVE_FROM          # first day PASTORE may issue
BEFORE_ACTIVATION = date(2026, 9, 29)                # like ESP-000056/57 in production
DIRECT_DAY = date(2026, 9, 16)
SHARE = Decimal("109.50")
NS = "{http://www.sped.fazenda.gov.br/nfse}"

# The keys evaluate() returned before M70. DIRECT/HOME must still return
# exactly these — that is what keeps their stored fingerprints valid.
PRE_M70_KEYS = {'financial_entry_id', 'policy_id', 'recipient_person_id', 'flow',
                'service_date', 'competence', 'service_municipio_ibge', 'amount_snapshot',
                'description', 'blocking_reasons'}


@pytest.fixture
def world(db, users):
    """Production profile, DIRECT/HOME + PASTORE policies, a canonical Pastore
    with one active unit and the rule R$ 109,50/exame, and one exam per
    situation. ``ready`` is a DIRECT exam so the M66 worker harness works."""
    admin = users["admin"].id
    for policy in production_policies():
        nfse.create_policy(db, policy, admin)
    nfse.create_policy(db, pastore_production_policy(), admin)
    fiscal_config.create_version(db, environment="production",
                                 effective_from=PRODUCTION_EFFECTIVE_FROM,
                                 validation_state="validated",
                                 configuration=production_configuration(), actor=admin)
    pastore = Partner(public_code="CLI-M70A", nome="Pastore", tipo="clinica", status="ativa")
    other = Partner(public_code="CLI-M70B", nome="Clínica Exemplo", tipo="clinica", status="ativa")
    db.add_all([pastore, other])
    db.flush()
    unit = PartnerUnit(public_code="UNI-M70A", partner_id=pastore.id, nome="Unidade Exemplo", ativo=True)
    unit2 = PartnerUnit(public_code="UNI-M70C", partner_id=pastore.id, nome="Unidade Dois", ativo=True)
    other_unit = PartnerUnit(public_code="UNI-M70B", partner_id=other.id, nome="Unidade Outra", ativo=True)
    db.add_all([unit, unit2, other_unit])
    db.flush()
    rule = Partnership(public_code="PAR-M70A", partner_id=pastore.id, status="em_negociacao",
                       modelo_recebimento="valor_por_exame", valor_recebido_por_exame=SHARE,
                       vigencia_inicio=date(2026, 7, 1),
                       # Money that LEAVES SoproLife: must never touch the invoice.
                       modelo_repasse="percentual", percentual_repasse=Decimal("50.00"))
    db.add(rule)
    db.flush()
    exams = {"_pastore": pastore, "_unit": unit, "_unit2": unit2, "_rule": rule}

    def make(label, code, *, nome=GOOD_NAME, cpf="auto", municipio="3304557", day=PASTORE_DAY,
             modalidade="clinica_parceira", partner=pastore, partner_unit=unit, bd=True,
             status="Realizado", entries=()):
        person = Person(public_code=f"PES-{code[4:]}", nome_completo=nome,
                        nome_normalizado=(nome or "").lower(),
                        cpf=synthetic_cpf(len(exams) + 70) if cpf == "auto" else cpf)
        db.add(person)
        db.flush()
        exam = SpirometryExam(public_code=code, person_id=person.id, status=status,
                              data_exame=day, data_exame_precisao="dia", modalidade=modalidade,
                              broncodilatador=bd, municipio_atendimento_ibge=municipio,
                              partner_id=partner.id if partner else None,
                              partner_unit_id=partner_unit.id if partner_unit else None)
        db.add(exam)
        db.flush()
        for n, (tipo, valor) in enumerate(entries):
            db.add(FinancialEntry(public_code=f"LAN-{code[4:]}-{n}", tipo=tipo,
                                  categoria="Espirometria" if tipo == "receita" else "Repasse médico",
                                  valor=Decimal(valor), status="Recebido",
                                  spirometry_exam_id=exam.id, data_competencia=day))
        db.commit()
        exams[label] = exam
        return exam

    make("ready", "ESP-070001", modalidade="cowork", partner=None, partner_unit=None,
         day=DIRECT_DAY, entries=(("receita", "220.00"),))
    make("home", "ESP-070002", modalidade="residencial", partner=None, partner_unit=None,
         day=DIRECT_DAY, entries=(("receita", "250.00"),))
    make("pastore", "ESP-070010")
    make("pastore_with_expense", "ESP-070011", entries=(("despesa", "40.00"),))
    make("pastore_no_cpf", "ESP-070012", cpf=None)
    make("pastore_no_municipio", "ESP-070013", municipio=None)
    make("pastore_not_performed", "ESP-070014", status="Aguardando")
    make("pastore_without_bd", "ESP-070015", bd=False)
    make("pastore_bd_unknown", "ESP-070016", bd=None)
    make("pastore_before_activation", "ESP-070017", day=BEFORE_ACTIVATION)
    make("pastore_own_revenue", "ESP-070018", entries=(("receita", "219.00"),))
    make("pastore_no_link", "ESP-070019", partner=None, partner_unit=None)
    make("other_partner", "ESP-070020", partner=other, partner_unit=other_unit)
    make("pastore_imported", "ESP-070021")
    return exams


def _eval(db, exam):
    return nfse.evaluate(db, exam.id, "production", for_update=False)


def _queue(client, auth):
    response = client.get("/api/v1/fiscal/producao/fila", headers=auth("gestor"))
    assert response.status_code == 200, response.text
    return response, {row["exam_code"]: row for row in response.json()["itens"]}


def _sent_dps(worker):
    body = json.loads(worker["sefin"]["requests"][0][2])
    return gzip.decompress(base64.b64decode(body["dpsXmlGZipB64"]))


# ------------------------------------------------------------- eligibility

def test_direct_and_home_are_byte_identical_to_pre_m70(db, world):
    for label, flow, amount in (("ready", "DIRECT", "220.00"), ("home", "HOME", "250.00")):
        data = _eval(db, world[label])
        assert set(data) == PRE_M70_KEYS, label
        assert data["flow"] == flow and data["blocking_reasons"] == []
        assert data["amount_snapshot"] == Decimal(amount)


def test_pastore_with_bd_is_ready_for_its_share(db, world):
    data = _eval(db, world["pastore"])
    assert data["blocking_reasons"] == []
    assert data["flow"] == "PASTORE"
    assert data["amount_snapshot"] == SHARE
    assert data["financial_entry_id"] is None
    assert data["amount_source"] == "partnership.valor_recebido_por_exame"
    assert data["partnership_id"] == world["_rule"].id
    assert data["partner_unit_id"] == world["_unit"].id


def test_partner_repasse_and_physician_expense_never_change_the_share(db, world):
    data = _eval(db, world["pastore_with_expense"])
    assert data["blocking_reasons"] == [] and data["amount_snapshot"] == SHARE


@pytest.mark.parametrize("label, reason", [
    ("pastore_no_cpf", "recipient_cpf_missing"),
    ("pastore_no_municipio", "service_location_missing"),
    ("pastore_not_performed", "service_not_performed"),
    ("pastore_without_bd", "partner_service_variant_unsupported"),
    ("pastore_bd_unknown", "partner_service_variant_unsupported"),
    ("pastore_before_activation", "partner_before_fiscal_activation"),
    ("pastore_own_revenue", "partner_amount_source_conflict"),
])
def test_pastore_blockers(db, world, label, reason):
    data = _eval(db, world[label])
    assert reason in data["blocking_reasons"], data["blocking_reasons"]
    assert data["amount_snapshot"] is None


def test_another_partner_and_a_partner_exam_without_links_stay_blocked(db, world):
    for label in ("other_partner", "pastore_no_link"):
        data = _eval(db, world[label])
        assert data["flow"] == "PARTNER", label
        assert "commercial_flow_unsupported" in data["blocking_reasons"]
        assert data["amount_snapshot"] is None


def test_classification_is_by_id_never_by_text(db, world):
    exam = world["other_partner"]
    exam.local_atendimento = "Pastore Ipanema"
    exam.observacao = "Pastore"
    db.commit()
    assert _eval(db, exam)["flow"] == "PARTNER"


def test_no_rule_in_force_on_the_service_date_blocks(db, world):
    world["_rule"].vigencia_inicio = date(2026, 10, 3)   # starts after the exam
    db.commit()
    assert "partner_rule_missing" in _eval(db, world["pastore"])["blocking_reasons"]


def test_an_undefined_newer_rule_blocks_instead_of_falling_back(db, world):
    db.add(Partnership(public_code="PAR-M70B", partner_id=world["_pastore"].id, status="ativa",
                       modelo_recebimento="indefinido", vigencia_inicio=date(2026, 9, 1)))
    db.commit()
    assert "partner_rule_missing" in _eval(db, world["pastore"])["blocking_reasons"]


def test_two_rules_starting_the_same_day_are_ambiguous(db, world):
    db.add(Partnership(public_code="PAR-M70C", partner_id=world["_pastore"].id, status="ativa",
                       modelo_recebimento="valor_por_exame",
                       valor_recebido_por_exame=Decimal("100.00"), vigencia_inicio=date(2026, 7, 1)))
    db.commit()
    data = _eval(db, world["pastore"])
    assert "partner_rule_ambiguous" in data["blocking_reasons"]
    assert data["amount_snapshot"] is None


def test_the_fiscal_resolver_uses_the_day_not_the_month(db, world):
    rule = world["_rule"]
    rule.vigencia_inicio = date(2026, 10, 15)
    db.commit()
    # The monthly forecast accepts a rule starting inside the month...
    assert partner_pricing.resolve_valor_por_exame(
        db, world["_pastore"], competencia=date(2026, 10, 1)).cadastrada
    # ...one invoice for an exam on the 2nd does not.
    assert partner_pricing.resolve_regra_fiscal_por_exame(
        db, world["_pastore"], PASTORE_DAY).situacao == partner_pricing.FISCAL_SEM_REGRA


def test_an_inactive_or_foreign_unit_blocks(db, world):
    world["_unit"].ativo = False
    db.commit()
    assert "partner_unit_invalid" in _eval(db, world["pastore"])["blocking_reasons"]


def test_without_a_pastore_policy_nothing_is_issuable(db, users):
    # Only DIRECT/HOME exist: a Pastore exam has no policy at all.
    from app.models import FiscalPolicy
    assert db.scalar(select(func.count()).select_from(FiscalPolicy)) == 0
    for policy in production_policies():
        nfse.create_policy(db, policy, users["admin"].id)
    partner = Partner(public_code="CLI-M70Z", nome="Pastore", tipo="clinica", status="ativa")
    db.add(partner)
    db.flush()
    unit = PartnerUnit(public_code="UNI-M70Z", partner_id=partner.id, nome="U", ativo=True)
    db.add(unit)
    db.add(Partnership(public_code="PAR-M70Z", partner_id=partner.id, status="ativa",
                       modelo_recebimento="valor_por_exame", valor_recebido_por_exame=SHARE,
                       vigencia_inicio=date(2026, 7, 1)))
    person = Person(public_code="PES-M70Z", nome_completo=GOOD_NAME,
                    nome_normalizado=GOOD_NAME.lower(), cpf=synthetic_cpf(7070))
    db.add(person)
    db.flush()
    exam = SpirometryExam(public_code="ESP-070099", person_id=person.id, status="Realizado",
                          data_exame=PASTORE_DAY, data_exame_precisao="dia",
                          modalidade="clinica_parceira", broncodilatador=True,
                          municipio_atendimento_ibge="3304557", partner_id=partner.id,
                          partner_unit_id=unit.id)
    db.add(exam)
    db.commit()
    assert "policy_missing" in _eval(db, exam)["blocking_reasons"]


# ---------------------------------------------------------------- policy

def test_the_pastore_policy_is_the_direct_tax_body_with_its_own_amount_basis():
    pastore = pastore_production_policy()
    direct = production_policy("DIRECT")
    a = pastore.configuration.model_dump(mode="json")
    b = direct.configuration.model_dump(mode="json")
    assert {k for k in a if a[k] != b[k]} == PASTORE_DIFFERS_FROM_DIRECT
    assert a["amount_basis"] == "partnership.valor_recebido_por_exame"
    # The tax facts of the manual NFS-e nº 3.
    assert (a["national_service_code"], a["municipal_service_code"], a["withholding"],
            a["municipality"]) == ("040201", "001", False, "3304557")
    assert pastore.flow == "PASTORE" and pastore.environment == "production"
    assert pastore.validation_state == "validated"
    assert pastore.effective_from == date(2026, 10, 2)   # prospective: the M70 deploy date
    assert "70000" not in json.dumps(a)                   # the manual Portal series never appears
    assert pastore.version not in {p.version for p in production_policies()}


def test_a_direct_policy_can_never_price_a_pastore_exam(db, world):
    assert nfse.AMOUNT_BASIS_BY_FLOW["PASTORE"] != nfse.AMOUNT_BASIS_BY_FLOW["DIRECT"]


# ------------------------------------------------------------ queue + modal

def test_the_queue_shows_pastore_ready_with_its_share(prod_env, client, auth, db, world):
    response, rows = _queue(client, auth)
    row = rows["ESP-070010"]
    assert (row["status"], row["status_label"], row["can_issue"]) == (
        "ready", "Pronto para emitir", True)
    assert (row["flow"], row["flow_label"], row["amount"], row["municipio_name"]) == (
        "PASTORE", "Pastore", "109.50", "Rio de Janeiro/RJ")
    assert row["amount_source"] == "partnership.valor_recebido_por_exame"
    assert "219" not in json.dumps(row)
    assert response.json()["batch_issuance_available"] is False
    old = rows["ESP-070017"]
    assert old["can_issue"] is False and old["block_category"] == "partner_before_fiscal_activation"
    assert old["block_label"] == "Pastore anterior à ativação fiscal — conferir nota manual"
    assert rows["ESP-070015"]["block_label"] == "Regra fiscal da parceria não definida"
    assert rows["ESP-070020"]["block_category"] == "blocked_by_partner_model"
    # No CPF anywhere in the listing.
    for exam in world.values():
        if isinstance(exam, SpirometryExam):
            person = db.get(Person, exam.person_id)
            if person.cpf:
                assert person.cpf not in response.text


def test_the_modal_shows_the_share_and_the_exact_phrase(prod_env, client, auth, db, world):
    response = client.post(f"/api/v1/fiscal/producao/exames/{world['pastore'].id}/preparar",
                           headers=auth("gestor"))
    assert response.status_code == 200, response.text
    summary = response.json()
    person = db.get(Person, world["pastore"].person_id)
    assert summary["can_confirm"] is True
    assert summary["exam_code"] == "ESP-070010"
    assert summary["flow_label"] == "Pastore"
    assert summary["amount_title"] == "Valor da NFS-e SoproLife"
    assert (summary["amount"], summary["amount_label"]) == ("109.50", "R$ 109,50")
    assert summary["confirmation_phrase"] == "Confirmar emissão de R$ 109,50"
    assert summary["cpf_masked"] == f"***.***.***-{person.cpf[-2:]}"
    assert person.cpf not in json.dumps(summary)
    description = summary["service_description"]
    assert description.startswith("Espirometria com broncodilatador, com emissão de laudo médico")
    for word in ("Pastore", "50%", "parceria", "comissão", "repasse"):
        assert word.lower() not in description.lower()
    prep = db.get(FiscalPreparation, summary["preparation_id"])
    assert prep.amount_snapshot == SHARE and prep.financial_entry_id is None
    assert prep.partnership_id == world["_rule"].id


def test_only_gestor_and_admin_prepare_a_pastore_exam(prod_env, client, auth, world):
    for role in ("leitura", "operacional"):
        response = client.post(f"/api/v1/fiscal/producao/exames/{world['pastore'].id}/preparar",
                               headers=auth(role))
        assert response.status_code == 403


def test_a_historic_exam_can_be_prepared_but_never_confirmed(prod_env, client, auth, db, world):
    summary = client.post(
        f"/api/v1/fiscal/producao/exames/{world['pastore_before_activation'].id}/preparar",
        headers=auth("gestor")).json()
    assert summary["can_confirm"] is False and summary["confirmation_phrase"] is None
    assert summary["amount"] is None


def test_an_imported_manual_nfse_never_gets_a_button(prod_env, client, auth, db, world, users):
    key = "33045572263544026000110000000000000326084782697636"   # the shape of nº 3
    register_external_issuance(db, ExternalIssuance(world["pastore_imported"].id, key,
                                                    str(PASTORE_DAY)), users["admin"].id)
    _, rows = _queue(client, auth)
    row = rows["ESP-070021"]
    assert row["status"] == "imported" and row["can_issue"] is False
    response = client.post(
        f"/api/v1/fiscal/producao/exames/{world['pastore_imported'].id}/preparar",
        headers=auth("gestor"))
    assert response.status_code == 409


# ---------------------------------------------------------------- worker

def _settlement_snapshot(db):
    db.expire_all()
    return (sorted((s.id, s.status, str(s.valor_total), s.sequencia)
                   for s in db.scalars(select(PartnerSettlement)).all()),
            db.scalar(select(func.count()).select_from(PartnerSettlementItem)))


def test_the_worker_issues_one_pastore_invoice_for_the_share(worker, db, world):
    db.add(PartnerSettlement(
        partner_id=world["_pastore"].id, partner_unit_id=world["_unit"].id,
        competencia=date(2026, 10, 1), periodo_inicio=date(2026, 10, 1),
        periodo_fim=date(2026, 10, 31), status="incluido", sequencia=1))
    db.commit()
    # Read the "before" state in its own short session: expiring the test
    # session here would make the harness refresh lazily and hold a SQLite
    # read lock across the worker (the M47 lesson).
    with worker["maker"]() as session:
        entries_before = session.scalar(select(func.count()).select_from(FinancialEntry))
        settlements_before = _settlement_snapshot(session)

    request = worker["confirm"]("pastore", key="m70-worker-0001")
    assert request["amount_confirmed"] == "109.50"
    worker["sefin"]["queue"] = [(201, envelope())]
    report = worker["run"](request["id"])
    assert _methods(worker["sefin"]) == ["POST"]
    assert report["status"] == "issued" and report["provider_post_count"] == 1

    sent = _sent_dps(worker)
    summary = worker["module"].dps_summary(sent)
    assert summary["vServ"] == "109.50"
    assert summary["serie"] != "70000" and summary["dCompet"] == PASTORE_DAY.isoformat()
    root = etree.fromstring(sent)
    person = db.get(Person, world["pastore"].person_id)
    assert root.find(f".//{NS}toma/{NS}CPF").text == person.cpf      # tomador = the patient
    description = root.find(f".//{NS}xDescServ").text
    assert "broncodilatador" in description
    for word in ("Pastore", "50%", "parceria", "219"):
        assert word.lower() not in description.lower()
    assert b"219.00" not in sent

    # Fiscal evidence only: no new revenue, no settlement touched.
    assert _count(db, FinancialEntry) == entries_before
    assert _settlement_snapshot(db) == settlements_before


def test_a_pastore_request_never_runs_twice_and_never_posts_twice(worker, db):
    request = worker["confirm"]("pastore", key="m70-worker-0002")
    worker["sefin"]["queue"] = [(201, envelope())]
    worker["run"](request["id"])
    worker["run"](request["id"])
    assert _methods(worker["sefin"]) == ["POST"]
    assert _request_row(worker, request["id"]).provider_post_count == 1
    # A second confirmation for the same, now issued, document is refused.
    with pytest.raises(Exception):
        worker["confirm"]("pastore", key="m70-worker-0002b")


def test_an_uncertain_pastore_outcome_is_reconciled_with_get_only(worker):
    import httpx
    request = worker["confirm"]("pastore", key="m70-worker-0003")
    worker["sefin"]["queue"] = [httpx.ReadTimeout("t"), (404, b"{}"), (404, b"{}")]
    report = worker["run"](request["id"])
    methods = _methods(worker["sefin"])
    assert methods[0] == "POST" and methods.count("POST") == 1
    assert set(methods[1:]) <= {"GET"}
    assert report["provider_post_count"] == 1


@pytest.mark.parametrize("change, expected", [
    ("rule_value", {"eligibility_now_blocked", "preparation_stale", "partner_amount_changed",
                    "amount_mismatch"}),
    ("unit", {"eligibility_now_blocked", "preparation_stale", "partner_changed"}),
    ("new_rule", {"eligibility_now_blocked", "preparation_stale", "partner_rule_changed"}),
])
def test_a_rule_partner_or_amount_change_after_confirmation_refuses_before_post(
        worker, db, world, change, expected):
    request = worker["confirm"]("pastore", key=f"m70-worker-{change}")
    if change == "rule_value":
        world["_rule"].valor_recebido_por_exame = Decimal("110.00")
    elif change == "unit":
        world["pastore"].partner_unit_id = world["_unit2"].id
    else:
        db.add(Partnership(public_code="PAR-M70N", partner_id=world["_pastore"].id, status="ativa",
                           modelo_recebimento="valor_por_exame",
                           valor_recebido_por_exame=SHARE, vigencia_inicio=date(2026, 9, 1)))
    db.commit()
    report = worker["run"](request["id"])
    assert worker["sefin"]["requests"] == []
    assert report["refused"] in expected
    row = _request_row(worker, request["id"])
    assert row.status == "refused" and row.provider_post_count == 0


def test_worker_explicit_guards_name_the_partner_fact(worker, db, world):
    """Independently of the fingerprint: the worker's own PASTORE checks."""
    request = worker["confirm"]("pastore", key="m70-worker-guards")
    world["_rule"].valor_recebido_por_exame = Decimal("110.00")
    db.commit()
    with worker["maker"]() as session:
        facts = {"document_id": request["document_id"], "kind": "issue",
                 "exam_id": world["pastore"].id,
                 "preparation_id": None, "preparation_fingerprint": None,
                 "recipient_fingerprint": None, "amount": SHARE}
        row = session.get(FiscalIssuanceRequest, request["id"])
        facts.update(preparation_id=row.preparation_id,
                     preparation_fingerprint=row.preparation_fingerprint,
                     recipient_fingerprint=row.recipient_fingerprint)
        from app.config import get_settings
        failures, _ = worker["module"].guards(session, facts, get_settings())
    assert "partner_amount_changed" in failures


# --------------------------------------------------------------- database

def test_the_database_refuses_a_partner_amount_on_a_non_pastore_or_double_source(db, world, users):
    data = _eval(db, world["pastore"])
    doc = FiscalDocument(spirometry_exam_id=world["pastore"].id, environment="production",
                         created_by=users["admin"].id, idempotency_key="m70-db-1",
                         idempotency_fingerprint="x" * 64)
    db.add(doc)
    db.flush()
    base = dict(data, document_id=doc.id, created_by=users["admin"].id,
                fingerprint=payload_fingerprint(data))
    db.add(FiscalPreparation(**dict(base, flow="DIRECT")))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_the_m70_migration_on_sqlite_keeps_triggers_and_old_rows(tmp_path):
    from alembic import command
    from sqlalchemy import create_engine
    from tests.test_nfse_migrations import config as alembic_config

    url = f"sqlite:///{tmp_path / 'm70.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "e8b3d6a4f190")
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.connect() as conn:
        triggers = {r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='fiscal_preparations'"))}
        assert triggers == {"fiscal_preparations_no_update", "fiscal_preparations_no_delete"}
        indexes = {r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='fiscal_preparations'"))}
        assert "ix_fiscal_preparations_document_id" in indexes
        ddl = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE name='fiscal_preparations'")).scalar_one()
        assert "partnership.valor_recebido_por_exame" in ddl
    command.downgrade(cfg, "e8b3d6a4f190")
    with engine.connect() as conn:
        ddl = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE name='fiscal_preparations'")).scalar_one()
        assert "partnership_id" not in ddl
        triggers = {r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='fiscal_preparations'"))}
        assert len(triggers) == 2
    command.upgrade(cfg, "head")


from tests.test_nfse_migrations import postgres_url  # noqa: E402,F401


def test_the_m70_migration_on_postgresql(postgres_url):  # noqa: F811
    from alembic import command
    from sqlalchemy import create_engine
    from tests.test_nfse_migrations import config as alembic_config

    cfg = alembic_config(postgres_url)
    command.upgrade(cfg, "head")
    command.check(cfg)
    engine = create_engine(postgres_url)
    with engine.connect() as conn:
        check = conn.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'ck_fiscal_preparations_fiscal_snapshot_source'")).scalar_one()
        assert "partnership.valor_recebido_por_exame" in check
        trigger = conn.execute(text(
            "SELECT count(*) FROM pg_trigger WHERE tgname = 'fiscal_preparations_immutable'")).scalar_one()
        assert trigger == 1
    command.downgrade(cfg, "e8b3d6a4f190")
    with engine.connect() as conn:
        check = conn.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'ck_fiscal_preparations_fiscal_snapshot_source'")).scalar_one()
        assert "partnership" not in check
    command.upgrade(cfg, "head")
    command.check(cfg)
    engine.dispose()
