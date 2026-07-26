"""Composição server-side do laudo — overlay sobre o PDF original (M24A).

Nunca modifica o PDF original em disco: lê os bytes, gera um overlay com
reportlab (bloco de interpretação + rodapé institucional), mescla com pypdf
e devolve bytes novos — sempre gravados como uma NOVA versão pelo chamador
(app/routers/reports.py), nunca sobrescrevendo o arquivo de origem.

Não inventa texto de interpretação médica: o texto vem do registro de
templates (app/models.py ReportTemplate), que nasce vazio/administrável.
Este módulo só desenha o texto que já foi selecionado.
"""

import io
import textwrap
from dataclasses import dataclass
from typing import Literal

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from .pdf_validation import ValidatedPdf, validate_pdf_bytes

Placement = Literal["topo", "rodape"]
PLACEMENTS: tuple[Placement, ...] = ("topo", "rodape")

# Rodapé institucional — texto PROVISÓRIO/placeholder. A redação jurídica
# final do rodapé oficial da SoproLife é uma decisão de produto/jurídica em
# aberto (M24A, item 17 do pedido) e NUNCA deve ser tratada como definitiva
# nem como declaração de assinatura digital válida.
DEFAULT_FOOTER_TEXT = (
    "SoproLife - Relatorio tecnico gerado eletronicamente. "
    "Assinatura digital pendente. "
    "[RODAPE OFICIAL PENDENTE DE DEFINICAO JURIDICA - M24A]"
)

_MARGIN = 36.0  # pontos (~1.27cm)
_FOOTER_FONT_SIZE = 7
_BODY_FONT_SIZE = 9
_LINE_HEIGHT = 12


class PdfCompositionError(ValueError):
    """Erro de composição com um `codigo` estável para a resposta 422/500."""

    def __init__(self, codigo: str, mensagem: str):
        self.codigo = codigo
        self.mensagem = mensagem
        super().__init__(mensagem)


@dataclass(frozen=True)
class ComposedPdf:
    data: bytes
    validated: ValidatedPdf


def _wrap(text: str, max_chars: int) -> list[str]:
    if not text:
        return []
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        wrapped = textwrap.wrap(paragraph, width=max_chars) or [""]
        lines.extend(wrapped)
    return lines


def _build_overlay_page(
    *,
    page_width: float,
    page_height: float,
    footer_text: str,
    interpretation_text: str | None,
    placement: Placement,
) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width, page_height))

    footer_lines = _wrap(footer_text, 130)
    c.setFont("Helvetica", _FOOTER_FONT_SIZE)
    y = _MARGIN - (len(footer_lines) - 1) * 9
    for line in footer_lines:
        c.drawString(_MARGIN, y, line)
        y += 9
    footer_top = _MARGIN + len(footer_lines) * 9 + 6

    if interpretation_text:
        body_lines = _wrap(interpretation_text, 100)
        block_height = len(body_lines) * _LINE_HEIGHT + 16
        max_width = page_width - 2 * _MARGIN
        if placement == "topo":
            box_top = page_height - _MARGIN
        else:  # "rodape": logo acima da faixa do rodapé institucional
            box_top = footer_top + block_height
        box_bottom = box_top - block_height
        if box_bottom < footer_top - 2 and placement == "rodape":
            # bloco não coube sem sobrepor o rodapé — sobe para o topo em vez
            # de desenhar texto por cima do rodapé institucional
            box_top = page_height - _MARGIN
            box_bottom = box_top - block_height

        c.setLineWidth(0.5)
        c.rect(_MARGIN - 4, box_bottom, max_width + 8, block_height, stroke=1, fill=0)
        c.setFont("Helvetica", _BODY_FONT_SIZE)
        text_y = box_top - _LINE_HEIGHT
        for line in body_lines:
            c.drawString(_MARGIN, text_y, line)
            text_y -= _LINE_HEIGHT

    c.showPage()
    c.save()
    return buf.getvalue()


def compose_report_pdf(
    *,
    original_bytes: bytes,
    page_number: int,
    placement: Placement,
    interpretation_text: str | None,
    footer_text: str = DEFAULT_FOOTER_TEXT,
    max_size_bytes: int,
) -> ComposedPdf:
    """Gera uma NOVA versão do PDF com o rodapé institucional em toda página
    e o bloco de interpretação na página/posição escolhida. Não muta
    `original_bytes` nem qualquer arquivo em disco — só devolve bytes.
    """
    if placement not in PLACEMENTS:
        raise PdfCompositionError("posicao_invalida", f"Posição '{placement}' desconhecida.")

    try:
        reader = PdfReader(io.BytesIO(original_bytes), strict=True)
    except Exception as exc:  # pragma: no cover - já validado antes do upload
        raise PdfCompositionError("pdf_malformado", "PDF original malformado.") from exc
    if reader.is_encrypted:
        raise PdfCompositionError("pdf_criptografado", "PDF original criptografado.")

    total_pages = len(reader.pages)
    if not (1 <= page_number <= total_pages):
        raise PdfCompositionError(
            "pagina_invalida", f"Página {page_number} fora do intervalo (1..{total_pages})."
        )

    # clone_from anexa as páginas ao writer ANTES do merge — evita o aviso
    # de depreciação do pypdf sobre merge_page em página ainda não anexada
    # (removido em pypdf 7.0) e evita qualquer mutação do `reader` original.
    writer = PdfWriter(clone_from=reader)
    for index, page in enumerate(writer.pages, start=1):
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        overlay_bytes = _build_overlay_page(
            page_width=width,
            page_height=height,
            footer_text=footer_text,
            interpretation_text=interpretation_text if index == page_number else None,
            placement=placement,
        )
        overlay_page = PdfReader(io.BytesIO(overlay_bytes)).pages[0]
        page.merge_page(overlay_page)

    out = io.BytesIO()
    writer.write(out)
    composed_bytes = out.getvalue()

    # Verificação pós-geração: o PDF que vamos gravar precisa ser válido de
    # verdade (reparse estrutural completo), não só "não lançou exceção" ao
    # escrever. Se a contagem de páginas não bater, algo corrompeu a
    # composição e o resultado nunca é armazenado.
    validated = validate_pdf_bytes(composed_bytes, max_size_bytes=max_size_bytes)
    if validated.page_count != total_pages:
        raise PdfCompositionError(
            "verificacao_pos_composicao_falhou",
            "Contagem de páginas do PDF gerado não corresponde ao original.",
        )
    return ComposedPdf(data=composed_bytes, validated=validated)
