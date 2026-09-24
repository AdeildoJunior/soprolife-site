"""M66 — production NFS-e from the Command Center: button -> confirmation -> one-shot worker.

Everything here is synthetic: invented names, check-digit-valid synthetic
CPFs, a throwaway certificate, and an ``httpx.MockTransport`` standing where
SEFIN would be. No test here can reach the network: the worker's own guard
refuses to build an HTTP client outside its transport, and the transport's
client constructor is replaced by a mock-backed one.

What must hold:

- the queue says WHY a fact cannot be issued, in the operator's words, and
  creates nothing while listing;
- only gestor/admin can prepare or confirm, and production has no batch;
- a confirmation must echo the exact preparation, amount and phrase shown;
- the worker makes at most one POST per confirmed request, re-checks every
  fact first, reconciles an unknown outcome with GETs only, never retries a
  rejection, and never runs the same request (or document) twice;
- no password and no full CPF reaches an API response, the audit trail, the
  request row or the worker's report.
"""
import base64
import gzip
import importlib.util
import json
import os
import stat
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.models import (AuditLog, FinancialEntry, FiscalAttempt, FiscalDocument,
                        FiscalIssuanceRequest, Person, SpirometryExam)
from app.services import nfse
from app.services import nfse_production_issuance as production
from app.services.nfse_external_issuance import ExternalIssuance, register_external_issuance
from app.services.nfse_national import clock as clock_module
from app.services.nfse_national import dispatch, fiscal_config
from app.services.nfse_national.production_profile import (PRODUCTION_EFFECTIVE_FROM,
                                                           production_configuration,
                                                           production_policies)
from app.services.nfse_national.signer import generate_synthetic_test_certificate

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "nfse_production_worker.py"
REAL_HTTPX_CLIENT = httpx.Client
SERVICE_DAY = date(2026, 9, 16)
ACCESS_KEY = "NFS" "3304557" "2" "2" "63544026000110" "0000000000001" "2609" "428247657" "6"
PROCESSED_AT = "2026-09-23T10:00:00.1234567-03:00"
GOOD_NAME = "Marina Rocha Albuquerque"
MANUAL_KEY = "33045572263544026000110000000000000426099130462655"


def synthetic_cpf(seed: int) -> str:
    """Check-digit-valid, distinct per seed, belongs to nobody we know of."""
    digits = [int(c) for c in f"{(seed * 104729 + 3141592) % 10**9:09d}"]
    for _ in range(2):
        n = len(digits)
        total = sum(digits[i] * ((n + 1) - i) for i in range(n))
        rest = (total * 10) % 11
        digits.append(0 if rest == 10 else rest)
    return "".join(map(str, digits))


def envelope(key=ACCESS_KEY, tp_amb=1):
    nfse_xml = ('<?xml version="1.0" encoding="UTF-8"?>'
                '<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse">'
                f'<infNFSe Id="{key}"></infNFSe></NFSe>').encode()
    return json.dumps({"tipoAmbiente": tp_amb, "versaoAplicativo": "SefinNacional_1.6.0",
                       "dataHoraProcessamento": PROCESSED_AT, "chaveAcesso": key[3:],
                       "nfseXmlGZipB64": base64.b64encode(gzip.compress(nfse_xml)).decode()
                       }).encode()


# ------------------------------------------------------------------ fixtures

@pytest.fixture
def spool(tmp_path):
    path = tmp_path / "spool"
    path.mkdir(mode=0o770)
    return path


@pytest.fixture
def prod_env(monkeypatch, spool):
    monkeypatch.setenv("M15_NFSE_ENABLED", "true")
    monkeypatch.setenv("M15_NFSE_ENVIRONMENT", "production")
    monkeypatch.setenv("M15_NFSE_PRODUCTION_WORKER_SPOOL_DIR", str(spool))
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def world(db, users):
    """Production profile and policies, plus one exam per situation the
    queue must explain. Returns {label: exam}."""
    admin = users["admin"].id
    for policy in production_policies():
        nfse.create_policy(db, policy, admin)
    fiscal_config.create_version(db, environment="production",
                                 effective_from=PRODUCTION_EFFECTIVE_FROM,
                                 validation_state="validated",
                                 configuration=production_configuration(), actor=admin)
    exams = {}

    def make(label, code, *, nome=GOOD_NAME, cpf="auto", municipio="3304557",
             modalidade="cowork", entries=(("220.00", "Recebido"),), seed=0):
        person = Person(public_code=f"PES-{code[4:]}", nome_completo=nome,
                        nome_normalizado=(nome or "").lower(),
                        cpf=synthetic_cpf(len(exams) + 11 + seed) if cpf == "auto" else cpf)
        db.add(person)
        db.flush()
        exam = SpirometryExam(public_code=code, person_id=person.id, status="Realizado",
                              data_exame=SERVICE_DAY, data_exame_precisao="dia",
                              modalidade=modalidade, broncodilatador=True,
                              municipio_atendimento_ibge=municipio)
        db.add(exam)
        db.flush()
        for n, (valor, status) in enumerate(entries):
            # The database already refuses a second "Espirometria" revenue per
            # exam (uq_financial_entries_receita_espirometria). A legacy
            # spelling with an invisible character slips past that index but
            # not past the ledger's own normalization — which is exactly the
            # duplicate evaluate() must still catch.
            categoria = "Espirometria" if n == 0 else "Espirometria\u200b"
            db.add(FinancialEntry(public_code=f"LAN-{code[4:]}-{n}", tipo="receita",
                                  categoria=categoria, valor=Decimal(valor), status=status,
                                  spirometry_exam_id=exam.id, data_competencia=SERVICE_DAY))
        db.commit()
        exams[label] = exam
        return exam

    make("ready", "ESP-066001")
    make("reserve", "ESP-000051")
    make("no_cpf", "ESP-066002", cpf=None)
    make("bad_cpf", "ESP-066003", cpf="52998224724")
    make("repeated_cpf", "ESP-066004", cpf="11111111111")
    make("short_name", "ESP-066005", nome="Marina")
    make("placeholder", "ESP-066006", nome="Paciente Teste Silva")
    make("no_municipio", "ESP-066007", municipio=None)
    make("no_entry", "ESP-066008", entries=())
    make("two_entries", "ESP-066009", entries=(("220.00", "Recebido"), ("220.00", "Recebido")))
    make("not_received", "ESP-066010", entries=(("220.00", "Pendente"),))
    make("pastore", "ESP-066011", modalidade="clinica_parceira")
    imported = make("imported", "ESP-066012")
    register_external_issuance(db, ExternalIssuance(imported.id, MANUAL_KEY, str(SERVICE_DAY)),
                               admin)
    return exams


def _code(response):
    """The API's error envelope: {"erro": {"mensagem": {"codigo": ...}}}."""
    return response.json()["erro"]["mensagem"]["codigo"]


def _count(db, model, *where):
    db.expire_all()
    return db.scalar(select(func.count()).select_from(model).where(*where))


def _confirm_payload(summary, **overrides):
    payload = {"preparation_id": summary["preparation_id"], "amount": summary["amount"],
               "confirmation": summary["confirmation_phrase"], "idempotency_key": "m66-test-0001"}
    payload.update(overrides)
    return payload


def _prepare(client, auth, exam, role="gestor"):
    response = client.post(f"/api/v1/fiscal/producao/exames/{exam.id}/preparar", headers=auth(role))
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------------- the queue

def test_the_queue_explains_every_blocker_and_creates_nothing(prod_env, client, auth, db, world):
    documents_before = _count(db, FiscalDocument)
    response = client.get("/api/v1/fiscal/producao/fila", headers=auth("gestor"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["batch_issuance_available"] is False
    rows = {row["exam_code"]: row for row in body["itens"]}

    ready = rows["ESP-066001"]
    assert ready["status"] == "ready" and ready["status_label"] == "Pronto para emitir"
    assert ready["can_issue"] is True
    assert (ready["amount"], ready["flow"], ready["municipio_name"]) == (
        "220.00", "DIRECT", "Rio de Janeiro/RJ")
    assert ready["patient_name"] == GOOD_NAME

    identity = {"ESP-066002": "recipient_cpf_missing",
                "ESP-066003": "recipient_cpf_check_digits_invalid",
                "ESP-066004": "recipient_cpf_repeated_digits",
                "ESP-066005": "recipient_name_not_a_full_name",
                "ESP-066006": "recipient_name_looks_like_placeholder"}
    for code, reason in identity.items():
        row = rows[code]
        assert row["status"] == "blocked" and row["can_issue"] is False, code
        assert reason in row["blocking_reasons"], (code, row["blocking_reasons"])
        assert row["block_label"] == "Dados fiscais do paciente incompletos", code

    assert "service_location_missing" in rows["ESP-066007"]["blocking_reasons"]
    assert rows["ESP-066007"]["block_label"] == "Município de prestação não registrado"
    assert "financial_entry_missing" in rows["ESP-066008"]["blocking_reasons"]
    assert "financial_entry_ambiguous" in rows["ESP-066009"]["blocking_reasons"]
    assert ("financial_revenue_not_received_or_invalid"
            in rows["ESP-066010"]["blocking_reasons"])
    pastore = rows["ESP-066011"]
    assert "commercial_flow_unsupported" in pastore["blocking_reasons"]
    assert pastore["block_category"] == "blocked_by_partner_model"
    assert rows["ESP-066012"]["status"] == "imported"
    assert rows["ESP-066012"]["can_issue"] is False

    # Listing is read-only: not one document was created.
    assert _count(db, FiscalDocument) == documents_before
    # And no CPF, full or partial, is in the listing at all.
    for exam in world.values():
        person = db.get(Person, exam.person_id)
        if person.cpf:
            assert person.cpf not in response.text


def test_only_gestor_and_admin_reach_production_endpoints(prod_env, client, auth, world):
    exam = world["ready"]
    for role in ("leitura", "operacional"):
        assert client.get("/api/v1/fiscal/producao/fila", headers=auth(role)).status_code == 403
        assert client.post(f"/api/v1/fiscal/producao/exames/{exam.id}/preparar",
                           headers=auth(role)).status_code == 403
    summary = _prepare(client, auth, exam, "admin")
    for role in ("leitura", "operacional"):
        response = client.post(
            f"/api/v1/fiscal/producao/documentos/{summary['document_id']}/confirmar-emissao",
            json=_confirm_payload(summary), headers=auth(role))
        assert response.status_code == 403
    assert client.get("/api/v1/fiscal/producao/fila").status_code == 401


def test_production_has_no_batch_and_no_web_side_provider_calls(prod_env, client, auth, db, world):
    summary = _prepare(client, auth, world["ready"])
    document_id = summary["document_id"]
    batch = client.post("/api/v1/fiscal/emitir-pendentes",
                        json={"idempotency_key": "m66-batch-01", "document_ids": [document_id]},
                        headers=auth("admin"))
    assert batch.status_code == 409
    assert _code(batch) == "production_operation_only_via_confirmed_worker"
    for path in ("emitir-mock", "reprocessar", "reconciliar", "cancelar-mock"):
        response = client.post(f"/api/v1/fiscal/documentos/{document_id}/{path}",
                               json={"idempotency_key": "m66-shortcut"}, headers=auth("admin"))
        assert response.status_code == 409, path
    assert _count(db, FiscalAttempt, FiscalAttempt.document_id == document_id) == 0
    status = client.get("/api/v1/fiscal/status", headers=auth("gestor")).json()
    assert status["production_issuance"]["batch_available"] is False
    assert status["real_issuance_available"] is False


# ------------------------------------------------------------ the confirmation

def test_the_confirmation_shows_what_will_be_sent_and_masks_the_cpf(
        prod_env, client, auth, db, world):
    summary = _prepare(client, auth, world["ready"])
    person = db.get(Person, world["ready"].person_id)
    assert summary["can_confirm"] is True
    assert summary["exam_code"] == "ESP-066001"
    assert summary["patient_name"] == GOOD_NAME
    assert summary["cpf_masked"] == f"***.***.***-{person.cpf[-2:]}"
    assert (summary["amount"], summary["amount_label"]) == ("220.00", "R$ 220,00")
    assert summary["competence"] == "2026-09-16"
    assert summary["municipio_name"] == "Rio de Janeiro/RJ"
    assert "com broncodilatador" in summary["service_description"]
    assert summary["confirmation_phrase"] == "Confirmar emissão de R$ 220,00"
    assert person.cpf not in json.dumps(summary)


@pytest.mark.parametrize("override, code", [
    ({"confirmation": ""}, None),                                   # confirmation absent (422)
    ({"confirmation": "Confirmar"}, "confirmation_phrase_mismatch"),
    ({"amount": "221.00"}, "confirmation_amount_mismatch"),
    ({"preparation_id": "00000000-0000-0000-0000-000000000000"}, "confirmation_stale"),
])
def test_a_wrong_or_missing_confirmation_records_nothing(
        prod_env, client, auth, db, world, spool, override, code):
    summary = _prepare(client, auth, world["ready"])
    response = client.post(
        f"/api/v1/fiscal/producao/documentos/{summary['document_id']}/confirmar-emissao",
        json=_confirm_payload(summary, **override), headers=auth("gestor"))
    assert response.status_code in (409, 422), response.text
    if code:
        assert _code(response) == code
    assert _count(db, FiscalIssuanceRequest) == 0
    assert list(spool.iterdir()) == []


def test_a_fact_changed_after_the_modal_refuses_the_confirmation(
        prod_env, client, auth, db, world, spool):
    summary = _prepare(client, auth, world["ready"])
    entry = db.scalars(select(FinancialEntry).where(
        FinancialEntry.spirometry_exam_id == world["ready"].id)).one()
    entry.valor = Decimal("230.00")
    db.commit()
    response = client.post(
        f"/api/v1/fiscal/producao/documentos/{summary['document_id']}/confirmar-emissao",
        json=_confirm_payload(summary), headers=auth("gestor"))
    assert response.status_code == 409
    assert _code(response) == "confirmation_stale"
    assert list(spool.iterdir()) == []


def test_a_confirmation_records_one_request_and_rings_once(prod_env, client, auth, db, world, spool):
    summary = _prepare(client, auth, world["ready"])
    url = f"/api/v1/fiscal/producao/documentos/{summary['document_id']}/confirmar-emissao"
    response = client.post(url, json=_confirm_payload(summary), headers=auth("gestor"))
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "authorized" and body["kind"] == "issue"
    assert body["amount_confirmed"] == "220.00"
    assert [p.name for p in spool.iterdir()] == [f"{body['id']}.issue"]
    # Same key replays; a different key is refused while one is live.
    assert client.post(url, json=_confirm_payload(summary), headers=auth("gestor")).json()["id"] == body["id"]
    again = client.post(url, json=_confirm_payload(summary, idempotency_key="m66-test-0002"),
                        headers=auth("admin"))
    assert again.status_code == 409
    assert _code(again) == "issuance_request_already_active"
    assert _count(db, FiscalIssuanceRequest) == 1
    audit = db.scalars(select(AuditLog).where(
        AuditLog.acao == "fiscal.production_issue_authorized")).one()
    assert audit.user_id is not None
    assert audit.detalhes["issuance_request_id"] == body["id"]
    assert audit.detalhes["exam_code"] == "ESP-066001"
    # The web process attempted nothing at the provider.
    assert _count(db, FiscalAttempt, FiscalAttempt.document_id == summary["document_id"]) == 0


@pytest.fixture
def prod_env_without_worker(monkeypatch):
    monkeypatch.setenv("M15_NFSE_ENABLED", "true")
    monkeypatch.setenv("M15_NFSE_ENVIRONMENT", "production")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def test_without_a_worker_spool_the_button_is_unavailable(
        prod_env_without_worker, client, auth, db, world):
    summary = _prepare(client, auth, world["ready"])
    response = client.post(
        f"/api/v1/fiscal/producao/documentos/{summary['document_id']}/confirmar-emissao",
        json=_confirm_payload(summary), headers=auth("gestor"))
    assert response.status_code == 503
    assert _code(response) == "production_worker_unavailable"
    assert _count(db, FiscalIssuanceRequest) == 0


def test_blocked_and_already_issued_facts_cannot_be_prepared_for_issuance(
        prod_env, client, auth, db, world):
    blocked = _prepare(client, auth, world["no_cpf"])
    assert blocked["can_confirm"] is False
    assert blocked["confirmation_phrase"] is None
    assert blocked["block_label"] == "Dados fiscais do paciente incompletos"
    issued = client.post(f"/api/v1/fiscal/producao/exames/{world['imported'].id}/preparar",
                         headers=auth("gestor"))
    assert issued.status_code == 409
    assert _code(issued) == "document_not_issuable"


# ------------------------------------------------------------------ the worker

@pytest.fixture
def worker(monkeypatch, tmp_path, engine, db, users, world, prod_env, spool):
    """The worker module, a throwaway A1 handed over the way systemd hands it
    (a credentials directory), and a mock SEFIN."""
    p12, password = generate_synthetic_test_certificate()
    creds = tmp_path / "credentials"
    creds.mkdir(mode=0o700)
    (creds / "nfse-a1.pfx").write_bytes(p12)
    (creds / "nfse-a1-password").write_text(password + "\n")
    for item in creds.iterdir():      # what systemd hands a service: 0400
        item.chmod(0o400)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(creds))
    monkeypatch.setenv("M15_NFSE_PRODUCTION_CERTIFICATE_MIN_DAYS", "0")
    monkeypatch.setenv("M15_NFSE_FISCAL_ARTIFACTS_DIR", str(tmp_path / "private" / "artifacts"))
    monkeypatch.setattr(clock_module, "read_clock_status",
                        lambda: clock_module.ClockStatus(True, "clock_synchronized"))
    # The worker swaps these process-wide; monkeypatch puts them back.
    monkeypatch.setattr(httpx, "Client", httpx.Client)
    monkeypatch.setattr(httpx, "AsyncClient", httpx.AsyncClient)
    monkeypatch.setattr(dispatch, "HttpxProductionTransport", dispatch.HttpxProductionTransport)

    spec = importlib.util.spec_from_file_location("m66_worker", WORKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    sefin = {"queue": [], "requests": []}

    def handler(request: httpx.Request):
        sefin["requests"].append((request.method, str(request.url), request.content))
        item = sefin["queue"].pop(0)
        if isinstance(item, Exception):
            raise item
        status, payload = item
        return httpx.Response(status, content=payload, headers={"content-type": "application/json"})

    def mock_client(*args, **kwargs):
        return REAL_HTTPX_CLIENT(base_url=kwargs["base_url"], timeout=kwargs.get("timeout"),
                                 transport=httpx.MockTransport(handler))
    module._ORIGINAL_HTTPX_CLIENT = mock_client
    module._install_network_guards()
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    runtime = tmp_path / "private" / "runtime"

    def confirm(label="ready", key="m66-worker-0001"):
        settings = get_settings()
        with maker() as session:
            doc = nfse.prepare(session, world[label].id, settings, users["gestor"].id)
            summary = production.confirmation_summary(session, doc)
            return production.authorize_issue(
                session, doc.id, preparation_id=summary["preparation_id"],
                amount=summary["amount"], confirmation=summary["confirmation_phrase"],
                idempotency_key=key, settings=settings, actor=users["gestor"].id)

    def run(request_id, kind="issue"):
        return module.run_request(request_id, kind, sessionmaker=maker,
                                  settings_factory=module.build_worker_settings,
                                  runtime_root=runtime)

    return {"module": module, "sefin": sefin, "confirm": confirm, "run": run, "maker": maker,
            "runtime": runtime, "password": password}


def _methods(sefin):
    return [method for method, _, _ in sefin["requests"]]


def _request_row(worker, request_id):
    with worker["maker"]() as session:
        return session.get(FiscalIssuanceRequest, request_id)


def test_success_is_one_post_issued_and_fiscally_valid(worker, db):
    request = worker["confirm"]()
    worker["sefin"]["queue"] = [(201, envelope())]
    report = worker["run"](request["id"])
    assert _methods(worker["sefin"]) == ["POST"]
    assert worker["sefin"]["requests"][0][1] == "https://sefin.nfse.gov.br/SefinNacional/nfse"
    assert report["status"] == "issued" and report["final_state"] == "issued"
    assert report["fiscal_validity"] is True
    assert report["provider_post_count"] == 1 and report["provider_get_count"] == 0
    row = _request_row(worker, request["id"])
    assert row.status == "issued" and row.external_id == ACCESS_KEY
    assert row.operation_id and row.provider_post_count == 1
    # The audit trail names the user, the request, the operation and the key.
    finished = db.scalars(select(AuditLog).where(
        AuditLog.acao == "fiscal.production_issue_finished")).one()
    assert finished.user_id == row.authorized_by
    assert finished.detalhes["operation_id"] == row.operation_id
    assert finished.detalhes["external_id"] == ACCESS_KEY
    assert finished.detalhes["provider_post_count"] == 1
    # Pre-POST evidence on disk, private.
    run_dir = worker["runtime"] / request["id"]
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    for name in ("submitted-dps-signed.xml", "post-intent.json", "post-response.body",
                 "result.json"):
        assert stat.S_IMODE((run_dir / name).stat().st_mode) == 0o600, name
    sent = gzip.decompress(base64.b64decode(json.loads(worker["sefin"]["requests"][0][2])
                                            ["dpsXmlGZipB64"]))
    assert (run_dir / "submitted-dps-signed.xml").read_bytes() == sent
    summary = worker["module"].dps_summary(sent)
    assert (summary["tpAmb"], summary["vServ"], summary["dCompet"], summary["cLocPrestacao"]) == (
        "1", "220.00", "2026-09-16", "3304557")


def test_the_same_request_never_runs_twice(worker):
    request = worker["confirm"]()
    worker["sefin"]["queue"] = [(201, envelope())]
    worker["run"](request["id"])
    worker["sefin"]["requests"].clear()
    again = worker["run"](request["id"])
    assert again["claimed"] is False
    assert worker["sefin"]["requests"] == []


def test_the_transport_refuses_a_second_post(worker):
    module = worker["module"]
    module.LEDGER.reset()
    module.LEDGER.claimed_request_id = "x"
    module.LEDGER.phase = "issue"
    module.LEDGER.post_count = 1
    transport = module.OneShotProductionTransport(network_enabled=True, environment="production")
    from app.services.nfse_national.transport import TransportRequest
    with pytest.raises(module.OneShotViolation, match="SEGUNDO POST"):
        transport.send(TransportRequest(method="POST", path="/nfse", body=b"{}", headers={}))
    assert module.LEDGER.second_post_attempted is True
    assert worker["sefin"]["requests"] == []


def test_a_transport_without_a_claimed_request_is_not_authorized(worker):
    module = worker["module"]
    module.LEDGER.reset()
    transport = module.OneShotProductionTransport(network_enabled=True, environment="production")
    assert transport._explicit_human_authorization is False


def test_a_timeout_is_reconciled_with_get_and_never_a_second_post(worker):
    request = worker["confirm"]()
    worker["sefin"]["queue"] = [httpx.ReadTimeout("synthetic"), (200, envelope())]
    report = worker["run"](request["id"])
    assert _methods(worker["sefin"]) == ["POST", "GET"]
    assert "/SefinNacional/dps/DPS" in worker["sefin"]["requests"][1][1]
    assert report["after_issue"] == "uncertain"
    assert report["status"] == "issued" and report["fiscal_validity"] is True
    assert report["provider_post_count"] == 1


def test_an_inconclusive_reconciliation_stays_uncertain(worker, db):
    request = worker["confirm"]()
    worker["sefin"]["queue"] = [httpx.ReadTimeout("synthetic"), (500, b"{}")]
    report = worker["run"](request["id"])
    assert _methods(worker["sefin"]) == ["POST", "GET"]
    assert report["status"] == "uncertain" and report["final_state"] == "uncertain"
    assert _request_row(worker, request["id"]).status == "uncertain"


def test_a_rejection_is_final_no_get_and_no_new_confirmation(worker, client, auth):
    request = worker["confirm"]()
    worker["sefin"]["queue"] = [(400, json.dumps({"erros": [{"Codigo": "E0014",
                                                             "Descricao": "DPS já existe"}]}).encode())]
    report = worker["run"](request["id"])
    assert _methods(worker["sefin"]) == ["POST"]
    assert report["status"] == "rejected" and report["final_state"] == "failed"
    # The button cannot send it again: the document is no longer pending.
    with pytest.raises(Exception) as error:
        worker["confirm"](key="m66-worker-0002")
    assert getattr(error.value, "detail", {}).get("codigo") in (
        "document_not_pending_eligible", "document_not_issuable")
    assert len(worker["sefin"]["requests"]) == 1


def test_a_fact_changed_after_confirmation_is_refused_before_the_post(worker, db, world):
    request = worker["confirm"]()
    entry = db.scalars(select(FinancialEntry).where(
        FinancialEntry.spirometry_exam_id == world["ready"].id)).one()
    entry.valor = Decimal("230.00")
    db.commit()
    report = worker["run"](request["id"])
    assert worker["sefin"]["requests"] == []
    assert report["refused"] in ("eligibility_now_blocked", "preparation_stale", "amount_mismatch")
    row = _request_row(worker, request["id"])
    assert row.status == "refused" and row.provider_post_count == 0
    # A refusal before any attempt frees the document for a NEW confirmation.
    entry.valor = Decimal("220.00")
    db.commit()
    assert worker["confirm"](key="m66-worker-0003")["status"] == "authorized"


def test_a_changed_tomador_identity_is_refused(worker, db, world):
    request = worker["confirm"]()
    person = db.get(Person, world["ready"].person_id)
    person.cpf = synthetic_cpf(999)
    db.commit()
    report = worker["run"](request["id"])
    assert worker["sefin"]["requests"] == []
    assert report["refused"] == "recipient_changed"


def test_an_expired_confirmation_is_never_claimed(worker, db):
    request = worker["confirm"]()
    row = db.get(FiscalIssuanceRequest, request["id"])
    row.expires_at = row.authorized_at - timedelta(minutes=1)
    db.commit()
    report = worker["run"](request["id"])
    assert report["claimed"] is False
    assert worker["sefin"]["requests"] == []
    assert _request_row(worker, request["id"]).status == "expired"


def test_missing_credentials_refuse_without_any_call(worker, monkeypatch):
    request = worker["confirm"]()
    monkeypatch.delenv("CREDENTIALS_DIRECTORY")
    report = worker["run"](request["id"])
    assert report["refused"] == "credentials_directory_missing"
    assert worker["sefin"]["requests"] == []


def test_a_reconcile_request_never_posts(worker, db, client, auth):
    request = worker["confirm"]()
    worker["sefin"]["queue"] = [httpx.ReadTimeout("synthetic"), (500, b"{}")]
    worker["run"](request["id"])
    worker["sefin"]["requests"].clear()
    response = client.post(
        f"/api/v1/fiscal/producao/documentos/{request['document_id']}/solicitar-reconciliacao",
        json={"idempotency_key": "m66-reconcile-01"}, headers=auth("gestor"))
    assert response.status_code == 202, response.text
    reconcile = response.json()
    assert reconcile["kind"] == "reconcile"
    worker["sefin"]["queue"] = [(200, envelope())]
    report = worker["run"](reconcile["id"], "reconcile")
    assert _methods(worker["sefin"]) == ["GET"]
    assert report["status"] == "reconciled" and report["fiscal_validity"] is True
    assert report["provider_post_count"] == 0


def test_a_doorbell_is_consumed_before_anything_else(worker, spool):
    request = worker["confirm"]()
    (spool / "junk.txt").write_text("x")
    assert worker["module"].take_doorbell(spool) == (request["id"], "issue")
    assert list(spool.iterdir()) == []


def test_secrets_and_cpf_never_leave_the_worker(worker, db, world, client, auth, capsys):
    request = worker["confirm"]()
    worker["sefin"]["queue"] = [(201, envelope())]
    report = worker["run"](request["id"])
    cpf = db.get(Person, world["ready"].person_id).cpf
    password = worker["password"]
    texts = [json.dumps(report, default=str), capsys.readouterr().out,
             json.dumps([a.detalhes for a in db.scalars(select(AuditLog)).all()], default=str),
             json.dumps(production.get_request(db, request["id"]), default=str),
             client.get(f"/api/v1/fiscal/producao/pedidos/{request['id']}",
                        headers=auth("gestor")).text,
             client.get("/api/v1/fiscal/producao/fila", headers=auth("gestor")).text,
             client.get("/api/v1/fiscal/status", headers=auth("gestor")).text]
    for text in texts:
        assert password not in text
        assert cpf not in text
    row = _request_row(worker, request["id"])
    assert cpf not in json.dumps({c.name: str(getattr(row, c.name))
                                  for c in FiscalIssuanceRequest.__table__.columns})


def test_the_reserve_and_every_other_exam_stay_untouched(worker, db, world):
    request = worker["confirm"]()
    worker["sefin"]["queue"] = [(201, envelope())]
    worker["run"](request["id"])
    documents = db.scalars(select(FiscalDocument).where(
        FiscalDocument.environment == "production")).all()
    issued_by_system = [d for d in documents if d.state == "issued"
                        and not production._is_imported(db, d.id)]
    assert [d.spirometry_exam_id for d in issued_by_system] == [world["ready"].id]
    assert _count(db, FiscalDocument,
                  FiscalDocument.spirometry_exam_id == world["reserve"].id) == 0
    assert _count(db, FiscalAttempt, FiscalAttempt.operation == "issue",
                  FiscalAttempt.phase == "started") == 1


def test_the_database_refuses_a_second_claimed_issue_for_a_document(worker, db):
    """Independent of the worker's code: the partial unique index."""
    from sqlalchemy.exc import IntegrityError

    from app.models import utcnow
    request = worker["confirm"]()
    worker["sefin"]["queue"] = [httpx.ReadTimeout("synthetic"), (500, b"{}")]
    worker["run"](request["id"])                      # claimed once, left uncertain
    first = db.get(FiscalIssuanceRequest, request["id"])
    clone = FiscalIssuanceRequest(
        document_id=first.document_id, spirometry_exam_id=first.spirometry_exam_id,
        preparation_id=first.preparation_id, preparation_fingerprint=first.preparation_fingerprint,
        recipient_fingerprint=first.recipient_fingerprint, amount_confirmed=first.amount_confirmed,
        kind="issue", status="running", authorized_by=first.authorized_by,
        authorized_at=utcnow(), expires_at=utcnow() + timedelta(minutes=5), claimed_at=utcnow(),
        idempotency_key="m66-clone-claim")
    db.add(clone)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_a_worker_that_died_mid_run_becomes_interrupted_and_stays_sent_once(worker, db):
    from app.models import utcnow
    request = worker["confirm"]()
    row = db.get(FiscalIssuanceRequest, request["id"])
    row.status, row.claimed_at = "running", utcnow() - timedelta(minutes=30)
    db.commit()
    assert production.get_request(db, request["id"])["status"] == "interrupted"
    # Still counts as handed to a worker once: a new confirmation is refused.
    with pytest.raises(Exception) as error:
        worker["confirm"](key="m66-worker-0009")
    assert error.value.detail["codigo"] == "document_already_sent_once"


# ------------------------------------------------------------- PostgreSQL

from tests.test_nfse_migrations import config as _alembic_config, postgres_url  # noqa: E402,F401


def test_the_m66_migration_and_its_guarantees_on_postgresql(postgres_url):  # noqa: F811
    """Production's own engine: the partial unique indexes must bite there,
    and the migration must go down and up cleanly with no model drift."""
    from alembic import command
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import IntegrityError

    cfg = _alembic_config(postgres_url)
    command.upgrade(cfg, "head")
    command.check(cfg)
    command.downgrade(cfg, "c4e8b1f37a92")
    command.upgrade(cfg, "head")
    command.check(cfg)

    engine = create_engine(postgres_url)
    ids = {}
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, email, nome, password_hash, ativo, created_at, updated_at) "
                          "VALUES ('u1', 'm66@teste.invalid', 'Op', 'x', true, now(), now())"))
        conn.execute(text("INSERT INTO people (id, public_code, nome_completo, nome_normalizado, status, "
                          "nao_contatar, arquivado, created_at, updated_at) VALUES ('p1', 'PES-M66', "
                          "'Marina Rocha Albuquerque', 'marina rocha albuquerque', 'ativo', false, false, now(), now())"))
        conn.execute(text("INSERT INTO spirometry_exams (id, public_code, person_id, status, "
                          "data_exame_dia_assumido, created_at, updated_at) VALUES "
                          "('e1', 'ESP-M66', 'p1', 'Realizado', false, now(), now())"))
        conn.execute(text("INSERT INTO fiscal_documents (id, spirometry_exam_id, environment, state, "
                          "eligibility, blocking_reasons, created_by, idempotency_key, "
                          "idempotency_fingerprint, created_at, updated_at) VALUES ('d1', 'e1', "
                          "'production', 'pending', 'eligible', '[]', 'u1', 'k-d1', 'f', now(), now())"))
        conn.execute(text("INSERT INTO fiscal_preparations (id, document_id, flow, blocking_reasons, "
                          "fingerprint, created_by, created_at) VALUES ('pr1', 'd1', 'DIRECT', '[]', 'fp', "
                          "'u1', now())"))

    def insert(key, status, claimed):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO fiscal_issuance_requests (id, document_id, spirometry_exam_id, "
                "preparation_id, preparation_fingerprint, recipient_fingerprint, amount_confirmed, "
                "kind, status, authorized_by, authorized_at, expires_at, claimed_at, idempotency_key) "
                "VALUES (:id, 'd1', 'e1', 'pr1', 'fp', 'rf', 220.00, 'issue', :status, 'u1', now(), "
                "now() + interval '15 minutes', :claimed, :key)"),
                {"id": key, "status": status, "claimed": claimed, "key": key})

    insert("r1", "refused", None)            # refused before any attempt: never blocks
    insert("r2", "running", None)
    with pytest.raises(IntegrityError):      # a second LIVE request for the document
        insert("r3", "authorized", None)
    with engine.begin() as conn:
        conn.execute(text("UPDATE fiscal_issuance_requests SET status = 'uncertain', "
                          "claimed_at = now() WHERE id = 'r2'"))
    with pytest.raises(IntegrityError):      # a second CLAIMED issue for the document, ever
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO fiscal_issuance_requests (id, document_id, spirometry_exam_id, "
                "preparation_id, preparation_fingerprint, recipient_fingerprint, amount_confirmed, "
                "kind, status, authorized_by, authorized_at, expires_at, claimed_at, idempotency_key) "
                "VALUES ('r4', 'd1', 'e1', 'pr1', 'fp', 'rf', 220.00, 'issue', 'issued', 'u1', now(), "
                "now(), now(), 'r4')"))
    with pytest.raises(IntegrityError):      # a reconcile request can never record a POST
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO fiscal_issuance_requests (id, document_id, spirometry_exam_id, "
                "preparation_id, preparation_fingerprint, recipient_fingerprint, amount_confirmed, "
                "kind, status, authorized_by, authorized_at, expires_at, provider_post_count, "
                "idempotency_key) VALUES ('r5', 'd1', 'e1', 'pr1', 'fp', 'rf', 220.00, 'reconcile', "
                "'reconciled', 'u1', now(), now(), 1, 'r5')"))
    # Downgrade refuses once a worker has claimed something.
    with pytest.raises(RuntimeError, match="já executados"):
        command.downgrade(cfg, "c4e8b1f37a92")
    engine.dispose()


def test_the_self_check_opens_the_certificate_and_sends_nothing(worker, engine, monkeypatch, capsys):
    from app import db as app_db
    monkeypatch.setattr(app_db, "get_sessionmaker",
                        lambda: sessionmaker(bind=engine, expire_on_commit=False))
    module = worker["module"]
    assert module.main(["--self-check"]) == 0
    out = capsys.readouterr().out
    report = json.loads(out.strip().splitlines()[-1])
    assert report["certificate_readable"] is True
    assert report["network_used"] is False
    assert worker["password"] not in out
    assert worker["sefin"]["requests"] == []
    with sessionmaker(bind=engine)() as session:
        assert session.scalar(select(func.count()).select_from(FiscalIssuanceRequest)) == 0


# ------------------------------------------------------------------ M69

def _systemd_shaped(worker_creds: Path, monkeypatch):
    """Turn the fixture's credentials into what systemd 255 really hands the
    worker (measured on the VPS): root:root 0440 + ACL user:<uid>:r, on a
    read-only tmpfs at /run/credentials/<unit>. Owner/ACL/mount need root, so
    those three lookups return the measured values."""
    from app.services.nfse_national import readiness
    for item in worker_creds.iterdir():
        item.chmod(0o440)
    def acl(path):
        perm = 5 if Path(path) == worker_creds else 4
        return [(1, perm, 0xFFFFFFFF), (2, perm, os.geteuid()), (4, 0, 0xFFFFFFFF),
                (16, perm, 0xFFFFFFFF), (32, 0, 0xFFFFFFFF)]
    monkeypatch.setattr(readiness, "_CREDENTIALS_ROOT", str(worker_creds.parent))
    monkeypatch.setattr(readiness, "_CREDENTIAL_OWNER_UID", os.getuid())
    monkeypatch.setattr(readiness, "_read_posix_acl", acl)
    monkeypatch.setattr(readiness, "_mountinfo_entry",
                        lambda p: ("tmpfs", {"ro", "nosuid", "nodev", "noexec"})
                        if p == str(worker_creds) else None)


def test_m69_a_permission_refusal_sends_nothing_and_a_new_human_confirmation_can_issue(
        worker, monkeypatch):
    creds = Path(os.environ["CREDENTIALS_DIRECTORY"])
    for item in creds.iterdir():            # the VPS mode, WITHOUT the systemd shape
        item.chmod(0o440)
    first = worker["confirm"]()
    report = worker["run"](first["id"])
    assert report["refused"] == "restricted_certificate_path_permissions_too_open"
    assert worker["sefin"]["requests"] == []
    row = _request_row(worker, first["id"])
    assert row.status == "refused" and row.provider_post_count == 0 and row.provider_get_count == 0
    with worker["maker"]() as session:
        assert _count(session, FiscalAttempt, FiscalAttempt.document_id == row.document_id) == 0
        doc = session.get(FiscalDocument, row.document_id)
        assert (doc.state, doc.eligibility) == ("pending", "eligible")
    # The same request never runs again, even once the cause is fixed.
    _systemd_shaped(creds, monkeypatch)
    worker["run"](first["id"])
    assert worker["sefin"]["requests"] == []
    assert _request_row(worker, first["id"]).status == "refused"
    # Only a NEW human confirmation produces a new request, and it issues once.
    second = worker["confirm"](key="m66-worker-0002")
    assert second["id"] != first["id"]
    worker["sefin"]["queue"] = [(201, envelope())]
    report = worker["run"](second["id"])
    assert _methods(worker["sefin"]) == ["POST"]
    assert report["status"] == "issued" and report["provider_post_count"] == 1
    assert _request_row(worker, first["id"]).status == "refused"   # history kept


def test_m69_the_self_check_accepts_the_real_systemd_credential(worker, engine, monkeypatch, capsys):
    from app import db as app_db
    monkeypatch.setattr(app_db, "get_sessionmaker",
                        lambda: sessionmaker(bind=engine, expire_on_commit=False))
    _systemd_shaped(Path(os.environ["CREDENTIALS_DIRECTORY"]), monkeypatch)
    assert worker["module"].main(["--self-check"]) == 0
    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert report["self_check"] == "ok"
    assert report["certificate_readable"] is True
    assert report["certificate_permissions_ok"] is True
    assert report["certificate_margin_ok"] is True
    assert report["clock_synchronized"] is True
    assert report["network_used"] is False
    assert worker["sefin"]["requests"] == []


def test_m69_the_self_check_reports_what_the_real_run_would_refuse(worker, engine, monkeypatch,
                                                                  capsys):
    from app import db as app_db
    monkeypatch.setattr(app_db, "get_sessionmaker",
                        lambda: sessionmaker(bind=engine, expire_on_commit=False))
    for item in Path(os.environ["CREDENTIALS_DIRECTORY"]).iterdir():
        item.chmod(0o440)
    assert worker["module"].main(["--self-check"]) == 2
    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert report["self_check"] == "attention"
    assert report["certificate_permissions_ok"] is False
    assert report["network_used"] is False
    assert worker["sefin"]["requests"] == []
