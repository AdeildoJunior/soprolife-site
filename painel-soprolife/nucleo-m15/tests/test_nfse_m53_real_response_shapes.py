"""M53 — the REAL restricted-SEFIN response envelopes, end to end.

Fixtures mirror the exact structures observed live on 2026-09-19 for DPS #10.
They are SANITIZED: the real NFS-e carries recipient name/CPF/address, so the
XML here is rebuilt with the same element structure and fictional personal
data. Only non-personal fiscal identifiers (issuer CNPJ, municipality,
competence, amount, access key) are reproduced verbatim.

Two defects are pinned here:

1. ``GET /nfse/{chave}`` answers ``application/json`` with the SAME
   ``nfseXmlGZipB64`` envelope as ``POST /nfse`` — not raw XML. Parsing the
   bytes as XML reported "fetch failed" on a perfectly good HTTP 200.
2. The envelope cross-check compared ``infNFSe/@Id`` (53 chars, ``NFS``
   prefix) against the JSON ``chaveAcesso`` (bare 50) with ``!=``, which
   could never pass — see test_nfse_access_key_real_sefin_shapes.py.
"""
import base64
import gzip
import json

import pytest

from app.services.nfse_national.identifiers import normalize_nfse_access_key
from app.services.nfse_national.wire import (
    WireFormatError,
    decode_nfse_document_response,
    decode_nfse_success_envelope,
    encode_xml_gzip_b64,
    find_nfse_access_key_in_response,
)

KEY = "33045572263544026000110000000000000126094282476576"
ID_ATTR = "NFS" + KEY
ID_DPS = "DPS330455726354402600011000001000000000000010"
LOCATION = "http://sefin.producaorestrita.nfse.gov.br/SefinNacional/nfse/" + KEY
NS = "http://www.sped.fazenda.gov.br/nfse"
DS = "http://www.w3.org/2000/09/xmldsig#"


def sanitized_nfse_xml(id_attr: str = ID_ATTR, id_dps: str = ID_DPS) -> bytes:
    """Same element structure as the real NFS-e; fictional personal data."""
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<NFSe versao="1.01" xmlns="{NS}">'
        f'<infNFSe Id="{id_attr}">'
        "<xLocEmi>Rio de Janeiro</xLocEmi><nNFSe>1</nNFSe>"
        "<verAplic>SefinNacional_1.6.0</verAplic><ambGer>2</ambGer>"
        "<dhProc>2026-09-19T01:27:33-03:00</dhProc>"
        "<emit><CNPJ>63544026000110</CNPJ><xNome>EMPRESA EXEMPLO LTDA</xNome></emit>"
        "<valores><vLiq>440.00</vLiq></valores>"
        f'<DPS><infDPS Id="{id_dps}">'
        "<tpAmb>2</tpAmb><dhEmi>2026-09-19T01:13:12-03:00</dhEmi>"
        "<serie>1</serie><nDPS>10</nDPS><dCompet>2026-09-15</dCompet>"
        "<cLocEmi>3304557</cLocEmi>"
        "<prest><CNPJ>63544026000110</CNPJ></prest>"
        "<toma><CPF>00000000000</CPF><xNome>PACIENTE EXEMPLO</xNome></toma>"
        "<valores><vServPrest><vServ>440.00</vServ></vServPrest></valores>"
        "</infDPS>"
        f'<Signature xmlns="{DS}"><SignatureValue>AA==</SignatureValue></Signature>'
        "</DPS>"
        "</infNFSe>"
        f'<Signature xmlns="{DS}"><SignatureValue>BB==</SignatureValue></Signature>'
        "</NFSe>"
    ).encode("utf-8")


def real_get_nfse_envelope(key: str = KEY, xml: bytes | None = None) -> bytes:
    """Exactly the 5-field shape GET /nfse/{chave} returned."""
    return json.dumps({
        "tipoAmbiente": 2,
        "versaoAplicativo": "SefinNacional_1.6.0",
        "dataHoraProcessamento": "2026-09-19T01:57:15.3962113-03:00",
        "chaveAcesso": key,
        "nfseXmlGZipB64": encode_xml_gzip_b64(xml if xml is not None else sanitized_nfse_xml()),
    }).encode("utf-8")


def real_get_dps_body(key: str = KEY) -> bytes:
    """The 4-field shape GET /dps/{idDPS} returned — no document, key only."""
    return json.dumps({
        "tipoAmbiente": 2,
        "versaoAplicativo": "SefinNacional_1.6.0",
        "dataHoraProcessamento": "2026-09-19T01:13:12.5527997-03:00",
        "chaveAcesso": key,
    }).encode("utf-8")


# ------------------------------------------------------------ GET /dps -----
def test_real_get_dps_body_yields_the_key():
    assert find_nfse_access_key_in_response(real_get_dps_body()) == ID_ATTR


def test_real_get_dps_body_agrees_with_location():
    assert find_nfse_access_key_in_response(
        real_get_dps_body(), location_header=LOCATION) == ID_ATTR


def test_get_dps_body_location_divergence_fails_closed():
    other = KEY[:-1] + ("0" if KEY[-1] != "0" else "1")
    assert find_nfse_access_key_in_response(
        real_get_dps_body(), location_header=LOCATION.replace(KEY, other)) is None


def test_get_dps_response_is_not_a_document_envelope():
    """It carries the key but no NFS-e — must never decode as one."""
    with pytest.raises(WireFormatError):
        decode_nfse_document_response(real_get_dps_body())


# ----------------------------------------------------------- GET /nfse -----
def test_real_get_nfse_envelope_decodes_to_the_xml():
    decoded = decode_nfse_document_response(real_get_nfse_envelope())
    assert decoded.access_key == ID_ATTR
    assert decoded.nfse_xml.lstrip().startswith(b"<?xml")
    assert ID_DPS.encode() in decoded.nfse_xml


def test_real_get_nfse_envelope_is_json_not_xml():
    """Regression for the M52.1 'fetch failed': the body starts with '{'."""
    assert real_get_nfse_envelope().lstrip()[:1] == b"{"


def test_get_nfse_accepts_raw_xml_too():
    xml = sanitized_nfse_xml()
    assert decode_nfse_document_response(xml).access_key == ID_ATTR


def test_get_nfse_cross_checks_expected_key_in_either_encoding():
    body = real_get_nfse_envelope()
    assert decode_nfse_document_response(body, expected_access_key=KEY).access_key == ID_ATTR
    assert decode_nfse_document_response(body, expected_access_key=ID_ATTR).access_key == ID_ATTR


def test_get_nfse_rejects_a_different_expected_key():
    other = KEY[:-1] + ("0" if KEY[-1] != "0" else "1")
    with pytest.raises(WireFormatError):
        decode_nfse_document_response(real_get_nfse_envelope(), expected_access_key=other)


def test_get_nfse_envelope_key_must_match_its_own_xml():
    other = KEY[:-1] + ("0" if KEY[-1] != "0" else "1")
    with pytest.raises(WireFormatError):
        decode_nfse_document_response(real_get_nfse_envelope(key=other))


# ------------------------------------------- the HTTP 201 POST envelope -----
def test_real_201_envelope_decodes_with_the_fixed_cross_check():
    """The shape the POST answered with. Before M52.1 the bare-vs-prefixed
    key comparison made this raise, producing
    provider_malformed_response:http_201 on a real issuance."""
    decoded = decode_nfse_success_envelope(real_get_nfse_envelope())
    assert decoded.access_key == ID_ATTR
    assert normalize_nfse_access_key(decoded.access_key) == KEY


def test_extra_metadata_fields_do_not_break_the_envelope():
    """tipoAmbiente / versaoAplicativo / dataHoraProcessamento are present in
    the real body and must simply be ignored."""
    body = json.loads(real_get_nfse_envelope())
    assert {"tipoAmbiente", "versaoAplicativo", "dataHoraProcessamento"} <= set(body)
    assert decode_nfse_success_envelope(real_get_nfse_envelope()).access_key == ID_ATTR


# ------------------------------------------------------- failure modes -----
def test_truncated_base64_payload_fails_closed():
    body = json.loads(real_get_nfse_envelope())
    body["nfseXmlGZipB64"] = body["nfseXmlGZipB64"][:100]
    with pytest.raises(WireFormatError):
        decode_nfse_document_response(json.dumps(body).encode())


def test_truncated_gzip_stream_fails_closed():
    raw = gzip.compress(sanitized_nfse_xml())
    body = json.loads(real_get_nfse_envelope())
    body["nfseXmlGZipB64"] = base64.b64encode(raw[: len(raw) // 2]).decode()
    with pytest.raises(WireFormatError):
        decode_nfse_document_response(json.dumps(body).encode())


def test_payload_that_is_not_xml_after_decoding_fails_closed():
    body = json.loads(real_get_nfse_envelope())
    body["nfseXmlGZipB64"] = encode_xml_gzip_b64(b"isto nao e xml")
    with pytest.raises(WireFormatError):
        decode_nfse_document_response(json.dumps(body).encode())


def test_xml_without_access_key_attribute_fails_closed():
    xml = sanitized_nfse_xml().replace(f'Id="{ID_ATTR}"'.encode(), b"")
    with pytest.raises(WireFormatError):
        decode_nfse_document_response(real_get_nfse_envelope(xml=xml))


@pytest.mark.parametrize("body", [b"", b"   ", b"nao json nem xml", b"{", b"[]"])
def test_malformed_bodies_fail_closed(body):
    with pytest.raises(WireFormatError):
        decode_nfse_document_response(body)
