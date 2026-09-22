"""M36 — durable, globally-unique allocation of ``numero_dps`` (part of
TSIdDPS, the DPS's official identifier).

Root cause this replaces: ``numero_dps`` used to be derived from
``FiscalAttempt.number`` — an append-only sequence, but scoped PER
``FiscalDocument``. Every document's first real dispatch attempt therefore
minted ``numero_dps=1``; for the same issuer municipality + tipo de
inscrição federal + inscrição federal + série DPS, two different documents
could produce an identical TSIdDPS (see
``nfse_national.identifiers.DPS_ID_PATTERN`` for the official uniqueness
shape this must never violate).

The fix is two durable tables (``DpsNumberSequence``/``DpsNumberAllocation``,
see ``app/models.py`` and the M36 migration), never an in-memory counter:
- ``DpsNumberSequence`` is the counter, one row per SCOPE (never per
  document), allocated with the exact same row-locked pattern already
  proven by ``ids.allocate_public_code``/``CodeSequence``.
- ``DpsNumberAllocation`` is append-only evidence of the ONE number a given
  document ever received — read back on every later attempt (retry,
  reconcile #2/#3, ...) instead of allocating again.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...models import DpsNumberAllocation, DpsNumberSequence
from .identifiers import CNPJ_PATTERN, CPF_PATTERN, InvalidIdentifierError, assert_municipio_ibge


def scope_key_for(*, codigo_municipio: str, tipo_inscricao_federal: int,
                  inscricao_federal: str, serie_dps: str) -> str:
    """Deterministic scope identity — EXACTLY the TSIdDPS components besides
    the number itself: the municipality of the ISSUER (from the active
    ``NationalDpsConfiguration`` — never a document's own service location,
    a distinct M31 concept), tipo de inscrição federal, inscrição federal
    (already zero-padded to 14 chars, matching TSIdDPS — see
    ``identifiers.DpsIdComponents.inscricao_federal_padded``), and série
    DPS. Two documents sharing this scope must never receive the same
    ``numero_dps`` — that guarantee is what this module exists to provide.

    Never guessed: every component is validated against the same official
    TSIdDPS-derived patterns ``identifiers.py`` already uses.
    """
    assert_municipio_ibge(codigo_municipio)
    if tipo_inscricao_federal not in (1, 2):
        raise InvalidIdentifierError("Tipo de inscrição federal deve ser 1 (CPF) ou 2 (CNPJ).")
    padded = inscricao_federal.rjust(14, "0") if tipo_inscricao_federal == 1 else inscricao_federal
    pattern = CPF_PATTERN if tipo_inscricao_federal == 1 else CNPJ_PATTERN
    check_value = inscricao_federal if tipo_inscricao_federal == 1 else padded
    if not pattern.fullmatch(check_value):
        raise InvalidIdentifierError("Inscrição federal não confere com o padrão TSCPF/TSCNPJ.")
    if not serie_dps.isdigit() or not (1 <= len(serie_dps) <= 5):
        raise InvalidIdentifierError("Série da DPS deve ter até 5 dígitos.")
    return f"{codigo_municipio}:{tipo_inscricao_federal}:{padded}:{serie_dps.rjust(5, '0')}"


def allocate_dps_number(db: Session, *, document_id: str, codigo_municipio: str,
                        tipo_inscricao_federal: int, inscricao_federal: str,
                        serie_dps: str) -> int:
    """Returns the ONE ``numero_dps`` this document will ever use within
    this scope — durable (survives process restart), append-only (never
    reused), and safe under concurrent issuance.

    - Same document, called again (idempotent retry, reconcile #2/#3, ...):
      returns the SAME number every time, without consuming a new sequence
      value — this is the M33 "reconciliation targets the original DPS"
      contract, now derived structurally instead of via ``FiscalAttempt.number``.
    - Different documents, same scope: strictly distinct, monotonically
      increasing numbers — enforced by ``DpsNumberSequence``'s row-locked
      allocation (row locking is genuinely race-safe on PostgreSQL; SQLite
      is single-writer, used here for deterministic tests only, exactly
      like every other sequence in this codebase — see ``app/db.py``/M23)
      AND independently by ``dps_number_allocations``'s own
      ``uq_dps_number_scope`` database constraint.
    - A rejected/uncertain outcome never frees or reuses a number: this
      function only ever GETS a document's number (existing or freshly
      allocated) — it has no notion of outcome/state, so there is no code
      path here that could roll an allocation back.
    """
    scope_key = scope_key_for(
        codigo_municipio=codigo_municipio, tipo_inscricao_federal=tipo_inscricao_federal,
        inscricao_federal=inscricao_federal, serie_dps=serie_dps,
    )
    existing = db.execute(
        select(DpsNumberAllocation).where(DpsNumberAllocation.document_id == document_id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing.dps_number

    for _attempt in range(3):
        seq = db.execute(
            select(DpsNumberSequence).where(DpsNumberSequence.scope_key == scope_key).with_for_update()
        ).scalar_one_or_none()
        if seq is None:
            try:
                with db.begin_nested():
                    db.add(DpsNumberSequence(scope_key=scope_key, next_value=1))
            except IntegrityError:
                pass  # another transaction created it first
            continue  # re-select (now under the row lock) either way
        value = seq.next_value
        seq.next_value = value + 1
        db.flush()
        try:
            with db.begin_nested():
                db.add(DpsNumberAllocation(document_id=document_id, scope_key=scope_key, dps_number=value))
            db.flush()
            return value
        except IntegrityError:
            # A concurrent transaction already allocated THIS document's
            # number first (with a different sequence value). `value` above
            # is a permanent, never-reused gap — acceptable by design: gaps
            # are fine, REUSE is what must never happen. Re-read the
            # winner's row instead of allocating a second one.
            existing = db.execute(
                select(DpsNumberAllocation).where(DpsNumberAllocation.document_id == document_id)
            ).scalar_one_or_none()
            if existing is not None:
                return existing.dps_number
            continue
    raise RuntimeError(f"Falha ao alocar numero_dps para o documento {document_id}.")
