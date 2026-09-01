"""M26 — exame realizado depois de o mês já ter fechado.

O cenário real que motivou esta etapa (produção, 30/08/2026): o fechamento de
2026-08 foi criado em 11/08 com os 3 exames que existiam, recebeu valor
conferido contra o extrato do parceiro e foi para `a_receber`. Entre 15 e
29/08 a operação realizou mais 14 exames Pastore no mesmo mês. Eles ficaram
permanentemente em "Aguardando fechamento mensal": a competência estava
ocupada pela chave única, e nenhuma rota anexava exame a fechamento existente.

Nada aqui infere preço. O complementar nasce sem valor, exatamente como o
fechamento comum.

M26.3 — o mecanismo do complementar continua idêntico; o que mudou foi o
GATILHO. O exame passou a entrar no fechamento no instante do cadastro, sem
depender de alguém clicar, então os testes exercitam o caminho automático e
cobram os mesmos resultados. O POST manual continua existindo como rota de
recuperação e é testado nessa condição.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import (
    AuditLog,
    FinancialEntry,
    Partner,
    PartnerSettlement,
    PartnerSettlementItem,
    PartnerUnit,
)

API = "/api/v1"


@pytest.fixture()
def pastore(db):
    partner = Partner(
        public_code="CLI-M26001", nome="Pastore", tipo="clinica",
        status="ativa", arquivado=False,
    )
    db.add(partner)
    db.flush()
    unit = PartnerUnit(
        public_code="UNI-M26001", partner_id=partner.id,
        nome="Pastore Ipanema", ativo=True,
    )
    db.add(unit)
    db.commit()
    return partner, unit


def _exame_resposta(client, auth, person, partner, unit, exam_date):
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_pastore",
            "espirometria": {
                "data_exame": exam_date,
                "status": "Realizado",
                "partner_id": partner.id,
                "partner_unit_id": unit.id,
            },
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _exame(client, auth, person, partner, unit, exam_date):
    return _exame_resposta(
        client, auth, person, partner, unit, exam_date
    )["espirometria"]["id"]


def _fechar(client, auth, unit, competencia="2026-08"):
    """A rota de recuperação. Depois da M26.3 ela só acha exame órfão vindo de
    fora da API (importação, correção em banco, unidade desativada)."""
    return client.post(
        f"{API}/pastore/fechamentos",
        json={"partner_unit_id": unit.id, "competencia": competencia},
        headers=auth("gestor"),
    )


def _estado(client, auth):
    return client.get(f"{API}/pastore/fechamentos", headers=auth("leitura")).json()


def _do_mes(client, auth, competencia="2026-08", sequencia=1):
    """O fechamento que o cadastro do exame criou sozinho."""
    for row in _estado(client, auth)["fechamentos"]:
        if row["competencia"] == competencia and row["sequencia"] == sequencia:
            return row
    raise AssertionError(f"sem fechamento {competencia} #{sequencia}")


def _valorar(client, auth, settlement_id, valor):
    return client.patch(
        f"{API}/pastore/fechamentos/{settlement_id}",
        json={"status": "a_receber", "valor_total": valor},
        headers=auth("gestor"),
    )


def _cenario_producao(client, auth, person, pastore):
    """3 exames fechados e valorados; 14 realizados depois, no mesmo mês."""

    partner, unit = pastore
    for data in ("2026-08-01", "2026-08-04", "2026-08-04"):
        _exame(client, auth, person, partner, unit, data)
    # Os 3 já estão num fechamento; ninguém precisou abrir nada (M26.3).
    primeiro = _do_mes(client, auth)
    assert primeiro["itens"]["total"] == 3
    assert _valorar(client, auth, primeiro["id"], "328.50").status_code == 200
    posteriores = [
        _exame(client, auth, person, partner, unit, data)
        for data in (
            ["2026-08-15"] * 5 + ["2026-08-18"] + ["2026-08-22"] * 3
            + ["2026-08-25"] * 4 + ["2026-08-29"]
        )
    ]
    assert len(posteriores) == 14
    return partner, unit, primeiro, posteriores


def test_exames_posteriores_ao_fechamento_entram_direto_no_complementar(
    client, auth, person, pastore
):
    """O cenário de produção deixou de produzir fila.

    Antes da M26.3 os 14 exames posteriores ficavam em "Aguardando fechamento
    mensal" esperando um clique. Agora o primeiro deles abre o complementar e
    os outros 13 entram nele — a fila nasce e morre dentro do mesmo POST.
    """

    _partner, _unit, _primeiro, posteriores = _cenario_producao(
        client, auth, person, pastore
    )
    body = _estado(client, auth)

    assert body["indicadores"]["aguardando_fechamento"] == 0
    assert body["elegiveis"] == []
    assert body["grupos_elegiveis"] == []
    assert len(posteriores) == 14

    complementar = _do_mes(client, auth, "2026-08", 2)
    assert complementar["itens"]["total"] == 14
    assert complementar["complementar"] is True
    assert complementar["sequencia"] == 2
    # Os 17 exames do mês estão todos vinculados, e em dois fechamentos
    # distintos — a identidade exame a exame é conferida no teste seguinte.
    assert body["indicadores"]["exames_em_fechamento"] == 17


def test_complementar_fecha_os_14_sem_tocar_no_valor_ja_conferido(
    client, auth, db, person, pastore
):
    _partner, unit, primeiro, posteriores = _cenario_producao(
        client, auth, person, pastore
    )

    body = _do_mes(client, auth, "2026-08", 2)
    assert body["sequencia"] == 2
    assert body["complementar"] is True
    assert body["titulo"] == "Fechamento 2026-08 — complementar 2"
    assert body["itens"]["total"] == 14
    assert body["status"] == "incluido"
    # O complementar não inventa preço nenhum.
    assert body["valor_total"] is None
    assert body["recebimento"] is None

    # O fechamento de 11/08 continua exatamente como estava: mesmo valor,
    # mesmo estado, mesmos 3 exames.
    anterior = db.get(PartnerSettlement, primeiro["id"])
    assert anterior.valor_total == Decimal("328.50")
    assert anterior.status == "a_receber"
    assert anterior.sequencia == 1
    itens_anterior = db.execute(
        select(PartnerSettlementItem).where(
            PartnerSettlementItem.settlement_id == anterior.id
        )
    ).scalars().all()
    assert len(itens_anterior) == 3

    novos = db.execute(
        select(PartnerSettlementItem).where(
            PartnerSettlementItem.settlement_id == body["id"]
        )
    ).scalars().all()
    assert {i.spirometry_exam_id for i in novos} == set(posteriores)

    # E nenhum lançamento financeiro nasceu — recibo só na confirmação.
    assert db.execute(select(FinancialEntry)).scalars().all() == []

    depois = _estado(client, auth)
    assert depois["indicadores"]["aguardando_fechamento"] == 0
    assert depois["grupos_elegiveis"] == []
    # Esta parceria de teste não tem regra cadastrada: nada é inferido.
    assert depois["regra_valor"] == "Não inferido; exige confirmação do gestor."


def test_fechamento_ainda_aberto_recebe_os_exames_em_vez_de_fragmentar_o_mes(
    client, auth, db, person, pastore
):
    partner, unit = pastore
    _exame(client, auth, person, partner, unit, "2026-08-01")
    primeiro = _do_mes(client, auth)
    assert primeiro["sequencia"] == 1
    assert primeiro["valor_total"] is None

    corpo = _exame_resposta(client, auth, person, partner, unit, "2026-08-15")
    assert corpo["fechamento_parceria"]["acao"] == "incorporado"
    body = _do_mes(client, auth)
    assert body["id"] == primeiro["id"]
    assert body["sequencia"] == 1
    assert body["itens"]["total"] == 2
    assert len(db.execute(select(PartnerSettlement)).scalars().all()) == 1


def test_fechamento_com_valor_digitado_nao_recebe_exame_novo_mesmo_incluido(
    client, auth, db, person, pastore
):
    """Valor conferido é intocável, mesmo antes de virar `a_receber`.

    Um fechamento em `incluido` com `valor_total` preenchido já teve o número
    batido contra o extrato. Enfiar exame nele passaria a fazer aquele valor
    afirmar algo que ninguém conferiu.
    """

    partner, unit = pastore
    _exame(client, auth, person, partner, unit, "2026-08-01")
    primeiro = _do_mes(client, auth)
    assert client.patch(
        f"{API}/pastore/fechamentos/{primeiro['id']}",
        json={"valor_total": "109.50"},
        headers=auth("gestor"),
    ).status_code == 200

    corpo = _exame_resposta(client, auth, person, partner, unit, "2026-08-15")
    assert corpo["fechamento_parceria"]["acao"] == "criado"
    body = _do_mes(client, auth, "2026-08", 2)
    assert body["sequencia"] == 2
    assert db.get(PartnerSettlement, primeiro["id"]).valor_total == Decimal("109.50")


def test_complementar_tem_valor_e_recibo_proprios(
    client, auth, db, person, pastore
):
    _partner, unit, primeiro, _posteriores = _cenario_producao(
        client, auth, person, pastore
    )
    segundo = _do_mes(client, auth, "2026-08", 2)

    assert _valorar(client, auth, segundo["id"], "1533.00").status_code == 200
    recebido = client.post(
        f"{API}/pastore/fechamentos/{segundo['id']}/receber",
        json={
            "valor_confirmado": "1533.00",
            "data_recebimento": "2026-09-05",
            "forma_pagamento": "Pix",
            "idempotency_key": "m26-complementar-001",
        },
        headers=auth("gestor"),
    )
    assert recebido.status_code == 201, recebido.text
    body = recebido.json()
    assert body["status"] == "recebido"
    assert body["recebimento"]["valor"] == "1533.00"

    entries = db.execute(select(FinancialEntry)).scalars().all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.tipo == "receita"
    assert entry.categoria == "Recebimento de parceiro"
    assert entry.data_competencia == date(2026, 8, 1)
    assert entry.partner_settlement_id == segundo["id"]

    # O fechamento de sequência 1 segue sem recibo — são obrigações distintas.
    assert db.get(PartnerSettlement, primeiro["id"]).status == "a_receber"


def test_um_exame_nunca_entra_em_dois_fechamentos_da_mesma_competencia(
    client, auth, db, person, pastore
):
    _partner, unit, primeiro, _posteriores = _cenario_producao(
        client, auth, person, pastore
    )
    segundo = _do_mes(client, auth, "2026-08", 2)

    vinculos = db.execute(select(PartnerSettlementItem)).scalars().all()
    assert len(vinculos) == 17
    assert len({v.spirometry_exam_id for v in vinculos}) == 17
    assert {v.settlement_id for v in vinculos} == {primeiro["id"], segundo["id"]}


def test_criacao_de_complementar_fica_na_trilha_com_a_sequencia(
    client, auth, db, person, pastore
):
    _partner, unit, _primeiro, _posteriores = _cenario_producao(
        client, auth, person, pastore
    )

    acoes = db.execute(
        select(AuditLog).where(AuditLog.acao.like("pastore.%")).order_by(AuditLog.id)
    ).scalars().all()
    criados = [a for a in acoes if a.acao == "pastore.fechamento_criado"]
    assert [a.detalhes["sequencia"] for a in criados] == [1, 2]
    # O complementar nasce com o 1º dos 14; os outros 13 são incorporações.
    assert criados[-1].detalhes["sequencia"] == 2
    incorporados = [
        a for a in acoes if a.acao == "pastore.fechamento_itens_incorporados"
    ]
    assert sum(a.detalhes["total"] for a in incorporados if a.detalhes["sequencia"] == 2) == 13


def test_incorporacao_tem_acao_propria_na_trilha(
    client, auth, db, person, pastore
):
    partner, unit = pastore
    _exame(client, auth, person, partner, unit, "2026-08-01")
    _exame(client, auth, person, partner, unit, "2026-08-15")

    incorporacoes = db.execute(
        select(AuditLog).where(
            AuditLog.acao == "pastore.fechamento_itens_incorporados"
        )
    ).scalars().all()
    assert len(incorporacoes) == 1
    assert incorporacoes[0].detalhes["total"] == 1
    assert incorporacoes[0].detalhes["sequencia"] == 1


def test_exame_nao_concluido_continua_fora_do_complementar(
    client, auth, person, pastore
):
    partner, unit = pastore
    _exame(client, auth, person, partner, unit, "2026-08-01")
    primeiro = _do_mes(client, auth)
    _valorar(client, auth, primeiro["id"], "109.50")

    client.post(
        f"{API}/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_pastore",
            "espirometria": {
                "data_exame": "2026-08-20",
                "status": "Aguardando",
                "partner_id": partner.id,
                "partner_unit_id": unit.id,
            },
        },
        headers=auth("operacional"),
    )
    assert _fechar(client, auth, unit).status_code == 422
    assert _estado(client, auth)["indicadores"]["aguardando_fechamento"] == 0


def test_competencias_diferentes_seguem_independentes(
    client, auth, person, pastore
):
    partner, unit = pastore
    _exame(client, auth, person, partner, unit, "2026-07-14")
    julho = _do_mes(client, auth, "2026-07")
    _valorar(client, auth, julho["id"], "109.50")
    _exame(client, auth, person, partner, unit, "2026-08-01")
    agosto = _do_mes(client, auth, "2026-08")

    assert julho["sequencia"] == 1
    assert agosto["sequencia"] == 1
    assert agosto["complementar"] is False
