"""Candidatos de identidade — NUNCA fusão automática.

Telefone igual ou nome igual geram apenas um IdentityCandidate pendente,
que exige decisão humana registrada em migration_decisions.

Correção telefone compartilhado (2026-11-21): telefone não é identificador de
paciente. Irmãos, dependentes e crianças sem telefone próprio compartilham o
contato do responsável. Por isso telefone igual SOZINHO é apenas um aviso
informativo — nunca um "duplicado forte" que bloqueia o cadastro. A prova de
que são a MESMA pessoa continua forte via CPF (caminho próprio) e via
nome + data de nascimento fortemente coincidentes (`duplicado_forte`).
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import IdentityCandidate, Person, PersonContact

# Texto que a tela mostra quando o único sinal é telefone igual: informa, mas
# não impede. Centralizado aqui para API e front dizerem a mesma coisa.
AVISO_TELEFONE_COMPARTILHADO = (
    "Este telefone já está vinculado a outro paciente. "
    "Se for o telefone do responsável, você pode continuar."
)


def duplicado_forte(
    nome_normalizado_novo: str | None,
    nascimento_novo: date | None,
    candidato: Person,
    motivo: str = "",
) -> bool:
    """Candidato é a MESMA pessoa com força suficiente para bloquear o cadastro?

    Regras (correção telefone compartilhado):

    * ``motivo == "cpf"`` — CPF idêntico é prova forte (mantido).
    * nomes diferentes — nunca é duplicado forte (ex.: só o telefone bateu,
      que é o caso dos irmãos no WhatsApp da mãe).
    * nomes iguais — é duplicado forte, EXCETO quando as duas datas de
      nascimento são conhecidas e diferentes: isso PROVA pessoas distintas
      (mesmo nome, nascimento diferente) e derruba o bloqueio.

    Nome igual com nascimento ausente em qualquer lado continua bloqueando
    (com confirmação humana), porque aí não há prova de que sejam distintas.
    """

    if motivo == "cpf":
        return True
    if not nome_normalizado_novo:
        return False
    if candidato.nome_normalizado != nome_normalizado_novo:
        return False
    if (
        nascimento_novo is not None
        and candidato.data_nascimento is not None
        and nascimento_novo != candidato.data_nascimento
    ):
        return False
    return True


def descrever_candidatos(
    candidatos: list[tuple[Person, str]],
    nome_normalizado_novo: str | None,
    nascimento_novo: date | None,
) -> list[dict]:
    """[(Person, motivo)] -> dicts que a tela consome, com `forte` e `aviso`.

    `forte=True` significa bloqueio (usar existente ou confirmar criação);
    `forte=False` é aviso informativo que NÃO impede o cadastro.
    """

    descritos: list[dict] = []
    for pessoa, motivo in candidatos:
        forte = duplicado_forte(nome_normalizado_novo, nascimento_novo, pessoa, motivo)
        descritos.append(
            {
                "id": pessoa.id,
                "public_code": pessoa.public_code,
                "nome_completo": pessoa.nome_completo,
                "data_nascimento": pessoa.data_nascimento.isoformat()
                if pessoa.data_nascimento
                else None,
                "motivo": motivo,
                "forte": forte,
                "aviso": None
                if forte
                else (
                    AVISO_TELEFONE_COMPARTILHADO
                    if motivo == "telefone_igual"
                    else None
                ),
            }
        )
    return descritos


def ha_bloqueante(candidatos_descritos: list[dict]) -> bool:
    """Existe ao menos um candidato forte (que exige decisão humana)?"""

    return any(c["forte"] for c in candidatos_descritos)


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
