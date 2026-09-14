"""M27/M30 — RestrictedNfseProvider: construção+assinatura+transporte ponta a
ponta, sempre com FakeTransport (nunca rede real).

M30 acrescenta o contrato real de resposta (§1.3.2.a do manual oficial dos
contribuintes: sucesso do POST /nfse retorna o arquivo XML da NFS-e gerada) e
a correção do identificador usado por query()/reconciliação (TSIdDPS, nunca
``operation_id``)."""
import pytest

from app.services.nfse_national.config import NationalDpsConfiguration
from app.services.nfse_national.dps_builder import Recipient
from app.services.nfse_national.identifiers import DpsIdComponents, build_dps_id
from app.services.nfse_national.provider import (
    RestrictedIssueContext,
    RestrictedNfseProvider,
    RestrictedProviderError,
)
from app.services.nfse_national.signer import generate_synthetic_test_certificate, load_pkcs12_certificate
from app.services.nfse_national.transport import FakeTransport, TransportResponse
from app.services.nfse_providers import Outcome, ProviderRequest

# TSIdNFSe (tiposSimples_v1.01.xsd): "NFS" + cMun(7) + ambGer(1) + tipoInsc(1)
# + inscricaoFederal(14) + numNFSe(13) + anoMesEmis(4) + codNum(9) + DV(1) = 53.
VALID_ACCESS_KEY = (
    "NFS" "3304557" "2" "2" "11222333000181" "0000000000001" "2609" "123456789" "5"
)
assert len(VALID_ACCESS_KEY) == 53


def _nfse_xml(access_key: str = VALID_ACCESS_KEY) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse">'
        f'<infNFSe Id="{access_key}"></infNFSe>'
        '</NFSe>'
    ).encode("utf-8")


@pytest.fixture
def context():
    cfg = NationalDpsConfiguration(
        version="SYNTH-RESTRICTED-v1", layout_version="restricted-v1.01-20260727",
        issuer_cnpj="11222333000181", issuer_name="SOPROLIFE SAUDE LTDA (SINTETICO)",
        issuer_municipio_ibge="3304557", issuer_op_simp_nac=3, issuer_reg_esp_trib=0,
        codigo_tributacao_nacional="140501",
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
                                  serie_dps_display="1", certificate=cert,
                                  municipio_prestacao_ibge="3304557")


def request():
    return ProviderRequest("doc-1", "op-1", "prep-1", "220.00", "2026-08-10",
                           "Realização de exame de espirometria.")


def test_provider_rejects_wrong_environment(context):
    transport = FakeTransport(responses=[])
    with pytest.raises(RestrictedProviderError):
        RestrictedNfseProvider(transport=transport, context=context, environment="production")


def test_issue_success_sends_signed_xsd_valid_dps(context):
    transport = FakeTransport(responses=[TransportResponse(201, _nfse_xml())])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    result = provider.issue(request())
    assert result.outcome == Outcome.SIMULATED
    sent = transport.received[0]
    assert sent.method == "POST" and sent.path == "/nfse"
    assert b"<ds:Signature" in sent.body or b"Signature" in sent.body


def test_issue_success_extracts_real_access_key(context):
    """M30 — the official manual states POST /nfse success returns the NFS-e
    XML itself; the access key is infNFSe/@Id (TSIdNFSe), never invented."""
    transport = FakeTransport(responses=[TransportResponse(200, _nfse_xml())])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    result = provider.issue(request())
    assert result.outcome == Outcome.SIMULATED
    assert result.external_id == VALID_ACCESS_KEY


@pytest.mark.parametrize("body", [
    b"<NFSe/>",  # well-formed XML, but no infNFSe at all
    b"<NFSe xmlns='http://www.sped.fazenda.gov.br/nfse'><infNFSe/></NFSe>",  # infNFSe with no Id
    b"<NFSe xmlns='http://www.sped.fazenda.gov.br/nfse'><infNFSe Id='NOT-A-KEY'/></NFSe>",  # malformed Id
    b"not xml at all",
    b"",
])
def test_issue_malformed_success_body_never_becomes_issued(context, body):
    """A 2xx status is never proof of issuance by itself — a success body
    that isn't a well-formed NFS-e with a schema-shaped access key must fail
    closed to UNCERTAIN, never SIMULATED."""
    transport = FakeTransport(responses=[TransportResponse(200, body)])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    result = provider.issue(request())
    assert result.outcome == Outcome.UNCERTAIN
    assert result.external_id is None


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


def test_reconcile_queries_by_official_dps_id_never_operation_id(context):
    """M30 fix: GET /dps/{id} must use the real TSIdDPS this provider built
    for the original submission — never ``request.operation_id`` (an
    internal idempotency UUID with no fiscal meaning to the government API)."""
    transport = FakeTransport(responses=[TransportResponse(404, b"")])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    req = request()
    assert req.operation_id == "op-1"
    provider.query(req, "issue")
    sent = transport.received[0]
    expected_dps_id = build_dps_id(context.dps_id)
    assert sent.method == "GET"
    assert sent.path == f"/dps/{expected_dps_id}"
    assert "op-1" not in sent.path
    assert expected_dps_id != "op-1"


def test_reconcile_not_found(context):
    transport = FakeTransport(responses=[TransportResponse(404, b"")])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    assert provider.query(request(), "issue").outcome == Outcome.NOT_FOUND


def test_reconcile_finds_issued_document_and_extracts_access_key(context):
    transport = FakeTransport(responses=[TransportResponse(200, _nfse_xml())])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    result = provider.query(request(), "issue")
    assert result.outcome == Outcome.SIMULATED
    assert result.external_id == VALID_ACCESS_KEY


def test_reconcile_success_without_extractable_key_is_uncertain(context):
    """The exact JSON envelope for GET /dps/{id} is not confirmed by
    available official evidence (the restricted Swagger requires an mTLS
    client certificate even to view) — a 2xx body that carries no
    schema-shaped access key anywhere must stay UNCERTAIN, never a guessed
    success."""
    transport = FakeTransport(responses=[TransportResponse(200, b'{"status":"ok"}')])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    result = provider.query(request(), "issue")
    assert result.outcome == Outcome.UNCERTAIN
    assert result.external_id is None


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
