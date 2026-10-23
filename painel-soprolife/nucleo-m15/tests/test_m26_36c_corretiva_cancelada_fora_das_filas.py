"""M26.36C — a corretiva cuja devolução foi cancelada sai das filas ATIVAS.

O QUE FALTOU NA M26.36B (e este arquivo tranca)

A reversão da devolução tem duas faces, e só uma estava implementada:

  * o PREDECESSOR volta à fila — a corretiva cancelada deixa de superá-lo.
    Isso a M26.36 já fazia, nas quatro subconsultas de sucessor.
  * a CORRETIVA sai da fila — ela não é mais trabalho a fazer.
    Isso valia na fila operacional (`/laudos`) e em "Meus laudos", mas NÃO
    na fila de entrega (`/laudos/assinatura-externa/fila`), que é a seção
    "Fila de laudos" do painel, com os chips contadores.

O efeito real, visto em produção: o laudo aberto por engano continuava na
tela como "AGUARDANDO LAUDO", e o contador do chip o somava. Uma pendência
que ninguém podia resolver — laudar um documento cancelado não entrega nada
— em cima de um exame cujo laudo legítimo já tinha sido assinado e entregue.

A REGRA DE PRODUTO que este arquivo verifica, item por item:

uma corretiva com `devolucao_cancelada_em` preenchido
  * permanece integralmente no histórico e na auditoria;
  * é consultável pelo recorte de superados;
  * NÃO aparece em nenhuma fila ativa;
  * NÃO soma em nenhum contador ativo;
  * NÃO é oferecida à médica como laudo a fazer;
  * e não contamina um SEGUNDO exame, que continua pendente normalmente.

Tudo sintético: pacientes "TESTE APAGAR", médicas de teste, PDFs gerados na
hora. Nenhum dado real de paciente.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest
from sqlalchemy import select

from app.models import ReportDocument


def _load(nome: str):
    caminho = pathlib.Path(__file__).with_name(nome)
    spec = importlib.util.spec_from_file_location(f"_{nome[:-3]}", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


_M2636 = _load("test_m26_36_cancelar_devolucao_e_reteste.py")

ambiente = _M2636.ambiente
_soltar = _M2636._soltar
_devolver = _M2636._devolver
_cancelar = _M2636._cancelar
_montar_caso = _M2636._montar_caso
_caso_em_elaboracao = _M2636._caso_em_elaboracao


@pytest.fixture()
def entregue(client, auth, db):
    """O laudo legítimo: concluído, assinado por fora e ENTREGUE."""

    return _montar_caso(
        client, auth, db, nome="TESTE APAGAR Paciente M26.36C", suffix="71"
    )


@pytest.fixture()
def pendente(client, auth, db):
    """Um SEGUNDO exame, de outro paciente, legitimamente aguardando laudo.

    Existe para provar o outro lado da correção: o recorte novo tira da
    fila exatamente a corretiva cancelada, e nada além dela.
    """

    return _caso_em_elaboracao(
        client, auth, db, nome_paciente="TESTE APAGAR Paciente Pendente", suffix="72"
    )


# ----------------------------------------------------------------- leituras

def _fila_entrega(client, auth, **params):
    resposta = client.get(
        "/api/v1/laudos/assinatura-externa/fila",
        params=params,
        headers=auth("operacional"),
    )
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def _ids(fila) -> set[str]:
    return {item["document_id"] for item in fila["itens"]}


def _contador(fila, chave: str) -> int:
    return next(e["total"] for e in fila["estados"] if e["chave"] == chave)


def _fila_operacional(client, auth, **params) -> list[dict]:
    resposta = client.get(
        "/api/v1/laudos", params=params, headers=auth("operacional")
    )
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


# ======================================================================
# O percurso completo do incidente, em uma leitura
# ======================================================================


def test_corretiva_cancelada_sai_da_fila_de_entrega_e_do_contador(
    client, auth, db, entregue, pendente
):
    document_id = entregue["document_id"]
    outro_id = pendente["document_id"]

    # --- 1. antes do clique: o laudo está entregue, nada aguardando laudo
    #        além do segundo exame, que é pendência legítima.
    _soltar(db)
    antes = _fila_entrega(client, auth)
    assert _ids(antes) >= {document_id, outro_id}
    # O estado legítimo do laudo não é fixado aqui: o que a reversão promete
    # é devolver EXATAMENTE o que havia antes do clique, seja ele qual for.
    estado_legitimo = next(
        i["estado"] for i in antes["itens"] if i["document_id"] == document_id
    )
    assert estado_legitimo != "aguardando_laudo"
    aguardando_antes = _contador(antes, "aguardando_laudo")
    assert outro_id in {
        i["document_id"] for i in antes["itens"] if i["estado"] == "aguardando_laudo"
    }

    # --- 2. o clique equivocado: nasce a corretiva vazia.
    corretiva_id = _devolver(client, auth, document_id, db)["id"]

    _soltar(db)
    durante = _fila_entrega(client, auth)
    assert corretiva_id in _ids(durante)
    assert _contador(durante, "aguardando_laudo") == aguardando_antes + 1
    # e o predecessor sai, porque passou a ter sucessora viva
    assert document_id not in _ids(durante)

    # --- 3. a reversão.
    assert _cancelar(client, auth, corretiva_id, db=db).status_code == 200

    _soltar(db)
    depois = _fila_entrega(client, auth)

    # A corretiva cancelada sumiu da fila de entrega...
    assert corretiva_id not in _ids(depois)
    # ...e do contador, que voltou exatamente ao valor de antes do clique.
    assert _contador(depois, "aguardando_laudo") == aguardando_antes
    # O predecessor voltou, no estado legítimo em que estava.
    assert next(
        i["estado"] for i in depois["itens"] if i["document_id"] == document_id
    ) == estado_legitimo
    assert _contador(depois, estado_legitimo) == _contador(antes, estado_legitimo)
    # E o segundo exame continua pendente, intocado.
    assert outro_id in {
        i["document_id"] for i in depois["itens"] if i["estado"] == "aguardando_laudo"
    }


def test_corretiva_cancelada_continua_consultavel_para_auditoria(
    client, auth, db, entregue
):
    """Sair da fila não é sumir: o recorte de superados a traz de volta."""

    document_id = entregue["document_id"]
    corretiva_id = _devolver(client, auth, document_id, db)["id"]
    assert _cancelar(client, auth, corretiva_id, db=db).status_code == 200

    _soltar(db)
    auditoria = _fila_entrega(client, auth, incluir_superados="true")
    assert corretiva_id in _ids(auditoria)

    # E o documento continua inteiro no banco, com o carimbo do cancelamento.
    db.expire_all()
    cancelada = db.get(ReportDocument, corretiva_id)
    assert cancelada is not None
    assert cancelada.devolucao_cancelada_em is not None


def test_corretiva_cancelada_fora_da_fila_operacional_e_dentro_do_historico(
    client, auth, db, entregue
):
    """`/laudos` (Acompanhamento operacional) e o seu histórico recolhido."""

    document_id = entregue["document_id"]
    corretiva_id = _devolver(client, auth, document_id, db)["id"]
    assert _cancelar(client, auth, corretiva_id, db=db).status_code == 200

    _soltar(db)
    ativa = _fila_operacional(client, auth)
    assert corretiva_id not in {linha["document_id"] for linha in ativa}
    # E a outra face da mesma reversão: o predecessor, que a corretiva viva
    # havia superado, volta à fila ativa.
    assert document_id in {linha["document_id"] for linha in ativa}

    historico = _fila_operacional(client, auth, somente_superados="true")
    linha = next(
        l for l in historico if l["document_id"] == corretiva_id
    )
    # M26.36C — o carimbo vai no payload para a tela poder NOMEAR a linha.
    # Sem ele o painel caía no `else` e dizia "Entregue ao paciente" sobre
    # um laudo que nunca foi escrito.
    assert linha["devolucao_cancelada_em"] is not None
    assert linha["is_delivered"] is False
    assert linha["has_corrective_successor"] is False


def test_corretiva_cancelada_nao_e_oferecida_a_medica(
    client, auth, db, entregue
):
    """A fila da própria médica não pode pedir um laudo que foi cancelado."""

    document_id = entregue["document_id"]
    doctor_auth = entregue["doctor_auth"]
    corretiva_id = _devolver(client, auth, document_id, db)["id"]

    _soltar(db)
    minha = client.get("/api/v1/laudos/meus", headers=doctor_auth)
    assert minha.status_code == 200, minha.text
    assert corretiva_id in {l["document_id"] for l in minha.json()}

    assert _cancelar(client, auth, corretiva_id, db=db).status_code == 200

    _soltar(db)
    minha = client.get("/api/v1/laudos/meus", headers=doctor_auth)
    assert minha.status_code == 200, minha.text
    assert corretiva_id not in {l["document_id"] for l in minha.json()}

    # Nem no histórico DELA: o cancelamento encerrou a atribuição, e
    # "Meus laudos" — nos dois recortes — é filtrado por atribuição ativa.
    # O documento não some: ele vive no histórico OPERACIONAL e na trilha
    # de auditoria, que é onde um laudo que nunca foi dela pertence.
    historico = client.get(
        "/api/v1/laudos/meus", params={"somente_superados": "true"},
        headers=doctor_auth,
    )
    assert historico.status_code == 200, historico.text
    assert corretiva_id not in {l["document_id"] for l in historico.json()}


def test_corretiva_cancelada_nao_conta_como_laudo_nas_estatisticas(
    client, auth, db, entregue
):
    """Um documento aberto por engano e revertido não é produção."""

    document_id = entregue["document_id"]

    _soltar(db)
    antes = client.get("/api/v1/laudos/estatisticas", headers=auth("admin"))
    assert antes.status_code == 200, antes.text
    total_antes = antes.json()["totais"]["laudos"]

    corretiva_id = _devolver(client, auth, document_id, db)["id"]
    assert _cancelar(client, auth, corretiva_id, db=db).status_code == 200

    _soltar(db)
    depois = client.get("/api/v1/laudos/estatisticas", headers=auth("admin"))
    assert depois.status_code == 200, depois.text
    assert depois.json()["totais"]["laudos"] == total_antes
