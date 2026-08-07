"""Autoridade de teste e CMS sintético para exercitar o fluxo PAdES.

NADA aqui é ICP-Brasil. A cadeia é gerada na hora, existe só dentro do
processo de teste e nunca toca disco. Serve para provar que a montagem, a
injeção e a validação funcionam de ponta a ponta sem depender de credencial
da Valid — e para produzir, de propósito, assinaturas ERRADAS que a
validação precisa recusar.
"""

from __future__ import annotations

import datetime as _dt

from asn1crypto import algos, cms, core, x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID


class SigningAuthorityFake:
    """Par de chaves + certificado autoassinado, gerados em memória."""

    def __init__(self, common_name: str = "TESTE APAGAR Assinante Sintetico"):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AC DE TESTE SOPROLIFE"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ])
        now = _dt.datetime.now(_dt.timezone.utc)
        self.certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(self.key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - _dt.timedelta(minutes=5))
            .not_valid_after(now + _dt.timedelta(days=1))
            .sign(self.key, hashes.SHA256())
        )

    @property
    def certificate_der(self) -> bytes:
        return self.certificate.public_bytes(serialization.Encoding.DER)

    def build_cms(
        self,
        digest_hex: str,
        *,
        corrupt_signature: bool = False,
        omit_message_digest: bool = False,
        omit_certificate: bool = False,
        two_signers: bool = False,
    ) -> bytes:
        """Monta um SignedData destacado sobre `digest_hex`.

        Os interruptores existem para os testes negativos: cada um produz um
        CMS estruturalmente plausível mas inválido de um jeito diferente.
        """

        digest = bytes.fromhex(digest_hex)
        attrs = [
            cms.CMSAttribute({
                "type": cms.CMSAttributeType("content_type"),
                "values": [cms.ContentType("data")],
            }),
            cms.CMSAttribute({
                "type": cms.CMSAttributeType("signing_time"),
                "values": [
                    cms.Time({"utc_time": _dt.datetime.now(_dt.timezone.utc)})
                ],
            }),
        ]
        if not omit_message_digest:
            attrs.append(cms.CMSAttribute({
                "type": cms.CMSAttributeType("message_digest"),
                "values": [core.OctetString(digest)],
            }))
        signed_attrs = cms.CMSAttributes(attrs)

        payload = signed_attrs.dump()
        payload = b"\x31" + payload[1:]
        signature = self.key.sign(payload, padding.PKCS1v15(), hashes.SHA256())
        if corrupt_signature:
            signature = bytes([signature[0] ^ 0xFF]) + signature[1:]

        signer_info = cms.SignerInfo({
            "version": "v1",
            "sid": cms.SignerIdentifier({
                "issuer_and_serial_number": cms.IssuerAndSerialNumber({
                    "issuer": asn1_x509.Certificate.load(
                        self.certificate_der
                    ).issuer,
                    "serial_number": self.certificate.serial_number,
                })
            }),
            "digest_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}),
            "signed_attrs": signed_attrs,
            "signature_algorithm": algos.SignedDigestAlgorithm(
                {"algorithm": "rsassa_pkcs1v15"}
            ),
            "signature": signature,
        })

        signer_infos = [signer_info, signer_info] if two_signers else [signer_info]
        certificates = (
            [] if omit_certificate
            else [asn1_x509.Certificate.load(self.certificate_der)]
        )
        signed_data = cms.SignedData({
            "version": "v1",
            "digest_algorithms": [algos.DigestAlgorithm({"algorithm": "sha256"})],
            # Assinatura DESTACADA: o SignedData declara o tipo do conteúdo
            # mas não o carrega. O conteúdo é o próprio PDF, que nunca sai
            # daqui — só o digest dele viaja.
            "encap_content_info": cms.ContentInfo({"content_type": "data"}),
            "certificates": certificates,
            "signer_infos": signer_infos,
        })
        return cms.ContentInfo({
            "content_type": "signed_data", "content": signed_data
        }).dump()
