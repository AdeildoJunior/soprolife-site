"""Duas camadas de identidade: UUID interno + código público sequencial.

Códigos públicos: PES-000001, ESP-000001, ... emitidos por code_sequences
com bloqueio de linha (PostgreSQL) — nunca derivados de linha de planilha,
relógio de navegador ou contador sem lock.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

PREFIXES = {
    "people": "PES",
    "leads": "LEA",
    "spirometry_exams": "ESP",
    "consultations": "CON",
    "partners": "CLI",
    "partner_units": "UNI",
    "partner_contacts": "CTT",
    "partnerships": "PAR",
    "partner_referrals": "ENC",
    "interactions": "INT",
    "followups": "FUP",
    "financial_entries": "LAN",
}


def new_uuid() -> str:
    return str(uuid.uuid4())


def format_public_code(prefix: str, value: int) -> str:
    return f"{prefix}-{value:06d}"


def allocate_public_code(session: Session, table_name: str) -> str:
    """Aloca o próximo código público de forma atômica.

    As sequências são PRESEEDADAS na migration inicial, então o
    SELECT ... FOR UPDATE sempre encontra a linha e serializa concorrentes
    no PostgreSQL. Se a linha faltar (banco antigo/teste montado à mão), o
    INSERT ocorre em SAVEPOINT: perder a corrida vira retry, nunca 500.
    Roda dentro da transação do chamador.
    """
    from sqlalchemy.exc import IntegrityError

    from .models import CodeSequence

    prefix = PREFIXES[table_name]
    for _attempt in range(2):
        seq = session.execute(
            select(CodeSequence).where(CodeSequence.prefix == prefix).with_for_update()
        ).scalar_one_or_none()
        if seq is not None:
            value = seq.next_value
            seq.next_value = value + 1
            session.flush()
            return format_public_code(prefix, value)
        try:
            with session.begin_nested():
                session.add(CodeSequence(prefix=prefix, next_value=1))
        except IntegrityError:
            continue  # outra transação criou a linha — relê com lock
    raise RuntimeError(f"Falha ao alocar código público para prefixo {prefix}.")
