"""M29 — RestrictedNfseProvider wiring into the live dispatcher (mission
section F). Every gate re-checked here is fail-closed: missing any ONE of
them must refuse with a specific, stable blocker code — never silently fall
back to a different provider, and never reach the transport layer.

``M15_NFSE_RESTRICTED_NETWORK_ENABLED`` is only ever set to ``True`` inside
these tests (never a real default — see ``app/config.py``), and the
transport itself is always ``FakeTransport`` here: no test in this file
ever performs real I/O.
"""
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.config import Settings
from app.models import FinancialEntry, Person, SpirometryExam
from app.services import nfse
from app.services.nfse_national import dispatch, fiscal_config
from app.services.nfse_national.config import NationalDpsConfiguration
from app.services.nfse_national.identifiers import DpsIdComponents, build_dps_id
from app.services.nfse_national.signer import generate_synthetic_test_certificate
from app.services.nfse_national.transport import (FakeTransport, PATH_GET_DPS,
                                                   PATH_ISSUE_NFSE, TransportResponse)
import tests.test_nfse_foundation as _foundation
from tests.test_nfse_foundation import events, policy_payload
from tests.test_nfse_national_provider import VALID_ACCESS_KEY, _nfse_xml

fiscal_enabled = _foundation.fiscal_enabled  # pytest fixture reuse


def synthetic_national_config(**changes) -> NationalDpsConfiguration:
    data = dict(
        version="SYNTH-M29-DISPATCH-v1", layout_version="restricted-v1.01-20260727",
        issuer_cnpj="11222333000181", issuer_name="SOPROLIFE SAUDE LTDA (SINTETICO)",
        issuer_municipio_ibge="3304557", issuer_op_simp_nac=3, issuer_reg_esp_trib=0,
        codigo_tributacao_nacional="140501",
        trib_issqn=1, tp_ret_issqn=1,
        amount_basis="financial_entry.valor", competence_rule="service_date",
        own_revenue_confirmed=True, validation_reference="SYNTHETIC-ONLY",
    )
    data.update(changes)
    return NationalDpsConfiguration.model_validate(data)


@pytest.fixture
def certificate_file(tmp_path):
    p12_bytes, password = generate_synthetic_test_certificate()
    path = tmp_path / "synthetic.pfx"
    path.write_bytes(p12_bytes)
    path.chmod(0o600)
    return path, password


@pytest.fixture
def fully_configured_settings(tmp_path, certificate_file):
    cert_path, cert_password = certificate_file
    return Settings(
        nfse_enabled=True, nfse_environment="restricted", nfse_real_enabled=True,
        nfse_restricted_network_enabled=True,
        nfse_restricted_base_url="https://restrito.example.gov.br",
        nfse_restricted_certificate_path=cert_path,
        nfse_restricted_certificate_password=cert_password,
        nfse_fiscal_artifacts_dir=tmp_path / "fiscal-artifacts",
    )


@pytest.fixture
def restricted_source(db, users):
    p = Person(public_code="PES-M29D", nome_completo="Pessoa Sintética M29 Dispatch",
              nome_normalizado="pessoa sintetica m29 dispatch", cpf="52998224725")
    db.add(p)
    db.flush()
    e = SpirometryExam(public_code="ESP-M29D", person_id=p.id, status="Realizado",
                       data_exame=date(2026, 8, 10), data_exame_precisao="dia",
                       modalidade="residencial", broncodilatador=True,
                       municipio_atendimento_ibge="3304557")
    db.add(e)
    db.flush()
    f = FinancialEntry(public_code="LAN-M29D", tipo="receita", categoria="Espirometria",
                       valor=Decimal("220.00"), status="Recebido", spirometry_exam_id=e.id,
                       data_competencia=date(2026, 9, 1))
    db.add(f)
    db.commit()
    return p, e, f


def _validate_fiscal_policies(db, users, *, environment="restricted", suffix=""):
    # readiness.py's fiscal-policy check requires BOTH flows validated for
    # the environment before it considers the provider "policy ready" —
    # every test that expects to reach dispatch's later gates needs both.
    for flow in ("DIRECT", "HOME"):
        nfse.create_policy(db, policy_payload(version=f"SYNTH-M29D-{flow}{suffix}",
                                              environment=environment, flow=flow),
                           users["admin"].id)


@pytest.fixture
def restricted_doc(db, users, restricted_source, fully_configured_settings):
    _validate_fiscal_policies(db, users)
    return nfse.prepare(db, restricted_source[1].id, fully_configured_settings, users["gestor"].id)


def _validate_active_config(db, users, *, effective_from=date(2026, 1, 1)):
    fiscal_config.create_version(
        db, environment="restricted", effective_from=effective_from, validation_state="validated",
        configuration=synthetic_national_config(), actor=users["admin"].id,
    )


def _expected_dps_id(number: int) -> str:
    """The exact TSIdDPS a real dispatch call must build for DPS number
    ``number``, matching ``synthetic_national_config()``'s issuer fields —
    used to assert the OUTBOUND path/body, never re-deriving it from
    application code (that would just restate the bug it's checking for).
    """
    return build_dps_id(DpsIdComponents(
        codigo_municipio="3304557", tipo_inscricao_federal=2,
        inscricao_federal="11222333000181", serie_dps="00001",
        numero_dps=str(number).rjust(15, "0"),
    ))


# --------------------------------------------------------------- structural gates


def test_default_settings_never_reach_restricted_dispatch():
    with pytest.raises(HTTPException) as error:
        nfse.get_provider(Settings())
    assert error.value.status_code == 503


def test_network_gate_off_blocks_even_with_everything_else_configured(
        db, users, restricted_doc, fully_configured_settings):
    settings = fully_configured_settings.model_copy(update={"nfse_restricted_network_enabled": False})
    _validate_active_config(db, users)
    with pytest.raises(HTTPException) as error:
        nfse.operate(db, restricted_doc.id, "issue", "m29-gate-network-off", settings,
                     users["gestor"].id)
    assert error.value.status_code == 503
    assert error.value.detail["codigo"] == "restricted_network_gate_disabled"


def test_missing_national_configuration_blocks_at_dispatch(db, users, restricted_doc,
                                                            fully_configured_settings):
    # Every structural (settings-only) gate is satisfied, but no
    # NationalDpsConfigurationVersion has been validated for 'restricted' —
    # this can ONLY be caught at the per-document dispatch step, never by
    # get_provider() alone (which has no database access).
    with pytest.raises(HTTPException) as error:
        nfse.operate(db, restricted_doc.id, "issue", "m29-gate-no-config", fully_configured_settings,
                     users["gestor"].id)
    assert error.value.status_code == 503
    assert error.value.detail["codigo"] == "national_dps_configuration_not_defined_for_any_real_document"


def test_recipient_without_cpf_blocks_at_dispatch(db, users, fully_configured_settings):
    p = Person(public_code="PES-M29D-NOCPF", nome_completo="Pessoa Sem CPF",
              nome_normalizado="pessoa sem cpf")
    db.add(p)
    db.flush()
    e = SpirometryExam(public_code="ESP-M29D-NOCPF", person_id=p.id, status="Realizado",
                       data_exame=date(2026, 8, 10), data_exame_precisao="dia",
                       modalidade="residencial", broncodilatador=True,
                       municipio_atendimento_ibge="3304557")
    db.add(e)
    db.flush()
    f = FinancialEntry(public_code="LAN-M29D-NOCPF", tipo="receita", categoria="Espirometria",
                       valor=Decimal("220.00"), status="Recebido", spirometry_exam_id=e.id,
                       data_competencia=date(2026, 9, 1))
    db.add(f)
    db.commit()
    _validate_fiscal_policies(db, users, suffix="-NOCPF")
    doc = nfse.prepare(db, e.id, fully_configured_settings, users["gestor"].id)
    _validate_active_config(db, users)
    with pytest.raises(HTTPException) as error:
        nfse.operate(db, doc.id, "issue", "m29-gate-no-cpf", fully_configured_settings,
                     users["gestor"].id)
    assert error.value.detail["codigo"] == "recipient_identity_not_supplied"


def test_certificate_path_missing_blocks_at_dispatch(db, users, restricted_doc,
                                                      fully_configured_settings):
    _validate_active_config(db, users)
    settings = fully_configured_settings.model_copy(update={"nfse_restricted_certificate_path": None})
    with pytest.raises(HTTPException) as error:
        nfse.operate(db, restricted_doc.id, "issue", "m29-gate-no-cert", settings, users["gestor"].id)
    assert error.value.detail["codigo"] == "restricted_certificate_path_missing"


def test_draft_configuration_never_becomes_active(db, users, restricted_doc, fully_configured_settings):
    fiscal_config.create_version(
        db, environment="restricted", effective_from=date(2026, 1, 1), validation_state="draft",
        configuration=synthetic_national_config(version="SYNTH-M29-DRAFT"), actor=users["admin"].id,
    )
    with pytest.raises(HTTPException) as error:
        nfse.operate(db, restricted_doc.id, "issue", "m29-gate-draft-config", fully_configured_settings,
                     users["gestor"].id)
    assert error.value.detail["codigo"] == "national_dps_configuration_not_defined_for_any_real_document"


def test_missing_service_location_blocks_at_dispatch(db, users, fully_configured_settings):
    """M31 — mirrors test_recipient_without_cpf_blocks_at_dispatch: a
    document reaching the dispatcher without a structured, supported service
    municipality must be refused BEFORE a RestrictedNfseProvider is ever
    constructed, never silently defaulted to Rio de Janeiro."""
    p = Person(public_code="PES-M31D-NOLOC", nome_completo="Pessoa Sem Local",
              nome_normalizado="pessoa sem local", cpf="52998224725")
    db.add(p)
    db.flush()
    e = SpirometryExam(public_code="ESP-M31D-NOLOC", person_id=p.id, status="Realizado",
                       data_exame=date(2026, 8, 10), data_exame_precisao="dia",
                       modalidade="residencial", broncodilatador=True,
                       municipio_atendimento_ibge=None)
    db.add(e)
    db.flush()
    f = FinancialEntry(public_code="LAN-M31D-NOLOC", tipo="receita", categoria="Espirometria",
                       valor=Decimal("220.00"), status="Recebido", spirometry_exam_id=e.id,
                       data_competencia=date(2026, 9, 1))
    db.add(f)
    db.commit()
    _validate_fiscal_policies(db, users, suffix="-NOLOC")
    doc = nfse.prepare(db, e.id, fully_configured_settings, users["gestor"].id)
    _validate_active_config(db, users)
    with pytest.raises(HTTPException) as error:
        nfse.operate(db, doc.id, "issue", "m31-gate-no-location", fully_configured_settings,
                     users["gestor"].id)
    assert error.value.detail["codigo"] == "service_location_missing"


def test_unsupported_service_location_blocks_at_dispatch(db, users, fully_configured_settings):
    p = Person(public_code="PES-M31D-BADLOC", nome_completo="Pessoa Local Não Suportado",
              nome_normalizado="pessoa local nao suportado", cpf="52998224725")
    db.add(p)
    db.flush()
    e = SpirometryExam(public_code="ESP-M31D-BADLOC", person_id=p.id, status="Realizado",
                       data_exame=date(2026, 8, 10), data_exame_precisao="dia",
                       modalidade="residencial", broncodilatador=True,
                       municipio_atendimento_ibge="9999999")
    db.add(e)
    db.flush()
    f = FinancialEntry(public_code="LAN-M31D-BADLOC", tipo="receita", categoria="Espirometria",
                       valor=Decimal("220.00"), status="Recebido", spirometry_exam_id=e.id,
                       data_competencia=date(2026, 9, 1))
    db.add(f)
    db.commit()
    _validate_fiscal_policies(db, users, suffix="-BADLOC")
    doc = nfse.prepare(db, e.id, fully_configured_settings, users["gestor"].id)
    _validate_active_config(db, users)
    with pytest.raises(HTTPException) as error:
        nfse.operate(db, doc.id, "issue", "m31-gate-bad-location", fully_configured_settings,
                     users["gestor"].id)
    assert error.value.detail["codigo"] == "service_location_unsupported"


# --------------------------------------------------------------------- happy path


def test_full_wiring_reaches_restricted_provider_via_fake_transport(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """Every gate satisfied, no provider injected: nfse.operate() must build
    a REAL, per-document RestrictedNfseProvider through the dispatch module
    on its own — the only test seam is the transport (FakeTransport), never
    the provider-resolution logic itself.
    """
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[TransportResponse(status_code=200, body=b"<nfse-ok/>")])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    doc = nfse.operate(db, restricted_doc.id, "issue", "m29-full-wiring-1", fully_configured_settings,
                       users["gestor"].id)
    assert len(fake.received) == 1  # the transport really was called exactly once
    # A well-formed 2xx response classifies as SIMULATED at the transport
    # level, but the restricted provider never returns an external_id (no
    # NFS-e access-key extraction exists yet — see the M29 report) and
    # nfse._normalized() already refuses to treat SIMULATED-without-id as
    # anything but UNCERTAIN, exactly as it would for any other provider.
    # This is the correct, honest, fail-closed behavior today, not a bug.
    assert doc.state == "uncertain"


def test_full_wiring_with_real_nfse_response_converges_to_simulated(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """M30 — with the response-contract fix, a well-formed NFS-e success body
    (the official contract for POST /nfse) now converges all the way to
    'simulated', with the REAL government access key recorded as
    external_id — never a MOCK-shaped one, never invented.
    """
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[TransportResponse(status_code=200, body=_nfse_xml())])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    doc = nfse.operate(db, restricted_doc.id, "issue", "m30-full-wiring-real-1",
                       fully_configured_settings, users["gestor"].id)
    assert len(fake.received) == 1
    assert doc.state == "simulated"
    completed = [a for a in events(db, doc) if a.phase == "completed"]
    assert completed[-1].outcome == "simulated"
    assert completed[-1].external_id == VALID_ACCESS_KEY


def test_full_wiring_without_bronchodilator_variant(
        monkeypatch, db, users, restricted_source, fully_configured_settings):
    exam = restricted_source[1]
    exam.broncodilatador = False
    db.commit()
    _validate_fiscal_policies(db, users, suffix="-NOBD")
    doc = nfse.prepare(db, exam.id, fully_configured_settings, users["gestor"].id)
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[TransportResponse(status_code=200, body=b"<nfse-ok/>")])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    nfse.operate(db, doc.id, "issue", "m29-full-wiring-2", fully_configured_settings, users["gestor"].id)
    assert len(fake.received) == 1
    sent_xml = fake.received[0].body
    assert b"Espirometria sem broncodilatador" in sent_xml


# ------------------------------------------------------------ M33 — reconcile targets the original DPS


def test_first_issue_attempt_builds_dps_for_its_own_attempt_number(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """A first issue attempt (attempt #1) has no target to reconcile — it
    mints a brand-new DPS under its own attempt number, embedded in the
    signed body sent to POST /nfse."""
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[TransportResponse(status_code=200, body=b"<nfse-ok/>")])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    doc = nfse.operate(db, restricted_doc.id, "issue", "m33-issue-1", fully_configured_settings,
                       users["gestor"].id)
    assert doc.state == "uncertain"
    assert len(fake.received) == 1
    assert fake.received[0].path == PATH_ISSUE_NFSE
    assert _expected_dps_id(1).encode() in fake.received[0].body


def test_reconcile_queries_original_issue_dps_not_reconcile_attempt_number(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """Root-cause regression (M33): attempt #1 (issue) goes uncertain, and
    the reconciliation that follows is attempt #2 — but the outbound GET
    /dps/{id} MUST still query the DPS built for attempt #1, never one
    rebuilt from the reconciliation's own attempt number (#2).
    """
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[
        TransportResponse(status_code=200, body=b"<nfse-ok/>"),  # issue #1 -> malformed -> uncertain
        TransportResponse(status_code=404, body=b""),            # reconcile #2 -> confirmed absent
    ])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    doc = nfse.operate(db, restricted_doc.id, "issue", "m33-reconcile-1", fully_configured_settings,
                       users["gestor"].id)
    assert doc.state == "uncertain"

    doc = nfse.operate(db, restricted_doc.id, "reconcile", "m33-reconcile-2", fully_configured_settings,
                       users["gestor"].id)
    assert len(fake.received) == 2
    reconcile_request = fake.received[1]
    assert reconcile_request.path == PATH_GET_DPS.format(dps_id=_expected_dps_id(1))
    # Never the bug's shape (a DPS rebuilt from the reconcile's own number=2):
    assert reconcile_request.path != PATH_GET_DPS.format(dps_id=_expected_dps_id(2))
    # M30 regression: still the official TSIdDPS, never doc.id/operation_id.
    assert doc.id not in reconcile_request.path

    assert doc.state == "failed"  # NOT_FOUND on a reconciled 'issue' target converges to 'failed'
    attempts = [a for a in events(db, doc) if a.phase == "started"]
    assert [a.number for a in attempts] == [1, 2]  # audit sequence still append-only


def test_repeated_reconciliation_keeps_targeting_original_issue_dps(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """A SECOND reconciliation (attempt #3, after attempt #2 also stayed
    uncertain) must still query the DPS from the ORIGINAL issue (attempt
    #1) — never attempt #2's or its own #3 number.
    """
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[
        TransportResponse(status_code=200, body=b"<nfse-ok/>"),  # issue #1 -> uncertain
        TransportResponse(status_code=500, body=b""),            # reconcile #2 -> still uncertain
        TransportResponse(status_code=404, body=b""),            # reconcile #3 -> confirmed absent
    ])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    doc = nfse.operate(db, restricted_doc.id, "issue", "m33-repeat-1", fully_configured_settings,
                       users["gestor"].id)
    assert doc.state == "uncertain"

    doc = nfse.operate(db, restricted_doc.id, "reconcile", "m33-repeat-2", fully_configured_settings,
                       users["gestor"].id)
    assert doc.state == "uncertain"

    doc = nfse.operate(db, restricted_doc.id, "reconcile", "m33-repeat-3", fully_configured_settings,
                       users["gestor"].id)
    assert doc.state == "failed"

    assert len(fake.received) == 3
    expected_original = PATH_GET_DPS.format(dps_id=_expected_dps_id(1))
    assert fake.received[1].path == expected_original  # attempt #2's query
    assert fake.received[2].path == expected_original  # attempt #3's query — still the original DPS
    attempts = [a for a in events(db, doc) if a.phase == "started"]
    assert [a.number for a in attempts] == [1, 2, 3]  # audit sequence still append-only


def test_injected_provider_bypasses_dispatch_entirely(db, users, restricted_doc,
                                                       fully_configured_settings):
    """An explicitly injected provider (test double) must be used AS-IS,
    exactly like the pre-existing mock-path contract — dispatch resolution
    only ever runs when nothing was injected.
    """
    from app.services.nfse_providers import Outcome, ProviderResult

    class FakeRestrictedProvider:
        name = "restricted"
        environment = "restricted"

        def issue(self, request):
            return ProviderResult(Outcome.REJECTED)

        def query(self, request, operation):
            return self.issue(request)

        cancel = issue

    doc = nfse.operate(db, restricted_doc.id, "issue", "m29-injected-1", fully_configured_settings,
                       users["gestor"].id, provider=FakeRestrictedProvider())
    assert doc.state == "failed"


# --------------------------------------------------------- batch safety (section I)


@pytest.fixture
def fiscal_restricted_enabled(monkeypatch):
    """Like ``fiscal_enabled`` (test_nfse_foundation.py), but for the
    restricted environment with the real provider path structurally
    reachable — MUST be listed FIRST in a test's fixture parameters so
    ``get_settings.cache_clear()`` runs before ``client``/``tokens`` ever
    call ``get_settings()`` (otherwise a token gets signed against one
    ephemeral secret and validated against a different one).
    """
    from app.config import get_settings

    monkeypatch.setenv("M15_NFSE_ENABLED", "true")
    monkeypatch.setenv("M15_NFSE_ENVIRONMENT", "restricted")
    monkeypatch.setenv("M15_NFSE_REAL_ENABLED", "true")  # isolates the network gate as the ONE thing missing
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_batch_endpoint_restricted_network_off_is_safe(fiscal_restricted_enabled, client, auth,
                                                        db, users):
    """"Fiscal → selecionar elegíveis → Emitir pendentes" over the HTTP API,
    for a restricted-environment document, with the network gate OFF (the
    only state M29 ships with): no government request is possible, the
    batch endpoint reports the exact blocker per document, and NOTHING about
    the document or its evidence trail changes as a side effect.
    """

    p = Person(public_code="PES-BATCHR", nome_completo="Pessoa Lote Restrito",
              nome_normalizado="pessoa lote restrito", cpf="52998224725")
    db.add(p)
    db.flush()
    e = SpirometryExam(public_code="ESP-BATCHR", person_id=p.id, status="Realizado",
                       data_exame=date(2026, 8, 10), data_exame_precisao="dia",
                       modalidade="residencial", broncodilatador=True,
                       municipio_atendimento_ibge="3304557")
    db.add(e)
    db.flush()
    f = FinancialEntry(public_code="LAN-BATCHR", tipo="receita", categoria="Espirometria",
                       valor=Decimal("220.00"), status="Recebido", spirometry_exam_id=e.id,
                       data_competencia=date(2026, 9, 1))
    db.add(f)
    db.commit()
    _validate_fiscal_policies(db, users, suffix="-BATCHR")

    prep = client.post("/api/v1/fiscal/preparar", json={"spirometry_exam_id": e.id}, headers=auth("gestor"))
    assert prep.status_code == 200, prep.text
    doc_id = prep.json()["id"]
    assert prep.json()["state"] == "pending"

    resp = client.post("/api/v1/fiscal/emitir-pendentes", headers=auth("gestor"),
                       json={"idempotency_key": "m29-batch-restricted-1", "document_ids": [doc_id]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["real_issuance_available"] is False
    item = body["itens"][0]
    assert item.get("error", {}).get("codigo") == "restricted_network_gate_disabled"
    assert item.get("status_code") == 503

    # No hidden side effect: the document is still exactly 'pending', and no
    # FiscalAttempt (started or completed) was ever recorded for it.
    inspect = client.get(f"/api/v1/fiscal/documentos/{doc_id}", headers=auth("leitura"))
    assert inspect.json()["state"] == "pending"
    attempts = client.get(f"/api/v1/fiscal/documentos/{doc_id}/tentativas", headers=auth("leitura"))
    assert attempts.json()["itens"] == []
