"""M61 — record an NFS-e this system did not issue, so it never issues it again.

THE PROBLEM

Two real NFS-e were issued by hand in production on 2026-09-15, before this
automation existed. The exams and financial entries that generated them sit in
the operational database looking exactly like any other eligible fact. Point
the automation at one and it will find no fiscal document, conclude the
service has not been invoiced, and issue a SECOND invoice for it.

A duplicate NFS-e is not a bug you fix by deleting a row. It is a real fiscal
document at the tax authority, and undoing it means a cancellation with its
own rules, deadlines and paperwork. So the only acceptable design is one that
makes the duplicate impossible before it happens.

THE SHAPE OF THE RECORD

An imported issuance is NOT an ``issue`` attempt. Writing one would be the
convenient lie: this system built no DPS, called no provider, and received
that access key from nobody — it was typed in by a human reading a portal.
``fiscal_attempts`` is append-only evidence of what happened at the provider
boundary, and filling it with issuances that never happened destroys the one
property that makes the history worth keeping.

So the record is an ``import`` attempt (M61 migration c4e8b1f37a92), from a
provider named ``external_manual``, carrying the key and nothing else:

    document   environment=production, state=issued
    attempt    operation=import, phase=completed, provider=external_manual,
               outcome=issued, external_id=<TSIdNFSe>, reconciliation_required=False
    artifacts  NONE — we do not have the NFS-e XML and will not invent one

WHAT THAT GIVES, WITHOUT CHANGING ANYTHING ELSE

Anti-duplication falls out of the existing state machine. ``nfse.operate()``
short-circuits an ``issue`` on a document already in a terminal success state,
and ``uq_fiscal_exam_environment`` allows only one production document per
exam. Nothing new guards this; the guards were already there and this gives
them something to see.

``fiscal_validity`` stays False, correctly, and on three independent counts
of the M58 contract: the operation is not issue/reconcile, the provider is not
``production``, and there is no ``nfse_xml`` artifact. M58 needed no
weakening and no special case. The document reads as exactly what it is —
really issued, really at the authority, and not something this system can
vouch for, because it never saw the document.

FAIL CLOSED

Registration refuses rather than guesses: an access key that is not a
well-formed TSIdNFSe, an exam that already has a production document, a key
already imported against a different exam, a non-production environment. The
same key against the same exam is idempotent, so a re-run after an
interruption is safe.

This module never calls a provider, never builds a DPS, never opens a socket.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..ids import new_uuid
from ..models import (FiscalAttempt, FiscalDocument, FiscalPreparation,
                      SpirometryExam, utcnow)
from .idempotency import payload_fingerprint
from .nfse_national.identifiers import NFSE_ACCESS_KEY_PATTERN

# The operation and provider names that say "this came from outside". Neither
# is accepted anywhere a real issuance is required — that is the point.
IMPORT_OPERATION = "import"
EXTERNAL_PROVIDER = "external_manual"
PRODUCTION_ENVIRONMENT = "production"


def fail(code: str, status: int = 409):
    raise HTTPException(status, detail={"codigo": code})


def normalize_access_key(value: str) -> str:
    """Accept the bare 50-character ``chaveAcesso`` or the 53-character
    ``NFS``-prefixed TSIdNFSe, and return the TSIdNFSe form.

    The storage convention is the prefixed one (M52.1), so a key copied from
    a portal — which shows the bare form — is normalized rather than refused.
    """
    if not isinstance(value, str):
        fail("external_access_key_malformed")
    candidate = value.strip().upper()
    if not candidate.startswith("NFS"):
        candidate = "NFS" + candidate
    if not NFSE_ACCESS_KEY_PATTERN.fullmatch(candidate):
        fail("external_access_key_malformed")
    return candidate


@dataclass(frozen=True)
class ExternalIssuance:
    """One NFS-e issued outside this system, as the operator knows it."""
    spirometry_exam_id: str
    access_key: str
    issued_on: str          # ISO date, as reported by the human. Evidence, not proof.
    # Free text for the OPERATOR's own record. Deliberately never reaches the
    # audit trail: app.audit's allowlist exists to keep free text out, because
    # that is where a patient's name eventually ends up.
    note: str = ""


def already_imported(db: Session, access_key: str) -> FiscalAttempt | None:
    """The import attempt carrying this key, if any. The uniqueness that
    matters is per KEY: the same NFS-e must never be attached to two facts."""
    return db.scalars(
        select(FiscalAttempt).where(
            FiscalAttempt.operation == IMPORT_OPERATION,
            FiscalAttempt.external_id == normalize_access_key(access_key),
        ).limit(1)
    ).first()


def production_document_for(db: Session, spirometry_exam_id: str) -> FiscalDocument | None:
    return db.scalars(
        select(FiscalDocument).where(
            FiscalDocument.spirometry_exam_id == spirometry_exam_id,
            FiscalDocument.environment == PRODUCTION_ENVIRONMENT,
        ).limit(1)
    ).first()


def register_external_issuance(db: Session, issuance: ExternalIssuance, actor: str,
                               request_id: str | None = None) -> FiscalDocument:
    """Record an externally issued production NFS-e against its exam.

    Idempotent for the same (exam, key). Refuses every ambiguity rather than
    resolving it: a wrong link here means either a duplicate invoice later or
    a real invoice attached to the wrong service.
    """
    access_key = normalize_access_key(issuance.access_key)

    exam = db.get(SpirometryExam, issuance.spirometry_exam_id)
    if exam is None:
        fail("external_issuance_exam_not_found", 404)

    existing_import = already_imported(db, access_key)
    if existing_import is not None:
        if existing_import.document_id != getattr(
                production_document_for(db, issuance.spirometry_exam_id), "id", None):
            # The same NFS-e cannot belong to two different facts.
            fail("external_access_key_already_imported_for_another_document")
        return db.get(FiscalDocument, existing_import.document_id)

    document = production_document_for(db, issuance.spirometry_exam_id)
    if document is not None:
        # An exam may hold only one production document. If it already has one
        # and it is not this import, something else created it and a human has
        # to look — never overwrite.
        fail("production_document_already_exists_for_exam")

    now = utcnow()
    document = FiscalDocument(
        id=new_uuid(), spirometry_exam_id=issuance.spirometry_exam_id,
        environment=PRODUCTION_ENVIRONMENT,
        # Terminal success from the start: there is nothing to attempt. This is
        # also what makes nfse.operate() refuse a later 'issue'.
        state="issued",
        eligibility="eligible", blocking_reasons=[], created_by=actor,
        idempotency_key=payload_fingerprint({"external_import": access_key}),
        idempotency_fingerprint=payload_fingerprint(
            {"exam": issuance.spirometry_exam_id, "key": access_key}),
    )
    db.add(document)
    db.flush()

    # A preparation is required by the attempt's FK. It records WHAT was
    # invoiced as far as we know it, and is explicitly marked as reconstructed
    # rather than computed by evaluate() — we are describing a past act, not
    # deciding a future one.
    preparation = FiscalPreparation(
        id=new_uuid(), document_id=document.id, flow="EXTERNAL",
        blocking_reasons=[], fingerprint=payload_fingerprint(
            {"external_import": access_key, "issued_on": issuance.issued_on}),
        created_by=actor, created_at=now,
    )
    db.add(preparation)
    db.flush()

    attempt = FiscalAttempt(
        id=new_uuid(), document_id=document.id, preparation_id=preparation.id,
        operation_id=new_uuid(), operation=IMPORT_OPERATION, phase="completed",
        number=1, provider=EXTERNAL_PROVIDER, environment=PRODUCTION_ENVIRONMENT,
        outcome="issued", external_id=access_key, error_code=None,
        reconciliation_required=False, actor_id=actor,
        started_at=now, completed_at=now,
        idempotency_key=payload_fingerprint({"import_attempt": access_key}),
        idempotency_fingerprint=payload_fingerprint({"import_attempt": access_key}),
    )
    db.add(attempt)
    db.flush()

    audit(db, "fiscal.external_issuance_imported", "fiscal_document", document.id,
          user_id=actor, request_id=request_id,
          detalhes={
              "external_id": access_key,          # a fiscal identifier, not PII
              "issued_on": issuance.issued_on,
              "provider": EXTERNAL_PROVIDER,
              "operation": IMPORT_OPERATION,
              "nfse_xml_present": False,
              "fiscal_validity_claimed": False,
          })
    db.commit()
    return document
