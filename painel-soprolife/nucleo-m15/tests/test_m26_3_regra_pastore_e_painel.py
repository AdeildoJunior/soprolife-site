"""M26.3 — a regra de recebimento Pastore, o fechamento automático e o painel.

Três coisas que a operação real cobrou e que o código passa a garantir sozinho:

1. **R$ 109,50 por exame** é uma regra CADASTRADA, com vigência, em campo
   próprio da parceria — não um número que alguém redigita todo mês.
2. **Exame realizado vira dívida do parceiro na hora**, sem depender de
   ninguém lembrar de clicar; e **vira receita só quando o gestor confirma o
   pagamento**. As duas grandezas nunca se somam nem se confundem.
3. O painel mostra as duas separadas, com origem e competência, e não desenha
   eixo nenhum quando não há dado.

Tudo em SQLite isolado das fixtures. Nenhum dado real, nenhum nome de
paciente, nenhuma conexão com produção.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import (
    FinancialEntry,
    Partner,
    PartnerSettlement,
    PartnerSettlementItem,
    PartnerUnit,
    Partnership,
    SpirometryExam,
)
from app.services.partner_pricing import resolve_valor_por_exame
from app.snapshots import build_financeiro_summary

API = "/api/v1"

VALOR_POR_EXAME = Decimal("109.50")


@pytest.fixture()
def pastore(db):
    """Pastore com a regra vigente cadastrada — o estado pós-M26.3."""
    partner = Partner(
        public_code="CLI-M263A", nome="Pastore", tipo="clinica",
        status="ativa", arquivado=False,
    )
    db.add(partner)
    db.flush()
    unit = PartnerUnit(
        public_code="UNI-M263A", partner_id=partner.id,
        nome="Pastore Ipanema", ativo=True,
    )
    db.add(unit)
    db.add(Partnership(
        public_code="PAR-M263A", partner_id=partner.id, status="em_negociacao",
        modelo_recebimento="valor_por_exame",
        valor_recebido_por_exame=VALOR_POR_EXAME,
        vigencia_inicio=date(2026, 7, 1),
    ))
    db.commit()
    return partner, unit


def _exame(client, auth, person, partner, unit, data, status="Realizado"):
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_pastore",
            "espirometria": {
                "data_exame": data, "status": status,
                "partner_id": partner.id, "partner_unit_id": unit.id,
            },
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _fechamentos(client, auth):
    return client.get(f"{API}/pastore/fechamentos", headers=auth("gestor")).json()


def _do_mes(corpo, competencia, sequencia=1):
    for row in corpo["fechamentos"]:
        if row["competencia"] == competencia and row["sequencia"] == sequencia:
            return row
    raise AssertionError(f"sem fechamento {competencia} #{sequencia}")


def _domiciliar(client, auth, person, valor, data="2026-08-28"):
    return client.post(
        f"{API}/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": {
                "data_exame": data, "status": "Realizado", "modalidade": "residencial",
            },
            "financeiro": {"espirometria": {
                "valor": valor, "status": "Recebido",
                "data_competencia": data, "data_recebimento": data,
            }},
        },
        headers=auth("operacional"),
    )


# ───────────────────────────────────── 1. a regra comercial existe e é lida


def test_1_regra_pastore_vale_109_50_por_exame(db, pastore):
    partner, unit = pastore
    regra = resolve_valor_por_exame(db, partner, unit, date(2026, 8, 1))
    assert regra.cadastrada is True
    assert regra.valor_por_exame == VALOR_POR_EXAME
    assert regra.origem == "partnership"
    assert regra.vigencia_inicio == date(2026, 7, 1)


@pytest.mark.parametrize(
    "quantidade,esperado",
    [(2, "219.00"), (3, "328.50"), (14, "1533.00")],
)
def test_2_as_tres_contas_historicas_batem(db, pastore, quantidade, esperado):
    """Os números que o gestor confirmou saem da regra, não de digitação."""
    partner, unit = pastore
    regra = resolve_valor_por_exame(db, partner, unit, date(2026, 8, 1))
    assert regra.previsto(quantidade) == Decimal(esperado)


def test_3_competencia_anterior_a_vigencia_nao_tem_regra(db, pastore):
    """Regra que começa em julho não afirma nada sobre junho."""
    partner, unit = pastore
    regra = resolve_valor_por_exame(db, partner, unit, date(2026, 6, 1))
    assert regra.cadastrada is False
    # E ausência de regra devolve None, nunca R$ 0,00 — zero é uma afirmação.
    assert regra.previsto(5) is None


def test_4_repasse_nunca_e_lido_como_recebimento(db, pastore):
    """Preencher só os campos de repasse não cria regra de recebimento."""
    partner, unit = pastore
    parceria = db.execute(select(Partnership)).scalar_one()
    parceria.modelo_recebimento = "indefinido"
    parceria.valor_recebido_por_exame = None
    parceria.vigencia_inicio = None
    parceria.modelo_repasse = "fixo"
    parceria.valor_repasse_fixo = Decimal("109.50")
    parceria.percentual_repasse = Decimal("50.00")
    db.commit()

    regra = resolve_valor_por_exame(db, partner, unit, date(2026, 8, 1))
    assert regra.cadastrada is False
    assert regra.previsto(14) is None


def test_5_banco_recusa_meia_regra(db, pastore):
    """Declarar `valor_por_exame` sem valor ou sem vigência é erro no banco."""
    partner, _unit = pastore
    db.add(Partnership(
        public_code="PAR-M263B", partner_id=partner.id, status="ativa",
        modelo_recebimento="valor_por_exame",
        valor_recebido_por_exame=Decimal("50.00"),
        vigencia_inicio=None,
    ))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# ─────────────────────── 6. fechamento automático e valor previsto derivado


def test_6_exame_entra_no_fechamento_sem_ninguem_clicar(client, auth, person, pastore):
    partner, unit = pastore
    corpo = _exame(client, auth, person, partner, unit, "2026-08-15")
    vinculo = corpo["fechamento_parceria"]
    assert vinculo is not None
    assert vinculo["acao"] == "criado"
    assert vinculo["competencia"] == "2026-08"

    listagem = _fechamentos(client, auth)
    assert listagem["indicadores"]["aguardando_fechamento"] == 0
    fechamento = _do_mes(listagem, "2026-08")
    assert fechamento["itens"]["total"] == 1
    assert fechamento["valor_previsto"] == "109.50"
    # Previsto NÃO é conferido: o valor mensal continua vazio até o extrato.
    assert fechamento["valor_total"] is None


def test_7_fechamento_aberto_recalcula_previsto_a_cada_exame(
    client, auth, person, pastore
):
    partner, unit = pastore
    esperados = ["109.50", "219.00", "328.50"]
    for i, data in enumerate(("2026-08-15", "2026-08-18", "2026-08-22")):
        _exame(client, auth, person, partner, unit, data)
        fechamento = _do_mes(_fechamentos(client, auth), "2026-08")
        assert fechamento["itens"]["total"] == i + 1
        # Derivado, não gravado: nenhum job, nenhum clique, nenhuma coluna
        # que envelheça em silêncio.
        assert fechamento["valor_previsto"] == esperados[i]


def test_8_exame_nunca_entra_em_dois_fechamentos(client, auth, person, pastore, db):
    partner, unit = pastore
    corpo = _exame(client, auth, person, partner, unit, "2026-08-15")
    exam_id = corpo["espirometria"]["id"]

    itens = db.execute(
        select(PartnerSettlementItem).where(
            PartnerSettlementItem.spirometry_exam_id == exam_id
        )
    ).scalars().all()
    assert len(itens) == 1

    # Nem o POST manual acrescenta de novo: não sobrou exame elegível.
    repetido = client.post(
        f"{API}/pastore/fechamentos",
        json={"partner_unit_id": unit.id, "competencia": "2026-08"},
        headers=auth("gestor"),
    )
    assert repetido.status_code == 422
    assert len(db.execute(select(PartnerSettlementItem)).scalars().all()) == 1


def test_9_exame_agendado_entra_so_quando_vira_realizado(
    client, auth, person, pastore
):
    """O gatilho é a conclusão, não o agendamento."""
    partner, unit = pastore
    corpo = _exame(client, auth, person, partner, unit, "2026-08-15", status="Aguardando")
    assert corpo["fechamento_parceria"] is None
    assert _fechamentos(client, auth)["fechamentos"] == []

    exam_id = corpo["espirometria"]["id"]
    resp = client.patch(
        f"{API}/espirometrias/{exam_id}",
        json={"status": "Realizado"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["fechamento_parceria"]["acao"] == "criado"
    assert _do_mes(_fechamentos(client, auth), "2026-08")["itens"]["total"] == 1


def test_10_competencia_ja_valorada_gera_complementar(
    client, auth, person, pastore
):
    """O valor já conferido contra o extrato nunca passa a cobrir mais exames."""
    partner, unit = pastore
    for data in ("2026-08-01", "2026-08-04", "2026-08-05"):
        _exame(client, auth, person, partner, unit, data)
    primeiro = _do_mes(_fechamentos(client, auth), "2026-08")
    assert primeiro["valor_previsto"] == "328.50"

    assert client.patch(
        f"{API}/pastore/fechamentos/{primeiro['id']}",
        json={"status": "a_receber", "valor_total": "328.50"},
        headers=auth("gestor"),
    ).status_code == 200

    # Exame novo no mesmo mês: complementar, não reabertura.
    _exame(client, auth, person, partner, unit, "2026-08-15")
    listagem = _fechamentos(client, auth)
    assert _do_mes(listagem, "2026-08", 1)["itens"]["total"] == 3
    complementar = _do_mes(listagem, "2026-08", 2)
    assert complementar["itens"]["total"] == 1
    assert complementar["valor_previsto"] == "109.50"
    assert complementar["complementar"] is True


# ──────────────────── 11. recebido × a receber: duas grandezas, nunca uma só


def test_11_recebimento_cria_exatamente_um_lancamento_e_nao_duplica(
    client, auth, person, pastore, db
):
    partner, unit = pastore
    for data in ("2026-08-15", "2026-08-18"):
        _exame(client, auth, person, partner, unit, data)
    fechamento = _do_mes(_fechamentos(client, auth), "2026-08")
    assert fechamento["valor_previsto"] == "219.00"
    # Antes do recebimento: previsto na tela, ZERO no Financeiro.
    assert db.execute(select(FinancialEntry)).scalars().all() == []
    db.rollback()  # libera o lock do SQLite antes da próxima escrita da API

    recibo = {
        "valor_confirmado": "219.00",
        "data_recebimento": "2026-08-31",
        "forma_pagamento": "Outro",
        "idempotency_key": "m263-receb-ago",
    }
    resp = client.post(
        f"{API}/pastore/fechamentos/{fechamento['id']}/receber",
        json=recibo, headers=auth("gestor"),
    )
    assert resp.status_code == 201, resp.text
    entradas = db.execute(select(FinancialEntry)).scalars().all()
    assert len(entradas) == 1
    assert entradas[0].valor == Decimal("219.00")
    assert entradas[0].status == "Recebido"
    assert entradas[0].spirometry_exam_id is None  # agregado, nunca por exame
    db.rollback()

    # Repetir não duplica.
    repetido = client.post(
        f"{API}/pastore/fechamentos/{fechamento['id']}/receber",
        json=recibo, headers=auth("gestor"),
    )
    assert repetido.status_code == 201
    assert repetido.json()["idempotente"] is True
    assert len(db.execute(select(FinancialEntry)).scalars().all()) == 1
    db.rollback()

    # E o fechamento recebido some do "a receber".
    depois = _do_mes(_fechamentos(client, auth), "2026-08")
    assert depois["status"] == "recebido"
    assert depois["valor_em_aberto"] is None


def test_12_exame_realizado_nao_vira_receita_sozinho(
    client, auth, person, pastore, db
):
    """A separação que sustenta o painel inteiro."""
    partner, unit = pastore
    for data in ("2026-08-15", "2026-08-18"):
        _exame(client, auth, person, partner, unit, data)

    resumo = build_financeiro_summary(db)
    # A Pastore DEVE R$ 219,00…
    assert resumo["totais"]["a_receber_total"] == 219.00
    assert resumo["totais"]["a_receber_parceria"] == 219.00
    # …e não pagou nada ainda.
    assert resumo["totais"]["receita_recebida"] == 0
    assert resumo["por_origem"] == []
    assert resumo["exames_em_fechamento_aberto"] == 2


def test_13_soprolife_direto_continua_usando_o_valor_do_operador(
    client, auth, person, db
):
    """A regra da parceria não contamina o preço do atendimento próprio."""
    resp = _domiciliar(client, auth, person, "230.00")
    assert resp.status_code == 201, resp.text
    entrada = db.execute(select(FinancialEntry)).scalar_one()
    assert entrada.valor == Decimal("230.00")   # digitado, não 109,50
    assert entrada.partner_settlement_id is None
    assert entrada.spirometry_exam_id is not None


# ───────────────────────────────────────────────── 14. o painel Financeiro


@pytest.fixture()
def cenario_completo(client, auth, person, pastore, db):
    """Réplica da FORMA de produção pós-regularização, em escala reduzida."""
    partner, unit = pastore
    _domiciliar(client, auth, person, "230.00", "2026-08-28")
    _domiciliar(client, auth, person, "220.00", "2026-08-26")

    for data in ("2026-07-14", "2026-07-18"):
        _exame(client, auth, person, partner, unit, data)
    julho = _do_mes(_fechamentos(client, auth), "2026-07")
    client.post(
        f"{API}/pastore/fechamentos/{julho['id']}/receber",
        json={"valor_confirmado": "219.00", "data_recebimento": "2026-08-31",
              "forma_pagamento": "Outro", "idempotency_key": "cen-jul"},
        headers=auth("gestor"),
    )
    for data in ("2026-08-15", "2026-08-18", "2026-08-22"):
        _exame(client, auth, person, partner, unit, data)
    return db


def test_14_receita_recebida_e_a_receber_sao_distintas(cenario_completo):
    resumo = build_financeiro_summary(cenario_completo)
    # Recebido: 230 + 220 próprios + 219,00 do fechamento de julho.
    assert resumo["totais"]["receita_recebida"] == 669.00
    # A receber: os 3 exames de agosto ainda em fechamento aberto.
    assert resumo["totais"]["a_receber_parceria"] == 328.50
    assert resumo["totais"]["a_receber_total"] == 328.50


def test_15_receita_por_origem_separa_soprolife_de_pastore(cenario_completo):
    resumo = build_financeiro_summary(cenario_completo)
    origens = {o["origem"]: o for o in resumo["por_origem"]}
    assert set(origens) == {"SoproLife", "Pastore"}
    assert origens["SoproLife"]["valor"] == 450.00
    assert origens["Pastore"]["valor"] == 219.00
    assert round(sum(o["percentual"] for o in resumo["por_origem"])) == 100


def test_16_competencia_e_do_mes_do_exame_nao_do_credito(cenario_completo):
    """O recibo de julho pago em 31/08 pertence a JULHO.

    É o mês em que aqueles dois exames foram feitos. Chavear pela data do
    crédito empilharia toda a regularização histórica numa barra só e apagaria
    a produção real dos meses anteriores.
    """
    resumo = build_financeiro_summary(cenario_completo)
    meses = {m["competencia"]: m["valor"] for m in resumo["por_competencia"]}
    assert meses == {"2026-07": 219.00, "2026-08": 450.00}
    assert [m["label"] for m in resumo["por_competencia"]] == ["Jul/2026", "Ago/2026"]
    # E a soma das barras continua batendo com a receita recebida.
    assert sum(meses.values()) == resumo["totais"]["receita_recebida"]


def test_17_lancamentos_recentes_aparecem_com_origem_e_status(cenario_completo):
    resumo = build_financeiro_summary(cenario_completo)
    recentes = resumo["lancamentos_recentes"]
    assert 0 < len(recentes) <= 10
    for item in recentes:
        assert item["origem"] in {"SoproLife", "Pastore"}
        assert item["status"]
        assert item["categoria"]
        assert item["valor"] is not None
    assert any(i["origem"] == "Pastore" for i in recentes)
    assert any(i["origem"] == "SoproLife" for i in recentes)


def test_18_media_e_por_exame_nunca_por_lancamento(cenario_completo):
    resumo = build_financeiro_summary(cenario_completo)
    # 2 exames próprios pagos + 2 exames de julho dentro do recibo Pastore.
    assert resumo["exames_pagos"] == 2
    assert resumo["exames_parceria_recebidos"] == 2
    assert resumo["exames_reconhecidos"] == 4
    assert resumo["receita_media_por_exame"] == round(669.00 / 4, 2)
    # A métrica antiga fica explicitamente aposentada: um recibo de
    # fechamento cobre vários exames e dividir por lançamento inflaria o
    # número sozinho a cada mês fechado.
    assert resumo["ticket_medio_real"] is None
    assert resumo["receita_media_por_exame"] != round(669.00 / 3, 2)


def test_19_banco_vazio_nao_produz_grafico_nem_metrica_falsa(db):
    resumo = build_financeiro_summary(db)
    # Nada de eixo de R$ 0,00 a R$ 1,00: sem dado, sem série.
    assert resumo["por_competencia"] == []
    assert resumo["por_origem"] == []
    assert resumo["lancamentos_recentes"] == []
    # E nada de média fabricada dividindo por zero exame.
    assert resumo["receita_media_por_exame"] is None
    assert resumo["valor_base_exame"] is None
    assert resumo["saldo_operacional"] is None


def test_20_receita_sem_data_e_declarada_em_vez_de_sumir(client, auth, person, db):
    """O lote histórico M18 não tem data nenhuma — e o gráfico precisa dizer.

    Somar essa receita num mês inventado seria mentira; ignorá-la em silêncio
    faria a soma das barras não bater com "Receita recebida" e ninguém
    entenderia por quê.
    """
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": person["id"], "tipo": "espirometria_soprolife",
            "espirometria": {"data_exame": "2026-06-01", "status": "Realizado",
                             "modalidade": "residencial"},
            "financeiro": {"espirometria": {"valor": "238.58", "status": "Recebido"}},
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text

    resumo = build_financeiro_summary(db)
    assert resumo["totais"]["receita_recebida"] == 238.58
    assert resumo["por_competencia"] == []
    assert resumo["receita_sem_competencia"] == 238.58
    assert resumo["lancamentos_sem_competencia"] == 1


def test_21_parceria_sem_regra_e_declarada_e_nao_vira_zero(
    client, auth, person, pastore, db
):
    partner, unit = pastore
    parceria = db.execute(select(Partnership)).scalar_one()
    parceria.modelo_recebimento = "indefinido"
    parceria.valor_recebido_por_exame = None
    parceria.vigencia_inicio = None
    db.commit()

    _exame(client, auth, person, partner, unit, "2026-08-15")
    resumo = build_financeiro_summary(db)
    assert resumo["totais"]["a_receber_parceria"] == 0
    assert resumo["parcerias_sem_regra"] == ["Pastore"]
    # Nenhum exame é contado como sustentando previsto que não existe.
    assert resumo["exames_em_fechamento_aberto"] == 0
