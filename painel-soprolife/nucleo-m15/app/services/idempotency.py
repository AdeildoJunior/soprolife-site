"""Idempotência atômica por chave + fingerprint canônico do payload.

Contrato:
- mesma chave + mesmo payload  -> devolve o registro existente (idempotente);
- mesma chave + payload diferente -> 409 (conflito de idempotência);
- corrida entre duas requisições com a mesma chave -> uma insere, a outra
  captura IntegrityError no SAVEPOINT, recarrega e aplica o contrato acima.
Nunca 500 para conflito conhecido.
"""

import hashlib
import json
from collections.abc import Callable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def payload_fingerprint(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _conflict() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail="Idempotency key já usada com payload diferente.",
    )


def _existing_or_conflict(model, existing, fingerprint: str):
    if existing.idempotency_fingerprint != fingerprint:
        raise _conflict()
    return existing


def idempotent_create(
    db: Session,
    model,
    key: str | None,
    payload: dict,
    factory: Callable[[str | None, str | None], object],
):
    """Cria com idempotência. `factory(key, fingerprint)` monta, adiciona e
    dá flush no objeto novo (dentro do SAVEPOINT desta função).

    Retorna (objeto, ja_existia: bool).
    """
    if not key:
        obj = factory(None, None)
        return obj, False
    fingerprint = payload_fingerprint(payload)
    existing = db.execute(
        select(model).where(model.idempotency_key == key)
    ).scalar_one_or_none()
    if existing is not None:
        return _existing_or_conflict(model, existing, fingerprint), True
    try:
        with db.begin_nested():
            obj = factory(key, fingerprint)
        return obj, False
    except IntegrityError:
        # corrida: outra transação inseriu a mesma chave primeiro
        existing = db.execute(
            select(model).where(model.idempotency_key == key)
        ).scalar_one_or_none()
        if existing is None:
            # colisão de outra unique (ex.: public_code) — conflito de domínio
            raise HTTPException(
                status_code=409, detail="Conflito de unicidade ao criar o registro."
            ) from None
        return _existing_or_conflict(model, existing, fingerprint), True
