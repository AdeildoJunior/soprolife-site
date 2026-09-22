"""M52.1 — regression against the REAL restricted-SEFIN response shapes.

Every payload here is the shape the live Produção Restrita API actually
returned on 2026-09-19 for DPS #10, not an invented example:

  GET /dps/{idDPS} -> 200 application/json
    Location: http://sefin.producaorestrita.nfse.gov.br/SefinNacional/nfse/<50>
    {"chaveAcesso": "<50>", "dataHoraProcessamento": "...",
     "tipoAmbiente": 2, "versaoAplicativo": "..."}

The bug these tests pin: the codebase modelled the access key ONLY as the
53-character ``TSIdNFSe`` (``NFS`` + 50), which is the ``infNFSe/@Id``
attribute, and compared it by string equality against the JSON
``chaveAcesso`` — which is the same identifier in its bare 50-character form.
That comparison could never succeed, so real successes were read as
"malformed" and the reconciliation extractor returned None on a body that
plainly carried the key.
"""
import gzip
import json

import pytest

from app.services.nfse_national.identifiers import (
    InvalidIdentifierError,
    access_key_from_location_header,
    nfse_access_keys_match,
    normalize_nfse_access_key,
)
from app.services.nfse_national.wire import (
    WireFormatError,
    decode_nfse_success_envelope,
    encode_xml_gzip_b64,
    find_nfse_access_key_in_response,
)

# The real key returned for DPS #10 (public fiscal identifier, no PII).
REAL_KEY = "33045572263544026000110000000000000126094282476576"
REAL_ID_ATTR = "NFS" + REAL_KEY
REAL_LOCATION = (
    "http://sefin.producaorestrita.nfse.gov.br/SefinNacional/nfse/" + REAL_KEY
)
NFSE_NS = "http://www.sped.fazenda.gov.br/nfse"


def real_dps_query_body(key: str = REAL_KEY) -> bytes:
    """Byte-for-byte the shape observed on GET /dps/{idDPS}."""
    return json.dumps({
        "chaveAcesso": key,
        "dataHoraProcessamento": "2026-09-19T01:13:12.5527997-03:00",
        "tipoAmbiente": 2,
        "versaoAplicativo": "1.0.0-PRODRESTRITA",
    }).encode("utf-8")


def nfse_xml(id_attr: str = REAL_ID_ATTR) -> bytes:
    return (
        f'<NFSe xmlns="{NFSE_NS}"><infNFSe Id="{id_attr}">'
        f"<nNFSe>1</nNFSe></infNFSe></NFSe>"
    ).encode("utf-8")


# ----------------------------------------------------------- normalization --
def test_bare_50_char_key_is_canonical():
    assert normalize_nfse_access_key(REAL_KEY) == REAL_KEY


def test_prefixed_53_char_id_normalizes_to_the_bare_key():
    assert normalize_nfse_access_key(REAL_ID_ATTR) == REAL_KEY


def test_the_two_encodings_are_the_same_identifier():
    assert nfse_access_keys_match(REAL_KEY, REAL_ID_ATTR)


def test_a_genuinely_different_key_still_fails():
    other = REAL_KEY[:-1] + ("0" if REAL_KEY[-1] != "0" else "1")
    assert not nfse_access_keys_match(REAL_KEY, other)


@pytest.mark.parametrize("bad", [
    "", "   ", "NFS", REAL_KEY[:-1], REAL_KEY + "0", "NFS" + REAL_KEY[:-1],
    "33045572263544026000110000000000000126094282-7657", None, 12345,
])
def test_invalid_keys_are_rejected(bad):
    with pytest.raises(InvalidIdentifierError):
        normalize_nfse_access_key(bad)


# ------------------------------------------------------------- Location -----
def test_location_header_yields_the_key():
    assert access_key_from_location_header(REAL_LOCATION) == REAL_KEY


def test_location_header_tolerates_trailing_slash():
    assert access_key_from_location_header(REAL_LOCATION + "/") == REAL_KEY


@pytest.mark.parametrize("bad", [None, "", "not a url", "https://x/nfse/", "https://x/nfse/123"])
def test_bad_location_header_returns_none_without_raising(bad):
    assert access_key_from_location_header(bad) is None


# -------------------------------------------- the real GET /dps/{id} body ---
def test_real_dps_query_body_yields_the_key():
    """Returned in the codebase's storage convention (TSIdNFSe, prefixed) —
    M52.1 fixes the parser without changing what a success records."""
    assert find_nfse_access_key_in_response(real_dps_query_body()) == REAL_ID_ATTR


def test_real_body_and_matching_location_agree():
    assert find_nfse_access_key_in_response(
        real_dps_query_body(), location_header=REAL_LOCATION) == REAL_ID_ATTR


def test_body_and_location_disagreement_fails_closed():
    other = REAL_KEY[:-1] + ("0" if REAL_KEY[-1] != "0" else "1")
    assert find_nfse_access_key_in_response(
        real_dps_query_body(), location_header=REAL_LOCATION.replace(REAL_KEY, other)) is None


def test_invalid_key_in_body_is_rejected_even_with_good_location():
    assert find_nfse_access_key_in_response(
        real_dps_query_body("123"), location_header=REAL_LOCATION) is None


def test_location_alone_is_never_promoted_to_the_key():
    """A body with no key does not become a success because of a header."""
    body = json.dumps({"tipoAmbiente": 2}).encode()
    assert find_nfse_access_key_in_response(body, location_header=REAL_LOCATION) is None


def test_body_without_location_still_works():
    """The Location header is corroboration, not a requirement."""
    assert find_nfse_access_key_in_response(
        real_dps_query_body(), location_header=None) == REAL_ID_ATTR


# ------------------------------------------- the POST success envelope ------
def test_post_success_envelope_accepts_the_two_encodings():
    """The cross-check that could never pass before: bare chaveAcesso in JSON
    vs. NFS-prefixed infNFSe/@Id in the XML."""
    body = json.dumps({
        "chaveAcesso": REAL_KEY,
        "nfseXmlGZipB64": encode_xml_gzip_b64(nfse_xml()),
    }).encode()
    decoded = decode_nfse_success_envelope(body)
    assert decoded.access_key == REAL_ID_ATTR


def test_post_success_envelope_still_rejects_a_real_mismatch():
    other = REAL_KEY[:-1] + ("0" if REAL_KEY[-1] != "0" else "1")
    body = json.dumps({
        "chaveAcesso": other,
        "nfseXmlGZipB64": encode_xml_gzip_b64(nfse_xml()),
    }).encode()
    with pytest.raises(WireFormatError):
        decode_nfse_success_envelope(body)


def test_post_envelope_without_xml_is_still_malformed():
    """GET /dps returns the key WITHOUT the document; that shape must never be
    accepted as a POST success envelope."""
    with pytest.raises(WireFormatError):
        decode_nfse_success_envelope(real_dps_query_body())


def test_envelope_with_key_only_in_xml_is_accepted():
    body = json.dumps({
        "chaveAcesso": REAL_ID_ATTR,   # prefixed form in JSON
        "nfseXmlGZipB64": encode_xml_gzip_b64(nfse_xml()),
    }).encode()
    assert decode_nfse_success_envelope(body).access_key == REAL_ID_ATTR


def test_gzip_envelope_roundtrip_is_unchanged():
    raw = nfse_xml()
    assert gzip.decompress(__import__("base64").b64decode(encode_xml_gzip_b64(raw))) == raw


def test_storage_convention_is_unchanged_by_the_fix():
    """The POST path still records the 53-char TSIdNFSe form it always did."""
    body = json.dumps({
        "chaveAcesso": REAL_KEY,
        "nfseXmlGZipB64": encode_xml_gzip_b64(nfse_xml()),
    }).encode()
    key = decode_nfse_success_envelope(body).access_key
    assert key.startswith("NFS") and len(key) == 53
    assert normalize_nfse_access_key(key) == REAL_KEY
