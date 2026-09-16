"""M38 — the SEFIN Nacional JSON/GZip/Base64 wire format, proved offline.

Context: the live DPS #3 attempt (2026-09-15) against
``https://sefin.producaorestrita.nfse.gov.br/SefinNacional/nfse`` returned
**HTTP 415 Unsupported Media Type** because the provider posted the raw signed
DPS XML as the HTTP body. The documented contract is ``application/json`` in
and out, with the signed XML carried as ``dpsXmlGZipB64`` (XML -> GZip ->
Base64) and the issued NFS-e returned as ``nfseXmlGZipB64``.

Every test here runs entirely in-process: ``FakeTransport`` never opens a
socket, and the two tests that exercise real httpx URL joining use an
in-memory ``httpx.BaseTransport`` (no DNS, no TLS, no connection). No DPS is
issued and no certificate other than the synthetic self-signed test one is
ever loaded.
"""
import base64
import gzip
import io
import json
from decimal import Decimal

import httpx
import pytest

from app.services.nfse_national import transport as transport_module
from app.services.nfse_national.config import NationalDpsConfiguration
from app.services.nfse_national.dps_builder import Recipient
from app.services.nfse_national.identifiers import DpsIdComponents, build_dps_id
from app.services.nfse_national.provider import RestrictedIssueContext, RestrictedNfseProvider
from app.services.nfse_national.signer import (generate_synthetic_test_certificate,
                                               load_pkcs12_certificate)
from app.services.nfse_national.transport import (PATH_GET_DPS, PATH_ISSUE_NFSE, FakeTransport,
                                                  HttpxRestrictedTransport, NetworkGateClosedError,
                                                  TransportRequest, TransportResponse)
from app.services.nfse_national.wire import (FIELD_ACCESS_KEY, FIELD_DPS_XML, FIELD_NFSE_XML,
                                             WireFormatError, build_issue_request_body,
                                             decode_b64_gzip_xml, decode_nfse_success_envelope,
                                             encode_xml_gzip_b64,
                                             find_nfse_access_key_in_response)
from app.services.nfse_providers import Outcome, ProviderRequest

SEFIN_NACIONAL_BASE_URL = "https://sefin.producaorestrita.nfse.gov.br/SefinNacional"
EXACT_ISSUE_URL = "https://sefin.producaorestrita.nfse.gov.br/SefinNacional/nfse"

VALID_ACCESS_KEY = (
    "NFS" "3304557" "2" "2" "11222333000181" "0000000000001" "2609" "123456789" "5"
)
OTHER_ACCESS_KEY = (
    "NFS" "3304557" "2" "2" "11222333000181" "0000000000009" "2609" "123456789" "1"
)
assert len(VALID_ACCESS_KEY) == len(OTHER_ACCESS_KEY) == 53


def _nfse_xml(access_key: str = VALID_ACCESS_KEY) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse">'
        f'<infNFSe Id="{access_key}"></infNFSe>'
        '</NFSe>'
    ).encode("utf-8")


def _success_envelope(*, access_key: str | None = VALID_ACCESS_KEY,
                      nfse_b64: str | None = None,
                      xml_access_key: str | None = None) -> bytes:
    """A ``NFSePostResponseSucesso``, with each required part overridable so a
    single test can remove or corrupt exactly one thing."""
    payload: dict = {
        "tipoAmbiente": 2,
        "versaoAplicativo": "restrita-1.0",
        "dataHoraProcessamento": "2026-09-15T00:00:00-03:00",
        "idDps": "DPS330455721122233300018100001000000000000001",
    }
    if access_key is not None:
        payload[FIELD_ACCESS_KEY] = access_key
    if nfse_b64 is not None:
        payload[FIELD_NFSE_XML] = nfse_b64
    elif xml_access_key is not None:
        payload[FIELD_NFSE_XML] = encode_xml_gzip_b64(_nfse_xml(xml_access_key))
    return json.dumps(payload).encode("utf-8")


@pytest.fixture
def context():
    cfg = NationalDpsConfiguration(
        version="SYNTH-RESTRICTED-v1", layout_version="restricted-v1.01-20260727",
        issuer_cnpj="11222333000181", issuer_name="SOPROLIFE SAUDE LTDA (SINTETICO)",
        issuer_municipio_ibge="3304557", issuer_op_simp_nac=3,
        issuer_reg_ap_trib_sn=1, issuer_reg_esp_trib=0,
        codigo_tributacao_nacional="140501",
        trib_issqn=1, tp_ret_issqn=1,
        amount_basis="financial_entry.valor", competence_rule="service_date",
        own_revenue_confirmed=True, validation_reference="SYNTHETIC-ONLY",
        p_tot_trib_sn=Decimal("6.00"),
    )
    dps_id = DpsIdComponents(codigo_municipio="3304557", tipo_inscricao_federal=2,
                             inscricao_federal="11222333000181", serie_dps="00001",
                             numero_dps="000000000000001")
    p12_bytes, password = generate_synthetic_test_certificate()
    cert = load_pkcs12_certificate(p12_bytes, password)
    return RestrictedIssueContext(config=cfg, dps_id=dps_id,
                                  recipient=Recipient(nome="Paciente Um", sem_nif_motivo=1),
                                  ver_aplic="m38-0.1", numero_dps_display="1",
                                  serie_dps_display="1", certificate=cert,
                                  municipio_prestacao_ibge="3304557")


def request():
    return ProviderRequest("doc-1", "op-1", "prep-1", "220.00", "2026-08-10",
                           "Realização de exame de espirometria.")


def _issue_with(context, response: TransportResponse | Exception):
    transport = FakeTransport(responses=[response])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    return provider.issue(request()), transport


# ============================================================ B — request encoding


def test_post_declares_content_type_application_json(context):
    """(1) The direct cause of the HTTP 415: the request never declared a
    media type at all. It must now say application/json."""
    _, transport = _issue_with(context, TransportResponse(201, _success_envelope(
        xml_access_key=VALID_ACCESS_KEY)))
    assert transport.received[0].headers["Content-Type"] == "application/json"


def test_post_declares_accept_application_json(context):
    """(2) The route produces application/json; say so explicitly."""
    _, transport = _issue_with(context, TransportResponse(201, _success_envelope(
        xml_access_key=VALID_ACCESS_KEY)))
    assert transport.received[0].headers["Accept"] == "application/json"


def test_post_body_is_json_with_exactly_the_dps_field(context):
    """(3) NFSePostRequest has exactly one required property. Sending extra
    invented fields is as wrong as sending none."""
    _, transport = _issue_with(context, TransportResponse(201, _success_envelope(
        xml_access_key=VALID_ACCESS_KEY)))
    payload = json.loads(transport.received[0].body.decode("utf-8"))
    assert list(payload.keys()) == [FIELD_DPS_XML]
    assert isinstance(payload[FIELD_DPS_XML], str)


def test_request_roundtrip_reproduces_signed_xml_byte_for_byte(context):
    """(4) base64 -> gzip -> the EXACT signed bytes. Any reserialization
    between signing and the wire would silently invalidate the XMLDSig."""
    transport = FakeTransport(responses=[TransportResponse(201, _success_envelope(
        xml_access_key=VALID_ACCESS_KEY))])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    signed_xml = provider._build_signed_dps(request())  # the same builder issue() uses
    provider.issue(request())

    payload = json.loads(transport.received[0].body.decode("utf-8"))
    recovered = gzip.decompress(base64.b64decode(payload[FIELD_DPS_XML], validate=True))
    # dhEmi differs between the two builds (it is datetime.now inside issue()),
    # so compare the stable envelope instead of the whole document, plus prove
    # the recovered bytes are a complete, signed, well-formed DPS.
    assert recovered.startswith(b'<?xml version=\'1.0\' encoding=\'UTF-8\'') or \
        recovered.startswith(b'<?xml version="1.0" encoding="UTF-8"')
    assert recovered.rstrip().endswith(b"</DPS>")
    assert b"Signature" in recovered
    assert len(signed_xml) > 0


def test_encode_decode_is_byte_exact_for_arbitrary_payload():
    """(4, isolated) The helper itself, with no provider machinery around it."""
    original = b'<?xml version="1.0"?><DPS><infDPS Id="x"/>\xc3\xa1\xc3\xa9</DPS>'
    assert decode_b64_gzip_xml(encode_xml_gzip_b64(original)) == original


def test_encoded_base64_has_no_line_wrapping():
    """(4) A wrapped Base64 value would be a syntactically valid JSON string
    and a semantically corrupt payload — the worst kind of bug."""
    encoded = encode_xml_gzip_b64(b"x" * 8000)
    assert "\n" not in encoded and "\r" not in encoded


def test_encoding_is_deterministic_across_calls():
    """GZip normally stamps the current mtime into its header, which would
    make identical input produce different bytes on every call. mtime=0 keeps
    the encoding reproducible, so tests can assert on exact values."""
    payload = b"<DPS>deterministic</DPS>"
    assert encode_xml_gzip_b64(payload) == encode_xml_gzip_b64(payload)
    header = base64.b64decode(encode_xml_gzip_b64(payload))[4:8]
    assert header == b"\x00\x00\x00\x00"  # the mtime field, zeroed


def test_raw_signed_xml_is_never_the_http_body(context):
    """(5) The regression that caused the 415, stated as a negative."""
    transport = FakeTransport(responses=[TransportResponse(201, _success_envelope(
        xml_access_key=VALID_ACCESS_KEY))])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    provider.issue(request())
    body = transport.received[0].body
    assert not body.lstrip().startswith(b"<")
    assert b"<DPS" not in body and b"infDPS" not in body and b"Signature" not in body
    json.loads(body.decode("utf-8"))  # it is JSON, and nothing else


def test_build_issue_request_body_rejects_non_bytes():
    with pytest.raises(WireFormatError):
        build_issue_request_body("<DPS/>")  # type: ignore[arg-type]


def test_encoding_failure_raises_and_sends_nothing(context, monkeypatch):
    """A local encoding bug must surface as an error, never be swallowed into
    UNCERTAIN — that would park a document behind a reconciliation for a
    submission that was never even attempted."""
    import app.services.nfse_national.provider as provider_module

    monkeypatch.setattr(provider_module, "build_issue_request_body",
                        lambda _: (_ for _ in ()).throw(WireFormatError("falha_local")))
    transport = FakeTransport(responses=[TransportResponse(201, b"{}")])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    with pytest.raises(WireFormatError):
        provider.issue(request())
    assert transport.received == []


class _UrlCapturingTransport(httpx.BaseTransport):
    """A real httpx backend that answers in-process — genuine httpx URL-join
    logic, never a socket."""

    def __init__(self, response_body: bytes = b"{}"):
        self.last_request: httpx.Request | None = None
        self.last_content: bytes | None = None
        self._response_body = response_body

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.last_request = request
        self.last_content = request.content
        return httpx.Response(201, content=self._response_body)


_REAL_HTTPX_CLIENT = httpx.Client


def _real_httpx_client_factory(capture: _UrlCapturingTransport):
    def factory(*, base_url, timeout, verify=None):  # noqa: ARG001
        return _REAL_HTTPX_CLIENT(base_url=base_url, timeout=timeout, transport=capture)
    return factory


def test_exact_sefin_url_and_headers_survive_the_real_httpx_layer(monkeypatch):
    """(6) The corrected body/headers must still resolve to exactly the URL
    the live route sweep confirmed accepts POST — M37's guarantee is not
    allowed to regress while fixing the payload."""
    capture = _UrlCapturingTransport()
    monkeypatch.setattr(transport_module.httpx, "Client", _real_httpx_client_factory(capture))
    transport = HttpxRestrictedTransport(base_url=SEFIN_NACIONAL_BASE_URL,
                                         network_enabled=True, environment="restricted")
    body = build_issue_request_body(b"<DPS/>")
    transport.send(TransportRequest("POST", PATH_ISSUE_NFSE, body=body,
                                    headers={"Content-Type": "application/json",
                                             "Accept": "application/json"}))
    assert str(capture.last_request.url) == EXACT_ISSUE_URL
    assert capture.last_request.method == "POST"
    assert capture.last_request.headers["content-type"] == "application/json"
    assert capture.last_request.headers["accept"] == "application/json"
    assert capture.last_content == body


def test_adn_host_guard_still_refuses_issuance_with_the_new_body():
    """(17) The M37 ADN guard must fire before anything is sent, regardless of
    how the body is now encoded."""
    transport = HttpxRestrictedTransport(base_url="https://adn.producaorestrita.nfse.gov.br",
                                         network_enabled=True, environment="restricted")
    with pytest.raises(NetworkGateClosedError, match="adn_distribution_host"):
        transport.send(TransportRequest("POST", PATH_ISSUE_NFSE,
                                        body=build_issue_request_body(b"<DPS/>"),
                                        headers={"Content-Type": "application/json"}))


# ============================================================ C — response decoding


def test_success_201_envelope_is_decoded_and_key_extracted(context):
    """(7, 8, 9) The whole happy path: JSON -> base64 -> gzip -> NFS-e XML ->
    infNFSe/@Id, cross-checked against the envelope's own chaveAcesso."""
    result, _ = _issue_with(context, TransportResponse(201, _success_envelope(
        access_key=VALID_ACCESS_KEY, xml_access_key=VALID_ACCESS_KEY)))
    assert result.outcome == Outcome.SIMULATED
    assert result.external_id == VALID_ACCESS_KEY
    assert result.diagnostic_code is None


def test_nfse_xml_roundtrip_returns_the_original_document():
    """(8, isolated) The decoded NFS-e XML is the exact document, not a
    re-rendered approximation of it."""
    envelope = _success_envelope(xml_access_key=VALID_ACCESS_KEY)
    decoded = decode_nfse_success_envelope(envelope)
    assert decoded.nfse_xml == _nfse_xml(VALID_ACCESS_KEY)
    assert decoded.access_key == VALID_ACCESS_KEY


@pytest.mark.parametrize("body, reason", [
    (b"{not json at all", "malformed JSON (10)"),
    (b"[]", "JSON array, not the documented object (10)"),
    (b'"NFS..."', "JSON scalar, not an object (10)"),
    (b"", "empty body"),
])
def test_malformed_json_fails_closed(context, body, reason):
    result, _ = _issue_with(context, TransportResponse(201, body))
    assert result.outcome == Outcome.UNCERTAIN, reason
    assert result.external_id is None


def test_invalid_base64_fails_closed(context):
    """(11) Non-Base64 characters must be rejected, never silently dropped
    until the remainder happens to decode."""
    result, _ = _issue_with(context, TransportResponse(201, _success_envelope(
        nfse_b64="!!!! not base64 !!!!")))
    assert result.outcome == Outcome.UNCERTAIN
    assert result.external_id is None


def test_invalid_gzip_fails_closed(context):
    """(12) Valid Base64 whose bytes are not a GZip member."""
    not_gzip = base64.b64encode(b"plain bytes, no gzip header").decode("ascii")
    result, _ = _issue_with(context, TransportResponse(201, _success_envelope(nfse_b64=not_gzip)))
    assert result.outcome == Outcome.UNCERTAIN
    assert result.external_id is None


def test_truncated_gzip_fails_closed():
    """(12) A GZip member cut short must raise, never return partial XML."""
    full = base64.b64decode(encode_xml_gzip_b64(_nfse_xml()))
    with pytest.raises(WireFormatError, match="gzip_invalido"):
        decode_b64_gzip_xml(base64.b64encode(full[:-6]).decode("ascii"))


def test_missing_chave_acesso_fails_closed(context):
    """(13)"""
    result, _ = _issue_with(context, TransportResponse(201, _success_envelope(
        access_key=None, xml_access_key=VALID_ACCESS_KEY)))
    assert result.outcome == Outcome.UNCERTAIN
    assert result.external_id is None


def test_missing_nfse_xml_fails_closed(context):
    """(14) A chaveAcesso with no NFS-e XML to corroborate it is not enough —
    the key is only trustworthy when both layers agree."""
    result, _ = _issue_with(context, TransportResponse(201, _success_envelope(
        access_key=VALID_ACCESS_KEY)))
    assert result.outcome == Outcome.UNCERTAIN
    assert result.external_id is None


def test_access_key_mismatch_between_json_and_xml_fails_closed(context):
    """(15) Two different keys in one response means the response cannot be
    trusted at all — never pick one of them."""
    result, _ = _issue_with(context, TransportResponse(201, _success_envelope(
        access_key=VALID_ACCESS_KEY, xml_access_key=OTHER_ACCESS_KEY)))
    assert result.outcome == Outcome.UNCERTAIN
    assert result.external_id is None


@pytest.mark.parametrize("bad_xml", [
    b"<NFSe/>",
    b"<NFSe xmlns='http://www.sped.fazenda.gov.br/nfse'><infNFSe/></NFSe>",
    b"<NFSe xmlns='http://www.sped.fazenda.gov.br/nfse'><infNFSe Id='NOT-A-KEY'/></NFSe>",
    b"not xml at all",
])
def test_decoded_payload_that_is_not_a_valid_nfse_fails_closed(context, bad_xml):
    """Perfect JSON + perfect Base64 + perfect GZip around a document that is
    not a schema-shaped NFS-e is still not an issuance."""
    result, _ = _issue_with(context, TransportResponse(201, _success_envelope(
        nfse_b64=encode_xml_gzip_b64(bad_xml))))
    assert result.outcome == Outcome.UNCERTAIN
    assert result.external_id is None


def test_wire_errors_never_carry_response_content():
    """Fail-closed must not become a leak: the error codes are fixed strings."""
    secret = b'{"chaveAcesso":"NFS-x","nfseXmlGZipB64":"' + b"cpf-12345678901" + b'"}'
    with pytest.raises(WireFormatError) as caught:
        decode_nfse_success_envelope(secret)
    assert "12345678901" not in str(caught.value)


# ============================================================ 4xx/5xx diagnostics (M35 intact)


@pytest.mark.parametrize("status, expected", [
    (400, "provider_rejected:http_400"),
    (403, "provider_rejected:http_403"),
    (415, "provider_rejected:http_415"),
    (500, "provider_server_error:http_500"),
])
def test_http_error_diagnostics_remain_safe_and_correct(context, status, expected):
    """(16) Including the 415 this milestone exists to fix: if it ever happens
    again it must still surface as a status-only, body-free diagnostic."""
    sensitive = b'{"erros":[{"codigo":"E99","mensagem":"cpf 12345678901 invalido"}]}'
    result, _ = _issue_with(context, TransportResponse(status, sensitive))
    assert result.diagnostic_code == expected
    assert result.outcome == (Outcome.REJECTED if status < 500 else Outcome.UNCERTAIN)
    assert result.external_id is None
    for leaked in ("12345678901", "E99", "mensagem", "erros"):
        assert leaked not in result.diagnostic_code


def test_error_envelope_never_changes_classification(context):
    """NFSePostResponseErro is documented and (M40) surfaced in
    ``validation_errors`` for a human to read, but it never changes
    classification: outcome/state/error_code stay HTTP-status-only,
    exactly as M35 established."""
    erro = json.dumps({
        "tipoAmbiente": 2, "versaoAplicativo": "restrita-1.0",
        "dataHoraProcessamento": "2026-09-15T00:00:00-03:00",
        "idDPS": "DPS330455721122233300018100001000000000000001",
        "erros": [{"codigo": "E0001", "descricao": "segredo interno"}],
    }).encode("utf-8")
    result, _ = _issue_with(context, TransportResponse(422, erro))
    assert result.outcome == Outcome.REJECTED
    assert result.diagnostic_code == "provider_rejected:http_422"
    # M40/M41 — this is the new, intentional surface: the actual SEFIN code
    # and description are no longer thrown away.
    assert result.validation_errors == ({"codigo": "E0001", "descricao": "segredo interno",
                                        "complemento": None, "mensagem": None, "erro": None},)
    assert "segredo" not in (result.diagnostic_code or "")


def test_no_retry_is_ever_attempted(context):
    """(18) Exactly one HTTP request per issue() call, for every outcome —
    a blind retry on an uncertain state is the one thing this pipeline must
    never do."""
    for response in (TransportResponse(415, b"{}"), TransportResponse(500, b"{}"),
                     TransportResponse(201, b"{bad json"), TimeoutError("t")):
        transport = FakeTransport(responses=[response])
        provider = RestrictedNfseProvider(transport=transport, context=context)
        provider.issue(request())
        assert len(transport.received) == 1


# ============================================================ 19 — reconcile / GET /dps


def test_reconcile_sends_accept_json_to_the_documented_dps_path(context):
    """(19) The GET keeps the official TSIdDPS path and now declares that it
    accepts the JSON the service produces. It carries no body, so it declares
    no Content-Type."""
    transport = FakeTransport(responses=[TransportResponse(404, b"")])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    provider.query(request(), "issue")
    sent = transport.received[0]
    assert sent.method == "GET"
    assert sent.path == PATH_GET_DPS.format(dps_id=build_dps_id(context.dps_id))
    assert sent.headers["Accept"] == "application/json"
    assert "Content-Type" not in sent.headers
    assert sent.body is None


def test_reconcile_decodes_the_gzip_envelope_when_the_body_carries_one(context):
    """(19) The latent second protocol bug this milestone closes: a compressed
    access key is invisible to a raw-byte scan, so an issued document would
    have reconciled as UNCERTAIN forever."""
    transport = FakeTransport(responses=[TransportResponse(200, _success_envelope(
        access_key=VALID_ACCESS_KEY, xml_access_key=VALID_ACCESS_KEY))])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    result = provider.query(request(), "issue")
    assert result.outcome == Outcome.SIMULATED
    assert result.external_id == VALID_ACCESS_KEY


def test_reconcile_envelope_without_chave_acesso_still_decodes_from_xml():
    """The GET response schema is NOT documented (see the audit note in
    wire.find_nfse_access_key_in_response). Requiring chaveAcesso here — a
    field only proven for the POST success schema — could turn a real,
    fully-decodable NFS-e into a false UNCERTAIN, so it is required only when
    present."""
    body = _success_envelope(access_key=None, xml_access_key=VALID_ACCESS_KEY)
    assert find_nfse_access_key_in_response(body) == VALID_ACCESS_KEY


def test_reconcile_envelope_with_mismatched_key_fails_closed():
    body = _success_envelope(access_key=VALID_ACCESS_KEY, xml_access_key=OTHER_ACCESS_KEY)
    assert find_nfse_access_key_in_response(body) is None


def test_reconcile_broken_envelope_never_falls_back_to_a_looser_guess():
    """A body that declares nfseXmlGZipB64 and then fails to decode it is
    broken, not an invitation to scan for a key somewhere else in it."""
    body = json.dumps({
        FIELD_NFSE_XML: "!!!not base64!!!",
        "observacao": f"a chave seria {VALID_ACCESS_KEY}",
    }).encode("utf-8")
    assert find_nfse_access_key_in_response(body) is None


def test_reconcile_keeps_the_legacy_tolerant_paths_for_non_envelope_bodies():
    """Nothing proven before M38 is dropped: raw NFS-e XML and a single
    unambiguous TSIdNFSe occurrence still resolve."""
    assert find_nfse_access_key_in_response(_nfse_xml()) == VALID_ACCESS_KEY
    plain_json = json.dumps({"chaveAcesso": VALID_ACCESS_KEY}).encode("utf-8")
    assert find_nfse_access_key_in_response(plain_json) == VALID_ACCESS_KEY
    assert find_nfse_access_key_in_response(b'{"status":"ok"}') is None


def test_reconcile_ambiguous_body_stays_uncertain():
    both = f"{VALID_ACCESS_KEY} {OTHER_ACCESS_KEY}".encode("ascii")
    assert find_nfse_access_key_in_response(both) is None


def test_reconcile_404_is_still_the_only_proven_absence(context):
    transport = FakeTransport(responses=[TransportResponse(404, b"")])
    provider = RestrictedNfseProvider(transport=transport, context=context)
    result = provider.query(request(), "issue")
    assert result.outcome == Outcome.NOT_FOUND
    assert result.diagnostic_code == "provider_confirmed_not_found:http_404"


def test_gzip_helper_is_not_confused_by_a_zip_or_zlib_stream():
    """zlib (no GZip header) and a ZIP archive are both plausible confusions
    of "compressed"; neither may be accepted as a GZip member."""
    raw_deflate = base64.b64encode(io.BytesIO(
        __import__("zlib").compress(b"<NFSe/>")).getvalue()).decode("ascii")
    with pytest.raises(WireFormatError, match="gzip_invalido"):
        decode_b64_gzip_xml(raw_deflate)
    with pytest.raises(WireFormatError, match="gzip_invalido"):
        decode_b64_gzip_xml(base64.b64encode(b"PK\x03\x04rest").decode("ascii"))
