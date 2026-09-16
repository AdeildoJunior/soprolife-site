"""Cálculo dos repasses por conclusão clínica, sem misturar receitas.

O único marco elegível é ``ReportDocument.released_at``. Status posterior,
assinatura e entrega não participam do predicado nem da competência.

M26.13 — 1 exame corrigido não pode virar 2 laudos pagos. Uma corretiva
(M25.2/M26.12) é um documento NOVO (`ReportDocument.corrects_document_id`
apontando para o original) que também passa por `released_at` quando a
médica a conclui de novo — sem a exclusão abaixo, o exame contava duas
vezes: uma pelo original, outra pela correção. A regra adotada é estável
de propósito: o documento-RAIZ (`corrects_document_id IS NULL`) é o único
que conta, sempre pela competência da SUA PRÓPRIA `released_at` — nunca a
da corretiva. Isso significa que corrigir um laudo depois NUNCA move a
contagem para outro mês nem soma um segundo laudo, mesmo que a competência
original já tenha repasse registrado/pago.
"""

from datetime import date, datetime, time, timezone
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    ASSINADO_ENTREGUE,
    ASSINADO_EM_CONFERENCIA,
    ASSINADO_RECUSADO,
    PhysicianProfile,
    PhysicianTransfer,
    ReportAssignment,
    ReportDocument,
    ReportDocumentVersion,
    ExternalSignedDocument,
    User,
)
from ..serializers import iso, money, to_local
from .report_conclusions import CONCLUSION_OPTIONS

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
    """Conta cada EXAME uma vez, pelo mês local da conclusão do documento-raiz.

    Uma corretiva (`corrects_document_id` preenchido) nunca conta — ela é a
    mesma produção clínica do original, apenas corrigida.
    """

    start, end = _utc_boundaries(competence)
    return int(
        db.scalar(
            select(func.count(func.distinct(ReportDocument.id))).where(
                ReportDocument.released_physician_profile_id == physician_profile_id,
                ReportDocument.corrects_document_id.is_(None),
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


# M26.13 — status aceitos de "voltou assinado" (tudo que NÃO é a conferência
# em andamento nem a recusa). `em_conferencia` é transitório; `recusado`
# nunca vira evidência de assinatura.
_SIGNED_BACK_EXCLUDED = (ASSINADO_EM_CONFERENCIA, ASSINADO_RECUSADO)


def physician_production_summary(
    db: Session, physician_profile_id: str, competence: date
) -> dict:
    """Produção da médica na competência, na MESMA regra de "laudo efetivo"
    de `eligible_report_count`: 1 exame == no máximo 1 laudo contado, pelo
    documento-raiz, mesmo que tenha sido corrigido depois.

    Resolvido em Python (não numa única query agregada) de propósito: o
    volume por médica/mês é pequeno (dezenas, não milhares) e a lógica de
    "qual documento da cadeia é o VIGENTE" fica auditável passo a passo, em
    vez de uma expressão SQL com COALESCE/OUTER JOIN difícil de conferir à
    mão — o dado é de repasse médico, errar aqui é errar pagamento.
    """

    start, end = _utc_boundaries(competence)
    roots = list(
        db.execute(
            select(ReportDocument).where(
                ReportDocument.released_physician_profile_id == physician_profile_id,
                ReportDocument.corrects_document_id.is_(None),
                ReportDocument.released_at.is_not(None),
                ReportDocument.released_at >= start,
                ReportDocument.released_at < end,
            )
        ).scalars()
    )
    effective = len(roots)
    if effective == 0:
        return {
            "competencia": competence_key(competence),
            "efetivos": 0,
            "corrigidos": 0,
            "assinados": 0,
            "entregues": 0,
            "aguardando_assinatura": 0,
            "pendentes": _pending_count(db, physician_profile_id),
            "distribuicao_conclusao": [],
        }

    root_ids = [root.id for root in roots]
    correctives = {
        row.corrects_document_id: row
        for row in db.execute(
            select(ReportDocument).where(
                ReportDocument.corrects_document_id.in_(root_ids)
            )
        ).scalars()
    }
    # O VIGENTE de cada exame é a corretiva, se existir; senão o próprio
    # original. É o vigente que decide "assinada"/"entregue"/conclusão —
    # nunca o original quando ele já foi superado.
    vigente_by_root = {
        root.id: correctives.get(root.id, root) for root in roots
    }
    vigente_ids = [doc.id for doc in vigente_by_root.values()]

    signed_status_by_document: dict[str, set[str]] = {}
    for row in db.execute(
        select(
            ExternalSignedDocument.report_document_id,
            ExternalSignedDocument.status,
        ).where(ExternalSignedDocument.report_document_id.in_(vigente_ids))
    ):
        signed_status_by_document.setdefault(row.report_document_id, set()).add(
            row.status
        )

    conclusion_code_by_version: dict[str, str | None] = {}
    version_ids = [
        doc.current_version_id
        for doc in vigente_by_root.values()
        if doc.current_version_id
    ]
    if version_ids:
        for row in db.execute(
            select(
                ReportDocumentVersion.id,
                ReportDocumentVersion.conclusion_code_snapshot,
            ).where(ReportDocumentVersion.id.in_(version_ids))
        ):
            conclusion_code_by_version[row.id] = row.conclusion_code_snapshot

    corrected = 0
    signed = 0
    delivered = 0
    conclusion_counts: dict[str, int] = {}
    for root in roots:
        vigente = vigente_by_root[root.id]
        if vigente.id != root.id:
            corrected += 1
        statuses = signed_status_by_document.get(vigente.id, set())
        is_signed = bool(statuses - set(_SIGNED_BACK_EXCLUDED))
        is_delivered = ASSINADO_ENTREGUE in statuses
        if is_signed:
            signed += 1
        if is_delivered:
            delivered += 1
        code = conclusion_code_by_version.get(vigente.current_version_id)
        if code:
            conclusion_counts[code] = conclusion_counts.get(code, 0) + 1

    labels = {option.code: option for option in CONCLUSION_OPTIONS}
    distribution = [
        {
            "conclusion_code": code,
            "rotulo": labels[code].short_label if code in labels else code,
            "grupo": labels[code].group if code in labels else "outro",
            "quantidade": count,
        }
        for code, count in sorted(
            conclusion_counts.items(), key=lambda item: item[1], reverse=True
        )
    ]

    return {
        "competencia": competence_key(competence),
        "efetivos": effective,
        "corrigidos": corrected,
        "assinados": signed,
        "entregues": delivered,
        "aguardando_assinatura": effective - signed,
        "pendentes": _pending_count(db, physician_profile_id),
        "distribuicao_conclusao": distribution,
    }


def _pending_count(db: Session, physician_profile_id: str) -> int:
    """Laudos atualmente na bancada da médica, ainda sem conclusão — estado
    ATUAL, não é filtrado por competência (não existe `released_at` para
    filtrar um laudo que ainda não foi liberado)."""

    return int(
        db.scalar(
            select(func.count(func.distinct(ReportAssignment.report_document_id)))
            .select_from(ReportAssignment)
            .join(
                ReportDocument,
                ReportDocument.id == ReportAssignment.report_document_id,
            )
            .where(
                ReportAssignment.physician_profile_id == physician_profile_id,
                ReportAssignment.active.is_(True),
                ReportDocument.status.in_(("atribuido", "em_elaboracao")),
            )
        )
        or 0
    )
