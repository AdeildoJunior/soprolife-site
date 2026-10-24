#!/usr/bin/env python3
"""
Gerador do resumo seguro de Custos & Investimentos (M26.21).

Entrada : painel-soprolife/data-private/custos-investimentos.local.json
          (livro-caixa unico por item em `movimentos`)
Saida   : painel-soprolife/data/custos-investimentos-summary.local.json   (marca SoproLife)
          painel-soprolife/data/custos-investimentos-eeglife.local.json   (marca EEGLife)

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
  - UMA MARCA POR ARQUIVO (M26.37). Cada item declara `marca` e cai em UM
    unico resumo. A marca e o ESCOPO do arquivo, nao uma coluna: o painel da
    SoproLife le o resumo da SoproLife e nunca ve item da EEGLife, e o modulo
    da EEGLife le o resumo da EEGLife e nunca ve item da SoproLife. Como a
    particao e exclusiva, nenhum ativo pode ser contado duas vezes - nao por
    disciplina de quem soma, mas porque ele existe em um arquivo so.

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

# Uma marca, um arquivo. O nome do resumo da SoproLife NAO muda: ele ja e o
# que o painel le hoje, e renomea-lo quebraria a tela por nada.
MARCA_PADRAO = "soprolife"
MARCAS = {
    "soprolife": {
        "rotulo": "SoproLife",
        "saida": os.path.join(ROOT, "data", "custos-investimentos-summary.local.json"),
    },
    "eeglife": {
        "rotulo": "EEGLife",
        "saida": os.path.join(ROOT, "data", "custos-investimentos-eeglife.local.json"),
    },
}

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
    # Insumo descartavel consumido no exame (M26.37). NAO e equipamento e por
    # isso NAO entra em `total_investido_equipamentos`: filtro de espirometria
    # se gasta, nao se imobiliza.
    "insumo": "Insumo",
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


def marca_do(obj):
    """Marca de um item ou pendencia. Ausente => SoproLife.

    O default existe porque os dez itens anteriores a M26.37 sao todos da
    SoproLife, e seria errado exigir que um cadastro antigo declarasse algo
    que ainda nao existia. Marca DESCONHECIDA, ao contrario, e erro duro: um
    item com a marca mal digitada sairia dos dois resumos em silencio, e um
    custo que desaparece e pior do que um custo no lugar errado.
    """
    m = obj.get("marca") or MARCA_PADRAO
    if m not in MARCAS:
        raise SystemExit("ERRO: marca desconhecida %r no registro %r."
                         % (m, obj.get("id")))
    return m


def posicoes(movs):
    """Agrupa movimentos por POSICAO de parcela (`ref`), na ordem de entrada.

    Ate a M26.37 cada posicao tinha exatamente um movimento, e contar
    movimento era o mesmo que contar parcela. O Nano EEG quebrou isso: a
    compra de 10x foi rateada entre dois socios, entao cada mes tem DOIS
    movimentos (um cartao de cada um). Contar movimento diria "0/20" num
    plano de 10 parcelas. Posicao e a unidade certa, e para item de pagador
    unico o resultado e identico ao de antes.
    """
    ordem, grupos = [], {}
    for m in movs:
        ref = m["ref"]
        if ref not in grupos:
            ordem.append(ref)
            grupos[ref] = []
        grupos[ref].append(m)
    return [grupos[r] for r in ordem]


def planos_por_pagador(item):
    """O plano de parcelas de cada pagador, derivado dos movimentos.

    Um ativo comprado em 10x e rateado entre dois socios tem UM valor total e
    DOIS planos de parcela. O que se divide e quem paga cada posicao, nao o
    ativo. Sem isto o unico numero visivel seria a parcela somada
    (R$ 2.429,00), que nenhum dos dois socios paga.
    """
    movs = item.get("movimentos", []) or []
    ordem, por = [], {}
    for m in movs:
        quem = m.get("pagador")
        if quem is None:
            continue
        if quem not in por:
            ordem.append(quem)
            por[quem] = {"parcelas": 0, "valores": [], "total": 0.0,
                         "pagas": 0, "em_aberto": 0}
        d = por[quem]
        d["parcelas"] += 1
        d["valores"].append(m["valor"])
        d["total"] += m["valor"]
        if m["status"] == PAGO:
            d["pagas"] += 1
        elif m["status"] in ABERTOS:
            d["em_aberto"] += 1
    planos = []
    for quem in ordem:
        d = por[quem]
        distintos = sorted(set(d["valores"]))
        planos.append({
            "pagador": quem,
            "parcelas": d["parcelas"],
            # Valor da parcela so quando ele e UM. Plano com parcelas de
            # valores diferentes nao ganha uma media inventada: fica null e a
            # tela mostra "-", com o historico ao lado para quem quiser ver.
            "valor_parcela": distintos[0] if len(distintos) == 1 else None,
            "total": r2(d["total"]),
            "parcelas_pagas": d["pagas"],
            "parcelas_em_aberto": d["em_aberto"],
        })
    return planos


def mensal_por_pagador(item):
    """Compromisso mensal de cada socio, derivado das posicoes EM ABERTO.

    Devolve vazio quando o fato NAO esta no livro-caixa — recorrente sem
    parcela, ou posicao em aberto sem pagador designado (Koko 13-15). Nesse
    caso quem decide e a regra do `responsavel`, que diz quem ADMINISTRA o
    item e nao quem paga; por isso ela e o ultimo recurso, nunca o primeiro.
    """
    pos = [g for g in posicoes(item.get("movimentos", []) or [])
           if any(m["status"] in ABERTOS for m in g)]
    if not pos:
        return {}
    por = {}
    for g in pos:
        for m in g:
            if m["status"] not in ABERTOS:
                continue
            if m.get("pagador") not in SOCIOS:
                return {}
            por[m["pagador"]] = por.get(m["pagador"], 0.0) + m["valor"]
    return {s: r2(v / len(pos)) for s, v in por.items()}


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
    pos = posicoes(movs)
    # Uma posicao so esta "paga" quando TODOS os seus movimentos foram pagos:
    # com rateio, o mes de um socio pago e o do outro em aberto nao fecha a
    # parcela. Para item de pagador unico cada posicao tem um movimento so e
    # estas contas dao exatamente o que davam antes da M26.37.
    pagas = sum(1 for g in pos if all(m["status"] == PAGO for m in g))
    aguardando = sum(1 for g in pos if any(m["status"] == AGUARDANDO for m in g))
    baixadas = sum(1 for g in pos
                   if any(m["status"] == BAIXA for m in g)
                   and not any(m["status"] in ABERTOS for m in g)
                   and not all(m["status"] == PAGO for m in g))
    pos_abertas = [g for g in pos if any(m["status"] in ABERTOS for m in g)]
    abertas = [m for g in pos for m in g if m["status"] in ABERTOS]
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
        "parcelas_em_aberto": len(pos_abertas),
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


def gerar(doc, marca=MARCA_PADRAO):
    """Resumo de UMA marca. Item de outra marca nao e lido aqui."""
    itens = [i for i in doc["itens"] if marca_do(i) == marca]
    pendencias = [p for p in doc.get("pendencias", []) if marca_do(p) == marca]
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

        # Compromisso mensal — o FATO vem primeiro: se cada posicao em aberto
        # tem pagador declarado, o compromisso de cada socio sai do
        # livro-caixa (M26.37). So na ausencia desse fato e que se cai na
        # regra do `responsavel`, e responsabilidade compartilhada vai para o
        # bucket comum em vez de ser rachada no meio por conta propria.
        if mensal_ativo:
            do_livro = mensal_por_pagador(item)
            if do_livro:
                for quem, valor in do_livro.items():
                    compromisso[quem] += valor
            else:
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
            "marca": marca,
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
            "marca": marca,
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
            # Mesma regra que `rateio_itens` ja usava: observacao curta curada
            # pelos socios tem precedencia sobre a derivada, que e generica
            # por natureza ("0/10 pagas" nao conta que sao dois planos).
            "obs_curta": item.get("obs_curta") or resumo_curto(item, ag, prog),
        })

        # `forma_pagamento_resumo` existe para o caso em que nenhum dos dois
        # rotulos fixos abaixo seria verdade — compra no cartao SEM debito
        # automatico, por exemplo. Sem ele, o Nano EEG apareceria como
        # "parcelamento direto com o fornecedor", que nao foi o que aconteceu.
        rotulo_forma = item.get("forma_pagamento_resumo")
        if prog and item.get("dia_vencimento_fatura"):
            parcelamentos.append({
                "item_id": item["id"],
                "item": item["nome"],
                "responsavel": resp,
                "forma_pagamento": rotulo_forma or "Cartão de crédito — fatura com pagamento automático",
                "valor_total": item.get("valor_total"),
                "valor_parcela": mensal,
                "divergencia_arredondamento": item.get("divergencia_arredondamento"),
                **prog,
                "planos_por_pagador": planos_por_pagador(item),
                "historico": historico_seguro(item),
            })
        elif prog and item.get("parcelas_total"):
            parcelamentos.append({
                "item_id": item["id"],
                "item": item["nome"],
                "responsavel": resp,
                "forma_pagamento": rotulo_forma or "Parcelamento direto com o fornecedor",
                "valor_total": item.get("valor_total"),
                "valor_parcela": mensal,
                "valor_nominal": item.get("valor_nominal"),
                "bonificacao": ag["desconto"] or None,
                **prog,
                "planos_por_pagador": planos_por_pagador(item),
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
            "modelo": "m26-37-livro-caixa-por-marca",
            "escopo_marca": marca,
            "marca_rotulo": MARCAS[marca]["rotulo"],
            "nota": "Valores agregados derivados do livro-caixa privado, no escopo de UMA marca. Sem dados bancários ou sensíveis.",
        },
        "escopo_marca": marca,
        "marca_rotulo": MARCAS[marca]["rotulo"],
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
        "pendencias_cadastro": len(pendencias),
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
                       for p in pendencias],
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

    # A particao por marca e conferida ANTES de gerar qualquer coisa: a unica
    # forma de um ativo ser contado duas vezes seria ele cair em dois resumos.
    ids = [i["id"] for i in doc["itens"]]
    if len(ids) != len(set(ids)):
        print("ERRO: id de item repetido no livro-caixa.")
        return 1
    por_marca = {m: [i["id"] for i in doc["itens"] if marca_do(i) == m] for m in MARCAS}
    cobertos = [i for lista in por_marca.values() for i in lista]
    if sorted(cobertos) != sorted(ids):
        print("ERRO: a particao por marca nao cobre exatamente uma vez cada item.")
        return 1

    resumos = {m: gerar(doc, m) for m in MARCAS}

    # Validacao tudo-ou-nada: TODOS os resumos passam pelo guarda antes de
    # QUALQUER gravacao. Gravar um e abortar no outro deixaria o painel de uma
    # marca novo e o da outra velho.
    try:
        import pii_guard
    except ImportError:
        print("AVISO: pii_guard nao importado — validacao de PII nao executada.", file=sys.stderr)
        return 1
    for m, resumo in resumos.items():
        pii_guard.ensure_summary_safe(
            resumo,
            rules=pii_guard._FILE_RULESETS["custos-investimentos-summary"],
            context="custos-investimentos-summary/%s (M26.37)" % m,
        )

    if args.check:
        print("OK — resumos gerados e validados em memoria (nada gravado).")
        for m, resumo in resumos.items():
            print("\n[%s] %d item(ns)" % (MARCAS[m]["rotulo"], resumo["itens_ativos"]))
            print(json.dumps({k: resumo[k] for k in (
                "total_mensal_atual", "total_investido_equipamentos",
                "total_desembolsado_confirmado",
                "total_bonificacao_sem_caixa", "pendente_sem_pagador")},
                ensure_ascii=False, indent=2))
        return 0

    for m, resumo in resumos.items():
        saida = MARCAS[m]["saida"]
        with open(saida, "w", encoding="utf-8") as fh:
            json.dump(resumo, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print("Gravado (%s): %s" % (MARCAS[m]["rotulo"], saida))
    return 0


if __name__ == "__main__":
    sys.exit(main())
