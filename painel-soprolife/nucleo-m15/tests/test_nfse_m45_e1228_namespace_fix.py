"""M45 — SEFIN rule E1228 ("Uso de prefixo de namespace não permitido na
área de dados descompactada") rejected DPS #1-#8. Confirmed on DPS #8's
real restricted response (2026-09-16): codigo=E1228, descricao="Xml
declarado com prefixo de namespace." Root cause proven by inspecting our
own signed XML: signxml's default configuration emits
``<ds:Signature xmlns:ds="...">`` — a namespace PREFIX.

This file proves the fix (signer.namespaces = {None: XMLDSig-URI}, a
supported signxml mechanism) produces a signature with ZERO namespace
prefixes anywhere, while every prior guarantee (cryptographic validity,
algorithms, XSD conformance, M38 wire round-trip, M41/M43 business
fields) still holds.
"""
import base64
import gzip

from lxml import etree

from app.services.nfse_national.dps_builder import build_dps_element, serialize_dps
from app.services.nfse_national.signer import (
    generate_synthetic_test_certificate,
    load_pkcs12_certificate,
    sign_dps,
    verify_dps_signature,
)
from app.services.nfse_national.wire import decode_b64_gzip_xml, encode_xml_gzip_b64
from app.services.nfse_national.xsd_validation import validate_dps_xml
from tests.test_nfse_m43_pis_cofins import synthetic_config
from tests.test_nfse_national_dps_builder import synthetic_dps_input

NFSE_NS = "http://www.sped.fazenda.gov.br/nfse"
DS_NS = "http://www.w3.org/2000/09/xmldsig#"


def _q(ns, tag):
    return f"{{{ns}}}{tag}"


def _certificate():
    p12_bytes, password = generate_synthetic_test_certificate()
    return load_pkcs12_certificate(p12_bytes, password)


def _signed_xml_and_root():
    """Every test in this file signs a DPS that ALSO carries the M41/M43
    proven corrections (regApTribSN, pTotTribSN, tribFed/piscofins) — the
    E1228 fix must hold together with everything already proven, not in
    isolation."""
    certificate = _certificate()
    config = synthetic_config(pis_cofins_cst="00", pis_cofins_tp_ret=0)
    root = build_dps_element(synthetic_dps_input(config=config))
    signed = sign_dps(root, certificate)
    xml_bytes = serialize_dps(signed)
    return xml_bytes, signed, certificate


def _all_prefixes(tree_root) -> set[str]:
    """Every namespace PREFIX (never the default/None binding) used
    anywhere in the tree — as an nsmap key at any element, and as any
    element's own resolved prefix. Empty set means zero prefixes."""
    prefixes: set[str] = set()
    for el in tree_root.iter():
        if not isinstance(el.tag, str):
            continue  # skip comments/processing instructions
        if el.prefix:
            prefixes.add(el.prefix)
        for p in el.nsmap:
            if p is not None:
                prefixes.add(p)
    return prefixes


# ============================================================ 1/2/3 — exact required shape


def test_signature_opening_tag_is_default_namespace_no_prefix():
    xml_bytes, _, _ = _signed_xml_and_root()
    assert b'<Signature xmlns="http://www.w3.org/2000/09/xmldsig#">' in xml_bytes


def test_signed_xml_contains_no_forbidden_prefix_markers():
    xml_bytes, _, _ = _signed_xml_and_root()
    for forbidden in (b"<ds:", b"</ds:", b"xmlns:ds=", b"ns0:", b"ns1:"):
        assert forbidden not in xml_bytes, f"found forbidden marker {forbidden!r}"


def test_no_element_or_namespace_declaration_uses_any_prefix():
    """Parse the COMPLETE signed DPS (from bytes, exactly as SEFIN would)
    and programmatically confirm zero prefixes anywhere — not just in the
    Signature subtree."""
    xml_bytes, _, _ = _signed_xml_and_root()
    reparsed = etree.fromstring(xml_bytes)
    prefixes = _all_prefixes(reparsed)
    assert prefixes == set(), f"namespace_prefix_count != 0: found prefixes {prefixes}"


def test_returned_signed_object_itself_has_zero_prefixes():
    """The object sign_dps() returns directly (before any caller-side
    serialize/reparse) must ALSO be prefix-free — this is the M45 fix to
    signxml's raw in-memory quirk, proven independently of bytes."""
    _, signed, _ = _signed_xml_and_root()
    assert _all_prefixes(signed) == set()
    assert signed.find(f".//{_q(DS_NS, 'Signature')}") is not None


# ============================================================ 4/5 — cryptographic validity


def test_signature_still_verifies_after_namespace_fix():
    xml_bytes, signed, certificate = _signed_xml_and_root()
    verify_dps_signature(signed, certificate.certificate_pem)  # must not raise
    # Also verify from a freshly re-parsed copy of the bytes, matching
    # exactly what a real consumer (XSD validation, wire transport) does.
    reparsed = etree.fromstring(xml_bytes)
    verify_dps_signature(reparsed, certificate.certificate_pem)


def test_reference_uri_still_targets_exact_infdps_id():
    xml_bytes, _, _ = _signed_xml_and_root()
    reparsed = etree.fromstring(xml_bytes)
    inf_dps_id = reparsed.find(_q(NFSE_NS, "infDPS")).get("Id")
    reference = reparsed.find(f".//{_q(DS_NS, 'Reference')}")
    assert reference is not None
    assert reference.get("URI") == f"#{inf_dps_id}"


def test_mutation_still_detected_after_namespace_fix():
    xml_bytes, _, certificate = _signed_xml_and_root()
    mutated = etree.fromstring(xml_bytes)
    value_el = mutated.find(f".//{_q(NFSE_NS, 'vServ')}")
    value_el.text = "999999.00"
    import pytest
    from app.services.nfse_national.signer import SignatureError
    with pytest.raises(SignatureError):
        verify_dps_signature(mutated, certificate.certificate_pem)


# ============================================================ 6 — algorithms unchanged


def test_algorithms_unchanged():
    xml_bytes, _, _ = _signed_xml_and_root()
    reparsed = etree.fromstring(xml_bytes)
    signed_info = reparsed.find(f".//{_q(DS_NS, 'SignedInfo')}")
    c14n = signed_info.find(_q(DS_NS, "CanonicalizationMethod")).get("Algorithm")
    sig_method = signed_info.find(_q(DS_NS, "SignatureMethod")).get("Algorithm")
    digest_method = signed_info.find(f".//{_q(DS_NS, 'DigestMethod')}").get("Algorithm")
    transforms = [
        t.get("Algorithm")
        for t in signed_info.findall(f".//{_q(DS_NS, 'Transform')}")
    ]
    assert c14n == "http://www.w3.org/2001/10/xml-exc-c14n#"
    assert sig_method == "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
    assert digest_method == "http://www.w3.org/2001/04/xmlenc#sha256"
    assert "http://www.w3.org/2000/09/xmldsig#enveloped-signature" in transforms


# ============================================================ 7 — XSD validation


def test_xsd_validation_still_passes():
    xml_bytes, _, _ = _signed_xml_and_root()
    validate_dps_xml(xml_bytes)  # raises on any violation


# ============================================================ 8 — M38 wire round-trip


def test_wire_roundtrip_reproduces_exact_signed_bytes():
    xml_bytes, _, _ = _signed_xml_and_root()
    encoded = encode_xml_gzip_b64(xml_bytes)
    decoded = decode_b64_gzip_xml(encoded)
    assert decoded == xml_bytes
    # Cross-check with the stdlib directly too, independent of the app's
    # own helper functions.
    manual = gzip.decompress(base64.b64decode(encoded))
    assert manual == xml_bytes


# ============================================================ 9 — M41/M43 business fields intact


def test_m41_m43_business_fields_survive_the_namespace_fix():
    xml_bytes, _, _ = _signed_xml_and_root()
    reparsed = etree.fromstring(xml_bytes)
    inf = reparsed.find(_q(NFSE_NS, "infDPS"))
    prest = inf.find(_q(NFSE_NS, "prest"))
    reg_trib = prest.find(_q(NFSE_NS, "regTrib"))
    trib = inf.find(_q(NFSE_NS, "valores")).find(_q(NFSE_NS, "trib"))
    trib_fed = trib.find(_q(NFSE_NS, "tribFed"))

    assert inf.find(_q(NFSE_NS, "tpEmit")).text == "1"
    assert prest.find(_q(NFSE_NS, "xNome")) is None  # E0121
    assert reg_trib.find(_q(NFSE_NS, "opSimpNac")).text == "3"
    assert reg_trib.find(_q(NFSE_NS, "regApTribSN")).text == "1"  # E0166
    assert reg_trib.find(_q(NFSE_NS, "regEspTrib")).text == "0"
    tot_trib = trib.find(_q(NFSE_NS, "totTrib"))
    assert tot_trib.find(_q(NFSE_NS, "indTotTrib")) is None  # E0712
    assert tot_trib.find(_q(NFSE_NS, "pTotTribSN")) is not None
    piscofins = trib_fed.find(_q(NFSE_NS, "piscofins")) if trib_fed is not None else None
    assert piscofins is not None
    assert piscofins.find(_q(NFSE_NS, "CST")).text == "00"
    assert piscofins.find(_q(NFSE_NS, "tpRetPisCofins")).text == "0"
    assert [c.tag for c in trib] == [
        _q(NFSE_NS, "tribMun"), _q(NFSE_NS, "tribFed"), _q(NFSE_NS, "totTrib"),
    ]


# ============================================================ 10 — dedicated E1228 regression


def test_e1228_regression_zero_namespace_prefixes_in_signed_dps():
    """The exact SEFIN rule this milestone fixes: 'Uso de prefixo de
    namespace não permitido na área de dados descompactada.' A signed DPS
    produced by our signer must contain ZERO namespace prefixes, full
    stop — checked both by raw byte markers and by a structural parse."""
    xml_bytes, signed, _ = _signed_xml_and_root()
    for forbidden in (b"<ds:", b"xmlns:ds=", b"ns0:", b"ns1:", b'xmlns:"'):
        assert forbidden not in xml_bytes
    reparsed = etree.fromstring(xml_bytes)
    namespace_prefix_count = len(_all_prefixes(reparsed))
    assert namespace_prefix_count == 0
    # And on the raw pre-serialization object too (M45's in-memory fix).
    assert len(_all_prefixes(signed)) == 0
