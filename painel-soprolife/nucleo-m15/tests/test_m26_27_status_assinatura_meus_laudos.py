"""M26.27 — "Meus laudos" dizia "aguardando assinatura qualificada" depois
de o PDF assinado ter sido recebido e aceito.

Causa: `report_documents.status` fica `liberado` para sempre depois da
conclusão — receber o PDF assinado não o altera, por desenho. A central de
assinatura exclui pelo `ExternalSignedDocument` e a fila de entrega deriva
o estado por `_estado_de_entrega`; "Meus laudos", a bancada e o
acompanhamento só tinham `status`, e o navegador rotulava `liberado` como
"Concluído — aguardando assinatura qualificada".

A correção não cria regra: as linhas passam a carregar `estado_entrega`,
derivado pela MESMA função da fila de entrega. Aqui se prova que as três
telas contam a mesma história, sem mexer em nenhum dado gravado.
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


@pytest.fixture(autouse=True)
def reports_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv(
        "M15_AUTH_SECRET", "m26-27-secret-de-teste-0123456789abcdef0123"
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


def _linha_meus(client, case, **params):
    fila = _get(client, "/api/v1/laudos/meus", case["doctor_auth"], **params)
    alvo = [i for i in fila if i["document_id"] == case["document"]["id"]]
    return alvo[0] if alvo else None


def _pendentes(client, case):
    return _get(
        client,
        "/api/v1/laudos/assinatura-externa/pendentes",
        case["doctor_auth"],
    )


def _linha_entrega(client, auth, document_id, **params):
    fila = _get(
        client,
        "/api/v1/laudos/assinatura-externa/fila",
        auth("operacional"),
        **params,
    )
    alvo = [i for i in fila["itens"] if i["document_id"] == document_id]
    return (alvo[0] if alvo else None), fila


def _ids_pendentes(pendentes):
    return {item["document_id"] for item in pendentes["laudos"]}


# ------------------------------------------------ Cenário A — antes do PDF


def test_a_concluido_sem_pdf_assinado_esta_aguardando_em_todas_as_telas(
    client, auth, case
):
    _liberar(client, case)
    document_id = case["document"]["id"]

    assert document_id in _ids_pendentes(_pendentes(client, case))

    linha = _linha_meus(client, case)
    assert linha is not None
    assert linha["status"] == "liberado"
    assert linha["estado_entrega"] == "aguardando_assinatura"
    assert linha["estado_entrega_rotulo"] == "Aguardando assinatura"

    entrega, _ = _linha_entrega(client, auth, document_id)
    assert entrega["estado"] == linha["estado_entrega"]


# ------------------------------------------ Cenário B — PDF assinado aceito


def test_b_pdf_assinado_aceito_tira_o_aguardando_das_tres_telas(
    client, auth, case
):
    _liberar(client, case)
    document_id = case["document"]["id"]
    _assinar_externamente(client, document_id, case["doctor_auth"])

    pendentes = _pendentes(client, case)
    assert document_id not in _ids_pendentes(pendentes)
    assert pendentes["total"] == 0

    linha = _linha_meus(client, case)
    assert linha is not None, (
        "assinado mas não entregue continua na fila ativa da médica"
    )
    # O dado gravado não muda — é isso que exige a derivação.
    assert linha["status"] == "liberado"
    assert linha["estado_entrega"] == "pronto_para_entrega", (
        "com o PDF assinado aceito, 'Meus laudos' não pode continuar "
        "derivando 'aguardando assinatura'"
    )
    assert linha["estado_entrega_rotulo"] == "Pronto para entrega"

    entrega, _ = _linha_entrega(client, auth, document_id)
    assert entrega["estado"] == "pronto_para_entrega"
    assert entrega["estado_rotulo"] == linha["estado_entrega_rotulo"]

    # Bancada (detalhe da médica) e acompanhamento operacional: mesma regra.
    detalhe = _get(client, f"/api/v1/laudos/{document_id}", case["doctor_auth"])
    assert detalhe["estado_entrega"] == "pronto_para_entrega"
    operacional = _get(client, "/api/v1/laudos", auth("operacional"))
    alvo = next(i for i in operacional if i["document_id"] == document_id)
    assert alvo["estado_entrega"] == "pronto_para_entrega"
    tecnico = _get(client, f"/api/v1/laudos/{document_id}", auth("operacional"))
    assert tecnico["estado_entrega"] == "pronto_para_entrega"


# ------------------------------- Cenário C — pendente real continua pendente


def test_c_arquivo_recusado_nao_conta_como_assinatura(client, auth, case):
    """Devolver o PDF final SEM assinar é recusado (M25.29G): o laudo
    continua aguardando, em todas as telas."""

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

    assert document_id in _ids_pendentes(_pendentes(client, case))
    linha = _linha_meus(client, case)
    assert linha["estado_entrega"] == "aguardando_assinatura"
    entrega, _ = _linha_entrega(client, auth, document_id)
    assert entrega["estado"] == "aguardando_assinatura"


def test_c_laudo_nao_concluido_nao_diz_aguardando_assinatura(client, case):
    linha = _linha_meus(client, case)
    assert linha["status"] != "liberado"
    assert linha["estado_entrega"] == "aguardando_laudo"


# ----------------------------------------------- Cenário D — histórico intacto


def test_d_historico_do_documento_preservado(client, case, db):
    from sqlalchemy import select

    from app.models import AuditLog

    _liberar(client, case)
    document_id = case["document"]["id"]
    _assinar_externamente(client, document_id, case["doctor_auth"])

    detalhe = _get(client, f"/api/v1/laudos/{document_id}", case["doctor_auth"])
    kinds = [v["kind"] for v in detalhe["versoes"]]
    assert "laudo_liberado" in kinds
    assert "laudo_assinado_externo_recebido" in kinds
    assert detalhe["status"] == "liberado"

    acoes = set(
        db.execute(
            select(AuditLog.acao).where(AuditLog.entidade_id == document_id)
        ).scalars()
    )
    assert "laudo_assinado_e_liberado" in acoes
    assert "laudo_assinado_aceito_automaticamente" in acoes


# ------------------------------------- Cenário F — superado não reaparece


def test_f_superado_por_corretiva_nao_reaparece(client, auth, case):
    _liberar(client, case)
    raiz = case["document"]["id"]
    _assinar_externamente(client, raiz, case["doctor_auth"])
    corretiva = _abrir_corretiva(client, case)["id"]

    assert _linha_meus(client, case) is None
    assert raiz not in _ids_pendentes(_pendentes(client, case))
    entrega, _ = _linha_entrega(client, auth, raiz)
    assert entrega is None

    fila = _get(client, "/api/v1/laudos/meus", case["doctor_auth"])
    nova = next(i for i in fila if i["document_id"] == corretiva)
    assert nova["estado_entrega"] == "aguardando_laudo"


# -------------------------------------- as três filas contam a mesma história


def test_mesma_regra_da_fila_de_entrega(client, auth, case, db):
    """Nada de quarta regra: o campo das linhas É `_estado_de_entrega`."""

    from app.models import ReportDocument
    from app.routers import reports

    _liberar(client, case)
    document_id = case["document"]["id"]
    _assinar_externamente(client, document_id, case["doctor_auth"])
    documento = db.get(ReportDocument, document_id)
    esperado = reports._estado_de_entrega(
        documento, reports._assinado_mais_recente(db, document_id)
    )
    db.commit()
    assert _linha_meus(client, case)["estado_entrega"] == esperado
