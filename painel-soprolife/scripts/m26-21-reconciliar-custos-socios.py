#!/usr/bin/env python3
"""
M26.21 — Reconciliação de Custos & Investimentos e rateio dos sócios.

Fonte dos fatos: informação direta dos sócios (Adeildo e Faustino), registrada
em cada movimento com origem="reconciliacao informada pelos socios".

Este script reescreve painel-soprolife/data-private/custos-investimentos.local.json
migrando o modelo antigo (parcelas_detalhe + agregados escritos à mão) para um
LIVRO-CAIXA único por item (`movimentos`), do qual todos os agregados passam a
ser derivados pelo gerador generate-custos-investimentos.py.

Nenhum registro legítimo é apagado: cada parcela/competência do arquivo antigo
entra no livro-caixa novo.

Uso:
    python3 .../m26-21-reconciliar-custos-socios.py              # dry-run (default)
    python3 .../m26-21-reconciliar-custos-socios.py --apply      # grava
"""

import argparse
import copy
import json
import os
import sys
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIVATE = os.path.join(ROOT, "data-private", "custos-investimentos.local.json")

ORIGEM = "reconciliacao informada pelos socios"
USUARIO = "Adeildo (sessao M26.21 - PC de casa)"
HOJE = date(2026, 10, 12)

# Status do livro-caixa:
#   pago                            -> saida de caixa confirmada
#   pendente                        -> ainda nao vencido / sem pagador definido
#   vencido_aguardando_confirmacao  -> vencimento passou, SEM confirmacao financeira
#   baixada_por_desconto            -> posicao liquidada sem saida de caixa
PAGO = "pago"
PENDENTE = "pendente"
AGUARDANDO = "vencido_aguardando_confirmacao"
BAIXA = "baixada_por_desconto"

A = "Adeildo"
F = "Faustino"


def mov(ref, competencia, valor, pagador, status, data=None, vencimento=None,
        valor_nominal=None, observacao=None):
    m = {
        "ref": ref,
        "competencia": competencia,
        "data": data,
        "vencimento": vencimento,
        "valor": valor,
        "pagador": pagador,
        "status": status,
        "origem": ORIGEM,
    }
    if valor_nominal is not None:
        m["valor_nominal"] = valor_nominal
    if observacao:
        m["observacao"] = observacao
    return m


def competencia_seq(ano, mes, n):
    """Gera n competencias 'YYYY-MM' consecutivas a partir de (ano, mes)."""
    out = []
    for i in range(n):
        total = (mes - 1) + i
        out.append("%04d-%02d" % (ano + total // 12, total % 12 + 1))
    return out


def parcelamento_cartao(valor_parcela, n_parcelas, ano_ini, mes_ini, dia_venc,
                        pagador, hoje=HOJE):
    """
    Parcelamento em cartao: vencimento no dia `dia_venc` de cada competencia.

    REGRA DELIBERADA (M26.21): nenhuma parcela e marcada 'pago' por ter chegado
    o dia do vencimento. Vencimento no passado sem confirmacao financeira ->
    'vencido_aguardando_confirmacao'. Automatiza calendario, nao conciliacao.
    """
    movs = []
    for i, comp in enumerate(competencia_seq(ano_ini, mes_ini, n_parcelas), start=1):
        ano, mes = int(comp[:4]), int(comp[5:])
        venc = date(ano, mes, dia_venc)
        status = AGUARDANDO if venc <= hoje else PENDENTE
        movs.append(mov(
            ref="%d/%d" % (i, n_parcelas),
            competencia=comp,
            valor=valor_parcela,
            pagador=pagador,
            status=status,
            vencimento=venc.isoformat(),
            observacao=("Fatura do cartao de %s, vencimento dia %d. "
                        "Sem confirmacao financeira registrada." % (pagador, dia_venc)),
        ))
    return movs


# --------------------------------------------------------------------------
# Fatos informados pelos socios
# --------------------------------------------------------------------------

# 2. Contabilidade — R$ 260,00/mes, jan..out/2026
CONTABILIDADE_PAGADORES = {
    "2026-01": F, "2026-02": A, "2026-03": A, "2026-04": F, "2026-05": A,
    "2026-06": A, "2026-07": F, "2026-08": F, "2026-09": F, "2026-10": F,
}

# 3. Impostos — todos pagos por Faustino
IMPOSTOS = {"2026-07": 12.00, "2026-08": 17.70, "2026-09": 20.36}

# 4. Mais Laudo — todos pagos por Faustino. Meses sem gasto NAO geram movimento.
MAIS_LAUDO = {
    "2026-02": 19.50, "2026-05": 26.00, "2026-06": 13.00,
    "2026-07": 26.00, "2026-08": 39.00, "2026-10": 6.50,
}

# 5. Koko — pagador por posicao nominal (20 posicoes de R$ 500,00)
KOKO_PAGOS = [
    (1,  "2025-11-09", F), (2,  "2025-12-08", F), (3,  "2026-01-09", A),
    (4,  "2026-02-09", F), (5,  "2026-03-09", F), (6,  "2026-04-10", A),
    (7,  "2026-05-07", A), (8,  "2026-06-10", F), (9,  "2026-07-03", F),
    (10, "2026-08-09", F), (11, "2026-09-07", F), (12, "2026-10-10", F),
]
KOKO_PENDENTES = [13, 14, 15]
# Adiantamento de 13/05/2026: posicoes 16-20 liquidadas, 4 pagas + 1 bonificada.
KOKO_ADIANTADAS_PAGAS = [16, 17, 18, 19]
KOKO_BONIFICADA = 20


def build_contabilidade():
    movs = [
        mov(ref=comp, competencia=comp, valor=260.00, pagador=pag, status=PAGO)
        for comp, pag in sorted(CONTABILIDADE_PAGADORES.items())
    ]
    return {
        "id": "contab-001",
        "nome": "Contabilidade / Contador",
        "categoria": "recorrente",
        "tipo": "Servico contabil / contador da empresa",
        "responsavel": "Faustino e Adeildo",
        "descricao": "Contabilidade / contador da empresa. Pagamento alternado entre os socios.",
        "valor_total": None,
        "valor_mensal": 260.00,
        "tipo_valor": "mensal",
        "moeda_original": "BRL",
        "inicio": "2026-01-01",
        "status": "ativo",
        "historico_confirmado": True,
        "movimentos": movs,
        "observacao": ("R$ 260,00/mes. Historico jan..out/2026 reconciliado com os socios: "
                       "Adeildo pagou fev, mar, mai e jun; Faustino pagou jan, abr, jul, ago, set e out. "
                       "Nao ha dia de pagamento informado - movimentos registrados por competencia."),
    }


def build_impostos():
    movs = [
        mov(ref=comp, competencia=comp, valor=v, pagador=F, status=PAGO)
        for comp, v in sorted(IMPOSTOS.items())
    ]
    return {
        "id": "imposto-001",
        "nome": "Impostos / Tributos da empresa",
        "categoria": "tributo",
        "tipo": "Tributo / imposto da empresa",
        "responsavel": "Faustino",
        "descricao": "Impostos e tributos da empresa, recolhidos por competencia.",
        "valor_total": None,
        "valor_mensal": None,
        "tipo_valor": "variavel",
        "moeda_original": "BRL",
        "inicio": "2026-07-01",
        "status": "ativo",
        "historico_confirmado": True,
        "movimentos": movs,
        "observacao": ("Valor variavel por competencia - nao ha valor mensal fixo. "
                       "Jul/2026 R$ 12,00; ago/2026 R$ 17,70; set/2026 R$ 20,36. "
                       "Todos pagos por Faustino. Competencia preservada."),
    }


def build_mais_laudo():
    movs = [
        mov(ref=comp, competencia=comp, valor=v, pagador=F, status=PAGO)
        for comp, v in sorted(MAIS_LAUDO.items())
    ]
    return {
        "id": "maislaudo-001",
        "nome": "Mais Laudo",
        "categoria": "servico",
        "tipo": "Servico de laudo terceirizado",
        "responsavel": "Faustino",
        "descricao": ("Servico de laudo utilizado na epoca em que a medica responsavel "
                      "ainda nao estava laudando internamente."),
        "valor_total": None,
        "valor_mensal": None,
        "tipo_valor": "variavel",
        "moeda_original": "BRL",
        "inicio": "2026-02-01",
        "status": "ativo",
        "historico_confirmado": True,
        "movimentos": movs,
        "observacao": ("Uso variavel: mar, abr e set/2026 sem gasto (nao geram movimento - "
                       "ausencia de gasto nao e lancamento de zero). Total efetivamente pago "
                       "R$ 130,00, integralmente por Faustino."),
    }


def build_koko(antigo):
    movs = []
    for numero, data, pagador in KOKO_PAGOS:
        movs.append(mov(
            ref="%d/20" % numero, competencia=data[:7], valor=500.00,
            pagador=pagador, status=PAGO, data=data,
        ))
    for numero in KOKO_PENDENTES:
        movs.append(mov(
            ref="%d/20" % numero, competencia=None, valor=500.00,
            pagador=None, status=PENDENTE,
            observacao="Sem data e sem pagador definidos.",
        ))
    for numero in KOKO_ADIANTADAS_PAGAS:
        movs.append(mov(
            ref="%d/20" % numero, competencia="2026-05", valor=500.00,
            pagador=F, status=PAGO, data="2026-05-13",
            observacao=("Liquidada no adiantamento de 13/05/2026 das posicoes 16-20. "
                        "Saida de caixa efetiva de R$ 2.000,00 cobriu 4 posicoes."),
        ))
    movs.append(mov(
        ref="%d/20" % KOKO_BONIFICADA, competencia="2026-05", valor=0.00,
        pagador=None, status=BAIXA, data="2026-05-13", valor_nominal=500.00,
        observacao=("Posicao liquidada por bonificacao/desconto de R$ 500,00 concedida no "
                    "adiantamento de 13/05/2026. Saida de caixa R$ 0,00 - NAO e pagamento."),
    ))
    movs.sort(key=lambda m: int(m["ref"].split("/")[0]))

    item = copy.deepcopy(antigo)
    item.pop("parcelas_detalhe", None)
    item.pop("parcelas_pagas", None)
    item.pop("total_pago_dinheiro", None)
    item.pop("total_pendente", None)
    item.pop("pix_antecipado", None)
    item.update({
        "tipo_valor": "unico",
        "valor_nominal": 10000.00,
        "desconto_antecipacao": 500.00,
        "valor_total": 9500.00,
        "valor_mensal": 500.00,
        "parcelas_total": 20,
        "historico_confirmado": True,
        "movimentos": movs,
        "observacao": (
            "Contrato nominal: 20 x R$ 500,00 = R$ 10.000,00. Houve bonificacao/desconto de "
            "UMA parcela de R$ 500,00 no adiantamento de 13/05/2026, portanto o CUSTO EFETIVO "
            "e R$ 9.500,00. Posicoes 16-20 ficaram liquidadas com saida de caixa de R$ 2.000,00 "
            "(4 parcelas pagas + 1 bonificada) - nao foram R$ 2.500 nem R$ 3.000. "
            "Desembolsado em dinheiro: Adeildo R$ 1.500,00 (posicoes 3, 6 e 7); "
            "Faustino R$ 6.500,00 (9 pagamentos normais + 4 do adiantamento). "
            "Pendentes: posicoes 13, 14 e 15 = R$ 1.500,00, sem data e sem pagador definidos. "
            "Conferencia: R$ 8.000,00 pagos + R$ 1.500,00 pendentes = R$ 9.500,00 de custo efetivo."
        ),
    })
    return item


def build_parcelado_cartao(antigo, n, valor_parcela, ano, mes, nota_extra=""):
    item = copy.deepcopy(antigo)
    item.pop("parcelas_pagas", None)
    movs = parcelamento_cartao(valor_parcela, n, ano, mes, 10, A)
    soma = round(sum(m["valor"] for m in movs), 2)
    divergencia = round((item.get("valor_total") or 0) - soma, 2)
    item.update({
        "tipo_valor": "unico",
        "responsavel": A,
        "valor_mensal": valor_parcela,
        "parcelas_total": n,
        "historico_confirmado": True,
        "forma_pagamento": "Cartao de credito de Adeildo - fatura com pagamento automatico no dia 10",
        "dia_vencimento_fatura": 10,
        "movimentos": movs,
    })
    if divergencia:
        item["divergencia_arredondamento"] = divergencia
    item["observacao"] = (
        "Parcelado em %d x R$ %s no cartao de credito de Adeildo; a fatura e paga "
        "automaticamente todo dia 10. Vencimentos derivados da competencia inicial %02d/%04d. "
        "NENHUMA parcela e marcada como paga apenas por ter chegado o dia 10: parcelas com "
        "vencimento no passado ficam em 'vencido_aguardando_confirmacao' ate haver confirmacao "
        "financeira real.%s%s" % (
            n, ("%.2f" % valor_parcela).replace(".", ","), mes, ano,
            (" Soma das parcelas R$ %s difere do valor total cadastrado em R$ %s (arredondamento) - "
             "divergencia registrada, nao corrigida por invencao." % (
                 ("%.2f" % soma).replace(".", ","),
                 ("%.2f" % abs(divergencia)).replace(".", ","))) if divergencia else "",
            nota_extra,
        )
    )
    return item


def build_cremerj(antigo):
    """Custo unico historico - pagamento de Faustino PRESERVADO integralmente."""
    item = copy.deepcopy(antigo)
    item.pop("id_99pay", None)
    item.pop("codigo_autorizacao", None)
    item.pop("cnpj_favorecido", None)
    item.update({
        "tipo_valor": "unico",
        "historico_confirmado": True,
        "movimentos": [{
            "ref": "unico",
            "competencia": "2026-03",
            "data": "2026-03-11",
            "vencimento": None,
            "valor": 2296.06,
            "pagador": F,
            "status": PAGO,
            "origem": "pagamento historico ja registrado - preservado",
        }],
    })
    return item


def build_recorrente_sem_historico(antigo, nota):
    """
    Recorrente cujo numero de meses efetivamente pagos NAO foi informado.
    historico_confirmado=False => o gerador emite null (nunca R$ 0,00 inventado).
    """
    item = copy.deepcopy(antigo)
    item.update({
        "tipo_valor": "mensal",
        "responsavel": A,
        "historico_confirmado": False,
        "movimentos": [],
        "observacao": nota,
    })
    return item


def build_vps(antigo):
    item = copy.deepcopy(antigo)
    item.update({
        "tipo_valor": "unico",
        "responsavel": A,
        "historico_confirmado": False,
        "periodicidade": "anual",
        "movimentos": [],
        "observacao": (
            "Periodicidade real PRESERVADA: plano anual de 12 meses, R$ 635,00, "
            "vencimento 30/06/2027; valor mensal rateado 635 / 12 = 52,92. "
            "Responsabilidade corrente: Adeildo (informado pelos socios). "
            "Quem/quando desembolsou os R$ 635,00 continua NAO confirmado - "
            "nao lancar como pago sem confirmacao financeira."
        ),
    })
    return item


def reconciliar(doc):
    antigos = {it["id"]: it for it in doc["itens"]}
    novos = [
        build_recorrente_sem_historico(antigos["ws-001"], (
            "Recorrente mensal de R$ 32,72 desde dez/2025. Responsabilidade corrente: Adeildo "
            "(informado pelos socios). O numero de meses efetivamente pagos NAO foi informado "
            "nesta reconciliacao, portanto o total desembolsado fica NAO CONFIRMADO (null) em vez "
            "de um valor inventado. O compromisso mensal de R$ 32,72 continua atribuido a Adeildo.")),
        build_recorrente_sem_historico(antigos["ts-001"], (
            "Recorrente mensal de US$ 8,00 convertido para R$ 42,00 (cotacao de referencia ~R$ 5,25/USD) "
            "desde jul/2026. Responsabilidade corrente: Adeildo (informado pelos socios). "
            "Numero de meses efetivamente pagos NAO informado - total desembolsado NAO CONFIRMADO. "
            "Ajustar arredondamento conforme variacao cambial.")),
        build_vps(antigos["vps-001"]),
        build_contabilidade(),
        build_impostos(),
        build_mais_laudo(),
        build_parcelado_cartao(antigos["espi-001"], 10, 1246.46, 2026, 7),
        build_parcelado_cartao(antigos["cal-001"], 10, 199.70, 2026, 7),
        build_koko(antigos["koko-001"]),
        build_cremerj(antigos["cremerj-001"]),
    ]

    out = copy.deepcopy(doc)
    out["source"] = {
        "type": "local_private_custos",
        "safeToDisplay": False,
        "containsPersonalData": False,
        "containsBankingData": False,
        "generatedAt": datetime(2026, 10, 12, 0, 0, 0).isoformat() + "Z",
        "modelo": "m26-21-livro-caixa",
        "nota": ("Arquivo privado. Livro-caixa unico por item em `movimentos`; todos os agregados "
                 "sao DERIVADOS por scripts/generate-custos-investimentos.py. Nunca commitar."),
    }
    out["itens"] = novos
    out.pop("socios", None)        # bloco legado nao lido pelo painel, substituido por rateio_socios
    out.pop("rateio_socios", None)  # passa a ser derivado pelo gerador
    out.pop("rateio_itens", None)
    out["pendencias"] = [
        {
            "id": "pend-002",
            "item": "Regularizacao / Documentos da empresa",
            "responsavel": "Adeildo",
            "descricao": ("CREMERJ PJ ja registrado (pago em 11/03/2026 por Faustino). Pendente cadastrar "
                          "demais custos: abertura, alvara, CNES, licenciamento sanitario e outros custos "
                          "de regularizacao. A contabilidade/contador ja foi cadastrada na M26.21."),
            "status": "aguardando_dados",
            "prioridade": "alta",
        },
        {
            "id": "pend-003",
            "item": "Espirometro Koko - posicoes 13, 14 e 15",
            "responsavel": "Faustino e Adeildo",
            "descricao": "3 parcelas de R$ 500,00 (R$ 1.500,00) ainda sem data definida e sem pagador designado.",
            "status": "aguardando_dados",
            "prioridade": "media",
        },
        {
            "id": "pend-004",
            "item": "MIR / Spirobank II Smart e Seringa MIR 3L - confirmacao financeira",
            "responsavel": "Adeildo",
            "descricao": ("Parcelas com vencimento ja passado (dia 10) estao em "
                          "'vencido_aguardando_confirmacao'. Confirmar na fatura do cartao quais parcelas "
                          "foram efetivamente debitadas para passarem a 'pago'."),
            "status": "aguardando_confirmacao_financeira",
            "prioridade": "alta",
        },
        {
            "id": "pend-005",
            "item": "Google Workspace, Tailscale e Hostinger VPS - historico de desembolso",
            "responsavel": "Adeildo",
            "descricao": ("Responsabilidade corrente definida (Adeildo), mas o numero de meses "
                          "efetivamente pagos (Workspace, Tailscale) e o desembolso do plano anual "
                          "(Hostinger R$ 635,00) seguem NAO confirmados. Enquanto isso o painel mostra "
                          "'-' em vez de valor inventado."),
            "status": "aguardando_dados",
            "prioridade": "media",
        },
    ]
    out["auditoria"] = construir_auditoria(doc, novos)
    return out


def construir_auditoria(doc_antigo, novos):
    ts = datetime(2026, 10, 12, 0, 0, 0).isoformat() + "Z"
    registros = []
    for item in novos:
        for m in item.get("movimentos", []):
            if m.get("origem") != ORIGEM:
                continue
            registros.append({
                "item_id": item["id"],
                "item": item["nome"],
                "competencia_ou_parcela": m.get("ref") or m.get("competencia"),
                "valor": m["valor"],
                "pagador": m["pagador"],
                "status": m["status"],
                "origem": ORIGEM,
                "usuario": USUARIO,
                "registrado_em": ts,
            })
    return {
        "missao": "M26.21",
        "descricao": ("Reconciliacao de Custos & Investimentos e rateio dos socios. Modelo migrado "
                      "para livro-caixa unico (`movimentos`); agregados passam a ser derivados."),
        "usuario": USUARIO,
        "registrado_em": ts,
        "origem": ORIGEM,
        "itens_antes": sorted(it["id"] for it in doc_antigo["itens"]),
        "itens_depois": sorted(it["id"] for it in novos),
        "movimentos_registrados": len(registros),
        "registros": registros,
    }


# --------------------------------------------------------------------------
# Dry-run
# --------------------------------------------------------------------------

def agregados(item):
    """Mesma derivacao usada pelo gerador - replicada aqui para o dry-run."""
    movs = item.get("movimentos", [])
    conf = item.get("historico_confirmado", False)
    abertos = (PENDENTE, AGUARDANDO)

    def soma(pred):
        return round(sum(m["valor"] for m in movs if pred(m)), 2)

    pago_a = soma(lambda m: m["status"] == PAGO and m["pagador"] == A)
    pago_f = soma(lambda m: m["status"] == PAGO and m["pagador"] == F)
    pend_a = soma(lambda m: m["status"] in abertos and m["pagador"] == A)
    pend_f = soma(lambda m: m["status"] in abertos and m["pagador"] == F)
    pend_sem = soma(lambda m: m["status"] in abertos and m["pagador"] is None)
    desconto = round(sum(m.get("valor_nominal", 0) for m in movs if m["status"] == BAIXA), 2)

    if not conf:
        pago_a = pago_f = None
    if item.get("tipo_valor") in ("mensal", "variavel"):
        pend_a = pend_f = pend_sem = None
        if not conf:
            pass
    return {
        "pago_adeildo": pago_a, "pago_faustino": pago_f,
        "pendente_adeildo": pend_a, "pendente_faustino": pend_f,
        "pendente_sem_pagador": pend_sem,
        "desconto_ou_baixa_sem_saida_caixa": desconto,
    }


def brl(v):
    if v is None:
        return "-"
    return ("R$ %0.2f" % v).replace(",", "X").replace(".", ",").replace("X", ".")


def dry_run(antigo, novo):
    old_items = {it["id"]: it for it in antigo["itens"]}
    old_rateio = {r["item_id"]: r for r in antigo.get("rateio_itens", [])}
    linhas = []
    for it in novo["itens"]:
        ag = agregados(it)
        o = old_items.get(it["id"])
        orr = old_rateio.get(it["id"], {})
        linhas.append({
            "item": it["nome"],
            "registro_atual": "ausente (nao cadastrado)" if o is None else
                              "%s mov. antigos" % len(o.get("parcelas_detalhe", []) or []) or "cadastrado",
            "registro_proposto": "%d movimento(s) no livro-caixa" % len(it.get("movimentos", [])),
            "valor_atual": None if o is None else o.get("valor_total"),
            "valor_proposto": it.get("valor_total"),
            "pagador_atual": None if o is None else o.get("responsavel"),
            "pagador_proposto": it.get("responsavel"),
            "status_atual": None if o is None else o.get("status"),
            "status_proposto": it.get("status"),
            "pago_a_atual": orr.get("pago_adeildo", None) if o else None,
            "pago_a_prop": ag["pago_adeildo"],
            "pago_f_atual": orr.get("pago_faustino", None) if o else None,
            "pago_f_prop": ag["pago_faustino"],
            "novo": o is None,
        })
    return linhas


def imprimir_dry_run(antigo, novo):
    linhas = dry_run(antigo, novo)
    print("=" * 110)
    print("M26.21 — DRY-RUN (read-only). Nada foi gravado.")
    print("=" * 110)
    for L in linhas:
        print("\nITEM: %s%s" % (L["item"], "   [NOVO CADASTRO]" if L["novo"] else ""))
        print("  registro   : %-34s -> %s" % (L["registro_atual"], L["registro_proposto"]))
        print("  valor total: %-34s -> %s" % (brl(L["valor_atual"]), brl(L["valor_proposto"])))
        print("  responsavel: %-34s -> %s" % (L["pagador_atual"] or "-", L["pagador_proposto"]))
        print("  status     : %-34s -> %s" % (L["status_atual"] or "-", L["status_proposto"]))
        print("  pago Adeild: %-34s -> %s" % (brl(L["pago_a_atual"]), brl(L["pago_a_prop"])))
        print("  pago Faust.: %-34s -> %s" % (brl(L["pago_f_atual"]), brl(L["pago_f_prop"])))

    print("\n" + "=" * 110)
    print("TOTAIS DERIVADOS")
    print("=" * 110)
    ta = tf = tdesc = tpend_sem = 0.0
    pend_a = pend_f = 0.0
    for it in novo["itens"]:
        ag = agregados(it)
        ta += ag["pago_adeildo"] or 0
        tf += ag["pago_faustino"] or 0
        tdesc += ag["desconto_ou_baixa_sem_saida_caixa"] or 0
        tpend_sem += ag["pendente_sem_pagador"] or 0
        pend_a += ag["pendente_adeildo"] or 0
        pend_f += ag["pendente_faustino"] or 0
    print("  Adeildo  desembolsado confirmado : %s" % brl(round(ta, 2)))
    print("  Faustino desembolsado confirmado : %s" % brl(round(tf, 2)))
    print("  Bonificacao/baixa sem caixa      : %s" % brl(round(tdesc, 2)))
    print("  Pendente atribuido a Adeildo     : %s" % brl(round(pend_a, 2)))
    print("  Pendente atribuido a Faustino    : %s" % brl(round(pend_f, 2)))
    print("  Pendente sem pagador definido    : %s" % brl(round(tpend_sem, 2)))

    print("\n" + "=" * 110)
    print("VERIFICACOES")
    print("=" * 110)
    erros = verificar(novo)
    for ok, msg in erros:
        print("  [%s] %s" % ("OK " if ok else "ERRO", msg))
    return all(ok for ok, _ in erros)


def verificar(novo):
    itens = {it["id"]: it for it in novo["itens"]}
    res = []

    def check(cond, msg):
        res.append((bool(cond), msg))

    # duplicidade de id
    ids = [it["id"] for it in novo["itens"]]
    check(len(ids) == len(set(ids)), "sem id de item duplicado (%d itens)" % len(ids))

    # duplicidade de movimento dentro do item
    for it in novo["itens"]:
        refs = [m["ref"] for m in it.get("movimentos", [])]
        check(len(refs) == len(set(refs)),
              "%s: sem ref de movimento duplicada (%d)" % (it["id"], len(refs)))

    # Contabilidade
    c = agregados(itens["contab-001"])
    check(c["pago_adeildo"] == 1040.00, "contabilidade Adeildo = R$ 1.040,00 (%s)" % brl(c["pago_adeildo"]))
    check(c["pago_faustino"] == 1560.00, "contabilidade Faustino = R$ 1.560,00 (%s)" % brl(c["pago_faustino"]))
    check(round((c["pago_adeildo"] or 0) + (c["pago_faustino"] or 0), 2) == 2600.00,
          "contabilidade total = R$ 2.600,00")

    # Impostos
    i = agregados(itens["imposto-001"])
    check(i["pago_faustino"] == 50.06, "impostos Faustino = R$ 50,06 (%s)" % brl(i["pago_faustino"]))
    check(i["pago_adeildo"] == 0, "impostos Adeildo = R$ 0,00 (fato confirmado)")

    # Mais Laudo
    ml = agregados(itens["maislaudo-001"])
    check(ml["pago_faustino"] == 130.00, "Mais Laudo Faustino = R$ 130,00 (%s)" % brl(ml["pago_faustino"]))
    check(len(itens["maislaudo-001"]["movimentos"]) == 6,
          "Mais Laudo com 6 movimentos (meses de R$ 0,00 nao geram lancamento)")

    # Koko
    k = itens["koko-001"]
    kg = agregados(k)
    check(k["valor_total"] == 9500.00, "Koko custo efetivo = R$ 9.500,00")
    check(k["valor_nominal"] == 10000.00, "Koko valor nominal = R$ 10.000,00")
    check(kg["pago_adeildo"] == 1500.00, "Koko pago Adeildo = R$ 1.500,00 (%s)" % brl(kg["pago_adeildo"]))
    check(kg["pago_faustino"] == 6500.00, "Koko pago Faustino = R$ 6.500,00 (%s)" % brl(kg["pago_faustino"]))
    check(kg["desconto_ou_baixa_sem_saida_caixa"] == 500.00, "Koko bonificacao = R$ 500,00")
    check(kg["pendente_sem_pagador"] == 1500.00, "Koko pendente = R$ 1.500,00 (%s)" % brl(kg["pendente_sem_pagador"]))
    caixa = round((kg["pago_adeildo"] or 0) + (kg["pago_faustino"] or 0), 2)
    check(caixa == 8000.00, "Koko desembolsado em dinheiro = R$ 8.000,00 (%s)" % brl(caixa))
    check(round(caixa + (kg["pendente_sem_pagador"] or 0), 2) == 9500.00,
          "Koko conferencia 8.000 + 1.500 = 9.500 (custo efetivo)")
    check(len(k["movimentos"]) == 20, "Koko com 20 posicoes")
    check(sum(1 for m in k["movimentos"] if m["status"] == BAIXA) == 1,
          "Koko com exatamente 1 posicao baixada por desconto")
    check(all(m["valor"] == 0.0 for m in k["movimentos"] if m["status"] == BAIXA),
          "Koko: parcela bonificada com saida de caixa R$ 0,00 (nao e pagamento)")

    # MIR e seringa: nenhuma parcela 'pago' sem confirmacao financeira
    for pid, total, n in (("espi-001", 12464.61, 10), ("cal-001", 1997.00, 10)):
        it = itens[pid]
        movs = it["movimentos"]
        check(not any(m["status"] == PAGO for m in movs),
              "%s: nenhuma parcela marcada como paga sem confirmacao" % pid)
        check(sum(1 for m in movs if m["status"] == AGUARDANDO) == 4,
              "%s: 4 parcelas vencidas aguardando confirmacao (jul..out/2026)" % pid)
        check(len(movs) == n, "%s: %d parcelas no calendario" % (pid, n))
        check(all(m["vencimento"][-2:] == "10" for m in movs),
              "%s: todo vencimento no dia 10" % pid)
        check(it["responsavel"] == A, "%s: responsavel corrente = Adeildo" % pid)

    # CREMERJ preservado
    cr = agregados(itens["cremerj-001"])
    check(cr["pago_faustino"] == 2296.06, "CREMERJ preservado em Faustino = R$ 2.296,06")

    # recorrentes sem historico -> null, nunca 0 inventado
    for rid in ("ws-001", "ts-001", "vps-001"):
        ag = agregados(itens[rid])
        check(ag["pago_adeildo"] is None and ag["pago_faustino"] is None,
              "%s: desembolso NAO confirmado = null (nunca R$ 0,00 inventado)" % rid)
        check(itens[rid]["responsavel"] == A, "%s: responsavel corrente = Adeildo" % rid)
    check(itens["vps-001"].get("periodicidade") == "anual" and itens["vps-001"]["valor_total"] == 635.00,
          "Hostinger VPS: periodicidade anual e R$ 635,00 preservados")

    # nenhum segredo remanescente
    blob = json.dumps(novo, ensure_ascii=False).lower()
    for termo in ("codigo_autorizacao", "id_99pay", "pix_antecipado", "cnpj_favorecido"):
        check(termo not in blob, "sem campo sensivel '%s'" % termo)
    # "Boleto via 99Pay" e forma de pagamento (historico legitimo), nao segredo:
    # o que bloqueia e o ID da transacao / codigo de autorizacao, removidos acima.

    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="grava o arquivo (default: dry-run)")
    args = ap.parse_args()

    with open(PRIVATE, encoding="utf-8") as fh:
        antigo = json.load(fh)

    novo = reconciliar(antigo)
    ok = imprimir_dry_run(antigo, novo)

    if not args.apply:
        print("\nDRY-RUN apenas. Use --apply para gravar.")
        return 0 if ok else 1

    if not ok:
        print("\nABORTADO: verificacoes falharam. Nada foi gravado.")
        return 1

    backup = PRIVATE + ".bak-m26-21"
    if not os.path.exists(backup):
        with open(backup, "w", encoding="utf-8") as fh:
            json.dump(antigo, fh, ensure_ascii=False, indent=2)
        os.chmod(backup, 0o600)
        print("\nBackup: %s" % backup)
    with open(PRIVATE, "w", encoding="utf-8") as fh:
        json.dump(novo, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.chmod(PRIVATE, 0o600)
    print("Gravado: %s" % PRIVATE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
