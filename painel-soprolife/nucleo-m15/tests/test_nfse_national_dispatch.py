"""M29 — NationalNfseProvider wiring into the live dispatcher (mission
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
from tests.test_nfse_national_provider import (VALID_ACCESS_KEY, _sent_dps_xml,
                                               _success_body)

fiscal_enabled = _foundation.fiscal_enabled  # pytest fixture reuse


def synthetic_national_config(**changes) -> NationalDpsConfiguration:
    data = dict(
        version="SYNTH-M29-DISPATCH-v1", layout_version="restricted-v1.01-20260727",
        issuer_cnpj="11222333000181", issuer_name="SOPROLIFE SAUDE LTDA (SINTETICO)",
        issuer_municipio_ibge="3304557", issuer_op_simp_nac=3,
        issuer_reg_ap_trib_sn=1, issuer_reg_esp_trib=0,
        codigo_tributacao_nacional="140501",
        trib_issqn=1, tp_ret_issqn=1,
        amount_basis="financial_entry.valor", competence_rule="service_date",
        own_revenue_confirmed=True, validation_reference="SYNTHETIC-ONLY",
        p_tot_trib_sn=Decimal("6.00"),
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


@pytest.fixture
def restricted_source_b(db, users):
    """A SECOND, independent exam/document — same issuer scope as
    ``restricted_source`` (same synthetic_national_config), used to prove
    numero_dps allocation is correctly scoped across DIFFERENT documents,
    never per-document (see M36)."""
    p = Person(public_code="PES-M29D-B", nome_completo="Pessoa Sintética M29 Dispatch B",
              nome_normalizado="pessoa sintetica m29 dispatch b", cpf="11144477735")
    db.add(p)
    db.flush()
    e = SpirometryExam(public_code="ESP-M29D-B", person_id=p.id, status="Realizado",
                       data_exame=date(2026, 8, 10), data_exame_precisao="dia",
                       modalidade="residencial", broncodilatador=True,
                       municipio_atendimento_ibge="3304557")
    db.add(e)
    db.flush()
    f = FinancialEntry(public_code="LAN-M29D-B", tipo="receita", categoria="Espirometria",
                       valor=Decimal("220.00"), status="Recebido", spirometry_exam_id=e.id,
                       data_competencia=date(2026, 9, 1))
    db.add(f)
    db.commit()
    return p, e, f


@pytest.fixture
def restricted_doc_b(db, users, restricted_source_b, fully_configured_settings):
    _validate_fiscal_policies(db, users)  # idempotent: same version+config as restricted_doc's call
    return nfse.prepare(db, restricted_source_b[1].id, fully_configured_settings, users["gestor"].id)


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
    municipality must be refused BEFORE a NationalNfseProvider is ever
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
    a REAL, per-document NationalNfseProvider through the dispatch module
    on its own — the only test seam is the transport (FakeTransport), never
    the provider-resolution logic itself.
    """
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[TransportResponse(status_code=200, body=b"<nfse-ok/>")])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    doc = nfse.operate(db, restricted_doc.id, "issue", "m29-full-wiring-1", fully_configured_settings,
                       users["gestor"].id)
    assert len(fake.received) == 1  # the transport really was called exactly once
    # A well-formed 2xx response classifies as ISSUED at the transport
    # level, but the restricted provider never returns an external_id (no
    # NFS-e access-key extraction exists yet — see the M29 report) and
    # nfse._normalized() already refuses to treat a success-without-id as
    # anything but UNCERTAIN, exactly as it would for any other provider.
    # This is the correct, honest, fail-closed behavior today, not a bug.
    assert doc.state == "uncertain"


def test_full_wiring_with_real_nfse_response_converges_to_issued(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """M30 — with the response-contract fix, a well-formed NFS-e success body
    (the official contract for POST /nfse) now converges all the way to a
    terminal success, with the REAL government access key recorded as
    external_id — never a MOCK-shaped one, never invented.

    M57 — that terminal state is 'issued', not 'simulated': this document's
    environment is 'restricted', where an NFS-e really does exist at SEFIN.
    """
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[TransportResponse(status_code=200, body=_success_body())])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    doc = nfse.operate(db, restricted_doc.id, "issue", "m30-full-wiring-real-1",
                       fully_configured_settings, users["gestor"].id)
    assert len(fake.received) == 1
    assert doc.state == "issued"
    completed = [a for a in events(db, doc) if a.phase == "completed"]
    assert completed[-1].outcome == "issued"
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
    sent_xml = _sent_dps_xml(fake)
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
    assert _expected_dps_id(1).encode() in _sent_dps_xml(fake, 0)


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


# --------------------------------------------------------- M35 — safe provider HTTP diagnostics


@pytest.mark.parametrize("status,expected_code", [
    (400, "provider_rejected:http_400"),
    (401, "provider_rejected:http_401"),
    (403, "provider_rejected:http_403"),
    (422, "provider_rejected:http_422"),
])
def test_issue_client_error_persists_safe_http_diagnostic(
        monkeypatch, db, users, restricted_doc, fully_configured_settings, status, expected_code):
    """M35 — root-cause investigation of a real REJECTED restricted response
    (M34's synthetic validation) found that FiscalAttempt.error_code carried
    only the fixed generic label 'provider_rejected' — not even the HTTP
    status. This is the fix: still fails closed exactly as before (state
    still 'failed'), just a more specific, still-privacy-safe label."""
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[TransportResponse(status_code=status, body=b"")])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    doc = nfse.operate(db, restricted_doc.id, "issue", f"m35-http-{status}", fully_configured_settings,
                       users["gestor"].id)
    assert doc.state == "failed"  # M33/M34 state-machine behavior unchanged
    completed = [a for a in events(db, doc) if a.phase == "completed"]
    assert completed[-1].outcome == "rejected"
    assert completed[-1].error_code == expected_code


def test_issue_server_error_persists_safe_http_diagnostic_and_stays_uncertain(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """A 500 now gets a more specific error_code than before, but the fiscal
    state machine is untouched: still Outcome.UNCERTAIN, still doc.state
    'uncertain', still reconciliation_required — only the label changes."""
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[TransportResponse(status_code=500, body=b"")])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    doc = nfse.operate(db, restricted_doc.id, "issue", "m35-http-500", fully_configured_settings,
                       users["gestor"].id)
    assert doc.state == "uncertain"
    completed = [a for a in events(db, doc) if a.phase == "completed"]
    assert completed[-1].outcome == "uncertain"
    assert completed[-1].error_code == "provider_server_error:http_500"
    assert completed[-1].reconciliation_required is True


def test_issue_timeout_persists_generic_diagnostic_label(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[TimeoutError("synthetic timeout")])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    doc = nfse.operate(db, restricted_doc.id, "issue", "m35-timeout", fully_configured_settings,
                       users["gestor"].id)
    assert doc.state == "uncertain"
    completed = [a for a in events(db, doc) if a.phase == "completed"]
    assert completed[-1].error_code == "provider_timeout"


def test_issue_connection_error_persists_generic_diagnostic_label(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[ConnectionError("synthetic connection error")])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    doc = nfse.operate(db, restricted_doc.id, "issue", "m35-conn-error", fully_configured_settings,
                       users["gestor"].id)
    assert doc.state == "uncertain"
    completed = [a for a in events(db, doc) if a.phase == "completed"]
    assert completed[-1].error_code == "provider_connection_error"


def test_issue_success_error_code_unchanged(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """M33/M34 behavior unchanged: a real success still records error_code=None."""
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[TransportResponse(status_code=200, body=_success_body())])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    doc = nfse.operate(db, restricted_doc.id, "issue", "m35-success", fully_configured_settings,
                       users["gestor"].id)
    assert doc.state == "issued"
    completed = [a for a in events(db, doc) if a.phase == "completed"]
    assert completed[-1].error_code is None


def test_reconcile_404_not_found_error_code_unchanged(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """M33/M34 behavior unchanged: a reconciliation confirming absence still
    leaves error_code None — the new diagnostic labeling only applies to
    rejected/uncertain outcomes, never to a confirmed-absence NOT_FOUND."""
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[
        TimeoutError("issue #1 -> uncertain"),
        TransportResponse(status_code=404, body=b""),
    ])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    doc = nfse.operate(db, restricted_doc.id, "issue", "m35-precede-reconcile",
                       fully_configured_settings, users["gestor"].id)
    assert doc.state == "uncertain"

    doc = nfse.operate(db, restricted_doc.id, "reconcile", "m35-reconcile-404",
                       fully_configured_settings, users["gestor"].id)
    assert doc.state == "failed"
    completed = [a for a in events(db, doc) if a.phase == "completed"]
    assert completed[-1].outcome == "not_found"
    assert completed[-1].error_code is None


def test_rejection_diagnostic_never_leaks_response_body_content(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """Even if a 4xx body contained something sensitive-looking, the
    diagnostic persisted to the DB never parses body content — only the
    HTTP status number, end to end through nfse.operate()."""
    _validate_active_config(db, users)
    sensitive_body = b'{"cpf":"12345678901","mensagem":"segredo interno da rejeicao","codigo":"X99"}'
    fake = FakeTransport(responses=[TransportResponse(status_code=400, body=sensitive_body)])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    doc = nfse.operate(db, restricted_doc.id, "issue", "m35-leak-check", fully_configured_settings,
                       users["gestor"].id)
    completed = [a for a in events(db, doc) if a.phase == "completed"]
    error_code = completed[-1].error_code
    assert error_code == "provider_rejected:http_400"
    for leaked in (b"12345678901", b"segredo", b"X99", b"cpf", b"mensagem"):
        assert leaked not in error_code.encode()


# --------------------------------------------------------- M36 — durable, globally-unique DPS numbering


def test_document_a_gets_dps_1_document_b_gets_dps_2_end_to_end(
        monkeypatch, db, users, restricted_doc, restricted_doc_b, fully_configured_settings):
    """Root-cause regression (M36): two DIFFERENT documents, same issuer
    scope, must never both submit numero_dps=1 — end to end, through the
    real dispatch path, asserted on the actual outbound XML."""
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[
        TransportResponse(status_code=200, body=b"<nfse-ok/>"),
        TransportResponse(status_code=200, body=b"<nfse-ok/>"),
    ])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    nfse.operate(db, restricted_doc.id, "issue", "m36-doc-a", fully_configured_settings, users["gestor"].id)
    nfse.operate(db, restricted_doc_b.id, "issue", "m36-doc-b", fully_configured_settings, users["gestor"].id)

    sent_a = _sent_dps_xml(fake, 0)
    sent_b = _sent_dps_xml(fake, 1)
    assert _expected_dps_id(1).encode() in sent_a
    assert _expected_dps_id(2).encode() in sent_b
    assert sent_a != sent_b


def test_rejected_document_a_does_not_free_its_number(
        monkeypatch, db, users, restricted_doc, restricted_doc_b, fully_configured_settings):
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[
        TransportResponse(status_code=400, body=b""),  # A -> rejected
        TransportResponse(status_code=200, body=b"<nfse-ok/>"),  # B -> issued
    ])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    doc_a = nfse.operate(db, restricted_doc.id, "issue", "m36-rej-a", fully_configured_settings,
                         users["gestor"].id)
    assert doc_a.state == "failed"
    nfse.operate(db, restricted_doc_b.id, "issue", "m36-rej-b", fully_configured_settings, users["gestor"].id)

    assert _expected_dps_id(1).encode() in _sent_dps_xml(fake, 0)  # A kept #1
    assert _expected_dps_id(2).encode() in _sent_dps_xml(fake, 1)  # B got #2, never reused A's


def test_uncertain_document_a_does_not_free_its_number(
        monkeypatch, db, users, restricted_doc, restricted_doc_b, fully_configured_settings):
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[
        TimeoutError("A -> uncertain"),
        TransportResponse(status_code=200, body=b"<nfse-ok/>"),  # B -> issued
    ])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    doc_a = nfse.operate(db, restricted_doc.id, "issue", "m36-unc-a", fully_configured_settings,
                         users["gestor"].id)
    assert doc_a.state == "uncertain"
    nfse.operate(db, restricted_doc_b.id, "issue", "m36-unc-b", fully_configured_settings, users["gestor"].id)

    assert _expected_dps_id(2).encode() in _sent_dps_xml(fake, 1)  # B got #2, never A's #1


def test_reprocess_retry_after_rejection_keeps_original_dps_number(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """Idempotent retry of the SAME document (M15's `reprocessar` path, a
    NEW idempotency key over the SAME failed document) must reuse the
    original numero_dps — never mint a second one."""
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[
        TransportResponse(status_code=400, body=b""),        # attempt #1 -> rejected
        TransportResponse(status_code=200, body=_success_body()),  # attempt #2 (reprocess) -> issued
    ])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    doc = nfse.operate(db, restricted_doc.id, "issue", "m36-retry-1", fully_configured_settings,
                       users["gestor"].id)
    assert doc.state == "failed"
    doc = nfse.operate(db, restricted_doc.id, "issue", "m36-retry-2", fully_configured_settings,
                       users["gestor"].id, reprocess=True)
    assert doc.state == "issued"

    assert _expected_dps_id(1).encode() in _sent_dps_xml(fake, 0)
    assert _expected_dps_id(1).encode() in _sent_dps_xml(fake, 1)  # SAME number, not #2


def test_reconcile_after_rejection_uses_documents_original_dps_number(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """M33's contract, now derived from the M36 durable allocation instead
    of FiscalAttempt.number: reconciliation always targets the ONE number
    this document was ever assigned."""
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[
        TimeoutError("issue #1 -> uncertain"),
        TransportResponse(status_code=404, body=b""),  # reconcile #2
    ])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    doc = nfse.operate(db, restricted_doc.id, "issue", "m36-reconcile-1", fully_configured_settings,
                       users["gestor"].id)
    assert doc.state == "uncertain"
    doc = nfse.operate(db, restricted_doc.id, "reconcile", "m36-reconcile-2", fully_configured_settings,
                       users["gestor"].id)
    assert doc.state == "failed"

    reconcile_request = fake.received[1]
    assert reconcile_request.path == PATH_GET_DPS.format(dps_id=_expected_dps_id(1))


def test_dps_number_allocation_persisted_independently_of_fiscal_attempt_number(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """Explicit proof of the M36 requirement: numero_dps no longer tracks
    FiscalAttempt.number at all. Three attempts on the SAME document (all
    uncertain, so FiscalAttempt.number climbs to 1, 2, 3) must all still
    submit numero_dps=1 — never 2 or 3."""
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[TimeoutError("t")] * 3)
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    nfse.operate(db, restricted_doc.id, "issue", "m36-fa-1", fully_configured_settings, users["gestor"].id)
    nfse.operate(db, restricted_doc.id, "reconcile", "m36-fa-2", fully_configured_settings, users["gestor"].id)
    nfse.operate(db, restricted_doc.id, "reconcile", "m36-fa-3", fully_configured_settings, users["gestor"].id)

    attempts = [a for a in events(db, doc=restricted_doc) if a.phase == "started"]
    assert [a.number for a in attempts] == [1, 2, 3]  # FiscalAttempt sequence still climbs
    expected_path = PATH_GET_DPS.format(dps_id=_expected_dps_id(1))
    assert fake.received[1].path == expected_path  # reconcile #2 targets DPS #1
    assert fake.received[2].path == expected_path  # reconcile #3 also targets DPS #1, never #2/#3


# --------------------------------------------- M36 (continued) — preflight/issue share one DPS identity


def test_preflight_tsiddps_equals_issue_tsiddps_for_same_document(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """Root-cause regression: run_offline_preflight() used to build its
    staged/signed DPS with an independent, hardcoded numero_dps_display="1"
    default — completely disconnected from the durable allocator the real
    issue path uses. The signed artifact a human reviews before
    authorizing a real POST must be the SAME identity that POST actually
    submits."""
    from app.services.nfse_national.preflight import run_offline_preflight

    _validate_active_config(db, users)
    preflight_result = run_offline_preflight(db, restricted_doc.id, fully_configured_settings,
                                             users["gestor"].id)
    assert preflight_result.status == "ready_to_send"
    stored_dir = fully_configured_settings.resolved_fiscal_artifacts_storage_dir()
    signed = next(a for a in preflight_result.staged_artifacts if a.kind == "dps_signed_xml")
    preflight_xml = (stored_dir / signed.relative_path).read_bytes()

    fake = FakeTransport(responses=[TransportResponse(status_code=200, body=_success_body())])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)
    doc = nfse.operate(db, restricted_doc.id, "issue", "m36-preflight-vs-issue",
                       fully_configured_settings, users["gestor"].id)
    assert doc.state == "issued"
    issued_xml = _sent_dps_xml(fake)

    expected_id = _expected_dps_id(1)
    assert expected_id.encode() in preflight_xml
    assert expected_id.encode() in issued_xml


def test_preflight_issue_and_reconcile_all_share_the_same_dps_number(
        monkeypatch, db, users, restricted_doc, restricted_doc_b, fully_configured_settings):
    """Full chain, both documents: preflight allocates first, real issue
    (rejected) reuses it, reconcile reuses it too — and a SECOND document's
    preflight never collides with the first's."""
    from app.services.nfse_national.preflight import run_offline_preflight

    _validate_active_config(db, users)
    preflight_a = run_offline_preflight(db, restricted_doc.id, fully_configured_settings, users["gestor"].id)
    assert preflight_a.status == "ready_to_send"
    signed_a = next(a for a in preflight_a.staged_artifacts if a.kind == "dps_signed_xml")
    xml_a = (fully_configured_settings.resolved_fiscal_artifacts_storage_dir()
            / signed_a.relative_path).read_bytes()
    assert _expected_dps_id(1).encode() in xml_a

    preflight_b = run_offline_preflight(db, restricted_doc_b.id, fully_configured_settings, users["gestor"].id)
    assert preflight_b.status == "ready_to_send"
    signed_b = next(a for a in preflight_b.staged_artifacts if a.kind == "dps_signed_xml")
    xml_b = (fully_configured_settings.resolved_fiscal_artifacts_storage_dir()
            / signed_b.relative_path).read_bytes()
    assert _expected_dps_id(2).encode() in xml_b  # never collides with A's #1

    fake = FakeTransport(responses=[
        TimeoutError("issue -> uncertain"),
        TransportResponse(status_code=404, body=b""),  # reconcile -> confirmed absent
    ])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)
    doc = nfse.operate(db, restricted_doc.id, "issue", "m36-chain-issue", fully_configured_settings,
                       users["gestor"].id)
    assert doc.state == "uncertain"
    doc = nfse.operate(db, restricted_doc.id, "reconcile", "m36-chain-reconcile", fully_configured_settings,
                       users["gestor"].id)
    assert doc.state == "failed"

    assert _expected_dps_id(1).encode() in _sent_dps_xml(fake, 0)  # issue used A's preflight number
    assert fake.received[1].path == PATH_GET_DPS.format(dps_id=_expected_dps_id(1))  # reconcile too


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
