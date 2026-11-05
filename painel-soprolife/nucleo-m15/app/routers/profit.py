"""Custos e lucro analíticos; operações financeiras e fiscais permanecem intactas."""
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..ids import new_uuid
from ..models import FinancialEntry, OperationalSession, OperationalSessionExam, Partner, ProfitCostRule, SpirometryExam, User
from ..security import ROLE_ADMIN, ROLE_GESTOR, require_role
from ..services.exam_profit import DEFAULT_RULES
from ..services.profit_projection import PLACES, build_projection, classify_place, summarize
from ..services.maclin import is_maclin, record_basis, split_gross

router = APIRouter(prefix="/financeiro/lucro", tags=["financeiro"])


class CostRuleInput(BaseModel):
    key: str
    effective_from: date
    value: Decimal = Field(ge=0)
    reason: str = Field(min_length=3, max_length=300)


class SessionInput(BaseModel):
    place: str
    service_date: date
    exam_ids: list[str] = Field(min_length=1)
    start_time: str | None = None
    end_time: str | None = None
    billable_hours: Decimal | None = Field(default=None, gt=0)
    parking_override: Decimal | None = Field(default=None, ge=0)
    fuel_override: Decimal | None = Field(default=None, ge=0)
    room_override: Decimal | None = Field(default=None, ge=0)
    other_shared: Decimal | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=500)
    status: str = "estimado"


class RevenueBasisInput(BaseModel):
    exam_id: str
    basis: str
    gross_amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    evidence_note: str = Field(min_length=5, max_length=300)


def _session_dict(session: OperationalSession, exam_ids: list[str]) -> dict:
    return {"id": session.id, "local": session.place, "data": session.service_date.isoformat(),
            "horas_faturaveis": str(session.billable_hours) if session.billable_hours is not None else None,
            "inicio": session.start_time, "fim": session.end_time,
            "estacionamento": str(session.parking_override) if session.parking_override is not None else None,
            "combustivel": str(session.fuel_override) if session.fuel_override is not None else None,
            "sala": str(session.room_override) if session.room_override is not None else None,
            "outros": str(session.other_shared) if session.other_shared is not None else None,
            "observacao": session.note, "fonte": session.source,
            "status": session.status, "exames": exam_ids}


@router.get("/painel")
def dashboard(start: date, end: date, local: str | None = None,
              parceiro: str | None = None, forma_pagamento: str | None = None,
              status_financeiro: str | None = None,
              db: Session = Depends(get_db),
              _user: User = Depends(require_role(ROLE_GESTOR))):
    if end < start or (end - start).days > 366:
        raise HTTPException(422, "Período inválido ou maior que 366 dias.")
    result = build_projection(db, start, end)
    all_rows = result["exames"]
    if any((local, parceiro, forma_pagamento, status_financeiro)):
        rows = [r for r in result["exames"]
                if (not local or r["local"] == local)
                and (not parceiro or r["parceiro"] == parceiro)
                and (not forma_pagamento or r["forma_pagamento"] == forma_pagamento)
                and (not status_financeiro or r["status_financeiro"] == status_financeiro)]
        result = summarize(rows)
    result["filtros"] = {
        "locais": sorted({r["local"] for r in all_rows}),
        "parceiros": sorted({r["parceiro"] for r in all_rows if r["parceiro"]}),
        "formas": sorted({r["forma_pagamento"] for r in all_rows if r["forma_pagamento"]}),
        "status": sorted({r["status_financeiro"] for r in all_rows}),
    }
    return result


@router.get("/regras")
def list_rules(db: Session = Depends(get_db),
               _user: User = Depends(require_role(ROLE_GESTOR))):
    rows = db.scalars(select(ProfitCostRule).order_by(ProfitCostRule.key, ProfitCostRule.effective_from, ProfitCostRule.created_at)).all()
    return {"padrao": {k: str(v) for k, v in DEFAULT_RULES.items()},
            "versoes": [{"id": r.id, "chave": r.key, "vigencia": r.effective_from.isoformat(),
                         "valor": str(r.value), "motivo": r.reason} for r in rows]}


@router.post("/regras", status_code=201)
def create_rule(payload: CostRuleInput, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_role(ROLE_ADMIN))):
    if payload.key not in DEFAULT_RULES:
        raise HTTPException(422, "Regra desconhecida.")
    if payload.key.endswith("_percent") and payload.value > 100:
        raise HTTPException(422, "Percentual deve ficar entre 0 e 100.")
    row = ProfitCostRule(key=payload.key, effective_from=payload.effective_from,
                         value=payload.value, reason=payload.reason,
                         created_by_user_id=user.id)
    db.add(row)
    db.flush()
    audit(db, "lucro.regra_criada", "profit_cost_rules", row.id,
          user_id=user.id, request_id=request.state.request_id,
          detalhes={"regra_custo": payload.key, "vigencia_custo": payload.effective_from.isoformat(),
                    "valor_custo": str(payload.value)})
    db.commit()
    return {"id": row.id}


@router.post("/bases-receita", status_code=201)
def confirm_revenue_basis(payload: RevenueBasisInput, request: Request,
                          db: Session = Depends(get_db),
                          user: User = Depends(require_role(ROLE_ADMIN))):
    if payload.basis not in ("bruto", "liquido"):
        raise HTTPException(422, "Base de receita deve ser bruto ou líquido.")
    exam = db.get(SpirometryExam, payload.exam_id)
    if not exam or not exam.partner_id:
        raise HTTPException(422, "Exame de parceiro não encontrado.")
    entries = db.scalars(select(FinancialEntry).where(
        FinancialEntry.spirometry_exam_id == exam.id,
        FinancialEntry.tipo == "receita",
        FinancialEntry.status != "Cancelado",
    )).all()
    if len(entries) != 1:
        raise HTTPException(422, "Exame precisa de uma única receita direta válida.")
    entry = entries[0]
    if payload.basis == "bruto" and payload.gross_amount is not None and payload.gross_amount != entry.valor:
        raise HTTPException(422, "Para base bruta, o preço deve igualar o lançamento financeiro.")
    if payload.basis == "liquido" and payload.gross_amount is not None and payload.gross_amount < entry.valor:
        raise HTTPException(422, "Preço bruto não pode ser menor que a receita líquida.")
    if is_maclin(db.get(Partner, exam.partner_id)) and payload.basis == "liquido" and payload.gross_amount is not None:
        if split_gross(payload.gross_amount)[0] != entry.valor:
            raise HTTPException(422, "Bruto Maclin não fecha com a parcela líquida de 70% em centavos.")
    row, existing = record_basis(db, exam, entry, basis=payload.basis,
                                gross_amount=payload.gross_amount,
                                evidence_note=payload.evidence_note, user_id=user.id,
                                request_id=request.state.request_id)
    db.commit()
    return {"id": row.id, "exame": exam.public_code, "base": row.basis, "idempotente": existing}


@router.get("/sessoes")
def list_sessions(start: date, end: date, db: Session = Depends(get_db),
                  _user: User = Depends(require_role(ROLE_GESTOR))):
    rows = db.scalars(select(OperationalSession).where(
        OperationalSession.service_date >= start,
        OperationalSession.service_date <= end,
    ).order_by(OperationalSession.service_date)).all()
    links = db.scalars(select(OperationalSessionExam).where(
        OperationalSessionExam.session_id.in_([s.id for s in rows]))).all() if rows else []
    by_session = {}
    for link in links:
        by_session.setdefault(link.session_id, []).append(link.spirometry_exam_id)
    return {"sessoes": [_session_dict(s, by_session.get(s.id, [])) for s in rows]}


def _validate_session(db: Session, payload: SessionInput,
                      existing_id: str | None = None) -> list[SpirometryExam]:
    if payload.place not in PLACES or payload.status not in ("estimado", "confirmado"):
        raise HTTPException(422, "Local ou status inválido.")
    if payload.place in ("downtown", "nova_america") and payload.status == "confirmado" and payload.billable_hours is None and payload.room_override is None:
        raise HTTPException(422, "Informe as horas faturáveis ou o custo de sala confirmado.")
    if len(set(payload.exam_ids)) != len(payload.exam_ids):
        raise HTTPException(422, "Exame repetido na sessão.")
    exams = db.scalars(select(SpirometryExam).where(SpirometryExam.id.in_(payload.exam_ids))).all()
    if len(exams) != len(payload.exam_ids):
        raise HTTPException(422, "Exame inexistente.")
    for exam in exams:
        if exam.data_exame != payload.service_date or exam.status not in ("Realizado", "Exame realizado", "Laudo Liberado", "Liberado") or exam.reteste_sem_cobranca:
            raise HTTPException(422, "Sessão contém exame de outra data ou não produtivo.")
        if classify_place(exam, None, None) not in (payload.place, None):
            raise HTTPException(422, "Local do exame diverge da sessão.")
    existing = db.scalars(select(OperationalSessionExam).where(
        OperationalSessionExam.spirometry_exam_id.in_(payload.exam_ids))).all()
    if any(link.session_id != existing_id for link in existing):
        raise HTTPException(409, "Exame já vinculado a outra sessão.")
    return exams


@router.post("/sessoes", status_code=201)
def create_session(payload: SessionInput, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_role(ROLE_ADMIN))):
    exams = _validate_session(db, payload)
    if payload.place == "pastore":
        other = db.scalar(select(OperationalSession.id).where(
            OperationalSession.place == "pastore",
            OperationalSession.service_date == payload.service_date))
        if other and not payload.note:
            raise HTTPException(422, "Segunda ida Pastore no mesmo dia exige observação de override.")
    row = OperationalSession(id=new_uuid(), place=payload.place,
                             service_date=payload.service_date, source="manual",
                             created_by_user_id=user.id)
    for key in ("start_time", "end_time", "billable_hours", "parking_override",
                "fuel_override", "room_override", "other_shared", "note", "status"):
        setattr(row, key, getattr(payload, key))
    db.add(row)
    for eid in payload.exam_ids:
        db.add(OperationalSessionExam(session_id=row.id, spirometry_exam_id=eid))
    audit(db, "lucro.sessao_criada", "operational_sessions", row.id,
          user_id=user.id, request_id=request.state.request_id,
          detalhes={"local_operacional": payload.place, "data_operacional": payload.service_date.isoformat(),
                    "exames_depois": [e.public_code for e in exams],
                    "horas_faturaveis": str(payload.billable_hours) if payload.billable_hours else None,
                    "estacionamento_override": str(payload.parking_override) if payload.parking_override is not None else None,
                    "combustivel_override": str(payload.fuel_override) if payload.fuel_override is not None else None,
                    "sala_override": str(payload.room_override) if payload.room_override is not None else None})
    db.commit()
    return _session_dict(row, payload.exam_ids)


@router.patch("/sessoes/{session_id}")
def update_session(session_id: str, payload: SessionInput, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(require_role(ROLE_ADMIN))):
    row = db.get(OperationalSession, session_id)
    if not row:
        raise HTTPException(404, "Sessão não encontrada.")
    exams = _validate_session(db, payload, session_id)
    if payload.place == "pastore":
        other = db.scalar(select(OperationalSession.id).where(
            OperationalSession.place == "pastore",
            OperationalSession.service_date == payload.service_date,
            OperationalSession.id != session_id))
        if other and not payload.note:
            raise HTTPException(422, "Segunda ida Pastore no mesmo dia exige observação de override.")
    previous = db.scalars(select(OperationalSessionExam).where(
        OperationalSessionExam.session_id == session_id)).all()
    previous_codes = [db.get(SpirometryExam, link.spirometry_exam_id).public_code for link in previous]
    for link in previous:
        db.delete(link)
    db.flush()
    for eid in payload.exam_ids:
        db.add(OperationalSessionExam(session_id=session_id, spirometry_exam_id=eid))
    for key in ("place", "service_date", "start_time", "end_time", "billable_hours",
                "parking_override", "fuel_override", "room_override", "other_shared", "note", "status"):
        setattr(row, key, getattr(payload, key))
    audit(db, "lucro.sessao_alterada", "operational_sessions", row.id,
          user_id=user.id, request_id=request.state.request_id,
          detalhes={"local_operacional": payload.place, "data_operacional": payload.service_date.isoformat(),
                    "exames_antes": previous_codes, "exames_depois": [e.public_code for e in exams],
                    "horas_faturaveis": str(payload.billable_hours) if payload.billable_hours else None,
                    "estacionamento_override": str(payload.parking_override) if payload.parking_override is not None else None,
                    "combustivel_override": str(payload.fuel_override) if payload.fuel_override is not None else None,
                    "sala_override": str(payload.room_override) if payload.room_override is not None else None})
    db.commit()
    return _session_dict(row, payload.exam_ids)
