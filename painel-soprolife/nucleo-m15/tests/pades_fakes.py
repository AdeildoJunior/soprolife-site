"""Autoridade de teste e CMS sintético para exercitar o fluxo PAdES.

NADA aqui é ICP-Brasil. A cadeia é gerada na hora, existe só dentro do
processo de teste e nunca toca disco. Serve para provar que a montagem, a
injeção e a validação funcionam de ponta a ponta sem depender de credencial
da Valid — e para produzir, de propósito, assinaturas ERRADAS que a
validação precisa recusar.
"""

from __future__ import annotations

import datetime as _dt
import hashlib

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


# ------------------------------- assinatura externa simulada (M25.8)

_PLACEHOLDER_BYTES = 8192


def sign_incrementally(prepared: bytes, authority: SigningAuthorityFake) -> bytes:
    """Assina ANEXANDO ao PDF, como Adobe Reader e VIDaaS fazem de verdade.

    Assinar um PDF não o reescreve: acrescenta uma seção de atualização
    incremental ao final. Por isso o arquivo preparado continua sendo
    PREFIXO EXATO do assinado — propriedade que a reimportação usa para
    provar que o conteúdo clínico não foi adulterado.

    Simular isso reescrevendo o arquivo daria um teste que passa e um fluxo
    real que falha, porque a verificação de prefixo cairia.
    """

    base = prepared if prepared.endswith(b"\n") else prepared + b"\n"
    corpo = (
        b"1000 0 obj\n<< /Type /Sig /Filter /Adobe.PPKLite "
        b"/SubFilter /ETSI.CAdES.detached /ByteRange "
        b"[ 0 9999999999 9999999999 9999999999 ] /Contents <"
        + b"0" * (_PLACEHOLDER_BYTES * 2)
        + b"> >>\nendobj\n"
    )
    montado = bytearray(base + corpo)

    marcador = b"/Contents <"
    inicio = montado.rfind(marcador) + len(marcador) - 1
    fim = inicio + 2 + _PLACEHOLDER_BYTES * 2
    byte_range = (0, inicio, fim, len(montado) - fim)

    alvo = b"[ 0 9999999999 9999999999 9999999999 ]"
    posicao = montado.rfind(alvo)
    novo = ("[ " + " ".join(str(v) for v in byte_range) + " ]").encode("ascii")
    novo = novo[:-1] + b" " * (len(alvo) - len(novo)) + b"]"
    montado[posicao : posicao + len(alvo)] = novo

    coberto = bytes(montado[: byte_range[1]]) + bytes(montado[byte_range[2] :])
    cms = authority.build_cms(hashlib.sha256(coberto).hexdigest())
    hexado = cms.hex().encode("ascii")
    montado[inicio + 1 : inicio + 1 + len(hexado)] = hexado
    return bytes(montado)
