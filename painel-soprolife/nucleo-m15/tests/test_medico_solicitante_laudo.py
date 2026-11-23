"""Médico solicitante: opcional, por exame, do cadastro ao PDF do laudo.

Pacientes, médicos e CRMs são sintéticos.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import (
    AuditLog,
    FinancialEntry,
    ReportDocument,
    ReportDocumentVersion,
    SpirometryExam,
)
from tests.test_m25_2_native_report import (  # noqa: F401
    _make_case,
    _pdf_text,
    _preview,
    _release,
    _stored_bytes,
    case,
    reports_enabled,
)

NOME = "Dra. Júlia Conceição Ñandú"
CRM = "1234567-8"
LINHA = f"Médico solicitante: {NOME} — CRM {CRM}"


def _salvar(client, headers, exame, nome=NOME, crm=CRM):
    return client.post(
        f"/api/v1/laudos/exames/{exame['public_code']}/medico-solicitante",
        json={"medico_solicitante_nome": nome, "medico_solicitante_crm": crm},
        headers=headers,
    )


def _texto(db, version_id):
    texto = _pdf_text(_stored_bytes(db, db.get(ReportDocumentVersion, version_id)))
    db.rollback()  # solta o lock de leitura do SQLite antes da próxima chamada
    return " ".join(texto.split())


def _detalhe(client, case):
    return client.get(
        f"/api/v1/laudos/{case['document']['id']}", headers=case["doctor_auth"]
    ).json()


def test_salva_reabre_e_aparece_para_medica_e_operacao(client, auth, case, db):
    antes = db.get(SpirometryExam, case["exam"]["id"])
    status, lancamentos = antes.status, db.query(FinancialEntry).count()
    db.rollback()
    resp = _salvar(client, auth("operacional"), case["exam"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["requesting_physician"] == f"{NOME} — CRM {CRM}"

    reaberto = client.get(
        f"/api/v1/espirometrias?public_code={case['exam']['public_code']}",
        headers=auth("operacional"),
    ).json()["itens"][0]
    assert reaberto["medico_solicitante_nome"] == NOME
    assert reaberto["medico_solicitante_crm"] == CRM
    linha = client.get("/api/v1/laudos", headers=auth("operacional")).json()
    assert linha[0]["medico_solicitante_nome"] == NOME
    assert (
        _detalhe(client, case)["exam"]["requesting_physician"]
        == f"{NOME} — CRM {CRM}"
    )

    db.expire_all()
    exam = db.get(SpirometryExam, case["exam"]["id"])
    assert exam.status == status
    assert db.query(FinancialEntry).count() == lancamentos
    assert db.get(ReportDocument, case["document"]["id"]).status == "atribuido"
    evento = db.execute(
        select(AuditLog).where(
            AuditLog.acao == "exame_medico_solicitante_atualizado"
        )
    ).scalar_one()
    assert NOME not in str(evento.detalhes)


def test_cadastro_do_atendimento_grava_e_outro_exame_fica_vazio(
    client, auth, person, case
):
    resp = client.post(
        "/api/v1/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": {
                "data_exame": "2026-07-16",
                "medico_solicitante_nome": f"  {NOME} ",
                "medico_solicitante_crm": CRM,
            },
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["espirometria"]["medico_solicitante_nome"] == NOME
    assert case["exam"]["medico_solicitante_nome"] is None


def test_pdf_preenchido_na_previa_e_no_liberado_com_acentos(
    client, auth, case, db
):
    _salvar(client, auth("operacional"), case["exam"])
    preview = _preview(client, case)
    assert preview.status_code == 200, preview.text
    assert LINHA in _texto(db, preview.json()["preview_version_id"])
    liberado = _release(client, case, preview.json())
    assert liberado.status_code == 200, liberado.text
    version = db.get(ReportDocumentVersion, liberado.json()["released_version_id"])
    assert LINHA in _texto(db, version.id)
    assert version.medico_solicitante_snapshot == f"{NOME} — CRM {CRM}"
    assert version.page_count == 1
    db.rollback()
    # Concluído: não se reescreve.
    tardio = _salvar(client, auth("operacional"), case["exam"], "Dr. Outro", "999")
    assert tardio.status_code == 409


def test_pdf_vazio_nao_mostra_o_campo(client, auth, case, db):
    _salvar(client, auth("operacional"), case["exam"])
    assert _salvar(client, auth("operacional"), case["exam"], "", "").status_code == 200
    preview = _preview(client, case)
    assert "solicitante" not in _texto(
        db, preview.json()["preview_version_id"]
    ).lower()
    liberado = _release(client, case, preview.json())
    assert liberado.status_code == 200
    assert "solicitante" not in _texto(
        db, liberado.json()["released_version_id"]
    ).lower()
    assert _detalhe(client, case)["exam"]["requesting_physician"] is None


def test_mudanca_apos_a_previa_exige_nova_previa(client, auth, case):
    preview = _preview(client, case)
    _salvar(client, auth("operacional"), case["exam"])
    recusado = _release(client, case, preview.json())
    assert recusado.status_code == 409
    assert "previa_desatualizada" in recusado.text
    assert _release(client, case, _preview(client, case).json()).status_code == 200


def test_rbac(client, auth, case):
    assert _salvar(client, {}, case["exam"]).status_code == 401
    assert _salvar(client, auth("leitura"), case["exam"]).status_code == 403
    assert _salvar(client, case["doctor_auth"], case["exam"]).status_code == 403
    for papel in ("operacional", "gestor", "admin"):
        assert _salvar(client, auth(papel), case["exam"]).status_code == 200


@pytest.mark.parametrize(
    "nome,crm", [("", CRM), (NOME, "<b>"), ("x" * 121, CRM), (NOME, "CRM 1")]
)
def test_entrada_invalida(client, auth, case, nome, crm):
    assert _salvar(client, auth("operacional"), case["exam"], nome, crm).status_code == 422


def test_so_o_nome_e_aceito(client, auth, case):
    resp = _salvar(client, auth("operacional"), case["exam"], NOME, "")
    assert resp.json()["requesting_physician"] == NOME


@pytest.mark.parametrize("crm", ["5240927-9", "52.40927-9", "12345/RJ", "CRM-"])
def test_formato_crm(client, auth, case, crm):
    esperado = 422 if crm == "CRM-" else 200
    assert _salvar(client, auth("operacional"), case["exam"], crm=crm).status_code == esperado
