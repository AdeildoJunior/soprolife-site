"""Regras canônicas da operação Pastore.

Pastore identifica o parceiro do atendimento, nunca a pessoa. Toda resolução
é fail-closed: exatamente um parceiro canônico não arquivado e uma unidade
ativa pertencente a ele.
"""

from datetime import date

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Partner, PartnerUnit, SpirometryExam


def _erro(codigo: str, mensagem: str, status: int = 422) -> HTTPException:
    return HTTPException(status_code=status, detail={"codigo": codigo, "mensagem": mensagem})


def canonical_pastore(db: Session) -> Partner:
    rows = db.execute(
        select(Partner).where(
            Partner.arquivado.is_(False),
            func.lower(func.trim(Partner.nome)) == "pastore",
        )
    ).scalars().all()
    if len(rows) != 1:
        raise _erro(
            "pastore_canonica_ambigua",
            "É necessário existir exatamente um parceiro Pastore canônico não arquivado.",
            409,
        )
    return rows[0]


def active_pastore_units(db: Session, partner: Partner | None = None) -> list[PartnerUnit]:
    partner = partner or canonical_pastore(db)
    return db.execute(
        select(PartnerUnit)
        .where(PartnerUnit.partner_id == partner.id, PartnerUnit.ativo.is_(True))
        .order_by(PartnerUnit.nome, PartnerUnit.public_code)
    ).scalars().all()


def pastore_unit(db: Session, unit_id: str, partner: Partner | None = None) -> PartnerUnit:
    partner = partner or canonical_pastore(db)
    unit = db.get(PartnerUnit, unit_id)
    if unit is None or unit.partner_id != partner.id or not unit.ativo:
        raise _erro(
            "unidade_pastore_invalida",
            "A unidade precisa ser uma unidade Pastore ativa.",
        )
    return unit


def is_completed_pastore_exam(exam: SpirometryExam) -> bool:
    if exam.data_exame is None:
        return False
    normalized = (exam.status or "").strip().casefold()
    return normalized not in {
        "",
        "aguardando",
        "agendada",
        "cancelado",
        "cancelada",
        "remarcado",
        "remarcada",
        "não compareceu",
        "nao compareceu",
    }


def competency_month(raw: str) -> date:
    year, month = (int(part) for part in raw.split("-", 1))
    return date(year, month, 1)


def month_end(first: date) -> date:
    if first.month == 12:
        next_month = date(first.year + 1, 1, 1)
    else:
        next_month = date(first.year, first.month + 1, 1)
    return date.fromordinal(next_month.toordinal() - 1)
