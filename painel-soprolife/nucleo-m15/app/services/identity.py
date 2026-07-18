"""Candidatos de identidade — NUNCA fusão automática.

Telefone igual ou nome igual geram apenas um IdentityCandidate pendente,
que exige decisão humana registrada em migration_decisions.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import IdentityCandidate, Person, PersonContact


def find_person_candidates(
    db: Session,
    nome_normalizado: str | None,
    telefones_normalizados: list[str],
    exclude_person_id: str | None = None,
) -> list[tuple[Person, str]]:
    """Retorna [(pessoa, motivo)] — candidatos, nunca prova de identidade."""
    results: dict[str, tuple[Person, str]] = {}
    if telefones_normalizados:
        stmt = (
            select(Person)
            .join(PersonContact, PersonContact.person_id == Person.id)
            .where(PersonContact.valor_normalizado.in_(telefones_normalizados))
        )
        for person in db.execute(stmt).scalars().unique():
            if person.id != exclude_person_id:
                results[person.id] = (person, "telefone_igual")
    if nome_normalizado:
        stmt = select(Person).where(Person.nome_normalizado == nome_normalizado)
        for person in db.execute(stmt).scalars():
            if person.id != exclude_person_id and person.id not in results:
                results[person.id] = (person, "nome_igual")
    return list(results.values())


def register_candidates(
    db: Session,
    person: Person,
    candidates: list[tuple[Person, str]],
    origem: str = "import",
    batch_id: str | None = None,
    import_row_id: str | None = None,
) -> list[IdentityCandidate]:
    """Registra candidatos pendentes SEM duplicar o mesmo par/motivo
    (idempotente sob o índice parcial único uq_identity_candidate_pendente)."""
    from sqlalchemy.exc import IntegrityError

    created = []
    for candidate_person, motivo in candidates:
        existing = db.execute(
            select(IdentityCandidate).where(
                IdentityCandidate.person_id == person.id,
                IdentityCandidate.candidate_person_id == candidate_person.id,
                IdentityCandidate.motivo == motivo,
                IdentityCandidate.status == "pendente",
            )
        ).scalars().first()
        if existing is not None:
            continue
        row = IdentityCandidate(
            origem=origem,
            batch_id=batch_id,
            import_row_id=import_row_id,
            person_id=person.id,
            candidate_person_id=candidate_person.id,
            motivo=motivo,
            detalhes={
                "person_public_code": person.public_code,
                "candidate_public_code": candidate_person.public_code,
            },
        )
        try:
            with db.begin_nested():
                db.add(row)
            created.append(row)
        except IntegrityError:
            continue  # corrida: outro fluxo registrou o mesmo par — ignora
    return created
