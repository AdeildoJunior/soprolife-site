"""External URI actions are untrusted and rejected without network access."""

import io
import socket

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)
from reportlab.pdfgen import canvas

from app.services.pdf_validation import (
    APPROVED_EXTERNAL_URI_ORIGINS,
    InvalidPdfError,
    validate_pdf_bytes,
)
from app.services.report_pdf import PdfCompositionError, compose_report_pdf

MAX_BYTES = 30 * 1024 * 1024


def _uri_action() -> DictionaryObject:
    return DictionaryObject(
        {
            NameObject("/S"): NameObject("/URI"),
            NameObject("/URI"): TextStringObject("https://untrusted.example.invalid/path"),
        }
    )


def _write(writer: PdfWriter) -> bytes:
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _assert_uri_rejected(data: bytes) -> None:
    with pytest.raises(InvalidPdfError) as caught:
        validate_pdf_bytes(data, max_size_bytes=MAX_BYTES)
    assert caught.value.codigo == "pdf_uri_externa_nao_permitida"


def test_uri_em_anotacao_e_rejeitada():
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    page[NameObject("/Annots")] = ArrayObject(
        [
            DictionaryObject(
                {
                    NameObject("/Subtype"): NameObject("/Link"),
                    NameObject("/Rect"): ArrayObject(
                        [
                            NumberObject(0),
                            NumberObject(0),
                            NumberObject(10),
                            NumberObject(10),
                        ]
                    ),
                    NameObject("/A"): _uri_action(),
                }
            )
        ]
    )
    _assert_uri_rejected(_write(writer))


def test_uri_em_arvore_de_nomes_e_acoes_e_rejeitada():
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer._root_object[NameObject("/Names")] = DictionaryObject(
        {
            NameObject("/Dests"): DictionaryObject(
                {
                    NameObject("/Names"): ArrayObject(
                        [TextStringObject("destino-tecnico"), _uri_action()]
                    )
                }
            )
        }
    )
    _assert_uri_rejected(_write(writer))


def test_uri_escondida_em_objeto_indireto_e_rejeitada():
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    action_ref = writer._add_object(_uri_action())
    page[NameObject("/Annots")] = ArrayObject(
        [
            DictionaryObject(
                {
                    NameObject("/Subtype"): NameObject("/Link"),
                    NameObject("/Rect"): ArrayObject(
                        [
                            NumberObject(0),
                            NumberObject(0),
                            NumberObject(10),
                            NumberObject(10),
                        ]
                    ),
                    NameObject("/A"): action_ref,
                }
            )
        ]
    )
    _assert_uri_rejected(_write(writer))


def test_ciclo_de_uri_termina_e_e_rejeitado():
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    action = _uri_action()
    action_ref = writer._add_object(action)
    action[NameObject("/Next")] = action_ref
    page[NameObject("/PieceInfo")] = action_ref
    _assert_uri_rejected(_write(writer))


def test_texto_visivel_contendo_url_nao_e_acao():
    output = io.BytesIO()
    pdf_canvas = canvas.Canvas(output, pagesize=(595, 842))
    pdf_canvas.drawString(72, 760, "Texto visivel: https://example.invalid/nao-e-link")
    pdf_canvas.showPage()
    pdf_canvas.save()
    assert validate_pdf_bytes(
        output.getvalue(), max_size_bytes=MAX_BYTES
    ).page_count == 1


def test_validacao_de_uri_nao_faz_chamada_de_rede(monkeypatch):
    calls: list[str] = []

    def forbidden_network(*_args, **_kwargs):
        calls.append("network")
        raise AssertionError("network access is forbidden during PDF validation")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden_network)
    monkeypatch.setattr(socket.socket, "connect", forbidden_network)

    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    page[NameObject("/PieceInfo")] = _uri_action()
    _assert_uri_rejected(_write(writer))
    assert calls == []
    assert APPROVED_EXTERNAL_URI_ORIGINS == frozenset()


def test_composicao_revalida_e_nao_aceita_uri_reintroduzida(monkeypatch):
    import app.services.report_pdf as report_pdf

    source_writer = PdfWriter()
    source_writer.add_blank_page(width=595, height=842)
    source = _write(source_writer)
    real_write = PdfWriter.write

    def write_with_injected_uri(self, stream):
        intermediate = io.BytesIO()
        real_write(self, intermediate)
        reader = PdfReader(io.BytesIO(intermediate.getvalue()), strict=True)
        injected = PdfWriter(clone_from=reader)
        injected.pages[0][NameObject("/PieceInfo")] = _uri_action()
        return real_write(injected, stream)

    monkeypatch.setattr(report_pdf.PdfWriter, "write", write_with_injected_uri)
    with pytest.raises(PdfCompositionError) as caught:
        compose_report_pdf(
            original_bytes=source,
            page_number=1,
            placement="topo",
            interpretation_text="conteudo sintetico",
            max_size_bytes=MAX_BYTES,
        )
    assert caught.value.codigo == "pdf_uri_externa_nao_permitida"
