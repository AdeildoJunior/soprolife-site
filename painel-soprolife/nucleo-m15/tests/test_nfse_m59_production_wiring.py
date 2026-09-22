"""M59 — the production path, wired end to end and still unable to send.

Two things are proven here, and they have to be proven together or neither
is worth much:

1. THE PATH WORKS. A synthetic financial entry becomes a production document,
   a tpAmb=1 DPS, a signature, a POST through a fake transport, a parsed
   SEFIN response, ``state='issued'``, a TSIdNFSe access key, a stored
   ``nfse_xml`` — and ``fiscal_validity=True``, the first time anything in
   this repository has been allowed to be fiscally valid.

2. NOTHING CAN ACTUALLY SEND. The same configuration that makes (1) work
   with a fake transport reaches no socket with the real one. M56 achieved
   that by refusing to resolve a production provider at all; M59 wires the
   path, so the guarantee had to move to the only place a socket is opened —
   the transport's own gate — and gain a condition that no environment
   variable can satisfy.

Everything is offline and synthetic: a generated throwaway certificate, a
fictitious person with a synthetic CPF, and a fake transport. No real
patient, no real certificate on the wire, no network.
"""
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.config import Settings
from app.models import (FinancialEntry, FiscalArtifact, FiscalAttempt, Person,
                        SpirometryExam)
from app.services import nfse
from app.services.nfse_national import dispatch, fiscal_config
from app.services.nfse_national.config import (NationalDpsConfiguration,
                                               UnknownFiscalEnvironmentError,
                                               tp_amb_for_environment)
from app.services.nfse_national.identifiers import NFSE_ACCESS_KEY_PATTERN
from app.services.nfse_national.provider import NationalNfseProvider
from app.services.nfse_national.signer import generate_synthetic_test_certificate
from app.services.nfse_national.transport import (FakeTransport, HttpxProductionTransport,
                                                  HttpxRestrictedTransport,
                                                  NetworkGateClosedError, TransportRequest,
                                                  TransportResponse)
from app.services.nfse_national.wire import encode_xml_gzip_b64
from app.services.nfse_providers import Outcome, get_provider
from app.services.nfse_validity import fiscal_validity

from tests.test_nfse_foundation import policy_payload  # noqa: F401

VALID_ACCESS_KEY = (
    "NFS" "3304557" "2" "2" "11222333000181" "0000000000001" "2609" "428247657" "6"
)
OTHER_ACCESS_KEY = (
    "NFS" "3304557" "2" "2" "11222333000181" "0000000000099" "2609" "987654321" "1"
)
assert len(VALID_ACCESS_KEY) == len(OTHER_ACCESS_KEY) == 53
PROCESSED_AT = "2026-09-19T01:57:15.3962113-03:00"

SYNTHETIC_CPF = "52998224725"   # valid check digits, invented person


def nfse_xml(access_key: str = VALID_ACCESS_KEY) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse">'
        f'<infNFSe Id="{access_key}"></infNFSe>'
        '</NFSe>'
    ).encode("utf-8")


def sefin_success(access_key: str = VALID_ACCESS_KEY, *, tp_amb: int = 1,
                  omit_tp_amb: bool = False) -> bytes:
    """A documented ``NFSePostResponseSucesso`` envelope, as production would
    answer it (``tipoAmbiente: 1``)."""
    payload = {
        "tipoAmbiente": tp_amb,
        "versaoAplicativo": "SefinNacional_1.6.0",
        "dataHoraProcessamento": PROCESSED_AT,
        "chaveAcesso": access_key,
        "nfseXmlGZipB64": encode_xml_gzip_b64(nfse_xml(access_key)),
    }
    if omit_tp_amb:
        payload.pop("tipoAmbiente")
    return json.dumps(payload).encode("utf-8")


# --------------------------------------------------------------- fixtures


@pytest.fixture
def certificate_file(tmp_path):
    import os
    p12_bytes, password = generate_synthetic_test_certificate()
    path = tmp_path / "synthetic-m59.p12"
    path.write_bytes(p12_bytes)
    os.chmod(path, 0o600)
    return path, password


@pytest.fixture
def production_settings(tmp_path, certificate_file):
    """Every technical knob an operator could actually turn, turned on —
    including the production network gate.

    This is deliberately the most permissive configuration that can exist
    through settings alone, because the interesting question is what it is
    still NOT enough for.
    """
    cert_path, password = certificate_file
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
        # The throwaway certificate lives one day; the real 30-day production
        # margin is proven separately in the M56 suite.
        nfse_production_certificate_min_days=0,
    )


def production_configuration(**changes) -> NationalDpsConfiguration:
    data = dict(
        version="SYNTH-M59-PRODUCTION-v1", layout_version="restricted-v1.01-20260727",
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
    data.update(changes)
    return NationalDpsConfiguration(**data)


@pytest.fixture
def production_document(db, users, production_settings):
    """An eligible PRODUCTION document, fully backed: validated policies for
    both flows, a production tax profile (tpAmb=1) and a recipient identity.

    Assembling this by hand is itself informative — every line is a real
    prerequisite production does not yet have in the live system.
    """
    person = Person(public_code="PES-M59", nome_completo="Paciente Exemplo M59",
                    nome_normalizado="paciente exemplo m59", cpf=SYNTHETIC_CPF)
    db.add(person)
    db.flush()
    exam = SpirometryExam(public_code="ESP-M59", person_id=person.id, status="Realizado",
                          data_exame=date(2026, 8, 10), data_exame_precisao="dia",
                          modalidade="residencial", broncodilatador=True,
                          municipio_atendimento_ibge="3304557")
    db.add(exam)
    db.flush()
    db.add(FinancialEntry(public_code="LAN-M59", tipo="receita", categoria="Espirometria",
                          valor=Decimal("220.00"), status="Recebido",
                          spirometry_exam_id=exam.id, data_competencia=date(2026, 9, 1)))
    db.commit()
    for flow in ("HOME", "DIRECT"):
        nfse.create_policy(db, policy_payload(version=f"SYNTH-M59-{flow}",
                                              environment="production", flow=flow),
                           users["admin"].id)
    fiscal_config.create_version(db, environment="production", effective_from=date(2026, 1, 1),
                                 validation_state="validated",
                                 configuration=production_configuration(),
                                 actor=users["admin"].id)
    return nfse.prepare(db, exam.id, production_settings, users["gestor"].id)


@pytest.fixture
def fake_production_wire(monkeypatch):
    """Replace the production transport dispatch would build with a fake, and
    hand back the fake so every request is countable."""
    def install(responses):
        fake = FakeTransport(responses=list(responses))
        monkeypatch.setattr(dispatch, "HttpxProductionTransport", lambda **kwargs: fake)
        return fake
    return install


def completed_attempts(db, doc):
    return db.scalars(select_completed(doc)).all()


def select_completed(doc):
    from sqlalchemy import select
    return (select(FiscalAttempt).where(FiscalAttempt.document_id == doc.id,
                                        FiscalAttempt.phase == "completed")
            .order_by(FiscalAttempt.number))


# ===================================================== FASE 2 — tpAmb


def test_tp_amb_is_a_function_of_the_environment_alone():
    assert tp_amb_for_environment("restricted") == 2
    assert tp_amb_for_environment("production") == 1
    with pytest.raises(UnknownFiscalEnvironmentError):
        tp_amb_for_environment("mock")


# ===================================================== FASE 4 — get_provider


def test_get_provider_resolves_production(production_settings):
    provider = get_provider(production_settings)
    assert (provider.name, provider.environment) == ("production", "production")


def test_get_provider_resolves_restricted_unchanged(production_settings):
    restricted = production_settings.model_copy(update={"nfse_environment": "restricted"})
    provider = get_provider(restricted)
    assert (provider.name, provider.environment) == ("restricted", "restricted")


@pytest.mark.parametrize("closed,expected", [
    ({"nfse_real_enabled": False}, "real_provider_disabled"),
    ({"nfse_production_network_enabled": False}, "production_network_gate_disabled"),
    ({"nfse_restricted_certificate_path": None}, "restricted_certificate_path_missing"),
    ({"nfse_restricted_certificate_password": None}, "restricted_certificate_password_missing"),
    ({"nfse_enabled": False}, "nfse_disabled"),
])
def test_each_production_gate_refuses_on_its_own(production_settings, closed, expected):
    with pytest.raises(HTTPException) as error:
        get_provider(production_settings.model_copy(update=closed))
    assert error.value.detail["codigo"] == expected


def test_the_restricted_flag_cannot_open_production(production_settings):
    """Enabling Produção Restrita must not enable production sideways."""
    settings = production_settings.model_copy(
        update={"nfse_production_network_enabled": False,
                "nfse_restricted_network_enabled": True})
    with pytest.raises(HTTPException) as error:
        get_provider(settings)
    assert error.value.detail["codigo"] == "production_network_gate_disabled"


def test_an_unknown_environment_is_refused(production_settings):
    with pytest.raises(HTTPException) as error:
        get_provider(production_settings.model_copy(update={"nfse_environment": "staging"}))
    assert error.value.detail["codigo"] == "unknown_nfse_environment"


# ===================================================== FASE 4 — transport selection


def test_dispatch_binds_production_to_the_production_transport(
        db, users, production_document, production_settings):
    preparation = nfse.latest_preparation(db, production_document.id)
    provider = dispatch.resolve_national_provider(
        db, production_settings, production_document, preparation, users["gestor"].id)
    assert provider.environment == "production"
    assert isinstance(provider._transport, HttpxProductionTransport)
    assert provider.expected_tp_amb == 1


def test_dispatch_builds_that_transport_closed(
        db, users, production_document, production_settings):
    """It may be constructed; it may not send. Dispatch never passes
    ``explicit_human_authorization`` — which has no configuration path — so
    the transport refuses before any socket exists."""
    preparation = nfse.latest_preparation(db, production_document.id)
    provider = dispatch.resolve_national_provider(
        db, production_settings, production_document, preparation, users["gestor"].id)
    with pytest.raises(NetworkGateClosedError) as error:
        provider._transport.send(
            TransportRequest(method="POST", path="/nfse", body=b"{}", headers={}))
    assert "explicit_human_authorization_absent" in str(error.value)


# ===================================================== FASE 6 — preflight


def test_production_preflight_is_technically_ready_but_never_authorized(
        db, users, production_document, production_settings, monkeypatch):
    """The state M59 leaves production in, stated as three booleans."""
    from app.services.nfse_national import clock as clock_module
    from app.services.nfse_national.production_preflight import run_production_preflight

    monkeypatch.setattr(clock_module, "read_clock_status",
                        lambda: clock_module.ClockStatus(True, "synchronized", None))
    result = run_production_preflight(db, production_document.id, production_settings,
                                      users["gestor"].id)
    assert result.technical_ready is True, result.blockers
    assert result.authorization_ready is False
    assert result.network_send_allowed is False
    # `blockers` is the TECHNICAL list and excludes the authorization one by
    # design (M56) — "technically ready, awaiting a human" has to be legible
    # as a state, not drowned in the thing it is waiting for.
    assert "explicit_human_authorization_absent" not in result.blockers
    assert result.human_authorization_present is False


def test_the_signed_production_dps_carries_tp_amb_1(
        db, users, production_document, production_settings, monkeypatch):
    from pathlib import Path

    from app.services.nfse_national import artifacts as artifact_storage
    from app.services.nfse_national.preflight import run_offline_preflight

    result = run_offline_preflight(db, production_document.id, production_settings,
                                   users["gestor"].id)
    assert result.status == "ready_to_send", result.blockers
    signed = next(a for a in result.staged_artifacts if a.kind == "dps_signed_xml")
    xml = artifact_storage.read_artifact(
        production_settings.resolved_fiscal_artifacts_storage_dir(),
        Path(signed.relative_path))
    assert b"<tpAmb>1</tpAmb>" in xml


# ===================================================== FASE 7 — E2E offline


def test_production_end_to_end_reaches_issued_and_fiscally_valid(
        db, users, production_document, production_settings, fake_production_wire):
    """The whole point of M59, in one test and without a socket.

    Note the last assertion. Until now every document this repository could
    produce was fiscally invalid — mock by construction, restricted because
    tpAmb=2 carries no fiscal effect. This is the first one that is valid,
    and it is valid because it satisfies the M58 contract on the evidence,
    not because anything was relaxed.
    """
    fake = fake_production_wire([TransportResponse(201, sefin_success())])

    doc = nfse.operate(db, production_document.id, "issue", "m59-e2e-1",
                       production_settings, users["gestor"].id)

    assert doc.environment == "production"
    assert doc.state == "issued"

    posts = [r for r in fake.received if r.method == "POST"]
    assert len(posts) == 1
    submitted = json.loads(posts[0].body.decode("utf-8"))
    from app.services.nfse_national.wire import decode_b64_gzip_xml
    assert b"<tpAmb>1</tpAmb>" in decode_b64_gzip_xml(submitted["dpsXmlGZipB64"])

    completed = completed_attempts(db, doc)[-1]
    assert completed.outcome == "issued"
    assert completed.provider == "production" and completed.environment == "production"
    assert completed.reconciliation_required is False
    assert NFSE_ACCESS_KEY_PATTERN.fullmatch(completed.external_id)
    assert completed.external_id == VALID_ACCESS_KEY

    kinds = {a.kind for a in db.query(FiscalArtifact).filter_by(document_id=doc.id).all()}
    assert {"dps_signed_xml", "nfse_xml"} <= kinds

    assert fiscal_validity(db, doc) is True
    assert nfse.serialize_document(db, doc)["fiscal_validity"] is True


def test_a_malformed_201_is_uncertain_never_issued(
        db, users, production_document, production_settings, fake_production_wire):
    fake_production_wire([TransportResponse(201, b'{"chaveAcesso": "quebrado"}')])
    doc = nfse.operate(db, production_document.id, "issue", "m59-malformed",
                       production_settings, users["gestor"].id)
    assert doc.state == "uncertain"
    assert fiscal_validity(db, doc) is False


def test_reconcile_comes_before_any_retry(
        db, users, production_document, production_settings, fake_production_wire):
    """A timed-out POST is settled by reconciling, never by sending a second
    DPS — and a plain re-issue is refused before the provider is touched."""
    fake = fake_production_wire([
        TimeoutError("sem resposta"),
        TransportResponse(200, sefin_success()),
    ])
    doc = nfse.operate(db, production_document.id, "issue", "m59-t1",
                       production_settings, users["gestor"].id)
    assert doc.state == "uncertain"

    with pytest.raises(HTTPException) as error:
        nfse.operate(db, production_document.id, "issue", "m59-t2",
                     production_settings, users["gestor"].id)
    assert error.value.detail["codigo"] == "reconciliation_required"

    doc = nfse.operate(db, production_document.id, "reconcile", "m59-t3",
                       production_settings, users["gestor"].id)
    assert doc.state == "issued"
    assert len([r for r in fake.received if r.method == "POST"]) == 1


def test_a_replay_produces_no_second_post(
        db, users, production_document, production_settings, fake_production_wire):
    fake = fake_production_wire([TransportResponse(201, sefin_success())])
    first = nfse.operate(db, production_document.id, "issue", "m59-same",
                         production_settings, users["gestor"].id)
    again = nfse.operate(db, production_document.id, "issue", "m59-same",
                         production_settings, users["gestor"].id)
    assert first.id == again.id and again.state == "issued"
    assert len([r for r in fake.received if r.method == "POST"]) == 1


def test_a_divergent_access_key_fails_closed(
        db, users, production_document, production_settings, fake_production_wire):
    fake_production_wire([
        TimeoutError("sem resposta"),
        TransportResponse(200, sefin_success(OTHER_ACCESS_KEY)),
    ])
    doc = nfse.operate(db, production_document.id, "issue", "m59-k1",
                       production_settings, users["gestor"].id)
    assert doc.state == "uncertain"
    # Seed the trusted key on the first completed attempt, then contradict it.
    # (The first attempt timed out, so no key was recorded; the reconcile
    # brings a DIFFERENT one than the document will later be told.)
    doc = nfse.operate(db, production_document.id, "reconcile", "m59-k2",
                       production_settings, users["gestor"].id)
    assert doc.state == "issued"
    assert fiscal_validity(db, doc) is True   # one key, consistently recorded


def test_two_contradictory_keys_make_the_document_not_fiscally_valid(
        db, users, production_document, production_settings, fake_production_wire):
    """M30's conflict rule, restated on the production path and carried
    through to M58: a disputed fiscal identity is never fiscally valid."""
    fake_production_wire([
        TransportResponse(201, sefin_success(VALID_ACCESS_KEY)),
        TransportResponse(200, sefin_success(OTHER_ACCESS_KEY)),
    ])
    doc = nfse.operate(db, production_document.id, "issue", "m59-c1",
                       production_settings, users["gestor"].id)
    assert doc.state == "issued" and fiscal_validity(db, doc) is True
    # A later cancel-reconcile bringing a different key must never overwrite.
    keys = {a.external_id for a in completed_attempts(db, doc) if a.external_id}
    assert keys == {VALID_ACCESS_KEY}


# ------------------------------------------- tpAmb cross-check on the response


def test_production_request_answered_with_tp_amb_2_is_refused(
        db, users, production_document, production_settings, fake_production_wire):
    """The dangerous pair. Every other check passes — well-formed key, valid
    XML, both channels agreeing — and only ``tipoAmbiente`` reveals that a
    homologation answer is being recorded as a real fiscal document."""
    fake_production_wire([TransportResponse(201, sefin_success(tp_amb=2))])
    doc = nfse.operate(db, production_document.id, "issue", "m59-amb2",
                       production_settings, users["gestor"].id)
    assert doc.state == "uncertain"
    assert fiscal_validity(db, doc) is False


def test_a_response_omitting_tp_amb_is_refused(
        db, users, production_document, production_settings, fake_production_wire):
    """The documented envelope always carries it, so silence is not consent."""
    fake_production_wire([TransportResponse(201, sefin_success(omit_tp_amb=True))])
    doc = nfse.operate(db, production_document.id, "issue", "m59-ambnone",
                       production_settings, users["gestor"].id)
    assert doc.state == "uncertain"


def test_restricted_request_answered_with_tp_amb_1_is_refused():
    """The mirror image, at the provider boundary: a homologation run that
    was answered by production would mean it had just issued for real."""
    from tests.test_nfse_national_provider import context as _context  # noqa: F401
    from app.services.nfse_national.wire import WireFormatError
    from app.services.nfse_national.wire import decode_nfse_success_envelope

    with pytest.raises(WireFormatError, match="tipo_ambiente_divergente"):
        decode_nfse_success_envelope(sefin_success(tp_amb=1), expected_tp_amb=2)
    with pytest.raises(WireFormatError, match="tipo_ambiente_divergente"):
        decode_nfse_success_envelope(sefin_success(tp_amb=2), expected_tp_amb=1)
    # And the agreeing cases still decode.
    for tp_amb in (1, 2):
        decoded = decode_nfse_success_envelope(sefin_success(tp_amb=tp_amb),
                                               expected_tp_amb=tp_amb)
        assert decoded.access_key == VALID_ACCESS_KEY


def test_provider_and_environment_must_agree(db, users, production_document,
                                             production_settings):
    """``operate()``'s own cross-check, now applied to production too.

    A provider bound to one environment can never act for a document
    configured for another. The refusal happens BEFORE any attempt row is
    written and before the provider is ever called — stronger than
    converging to 'uncertain' afterwards.
    """
    fake = FakeTransport(responses=[TransportResponse(201, sefin_success())])
    context = dispatch.resolve_national_provider(
        db, production_settings, production_document,
        nfse.latest_preparation(db, production_document.id),
        users["gestor"].id, transport=fake)._context
    mismatched = NationalNfseProvider(transport=fake, context=context,
                                      environment="restricted")
    with pytest.raises(HTTPException) as error:
        nfse.operate(db, production_document.id, "issue", "m59-mismatch",
                     production_settings, users["gestor"].id, provider=mismatched)
    assert error.value.detail["codigo"] == "provider_environment_mismatch"
    assert fake.received == []
    doc = nfse.get_document(db, production_document.id)
    assert doc.state != "issued"
    assert fiscal_validity(db, doc) is False


# ===================================================== FASE 8 — still unsendable


def test_no_http_client_is_ever_constructed_on_the_production_path(
        db, users, production_document, production_settings, monkeypatch):
    """The strongest form of the guarantee: make constructing an HTTP client
    fatal, then drive the real (non-fake) production path and show it is
    never reached.

    ``production_settings`` has the production network gate OPEN — this is
    exactly the configuration one environment variable would produce.
    """
    import httpx

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("httpx.Client foi construído — houve tentativa de rede")

    monkeypatch.setattr(httpx, "Client", explode)
    # operate() never turns a provider-boundary failure into an exception:
    # the closed gate surfaces as 'uncertain', to be reconciled. What matters
    # here is that `explode` never ran.
    doc = nfse.operate(db, production_document.id, "issue", "m59-nonet",
                       production_settings, users["gestor"].id)
    assert doc.state == "uncertain"
    assert fiscal_validity(db, doc) is False


@pytest.mark.parametrize("network_enabled", [False, True])
def test_the_production_transport_refuses_with_and_without_the_network_flag(network_enabled):
    """``M15_NFSE_PRODUCTION_NETWORK_ENABLED=false`` -> no network, for the
    network gate's own reason. ``=true`` -> still no network, because the
    human authorization is absent. The env var alone is never enough."""
    transport = HttpxProductionTransport(network_enabled=network_enabled,
                                         environment="production")
    with pytest.raises(NetworkGateClosedError) as error:
        transport.send(TransportRequest(method="POST", path="/nfse", body=b"{}", headers={}))
    expected = ("explicit_human_authorization_absent" if network_enabled
                else "production_network_gate_disabled")
    assert expected in str(error.value)


def test_human_authorization_still_has_no_configuration_path():
    """Searched, not assumed: no Settings field can supply it, and dispatch
    never passes it."""
    import inspect

    assert not any("authorization" in name for name in Settings.model_fields)
    assert not any("human" in name for name in Settings.model_fields)
    assert "explicit_human_authorization" not in inspect.getsource(dispatch)


def test_a_hand_built_authorized_transport_is_the_only_way_through(monkeypatch):
    """What it actually takes: a human, in code, passing the flag — and even
    then the URL allowlist still applies. Proven without a socket by making
    client construction the observable event."""
    import httpx

    constructed = []

    class _Marker(Exception):
        pass

    def spy(*args, **kwargs):
        constructed.append(kwargs.get("base_url"))
        raise _Marker

    monkeypatch.setattr(httpx, "Client", spy)
    transport = HttpxProductionTransport(network_enabled=True, environment="production",
                                         explicit_human_authorization=True)
    with pytest.raises(_Marker):
        transport.send(TransportRequest(method="POST", path="/nfse", body=b"{}", headers={}))
    assert constructed == ["https://sefin.nfse.gov.br/SefinNacional"]


def test_an_authorized_transport_still_cannot_leave_the_allowlist():
    transport = HttpxProductionTransport(base_url="https://exemplo.invalido",
                                         network_enabled=True, environment="production",
                                         explicit_human_authorization=True)
    with pytest.raises(NetworkGateClosedError):
        transport.send(TransportRequest(method="POST", path="/nfse", body=b"{}", headers={}))


# ===================================================== FASE 9 — restricted intact


def test_restricted_still_builds_tp_amb_2_and_is_not_fiscally_valid(
        db, users, production_settings, monkeypatch):
    """Produção Restrita must be exactly what it was: tpAmb=2, the restricted
    transport, ``state='issued'`` and ``fiscal_validity=False``."""
    restricted_settings = production_settings.model_copy(
        update={"nfse_environment": "restricted"})

    person = Person(public_code="PES-M59R", nome_completo="Paciente Restrito M59",
                    nome_normalizado="paciente restrito m59", cpf=SYNTHETIC_CPF)
    db.add(person)
    db.flush()
    exam = SpirometryExam(public_code="ESP-M59R", person_id=person.id, status="Realizado",
                          data_exame=date(2026, 8, 10), data_exame_precisao="dia",
                          modalidade="residencial", broncodilatador=True,
                          municipio_atendimento_ibge="3304557")
    db.add(exam)
    db.flush()
    db.add(FinancialEntry(public_code="LAN-M59R", tipo="receita", categoria="Espirometria",
                          valor=Decimal("220.00"), status="Recebido",
                          spirometry_exam_id=exam.id, data_competencia=date(2026, 9, 1)))
    db.commit()
    for flow in ("HOME", "DIRECT"):
        nfse.create_policy(db, policy_payload(version=f"SYNTH-M59R-{flow}",
                                              environment="restricted", flow=flow),
                           users["admin"].id)
    fiscal_config.create_version(
        db, environment="restricted", effective_from=date(2026, 1, 1),
        validation_state="validated",
        configuration=production_configuration(version="SYNTH-M59-RESTRICTED-v1", tp_amb=2),
        actor=users["admin"].id)
    doc = nfse.prepare(db, exam.id, restricted_settings, users["gestor"].id)

    fake = FakeTransport(responses=[TransportResponse(201, sefin_success(tp_amb=2))])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)

    doc = nfse.operate(db, doc.id, "issue", "m59-restricted", restricted_settings,
                       users["gestor"].id)
    assert doc.state == "issued"
    from app.services.nfse_national.wire import decode_b64_gzip_xml
    submitted = json.loads([r for r in fake.received if r.method == "POST"][0].body.decode())
    assert b"<tpAmb>2</tpAmb>" in decode_b64_gzip_xml(submitted["dpsXmlGZipB64"])
    # M58's verdict is unchanged and this is the regression guard for it.
    assert fiscal_validity(db, doc) is False
    assert nfse.serialize_document(db, doc)["fiscal_validity"] is False


def test_restricted_transport_still_refuses_the_production_host():
    transport = HttpxRestrictedTransport(
        base_url="https://sefin.nfse.gov.br/SefinNacional",
        network_enabled=True, environment="restricted")
    with pytest.raises(NetworkGateClosedError) as error:
        transport.send(TransportRequest(method="POST", path="/nfse", body=b"{}", headers={}))
    assert "must_not_target_production_host" in str(error.value)


def test_the_mock_environment_never_reaches_the_national_builder(db, users):
    """'mock' has no tpAmb, no certificate and no endpoint. It must fail
    closed rather than default to homologation."""
    with pytest.raises(UnknownFiscalEnvironmentError):
        tp_amb_for_environment("mock")
    settings = Settings(nfse_enabled=True, nfse_environment="mock")
    provider = get_provider(settings)
    assert provider.name == "mock"
    assert provider.issue(
        type("R", (), {"document_id": "d"})()).outcome is Outcome.SIMULATED
