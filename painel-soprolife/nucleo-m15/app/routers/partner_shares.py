"""Acerto individual genérico; Pastore conserva seu fechamento mensal existente."""
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from ..db import get_db
from ..models import ExamPartnerShare, User, PartnerCommercialRule, Partner, PartnerUnit, Partnership
from ..schemas import StrictModel, Money, FormaPagamento, Percent
from ..audit import audit
from ..security import ROLE_GESTOR, ROLE_LEITURA, require_role
from ..services import partner_shares

router = APIRouter(tags=["partilhas de parceiros"])


class ShareMovementCreate(StrictModel):
    valor: Money
    natureza: Literal["acerto", "regularizacao"]
    direcao: Literal["entrada", "saida"]
    forma_pagamento: FormaPagamento
    conta: str = Field(min_length=1, max_length=60)
    origem_bancaria: str | None = Field(default=None, max_length=120)
    ocorrido_em: datetime
    idempotency_key: str = Field(min_length=4, max_length=64)

    @field_validator("ocorrido_em")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Informe data/hora com fuso horário.")
        return value


@router.get("/financeiro/partilhas")
def shares(db: Session = Depends(get_db), _user: User = Depends(require_role(ROLE_LEITURA))):
    rows = db.scalars(select(ExamPartnerShare).order_by(ExamPartnerShare.created_at.desc())).all()
    return {"itens": [partner_shares.position(db, s) for s in rows]}


@router.post("/financeiro/partilhas/{share_id}/movimentos", status_code=201)
def movement(share_id: str, payload: ShareMovementCreate, request: Request,
             db: Session = Depends(get_db), user: User = Depends(require_role(ROLE_GESTOR))):
    try:
        result = partner_shares.record_movement(db, share_id, payload, user.id, request.state.request_id)
        db.commit()
        return result
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Movimento já registrado ou vínculo inconsistente; recarregue a partilha.") from None
    except Exception:
        db.rollback()
        raise


class CommercialRuleCreate(StrictModel):
    codigo: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_]+$")
    partner_unit_id: str
    modalidade: Literal["clinica_parceira"] = "clinica_parceira"
    vigencia_inicio: date
    vigencia_fim: date | None = None
    percentual_parceiro: Percent
    percentual_soprolife: Percent


@router.post("/parcerias/{partnership_id}/regras-comerciais", status_code=201)
def commercial_rule(partnership_id: str, payload: CommercialRuleCreate, request: Request,
                    db: Session = Depends(get_db), user: User = Depends(require_role(ROLE_GESTOR))):
    """Nova vigência comercial. Não cria nem habilita regra/política fiscal."""
    agreement = db.scalar(select(Partnership).where(Partnership.id == partnership_id).with_for_update())
    if not agreement:
        raise HTTPException(404, "Parceria não encontrada.")
    unit = db.get(PartnerUnit, payload.partner_unit_id)
    partner = db.get(Partner, agreement.partner_id)
    if agreement.status != "ativa" or partner.arquivado or not unit or not unit.ativo or unit.partner_id != partner.id:
        raise HTTPException(422, "Parceiro, parceria e unidade devem estar ativos e coerentes.")
    if payload.percentual_parceiro + payload.percentual_soprolife != 100:
        raise HTTPException(422, "Percentuais devem somar 100%.")
    if payload.vigencia_fim and payload.vigencia_fim < payload.vigencia_inicio:
        raise HTTPException(422, "Fim da vigência anterior ao início.")
    versions = db.scalars(select(PartnerCommercialRule).where(PartnerCommercialRule.codigo == payload.codigo)).all()
    if any(r.partnership_id != partnership_id for r in versions):
        raise HTTPException(409, "Código comercial já pertence a outra parceria.")
    if any(r.vigencia_inicio == payload.vigencia_inicio for r in versions):
        raise HTTPException(409, "Vigência já cadastrada; regras anteriores são imutáveis.")
    fiscal_flow = max(versions, key=partner_shares.rule_version_key).fiscal_flow if versions else None
    row = PartnerCommercialRule(**payload.model_dump(), partnership_id=agreement.id,
                                fiscal_flow=fiscal_flow, created_by_user_id=user.id)
    db.add(row)
    try:
        db.flush()
        audit(db, "parceria.regra_criada", "partner_commercial_rules", row.id, user.id, request.state.request_id,
              {"tipo": payload.codigo, "origin_partner_unit_id": unit.id})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Regra já cadastrada ou vínculo inconsistente.") from None
    return {"id": row.id, "codigo": row.codigo, "vigencia_inicio": row.vigencia_inicio.isoformat()}
