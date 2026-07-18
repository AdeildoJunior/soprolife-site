"""Clínicas e parceiros: parceiro, unidades, contatos, parcerias, encaminhamentos.

Hardening M15.1A:
- integridade cruzada validada na transação (unidade/contato/parceria do
  parceiro certo; exame/consulta da pessoa certa; lançamento coerente);
- campos financeiros do encaminhamento em endpoint próprio (gestor/admin);
- parcerias (percentual/valor de repasse) exigem gestor;
- mudança de autorização/responsável sincroniza o follow-up na transação.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..dates import parse_incomplete_date
from ..db import get_db
from ..domain import ensure_sem_pcmso
from ..ids import allocate_public_code
from ..models import (
    Partner,
    PartnerContact,
    PartnerReferral,
    PartnerSettlement,
    PartnerUnit,
    Partnership,
    Person,
    User,
)
from ..pagination import PageParams, paginate
from ..schemas import (
    PartnerContactCreate,
    PartnerContactUpdate,
    PartnerCreate,
    PartnershipCreate,
    PartnershipUpdate,
    PartnerUnitCreate,
    PartnerUnitUpdate,
    PartnerUpdate,
    ReferralCreate,
    ReferralFinanceUpdate,
    ReferralUpdate,
)
from ..security import ROLE_GESTOR, ROLE_LEITURA, ROLE_OPERACIONAL, require_role
from ..serializers import (
    money,
    ser_partner,
    ser_partner_contact,
    ser_partner_unit,
    ser_partnership,
    ser_referral,
)
from ..services.followup import sync_followup_for_origin
from ..services.integrity import (
    ensure_consultation_of_person,
    ensure_contact_of_partner,
    ensure_entry_for_referral,
    ensure_exam_of_person,
    ensure_partner_exists,
    ensure_referral_state,
    ensure_unit_of_partner,
)

router = APIRouter(tags=["parceiros"])

PARTNER_DETAIL_MAX = 50


def _apply_date(obj, prefix: str, raw: str | None) -> None:
    nd = parse_incomplete_date(raw)
    setattr(obj, prefix, nd.value)
    setattr(obj, f"{prefix}_original", nd.original or None)
    setattr(obj, f"{prefix}_precisao", nd.precision)
    setattr(obj, f"{prefix}_dia_assumido", nd.day_assumed)


def _check_pcmso(db, request, user, payload, contexto: str) -> None:
    try:
        ensure_sem_pcmso(payload.model_dump(exclude_none=True), contexto)
    except HTTPException as exc:
        audit(db, "pcmso.rejeitado", user_id=user.id,
              request_id=request.state.request_id,
              detalhes=exc.detail if isinstance(exc.detail, dict) else None)
        db.commit()
        raise


# ---------------------------------------------------------------- parceiros

@router.get("/parceiros")
def list_partners(
    status: str | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    stmt = select(Partner).order_by(Partner.created_at.desc())
    if status:
        stmt = stmt.where(Partner.status == status)
    return paginate(db, stmt, params, ser_partner)


@router.post("/parceiros", status_code=201)
def create_partner(
    payload: PartnerCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    _check_pcmso(db, request, user, payload, "parceiros.create")
    partner = Partner(
        public_code=allocate_public_code(db, "partners"),
        nome=payload.nome,
        tipo=payload.tipo,
        status=payload.status,
        cidade=payload.cidade,
        observacao=payload.observacao,
    )
    db.add(partner)
    db.flush()
    audit(db, "parceiro.criado", "partners", partner.id, user.id,
          request.state.request_id, {"public_code": partner.public_code})
    db.commit()
    return ser_partner(partner)


def _limited(db: Session, model, partner_id: str, serializer) -> dict:
    total = db.execute(
        select(func.count()).select_from(model).where(model.partner_id == partner_id)
    ).scalar_one()
    rows = db.execute(
        select(model).where(model.partner_id == partner_id).limit(PARTNER_DETAIL_MAX)
    ).scalars().all()
    return {"total": total, "itens": [serializer(r) for r in rows]}


@router.get("/parceiros/{partner_id}")
def get_partner(
    partner_id: str,
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    partner = ensure_partner_exists(db, partner_id)
    data = ser_partner(partner)
    # agregados limitados (máx. 50 cada) com total eficiente por count
    data["unidades"] = _limited(db, PartnerUnit, partner_id, ser_partner_unit)
    data["contatos"] = _limited(db, PartnerContact, partner_id, ser_partner_contact)
    data["parcerias"] = _limited(db, Partnership, partner_id, ser_partnership)
    return data


@router.patch("/parceiros/{partner_id}")
def update_partner(
    partner_id: str,
    payload: PartnerUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    partner = ensure_partner_exists(db, partner_id)
    _check_pcmso(db, request, user, payload, "parceiros.update")
    changed = []
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(partner, field, value)
        changed.append(field)
    audit(db, "parceiro.atualizado", "partners", partner.id, user.id,
          request.state.request_id, {"campos": changed})
    db.commit()
    return ser_partner(partner)


# ----------------------------------------------------------------- unidades

@router.get("/unidades")
def list_units(
    partner_id: str | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    stmt = select(PartnerUnit).order_by(PartnerUnit.created_at.desc())
    if partner_id:
        stmt = stmt.where(PartnerUnit.partner_id == partner_id)
    return paginate(db, stmt, params, ser_partner_unit)


@router.post("/unidades", status_code=201)
def create_unit(
    payload: PartnerUnitCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    ensure_partner_exists(db, payload.partner_id)
    unit = PartnerUnit(
        public_code=allocate_public_code(db, "partner_units"),
        partner_id=payload.partner_id,
        nome=payload.nome,
        bairro=payload.bairro,
        cidade=payload.cidade,
        observacao=payload.observacao,
    )
    db.add(unit)
    db.flush()
    audit(db, "unidade.criada", "partner_units", unit.id, user.id,
          request.state.request_id, {"public_code": unit.public_code})
    db.commit()
    return ser_partner_unit(unit)


@router.patch("/unidades/{unit_id}")
def update_unit(
    unit_id: str,
    payload: PartnerUnitUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    unit = db.get(PartnerUnit, unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail="Unidade não encontrada.")
    changed = []
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(unit, field, value)
        changed.append(field)
    audit(db, "unidade.atualizada", "partner_units", unit.id, user.id,
          request.state.request_id, {"campos": changed})
    db.commit()
    return ser_partner_unit(unit)


# ------------------------------------------------------- contatos de parceiro

@router.get("/contatos-parceiros")
def list_partner_contacts(
    partner_id: str | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    stmt = select(PartnerContact).order_by(PartnerContact.created_at.desc())
    if partner_id:
        stmt = stmt.where(PartnerContact.partner_id == partner_id)
    return paginate(db, stmt, params, ser_partner_contact)


@router.post("/contatos-parceiros", status_code=201)
def create_partner_contact(
    payload: PartnerContactCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    ensure_partner_exists(db, payload.partner_id)
    ensure_unit_of_partner(db, payload.partner_unit_id, payload.partner_id)
    contact = PartnerContact(
        public_code=allocate_public_code(db, "partner_contacts"),
        partner_id=payload.partner_id,
        partner_unit_id=payload.partner_unit_id,
        nome=payload.nome,
        cargo=payload.cargo,
        telefone=payload.telefone,
        email=payload.email,
        principal=payload.principal,
        observacao=payload.observacao,
    )
    db.add(contact)
    db.flush()
    audit(db, "contato_parceiro.criado", "partner_contacts", contact.id, user.id,
          request.state.request_id, {"public_code": contact.public_code})
    db.commit()
    return ser_partner_contact(contact)


@router.patch("/contatos-parceiros/{contact_id}")
def update_partner_contact(
    contact_id: str,
    payload: PartnerContactUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    contact = db.get(PartnerContact, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contato não encontrado.")
    updates = payload.model_dump(exclude_none=True)
    if "partner_unit_id" in updates:
        ensure_unit_of_partner(db, updates["partner_unit_id"], contact.partner_id)
    changed = []
    for field, value in updates.items():
        setattr(contact, field, value)
        changed.append(field)
    audit(db, "contato_parceiro.atualizado", "partner_contacts", contact.id, user.id,
          request.state.request_id, {"campos": changed})
    db.commit()
    return ser_partner_contact(contact)


# ---------------------------------------------------------------- parcerias

@router.get("/parcerias")
def list_partnerships(
    partner_id: str | None = None,
    status: str | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    stmt = select(Partnership).order_by(Partnership.created_at.desc())
    if partner_id:
        stmt = stmt.where(Partnership.partner_id == partner_id)
    if status:
        stmt = stmt.where(Partnership.status == status)
    return paginate(db, stmt, params, ser_partnership)


@router.post("/parcerias", status_code=201)
def create_partnership(
    payload: PartnershipCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_GESTOR)),
):
    """Parceria define percentual/valor de repasse — papel gestor/admin."""
    ensure_partner_exists(db, payload.partner_id)
    partnership = Partnership(
        public_code=allocate_public_code(db, "partnerships"),
        partner_id=payload.partner_id,
        status=payload.status,
        modelo_repasse=payload.modelo_repasse,
        percentual_repasse=payload.percentual_repasse,
        valor_repasse_fixo=payload.valor_repasse_fixo,
        responsavel_soprolife=payload.responsavel_soprolife,
        responsavel_followup=payload.responsavel_followup,
        observacao=payload.observacao,
    )
    _apply_date(partnership, "data_inicio", payload.data_inicio)
    db.add(partnership)
    db.flush()
    audit(db, "parceria.criada", "partnerships", partnership.id, user.id,
          request.state.request_id, {"public_code": partnership.public_code})
    db.commit()
    return ser_partnership(partnership)


@router.patch("/parcerias/{partnership_id}")
def update_partnership(
    partnership_id: str,
    payload: PartnershipUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_GESTOR)),
):
    """Parceria envolve repasses — mutação restrita a gestor/admin."""
    partnership = db.get(Partnership, partnership_id)
    if not partnership:
        raise HTTPException(status_code=404, detail="Parceria não encontrada.")
    updates = payload.model_dump(exclude_none=True)
    changed = []
    for field, value in updates.items():
        if field == "data_inicio":
            _apply_date(partnership, "data_inicio", value)
        else:
            setattr(partnership, field, value)
        changed.append(field)
    audit(db, "parceria.atualizada", "partnerships", partnership.id, user.id,
          request.state.request_id, {"campos": changed})
    db.commit()
    return ser_partnership(partnership)


# ---------------------------------------------------------- encaminhamentos

def _referral_or_404(db: Session, referral_id: str) -> PartnerReferral:
    referral = db.get(PartnerReferral, referral_id)
    if not referral:
        raise HTTPException(status_code=404, detail="Encaminhamento não encontrado.")
    return referral


def _sync_referral_followup(db: Session, referral: PartnerReferral) -> dict:
    """Follow-up do encaminhamento: fail-closed para autorização desconhecida.

    Só existe follow-up acionável quando a clínica AUTORIZOU explicitamente
    o contato da SoproLife (True). None (desconhecido) e False não criam
    fila — e cancelam o pendente se a autorização for retirada.
    """
    person = db.get(Person, referral.person_id)
    autorizado = referral.autorizacao_contato_soprolife is True
    ativo = autorizado and referral.status not in ("Cancelado", "Concluído")
    fup, motivo = sync_followup_for_origin(
        db, person, "encaminhamento_parceiro", "partner_referrals", referral.id,
        due=referral.proximo_followup or referral.data_agendada or referral.data_encaminhamento,
        active=ativo,
        responsavel=referral.responsavel_soprolife,
        controlado_por_parceiro=referral.responsavel_followup == "parceiro",
        partner_id=referral.partner_id,
    )
    return {"motivo": motivo, "id": fup.id if fup else None}


@router.get("/encaminhamentos")
def list_referrals(
    partner_id: str | None = None,
    status: str | None = None,
    person_id: str | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    stmt = select(PartnerReferral).order_by(PartnerReferral.created_at.desc())
    if partner_id:
        stmt = stmt.where(PartnerReferral.partner_id == partner_id)
    if status:
        stmt = stmt.where(PartnerReferral.status == status)
    if person_id:
        stmt = stmt.where(PartnerReferral.person_id == person_id)
    return paginate(db, stmt, params, ser_referral)


@router.post("/encaminhamentos", status_code=201)
def create_referral(
    payload: ReferralCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    _check_pcmso(db, request, user, payload, "encaminhamentos.create")
    person = db.get(Person, payload.person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada.")
    ensure_partner_exists(db, payload.partner_id)
    ensure_unit_of_partner(db, payload.partner_unit_id, payload.partner_id)
    ensure_contact_of_partner(
        db, payload.partner_contact_id, payload.partner_id, payload.partner_unit_id
    )
    referral = PartnerReferral(
        public_code=allocate_public_code(db, "partner_referrals"),
        person_id=payload.person_id,
        partner_id=payload.partner_id,
        partner_unit_id=payload.partner_unit_id,
        partner_contact_id=payload.partner_contact_id,
        servico_solicitado=payload.servico_solicitado,
        data_agendada=payload.data_agendada,
        status=payload.status,
        responsavel_soprolife=payload.responsavel_soprolife,
        observacao_operacional=payload.observacao_operacional,
        autorizacao_contato_soprolife=payload.autorizacao_contato_soprolife,
        responsavel_followup=payload.responsavel_followup,
    )
    _apply_date(referral, "data_encaminhamento", payload.data_encaminhamento)
    db.add(referral)
    db.flush()
    fup_info = _sync_referral_followup(db, referral)
    audit(db, "encaminhamento.criado", "partner_referrals", referral.id, user.id,
          request.state.request_id,
          {"public_code": referral.public_code, "followup": fup_info["motivo"]})
    db.commit()
    data = ser_referral(referral)
    data["followup"] = fup_info
    return data


@router.patch("/encaminhamentos/{referral_id}")
def update_referral(
    referral_id: str,
    payload: ReferralUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    referral = _referral_or_404(db, referral_id)
    _check_pcmso(db, request, user, payload, "encaminhamentos.update")
    updates = payload.model_dump(exclude_none=True)
    # integridade cruzada ANTES de aplicar
    if "partner_unit_id" in updates:
        ensure_unit_of_partner(db, updates["partner_unit_id"], referral.partner_id)
    if "partner_contact_id" in updates:
        ensure_contact_of_partner(
            db, updates["partner_contact_id"], referral.partner_id,
            updates.get("partner_unit_id", referral.partner_unit_id),
        )
    if "spirometry_exam_id" in updates:
        ensure_exam_of_person(db, updates["spirometry_exam_id"], referral.person_id)
    if "consultation_id" in updates:
        ensure_consultation_of_person(db, updates["consultation_id"], referral.person_id)
    ensure_referral_state(referral, updates)
    changed = []
    for field, value in updates.items():
        setattr(referral, field, value)
        changed.append(field)
    # autorização/responsável mudou -> follow-up sincroniza na MESMA transação
    fup_info = _sync_referral_followup(db, referral)
    audit(db, "encaminhamento.atualizado", "partner_referrals", referral.id, user.id,
          request.state.request_id, {"campos": changed, "followup": fup_info["motivo"]})
    db.commit()
    data = ser_referral(referral)
    data["followup"] = fup_info
    return data


@router.patch("/encaminhamentos/{referral_id}/financeiro")
def update_referral_finance(
    referral_id: str,
    payload: ReferralFinanceUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_GESTOR)),
):
    """Valores/repasses do encaminhamento — exclusivo de gestor/admin."""
    referral = _referral_or_404(db, referral_id)
    updates = payload.model_dump(exclude_none=True)
    if "financial_entry_id" in updates:
        ensure_entry_for_referral(db, updates["financial_entry_id"], referral)
    ensure_referral_state(referral, updates)
    changed = []
    for field, value in updates.items():
        setattr(referral, field, value)
        changed.append(field)
    audit(db, "encaminhamento.financeiro_atualizado", "partner_referrals",
          referral.id, user.id, request.state.request_id, {"campos": changed})
    db.commit()
    return ser_referral(referral)


# ------------------------------------------------------------------ acertos

@router.get("/acertos-parceiros")
def list_settlements(
    partner_id: str | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    stmt = select(PartnerSettlement).order_by(PartnerSettlement.created_at.desc())
    if partner_id:
        stmt = stmt.where(PartnerSettlement.partner_id == partner_id)

    def ser(s: PartnerSettlement) -> dict:
        return {
            "id": s.id,
            "partner_id": s.partner_id,
            "partnership_id": s.partnership_id,
            "periodo_inicio": s.periodo_inicio.isoformat() if s.periodo_inicio else None,
            "periodo_fim": s.periodo_fim.isoformat() if s.periodo_fim else None,
            "valor_total": money(s.valor_total),
            "status": s.status,
        }

    return paginate(db, stmt, params, ser)
