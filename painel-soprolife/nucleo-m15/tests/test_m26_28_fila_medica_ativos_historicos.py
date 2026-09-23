"""M26.28 — "Meus laudos" mostra só o que ainda exige ação da médica.

Depois da M26.27 o laudo com PDF assinado aceito dizia a verdade
("Pronto para entrega"), mas continuava ocupando a fila ATIVA da médica —
pendência que é da administração. A fila ativa agora pergunta "este laudo
ainda exige alguma ação da médica?" com a MESMA regra que tira o laudo da
central de assinatura (`_tem_assinado_vigente`), e o que terminou vai para
Históricos (`somente_superados=true`). Nenhum dado gravado muda: a
separação é de consulta.
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


_M26_13 = _load("test_m26_13_laudos_efetivos_producao.py")
_M25_2 = _load("test_m25_2_native_report.py")
_make_case = _M25_2._make_case
_liberar = _M26_13._liberar
_abrir_corretiva = _M26_13._abrir_corretiva
_assinar_externamente = _M26_13._assinar_externamente

ATIVOS_DA_MEDICA = {"aguardando_laudo", "aguardando_assinatura"}


@pytest.fixture(autouse=True)
def reports_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv(
        "M15_AUTH_SECRET", "m26-28-secret-de-teste-0123456789abcdef0123"
    )
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def case(client, auth, db, person):
    return _make_case(client, auth, db, person, com_bd=True)


def _get(client, url, headers, **params):
    resposta = client.get(url, params=params or None, headers=headers)
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def _ativos(client, case, **params):
    return _get(client, "/api/v1/laudos/meus", case["doctor_auth"], **params)


def _historicos(client, case):
    return _get(
        client, "/api/v1/laudos/meus", case["doctor_auth"],
        somente_superados="true",
    )


def _ids(linhas):
    return {item["document_id"] for item in linhas}


def _pendentes(client, case):
    return _get(
        client, "/api/v1/laudos/assinatura-externa/pendentes",
        case["doctor_auth"],
    )


def _fila_entrega(client, auth):
    return _get(
        client, "/api/v1/laudos/assinatura-externa/fila", auth("operacional")
    )


def _assinado_id(db, document_id):
    from app.models import ExternalSignedDocument

    valor = db.execute(
        select(ExternalSignedDocument.id).where(
            ExternalSignedDocument.report_document_id == document_id
        )
    ).scalar_one()
    db.commit()
    return valor


def _estado_na_fila(client, auth, document_id):
    for item in _fila_entrega(client, auth)["itens"]:
        if item["document_id"] == document_id:
            return item["estado"]
    return None


# ------------------------------------------------------------ A e B


def test_a_aguardando_laudo_esta_nos_ativos(client, case):
    document_id = case["document"]["id"]
    assert document_id in _ids(_ativos(client, case))
    assert document_id not in _ids(_historicos(client, case))


def test_b_concluido_aguardando_assinatura_esta_nos_ativos(client, case):
    _liberar(client, case)
    document_id = case["document"]["id"]
    ativos = _ativos(client, case)
    assert document_id in _ids(ativos)
    assert document_id not in _ids(_historicos(client, case))
    linha = next(i for i in ativos if i["document_id"] == document_id)
    assert linha["estado_entrega"] == "aguardando_assinatura"


# ------------------------------------------------------------ C


def test_c_pdf_assinado_aceito_sai_dos_ativos_e_vai_para_historicos(
    client, auth, case, db
):
    from app.models import ReportDocument

    _liberar(client, case)
    document_id = case["document"]["id"]
    _assinar_externamente(client, document_id, case["doctor_auth"])

    assert document_id not in _ids(_ativos(client, case))
    historico = _historicos(client, case)
    linha = next(i for i in historico if i["document_id"] == document_id)
    assert linha["estado_entrega"] == "pronto_para_entrega"
    assert linha["estado_entrega_rotulo"] == "Pronto para entrega"
    assert linha["released_at"], "a data de conclusão continua acessível"

    # As outras duas telas não mudam de história.
    assert document_id not in {i["document_id"] for i in _pendentes(client, case)["laudos"]}
    assert _estado_na_fila(client, auth, document_id) == "pronto_para_entrega"

    # Continua consultável pela médica (a atribuição não foi desativada).
    detalhe = _get(client, f"/api/v1/laudos/{document_id}", case["doctor_auth"])
    assert detalhe["status"] == "liberado"
    assert db.get(ReportDocument, document_id).status == "liberado"
    db.commit()


# ------------------------------------------------------------ D


def test_d_entregue_fica_so_em_historicos(client, auth, case, db):
    _liberar(client, case)
    document_id = case["document"]["id"]
    _assinar_externamente(client, document_id, case["doctor_auth"])
    signed_id = _assinado_id(db, document_id)
    entrega = client.post(
        f"/api/v1/laudos/assinatura-externa/{signed_id}/entrega",
        headers=auth("operacional"),
    )
    assert entrega.status_code == 200, entrega.text

    assert document_id not in _ids(_ativos(client, case))
    linha = next(
        i for i in _historicos(client, case) if i["document_id"] == document_id
    )
    assert linha["is_delivered"] is True
    assert linha["estado_entrega"] == "entregue"


# ------------------------------------------------------------ E


def test_e_assinatura_recusada_permanece_ativa(client, auth, case):
    """PDF final devolvido SEM assinar é recusado (M25.29G): a médica
    precisa assinar de novo, então o laudo continua na fila dela."""

    _liberar(client, case)
    document_id = case["document"]["id"]
    baixado = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [document_id]},
        headers=case["doctor_auth"],
    )
    assert baixado.status_code == 200, baixado.text
    envio = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={"arquivos": ("sem-assinatura.pdf", baixado.content, "application/pdf")},
        headers=case["doctor_auth"],
    )
    assert envio.status_code == 200, envio.text
    assert envio.json()["aceitos"] == 0

    assert document_id in _ids(_ativos(client, case))
    assert document_id not in _ids(_historicos(client, case))
    assert document_id in {i["document_id"] for i in _pendentes(client, case)["laudos"]}

    # E depois de assinar de verdade, sai.
    _assinar_externamente(client, document_id, case["doctor_auth"])
    assert document_id not in _ids(_ativos(client, case))
    assert document_id in _ids(_historicos(client, case))


# ------------------------------------------------------------ F


def test_f_superado_nao_reaparece_como_ativo(client, case):
    _liberar(client, case)
    raiz = case["document"]["id"]
    corretiva = _abrir_corretiva(client, case)["id"]

    ativos = _ids(_ativos(client, case))
    assert raiz not in ativos
    assert corretiva in ativos
    assert raiz in _ids(_historicos(client, case))


# ------------------------------------------------------------ H


def test_h_filtro_liberado_so_traz_quem_aguarda_assinatura(client, auth, db, person):
    """`status=liberado` ("Concluídos — aguardando assinatura") atua sobre a
    fila ativa: não pode mais trazer laudo cujo PDF assinado já voltou."""

    assinado = _make_case(client, auth, db, person, com_bd=True)
    _liberar(client, assinado)
    _assinar_externamente(client, assinado["document"]["id"], assinado["doctor_auth"])

    filtrados = _ativos(client, assinado, status="liberado")
    assert assinado["document"]["id"] not in _ids(filtrados)
    assert all(i["estado_entrega"] == "aguardando_assinatura" for i in filtrados)


def _outro_documento(client, auth, person, base):
    """Mais um laudo atribuído à MESMA médica do caso base."""

    exame = _M25_2._create_exam(client, auth, person, com_bd=True)
    enviado = _M25_2._upload(client, auth, exame, base["profile"])
    assert enviado.status_code == 201, enviado.text
    return {**base, "exam": exame, "document": enviado.json()}


def test_ativos_e_historicos_particionam_a_fila_completa(client, auth, db, person):
    """Nada some: ativos ∪ históricos == tudo, sem interseção, e cada lado
    tem o estado canônico que o define."""

    pendente = _make_case(client, auth, db, person, com_bd=True)
    aguardando = _outro_documento(client, auth, person, pendente)
    _liberar(client, aguardando)
    pronto = _outro_documento(client, auth, person, pendente)
    _liberar(client, pronto)
    _assinar_externamente(client, pronto["document"]["id"], pronto["doctor_auth"])

    ativos = _ativos(client, pendente)
    historicos = _historicos(client, pendente)
    todos = _ativos(client, pendente, incluir_superados="true")
    assert _ids(ativos) & _ids(historicos) == set()
    assert _ids(ativos) | _ids(historicos) == _ids(todos)
    assert {pendente["document"]["id"], aguardando["document"]["id"]} <= _ids(ativos)
    assert pronto["document"]["id"] in _ids(historicos)
    for item in ativos:
        assert item["estado_entrega"] in ATIVOS_DA_MEDICA, item
    for item in historicos:
        assert (
            item["has_corrective_successor"]
            or item["estado_entrega"] not in ATIVOS_DA_MEDICA
        ), item


def test_central_e_fila_ativa_usam_a_mesma_pergunta():
    """Nada de regra paralela: as duas consultas usam `_tem_assinado_vigente`."""

    from app.routers import reports as rotas

    fonte = pathlib.Path(rotas.__file__).read_text()
    central = fonte[fonte.index("def _aguardando_assinatura_externa"):][:3000]
    meus = fonte[fonte.index("def list_my_report_queue"):][:5000]
    assert "~_tem_assinado_vigente()" in central
    assert "_tem_assinado_vigente()" in meus
