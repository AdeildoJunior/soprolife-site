#!/usr/bin/env python3
"""
M26.21 — testes da reconciliacao de Custos & Investimentos e do rateio.

Offline, sem rede. Valida o livro-caixa privado E o resumo derivado, item por
item, contra os fatos informados pelos socios.

Desde a M26.37 o gerador escreve UM RESUMO POR MARCA e este arquivo valida o
resumo da SoproLife. Por isso as conferencias de cobertura ("um registro por
item") comparam contra os itens DA SOPROLIFE, nao contra o livro-caixa
inteiro: item de outra marca nao sair daqui e o comportamento correto, nao uma
sobra. Os fatos da EEGLife tem teste proprio em
scripts/test-custos-marcas-m26-37.py.

Uso:
    python3 painel-soprolife/scripts/test-custos-socios-m26-21.py
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIVATE = os.path.join(ROOT, "data-private", "custos-investimentos.local.json")
SUMMARY = os.path.join(ROOT, "data", "custos-investimentos-summary.local.json")

A, F = "Adeildo", "Faustino"
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
    summ = json.load(open(SUMMARY, encoding="utf-8"))
    itens = {i["id"]: i for i in priv["itens"]}
    marca_resumo = summ.get("escopo_marca", "soprolife")
    da_marca = [i for i in priv["itens"]
                if (i.get("marca") or "soprolife") == marca_resumo]
    rat = {r["item_id"]: r for r in summ["rateio_itens"]}
    res = {i["id"]: i for i in summ["itens"]}
    soc = {s["nome"]: s for s in summ["rateio_socios"]}
    parc = {p["item_id"]: p for p in summ["parcelamentos"]}

    print("\n== contabilidade ==")
    c = rat["contab-001"]
    check(c["pago_adeildo"] == 1040.00, "contabilidade: Adeildo R$ 1.040,00")
    check(c["pago_faustino"] == 1560.00, "contabilidade: Faustino R$ 1.560,00")
    check(c["valor_mensal"] == 260.00, "contabilidade: R$ 260,00/mes")
    movs = itens["contab-001"]["movimentos"]
    check(len(movs) == 10, "contabilidade: 10 competencias (jan..out/2026)")
    esperado = {"2026-01": F, "2026-02": A, "2026-03": A, "2026-04": F, "2026-05": A,
                "2026-06": A, "2026-07": F, "2026-08": F, "2026-09": F, "2026-10": F}
    for m in movs:
        check(m["pagador"] == esperado[m["competencia"]],
              "contabilidade %s: pagador %s" % (m["competencia"], esperado[m["competencia"]]))
        check(m["valor"] == 260.00, "contabilidade %s: R$ 260,00" % m["competencia"])

    print("\n== impostos ==")
    i = rat["imposto-001"]
    check(i["pago_faustino"] == 50.06, "impostos: Faustino R$ 50,06")
    check(i["pago_adeildo"] == 0, "impostos: Adeildo R$ 0,00 (fato, nao desconhecido)")
    imp = {m["competencia"]: m["valor"] for m in itens["imposto-001"]["movimentos"]}
    check(imp == {"2026-07": 12.00, "2026-08": 17.70, "2026-09": 20.36},
          "impostos: competencias jul/ago/set 2026 preservadas com valor exato")
    check(all(m["pagador"] == F for m in itens["imposto-001"]["movimentos"]),
          "impostos: todos pagos por Faustino")

    print("\n== Mais Laudo ==")
    ml = rat["maislaudo-001"]
    check(ml["pago_faustino"] == 130.00, "Mais Laudo: Faustino R$ 130,00")
    mlm = {m["competencia"]: m["valor"] for m in itens["maislaudo-001"]["movimentos"]}
    check(mlm == {"2026-02": 19.50, "2026-05": 26.00, "2026-06": 13.00,
                  "2026-07": 26.00, "2026-08": 39.00, "2026-10": 6.50},
          "Mais Laudo: 6 competencias com gasto, valores exatos")
    check(not any(c in mlm for c in ("2026-03", "2026-04", "2026-09")),
          "Mais Laudo: mes sem gasto NAO gera lancamento de zero")

    print("\n== Koko ==")
    k, kr = itens["koko-001"], rat["koko-001"]
    check(k["valor_nominal"] == 10000.00, "Koko: nominal R$ 10.000,00")
    check(k["valor_total"] == 9500.00, "Koko: custo efetivo R$ 9.500,00 (nunca 10.000)")
    check(kr["pago_adeildo"] == 1500.00, "Koko: Adeildo R$ 1.500,00 (posicoes 3, 6 e 7)")
    check(kr["pago_faustino"] == 6500.00, "Koko: Faustino R$ 6.500,00 (9 normais + 4 do adiantamento)")
    check(kr["pago_faustino"] != 3000.00, "Koko: R$ 3.000 NAO e registrado como pagamento do Faustino")
    check(kr["desconto_ou_baixa_sem_saida_caixa"] == 500.00, "Koko: bonificacao R$ 500,00")
    check(kr["pendente_sem_pagador"] == 1500.00, "Koko: pendente R$ 1.500,00 (posicoes 13, 14, 15)")
    caixa = kr["pago_adeildo"] + kr["pago_faustino"]
    check(caixa == 8000.00, "Koko: desembolsado em dinheiro R$ 8.000,00")
    check(caixa + kr["pendente_sem_pagador"] == 9500.00,
          "Koko: conferencia 8.000 + 1.500 = 9.500 (custo efetivo)")
    kmov = {m["ref"]: m for m in k["movimentos"]}
    check(len(kmov) == 20, "Koko: 20 posicoes, nenhuma duplicada")
    pagador_esperado = {1: F, 2: F, 3: A, 4: F, 5: F, 6: A, 7: A, 8: F, 9: F,
                        10: F, 11: F, 12: F, 16: F, 17: F, 18: F, 19: F}
    for n, pag in pagador_esperado.items():
        m = kmov["%d/20" % n]
        check(m["status"] == "pago" and m["pagador"] == pag,
              "Koko %d/20: pago por %s" % (n, pag))
    for n in (13, 14, 15):
        m = kmov["%d/20" % n]
        check(m["status"] == "pendente" and m["pagador"] is None,
              "Koko %d/20: pendente, sem pagador definido" % n)
    b = kmov["20/20"]
    check(b["status"] == "baixada_por_desconto", "Koko 20/20: baixada por desconto")
    check(b["valor"] == 0.0, "Koko 20/20: saida de caixa R$ 0,00 — bonificacao nao e pagamento")
    check(b["valor_nominal"] == 500.0, "Koko 20/20: nominal R$ 500,00 preservado")
    adiant = [kmov["%d/20" % n] for n in (16, 17, 18, 19)]
    check(sum(m["valor"] for m in adiant) == 2000.00,
          "Koko: adiantamento de 13/05/2026 = R$ 2.000,00 (nao 2.500 nem 3.000)")
    check(all(m["data"] == "2026-05-13" for m in adiant + [b]),
          "Koko: posicoes 16-20 liquidadas em 13/05/2026")

    print("\n== MIR / Spirobank II Smart e Seringa MIR 3L ==")
    for pid, n, vp in (("espi-001", 10, 1246.46), ("cal-001", 10, 199.70)):
        it, p = itens[pid], parc[pid]
        check(it["responsavel"] == A, "%s: responsavel corrente Adeildo" % pid)
        check(it["dia_vencimento_fatura"] == 10, "%s: fatura do cartao vence dia 10" % pid)
        check(p["valor_parcela"] == vp, "%s: parcela R$ %.2f" % (pid, vp))
        check(p["parcelas_total"] == n, "%s: %d parcelas" % (pid, n))
        # M26.21A — o socio confirmou que as faturas do cartao sao pagas
        # automaticamente no dia 10, entao as 4 vencidas (jul..out/2026) passaram
        # a 'pago'. As 6 futuras seguem 'pendente' e NAO viram pagas por ter
        # chegado o dia 10 — a trava segue valendo para elas.
        check(p["parcelas_pagas"] == 4,
              "%s: 4 parcelas pagas (confirmadas pelo socio na M26.21A)" % pid)
        check(p["parcelas_aguardando_confirmacao"] == 0,
              "%s: nenhuma parcela segue aguardando confirmacao" % pid)
        check(p["total_pago"] == round(4 * vp, 2),
              "%s: total pago = 4 x %.2f" % (pid, vp))
        check(p["saldo_restante"] == round((n - 4) * vp, 2),
              "%s: saldo restante = 6 parcelas futuras" % pid)
        futuras = [h for h in p["historico"] if h["status"] == "pendente"]
        check(len(futuras) == 6, "%s: 6 parcelas futuras intactas" % pid)
        check(all(h["data_pagamento"] is None for h in futuras),
              "%s: parcela futura sem data de pagamento fabricada" % pid)
        pagas = [h for h in p["historico"] if h["status"] == "pago"]
        check(all(h["data_pagamento"] == h["vencimento"] for h in pagas),
              "%s: data de pagamento = vencimento (fatura no dia 10)" % pid)
        check(sorted(h["competencia"] for h in pagas)
              == ["ago/2026", "jul/2026", "out/2026", "set/2026"],
              "%s: competencias pagas = jul..out/2026, preservadas" % pid)
        check(p["proxima_competencia"] == "nov/2026", "%s: proxima competencia nov/2026" % pid)
        check(p["proximo_vencimento"] == "2026-11-10", "%s: proximo vencimento 10/11/2026" % pid)
        check(all(h["vencimento"].endswith("-10") for h in p["historico"]),
              "%s: todo vencimento no dia 10" % pid)
        check(rat[pid]["pago_adeildo"] == round(4 * vp, 2),
              "%s: pago por Adeildo = 4 x %.2f" % (pid, vp))
        check(rat[pid]["pago_faustino"] == 0,
              "%s: Faustino nao pagou nada deste item" % pid)
        check(rat[pid]["pendente_adeildo"] == round((n - 4) * vp, 2),
              "%s: saldo atribuido a Adeildo = 6 parcelas" % pid)

    print("\n== recorrentes: Workspace, Tailscale, Hostinger ==")
    for rid, mensal in (("ws-001", 32.72), ("ts-001", 42.00), ("vps-001", 52.92)):
        check(itens[rid]["responsavel"] == A, "%s: responsabilidade corrente Adeildo" % rid)
        check(rat[rid]["valor_mensal"] == mensal, "%s: R$ %.2f/mes" % (rid, mensal))
        check(rat[rid]["pago_adeildo"] is None and rat[rid]["pago_faustino"] is None,
              "%s: desembolso NAO confirmado = null (nunca R$ 0,00 inventado)" % rid)
        check(res[rid]["historico_confirmado"] is False,
              "%s: marcado como historico nao confirmado" % rid)
    check(itens["vps-001"]["periodicidade"] == "anual",
          "Hostinger: periodicidade anual REAL preservada (nao convertida em mensal)")
    check(itens["vps-001"]["valor_total"] == 635.00 and itens["vps-001"]["periodo_meses"] == 12,
          "Hostinger: R$ 635,00 / 12 meses preservados")
    check(itens["vps-001"]["vencimento"] == "2027-06-30", "Hostinger: vencimento 30/06/2027")

    print("\n== historico preservado ==")
    check(rat["cremerj-001"]["pago_faustino"] == 2296.06,
          "CREMERJ: pagamento historico de Faustino PRESERVADO (nao reatribuido)")
    check(rat["cremerj-001"]["pago_adeildo"] == 0,
          "CREMERJ: nao reatribuido a Adeildo")

    print("\n== totais por socio ==")
    check(soc[A]["total_desembolsado"] == 8324.64,
          "Adeildo: total desembolsado R$ 8.324,64 (contabilidade 1.040 + Koko 1.500 "
          "+ MIR 4.985,84 + seringa 798,80)")
    check(soc[F]["total_desembolsado"] == 12091.12,
          "Faustino: total desembolsado R$ 12.091,12 (10.536,12 da M26.21 + "
          "1.555,00 dos filtros do KoKo na M26.37)")
    check(round(soc[A]["total_desembolsado"] + soc[F]["total_desembolsado"], 2)
          == summ["total_desembolsado_confirmado"],
          "soma dos socios = total desembolsado confirmado do painel")
    check(soc[A]["compromisso_mensal"] == 1573.80,
          "Adeildo: compromisso mensal R$ 1.573,80")
    check(soc[F]["compromisso_mensal"] == 0.0,
          "Faustino: sem compromisso mensal fixo individual")
    check(summ["compromisso_mensal_compartilhado"] == 760.00,
          "compromisso compartilhado R$ 760,00 (contabilidade 260 + Koko 500)")
    check(soc[A]["saldo_pendente_atribuido"] == 8676.96,
          "Adeildo: saldo pendente atribuido R$ 8.676,96 (6 parcelas de MIR + 6 da seringa)")
    check(soc[F]["saldo_pendente_atribuido"] == 0.0,
          "Faustino: sem saldo pendente atribuido")
    check(summ["pendente_sem_pagador"] == 1500.00, "pendente sem pagador R$ 1.500,00")
    check(summ["total_bonificacao_sem_caixa"] == 500.00, "bonificacao total R$ 500,00")

    print("\n== rateio por item fecha com o agregado ==")
    for campo, chave in (("pago_adeildo", A), ("pago_faustino", F)):
        s = round(sum(r[campo] or 0 for r in summ["rateio_itens"]), 2)
        check(s == soc[chave]["total_desembolsado"],
              "soma de %s nos itens = card de %s (%.2f)" % (campo, chave, s))
    for campo, chave in (("pendente_adeildo", A), ("pendente_faustino", F)):
        s = round(sum(r[campo] or 0 for r in summ["rateio_itens"]), 2)
        check(s == soc[chave]["saldo_pendente_atribuido"],
              "soma de %s nos itens = saldo pendente de %s" % (campo, chave))
    s = round(sum(r["pendente_sem_pagador"] or 0 for r in summ["rateio_itens"]), 2)
    check(s == summ["pendente_sem_pagador"], "soma de pendente_sem_pagador = agregado")
    s = round(sum(r["desconto_ou_baixa_sem_saida_caixa"] or 0 for r in summ["rateio_itens"]), 2)
    check(s == summ["total_bonificacao_sem_caixa"], "soma de bonificacao = agregado")

    print("\n== ausencia de duplicidade ==")
    ids = [i["id"] for i in priv["itens"]]
    check(len(ids) == len(set(ids)), "sem id de item duplicado")
    ids_marca = [i["id"] for i in da_marca]
    check(len(summ["rateio_itens"]) == len(ids_marca),
          "um registro de rateio por item da marca %s, sem sobra" % marca_resumo)
    check(len(summ["itens"]) == len(ids_marca),
          "um registro de resumo por item da marca %s" % marca_resumo)
    for it in priv["itens"]:
        # A identidade de um movimento e (posicao, pagador), nao a posicao
        # sozinha: num item rateado a MESMA parcela tem uma linha por cartao
        # (Nano EEG, M26.37). Duas linhas iguais do MESMO pagador na mesma
        # posicao, isso sim, seria pagamento contado duas vezes.
        refs = [(m["ref"], m.get("pagador")) for m in it.get("movimentos", [])]
        check(len(refs) == len(set(refs)), "%s: sem movimento duplicado" % it["id"])
        comps = [(m["competencia"], m["valor"], m["pagador"]) for m in it.get("movimentos", [])
                 if m["competencia"] and m["status"] == "pago"]
        check(len(comps) == len(set(comps)) or it["id"] == "koko-001",
              "%s: sem pagamento repetido na mesma competencia" % it["id"])

    print("\n== bonificacao nunca entra como pagamento ==")
    for it in priv["itens"]:
        for m in it.get("movimentos", []):
            if m["status"] == "baixada_por_desconto":
                check(m["valor"] == 0.0,
                      "%s %s: baixa com saida de caixa R$ 0,00" % (it["id"], m["ref"]))
                check(m["pagador"] is None,
                      "%s %s: baixa sem pagador (nao e desembolso de ninguem)" % (it["id"], m["ref"]))

    print("\n== nenhuma parcela paga por 'chegou o dia 10' ==")
    for p in summ["parcelamentos"]:
        if not p.get("dia_vencimento"):
            continue
        for h in p["historico"]:
            if h["status"] == "pago":
                check(h["data_pagamento"] is not None,
                      "%s %s: 'pago' so com data de pagamento real" % (p["item_id"], h["ref"]))

    print("\n== auditoria ==")
    aud = priv.get("auditoria") or {}
    check(aud.get("missao") == "M26.21", "auditoria: missao registrada")
    check(aud.get("origem") == "reconciliacao informada pelos socios",
          "auditoria: origem 'reconciliacao informada pelos socios'")
    check(bool(aud.get("usuario")), "auditoria: usuario responsavel registrado")
    check(bool(aud.get("registrado_em")), "auditoria: data/hora registrada")
    check(aud.get("movimentos_registrados", 0) > 0, "auditoria: movimentos registrados")
    for r in aud.get("registros", []):
        check(all(k in r for k in ("item", "competencia_ou_parcela", "valor", "pagador",
                                   "status", "origem", "usuario", "registrado_em")),
              "auditoria: registro de %s completo" % r.get("item_id"))
        break
    check(set(aud.get("itens_antes", [])) <= set(aud.get("itens_depois", [])),
          "auditoria: nenhum item do cadastro anterior desapareceu")

    print("\n== auditoria complementar M26.21A ==")
    comps = aud.get("complementos") or []
    check(len(comps) >= 1, "auditoria: complemento registrado")
    c21a = next((x for x in comps if x.get("missao") == "M26.21A"), None)
    check(c21a is not None, "auditoria: complemento M26.21A presente")
    if c21a:
        check(c21a["parcelas_atualizadas"] == 8,
              "M26.21A: 8 parcelas atualizadas (4 MIR + 4 seringa)")
        check(c21a["total_adicional_confirmado"] == 5784.64,
              "M26.21A: total adicional R$ 5.784,64")
        check("confirmacao explicita do socio" in c21a["origem"],
              "M26.21A: origem = confirmacao explicita do socio")
        check(bool(c21a["usuario"]) and bool(c21a["registrado_em"]),
              "M26.21A: usuario e data/hora registrados")
        check(round(sum(r["valor"] for r in c21a["registros"]), 2) == 5784.64,
              "M26.21A: registros somam o total adicional")
        check(all(r["status_antes"] == "vencido_aguardando_confirmacao"
                  and r["status"] == "pago" for r in c21a["registros"]),
              "M26.21A: cada registro guarda o status anterior e o novo")

    print("\n== seguranca do resumo publico ==")
    blob = json.dumps(summ, ensure_ascii=False).lower()
    for termo in ("99pay", "codigo_autorizacao", "id_99pay", "pix", "cnpj", "cpf", "boleto"):
        check(termo not in blob, "resumo sem '%s'" % termo)
    check(summ["source"]["safeToDisplay"] is True, "resumo marcado como seguro para exibir")
    check(priv["source"]["safeToDisplay"] is False, "livro-caixa privado NAO e exibivel")

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
