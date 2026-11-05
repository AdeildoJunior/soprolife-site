"""Plano append-only autorizado pelos sócios; execução separada de dry-run.

Só cria versões de custo e auditoria. Nunca modifica fatos clínicos ou razão.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..models import Person, ProfitCostRule, SpirometryExam
from .exam_profit import DEFAULT_RULES
from .profit_projection import classify_place, rules_at

REASON = "Valores confirmados pelos sócios como vigentes desde o início da operação."


def plan_history(db: Session, *, as_of: date, first_date_policy: str = "exact",
                 minimum_room_hours: Decimal | None = None) -> dict:
    exams = db.scalars(select(SpirometryExam).join(Person).where(
        Person.arquivado.is_not(True), SpirometryExam.data_exame.is_not(None),
        SpirometryExam.status.in_(("Realizado", "Exame realizado", "Laudo Liberado", "Liberado")),
    ).order_by(SpirometryExam.data_exame, SpirometryExam.public_code)).all()
    if not exams:
        raise ValueError("Nenhum exame real encontrado.")
    earliest = exams[0]
    exact = next((e.data_exame for e in exams if not e.data_exame_dia_assumido), None)
    approximate = bool(earliest.data_exame_dia_assumido)
    if approximate and first_date_policy not in ("month_boundary", "first_exact"):
        raise ValueError("Primeiro exame tem apenas mês/ano. Escolha explicitamente o limite técnico de mês ou o primeiro dia exato.")
    beginning = exact if first_date_policy == "first_exact" else earliest.data_exame
    if beginning is None:
        raise ValueError("Não existe exame com dia exato.")
    rows = db.scalars(select(ProfitCostRule).order_by(ProfitCostRule.created_at, ProfitCostRule.id)).all()
    current = rules_at(rows, as_of)
    if not set(DEFAULT_RULES).difference({"downtown.minimum_hours", "nova_america.minimum_hours"}).issubset(current):
        raise ValueError("Configuração operacional atual incompleta; audite antes de executar.")
    proposed, preserved = [], []
    for key, value in sorted(current.items()):
        if key not in DEFAULT_RULES:
            raise ValueError(f"Regra desconhecida: {key}")
        existing = rules_at(rows, beginning).get(key)
        if key.startswith("pastore."):
            pastore_dates = [e.data_exame for e in exams if classify_place(e, None, None) == "pastore"]
            applicable = min(pastore_dates) if pastore_dates else beginning
            if rules_at(rows, applicable).get(key) == value:
                preserved.append(key)
                continue
        if existing == value:
            preserved.append(key)
        elif existing is not None:
            raise ValueError(f"Histórico divergente em {key}; revisão factual necessária.")
        else:
            proposed.append({"key": key, "value": str(value), "effective_from": beginning.isoformat(), "reason": REASON})
    if minimum_room_hours is not None:
        if minimum_room_hours <= 0:
            raise ValueError("Mínimo de sala deve ser positivo.")
        for place in ("downtown", "nova_america"):
            key = f"{place}.minimum_hours"
            existing = rules_at(rows, beginning).get(key)
            if existing is None:
                proposed.append({"key": key, "value": str(minimum_room_hours), "effective_from": beginning.isoformat(),
                                 "reason": "Mínimo de horas por sessão confirmado expressamente pelo proprietário; sem horas adicionais presumidas."})
            elif existing != minimum_room_hours:
                raise ValueError("Mínimo de sala histórico diverge da confirmação.")
    plan = {"first_stored_date": earliest.data_exame.isoformat(), "first_date_is_approximate": approximate,
            "first_exact_date": exact.isoformat() if exact else None,
            "first_date_policy": first_date_policy, "effective_from": beginning.isoformat(),
            "as_of": as_of.isoformat(), "preserved": preserved, "create": proposed,
            "source_versions": [{"id": r.id, "key": r.key, "value": str(r.value), "date": r.effective_from.isoformat()} for r in rows]}
    plan["sha256"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    return plan


def append_history(db: Session, plan: dict, expected_sha256: str) -> list[str]:
    if plan["sha256"] != expected_sha256:
        raise ValueError("Plano mudou desde o dry-run; nenhuma escrita autorizada.")
    ids = []
    for item in plan["create"]:
        row = ProfitCostRule(key=item["key"], value=Decimal(item["value"]),
                             effective_from=date.fromisoformat(item["effective_from"]), reason=item["reason"])
        db.add(row)
        db.flush()
        audit(db, "lucro.regra_historica_criada", "profit_cost_rules", row.id,
              detalhes={"regra_custo": row.key, "vigencia_custo": row.effective_from.isoformat(),
                        "valor_custo": str(row.value), "reason_code": "socios_confirmaram_inicio_operacao"})
        ids.append(row.id)
    return ids
