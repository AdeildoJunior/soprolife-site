"""M25.7 — preparação, injeção e validação de assinatura PAdES no laudo.

Este módulo NÃO assina nada: ele prepara o PDF para receber uma assinatura
feita por terceiro (o HSM da AC, via IntegraICP/VIDaaS), injeta o CMS que
volta de lá e valida o resultado. A chave privada da médica nunca passa por
aqui — nem existe no servidor.

Divisão de responsabilidade, na ordem em que acontece:

1. ``prepare_pades`` recebe o PDF do laudo já congelado e devolve um PDF
   PREPARADO: mesmo conteúdo visual, com um dicionário de assinatura cujo
   ``/Contents`` é um espaço reservado vazio e cujo ``/ByteRange`` cobre todo
   o arquivo EXCETO esse espaço. Devolve também o SHA-256 exatamente do que o
   ByteRange cobre — o único dado que sai daqui para a AC.
2. ``inject_cms`` grava o CMS devolvido pela AC dentro do espaço reservado,
   sem mexer em mais nenhum byte. O ByteRange continua válido por
   construção: os bytes cobertos não mudaram.
3. ``validate_pades`` refaz o cálculo de fora para dentro e confere
   criptograficamente. É ele que decide se o laudo pode ser liberado.

Por que não incremental update: um laudo recém-gerado não tem assinatura
anterior a preservar. A atualização incremental existe para não invalidar
assinaturas já presentes; na PRIMEIRA assinatura, reescrever o arquivo com o
espaço reservado é igualmente válido em PAdES e muito menos frágil.

Honestidade de nível PAdES: este módulo produz **PAdES-B-B** (assinatura
básica). Ele NÃO adiciona carimbo do tempo (RFC 3161) nem informações de
revogação, portanto NÃO produz PAdES-T, -LT nem -LTA, e nunca declara esses
níveis. ``PADES_LEVEL_ACHIEVED`` é o valor gravado no banco — mudar o nível
exige implementar o que o nível exige, não editar a constante.
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from datetime import datetime

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    ByteStringObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

# Nível efetivamente produzido. Só suba isto junto com a implementação do
# que o nível exige — carimbo do tempo para -T, revogação embutida para -LT.
PADES_LEVEL_ACHIEVED = "PAdES-B-B"

# Espaço reservado para o CMS. Um CMS com cadeia ICP-Brasil completa fica
# tipicamente entre 4 e 12 KB; 32 KB dá folga sem inchar o laudo.
DEFAULT_PLACEHOLDER_BYTES = 32768

# Largura fixa dos números do ByteRange. O array é escrito com números
# preenchidos e depois corrigido no lugar: se o comprimento mudasse, todos os
# deslocamentos seguintes mudariam junto e o ByteRange passaria a mentir.
_BYTERANGE_DIGITS = 10
# Forma EXATA como o pypdf serializa um ArrayObject de números — com espaço
# depois de "[" e antes de "]". Um marcador escrito "à mão" sem esses
# espaços não é encontrado no arquivo gerado.
_BYTERANGE_PLACEHOLDER = b"[ 0 9999999999 9999999999 9999999999 ]"

_SUBFILTER = "/ETSI.CAdES.detached"


class PadesError(ValueError):
    """Falha de preparação, injeção ou validação, com código estável."""

    def __init__(self, codigo: str, mensagem: str):
        self.codigo = codigo
        self.mensagem = mensagem
        super().__init__(mensagem)


@dataclass(frozen=True)
class PreparedPdf:
    """PDF pronto para receber o CMS.

    `signed_digest_sha256` é o hash do conteúdo REALMENTE assinável (o que o
    ByteRange cobre) — não é o hash do arquivo. Os dois nunca coincidem, e
    confundi-los é o erro clássico deste fluxo.
    """

    data: bytes
    signed_digest_sha256: str
    byte_range: tuple[int, int, int, int]
    contents_offset: int
    placeholder_bytes: int

    @property
    def prepared_sha256(self) -> str:
        """Hash do ARQUIVO preparado — serve de âncora de idempotência."""

        return hashlib.sha256(self.data).hexdigest()


@dataclass(frozen=True)
class PadesValidation:
    """Resultado da conferência do PDF assinado."""

    signed_digest_sha256: str
    final_sha256: str
    signer_subject: str
    signer_issuer: str
    signer_serial: str
    not_valid_before: datetime
    not_valid_after: datetime
    level: str = PADES_LEVEL_ACHIEVED


def _covered_bytes(data: bytes, byte_range: tuple[int, int, int, int]) -> bytes:
    start1, len1, start2, len2 = byte_range
    return data[start1 : start1 + len1] + data[start2 : start2 + len2]


def prepare_pades(
    pdf: bytes,
    *,
    reason: str,
    location: str,
    contact: str | None = None,
    placeholder_bytes: int = DEFAULT_PLACEHOLDER_BYTES,
) -> PreparedPdf:
    """Insere o campo de assinatura vazio e calcula o digest assinável.

    Nenhum dado de paciente entra no dicionário de assinatura: `reason` e
    `location` são institucionais e conferidos por quem chama.
    """

    if not pdf.startswith(b"%PDF-"):
        raise PadesError("pdf_invalido", "O arquivo a preparar não é um PDF.")
    if placeholder_bytes < 2048:
        raise PadesError(
            "placeholder_pequeno",
            "O espaço reservado para a assinatura é pequeno demais.",
        )

    try:
        reader = PdfReader(io.BytesIO(pdf))
        writer = PdfWriter(clone_from=reader)
    except Exception as exc:  # pypdf levanta tipos variados
        raise PadesError(
            "pdf_ilegivel", "O PDF do laudo não pôde ser lido para assinatura."
        ) from exc

    if not writer.pages:
        raise PadesError("pdf_sem_paginas", "O PDF do laudo não tem páginas.")

    signature = DictionaryObject()
    signature[NameObject("/Type")] = NameObject("/Sig")
    signature[NameObject("/Filter")] = NameObject("/Adobe.PPKLite")
    signature[NameObject("/SubFilter")] = NameObject(_SUBFILTER)
    signature[NameObject("/Reason")] = TextStringObject(reason)
    signature[NameObject("/Location")] = TextStringObject(location)
    if contact:
        signature[NameObject("/ContactInfo")] = TextStringObject(contact)
    # Espaço reservado: zeros que serão substituídos pelo CMS. Escrito como
    # string de bytes para o pypdf serializar em hexadecimal de tamanho
    # previsível (2 caracteres por byte, entre < e >).
    signature[NameObject("/Contents")] = ByteStringObject(
        b"\x00" * placeholder_bytes
    )
    signature[NameObject("/ByteRange")] = ArrayObject(
        [NumberObject(0), NumberObject(9999999999),
         NumberObject(9999999999), NumberObject(9999999999)]
    )
    signature_ref = writer._add_object(signature)

    # Campo de assinatura invisível: o laudo já traz a identificação visual
    # da médica e o selo. Um segundo carimbo desenhado por cima seria ruído,
    # e a área de assinatura é EXCLUSIVA por regra do projeto.
    field = DictionaryObject()
    field[NameObject("/Type")] = NameObject("/Annot")
    field[NameObject("/Subtype")] = NameObject("/Widget")
    field[NameObject("/FT")] = NameObject("/Sig")
    field[NameObject("/T")] = TextStringObject("SoproLifeAssinaturaICP")
    field[NameObject("/V")] = signature_ref
    field[NameObject("/F")] = NumberObject(132)  # oculto na impressão
    field[NameObject("/Rect")] = ArrayObject(
        [FloatObject(0), FloatObject(0), FloatObject(0), FloatObject(0)]
    )
    page_ref = writer.pages[0].indirect_reference
    if page_ref is not None:
        field[NameObject("/P")] = page_ref
    field_ref = writer._add_object(field)

    page = writer.pages[0]
    annots = page.get(NameObject("/Annots"))
    if isinstance(annots, ArrayObject):
        annots.append(field_ref)
    else:
        page[NameObject("/Annots")] = ArrayObject([field_ref])

    acroform = DictionaryObject()
    acroform[NameObject("/Fields")] = ArrayObject([field_ref])
    # SigFlags 3 = documento contém assinatura e o campo é somente-anexar.
    acroform[NameObject("/SigFlags")] = NumberObject(3)
    writer._root_object[NameObject("/AcroForm")] = writer._add_object(acroform)

    buffer = io.BytesIO()
    writer.write(buffer)
    data = bytearray(buffer.getvalue())

    contents_start, contents_end = _locate_contents(bytes(data), placeholder_bytes)
    byte_range = (
        0,
        contents_start,
        contents_end,
        len(data) - contents_end,
    )
    _patch_byte_range(data, byte_range)

    final = bytes(data)
    digest = hashlib.sha256(_covered_bytes(final, byte_range)).hexdigest()
    return PreparedPdf(
        data=final,
        signed_digest_sha256=digest,
        byte_range=byte_range,
        contents_offset=contents_start,
        placeholder_bytes=placeholder_bytes,
    )


def _locate_contents(data: bytes, placeholder_bytes: int) -> tuple[int, int]:
    """Delimita o espaço reservado, incluindo os delimitadores < e >.

    O ByteRange precisa excluir os delimitadores junto com o conteúdo: é
    assim que todo verificador de PDF interpreta o intervalo.
    """

    expected = b"<" + b"0" * (placeholder_bytes * 2) + b">"
    index = data.find(expected)
    if index < 0:
        raise PadesError(
            "espaco_de_assinatura_nao_encontrado",
            "O espaço reservado da assinatura não foi localizado no PDF.",
        )
    if data.find(expected, index + 1) >= 0:
        raise PadesError(
            "espaco_de_assinatura_ambiguo",
            "Mais de um espaço de assinatura foi encontrado no PDF.",
        )
    return index, index + len(expected)


def _patch_byte_range(data: bytearray, byte_range: tuple[int, int, int, int]) -> None:
    """Escreve o ByteRange real sem alterar o comprimento do array."""

    index = bytes(data).find(_BYTERANGE_PLACEHOLDER)
    if index < 0:
        raise PadesError(
            "byte_range_nao_encontrado",
            "O marcador de ByteRange não foi localizado no PDF preparado.",
        )
    numbers = " ".join(str(value) for value in byte_range)
    rendered = ("[ " + numbers + " ]").encode("ascii")
    if len(rendered) > len(_BYTERANGE_PLACEHOLDER):
        raise PadesError(
            "byte_range_longo_demais",
            "O ByteRange calculado não cabe no espaço reservado.",
        )
    # Preenche com espaços à direita, DENTRO do array: o PDF aceita espaço
    # sobrando entre o último número e o colchete, e o comprimento total
    # precisa ficar idêntico — se mudasse, todos os deslocamentos seguintes
    # mudariam e o ByteRange passaria a apontar para o lugar errado.
    padding_needed = len(_BYTERANGE_PLACEHOLDER) - len(rendered)
    rendered = rendered[:-1] + b" " * padding_needed + b"]"
    data[index : index + len(_BYTERANGE_PLACEHOLDER)] = rendered


def inject_cms(prepared: PreparedPdf, cms_der: bytes) -> bytes:
    """Grava o CMS no espaço reservado, sem tocar em nenhum outro byte."""

    if not cms_der:
        raise PadesError("cms_vazio", "A autoridade não devolveu assinatura.")
    if len(cms_der) > prepared.placeholder_bytes:
        raise PadesError(
            "cms_maior_que_o_espaco",
            "A assinatura devolvida não cabe no espaço reservado do PDF.",
        )
    data = bytearray(prepared.data)
    start = prepared.contents_offset
    hexed = cms_der.hex().encode("ascii")
    # +1 pula o "<"; o resto do espaço continua zerado, como manda a prática
    # para campos de assinatura de tamanho fixo.
    data[start + 1 : start + 1 + len(hexed)] = hexed

    final = bytes(data)
    if hashlib.sha256(
        _covered_bytes(final, prepared.byte_range)
    ).hexdigest() != prepared.signed_digest_sha256:
        # Não deveria acontecer: seria um erro de programação na injeção.
        raise PadesError(
            "injecao_alterou_conteudo_assinado",
            "A injeção da assinatura alterou bytes cobertos pelo ByteRange.",
        )
    return final


def read_signature_fields(pdf: bytes) -> tuple[tuple[int, int, int, int], bytes]:
    """Extrai ByteRange e CMS de um PDF já assinado, lendo os bytes crus.

    Deliberadamente não usa o parser de objetos: a validação precisa
    enxergar o arquivo como um verificador externo o enxerga, e não como a
    nossa própria biblioteca gostaria que ele fosse.
    """

    match = re.search(
        rb"/ByteRange\s*\[\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*\]", pdf
    )
    if match is None:
        raise PadesError(
            "byte_range_ausente", "O PDF assinado não declara ByteRange."
        )
    byte_range = (
        int(match.group(1)), int(match.group(2)),
        int(match.group(3)), int(match.group(4)),
    )
    start1, len1, start2, len2 = byte_range
    if start1 != 0 or len1 <= 0 or start2 <= len1 or len2 < 0:
        raise PadesError(
            "byte_range_incoerente", "O ByteRange do PDF assinado é inválido."
        )
    if start2 + len2 != len(pdf):
        raise PadesError(
            "byte_range_nao_cobre_o_arquivo",
            "O ByteRange não cobre o arquivo inteiro fora da assinatura.",
        )
    # O CMS mora exatamente na lacuna entre os dois trechos cobertos.
    gap = pdf[len1:start2]
    if not gap.startswith(b"<") or not gap.endswith(b">"):
        raise PadesError(
            "assinatura_mal_delimitada",
            "O campo de assinatura do PDF não está delimitado corretamente.",
        )
    try:
        raw = bytes.fromhex(gap[1:-1].decode("ascii"))
    except ValueError as exc:
        raise PadesError(
            "assinatura_nao_hexadecimal",
            "O conteúdo do campo de assinatura não é hexadecimal válido.",
        ) from exc
    return byte_range, _trim_der(raw)


def _trim_der(raw: bytes) -> bytes:
    """Corta o preenchimento lendo o COMPRIMENTO declarado no próprio DER.

    O espaço reservado é maior que o CMS e o resto fica zerado. A tentação é
    remover os zeros à direita — e é errado: quando a assinatura RSA termina
    em 0x00, `rstrip` come um byte real e o DER deixa de ser decodificável.
    Isso falha de forma ALEATÓRIA, em uma assinatura a cada poucas centenas.

    Ler o comprimento do cabeçalho ASN.1 corta exatamente onde deve.
    """

    if len(raw) < 2:
        raise PadesError(
            "cms_truncado", "O campo de assinatura do PDF está vazio."
        )
    primeiro = raw[1]
    if primeiro < 0x80:
        total = 2 + primeiro
    else:
        bytes_de_comprimento = primeiro & 0x7F
        if bytes_de_comprimento == 0 or len(raw) < 2 + bytes_de_comprimento:
            raise PadesError(
                "cms_truncado",
                "O comprimento declarado na assinatura é inválido.",
            )
        total = (
            2
            + bytes_de_comprimento
            + int.from_bytes(raw[2 : 2 + bytes_de_comprimento], "big")
        )
    if total > len(raw):
        raise PadesError(
            "cms_truncado",
            "A assinatura declara mais bytes do que os presentes no PDF.",
        )
    return raw[:total]


def validate_pades(
    pdf: bytes, *, expected_signed_digest_sha256: str | None = None
) -> PadesValidation:
    """Confere o PDF assinado de fora para dentro. Fail-closed.

    Confere, nesta ordem: o ByteRange cobre o arquivo; o digest do conteúdo
    coberto bate com o que foi enviado à AC; o CMS é um SignedData legível;
    o atributo assinado `messageDigest` bate com esse mesmo digest; e a
    assinatura confere contra a chave pública do certificado do signatário.

    Qualquer falha levanta `PadesError` — nunca devolve um resultado parcial,
    porque um resultado parcial viraria "assinado" na tela.
    """

    byte_range, cms_der = read_signature_fields(pdf)
    digest = hashlib.sha256(_covered_bytes(pdf, byte_range)).hexdigest()
    if (
        expected_signed_digest_sha256
        and digest != expected_signed_digest_sha256
    ):
        raise PadesError(
            "digest_divergente",
            "O conteúdo assinado não corresponde ao digest enviado para "
            "assinatura.",
        )

    signer = _verify_cms(cms_der, digest)
    return PadesValidation(
        signed_digest_sha256=digest,
        final_sha256=hashlib.sha256(pdf).hexdigest(),
        signer_subject=signer["subject"],
        signer_issuer=signer["issuer"],
        signer_serial=signer["serial"],
        not_valid_before=signer["not_before"],
        not_valid_after=signer["not_after"],
    )


def _verify_cms(cms_der: bytes, expected_digest_hex: str) -> dict:
    """Valida o SignedData contra o digest esperado.

    Importa asn1crypto/cryptography aqui dentro: um ambiente sem a
    integração qualificada não precisa carregar nem instalar nada disso para
    emitir laudos com liberação institucional.
    """

    try:
        from asn1crypto import cms as asn1_cms
        from cryptography.hazmat.primitives import hashes as crypto_hashes
        from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
        from cryptography.x509 import load_der_x509_certificate
    except ImportError as exc:  # pragma: no cover - ambiente sem extras
        raise PadesError(
            "validacao_indisponivel",
            "As bibliotecas de validação criptográfica não estão instaladas.",
        ) from exc

    # O asn1crypto decodifica de forma PREGUIÇOSA: `load` aceita quase
    # qualquer coisa e o erro só aparece ao tocar no conteúdo. Por isso a
    # leitura do tipo e do conteúdo precisa ficar DENTRO do try — deixá-la
    # fora fazia um CMS inválido escapar como exceção não tratada.
    try:
        info = asn1_cms.ContentInfo.load(cms_der)
        content_type = info["content_type"].native
        signed = info["content"] if content_type == "signed_data" else None
        if signed is not None:
            # Força a materialização: sem isto, um corpo corrompido só
            # estouraria mais adiante, longe deste tratamento.
            len(signed["signer_infos"])
    except Exception as exc:
        raise PadesError(
            "cms_ilegivel", "A assinatura devolvida não é um CMS válido."
        ) from exc
    if content_type != "signed_data":
        raise PadesError(
            "cms_nao_e_signed_data",
            "A assinatura devolvida não é do tipo SignedData.",
        )

    signers = signed["signer_infos"]
    if len(signers) != 1:
        raise PadesError(
            "cms_signatarios_inesperados",
            "A assinatura precisa ter exatamente um signatário.",
        )
    signer_info = signers[0]

    signed_attrs = signer_info["signed_attrs"]
    if signed_attrs is None or len(signed_attrs) == 0:
        raise PadesError(
            "cms_sem_atributos_assinados",
            "A assinatura não traz atributos assinados.",
        )
    message_digest = None
    for attr in signed_attrs:
        if attr["type"].native == "message_digest":
            message_digest = attr["values"][0].native
            break
    if message_digest is None:
        raise PadesError(
            "cms_sem_message_digest",
            "A assinatura não declara o digest do conteúdo.",
        )
    if message_digest.hex() != expected_digest_hex:
        raise PadesError(
            "digest_assinado_divergente",
            "O digest dentro da assinatura não corresponde ao conteúdo do "
            "PDF.",
        )

    certificates = signed["certificates"]
    if certificates is None or len(certificates) == 0:
        raise PadesError(
            "cms_sem_certificado",
            "A assinatura não traz o certificado do signatário.",
        )
    try:
        cert = load_der_x509_certificate(certificates[0].chosen.dump())
    except Exception as exc:
        raise PadesError(
            "certificado_ilegivel",
            "O certificado do signatário não pôde ser lido.",
        ) from exc

    # A assinatura CMS cobre a forma DER SET OF dos atributos assinados, não
    # o conteúdo do documento — confundir os dois é o erro clássico aqui.
    payload = signed_attrs.dump()
    if payload[:1] != b"\x31":
        payload = b"\x31" + payload[1:]
    signature = signer_info["signature"].native
    public_key = cert.public_key()
    try:
        if isinstance(public_key, rsa.RSAPublicKey):
            public_key.verify(
                signature, payload, padding.PKCS1v15(), crypto_hashes.SHA256()
            )
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(
                signature, payload, ec.ECDSA(crypto_hashes.SHA256())
            )
        else:
            raise PadesError(
                "algoritmo_nao_suportado",
                "O algoritmo do certificado do signatário não é suportado.",
            )
    except PadesError:
        raise
    except Exception as exc:
        raise PadesError(
            "assinatura_nao_confere",
            "A assinatura não confere com o certificado do signatário.",
        ) from exc

    return {
        "subject": cert.subject.rfc4514_string(),
        "issuer": cert.issuer.rfc4514_string(),
        "serial": format(cert.serial_number, "x"),
        "not_before": cert.not_valid_before_utc,
        "not_after": cert.not_valid_after_utc,
    }


def rebuild_prepared(data: bytes) -> PreparedPdf:
    """Reconstrói o descritor a partir dos bytes preparados já gravados.

    O fluxo qualificado é interrompido: o objeto criado por `prepare_pades`
    morre com a requisição que iniciou a assinatura. Na volta do callback só
    existem os bytes — e tudo que o descritor guarda pode ser relido deles,
    sem recalcular nada e sem regerar o PDF (regerar mudaria os bytes e
    invalidaria o digest já enviado à autoridade).
    """

    byte_range, _cms = read_signature_fields(data)
    start1, len1, start2, _len2 = byte_range
    return PreparedPdf(
        data=data,
        signed_digest_sha256=hashlib.sha256(
            _covered_bytes(data, byte_range)
        ).hexdigest(),
        byte_range=byte_range,
        contents_offset=len1,
        placeholder_bytes=(start2 - len1 - 2) // 2,
    )
