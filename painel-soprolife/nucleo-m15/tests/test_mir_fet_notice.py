"""Observação técnica de FET nos laudos MIR.

A observação aparece no laudo nativo SOMENTE quando o exame foi registrado
como MIR Spirobank Smart II. KoKo, Outro e "não informado" (NULL) não a
recebem. Nada aqui representa paciente, médico, CRM ou assinatura real: todas
as fixtures são marcadamente sintéticas (TESTE - APAGAR) e o PNG de assinatura
é um traço geométrico gerado na hora.

Cobre os dez casos da missão:
  1. exame MIR -> observação aparece;
  2. exame KoKo -> não aparece;
  3. equipamento desconhecido (NULL) -> não aparece;
  4. a observação não entra na conclusão médica;
  5. a conclusão e os dados do laudo permanecem idênticos com/sem a observação;
  6. o PDF continua bem formado e não ganha página à toa;
  7. a assinatura continua funcionando com a observação presente;
  8-10. persistência/auditoria do campo e laudo histórico intocado.
"""

from __future__ import annotations

import io
import struct
import zlib
from datetime import datetime, timezone

import pytest
from pypdf import PdfReader

from app.config import get_settings
from app.models import (
    EQUIPAMENTO_FONTE_KOKO,
    EQUIPAMENTO_FONTE_MIR_SPIROBANK_SMART_II,
    EQUIPAMENTO_FONTE_OUTRO,
    equipamento_fonte_e_mir,
)
from app.services.report_native_pdf import (
    FET_NOTICE_TITLE,
    ExamBlock,
    LocationBlock,
    NativeReportContent,
    PatientBlock,
    PhysicianBlock,
    SignatureImage,
    build_native_report_pdf,
)

API = "/api/v1"
CONCLUSAO = (
    "Espirometria dentro dos limites da normalidade. TESTE - APAGAR. "
    "Sem distúrbio ventilatório obstrutivo ou restritivo evidente."
)


def _synthetic_signature_png(width: int = 300, height: int = 90) -> bytes:
    """PNG sintético — traço geométrico, jamais uma assinatura real."""

    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            on = abs((x * height) // width - y) < 3
            value = 0 if on else 255
            raw.extend((value, value, value, 255 if on else 0))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )


def _pdf_text(data: bytes) -> str:
    return "\n".join(
        page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages
    )


def _content(
    *,
    equipamento_fonte: str | None,
    released: bool = False,
    with_signature: bool = False,
    conclusion: str = CONCLUSAO,
) -> NativeReportContent:
    now = datetime(2026, 10, 29, 13, 30, tzinfo=timezone.utc)
    signature = None
    if with_signature:
        png = _synthetic_signature_png()
        signature = SignatureImage(data=png, width=300, height=90)
    return NativeReportContent(
        document_code="LAU-TESTE-001",
        version_number=1,
        patient=PatientBlock(
            full_name="TESTE - APAGAR Paciente 001",
            birth_date=None,
            sex="feminino",
            public_code="PAC-TESTE-001",
        ),
        exam=ExamBlock(
            public_code="ESP-TESTE-001",
            exam_date=None,
            exam_time=None,
            date_precision=None,
            has_post_bd=False,
            clinical_indication=None,
            is_mir_equipment=equipamento_fonte_e_mir(equipamento_fonte),
        ),
        location=LocationBlock(
            name="SoproLife — Unidade Sintética",
            address_line="Rua de Teste, 000 — TESTE",
            contact_line="contato@teste.local",
        ),
        physician=PhysicianBlock(
            professional_name="TESTE - APAGAR Médica",
            specialty="Pneumologia",
            crm_display="00000-RJ",
            crm_state="RJ",
            rqe=None,
        ),
        conclusion_text=conclusion,
        observations=None,
        issued_at_local=now,
        released=released,
        released_at_local=now if released else None,
        validation_code="ABCDEFGH2345" if released else None,
        signature_image=signature,
    )


# -------------------------------------------------------------- domínio


def test_so_o_mir_spirobank_smart_ii_e_mir():
    assert equipamento_fonte_e_mir(EQUIPAMENTO_FONTE_MIR_SPIROBANK_SMART_II) is True
    assert equipamento_fonte_e_mir(EQUIPAMENTO_FONTE_KOKO) is False
    assert equipamento_fonte_e_mir(EQUIPAMENTO_FONTE_OUTRO) is False
    assert equipamento_fonte_e_mir(None) is False
    assert equipamento_fonte_e_mir("MIR") is False  # rótulo solto não conta


# ------------------------------------------------- PDF: aparece / não aparece


def test_caso_1_exame_mir_mostra_observacao():
    texto = _pdf_text(
        build_native_report_pdf(
            _content(equipamento_fonte=EQUIPAMENTO_FONTE_MIR_SPIROBANK_SMART_II)
        )
    )
    assert FET_NOTICE_TITLE in texto
    assert "FET" in texto


def test_caso_2_exame_koko_nao_mostra_observacao():
    texto = _pdf_text(
        build_native_report_pdf(_content(equipamento_fonte=EQUIPAMENTO_FONTE_KOKO))
    )
    assert FET_NOTICE_TITLE not in texto


def test_caso_3_equipamento_desconhecido_nao_mostra_observacao():
    texto_nulo = _pdf_text(
        build_native_report_pdf(_content(equipamento_fonte=None))
    )
    texto_outro = _pdf_text(
        build_native_report_pdf(_content(equipamento_fonte=EQUIPAMENTO_FONTE_OUTRO))
    )
    assert FET_NOTICE_TITLE not in texto_nulo
    assert FET_NOTICE_TITLE not in texto_outro


def test_caso_4_observacao_nao_entra_na_conclusao_medica():
    texto = _pdf_text(
        build_native_report_pdf(
            _content(equipamento_fonte=EQUIPAMENTO_FONTE_MIR_SPIROBANK_SMART_II)
        )
    )
    # A observação não faz parte do texto que a médica assina...
    assert FET_NOTICE_TITLE not in CONCLUSAO
    # ...e no documento vem DEPOIS da seção de conclusões E depois da nota de
    # equipamento (rodapé técnico), nunca dentro da conclusão.
    assert texto.index("CONCLUS") < texto.index(FET_NOTICE_TITLE)
    assert texto.index("PDF técnico do equipamento") < texto.index(FET_NOTICE_TITLE)


def test_caso_5_conclusao_e_dados_identicos_com_e_sem_observacao():
    com = _pdf_text(
        build_native_report_pdf(
            _content(equipamento_fonte=EQUIPAMENTO_FONTE_MIR_SPIROBANK_SMART_II)
        )
    )
    sem = _pdf_text(_content_pdf_koko())
    # A conclusão e a identificação aparecem exatamente iguais nos dois; o
    # único acréscimo do MIR é o bloco da observação técnica.
    for trecho in ("TESTE - APAGAR Paciente 001", "ESP-TESTE-001", "normalidade"):
        assert trecho in com and trecho in sem
    assert FET_NOTICE_TITLE in com and FET_NOTICE_TITLE not in sem


def _content_pdf_koko() -> bytes:
    return build_native_report_pdf(
        _content(equipamento_fonte=EQUIPAMENTO_FONTE_KOKO)
    )


def test_caso_6_pdf_bem_formado_e_sem_pagina_extra_desnecessaria():
    pdf_mir = build_native_report_pdf(
        _content(equipamento_fonte=EQUIPAMENTO_FONTE_MIR_SPIROBANK_SMART_II)
    )
    pdf_koko = build_native_report_pdf(
        _content(equipamento_fonte=EQUIPAMENTO_FONTE_KOKO)
    )
    reader_mir = PdfReader(io.BytesIO(pdf_mir))
    reader_koko = PdfReader(io.BytesIO(pdf_koko))
    # Com conclusão curta, a observação cabe na mesma página: não empurra a
    # assinatura/QR para uma página nova à toa.
    assert len(reader_mir.pages) == 1
    assert len(reader_koko.pages) == len(reader_mir.pages)


def test_caso_7_assinatura_continua_funcionando_com_a_observacao():
    pdf = build_native_report_pdf(
        _content(
            equipamento_fonte=EQUIPAMENTO_FONTE_MIR_SPIROBANK_SMART_II,
            released=True,
            with_signature=True,
        )
    )
    texto = _pdf_text(pdf)
    assert FET_NOTICE_TITLE in texto
    # Documento liberado e assinado: o código de validação e o bloco médico
    # continuam presentes mesmo com a observação de FET desenhada.
    assert "ABCDEFGH2345" in texto
    assert "00000-RJ" in texto
    assert PdfReader(io.BytesIO(pdf)).pages  # parseável


# ------------------------------------------ persistência, API e auditoria


def test_caso_8_campo_persiste_e_volta_na_serializacao(client, auth, person):
    resp = client.post(
        f"{API}/espirometrias",
        json={
            "person_id": person["id"],
            "equipamento_fonte": EQUIPAMENTO_FONTE_MIR_SPIROBANK_SMART_II,
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["equipamento_fonte"] == EQUIPAMENTO_FONTE_MIR_SPIROBANK_SMART_II


def test_caso_9_ausente_fica_nulo_legado_intocado(client, auth, person):
    resp = client.post(
        f"{API}/espirometrias",
        json={"person_id": person["id"]},
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["equipamento_fonte"] is None


def test_caso_10_patch_informa_no_recebimento_e_audita(client, auth, person, db):
    from app.models import AuditLog, SpirometryExam
    from sqlalchemy import select

    criado = client.post(
        f"{API}/espirometrias",
        json={"person_id": person["id"]},
        headers=auth("operacional"),
    ).json()
    assert criado["equipamento_fonte"] is None

    patch = client.patch(
        f"{API}/espirometrias/{criado['id']}",
        json={"equipamento_fonte": EQUIPAMENTO_FONTE_MIR_SPIROBANK_SMART_II},
        headers=auth("operacional"),
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["equipamento_fonte"] == EQUIPAMENTO_FONTE_MIR_SPIROBANK_SMART_II

    exam = db.execute(
        select(SpirometryExam).where(SpirometryExam.id == criado["id"])
    ).scalar_one()
    assert exam.equipamento_fonte == EQUIPAMENTO_FONTE_MIR_SPIROBANK_SMART_II

    eventos = db.execute(
        select(AuditLog).where(AuditLog.entidade_id == criado["id"])
    ).scalars().all()
    campos = [
        c
        for e in eventos
        if e.acao == "espirometria.atualizada"
        for c in (e.detalhes or {}).get("campos", [])
    ]
    assert "equipamento_fonte" in campos


def test_valor_fora_do_catalogo_e_recusado(client, auth, person):
    resp = client.post(
        f"{API}/espirometrias",
        json={"person_id": person["id"], "equipamento_fonte": "MINHA_MARCA"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 422, resp.text
