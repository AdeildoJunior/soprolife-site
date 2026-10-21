"""M26.35 — a NFS-e agrupada sai pelo MESMO caminho de emissão da individual.

Não existe rota nova de emissão: o documento agrupado usa
`GET /producao/documentos/{id}/confirmacao` e
`POST /producao/documentos/{id}/confirmar-emissao`, que já eram genéricos por
documento, e o mesmo executor one-shot. Estes testes provam que isso vale de
ponta a ponta até a campainha do spool — e param ali: nenhum teste chama o
worker para enviar, nenhum provedor existe, o contador de POST fica em zero.

Tudo sintético (as fixtures vêm de `test_m26_34_nfse_agrupada`).
"""

import importlib.util
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.models import (AuditLog, FiscalAttempt, FiscalDocument, FiscalDocumentItem,
                        FiscalIssuanceRequest, Person)
from app.services import nfse
from app.services.nfse_production_issuance import recipient_fingerprint

from .test_m26_34_nfse_agrupada import (API, RIO, SERVICE_DAY, _count, _pagadora,  # noqa: F401
                                        _preparar, _solta, mundo, prod_env,
                                        prod_env_worker)


def _resumo(client, auth, doc_id):
    resposta = client.get(f"{API}/producao/documentos/{doc_id}/confirmacao",
                          headers=auth("gestor"))
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def _confirmar(client, auth, resumo, *, chave="m2635-chave-0001", role="gestor", **troca):
    corpo = {"preparation_id": resumo["preparation_id"], "amount": resumo["amount"],
             "confirmation": resumo["confirmation_phrase"], "idempotency_key": chave}
    corpo.update(troca)
    return client.post(f"{API}/producao/documentos/{resumo['document_id']}/confirmar-emissao",
                       json=corpo, headers=auth(role))


def _codigo(resposta):
    """O envelope de erro da API: {"erro": {"mensagem": {"codigo": ...}}}."""
    return resposta.json()["erro"]["mensagem"]["codigo"]


def _spool(settings):
    return sorted(p.name for p in Path(settings.nfse_production_worker_spool_dir).iterdir())


def test_o_agrupado_e_confirmado_pela_mesma_rota_e_toca_a_campainha_uma_vez(
    prod_env_worker, client, auth, db, mundo
):
    doc_id = _preparar(client, auth, mundo, db=db).json()["id"]
    resumo = _resumo(client, auth, doc_id)
    assert resumo["kind"] == "grouped" and resumo["can_confirm"] is True
    assert resumo["confirmation_phrase"] == "Confirmar emissão de R$ 500,00"

    resposta = _confirmar(client, auth, resumo)
    assert resposta.status_code == 202, resposta.text
    pedido = resposta.json()
    assert pedido["status"] == "authorized" and pedido["kind"] == "issue"
    assert pedido["document_id"] == doc_id
    assert pedido["amount_confirmed"] == "500.00"
    assert _spool(prod_env_worker) == [f"{pedido['id']}.issue"]

    _solta(db)
    linha = db.get(FiscalIssuanceRequest, pedido["id"])
    # Agrupado não tem "o" exame: o pedido também não elege um.
    assert linha.spirometry_exam_id is None
    # O tomador do pedido é a pagadora, nunca o paciente de um item.
    pagadora = db.get(Person, _pagadora(mundo))
    assert linha.recipient_fingerprint == recipient_fingerprint(pagadora)
    for label in ("a", "b"):
        paciente = db.get(Person, mundo["exames"][label].person_id)
        assert linha.recipient_fingerprint != recipient_fingerprint(paciente)
    postados = linha.provider_post_count
    # Nada atravessou a fronteira: só o pedido e a campainha.
    assert postados == 0
    assert _count(db, FiscalAttempt) == 0


def test_duplo_clique_nao_gera_dois_pedidos(prod_env_worker, client, auth, db, mundo):
    doc_id = _preparar(client, auth, mundo, db=db).json()["id"]
    resumo = _resumo(client, auth, doc_id)
    primeiro = _confirmar(client, auth, resumo).json()
    # Mesma chave (o mesmo clique repetido): devolve o mesmo pedido.
    replay = _confirmar(client, auth, resumo)
    assert replay.status_code == 202 and replay.json()["id"] == primeiro["id"]
    # Outra chave com um pedido vivo: recusado.
    outro = _confirmar(client, auth, resumo, chave="m2635-chave-0002", role="admin")
    assert outro.status_code == 409
    assert _codigo(outro) == "issuance_request_already_active"
    assert _count(db, FiscalIssuanceRequest) == 1
    assert _spool(prod_env_worker) == [f"{primeiro['id']}.issue"]


def test_valor_ou_frase_diferentes_nao_registram_nada(
    prod_env_worker, client, auth, db, mundo
):
    doc_id = _preparar(client, auth, mundo, db=db).json()["id"]
    resumo = _resumo(client, auth, doc_id)
    for troca, codigo in (({"amount": "250.00"}, "confirmation_amount_mismatch"),
                          ({"confirmation": "Confirmar emissão de R$ 250,00"},
                           "confirmation_phrase_mismatch"),
                          ({"preparation_id": "00000000-0000-0000-0000-000000000000"},
                           "confirmation_stale")):
        resposta = _confirmar(client, auth, resumo, **troca)
        assert resposta.status_code in (409, 422), resposta.text
        assert _codigo(resposta) == codigo
    assert _count(db, FiscalIssuanceRequest) == 0
    assert _spool(prod_env_worker) == []


def test_a_emissao_preserva_os_itens_e_o_total(prod_env_worker, client, auth, db, mundo):
    doc_id = _preparar(client, auth, mundo, db=db).json()["id"]
    _confirmar(client, auth, _resumo(client, auth, doc_id))
    _solta(db)
    itens = db.scalars(select(FiscalDocumentItem).where(
        FiscalDocumentItem.document_id == doc_id).order_by(FiscalDocumentItem.position)).all()
    assert [i.spirometry_exam_id for i in itens] == [mundo["exames"]["a"].id,
                                                     mundo["exames"]["b"].id]
    assert sum(i.amount for i in itens) == Decimal("500.00")
    assert nfse.latest_preparation(db, doc_id).amount_snapshot == Decimal("500.00")
    # Um documento só, sem documento individual para nenhum dos exames.
    assert _count(db, FiscalDocument) == 1
    assert _count(db, FiscalDocument, FiscalDocument.spirometry_exam_id.in_(
        [mundo["exames"]["a"].id, mundo["exames"]["b"].id])) == 0


def test_item_do_agrupado_nao_pode_ser_emitido_individualmente(
    prod_env_worker, client, auth, db, mundo
):
    _preparar(client, auth, mundo, db=db)
    resposta = client.post(f"{API}/producao/exames/{mundo['exames']['a'].id}/preparar",
                           headers=auth("gestor"))
    assert resposta.status_code == 409, resposta.text
    assert _count(db, FiscalDocument) == 1
    assert _count(db, FiscalIssuanceRequest) == 0
    assert _spool(prod_env_worker) == []


def test_o_worker_aceita_o_pedido_que_a_tela_gravou(
    prod_env_worker, client, auth, db, mundo
):
    """Os fatos que o worker reivindica são os do PEDIDO gravado pela rota.
    A única recusa possível aqui é ambiental (não há A1 nesta máquina)."""
    doc_id = _preparar(client, auth, mundo, db=db).json()["id"]
    pedido = _confirmar(client, auth, _resumo(client, auth, doc_id)).json()
    _solta(db)
    linha = db.get(FiscalIssuanceRequest, pedido["id"])
    facts = {"id": linha.id, "document_id": linha.document_id, "kind": linha.kind,
             "exam_id": linha.spirometry_exam_id, "preparation_id": linha.preparation_id,
             "preparation_fingerprint": linha.preparation_fingerprint,
             "recipient_fingerprint": linha.recipient_fingerprint,
             "amount": Decimal(linha.amount_confirmed), "actor": linha.authorized_by}
    spec = importlib.util.spec_from_file_location(
        "m2635_worker", Path(__file__).parents[1] / "scripts" / "nfse_production_worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    falhas, esperado = worker.guards(db, facts, prod_env_worker)
    assert falhas == ["certificate_margin_insufficient"], falhas
    assert esperado == {"tpAmb": "1", "dCompet": SERVICE_DAY.isoformat(),
                        "cLocPrestacao": RIO, "vServ": "500.00"}


def test_o_individual_continua_saindo_pela_mesma_rota(
    prod_env_worker, client, auth, db, mundo
):
    resumo = client.post(f"{API}/producao/exames/{mundo['exames']['c'].id}/preparar",
                         headers=auth("gestor")).json()
    assert resumo["kind"] == "single" and resumo["can_confirm"] is True
    resposta = _confirmar(client, auth, resumo, chave="m2635-individual")
    assert resposta.status_code == 202, resposta.text
    _solta(db)
    linha = db.get(FiscalIssuanceRequest, resposta.json()["id"])
    assert linha.spirometry_exam_id == mundo["exames"]["c"].id
    assert linha.amount_confirmed == Decimal("250.00")


def test_a_trilha_do_pedido_agrupado_nao_tem_pii(prod_env_worker, client, auth, db, mundo):
    doc_id = _preparar(client, auth, mundo, db=db).json()["id"]
    _confirmar(client, auth, _resumo(client, auth, doc_id))
    _solta(db)
    linha = db.scalars(select(AuditLog).where(
        AuditLog.acao == "fiscal.production_issue_authorized")).one()
    assert linha.detalhes.get("itens") == 2
    assert linha.detalhes.get("valor_documentado") == "500.00"
    texto = f"{linha.detalhes}"
    for pessoa in mundo["pessoas"].values():
        assert pessoa.nome_completo not in texto
        if pessoa.cpf:
            assert pessoa.cpf not in texto
