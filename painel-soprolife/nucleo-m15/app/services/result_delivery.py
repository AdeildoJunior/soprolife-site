"""M26.33 — autorização explícita para um terceiro receber o resultado.

Mesma disciplina de `services/relationships.py`: nada de matching
aproximado, criação idempotente pela chave natural exata (paciente +
destinatário) e conflito de conteúdo recusado em vez de sobrescrito.

O que este módulo deliberadamente NÃO faz: criar pessoa, criar atendimento,
criar exame, copiar contato, emitir link de portal ou enviar qualquer coisa.
Autorizar a entrega e executá-la são atos distintos — o envio continua
dependendo de laudo concluído, assinado e liberado.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Person, ResultDeliveryAuthorization

# A relação é a do DESTINATÁRIO com o PACIENTE. `legal_guardian` está fora
# de propósito: representação legal vive em `person_relationships`, com
# evidência de tutela/curatela, e não se declara de passagem aqui.
RELATIONSHIP_TYPES = ("daughter", "son", "spouse", "parent", "sibling", "other")


class ResultDeliveryError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def get_authorization(
    db: Session, patient_person_id: str, recipient_person_id: str
) -> ResultDeliveryAuthorization | None:
    return db.execute(
        select(ResultDeliveryAuthorization).where(
            ResultDeliveryAuthorization.patient_person_id == patient_person_id,
            ResultDeliveryAuthorization.recipient_person_id == recipient_person_id,
        )
    ).scalar_one_or_none()


def list_authorizations(
    db: Session, patient_person_id: str, *, active: bool | None = None
) -> list[ResultDeliveryAuthorization]:
    stmt = (
        select(ResultDeliveryAuthorization)
        .where(ResultDeliveryAuthorization.patient_person_id == patient_person_id)
        .order_by(
            ResultDeliveryAuthorization.created_at,
            ResultDeliveryAuthorization.id,
        )
    )
    if active is not None:
        stmt = stmt.where(ResultDeliveryAuthorization.active == active)
    return list(db.execute(stmt).scalars().all())


def create_authorization(
    db: Session,
    *,
    patient_person_id: str,
    recipient_person_id: str,
    relationship_type: str,
    registered_by_user_id: str,
    active: bool = True,
) -> tuple[ResultDeliveryAuthorization, bool]:
    """Cria pela chave natural exata; replay devolve a MESMA linha.

    Devolve `(linha, replay)`. Um replay com conteúdo divergente é recusado:
    mudar a relação declarada ou a vigência é uma decisão humana, feita pelo
    endpoint próprio, nunca um efeito colateral de reenviar o cadastro.
    """
    if relationship_type not in RELATIONSHIP_TYPES:
        raise ResultDeliveryError("relationship_type_unsupported")
    if patient_person_id == recipient_person_id:
        raise ResultDeliveryError("patient_and_recipient_must_differ")
    patient = db.get(Person, patient_person_id)
    if patient is None:
        raise ResultDeliveryError("patient_person_not_found")
    recipient = db.get(Person, recipient_person_id)
    if recipient is None:
        raise ResultDeliveryError("recipient_person_not_found")
    if recipient.arquivado:
        raise ResultDeliveryError("recipient_person_archived")

    existing = get_authorization(db, patient_person_id, recipient_person_id)
    if existing is not None:
        if (
            existing.relationship_type != relationship_type
            or existing.active != active
        ):
            raise ResultDeliveryError("authorization_payload_conflict")
        return existing, True

    authorization = ResultDeliveryAuthorization(
        patient_person_id=patient_person_id,
        recipient_person_id=recipient_person_id,
        relationship_type=relationship_type,
        active=active,
        registered_by_user_id=registered_by_user_id,
    )
    try:
        with db.begin_nested():
            db.add(authorization)
            db.flush()
    except IntegrityError:
        existing = get_authorization(db, patient_person_id, recipient_person_id)
        if existing is None:
            raise
        if (
            existing.relationship_type != relationship_type
            or existing.active != active
        ):
            raise ResultDeliveryError("authorization_payload_conflict")
        return existing, True
    return authorization, False
