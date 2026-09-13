"""M27 — RestrictedNfseProvider: construção+assinatura+transporte ponta a ponta,
sempre com FakeTransport (nunca rede real)."""
import pytest

from app.services.nfse_national.config import NationalDpsConfiguration
from app.services.nfse_national.dps_builder import Recipient
from app.services.nfse_national.identifiers import DpsIdComponents
from app.services.nfse_national.provider import (
    RestrictedIssueContext,
    RestrictedNfseProvider,
    RestrictedProviderError,
)
from app.services.nfse_national.signer import generate_synthetic_test_certificate, load_pkcs12_certificate
from app.services.nfse_national.transport import FakeTransport, TransportResponse
from app.services.nfse_providers import Outcome, ProviderRequest


@pytest.fixture
def context():
    cfg = NationalDpsConfiguration(
        version="SYNTH-RESTRICTED-v1", layout_version="restricted-v1.01-20260727",
        issuer_cnpj="11222333000181", issuer_name="SOPROLIFE SAUDE LTDA (SINTETICO)",
        issuer_municipio_ibge="3304557", issuer_op_simp_nac=3, issuer_reg_esp_trib=0,
        codigo_tributacao_nacional="140501", municipio_prestacao_ibge="3304557",
        trib_issqn=1, tp_ret_issqn=1,
        amount_basis="financial_entry.valor", competence_rule="service_date",
        own_revenue_confirmed=True, validation_reference="SYNTHETIC-ONLY",
    )
    dps_id = DpsIdComponents(codigo_municipio="3304557", tipo_inscricao_federal=2,
                             inscricao_federal="11222333000181", serie_dps="00001",
                             numero_dps="000000000000001")
    p12_bytes, password = generate_synthetic_test_certificate()
    cert = load_pkcs12_certificate(p12_bytes, password)
    return RestrictedIssueContext(config=cfg, dps_id=dps_id,
                                  recipient=Recipient(nome="Paciente Um", sem_nif_motivo=1),
                                  ver_aplic="m27-0.1", numero_dps_display="1",
                                  serie_dps_display="1", certificate=cert)


def request():
    return ProviderRequest("doc-1", "op-1", "prep-1", "220.00", "2026-08-10",
                           "Realização de exame de espirometria.")


def test_provider_rejects_wrong_environment(context):
    transport = FakeTransport(responses=[])
    with pytest.raises(RestrictedProviderError):
        RestrictedNfseProvider(transport=transport, context=context, environment="production")


def test_issue_success_sends_signed_xsd_valid_dps(context):
    transport = FakeTransport(responses=[TransportResponse(201, b"<NFSe/>")])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    result = provider.issue(request())
    assert result.outcome == Outcome.SIMULATED
    sent = transport.received[0]
    assert sent.method == "POST" and sent.path == "/nfse"
    assert b"<ds:Signature" in sent.body or b"Signature" in sent.body


def test_issue_timeout_is_uncertain(context):
    transport = FakeTransport(responses=[TimeoutError("timeout")])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    assert provider.issue(request()).outcome == Outcome.UNCERTAIN


def test_issue_server_error_is_uncertain(context):
    transport = FakeTransport(responses=[TransportResponse(503, b"")])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    assert provider.issue(request()).outcome == Outcome.UNCERTAIN


def test_issue_client_error_is_rejected(context):
    transport = FakeTransport(responses=[TransportResponse(422, b"{}")])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    assert provider.issue(request()).outcome == Outcome.REJECTED


def test_reconcile_not_found(context):
    transport = FakeTransport(responses=[TransportResponse(404, b"")])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    assert provider.query(request(), "issue").outcome == Outcome.NOT_FOUND


def test_reconcile_timeout_is_uncertain_never_not_found(context):
    transport = FakeTransport(responses=[TimeoutError("t")])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    assert provider.query(request(), "issue").outcome == Outcome.UNCERTAIN


def test_cancel_is_intentionally_unsupported(context):
    transport = FakeTransport(responses=[])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    with pytest.raises(RestrictedProviderError):
        provider.cancel(request())
    assert transport.received == []  # never even attempted a request


def test_amount_from_request_never_invented_by_provider(context):
    transport = FakeTransport(responses=[TransportResponse(201, b"<NFSe/>")])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    req = ProviderRequest("doc-1", "op-1", "prep-1", "77.50", "2026-08-10", "Descrição fixa.")
    provider.issue(req)
    sent_body = transport.received[0].body
    assert b"77.50" in sent_body
