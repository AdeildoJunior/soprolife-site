#!/usr/bin/env python3
"""
M26.21A — confirmacao das faturas do cartao de Adeildo (MIR e seringa MIR 3L).

A M26.21 deixou de proposito as parcelas ja vencidas em
`vencido_aguardando_confirmacao`: o calendario era automatico, a conciliacao
nao. O socio confirmou EXPLICITAMENTE que todas as faturas do cartao pessoal
foram pagas automaticamente no dia 10 — essa confirmacao e exatamente o dado
que faltava, e por isso as 4 parcelas vencidas (jul..out/2026) de cada item
passam a `pago`.

O que este script NAO faz:
  - nao cria nem remove movimento (a contagem por item fica identica);
  - nao toca parcela futura (status `pendente` segue intacto);
  - nao altera competencia nem vencimento;
  - nao toca Workspace, Tailscale, Hostinger, Koko, CREMERJ, contabilidade,
    impostos nem Mais Laudo.

A data de pagamento registrada e o PROPRIO vencimento, porque o fato confirmado
e "a fatura e paga automaticamente no dia 10" — nao se inventa outra data.

Uso:
    python3 .../m26-21a-confirmar-faturas-cartao.py           # dry-run (default)
    python3 .../m26-21a-confirmar-faturas-cartao.py --apply
"""

import argparse
import copy
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIVATE = os.path.join(ROOT, "data-private", "custos-investimentos.local.json")

ADEILDO = "Adeildo"
PAGO = "pago"
AGUARDANDO = "vencido_aguardando_confirmacao"

ORIGEM = ("confirmacao explicita do socio: faturas do cartao pessoal de Adeildo "
          "pagas automaticamente no dia 10")
USUARIO = "Adeildo (sessao M26.21A - PC de casa)"
REGISTRADO_EM = datetime(2026, 10, 13, 0, 0, 0).isoformat() + "Z"

# Fato informado, usado como trava: item -> (n_parcelas, valor_unitario, total)
CONFIRMADO = {
    "espi-001": (4, 1246.46, 4985.84),
    "cal-001": (4, 199.70, 798.80),
}
COMPETENCIAS = ("2026-07", "2026-08", "2026-09", "2026-10")
TOTAL_ADICIONAL = 5784.64
NOVO_TOTAL_ADEILDO = 8324.64


def brl(v):
    if v is None:
        return "-"
    return ("R$ %0.2f" % v).replace(",", "X").replace(".", ",").replace("X", ".")


def confirmar(doc):
    out = copy.deepcopy(doc)
    itens = {i["id"]: i for i in out["itens"]}
    mudancas = []

    for pid, (n_esp, valor_esp, total_esp) in CONFIRMADO.items():
        item = itens[pid]
        alvo = [m for m in item["movimentos"] if m["status"] == AGUARDANDO]

        # Travas: so prossegue se o arquivo bate exatamente com o fato informado.
        if len(alvo) != n_esp:
            raise SystemExit(
                "ABORTADO: %s tem %d parcela(s) aguardando confirmacao, "
                "o fato informado fala de %d." % (pid, len(alvo), n_esp))
        comps = tuple(sorted(m["competencia"] for m in alvo))
        if comps != COMPETENCIAS:
            raise SystemExit(
                "ABORTADO: %s aguardando confirmacao em %s, esperado %s."
                % (pid, comps, COMPETENCIAS))
        soma = round(sum(m["valor"] for m in alvo), 2)
        if soma != total_esp:
            raise SystemExit(
                "ABORTADO: %s soma %s nas parcelas vencidas, o fato informado "
                "fala de %s." % (pid, brl(soma), brl(total_esp)))

        for m in alvo:
            antes = {"status": m["status"], "data": m.get("data"),
                     "pagador": m.get("pagador"), "origem": m.get("origem")}
            # Competencia e vencimento ficam INTOCADOS.
            m["status"] = PAGO
            m["data"] = m["vencimento"]
            m["pagador"] = ADEILDO
            m["origem"] = ORIGEM
            m["observacao"] = ("Fatura do cartao de Adeildo com pagamento automatico no "
                               "dia 10, confirmada pelo socio.")
            mudancas.append({
                "item_id": pid,
                "item": item["nome"],
                "competencia_ou_parcela": m["ref"],
                "competencia": m["competencia"],
                "vencimento": m["vencimento"],
                "valor": m["valor"],
                "pagador": ADEILDO,
                "status_antes": antes["status"],
                "status": PAGO,
                "data_pagamento": m["data"],
                "origem": ORIGEM,
                "usuario": USUARIO,
                "registrado_em": REGISTRADO_EM,
            })

        item["observacao"] = item["observacao"].replace(
            "NENHUMA parcela e marcada como paga apenas por ter chegado o dia 10: "
            "parcelas com vencimento no passado ficam em 'vencido_aguardando_confirmacao' "
            "ate haver confirmacao financeira real.",
            "As 4 parcelas vencidas (jul..out/2026) foram confirmadas como pagas pelo "
            "socio na M26.21A (fatura do cartao debitada automaticamente no dia 10). "
            "As parcelas futuras seguem 'pendente' e NAO sao marcadas como pagas por "
            "ter chegado o dia 10 - so com confirmacao financeira real.")

    # A pendencia pend-004 era exatamente "confirmar na fatura quais parcelas
    # foram debitadas". Essa confirmacao acabou de chegar, entao a pendencia esta
    # RESOLVIDA e sai da lista — deixa-la contradiria o dado. As outras ficam.
    pend_antes = [pp["id"] for pp in out.get("pendencias", [])]
    out["pendencias"] = [pp for pp in out.get("pendencias", []) if pp["id"] != "pend-004"]
    pend_removidas = [i for i in pend_antes if i not in {pp["id"] for pp in out["pendencias"]}]

    # Auditoria complementar: o bloco da M26.21 permanece intacto.
    out["auditoria"].setdefault("complementos", []).append({
        "missao": "M26.21A",
        "descricao": ("Confirmacao das faturas do cartao pessoal de Adeildo: as 4 parcelas "
                      "vencidas de jul..out/2026 do MIR/Spirobank II Smart e da Seringa MIR 3L "
                      "passam de 'vencido_aguardando_confirmacao' para 'pago'."),
        "usuario": USUARIO,
        "registrado_em": REGISTRADO_EM,
        "origem": ORIGEM,
        "total_adicional_confirmado": TOTAL_ADICIONAL,
        "parcelas_atualizadas": len(mudancas),
        "pendencias_resolvidas": pend_removidas,
        "registros": mudancas,
    })
    return out, mudancas


def verificar(antigo, novo, mudancas):
    """Checagens que nao dependem do gerador — rodam no dry-run e antes de gravar."""
    res = []

    def check(cond, msg):
        res.append((bool(cond), msg))

    old_it = {i["id"]: i for i in antigo["itens"]}
    new_it = {i["id"]: i for i in novo["itens"]}

    check(len(mudancas) == 8, "8 parcelas atualizadas (4 MIR + 4 seringa) — %d" % len(mudancas))
    check(round(sum(m["valor"] for m in mudancas), 2) == TOTAL_ADICIONAL,
          "total adicional = %s" % brl(TOTAL_ADICIONAL))
    check(all(m["pagador"] == ADEILDO for m in mudancas), "todas com pagador Adeildo")
    check(all(m["origem"] == ORIGEM for m in mudancas),
          "todas com origem de confirmacao explicita do socio")

    # Sem duplicidade: mesma quantidade de itens e de movimentos em TODO item.
    check(len(old_it) == len(new_it), "nenhum item criado nem removido (%d)" % len(new_it))
    for pid, novo_item in new_it.items():
        antes = old_it[pid]
        check(len(novo_item.get("movimentos", [])) == len(antes.get("movimentos", [])),
              "%s: numero de movimentos inalterado (%d)" % (pid, len(novo_item.get("movimentos", []))))
        refs = [m["ref"] for m in novo_item.get("movimentos", [])]
        check(len(refs) == len(set(refs)), "%s: sem ref duplicada" % pid)

    # Itens que NAO podem ter mudado em nada.
    intocados = [p for p in new_it if p not in CONFIRMADO]
    for pid in intocados:
        check(json.dumps(old_it[pid], sort_keys=True) == json.dumps(new_it[pid], sort_keys=True),
              "%s: intocado byte a byte" % pid)

    for pid, (n_esp, valor_esp, total_esp) in CONFIRMADO.items():
        movs_n = new_it[pid]["movimentos"]
        movs_o = old_it[pid]["movimentos"]
        pagas = [m for m in movs_n if m["status"] == PAGO]
        check(len(pagas) == n_esp, "%s: %d parcela(s) paga(s)" % (pid, n_esp))
        check(round(sum(m["valor"] for m in pagas), 2) == total_esp,
              "%s: total pago = %s" % (pid, brl(total_esp)))
        check(not any(m["status"] == AGUARDANDO for m in movs_n),
              "%s: nenhuma parcela segue aguardando confirmacao" % pid)
        check(all(m["data"] == m["vencimento"] for m in pagas),
              "%s: data de pagamento = vencimento (fatura no dia 10)" % pid)
        check(sorted(m["competencia"] for m in pagas) == list(COMPETENCIAS),
              "%s: competencias pagas = jul..out/2026" % pid)
        # Competencia e vencimento preservados posicao por posicao.
        check(all(a["competencia"] == b["competencia"] and a["vencimento"] == b["vencimento"]
                  for a, b in zip(movs_o, movs_n)),
              "%s: competencia e vencimento preservados em todas as posicoes" % pid)
        check(all(a["valor"] == b["valor"] for a, b in zip(movs_o, movs_n)),
              "%s: nenhum valor de parcela alterado" % pid)
        # Parcelas futuras intactas.
        fut_o = [m for m in movs_o if m["status"] == "pendente"]
        fut_n = [m for m in movs_n if m["status"] == "pendente"]
        check(len(fut_o) == len(fut_n) == 6, "%s: 6 parcelas futuras" % pid)
        check(json.dumps(fut_o, sort_keys=True) == json.dumps(fut_n, sort_keys=True),
              "%s: parcelas futuras intocadas" % pid)

    # Auditoria da M26.21 preservada.
    check(novo["auditoria"]["missao"] == "M26.21", "bloco de auditoria da M26.21 preservado")
    check(len(novo["auditoria"]["registros"]) == len(antigo["auditoria"]["registros"]),
          "registros da M26.21 nao foram alterados")
    check(novo["auditoria"]["complementos"][-1]["missao"] == "M26.21A",
          "complemento M26.21A registrado na auditoria")

    ids_antes = {pp["id"] for pp in antigo.get("pendencias", [])}
    ids_depois = {pp["id"] for pp in novo.get("pendencias", [])}
    check("pend-004" not in ids_depois,
          "pendencia pend-004 (confirmacao financeira do cartao) fechada")
    check(ids_antes - ids_depois == {"pend-004"},
          "nenhuma outra pendencia foi removida")
    check(novo["auditoria"]["complementos"][-1]["pendencias_resolvidas"] == ["pend-004"],
          "fechamento de pend-004 registrado na auditoria")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="grava (default: dry-run)")
    args = ap.parse_args()

    with open(PRIVATE, encoding="utf-8") as fh:
        antigo = json.load(fh)
    novo, mudancas = confirmar(antigo)

    print("=" * 104)
    print("M26.21A — DRY-RUN" if not args.apply else "M26.21A — APLICANDO")
    print("=" * 104)
    print("\n%-34s %-7s %-9s %-11s %-12s %-28s -> %s" % (
        "ITEM", "PARC.", "COMPET.", "VENCIMENTO", "VALOR", "STATUS ATUAL", "STATUS PROPOSTO"))
    for m in mudancas:
        print("%-34s %-7s %-9s %-11s %-12s %-28s -> %s" % (
            m["item"][:34], m["competencia_ou_parcela"], m["competencia"], m["vencimento"],
            brl(m["valor"]), m["status_antes"], m["status"]))

    print("\n%-34s %s" % ("TOTAL ADICIONAL CONFIRMADO", brl(round(sum(m["valor"] for m in mudancas), 2))))
    print("%-34s %s" % ("PAGADOR", ADEILDO))
    print("%-34s %s" % ("MOTIVO", "confirmacao explicita do socio (fatura automatica no dia 10)"))

    print("\n" + "=" * 104)
    print("VERIFICACOES")
    print("=" * 104)
    resultados = verificar(antigo, novo, mudancas)
    for ok, msg in resultados:
        print("  [%s] %s" % ("OK " if ok else "ERRO", msg))
    ok = all(o for o, _ in resultados)

    if not args.apply:
        print("\nDRY-RUN apenas. Use --apply para gravar.")
        return 0 if ok else 1
    if not ok:
        print("\nABORTADO: verificacoes falharam. Nada foi gravado.")
        return 1

    backup = PRIVATE + ".bak-m26-21a"
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
    print("\nAgora rode: python3 painel-soprolife/scripts/generate-custos-investimentos.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
