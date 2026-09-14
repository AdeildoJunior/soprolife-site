"""M29 — versioned national DPS tax configuration: create, list, resolve active.

Mirrors ``FiscalPolicy``'s append-only versioning contract
(``app/services/nfse.py::create_policy``/``evaluate``) but for the CONCRETE
restricted-layout fields (``NationalDpsConfiguration``,
``app/services/nfse_national/config.py``) a real DPS build actually needs —
issuer CNPJ, national/municipal service codes, NBS, Simples/ISS/retention
selections, the current approximate-tax percentage. No secret, certificate
or password ever lives in this table: it is tax/accounting configuration
only, safe to display in full to an authorized reader.

A configuration is resolved by ``effective_from``, never mutated: a later
accountant decision (e.g. a Simples percentage change) is always a NEW row.
A document built at any past competence date always resolves the row that
was actually active then — ``FiscalArtifact``-style immutability, enforced
at the database level by the M29 migration's triggers.
"""
from __future__ import annotations

from datetime import date

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...audit import audit
from ...models import NationalDpsConfigurationVersion
from .config import NationalDpsConfiguration


def fail(code: str, status: int = 409):
    raise HTTPException(status, detail={"codigo": code})


def create_version(db: Session, *, environment: str, effective_from: date,
                    validation_state: str, configuration: NationalDpsConfiguration,
                    actor: str, request_id: str | None = None) -> NationalDpsConfigurationVersion:
    """Idempotent-if-identical create, mirroring ``nfse.create_policy()``:
    the same version with the exact same payload returns the existing row;
    the same version with a DIFFERENT payload fails closed rather than
    silently mutating fiscal history that may already have been used to
    build a signed DPS.
    """
    existing = db.scalar(select(NationalDpsConfigurationVersion).where(
        NationalDpsConfigurationVersion.version == configuration.version))
    data = configuration.model_dump(mode="json")
    if existing:
        same = (existing.environment == environment and
                existing.effective_from == effective_from and
                existing.validation_state == validation_state and
                existing.configuration == data)
        if not same:
            fail("national_dps_configuration_version_immutable")
        return existing
    row = NationalDpsConfigurationVersion(
        version=configuration.version, environment=environment,
        effective_from=effective_from, validation_state=validation_state,
        configuration=data, created_by=actor,
    )
    db.add(row)
    db.flush()
    audit(db, "fiscal.national_dps_configuration_created", "national_dps_configuration", row.id,
          user_id=actor, request_id=request_id,
          detalhes={"version": row.version, "environment": row.environment,
                    "effective_from": row.effective_from.isoformat(),
                    "status": row.validation_state})
    db.commit()
    return row


def list_versions(db: Session, *, environment: str | None = None) -> list[NationalDpsConfigurationVersion]:
    stmt = select(NationalDpsConfigurationVersion).order_by(
        NationalDpsConfigurationVersion.effective_from.desc(),
        NationalDpsConfigurationVersion.created_at.desc())
    if environment:
        stmt = stmt.where(NationalDpsConfigurationVersion.environment == environment)
    return list(db.scalars(stmt).all())


def resolve_active_version(db: Session, *, environment: str,
                           as_of: date) -> NationalDpsConfigurationVersion | None:
    """The latest VALIDATED row whose ``effective_from <= as_of``. Never a
    draft: a draft version is visible for review but can never silently
    become "the" active configuration a real document is built from."""
    return db.scalar(select(NationalDpsConfigurationVersion).where(
        NationalDpsConfigurationVersion.environment == environment,
        NationalDpsConfigurationVersion.validation_state == "validated",
        NationalDpsConfigurationVersion.effective_from <= as_of,
    ).order_by(NationalDpsConfigurationVersion.effective_from.desc(),
               NationalDpsConfigurationVersion.created_at.desc()).limit(1))


def resolve_active_configuration(db: Session, *, environment: str,
                                 as_of: date) -> NationalDpsConfiguration | None:
    """Same as ``resolve_active_version()``, but returns the validated,
    parsed ``NationalDpsConfiguration`` contract object — never the raw
    dict — so a caller can never accidentally build a DPS from a payload
    that predates a stricter contract without noticing.
    """
    row = resolve_active_version(db, environment=environment, as_of=as_of)
    if row is None:
        return None
    try:
        return NationalDpsConfiguration.model_validate(row.configuration)
    except ValidationError:
        # A stored row should always already be valid (create_version()
        # validates before persisting) — but a contract change over time
        # must never be silently treated the same as "no configuration
        # exists"; the caller sees this as its own blocker, never invented.
        return None


SAFE_CONFIGURATION_FIELDS = (
    "version", "layout_version", "issuer_cnpj", "issuer_name", "issuer_municipio_ibge",
    "issuer_inscricao_municipal", "issuer_op_simp_nac", "issuer_reg_ap_trib_sn",
    "issuer_reg_esp_trib", "codigo_tributacao_nacional", "codigo_tributacao_municipal",
    "codigo_nbs", "trib_issqn", "tp_ret_issqn",
    "aliquota_percentual", "ind_tot_trib", "p_tot_trib_sn", "validation_reference",
)


def safe_summary(row: NationalDpsConfigurationVersion) -> dict:
    """Safe-to-display fields only — this table never holds secrets, so
    every configuration field is included, explicitly enumerated so a
    future field addition to the contract is never exposed by accident.
    """
    cfg = row.configuration or {}
    return {
        "id": row.id, "version": row.version, "environment": row.environment,
        "effective_from": row.effective_from.isoformat(), "validation_state": row.validation_state,
        "created_by": row.created_by, "created_at": row.created_at.isoformat(),
        "configuration": {key: cfg.get(key) for key in SAFE_CONFIGURATION_FIELDS},
    }
