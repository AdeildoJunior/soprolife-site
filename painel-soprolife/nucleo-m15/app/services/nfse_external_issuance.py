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

M71 — A MANUAL NOTE AFTER A PRE-SEND REFUSAL

The button can create a production document, have every confirmation refused
by the worker BEFORE the provider boundary (e.g. an A1 too close to expiry),
and the team then issues the note by hand. The exam already holds a
production document, so the rule above would refuse the import — and the
document would stay ``pending``, one click away from a duplicate.

That one case, and only that one, may reuse the existing document. It must
be provably never sent (``never_sent_blockers``): pending, no attempt, no
artifact, no DPS number, every request terminal as ``refused``/``expired``
with zero POST and zero GET, no doorbell left in the spool, no post-intent
in the worker's runtime directory. The old preparations and refused requests
stay as they are; the import adds its own preparation and attempt and moves
the document to ``issued``. Anything short of that proof is refused.

This module never calls a provider, never builds a DPS, never opens a socket.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..ids import new_uuid
from ..models import (DpsNumberAllocation, FiscalArtifact, FiscalAttempt, FiscalDocument,
                      FiscalIssuanceRequest, FiscalPreparation, SpirometryExam, utcnow)
from .idempotency import payload_fingerprint
from . import nfse
from .nfse_national.identifiers import NFSE_ACCESS_KEY_PATTERN

# The operation and provider names that say "this came from outside". Neither
# is accepted anywhere a real issuance is required — that is the point.
IMPORT_OPERATION = "import"
EXTERNAL_PROVIDER = "external_manual"
PRODUCTION_ENVIRONMENT = "production"
# M71 — the only request outcomes that prove the worker never crossed the
# provider boundary. 'interrupted' is deliberately absent: it counts as sent.
NEVER_SENT_REQUEST_STATUSES = ("refused", "expired")


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


def _path_exists(path: Path) -> bool | None:
    """True/False, or None when it cannot be known (permission, I/O)."""
    try:
        os.stat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    return True


def never_sent_blockers(db: Session, document: FiscalDocument, *,
                        spool_dir: Path | str | None,
                        runtime_dir: Path | str | None) -> list[str]:
    """Why ``document`` cannot be proven never sent. Empty means: a production
    document prepared by the button and never handed to the provider, so a
    manual NFS-e may be attached to it (M71). Read-only.

    Fail closed: a worker directory that is not configured or cannot be read
    is a blocker, because the absence of evidence there is not proof.
    """
    blockers: list[str] = []
    if document.environment != PRODUCTION_ENVIRONMENT:
        blockers.append("document_not_production")
    if document.state != "pending":
        blockers.append("document_not_pending")
    if db.scalar(select(FiscalAttempt.id).where(
            FiscalAttempt.document_id == document.id).limit(1)) is not None:
        blockers.append("document_has_attempt")
    if db.scalar(select(FiscalArtifact.id).where(
            FiscalArtifact.document_id == document.id).limit(1)) is not None:
        blockers.append("document_has_artifact")
    if db.get(DpsNumberAllocation, document.id) is not None:
        blockers.append("document_has_dps_number")

    requests = db.scalars(select(FiscalIssuanceRequest).where(
        FiscalIssuanceRequest.document_id == document.id)).all()
    for row in requests:
        if row.status not in NEVER_SENT_REQUEST_STATUSES:
            blockers.append(f"request_{row.status}")
        if row.kind != "issue":
            blockers.append("request_reconcile")
        if (row.provider_post_count or 0) != 0:
            blockers.append("request_post_count")
        if (row.provider_get_count or 0) != 0:
            blockers.append("request_get_count")
        if row.operation_id is not None or row.external_id is not None:
            blockers.append("request_reached_operation")

    if spool_dir is None or runtime_dir is None:
        blockers.append("worker_dirs_not_configured")
    else:
        spool, runtime = Path(spool_dir), Path(runtime_dir)
        if _path_exists(spool) is not True or _path_exists(runtime) is not True:
            blockers.append("worker_dirs_unreadable")
        else:
            for row in requests:
                for kind in ("issue", "reconcile"):
                    found = _path_exists(spool / f"{row.id}.{kind}")
                    if found is not False:
                        blockers.append("doorbell_present" if found else "spool_unreadable")
                found = _path_exists(runtime / row.id / "post-intent.json")
                if found is not False:
                    blockers.append("post_intent_present" if found else "runtime_unreadable")
    # Stable, de-duplicated, in first-seen order.
    return list(dict.fromkeys(blockers))


def register_external_issuance(db: Session, issuance: ExternalIssuance, actor: str,
                               request_id: str | None = None, *,
                               spool_dir: Path | str | None = None,
                               runtime_dir: Path | str | None = None) -> FiscalDocument:
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
    reused = document is not None
    if reused:
        # An exam may hold only one production document. If it already has one
        # and it is not this import, something else created it and a human has
        # to look — never overwrite. The single exception (M71) is a document
        # PROVEN never sent; the row is locked so a concurrent confirmation
        # (authorize_issue locks it too) cannot slip in between.
        if document.state != "pending":
            fail("production_document_already_exists_for_exam")
        document = db.scalars(select(FiscalDocument).where(
            FiscalDocument.id == document.id).with_for_update()).one()
        if never_sent_blockers(db, document, spool_dir=spool_dir, runtime_dir=runtime_dir):
            fail("pending_document_not_provably_unsent")

    now = utcnow()
    if reused:
        prior_requests = len(db.scalars(select(FiscalIssuanceRequest.id).where(
            FiscalIssuanceRequest.document_id == document.id)).all())
        document.state = "issued"
        db.flush()
    else:
        prior_requests = 0
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
        # M26.34 — claim the exam's occupancy, exactly like nfse.prepare()
        # does. Without this an exam already inside a GROUPED document would
        # also get its own imported single document: the old
        # `uq_fiscal_exam_environment` cannot see a grouped document (its
        # column is NULL), so nothing at the database level would have
        # stopped the same service being invoiced twice — once in the group,
        # once here. Refuses with a code instead of writing the duplicate.
        nfse.claim_exam_occupancy(db, document, issuance.spirometry_exam_id)

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
              "reused_pending_document": reused,
              "prior_refused_requests": prior_requests,
          })
    db.commit()
    return document
