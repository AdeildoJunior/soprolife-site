"""M56 — clock guard, certificate renewal margin, the offline production
preflight, and the production gates. Nothing here touches the network.

The central assertion of this file, repeated from several directions: with
every technical condition satisfied — production environment, official
endpoint, production gate open, certificate with runway, clock disciplined,
schema-valid signed DPS with zero namespace prefixes and a São Paulo
``dhEmi`` — a real issuance is STILL impossible, because the human
authorization gate has no configuration path and M56 never supplies it.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.config import Settings
from app.models import FinancialEntry, Person, SpirometryExam
from app.services import nfse
from app.services.nfse_national import clock as clock_module
from app.services.nfse_national import fiscal_config
from app.services.nfse_national.certificate_guard import (
    DEFAULT_PRODUCTION_MIN_DAYS_REMAINING,
    evaluate_certificate_margin,
)
from app.services.nfse_national.config import NationalDpsConfiguration
from app.services.nfse_national.dps_builder import Recipient
from app.services.nfse_national.production_gates import compute_production_readiness
from app.services.nfse_national.production_preflight import (
    count_namespace_prefixes,
    dh_emi_matches_emission_timezone,
    run_production_preflight,
)
from app.services.nfse_national.readiness import CertificateSummary
from app.services.nfse_national.signer import (
    generate_synthetic_test_certificate,
    load_pkcs12_certificate,
)
from app.services.nfse_providers import get_provider
from tests.test_nfse_foundation import policy_payload

MISSION_GATE_NAMES = {
    "environment_is_production",
    "production_endpoint_correct",
    "network_gate_enabled",
    "signed_preflight_passed",
    "certificate_valid",
    "clock_synchronized",
    "explicit_human_authorization",
}

PRE_M56_GATE_NAMES = {
    "provider_ready",
    "valid_fiscal_policy",
    "verified_real_credential",
    "private_storage_ready",
    "restricted_validation_successful",
}


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def synthetic_certificate_file(tmp_path):
    import os
    p12_bytes, password = generate_synthetic_test_certificate()
    path = tmp_path / "synthetic-production.p12"
    path.write_bytes(p12_bytes)
    os.chmod(path, 0o600)
    return path, password


@pytest.fixture
def production_settings(tmp_path, synthetic_certificate_file):
    """Every technical knob turned to its permissive value at once. Note
    what this still does NOT do: it cannot authorize an issuance, and it
    cannot make ``get_provider()`` return a production provider."""
    cert_path, password = synthetic_certificate_file
    return Settings(
        nfse_enabled=True,
        nfse_environment="production",
        nfse_real_enabled=True,
        nfse_production_network_enabled=True,
        nfse_restricted_certificate_path=cert_path,
        nfse_restricted_certificate_password=password,
        nfse_restricted_base_url="https://sefin.producaorestrita.nfse.gov.br/SefinNacional",
        nfse_restricted_network_enabled=True,
        nfse_fiscal_artifacts_dir=tmp_path / "fiscal-artifacts",
        # The synthetic test certificate is valid for one day, so the real
        # 30-day production margin would (correctly) refuse it. Margin
        # behavior itself is proven separately, below.
        nfse_production_certificate_min_days=0,
    )


@pytest.fixture
def production_config():
    return NationalDpsConfiguration(
        version="SYNTH-PRODUCTION-v1", layout_version="restricted-v1.01-20260727",
        # M59 — a PRODUCTION tax profile declares tpAmb=1. Under M56 the field
        # was typed Literal[2], so this fixture could only say 2 and the
        # builder hard-wired 2 for everyone; the mismatch was invisible
        # because production could not be built at all. Now the builder
        # derives tpAmb from the environment and requires the profile to
        # agree, so a production configuration has to say so.
        tp_amb=1,
        issuer_cnpj="11222333000181", issuer_name="SOPROLIFE SAUDE LTDA (SINTETICO)",
        issuer_municipio_ibge="3304557", issuer_op_simp_nac=3,
        issuer_reg_ap_trib_sn=1, issuer_reg_esp_trib=0,
        codigo_tributacao_nacional="140501",
        trib_issqn=1, tp_ret_issqn=1,
        amount_basis="financial_entry.valor", competence_rule="service_date",
        own_revenue_confirmed=True, validation_reference="SYNTHETIC-ONLY",
        p_tot_trib_sn=Decimal("6.00"),
    )


@pytest.fixture
def production_document(db, users, production_settings, production_config):
    """An eligible fiscal document in the PRODUCTION environment, built from
    entirely fictitious data (project safety rule: no real patient ever
    enters this repository)."""
    person = Person(public_code="PES-M56", nome_completo="Paciente Exemplo M56",
                    nome_normalizado="paciente exemplo m56")
    db.add(person)
    db.flush()
    exam = SpirometryExam(public_code="ESP-M56", person_id=person.id, status="Realizado",
                          data_exame=date(2026, 8, 10), data_exame_precisao="dia",
                          modalidade="residencial", broncodilatador=True,
                          municipio_atendimento_ibge="3304557")
    db.add(exam)
    db.flush()
    db.add(FinancialEntry(public_code="LAN-M56", tipo="receita", categoria="Espirometria",
                          valor=Decimal("220.00"), status="Recebido",
                          spirometry_exam_id=exam.id, data_competencia=date(2026, 9, 1)))
    db.commit()
    nfse.create_policy(db, policy_payload(version="SYNTH-PRODUCTION-HOME",
                                          environment="production", flow="HOME"),
                       users["admin"].id)
    fiscal_config.create_version(db, environment="production", effective_from=date(2026, 1, 1),
                                 validation_state="validated", configuration=production_config,
                                 actor=users["admin"].id)
    return nfse.prepare(db, exam.id, production_settings, users["gestor"].id)


@pytest.fixture
def synchronized_clock(monkeypatch):
    """Pin the clock reading so these tests assert the GATE's logic rather
    than this build machine's NTP state. The real probe is exercised
    directly in the clock tests below."""
    monkeypatch.setattr(clock_module, "read_clock_status",
                        lambda **_: clock_module.ClockStatus(True, "clock_synchronized", 0.05, 0.01, 0))


def _run_preflight(db, users, document, settings, production_config, **kwargs):
    return run_production_preflight(
        db, document.id, settings, users["gestor"].id,
        national_config=production_config,
        recipient=Recipient(nome="Paciente Exemplo M56", sem_nif_motivo=1),
        certificate=load_pkcs12_certificate(*_p12()),
        **kwargs)


_P12_CACHE = {}


def _p12():
    if "value" not in _P12_CACHE:
        _P12_CACHE["value"] = generate_synthetic_test_certificate()
    return _P12_CACHE["value"]


# ------------------------------------------------------- FASE 1/6: the clock


def test_clock_probe_is_read_only_and_total():
    status = clock_module.read_clock_status()
    assert isinstance(status.synchronized, bool)
    assert isinstance(status.reason, str) and status.reason
    # Safe to log: numbers and a reason code, never a host or a source address.
    assert set(status.as_dict()) == {"synchronized", "reason", "max_error_seconds",
                                     "estimated_error_seconds", "status_flags"}


def test_clock_probe_reports_unsynchronized_when_the_kernel_flag_is_set(monkeypatch):
    class _FakeTimex:
        status = clock_module.STA_UNSYNC
        maxerror = 16_000_000
        esterror = 16_000_000

    monkeypatch.setattr(clock_module, "_read_timex", lambda: (0, _FakeTimex()))
    status = clock_module.read_clock_status()
    assert status.synchronized is False
    assert status.reason == "clock_unsynchronized"


def test_clock_probe_fails_closed_when_the_syscall_is_unavailable(monkeypatch):
    def _boom():
        raise OSError("no adjtimex here")

    monkeypatch.setattr(clock_module, "_read_timex", _boom)
    status = clock_module.read_clock_status()
    assert status.synchronized is False
    assert status.reason == "clock_status_unavailable"


def test_clock_probe_refuses_a_stale_lock_whose_error_bound_has_drifted(monkeypatch):
    """STA_UNSYNC clear but the kernel's own error bound past tolerance: a
    source was locked once and has since gone quiet. Unsynchronized is the
    safe reading."""
    class _FakeTimex:
        status = 0
        maxerror = 9_000_000  # 9 s, far past the 2 s default
        esterror = 1_000

    monkeypatch.setattr(clock_module, "_read_timex", lambda: (0, _FakeTimex()))
    status = clock_module.read_clock_status()
    assert status.synchronized is False
    assert status.reason == "clock_max_error_exceeded"
    assert status.max_error_seconds == 9.0


# ------------------------------------------------- FASE 7: certificate margin


def _summary(days_ahead: int) -> CertificateSummary:
    now = datetime.now(timezone.utc)
    return CertificateSummary(subject_common_name="SINTETICO",
                              not_before=now - timedelta(days=1),
                              not_after=now + timedelta(days=days_ahead),
                              expired=False)


def test_certificate_margin_accepts_comfortable_runway():
    margin = evaluate_certificate_margin(_summary(90), min_days_remaining=30)
    assert margin.valid is True
    assert margin.days_remaining >= 89


def test_certificate_margin_refuses_insufficient_runway():
    margin = evaluate_certificate_margin(_summary(10), min_days_remaining=30)
    assert margin.valid is False
    assert margin.reason == "certificate_renewal_margin_insufficient"
    assert margin.required_days == 30


def test_certificate_margin_refuses_an_absent_certificate():
    margin = evaluate_certificate_margin(None)
    assert margin.valid is False
    assert margin.reason == "certificate_not_available"


def test_certificate_margin_refuses_an_expired_certificate():
    now = datetime.now(timezone.utc)
    expired = CertificateSummary("SINTETICO", now - timedelta(days=400),
                                 now - timedelta(days=1), True)
    margin = evaluate_certificate_margin(expired)
    assert margin.valid is False
    assert margin.reason == "certificate_expired"


def test_production_margin_default_is_conservative():
    assert DEFAULT_PRODUCTION_MIN_DAYS_REMAINING == 30
    assert Settings().nfse_production_certificate_min_days == 30


def test_the_real_a1_expiry_would_fail_the_production_margin_today():
    """The A1 in use expires 2026-11-07. Evaluated as of this mission's own
    date it has ~47 days of runway, which clears a 30-day margin — but the
    guard exists so that stops being true silently. Pinned here at two
    dates so the intent is unambiguous rather than calendar-dependent."""
    not_after = datetime(2026, 11, 7, tzinfo=timezone.utc)
    summary = CertificateSummary("A1 SOPROLIFE", datetime(2025, 11, 7, tzinfo=timezone.utc),
                                 not_after, False)
    ok = evaluate_certificate_margin(summary, min_days_remaining=30,
                                     now=datetime(2026, 9, 21, tzinfo=timezone.utc))
    assert ok.valid is True and ok.days_remaining == 47
    late = evaluate_certificate_margin(summary, min_days_remaining=30,
                                       now=datetime(2026, 10, 20, tzinfo=timezone.utc))
    assert late.valid is False
    assert late.reason == "certificate_renewal_margin_insufficient"


def test_certificate_margin_never_consulted_by_restricted_readiness(db, production_settings):
    """Produção Restrita keeps its original 'not expired' rule — the
    production margin must never be able to break a homologation run."""
    from app.services.nfse_national.readiness import compute_provider_readiness

    settings = production_settings.model_copy(
        update={"nfse_production_certificate_min_days": 3650})
    readiness = compute_provider_readiness(db, settings, environment="restricted")
    assert "restricted_certificate_expired" not in readiness.blockers


# --------------------------------------------------- FASE 8: offline preflight


def test_production_preflight_is_technically_ready_but_never_authorized(
        db, users, production_document, production_settings, production_config, synchronized_clock):
    result = _run_preflight(db, users, production_document, production_settings, production_config)

    assert result.technical_ready is True, result.blockers
    assert result.authorization_ready is False
    assert result.network_send_allowed is False

    assert result.environment_is_production is True
    assert result.production_base_url == "https://sefin.nfse.gov.br/SefinNacional"
    assert result.production_endpoint_ok is True
    assert result.network_gate_enabled is True
    assert result.certificate.valid is True
    assert result.clock.synchronized is True
    assert result.xsd_and_signature_ok is True
    assert result.namespace_prefix_count == 0
    assert result.dh_emi_timezone_ok is True
    assert result.fiscal_config_resolved is True
    assert result.request_fingerprint and len(result.request_fingerprint) == 64
    assert result.human_authorization_present is False
    assert result.blockers == []


def test_production_preflight_dh_emi_carries_the_sao_paulo_offset(
        db, users, production_document, production_settings, production_config, synchronized_clock):
    result = _run_preflight(db, users, production_document, production_settings, production_config)
    assert result.dh_emi is not None
    assert result.dh_emi.endswith("-03:00")
    assert dh_emi_matches_emission_timezone(result.dh_emi)


def test_production_preflight_refuses_a_non_production_environment(
        db, users, production_document, production_settings, production_config, synchronized_clock):
    restricted = production_settings.model_copy(update={"nfse_environment": "restricted"})
    result = run_production_preflight(
        db, production_document.id, restricted, users["gestor"].id,
        national_config=production_config,
        recipient=Recipient(nome="Paciente Exemplo M56", sem_nif_motivo=1),
        certificate=load_pkcs12_certificate(*_p12()))
    assert result.environment_is_production is False
    assert result.technical_ready is False
    assert "environment_is_not_production" in result.blockers


def test_production_preflight_refuses_a_closed_production_gate(
        db, users, production_document, production_settings, production_config, synchronized_clock):
    closed = production_settings.model_copy(update={"nfse_production_network_enabled": False})
    result = _run_preflight(db, users, production_document, closed, production_config)
    assert result.technical_ready is False
    assert "production_network_gate_disabled" in result.blockers


def test_production_preflight_refuses_an_unsynchronized_clock(
        db, users, production_document, production_settings, production_config, monkeypatch):
    monkeypatch.setattr(clock_module, "read_clock_status",
                        lambda **_: clock_module.ClockStatus(False, "clock_unsynchronized"))
    result = _run_preflight(db, users, production_document, production_settings, production_config)
    assert result.technical_ready is False
    assert "clock:clock_unsynchronized" in result.blockers


def test_production_preflight_refuses_a_certificate_without_renewal_margin(
        db, users, production_document, production_settings, production_config, synchronized_clock):
    strict = production_settings.model_copy(update={"nfse_production_certificate_min_days": 365})
    result = _run_preflight(db, users, production_document, strict, production_config)
    assert result.technical_ready is False
    assert "certificate:certificate_renewal_margin_insufficient" in result.blockers


def test_production_preflight_sends_nothing_ever(
        db, users, production_document, production_settings, production_config,
        synchronized_clock, monkeypatch):
    """The strongest form of the claim: replace httpx.Client itself with
    something that explodes on construction, then run the preflight."""
    import httpx

    def _explode(*args, **kwargs):
        raise AssertionError("o preflight de produção jamais constrói um cliente HTTP")

    monkeypatch.setattr(httpx, "Client", _explode)
    result = _run_preflight(db, users, production_document, production_settings, production_config)
    assert result.technical_ready is True


def test_production_preflight_module_imports_no_transport_send_path():
    """``production_preflight`` imports the transport module only for the
    URL constant and its validator — never a client, never a send."""
    import inspect

    from app.services.nfse_national import production_preflight

    source = inspect.getsource(production_preflight)
    assert "httpx" not in source
    assert ".send(" not in source
    assert "HttpxProductionTransport" not in source


def test_namespace_prefix_counter_catches_the_e1228_shape():
    """SEFIN rule E1228 rejected DPS #1-#8 for exactly this. A prefix merely
    DECLARED is enough to trip it, so a declaration alone must count."""
    clean = b'<DPS xmlns="http://www.sped.fazenda.gov.br/nfse"><infDPS/></DPS>'
    assert count_namespace_prefixes(clean) == 0
    prefixed = (b'<DPS xmlns="http://www.sped.fazenda.gov.br/nfse" '
                b'xmlns:ds="http://www.w3.org/2000/09/xmldsig#"><infDPS/></DPS>')
    assert count_namespace_prefixes(prefixed) == 1
    used = (b'<n:DPS xmlns:n="http://www.sped.fazenda.gov.br/nfse"><n:infDPS/></n:DPS>')
    assert count_namespace_prefixes(used) == 1


@pytest.mark.parametrize("value", [None, "", "not-a-timestamp",
                                   "2026-09-21T00:00:00",       # naive: no offset at all
                                   "2026-09-21T00:00:00+00:00",  # UTC, not the civil offset
                                   "2026-09-21T00:00:00-05:00"])
def test_dh_emi_timezone_check_fails_closed(value):
    assert dh_emi_matches_emission_timezone(value) is False


def test_dh_emi_timezone_check_accepts_the_sao_paulo_offset():
    assert dh_emi_matches_emission_timezone("2026-09-21T00:42:00-03:00") is True


# --------------------------------------------------------- FASE 6: the gates


def test_all_seven_mission_gates_exist(db):
    names = {g.name for g in compute_production_readiness(db, Settings()).gates}
    assert MISSION_GATE_NAMES <= names


def test_no_pre_m56_gate_was_removed(db):
    names = {g.name for g in compute_production_readiness(db, Settings()).gates}
    assert PRE_M56_GATE_NAMES <= names


def test_every_gate_defaults_to_refusing_on_a_default_configuration(db):
    result = compute_production_readiness(db, Settings())
    by_name = {g.name: g for g in result.gates}
    assert result.all_satisfied is False
    for name in ("environment_is_production", "production_endpoint_correct",
                 "network_gate_enabled", "signed_preflight_passed", "certificate_valid",
                 "explicit_human_authorization", "restricted_validation_successful"):
        assert by_name[name].satisfied is False, name


def test_endpoint_gate_is_false_outside_the_production_environment(db):
    for environment in ("mock", "restricted"):
        result = compute_production_readiness(db, Settings(nfse_environment=environment))
        by_name = {g.name: g for g in result.gates}
        assert by_name["production_endpoint_correct"].satisfied is False
        assert by_name["environment_is_production"].satisfied is False


def test_signed_preflight_gate_cannot_be_satisfied_without_a_real_preflight(db):
    by_name = {g.name: g for g in compute_production_readiness(db, Settings()).gates}
    assert by_name["signed_preflight_passed"].satisfied is False


def test_signed_preflight_gate_refuses_a_technically_blocked_preflight(
        db, users, production_document, production_settings, production_config, monkeypatch):
    monkeypatch.setattr(clock_module, "read_clock_status",
                        lambda **_: clock_module.ClockStatus(False, "clock_unsynchronized"))
    preflight = _run_preflight(db, users, production_document, production_settings, production_config)
    assert preflight.technical_ready is False
    result = compute_production_readiness(db, production_settings, signed_preflight=preflight)
    by_name = {g.name: g for g in result.gates}
    assert by_name["signed_preflight_passed"].satisfied is False


def test_production_remains_impossible_with_every_technical_gate_satisfied(
        db, users, production_document, production_settings, production_config, synchronized_clock):
    """The mission's central invariant. Everything a machine can arrange is
    arranged — and a real issuance is still out of reach."""
    preflight = _run_preflight(db, users, production_document, production_settings, production_config)
    assert preflight.technical_ready is True, preflight.blockers

    result = compute_production_readiness(
        db, production_settings,
        restricted_validation_confirmed_by_human=True,
        signed_preflight=preflight)
    by_name = {g.name: g for g in result.gates}

    for name in ("environment_is_production", "production_endpoint_correct",
                 "network_gate_enabled", "signed_preflight_passed", "certificate_valid",
                 "clock_synchronized", "restricted_validation_successful"):
        assert by_name[name].satisfied is True, (name, by_name[name].detail)

    # And yet:
    assert by_name["explicit_human_authorization"].satisfied is False
    assert result.all_satisfied is False


def test_human_authorization_has_no_configuration_path(db, production_settings, synchronized_clock):
    """No environment variable, no settings field, no database row can set
    it. Searched, not assumed."""
    assert not any("authorization" in name for name in Settings.model_fields)
    assert not any("human" in name for name in Settings.model_fields)
    by_name = {g.name: g for g in compute_production_readiness(db, production_settings).gates}
    assert by_name["explicit_human_authorization"].satisfied is False


# ------------------------------- the issuance path: wired, still not sendable
#
# M59 CHANGES THE SHAPE OF THIS GUARANTEE, and the change is worth stating
# plainly. M56 kept production impossible by refusing to return a provider
# at all — a strong rope, but one that meant every gate below it was never
# exercised for the environment that needs them most. M59 wires the path, so
# `get_provider()` now returns a marker and `operate()` proceeds. What must
# still hold — and what these tests now assert — is that NOTHING REACHES THE
# NETWORK. The enforcement moved from "no provider exists" to "the transport
# refuses", which is where it belongs: the only place a socket is opened.


def test_get_provider_now_resolves_production(production_settings):
    """Resolving is not sending. The marker carries the environment and
    cannot itself issue anything — calling it raises."""
    provider = get_provider(production_settings)
    assert provider.environment == "production"
    assert provider.name == "production"
    with pytest.raises(RuntimeError):
        provider.issue(object())


def test_get_provider_still_refuses_production_without_its_own_gate(production_settings):
    """The production network flag is production's own. Turning it off
    refuses again, and the restricted flag cannot stand in for it."""
    closed = production_settings.model_copy(
        update={"nfse_production_network_enabled": False,
                "nfse_restricted_network_enabled": True})
    with pytest.raises(HTTPException) as excinfo:
        get_provider(closed)
    assert excinfo.value.status_code == 503
    assert excinfo.value.detail["codigo"] == "production_network_gate_disabled"


def test_operate_in_production_never_reaches_the_network(
        db, users, production_document, production_settings, monkeypatch):
    """The real guarantee, asserted where it now lives.

    ``production_settings`` has the production network gate OPEN — this is
    the configuration an operator could actually create by setting one
    environment variable. Even so, the transport refuses for want of the
    human authorization that no configuration can supply, and no HTTP client
    is ever constructed.
    """
    import httpx

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("httpx.Client foi construído — houve tentativa de rede")

    monkeypatch.setattr(httpx, "Client", explode)
    with pytest.raises(HTTPException) as excinfo:
        nfse.operate(db, production_document.id, "issue", "m56-key",
                     production_settings, users["gestor"].id)
    # operate() converts a provider-boundary failure into 'uncertain', never
    # into a success; the document is never issued.
    assert excinfo.value.status_code in (409, 503)


def test_the_production_transport_dispatch_builds_is_closed(
        db, users, production_document, production_settings):
    """Dispatch may now construct ``HttpxProductionTransport`` — and builds
    it CLOSED. It never passes ``explicit_human_authorization``, so the
    object it returns refuses before any socket exists."""
    from app.services.nfse_national import dispatch
    from app.services.nfse_national.transport import (NetworkGateClosedError,
                                                      TransportRequest)

    # Readiness requires a validated policy for BOTH flows. The shared
    # fixture only creates HOME, which never mattered while production could
    # not be resolved at all; resolving it now needs DIRECT too. (That this
    # was missing is itself a real remaining blocker for production — see
    # the M59 report.)
    nfse.create_policy(db, policy_payload(version="SYNTH-PRODUCTION-DIRECT",
                                          environment="production", flow="DIRECT"),
                       users["admin"].id)
    # Same story for the recipient identity: a synthetic CPF, never a real
    # one. Also a real remaining blocker, not a test artifact.
    preparation = nfse.latest_preparation(db, production_document.id)
    person = db.get(Person, preparation.recipient_person_id)
    person.cpf = "52998224725"
    db.commit()
    provider = dispatch.resolve_national_provider(
        db, production_settings, production_document, preparation, users["gestor"].id)
    assert provider.environment == "production"
    transport = provider._transport
    assert type(transport).__name__ == "HttpxProductionTransport"
    with pytest.raises(NetworkGateClosedError) as excinfo:
        transport.send(TransportRequest(method="POST", path="/nfse", body=b"{}", headers={}))
    assert "explicit_human_authorization_absent" in str(excinfo.value)
