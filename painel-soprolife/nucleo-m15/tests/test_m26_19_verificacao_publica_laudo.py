"""M26.19 — a sexta superfície pública: confirmar que um código impresso no
laudo corresponde a um documento real e liberado, sem sessão de paciente
nenhuma. Qualquer pessoa com o código (a própria paciente, um convênio, um
empregador) pode conferir — mas a resposta nunca traz paciente, exame nem
conteúdo clínico.

Reaproveita os fixtures já provados em `test_m26_4_portal_resultados.py`
(`portal`, `portal_ligado`) e os helpers de `test_m25_2_native_report.py`
via import dinâmico — dirige o fluxo pela API real.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest


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

_M26_4 = _load("test_m26_4_portal_resultados.py")
portal_ligado = _M26_4.portal_ligado
portal = _M26_4.portal


@pytest.fixture()
def case(client, auth, db, person):
    return _make_case(client, auth, db, person, com_bd=True)


def _liberar(client, case, *, conclusion_code="NORMAL"):
    preview = _preview(
        client, case, conclusion_code=conclusion_code,
        bronchodilator_code="BD_NAO_REALIZADO",
    ).json()
    released = _release(client, case, preview)
    assert released.status_code == 200, released.text
    return released.json()


def test_codigo_real_e_liberado_confirma_sem_dado_de_paciente(portal, client, case, person):
    liberado = _liberar(client, case)
    codigo = liberado["validation_code"]

    resposta = portal.get(f"/p/v1/verificar/{codigo}")
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()

    assert corpo["laudo"] == liberado["public_code"]
    assert corpo["codigo_verificacao"] == codigo
    assert corpo["liberado_em"] is not None
    assert corpo["medica_nome"]
    assert corpo["medica_crm"]
    assert corpo["documento_sha256"]
    assert corpo["instituicao"] == "SoproLife Diagnósticos e Soluções em Saúde"

    # Nada de identidade de paciente nem de exame na resposta.
    bruto = resposta.text
    assert person["nome_completo"] not in bruto
    assert case["exam"]["public_code"] not in bruto


def test_codigo_inexistente_da_a_mesma_mensagem_generica(portal):
    resposta = portal.get("/p/v1/verificar/AAAAAAAAAAAA")
    assert resposta.status_code == 404
    corpo = resposta.json()
    assert corpo["erro"]["codigo"] == "laudo_nao_localizado"


def test_codigo_de_laudo_ainda_nao_liberado_da_a_mesma_mensagem(portal, client, case):
    """Prévia/em elaboração não tem `validation_code` (só existe na
    liberação) — mas o formato precisa continuar recusado do mesmo jeito
    genérico, não vazando "existe mas não terminou"."""

    _preview(client, case, conclusion_code="NORMAL", bronchodilator_code="BD_NAO_REALIZADO")
    resposta = portal.get("/p/v1/verificar/BBBBBBBBBBBB")
    assert resposta.status_code == 404
    assert resposta.json()["erro"]["codigo"] == "laudo_nao_localizado"


def test_formato_de_codigo_invalido_e_recusado_sem_consultar_banco(portal):
    for ruim in ("curto", "com espaço aqui", "minusculo123", "!!!!!!!!"):
        resposta = portal.get(f"/p/v1/verificar/{ruim}")
        assert resposta.status_code in (404, 422), (ruim, resposta.text)


def test_portal_desligado_recusa_a_verificacao(portal, client, case, monkeypatch):
    from app.config import get_settings

    liberado = _liberar(client, case)
    monkeypatch.setenv("M15_PORTAL_ENABLED", "false")
    get_settings.cache_clear()
    try:
        resposta = portal.get(f"/p/v1/verificar/{liberado['validation_code']}")
        assert resposta.status_code == 503
    finally:
        monkeypatch.setenv("M15_PORTAL_ENABLED", "true")
        get_settings.cache_clear()
