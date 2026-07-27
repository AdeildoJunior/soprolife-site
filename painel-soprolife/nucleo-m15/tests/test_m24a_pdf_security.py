"""Regressões hostis de geometria e conteúdo ativo do PDF M24A."""

import io

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    RectangleObject,
    TextStringObject,
)

from app.services.pdf_validation import InvalidPdfError, validate_pdf_bytes
from app.services.report_pdf import (
    PdfCompositionError,
    _rendered_interpretation_baselines,
    compose_report_pdf,
)

MAX_BYTES = 30 * 1024 * 1024


def _pdf(width: float = 595, height: float = 842, *, origin=(0, 0)) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=width, height=height)
    if origin != (0, 0):
        left, bottom = origin
        page.mediabox = RectangleObject(
            [left, bottom, left + width, bottom + height]
        )
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("placement", ["topo", "rodape"])
def test_texto_longo_nunca_e_truncado_nem_sai_da_pagina(placement):
    with pytest.raises(PdfCompositionError) as caught:
        compose_report_pdf(
            original_bytes=_pdf(),
            page_number=1,
            placement=placement,
            interpretation_text="X" * 8000,
            max_size_bytes=MAX_BYTES,
        )
    assert caught.value.codigo == "interpretacao_nao_cabe_na_pagina"


@pytest.mark.parametrize("placement", ["topo", "rodape"])
def test_pagina_pequena_recusa_bloco_acima_do_rodape(placement):
    with pytest.raises(PdfCompositionError) as caught:
        compose_report_pdf(
            original_bytes=_pdf(width=200, height=140),
            page_number=1,
            placement=placement,
            interpretation_text="TESTE " * 20,
            max_size_bytes=MAX_BYTES,
        )
    assert caught.value.codigo == "interpretacao_nao_cabe_na_pagina"


def test_pagina_sem_area_para_rodape_institucional_e_recusada():
    with pytest.raises(PdfCompositionError) as caught:
        compose_report_pdf(
            original_bytes=_pdf(width=200, height=100),
            page_number=1,
            placement="topo",
            interpretation_text=None,
            max_size_bytes=MAX_BYTES,
        )
    assert caught.value.codigo == "pagina_sem_area_util"


@pytest.mark.parametrize(
    ("placement", "expected_band"),
    [("topo", (700, 842)), ("rodape", (55, 180))],
)
def test_coordenadas_pos_composicao_ficam_no_mediabox_e_acima_do_rodape(
    placement, expected_band
):
    source = _pdf(origin=(20, 30))
    composed = compose_report_pdf(
        original_bytes=source,
        page_number=1,
        placement=placement,
        interpretation_text="linha sintetica um\nlinha sintetica dois\nlinha sintetica tres",
        max_size_bytes=MAX_BYTES,
    )
    reader = PdfReader(io.BytesIO(composed.data), strict=True)
    page = reader.pages[0]
    coordinates = _rendered_interpretation_baselines(page, reader)

    assert len(coordinates) == 3
    left = float(page.mediabox.left)
    bottom = float(page.mediabox.bottom)
    right = float(page.mediabox.right)
    top = float(page.mediabox.top)
    assert all(left <= x <= right for x, _y in coordinates)
    assert all(bottom + 55 < y < top - 30 for _x, y in coordinates)
    low, high = expected_band
    assert all(bottom + low < y < bottom + high for _x, y in coordinates)


def _active_pdf(kind: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    uri = DictionaryObject(
        {
            NameObject("/S"): NameObject("/URI"),
            NameObject("/URI"): TextStringObject("https://example.test/automatico"),
        }
    )
    if kind == "open_action":
        writer._root_object[NameObject("/OpenAction")] = uri
    elif kind == "aa":
        page[NameObject("/AA")] = DictionaryObject(
            {NameObject("/O"): uri}
        )
    elif kind == "javascript_tree":
        writer._root_object[NameObject("/Names")] = DictionaryObject(
            {
                NameObject("/JavaScript"): DictionaryObject(
                    {NameObject("/Names"): ArrayObject()}
                )
            }
        )
    elif kind == "embedded_files":
        writer._root_object[NameObject("/Names")] = DictionaryObject(
            {
                NameObject("/EmbeddedFiles"): DictionaryObject(
                    {NameObject("/Names"): ArrayObject()}
                )
            }
        )
    elif kind == "file_attachment":
        page[NameObject("/Annots")] = ArrayObject(
            [
                DictionaryObject(
                    {
                        NameObject("/Subtype"): NameObject("/FileAttachment"),
                        NameObject("/Rect"): ArrayObject(
                            [NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)]
                        ),
                    }
                )
            ]
        )
    elif kind == "launch":
        page[NameObject("/Annots")] = ArrayObject(
            [
                DictionaryObject(
                    {
                        NameObject("/Subtype"): NameObject("/Link"),
                        NameObject("/Rect"): ArrayObject(
                            [NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)]
                        ),
                        NameObject("/A"): DictionaryObject(
                            {
                                NameObject("/S"): NameObject("/Launch"),
                                NameObject("/F"): TextStringObject("programa"),
                            }
                        ),
                    }
                )
            ]
        )
    elif kind == "rich_media":
        page[NameObject("/Annots")] = ArrayObject(
            [
                DictionaryObject(
                    {
                        NameObject("/Subtype"): NameObject("/RichMedia"),
                        NameObject("/Rect"): ArrayObject(
                            [NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)]
                        ),
                    }
                )
            ]
        )
    else:  # pragma: no cover - helper fechado
        raise AssertionError(kind)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize(
    ("kind", "codigo"),
    [
        ("open_action", "pdf_conteudo_ativo_openaction"),
        ("aa", "pdf_conteudo_ativo_aa"),
        ("javascript_tree", "pdf_conteudo_ativo_javascript"),
        ("embedded_files", "pdf_conteudo_embutido"),
        ("file_attachment", "pdf_conteudo_embutido"),
        ("launch", "pdf_conteudo_ativo_launch"),
        ("rich_media", "pdf_conteudo_ativo_richmedia"),
    ],
)
def test_conteudo_ativo_e_embutido_e_rejeitado(kind, codigo):
    with pytest.raises(InvalidPdfError) as caught:
        validate_pdf_bytes(_active_pdf(kind), max_size_bytes=MAX_BYTES)
    assert caught.value.codigo == codigo


def test_link_uri_manual_nao_e_confundido_com_acao_automatica():
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    page[NameObject("/Annots")] = ArrayObject(
        [
            DictionaryObject(
                {
                    NameObject("/Subtype"): NameObject("/Link"),
                    NameObject("/Rect"): ArrayObject(
                        [NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)]
                    ),
                    NameObject("/A"): DictionaryObject(
                        {
                            NameObject("/S"): NameObject("/URI"),
                            NameObject("/URI"): TextStringObject(
                                "https://example.test/manual"
                            ),
                        }
                    ),
                }
            )
        ]
    )
    output = io.BytesIO()
    writer.write(output)
    assert validate_pdf_bytes(
        output.getvalue(), max_size_bytes=MAX_BYTES
    ).page_count == 1


def test_traversal_com_referencia_ciclica_termina_com_seguranca():
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    cycle = DictionaryObject({NameObject("/Type"): NameObject("/Metadata")})
    cycle_ref = writer._add_object(cycle)
    cycle[NameObject("/Next")] = cycle_ref
    page[NameObject("/PieceInfo")] = cycle_ref
    output = io.BytesIO()
    writer.write(output)
    assert validate_pdf_bytes(
        output.getvalue(), max_size_bytes=MAX_BYTES
    ).page_count == 1


def test_validacao_e_repetida_depois_da_composicao(monkeypatch):
    import app.services.report_pdf as report_pdf

    real_validate = report_pdf.validate_pdf_bytes
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_validate(*args, **kwargs)

    monkeypatch.setattr(report_pdf, "validate_pdf_bytes", counted)
    compose_report_pdf(
        original_bytes=_pdf(),
        page_number=1,
        placement="topo",
        interpretation_text="texto sintetico",
        max_size_bytes=MAX_BYTES,
    )
    assert calls == 2
