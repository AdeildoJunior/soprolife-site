"""Financeiro: fonte canônica, sem PII, Decimal, vinculado só por IDs técnicos.

Hardening M15.1A: idempotência atômica também em repasses; coerência
parceiro/parceria/encaminhamento/acerto validada na transação; transições
de status de repasse controladas; papel gestor obrigatório para mutações.
"""

from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..dates import parse_incomplete_date
from ..db import get_db
from ..domain import ensure_sem_pcmso
from ..ids import allocate_public_code
from ..models import FinancialEntry, PartnerTransfer, User
from ..pagination import PageParams, paginate
from ..schemas import FinancialEntryCreate, TransferCreate
from ..security import ROLE_GESTOR, ROLE_LEITURA, require_role
from ..serializers import ser_financial_entry, ser_transfer
from ..services.idempotency import idempotent_create
from ..services.integrity import (
    ensure_partner_exists,
    ensure_entry_for_referral,
    ensure_financial_links_coherent,
    ensure_partnership_of_partner,
    ensure_referral_of_partner,
    ensure_settlement_of_partner,
)

router = APIRouter(tags=["financeiro"])

MONEY_QUANT = Decimal("0.01")


def _q(value: Decimal | None) -> Decimal | None:
    """Quantização monetária canônica: 2 casas, ROUND_HALF_UP."""
    if value is None:
        return None
    return Decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _check_pcmso(db: Session, request: Request, user: User, payload, contexto: str) -> None:
    try:
        ensure_sem_pcmso(payload.model_dump(exclude_none=True), contexto)
    except HTTPException as exc:
        audit(
            db,
            "pcmso.rejeitado",
            user_id=user.id,
            request_id=request.state.request_id,
            detalhes=exc.detail if isinstance(exc.detail, dict) else None,
        )
        db.commit()
        raise


@router.get("/lancamentos")
def list_entries(
    tipo: str | None = None,
    status: str | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    stmt = select(FinancialEntry).order_by(FinancialEntry.created_at.desc())
    if tipo:
        stmt = stmt.where(FinancialEntry.tipo == tipo)
    if status:
        stmt = stmt.where(FinancialEntry.status == status)
    return paginate(db, stmt, params, ser_financial_entry)


@router.post("/lancamentos", status_code=201)
def create_entry(
    payload: FinancialEntryCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_GESTOR)),
):
    _check_pcmso(db, request, user, payload, "lancamentos.create")
    ensure_financial_links_coherent(
        db,
        payload.spirometry_exam_id,
        payload.consultation_id,
        payload.partner_referral_id,
    )

    def factory(key, fingerprint):
        entry = FinancialEntry(
            public_code=allocate_public_code(db, "financial_entries"),
            tipo=payload.tipo,
            categoria=payload.categoria,
            descricao=payload.descricao,
            valor=_q(payload.valor),
            moeda=payload.moeda,
            data_recebimento=payload.data_recebimento,
            status=payload.status,
            forma_pagamento=payload.forma_pagamento,
            origem_preco=payload.origem_preco,
            spirometry_exam_id=payload.spirometry_exam_id,
            consultation_id=payload.consultation_id,
            partner_referral_id=payload.partner_referral_id,
            idempotency_key=key,
            idempotency_fingerprint=fingerprint,
        )
        nd = parse_incomplete_date(payload.data_competencia)
        entry.data_competencia = nd.value
        entry.data_competencia_original = nd.original or None
        entry.data_competencia_precisao = nd.precision
        entry.data_competencia_dia_assumido = nd.day_assumed
        db.add(entry)
        db.flush()
        return entry

    entry, ja_existia = idempotent_create(
        db, FinancialEntry, payload.idempotency_key,
        payload.model_dump(mode="json"), factory,
    )
    if ja_existia:
        data = ser_financial_entry(entry)
        data["idempotente"] = True
        return data
    audit(db, "lancamento.criado", "financial_entries", entry.id, user.id,
          request.state.request_id,
          {"public_code": entry.public_code, "tipo": entry.tipo, "status": entry.status})
    db.commit()
    return ser_financial_entry(entry)


@router.get("/repasses")
def list_transfers(
    partner_id: str | None = None,
    status: str | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    stmt = select(PartnerTransfer).order_by(PartnerTransfer.created_at.desc())
    if partner_id:
        stmt = stmt.where(PartnerTransfer.partner_id == partner_id)
    if status:
        stmt = stmt.where(PartnerTransfer.status == status)
    return paginate(db, stmt, params, ser_transfer)


@router.post("/repasses", status_code=201)
def create_transfer(
    payload: TransferCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_GESTOR)),
):
    ensure_partner_exists(db, payload.partner_id)
    # coerência cruzada: parceria/encaminhamento/acerto do MESMO parceiro
    partnership = ensure_partnership_of_partner(
        db, payload.partnership_id, payload.partner_id
    )
    referral = ensure_referral_of_partner(db, payload.partner_referral_id, payload.partner_id)
    settlement = ensure_settlement_of_partner(db, payload.settlement_id, payload.partner_id)
    if (
        partnership
        and settlement
        and settlement.partnership_id not in (None, partnership.id)
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "codigo": "acerto_parceria_incoerente",
                "mensagem": "O acerto pertence a outra parceria do mesmo parceiro.",
            },
        )
    if payload.financial_entry_id:
        entry = db.get(FinancialEntry, payload.financial_entry_id)
        if entry is None:
            raise HTTPException(status_code=422, detail={
                "codigo": "vinculo_inexistente", "mensagem": "Lançamento não encontrado."})
        if referral:
            ensure_entry_for_referral(db, entry.id, referral)
    if payload.status == "pago" and payload.data_pagamento is None:
        raise HTTPException(status_code=422, detail={
            "codigo": "pagamento_sem_data",
            "mensagem": "Repasse pago exige data_pagamento."})

    def factory(key, fingerprint):
        transfer = PartnerTransfer(
            partner_id=payload.partner_id,
            partnership_id=payload.partnership_id,
            partner_referral_id=payload.partner_referral_id,
            settlement_id=payload.settlement_id,
            financial_entry_id=payload.financial_entry_id,
            valor=_q(payload.valor),
            status=payload.status,
            data_prevista=payload.data_prevista,
            data_pagamento=payload.data_pagamento,
            idempotency_key=key,
            idempotency_fingerprint=fingerprint,
        )
        db.add(transfer)
        db.flush()
        return transfer

    transfer, ja_existia = idempotent_create(
        db, PartnerTransfer, payload.idempotency_key,
        payload.model_dump(mode="json"), factory,
    )
    if ja_existia:
        data = ser_transfer(transfer)
        data["idempotente"] = True
        return data
    audit(db, "repasse.criado", "partner_transfers", transfer.id, user.id,
          request.state.request_id, {"status": transfer.status})
    db.commit()
    return ser_transfer(transfer)
