"""XMLDSig signature boundary for the DPS (``TCDPS`` allows an optional
``ds:Signature`` sibling of ``infDPS``, referencing its ``Id`` attribute).

Certificate/key handling rules (non-negotiable):
- PKCS#12 bytes and password come only from external configuration/paths —
  never from a request body, a policy row or a repository file;
- the password is never logged, never included in an exception message, and
  never returned from any function in this module;
- only ephemeral, explicitly-generated test certificates may be used in
  tests — this module has no notion of "the real SoproLife certificate".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml import XMLSigner, XMLVerifier
from signxml.exceptions import InvalidSignature

NFSE_NS = "http://www.sped.fazenda.gov.br/nfse"
DS_NS = "http://www.w3.org/2000/09/xmldsig#"


class SignatureError(ValueError):
    """Never carries key material, password or raw certificate bytes."""


@dataclass(frozen=True)
class LoadedCertificate:
    """Result of opening a PKCS#12 file. Holds live key/cert objects only in
    memory for the caller's own signing call — nothing here is serializable
    back to text, and ``__repr__``/``__str__`` are not overridden to leak it."""
    private_key: object
    certificate: x509.Certificate
    certificate_pem: bytes


def load_pkcs12_certificate(pkcs12_bytes: bytes, password: str) -> LoadedCertificate:
    """Open a PKCS#12 bundle. Fails closed on any structural problem.

    The password argument is used once and discarded; callers must not retain
    it longer than this call.
    """
    try:
        key, cert, _chain = pkcs12.load_key_and_certificates(pkcs12_bytes, password.encode("utf-8"))
    except Exception:
        # Never include the exception's own text in a way that could echo
        # password-derived material; cryptography's errors here are generic
        # ("Invalid password or PKCS12 data") and safe to keep, but we still
        # normalize to our own message to avoid depending on that wording.
        raise SignatureError("Não foi possível abrir o arquivo PKCS#12 fornecido.") from None
    if key is None or cert is None:
        raise SignatureError("PKCS#12 não contém par chave privada/certificado.")
    return LoadedCertificate(
        private_key=key,
        certificate=cert,
        certificate_pem=cert.public_bytes(serialization.Encoding.PEM),
    )


def generate_synthetic_test_certificate(*, common_name: str = "SoproLife M27 Synthetic Test") -> tuple[bytes, str]:
    """Build a throwaway self-signed cert/key, PKCS#12-encoded, for tests only.

    Never used outside the test suite: the common name says "Synthetic Test"
    on purpose, and nothing in this module treats this certificate as
    authoritative for any real fiscal operation.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    password = "synthetic-test-only"
    p12 = pkcs12.serialize_key_and_certificates(
        name=b"soprolife-m27-synthetic",
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password.encode("utf-8")),
    )
    return p12, password


def sign_dps(root: etree._Element, loaded: LoadedCertificate) -> etree._Element:
    """Enveloped XMLDSig signature over ``infDPS``, referenced by its ``Id``.

    Algorithm choice (RSA-SHA256, exclusive C14N) follows ``xmldsig-core-schema.xsd``
    (vendored alongside DPS_v1.01.xsd) and the RSA/SHA-256 signature method the
    manual documents; this is the same algorithm family already used for
    PAdES in ``report_pades.py``, kept consistent across the codebase.
    """
    inf = root.find(f"{{{NFSE_NS}}}infDPS")
    if inf is None:
        raise SignatureError("Documento não contém infDPS para assinar.")
    dps_id = inf.get("Id")
    if not dps_id:
        raise SignatureError("infDPS sem atributo Id: nada para referenciar na assinatura.")
    signer = XMLSigner(
        method=_enveloped_method(),
        signature_algorithm="rsa-sha256",
        digest_algorithm="sha256",
        c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#",
    )
    signed = signer.sign(
        root,
        key=loaded.private_key,
        cert=[loaded.certificate_pem],
        reference_uri=f"#{dps_id}",
    )
    return signed


def verify_dps_signature(signed_root: etree._Element, expected_certificate_pem: bytes) -> None:
    """Raise ``SignatureError`` unless the signature is valid over the
    CURRENT content — any post-signing mutation must fail here."""
    try:
        XMLVerifier().verify(signed_root, x509_cert=expected_certificate_pem)
    except InvalidSignature:
        raise SignatureError("Assinatura XMLDSig inválida ou documento alterado após assinar.") from None
    except Exception:  # signxml raises assorted exception types on malformed input
        raise SignatureError("Falha ao verificar a assinatura XMLDSig.") from None


def _enveloped_method():
    from signxml import methods

    return methods.enveloped
