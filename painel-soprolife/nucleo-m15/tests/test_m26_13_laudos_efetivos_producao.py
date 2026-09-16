"""M26.13 — "laudo efetivo": correção não duplica repasse nem produção.

Reaproveita os helpers já provados em `test_m25_2_native_report.py`
(criação de caso, prévia, liberação) e `test_m25_29d_fluxo_conclusao_
assinatura.py` (assinatura externa sintética) via import dinâmico — dirige
o fluxo pela API real, nunca inserindo linha de `ReportDocumentVersion` à
mão (evitaria violar as CheckConstraints de coerência clínica).

Tudo sintético: pacientes "TESTE APAGAR", médicas de teste, PDFs gerados
na hora.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest
from sqlalchemy import select


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

_M29D = _load("test_m25_29d_fluxo_conclusao_assinatura.py")
_assinar_por_fora = _M29D._assinar_por_fora


@pytest.fixture(autouse=True)
def reports_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv(
        "M15_AUTH_SECRET", "m26-13-secret-de-teste-0123456789abcdef0123"
    )
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def case(client, auth, db, person):
    return _make_case(client, auth, db, person, com_bd=True)


def _liberar(client, case, *, conclusion_code="NORMAL"):
    preview = _preview(
        client, case, conclusion_code=conclusion_code, bronchodilator_code="BD_NAO_REALIZADO"
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


def _assinar_externamente(client, document_id, doctor_auth):
    baixado = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [document_id]},
        headers=doctor_auth,
    )
    assert baixado.status_code == 200, baixado.text
    assinado = _assinar_por_fora(baixado.content)
    envio = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={"arquivos": ("TESTE APAGAR - assinado.pdf", assinado, "application/pdf")},
        headers=doctor_auth,
    )
    assert envio.status_code == 200, envio.text


def _producao(client, auth, profile_id, competencia):
    resposta = client.get(
        f"/api/v1/financeiro/repasses-medicos/{profile_id}/producao",
        params={"competencia": competencia},
        headers=auth("gestor"),
    )
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def _competencia_de(iso_datetime: str) -> str:
    return iso_datetime[:7]


# ---------------------------------------------------- contagem de repasse


def test_correcao_nao_soma_um_segundo_laudo_elegivel(client, auth, case, db):
    from app.services.medical_transfers import eligible_report_count, parse_competence

    liberado = _liberar(client, case)
    competencia = parse_competence(_competencia_de(liberado["released_at"]))
    antes = eligible_report_count(db, case["profile"]["id"], competencia)
    assert antes == 1
    # M26.4 já documentou este cuidado: a suíte usa SQLite e a sessão do
    # teste divide o arquivo com a sessão da API. Uma leitura aberta aqui
    # trava o próximo `commit()` do endpoint com "database is locked".
    db.commit()

    corretiva = _abrir_corretiva(client, case)
    novo_caso = {**case, "document": {"id": corretiva["id"]}}
    _liberar(client, novo_caso)

    depois = eligible_report_count(db, case["profile"]["id"], competencia)
    assert depois == 1, "a corretiva liberada não pode somar um segundo laudo elegível"


def test_endpoint_de_repasse_reflete_a_mesma_regra(client, auth, case, db):
    liberado = _liberar(client, case)
    competencia = _competencia_de(liberado["released_at"])
    corretiva = _abrir_corretiva(client, case)
    novo_caso = {**case, "document": {"id": corretiva["id"]}}
    _liberar(client, novo_caso)

    resposta = client.get(
        f"/api/v1/financeiro/repasses-medicos?competencia={competencia}",
        headers=auth("gestor"),
    )
    assert resposta.status_code == 200, resposta.text
    linha = next(
        m for m in resposta.json()["medicas"]
        if m["physician_profile_id"] == case["profile"]["id"]
    )
    assert linha["quantidade_laudos_elegiveis"] == 1


# ------------------------------------------------- produção da médica


def test_producao_mostra_efetivo_e_corrigido_sem_duplicar(client, auth, case):
    liberado = _liberar(client, case, conclusion_code="DVO_LEVE")
    competencia = _competencia_de(liberado["released_at"])
    corretiva = _abrir_corretiva(client, case)
    novo_caso = {**case, "document": {"id": corretiva["id"]}}
    _liberar(client, novo_caso, conclusion_code="NORMAL")

    producao = _producao(client, auth, case["profile"]["id"], competencia)
    assert producao["efetivos"] == 1
    assert producao["corrigidos"] == 1
    # A conclusão que conta no painel é a da corretiva (VIGENTE) — a
    # original foi superada, não é mais o retrato clínico válido do exame.
    assert producao["distribuicao_conclusao"] == [
        {"conclusion_code": "NORMAL", "rotulo": "Normal", "grupo": "normal", "quantidade": 1}
    ]


def test_producao_sem_correcao_usa_a_conclusao_do_proprio_original(client, auth, case):
    liberado = _liberar(client, case, conclusion_code="DVO_MODERADO")
    competencia = _competencia_de(liberado["released_at"])
    producao = _producao(client, auth, case["profile"]["id"], competencia)
    assert producao["efetivos"] == 1
    assert producao["corrigidos"] == 0
    assert producao["distribuicao_conclusao"][0]["conclusion_code"] == "DVO_MODERADO"


def test_producao_conta_assinado_e_entregue_pelo_documento_vigente(client, auth, case):
    liberado = _liberar(client, case)
    competencia = _competencia_de(liberado["released_at"])
    _assinar_externamente(client, case["document"]["id"], case["doctor_auth"])

    producao = _producao(client, auth, case["profile"]["id"], competencia)
    assert producao["efetivos"] == 1
    assert producao["assinados"] == 1
    assert producao["aguardando_assinatura"] == 0


def test_producao_pendente_reflete_bancada_atual_sem_filtrar_por_competencia(
    client, auth, case
):
    # Exame ainda em elaboração — sem released_at, não entra em "efetivos".
    _preview(client, case, conclusion_code="NORMAL", bronchodilator_code="BD_NAO_REALIZADO")
    producao = _producao(client, auth, case["profile"]["id"], "2020-01")
    assert producao["efetivos"] == 0
    assert producao["pendentes"] == 1


def test_medica_e_operacional_nao_acessam_producao(client, auth, case):
    assert client.get(
        f"/api/v1/financeiro/repasses-medicos/{case['profile']['id']}/producao",
        headers=case["doctor_auth"],
    ).status_code == 403
    assert client.get(
        f"/api/v1/financeiro/repasses-medicos/{case['profile']['id']}/producao",
        headers=auth("operacional"),
    ).status_code == 403


def test_medica_inexistente_e_404(client, auth):
    assert client.get(
        "/api/v1/financeiro/repasses-medicos/00000000-0000-0000-0000-000000000000/producao",
        headers=auth("gestor"),
    ).status_code == 404


# --------------------------------------- acompanhamento operacional limpo


def _fila_operacional(client, auth, **params):
    resposta = client.get(
        "/api/v1/laudos", params=params, headers=auth("operacional")
    )
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def test_laudo_superado_por_corretiva_some_da_fila_ativa(client, auth, case):
    _liberar(client, case)
    document_id = case["document"]["id"]
    assert any(
        item["document_id"] == document_id for item in _fila_operacional(client, auth)
    ), "antes de corrigir, o laudo original precisa estar na fila ativa"

    _abrir_corretiva(client, case)

    ativa = _fila_operacional(client, auth)
    assert not any(item["document_id"] == document_id for item in ativa), (
        "o laudo superado por corretiva não pode continuar na fila ativa"
    )
    historico = _fila_operacional(client, auth, somente_superados="true")
    alvo = next(item for item in historico if item["document_id"] == document_id)
    assert alvo["has_corrective_successor"] is True
    assert alvo["is_delivered"] is False

    completa = _fila_operacional(client, auth, incluir_superados="true")
    assert any(item["document_id"] == document_id for item in completa)


def test_laudo_entregue_some_da_fila_ativa(client, auth, case, db):
    from app.models import ExternalSignedDocument

    _liberar(client, case)
    document_id = case["document"]["id"]
    _assinar_externamente(client, document_id, case["doctor_auth"])
    db.commit()
    assinado = db.execute(
        select(ExternalSignedDocument).where(
            ExternalSignedDocument.report_document_id == document_id
        )
    ).scalar_one()
    db.commit()

    entrega = client.post(
        f"/api/v1/laudos/assinatura-externa/{assinado.id}/entrega",
        headers=auth("operacional"),
    )
    assert entrega.status_code == 200, entrega.text

    ativa = _fila_operacional(client, auth)
    assert not any(item["document_id"] == document_id for item in ativa), (
        "o laudo já entregue não pode continuar poluindo a fila ativa"
    )
    historico = _fila_operacional(client, auth, somente_superados="true")
    alvo = next(item for item in historico if item["document_id"] == document_id)
    assert alvo["is_delivered"] is True
    assert alvo["has_corrective_successor"] is False


def test_laudo_ativo_normal_continua_visivel_por_padrao(client, auth, case):
    _preview(client, case, conclusion_code="NORMAL", bronchodilator_code="BD_NAO_REALIZADO")
    document_id = case["document"]["id"]
    ativa = _fila_operacional(client, auth)
    alvo = next(item for item in ativa if item["document_id"] == document_id)
    assert alvo["has_corrective_successor"] is False
    assert alvo["is_delivered"] is False
