#!/usr/bin/env python3
"""
Gerador do resumo seguro de Custos & Investimentos (M26.21).

Entrada : painel-soprolife/data-private/custos-investimentos.local.json
          (livro-caixa unico por item em `movimentos`)
Saida   : painel-soprolife/data/custos-investimentos-summary.local.json

TODO agregado do painel e DERIVADO dos movimentos reais. Nada e hardcoded para
bater com um print: mudar um movimento e rodar o gerador muda o painel.

Regras nao negociaveis (skill soprolife-finance-costs):
  - "responsavel" (quem administra o item) NUNCA e usado como "quem pagou".
    Pagador vem do campo `pagador` de cada movimento.
  - desconto/bonificacao NUNCA entra como pagamento: vai para
    `desconto_ou_baixa_sem_saida_caixa`, derivado de `valor_nominal` dos
    movimentos com status `baixada_por_desconto` (cuja saida de caixa e 0,00).
  - valor desconhecido -> null (o painel mostra "-"), nunca R$ 0,00 inventado.
    Itens com `historico_confirmado: false` emitem null em pago_*.
  - parcela vencida sem confirmacao financeira NAO e "paga": fica em
    `vencido_aguardando_confirmacao` e conta como saldo em aberto.

Uso:
    python3 painel-soprolife/scripts/generate-custos-investimentos.py
    python3 painel-soprolife/scripts/generate-custos-investimentos.py --check
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

PRIVATE = os.path.join(ROOT, "data-private", "custos-investimentos.local.json")
SUMMARY = os.path.join(ROOT, "data", "custos-investimentos-summary.local.json")

ADEILDO = "Adeildo"
FAUSTINO = "Faustino"
SOCIOS = (ADEILDO, FAUSTINO)

PAGO = "pago"
PENDENTE = "pendente"
AGUARDANDO = "vencido_aguardando_confirmacao"
BAIXA = "baixada_por_desconto"
ABERTOS = (PENDENTE, AGUARDANDO)

CATEGORIA_LABEL = {
    "recorrente": "Recorrente",
    "infraestrutura": "Infraestrutura",
    "equipamento": "Equipamento",
    "regularizacao": "Regularização",
    "tributo": "Tributo",
    "servico": "Serviço",
}

MES_LABEL = ["jan", "fev", "mar", "abr", "mai", "jun",
             "jul", "ago", "set", "out", "nov", "dez"]


def r2(v):
    return round(v + 0.0, 2)


def label_competencia(comp):
    if not comp:
        return None
    ano, mes = int(comp[:4]), int(comp[5:])
    return "%s/%d" % (MES_LABEL[mes - 1], ano)


def label_inicio(iso):
    if not iso:
        return "—"
    return label_competencia(iso[:7]).capitalize()


def soma(movs, pred):
    return r2(sum(m["valor"] for m in movs if pred(m)))


def agregar_item(item):
    """Deriva os agregados de rateio de um item a partir do seu livro-caixa."""
    movs = item.get("movimentos", []) or []
    confirmado = bool(item.get("historico_confirmado"))
    tipo_valor = item.get("tipo_valor", "unico")

    pago = {s: soma(movs, lambda m, s=s: m["status"] == PAGO and m["pagador"] == s)
            for s in SOCIOS}
    pendente = {s: soma(movs, lambda m, s=s: m["status"] in ABERTOS and m["pagador"] == s)
                for s in SOCIOS}
    pendente_sem = soma(movs, lambda m: m["status"] in ABERTOS and m["pagador"] is None)
    desconto = r2(sum(m.get("valor_nominal", 0) for m in movs if m["status"] == BAIXA))

    # Historico nao confirmado => NADA se sabe sobre esse item: nem quanto foi
    # desembolsado, nem quanto esta em aberto. Zero seria uma afirmacao, e
    # afirmacao sem dado e invencao. Tudo vira null e o painel mostra "-".
    if not confirmado:
        pago = {s: None for s in SOCIOS}
        pendente = {s: None for s in SOCIOS}
        pendente_sem = None

    # Recorrente/variavel sem total fechado => saldo em aberto nao e calculavel.
    if tipo_valor in ("mensal", "variavel"):
        pendente = {s: None for s in SOCIOS}
        pendente_sem = None

    return {
        "pago": pago,
        "pendente": pendente,
        "pendente_sem_pagador": pendente_sem,
        "desconto": desconto,
        "confirmado": confirmado,
    }


def progresso_parcelas(item):
    """Progresso de um item parcelado, derivado dos movimentos."""
    movs = item.get("movimentos", []) or []
    total = item.get("parcelas_total")
    if not total:
        return None
    pagas = sum(1 for m in movs if m["status"] == PAGO)
    aguardando = sum(1 for m in movs if m["status"] == AGUARDANDO)
    baixadas = sum(1 for m in movs if m["status"] == BAIXA)
    abertas = [m for m in movs if m["status"] in ABERTOS]
    # Dois conceitos DIFERENTES, deliberadamente separados (M26.21):
    #  - proxima_*            : o proximo vencimento AINDA NO FUTURO;
    #  - mais_antiga_em_aberto_*: a posicao em aberto mais antiga, que pode ja
    #    ter vencido e estar aguardando confirmacao.
    # Mostrar so a mais antiga como "proxima" esconderia o atraso; mostrar so a
    # futura esconderia a fila acumulada. Sem vencimento cadastrado (ex.: Koko,
    # posicoes sem data definida) os dois ficam null — nao se inventa data.
    com_venc = sorted([m for m in abertas if m.get("vencimento")],
                      key=lambda m: m["vencimento"])
    hoje_iso = date(2026, 10, 12).isoformat()
    futuras = [m for m in com_venc if m["vencimento"] > hoje_iso]
    proxima = futuras[0] if futuras else None
    mais_antiga = com_venc[0] if com_venc else None
    total_pago = soma(movs, lambda m: m["status"] == PAGO)
    saldo = soma(movs, lambda m: m["status"] in ABERTOS)
    return {
        "parcelas_total": total,
        "parcelas_pagas": pagas,
        "parcelas_aguardando_confirmacao": aguardando,
        "parcelas_baixadas_sem_caixa": baixadas,
        "parcelas_em_aberto": len(abertas),
        "parcela_atual": "%d/%d" % (pagas + baixadas, total),
        "proxima_ref": proxima["ref"] if proxima else None,
        "proxima_competencia": label_competencia(proxima["competencia"]) if proxima else None,
        "proximo_vencimento": proxima["vencimento"] if proxima else None,
        "mais_antiga_em_aberto_ref": mais_antiga["ref"] if mais_antiga else None,
        "mais_antiga_em_aberto_competencia": label_competencia(mais_antiga["competencia"]) if mais_antiga else None,
        "mais_antiga_em_aberto_vencimento": mais_antiga["vencimento"] if mais_antiga else None,
        "dia_vencimento": item.get("dia_vencimento_fatura"),
        "total_pago": total_pago,
        "saldo_restante": saldo,
    }


def historico_seguro(item):
    """Historico de parcelas sem nenhum dado sensivel (sem id de transacao, sem Pix)."""
    out = []
    for m in item.get("movimentos", []) or []:
        out.append({
            "ref": m["ref"],
            "competencia": label_competencia(m.get("competencia")),
            "vencimento": m.get("vencimento"),
            "data_pagamento": m.get("data"),
            "valor": m["valor"],
            "valor_nominal": m.get("valor_nominal"),
            "pagador": m.get("pagador"),
            "status": m["status"],
        })
    return out


def gerar(doc):
    itens = doc["itens"]
    hoje = date(2026, 10, 12)

    rateio_itens = []
    itens_resumo = []
    parcelamentos = []

    total_mensal_recorrente = 0.0
    total_mensal_parcelas = 0.0
    total_investido_equip = 0.0
    por_categoria = {}
    por_responsavel = {}

    desembolsado = {s: 0.0 for s in SOCIOS}
    pendente_atribuido = {s: 0.0 for s in SOCIOS}
    compromisso = {s: 0.0 for s in SOCIOS}
    itens_vinculados = {s: 0 for s in SOCIOS}
    bonificacao = {s: 0.0 for s in SOCIOS}
    nao_confirmado = {s: [] for s in SOCIOS}
    compromisso_compartilhado = 0.0
    pendente_sem_pagador_total = 0.0

    for item in itens:
        ag = agregar_item(item)
        cat = CATEGORIA_LABEL.get(item["categoria"], item["categoria"])
        mensal = item.get("valor_mensal")
        resp = item.get("responsavel") or "—"
        prog = progresso_parcelas(item)

        # --- totais mensais ---
        encerrado = item.get("status") in ("pago", "encerrado")
        mensal_ativo = 0.0
        if mensal and not encerrado:
            tem_aberto = prog is None or prog["parcelas_em_aberto"] > 0
            if tem_aberto:
                mensal_ativo = mensal
                if cat == "Equipamento":
                    total_mensal_parcelas += mensal
                else:
                    total_mensal_recorrente += mensal

        if cat == "Equipamento":
            total_investido_equip += item.get("valor_total") or 0.0

        por_categoria.setdefault(cat, 0.0)
        por_categoria[cat] += mensal_ativo

        pr = por_responsavel.setdefault(resp, {"total_mensal": 0.0, "itens": 0})
        pr["total_mensal"] += mensal_ativo
        pr["itens"] += 1

        # --- rateio por socio (derivado dos movimentos, nunca do "responsavel") ---
        for s in SOCIOS:
            if ag["pago"][s] is None:
                if resp == s or s in resp:
                    nao_confirmado[s].append(item["nome"])
            else:
                desembolsado[s] += ag["pago"][s]
            if ag["pendente"][s]:
                pendente_atribuido[s] += ag["pendente"][s]
        pendente_sem_pagador_total += ag["pendente_sem_pagador"] or 0.0

        # compromisso mensal: item de responsavel unico vai para o socio;
        # responsabilidade compartilhada/indefinida vai para o bucket comum.
        if mensal_ativo:
            unico = [s for s in SOCIOS if resp == s]
            if len(unico) == 1:
                compromisso[unico[0]] += mensal_ativo
            else:
                compromisso_compartilhado += mensal_ativo

        for s in SOCIOS:
            toca = (ag["pago"][s] not in (None, 0)) or bool(ag["pendente"][s]) or (resp == s) or (s in resp)
            if toca:
                itens_vinculados[s] += 1

        rateio_itens.append({
            "item_id": item["id"],
            "item": item["nome"],
            "tipo_valor": item.get("tipo_valor", "unico"),
            "valor_total": item.get("valor_total"),
            "valor_mensal": mensal,
            "pago_adeildo": ag["pago"][ADEILDO],
            "pago_faustino": ag["pago"][FAUSTINO],
            "pendente_adeildo": ag["pendente"][ADEILDO],
            "pendente_faustino": ag["pendente"][FAUSTINO],
            "pendente_sem_pagador": ag["pendente_sem_pagador"],
            "desconto_ou_baixa_sem_saida_caixa": ag["desconto"],
            "historico_confirmado": ag["confirmado"],
            "observacao_curta": item.get("obs_curta") or resumo_curto(item, ag, prog),
        })

        itens_resumo.append({
            "id": item["id"],
            "nome": item["nome"],
            "categoria": cat,
            "responsavel": resp,
            "valor_total": item.get("valor_total"),
            "valor_mensal": mensal,
            "tipo_valor": item.get("tipo_valor", "unico"),
            "parcelas_total": item.get("parcelas_total"),
            "parcelas_pagas": prog["parcelas_pagas"] if prog else None,
            "parcelas_aguardando_confirmacao": prog["parcelas_aguardando_confirmacao"] if prog else None,
            "saldo_restante": prog["saldo_restante"] if prog else None,
            "inicio": label_inicio(item.get("inicio")),
            "status": item.get("status"),
            "historico_confirmado": ag["confirmado"],
            "obs_curta": resumo_curto(item, ag, prog),
        })

        if prog and item.get("dia_vencimento_fatura"):
            parcelamentos.append({
                "item_id": item["id"],
                "item": item["nome"],
                "responsavel": resp,
                "forma_pagamento": "Cartão de crédito — fatura com pagamento automático",
                "valor_total": item.get("valor_total"),
                "valor_parcela": mensal,
                "divergencia_arredondamento": item.get("divergencia_arredondamento"),
                **prog,
                "historico": historico_seguro(item),
            })
        elif prog and item.get("parcelas_total"):
            parcelamentos.append({
                "item_id": item["id"],
                "item": item["nome"],
                "responsavel": resp,
                "forma_pagamento": "Parcelamento direto com o fornecedor",
                "valor_total": item.get("valor_total"),
                "valor_parcela": mensal,
                "valor_nominal": item.get("valor_nominal"),
                "bonificacao": ag["desconto"] or None,
                **prog,
                "historico": historico_seguro(item),
            })

    rateio_socios = []
    for s in SOCIOS:
        rateio_socios.append({
            "nome": s,
            "total_desembolsado": r2(desembolsado[s]),
            "compromisso_mensal": r2(compromisso[s]),
            "saldo_pendente_atribuido": r2(pendente_atribuido[s]),
            "pendente_sem_pagador_relacionado": r2(pendente_sem_pagador_total),
            "itens_vinculados": itens_vinculados[s],
            "itens_sem_historico_confirmado": len(nao_confirmado[s]),
        })

    alertas = montar_alertas(doc, rateio_itens, parcelamentos, nao_confirmado)

    summary = {
        "source": {
            "type": "local_safe_custos_summary",
            "safeToDisplay": True,
            "containsPersonalData": False,
            "containsBankingData": False,
            "generatedAt": datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
            "generator": "generate-custos-investimentos.py",
            "modelo": "m26-21-livro-caixa",
            "nota": "Valores agregados derivados do livro-caixa privado. Sem dados bancários ou sensíveis.",
        },
        "competencia_referencia": label_competencia(hoje.isoformat()[:7]),
        "total_mensal_recorrente": r2(total_mensal_recorrente),
        "total_mensal_parcelas": r2(total_mensal_parcelas),
        "total_mensal_atual": r2(total_mensal_recorrente + total_mensal_parcelas),
        "total_investido_equipamentos": r2(total_investido_equip),
        "parcelas_mensais_equipamentos": r2(total_mensal_parcelas),
        "total_desembolsado_confirmado": r2(sum(desembolsado.values())),
        "total_bonificacao_sem_caixa": r2(sum(i["desconto_ou_baixa_sem_saida_caixa"] or 0 for i in rateio_itens)),
        "compromisso_mensal_compartilhado": r2(compromisso_compartilhado),
        "pendente_sem_pagador": r2(pendente_sem_pagador_total),
        "itens_ativos": len(itens),
        "pendencias_cadastro": len(doc.get("pendencias", [])),
        "por_categoria": [{"categoria": k, "total_mensal": r2(v)}
                          for k, v in sorted(por_categoria.items())],
        "por_responsavel": [{"responsavel": k, "total_mensal": r2(v["total_mensal"]), "itens": v["itens"]}
                            for k, v in sorted(por_responsavel.items())],
        "alertas": alertas,
        "itens": itens_resumo,
        "parcelamentos": parcelamentos,
        "rateio_socios": rateio_socios,
        "rateio_itens": rateio_itens,
        "pendencias": [{"item": p["item"], "responsavel": p["responsavel"],
                        "descricao": p["descricao"], "status": p["status"],
                        "prioridade": p["prioridade"]}
                       for p in doc.get("pendencias", [])],
    }
    return summary


def resumo_curto(item, ag, prog):
    """Observacao curta derivada — sem texto livre sensivel."""
    if not ag["confirmado"]:
        return "Desembolso histórico não confirmado — ver coluna Responsável"
    if prog:
        partes = ["%s pagas" % prog["parcela_atual"]]
        if prog["parcelas_aguardando_confirmacao"]:
            partes.append("%d vencida(s) aguardando confirmação" % prog["parcelas_aguardando_confirmacao"])
        if prog["parcelas_baixadas_sem_caixa"]:
            partes.append("%d por bonificação" % prog["parcelas_baixadas_sem_caixa"])
        abertas_sem_pagador = ag["pendente_sem_pagador"] or 0
        if abertas_sem_pagador:
            partes.append("saldo sem pagador definido")
        return " · ".join(partes)
    movs = item.get("movimentos", []) or []
    pagos = [m for m in movs if m["status"] == PAGO]
    if pagos:
        return "%d competência(s) paga(s) — histórico reconciliado" % len(pagos)
    return "Sem movimento registrado — ver coluna Responsável"


def montar_alertas(doc, rateio_itens, parcelamentos, nao_confirmado):
    alertas = []
    for p in parcelamentos:
        if p.get("parcelas_aguardando_confirmacao"):
            alertas.append(
                "%s: %d parcela(s) com vencimento já passado aguardando confirmação financeira — "
                "não marcadas como pagas automaticamente" % (p["item"], p["parcelas_aguardando_confirmacao"]))
    sem_pagador = [i for i in rateio_itens if i.get("pendente_sem_pagador")]
    for i in sem_pagador:
        alertas.append("%s: saldo em aberto sem pagador definido" % i["item"])
    for s, nomes in nao_confirmado.items():
        if nomes:
            alertas.append("%s: histórico de desembolso não confirmado em %d item(ns) — painel mostra “—”, "
                           "não R$ 0,00" % (s, len(nomes)))
    for p in parcelamentos:
        if p.get("divergencia_arredondamento"):
            alertas.append("%s: soma das parcelas difere do valor total cadastrado por arredondamento — "
                           "divergência registrada, não corrigida" % p["item"])
    return alertas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="valida sem gravar")
    args = ap.parse_args()

    with open(PRIVATE, encoding="utf-8") as fh:
        doc = json.load(fh)
    summary = gerar(doc)

    try:
        import pii_guard
        pii_guard.ensure_summary_safe(
            summary,
            rules=pii_guard._FILE_RULESETS["custos-investimentos-summary"],
            context="custos-investimentos-summary (M26.21)",
        )
    except ImportError:
        print("AVISO: pii_guard nao importado — validacao de PII nao executada.", file=sys.stderr)
        return 1

    if args.check:
        print("OK — resumo gerado e validado em memoria (nada gravado).")
        print(json.dumps({k: summary[k] for k in (
            "total_mensal_atual", "total_desembolsado_confirmado",
            "total_bonificacao_sem_caixa", "pendente_sem_pagador")},
            ensure_ascii=False, indent=2))
        return 0

    with open(SUMMARY, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("Gravado: %s" % SUMMARY)
    return 0


if __name__ == "__main__":
    sys.exit(main())
