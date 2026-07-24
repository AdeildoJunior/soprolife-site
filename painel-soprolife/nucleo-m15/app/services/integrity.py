"""Integridade cruzada entre parceiro, unidade, contato, pessoa, atendimento e
financeiro — validada na transação, com 409/422 estruturados.

FKs simples garantem existência; estas funções garantem COERÊNCIA:
unidade do parceiro certo, exame da pessoa certa, repasse do parceiro certo.
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import (
    Consultation,
    FinancialEntry,
    Partner,
    PartnerContact,
    PartnerReferral,
    PartnerSettlement,
    PartnerUnit,
    Partnership,
    SpirometryExam,
)


def _domain_error(status: int, codigo: str, mensagem: str, **extra):
    detail = {"codigo": codigo, "mensagem": mensagem}
    detail.update({k: v for k, v in extra.items() if v is not None})
    return HTTPException(status_code=status, detail=detail)


def _get_or_422(db: Session, model, entity_id: str, nome: str):
    obj = db.get(model, entity_id)
    if obj is None:
        raise _domain_error(422, "vinculo_inexistente", f"{nome} não encontrado(a).",
                            id=entity_id)
    return obj


def ensure_partner_exists(db: Session, partner_id: str) -> Partner:
    partner = db.get(Partner, partner_id)
    if partner is None:
        raise HTTPException(status_code=404, detail="Parceiro não encontrado.")
    # M20: parceiro consolidado sai da operação — escrever nele voltaria a
    # dividir a mesma parceria em dois registros.
    if partner.arquivado:
        raise _domain_error(
            409, "parceiro_arquivado",
            "Parceiro arquivado por consolidação — use o parceiro canônico.",
            id=partner.merged_into_partner_id,
        )
    return partner


def ensure_unit_of_partner(db: Session, unit_id: str | None, partner_id: str) -> PartnerUnit | None:
    if unit_id is None:
        return None
    unit = _get_or_422(db, PartnerUnit, unit_id, "Unidade")
    if unit.partner_id != partner_id:
        raise _domain_error(422, "unidade_de_outro_parceiro",
                            "A unidade informada pertence a outro parceiro.")
    return unit


def ensure_contact_of_partner(
    db: Session, contact_id: str | None, partner_id: str, unit_id: str | None = None
) -> PartnerContact | None:
    if contact_id is None:
        return None
    contact = _get_or_422(db, PartnerContact, contact_id, "Contato de parceiro")
    if contact.partner_id != partner_id:
        raise _domain_error(422, "contato_de_outro_parceiro",
                            "O contato informado pertence a outro parceiro.")
    if unit_id and contact.partner_unit_id and contact.partner_unit_id != unit_id:
        raise _domain_error(422, "contato_de_outra_unidade",
                            "O contato informado pertence a outra unidade.")
    return contact


def ensure_partnership_of_partner(
    db: Session, partnership_id: str | None, partner_id: str
) -> Partnership | None:
    if partnership_id is None:
        return None
    partnership = _get_or_422(db, Partnership, partnership_id, "Parceria")
    if partnership.partner_id != partner_id:
        raise _domain_error(422, "parceria_de_outro_parceiro",
                            "A parceria informada pertence a outro parceiro.")
    return partnership


def ensure_exam_of_person(
    db: Session, exam_id: str | None, person_id: str
) -> SpirometryExam | None:
    if exam_id is None:
        return None
    exam = _get_or_422(db, SpirometryExam, exam_id, "Exame")
    if exam.person_id != person_id:
        raise _domain_error(422, "exame_de_outra_pessoa",
                            "O exame informado pertence a outra pessoa.")
    return exam


def ensure_consultation_of_person(
    db: Session, consultation_id: str | None, person_id: str
) -> Consultation | None:
    if consultation_id is None:
        return None
    consultation = _get_or_422(db, Consultation, consultation_id, "Consulta")
    if consultation.person_id != person_id:
        raise _domain_error(422, "consulta_de_outra_pessoa",
                            "A consulta informada pertence a outra pessoa.")
    return consultation


def ensure_entry_for_referral(
    db: Session, entry_id: str | None, referral: PartnerReferral
) -> FinancialEntry | None:
    """Lançamento ligado ao encaminhamento deve apontar para o MESMO
    atendimento (exame/consulta) ou para o próprio encaminhamento."""
    if entry_id is None:
        return None
    entry = _get_or_422(db, FinancialEntry, entry_id, "Lançamento")
    coerente = (
        (entry.partner_referral_id in (None, referral.id))
        and (entry.spirometry_exam_id is None
             or entry.spirometry_exam_id == referral.spirometry_exam_id)
        and (entry.consultation_id is None
             or entry.consultation_id == referral.consultation_id)
    )
    if not coerente:
        raise _domain_error(409, "lancamento_incoerente",
                            "O lançamento aponta para outro atendimento/encaminhamento.")
    return entry


def ensure_financial_links_coherent(
    db: Session,
    exam_id: str | None,
    consultation_id: str | None,
    referral_id: str | None,
) -> None:
    """Valida que todos os IDs técnicos de um lançamento descrevem a mesma
    pessoa e o mesmo encaminhamento, quando mais de um vínculo é informado."""
    exam = _get_or_422(db, SpirometryExam, exam_id, "Exame") if exam_id else None
    consultation = (
        _get_or_422(db, Consultation, consultation_id, "Consulta")
        if consultation_id else None
    )
    referral = (
        _get_or_422(db, PartnerReferral, referral_id, "Encaminhamento")
        if referral_id else None
    )
    person_ids = {
        obj.person_id for obj in (exam, consultation, referral) if obj is not None
    }
    if len(person_ids) > 1:
        raise _domain_error(
            409,
            "financeiro_pessoas_incoerentes",
            "Os vínculos do lançamento pertencem a pessoas diferentes.",
        )
    if referral is not None:
        if exam is not None and referral.spirometry_exam_id not in (None, exam.id):
            raise _domain_error(
                409,
                "financeiro_exame_incoerente",
                "O exame não corresponde ao atendimento do encaminhamento.",
            )
        if consultation is not None and referral.consultation_id not in (
            None,
            consultation.id,
        ):
            raise _domain_error(
                409,
                "financeiro_consulta_incoerente",
                "A consulta não corresponde ao atendimento do encaminhamento.",
            )


def ensure_settlement_of_partner(
    db: Session, settlement_id: str | None, partner_id: str
) -> PartnerSettlement | None:
    if settlement_id is None:
        return None
    settlement = _get_or_422(db, PartnerSettlement, settlement_id, "Acerto")
    if settlement.partner_id != partner_id:
        raise _domain_error(422, "acerto_de_outro_parceiro",
                            "O acerto informado pertence a outro parceiro.")
    return settlement


def ensure_referral_of_partner(
    db: Session, referral_id: str | None, partner_id: str
) -> PartnerReferral | None:
    if referral_id is None:
        return None
    referral = _get_or_422(db, PartnerReferral, referral_id, "Encaminhamento")
    if referral.partner_id != partner_id:
        raise _domain_error(422, "encaminhamento_de_outro_parceiro",
                            "O encaminhamento informado pertence a outro parceiro.")
    return referral


REFERRAL_DONE_STATUSES = {
    "Realizado", "Laudo enviado", "Aguardando pagamento", "Concluído",
}


def ensure_referral_state(referral: PartnerReferral, updates: dict) -> None:
    """Invariantes de estado do encaminhamento (laudo/pagamento/repasse)."""
    status = updates.get("status", referral.status)
    laudo = updates.get("laudo_enviado", referral.laudo_enviado)
    data_realizacao = updates.get("data_realizacao", referral.data_realizacao)
    tem_atendimento = (
        updates.get("spirometry_exam_id", referral.spirometry_exam_id)
        or updates.get("consultation_id", referral.consultation_id)
        or data_realizacao
    )
    if laudo and not tem_atendimento:
        raise _domain_error(409, "laudo_sem_atendimento",
                            "Laudo enviado exige atendimento realizado "
                            "(exame/consulta vinculado ou data de realização).")
    if status == "Laudo enviado" and not laudo:
        raise _domain_error(409, "status_laudo_incoerente",
                            "Status 'Laudo enviado' exige laudo_enviado=true.")
    valor_recebido = updates.get("valor_recebido", referral.valor_recebido)
    if valor_recebido is not None and status not in REFERRAL_DONE_STATUSES:
        raise _domain_error(409, "recebimento_sem_realizacao",
                            "Valor recebido exige atendimento realizado.")


TRANSFER_STATUS_FLOW = {
    "previsto": {"aguardando", "pago", "cancelado"},
    "aguardando": {"pago", "cancelado"},
    "pago": set(),
    "cancelado": set(),
}
