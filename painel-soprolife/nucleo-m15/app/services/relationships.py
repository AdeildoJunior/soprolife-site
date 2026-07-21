"""Relações explícitas entre pessoas, sem qualquer matching aproximado."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Person, PersonRelationship


class RelationshipError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _person_exists(db: Session, person_id: str) -> bool:
    return db.get(Person, person_id) is not None


def get_relationship(
    db: Session,
    minor_person_id: str,
    guardian_person_id: str,
    relationship_type: str,
) -> PersonRelationship | None:
    return db.execute(
        select(PersonRelationship).where(
            PersonRelationship.minor_person_id == minor_person_id,
            PersonRelationship.guardian_person_id == guardian_person_id,
            PersonRelationship.relationship_type == relationship_type,
        )
    ).scalar_one_or_none()


def create_relationship(
    db: Session,
    *,
    minor_person_id: str,
    guardian_person_id: str,
    relationship_type: str,
    is_legal_guardian: bool,
    active: bool = True,
) -> tuple[PersonRelationship, bool]:
    """Cria pela chave técnica exata; replay devolve a mesma linha."""
    if minor_person_id == guardian_person_id:
        raise RelationshipError("minor_and_guardian_must_differ")
    if not _person_exists(db, minor_person_id):
        raise RelationshipError("minor_person_not_found")
    if not _person_exists(db, guardian_person_id):
        raise RelationshipError("guardian_person_not_found")
    existing = get_relationship(
        db, minor_person_id, guardian_person_id, relationship_type
    )
    if existing is not None:
        if (
            existing.is_legal_guardian != is_legal_guardian
            or existing.active != active
        ):
            raise RelationshipError("relationship_payload_conflict")
        return existing, True
    relationship = PersonRelationship(
        minor_person_id=minor_person_id,
        guardian_person_id=guardian_person_id,
        relationship_type=relationship_type,
        is_legal_guardian=is_legal_guardian,
        active=active,
    )
    try:
        with db.begin_nested():
            db.add(relationship)
            db.flush()
    except IntegrityError:
        existing = get_relationship(
            db, minor_person_id, guardian_person_id, relationship_type
        )
        if existing is None:
            raise
        if (
            existing.is_legal_guardian != is_legal_guardian
            or existing.active != active
        ):
            raise RelationshipError("relationship_payload_conflict")
        return existing, True
    return relationship, False


def legal_contact_relationship(
    db: Session, patient_person_id: str, contact_person_id: str
) -> PersonRelationship | None:
    """Autoriza contato separado somente por vínculo legal ativo explícito."""
    return db.execute(
        select(PersonRelationship).where(
            PersonRelationship.minor_person_id == patient_person_id,
            PersonRelationship.guardian_person_id == contact_person_id,
            PersonRelationship.is_legal_guardian.is_(True),
            PersonRelationship.active.is_(True),
        )
    ).scalars().first()
