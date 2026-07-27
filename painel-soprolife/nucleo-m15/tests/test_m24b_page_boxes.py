"""Adversarial M24B tests for the effective visible PDF page area."""

import io
import math

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NullObject,
    NumberObject,
    RectangleObject,
)

from app.services.pdf_geometry import (
    PdfPageGeometryError,
    effective_page_box,
)
from app.services.report_pdf import (
    PdfCompositionError,
    _FOOTER_MARKER,
    _rendered_interpretation_baselines,
    _rendered_marked_baselines,
    _wrap_to_width,
    compose_report_pdf,
)

MAX_BYTES = 30 * 1024 * 1024


def _boxed_pdf(
    *,
    media=(0, 0, 595, 842),
    crop=None,
    trim=None,
    rotation=0,
) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    page[NameObject("/MediaBox")] = RectangleObject(media)
    if crop is not None:
        page[NameObject("/CropBox")] = RectangleObject(crop)
    if trim is not None:
        page[NameObject("/TrimBox")] = RectangleObject(trim)
    if rotation:
        page[NameObject("/Rotate")] = NumberObject(rotation)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _compose(source: bytes, *, placement="topo", text="linha um\nlinha dois"):
    original_copy = bytes(source)
    result = compose_report_pdf(
        original_bytes=source,
        page_number=1,
        placement=placement,
        interpretation_text=text,
        max_size_bytes=MAX_BYTES,
    )
    assert source == original_copy
    assert PdfReader(io.BytesIO(result.data), strict=True).get_num_pages() == 1
    return result


@pytest.mark.parametrize("placement", ["topo", "rodape"])
def test_cropbox_menor_que_mediabox_governa_toda_a_composicao(placement):
    result = _compose(
        _boxed_pdf(crop=(80, 120, 515, 720)),
        placement=placement,
    )
    reader = PdfReader(io.BytesIO(result.data), strict=True)
    box = effective_page_box(reader.pages[0])
    points = _rendered_interpretation_baselines(reader.pages[0], reader)
    assert (box.left, box.bottom, box.right, box.top) == (80, 120, 515, 720)
    assert all(
        0 <= x <= box.visible_width and 0 <= y <= box.visible_height
        for x, y in (box.user_to_visible(*point) for point in points)
    )


def test_trimbox_menor_que_cropbox_define_intersecao_efetiva():
    source = _boxed_pdf(
        media=(10, 20, 710, 920),
        crop=(40, 60, 680, 880),
        trim=(120, 180, 620, 800),
    )
    result = _compose(source)
    page = PdfReader(io.BytesIO(result.data), strict=True).pages[0]
    box = effective_page_box(page)
    assert (box.left, box.bottom, box.right, box.top) == (120, 180, 620, 800)
    assert tuple(float(value) for value in page.mediabox) == (10, 20, 710, 920)
    assert tuple(float(value) for value in page.cropbox) == (40, 60, 680, 880)
    assert tuple(float(value) for value in page.trimbox) == (120, 180, 620, 800)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("placement", ["topo", "rodape"])
def test_origem_nao_zero_e_rotacoes_preservam_area_visivel(rotation, placement):
    result = _compose(
        _boxed_pdf(
            media=(50, 100, 750, 1000),
            crop=(100, 150, 700, 950),
            trim=(120, 180, 680, 920),
            rotation=rotation,
        ),
        placement=placement,
    )
    reader = PdfReader(io.BytesIO(result.data), strict=True)
    page = reader.pages[0]
    box = effective_page_box(page)
    assert box.rotation == rotation
    assert (box.left, box.bottom, box.right, box.top) == (120, 180, 680, 920)
    assert len(_rendered_marked_baselines(page, reader, marker=_FOOTER_MARKER)) > 0
    assert len(_rendered_interpretation_baselines(page, reader)) == 2


@pytest.mark.parametrize(
    "box",
    [
        ArrayObject([NumberObject(0), NumberObject(0), NumberObject(0), NumberObject(10)]),
        ArrayObject([NumberObject(10), NumberObject(0), NumberObject(0), NumberObject(10)]),
        ArrayObject([NumberObject(0), NumberObject(10), NumberObject(10), NumberObject(0)]),
        ArrayObject([NumberObject(0), NumberObject(0), NumberObject(10)]),
        ArrayObject(
            [
                NumberObject(0),
                NumberObject(0),
                FloatObject(math.inf),
                NumberObject(10),
            ]
        ),
    ],
)
def test_caixas_malformadas_invertidas_vazias_ou_nao_finitas_sao_rejeitadas(box):
    page = DictionaryObject({NameObject("/MediaBox"): box})
    with pytest.raises(PdfPageGeometryError) as caught:
        effective_page_box(page)
    assert caught.value.codigo == "pdf_caixa_pagina_malformada"


def test_caixas_sem_intersecao_sao_rejeitadas_sem_normalizacao():
    source = _boxed_pdf(
        media=(0, 0, 595, 842),
        crop=(700, 900, 800, 1000),
    )
    with pytest.raises(PdfCompositionError) as caught:
        _compose(source)
    assert caught.value.codigo == "pdf_caixas_pagina_sem_intersecao"


def test_cropbox_presente_com_null_e_malformada():
    page = DictionaryObject(
        {
            NameObject("/MediaBox"): RectangleObject((0, 0, 595, 842)),
            NameObject("/CropBox"): NullObject(),
        }
    )
    with pytest.raises(PdfPageGeometryError) as caught:
        effective_page_box(page)
    assert caught.value.codigo == "pdf_caixa_pagina_malformada"


@pytest.mark.parametrize("placement", ["topo", "rodape"])
def test_area_visivel_minima_recusa_interpretacao_e_rodape(placement):
    source = _boxed_pdf(crop=(200, 300, 400, 400))
    with pytest.raises(PdfCompositionError) as caught:
        _compose(source, placement=placement, text="texto longo " * 30)
    assert caught.value.codigo in {
        "pagina_sem_area_util",
        "interpretacao_nao_cabe_na_pagina",
    }


def test_rodape_completo_e_revalidado_em_todas_as_paginas():
    writer = PdfWriter()
    first = writer.add_blank_page(width=595, height=842)
    first[NameObject("/CropBox")] = RectangleObject((30, 40, 560, 800))
    second = writer.add_blank_page(width=595, height=842)
    second[NameObject("/TrimBox")] = RectangleObject((50, 70, 540, 780))
    output = io.BytesIO()
    writer.write(output)
    result = compose_report_pdf(
        original_bytes=output.getvalue(),
        page_number=2,
        placement="rodape",
        interpretation_text="conteudo sintetico",
        max_size_bytes=MAX_BYTES,
    )
    reader = PdfReader(io.BytesIO(result.data), strict=True)
    assert len(reader.pages) == 2
    assert all(
        _rendered_marked_baselines(page, reader, marker=_FOOTER_MARKER)
        for page in reader.pages
    )


@pytest.mark.parametrize("placement", ["topo", "rodape"])
def test_texto_longo_nunca_trunca_nem_cria_pagina(placement):
    with pytest.raises(PdfCompositionError) as caught:
        _compose(_boxed_pdf(), placement=placement, text="palavra " * 5000)
    assert caught.value.codigo == "interpretacao_nao_cabe_na_pagina"


def test_saida_a4_normal_continua_valida():
    result = _compose(_boxed_pdf(), text="interpretação sintética curta")
    reader = PdfReader(io.BytesIO(result.data), strict=True)
    assert len(reader.pages) == 1
    assert effective_page_box(reader.pages[0]).visible_width == 595


def test_quebra_de_linha_preserva_todos_os_caracteres_e_espacos():
    original = "texto  com   espaços preservados e palavra-supercomprida"
    lines = _wrap_to_width(original, font_size=9, max_width=75)
    assert "".join(lines) == original
    assert all(lines)
