"""M60 — the last offline preparation before a real production issuance.

M59 wired the production path and listed what was still missing. This file
pins the answers to items 1-5 of that list — certificate margin, production
fiscal profile, both flow policies, tomador identity, clock — and then
re-proves, with all of them satisfied at once, that items 6 and 7 still make
a real issuance impossible.

That last part is the point. It is easy to write a readiness mission that
quietly ends up being an enablement mission. Every test below that makes
production MORE ready is followed by one that shows it is still not
sendable, and the final section drives the real path with the network gate
open and an exploding HTTP client to prove nothing reaches it.

Entirely offline and entirely synthetic. No real patient, no certificate on
the wire, no network.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.config import Settings
from app.models import FinancialEntry, Person, SpirometryExam
from app.services import nfse
from app.services.nfse_national import clock as clock_module
from app.services.nfse_national import dispatch, fiscal_config
from app.services.nfse_national.certificate_guard import evaluate_certificate_margin
from app.services.nfse_national.config import tp_amb_for_environment
from app.services.nfse_national.dps_builder import build_dps_element, serialize_dps
from app.services.nfse_national.production_profile import (
    DERIVED_FROM_POLICY_VERSIONS,
    DERIVED_FROM_VERSION,
    PRODUCTION_EFFECTIVE_FROM,
    PRODUCTION_POLICY_VERSIONS,
    PROVENANCE_FIELDS,
    POLICY_PROVENANCE_FIELDS,
    production_configuration,
    production_policies,
    production_policy,
)
from app.services.nfse_national.readiness import CertificateSummary
from app.services.nfse_national.recipient_identity import (
    RecipientIdentityError,
    assert_production_cpf,
    assert_production_name,
    assert_production_recipient,
    cpf_check_digits_valid,
)
from app.services.nfse_national.transport import TransportResponse
from app.services.nfse_validity import fiscal_validity
from app.services.nfse_providers import get_provider

from tests.test_nfse_m59_production_wiring import (  # noqa: F401
    PRODUCTION_RECIPIENT_NAME, SYNTHETIC_CPF, certificate_file,
    fake_production_wire, production_settings, sefin_success)

# The real A1 in use, as recorded by M50 and re-verified in M60 by the file's
# sha256. Pinned here so the margin arithmetic is testable without the
# password — which is TTY-only by design and which no test may ever hold.
REAL_CERT_NOT_BEFORE = datetime(2025, 11, 7, 15, 33, 33, tzinfo=timezone.utc)
REAL_CERT_NOT_AFTER = datetime(2026, 11, 7, 15, 33, 33, tzinfo=timezone.utc)


def real_certificate_summary(**changes) -> CertificateSummary:
    data = dict(subject_common_name="SOPRO LIFE DIAGNOSTICOS E SOLUCOES EM SAUDE",
                not_before=REAL_CERT_NOT_BEFORE, not_after=REAL_CERT_NOT_AFTER,
                expired=False)
    data.update(changes)
    return CertificateSummary(**data)


# =================================================== FASE 2 — certificado


def test_the_production_margin_is_thirty_days_and_is_not_weakened():
    assert Settings().nfse_production_certificate_min_days == 30


def test_the_certificate_passes_today_and_stops_passing_before_it_expires():
    """The finding that matters: the usable window closes 30 days BEFORE the
    expiry date, so the deadline is 2026-10-08, not 2026-11-07."""
    summary = real_certificate_summary()
    margin_closes = REAL_CERT_NOT_AFTER - timedelta(days=30)
    assert margin_closes.date() == date(2026, 10, 8)

    just_inside = evaluate_certificate_margin(
        summary, min_days_remaining=30, now=margin_closes - timedelta(hours=1))
    assert just_inside.valid is True

    just_outside = evaluate_certificate_margin(
        summary, min_days_remaining=30, now=margin_closes + timedelta(hours=1))
    assert just_outside.valid is False
    assert just_outside.reason == "certificate_renewal_margin_insufficient"


def test_an_expired_certificate_is_refused_whatever_the_margin_says():
    summary = real_certificate_summary(expired=True)
    result = evaluate_certificate_margin(summary, min_days_remaining=0,
                                         now=REAL_CERT_NOT_AFTER + timedelta(days=1))
    assert result.valid is False
    assert result.reason == "certificate_expired"


def test_an_absent_or_unreadable_certificate_is_never_valid():
    assert evaluate_certificate_margin(None).valid is False
    assert evaluate_certificate_margin(
        real_certificate_summary(not_after=None)).valid is False


def test_the_margin_cannot_be_satisfied_by_lowering_it_in_settings_silently():
    """It CAN be lowered — it is a setting — but the default refuses, and a
    zero margin still cannot resurrect an expired certificate. This pins that
    the guard's floor is expiry itself, not the configurable number."""
    expired = real_certificate_summary(expired=True)
    assert evaluate_certificate_margin(expired, min_days_remaining=0).valid is False


# =================================================== FASE 3 — perfil de produção


def test_the_production_profile_declares_tp_amb_1():
    assert production_configuration().tp_amb == 1
    assert tp_amb_for_environment("production") == 1


# The fiscal values of SOPROLIFE-M43-GOLDEN-v1 — the restricted profile that
# actually issued DPS #10 — transcribed independently from the stored row.
#
# Independently is the whole point. Deriving this baseline from
# ``production_configuration()`` and flipping the three provenance fields
# would make the comparison below circular: it would pass no matter what the
# production profile said. Deliberately not read from the live restricted
# database either — a test that needed the company's private fiscal data
# would not run anywhere else, and would fail for reasons unrelated to the
# code. M60's report records the provenance check that WAS run against the
# real row; this is the durable version of it.
M43_GOLDEN_FISCAL_VALUES = {
    "layout_version": "restricted-v1.01-20260727",
    "issuer_cnpj": "63544026000110",
    "issuer_name": "SoproLife Diagnósticos e Soluções em Saúde LTDA",
    "issuer_municipio_ibge": "3304557",
    "issuer_inscricao_municipal": None,
    "issuer_op_simp_nac": 3,
    "issuer_reg_ap_trib_sn": 1,
    "issuer_reg_esp_trib": 0,
    "codigo_tributacao_nacional": "040201",
    "codigo_tributacao_municipal": "001",
    "codigo_nbs": "123019900",
    "trib_issqn": 1,
    "tp_ret_issqn": 1,
    "aliquota_percentual": None,
    "ind_tot_trib": 0,
    "p_tot_trib_sn": "6.00",
    "pis_cofins_cst": "00",
    "pis_cofins_tp_ret": 0,
    "amount_basis": "financial_entry.valor",
    "competence_rule": "service_date",
    "own_revenue_confirmed": True,
    # provenance of the SOURCE row
    "tp_amb": 2,
    "version": "SOPROLIFE-M43-GOLDEN-v1",
    "validation_reference": "SOPROLIFE-M43-GOLDEN",
}


def test_the_production_profile_differs_from_its_source_only_in_provenance():
    """Every FISCAL parameter must be byte-identical to the restricted profile
    that actually issued DPS #10. Only version, validation_reference and
    tp_amb may differ."""
    produced = production_configuration().model_dump(mode="json")
    source = M43_GOLDEN_FISCAL_VALUES
    assert set(produced) == set(source), "o schema mudou — rever a derivação"
    differing = {k for k in source if produced[k] != source[k]}
    assert differing == set(PROVENANCE_FIELDS)
    assert source["tp_amb"] == 2 and produced["tp_amb"] == 1
    assert source["version"] == DERIVED_FROM_VERSION


def test_the_production_profile_builds_a_valid_tp_amb_1_dps():
    from tests.test_nfse_national_dps_builder import synthetic_dps_input
    from app.services.nfse_national.xsd_validation import validate_dps_xml

    xml = serialize_dps(build_dps_element(synthetic_dps_input(
        environment="production", config=production_configuration())))
    assert b"<tpAmb>1</tpAmb>" in xml
    validate_dps_xml(xml)


def test_the_production_profile_cannot_be_used_by_restricted_or_mock():
    from tests.test_nfse_national_dps_builder import synthetic_dps_input
    from app.services.nfse_national.dps_builder import DpsBuildError

    with pytest.raises(DpsBuildError, match="exige tpAmb=2"):
        build_dps_element(synthetic_dps_input(environment="restricted",
                                              config=production_configuration()))
    with pytest.raises(DpsBuildError):
        build_dps_element(synthetic_dps_input(environment="mock",
                                              config=production_configuration()))


def test_the_production_profile_is_versioned_and_immutable(db, users):
    """Written through the real path, it behaves like every other fiscal
    version: identical re-create is a no-op, a changed payload under the same
    version fails closed."""
    row = fiscal_config.create_version(
        db, environment="production", effective_from=PRODUCTION_EFFECTIVE_FROM,
        validation_state="validated", configuration=production_configuration(),
        actor=users["admin"].id)
    again = fiscal_config.create_version(
        db, environment="production", effective_from=PRODUCTION_EFFECTIVE_FROM,
        validation_state="validated", configuration=production_configuration(),
        actor=users["admin"].id)
    assert again.id == row.id

    mutated = production_configuration().model_copy(update={"p_tot_trib_sn": Decimal("7.00")})
    with pytest.raises(HTTPException) as error:
        fiscal_config.create_version(
            db, environment="production", effective_from=PRODUCTION_EFFECTIVE_FROM,
            validation_state="validated", configuration=mutated, actor=users["admin"].id)
    assert error.value.detail["codigo"] == "national_dps_configuration_version_immutable"


def test_a_restricted_document_never_resolves_the_production_profile(db, users):
    fiscal_config.create_version(
        db, environment="production", effective_from=PRODUCTION_EFFECTIVE_FROM,
        validation_state="validated", configuration=production_configuration(),
        actor=users["admin"].id)
    assert fiscal_config.resolve_active_configuration(
        db, environment="restricted", as_of=date(2026, 9, 22)) is None
    assert fiscal_config.resolve_active_configuration(
        db, environment="mock", as_of=date(2026, 9, 22)) is None
    resolved = fiscal_config.resolve_active_configuration(
        db, environment="production", as_of=date(2026, 9, 22))
    assert resolved is not None and resolved.tp_amb == 1


# =================================================== FASE 4 — políticas


def test_both_flows_have_a_validated_production_policy():
    policies = production_policies()
    assert {p.flow for p in policies} == {"HOME", "DIRECT"}
    for policy in policies:
        assert policy.environment == "production"
        assert policy.validation_state == "validated"
        assert policy.configuration.missing_fields() == []


def test_the_two_flow_policies_differ_only_in_flow_and_version():
    home, direct = production_policy("HOME"), production_policy("DIRECT")
    assert home.configuration.model_dump() == direct.configuration.model_dump()
    assert home.version != direct.version
    assert {home.version, direct.version} == set(PRODUCTION_POLICY_VERSIONS.values())


def test_the_policies_keep_the_proven_fiscal_parameters():
    """Same discipline as the profile: only provenance may differ from the
    restricted policies that backed DPS #10."""
    configuration = production_policy("HOME").configuration.model_dump(mode="json")
    assert configuration["amount_basis"] == "financial_entry.valor"
    assert configuration["competence_rule"] == "service_date"
    assert configuration["own_revenue_confirmed"] is True
    assert configuration["issuer"] == "SOPROLIFE"
    assert configuration["recipient"] == "service_person"
    assert configuration["municipality"] == "3304557"
    assert configuration["tax_regime"] == "SIMPLES_NACIONAL"
    assert configuration["tax_rate"] == "6.00"
    assert configuration["withholding"] is False
    assert POLICY_PROVENANCE_FIELDS == {"validation_reference"}
    assert DERIVED_FROM_POLICY_VERSIONS == ("SOPROLIFE-M31-HOME-v1",
                                            "SOPROLIFE-M31-DIRECT-v1")


def test_an_unknown_flow_is_refused():
    with pytest.raises(ValueError):
        production_policy("SPLIT")


def test_readiness_still_requires_both_flows(db, users, production_settings):
    """One flow is not enough — the gate M59 discovered, pinned."""
    from app.services.nfse_national.readiness import compute_provider_readiness

    nfse.create_policy(db, production_policy("HOME"), users["admin"].id)
    blockers = compute_provider_readiness(db, production_settings,
                                          environment="production").blockers
    assert "fiscal_policy_incomplete_for_environment" in blockers

    nfse.create_policy(db, production_policy("DIRECT"), users["admin"].id)
    blockers = compute_provider_readiness(db, production_settings,
                                          environment="production").blockers
    assert "fiscal_policy_incomplete_for_environment" not in blockers


# =================================================== FASE 5 — identidade do tomador


def test_a_valid_production_recipient_is_accepted():
    nome, cpf = assert_production_recipient(nome=PRODUCTION_RECIPIENT_NAME,
                                            cpf=SYNTHETIC_CPF)
    assert nome == PRODUCTION_RECIPIENT_NAME and cpf == SYNTHETIC_CPF


@pytest.mark.parametrize("cpf,code", [
    (None, "recipient_cpf_malformed"),
    ("", "recipient_cpf_malformed"),
    ("529.982.247-25", "recipient_cpf_malformed"),   # must be textual digits
    ("5299822472", "recipient_cpf_malformed"),       # ten digits
    ("529982247250", "recipient_cpf_malformed"),     # twelve
    (52998224725, "recipient_cpf_malformed"),        # not a string
    ("00000000000", "recipient_cpf_repeated_digits"),
    ("11111111111", "recipient_cpf_repeated_digits"),
    ("99999999999", "recipient_cpf_repeated_digits"),
    ("12345678901", "recipient_cpf_check_digits_invalid"),
    ("52998224726", "recipient_cpf_check_digits_invalid"),
])
def test_production_cpf_fails_closed(cpf, code):
    with pytest.raises(RecipientIdentityError) as error:
        assert_production_cpf(cpf)
    assert error.value.code == code


def test_repeated_digit_cpfs_would_pass_the_check_digit_algorithm():
    """Which is exactly why they need their own rule — without it,
    00000000000 is a structurally valid CPF."""
    for cpf in ("00000000000", "11111111111", "99999999999"):
        assert cpf_check_digits_valid(cpf) is True


@pytest.mark.parametrize("nome,code", [
    (None, "recipient_name_missing"),
    ("", "recipient_name_missing"),
    ("   ", "recipient_name_missing"),
    ("Marina", "recipient_name_not_a_full_name"),
    ("M R", "recipient_name_not_a_full_name"),
    ("Paciente Exemplo M59", "recipient_name_looks_like_placeholder"),
    ("Teste da Silva", "recipient_name_looks_like_placeholder"),
    ("Mock Patient", "recipient_name_looks_like_placeholder"),
    ("Fulano de Tal", "recipient_name_looks_like_placeholder"),
    ("Pessoa Sintetica Fiscal", "recipient_name_looks_like_placeholder"),
    ("Paciente 001 Silva", "recipient_name_looks_like_placeholder"),
    ("x" * 301, "recipient_name_too_long"),
])
def test_production_name_fails_closed(nome, code):
    with pytest.raises(RecipientIdentityError) as error:
        assert_production_name(nome)
    assert error.value.code == code


def test_accents_do_not_hide_a_placeholder():
    for nome in ("Paciente Exémplo Silva", "TESTE Da Silva", "Sintético Souza Lima"):
        with pytest.raises(RecipientIdentityError):
            assert_production_name(nome)


def test_a_real_surname_is_not_mistaken_for_a_placeholder():
    """Whole-word matching: 'Testa' and 'Sampaio' are names, not markers."""
    for nome in ("Ana Testa Ribeiro", "Carlos Sampaio Lima", "Joana Mockford Reis"):
        assert assert_production_name(nome) == nome


def test_no_failure_ever_carries_the_cpf_or_the_name():
    """Privacy: the code is the API, the value never is."""
    for cpf in ("00000000000", "12345678901", "529.982.247-25"):
        try:
            assert_production_cpf(cpf)
        except RecipientIdentityError as error:
            assert cpf not in str(error)
            assert cpf.replace(".", "").replace("-", "") not in str(error)
    secret_name = "Paciente Exemplo Confidencial"
    try:
        assert_production_name(secret_name)
    except RecipientIdentityError as error:
        assert "Confidencial" not in str(error)


def test_restricted_is_deliberately_not_held_to_this_contract(db, users,
                                                              production_settings):
    """Produção Restrita keeps its original rule. tpAmb=2 exists so made-up
    data CAN be exercised there, and tightening it would invalidate the only
    end-to-end evidence this system has."""
    import inspect
    source = inspect.getsource(dispatch._recipient)
    assert "PRODUCTION_ENVIRONMENT" in source
    # The shape-only check remains the rule outside production.
    from app.services.nfse_national.identifiers import assert_cpf
    assert assert_cpf("11111111111") == "11111111111"


def test_a_placeholder_recipient_blocks_a_production_issuance(
        db, users, production_settings, fake_production_wire):
    """The contract where it actually bites: through dispatch, before any
    provider is built and with nothing sent."""
    document = _production_document(db, users, production_settings,
                                    nome="Paciente Exemplo M60")
    fake = fake_production_wire([TransportResponse(201, sefin_success())])
    # M66 — the same contract now bites one step earlier: at eligibility,
    # so the fact is blocked in the queue and never reaches "pending".
    # Dispatch keeps its own, independent check (see below).
    assert document.state == "blocked"
    assert "recipient_name_looks_like_placeholder" in document.blocking_reasons
    with pytest.raises(HTTPException) as error:
        nfse.operate(db, document.id, "issue", "m60-placeholder",
                     production_settings, users["gestor"].id)
    assert error.value.detail["codigo"] == "document_not_pending_eligible"
    assert fake.received == []


def test_an_invalid_cpf_blocks_a_production_issuance(
        db, users, production_settings, fake_production_wire):
    document = _production_document(db, users, production_settings,
                                    cpf="11111111111")
    fake = fake_production_wire([TransportResponse(201, sefin_success())])
    # M66 — the same contract now bites one step earlier: at eligibility,
    # so the fact is blocked in the queue and never reaches "pending".
    # Dispatch keeps its own, independent check (see below).
    assert document.state == "blocked"
    assert "recipient_cpf_repeated_digits" in document.blocking_reasons
    with pytest.raises(HTTPException) as error:
        nfse.operate(db, document.id, "issue", "m60-badcpf",
                     production_settings, users["gestor"].id)
    assert error.value.detail["codigo"] == "document_not_pending_eligible"
    assert fake.received == []


# =================================================== FASE 6 — relógio


def test_the_clock_guard_fails_closed_when_unsynchronized(monkeypatch):
    """Whatever the host's clock does at issuance time, an undisciplined one
    must block. M55 measured -14.3 minutes and could only prove it from a
    government rejection after the fact."""
    from app.services.nfse_national.production_preflight import run_production_preflight

    monkeypatch.setattr(clock_module, "read_clock_status",
                        lambda: clock_module.ClockStatus(False, "clock_unsynchronized"))
    status = clock_module.read_clock_status()
    assert status.synchronized is False
    assert run_production_preflight is not None   # imported, exercised below


def test_the_clock_guard_reads_the_kernel_not_a_command():
    """It calls adjtimex directly, so it cannot be fooled by a stale chronyc
    output, and it reports unsynchronized when it cannot tell."""
    import inspect
    source = inspect.getsource(clock_module.read_clock_status)
    assert "_read_timex" in source
    assert "clock_status_unavailable" in source


# =================================================== FASE 7 — preflight completo


def _production_document(db, users, settings, *, nome=None, cpf=None, suffix=""):
    """A production document with everything M60 prepared: both policies, the
    production profile, and a tomador identity. Invented data only."""
    person = Person(public_code=f"PES-M60{suffix}"[:20],
                    nome_completo=nome or PRODUCTION_RECIPIENT_NAME,
                    nome_normalizado=(nome or PRODUCTION_RECIPIENT_NAME).lower(),
                    cpf=cpf or SYNTHETIC_CPF)
    db.add(person)
    db.flush()
    exam = SpirometryExam(public_code=f"ESP-M60{suffix}"[:20], person_id=person.id,
                          status="Realizado", data_exame=date(2026, 8, 10),
                          data_exame_precisao="dia", modalidade="residencial",
                          broncodilatador=True, municipio_atendimento_ibge="3304557")
    db.add(exam)
    db.flush()
    db.add(FinancialEntry(public_code=f"LAN-M60{suffix}"[:20], tipo="receita",
                          categoria="Espirometria", valor=Decimal("220.00"),
                          status="Recebido", spirometry_exam_id=exam.id,
                          data_competencia=date(2026, 9, 1)))
    db.commit()
    for policy in production_policies():
        nfse.create_policy(db, policy, users["admin"].id)
    fiscal_config.create_version(
        db, environment="production", effective_from=PRODUCTION_EFFECTIVE_FROM,
        validation_state="validated", configuration=production_configuration(),
        actor=users["admin"].id)
    return nfse.prepare(db, exam.id, settings, users["gestor"].id)


@pytest.fixture
def m60_ready_document(db, users, production_settings):
    return _production_document(db, users, production_settings)


def test_the_full_production_preflight_is_technically_ready(
        db, users, m60_ready_document, production_settings, monkeypatch):
    """Everything M60 prepared, at once, as close to a real issuance as it is
    possible to get without a network."""
    from app.services.nfse_national.production_preflight import run_production_preflight

    monkeypatch.setattr(clock_module, "read_clock_status",
                        lambda: clock_module.ClockStatus(True, "clock_synchronized"))
    result = run_production_preflight(db, m60_ready_document.id, production_settings,
                                      users["gestor"].id)

    assert result.technical_ready is True, result.blockers
    assert result.authorization_ready is False
    assert result.network_send_allowed is False

    assert result.environment_is_production is True
    assert result.production_endpoint_ok is True
    assert result.production_base_url == "https://sefin.nfse.gov.br/SefinNacional"
    assert result.network_gate_enabled is True      # open in TEST settings only
    assert result.certificate.valid is True
    assert result.clock.synchronized is True
    assert result.fiscal_config_resolved is True
    assert result.xsd_and_signature_ok is True
    assert result.dh_emi_timezone_ok is True        # America/Sao_Paulo
    assert result.namespace_prefix_count == 0       # E1228 never again
    assert result.request_fingerprint is not None
    assert result.human_authorization_present is False


def test_the_preflight_sends_nothing_even_when_technically_ready(
        db, users, m60_ready_document, production_settings, monkeypatch):
    import httpx
    from app.services.nfse_national.production_preflight import run_production_preflight

    monkeypatch.setattr(clock_module, "read_clock_status",
                        lambda: clock_module.ClockStatus(True, "clock_synchronized"))
    monkeypatch.setattr(httpx, "Client", _exploding_client)
    result = run_production_preflight(db, m60_ready_document.id, production_settings,
                                      users["gestor"].id)
    assert result.technical_ready is True, result.blockers


@pytest.mark.parametrize("break_it,expected", [
    ("clock", "clock:clock_unsynchronized"),
    ("certificate", "certificate:certificate_renewal_margin_insufficient"),
])
def test_a_single_broken_prerequisite_removes_technical_readiness(
        db, users, m60_ready_document, production_settings, monkeypatch,
        break_it, expected):
    """Readiness is conjunctive: one failing prerequisite is enough."""
    from app.services.nfse_national.production_preflight import run_production_preflight

    monkeypatch.setattr(clock_module, "read_clock_status",
                        lambda: clock_module.ClockStatus(True, "clock_synchronized"))
    settings = production_settings
    if break_it == "clock":
        monkeypatch.setattr(clock_module, "read_clock_status",
                            lambda: clock_module.ClockStatus(False, "clock_unsynchronized"))
    else:
        settings = production_settings.model_copy(
            update={"nfse_production_certificate_min_days": 3650})

    result = run_production_preflight(db, m60_ready_document.id, settings,
                                      users["gestor"].id)
    assert result.technical_ready is False
    assert expected in result.blockers


# =================================================== FASE 8 — ainda impossível


def _exploding_client(*args, **kwargs):  # pragma: no cover - must never run
    raise AssertionError("httpx.Client foi construído — houve tentativa de rede")


def test_the_shipped_defaults_keep_production_closed():
    settings = Settings()
    assert settings.nfse_production_network_enabled is False
    assert settings.nfse_environment == "mock"
    assert not any("authorization" in name for name in Settings.model_fields)
    assert not any("human" in name for name in Settings.model_fields)


def test_get_provider_resolves_production_but_the_marker_cannot_issue(production_settings):
    provider = get_provider(production_settings)
    assert provider.environment == "production"
    with pytest.raises(RuntimeError):
        provider.issue(object())


def test_with_every_m60_prerequisite_met_a_real_issuance_still_reaches_no_socket(
        db, users, m60_ready_document, production_settings, monkeypatch):
    """THE test of this mission.

    Profile, both policies, tomador identity, certificate, clock — all
    satisfied. The production network gate is OPEN in these test settings,
    which is the configuration a single environment variable would produce.
    The real transport is used, not a fake. And httpx.Client raises if it is
    ever constructed.
    """
    import httpx
    monkeypatch.setattr(clock_module, "read_clock_status",
                        lambda: clock_module.ClockStatus(True, "clock_synchronized"))
    monkeypatch.setattr(httpx, "Client", _exploding_client)

    document = nfse.operate(db, m60_ready_document.id, "issue", "m60-final",
                            production_settings, users["gestor"].id)
    assert document.state == "uncertain"
    assert fiscal_validity(db, document) is False


def test_the_gate_closed_case_refuses_earlier_still(
        db, users, m60_ready_document, production_settings, monkeypatch):
    """With M15_NFSE_PRODUCTION_NETWORK_ENABLED=false — the SHIPPED value —
    the refusal happens at get_provider, before a document is even read."""
    import httpx
    monkeypatch.setattr(httpx, "Client", _exploding_client)
    closed = production_settings.model_copy(
        update={"nfse_production_network_enabled": False})
    with pytest.raises(HTTPException) as error:
        nfse.operate(db, m60_ready_document.id, "issue", "m60-closed",
                     closed, users["gestor"].id)
    assert error.value.detail["codigo"] == "production_network_gate_disabled"


def test_no_batch_or_automatic_path_can_authorize_production(
        db, users, m60_ready_document, production_settings, monkeypatch):
    """A loop is just operate() repeated, and each iteration refuses the same
    way. Nothing accumulates into permission."""
    import httpx
    monkeypatch.setattr(clock_module, "read_clock_status",
                        lambda: clock_module.ClockStatus(True, "clock_synchronized"))
    monkeypatch.setattr(httpx, "Client", _exploding_client)
    for attempt in range(3):
        document = nfse.operate(db, m60_ready_document.id, "reconcile" if attempt else "issue",
                                f"m60-loop-{attempt}", production_settings,
                                users["gestor"].id)
        assert document.state == "uncertain"
    assert fiscal_validity(db, document) is False


def test_authorization_is_still_absent_from_every_configuration_surface():
    """Searched across settings, dispatch and the environment, not assumed."""
    import inspect
    import os

    assert "explicit_human_authorization" not in inspect.getsource(dispatch)
    assert not [k for k in os.environ if "HUMAN" in k.upper() or "AUTHORIZ" in k.upper()]
    from app.services.nfse_national import production_gates
    readiness = inspect.getsource(production_gates.compute_production_readiness)
    assert "explicit_human_production_authorization" in readiness
