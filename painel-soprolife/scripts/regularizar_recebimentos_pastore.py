#!/usr/bin/env python3
"""Regularização dos recebimentos Pastore — M26.3.

Duas coisas, nesta ordem, e nenhuma delas inventada:

1. **Cadastra a regra comercial** na parceria: R$ 109,50 recebidos por exame,
   com vigência. Até aqui esse número existia só como fato de dois extratos —
   219,00 = 2 × 109,50 e 328,50 = 3 × 109,50 — e era redigitado a cada mês.
2. **Registra como recebidos** os três fechamentos que o gestor confirmou:
   2026-07 #1 (2 exames), 2026-08 #1 (3 exames) e 2026-08 #2 (14 exames).

    # confere tudo e não escreve nada (padrão)
    python scripts/regularizar_recebimentos_pastore.py

    # aplica (exige e-mail de um gestor/admin ATIVO)
    python scripts/regularizar_recebimentos_pastore.py --apply --email a@b.c

FAIL-CLOSED POR CONFERÊNCIA
---------------------------
O script recusa escrever se QUALQUER número divergir do esperado: contagem de
exames por fechamento, valor derivado da regra, valor já conferido no
fechamento, receita própria total e total geral. Divergir significa que a
realidade não é a que o gestor descreveu — e nesse caso a decisão volta para
ele, não para o script.

O QUE ELE NUNCA FAZ
-------------------
* nunca altera lançamento próprio existente (LAN-000017 e os outros 14 ficam
  byte a byte como estão);
* nunca cria receita por exame Pastore — o recibo é agregado, um por
  fechamento, garantido pela unicidade de `partner_settlement_id`;
* nunca inventa forma de pagamento. Não há forma documentada para estes
  recebimentos em registro nenhum (parceria, extrato, trilha), então usa a
  representação neutra `"Outro"` que o modelo já suporta;
* nunca afirma que o banco creditou na data gravada. `data_recebimento` é a
  data da CONFIRMAÇÃO OPERACIONAL, e a observação de cada recibo diz isso com
  todas as letras;
* nunca duplica: a segunda execução não encontra nada a fazer.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

_NUCLEO = pathlib.Path(__file__).resolve().parent.parent / "nucleo-m15"
if str(_NUCLEO) not in sys.path:
    sys.path.insert(0, str(_NUCLEO))

from app.audit import audit  # noqa: E402
from app.db import get_sessionmaker  # noqa: E402
from app.ids import allocate_public_code  # noqa: E402
from app.models import (  # noqa: E402
    FinancialEntry,
    PartnerSettlement,
    PartnerSettlementItem,
    PartnerUnit,
    Partnership,
    User,
)
from app.services.pastore import canonical_pastore  # noqa: E402
from app.services.partner_pricing import (  # noqa: E402
    MODELO_VALOR_POR_EXAME,
    resolve_valor_por_exame,
)
from sqlalchemy import func, select  # noqa: E402

CENTAVO = Decimal("0.01")

# ---------------------------------------------------------------- o combinado
#
# Os números que o gestor confirmou em 31/08/2026. Estão aqui como CONFERÊNCIA,
# não como fonte: o valor que o script grava é sempre o derivado da regra
# (quantidade de exames × valor por exame). Se os dois não baterem, o script
# para. Constante que confere não é constante que decide.

VALOR_POR_EXAME = Decimal("109.50")
VIGENCIA_INICIO = date(2026, 7, 1)

# Data em que o gestor confirmou os pagamentos. NÃO é data bancária.
DATA_CONFIRMACAO = date(2026, 8, 31)

FORMA_PAGAMENTO = "Outro"

# `audit()` trunca valores longos. O `motivo` do log carrega a versão curta
# da mesma afirmação; a frase inteira fica na observação do fechamento, que
# não é truncada.
MOTIVO_RECIBO = (
    "regularizacao_pastore_m26_3: confirmado pelo gestor em 31/08/2026; "
    "data bancária original não informada"
)

OBSERVACAO_RECIBO = (
    "Pagamento histórico confirmado pelo gestor em 31/08/2026; data bancária "
    "original não informada. Forma de pagamento não documentada em nenhum "
    "registro — registrada como 'Outro'."
)

#: (competência, sequência, exames esperados, valor esperado)
FECHAMENTOS_ESPERADOS = (
    (date(2026, 7, 1), 1, 2, Decimal("219.00")),
    (date(2026, 8, 1), 1, 3, Decimal("328.50")),
    (date(2026, 8, 1), 2, 14, Decimal("1533.00")),
)

RECEITA_PROPRIA_ESPERADA = Decimal("3494.79")
PASTORE_ESPERADO = Decimal("2080.50")
TOTAL_ESPERADO = Decimal("5575.29")


def _q(v: Decimal) -> Decimal:
    return Decimal(v).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def _brl(v) -> str:
    return f"R$ {Decimal(v):,.2f}".replace(",", "~").replace(".", ",").replace("~", ".")


# ------------------------------------------------------------------- leitura


def coletar(db) -> dict:
    """Fotografia read-only do que existe hoje."""
    parceiro = canonical_pastore(db)
    parcerias = db.execute(
        select(Partnership).where(Partnership.partner_id == parceiro.id)
    ).scalars().all()
    settlements = db.execute(
        select(PartnerSettlement)
        .where(PartnerSettlement.partner_id == parceiro.id)
        .order_by(PartnerSettlement.competencia, PartnerSettlement.sequencia)
    ).scalars().all()

    linhas = []
    for s in settlements:
        itens = db.scalar(
            select(func.count()).select_from(PartnerSettlementItem)
            .where(PartnerSettlementItem.settlement_id == s.id)
        ) or 0
        recibo = db.execute(
            select(FinancialEntry).where(FinancialEntry.partner_settlement_id == s.id)
        ).scalar_one_or_none()
        linhas.append({
            "_settlement": s,
            "competencia": s.competencia,
            "sequencia": s.sequencia,
            "itens": itens,
            "status": s.status,
            "valor_total": s.valor_total,
            "recibo": recibo.public_code if recibo else None,
        })

    entradas = db.execute(select(FinancialEntry)).scalars().all()
    receita_propria = sum(
        (e.valor for e in entradas
         if e.tipo == "receita" and e.status == "Recebido"
         and e.partner_settlement_id is None),
        Decimal("0"),
    )
    receita_parceria = sum(
        (e.valor for e in entradas
         if e.tipo == "receita" and e.status == "Recebido"
         and e.partner_settlement_id is not None),
        Decimal("0"),
    )
    return {
        "parceiro": parceiro,
        "parcerias": parcerias,
        "fechamentos": linhas,
        "receita_propria": _q(receita_propria),
        "receita_parceria": _q(receita_parceria),
        "lancamentos": len(entradas),
    }


def conferir(db, dados: dict) -> list[str]:
    """Todo motivo para NÃO escrever. Lista vazia = liberado."""
    problemas: list[str] = []

    if len(dados["parcerias"]) != 1:
        problemas.append(
            f"esperava exatamente 1 parceria Pastore, encontrei "
            f"{len(dados['parcerias'])}"
        )

    por_chave = {
        (l["competencia"], l["sequencia"]): l for l in dados["fechamentos"]
    }
    for competencia, sequencia, exames, valor in FECHAMENTOS_ESPERADOS:
        linha = por_chave.get((competencia, sequencia))
        if linha is None:
            problemas.append(
                f"fechamento {competencia:%Y-%m} #{sequencia} não existe"
            )
            continue
        if linha["itens"] != exames:
            problemas.append(
                f"fechamento {competencia:%Y-%m} #{sequencia}: "
                f"{linha['itens']} exame(s), esperava {exames}"
            )
        derivado = _q(VALOR_POR_EXAME * exames)
        if derivado != valor:
            problemas.append(
                f"fechamento {competencia:%Y-%m} #{sequencia}: regra deriva "
                f"{_brl(derivado)}, o gestor confirmou {_brl(valor)}"
            )
        se_ja_valorado = linha["valor_total"]
        if se_ja_valorado is not None and _q(se_ja_valorado) != valor:
            problemas.append(
                f"fechamento {competencia:%Y-%m} #{sequencia}: valor já gravado "
                f"{_brl(se_ja_valorado)} diverge do confirmado {_brl(valor)}"
            )

    inesperados = [
        f"{l['competencia']:%Y-%m} #{l['sequencia']}"
        for l in dados["fechamentos"]
        if (l["competencia"], l["sequencia"]) not in {
            (c, s) for c, s, _e, _v in FECHAMENTOS_ESPERADOS
        }
    ]
    if inesperados:
        problemas.append(
            "fechamento(s) fora do combinado: " + ", ".join(inesperados)
        )

    if dados["receita_propria"] != RECEITA_PROPRIA_ESPERADA:
        problemas.append(
            f"receita SoproLife direta é {_brl(dados['receita_propria'])}, "
            f"esperava {_brl(RECEITA_PROPRIA_ESPERADA)}"
        )

    ja_recebido = dados["receita_parceria"]
    a_receber = sum(
        (v for _c, _s, _e, v in FECHAMENTOS_ESPERADOS
         if por_chave.get((_c, _s), {}).get("recibo") is None),
        Decimal("0"),
    )
    if _q(ja_recebido + a_receber) != PASTORE_ESPERADO:
        problemas.append(
            f"Pastore somaria {_brl(ja_recebido + a_receber)} depois da "
            f"regularização, esperava {_brl(PASTORE_ESPERADO)}"
        )
    if _q(dados["receita_propria"] + ja_recebido + a_receber) != TOTAL_ESPERADO:
        problemas.append(
            f"total somaria "
            f"{_brl(dados['receita_propria'] + ja_recebido + a_receber)}, "
            f"esperava {_brl(TOTAL_ESPERADO)}"
        )
    return problemas


def planejar(db, dados: dict) -> dict:
    """O que `--apply` faria. Nada aqui escreve."""
    parceria = dados["parcerias"][0] if len(dados["parcerias"]) == 1 else None
    regra_ja_cadastrada = bool(
        parceria
        and parceria.modelo_recebimento == MODELO_VALOR_POR_EXAME
        and parceria.valor_recebido_por_exame is not None
        and _q(parceria.valor_recebido_por_exame) == VALOR_POR_EXAME
        and parceria.vigencia_inicio == VIGENCIA_INICIO
    )
    recibos = []
    for competencia, sequencia, exames, valor in FECHAMENTOS_ESPERADOS:
        linha = next(
            (l for l in dados["fechamentos"]
             if l["competencia"] == competencia and l["sequencia"] == sequencia),
            None,
        )
        if linha is None:
            continue
        recibos.append({
            "competencia": f"{competencia:%Y-%m}",
            "sequencia": sequencia,
            "exames": exames,
            "valor": valor,
            "ja_recebido": linha["recibo"] is not None,
            "recibo_existente": linha["recibo"],
            "_settlement": linha["_settlement"],
        })
    return {
        "cadastrar_regra": not regra_ja_cadastrada,
        "regra_ja_cadastrada": regra_ja_cadastrada,
        "recibos": recibos,
        "recibos_a_criar": [r for r in recibos if not r["ja_recebido"]],
    }


# ------------------------------------------------------------------- escrita


def aplicar(db, dados: dict, plano: dict, user: User) -> dict:
    """Idempotente: o que já está feito não é refeito."""
    feito = {"regra": None, "recibos": []}

    if plano["cadastrar_regra"]:
        parceria = dados["parcerias"][0]
        parceria.modelo_recebimento = MODELO_VALOR_POR_EXAME
        parceria.valor_recebido_por_exame = VALOR_POR_EXAME
        parceria.vigencia_inicio = VIGENCIA_INICIO
        db.flush()
        # `audit()` só persiste chaves do vocabulário fechado de
        # `app/audit.py::ALLOWED_KEYS` — o resto é descartado em silêncio. O
        # que precisa sobreviver vai em `motivo`, `campos` e `decisao`.
        audit(
            db, "parceria.regra_recebimento_cadastrada", "partnerships",
            parceria.id, user.id, None,
            {
                "public_code": parceria.public_code,
                "campos": ["modelo_recebimento", "valor_recebido_por_exame",
                           "vigencia_inicio"],
                "decisao": (
                    f"{MODELO_VALOR_POR_EXAME} = {VALOR_POR_EXAME} por exame, "
                    f"vigente desde {VIGENCIA_INICIO.isoformat()}"
                ),
                "motivo": "regularizacao_pastore_m26_3",
            },
        )
        feito["regra"] = {
            "public_code": parceria.public_code,
            "valor_por_exame": str(VALOR_POR_EXAME),
            "vigencia_inicio": VIGENCIA_INICIO.isoformat(),
        }

    for recibo in plano["recibos_a_criar"]:
        s: PartnerSettlement = recibo["_settlement"]
        unidade = db.get(PartnerUnit, s.partner_unit_id) if s.partner_unit_id else None

        # O valor gravado é o DERIVADO da regra — `conferir()` já provou que
        # ele bate com o que o gestor confirmou.
        valor = _q(VALOR_POR_EXAME * recibo["exames"])

        entry = FinancialEntry(
            public_code=allocate_public_code(db, "financial_entries"),
            tipo="receita",
            categoria="Recebimento de parceiro",
            descricao=(
                f"Fechamento Pastore {s.competencia:%Y-%m}"
                + (f" — complementar {s.sequencia}" if s.sequencia > 1 else "")
                + f" — {unidade.public_code if unidade else 'unidade'}"
            ),
            valor=valor,
            moeda="BRL",
            data_competencia=s.competencia,
            data_competencia_original=s.competencia.isoformat(),
            data_competencia_precisao="dia",
            data_competencia_dia_assumido=False,
            data_recebimento=DATA_CONFIRMACAO,
            status="Recebido",
            forma_pagamento=FORMA_PAGAMENTO,
            origem_preco="Parceria",
            partner_settlement_id=s.id,
            # A chave amarra o recibo ao fechamento: reexecutar não cria outro,
            # e o índice único de `partner_settlement_id` é o backstop.
            idempotency_key=f"m26-3-recibo-{s.competencia:%Y%m}-{s.sequencia}",
        )
        db.add(entry)
        db.flush()

        s.valor_total = valor
        s.status = "recebido"
        s.observacao = ((s.observacao or "") + "\n" + OBSERVACAO_RECIBO).strip()
        audit(
            db, "pastore.fechamento_recebido", "partner_settlements", s.id,
            user.id, None,
            {
                "status": s.status,
                "public_code": entry.public_code,
                "total": recibo["exames"],
                "decisao": (
                    f"{recibo['exames']} x {VALOR_POR_EXAME} = {valor}; "
                    f"data_recebimento={DATA_CONFIRMACAO.isoformat()}; "
                    f"forma_pagamento={FORMA_PAGAMENTO}"
                ),
                # É esta frase que impede alguém de ler a data gravada como
                # data de crédito bancário.
                "motivo": MOTIVO_RECIBO,
            },
        )
        feito["recibos"].append({
            "competencia": recibo["competencia"],
            "sequencia": recibo["sequencia"],
            "exames": recibo["exames"],
            "valor": str(valor),
            "public_code": entry.public_code,
        })

    db.commit()
    return feito


# ------------------------------------------------------------------ relatório


def imprimir(dados, plano, problemas, feito) -> None:
    print("═" * 72)
    print("M26.3 — REGULARIZAÇÃO DOS RECEBIMENTOS PASTORE")
    print("═" * 72)
    print()
    print("ESTADO ATUAL")
    for l in dados["fechamentos"]:
        print(f"  {l['competencia']:%Y-%m} #{l['sequencia']}  "
              f"{l['itens']:>2} exame(s)  {l['status']:<10} "
              f"valor={_brl(l['valor_total']) if l['valor_total'] is not None else '—':>14}  "
              f"recibo={l['recibo'] or '—'}")
    print(f"  receita SoproLife direta : {_brl(dados['receita_propria'])}")
    print(f"  receita de parceria      : {_brl(dados['receita_parceria'])}")
    print()

    print("CONFERÊNCIA")
    if problemas:
        for p in problemas:
            print(f"  ✗ {p}")
        print()
        print(">>> BLOQUEADO. Nenhuma escrita será feita enquanto isso divergir.")
        return
    print("  ✓ contagem de exames, valores derivados, receita própria e total"
          " conferem")
    print()

    print("PLANO")
    if plano["regra_ja_cadastrada"]:
        print(f"  regra {_brl(VALOR_POR_EXAME)}/exame: já cadastrada")
    else:
        print(f"  cadastrar regra: {_brl(VALOR_POR_EXAME)} por exame, "
              f"vigente desde {VIGENCIA_INICIO:%d/%m/%Y}")
    for r in plano["recibos"]:
        if r["ja_recebido"]:
            print(f"  {r['competencia']} #{r['sequencia']}: já recebido "
                  f"({r['recibo_existente']})")
        else:
            print(f"  {r['competencia']} #{r['sequencia']}: {r['exames']:>2} × "
                  f"{_brl(VALOR_POR_EXAME)} = {_brl(r['valor'])} → recibo, "
                  f"data de confirmação {DATA_CONFIRMACAO:%d/%m/%Y}, "
                  f"forma '{FORMA_PAGAMENTO}'")
    print()

    if feito is None:
        print(">>> DRY-RUN. Nada foi escrito. "
              "Use --apply --email <gestor> para aplicar.")
        return

    print("APLICADO")
    if feito["regra"]:
        print(f"  regra cadastrada em {feito['regra']['public_code']}")
    if feito["recibos"]:
        for r in feito["recibos"]:
            print(f"  {r['competencia']} #{r['sequencia']}: {r['public_code']} "
                  f"= {_brl(r['valor'])}")
    if not feito["regra"] and not feito["recibos"]:
        print("  nada a fazer. Já estava regularizado.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="aplica a regularização (padrão: dry-run)")
    ap.add_argument("--email", help="e-mail do gestor/admin que assina a ação")
    ap.add_argument("--json", action="store_true", help="saída machine-readable")
    args = ap.parse_args()

    session = get_sessionmaker()()
    try:
        dados = coletar(session)
        problemas = conferir(session, dados)
        plano = planejar(session, dados)

        feito = None
        if args.apply:
            if problemas:
                imprimir(dados, plano, problemas, None)
                return 3
            if not args.email:
                print("ERRO: --apply exige --email de um gestor/admin.",
                      file=sys.stderr)
                return 2
            user = session.execute(
                select(User).where(User.email == args.email.strip().lower())
            ).scalar_one_or_none()
            if user is None or not user.ativo:
                print(f"ERRO: usuário ativo não encontrado: {args.email}",
                      file=sys.stderr)
                return 2
            if not {r.name for r in user.roles} & {"admin", "gestor"}:
                print("ERRO: o e-mail informado não é admin nem gestor.",
                      file=sys.stderr)
                return 2
            feito = aplicar(session, dados, plano, user)
            dados = coletar(session)
            plano = planejar(session, dados)

        if args.json:
            print(json.dumps({
                "problemas": problemas,
                "dry_run": feito is None,
                "aplicado": feito,
                "fechamentos": [
                    {k: v for k, v in l.items() if not k.startswith("_")}
                    for l in dados["fechamentos"]
                ],
                "receita_propria": str(dados["receita_propria"]),
                "receita_parceria": str(dados["receita_parceria"]),
            }, ensure_ascii=False, indent=2, default=str))
        else:
            imprimir(dados, plano, problemas, feito)
        return 3 if problemas and not args.apply else 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
