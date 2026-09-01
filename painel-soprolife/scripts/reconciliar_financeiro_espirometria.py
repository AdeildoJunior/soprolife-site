#!/usr/bin/env python3
"""Reconciliação do Financeiro da espirometria — M26.2.

Confere, exame a exame, se a receita que deveria existir existe, e fecha a
única lacuna que pode ser fechada sem inventar preço: exame Pastore concluído
que não pertence a fechamento nenhum.

    # relatório completo, nada é escrito (padrão)
    python scripts/reconciliar_financeiro_espirometria.py
    python scripts/reconciliar_financeiro_espirometria.py --json

    # aplica só o comprovadamente faltante (exige e-mail de gestor/admin)
    python scripts/reconciliar_financeiro_espirometria.py --apply --email a@b.c

O QUE ELE NUNCA FAZ, por desenho:

* nunca cria `FinancialEntry` para exame Pastore — no domínio Pastore o único
  lançamento possível é o recibo agregado do fechamento, e ele exige valor,
  data e forma confirmados pelo gestor contra o extrato do parceiro;
* nunca inventa preço para exame SoproLife direto — não existe tabela de preço
  no sistema, o valor sempre veio digitado no atendimento. Exame próprio sem
  receita é REPORTADO, nunca lançado;
* nunca altera, reclassifica ou apaga lançamento existente;
* nunca duplica: a segunda execução não encontra nada elegível e não escreve.

O único efeito de `--apply` é NÃO MONETÁRIO: vincular exames órfãos a um
fechamento mensal que nasce (ou continua) com `valor_total = NULL`. Isso tira
o exame do limbo e o coloca na fila onde o gestor digita o valor — sem afirmar
nada sobre quanto a Pastore pagou.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

_NUCLEO = pathlib.Path(__file__).resolve().parent.parent / "nucleo-m15"
if str(_NUCLEO) not in sys.path:
    sys.path.insert(0, str(_NUCLEO))

from app.audit import audit  # noqa: E402
from app.db import get_sessionmaker  # noqa: E402
from app.models import (  # noqa: E402
    Consultation,
    FinancialEntry,
    Partner,
    PartnerSettlement,
    PartnerSettlementItem,
    PartnerUnit,
    SpirometryExam,
    User,
)
from app.services.pastore import (  # noqa: E402
    attach_eligible_exams,
    canonical_pastore,
    is_completed_pastore_exam,
    planned_action,
    settlements_of_competency,
)
from sqlalchemy import select  # noqa: E402

CATEGORIA_ESPIROMETRIA = "Espirometria"

REGRA_PASTORE = (
    "Pastore: exame não é recebimento. Receita só no recibo agregado do "
    "fechamento mensal, com valor confirmado pelo gestor."
)
REGRA_DIRETO = (
    "SoproLife direto: receita criada na mesma transação do atendimento, "
    "com o valor digitado no formulário. Não há tabela de preço."
)


# ------------------------------------------------------------------ leitura


def _competencia(d: date | None) -> str | None:
    return d.strftime("%Y-%m") if d else None


def _bd(exam: SpirometryExam) -> str:
    if exam.broncodilatador is True:
        return "com BD"
    if exam.broncodilatador is False:
        return "sem BD"
    return "não informado"


def _receitas_do_exame(entries: list[FinancialEntry]) -> list[FinancialEntry]:
    return [e for e in entries if e.tipo == "receita"]


def coletar(db) -> dict:
    """Fotografia read-only do universo exame ↔ lançamento."""

    try:
        pastore = canonical_pastore(db)
    except Exception:  # parceiro canônico ausente/ambíguo não impede a auditoria
        pastore = None

    partners = {p.id: p for p in db.execute(select(Partner)).scalars().all()}
    units = {u.id: u for u in db.execute(select(PartnerUnit)).scalars().all()}
    exams = db.execute(
        select(SpirometryExam).order_by(SpirometryExam.public_code)
    ).scalars().all()
    entries = db.execute(
        select(FinancialEntry).order_by(FinancialEntry.public_code)
    ).scalars().all()
    settlements = db.execute(
        select(PartnerSettlement).order_by(
            PartnerSettlement.competencia, PartnerSettlement.sequencia
        )
    ).scalars().all()
    items = db.execute(select(PartnerSettlementItem)).scalars().all()

    por_exame: dict[str, list[FinancialEntry]] = defaultdict(list)
    for e in entries:
        if e.spirometry_exam_id:
            por_exame[e.spirometry_exam_id].append(e)

    item_por_exame = {i.spirometry_exam_id: i for i in items}
    settlement_por_id = {s.id: s for s in settlements}
    recibo_por_settlement = {
        e.partner_settlement_id: e for e in entries if e.partner_settlement_id
    }

    linhas = []
    for exam in exams:
        partner = partners.get(exam.partner_id) if exam.partner_id else None
        unit = units.get(exam.partner_unit_id) if exam.partner_unit_id else None
        eh_pastore = bool(pastore and partner and partner.id == pastore.id)
        receitas = _receitas_do_exame(por_exame.get(exam.id, []))
        item = item_por_exame.get(exam.id)
        settlement = settlement_por_id.get(item.settlement_id) if item else None
        recibo = recibo_por_settlement.get(settlement.id) if settlement else None

        linhas.append({
            "esp": exam.public_code,
            "exam_id": exam.id,
            "data": exam.data_exame.isoformat() if exam.data_exame else None,
            "origem": (
                "Pastore" if eh_pastore
                else (f"Parceiro {partner.public_code}" if partner else "SoproLife direto")
            ),
            "parceiro": partner.public_code if partner else None,
            "unidade": unit.public_code if unit else None,
            "bd": _bd(exam),
            "status": exam.status,
            "concluido": is_completed_pastore_exam(exam),
            "lan": [e.public_code for e in receitas],
            "valor": [str(e.valor) for e in receitas],
            "competencia_lan": [_competencia(e.data_competencia) for e in receitas],
            "competencia_exame": _competencia(exam.data_exame),
            "estado_financeiro": (
                "com receita" if receitas
                else ("sem receita — por desenho" if eh_pastore else "SEM RECEITA")
            ),
            "fechamento": (
                {
                    "competencia": _competencia(settlement.competencia),
                    "sequencia": settlement.sequencia,
                    "status": settlement.status,
                    "valor_total": (
                        str(settlement.valor_total)
                        if settlement.valor_total is not None else None
                    ),
                    "recibo": recibo.public_code if recibo else None,
                } if settlement else None
            ),
            "idempotency_key": exam.idempotency_key,
            "regra": REGRA_PASTORE if eh_pastore else REGRA_DIRETO,
            "_eh_pastore": eh_pastore,
        })

    return {
        "pastore": pastore,
        "partners": partners,
        "units": units,
        "exams": exams,
        "entries": entries,
        "settlements": settlements,
        "linhas": linhas,
        "recibo_por_settlement": recibo_por_settlement,
    }


# --------------------------------------------------------------- classificação


def classificar(db, dados: dict) -> dict:
    linhas = dados["linhas"]
    entries = dados["entries"]
    settlements = dados["settlements"]
    pastore = dados["pastore"]

    grupo_a = [l for l in linhas if l["lan"]]
    grupo_b = [l for l in linhas if not l["lan"] and not l["_eh_pastore"]]
    grupo_c = [
        l for l in linhas
        if l["_eh_pastore"] and l["fechamento"] is None and l["concluido"]
    ]
    grupo_d = [
        {
            "competencia": _competencia(s.competencia),
            "sequencia": s.sequencia,
            "unidade": (
                dados["units"][s.partner_unit_id].public_code
                if s.partner_unit_id in dados["units"] else None
            ),
            "status": s.status,
            "valor_total": str(s.valor_total) if s.valor_total is not None else None,
            "itens": sum(
                1 for l in linhas
                if l["fechamento"] and l["fechamento"]["competencia"]
                == _competencia(s.competencia)
                and l["fechamento"]["sequencia"] == s.sequencia
            ),
            "recibo": (
                dados["recibo_por_settlement"][s.id].public_code
                if s.id in dados["recibo_por_settlement"] else None
            ),
        }
        for s in settlements
    ]

    # E) duplicidades — mais de uma receita para o mesmo exame. O índice
    #    parcial único cobre a categoria canônica; uma receita gravada em
    #    OUTRA categoria escaparia dele, então a conferência é por contagem.
    contagem = Counter(
        e.spirometry_exam_id for e in entries
        if e.tipo == "receita" and e.spirometry_exam_id
    )
    grupo_e = [
        {"esp": l["esp"], "lan": l["lan"], "valor": l["valor"]}
        for l in linhas if contagem.get(l["exam_id"], 0) > 1
    ]

    # F) LAN de espirometria sem exame vinculado.
    grupo_f = [
        {
            "lan": e.public_code,
            "valor": str(e.valor),
            "categoria": e.categoria,
            "competencia": _competencia(e.data_competencia),
            "descricao": e.descricao,
        }
        for e in entries
        if e.tipo == "receita"
        and e.spirometry_exam_id is None
        and e.consultation_id is None
        and e.partner_settlement_id is None
        and (e.categoria or "").strip().casefold() == CATEGORIA_ESPIROMETRIA.casefold()
    ]

    # G) divergências de valor. Não existe tabela de preço, então não há
    #    "valor esperado" para exame próprio: o que É verificável é o recibo
    #    do fechamento contra o valor declarado, e a competência do
    #    lançamento contra o mês do exame.
    grupo_g = []
    for s in settlements:
        recibo = dados["recibo_por_settlement"].get(s.id)
        if recibo is None or s.valor_total is None:
            continue
        if Decimal(recibo.valor) != Decimal(s.valor_total):
            grupo_g.append({
                "tipo": "recibo_diverge_do_fechamento",
                "fechamento": f"{_competencia(s.competencia)}#{s.sequencia}",
                "valor_fechamento": str(s.valor_total),
                "valor_recibo": str(recibo.valor),
            })
    for l in linhas:
        for lan, comp in zip(l["lan"], l["competencia_lan"]):
            if comp and l["competencia_exame"] and comp != l["competencia_exame"]:
                grupo_g.append({
                    "tipo": "competencia_diverge_do_exame",
                    "esp": l["esp"],
                    "lan": lan,
                    "competencia_exame": l["competencia_exame"],
                    "competencia_lancamento": comp,
                })

    # Grupos Pastore que dá para fechar agora, e o que o fechamento faria.
    acoes = []
    if pastore is not None:
        por_grupo: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for l in grupo_c:
            unit_id = next(
                (e.partner_unit_id for e in dados["exams"] if e.id == l["exam_id"]),
                None,
            )
            if unit_id and l["competencia_exame"]:
                por_grupo[(unit_id, l["competencia_exame"])].append(l)
        for (unit_id, comp), itens in sorted(
            por_grupo.items(), key=lambda kv: (kv[0][1], kv[0][0])
        ):
            unit = dados["units"].get(unit_id)
            if unit is None:
                continue
            ano, mes = (int(x) for x in comp.split("-"))
            existentes = settlements_of_competency(
                db, pastore, unit, date(ano, mes, 1)
            )
            acoes.append({
                "unidade": unit.public_code,
                "unidade_id": unit_id,
                "competencia": comp,
                "exames": [i["esp"] for i in itens],
                "quantidade": len(itens),
                "fechamentos_existentes": len(existentes),
                "acao_prevista": planned_action(existentes),
                "valor": None,
                "regra": REGRA_PASTORE,
            })

    # Consultas sem receita — fora do escopo de correção, mas não some do radar.
    consultas_sem_receita = [
        c.public_code
        for c in db.execute(select(Consultation)).scalars().all()
        if not any(
            e.consultation_id == c.id and e.tipo == "receita" for e in entries
        )
    ]

    total_financeiro = sum(
        (e.valor for e in entries if e.tipo == "receita"), Decimal("0")
    )

    return {
        "a_com_financeiro": grupo_a,
        "b_sem_financeiro": grupo_b,
        "c_pastore_aguardando": grupo_c,
        "d_fechamentos": grupo_d,
        "e_duplicidades": grupo_e,
        "f_lan_sem_esp": grupo_f,
        "g_divergencias": grupo_g,
        "acoes_possiveis": acoes,
        "consultas_sem_receita": consultas_sem_receita,
        "total_receita": str(total_financeiro),
    }


# ------------------------------------------------------------------- escrita


def aplicar(db, dados: dict, resultado: dict, user: User) -> list[dict]:
    """Executa SÓ o vínculo de fechamento. Nada monetário."""

    pastore = dados["pastore"]
    if pastore is None:
        return []
    aplicados = []
    for acao in resultado["acoes_possiveis"]:
        unit = dados["units"][acao["unidade_id"]]
        ano, mes = (int(x) for x in acao["competencia"].split("-"))
        try:
            attachment = attach_eligible_exams(
                db, pastore, unit, date(ano, mes, 1),
                observacao=(
                    "Fechamento aberto pela reconciliação M26.2. Valor NÃO "
                    "definido: exige confirmação do gestor contra o extrato."
                ) if acao["acao_prevista"] != "incorporar" else None,
            )
        except ValueError:
            # Nada elegível — segunda execução cai aqui e não escreve.
            continue
        audit(
            db,
            "pastore.fechamento_criado" if attachment.acao == "criado"
            else "pastore.fechamento_itens_incorporados",
            "partner_settlements", attachment.settlement.id,
            user.id, None,
            {
                "status": attachment.settlement.status,
                "total": len(attachment.exams),
                "sequencia": attachment.sequencia,
                "motivo": "reconciliacao_financeiro_espirometria_m26_2",
            },
        )
        db.commit()
        aplicados.append({
            "unidade": unit.public_code,
            "competencia": acao["competencia"],
            "acao": attachment.acao,
            "sequencia": attachment.sequencia,
            "exames": [e.public_code for e in attachment.exams],
            "valor_total": None,
        })
    return aplicados


# --------------------------------------------------------------------- saída


def imprimir(dados: dict, resultado: dict, aplicados: list[dict] | None) -> None:
    linhas = dados["linhas"]
    print("=" * 78)
    print("RECONCILIAÇÃO DO FINANCEIRO DA ESPIROMETRIA — M26.2")
    print("=" * 78)
    print()
    print(f"{'ESP':<12} {'DATA':<11} {'ORIGEM':<16} {'UNID':<11} {'BD':<14}"
          f" {'STATUS':<15} {'LAN':<12} {'VALOR':>9}  FECHAMENTO")
    print("-" * 78)
    for l in linhas:
        fech = l["fechamento"]
        fech_txt = (
            f"{fech['competencia']}#{fech['sequencia']} {fech['status']}"
            if fech else ("aguardando" if l["_eh_pastore"] and l["concluido"] else "—")
        )
        print(
            f"{l['esp']:<12} {(l['data'] or '—'):<11} {l['origem']:<16}"
            f" {(l['unidade'] or '—'):<11} {l['bd']:<14} {(l['status'] or '—'):<15}"
            f" {(','.join(l['lan']) or '—'):<12} {(','.join(l['valor']) or '—'):>9}"
            f"  {fech_txt}"
        )
    print()
    print("-" * 78)
    print(f"A) com financeiro                      : {len(resultado['a_com_financeiro'])}")
    print(f"B) sem financeiro (não-Pastore)        : {len(resultado['b_sem_financeiro'])}")
    print(f"C) Pastore aguardando fechamento       : {len(resultado['c_pastore_aguardando'])}")
    print(f"D) fechamentos existentes              : {len(resultado['d_fechamentos'])}")
    print(f"E) duplicidades de receita             : {len(resultado['e_duplicidades'])}")
    print(f"F) LAN de espirometria sem ESP         : {len(resultado['f_lan_sem_esp'])}")
    print(f"G) divergências verificáveis           : {len(resultado['g_divergencias'])}")
    print(f"   receita total no Financeiro         : R$ {resultado['total_receita']}")
    print()

    if resultado["b_sem_financeiro"]:
        print("B) EXAMES PRÓPRIOS SEM RECEITA — reportados, NUNCA lançados:")
        for l in resultado["b_sem_financeiro"]:
            print(f"   {l['esp']}  {l['data']}  {l['status']}")
        print("   Motivo: não existe tabela de preço no sistema. O valor sempre")
        print("   veio digitado no atendimento. Lançar aqui seria inventar preço.")
        print()

    if resultado["acoes_possiveis"]:
        print("AÇÕES POSSÍVEIS SEM DECIDIR PREÇO (vínculo, valor fica NULL):")
        for a in resultado["acoes_possiveis"]:
            print(f"   {a['unidade']}  {a['competencia']}  {a['quantidade']} exame(s)"
                  f"  → {a['acao_prevista']}"
                  f"  (já existem {a['fechamentos_existentes']})")
            print(f"     {', '.join(a['exames'])}")
        print()

    if resultado["e_duplicidades"] or resultado["f_lan_sem_esp"] or resultado["g_divergencias"]:
        print("ACHADOS:")
        for d in resultado["e_duplicidades"]:
            print(f"   DUPLICIDADE {d['esp']}: {d['lan']}")
        for f in resultado["f_lan_sem_esp"]:
            print(f"   LAN SEM ESP {f['lan']}  R$ {f['valor']}  {f['competencia']}")
        for g in resultado["g_divergencias"]:
            print(f"   DIVERGÊNCIA {g}")
        print()

    if resultado["consultas_sem_receita"]:
        print("Fora do escopo, mas registrado — consultas sem receita: "
              + ", ".join(resultado["consultas_sem_receita"]))
        print()

    if aplicados is None:
        print(">>> DRY-RUN. Nada foi escrito. Use --apply --email <gestor> para aplicar.")
    elif aplicados:
        print(">>> APLICADO:")
        for a in aplicados:
            print(f"   {a['unidade']} {a['competencia']} — fechamento {a['acao']}"
                  f" (sequência {a['sequencia']}), {len(a['exames'])} exame(s),"
                  f" valor_total = NULL")
    else:
        print(">>> APLICADO: nada a fazer. Já estava reconciliado.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="aplica o vínculo de fechamento (padrão: dry-run)")
    ap.add_argument("--email", help="e-mail do gestor/admin que assina a ação")
    ap.add_argument("--json", action="store_true", help="saída machine-readable")
    args = ap.parse_args()

    session = get_sessionmaker()()
    try:
        dados = coletar(session)
        resultado = classificar(session, dados)

        aplicados = None
        if args.apply:
            if not args.email:
                print("ERRO: --apply exige --email de um gestor/admin.", file=sys.stderr)
                return 2
            user = session.execute(
                select(User).where(User.email == args.email.strip().lower())
            ).scalar_one_or_none()
            if user is None or not user.ativo:
                print(f"ERRO: usuário ativo não encontrado: {args.email}", file=sys.stderr)
                return 2
            papeis = {r.name for r in user.roles}
            if not papeis & {"admin", "gestor"}:
                print("ERRO: o e-mail informado não é admin nem gestor.", file=sys.stderr)
                return 2
            aplicados = aplicar(session, dados, resultado, user)
            # Refaz a leitura para que o relatório final mostre o estado depois.
            dados = coletar(session)
            resultado = classificar(session, dados)

        if args.json:
            saida = {k: v for k, v in resultado.items()}
            saida["linhas"] = [
                {k: v for k, v in l.items() if not k.startswith("_")}
                for l in dados["linhas"]
            ]
            saida["aplicados"] = aplicados
            saida["dry_run"] = aplicados is None
            print(json.dumps(saida, ensure_ascii=False, indent=2, default=str))
        else:
            imprimir(dados, resultado, aplicados)
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
