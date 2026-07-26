"""Validação de PDF hostil enviado pelo usuário — nunca confia no cliente.

Ordem das checagens, fail-closed (a primeira falha recusa o upload):
tamanho -> tipo declarado -> assinatura mágica -> heurística de polyglot ->
parse estrutural (pypdf, strict) -> criptografado -> contagem de páginas ->
sha256. Nenhuma checagem depende do nome de arquivo enviado.
"""

import hashlib
import io
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PdfReadError

PDF_MAGIC = b"%PDF-"
MIN_PAGES = 1
MAX_PAGES = 300
ACCEPTED_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}

# Assinaturas de outros formatos que não podem coexistir com um PDF
# monoformato legítimo — defesa heurística contra arquivo polyglot
# (ex.: PDF concatenado com um ZIP, técnica comum de evasão de scanner).
_ZIP_LOCAL_HEADER = b"PK\x03\x04"
_ZIP_EOCD = b"PK\x05\x06"


class InvalidPdfError(ValueError):
    """Erro de validação com um `codigo` estável para a resposta 422."""

    def __init__(self, codigo: str, mensagem: str):
        self.codigo = codigo
        self.mensagem = mensagem
        super().__init__(mensagem)


@dataclass(frozen=True)
class ValidatedPdf:
    sha256: str
    size_bytes: int
    page_count: int


def validate_pdf_bytes(
    data: bytes,
    *,
    max_size_bytes: int,
    declared_content_type: str | None = None,
    max_pages: int = MAX_PAGES,
) -> ValidatedPdf:
    if not data:
        raise InvalidPdfError("pdf_vazio", "Arquivo vazio.")
    if len(data) > max_size_bytes:
        raise InvalidPdfError(
            "pdf_excede_tamanho_maximo",
            f"Arquivo excede o limite de {max_size_bytes} bytes.",
        )
    if declared_content_type and declared_content_type.split(";")[0].strip().lower() not in (
        ACCEPTED_CONTENT_TYPES
    ):
        raise InvalidPdfError(
            "tipo_mime_invalido", "Tipo de conteúdo declarado não é PDF."
        )
    if not data.startswith(PDF_MAGIC):
        raise InvalidPdfError(
            "assinatura_invalida", "Arquivo não começa com a assinatura %PDF-."
        )
    if _ZIP_LOCAL_HEADER in data or _ZIP_EOCD in data:
        raise InvalidPdfError(
            "arquivo_polyglot_suspeito",
            "Arquivo contém assinatura de outro formato (possível polyglot).",
        )

    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
    except (PdfReadError, ValueError, OSError, KeyError, IndexError) as exc:
        raise InvalidPdfError("pdf_malformado", "PDF malformado ou corrompido.") from exc

    if reader.is_encrypted:
        raise InvalidPdfError("pdf_criptografado", "PDF criptografado não é aceito.")

    try:
        page_count = len(reader.pages)
        for page in reader.pages:
            _ = page.mediabox  # força o parse estrutural de cada página
    except (PdfReadError, ValueError, OSError, KeyError, IndexError) as exc:
        raise InvalidPdfError("pdf_malformado", "PDF malformado ou corrompido.") from exc

    if page_count < MIN_PAGES:
        raise InvalidPdfError("pdf_sem_paginas", "PDF sem páginas.")
    if page_count > max_pages:
        raise InvalidPdfError(
            "pdf_excede_paginas_maximas",
            f"PDF excede o limite de {max_pages} páginas.",
        )

    return ValidatedPdf(
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        page_count=page_count,
    )
