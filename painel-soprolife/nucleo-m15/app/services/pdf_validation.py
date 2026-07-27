"""Validação estrutural e de conteúdo de PDFs hostis."""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass
from urllib.parse import urlsplit

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

from .pdf_geometry import PdfPageGeometryError, effective_page_box

PDF_MAGIC = b"%PDF-"
MIN_PAGES = 1
MAX_PAGES = 300
ACCEPTED_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}
_MAX_GRAPH_DEPTH = 200
_MAX_GRAPH_OBJECTS = 100_000

# Deliberately empty in production.  A future milestone may populate a closed,
# reviewed origin allowlist through a separate policy/configuration contract.
# Parsing remains purely local: validation must never resolve DNS or perform an
# outbound request.
APPROVED_EXTERNAL_URI_ORIGINS: frozenset[str] = frozenset()

_FORBIDDEN_KEYS = {
    "/OpenAction": "pdf_conteudo_ativo_openaction",
    "/AA": "pdf_conteudo_ativo_aa",
    "/JavaScript": "pdf_conteudo_ativo_javascript",
    "/JS": "pdf_conteudo_ativo_javascript",
    "/EmbeddedFiles": "pdf_conteudo_embutido",
    "/EF": "pdf_conteudo_embutido",
    "/Collection": "pdf_conteudo_embutido",
    "/AcroForm": "pdf_conteudo_ativo_formulario",
    "/XFA": "pdf_conteudo_ativo_formulario",
    "/RichMedia": "pdf_conteudo_ativo_richmedia",
    "/RichMediaContent": "pdf_conteudo_ativo_richmedia",
}
_FORBIDDEN_ACTION_TYPES = {
    "/JavaScript": "pdf_conteudo_ativo_javascript",
    "/Launch": "pdf_conteudo_ativo_launch",
    "/RichMediaExecute": "pdf_conteudo_ativo_richmedia",
    "/Rendition": "pdf_conteudo_ativo_richmedia",
    "/Sound": "pdf_conteudo_ativo_multimidia",
    "/Movie": "pdf_conteudo_ativo_multimidia",
    "/GoToR": "pdf_conteudo_ativo_externo",
    "/GoToE": "pdf_conteudo_ativo_externo",
    "/SubmitForm": "pdf_conteudo_ativo_formulario",
    "/ImportData": "pdf_conteudo_ativo_formulario",
}
_FORBIDDEN_ANNOTATION_SUBTYPES = {
    "/RichMedia": "pdf_conteudo_ativo_richmedia",
    "/FileAttachment": "pdf_conteudo_embutido",
    "/Movie": "pdf_conteudo_ativo_multimidia",
    "/Sound": "pdf_conteudo_ativo_multimidia",
    "/Screen": "pdf_conteudo_ativo_multimidia",
    "/3D": "pdf_conteudo_ativo_multimidia",
    "/Widget": "pdf_conteudo_ativo_formulario",
}


class InvalidPdfError(ValueError):
    """Erro de validação com código de domínio estável."""

    def __init__(self, codigo: str, mensagem: str):
        self.codigo = codigo
        self.mensagem = mensagem
        super().__init__(mensagem)


@dataclass(frozen=True)
class ValidatedPdf:
    sha256: str
    size_bytes: int
    page_count: int


def _has_coherent_zip_structure(data: bytes) -> bool:
    """Exige um ZIP parseável; quatro bytes isolados nunca bastam.

    `zipfile` valida EOCD, diretório central e offsets. Não extraímos nem
    descomprimimos entradas, evitando transformar a heurística em zip-bomb.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data), mode="r") as archive:
            infos = archive.infolist()
            if len(infos) > 10_000:
                return True
            for info in infos:
                offset = int(info.header_offset)
                if offset < 0 or data[offset : offset + 4] != b"PK\x03\x04":
                    return False
            # Um ZIP vazio ainda é uma estrutura ZIP coerente (EOCD válido).
            return True
    except (zipfile.BadZipFile, ValueError, OSError, OverflowError):
        return False


def _resolved_scalar(value):
    if isinstance(value, IndirectObject):
        return value.get_object()
    return value


def _uri_origin(value) -> str | None:
    value = _resolved_scalar(value)
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value.strip())
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    default_port = 443 if scheme == "https" else 80
    effective_port = port or default_port
    return f"{scheme}://{parsed.hostname.lower()}:{effective_port}"


def _uri_action_is_approved(value) -> bool:
    """Local-only future extension point for a deliberately closed allowlist."""

    origin = _uri_origin(value)
    return origin is not None and origin in APPROVED_EXTERNAL_URI_ORIGINS


def _validate_no_active_content(reader: PdfReader) -> None:
    """Percorre o grafo PDF com detecção de ciclos, sem ler streams binários."""
    seen_indirect: set[tuple[int, int]] = set()
    seen_direct: set[int] = set()
    visited = 0

    def walk(value, *, automatic_action: bool = False, depth: int = 0) -> None:
        nonlocal visited
        if depth > _MAX_GRAPH_DEPTH:
            raise InvalidPdfError(
                "pdf_grafo_excessivo",
                "PDF possui grafo de objetos excessivamente profundo.",
            )
        if isinstance(value, IndirectObject):
            key = (value.idnum, value.generation)
            if key in seen_indirect:
                return
            seen_indirect.add(key)
            try:
                value = value.get_object()
            except (PdfReadError, ValueError, KeyError, IndexError, TypeError) as exc:
                raise InvalidPdfError(
                    "pdf_malformado", "PDF malformado ou corrompido."
                ) from exc

        if not isinstance(value, (DictionaryObject, ArrayObject, dict, list, tuple)):
            return
        direct_id = id(value)
        if direct_id in seen_direct:
            return
        seen_direct.add(direct_id)
        visited += 1
        if visited > _MAX_GRAPH_OBJECTS:
            raise InvalidPdfError(
                "pdf_grafo_excessivo",
                "PDF possui objetos demais para validação segura.",
            )

        if isinstance(value, (DictionaryObject, dict)):
            string_keys = {str(key): item for key, item in value.items()}
            for key, codigo in _FORBIDDEN_KEYS.items():
                if key in string_keys:
                    raise InvalidPdfError(
                        codigo,
                        "PDF contém ação automática, conteúdo ativo ou arquivo embutido.",
                    )

            action_type = str(_resolved_scalar(string_keys.get("/S", "")))
            if action_type in _FORBIDDEN_ACTION_TYPES:
                raise InvalidPdfError(
                    _FORBIDDEN_ACTION_TYPES[action_type],
                    "PDF contém ação ativa não permitida.",
                )
            subtype = str(_resolved_scalar(string_keys.get("/Subtype", "")))
            if subtype in _FORBIDDEN_ANNOTATION_SUBTYPES:
                raise InvalidPdfError(
                    _FORBIDDEN_ANNOTATION_SUBTYPES[subtype],
                    "PDF contém mídia ativa, formulário ou anexo não permitido.",
                )
            if action_type == "/URI" and not _uri_action_is_approved(
                string_keys.get("/URI")
            ):
                raise InvalidPdfError(
                    "pdf_uri_externa_nao_permitida",
                    "PDF contém ação de URI externa não permitida.",
                )

            for key, item in value.items():
                key_text = str(key)
                walk(
                    item,
                    automatic_action=automatic_action
                    or key_text in {"/OpenAction", "/AA"},
                    depth=depth + 1,
                )
            return

        for item in value:
            walk(item, automatic_action=automatic_action, depth=depth + 1)

    try:
        walk(reader.trailer)
        for page in reader.pages:
            walk(page)
    except InvalidPdfError:
        raise
    except (PdfReadError, ValueError, OSError, KeyError, IndexError, TypeError) as exc:
        raise InvalidPdfError(
            "pdf_malformado", "PDF malformado ou corrompido."
        ) from exc


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
    if _has_coherent_zip_structure(data):
        raise InvalidPdfError(
            "arquivo_polyglot_suspeito",
            "Arquivo também contém uma estrutura ZIP coerente.",
        )

    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
    except (PdfReadError, ValueError, OSError, KeyError, IndexError) as exc:
        raise InvalidPdfError(
            "pdf_malformado", "PDF malformado ou corrompido."
        ) from exc

    if reader.is_encrypted:
        raise InvalidPdfError(
            "pdf_criptografado", "PDF criptografado não é aceito."
        )

    try:
        page_count = len(reader.pages)
        for page in reader.pages:
            effective_page_box(page)
    except PdfPageGeometryError as exc:
        raise InvalidPdfError(exc.codigo, exc.mensagem) from exc
    except (PdfReadError, ValueError, OSError, KeyError, IndexError, TypeError) as exc:
        raise InvalidPdfError(
            "pdf_malformado", "PDF malformado ou corrompido."
        ) from exc

    if page_count < MIN_PAGES:
        raise InvalidPdfError("pdf_sem_paginas", "PDF sem páginas.")
    if page_count > max_pages:
        raise InvalidPdfError(
            "pdf_excede_paginas_maximas",
            f"PDF excede o limite de {max_pages} páginas.",
        )

    _validate_no_active_content(reader)

    return ValidatedPdf(
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        page_count=page_count,
    )
