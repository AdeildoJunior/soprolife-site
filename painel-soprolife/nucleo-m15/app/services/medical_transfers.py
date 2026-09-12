"""Cálculo dos repasses por conclusão clínica, sem misturar receitas.

O único marco elegível é ``ReportDocument.released_at``. Status posterior,
assinatura e entrega não participam do predicado nem da competência.
"""

from datetime import date, datetime, time, timezone
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import PhysicianProfile, PhysicianTransfer, ReportDocument, User
from ..serializers import iso, money, to_local

MONEY_QUANT = Decimal("0.01")


def parse_competence(value: str | None) -> date:
    if value is None:
        today = datetime.now(ZoneInfo(get_settings().display_timezone)).date()
        return today.replace(day=1)
    try:
        parsed = datetime.strptime(value, "%Y-%m").date().replace(day=1)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={
                "codigo": "competencia_invalida",
                "mensagem": "Use competência no formato AAAA-MM.",
            },
        ) from None
    return parsed


def competence_key(value: date) -> str:
    return value.strftime("%Y-%m")


def _utc_boundaries(competence: date) -> tuple[datetime, datetime]:
    zone = ZoneInfo(get_settings().display_timezone)
    next_month = (
        competence.replace(year=competence.year + 1, month=1)
        if competence.month == 12
        else competence.replace(month=competence.month + 1)
    )
    start = datetime.combine(competence, time.min, tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(next_month, time.min, tzinfo=zone).astimezone(timezone.utc)
    return start, end


def eligible_report_count(
    db: Session, physician_profile_id: str, competence: date
) -> int:
    """Conta cada documento uma vez, pelo mês local de sua conclusão."""

    start, end = _utc_boundaries(competence)
    return int(
        db.scalar(
            select(func.count(func.distinct(ReportDocument.id))).where(
                ReportDocument.released_physician_profile_id == physician_profile_id,
                ReportDocument.released_at.is_not(None),
                ReportDocument.released_at >= start,
                ReportDocument.released_at < end,
            )
        )
        or 0
    )


def serialize_transfer(
    transfer: PhysicianTransfer,
    profile: PhysicianProfile,
    *,
    created_by: User | None = None,
    payment_by: User | None = None,
) -> dict:
    return {
        "id": transfer.id,
        "physician_profile_id": profile.id,
        "medica": profile.professional_name,
        "competencia": competence_key(transfer.competencia),
        "quantidade_laudos_elegiveis": transfer.eligible_report_count,
        "valor_unitario": money(transfer.unit_amount),
        "total_calculado": money(transfer.reference_total),
        "valor_pago": money(transfer.paid_amount),
        "data_pagamento": (
            transfer.payment_date.isoformat() if transfer.payment_date else None
        ),
        "status": transfer.status,
        "registrado_por": created_by.nome if created_by else None,
        "registrado_em_utc": iso(transfer.created_at),
        "registrado_em_local": to_local(transfer.created_at),
        "pagamento_registrado_por": payment_by.nome if payment_by else None,
    }


def medical_transfer_dashboard(db: Session, competence: date) -> dict:
    transfers = {
        row.physician_profile_id: row
        for row in db.execute(
            select(PhysicianTransfer).where(
                PhysicianTransfer.competencia == competence
            )
        ).scalars()
    }
    profiles = list(
        db.execute(
            select(PhysicianProfile)
            .where(PhysicianProfile.active.is_(True))
            .order_by(PhysicianProfile.professional_name)
        ).scalars()
    )
    known = {profile.id for profile in profiles}
    for profile_id in transfers:
        if profile_id not in known:
            profile = db.get(PhysicianProfile, profile_id)
            if profile:
                profiles.append(profile)

    doctors = []
    reference_sum = Decimal("0.00")
    paid_sum = Decimal("0.00")
    uncovered = False
    for profile in profiles:
        transfer = transfers.get(profile.id)
        live_count = eligible_report_count(db, profile.id, competence)
        if live_count == 0 and transfer is None:
            continue
        if transfer:
            reference_sum += transfer.reference_total
            paid_sum += transfer.paid_amount
            row = serialize_transfer(
                transfer,
                profile,
                created_by=db.get(User, transfer.created_by_user_id),
                payment_by=(
                    db.get(User, transfer.payment_registered_by_user_id)
                    if transfer.payment_registered_by_user_id
                    else None
                ),
            )
            row["quantidade_laudos_atual"] = live_count
        else:
            uncovered = live_count > 0
            row = {
                "id": None,
                "physician_profile_id": profile.id,
                "medica": profile.professional_name,
                "competencia": competence_key(competence),
                "quantidade_laudos_elegiveis": live_count,
                "quantidade_laudos_atual": live_count,
                "valor_unitario": None,
                "total_calculado": None,
                "valor_pago": "0.00",
                "data_pagamento": None,
                "status": "Pendente",
                "registrado_por": None,
                "registrado_em_utc": None,
                "registrado_em_local": None,
                "pagamento_registrado_por": None,
            }
        doctors.append(row)

    history = []
    for transfer in db.execute(
        select(PhysicianTransfer).order_by(
            PhysicianTransfer.competencia.desc(), PhysicianTransfer.created_at.desc()
        )
    ).scalars():
        profile = db.get(PhysicianProfile, transfer.physician_profile_id)
        if not profile:  # pragma: no cover - FK impede no banco real
            continue
        history.append(
            serialize_transfer(
                transfer,
                profile,
                created_by=db.get(User, transfer.created_by_user_id),
                payment_by=(
                    db.get(User, transfer.payment_registered_by_user_id)
                    if transfer.payment_registered_by_user_id
                    else None
                ),
            )
        )

    return {
        "competencia": competence_key(competence),
        "total_a_pagar": money(reference_sum),
        "total_pago": money(paid_sum),
        "total_a_pagar_tem_valor_a_definir": uncovered,
        "medicas": doctors,
        "historico": history,
    }


def calculate_reference_total(quantity: int, unit_amount: Decimal) -> Decimal:
    return (Decimal(quantity) * unit_amount).quantize(
        MONEY_QUANT, rounding=ROUND_HALF_UP
    )
