"""M26.16 — um laudo SUPERADO por corretiva não pode continuar parecendo
elegível para assinatura externa, nem aparecer em "Meus laudos" como se
ainda precisasse de ação, nem contaminar a leitura de assinatura/entrega/
conclusão do repasse médico quando a cadeia de correção tem mais de um
salto.

Caso real que motivou a missão: Claudia de Vasconcelos Almeida, ESP-000050.
LAU-000035 (raiz, conteúdo clínico errado) → LAU-000036 (corretiva de
conteúdo, M26.12) → LAU-000038 (corretiva do PDF técnico da corretiva
anterior, M26.14/M26.15). LAU-000035 e LAU-000036 continuavam aparecendo em
"Meus laudos" como "Concluído — aguardando assinatura qualificada" e
LAU-000036 aparecia selecionável em "Assinatura externa — aguardando
assinatura qualificada", como se ainda precisassem de assinatura.

Reaproveita os helpers já provados em `test_m26_13_laudos_efetivos_
producao.py` via import dinâmico — dirige o fluxo pela API real.
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


_M26_13 = _load("test_m26_13_laudos_efetivos_producao.py")
_M25_2 = _load("test_m25_2_native_report.py")
_make_case = _M25_2._make_case
_liberar = _M26_13._liberar
_abrir_corretiva = _M26_13._abrir_corretiva
_assinar_externamente = _M26_13._assinar_externamente
_producao = _M26_13._producao
_competencia_de = _M26_13._competencia_de


@pytest.fixture(autouse=True)
def reports_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv(
        "M15_AUTH_SECRET", "m26-16-secret-de-teste-0123456789abcdef0123"
    )
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def case(client, auth, db, person):
    return _make_case(client, auth, db, person, com_bd=True)


def _minha_fila(client, doctor_auth):
    resposta = client.get("/api/v1/laudos/meus", headers=doctor_auth)
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def _pendentes_assinatura(client, doctor_auth):
    resposta = client.get(
        "/api/v1/laudos/assinatura-externa/pendentes", headers=doctor_auth
    )
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def _abrir_cadeia_de_dois_saltos(client, case):
    """Reproduz o caso real: raiz liberada → corretiva1 liberada → corretiva2
    aberta (ainda não liberada). Devolve os três ids de documento."""

    raiz = case["document"]["id"]
    _liberar(client, case)
    corretiva1 = _abrir_corretiva(client, case)["id"]
    caso1 = {**case, "document": {"id": corretiva1}}
    _liberar(client, caso1)
    corretiva2 = _abrir_corretiva(client, caso1, reason_code="technical_document_correction")["id"]
    return raiz, corretiva1, corretiva2


# ------------------------------------------------------- "Meus laudos"


def test_documento_superado_aparece_marcado_em_meus_laudos(client, case):
    document_id = case["document"]["id"]
    _liberar(client, case)
    _abrir_corretiva(client, case)

    fila = _minha_fila(client, case["doctor_auth"])
    alvo = next(item for item in fila if item["document_id"] == document_id)
    assert alvo["has_corrective_successor"] is True, (
        "o laudo já superado por uma corretiva precisa vir marcado — sem "
        "isso a tela mostra 'aguardando assinatura qualificada' como se "
        "ainda precisasse de ação"
    )
    assert alvo["is_delivered"] is False


def test_documento_terminal_da_cadeia_nao_vem_marcado_como_superado(
    client, case
):
    raiz, corretiva1, corretiva2 = _abrir_cadeia_de_dois_saltos(client, case)
    fila = _minha_fila(client, case["doctor_auth"])
    por_id = {item["document_id"]: item for item in fila}

    assert por_id[raiz]["has_corrective_successor"] is True
    assert por_id[corretiva1]["has_corrective_successor"] is True, (
        "corretiva1 já foi superada pela corretiva2 (cadeia de 2 saltos) — "
        "precisa vir marcada também, não só a raiz"
    )
    assert por_id[corretiva2]["has_corrective_successor"] is False, (
        "corretiva2 é o fim da cadeia: ninguém a corrigiu, não pode vir "
        "marcada como superada"
    )


# --------------------------------------------- assinatura externa (real risco)


def test_documento_superado_some_da_fila_de_assinatura_externa(client, case):
    document_id = case["document"]["id"]
    _liberar(client, case)

    antes = _pendentes_assinatura(client, case["doctor_auth"])
    assert any(l["document_id"] == document_id for l in antes["laudos"]), (
        "antes de corrigir, o laudo liberado precisa estar elegível para "
        "assinatura externa"
    )

    _abrir_corretiva(client, case)

    depois = _pendentes_assinatura(client, case["doctor_auth"])
    assert not any(l["document_id"] == document_id for l in depois["laudos"]), (
        "um laudo já SUPERADO por corretiva não pode continuar aparecendo "
        "como pronto para assinatura externa — risco real de assinar e "
        "entregar ao paciente o documento errado"
    )


def test_download_para_assinatura_recusa_documento_superado_mesmo_pedido_direto(
    client, case
):
    """O servidor decide o que é elegível; o pedido só pode ESTREITAR.

    Mesmo que o navegador (ou alguém manipulando a chamada) peça
    explicitamente o id do documento já superado, o download não pode
    devolvê-lo: sem nenhum id elegível sobrando, a resposta é o erro de
    lote vazio, nunca o PDF do documento errado.
    """

    document_id = case["document"]["id"]
    _liberar(client, case)
    _abrir_corretiva(client, case)

    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [document_id]},
        headers=case["doctor_auth"],
    )
    assert resposta.status_code == 409, resposta.text
    assert resposta.json()["erro"]["codigo"] == "lote_vazio"


def test_cadeia_de_dois_saltos_so_libera_o_documento_terminal_para_assinatura(
    client, case
):
    raiz, corretiva1, corretiva2 = _abrir_cadeia_de_dois_saltos(client, case)
    caso2 = {**case, "document": {"id": corretiva2}}
    _liberar(client, caso2)

    pendentes = {
        l["document_id"]
        for l in _pendentes_assinatura(client, case["doctor_auth"])["laudos"]
    }
    assert pendentes == {corretiva2}, (
        "só o documento TERMINAL da cadeia (corretiva2, liberado e nunca "
        "corrigido por ninguém) pode estar elegível para assinatura "
        "externa — raiz e corretiva1 já foram superados"
    )


# --------------------------------------------- produção da médica (repasse)


def test_producao_le_assinatura_do_documento_terminal_em_cadeia_de_dois_saltos(
    client, auth, case
):
    """Reproduz o caso real de Claudia: 2 saltos de correção, e o
    documento assinado/entregue é o TERMINAL (LAU-000038), não o do meio
    (LAU-000036). Antes da M26.16, `vigente_by_root` resolvia só 1 salto e
    lia a assinatura do documento ERRADO (o do meio, já superado) — o que
    faria a produção mostrar "assinados"/"entregues" zerados mesmo com o
    laudo real já assinado e entregue.
    """

    raiz = case["document"]["id"]
    liberado_raiz = _liberar(client, case)
    competencia = _competencia_de(liberado_raiz["released_at"])

    corretiva1 = _abrir_corretiva(client, case)["id"]
    caso1 = {**case, "document": {"id": corretiva1}}
    _liberar(client, caso1)

    corretiva2 = _abrir_corretiva(
        client, caso1, reason_code="technical_document_correction"
    )["id"]
    caso2 = {**case, "document": {"id": corretiva2}}
    _liberar(client, caso2)

    _assinar_externamente(client, corretiva2, case["doctor_auth"])

    producao = _producao(client, auth, case["profile"]["id"], competencia)
    assert producao["efetivos"] == 1, "1 exame corrigido duas vezes continua sendo 1 efetivo"
    assert producao["corrigidos"] == 1
    assert producao["assinados"] == 1, (
        "a assinatura do documento TERMINAL da cadeia precisa contar — "
        "sem o fix, a leitura ficava presa na corretiva do meio (não "
        "assinada) e mostrava 0"
    )
    assert producao["aguardando_assinatura"] == 0
