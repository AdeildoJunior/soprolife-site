"""Contrato Maclin: caixa bruto e receita econômica líquida são distintos."""
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import select

from ..audit import audit
from ..models import Partner, ProfitRevenueBasis, SpirometryExam


def is_maclin(partner: Partner | None) -> bool:
    return bool(partner and not partner.arquivado and partner.nome.strip().casefold() == "maclin")


def canonical_maclin(db):
    rows = [p for p in db.scalars(select(Partner)).all() if is_maclin(p)]
    if len(rows) != 1:
        raise HTTPException(422, "Cadastro Maclin ausente ou ambíguo.")
    return rows[0]


def split_gross(gross: Decimal) -> tuple[Decimal, Decimal]:
    """30% arredondado ao centavo; restante para SoproLife, soma exata."""
    partner = (gross * Decimal("0.30")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return gross - partner, partner


def latest_basis(db, exam_id):
    return db.scalar(select(ProfitRevenueBasis).where(
        ProfitRevenueBasis.spirometry_exam_id == exam_id,
    ).order_by(ProfitRevenueBasis.created_at.desc(), ProfitRevenueBasis.id.desc()).limit(1))


def record_basis(db, exam, entry, *, basis, gross_amount, evidence_note, user_id, request_id):
    """Append-only, serializado por exame e idempotente sobre os fatos."""
    db.scalar(select(SpirometryExam).where(SpirometryExam.id == exam.id).with_for_update())
    previous = latest_basis(db, exam.id)
    if (previous and previous.financial_entry_id == entry.id
            and previous.entry_amount_snapshot == entry.valor
            and previous.basis == basis and previous.gross_amount == gross_amount):
        return previous, True
    row = ProfitRevenueBasis(spirometry_exam_id=exam.id, financial_entry_id=entry.id,
                             entry_amount_snapshot=entry.valor, basis=basis,
                             gross_amount=gross_amount, evidence_note=evidence_note,
                             created_by_user_id=user_id)
    db.add(row)
    db.flush()
    audit(db, "lucro.base_receita_confirmada", "profit_revenue_basis", row.id,
          user_id=user_id, request_id=request_id,
          detalhes={"exam_code": exam.public_code, "entry_code": entry.public_code,
                    "base_receita": basis})
    return row, False
