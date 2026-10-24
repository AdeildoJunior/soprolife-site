#!/usr/bin/env python3
"""
M26.37 — testes dos dois fatos economicos de 23/10/2026 e do escopo por marca.

Offline, sem rede. Valida:

  A) 200 filtros descartaveis do espirometro KoKo — SoproLife, INSUMO,
     R$ 1.555,00, desembolso de Faustino;
  B) Nano EEG iCelera — EEGLife, EQUIPAMENTO, R$ 24.290,00 contado UMA vez,
     rateado em dois planos de 10x (Adeildo R$ 1.200,00 + Faustino R$ 1.229,00);
  C) a particao por marca: cada item em exatamente um resumo, nenhum total
     somando o mesmo ativo duas vezes;
  D) que nenhum fato anterior a esta missao mudou de valor.

A prova de nao duplicidade e estrutural, nao aritmetica: o Nano EEG existe em
UM arquivo so. Nao ha como um total dobrar para R$ 48.580,00 porque nao ha um
segundo registro para somar.

Uso:
    python3 painel-soprolife/scripts/test-custos-marcas-m26-37.py
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIVATE = os.path.join(ROOT, "data-private", "custos-investimentos.local.json")
SUMMARY_SOPRO = os.path.join(ROOT, "data", "custos-investimentos-summary.local.json")
SUMMARY_EEG = os.path.join(ROOT, "data", "custos-investimentos-eeglife.local.json")
GERADOR = os.path.join(ROOT, "scripts", "generate-custos-investimentos.py")
EEG_JS = os.path.join(ROOT, "js", "eeg-operacao.js")

A, F = "Adeildo", "Faustino"

# Impressao economica dos dez itens que existiam ANTES da M26.37, tirada do
# backup pre-missao. Ordem: valor_total, valor_mensal, responsavel, numero de
# movimentos, soma dos movimentos, parcelas_total, status. Se qualquer um
# mudar, esta missao mexeu em historico — e nao era para mexer.
ANTES = {
    "ws-001":       [None, 32.72, A, 0, 0, None, "ativo"],
    "ts-001":       [None, 42.0, A, 0, 0, None, "ativo"],
    "vps-001":      [635.0, 52.92, A, 0, 0, None, "ativo"],
    "contab-001":   [None, 260.0, "Faustino e Adeildo", 10, 2600.0, None, "ativo"],
    "imposto-001":  [None, None, F, 3, 50.06, None, "ativo"],
    "maislaudo-001": [None, None, F, 6, 130.0, None, "ativo"],
    "espi-001":     [12464.61, 1246.46, A, 10, 12464.6, 10, "parcelado"],
    "cal-001":      [1997.0, 199.7, A, 10, 1997.0, 10, "parcelado"],
    "koko-001":     [9500.0, 500.0, "Faustino e Adeildo", 20, 9500.0, 20, "parcelado"],
    "cremerj-001":  [2296.06, None, F, 1, 2296.06, None, "pago"],
}

falhas = []
total = 0


def check(cond, msg):
    global total
    total += 1
    if not cond:
        falhas.append(msg)
        print("  FAIL: %s" % msg)
    else:
        print("  PASS: %s" % msg)


def main():
    priv = json.load(open(PRIVATE, encoding="utf-8"))
    sopro = json.load(open(SUMMARY_SOPRO, encoding="utf-8"))
    eeg = json.load(open(SUMMARY_EEG, encoding="utf-8"))
    itens = {i["id"]: i for i in priv["itens"]}

    print("\n== escopo dos arquivos ==")
    check(sopro["escopo_marca"] == "soprolife", "resumo da SoproLife declara escopo soprolife")
    check(eeg["escopo_marca"] == "eeglife", "resumo da EEGLife declara escopo eeglife")
    check(sopro["marca_rotulo"] == "SoproLife" and eeg["marca_rotulo"] == "EEGLife",
          "cada resumo carrega o rotulo da sua marca")
    check(sopro["source"]["safeToDisplay"] is True and eeg["source"]["safeToDisplay"] is True,
          "os dois resumos sao seguros para exibir")
    check(priv["source"]["safeToDisplay"] is False, "o livro-caixa privado NAO e exibivel")

    print("\n== A) SoproLife: 200 filtros para o espirometro KoKo ==")
    fil = itens.get("filtros-001")
    check(fil is not None, "filtros-001 existe no livro-caixa")
    check(sum(1 for i in priv["itens"] if i["id"] == "filtros-001") == 1,
          "filtros-001 existe UMA unica vez no livro-caixa")
    check(fil["marca"] == "soprolife", "filtros: marca SoproLife")
    check(fil["valor_total"] == 1555.00, "filtros: R$ 1.555,00")
    check(fil["quantidade"] == 200, "filtros: 200 unidades")
    check(fil["custo_unitario"] == 7.775, "filtros: custo unitario R$ 7,775")
    check(round(fil["quantidade"] * fil["custo_unitario"], 2) == 1555.00,
          "filtros: 200 x 7,775 = R$ 1.555,00 (o unitario fecha com o total)")
    check(fil["categoria"] == "insumo", "filtros: categoria 'insumo'")
    check(fil["categoria"] != "equipamento", "filtros: NAO e equipamento")
    check(fil["responsavel"] == F, "filtros: responsavel Faustino")
    check(fil["inicio"] == "2026-10-23", "filtros: data 23/10/2026")
    check(fil.get("parcelas_total") is None, "filtros: nao parcelado")
    check(len(fil["movimentos"]) == 1, "filtros: um unico movimento")
    m = fil["movimentos"][0]
    check(m["valor"] == 1555.00 and m["pagador"] == F and m["status"] == "pago",
          "filtros: desembolso de R$ 1.555,00 por Faustino, confirmado")
    check(m["data"] == "2026-10-23", "filtros: saida de caixa em 23/10/2026")

    rs = {r["id"]: r for r in sopro["itens"]}
    rat_s = {r["item_id"]: r for r in sopro["rateio_itens"]}
    check("filtros-001" in rs, "filtros aparece no resumo da SoproLife")
    check(rs["filtros-001"]["categoria"] == "Insumo", "filtros: rotulo 'Insumo' na tela")
    check(rs["filtros-001"]["valor_total"] == 1555.00, "filtros: R$ 1.555,00 na tela")
    check(rat_s["filtros-001"]["pago_faustino"] == 1555.00,
          "filtros: rateio credita R$ 1.555,00 a Faustino")
    check(rat_s["filtros-001"]["pago_adeildo"] == 0.0,
          "filtros: nada atribuido a Adeildo")
    check("Insumo" in [c["categoria"] for c in sopro["por_categoria"]],
          "'Por categoria' da SoproLife passa a ter Insumo")
    # O ponto economico: filtro se consome, nao se imobiliza.
    check(sopro["total_investido_equipamentos"] == 23961.61,
          "filtros NAO entram em 'investido em equipamentos' (segue R$ 23.961,61)")
    check(sopro["total_desembolsado_confirmado"] == 20415.76,
          "desembolso da SoproLife sobe para R$ 20.415,76 (18.860,76 + 1.555,00)")

    print("\n== B) EEGLife: Nano EEG iCelera ==")
    nano = itens.get("nanoeeg-001")
    check(nano is not None, "nanoeeg-001 existe no livro-caixa")
    check(sum(1 for i in priv["itens"] if i["id"] == "nanoeeg-001") == 1,
          "nanoeeg-001 existe UMA unica vez no livro-caixa")
    check(nano["marca"] == "eeglife", "Nano EEG: marca EEGLife")
    check(nano["categoria"] == "equipamento", "Nano EEG: categoria equipamento")
    check(nano["valor_equipamento"] == 24010.00, "Nano EEG: equipamento R$ 24.010,00")
    check(nano["valor_frete"] == 280.00, "Nano EEG: frete R$ 280,00")
    check(round(nano["valor_equipamento"] + nano["valor_frete"], 2) == 24290.00,
          "Nano EEG: 24.010,00 + 280,00 = R$ 24.290,00")
    check(nano["valor_total"] == 24290.00, "Nano EEG: valor total R$ 24.290,00")
    check(nano["inicio"] == "2026-10-23", "Nano EEG: aquisicao em 23/10/2026")
    check("iCelera" in nano.get("fornecedor_marca_comercial", ""),
          "Nano EEG: marca comercial iCelera registrada")
    check("Celera" in nano.get("fornecedor", ""),
          "Nano EEG: fornecedor Celera registrado")
    check(nano["parcelas_total"] == 10, "Nano EEG: 10 parcelas")
    check(nano["valor_mensal"] == 2429.00, "Nano EEG: parcela combinada R$ 2.429,00")
    check(round(1200.00 + 1229.00, 2) == nano["valor_mensal"],
          "Nano EEG: 1.200,00 + 1.229,00 = R$ 2.429,00/mes")

    # Acessorios: um pedido, um ativo. Cada acessorio como item proprio
    # multiplicaria o mesmo investimento em dez linhas.
    for peca in ("software", "25 eletrodos", "maleta", "fotoestimulador",
                 "audio estimulador", "toucas", "kit video", "acrilico",
                 "pasta condutora", "transferencia"):
        check(peca.lower() in nano["descricao"].lower(),
              "Nano EEG: acessorio '%s' descrito no mesmo item" % peca)
    acessorios_soltos = [i["id"] for i in priv["itens"]
                         if i["id"] != "nanoeeg-001"
                         and "icelera" in json.dumps(i, ensure_ascii=False).lower()]
    check(not acessorios_soltos,
          "Nano EEG: nenhum acessorio virou investimento separado (%s)" % acessorios_soltos)

    movs = nano["movimentos"]
    check(len(movs) == 20, "Nano EEG: 20 movimentos (10 posicoes x 2 cartoes)")
    refs = sorted({m["ref"] for m in movs})
    check(len(refs) == 10, "Nano EEG: 10 posicoes de parcela distintas")
    for ref in refs:
        do_ref = [m for m in movs if m["ref"] == ref]
        check(len(do_ref) == 2, "Nano EEG %s: duas linhas, uma por cartao" % ref)
        check(sorted(m["pagador"] for m in do_ref) == [A, F],
              "Nano EEG %s: uma linha de Adeildo e uma de Faustino" % ref)
        check(round(sum(m["valor"] for m in do_ref), 2) == 2429.00,
              "Nano EEG %s: as duas linhas somam R$ 2.429,00" % ref)
    check(all(m["status"] == "pendente" for m in movs),
          "Nano EEG: nenhuma parcela marcada como paga")
    # O que os comprovantes provam e a APROVACAO DA COMPRA, nao a quitacao de
    # fatura. Inventar data de vencimento seria inventar divida com prazo.
    check(all(m["data"] is None for m in movs),
          "Nano EEG: nenhuma data de pagamento inventada")
    check(all(m["vencimento"] is None for m in movs),
          "Nano EEG: nenhum vencimento inventado (dia da fatura nao informado)")
    check(round(sum(m["valor"] for m in movs if m["pagador"] == A), 2) == 12000.00,
          "Nano EEG: Adeildo responde por R$ 12.000,00")
    check(round(sum(m["valor"] for m in movs if m["pagador"] == F), 2) == 12290.00,
          "Nano EEG: Faustino responde por R$ 12.290,00")
    check(round(sum(m["valor"] for m in movs), 2) == 24290.00,
          "Nano EEG: a soma dos dois rateios e R$ 24.290,00 — nao R$ 48.580,00")

    print("\n== B.1) Nano EEG no resumo da EEGLife ==")
    check(eeg["itens_ativos"] == 1, "resumo da EEGLife tem um item")
    ie = eeg["itens"][0]
    check(ie["id"] == "nanoeeg-001", "o item e o Nano EEG")
    check(ie["categoria"] == "Equipamento", "Nano EEG: rotulo 'Equipamento' na tela")
    check(ie["valor_total"] == 24290.00, "Nano EEG: R$ 24.290,00 na tela")
    check(ie["parcelas_pagas"] == 0 and ie["parcelas_total"] == 10,
          "Nano EEG: 0/10 parcelas pagas (nenhuma fatura quitada)")
    check(ie["saldo_restante"] == 24290.00, "Nano EEG: saldo em aberto R$ 24.290,00")
    check(eeg["total_investido_equipamentos"] == 24290.00,
          "EEGLife: investido em equipamentos R$ 24.290,00 — contado uma vez")
    check(eeg["parcelas_mensais_equipamentos"] == 2429.00,
          "EEGLife: parcela mensal combinada R$ 2.429,00")
    check(eeg["total_desembolsado_confirmado"] == 0.0,
          "EEGLife: desembolso confirmado R$ 0,00 — a compra tem um dia")

    soc = {s["nome"]: s for s in eeg["rateio_socios"]}
    check(soc[A]["saldo_pendente_atribuido"] == 12000.00,
          "'Por responsavel' da EEGLife: Adeildo R$ 12.000,00 em aberto")
    check(soc[F]["saldo_pendente_atribuido"] == 12290.00,
          "'Por responsavel' da EEGLife: Faustino R$ 12.290,00 em aberto")
    check(round(soc[A]["saldo_pendente_atribuido"]
                + soc[F]["saldo_pendente_atribuido"], 2) == 24290.00,
          "a soma dos responsaveis e exatamente o valor do ativo")
    check(soc[A]["compromisso_mensal"] == 1200.00,
          "compromisso mensal de Adeildo: R$ 1.200,00")
    check(soc[F]["compromisso_mensal"] == 1229.00,
          "compromisso mensal de Faustino: R$ 1.229,00")
    check(soc[A]["total_desembolsado"] == 0.0 and soc[F]["total_desembolsado"] == 0.0,
          "nenhum desembolso atribuido a ninguem ainda")
    check(eeg["pendente_sem_pagador"] == 0.0,
          "nenhum saldo da EEGLife sem pagador definido")

    parc = eeg["parcelamentos"]
    check(len(parc) == 1, "um unico parcelamento na EEGLife")
    p = parc[0]
    check(p["parcela_atual"] == "0/10",
          "o parcelamento mostra 0/10 — posicao, nao movimento (seriam 0/20)")
    check(p["parcelas_em_aberto"] == 10, "10 posicoes em aberto, nao 20")
    check("artão de crédito" in p["forma_pagamento"],
          "forma de pagamento diz cartao, nao 'parcelamento com o fornecedor'")
    check(p["valor_parcela"] == 2429.00, "parcela combinada R$ 2.429,00")
    planos = {pl["pagador"]: pl for pl in p["planos_por_pagador"]}
    check(set(planos) == {A, F}, "dois planos de parcela, um por socio")
    check(planos[A]["parcelas"] == 10 and planos[A]["valor_parcela"] == 1200.00
          and planos[A]["total"] == 12000.00,
          "plano de Adeildo: 10 x R$ 1.200,00 = R$ 12.000,00")
    check(planos[F]["parcelas"] == 10 and planos[F]["valor_parcela"] == 1229.00
          and planos[F]["total"] == 12290.00,
          "plano de Faustino: 10 x R$ 1.229,00 = R$ 12.290,00")
    check(planos[A]["parcelas_pagas"] == 0 and planos[F]["parcelas_pagas"] == 0,
          "nenhum dos dois planos tem parcela paga")
    pend_eeg = {pd["item"] for pd in eeg["pendencias"]}
    check(eeg["pendencias_cadastro"] == 2,
          "as duas lacunas conhecidas da EEGLife ficam registradas como pendencia")
    check(any("vencimento" in i for i in pend_eeg),
          "o dia de vencimento das faturas e pendencia, nao suposicao")
    check(any("sanit" in i for i in pend_eeg),
          "o registro sanitario do equipamento e pendencia, nao fato perdido")

    print("\n== C) particao por marca: nenhum ativo contado duas vezes ==")
    ids_priv = sorted(i["id"] for i in priv["itens"])
    ids_sopro = sorted(i["id"] for i in sopro["itens"])
    ids_eeg = sorted(i["id"] for i in eeg["itens"])
    check(len(ids_priv) == len(set(ids_priv)), "sem id repetido no livro-caixa")
    check(not (set(ids_sopro) & set(ids_eeg)),
          "nenhum item aparece nos dois resumos (intersecao vazia)")
    check(sorted(ids_sopro + ids_eeg) == ids_priv,
          "a uniao dos dois resumos e exatamente o livro-caixa, sem sobra nem falta")
    check("nanoeeg-001" not in ids_sopro,
          "o Nano EEG NAO contamina a visao exclusiva da SoproLife")
    check("filtros-001" not in ids_eeg,
          "os filtros do KoKo NAO aparecem como custo exclusivo da EEGLife")
    soma_marcas = round(sum(i["valor_total"] or 0 for i in sopro["itens"])
                        + sum(i["valor_total"] or 0 for i in eeg["itens"]), 2)
    soma_priv = round(sum(i.get("valor_total") or 0 for i in priv["itens"]), 2)
    check(soma_marcas == soma_priv,
          "a soma dos valores nas duas marcas e a do livro-caixa (%.2f)" % soma_priv)
    check(sopro["total_investido_equipamentos"]
          != sopro["total_investido_equipamentos"] + 24290.00,
          "o investimento da EEGLife nao foi somado ao da SoproLife")
    # 48.580,00 e o numero que apareceria se o mesmo ativo fosse cadastrado
    # duas vezes. Ele nao pode existir em lugar nenhum.
    blob = json.dumps([priv, sopro, eeg], ensure_ascii=False)
    for proibido in ("48580", "48.580"):
        check(proibido not in blob,
              "o numero de ativo duplicado (%s) nao aparece em lugar algum" % proibido)

    print("\n== D) nada do historico mudou ==")
    for iid, esperado in ANTES.items():
        it = itens.get(iid)
        check(it is not None, "%s continua cadastrado" % iid)
        if not it:
            continue
        movs_it = it.get("movimentos") or []
        agora = [it.get("valor_total"), it.get("valor_mensal"), it.get("responsavel"),
                 len(movs_it), round(sum(m["valor"] for m in movs_it), 2),
                 it.get("parcelas_total"), it.get("status")]
        check(agora == esperado,
              "%s: valor, responsavel, movimentos e status intactos" % iid)
        check(it.get("marca") == "soprolife",
              "%s: marca SoproLife declarada (unico campo novo)" % iid)
    rat_antes = {r["item_id"]: r for r in sopro["rateio_itens"]}
    check(rat_antes["koko-001"]["pago_faustino"] == 6500.00
          and rat_antes["koko-001"]["pago_adeildo"] == 1500.00,
          "Koko: rateio historico preservado (Faustino 6.500 / Adeildo 1.500)")
    check(rat_antes["cremerj-001"]["pago_faustino"] == 2296.06,
          "CREMERJ: pagamento historico de Faustino preservado")
    socs = {s["nome"]: s for s in sopro["rateio_socios"]}
    check(socs[A]["total_desembolsado"] == 8324.64,
          "Adeildo: desembolso na SoproLife inalterado (R$ 8.324,64)")
    check(socs[A]["compromisso_mensal"] == 1573.80,
          "Adeildo: compromisso mensal na SoproLife inalterado (R$ 1.573,80)")
    check(sopro["total_mensal_atual"] == 2333.80,
          "custo mensal da SoproLife inalterado (R$ 2.333,80 — insumo nao e recorrente)")
    check(sopro["total_bonificacao_sem_caixa"] == 500.00,
          "bonificacao do Koko inalterada (R$ 500,00)")
    check(sopro["pendente_sem_pagador"] == 1500.00,
          "saldo do Koko sem pagador inalterado (R$ 1.500,00)")

    print("\n== E) isolamento entre as marcas no codigo ==")
    ger = open(GERADOR, encoding="utf-8").read()
    check('"soprolife"' in ger and '"eeglife"' in ger,
          "o gerador conhece as duas marcas")
    check("custos-investimentos-eeglife.local.json" in ger,
          "o gerador escreve o arquivo proprio da EEGLife")
    check("particao por marca nao cobre" in ger,
          "o gerador recusa gravar se a particao por marca nao fechar")
    check("marca desconhecida" in ger,
          "marca mal digitada e erro duro, nao item que desaparece")
    js = open(EEG_JS, encoding="utf-8").read()
    check("custos-investimentos-eeglife.local.json" in js,
          "a tela da EEGLife le o resumo da EEGLife")
    check("custos-investimentos-summary.local.json" not in js,
          "a tela da EEGLife NAO le o resumo da SoproLife")
    check('escopo_marca !== "eeglife"' in js,
          "a tela recusa resumo que nao seja o da marca EEGLife (fail-closed)")
    check("safeToDisplay !== true" in js,
          "a tela recusa resumo nao marcado como seguro")

    print("\n== F) seguranca dos resumos publicos ==")
    for nome, resumo in (("SoproLife", sopro), ("EEGLife", eeg)):
        texto = json.dumps(resumo, ensure_ascii=False).lower()
        for termo in ("cnpj", "cpf", "pix", "11.336.271", "08.477.694",
                      "80884619003", "infaxpaper", "boleto", "comprovante"):
            check(termo not in texto, "resumo %s sem '%s'" % (nome, termo))
        check(resumo["source"]["containsPersonalData"] is False,
              "resumo %s declara ausencia de dado pessoal" % nome)
    # O que é sensível fica no privado, que é gitignored — e tem de estar lá,
    # senão o fato fiscal da compra se perde.
    priv_txt = json.dumps(priv, ensure_ascii=False)
    check("11.336.271/0001-39" in priv_txt, "CNPJ do fornecedor dos filtros guardado no privado")
    check("08.477.694/0001-64" in priv_txt, "CNPJ da Celera guardado no privado")
    # O registro ANVISA tem 11 digitos e e indistinguivel de um CPF: a guarda
    # de formato (check-access.sh) o recusa ate no arquivo privado, e esta
    # certa em recusar. Registro sanitario tambem nao e fato economico.
    import re as _re
    check(not _re.search(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}", priv_txt),
          "livro-caixa sem nenhuma cadeia com forma de CPF")
    check(not _re.search(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}", priv_txt),
          "livro-caixa sem nenhuma cadeia com forma de telefone")
    check(any(pd["id"] == "pend-007" for pd in priv["pendencias"]),
          "o registro sanitario do Nano EEG vira pendencia em vez de sumir")

    print("\n== G) trilha de auditoria ==")
    comps = (priv.get("auditoria") or {}).get("complementos") or []
    c = next((x for x in comps if x.get("missao") == "M26.37"), None)
    check(c is not None, "complemento M26.37 registrado")
    if c:
        check(sorted(c["itens_adicionados"]) == ["filtros-001", "nanoeeg-001"],
              "auditoria: os dois itens adicionados estao nomeados")
        check(c["itens_alterados"] == [],
              "auditoria: nenhum item existente foi alterado")
        check(c["movimentos_registrados"] == 21,
              "auditoria: 21 movimentos registrados (1 + 20)")
        check(bool(c["usuario"]) and bool(c["registrado_em"]),
              "auditoria: usuario e data/hora registrados")
        valores = sorted(r["valor"] for r in c["registros"])
        check(valores == [1555.00, 12000.00, 12290.00],
              "auditoria: os registros somam os fatos informados")
        check(round(sum(valores), 2) == 25845.00,
              "auditoria: 1.555,00 + 12.000,00 + 12.290,00 = R$ 25.845,00")

    print("\n" + "=" * 70)
    if falhas:
        print("FALHARAM %d de %d:" % (len(falhas), total))
        for f in falhas:
            print("  - %s" % f)
        return 1
    print("OK — %d verificacoes passaram." % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
