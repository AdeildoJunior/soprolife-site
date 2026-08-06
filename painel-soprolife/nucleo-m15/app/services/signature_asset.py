"""M25.2 — armazenamento privado do ativo de assinatura manuscrita.

A imagem autorizada da assinatura da médica é um ativo sensível. Ela vive
exclusivamente sob ``M15_REPORTS_STORAGE_DIR`` (raiz privada 0700, fora do
Git) e só é lida no instante em que o PDF do laudo liberado é desenhado.

Proibições que este módulo existe para sustentar:

- nunca versionar a imagem no repositório;
- nunca entregá-la em JavaScript ou em qualquer resposta de API;
- nunca publicá-la em URL previsível ou permanente;
- nunca registrar bytes, nome de arquivo ou caminho absoluto em log;
- nunca colocá-la em fixture, exemplo ou dado de teste.

O sistema permanece FUNCIONAL sem o ativo: quando não houver imagem ativa
cadastrada, o laudo é liberado apenas com o bloco identificador da médica.

Uma imagem de assinatura NÃO é assinatura digital qualificada e este módulo
não faz nenhuma afirmação nesse sentido.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .report_storage import (
    ReportStorageError,
    StoredPdfIntegrityError,
    StoredPdfMissingError,
    assert_safe_storage_id,
    read_private_file_bytes,
)

# Somente PNG: formato sem perdas, com canal alfa, e um único decodificador
# a auditar. JPEG/SVG são recusados (SVG carregaria conteúdo ativo).
ALLOWED_MIME = "image/png"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

MAX_SIGNATURE_BYTES = 2 * 1024 * 1024
MIN_DIMENSION = 40
MAX_DIMENSION = 4000
# Sanidade de formato, NÃO controle de segurança: só barra arquivo
# grosseiramente errado (uma captura de tela inteira, um banner). Quem
# protege este ativo de verdade é o RBAC do cadastro e a conferência visual
# do admin, não esta razão.
#
# O piso era 0.8, escrito sob a premissa de que "assinatura é um traço largo
# e baixo". A primeira assinatura autorizada real derrubou a premissa: é um
# floreio vertical, medido em 0.42. Assinar com monograma ou rubrica alta é
# comum, então o piso desceu para caber nesse caso sem deixar de recusar
# proporções absurdas.
MIN_ASPECT_RATIO = 0.25
MAX_ASPECT_RATIO = 12.0


class SignatureAssetError(ValueError):
    """Erro de ativo de assinatura com `codigo` estável para a resposta."""

    def __init__(self, codigo: str, mensagem: str):
        self.codigo = codigo
        self.mensagem = mensagem
        super().__init__(mensagem)


@dataclass(frozen=True)
class ValidatedSignatureImage:
    data: bytes
    sha256: str
    size_bytes: int
    width: int
    height: int
    mime_type: str = ALLOWED_MIME


def validate_signature_png(
    data: bytes, *, max_size_bytes: int = MAX_SIGNATURE_BYTES
) -> ValidatedSignatureImage:
    """Valida tipo, tamanho, estrutura e integridade da imagem enviada."""

    if not data:
        raise SignatureAssetError(
            "assinatura_vazia", "O arquivo de assinatura está vazio."
        )
    if len(data) > max_size_bytes:
        raise SignatureAssetError(
            "assinatura_muito_grande",
            "O arquivo de assinatura excede o tamanho permitido.",
        )
    if not data.startswith(_PNG_MAGIC):
        raise SignatureAssetError(
            "assinatura_formato_invalido",
            "A assinatura precisa ser um arquivo PNG.",
        )

    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format != "PNG":
                raise SignatureAssetError(
                    "assinatura_formato_invalido",
                    "A assinatura precisa ser um arquivo PNG.",
                )
            width, height = probe.size
            # `verify()` invalida a instância; a decodificação completa é
            # refeita a seguir para provar que os dados são realmente
            # decodificáveis, e não só estruturalmente plausíveis.
            probe.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except SignatureAssetError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, MemoryError) as exc:
        raise SignatureAssetError(
            "assinatura_corrompida",
            "O arquivo de assinatura está corrompido ou não é um PNG válido.",
        ) from exc

    if not (MIN_DIMENSION <= width <= MAX_DIMENSION) or not (
        MIN_DIMENSION <= height <= MAX_DIMENSION
    ):
        raise SignatureAssetError(
            "assinatura_dimensao_invalida",
            "As dimensões da imagem de assinatura estão fora do permitido.",
        )
    ratio = width / height
    if not (MIN_ASPECT_RATIO <= ratio <= MAX_ASPECT_RATIO):
        raise SignatureAssetError(
            "assinatura_proporcao_invalida",
            "A proporção da imagem não corresponde a uma assinatura.",
        )

    return ValidatedSignatureImage(
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        width=width,
        height=height,
    )


def signature_asset_storage_path(
    root: Path, *, physician_profile_id: str, asset_id: str
) -> Path:
    """Caminho interno composto só por UUIDs — nunca o nome enviado."""

    assert_safe_storage_id(
        physician_profile_id, label="physician_profile_id"
    )
    assert_safe_storage_id(asset_id, label="asset_id")
    return root / "assinaturas" / physician_profile_id / f"{asset_id}.png"


def read_and_validate_signature_asset(
    path: Path,
    *,
    root: Path,
    expected_sha256: str,
    expected_size_bytes: int,
    expected_width: int,
    expected_height: int,
) -> ValidatedSignatureImage:
    """Releitura fail-closed antes de desenhar a assinatura no PDF.

    Substituição do arquivo em disco, corrupção ou divergência de hash,
    tamanho ou dimensões interrompem a liberação — nunca são aceitas
    silenciosamente nem "corrigidas".
    """

    data = read_private_file_bytes(path, root=root)
    validated = validate_signature_png(data)
    if (
        validated.sha256 != expected_sha256
        or validated.size_bytes != expected_size_bytes
        or validated.width != expected_width
        or validated.height != expected_height
    ):
        raise StoredPdfIntegrityError(
            "assinatura_armazenada_divergente",
            "O ativo de assinatura armazenado diverge dos metadados.",
        )
    return validated


__all__ = [
    "ALLOWED_MIME",
    "MAX_SIGNATURE_BYTES",
    "ReportStorageError",
    "SignatureAssetError",
    "StoredPdfIntegrityError",
    "StoredPdfMissingError",
    "ValidatedSignatureImage",
    "read_and_validate_signature_asset",
    "signature_asset_storage_path",
    "validate_signature_png",
]
