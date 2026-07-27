"""Composição server-side do laudo — overlay sobre o PDF original (M24A).

Nunca modifica o PDF original em disco: lê os bytes, gera um overlay com
reportlab (bloco de interpretação + rodapé institucional), mescla com pypdf
e devolve bytes novos — sempre gravados como uma NOVA versão pelo chamador
(app/routers/reports.py), nunca sobrescrevendo o arquivo de origem.

Não inventa texto de interpretação médica: o texto vem do registro de
templates (app/models.py ReportTemplate), que nasce vazio/administrável.
Este módulo só desenha o texto que já foi selecionado.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Literal

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from .pdf_geometry import EffectivePageBox, PdfPageGeometryError, effective_page_box
from .pdf_validation import InvalidPdfError, ValidatedPdf, validate_pdf_bytes

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

_FONT = "Helvetica"
_MARGIN = 36.0  # pontos (~1.27cm)
_FOOTER_FONT_SIZE = 7.0
_FOOTER_LINE_HEIGHT = 9.0
_FOOTER_BODY_GAP = 8.0
_BODY_FONT_SIZE = 9.0
_BODY_LINE_HEIGHT = 12.0
_BODY_PADDING_TOP = 8.0
_BODY_PADDING_BOTTOM = 8.0
_COORDINATE_TOLERANCE = 0.25
_INTERPRETATION_MARKER = "/SoproLifeM24AInterpretation"
_FOOTER_MARKER = "/SoproLifeM24BFooter"


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


@dataclass(frozen=True)
class _RenderedLine:
    x: float
    baseline: float
    width: float
    font_size: float


@dataclass(frozen=True)
class _OverlayGeometry:
    interpretation_lines: tuple[_RenderedLine, ...]
    footer_lines: tuple[_RenderedLine, ...]
    footer_top: float


def _font_ascent(font_size: float) -> float:
    return float(pdfmetrics.getAscent(_FONT, font_size))


def _font_descent(font_size: float) -> float:
    return float(pdfmetrics.getDescent(_FONT, font_size))


def _wrap_to_width(text: str, *, font_size: float, max_width: float) -> list[str]:
    """Wrap by rendered width while preserving every non-newline character."""

    if max_width <= 0:
        raise PdfCompositionError(
            "pagina_sem_area_util",
            "A página não possui área útil suficiente para composição.",
        )
    if not text:
        return []

    rendered_lines: list[str] = []
    paragraphs = text.splitlines()
    if text.endswith(("\n", "\r")):
        paragraphs.append("")

    for paragraph in paragraphs:
        if not paragraph:
            rendered_lines.append("")
            continue
        current = ""
        for character in paragraph:
            candidate = current + character
            if pdfmetrics.stringWidth(candidate, _FONT, font_size) <= max_width:
                current = candidate
                continue
            if not current or pdfmetrics.stringWidth(
                character, _FONT, font_size
            ) > max_width:
                raise PdfCompositionError(
                    "pagina_sem_area_util",
                    "A página não possui largura útil suficiente para o conteúdo institucional.",
                )
            rendered_lines.append(current)
            current = character
        rendered_lines.append(current)
    return rendered_lines


def _validate_page_dimensions(page_width: float, page_height: float) -> None:
    if (
        not math.isfinite(page_width)
        or not math.isfinite(page_height)
        or page_width <= 0
        or page_height <= 0
    ):
        raise PdfCompositionError(
            "pagina_sem_area_util",
            "A página não possui dimensões válidas para composição.",
        )


def _build_overlay_page(
    *,
    page_width: float,
    page_height: float,
    footer_text: str,
    interpretation_text: str | None,
    placement: Placement,
) -> tuple[bytes, _OverlayGeometry]:
    _validate_page_dimensions(page_width, page_height)
    available_width = page_width - 2 * _MARGIN
    footer_lines = _wrap_to_width(
        footer_text,
        font_size=_FOOTER_FONT_SIZE,
        max_width=available_width,
    ) or [""]

    footer_ascent = _font_ascent(_FOOTER_FONT_SIZE)
    footer_descent = _font_descent(_FOOTER_FONT_SIZE)
    first_footer_baseline = _MARGIN - footer_descent
    last_footer_baseline = (
        first_footer_baseline + (len(footer_lines) - 1) * _FOOTER_LINE_HEIGHT
    )
    footer_top = last_footer_baseline + footer_ascent + _FOOTER_BODY_GAP
    usable_top = page_height - _MARGIN
    if footer_top > usable_top + _COORDINATE_TOLERANCE:
        raise PdfCompositionError(
            "pagina_sem_area_util",
            "A página não possui área útil suficiente acima do rodapé institucional.",
        )

    body_lines: list[str] = []
    baselines: tuple[float, ...] = ()
    rendered_interpretation_lines: tuple[_RenderedLine, ...] = ()
    box_top: float | None = None
    box_bottom: float | None = None
    if interpretation_text:
        body_lines = _wrap_to_width(
            interpretation_text,
            font_size=_BODY_FONT_SIZE,
            max_width=available_width,
        )
        body_ascent = _font_ascent(_BODY_FONT_SIZE)
        body_descent = _font_descent(_BODY_FONT_SIZE)

        if placement == "topo":
            first_body_baseline = usable_top - _BODY_PADDING_TOP - body_ascent
            calculated = tuple(
                first_body_baseline - line_index * _BODY_LINE_HEIGHT
                for line_index in range(len(body_lines))
            )
            last_body_baseline = calculated[-1]
            box_top = usable_top
            box_bottom = last_body_baseline + body_descent - _BODY_PADDING_BOTTOM
        else:
            last_body_baseline = footer_top + _BODY_PADDING_BOTTOM - body_descent
            first_body_baseline = (
                last_body_baseline + (len(body_lines) - 1) * _BODY_LINE_HEIGHT
            )
            calculated = tuple(
                first_body_baseline - line_index * _BODY_LINE_HEIGHT
                for line_index in range(len(body_lines))
            )
            box_bottom = footer_top
            box_top = first_body_baseline + body_ascent + _BODY_PADDING_TOP

        if (
            box_bottom < footer_top - _COORDINATE_TOLERANCE
            or box_top > usable_top + _COORDINATE_TOLERANCE
        ):
            raise PdfCompositionError(
                "interpretacao_nao_cabe_na_pagina",
                "O texto clínico não cabe integralmente na área útil da página.",
            )
        baselines = calculated
        rendered_interpretation_lines = tuple(
            _RenderedLine(
                x=_MARGIN,
                baseline=baseline,
                width=float(pdfmetrics.stringWidth(line, _FONT, _BODY_FONT_SIZE)),
                font_size=_BODY_FONT_SIZE,
            )
            for line, baseline in zip(body_lines, baselines, strict=True)
        )

    buf = io.BytesIO()
    pdf_canvas = canvas.Canvas(buf, pagesize=(page_width, page_height))
    pdf_canvas.setFont(_FONT, _FOOTER_FONT_SIZE)
    footer_y = first_footer_baseline
    pdf_canvas.addLiteral(f"{_FOOTER_MARKER} BMC")
    for line in footer_lines:
        pdf_canvas.drawString(_MARGIN, footer_y, line)
        footer_y += _FOOTER_LINE_HEIGHT
    pdf_canvas.addLiteral("EMC")

    if body_lines and box_top is not None and box_bottom is not None:
        pdf_canvas.setLineWidth(0.5)
        pdf_canvas.rect(
            _MARGIN - 4,
            box_bottom,
            available_width + 8,
            box_top - box_bottom,
            stroke=1,
            fill=0,
        )
        pdf_canvas.setFont(_FONT, _BODY_FONT_SIZE)
        pdf_canvas.addLiteral(f"{_INTERPRETATION_MARKER} BMC")
        for line, baseline in zip(body_lines, baselines, strict=True):
            pdf_canvas.drawString(_MARGIN, baseline, line)
        pdf_canvas.addLiteral("EMC")

    pdf_canvas.showPage()
    pdf_canvas.save()
    return buf.getvalue(), _OverlayGeometry(
        interpretation_lines=rendered_interpretation_lines,
        footer_lines=tuple(
            _RenderedLine(
                x=_MARGIN,
                baseline=first_footer_baseline
                + line_index * _FOOTER_LINE_HEIGHT,
                width=float(
                    pdfmetrics.stringWidth(line, _FONT, _FOOTER_FONT_SIZE)
                ),
                font_size=_FOOTER_FONT_SIZE,
            )
            for line_index, line in enumerate(footer_lines)
        ),
        footer_top=footer_top,
    )


def _multiply_matrix(
    left: tuple[float, float, float, float, float, float],
    right: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
    la, lb, lc, ld, le, lf = left
    ra, rb, rc, rd, re, rf = right
    return (
        la * ra + lc * rb,
        lb * ra + ld * rb,
        la * rc + lc * rd,
        lb * rc + ld * rd,
        la * re + lc * rf + le,
        lb * re + ld * rf + lf,
    )


def _transformed_point(
    matrix: tuple[float, float, float, float, float, float],
    x: float,
    y: float,
) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return a * x + c * y + e, b * x + d * y + f


def _rendered_marked_baselines(
    page: object,
    reader: PdfReader,
    *,
    marker: str,
) -> list[tuple[float, float]]:
    """Extract real coordinates of marked text operators from the final PDF."""

    stream = ContentStream(page.get_contents(), reader)  # type: ignore[attr-defined]
    identity = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    ctm = identity
    graphics_stack: list[tuple[float, float, float, float, float, float]] = []
    text_matrix = identity
    text_leading = 0.0
    marked_stack: list[bool] = []
    marked_depth = 0
    coordinates: list[tuple[float, float]] = []

    for operands, operator in stream.operations:
        if operator == b"q":
            graphics_stack.append(ctm)
        elif operator == b"Q":
            if not graphics_stack:
                raise PdfCompositionError(
                    "verificacao_pos_composicao_falhou",
                    "Estado gráfico inválido no PDF composto.",
                )
            ctm = graphics_stack.pop()
        elif operator == b"cm" and len(operands) == 6:
            incoming = tuple(float(value) for value in operands)
            ctm = _multiply_matrix(ctm, incoming)  # type: ignore[arg-type]
        elif operator == b"BT":
            text_matrix = identity
            text_leading = 0.0
        elif operator == b"Tm" and len(operands) == 6:
            text_matrix = tuple(float(value) for value in operands)  # type: ignore[assignment]
        elif operator in {b"Td", b"TD"} and len(operands) == 2:
            tx, ty = (float(value) for value in operands)
            text_matrix = _multiply_matrix(text_matrix, (1.0, 0.0, 0.0, 1.0, tx, ty))
            if operator == b"TD":
                text_leading = -ty
        elif operator == b"TL" and operands:
            text_leading = float(operands[0])
        elif operator == b"T*":
            text_matrix = _multiply_matrix(
                text_matrix,
                (1.0, 0.0, 0.0, 1.0, 0.0, -text_leading),
            )
        elif operator in {b"BMC", b"BDC"} and operands:
            is_target = str(operands[0]) == marker
            marked_stack.append(is_target)
            if is_target:
                marked_depth += 1
        elif operator == b"EMC":
            if marked_stack:
                if marked_stack.pop():
                    marked_depth -= 1
        elif operator in {b"Tj", b"TJ", b"'", b'"'} and marked_depth:
            if operator in {b"'", b'"'}:
                text_matrix = _multiply_matrix(
                    text_matrix,
                    (1.0, 0.0, 0.0, 1.0, 0.0, -text_leading),
                )
            coordinates.append(_transformed_point(ctm, text_matrix[4], text_matrix[5]))

    if marked_depth or graphics_stack:
        raise PdfCompositionError(
            "verificacao_pos_composicao_falhou",
            "Estrutura gráfica incompleta no PDF composto.",
        )
    return coordinates


def _rendered_interpretation_baselines(
    page: object, reader: PdfReader
) -> list[tuple[float, float]]:
    """Compatibility helper used by the geometry regression tests."""

    return _rendered_marked_baselines(
        page,
        reader,
        marker=_INTERPRETATION_MARKER,
    )


def _same_effective_box(left: EffectivePageBox, right: EffectivePageBox) -> bool:
    return left.rotation == right.rotation and all(
        abs(first - second) <= _COORDINATE_TOLERANCE
        for first, second in zip(
            (left.left, left.bottom, left.right, left.top),
            (right.left, right.bottom, right.right, right.top),
            strict=True,
        )
    )


def _verify_rendered_lines(
    *,
    label: str,
    actual: list[tuple[float, float]],
    expected: tuple[_RenderedLine, ...],
    box: EffectivePageBox,
) -> None:
    if len(actual) != len(expected):
        raise PdfCompositionError(
            "verificacao_pos_composicao_falhou",
            f"Nem todas as linhas de {label} foram renderizadas no PDF composto.",
        )

    for (actual_user_x, actual_user_y), expected_line in zip(
        actual, expected, strict=True
    ):
        actual_x, actual_y = box.user_to_visible(actual_user_x, actual_user_y)
        descent = _font_descent(expected_line.font_size)
        ascent = _font_ascent(expected_line.font_size)
        if (
            abs(actual_x - expected_line.x) > _COORDINATE_TOLERANCE
            or abs(actual_y - expected_line.baseline) > _COORDINATE_TOLERANCE
            or actual_x < -_COORDINATE_TOLERANCE
            or actual_x + expected_line.width
            > box.visible_width + _COORDINATE_TOLERANCE
            or actual_y + descent < -_COORDINATE_TOLERANCE
            or actual_y + ascent
            > box.visible_height + _COORDINATE_TOLERANCE
        ):
            raise PdfCompositionError(
                "verificacao_pos_composicao_falhou",
                f"Uma linha de {label} ficou fora da área visível do PDF composto.",
            )


def _verify_page_coordinates(
    *,
    page: object,
    reader: PdfReader,
    geometry: _OverlayGeometry,
    original_box: EffectivePageBox,
) -> None:
    try:
        composed_box = effective_page_box(page)
    except PdfPageGeometryError as exc:
        raise PdfCompositionError(exc.codigo, exc.mensagem) from exc
    if not _same_effective_box(original_box, composed_box):
        raise PdfCompositionError(
            "verificacao_pos_composicao_falhou",
            "As caixas visíveis da página mudaram durante a composição.",
        )

    _verify_rendered_lines(
        label="rodapé",
        actual=_rendered_marked_baselines(page, reader, marker=_FOOTER_MARKER),
        expected=geometry.footer_lines,
        box=composed_box,
    )
    _verify_rendered_lines(
        label="interpretação",
        actual=_rendered_interpretation_baselines(page, reader),
        expected=geometry.interpretation_lines,
        box=composed_box,
    )

    for line in geometry.interpretation_lines:
        if (
            line.baseline < geometry.footer_top - _COORDINATE_TOLERANCE
            or line.baseline
            > composed_box.visible_height - _MARGIN + _COORDINATE_TOLERANCE
        ):
            raise PdfCompositionError(
                "verificacao_pos_composicao_falhou",
                "Uma linha clínica ficou fora da área útil do PDF composto.",
            )


def compose_report_pdf(
    *,
    original_bytes: bytes,
    page_number: int,
    placement: Placement,
    interpretation_text: str | None,
    footer_text: str = DEFAULT_FOOTER_TEXT,
    max_size_bytes: int,
) -> ComposedPdf:
    """Gera uma nova versão, recusando qualquer linha fora da área útil."""

    if placement not in PLACEMENTS:
        raise PdfCompositionError("posicao_invalida", f"Posição '{placement}' desconhecida.")

    try:
        original_validated = validate_pdf_bytes(
            original_bytes,
            max_size_bytes=max_size_bytes,
        )
    except InvalidPdfError as exc:
        raise PdfCompositionError(exc.codigo, exc.mensagem) from exc

    reader = PdfReader(io.BytesIO(original_bytes), strict=True)
    total_pages = len(reader.pages)
    if total_pages != original_validated.page_count:
        raise PdfCompositionError(
            "pdf_malformado",
            "A estrutura do PDF original mudou durante a composição.",
        )
    if not (1 <= page_number <= total_pages):
        raise PdfCompositionError(
            "pagina_invalida", f"Página {page_number} fora do intervalo (1..{total_pages})."
        )

    writer = PdfWriter(clone_from=reader)
    page_geometries: list[tuple[EffectivePageBox, _OverlayGeometry]] = []
    for index, page in enumerate(writer.pages, start=1):
        try:
            effective_box = effective_page_box(page)
        except PdfPageGeometryError as exc:
            raise PdfCompositionError(exc.codigo, exc.mensagem) from exc
        overlay_bytes, geometry = _build_overlay_page(
            page_width=effective_box.visible_width,
            page_height=effective_box.visible_height,
            footer_text=footer_text,
            interpretation_text=interpretation_text if index == page_number else None,
            placement=placement,
        )
        overlay_page = PdfReader(io.BytesIO(overlay_bytes), strict=True).pages[0]
        page.merge_transformed_page(
            overlay_page,
            effective_box.overlay_to_user_matrix,
            over=True,
            expand=False,
        )
        page_geometries.append((effective_box, geometry))

    out = io.BytesIO()
    writer.write(out)
    composed_bytes = out.getvalue()

    try:
        validated = validate_pdf_bytes(composed_bytes, max_size_bytes=max_size_bytes)
    except InvalidPdfError as exc:
        raise PdfCompositionError(exc.codigo, exc.mensagem) from exc
    if validated.page_count != total_pages:
        raise PdfCompositionError(
            "verificacao_pos_composicao_falhou",
            "Contagem de páginas do PDF gerado não corresponde ao original.",
        )

    verification_reader = PdfReader(io.BytesIO(composed_bytes), strict=True)
    for page, (original_box, geometry) in zip(
        verification_reader.pages,
        page_geometries,
        strict=True,
    ):
        _verify_page_coordinates(
            page=page,
            reader=verification_reader,
            geometry=geometry,
            original_box=original_box,
        )
    return ComposedPdf(data=composed_bytes, validated=validated)
