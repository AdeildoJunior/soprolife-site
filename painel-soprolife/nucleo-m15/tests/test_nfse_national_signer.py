"""M27 — assinatura XMLDSig sobre a DPS: certificado sintético apenas."""
import pytest
from lxml import etree

from app.services.nfse_national.dps_builder import build_dps_element, serialize_dps
from app.services.nfse_national.signer import (
    SignatureError,
    generate_synthetic_test_certificate,
    load_pkcs12_certificate,
    sign_dps,
    verify_dps_signature,
)
from app.services.nfse_national.xsd_validation import validate_dps_xml
from tests.test_nfse_national_dps_builder import synthetic_dps_input


@pytest.fixture
def certificate():
    p12_bytes, password = generate_synthetic_test_certificate()
    return load_pkcs12_certificate(p12_bytes, password)


def test_generated_certificate_is_explicitly_synthetic():
    p12_bytes, password = generate_synthetic_test_certificate()
    loaded = load_pkcs12_certificate(p12_bytes, password)
    assert b"Synthetic Test" in loaded.certificate_pem or "Synthetic" in str(loaded.certificate.subject)


def test_wrong_password_rejected():
    p12_bytes, _ = generate_synthetic_test_certificate()
    with pytest.raises(SignatureError):
        load_pkcs12_certificate(p12_bytes, "definitely-wrong")


def test_wrong_password_error_never_contains_password():
    p12_bytes, _ = generate_synthetic_test_certificate()
    secret_guess = "definitely-wrong-password-marker"
    try:
        load_pkcs12_certificate(p12_bytes, secret_guess)
    except SignatureError as exc:
        assert secret_guess not in str(exc)


def test_sign_produces_schema_valid_signed_dps(certificate):
    root = build_dps_element(synthetic_dps_input())
    signed = sign_dps(root, certificate)
    xml = serialize_dps(signed)
    validate_dps_xml(xml)
    assert signed.find(".//{http://www.w3.org/2000/09/xmldsig#}Signature") is not None


def test_verify_succeeds_on_untouched_signature(certificate):
    root = build_dps_element(synthetic_dps_input())
    signed = sign_dps(root, certificate)
    verify_dps_signature(signed, certificate.certificate_pem)  # must not raise


def test_mutation_after_signing_fails_verification(certificate):
    root = build_dps_element(synthetic_dps_input())
    signed = sign_dps(root, certificate)
    xml = serialize_dps(signed)
    mutated = etree.fromstring(xml)
    value_el = mutated.find(".//{http://www.sped.fazenda.gov.br/nfse}vServ")
    value_el.text = "999999.00"
    with pytest.raises(SignatureError):
        verify_dps_signature(mutated, certificate.certificate_pem)


def test_verify_fails_with_a_different_certificate(certificate):
    root = build_dps_element(synthetic_dps_input())
    signed = sign_dps(root, certificate)
    other_p12, other_password = generate_synthetic_test_certificate(common_name="Other Synthetic Test")
    other = load_pkcs12_certificate(other_p12, other_password)
    with pytest.raises(SignatureError):
        verify_dps_signature(signed, other.certificate_pem)


def test_signing_without_infdps_fails_closed(certificate):
    empty_root = etree.Element("{http://www.sped.fazenda.gov.br/nfse}DPS")
    with pytest.raises(SignatureError):
        sign_dps(empty_root, certificate)


def test_bogus_pkcs12_bytes_rejected():
    with pytest.raises(SignatureError):
        load_pkcs12_certificate(b"not a pkcs12 file at all", "whatever")
