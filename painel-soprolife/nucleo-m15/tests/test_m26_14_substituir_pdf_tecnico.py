"""M26.14 — substituir o PDF técnico de uma corretiva ainda não iniciada.

Motivo real: o PDF do aparelho de espirometria pode sair incompleto (sem a
anotação do motivo do exame), e isso só é percebido DEPOIS de já ter aberto
uma corretiva por outro motivo (conteúdo clínico). O mecanismo de corretiva
do M25.2/M26.12 sempre herdava o PDF técnico do predecessor — esta missão
fecha essa lacuna com uma rota estreita: só corretiva, só antes de a médica
começar a elaborar, e a versão antiga nunca é apagada.

Reaproveita os helpers já provados em `test_m25_2_native_report.py` — dirige
o fluxo pela API real.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest
from sqlalchemy import select

from app.models import AuditLog, ReportDocumentVersion


def _load(nome: str):
    caminho = pathlib.Path(__file__).with_name(nome)
    spec = importlib.util.spec_from_file_location(f"_{nome[:-3]}", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


_M25_2 = _load("test_m25_2_native_report.py")
_make_case = _M25_2._make_case
_preview = _M25_2._preview
_release = _M25_2._release
_minimal_pdf = _M25_2._minimal_pdf


@pytest.fixture(autouse=True)
def reports_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv(
        "M15_AUTH_SECRET", "m26-14-secret-de-teste-0123456789abcdef0123"
    )
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def case(client, auth, db, person):
    return _make_case(client, auth, db, person, com_bd=True)


def _liberar(client, case):
    preview = _preview(
        client, case, conclusion_code="NORMAL", bronchodilator_code="BD_NAO_REALIZADO"
    ).json()
    released = _release(client, case, preview)
    assert released.status_code == 200, released.text
    return released.json()


def _abrir_corretiva(client, case, *, reason_code="clinical_correction"):
    resposta = client.post(
        f"/api/v1/laudos/{case['document']['id']}/nova-versao-corretiva",
        json={"reason_code": reason_code},
        headers=case["doctor_auth"],
    )
    assert resposta.status_code == 201, resposta.text
    return resposta.json()


def _substituir_pdf(client, auth, document_id, *, pdf_bytes=None, role_headers=None):
    return client.post(
        f"/api/v1/laudos/{document_id}/pdf-tecnico-original",
        files={"file": ("corrigido.pdf", pdf_bytes or _minimal_pdf(2), "application/pdf")},
        headers=role_headers or auth("operacional"),
    )


# ----------------------------------------------------------------- caminho feliz


def test_substitui_pdf_de_corretiva_nao_iniciada(client, auth, case, db):
    _liberar(client, case)
    corretiva = _abrir_corretiva(client, case, reason_code="technical_document_correction")
    document_id = corretiva["id"]

    versao_anterior_id = next(
        v["id"] for v in corretiva["versoes"] if v["kind"] == "original"
    )
    novo_pdf = _minimal_pdf(3)  # conteúdo claramente diferente (3 páginas)

    resposta = _substituir_pdf(client, auth, document_id, pdf_bytes=novo_pdf)
    assert resposta.status_code == 200, resposta.text
    body = resposta.json()

    versoes_originais = [v for v in body["versoes"] if v["kind"] == "original"]
    assert len(versoes_originais) == 2, "a versão antiga precisa continuar no histórico"
    nova_versao = max(versoes_originais, key=lambda v: v["version_number"])
    antiga_versao = min(versoes_originais, key=lambda v: v["version_number"])
    assert antiga_versao["id"] == versao_anterior_id
    assert nova_versao["id"] != versao_anterior_id
    assert nova_versao["sha256"] != antiga_versao["sha256"]
    assert nova_versao["page_count"] == 3
    assert body["current_version_id"] == nova_versao["id"]

    # A versão antiga continua intacta no banco — nunca é apagada.
    antiga_db = db.get(ReportDocumentVersion, versao_anterior_id)
    assert antiga_db is not None
    assert antiga_db.sha256 == antiga_versao["sha256"]

    evento = db.execute(
        select(AuditLog).where(AuditLog.acao == "laudo_pdf_tecnico_substituido")
    ).scalar_one()
    assert evento.entidade_id == document_id
    assert evento.detalhes["previous_version_id"] == versao_anterior_id
    assert evento.detalhes["report_version_id"] == nova_versao["id"]


def test_medica_consegue_concluir_normalmente_apos_a_substituicao(client, auth, case):
    _liberar(client, case)
    corretiva = _abrir_corretiva(client, case)
    _substituir_pdf(client, auth, corretiva["id"])

    novo_caso = {**case, "document": {"id": corretiva["id"]}}
    liberado = _liberar(client, novo_caso)
    assert liberado["status"] == "liberado"


# -------------------------------------------------------------- estados recusados


def test_recusa_substituir_pdf_do_documento_original(client, auth, case):
    resposta = _substituir_pdf(client, auth, case["document"]["id"])
    assert resposta.status_code == 409, resposta.text
    assert resposta.json()["erro"]["codigo"] == "nao_e_corretiva"


def test_recusa_depois_que_a_medica_ja_comecou(client, auth, case):
    _liberar(client, case)
    corretiva = _abrir_corretiva(client, case)
    # "Começar a elaborar" = gerar qualquer prévia (mesmo sem concluir).
    preview = client.post(
        f"/api/v1/laudos/{corretiva['id']}/laudo/previa",
        json={"conclusion_code": "NORMAL", "bronchodilator_code": "BD_NAO_REALIZADO"},
        headers=case["doctor_auth"],
    )
    assert preview.status_code == 200, preview.text

    resposta = _substituir_pdf(client, auth, corretiva["id"])
    assert resposta.status_code == 409, resposta.text
    assert resposta.json()["erro"]["codigo"] == "corretiva_ja_iniciada"


def test_recusa_depois_que_a_corretiva_ja_foi_liberada(client, auth, case):
    _liberar(client, case)
    corretiva = _abrir_corretiva(client, case)
    novo_caso = {**case, "document": {"id": corretiva["id"]}}
    _liberar(client, novo_caso)

    resposta = _substituir_pdf(client, auth, corretiva["id"])
    assert resposta.status_code == 409, resposta.text
    assert resposta.json()["erro"]["codigo"] == "corretiva_ja_iniciada"


def test_pdf_invalido_e_recusado(client, auth, case):
    _liberar(client, case)
    corretiva = _abrir_corretiva(client, case)
    resposta = client.post(
        f"/api/v1/laudos/{corretiva['id']}/pdf-tecnico-original",
        files={"file": ("nao-e-pdf.pdf", b"isto nao e um pdf de verdade", "application/pdf")},
        headers=auth("operacional"),
    )
    assert resposta.status_code == 422, resposta.text


def test_documento_inexistente_e_404(client, auth):
    resposta = _substituir_pdf(client, auth, "00000000-0000-0000-0000-000000000000")
    assert resposta.status_code == 404


# ------------------------------------------------------------------------ RBAC


def test_medica_sozinha_nao_substitui(client, auth, case):
    _liberar(client, case)
    corretiva = _abrir_corretiva(client, case)
    resposta = _substituir_pdf(
        client, auth, corretiva["id"], role_headers=case["doctor_auth"]
    )
    assert resposta.status_code == 403, resposta.text


def test_leitura_nao_substitui(client, auth, case):
    _liberar(client, case)
    corretiva = _abrir_corretiva(client, case)
    resposta = _substituir_pdf(
        client, auth, corretiva["id"], role_headers=auth("leitura")
    )
    assert resposta.status_code == 403, resposta.text
